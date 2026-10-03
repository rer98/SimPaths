"""(C) Copyright 2026, by Ross Richardson

Offline matching MultiRun PostgreSQL/private-file backup and isolated restoration.
Restore never overwrites state or starts services; verified copies need activation.
@author ross richardson
"""
import argparse
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from uuid import uuid4

from .artifacts import ArtifactError, write_attribution
from .backup_files import (RESERVE, check_tree, copy_tree, file_hash, open_file, publish,
                           read_metadata, scan, sync_tree)
from .backup_postgres import PostgresTools
from .maintenance import GATE, LOCK, state_guard
from .releases import ReleaseRegistry, atomic_json, existing_directory, installed_image

FORMAT = 'simpaths.multirun.backup.v1'
GATE_FORMAT = 'simpaths.multirun.restore.v1'
EXCLUDE = (LOCK,GATE,'execution/download-cache','execution/visualiser-cache')
EXCLUDE += ('.backup-files.lock',)
LIVE_FORMAT = 'simpaths.multirun.backup.v2'


def utc():
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value):
    return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def hash_bytes(value, mode=0o600):
    return dict(bytes=len(value),sha256=hashlib.sha256(value).hexdigest(),mode=mode)


def absolute(value):
    if (not isinstance(value,str) or not value.startswith('/') or '..' in Path(value).parts
            or any(ord(c)<32 for c in value)):
        raise ArtifactError('Invalid private backup source path')
    return Path(value)


def source_roots(state, locations):
    """Copy each distinct external prepared directory only once."""
    external=sorted({str(absolute(p)) for p in locations if not absolute(p).is_relative_to(state)})
    roots=[]
    for value in external:
        path=existing_directory(value)
        if state.is_relative_to(path):
            raise ArtifactError('Prepared storage must not contain the entire service state')
        if any(path.is_relative_to(p) or p.is_relative_to(path) for p in roots):
            raise ArtifactError('External prepared dataset directories must not overlap')
        roots.append(path)
    return [dict(source=str(p),directory=f'prepared/{i:04d}') for i,p in enumerate(roots)]


def processor_guards(state, stack):
    """Catch surviving local processors and older dispatcher processes too."""
    import fcntl
    for key in ('execution/dispatcher.lock','execution/download-cache/builder.lock',
                'execution/visualiser-cache/processor.lock'):
        path=state/key
        if not path.exists() and not path.is_symlink(): continue
        stream=stack.enter_context(open_file(state,key))
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ArtifactError('Invalid processor lock')
        try: fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArtifactError('A dispatcher or result processor still owns this state') from None


def check_outputs(c, queue, state):
    """Check retained successful outputs against the recorded repetition hashes."""
    from psycopg import sql
    from .queue_adapter import result_catalogue
    rows=c.execute(sql.SQL('''SELECT a.*,j.configuration_id,j.resources,j.dataset_id,j.model_digest,
        j.prepared_fingerprint,j.execution_run,e.specification FROM {} a JOIN {} j ON j.id=a.job_id
        JOIN {} e ON e.id=j.experiment_id
        LEFT JOIN {} d ON d.attempt_id=a.id LEFT JOIN {} r
          ON r.pool_id=a.pool_id AND r.kind='output' AND r.artifact_id=a.id::text
        WHERE a.outcome='success' AND COALESCE(e.specification->>'operation','simulation')<>'prepare'
        AND d.deleted_at IS NULL AND (r.state IS NULL OR r.state<>'deleted')''').format(
            *[sql.Identifier(queue.schema,t) for t in ('attempts','jobs','experiments','output_deletions','artifact_retention')])).fetchall()
    for row in rows:
        lease=queue._lease(row,row)
        actual=result_catalogue(lease,state/'execution'/lease.execution_key/'work')
        expected=c.execute(sql.SQL('SELECT actual_seed,output_fingerprint FROM {} WHERE attempt_id=%s '
                                  'ORDER BY ordinal').format(sql.Identifier(queue.schema,'repetitions')),(row['id'],)).fetchall()
        if [(r['seed'],r['fingerprint']) for r in actual]!=[(r['actual_seed'],r['output_fingerprint']) for r in expected]:
            raise ArtifactError('Retained simulation output differs from its PostgreSQL receipts')


