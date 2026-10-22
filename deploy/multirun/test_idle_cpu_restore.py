"""(C) Copyright 2026, by Ross Richardson

Native backup/restore preserves the optional idle CPU policy and clears stale
online launch recall without altering the source service. Fictional data only.
@author ross richardson
"""
from contextlib import redirect_stderr
import io
import unittest

from . import backup,backup_live
from .maintenance import GATE,service_state
from .test_backup import BackupRestoreTests,DSN


@unittest.skipUnless(DSN,'Use the disposable idle CPU loan proof')
class IdleCPURestoreTests(unittest.TestCase):
    def setUp(self):
        self.f=BackupRestoreTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def check_restore(self, online):
        f=self.f
        from jasmine_web.batch.idle_interactive_cpu import configure
        configure(f.q,True)
        with f.q._connection() as c:
            c.execute("UPDATE pools SET interactive_cpu_recall_until=clock_timestamp()+interval '30 seconds'")
            before=c.execute('SELECT idle_interactive_cpu,interactive_cpu_recall_until FROM pools').fetchone()
        with redirect_stderr(io.StringIO()):
            if online:
                with service_state(f.state): backup_live.create(f.q,f.state,f.backup,f.tools)
            else: backup.create(f.q,f.state,f.backup,f.tools)
        backup.restore(f.target_q,f.backup,f.target,f.target_tools)
        with f.target_q._connection() as c:
            restored=c.execute('SELECT idle_interactive_cpu,interactive_cpu_recall_until FROM pools').fetchone()
        self.assertTrue(restored['idle_interactive_cpu'])
        self.assertEqual(restored['interactive_cpu_recall_until'],None if online else before['interactive_cpu_recall_until'])
        self.assertTrue((f.target/GATE).exists())
        with f.q._connection() as c:
            self.assertEqual(before,c.execute('SELECT idle_interactive_cpu,interactive_cpu_recall_until FROM pools').fetchone())

    def test_online_restore_preserves_policy_and_clears_old_launch_intent(self):
        self.check_restore(True)

    def test_offline_restore_preserves_policy_and_bounded_recall_record(self):
        self.check_restore(False)
