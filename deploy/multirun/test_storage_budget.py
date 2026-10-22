"""(C) Copyright 2026, by Ross Richardson

VM storage-budget controls, admission-hook wiring and native backup/recovery.
Uses fictional jobs and private temporary files, never scientific simulations.
@author ross richardson
"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy._workflow import frontend_path
sys.path.insert(0, str(frontend_path()))
from jasmine_web.batch.storage_budget import StorageBudget, current, read_control
from jasmine_web.batch.policy import Conflict, Policy, Resources
from . import storage_budget


class OperatorControlTests(unittest.TestCase):
    def test_invalid_and_mixed_connection_settings_are_rejected_before_contacting_resources(self):
        for arguments in (['--config', '/private/config', '--state', '/private/state', 'status'],
                          ['--frontend', '/source', 'status']):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                storage_budget.main(arguments)

    def test_local_operator_failure_does_not_expose_private_error_text(self):
        arguments = ['--frontend', str(frontend_path()), '--state', '/private/state',
                     '--private-root', '/private', '--dsn-file', '/private/dsn', 'status']
        result = io.StringIO()
        with patch('deploy.multirun.vm_web.private_text', side_effect=RuntimeError('PRIVATE_CREDENTIAL')), \
                contextlib.redirect_stderr(result):
            self.assertEqual(storage_budget.main(arguments), 1)
        self.assertNotIn('PRIVATE_CREDENTIAL', result.getvalue())

    def test_controller_uses_actual_private_root_and_dedicated_storage_mode(self):
        with patch('jasmine_web.batch.storage_budget.StorageBudget') as controller:
            storage_budget.controller(SimpleNamespace(private_root=Path('/private'),
                dedicated_storage=True), Mock(), Path('/private/state'))
        self.assertEqual(controller.call_args.args[2], Path('/private'))
        self.assertTrue(controller.call_args.kwargs['dedicated'])

    def test_operator_write_obeys_inactive_restore_gate_before_configuring_capacity(self):
        from .artifacts import ArtifactError
        arguments = ['--frontend', str(frontend_path()), '--state', '/private/state',
                     '--private-root', '/private', '--dsn-file', '/private/dsn', 'follow']
        result = io.StringIO()
        with patch('deploy.multirun.vm_web.private_text', return_value='fictional-dsn'), \
                patch('jasmine_web.batch.backup.database_guard', return_value=contextlib.nullcontext()), \
                patch('deploy.multirun.maintenance.service_state', side_effect=ArtifactError('PRIVATE_RESTORE')), \
                patch.object(storage_budget, 'controller') as controller, contextlib.redirect_stderr(result):
            self.assertEqual(storage_budget.main(arguments), 1)
        controller.assert_not_called()
        self.assertNotIn('PRIVATE_RESTORE', result.getvalue())


DSN = os.environ.get('JASMINE_BATCH_TEST_DSN')


@unittest.skipUnless(DSN, 'Use storage_budget_proof.py with disposable PostgreSQL')
class NativeStorageBudgetRestoreTests(unittest.TestCase):
    def setUp(self):
        from .test_backup_live import LiveBackupTests
        self.f = LiveBackupTests()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.q, self.state = self.f.q, self.f.state
        with self.q._connection() as connection:
            self.base = connection.execute('SELECT * FROM pools WHERE id=%s', (self.q.pool_id,)).fetchone()
        self.capacity = self.base['storage_mib']+10000
        self.controller = StorageBudget(self.q, self.state, self.state.parent, dedicated=False,
            reader=lambda: dict(capacity_mib=self.capacity, total_bytes=self.capacity*1024**2,
                                reserved_bytes=0, free_bytes=100*1024**2, device=1, inode=2))
        self.control = self.controller.configure('follow')['control']
        self.capacity += 16000
        self.assertTrue(self.controller.tick(force=True))
        self.expected = self.base['storage_mib']+16000

    def restart(self, queue, state, measurement):
        controller = StorageBudget(queue, state, state.parent, dedicated=False, reader=lambda: measurement)
        queue.create_pool(Resources(self.base['cpu_millis'], self.base['memory_mib'], self.base['storage_mib']),
            holdback=dict(cpu_millis=self.base['hold_cpu'], memory_mib=self.base['hold_memory'],
                          storage_mib=self.base['hold_storage']), policy=Policy(**self.base['policy']),
            storage_budget_mib=controller.bootstrap())

    def test_online_backup_keeps_matching_live_control_and_restores_effective_budget(self):
        from jasmine_web.batch.backup_guard import BackupBusy
        def during():
            with self.assertRaises(BackupBusy):
                self.controller.configure('fixed')
        self.f.save(after_snapshot=during)
        self.f.restore()
        target = self.f.f.target
        restored = self.f.f.target_q
        self.assertEqual(read_control(target), self.control)
        self.assertEqual(current(restored), self.expected)
        self.restart(restored, target, dict(capacity_mib=self.capacity, device=100, inode=200))
        self.assertEqual(current(restored), self.expected)
        self.restart(restored, target, dict(capacity_mib=self.capacity+8000, device=100, inode=200))
        self.assertEqual(current(restored), self.expected+8000)

    def test_offline_backup_restores_controls_and_smaller_target_is_gated(self):
        from . import backup
        backup.create(self.q, self.state, self.f.f.backup, self.f.f.tools)
        self.f.restore()
        restored, state = self.f.f.target_q, self.f.f.target
        self.assertEqual(read_control(state), self.control)
        self.assertEqual(current(restored), self.expected)
        with self.assertRaises(Conflict):
            self.restart(restored, state, dict(capacity_mib=self.capacity-20000, device=100, inode=200))
        self.assertEqual(current(restored), self.expected)


@unittest.skipUnless(DSN, 'Use storage_budget_proof.py with disposable PostgreSQL')
class NativeStorageBudgetRuntimeTests(unittest.TestCase):
    def test_dispatcher_follows_growth_while_original_process_and_attempt_continue(self):
        from . import runtime
        from .test_vm_runtime import VMRuntimeTests, until
        from adapter_fixture import DummyAdapter
        from jasmine_web.batch.local_executor import Command
        from starlette.testclient import TestClient

        fixture = VMRuntimeTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        dataset = fixture.dataset()
        other = fixture.access.approve_email('bob@example.org')
        fixture.q.grant_dataset(other, dataset)
        gate = fixture.state/'release-fictional-workers'
        self.addCleanup(lambda: gate.touch(exist_ok=True) if gate.parent.exists() else None)

        class GatedAdapter(DummyAdapter):
            def command(self, lease, work):
                command = super().command(lease, work)
                script = ('import pathlib,runpy,sys,time\n'
                          'deadline=time.monotonic()+30\n'
                          'while not pathlib.Path(sys.argv[1]).exists():\n'
                          ' if time.monotonic()>deadline: raise SystemExit(1)\n'
                          ' time.sleep(.02)\n'
                          'runpy.run_path(sys.argv[2],run_name="__main__")\n')
                return Command((sys.executable, '-c', script, str(gate), command.argv[1]), {})

        adapter = GatedAdapter(delay=.01)
        adapter.preparation = SimpleNamespace(retire_failed=lambda *a, **kw: None)
        capacity = [22288]
        controller = StorageBudget(fixture.q, fixture.state, fixture.state.parent, dedicated=False,
            reader=lambda: dict(capacity_mib=capacity[0], device=1, inode=2,
                                total_bytes=capacity[0]*1024**2, reserved_bytes=0, free_bytes=20000*1024**2))
        controller.configure('follow', headroom_mib=10000)
        experiments = [fixture.q.submit(owner, 'fictional-'+str(number), label='Storage growth',
            model_digest=fixture.image, dataset_id=dataset, seed_plan=['606'],
            run_sets=[dict(id='baseline', parameters=dict(name='Baseline'))],
            resources=Resources(1000, 1000, 8000)) for number, owner in enumerate((fixture.owner, other))]

        def states():
            return [fixture.q.inspect(owner, experiment)['jobs'][0]['state']
                    for owner, experiment in zip((fixture.owner, other), experiments)]

        def first_attempt():
            with fixture.q._connection() as connection:
                return connection.execute('''SELECT id,execution_key,initial_resources,current_resources
                    FROM attempts WHERE job_id IN (SELECT id FROM jobs WHERE experiment_id=%s)''',
                    (experiments[0],)).fetchone()

        with patch.object(runtime, 'DispatchAdapter', return_value=adapter), \
                patch('deploy.multirun.storage_budget.controller', return_value=controller):
            application = fixture.app()
        with TestClient(application, base_url=fixture.origin):
            try:
                until(lambda: states() == ['active', 'queued'])
                original = first_attempt()
                self.assertIsNotNone(original)
                receipt = fixture.state/'execution'/original['execution_key']/'process.json'
                until(receipt.exists)
                original_process = json.loads(receipt.read_text())
                capacity[0] += 8000
                controller.next_check = 0
                until(lambda: states() == ['active', 'active'])
                self.assertEqual(first_attempt(), original)
                self.assertEqual(json.loads(receipt.read_text()), original_process)
                self.assertFalse((receipt.parent/'exit.json').exists())
                self.assertEqual(current(fixture.q), 20288)
            finally:
                gate.touch(exist_ok=True)
                until(lambda: states() == ['succeeded', 'succeeded'])
        with fixture.q._connection() as connection:
            attempts = connection.execute('SELECT number,outcome FROM attempts ORDER BY created_at').fetchall()
        self.assertEqual(attempts, [dict(number=1, outcome='success'), dict(number=1, outcome='success')])
