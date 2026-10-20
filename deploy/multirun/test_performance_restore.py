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
        from jasmine_web.batch import performance_exports as exports,performance_archive as archive
        f=self.f
        export_settings=exports.Settings(enabled=True,mail_enabled=True)
        exports.set_control(f.state,enabled=True,mail_enabled=True)
        exports.configure(f.q,export_settings)
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
        exports.snapshot_context(f.q)
        exports.build_batch(f.q,export_settings,force=True)
        # Both a durable attachment and a not-yet-packed change must survive.
        trace.emit('model',f.completed.attempt_id,'sample',cpu_ns=123,working_memory_bytes=2048)
        trace.flush(Settings())
        archived=lambda queue:[bytes(r['payload']) for r in self.rows(queue,'SELECT payload FROM performance_export_parts ORDER BY batch_id,number')]
        original_parts=archived(f.q)
        pending=self.rows(f.q,'SELECT epoch,revision,dataset,record_key,payload,sha256 FROM performance_export_changes ORDER BY revision')
        source=exports.status(f.q)
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
        self.assertEqual(archived(f.target_q),original_parts)
        self.assertEqual(self.rows(f.target_q,'SELECT epoch,revision,dataset,record_key,payload,sha256 FROM performance_export_changes ORDER BY revision'),pending)
        self.assertEqual(exports.status(f.target_q),source)
        restored=ReviewTrace(f.target_q,f.target)
        self.assertEqual(restored.fingerprint(f.completed),fingerprint)
        self.assertEqual(restored.platform['provider'],'Fictional')
        self.assertEqual((f.target/'.performance-fingerprint-key').stat().st_mode&0o777,0o600)
        # Online snapshots deliberately omit live review controls. The operator
        # re-enables them on the replacement; an offline state copy retains them.
        self.assertEqual(read_control(f.target,java=True)['enabled'],not online)
        from .maintenance import GATE
        self.assertTrue((f.target/GATE).exists())
        # Pack the restored prefix, then give newly captured measurements a child
        # epoch. The archive already imported by the administrator is reused.
        exports.build_batch(f.target_q,export_settings,force=True)
        exports.configure(f.target_q,export_settings,rotate=True)
        restored.emit('model',f.completed.attempt_id,'sample',cpu_ns=321,working_memory_bytes=4096)
        restored.flush(Settings());self.assertIsNone(restored.write_error)
        child=exports.build_batch(f.target_q,export_settings,force=True)
        self.assertEqual(child['parent'],source['epoch'])
        calls=[]
        self.assertTrue(exports.deliver_one(f.target_q,export_settings,'admin@example.invalid',lambda *a:calls.append(a),force=True))
        c=archive.connect(f.target.parent/'monitoring-record.sqlite')
        try:
            for part in original_parts+archived(f.target_q):archive.import_part(c,part)
            report=archive.report(c)
            self.assertTrue(report['coverage']['complete'],report['coverage'])
            self.assertEqual(report['coverage']['observations'],4)
            self.assertTrue(calls)
        finally:c.close()

    def rows(self,queue,query):
        with queue._connection() as connection:return connection.execute(query).fetchall()

    def test_offline_restore_preserves_measurements_and_workload_comparability(self):
        self.roundtrip(False)

    def test_online_restore_preserves_measurements_without_enabling_new_profiling(self):
        self.roundtrip(True)
