"""(C) Copyright 2026, by Ross Richardson

Pipeline scratch omission and gated native PostgreSQL backup restoration.
Quota arithmetic uses an explicit fake; real kernel enforcement is separately
proved by the disposable two-filesystem native quota fixture.
@author ross richardson
"""
from contextlib import redirect_stderr
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

from . import backup_pipeline as pipeline
from .artifacts import ArtifactError
from .visualiser_summary_cache import SummaryCache
from .visualiser_summary_holds import scope


class PipelineBackupFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.key = 'batch-00000000-0000-0000-0000-000000000001'

    def test_only_bounded_pipeline_scratch_is_omitted_and_source_bytes_are_untouched(self):
        scratch = self.state / 'artifacts' / ('pipeline-' + self.key)
        scratch.mkdir(mode=0o700, parents=True)
        (scratch / 'partial.json').write_bytes(b'private unfinished temporary metric')
        prepared = self.state / 'artifacts' / self.key
        prepared.mkdir(mode=0o700)
        (prepared / 'input').write_bytes(b'keep prepared input')
        self.assertEqual(pipeline.scratch_exclusions(self.state), ('artifacts/pipeline-' + self.key,))
        from .backup_files import copy_tree
        destination = self.state / 'backup'
        # Keep the actual fixture destination outside its source tree.
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        destination = Path(other.name) / 'copied'
        copy_tree(self.state, destination, exclude=pipeline.scratch_exclusions(self.state))
        self.assertFalse((destination / 'artifacts' / ('pipeline-' + self.key)).exists())
        self.assertEqual((destination / 'artifacts' / self.key / 'input').read_bytes(), b'keep prepared input')
        self.assertEqual((scratch / 'partial.json').read_bytes(), b'private unfinished temporary metric')

    def test_a_linked_scratch_root_is_refused_without_following_or_deleting_it(self):
        artifacts = self.state / 'artifacts'
        artifacts.mkdir(mode=0o700)
        original = self.state / 'original'
        original.mkdir(mode=0o700)
        (original / 'keep').write_bytes(b'keep')
        (artifacts / ('pipeline-' + self.key)).symlink_to(original, target_is_directory=True)
        with self.assertRaises(ArtifactError):
            pipeline.scratch_exclusions(self.state)
        self.assertEqual((original / 'keep').read_bytes(), b'keep')

    def summaries(self, *, prepared='prepared', attempt='attempt', seeds=('606', '607'), held=True):
        root = self.state / 'execution/visualiser-cache/summaries'
        for directory in (self.state/'execution', root.parent, root):
            directory.mkdir(mode=0o700, exist_ok=True)
        cache = SummaryCache(root)
        identities = []
        for seed in seeds:
            identity = dict(owner='alice', configuration='baseline', seed=seed,
                            execution=dict(job='job', attempt=attempt, execution_key=self.key, prepared=prepared),
                            calculation={'runner.cjs': 'a' * 64, 'calculation.cjs': 'b' * 64}, node='v22.fixture')
            metrics = self.state / ('metric-' + seed + '.json')
            metrics.write_bytes(b'[{"metric_value":1}]')
            metrics.chmod(0o600)
            with cache.entry(identity) as entry:
                self.assertTrue(entry.publish(metrics))
            identities.append(identity)
        if held:
            now = time.time()
            plan = dict(comparison='c' * 64, created_at=now - 1, expires_at=now + 3600,
                        configurations=[dict(configuration='baseline', job='job', attempt=attempt, seeds=list(seeds))])
            cache.holds.replace('alice', 'experiment', [plan], calculation=scope(identities[0]), owner_entries=4)
        row = dict(id='attempt', job_id='job', owner_id='alice', configuration_id='baseline',
                   execution_key=self.key, prepared_fingerprint='prepared')
        return row, identities

    def test_only_an_exact_complete_protected_seed_set_discharges_a_future_promise(self):
        row, _ = self.summaries()
        cache, groups = pipeline.summary_index(self.state)
        self.assertTrue(pipeline.protected(cache, groups, row, ['606', '607']))
        for field, value in [('id', 'other'), ('job_id', 'other'), ('owner_id', 'bob'),
                             ('prepared_fingerprint', 'changed'), ('configuration_id', 'other')]:
            with self.subTest(field=field):
                self.assertFalse(pipeline.protected(cache, groups, dict(row, **{field: value}), ['606', '607']))
        self.assertFalse(pipeline.protected(cache, groups, row, ['606', '607', '608']))

    def test_unheld_summaries_keep_the_need_for_a_fresh_physical_promise(self):
        row, _ = self.summaries(held=False)
        cache, groups = pipeline.summary_index(self.state)
        self.assertFalse(pipeline.protected(cache, groups, row, ['606', '607']))

    def test_corrupt_protected_summary_refuses_activation_instead_of_skipping_validation(self):
        row, identities = self.summaries()
        cache, groups = pipeline.summary_index(self.state)
        from .visualiser_summary_cache import canonical
        key = hashlib.sha256(canonical(identities[0])).hexdigest()
        (cache.root / key / 'metrics.json').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            pipeline.protected(cache, groups, row, ['606', '607'])

    def test_native_summary_fixture_is_private_and_capturable_with_a_permissive_umask(self):
        # Exercise the exact native fixture builder without a database. Only its
        # final fictional SQL insert is captured; the real backup file verifier,
        # summary receipts and dependency protection checks all run unchanged.
        (self.state/'execution').mkdir(mode=0o700)
        lease=SimpleNamespace(configuration_id='baseline',job_id='job',attempt_id='attempt',
                              execution_key=self.key)
        recorded=[]
        fixture=NativePipelineRestoreTests()
        fixture.f=SimpleNamespace(state=self.state,root=self.state,completed=lease,
            owner='alice',exp='experiment',receipt={'sha256':'a'*64},
            q=SimpleNamespace(pool_id='fictional'),f=SimpleNamespace(sql=lambda *args:recorded.append(args)))
        previous=os.umask(0o002)
        try:
            fixture.protect_completed()
        finally:
            os.umask(previous)
        self.assertEqual(len(recorded),1)
        for directory in ('execution','execution/visualiser-cache','execution/visualiser-cache/summaries'):
            self.assertEqual((self.state/directory).stat().st_mode & 0o777,0o700)
        from . import backup_visualiser as archive
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'restored'
            receipt=archive.capture(self.state,target)
            archive.verify(target,receipt)
            self.assertEqual(receipt['summaries'],1)
            cache,groups=pipeline.summary_index(target)
            row=dict(id='attempt',job_id='job',owner_id='alice',configuration_id='baseline',
                     execution_key=self.key,prepared_fingerprint='a'*64)
            self.assertTrue(pipeline.protected(cache,groups,row,['606']))
            self.assertFalse((cache.root/'.cache.lock').exists())


