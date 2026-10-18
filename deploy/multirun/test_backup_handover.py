"""(C) Copyright 2026, by Ross Richardson

Encrypted recovery of stopped-model handovers with real PostgreSQL, inherited
locks and a tiny waiting helper. Scientific rows are fictional; model containers,
external storage, user keyrings and real mail are never used.
@author ross richardson
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
sys.path.insert(0,str(frontend_path()/'tests/batch'))
from jasmine_web.batch.policy import Conflict, NotFound, Resources
from jasmine_web.batch.results import verified_file
from . import backup, backup_live, backup_transport as transport
from .artifacts import ArtifactError
from .maintenance import GATE, service_state
from .releases import atomic_json
from . import test_backup as fixtures
from .test_backup import DSN, IMAGE
from .visualiser_backend import VisualiserBackend
from .visualiser_summary_cache import SummaryCache
from .visualiser_summary_holds import SummaryCapacityError
from test_visualiser import Backend, row

MIB=1024**2
RESOURCES=Resources(1000,1024,16)


def recovery_runs(configuration,names):
    """Match production's self-contained frozen parameters for each Run Set."""
    from .configuration import normalise
    runs=[]
    for name in names:
        parameters=configuration.run_configuration('baseline')
        parameters['run_sets'][0].update(id=name,name=name.title())
        runs.append(dict(id=name,parameters=normalise(parameters).editable_configuration()))
    return runs


class RecoveryBackend(Backend):
    """Fictional calculation with real summary receipts, holds and publication."""
    supports_summary_retention=True
    supports_source_ready=True
    supports_selective_views=True
    summary_retention_seconds=7*86400
    capacity_errors=(SummaryCapacityError,)
    identity=dict(revision='fictional-handover-recovery-v1',node='fictional-parser')
    manifest=dict(files={'runner.cjs':'c'*64,'calculation.cjs':'d'*64})
    retain_summaries=VisualiserBackend.retain_summaries
    summary_retention_experiments=VisualiserBackend.summary_retention_experiments
    check_summary_dependencies=VisualiserBackend.check_summary_dependencies
    storage_snapshot=VisualiserBackend.storage_snapshot
    prune_summaries=VisualiserBackend.prune_summaries
    prune_source_summaries=VisualiserBackend.prune_source_summaries

    def __init__(self,root):
        super().__init__()
        self.root=Path(root)
        cache_root=self.root/'visualiser-cache';cache_root.mkdir(mode=0o700,exist_ok=True)
        self.summary_cache=SummaryCache(cache_root/'summaries',
            maximum_bytes=4*MIB,maximum_entries=64,entry_bytes=4096)
        self.raw_reads=0;self.hits=0
        self.pause=False
        self.helper_ready=self.root/'recovery-helper-ready'
        self.helper_release=self.root/'recovery-helper-release'

    def process(self,sources,work,command,progress,*,comparison_set=False,source_ready=None):
        self.calls+=1;done=0;values=[]
        for index,source in enumerate(sources):
            selected=source['identity'];configuration=source['configuration']
            # Single-seed fixtures exercise the real cache/hold identities, not
            # the scientific summariser's numerical or resource performance.
            self.assert_single_seed(configuration)
            identity=dict(owner=source['owner'],configuration=configuration['id'],
                seed=str(configuration['runs'][0]['seed']),
                execution={k:selected[k] for k in ('job','attempt')},
                calculation=self.manifest['files'],node=self.identity['node'])
            metrics=work/('summary-'+str(index)+'.json')
            with self.summary_cache.entry(identity) as entry:
                if entry.restore(metrics):self.hits+=1
                else:
                    if source.get('retained_only'):raise AssertionError('Missing retained fixture summary')
                    for item in source['files']:
                        with verified_file(self.root,item) as stream:stream.read()
                    self.raw_reads+=1
                    atomic_json(metrics,dict(metric_value=20))
                    if not entry.publish(metrics):raise AssertionError('Fixture summary did not fit')
            value=row(configuration['role'].lower())
            value['mean_value']=json.loads(metrics.read_text())['metric_value']
            values.append(value);done+=len(source['files']);progress(done)
            if source_ready:source_ready(selected['job'])
        if self.pause:
            self.pause=False
            command([sys.executable,'-c',
                'import os,time; from pathlib import Path; '
                'ready=Path(__import__("sys").argv[1]); '
                'release=Path(__import__("sys").argv[2]); '
                'ready.write_text(str(os.getpid())); '
                'deadline=time.monotonic()+60\n'
                'while not release.exists() and time.monotonic()<deadline: time.sleep(.05)\n'
                'if not release.exists(): raise RuntimeError("Fixture helper release timed out")',
                str(self.helper_ready),str(self.helper_release)])
        data=dict(series=[dict(configuration=s['configuration']['id'],rows=[v])
                          for s,v in zip(sources,values)]) if comparison_set else dict(rows=values)
        return dict(**data,comparison_available=False,notice='Fictional recovery levels only.')

    @staticmethod
    def assert_single_seed(configuration):
        if len(configuration['runs'])!=1:raise AssertionError('Use one fictional repetition')


