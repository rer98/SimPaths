"""(C) Copyright 2026, by Ross Richardson

Verified retained-summary/chart backup selection, corruption guards, and native
offline/online restoration after routine raw expiry. No scientific simulations.
@author ross richardson
"""
import asyncio
from contextlib import redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
sys.path.insert(0,str(frontend_path()/'tests/batch'))
from . import backup_visualiser as archive
from . import backup,backup_live
from .artifacts import ArtifactError
from .releases import atomic_json
from .visualiser_summary_cache import SummaryCache
from .test_backup import DSN
from jasmine_web.batch.downloads import lock


class VisualiserBackupFilesTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.state=self.root/'state';self.state.mkdir(mode=0o700)
        self.cache=self.state/archive.ROOT;self.cache.mkdir(mode=0o700,parents=True)
        self.key='a'*64;folder=self.cache/self.key;folder.mkdir(mode=0o700)
        body=b'{"fictional":"approved aggregate"}'
        (folder/'published.json').write_bytes(body)
        self.record=dict(state='ready',backend={'revision':'fictional'},expires_at=2000000000,
            artifact_hash_format='aggregate-json-v1',artifact_hash=hashlib.sha256(body).hexdigest(),
            selection=dict(owner='alice',jobs=[dict(attempt=str(uuid4()))]))
        atomic_json(folder/'status.json',self.record)
        self.summary=SummaryCache(self.cache/'summaries')
        self.identity=dict(owner='alice',configuration='baseline',execution={'attempt':str(uuid4())})
        source=self.root/'summary.json';source.write_bytes(b'[{"metric_value":1}]');source.chmod(0o600)
        with self.summary.entry(self.identity) as entry:
            entry.publish(source);self.summary_key=entry.key
        self.target=self.root/'backup-state';self.target.mkdir(mode=0o700)

    def capture(self):
        return archive.capture(self.state,self.target)

    def test_summary_publication_receipts_and_only_committed_data_are_copied(self):
        work=self.cache/self.key/'private';work.mkdir();(work/'person.csv').write_text('PRIVATE')
        pending=self.cache/'summaries/.pending-incomplete';pending.mkdir();(pending/'metrics.json').write_text('partial')
        record=self.capture();self.assertEqual((record['summaries'],record['publications']),(1,1))
        archive.verify(self.target,record)
        self.assertFalse((self.target/archive.ROOT/self.key/'private').exists())
        self.assertFalse((self.target/archive.ROOT/'summaries/.pending-incomplete').exists())
        restored=SummaryCache(self.target/archive.ROOT/'summaries')
        with restored.entry(self.identity) as entry:self.assertTrue(entry.restore(self.root/'restored.json'))
        self.assertEqual((self.root/'restored.json').read_bytes(),b'[{"metric_value":1}]')
        self.assertEqual(json.loads((self.target/archive.ROOT/self.key/'status.json').read_text())['expires_at'],2000000000)

    def test_active_shared_reader_does_not_prevent_backup(self):
        with lock(self.cache/self.key/'job.lock',shared=True):
            self.assertEqual(self.capture()['publications'],1)

    def test_brief_maintenance_and_summary_locks_are_waited_out(self):
        for path in (self.cache/'maintenance.lock',self.cache/'summaries/.cache.lock'):
            with self.subTest(lock=path.name):
                entered=threading.Event();release=threading.Event()
                def housekeeping():
                    with lock(path):entered.set();release.wait(3)
                worker=threading.Thread(target=housekeeping);worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    original_sleep=time.sleep
                    def pause(seconds):release.set();original_sleep(min(seconds,0.01))
                    destination=self.root/uuid4().hex;destination.mkdir(mode=0o700)
                    with patch.object(archive.time,'sleep',side_effect=pause):
                        result=archive.capture(self.state,destination)
                    archive.verify(destination,result)
                    self.assertEqual((result['publications'],result['summaries']),(1,1))
                finally:release.set();worker.join(3)

    def test_cache_lock_wait_remains_bounded(self):
        with lock(self.cache/'maintenance.lock'):
            with self.assertRaises(BlockingIOError):
                with archive.capture_lock(self.cache/'maintenance.lock',seconds=0):pass

    def test_busy_processor_is_omitted_instead_of_copying_partial_publication(self):
        with lock(self.cache/self.key/'job.lock'):
            record=self.capture()
        self.assertEqual(record['publications'],0);self.assertEqual(record['summaries'],1)

    def test_corrupt_summary_is_rejected(self):
        (self.summary.root/self.summary_key/'metrics.json').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ArtifactError,'receipt'):self.capture()

    def test_corrupt_publication_is_rejected(self):
        (self.cache/self.key/'published.json').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ArtifactError,'receipt'):self.capture()

    def test_unknown_snapshot_sources_are_not_included(self):
        with patch.object(archive,'known_source',return_value=False):
            record=self.capture()
        self.assertEqual((record['publications'],record['summaries']),(0,0))

    def test_chart_index_and_every_section_are_verified(self):
        views=self.cache/self.key/'views';views.mkdir(mode=0o700)
        section='b'*64;content=b'[{"fictional":1}]'
        (views/(section+'.json')).write_bytes(content)
        index=dict(format='simpaths.visualiser.index.v1',publication=self.record['artifact_hash'],
            catalogue={},sections={section:dict(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())})
        atomic_json(views/'index.json',index)
        self.record['index_sha256']=hashlib.sha256((views/'index.json').read_bytes()).hexdigest()
        atomic_json(self.cache/self.key/'status.json',self.record)
        record=self.capture();archive.verify(self.target,record)
        (self.target/archive.ROOT/self.key/'views'/(section+'.json')).write_bytes(b'changed')
        with self.assertRaisesRegex(ArtifactError,'receipt'):archive.verify(self.target,record)

    def test_unexpected_restored_files_are_rejected(self):
        record=self.capture()
        (self.target/archive.ROOT/self.key/'raw.csv').write_bytes(b'private')
        with self.assertRaisesRegex(ArtifactError,'inventory'):archive.verify(self.target,record)

    def test_linked_metrics_are_not_followed(self):
        metrics=self.summary.root/self.summary_key/'metrics.json';metrics.unlink()
        metrics.symlink_to(self.root/'summary.json')
        with self.assertRaises((ArtifactError,OSError)):self.capture()

    def test_eviction_markers_budget_controls_and_user_access_survive_capture(self):
        from jasmine_web.batch.visualiser_storage import BUDGET,FORMAT,budget
        self.record.update(state='evicted',evicted_at=time.time())
        atomic_json(self.cache/self.key/'status.json',self.record)
        # Also exercise recovery of marker-first eviction interrupted before
        # deletion: the stale finished payload must not enter a new backup.
        atomic_json(self.cache/self.key/'access.json',dict(at=time.time()-4*86400))
        control=dict(format=FORMAT,initial_bytes=64*1024**2,capacity_bytes=80*1024**2,
            maximum_bytes=128*1024**2,summary_bytes=2*1024**2,summary_maximum_bytes=4*1024**2,growths=1)
        atomic_json(self.cache/BUDGET,control)
        record=self.capture();archive.verify(self.target,record)
        self.assertEqual((record['publications'],record['evictions'],record['summaries']),(0,1,1))
        restored=self.target/archive.ROOT
        self.assertEqual(budget(restored),control)
        self.assertEqual(json.loads((restored/self.key/'status.json').read_text())['state'],'evicted')
        self.assertTrue((restored/self.key/'access.json').exists())
        self.assertFalse((restored/self.key/'published.json').exists())

    def test_existing_v1_retained_backup_inventory_still_verifies(self):
        record=self.capture();record.pop('evictions');record.pop('experiment_views');record['format']=archive.LEGACY_FORMAT
        archive.verify(self.target,record)

    def test_visualisation_baseline_preferences_are_checked_and_preserved_independently(self):
        from jasmine_web.batch.visualiser_baseline import save,selected,DIRECTORY
        experiment=dict(id='experiment-alice',owner_id='alice',specification=dict(baseline=None,
            run_sets=[dict(id='first'),dict(id='second')]))
        save(self.cache,experiment,'second')
        record=self.capture();self.assertEqual(record['experiment_views'],1)
        self.assertEqual(selected(self.target/archive.ROOT,experiment),'second')
        self.assertIsNone(experiment['specification']['baseline'])
        file=next((self.target/archive.ROOT/DIRECTORY).iterdir())
        value=json.loads(file.read_text());value['owner']='bob';atomic_json(file,value)
        with self.assertRaises(ValueError):archive.verify(self.target,record)

    def test_existing_v2_retained_backup_inventory_still_verifies(self):
        record=self.capture();record.pop('experiment_views');record['format']=archive.PREVIOUS_FORMAT
        archive.verify(self.target,record)


