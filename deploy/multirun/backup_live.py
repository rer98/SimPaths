"""(C) Copyright 2026, by Ross Richardson

Online PostgreSQL/private-file recovery points with immutable-file retention.
Active model workspaces are excluded; restoration records bounded interruption,
preserves frozen settings and never treats partial output as scientific success.
@author ross richardson
"""
from contextlib import ExitStack
from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
from types import SimpleNamespace
from uuid import uuid4

from .artifacts import ArtifactError, write_attribution
from .backup_files import copy_selected, copy_tree, file_hash, has_space, publish, reserve_space, scan, sync_tree
from .maintenance import GATE, state_guard
from .releases import ReleaseRegistry, atomic_json, existing_directory

FORMAT = 'simpaths.multirun.backup.v2'


def rows(c, queue, query, tables, args=()):
    from psycopg import sql
    return c.execute(sql.SQL(query).format(*[sql.Identifier(queue.schema,t) for t in tables]),args).fetchall()


def selected_files(c, queue, state):
    """Choose only snapshot-referenced uploads, prepared inputs and settled output."""
    from .queue_adapter import read_prepared, result_catalogue
    from .prepared_dataset import verify_snapshot
    from .backup import source_roots
    keys = set()
    uploads = rows(c,queue,"SELECT id,bytes,sha256 FROM {} WHERE state='ready'",['uploads'])
    for row in uploads:
        key='uploads/'+row['id']
        actual=file_hash(state,key)
        if (actual['bytes'],actual['sha256']) != (row['bytes'],row['sha256']):
            raise ArtifactError('An upload differs from its PostgreSQL receipt')
        keys.add(key)
    prepared = rows(c,queue,'''SELECT p.location,d.prepared_fingerprint FROM {} p
        JOIN {} d ON d.pool_id=p.pool_id AND d.id=p.dataset_id
        LEFT JOIN {} l ON l.pool_id=p.pool_id AND l.dataset_id=p.dataset_id
        WHERE l.state IS NULL OR l.state NOT IN ('deleting','deleted') ORDER BY p.location''',
        ['prepared_locations','datasets','dataset_lifecycle'])
    for row in prepared:
        path=existing_directory(row['location'])
        receipt=read_prepared(path)
        if receipt['sha256'] != row['prepared_fingerprint']:
            raise ArtifactError('A prepared dataset differs from its PostgreSQL receipt')
        verify_snapshot(path,receipt)
        if path.is_relative_to(state):
            keys.update((path.relative_to(state)/key).as_posix() for key,info in scan(path).items() if stat.S_ISREG(info[2]))
    roots=source_roots(state,[p['location'] for p in prepared])
    attempts = rows(c,queue,'''SELECT a.*,j.configuration_id,j.resources,j.dataset_id,j.model_digest,
        j.prepared_fingerprint,j.execution_run,e.specification,d.requested_at,d.deleted_at,
        r.state AS retention_state FROM {} a JOIN {} j ON j.id=a.job_id JOIN {} e ON e.id=j.experiment_id
        LEFT JOIN {} d ON d.attempt_id=a.id LEFT JOIN {} r
          ON r.pool_id=a.pool_id AND r.kind='output' AND r.artifact_id=a.id::text
        WHERE a.phase='finished' ORDER BY a.id''',
        ['attempts','jobs','experiments','output_deletions','artifact_retention'])
    for row in attempts:
        base=state/'execution'/row['execution_key']
        # No unconfirmed/live workspace is read, even if a repetition CSV exists.
        if not base.exists():
            if row['outcome']=='success' and row['specification'].get('operation')!='prepare' and not row['requested_at'] and row['retention_state']!='deleted':
                raise ArtifactError('A retained successful output is missing')
            continue
        for leaf in ('identity.json','exit.json','payload-retired.json'):
            if (base/leaf).exists(): keys.add((base.relative_to(state)/leaf).as_posix())
        diagnostics=base/'diagnostics'
        if diagnostics.exists():
            keys.update((diagnostics.relative_to(state)/k).as_posix() for k,v in scan(diagnostics).items() if stat.S_ISREG(v[2]))
        if row['specification'].get('operation')=='prepare' or row['requested_at'] or row['retention_state'] in ('deleting','deleted'):
            continue
        output=base/'work/output'
        if not output.exists(): continue
        if row['outcome']=='success':
            lease=queue._lease(row,row)
            actual=result_catalogue(lease,base/'work')
            expected=rows(c,queue,'SELECT actual_seed,output_fingerprint FROM {} WHERE attempt_id=%s ORDER BY ordinal',
                          ['repetitions'],(row['id'],))
            if [(v['seed'],v['fingerprint']) for v in actual] != [(v['actual_seed'],v['output_fingerprint']) for v in expected]:
                raise ArtifactError('Retained output differs from its PostgreSQL receipts')
        for run in output.iterdir():
            existing_directory(run)
            csv=run/'csv'
            if csv.exists():
                keys.update((csv.relative_to(state)/key).as_posix() for key,info in scan(csv).items() if stat.S_ISREG(info[2]))
            if (run/'input/options.txt').exists(): keys.add((run/'input/options.txt').relative_to(state).as_posix())
    return keys,roots