class RecoveryBackendTests(unittest.TestCase):
    def test_each_fixture_configuration_is_executable_through_the_real_adapter(self):
        from .configuration import normalise
        from .queue_adapter import result_name
        from .test_configuration import document
        runs=recovery_runs(normalise(document()),['baseline','alternative'])
        self.assertEqual([result_name(run) for run in runs],['Baseline','Alternative'])
        for run in runs:
            native=normalise(run['parameters']).native_configuration(run['id'])
            self.assertEqual(native['randomSeed'],606)

    def test_fixture_initialises_with_the_real_private_storage_and_handover_policy(self):
        import test_visualiser_preparation as preparation
        from jasmine_web.batch.visualiser import Visualiser
        f=preparation.PreparationTests();self.addCleanup(f.doCleanups);f.setUp()
        backend=RecoveryBackend(f.results.executor.root)
        value=Visualiser(f.results,backend,resources=RESOURCES,automatic=True,
            bounded_comparisons=True,resource_scope=Mock(),handover=True,
            storage_policy=dict(enabled=True,physical=lambda:dict(free_bytes=10**12,
                committed_bytes=0,growth_committed_bytes=0,reserve_bytes=1)))
        self.assertEqual(value.root.stat().st_mode&0o077,0)
        self.assertEqual(value.capacity,256*MIB)

    def test_retained_metrics_reopen_without_raw_files_and_do_not_freeze_the_role(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);backend=RecoveryBackend(root)
            raw=root/'raw.csv';raw.write_bytes(b'fictional input');raw.chmod(0o600)
            source=dict(owner='alice',identity=dict(job='job',attempt='attempt'),
                configuration=dict(id='baseline',role='Scenario',runs=[dict(seed='606')]),
                files=[dict(path=raw.name,bytes=raw.stat().st_size,sha256=hashlib.sha256(raw.read_bytes()).hexdigest())])
            work=root/'work';work.mkdir(mode=0o700)
            callback=Mock()
            first=backend.process([source],work,Mock(),Mock(),source_ready=callback)
            raw.unlink();source['retained_only']=True;source['configuration']['role']='Baseline'
            second=backend.process([source],work,Mock(),Mock(),source_ready=callback)
            self.assertEqual((backend.raw_reads,backend.hits),(1,1))
            self.assertEqual(first['rows'][0]['mean_value'],second['rows'][0]['mean_value'])
            self.assertEqual(second['rows'][0]['scenario'],'baseline')
            self.assertEqual(callback.call_count,2)


