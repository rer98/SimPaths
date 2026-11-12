"""(C) Copyright 2026, by Ross Richardson

Fixed summary deadlines survive capture/restore without accepting partial holds.
Fictional metrics only; no model execution or real recovery copies.
@author ross richardson
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest
from contextlib import redirect_stderr

from .artifacts import ArtifactError
from .backup_summary_retention import verify_bundles
from .visualiser_backend import VisualiserBackend
from .visualiser_summary_cache import SummaryCache, canonical
from .visualiser_summary_holds import scope


class FixedSummaryRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.root = self.state/'execution/visualiser-cache/summaries'
        self.root.mkdir(parents=True, mode=0o700)
        self.cache = SummaryCache(self.root)
        self.now = time.time()
        self.deadline = datetime.fromtimestamp(self.now+30*86400,timezone.utc).timestamp()
        self.files = {'runner.cjs':'a'*64, 'calculation.cjs':'b'*64}
        self.description = dict(calculation=dict(mode='development-paired',files=self.files),node='v22.fixture')
        identity = dict(job='job',attempt='attempt',execution_key='execution',prepared='prepared',
                        configuration='baseline',repetitions=[dict(seed='606'),dict(seed='607')])
        configuration = dict(id='baseline',model='model',dataset='dataset',prepared_fingerprint='prepared',
                             runs=[dict(seed='606',folder='Scenario/run_1'),
                                   dict(seed='607',folder='Scenario/run_2')])
        files = [dict(name=f'Scenario/run_{number}/csv/{kind}.csv',path=f'run_{number}/{kind}.csv',
                      bytes=10,sha256=hashlib.sha256(kind.encode()).hexdigest())
                 for number in (1,2) for kind in ('Person','BenefitUnit')]
        source = dict(configuration=configuration, files=files,owner='alice',identity=identity)
        adapter = SimpleNamespace(manifest=dict(files=self.files),identity=dict(node='v22.fixture'))
        self.identities = [VisualiserBackend._summary_identity(adapter,source,run,
            VisualiserBackend._summary_inputs(source,run)) for run in configuration['runs']]
        metric = self.state/'fictional.json'
        metric.write_bytes(b'[{"metric_value":1}]');metric.chmod(0o600)
        for value in self.identities:
            with self.cache.entry(value) as entry:
                self.assertTrue(entry.publish(metric))
        self.created = self.now-10
        self.hold(self.deadline)
        self.row = dict(owner_id='alice',identity=identity,source=source,
            summary_protected_at=datetime.fromtimestamp(self.created,timezone.utc),
            summary_expires_at=datetime.fromtimestamp(self.deadline,timezone.utc),
            summary_backend=self.description)

    def hold(self, deadline):
        plan = dict(comparison='c'*64,created_at=self.created,expires_at=deadline,
            configurations=[dict(configuration='baseline',job='job',attempt='attempt',seeds=['606','607'])])
        self.cache.holds.replace('alice','experiment',[plan],calculation=scope(self.identities[0]),owner_entries=4)

    def verify(self, rows=None):
        return verify_bundles(self.state,[self.row] if rows is None else rows,now=self.now)

    def test_complete_matching_set_preserves_all_deadlines_and_files(self):
        before = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.verify(),dict(sources=1,seeds=2))
        after = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(after,before)
        self.assertEqual(self.row['summary_expires_at'].timestamp(),self.deadline)

    def test_hold_that_is_live_today_but_ends_early_cannot_activate(self):
        self.hold(self.now+86400)
        with self.assertRaises(ArtifactError):self.verify()

    def test_one_missing_seed_refuses_recovery_even_with_protected_companion(self):
        key=hashlib.sha256(canonical(self.identities[-1])).hexdigest()
        (self.root/key/'metrics.json').unlink()
        with self.assertRaises(ArtifactError):self.verify()

    def test_corrupt_seed_and_changed_calculation_are_refused(self):
        row=deepcopy(self.row)
        row['summary_backend']['calculation']['files']['runner.cjs']='c'*64
        with self.assertRaises(ArtifactError):self.verify([row])
        key=hashlib.sha256(canonical(self.identities[0])).hexdigest()
        (self.root/key/'metrics.json').write_bytes(b'corrupt')
        with self.assertRaises(ArtifactError):self.verify()

    def test_expired_promise_is_not_renewed_or_required_for_activation(self):
        self.row['summary_expires_at']=datetime.fromtimestamp(self.now-1,timezone.utc)
        self.assertEqual(self.verify(),dict(sources=0,seeds=0))
        self.assertLess(self.row['summary_expires_at'].timestamp(),self.now)


@unittest.skipUnless(os.environ.get('JASMINE_BATCH_TEST_DSN'), 'Use disposable PostgreSQL')
class NativeFixedSummaryRestoreTests(unittest.TestCase):
    def setUp(self):
        from .test_backup import BackupRestoreTests
        self.f=BackupRestoreTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def roundtrip(self, *, online):
        from .test_backup_visualiser import NativeVisualiserRestoreTests
        from . import backup, backup_live
        from jasmine_web.batch.visualiser_sources import commit_retention, retention
        import asyncio
        service=NativeVisualiserRestoreTests.service(self,self.f.q,self.f.state)
        challenge=asyncio.run(service.access.issue('alice@example.org','fictional-client'))['challenge']
        auth=service.access.verify(challenge,self.f.mail['alice@example.org'],'fictional-client')
        visualiser=service.visualiser
        visualiser.request(auth['token'],auth['csrf'],self.f.completed.job_id);visualiser.tick()
        with self.f.q._connection() as c:
            row=c.execute('SELECT identity,source FROM visualiser_sources WHERE attempt_id=%s',
                          (self.f.completed.attempt_id,)).fetchone()
        source=dict(row['source'],owner=self.f.owner,identity=row['identity'])
        files={'runner.cjs':'a'*64,'calculation.cjs':'b'*64}
        description=dict(calculation=dict(mode='development-paired',files=files),node='v22.fixture')
        adapter=SimpleNamespace(manifest=dict(files=files),identity=dict(node='v22.fixture'))
        identities=[VisualiserBackend._summary_identity(adapter,source,run,
            VisualiserBackend._summary_inputs(source,run)) for run in source['configuration']['runs']]
        cache=SummaryCache(visualiser.root/'summaries')
        metric=self.f.root/'fictional-fixed-summary.json'
        metric.write_bytes(b'[{"metric_value":1}]');metric.chmod(0o600)
        for identity in identities:
            with cache.entry(identity) as entry:self.assertTrue(entry.publish(metric))
        created=datetime.now(timezone.utc).timestamp()
        deadline=datetime.fromtimestamp(created+30*86400,timezone.utc)
        value=dict(protected_at=datetime.fromtimestamp(created,timezone.utc).isoformat(),
                   expires_at=deadline.isoformat(),backend=description)
        plan=dict(comparison='d'*64,created_at=created,expires_at=deadline.timestamp(),
            configurations=[dict(configuration=self.f.completed.configuration_id,
                job=self.f.completed.job_id,attempt=self.f.completed.attempt_id,
                seeds=[str(run['seed']) for run in source['configuration']['runs']])])
        cache.holds.replace(self.f.owner,self.f.exp,[plan],calculation=scope(identities[0]),owner_entries=4)
        with cache._lock('.cache.lock'):
            commit_retention(service.results,self.f.owner,row['identity'],value)
        with self.f.q._transaction() as (c,_,now):
            c.execute('''INSERT INTO output_deletions(attempt_id,pool_id,owner_id,targets,identity,
                requested_at,deleted_at,reason,summary_expires_at)
                VALUES (%s,%s,%s,'["output"]',%s::jsonb,%s,%s,'raw_only',%s)''',
                (self.f.completed.attempt_id,self.f.q.pool_id,self.f.owner,
                 json.dumps(self.f.executor._identity(self.f.completed)),now,now,deadline))
        shutil.rmtree(self.f.executor.workspace(self.f.completed)/'work/output')
        with redirect_stderr(io.StringIO()):
            if online:backup_live.create(self.f.q,self.f.state,self.f.backup,self.f.tools)
            else:self.f.save()
        backup.restore(self.f.target_q,self.f.backup,self.f.target,self.f.target_tools)
        backup.activate(self.f.target_q,self.f.backup,self.f.target,image_check=lambda _:None,
                        source_isolated=online)
        with self.f.target_q._connection() as c:
            restored=c.execute('SELECT * FROM visualiser_sources WHERE attempt_id=%s',
                               (self.f.completed.attempt_id,)).fetchone()
        self.assertEqual(retention(restored),value)
        self.assertFalse((self.f.target/'execution'/self.f.completed.execution_key/'work/output').exists())

    def test_offline_restore_keeps_fixed_thirty_day_source_promise_without_raw(self):
        self.roundtrip(online=False)

    def test_online_restore_keeps_fixed_thirty_day_source_promise_without_raw(self):
        self.roundtrip(online=True)


if __name__=='__main__':unittest.main()