def create(queue, state, destination, tools, *, after_snapshot=None, single_capture=None, minimum_free_gib=1):
    from jasmine_web.batch.backup import online_database, table_inventory
    from jasmine_web.batch.backup_guard import file_guard
    from . import backup
    from .backup_single import discover, capture, schema_guard
    state=existing_directory(state)
    destination=Path(destination).absolute(); parent=existing_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise ArtifactError('Choose a new backup destination; existing files are never replaced')
    if destination.is_relative_to(state) or state.is_relative_to(destination):
        raise ArtifactError('Keep backups outside service state')
    staging=Path(tempfile.mkdtemp(prefix='.backup-incomplete-',dir=parent))
    try:
        with reserve_space(minimum_free_gib*1024**3),state_guard(state),file_guard(state,backup=True),ExitStack() as stack:
            if (state/GATE).exists(): raise ArtifactError('Activate or finish the inactive restoration first')
            registry=ReleaseRegistry(state)
            stack.enter_context(registry.locked())
            releases=registry.inventory()
            # VM removals/proxy mutations use a separate shared PostgreSQL fence.
            vm_schemas=discover(queue)
            for schema in vm_schemas: stack.enter_context(schema_guard(queue.dsn,schema,backup=True))
            with online_database(queue) as (c,snapshot):
                from psycopg import sql
                from jasmine_web.batch.backup import schema_tables
                # Match the SingleRun migration mutex as well as its ordinary
                # control/removal fence. ACCESS SHARE allows status heartbeats.
                for schema in vm_schemas:
                    if not c.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS held',
                                     ('jasmine-vm:'+schema,)).fetchone()['held']:
                        raise ArtifactError('A SingleRun migration is in progress')
                    c.execute(sql.SQL('LOCK TABLE {} IN ACCESS SHARE MODE').format(sql.SQL(',').join(
                        sql.Identifier(schema,t) for t in schema_tables(c,SimpleNamespace(schema=schema)))))
                # Reject a registry created between discovery and the snapshot.
                from .backup_single import discover as discover_at_snapshot
                if discover_at_snapshot(queue,connection=c)!=vm_schemas:
                    raise ArtifactError('Shared SingleRun registries changed; retry the recovery point')
                bound=rows(c,queue,'SELECT identity FROM {} WHERE pool_id=%s',['executor_bindings'],(queue.pool_id,))
                if bound:
                    root=existing_directory(state/'execution'); info=root.stat()
                    expected=dict(root=str(root),host=Path('/etc/machine-id').read_text().strip(),device=info.st_dev,inode=info.st_ino)
                    if any(bound[0]['identity'].get(k)!=v for k,v in expected.items()):
                        raise ArtifactError('Backup state does not match the queue executor binding')
                version=tools.check(c)
                captured=c.execute('SELECT transaction_timestamp() AS time').fetchone()['time'].isoformat()
                active=rows(c,queue,"SELECT id::text AS id FROM {} WHERE phase<>'finished' ORDER BY id",['attempts'])
                receiving=rows(c,queue,"SELECT id FROM {} WHERE state='receiving' ORDER BY id",['uploads'])
                database=table_inventory(c,queue)
                vm={schema:dict(database=table_inventory(c,SimpleNamespace(schema=schema))) for schema in vm_schemas}
                if after_snapshot: after_snapshot()
                keys,roots=selected_files(c,queue,state)
                if any(destination.is_relative_to(Path(p['source'])) or Path(p['source']).is_relative_to(destination) for p in roots):
                    raise ArtifactError('Keep backups outside prepared input storage')
                # Release bundles/secrets are fixed; operator release changes are
                # fenced separately. Receiving uploads/new workspaces stay writable.
                for folder in ('releases','release'):
                    if (state/folder).exists():
                        keys.update(folder+'/'+k for k,v in scan(state/folder).items()
                            if stat.S_ISREG(v[2]) and not any(p.startswith('.pending-') for p in Path(k).parts))
                for key in ('session-secret','training-imports.json'):
                    if (state/key).exists(): keys.add(key)
                estimate=sum((state/k).stat().st_size for k in keys)
                estimate+=sum(v[3] for p in roots for v in scan(p['source']).values() if stat.S_ISREG(v[2]))
                if not has_space(parent,estimate):
                    raise ArtifactError('Insufficient free space for the complete online backup')
                tools.dump(staging/'database.dump',schema=[queue.schema,*vm_schemas],snapshot=snapshot)
                tree=copy_selected(state,staging/'state',keys)
                for key in ('uploads','artifacts','execution'):
                    (staging/'state'/key).mkdir(mode=0o700,exist_ok=True)
                    if key not in tree['directories']: tree['directories'].append(key)
                tree['directories'].sort()
                (staging/'prepared').mkdir(mode=0o700)
                for root in roots: root['tree'],_=copy_tree(Path(root['source']),staging/root['directory'])
                images={r['image'] for r in releases.values()}
                images.update(r['model_digest'] for r in rows(c,queue,'SELECT model_digest FROM {}',['prepared_locations']))
                for schema in vm_schemas:
                    vm[schema].update((single_capture or capture)(c,queue.dsn,schema,staging/'state',state))
                    images.update(vm[schema]['images'])
                # SingleRun exports are new backup-owned files, so inventory them
                # after capture without following unselected live container paths.
                if vm_schemas:
                    tree=dict(directories=sorted(k for k,v in scan(staging/'state').items() if stat.S_ISDIR(v[2])),
                              files={k:file_hash(staging/'state',k) for k,v in scan(staging/'state').items() if stat.S_ISREG(v[2])})
                manifest=dict(format=FORMAT,id=uuid4().hex,created_at=backup.utc(),pool_id=queue.pool_id,
                    schema=queue.schema,postgres_major=version,state_source=str(state),state_tree=tree,
                    prepared=roots,database=database,dump=file_hash(staging,'database.dump'),images=sorted(images),
                    default_release=next(iter(releases)),release_ids=list(releases),omitted_caches=['download-cache','visualiser-cache'],
                    recovery=dict(captured_at=captured,interrupted_attempts=[r['id'] for r in active],
                                  receiving_uploads=[r['id'] for r in receiving]),vm=vm)
                backup.training_imports(staging,manifest,state)
                write_attribution(staging); (staging/'COPYRIGHT.md').chmod(0o600)
                manifest['attribution']=file_hash(staging,'COPYRIGHT.md')
                atomic_json(staging/'manifest.json',manifest)
                backup.verify(staging); sync_tree(staging); publish(staging,destination)
        return backup.summary(manifest,'backup',online=True,interrupted_attempts=len(active),singlerun_sessions=sum(len(v['sessions']) for v in vm.values()))
    finally:
        if staging.exists(): shutil.rmtree(staging)