def create(queue, state, destination, tools):
    from jasmine_web.batch.backup import frozen_database, table_inventory
    from psycopg import sql
    state=existing_directory(state)
    destination=Path(destination).absolute()
    parent=existing_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise ArtifactError('Choose a new backup destination; existing files are never replaced')
    with state_guard(state,exclusive=True), ExitStack() as stack:
        if (state/GATE).exists():
            raise ArtifactError('Activate or finish the previous restore before taking another backup')
        processor_guards(state,stack)
        releases=ReleaseRegistry(state).inventory()
        with frozen_database(queue) as (c,snapshot):
            version=tools.check(c)
            bound=c.execute(sql.SQL('SELECT identity FROM {} WHERE pool_id=%s').format(
                sql.Identifier(queue.schema,'executor_bindings')),(queue.pool_id,)).fetchone()
            if bound:
                root=existing_directory(state/'execution')
                info=root.stat()
                identity=bound['identity']
                if any(identity.get(key)!=value for key,value in dict(root=str(root),
                        host=Path('/etc/machine-id').read_text().strip(),device=info.st_dev,inode=info.st_ino).items()):
                    raise ArtifactError('Backup state does not match the queue executor binding')
            rows=c.execute(sql.SQL('''SELECT p.dataset_id,p.location,d.prepared_fingerprint,l.state
                FROM {} p JOIN {} d ON d.pool_id=p.pool_id AND d.id=p.dataset_id
                LEFT JOIN {} l ON l.pool_id=p.pool_id AND l.dataset_id=p.dataset_id
                ORDER BY p.dataset_id''').format(sql.Identifier(queue.schema,'prepared_locations'),
                    sql.Identifier(queue.schema,'datasets'),sql.Identifier(queue.schema,'dataset_lifecycle'))).fetchall()
            live=[r for r in rows if r['state']!='deleted']
            roots=source_roots(state,[r['location'] for r in live])
            all_sources=[state,*[Path(r['source']) for r in roots]]
            if any(destination.is_relative_to(p) or p.is_relative_to(destination) for p in all_sources):
                raise ArtifactError('Keep backups outside every source directory')
            # Validate registered inputs before publication, not just the bytes of
            # a possibly already damaged source. Deletion history stays in SQL.
            from .queue_adapter import read_prepared
            from .prepared_dataset import verify_snapshot
            for row in live:
                receipt=read_prepared(row['location'])
                if receipt['sha256']!=row['prepared_fingerprint']:
                    raise ArtifactError('A prepared dataset differs from its PostgreSQL receipt')
                verify_snapshot(row['location'],receipt)
            uploads=c.execute(sql.SQL("SELECT id,bytes,sha256 FROM {} WHERE state='ready'").format(
                sql.Identifier(queue.schema,'uploads'))).fetchall()
            for row in uploads:
                actual=file_hash(state,'uploads/'+row['id'])
                if (actual['bytes'],actual['sha256'])!=(row['bytes'],row['sha256']):
                    raise ArtifactError('An upload differs from its PostgreSQL receipt')
            check_outputs(c,queue,state)
            source_stats=[scan(state,exclude=EXCLUDE),*[scan(r['source']) for r in roots]]
            estimate=sum(info[3] for tree in source_stats for info in tree.values() if stat.S_ISREG(info[2]))
            if shutil.disk_usage(parent).free<estimate+RESERVE:
                raise ArtifactError('Insufficient free space for the complete private backup')
            staging=Path(tempfile.mkdtemp(prefix='.backup-incomplete-',dir=parent))
            try:
                print('Saving the offline PostgreSQL snapshot and verified private files.',file=sys.stderr,flush=True)
                database=table_inventory(c,queue)
                tools.dump(staging/'database.dump',schema=queue.schema,snapshot=snapshot)
                state_tree,_=copy_tree(state,staging/'state',exclude=EXCLUDE)
                (staging/'prepared').mkdir(mode=0o700)
                for root in roots:
                    root['tree'],_=copy_tree(Path(root['source']),staging/root['directory'])
                # A file changed after its individual copy is also a mismatch.
                if [scan(state,exclude=EXCLUDE),*[scan(r['source']) for r in roots]]!=source_stats:
                    raise ArtifactError('Source files changed during backup; no backup was published')
                images={r['image'] for r in releases.values()}
                images.update(r['model_digest'] for r in c.execute(sql.SQL('SELECT model_digest FROM {}').format(
                    sql.Identifier(queue.schema,'prepared_locations'))).fetchall())
                manifest=dict(format=FORMAT,id=uuid4().hex,created_at=utc(),pool_id=queue.pool_id,
                    schema=queue.schema,postgres_major=version,state_source=str(state),state_tree=state_tree,
                    prepared=roots,database=database,dump=file_hash(staging,'database.dump'),
                    images=sorted(images),default_release=next(iter(releases)),
                    release_ids=list(releases),omitted_caches=['download-cache','visualiser-cache'])
                training_imports(staging,manifest,state)
                write_attribution(staging); (staging/'COPYRIGHT.md').chmod(0o600)
                manifest['attribution']=file_hash(staging,'COPYRIGHT.md')
                atomic_json(staging/'manifest.json',manifest)
                verify(staging)
                sync_tree(staging)
                publish(staging,destination)
            finally:
                if staging.exists(): shutil.rmtree(staging)
    return summary(manifest,'backup')


