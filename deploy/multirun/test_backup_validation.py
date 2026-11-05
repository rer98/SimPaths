"""(C) Copyright 2026, by Ross Richardson

Validation history survives native recovery without restarting completed models.
Only fictional files and a disposable PostgreSQL database are used.
@author ross richardson
"""
import unittest
from .test_backup import DSN
from .test_backup_live import LiveBackupTests


@unittest.skipUnless(DSN,'Use the disposable backup proof')
class ValidationRestoreTests(unittest.TestCase):
    def setUp(self):
        self.f=LiveBackupTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_live_snapshot_retains_validation_history_and_requires_review_without_scientific_retry(self):
        q=self.f.q;lease=self.f.active()
        now=self.f.f.f.sql('SELECT clock_timestamp() AS now')[0]['now']
        record=q.begin_validation(lease,model_finished_at=now,stop_evidence='a'*64)
        self.f.save();self.f.restore()
        target=self.f.f.target_q
        with target._connection() as c:
            restored=c.execute('SELECT * FROM attempt_validations WHERE attempt_id=%s',(lease.attempt_id,)).fetchone()
            job=c.execute('SELECT state,attempts FROM jobs WHERE id=%s',(lease.job_id,)).fetchone()
            reservation=c.execute('SELECT released_at FROM reservations WHERE attempt_id=%s',(lease.attempt_id,)).fetchone()
        self.assertEqual(restored['deadline'],record['deadline'])
        self.assertEqual(restored['model_finished_at'],record['model_finished_at'])
        self.assertEqual(job,dict(state='review',attempts=1))
        self.assertIsNotNone(reservation['released_at'])
        from .maintenance import GATE
        self.assertTrue((self.f.f.target/GATE).exists())


if __name__=='__main__':unittest.main()
