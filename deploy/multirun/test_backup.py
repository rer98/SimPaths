"""(C) Copyright 2026, by Ross Richardson

Private-file backup safety and disposable native PostgreSQL restore acceptance.
Uses fictional inputs/CSVs; scientific models and real mail are never launched.
@author ross richardson
"""
from contextlib import redirect_stderr
from copy import deepcopy
import asyncio
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
sys.path.insert(0,str(frontend_path()/'tests'/'batch'))
from . import backup, backup_files
from .artifacts import ArtifactError, digest, fingerprint, inventory, write_attribution, write_json
from .backup_postgres import PostgresTools
from .maintenance import GATE, LOCK, service_state, state_guard
from .releases import ReleaseRegistry, atomic_json

IMAGE='sha256:'+'a'*64


class BackupFileTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.state=self.root/'state'; self.state.mkdir(mode=0o700)
        jar=self.root/'model.jar'; jar.write_bytes(b'fictional model')
        defaults=self.root/'defaults'; defaults.mkdir()
        (defaults/'scenario_CPI.xlsx').write_bytes(b'fictional workbook')
        self.release=ReleaseRegistry(self.state).register(image=IMAGE,name='Retained test release',jar=jar,defaults=defaults)

    def build_backup(self):
        output=self.root/'backup'; output.mkdir(mode=0o700)
        tree,_=backup_files.copy_tree(self.state,output/'state',exclude=backup.EXCLUDE)
        (output/'prepared').mkdir(mode=0o700)
        (output/'database.dump').write_bytes(b'fictional dump'); (output/'database.dump').chmod(0o600)
        write_attribution(output); (output/'COPYRIGHT.md').chmod(0o600)
        value=dict(format=backup.FORMAT,id=uuid4().hex,created_at=backup.utc(),pool_id='test',schema='test_backup',
            postgres_major=17,state_source=str(self.state),state_tree=tree,prepared=[],
            database={'schema_version':dict(rows=20,sha256='a'*64)},
            dump=backup_files.file_hash(output,'database.dump'),images=[IMAGE],default_release=self.release,
            release_ids=[self.release],omitted_caches=['download-cache','visualiser-cache'],
            attribution=backup_files.file_hash(output,'COPYRIGHT.md'))
        atomic_json(output/'manifest.json',value)
        return output,value

    def test_copy_preserves_bytes_empty_directories_and_owner_executable_bits(self):
        source=self.root/'source'; source.mkdir(mode=0o700)
        (source/'empty').mkdir(); (source/'long filename.txt').write_bytes(b'private fictional bytes')
        (source/'run.sh').write_bytes(b'#!/bin/sh\nexit 0\n'); (source/'run.sh').chmod(0o755)
        target=self.root/'copy'
        tree,_=backup_files.copy_tree(source,target)
        backup_files.check_tree(target,tree)
        self.assertEqual((target/'run.sh').stat().st_mode&0o777,0o700)
        self.assertEqual((target/'long filename.txt').stat().st_mode&0o777,0o600)
        self.assertTrue((target/'empty').is_dir())

    def test_links_hardlinks_and_special_files_are_rejected_without_reading_their_targets(self):
        source=self.root/'source'; source.mkdir(mode=0o700)
        secret=self.root/'secret'; secret.write_bytes(b'unrelated')
        for kind in ('symlink','hardlink','fifo','linked_directory'):
            path=source/'unsafe'
            if kind=='symlink':path.symlink_to(secret)
            elif kind=='hardlink':os.link(secret,path)
            elif kind=='fifo':os.mkfifo(path)
            else:path.symlink_to(self.root,target_is_directory=True)
            with self.subTest(kind=kind),self.assertRaises(ArtifactError):backup_files.scan(source)
            path.unlink()
        self.assertEqual(secret.read_bytes(),b'unrelated')

    def test_changed_file_is_rejected_even_after_its_copy_finished(self):
        source=self.root/'source'; source.mkdir(mode=0o700)
        (source/'a').write_bytes(b'old'); (source/'b').write_bytes(b'old')
        real=backup_files.file_hash
        def copied(root,key,**kwargs):
            result=real(root,key,**kwargs)
            if key=='b':(source/'a').write_bytes(b'new')
            return result
        with patch.object(backup_files,'file_hash',side_effect=copied),self.assertRaises(ArtifactError):
            backup_files.copy_tree(source,self.root/'target')

    def test_verify_rejects_corruption_missing_files_and_unlisted_files(self):
        output,value=self.build_backup()
        self.assertEqual(backup.verify(output)['id'],value['id'])
        original=(output/'database.dump').read_bytes()
        (output/'database.dump').write_bytes(b'changed')
        with self.assertRaises(ArtifactError):backup.verify(output)
        (output/'database.dump').write_bytes(original)
        (output/'state/unlisted').write_bytes(b'extra')
        with self.assertRaises(ArtifactError):backup.verify(output)
        (output/'state/unlisted').unlink()
        (output/'state'/f'releases/{self.release}/model.jar').unlink()
        with self.assertRaises(ArtifactError):backup.verify(output)

    def test_manifest_traversal_duplicate_fields_and_nonfinite_values_fail_closed(self):
        output,value=self.build_backup()
        original=(output/'manifest.json').read_bytes()
        for text in ('{"format":"first","format":"second"}','{"format":NaN}'):
            (output/'manifest.json').write_text(text)
            with self.assertRaises(ArtifactError):backup.verify(output)
        (output/'manifest.json').write_bytes(original)
        tree=deepcopy(value['state_tree']); tree['files']['../outside']=dict(bytes=0,sha256='a'*64,mode=0o600)
        with self.assertRaises(ArtifactError):backup_files.check_tree(output/'state',tree)

    def test_file_verification_checks_counts_mode_and_checksum(self):
        output,value=self.build_backup()
        jar=output/'state'/f'releases/{self.release}/model.jar'
        jar.chmod(0o600)
        with self.assertRaises(ArtifactError):backup.verify(output)

    def test_atomic_publication_cannot_replace_an_existing_empty_directory(self):
        source=self.root/'pending'; source.mkdir(mode=0o700); (source/'data').write_bytes(b'new')
        target=self.root/'target'; target.mkdir(mode=0o700)
        with self.assertRaises(OSError):backup_files.publish(source,target)
        self.assertEqual(list(target.iterdir()),[])
        self.assertEqual((source/'data').read_bytes(),b'new')

    def test_state_lock_excludes_launchers_and_release_writes_and_rejects_linked_locks(self):
        with state_guard(self.state,exclusive=True):
            with self.assertRaises(ArtifactError):
                with service_state(self.state):pass
            with self.assertRaises(ArtifactError):ReleaseRegistry(self.state).select(self.release)
        (self.state/LOCK).unlink(); (self.state/LOCK).symlink_to(self.root/'outside')
        with self.assertRaises(OSError):
            with state_guard(self.state):pass
        self.assertFalse((self.root/'outside').exists())

    def test_inactive_restore_blocks_normal_start_and_default_selection(self):
        atomic_json(self.state/GATE,{'phase':'verified'})
        with self.assertRaises(ArtifactError):
            with service_state(self.state):pass
        with self.assertRaises(ArtifactError):ReleaseRegistry(self.state).select(self.release)
        self.assertTrue((self.state/GATE).exists())

    def test_cache_omission_preserves_simulation_output_and_session_secret(self):
        (self.state/'session-secret').write_bytes(b's'*64)
        for key in ('execution/download-cache/archive.zip','execution/visualiser-cache/private/raw.csv','execution/batch-test/work/output/run_1/csv/Person.csv'):
            path=self.state/key; path.parent.mkdir(mode=0o700,parents=True,exist_ok=True); path.write_bytes(b'fictional')
        output,_=self.build_backup()
        backup.verify(output)
        self.assertFalse((output/'state/execution/download-cache').exists())
        self.assertFalse((output/'state/execution/visualiser-cache').exists())
        self.assertTrue((output/'state/execution/batch-test/work/output/run_1/csv/Person.csv').exists())
        self.assertEqual((output/'state/session-secret').read_bytes(),b's'*64)

    def test_source_mapping_is_explicit_deduplicated_and_never_rewrites_arbitrary_text(self):
        prepared=self.root/'prepared'; prepared.mkdir(mode=0o700)
        roots=backup.source_roots(self.state,[str(prepared),str(prepared),str(self.state/'artifacts')])
        self.assertEqual(len(roots),1)
        nested=prepared/'nested'; nested.mkdir(mode=0o700)
        with self.assertRaises(ArtifactError):backup.source_roots(self.state,[str(prepared),str(nested)])
        target=self.root/'restored'
        mapping=[(self.state,target)]
        self.assertEqual(backup.relocate(str(self.state/'artifacts/data'),mapping),str(target/'artifacts/data'))
        self.assertEqual(backup.relocate(str(self.root/'unrelated'),mapping),str(self.root/'unrelated'))
        manifest=dict(id='f'*32,state_source=str(self.state),prepared=roots)
        mapping=backup.mappings(manifest,target)
        restored_input=target/backup.import_directory(manifest)/'0000'
        self.assertEqual(backup.relocate(str(prepared),mapping),str(restored_input))
        inverse=[(new,old) for old,new in mapping]
        # The target's external-input copy is also beneath the target state root.
        # Reversing it must restore its original external path, not a state path.
        self.assertEqual(backup.relocate(str(restored_input/'input/input.mv.db'),inverse),
                         str(prepared/'input/input.mv.db'))
        self.assertEqual(backup.relocate(str(target/'artifacts/data'),inverse),
                         str(self.state/'artifacts/data'))

    def test_cli_failure_is_machine_readable_and_hides_private_paths(self):
        stdout=io.StringIO()
        with patch.object(backup,'verify',side_effect=RuntimeError('secret password private-path')),patch('sys.stdout',stdout):
            result=backup.main(['verify','--backup','/private/fictional','--json'])
        self.assertEqual(result,2)
        value=json.loads(stdout.getvalue()); self.assertFalse(value['passed'])
        self.assertNotIn('password',stdout.getvalue()); self.assertNotIn('private-path',stdout.getvalue())


