"""(C) Copyright 2026, by Ross Richardson

Recovery launcher settings and immutable model/heap integration checks.
@author ross richardson
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from deploy._workflow import frontend_path
import sys
sys.path.insert(0,str(frontend_path()))
from jasmine_web.batch.resource_recovery import RecoveryPolicy
from .local_web import parse_args
from .resource_recovery_settings import policy
from . import test_container_adapter


class ResourceRecoveryTests(unittest.TestCase):
    def test_local_launcher_opt_in_keeps_initial_model_defaults(self):
        args=parse_args(['serve','--console-codes'])
        self.assertIsNone(policy(args))
        args=parse_args(['serve','--console-codes','--resource-recovery'])
        self.assertEqual(policy(args),RecoveryPolicy())
        args.recovery_max_memory_mib=6144
        self.assertEqual(policy(args).max_memory_mib,6144)

    def test_invalid_operator_ceilings_and_boolean_numbers_are_rejected(self):
        args=parse_args(['serve','--console-codes','--resource-recovery'])
        for key,value in [('recovery_pressure_percent',100),('recovery_storage_multiplier',0),
                          ('recovery_max_memory_mib',True)]:
            changed=deepcopy(args); setattr(changed,key,value)
            with self.assertRaises(ValueError):policy(changed)

    def test_model_uses_frozen_retry_heap_without_changing_scientific_settings(self):
        fixture=test_container_adapter.ContainerAdapterTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        lease=fixture.lease
        spec=deepcopy(lease.specification)
        recovery=RecoveryPolicy().describe(spec,lease.resources,{run['id']:2048 for run in spec['run_sets']})
        spec['resource_recovery']=recovery
        grown=SimpleNamespace(**{**vars(lease),'specification':spec,
            'resources':{**lease.resources,'memory_mib':5120},'heap_mib':3072})
        command=fixture.adapter.container_command(grown,fixture.fixture.work)
        self.assertEqual(command.argv,('/bin/sh','/request/run.sh','3072m'))
        self.assertEqual((fixture.fixture.work/'run.yml').read_text(),fixture.fixture.configuration.native_yaml('savings-0001'))
        self.assertEqual(spec['run_sets'],lease.specification['run_sets'])


if __name__=='__main__':unittest.main()
