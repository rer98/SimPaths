"""(C) Copyright 2026, by Ross Richardson

Native backup/restore of review timelines, launch mode and private workload keys.
Fictional model outputs only; no scientific jobs, real email or external storage.
@author ross richardson
"""
from contextlib import redirect_stderr
import io
import unittest

from . import backup,backup_live
from .releases import atomic_json
from .test_backup import BackupRestoreTests,DSN


@unittest.skipUnless(DSN,'Use the disposable performance-monitoring proof')
class PerformanceRestoreTests(unittest.TestCase):
    def setUp(self):
        self.f=BackupRestoreTests();self.addCleanup(self.f.doCleanups);self.f.setUp()

    def roundtrip(self,online):
        from jasmine_web.batch.performance_review import Settings,set_control,read_control
        from jasmine_web.batch.performance_trace import ReviewTrace,trace_history
        f=self.f
        trace=ReviewTrace(f.q,f.state)
        fingerprint=trace.fingerprint(f.completed)
        atomic_json(f.state/'performance-platform.json',dict(provider='Fictional',plan='Recovery',storage_kind='ssd'))
        set_control(f.state,True,java=True)
        trace.emit('model',f.completed.attempt_id,'launch',job=f.completed.job_id,
            java_mode='enabled',workload_fingerprint=fingerprint)
        trace.emit('model',f.completed.attempt_id,'java',sequence=0,elapsed_ms=1,
            phase='finished',heap_used_bytes=1024,dropped=0)
        trace.flush(Settings())
        self.assertIsNone(trace.write_error)
        before=trace_history(f.q)
        with redirect_stderr(io.StringIO()):
            if online:backup_live.create(f.q,f.state,f.backup,f.tools)
            else:f.save()
        backup.verify(f.backup)
        backup.restore(f.target_q,f.backup,f.target,f.target_tools)
        after=trace_history(f.target_q)
        self.assertEqual(after['observations'],before['observations'])
        self.assertEqual(after['subjects'],before['subjects'])
        self.assertEqual(after['attempts'],before['attempts'])
        restored=ReviewTrace(f.target_q,f.target)
        self.assertEqual(restored.fingerprint(f.completed),fingerprint)
        self.assertEqual(restored.platform['provider'],'Fictional')
        self.assertEqual((f.target/'.performance-fingerprint-key').stat().st_mode&0o777,0o600)
        # Online snapshots deliberately omit live review controls. The operator
        # re-enables them on the replacement; an offline state copy retains them.
        self.assertEqual(read_control(f.target,java=True)['enabled'],not online)
        from .maintenance import GATE
        self.assertTrue((f.target/GATE).exists())

    def test_offline_restore_preserves_measurements_and_workload_comparability(self):
        self.roundtrip(False)

    def test_online_restore_preserves_measurements_without_enabling_new_profiling(self):
        self.roundtrip(True)
