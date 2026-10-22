"""(C) Copyright 2026, by Ross Richardson

Operator capacity controls, real runtime restart and native backup/restore proofs.
Fictional jobs and measurements only; no scientific simulations or real emails.
@author ross richardson
"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
sys.path.insert(0,str(frontend_path()/'tests/batch'))
from jasmine_web.batch import compute_capacity as budget
from jasmine_web.batch.policy import Conflict,Resources
from . import compute_capacity as operator
from . import test_backup as backups, test_vm_runtime as vm_tests

DSN=backups.DSN


def host(**changes):
    return dict(dict(format='jasmine.compute-host.v1',host='a'*64,boot='b'*64,
        cgroup='/sys/fs/cgroup/system.slice',online_cpus=12,cpu_millis=12000,
        memory_mib=32768,limits=[]),**changes)


class ComputeOperatorTests(unittest.TestCase):
    def test_initial_opt_in_and_restart_validation_are_independent_of_monitoring(self):
        queue=Mock();capacity=Resources(8000,16000,32000)
        settings=dict(enabled=True,cgroup='/sys/fs/cgroup/system.slice',reserve_cpu_millis=0,reserve_memory_mib=0)
        args=SimpleNamespace(compute_capacity=settings,operational_metrics=False)
        with patch.object(budget,'initialise') as initialise,patch.object(budget,'observe') as observe:
            operator.bootstrap(args,queue,capacity)
            initialise.assert_called_once_with(queue,capacity,cgroup=settings['cgroup'],reserve_cpu_millis=0,reserve_memory_mib=0)
            observe.assert_called_once_with(queue)
        with patch.object(budget,'initialise') as initialise,patch.object(budget,'observe') as observe:
            operator.bootstrap(SimpleNamespace(),queue,capacity)
            initialise.assert_not_called();observe.assert_called_once_with(queue)

    def test_operator_errors_do_not_print_credentials_or_trigger_migrations(self):
        from . import privilege_config
        output=io.StringIO()
        with (patch.object(privilege_config,'trusted_installation',side_effect=ValueError('PRIVATE PASSWORD')),
                redirect_stdout(output)):
            code=operator.main(['--config','/private/settings.toml','status'])
        self.assertEqual(code,1)
        self.assertNotIn('PASSWORD',output.getvalue())


@unittest.skipUnless(DSN,'Use the disposable compute-capacity proof')
class ComputeRuntimeTests(unittest.TestCase):
    def test_runtime_restarts_using_original_baseline_and_preserves_the_approved_budget(self):
        from . import runtime
        f=vm_tests.VMRuntimeTests();self.addCleanup(f.doCleanups);f.setUp()
        base=Resources(2000,5120,12288)
        current=host();reader=lambda scope:dict(current,cgroup=scope)
        budget.initialise(f.q,base,reader=reader)
        current.update(cpu_millis=20000,memory_mib=65536)
        proposal=budget.preview(f.q,reader=reader)
        budget.apply(f.q,proposal['proposal'],reader=reader)
        with patch.object(budget,'measure',side_effect=reader):
            runtime.create_registry(f.args,f.q,f.state,f.deliver,capacity=base)
        self.assertEqual(budget.status(f.q)['approved'],proposal['target'])
        # Monitoring remains off; this validation is part of assembly itself.
        current['memory_mib']=20000
        with patch.object(budget,'measure',side_effect=reader):
            runtime.create_registry(f.args,f.q,f.state,f.deliver,capacity=base)
        self.assertFalse(budget.status(f.q)['admission_allowed'])


@unittest.skipUnless(DSN,'Use the disposable compute-capacity proof')
class ComputeRestoreTests(unittest.TestCase):
    def roundtrip(self,online):
        from . import backup_live
        from .maintenance import GATE,service_state
        f=backups.BackupRestoreTests();self.addCleanup(f.doCleanups);f.setUp()
        f.q.backup_state=f.state
        current=host();reader=lambda scope:dict(current,cgroup=scope)
        budget.initialise(f.q,Resources(8000,16000,32000),reader=reader)
        current.update(cpu_millis=20000,memory_mib=65536)
        proposal=budget.preview(f.q,reader=reader)
        budget.apply(f.q,proposal['proposal'],reader=reader)
        # Also preserve an unapproved proposal; a changed target host must not
        # be able to reuse an approval prepared on this source machine.
        current.update(cpu_millis=24000,memory_mib=98304)
        pending=budget.preview(f.q,reader=reader)
        if online:
            with service_state(f.state):backup_live.create(f.q,f.state,f.backup,f.tools)
        else:f.save()
        f.restore()
        self.assertTrue((f.target/GATE).exists())
        result=budget.status(f.target_q)
        self.assertEqual(result['approved'],proposal['target'])
        self.assertEqual(len(result['proposals']),2)
        self.assertEqual(sum(row['applied_at'] is not None for row in result['proposals']),1)
        current.update(host='c'*64,boot='d'*64)
        with self.assertRaises(Conflict):budget.apply(f.target_q,pending['proposal'],reader=reader)
        # Smaller restored hardware pauses new admissions, preserving the saved
        # policy and source host. Activation remains a separate existing gate.
        current.update(cpu_millis=12000,memory_mib=32768)
        budget.observe(f.target_q,reader=reader)
        self.assertFalse(budget.status(f.target_q)['admission_allowed'])
        self.assertTrue(budget.status(f.q)['admission_allowed'])
        self.assertEqual(budget.status(f.target_q)['approved'],proposal['target'])

    def test_offline_backup_preserves_approval_history_and_revalidates_restored_capacity(self):
        self.roundtrip(False)

    def test_online_backup_preserves_approval_history_and_revalidates_restored_capacity(self):
        self.roundtrip(True)