def load_manifest(backup):
    backup=existing_directory(backup)
    value=read_metadata(backup/'manifest.json')
    fields={'format','id','created_at','pool_id','schema','postgres_major','state_source','state_tree',
        'prepared','database','dump','images','default_release','release_ids','omitted_caches','attribution'}
    if isinstance(value,dict) and value.get('format')==LIVE_FORMAT:
        fields |= {'recovery','vm'}
    if (type(value) is not dict or set(value)!=fields or value['format'] not in (FORMAT,LIVE_FORMAT) or
            not re.fullmatch('[a-f0-9]{32}',str(value['id'])) or
            not re.fullmatch('[a-z][a-z0-9_]{0,62}',str(value['schema'])) or
            not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',str(value['pool_id'])) or
            type(value['postgres_major']) is not int or value['postgres_major']<14 or
            type(value['database']) is not dict or type(value['prepared']) is not list or
            len(value['prepared'])>10000 or type(value['images']) is not list or not value['images'] or
            len(set(value['images']))!=len(value['images']) or
            any(not re.fullmatch('sha256:[a-f0-9]{64}',str(p)) for p in value['images']) or
            value['omitted_caches']!=['download-cache','visualiser-cache']):
        raise ArtifactError('Invalid or incompatible backup manifest')
    if value['format']==LIVE_FORMAT:
        from .backup_single import validate_manifest
        validate_manifest(value)
    absolute(value['state_source'])
    for i,record in enumerate(value['prepared']):
        if (type(record) is not dict or set(record)!={'source','directory','tree'} or
                record['directory']!=f'prepared/{i:04d}'):
            raise ArtifactError('Invalid prepared backup inventory')
        absolute(record['source'])
    roots=[Path(value['state_source']),*[Path(r['source']) for r in value['prepared']]]
    if any(a==b or a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(roots) for b in roots[i+1:]):
        raise ArtifactError('Backup source directories overlap')
    validate_database(value['database'])
    return value


def validate_database(database):
    if not isinstance(database,dict) or not database or len(database)>1000:
        raise ArtifactError('Invalid database verification inventory')
    for table,entry in database.items():
        if (not re.fullmatch('[a-z][a-z0-9_]{0,62}',table) or type(entry) is not dict or
                set(entry)!={'rows','sha256'} or type(entry['rows']) is not int or entry['rows']<0 or
                not re.fullmatch('[a-f0-9]{64}',str(entry['sha256']))):
            raise ArtifactError('Invalid database verification inventory')


