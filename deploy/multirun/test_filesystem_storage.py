"""(C) Copyright 2026, by Ross Richardson

VM opt-in and administrator reporting for independent storage filesystems.
No daemon, database, scientific rows or real user sessions are used locally.
@author ross richardson
"""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
from . import storage_budget
from . import test_pipeline_storage_config as config_fixture
from . import test_administrator_storage as admin_fixture


class FilesystemConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.f=config_fixture.PipelineConfigurationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_opt_in_leaves_cpu_memory_and_runtime_policy_unchanged(self):
        ordinary=self.f.f.config(self.f.enabled())
        new=self.f.f.config(self.f.enabled()+'\n[storage_admission]\nmode = "filesystem"\n')
        self.assertEqual(ordinary.storage_mode,'shared');self.assertEqual(new.storage_mode,'filesystem')
        for key in ('capacity','holdback','visualiser_max_workers','visualiser_seed_workers','runtime_setup_minutes'):
            self.assertEqual(getattr(new,key),getattr(ordinary,key))

    def test_requires_pipeline_quotas_and_rejects_unknown_mode(self):
        for body in (self.f.text+'\n[storage_admission]\nmode = "filesystem"\n',
                     self.f.enabled()+'\n[storage_admission]\nmode = "unlimited"\n'):
            with self.assertRaises(ValueError):self.f.f.config(body)

    def test_controller_derives_docker_location_from_the_fixed_local_daemon(self):
        with patch('jasmine_web.batch.docker_executor.DockerCLI') as cli:
            cli.return_value._json.return_value={'DockerRootDir':'/var/lib/docker','secret':'unrelated'}
            self.assertEqual(storage_budget.docker_storage_root(),Path('/var/lib/docker'))
            cli.return_value._json.assert_called_once_with('/info')
            cli.return_value._json.return_value['DockerRootDir']='../wrong'
            with self.assertRaises(ValueError):storage_budget.docker_storage_root()

    def test_filesystem_controller_cannot_be_constructed_for_nonquota_hosts(self):
        from jasmine_web.batch.filesystem_storage import FilesystemStorage
        args=SimpleNamespace(storage_mode='filesystem',dedicated_storage=False,visualiser_pipeline_storage=True)
        with self.assertRaises(ValueError):storage_budget.controller(args,Mock(),Path('/private/state'))


class FilesystemAdministratorTests(unittest.TestCase):
    def setUp(self):
        self.f=admin_fixture.AdministratorStorageTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_docker_capacity_is_reported_without_scanning_its_contents(self):
        from jasmine_web.batch.administration import validate_storage_report
        self.f.executor.workspace_quotas.ready=lambda:dict(remaining_reserved_bytes=123,
            reserve_bytes=456,pipeline_floor_bytes=12*1024**3)
        reader=self.f.reader(docker_root=lambda:self.f.root)
        observed=[]
        original=reader._scan_tree
        def scan(root,*args,**kw):observed.append(root);return original(root,*args,**kw)
        reader._scan_tree=scan
        result=validate_storage_report(self.f.settled(reader))
        self.assertTrue(any('docker' in row['areas'] for row in result['filesystems']))
        self.assertFalse(any(row['id']=='docker' for row in result['areas']))
        self.assertNotIn(self.f.root,observed)
        self.assertNotIn(str(self.f.root),str(result))
        self.assertEqual(result['commitments']['pipeline_floor_bytes'],12*1024**3)

    def test_failed_docker_read_is_unknown_without_hiding_other_filesystems(self):
        from jasmine_web.batch.administration import validate_storage_report
        reader=self.f.reader(docker_root=Mock(side_effect=PermissionError('PRIVATE')))
        value=validate_storage_report(self.f.settled(reader))
        row=next(r for r in value['filesystems'] if 'docker' in r['areas'])
        self.assertFalse(row['available']);self.assertIsNone(row['available_bytes'])
        self.assertTrue(any(r['available'] for r in value['filesystems']))
        self.assertNotIn('PRIVATE',str(value))


if __name__=='__main__':unittest.main()