@unittest.skipUnless(DSN,'Use the disposable native PostgreSQL proof')
class NativeVisualiserRestoreTests(unittest.TestCase):
    def setUp(self):
        from .test_backup import BackupRestoreTests
        self.f=BackupRestoreTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def service(self,queue,state):
        from jasmine_web.batch.access import Access
        from jasmine_web.batch.datasets import Datasets
        from jasmine_web.batch.local_executor import LocalExecutor
        from jasmine_web.batch.results import Results
        from jasmine_web.batch.submission_service import Submissions
        from jasmine_web.batch.visualiser import Visualiser
        from jasmine_web.batch.policy import Resources
        from .browser_model import BrowserModel
        from .queue_adapter import result_catalogue,result_name
        from .releases import ReleaseRegistry
        from test_visualiser import Backend
        access=Access(queue,self.f.secret,self.f.deliver)
        executor=LocalExecutor(state/'execution');queue.bind_executor(executor.identity())
        service=Submissions(access,Datasets(queue,state/'uploads',reserve_bytes=1),
                            BrowserModel(ReleaseRegistry(state).inventory()))
        service.results=Results(service,executor,result_catalogue,name=result_name)
        backend=Backend();backend.root=executor.root;backend.supports_selective_views=True
        service.visualiser=Visualiser(service.results,backend,resources=Resources(1000,1024,16),storage_policy=dict(enabled=True,
            physical=lambda:dict(free_bytes=10**12,committed_bytes=0,growth_committed_bytes=0,reserve_bytes=1)))
        return service

    def check_roundtrip(self,online,*,evicted=False):
        from jasmine_web.batch.policy import NotFound
        source=self.service(self.f.q,self.f.state)
        challenge=asyncio.run(source.access.issue('alice@example.org','fictional-client'))['challenge']
        auth=source.access.verify(challenge,self.f.mail['alice@example.org'],'fictional-client')
        v=source.visualiser
        key=v.request(auth['token'],auth['csrf'],self.f.completed.job_id)['id'];v.tick()
        original=v.data_bytes(auth['token'],key)
        q=self.f.q
        from jasmine_web.batch.visualiser_baseline import save as save_baseline,selected as selected_baseline
        exp,_=source.results._snapshot(auth['token'],experiment=self.f.exp)
        save_baseline(v.root,exp,None)
        from .releases import ReleaseRegistry, utc_now
        from .release_retirement import ReleaseRetirement
        from . import release_archives
        from datetime import timedelta
        registry=ReleaseRegistry(self.f.state)
        original_default=next(iter(registry.load()))
        jar=self.f.root/'retired-model.jar';jar.write_bytes(b'fictional retired model')
        old=registry.register(image='sha256:'+'e'*64,name='Retired recovery fixture',jar=jar,
            defaults=registry.load()[original_default]['defaults'])
        registry.set_availability(old,'recovery')
        retirement=ReleaseRetirement(q,self.f.state);now=utc_now()
        retirement.tick(force=True,now=now)
        self.assertEqual(retirement.tick(force=True,now=now+timedelta(days=31))['retired'],[old])
        archive_identity=release_archives.verify(registry,old)['archive']
        source_row=self.f.f.sql('SELECT identity FROM visualiser_sources WHERE attempt_id=%s',
                                (self.f.completed.attempt_id,))[0]
        cache=SummaryCache(v.root/'summaries')
        identity=dict(owner=self.f.owner,configuration='baseline',execution={
            'job':self.f.completed.job_id,'attempt':self.f.completed.attempt_id},seed='606')
        summary=self.f.root/'summary.json'
        summary.write_bytes(b'[{"year":2019,"scenario":"baseline","run":"606","metric_value":1}]')
        summary.chmod(0o600)
        with cache.entry(identity) as entry:self.assertTrue(entry.publish(summary))
        with q._transaction() as (c,_,now):
            c.execute('''INSERT INTO output_deletions(attempt_id,pool_id,owner_id,targets,identity,requested_at,deleted_at,reason)
                VALUES (%s,%s,%s,'["output"]',%s::jsonb,%s,%s,'expiry')''',
                (self.f.completed.attempt_id,q.pool_id,self.f.owner,
                 json.dumps(self.f.executor._identity(self.f.completed)),now,now))
        shutil.rmtree(self.f.executor.workspace(self.f.completed)/'work/output')
        self.assertEqual(v.data_bytes(auth['token'],key),original)
        from jasmine_web.batch.visualiser_storage import approve,budget
        approved=approve(v.root,maximum_bytes=512*1024**2)
        if evicted:
            record=v._record(key);record.update(state='evicted',evicted_at=time.time())
            atomic_json(v._path(key)/'status.json',record)
            (v._path(key)/'published.json').unlink();shutil.rmtree(v._path(key)/'views')
        with redirect_stderr(io.StringIO()):
            if online:backup_live.create(q,self.f.state,self.f.backup,self.f.tools)
            else:self.f.save()
        manifest=backup.verify(self.f.backup)
        self.assertEqual(manifest['visualiser']['summaries'],1)
        self.assertEqual(manifest['visualiser']['publications'],0 if evicted else 1)
        self.assertEqual(manifest['visualiser']['evictions'],1 if evicted else 0)
        self.assertEqual(manifest['visualiser']['experiment_views'],1)
        backup.restore(self.f.target_q,self.f.backup,self.f.target,self.f.target_tools)
        backup.activate(self.f.target_q,self.f.backup,self.f.target,image_check=lambda _:None,source_isolated=online)
        restored_registry=ReleaseRegistry(self.f.target)
        self.assertEqual(release_archives.verify(restored_registry,old)['archive'],archive_identity)
        self.assertNotIn(old,restored_registry.load())
        restored=self.service(self.f.target_q,self.f.target)
        self.assertIsNone(selected_baseline(restored.visualiser.root,exp))
        self.assertEqual(budget(restored.visualiser.root),approved)
        with patch.object(restored.visualiser.backend,'process',side_effect=AssertionError('Restore must not recalculate')):
            if evicted:
                self.assertEqual(restored.visualiser.status(auth['token'],key)['state'],'evicted')
                self.assertEqual(restored.visualiser._enqueue(restored.visualiser._record(key)['selection'])['state'],'evicted')
            else:
                self.assertEqual(restored.visualiser.data_bytes(auth['token'],key),original)
                catalogue=json.loads(restored.visualiser.catalogue_bytes(auth['token'],key))
                self.assertEqual(catalogue['seeds'],['606'])
        restored_cache=SummaryCache(restored.visualiser.root/'summaries')
        with restored_cache.entry(identity) as entry:self.assertTrue(entry.restore(self.f.root/'restored-summary.json'))
        self.assertEqual((self.f.root/'restored-summary.json').read_bytes(),summary.read_bytes())
        with self.f.target_q._connection() as c:
            self.assertEqual(c.execute('SELECT identity FROM visualiser_sources WHERE attempt_id=%s',
                (self.f.completed.attempt_id,)).fetchone(),source_row)
        self.f.target_q.grant_dataset(self.f.owner,self.f.dataset,seconds=None)
        with self.assertRaises(NotFound):restored.visualiser.data_bytes(auth['token'],key)

    def test_offline_restore_preserves_summaries_charts_expiry_and_revocation_after_raw_expiry(self):
        self.check_roundtrip(False)

    def test_online_restore_preserves_summaries_charts_expiry_and_revocation_after_raw_expiry(self):
        self.check_roundtrip(True)

    def test_offline_restore_keeps_eviction_suppression_and_live_approval(self):
        self.check_roundtrip(False,evicted=True)

    def test_online_restore_keeps_eviction_suppression_and_live_approval(self):
        self.check_roundtrip(True,evicted=True)


if __name__=='__main__':unittest.main()