def verify(backup):
    backup=existing_directory(backup)
    manifest=load_manifest(backup)
    if set(p.name for p in backup.iterdir())!={'manifest.json','database.dump','state','prepared','COPYRIGHT.md'}:
        raise ArtifactError('Backup contains unexpected or missing files')
    if (file_hash(backup,'database.dump')!=manifest['dump'] or
            file_hash(backup,'COPYRIGHT.md')!=manifest['attribution']):
        raise ArtifactError('Backup dump or attribution checksum differs')
    check_tree(backup/'state',manifest['state_tree'])
    existing_directory(backup/'prepared')
    if sorted(p.name for p in (backup/'prepared').iterdir())!=[f'{i:04d}' for i in range(len(manifest['prepared']))]:
        raise ArtifactError('Prepared backup inventory differs')
    for root in manifest['prepared']: check_tree(backup/root['directory'],root['tree'])
    retained=ReleaseRegistry(backup/'state').inventory()
    if list(retained)!=manifest['release_ids'] or next(iter(retained))!=manifest['default_release']:
        raise ArtifactError('Retained release inventory differs')
    return manifest


def mappings(manifest, target):
    return [(Path(manifest['state_source']),target),*[(Path(p['source']),target/import_directory(manifest)/f'{i:04d}')
                                                   for i,p in enumerate(manifest['prepared'])]]


def import_directory(manifest):
    return '.restored-inputs-'+manifest['id']


def relocate(value, mapping):
    path=absolute(value)
    # Restored external inputs live below the restored state root. Inverting
    # that mapping must choose their specific directory before the state root.
    for old,new in sorted(mapping,key=lambda pair:len(pair[0].parts),reverse=True):
        if path.is_relative_to(old): return str(new/path.relative_to(old))
    return value


def training_imports(backup, manifest, target):
    if 'training-imports.json' not in manifest['state_tree']['files']: return None
    value=read_metadata(Path(backup)/'state'/'training-imports.json')
    if type(value) is not list or any(not isinstance(v,str) for v in value):
        raise ArtifactError('Invalid retained training import list')
    mapping=mappings(manifest,target)
    if any(not any(absolute(v).is_relative_to(old) for old,_ in mapping) for v in value):
        raise ArtifactError('Training imports must be included in the registered prepared backup inventory')
    return canonical_bytes([relocate(v,mapping) for v in value])


def check_restored_files(backup, manifest, target):
    expected=deepcopy(manifest['state_tree'])
    imports=training_imports(backup,manifest,target)
    if imports is not None: expected['files']['training-imports.json']=hash_bytes(imports)
    exclude=[LOCK,GATE]
    if manifest['prepared']: exclude.append(import_directory(manifest))
    check_tree(target,expected,exclude=exclude)
    if manifest['prepared']:
        root=existing_directory(target/import_directory(manifest))
        if sorted(p.name for p in root.iterdir())!=[f'{i:04d}' for i in range(len(manifest['prepared']))]:
            raise ArtifactError('Restored prepared inventory differs')
        for i,record in enumerate(manifest['prepared']): check_tree(root/f'{i:04d}',record['tree'])


def restored_database(c, queue, manifest, target):
    from jasmine_web.batch.backup import check_schema, require_idle, table_inventory
    from psycopg import sql
    check_schema(c,queue)
    live=manifest['format']==LIVE_FORMAT
    if not live: require_idle(c,queue)
    rows=c.execute(sql.SQL('SELECT location FROM {}').format(sql.Identifier(queue.schema,'prepared_locations'))).fetchall()
    mapping=[(new,old) for old,new in mappings(manifest,target)]
    inverse={row['location']:relocate(row['location'],mapping) for row in rows}
    result=table_inventory(c,queue,original_locations=inverse)
    if live:
        from types import SimpleNamespace
        expected=load_gate(target)['recovery_database']['batch']
        vm={s:table_inventory(c,SimpleNamespace(schema=s)) for s in manifest['vm']}
        if vm!=load_gate(target)['recovery_database']['vm']:
            raise ArtifactError('Restored SingleRun database differs from its approved recovery conversion')
    else:
        expected=deepcopy(manifest['database'])
        expected['executor_bindings']=dict(rows=0,sha256=hashlib.sha256(b'').hexdigest())
    if result!=expected:
        raise ArtifactError('Restored database differs from the backup beyond the approved path and executor relocation')
    return result


