"""(C) Copyright 2026, by Ross Richardson

Native quota selection and inactive restored-file protection from frozen rows.
These isolated tests do not mount filesystems or contact a privileged broker.
@author ross richardson
"""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
from jasmine_web.batch.workspace_quota import WorkspaceQuotaUnavailable
from deploy.multirun.artifacts import ArtifactError
from deploy.multirun.backup import restore_workspace_quotas
from deploy.multirun.vm_web import workspace_quotas

KEY='batch-00000000-0000-0000-0000-000000000001'


class WorkspaceIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.state=Path(temporary.name)
        self.options=SimpleNamespace(state=self.state,dedicated_storage=True,
            workspace_quota_socket=Path('/run/jasmine-workspace-quotas/broker.sock'),
            workspace_guard=Path('/usr/local/libexec/jasmine-workspace-guard'))
        self.queue=SimpleNamespace(schema='test_quota',pool_id='test')
        self.connection=Mock()
        self.connection.execute.return_value.fetchall.return_value=[dict(execution_key=KEY,resources=dict(storage_mib=5632))]
        self.quotas=Mock()

    def test_native_dedicated_volume_requires_broker_and_private_staging_is_explicit(self):
        client=workspace_quotas(self.options)
        self.assertEqual(client.root,self.state/'execution')
        self.assertEqual(client.guard_path,self.options.workspace_guard)
        self.options.dedicated_storage=False
        with patch('socket.socket',side_effect=AssertionError('staging does not claim kernel enforcement')):
            self.assertIsNone(workspace_quotas(self.options))

    def test_restore_uses_frozen_attempt_bytes_without_rewriting_configuration(self):
        root=self.state/'execution'/KEY; root.mkdir(parents=True,mode=0o700)
        saved=root/'saved'; saved.write_bytes(b'original bytes')
        restore_workspace_quotas(self.connection,self.queue,self.state,self.quotas)
        self.quotas.preflight.assert_called_once(); self.quotas.guard.assert_called_once()
        lease=self.quotas.restore.call_args.args[0]
        self.assertEqual(lease.execution_key,KEY); self.assertEqual(lease.resources['storage_mib'],5632)
        self.assertEqual(saved.read_bytes(),b'original bytes')

    def test_restore_broker_failure_propagates_without_a_monitoring_fallback(self):
        self.quotas.preflight.side_effect=WorkspaceQuotaUnavailable('offline')
        with self.assertRaises(WorkspaceQuotaUnavailable):
            restore_workspace_quotas(self.connection,self.queue,self.state,self.quotas)
        self.quotas.restore.assert_not_called(); self.connection.execute.assert_not_called()

    def test_restore_rejects_linked_or_orphan_prepared_artifact(self):
        target=self.state/'artifacts'/KEY; target.mkdir(parents=True,mode=0o700)
        with self.assertRaises(ArtifactError): restore_workspace_quotas(self.connection,self.queue,self.state,self.quotas)
        (self.state/'execution').mkdir()
        (self.state/'execution'/KEY).symlink_to(target)
        with self.assertRaises(ArtifactError): restore_workspace_quotas(self.connection,self.queue,self.state,self.quotas)
        self.quotas.restore.assert_not_called()


if __name__=='__main__':unittest.main()