def test_tools(dsn):
    """Only the explicitly disposable PostgreSQL container is eligible in proofs."""
    if shutil.which('pg_dump') and shutil.which('pg_restore'):return PostgresTools(dsn)
    names=subprocess.check_output(['docker','--host','unix:///var/run/docker.sock','ps','--filter',
        'label=jasmine.queue-test=true','--format','{{.Names}}'],text=True).splitlines()
    for name in names:
        try:return PostgresTools(dsn,container=name)
        except ArtifactError:pass
    raise AssertionError('Matching disposable PostgreSQL client container not found')


DSN=os.environ.get('JASMINE_BATCH_TEST_DSN')


@unittest.skipUnless(DSN,'Use backup_proof.py with disposable PostgreSQL')
class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        import test_postgres
        from jasmine_web.batch.access import Access
        from jasmine_web.batch.datasets import Datasets
        from jasmine_web.batch.local_executor import LocalExecutor
        from jasmine_web.batch.preparation import Preparations
        from jasmine_web.batch.policy import Resources
        from .prepare_training import RECEIPT_VERSION
        from .test_configuration import document
        from .configuration import normalise
        self.f=test_postgres.PostgresQueueTests(); self.addCleanup(self.f.doCleanups); self.f.setUp()
        self.q=self.f.q
        temporary=tempfile.TemporaryDirectory(prefix='backup-proof-'); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.state=self.root/'source'; self.state.mkdir(mode=0o700)
        jar=self.root/'model.jar'; jar.write_bytes(b'fictional model')
        defaults=self.root/'defaults'; defaults.mkdir(); (defaults/'scenario_CPI.xlsx').write_bytes(b'fictional workbook')
        self.release=ReleaseRegistry(self.state).register(image=IMAGE,name='Retained test release',jar=jar,defaults=defaults)
        self.secret='fictional-stable-session-secret-'*3
        (self.state/'session-secret').write_text(self.secret); (self.state/'session-secret').chmod(0o600)
        self.mail={}
        async def deliver(email,code):self.mail[email]=code
        self.deliver=deliver
        self.access=Access(self.q,self.secret,deliver)
        self.owner=self.access.approve_email('alice@example.org')
        self.other=self.access.approve_email('bob@example.org')
        self.datasets=Datasets(self.q,self.state/'uploads',reserve_bytes=1)
        upload=self.datasets.receive(self.owner,'population.csv',io.BytesIO(b'fictional input'),expected_bytes=15)
        self.prepared=self.root/'prepared'; self.prepared.mkdir(mode=0o700)
        (self.prepared/'model.jar').write_bytes(b'fictional model'); (self.prepared/'input').mkdir(mode=0o700)
        (self.prepared/'input/input.mv.db').write_bytes(b'fictional database')
        identity=dict(format=RECEIPT_VERSION,source='bundled-public-training',country='UK',start_year=2019,
            model=fingerprint(self.prepared/'model.jar'),prepared=inventory(self.prepared/'input'))
        self.receipt=dict(identity=identity,sha256=digest(identity))
        write_json(self.prepared/'receipt.json',self.receipt)
        self.dataset=self.datasets.publish(self.owner,self.receipt['sha256'],uploads=[upload])
        self.provider=self.datasets.register_provider(self.receipt['sha256']); self.q.grant_dataset(self.owner,self.provider)
        prep=Preparations(self.datasets)
        for key in (self.dataset,self.provider):prep.register_location(key,location=str(self.prepared),image=IMAGE)
        atomic_json(self.state/'training-imports.json',[str(self.prepared)])
        self.executor=LocalExecutor(self.state/'execution'); self.q.bind_executor(self.executor.identity())
        draft=document(); draft.update(model_release=self.release,dataset_revision=self.dataset)
        draft['common'].update(end_year=2020,population=2000)
        draft['seed_plan']['repetitions']=1
        self.configuration=normalise(draft)
        run=dict(id='baseline',parameters=self.configuration.editable_configuration())
        self.exp=self.q.submit(self.owner,uuid4().hex,label='Fictional restored comparison',model_digest=IMAGE,
            dataset_id=self.dataset,seed_plan=['606'],run_sets=[run],resources=Resources(2000,4000,8000))
        self.completed=self.finish()
        draft['dataset_revision']=self.provider
        config=normalise(draft)
        self.provider_exp=self.q.submit(self.owner,uuid4().hex,label='Restricted provider result',model_digest=IMAGE,
            dataset_id=self.provider,seed_plan=['606'],run_sets=[dict(id='baseline',parameters=config.editable_configuration())],
            resources=Resources(2000,4000,8000))
        self.provider_completed=self.finish()
        self.queued=self.q.submit(self.owner,uuid4().hex,label='Unstarted frozen configuration',model_digest=IMAGE,
            dataset_id=self.dataset,seed_plan=['606'],run_sets=[run],resources=Resources(2000,4000,8000))
        # Deadline/outbox values are restored verbatim; no restore-time renewal.
        self.f.sql("INSERT INTO artifact_retention(pool_id,kind,artifact_id,owner_id,basis_at,expires_at,updated_at) "
            "VALUES (%s,'output',%s,%s,clock_timestamp(),clock_timestamp()+interval '7 days',clock_timestamp())",
            (self.q.pool_id,str(self.completed.attempt_id),self.owner))
        self.f.sql("INSERT INTO problem_notifications(id,pool_id,incident_key,kind,owner_id,opened_at,retry_at,context) "
            "VALUES (%s,%s,'fictional-incident','upload_allowance',%s,clock_timestamp(),clock_timestamp(),'{\"private\":\"fictional\"}')",
            (uuid4(),self.q.pool_id,self.owner))
        self.tools=test_tools(self.q.dsn)
        self.backup=self.root/'backup'
        self.target=self.root/'restored'
        self.target_q,self.target_tools=self.new_database()

    def new_database(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from jasmine_web.batch.store import Queue
        database='restore_'+uuid4().hex
        with psycopg.connect(DSN,autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        def remove():
            with psycopg.connect(DSN,autocommit=True) as c:
                c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
        self.addCleanup(remove)
        dsn=make_conninfo(DSN,dbname=database)
        return Queue(dsn,self.q.pool_id,schema=self.q.schema),test_tools(dsn)

    def finish(self):
        from jasmine_web.batch.local_executor import atomic_json
        from .configuration import normalise
        from .queue_adapter import OPTIONS_NOT_EXPORTED, result_catalogue
        lease=self.q.claim('fictional-writer'); self.assertIsNotNone(lease); self.q.started(lease)
        work=self.executor.workspace(lease)/'work'; work.mkdir(mode=0o700,parents=True)
        atomic_json(work.parent/'identity.json',self.executor._identity(lease))
        run=lease.specification['run_sets'][0]; config=normalise(run['parameters'])
        native=config.native_configuration(run['id'])
        fields=dict(country='UK',startYear=2019,endYear=2020,popSize=2000,randomSeedIfFixed='606',**native['model_args'])
        fields={k:v for k,v in fields.items() if k not in OPTIONS_NOT_EXPORTED}
        directory=work/'output/run_1'; (directory/'input').mkdir(mode=0o700,parents=True); (directory/'csv').mkdir(mode=0o700)
        (directory/'input/options.txt').write_text('\n'.join(f'{k}: {str(v).lower() if isinstance(v,bool) else v}' for k,v in fields.items()))
        for name in ('Person.csv','BenefitUnit.csv'):
            (directory/'csv'/name).write_text('run,time,id,value\nrun-606,2019,1,2\nrun-606,2020,1,3\n')
        for ordinal,row in enumerate(result_catalogue(lease,work)):
            self.q.record_repetition(lease,ordinal,row['seed'],row['fingerprint'])
        self.q.finish(lease,outcome='success',stop_evidence='f'*64)
        return lease

    def save(self):
        with redirect_stderr(io.StringIO()):return backup.create(self.q,self.state,self.backup,self.tools)

    def restore(self, **options):
        return backup.restore(self.target_q,self.backup,self.target,self.target_tools,**options)

    def activate(self):
        return backup.activate(self.target_q,self.backup,self.target,image_check=lambda image:None)

    def test_real_dump_restore_verifies_all_rows_files_external_deduplication_and_preserved_deadlines(self):
        before=self.f.sql('SELECT * FROM jobs ORDER BY id')
        result=self.save(); manifest=backup.verify(self.backup)
        self.assertEqual(len(manifest['prepared']),1)
        self.assertGreater(result['tables'],20)
        restored=self.restore(); self.assertTrue(restored['inactive'])
        self.assertEqual((self.target/'session-secret').read_text(),self.secret)
        with self.target_q._connection() as c:
            self.assertEqual(c.execute('SELECT * FROM jobs ORDER BY id').fetchall(),before)
            self.assertEqual(c.execute('SELECT sent_at FROM problem_notifications').fetchone()['sent_at'],None)
            backup.restored_database(c,self.target_q,manifest,self.target)
        self.assertTrue((self.target/GATE).exists()); self.activate(); self.assertFalse((self.target/GATE).exists())

    def test_interruption_after_dump_commit_resumes_without_reimport_or_model_attempts(self):
        self.save()
        def interrupted():raise RuntimeError('fictional interruption')
        with self.assertRaises(RuntimeError):self.restore(after_database=interrupted)
        self.assertTrue((self.target/GATE).exists())
        with patch.object(self.target_tools,'restore',side_effect=AssertionError('Must not import twice')):
            self.restore(resume=True)
        with self.target_q._connection() as c:
            self.assertEqual(c.execute('SELECT count(*) AS n FROM attempts').fetchone()['n'],2)

    def test_corrupt_backup_cannot_create_target_or_change_existing_source_database(self):
        self.save(); (self.backup/'database.dump').write_bytes(b'corrupt')
        with self.assertRaises(ArtifactError):self.restore()
        self.assertFalse(self.target.exists())
        self.assertEqual(len(self.f.sql('SELECT * FROM attempts')),2)

    def test_existing_state_and_nonempty_database_are_never_overwritten(self):
        self.save(); self.target.mkdir(mode=0o700); (self.target/'unrelated').write_bytes(b'keep')
        with self.assertRaises(ArtifactError):self.restore()
        self.assertEqual((self.target/'unrelated').read_bytes(),b'keep')
        shutil.rmtree(self.target)
        with self.target_q._connection() as c:c.execute('CREATE TABLE public.unrelated (value text)')
        with self.assertRaises(Exception):self.restore()
        self.assertFalse(self.target.exists())

    def test_active_work_and_live_launcher_cannot_be_backed_up_and_no_attempt_is_cancelled(self):
        with service_state(self.state),self.assertRaises(ArtifactError):self.save()
        lease=self.q.claim('still-running')
        with self.assertRaises(Exception):self.save()
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.f.sql('SELECT phase FROM attempts WHERE id=%s',(lease.attempt_id,))[0]['phase'],'reserved')

    def test_activation_checks_images_and_both_file_and_database_integrity(self):
        self.save(); self.restore()
        def missing(_):raise ArtifactError('Required pinned image is unavailable')
        with self.assertRaises(ArtifactError):backup.activate(self.target_q,self.backup,self.target,image_check=missing)
        self.assertTrue((self.target/GATE).exists())
        with self.target_q._connection() as c:c.execute("UPDATE jobs SET auto_retry=false WHERE state='queued'")
        with self.assertRaises(ArtifactError):self.activate()
        self.assertTrue((self.target/GATE).exists())

    def test_queued_configuration_claims_once_after_activation_with_original_frozen_specification_and_seed(self):
        self.save(); self.restore(); self.activate()
        with self.target_q._connection() as c:before=c.execute("SELECT * FROM jobs WHERE state='queued'").fetchone()
        lease=self.target_q.claim('restored-worker'); self.assertIsNotNone(lease)
        self.assertEqual(lease.specification['seeds'],['606'])
        self.assertEqual(lease.resources,dict(cpu_millis=2000,memory_mib=4000,storage_mib=8000))
        self.assertEqual(lease.job_id,str(before['id']))
        self.assertIsNone(self.target_q.claim('another-worker'))

    def test_restored_results_use_existing_http_permissions_and_provider_raw_downloads_stay_denied(self):
        from jasmine_web.batch.access import Access
        from jasmine_web.batch.browser import COOKIE, create_app
        from jasmine_web.batch.datasets import Datasets
        from jasmine_web.batch.local_executor import LocalExecutor
        from jasmine_web.batch.results import Results
        from jasmine_web.batch.submission_service import Submissions
        from starlette.testclient import TestClient
        from .browser_model import BrowserModel
        from .queue_adapter import result_catalogue, result_name
        challenge=asyncio.run(self.access.issue('alice@example.org','fictional-client'))['challenge']
        authentication=self.access.verify(challenge,self.mail['alice@example.org'],'fictional-client')
        self.assertTrue(authentication['authorised'])
        self.save(); self.restore(); self.activate()
        access=Access(self.target_q,self.secret,self.deliver)
        datasets=Datasets(self.target_q,self.target/'uploads',reserve_bytes=1)
        executor=LocalExecutor(self.target/'execution'); self.target_q.bind_executor(executor.identity())
        service=Submissions(access,datasets,BrowserModel(ReleaseRegistry(self.target).inventory()))
        service.results=Results(service,executor,result_catalogue,name=result_name)
        with TestClient(create_app(service,origin='https://restore.example.org'),base_url='https://restore.example.org',
                        headers={'Origin':'https://restore.example.org'}) as client:
            def sign_in(email):
                challenge=client.post('/api/code',json={'email':email}).json()['challenge']
                response=client.post('/api/verify',json={'challenge':challenge,'code':self.mail[email]})
                self.assertTrue(response.json()['authorised'])
            client.cookies.set(COOKIE,authentication['token'])
            session=client.get('/api/session').json()
            self.assertTrue(session['signed_in'])
            self.assertEqual(session['csrf'],authentication['csrf'])
            self.assertEqual(client.get('/downloads/'+self.completed.job_id).status_code,200)
            self.assertEqual(client.get('/downloads/'+self.provider_completed.job_id).status_code,403)
            sign_in('bob@example.org')
            self.assertEqual(client.get('/downloads/'+self.completed.job_id).status_code,403)
        self.assertEqual(len(self.f.sql('SELECT * FROM attempts')),2)