def database_identity(c):
    # An operator restore role needs access to the cluster identifier. This binds
    # resumable restoration to a particular empty database, not just its name.
    row=c.execute('SELECT system_identifier::text AS cluster FROM pg_control_system()').fetchone()
    db=c.execute('SELECT oid::text AS oid FROM pg_database WHERE datname=current_database()').fetchone()
    return hashlib.sha256((row['cluster']+':'+db['oid']).encode()).hexdigest()


def load_gate(target):
    gate=read_metadata(target/GATE)
    expected={'format','backup_sha256','database','phase','created_at'}
    if type(gate) is not dict:raise ArtifactError('Invalid inactive restore marker')
    if gate.get('phase')=='verified':expected.add('verified_at')
    if 'recovery_database' in gate: expected.add('recovery_database')
    if (set(gate)!=expected or gate.get('format')!=GATE_FORMAT or
            gate.get('phase') not in ('copying-files','files-copied','verified') or
            any(not re.fullmatch('[a-f0-9]{64}',str(gate.get(k))) for k in ('backup_sha256','database'))):
        raise ArtifactError('Invalid inactive restore marker')
    return gate


def restore(queue, backup, target, tools, *, resume=False, after_database=None):
    from jasmine_web.batch.backup import database_guard, empty_database, table_inventory
    from psycopg import sql
    backup=existing_directory(backup); manifest=verify(backup)
    if (queue.pool_id,queue.schema)!=(manifest['pool_id'],manifest['schema']):
        raise ArtifactError('Restore must use the backup pool and schema identifiers')
    target=Path(target).absolute()
    existing_directory(target.parent)
    original_roots=[Path(manifest['state_source']),*[Path(p['source']) for p in manifest['prepared']]]
    if (target.is_relative_to(backup) or backup.is_relative_to(target) or
            any(target.is_relative_to(p) or p.is_relative_to(target) for p in original_roots)):
        raise ArtifactError('Restore into a separate new state directory')
    marker_digest=file_hash(backup,'manifest.json')['sha256']
    with database_guard(queue,exclusive=True) as c:
        cluster=database_identity(c)
        if tools.check(c)!=manifest['postgres_major']:
            raise ArtifactError('Restore using the backup PostgreSQL major version')
        if target.exists() or target.is_symlink():
            if not resume:
                raise ArtifactError('Choose a new restore directory; existing state is never replaced')
            gate=load_gate(target)
            if (gate['backup_sha256'],gate['database'])!=(marker_digest,cluster):
                raise ArtifactError('Restore marker does not match this backup and target database')
        else:
            if resume: raise ArtifactError('There is no interrupted restore to resume')
            empty_database(c)
            target.mkdir(mode=0o700)
            gate=dict(format=GATE_FORMAT,backup_sha256=marker_digest,database=cluster,phase='copying-files',created_at=utc())
            atomic_json(target/GATE,gate)
        with state_guard(target,exclusive=True):
            if gate['phase']=='copying-files':
                # Only a directory with our matching inactive marker may be
                # resumed. Recopy its incomplete payload, never a live service.
                for entry in target.iterdir():
                    if entry.name in (LOCK,GATE): continue
                    if entry.is_symlink(): raise ArtifactError('Linked incomplete restore storage')
                    if entry.is_dir(): shutil.rmtree(entry)
                    else: entry.unlink()
                temporary=target/'.copying-state'
                copy_tree(backup/'state',temporary)
                for entry in temporary.iterdir(): os.rename(entry,target/entry.name)
                temporary.rmdir()
                if manifest['prepared']:
                    (target/import_directory(manifest)).mkdir(mode=0o700)
                    for i,record in enumerate(manifest['prepared']):
                        copy_tree(backup/record['directory'],target/import_directory(manifest)/f'{i:04d}')
                imports=training_imports(backup,manifest,target)
                if imports is not None:
                    fd=os.open(target/'training-imports.json',os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW)
                    with os.fdopen(fd,'wb') as stream:
                        stream.write(imports); stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(),0o600)
                gate['phase']='files-copied'; atomic_json(target/GATE,gate)
            check_restored_files(backup,manifest,target)
            present=c.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(queue.schema,)).fetchone()
            if not present:
                empty_database(c)
                tools.restore(backup/'database.dump')
            if after_database: after_database()
            # Single-transaction pg_restore is all-or-nothing. On a crash after
            # its commit, accept only the exact snapshot or its verified relocation.
            before=table_inventory(c,queue)
            if before==manifest['database']:
                live=manifest['format']==LIVE_FORMAT
                if live:
                    from types import SimpleNamespace
                    from .backup_live import recovery_inventory, recover_database
                    if {s:table_inventory(c,SimpleNamespace(schema=s)) for s in manifest['vm']} != {s:v['database'] for s,v in manifest['vm'].items()}:
                        raise ArtifactError('SingleRun database differs from the dumped recovery snapshot')
                mapping=mappings(manifest,target)
                with c.transaction():
                    if live: recover_database(c,queue,manifest,target)
                    rows=c.execute(sql.SQL('SELECT dataset_id,location FROM {}').format(
                        sql.Identifier(queue.schema,'prepared_locations'))).fetchall()
                    for row in rows:
                        c.execute(sql.SQL('UPDATE {} SET location=%s WHERE dataset_id=%s').format(
                            sql.Identifier(queue.schema,'prepared_locations')),
                            (relocate(row['location'],mapping),row['dataset_id']))
                    # There is no active work or held capacity. The next worker
                    # binds this new host/root before serving or claiming jobs.
                    c.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(queue.schema,'executor_bindings')))
                    if live:
                        # Save the verification journal before the transaction
                        # commits. On crash, either the exact dump remains or
                        # this exact converted transaction has committed.
                        gate['recovery_database']=recovery_inventory(c,queue,manifest,target)
                        atomic_json(target/GATE,gate)
            restored_database(c,queue,manifest,target)
            ReleaseRegistry(target).inventory()
            gate['phase']='verified'; gate['verified_at']=utc(); atomic_json(target/GATE,gate)
            sync_tree(target)
    return summary(manifest,'restore',inactive=True)