class Quotas:
    """Explicit protocol double; never claims native kernel enforcement."""
    def __init__(self, gate):
        self.gate = gate
        self.restored = []
        self.promises = {}
        self.full = False

    def guard(self):
        if not self.gate.exists():
            raise AssertionError('Restoration gate was removed before quota checks')

    def preflight(self):
        self.guard()

    def pipeline_ready(self):
        self.guard()
        return dict(pipeline_reservations=True)

    def restore(self, lease):
        self.guard()
        self.restored.append(lease.execution_key)

    def restore_pipeline(self, key, token, size):
        from jasmine_web.batch.workspace_quota import WorkspaceQuotaSpace
        self.guard()
        if self.full:
            raise WorkspaceQuotaSpace(size + 1, size)
        self.promises.setdefault(key, (token, size))
        if self.promises[key] != (token, size):
            raise ValueError('Changed promise')


@unittest.skipUnless(os.environ.get('JASMINE_BATCH_TEST_DSN'), 'Use the disposable PostgreSQL proof')
class NativePipelineRestoreTests(unittest.TestCase):
    def setUp(self):
        from .test_backup import BackupRestoreTests
        self.f = BackupRestoreTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def protect_completed(self):
        lease = self.f.completed
        root = self.f.state / 'execution/visualiser-cache/summaries'
        for directory in (self.f.state/'execution', root.parent, root):
            directory.mkdir(mode=0o700, exist_ok=True)
        cache = SummaryCache(root)
        identity = dict(owner=self.f.owner, configuration=lease.configuration_id, seed='606',
            execution=dict(job=lease.job_id, attempt=lease.attempt_id, execution_key=lease.execution_key,
                           prepared=self.f.receipt['sha256']),
            calculation={'runner.cjs': 'a' * 64, 'calculation.cjs': 'b' * 64}, node='v22.fixture')
        metrics = self.f.root / 'protected-metric.json'
        metrics.write_bytes(b'[{"metric_value":1}]')
        metrics.chmod(0o600)
        with cache.entry(identity) as entry:
            self.assertTrue(entry.publish(metrics))
        now = time.time()
        plan = dict(comparison='c' * 64, created_at=now - 1, expires_at=now + 3600,
            configurations=[dict(configuration=lease.configuration_id, job=lease.job_id,
                                 attempt=lease.attempt_id, seeds=['606'])])
        cache.holds.replace(self.f.owner, self.f.exp, [plan], calculation=scope(identity), owner_entries=4)
        self.f.f.sql('''INSERT INTO visualiser_sources(attempt_id,pool_id,owner_id,identity,source)
            VALUES (%s,%s,%s,%s::jsonb,%s::jsonb)''',
            (lease.attempt_id,self.f.q.pool_id,self.f.owner,json.dumps(identity),json.dumps(dict(fictional=True))))

    def roundtrip(self, *, online=False, shortage=False, enabled=True, protected=False):
        from . import backup, backup_live
        from .maintenance import GATE
        if protected:
            self.protect_completed()
        if online:
            with redirect_stderr(io.StringIO()):
                backup_live.create(self.f.q, self.f.state, self.f.backup, self.f.tools)
        else:
            self.f.save()
        self.assertTrue(self.f.restore()['inactive'])
        gate = self.f.target / GATE
        quotas = Quotas(gate)
        quotas.full = shortage
        options = dict(image_check=lambda _: None, source_isolated=online,
                       workspace_quotas=quotas, pipeline_storage=enabled)
        if shortage:
            from jasmine_web.batch.workspace_quota import WorkspaceQuotaSpace
            with self.assertRaises(WorkspaceQuotaSpace):
                backup.activate(self.f.target_q, self.f.backup, self.f.target, **options)
            self.assertTrue(gate.exists())
            self.assertEqual(quotas.promises, {})
            quotas.full = False
        self.assertFalse(backup.activate(self.f.target_q, self.f.backup, self.f.target, **options)['inactive'])
        self.assertFalse(gate.exists())
        if enabled:
            from jasmine_web.batch.pipeline_storage import promise
            expected = {lease.execution_key: (promise(lease.execution_key, 1).token,
                                             promise(lease.execution_key, 1).size)
                        for lease in ((self.f.provider_completed,) if protected else
                                      (self.f.completed, self.f.provider_completed))}
            self.assertEqual(quotas.promises, expected)
        else:
            self.assertEqual(quotas.promises, {})
        with self.f.target_q._connection() as c:
            rows = c.execute('SELECT state FROM jobs ORDER BY id').fetchall()
        self.assertEqual(sorted(r['state'] for r in rows), ['queued', 'succeeded', 'succeeded'])

    def test_offline_restore_funds_unfinished_summaries_before_activation(self):
        self.roundtrip()

    def test_online_restore_funds_unfinished_summaries_before_activation(self):
        self.roundtrip(online=True)

    def test_shortage_keeps_offline_restoration_inactive_until_a_verified_retry(self):
        self.roundtrip(shortage=True)

    def test_shortage_keeps_online_restoration_inactive_until_a_verified_retry(self):
        self.roundtrip(online=True, shortage=True)

    def test_ordinary_backup_activation_keeps_pipeline_funding_disabled(self):
        self.roundtrip(enabled=False)

    def test_offline_complete_protected_summaries_need_no_duplicate_promise(self):
        self.roundtrip(protected=True)

    def test_online_complete_protected_summaries_need_no_duplicate_promise(self):
        self.roundtrip(online=True, protected=True)

    def test_protected_summary_check_does_not_mutate_files_or_break_activation_retry(self):
        self.roundtrip(shortage=True, protected=True)