@unittest.skipUnless(DSN,'Use backup_handover_proof.py with disposable PostgreSQL')
class NativeHandoverRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary=tempfile.TemporaryDirectory(prefix='handover-recovery-key-')
        cls.addClassCleanup(temporary.cleanup)
        cls.keyroot=Path(temporary.name);cls.home=cls.keyroot/'keyring';cls.home.mkdir(mode=0o700)
        cls.addClassCleanup(transport.dispose_keyring,cls.home)
        command=transport.gpg_command(cls.home)
        subprocess.run(command+['--pinentry-mode','loopback','--passphrase','',
            '--quick-generate-key','Fictional Handover Proof <backup@example.invalid>',
            'rsa2048','encrypt','1d'],check=True,capture_output=True,timeout=90)
        listing=subprocess.check_output(command+['--with-colons','--fingerprint','--list-keys'],timeout=30)
        cls.recipient=next(line.split(':')[9] for line in listing.decode().splitlines() if line.startswith('fpr:'))
        cls.public=cls.keyroot/'public.asc'
        cls.public.write_bytes(subprocess.check_output(command+['--armor','--export',cls.recipient],timeout=30))
        cls.public.chmod(0o600)

    def setUp(self):
        self.f=fixtures.BackupRestoreTests();self.addCleanup(self.f.doCleanups);self.f.setUp()
        self.q=self.f.q
        self.q.backup_state=self.f.state
        self.q.configure_processing(RESOURCES)
        self.scopes=[]

    @staticmethod
    def rows(queue,query,args=()):
        with queue._connection() as c:return c.execute(query,args).fetchall()

    def service(self,queue,state):
        from jasmine_web.batch.access import Access
        from jasmine_web.batch.datasets import Datasets
        from jasmine_web.batch.local_executor import LocalExecutor
        from jasmine_web.batch.results import Results
        from jasmine_web.batch.submission_service import Submissions
        from jasmine_web.batch.visualiser import Visualiser
        from .browser_model import BrowserModel
        from .queue_adapter import result_catalogue,result_name
        from .releases import ReleaseRegistry
        executor=LocalExecutor(state/'execution');queue.bind_executor(executor.identity())
        access=Access(queue,self.f.secret,self.f.deliver)
        service=Submissions(access,Datasets(queue,state/'uploads',reserve_bytes=1),
            BrowserModel(ReleaseRegistry(state).inventory()))
        service.results=Results(service,executor,result_catalogue,name=result_name)
        backend=RecoveryBackend(executor.root)
        @contextmanager
        def scope(resources,processor):
            recorded=self.rows(queue,'SELECT * FROM processing_reservations WHERE processor=%s',(processor,))
            self.assertEqual(len(recorded),1);self.assertFalse(recorded[0]['handover_pending'])
            self.assertEqual({k:recorded[0][k] for k in asdict(resources)},asdict(resources))
            self.scopes.append(resources)
            class Scope:
                pass_fds=()
                def wrap(self,argv):return argv
            # The tiny helper uses production command/lock cleanup. Kernel
            # cgroup limits are covered by the separate delegated-scope proof.
            yield Scope()
        service.visualiser=Visualiser(service.results,backend,automatic=True,resources=RESOURCES,
            bounded_comparisons=True,resource_scope=scope,handover=True,retain_completed_summaries=True,
            storage_policy=dict(enabled=True,physical=lambda:dict(free_bytes=10**12,
                committed_bytes=0,growth_committed_bytes=0,reserve_bytes=1)))
        from jasmine_web.batch.preparation_scheduler import PreparationScheduler
        service.preparation_scheduler=PreparationScheduler(queue,service.visualiser)
        service.visualiser.processing_admission=service.preparation_scheduler.reserve
        return service

    def auth(self,service,email='alice@example.org'):
        challenge=asyncio.run(service.access.issue(email,'fictional-recovery-client'))['challenge']
        return service.access.verify(challenge,self.f.mail[email],'fictional-recovery-client')

    def submit(self,label,runs,resources):
        return self.q.submit(self.f.owner,uuid4().hex,label=label,model_digest=IMAGE,
            dataset_id=self.f.dataset,seed_plan=['606'],run_sets=recovery_runs(self.f.configuration,runs),
            baseline=runs[0] if len(runs)>1 else None,resources=resources)

    def expire_raw(self,lease):
        with self.q._transaction() as (c,_,now):
            c.execute('''INSERT INTO output_deletions(attempt_id,pool_id,owner_id,targets,identity,
                requested_at,deleted_at,reason) VALUES (%s,%s,%s,'["output"]',%s::jsonb,%s,%s,'expiry')''',
                (lease.attempt_id,self.q.pool_id,self.f.owner,
                 json.dumps(self.f.executor._identity(lease)),now,now))
        shutil.rmtree(self.f.executor.workspace(lease)/'work/output')

    def check_recovery(self,*,running):
        from jasmine_web.batch.visualiser_storage import approve,budget
        source=self.service(self.q,self.f.state);v=source.visualiser;auth=self.auth(source)
        old_key=v.request(auth['token'],auth['csrf'],self.f.completed.job_id)['id'];v.tick()
        old_data=v.data_bytes(auth['token'],old_key)
        self.expire_raw(self.f.completed)
        self.assertEqual(v.data_bytes(auth['token'],old_key),old_data)
        approved=approve(v.root,maximum_bytes=512*MIB,summary_maximum_bytes=8*MIB)
        self.q.cancel(self.f.owner,self.f.f.job(self.f.owner,self.f.queued)['id'])
        experiment=self.submit('Fictional handover recovery',['baseline','alternative'],Resources(2000,4000,8000))
        finish=self.q.finish
        def complete(*args,**kwargs):return finish(*args,**kwargs,handover=v.completion_handover)
        with patch.object(self.q,'finish',side_effect=complete):
            first=self.f.finish();second=self.f.finish()
        self.assertEqual(first.specification['seeds'],second.specification['seeds'])
        pending=self.rows(self.q,'SELECT * FROM processing_reservations ORDER BY processor')
        self.assertEqual(len(pending),2);self.assertTrue(all(r['handover_pending'] for r in pending))
        competitor=self.submit('Fictional waiting admission',['competitor'],Resources(5000,5000,8000))
        self.assertIsNone(self.q.claim('must-wait-for-preparation'))
        # Exercise the real coordinator: acknowledge completion delivery and
        # queue derived work before backup. These queued/partial files are
        # intentionally omitted, so target startup must rediscover them even
        # when the completion receipt was already acknowledged on the source.
        source.preparation_scheduler.refresh()
        event=self.rows(self.q,'SELECT version,visualiser_version FROM completion_events WHERE experiment_id=%s',
            (experiment,))[0]
        self.assertEqual(event['visualiser_version'],event['version'])
        completed_before=self.rows(self.q,'SELECT * FROM jobs WHERE experiment_id=%s ORDER BY id',(experiment,))
        self.assertTrue(all(r['state']=='succeeded' and r['attempts']==1 for r in completed_before))
        future=None;pool=None
        if running:
            v.backend.pause=True;pool=ThreadPoolExecutor(max_workers=1)
            future=pool.submit(v.tick)
        try:
            if running:
                deadline=time.monotonic()+20
                while not v.backend.helper_ready.exists() and time.monotonic()<deadline:
                    if future.done():future.result();self.fail('Preparation ended before the fixture helper started')
                    time.sleep(.05)
                self.assertTrue(v.backend.helper_ready.exists(),'Preparation helper did not start')
                helper_pid=int(v.backend.helper_ready.read_text())
                os.kill(helper_pid,0)
            transfers=self.rows(self.q,'SELECT * FROM processing_reservations ORDER BY processor')
            self.assertEqual(len(transfers),2)
            self.assertEqual(sum(not r['handover_pending'] for r in transfers),int(running))
            with self.assertRaises((ArtifactError,Conflict)):
                backup.create(self.q,self.f.state,self.f.root/'must-not-publish-offline',self.f.tools)
            self.assertFalse((self.f.root/'must-not-publish-offline').exists())
            with service_state(self.f.state),redirect_stderr(io.StringIO()):
                backup_live.create(self.q,self.f.state,self.f.backup,self.f.tools)
            manifest=backup.verify(self.f.backup)
            # Enqueuing a bounded comparison can grow the admitted allowance
            # inside the approved ceiling before this snapshot is captured.
            approved=budget(self.f.backup/'state/execution/visualiser-cache')
            self.assertEqual(approved['maximum_bytes'],512*MIB)
            self.assertEqual(manifest['database']['processing_reservations']['rows'],2)
            self.assertEqual(manifest['recovery']['interrupted_attempts'],[])
            self.assertEqual(manifest['visualiser']['publications'],1)
            self.assertEqual(manifest['visualiser']['summaries'],1+int(running))
            self.assertFalse(any('/private/' in key and 'visualiser-cache/' in key
                                 for key in manifest['state_tree']['files']))
            if running:
                self.assertFalse(future.done());os.kill(helper_pid,0)
                self.assertEqual(transfers,self.rows(self.q,'SELECT * FROM processing_reservations ORDER BY processor'))
        finally:
            if running:
                v.backend.helper_release.write_text('release fictional helper')
                try:future.result(timeout=30)
                finally:pool.shutdown(wait=True,cancel_futures=True)
        if running:
            with self.assertRaises(ProcessLookupError):os.kill(helper_pid,0)
        source_jobs=self.rows(self.q,'SELECT * FROM jobs WHERE experiment_id=%s ORDER BY id',(experiment,))
        self.assertEqual(source_jobs,completed_before)
        cipher=self.f.root/'handover-recovery.tar.gpg'
        receipt=transport.seal(self.f.backup,cipher,public_key=self.public,recipient=self.recipient)
        self.assertNotIn(self.f.secret.encode(),cipher.read_bytes())
        recovered=self.f.root/'decrypted'
        transport.unseal(cipher,recovered,keyring=self.home,expected_sha256=receipt['sha256'])
        self.assertEqual(backup.verify(recovered),manifest)
        if not running:
            def interrupted():raise RuntimeError('Fictional interruption after restored database commit')
            with self.assertRaises(RuntimeError):
                backup.restore(self.f.target_q,recovered,self.f.target,self.f.target_tools,after_database=interrupted)
            backup.restore(self.f.target_q,recovered,self.f.target,self.f.target_tools,resume=True)
        else:backup.restore(self.f.target_q,recovered,self.f.target,self.f.target_tools)
        target=self.f.target_q
        self.assertEqual(self.rows(target,'SELECT * FROM processing_reservations'),[])
        self.assertEqual(self.rows(target,'SELECT * FROM reservations WHERE released_at IS NULL'),[])
        self.assertEqual(self.rows(target,'SELECT * FROM jobs WHERE experiment_id=%s ORDER BY id',(experiment,)),completed_before)
        with self.assertRaises(ArtifactError):
            with service_state(self.f.target):pass
        with self.assertRaises(ArtifactError):
            backup.activate(target,recovered,self.f.target,image_check=lambda _:None)
        with service_state(self.f.state),self.assertRaises(ArtifactError):
            backup.activate(target,recovered,self.f.target,image_check=lambda _:None,source_isolated=True)
        self.assertTrue((self.f.target/GATE).exists())
        backup.activate(target,recovered,self.f.target,image_check=lambda _:None,source_isolated=True)
        restored=self.service(target,self.f.target);rv=restored.visualiser
        self.assertEqual(budget(rv.root),approved)
        self.assertEqual(rv.data_bytes(auth['token'],old_key),old_data)
        self.assertEqual(json.loads(rv.catalogue_bytes(auth['token'],old_key))['seeds'],['606'])
        restored.preparation_scheduler.refresh()
        rv.tick()
        ready=[(key,record) for key,record in rv._entries()
               if record['selection']['experiment']==experiment]
        self.assertEqual(len(ready),3);self.assertTrue(all(r['state']=='ready' for _,r in ready))
        self.assertEqual(rv.backend.raw_reads,2-int(running))
        self.assertGreaterEqual(rv.backend.hits,2+int(running))
        self.assertEqual(self.rows(target,'SELECT * FROM processing_reservations'),[])
        self.assertEqual(self.rows(target,'SELECT * FROM jobs WHERE experiment_id=%s ORDER BY id',(experiment,)),completed_before)
        self.assertEqual(self.rows(self.q,'SELECT * FROM jobs WHERE experiment_id=%s ORDER BY id',(experiment,)),source_jobs)
        pair_key,pair=next((key,r) for key,r in ready if r['selection'].get('baseline'))
        self.assertEqual(json.loads(rv.catalogue_bytes(auth['token'],pair_key))['seeds'],['606'])
        bob=self.auth(restored,'bob@example.org')
        with self.assertRaises(NotFound):rv.data_bytes(bob['token'],pair_key)
        # Prove restored capacity can admit waiting work once, with no stranded
        # old processor charge, before testing a grant revocation below.
        lease=target.claim('restored-admission')
        self.assertIsNotNone(lease)
        job=self.rows(target,'SELECT id FROM jobs WHERE experiment_id=%s',(competitor,))[0]
        self.assertEqual(lease.job_id,str(job['id']))
        self.assertIsNone(target.claim('duplicate-restored-admission'))
        target.finish(lease,outcome='cancelled',stop_evidence='f'*64)
        self.expire_restored_raw(target,first,second)
        self.assertEqual(rv.status(auth['token'],pair_key)['state'],'ready')
        with target._connection() as c:
            c.execute('UPDATE dataset_grants SET valid_until=clock_timestamp() WHERE pool_id=%s AND owner_id=%s AND dataset_id=%s',
                (target.pool_id,self.f.owner,self.f.dataset))
        with self.assertRaises(NotFound):rv.data_bytes(auth['token'],pair_key)

    def expire_restored_raw(self,queue,*leases):
        from jasmine_web.batch.local_executor import LocalExecutor
        executor=LocalExecutor(self.f.target/'execution')
        for lease in leases:
            with queue._transaction() as (c,_,now):
                c.execute('''INSERT INTO output_deletions(attempt_id,pool_id,owner_id,targets,identity,
                    requested_at,deleted_at,reason) VALUES (%s,%s,%s,'["output"]',%s::jsonb,%s,%s,'expiry')''',
                    (lease.attempt_id,queue.pool_id,self.f.owner,json.dumps(executor._identity(lease)),now,now))
            shutil.rmtree(executor.workspace(lease)/'work/output')

    def test_encrypted_pending_handovers_restore_without_repeating_models_or_stranding_capacity(self):
        self.check_recovery(running=False)

    def test_encrypted_running_preparation_omits_partial_files_and_resumes_with_committed_summaries(self):
        self.check_recovery(running=True)


if __name__=='__main__':unittest.main()