def activate(queue, backup, target, *, image_check=installed_image, source_isolated=False):
    from jasmine_web.batch.backup import frozen_database
    target=existing_directory(target); backup=existing_directory(backup)
    manifest=verify(backup)
    live=manifest['format']==LIVE_FORMAT
    if live and not source_isolated:
        raise ArtifactError('Confirm source isolation with --source-isolated before activating an online recovery point')
    with ExitStack() as stack:
        if live:
            # If the old state still exists on this host, also check its service
            # mutex. Isolation on a destroyed/different host remains an explicit
            # operator decision; a flag cannot stop remote source containers.
            source=Path(manifest['state_source'])
            if source.exists(): stack.enter_context(state_guard(source,exclusive=True))
        stack.enter_context(state_guard(target,exclusive=True))
        c,_=stack.enter_context(frozen_database(queue,idle=not live))
        gate=load_gate(target)
        if (gate.get('format')!=GATE_FORMAT or gate.get('phase')!='verified' or
                gate.get('backup_sha256')!=file_hash(backup,'manifest.json')['sha256'] or
                gate.get('database')!=database_identity(c) or
                (queue.pool_id,queue.schema)!=(manifest['pool_id'],manifest['schema'])):
            raise ArtifactError('Complete and verify restoration before activation')
        check_restored_files(backup,manifest,target)
        restored_database(c,queue,manifest,target)
        for image in manifest['images']: image_check(image)
        (target/GATE).unlink()
        sync_tree(target)
    return summary(manifest,'activate',inactive=False)


