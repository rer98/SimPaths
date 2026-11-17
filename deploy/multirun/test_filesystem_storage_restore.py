"""(C) Copyright 2026, by Ross Richardson

Disposable database/file backup/restore of filesystem admission and startup gating.
Only fictional inputs and a temporary PostgreSQL cluster are used.
@author ross richardson
"""
import os
import sys
import unittest
from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
from jasmine_web.batch.filesystem_storage import FilesystemStorage
from tests.test_filesystem_storage import measured


@unittest.skipUnless(os.environ.get('JASMINE_BATCH_TEST_DSN'),'Use disposable PostgreSQL proof')
class FilesystemRestoreTests(unittest.TestCase):
    def test_restored_mode_is_preserved_but_capacity_and_layout_are_measured_again(self):
        from .test_backup_live import LiveBackupTests
        f=LiveBackupTests();self.addCleanup(f.doCleanups);f.setUp()
        with f.q._connection() as c:
            pool=c.execute('SELECT * FROM pools WHERE id=%s',(f.q.pool_id,)).fetchone()
            c.execute('UPDATE pools SET update_admission_allowed=false WHERE id=%s',(f.q.pool_id,))
        value=measured(capacity_mib=pool['storage_mib']+14000)
        controller=FilesystemStorage(f.q,f.state,lambda:dict(value))
        controller.activate(expected_storage_mib=pool['storage_mib']);self.assertTrue(controller.tick(force=True))
        f.save();f.restore()
        restored=f.f.target_q
        with restored._connection() as c:
            row=c.execute('SELECT * FROM pools WHERE id=%s',(restored.pool_id,)).fetchone()
        self.assertEqual(row['storage_mode'],'filesystem')
        self.assertEqual(row['storage_mib'],pool['storage_mib']+10000)
        value['device']=100;value['docker_device']=200
        fresh=FilesystemStorage(restored,f.f.target,lambda:dict(value))
        self.assertTrue(fresh.tick(force=True))
        value['capacity_mib']-=1
        self.assertFalse(fresh.tick(force=True))
        with restored._connection() as c:
            after=c.execute('SELECT * FROM pools WHERE id=%s',(restored.pool_id,)).fetchone()
        self.assertEqual(after['storage_mib'],row['storage_mib'])
        self.assertFalse(after['storage_admission_allowed'])


if __name__=='__main__':unittest.main()