def recover_database(c, queue, manifest, target):
    """One deterministic, transactional conversion, only in an inactive target."""
    from psycopg import sql
    from jasmine_web.batch.policy import Policy, next_state
    from .backup_single import recover
    captured=datetime.fromisoformat(manifest['recovery']['captured_at'])
    c.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(queue.schema)))
    active=rows(c,queue,'''SELECT a.*,j.state,j.auto_retry,j.cancel_requested,j.attempts,j.spent_seconds,
        j.retry_credit_seconds,j.stop_reason,e.policy,e.owner_id,j.dataset_id FROM {} a JOIN {} j ON j.id=a.job_id
        JOIN {} e ON e.id=j.experiment_id WHERE a.phase<>'finished' ORDER BY a.id''',['attempts','jobs','experiments'])
    if [str(r['id']) for r in active] != manifest['recovery']['interrupted_attempts']:
        raise ArtifactError('Interrupted attempt inventory differs from the recovery snapshot')
    for row in active:
        at=max(captured,row['created_at'])
        spent=row['spent_seconds']+max(0,(at-row['created_at']).total_seconds())
        policy=Policy(**row['policy'])
        authorised=queue._authorised(c,row['owner_id'],row['dataset_id'],at)
        outcome=('cancelled' if row['cancel_requested'] else row['stop_reason'] or ('revoked' if not authorised else
                 'time_limit' if at>=row['deadline'] or spent-row['retry_credit_seconds']>=policy.total_seconds else 'transient')
                 )
        state=next_state(outcome=outcome,cancelled=row['cancel_requested'],authorised=authorised,
            auto_retry=row['auto_retry'],attempts=row['attempts'],max_attempts=policy.max_attempts,
            spent_seconds=max(0,spent-row['retry_credit_seconds']),total_seconds=policy.total_seconds)
        evidence=hashlib.sha256((manifest['id']+':restored-interruption:'+str(row['id'])).encode()).hexdigest()
        c.execute(sql.SQL("UPDATE {} SET phase='finished',finished_at=%s,outcome=%s,stop_evidence=%s WHERE id=%s").format(
            sql.Identifier(queue.schema,'attempts')),(at,outcome,evidence,row['id']))
        c.execute(sql.SQL('UPDATE {} SET state=%s,spent_seconds=%s,eligible_at=%s WHERE id=%s').format(
            sql.Identifier(queue.schema,'jobs')),(state,spent,at+timedelta(seconds=policy.retry_delay_seconds),row['job_id']))
        # Partial scientific receipts cannot expose output omitted from this backup.
        c.execute(sql.SQL('UPDATE {} SET actual_seed=NULL,output_fingerprint=NULL WHERE attempt_id=%s').format(
            sql.Identifier(queue.schema,'repetitions')),(row['id'],))
    c.execute(sql.SQL('UPDATE {} SET released_at=%s WHERE released_at IS NULL').format(sql.Identifier(queue.schema,'reservations')),(captured,))
    for table in ('executor_bindings','processing_reservations','interactive_reservations'):
        c.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(queue.schema,table)))
    c.execute(sql.SQL("UPDATE {} SET state='failed' WHERE state='receiving'").format(sql.Identifier(queue.schema,'uploads')))
    for schema,record in manifest['vm'].items(): recover(c,schema,record,target,manifest['id'],captured)


def recovery_inventory(c, queue, manifest, target):
    """Journal allowed conversion before commit, only from the exact native dump.

    Triggers can record transaction-time events. Hash the actual transaction,
    rather than running it twice and assuming both clocks will be identical.
    """
    from jasmine_web.batch.backup import table_inventory
    from .backup import mappings,relocate
    locations=rows(c,queue,'SELECT location FROM {}',['prepared_locations'])
    reverse=[(new,old) for old,new in mappings(manifest,target)]
    original={r['location']:relocate(r['location'],reverse) for r in locations}
    return dict(batch=table_inventory(c,queue,original_locations=original),
                vm={s:table_inventory(c,SimpleNamespace(schema=s)) for s in manifest['vm']})