def summary(manifest, operation, **extra):
    files=[*manifest['state_tree']['files'].values(),*[f for p in manifest['prepared'] for f in p['tree']['files'].values()]]
    return dict(format=manifest['format'],operation=operation,passed=True,backup_id=manifest['id'],
        files=len(files),file_bytes=sum(f['bytes'] for f in files),tables=len(manifest['database']),
        rows=sum(t['rows'] for t in manifest['database'].values()),**extra)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('create','verify','restore','activate'))
    parser.add_argument('--backup',type=Path,required=True,help='New private backup directory for create; existing backup otherwise')
    parser.add_argument('--frontend',type=Path)
    parser.add_argument('--config',type=Path,help='Native VM TOML for the source or isolated restore target')
    parser.add_argument('--state',type=Path)
    parser.add_argument('--dsn-file',type=Path)
    parser.add_argument('--pool')
    parser.add_argument('--schema')
    parser.add_argument('--client-container',help='Use native PostgreSQL clients inside the selected loopback database container')
    parser.add_argument('--resume',action='store_true',help='Resume only a matching inactive, interrupted restore')
    parser.add_argument('--online',action='store_true',help='Create a recovery point while simulations continue')
    parser.add_argument('--minimum-free-gib',type=int,default=1,help='Free-space reserve for online capture; use a separate backup filesystem')
    parser.add_argument('--source-isolated',action='store_true',help='Operator confirms original services/models cannot execute after recovery')
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args(argv)
    try:
        os.umask(0o077)
        if args.command=='verify':
            result=summary(verify(args.backup),'verify')
        else:
            if args.config:
                from .vm_config import load_config
                options=load_config(args.config)
                if any((args.state,args.dsn_file,args.frontend,args.pool,args.schema)):
                    raise ArtifactError('Use VM configuration or explicit laptop settings, not both')
                state,frontend,pool,schema=options.state,options.frontend,options.pool_id,'jasmine_batch'
                from .vm_web import database_dsn
                dsn=database_dsn(options)
            else:
                from deploy._workflow import frontend_path
                state=(args.state or Path.home()/'simpaths-multirun-local').absolute()
                frontend=args.frontend or frontend_path()
                pool,schema=args.pool or 'simpaths-local',args.schema or 'jasmine_batch'
            sys.path.insert(0,str(frontend.resolve(strict=True)))
            if not args.config:
                if args.dsn_file:
                    from .vm_web import private_text
                    dsn=private_text(args.dsn_file)
                elif args.command=='restore':
                    raise ArtifactError('Provide the separate empty restore database in a private DSN file')
                else:
                    from jasmine_web.batch.local_database import existing_local_postgres
                    dsn=existing_local_postgres(state)
                    if not args.client_container and not shutil.which('pg_dump'):
                        args.client_container='jasmine-multirun-local-'+hashlib.sha256(str(state).encode()).hexdigest()[:12]
            from jasmine_web.batch.store import Queue
            queue=Queue(dsn,pool,schema=schema)
            if args.command=='activate': result=activate(queue,args.backup,state,source_isolated=args.source_isolated)
            else:
                tools=PostgresTools(dsn,container=args.client_container)
                creator=create
                if args.online:
                    if args.command!='create': raise ArtifactError('--online is only a backup creation option')
                    from .backup_live import create as creator
                options=dict(minimum_free_gib=args.minimum_free_gib) if args.online else {}
                result=creator(queue,state,args.backup,tools,**options) if args.command=='create' else restore(
                    queue,args.backup,state,tools,resume=args.resume)
        if args.json: print(json.dumps(result,sort_keys=True))
        else:
            print('Verified '+result['operation']+': '+str(result['files'])+' file(s), '+str(result['tables'])+' table(s).')
            if result.get('inactive'): print('Restored state remains inactive. Activate only after the original service and its containers are isolated.')
        return 0
    except Exception as error:
        message=str(error) if isinstance(error,ArtifactError) else 'Backup operation could not finish. The destination remains inactive; inspect private operator configuration.'
        result=dict(format=FORMAT,operation=args.command,passed=False,error=message)
        if args.json: print(json.dumps(result,sort_keys=True))
        else: print(message,file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
