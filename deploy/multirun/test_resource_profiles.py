"""(C) Copyright 2026, by Ross Richardson

Workload envelope selection and frozen initial heap/container/storage contracts.
All allocations here are synthetic fixtures, not production recommendations.
@author ross richardson
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from .artifacts import ArtifactError
from .browser_model import BrowserModel
from .configuration import normalise
from .container_adapter import SimPathsContainerAdapter
from .prepared_dataset import allocation
from .queue_adapter import read_prepared
from .resource_policy import (DEFAULT_POLICY, LEGACY_POLICY, PROFILE_FORMAT, check_policy,
    select_profile, simulation_resources, starting_allocation, workload)
from .schema import COLLECTOR_FIELDS, DeploymentLimitError
from . import test_submission_adapter


def fixture_policy():
    collectors={key:field.default for key,field in COLLECTOR_FIELDS.items()}
    small=dict(id='small-fixture',max_population=20000,max_years=8,max_repetitions=12,
        collectors=collectors,cpu_millis=2000,memory_mib=4096,heap_mib=2048,native_headroom_mib=2048,
        storage=dict(setup_mib=4096,per_repetition_mib=256))
    large=dict(deepcopy(small),id='large-fixture',max_population=100000,max_years=52,
               memory_mib=6144,heap_mib=4096,storage=dict(setup_mib=4096,per_repetition_mib=4096))
    return dict(deepcopy(DEFAULT_POLICY),format=PROFILE_FORMAT,profiles=[small,large])


class ProfilePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy=fixture_policy()
        self.context=dict(population=20000,years=8,repetitions=12,
                          collectors=deepcopy(self.policy['profiles'][0]['collectors']))

    def test_population_years_and_repetitions_choose_the_smallest_covering_profile(self):
        self.assertEqual(select_profile(self.policy,**self.context)['id'],'small-fixture')
        for change in (dict(population=50000),dict(years=52)):
            self.assertEqual(select_profile(self.policy,**(self.context|change))['id'],'large-fixture')
        self.assertEqual(simulation_resources(self.policy,**self.context),
                         dict(cpu_millis=2000,memory_mib=4096,storage_mib=7168))
        self.assertEqual(simulation_resources(self.policy,**(self.context|dict(population=100000,years=52))),
                         dict(cpu_millis=2000,memory_mib=6144,storage_mib=53248))

    def test_unknown_or_incomplete_workloads_are_rejected_without_fallback(self):
        for change in (dict(population=100001),dict(years=53),dict(repetitions=13),
                       dict(collectors=self.context['collectors']|dict(persistHouseholds=False))):
            with self.subTest(change=change),self.assertRaises(ValueError) as error:
                select_profile(self.policy,**(self.context|change))
            self.assertEqual(error.exception.supported_limit,'unprofiled')
        with self.assertRaises(ArtifactError):select_profile(self.policy,population=20000,repetitions=3)

    def test_ambiguous_or_duplicate_envelopes_are_rejected_independent_of_order(self):
        self.policy['profiles'][1]['max_population']=10000
        for profiles in (self.policy['profiles'],list(reversed(self.policy['profiles']))):
            with self.assertRaises(ArtifactError):check_policy(self.policy|dict(profiles=profiles))
        self.policy['profiles'][1]=deepcopy(self.policy['profiles'][0])
        with self.assertRaises(ArtifactError):check_policy(self.policy)

    def test_collector_contract_types_and_headroom_cannot_be_bypassed(self):
        changes=[dict(memory_mib=4096),dict(heap_mib=True),dict(native_headroom_mib=0),
                 dict(collectors={'persistPersons':True}),
                 dict(collectors=self.context['collectors']|dict(exportToCSV=1)),
                 dict(collectors=self.context['collectors']|dict(dataDumpTimePeriod=float('inf')))]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ArtifactError):
                check_policy(self.policy|dict(profiles=[self.policy['profiles'][1]|change]))

    def test_larger_prepared_inputs_raise_fixed_storage_without_reducing_repetition_allowance(self):
        resources=simulation_resources(self.policy,**self.context,minimum_setup_bytes=8192*1024**2+1)
        self.assertEqual(resources['storage_mib'],8193+12*256)

    def test_version_one_and_legacy_defaults_are_unchanged(self):
        before=deepcopy(DEFAULT_POLICY)
        self.assertIsNone(starting_allocation(DEFAULT_POLICY,**self.context))
        self.assertEqual(simulation_resources(DEFAULT_POLICY,population=50000,repetitions=12),
                         dict(cpu_millis=2000,memory_mib=5120,storage_mib=7168))
        self.assertEqual(simulation_resources(LEGACY_POLICY,population=50000,repetitions=12)['storage_mib'],10240)
        self.assertEqual(DEFAULT_POLICY,before)

    def test_explicit_profiles_accept_fractional_quotas_without_changing_defaults(self):
        before = deepcopy(DEFAULT_POLICY)
        for cpu_millis in (1000,1500,2000):
            changed = deepcopy(self.policy)
            changed['profiles'][0]['cpu_millis'] = cpu_millis
            self.assertEqual(simulation_resources(changed, **self.context)['cpu_millis'], cpu_millis)
            changed['simulation']['cpu_millis'] = cpu_millis
            self.assertEqual(check_policy(changed)['simulation']['cpu_millis'], cpu_millis)
        for value in (999,True,1.5):
            changed = deepcopy(self.policy);changed['profiles'][0]['cpu_millis'] = value
            with self.assertRaises(ArtifactError):check_policy(changed)
            changed = deepcopy(DEFAULT_POLICY);changed['simulation']['cpu_millis'] = value
            with self.assertRaises(ArtifactError):check_policy(changed)
        self.assertEqual(DEFAULT_POLICY,before)

    def test_policy_hash_binds_the_full_envelope_and_inputs_are_not_mutated(self):
        first=starting_allocation(self.policy,**self.context)
        self.policy['profiles'][0]['max_years']=7
        second=starting_allocation(self.policy,**(self.context|dict(years=7)))
        self.assertNotEqual(first['policy_sha256'],second['policy_sha256'])
        self.assertEqual(first['heap_mib'],2048)

    def test_live_growth_and_cause_specific_retries_can_use_the_frozen_starting_heap(self):
        from jasmine_web.batch.resource_recovery import RecoveryPolicy, target
        context=self.context|dict(population=100000,years=52,repetitions=2)
        resources=simulation_resources(self.policy,**context)
        initial=starting_allocation(self.policy,**context)
        spec=dict(seeds=['606','607'],run_sets=[dict(id='baseline',execution=dict(resources=resources),starting_allocation=initial)])
        recovery=RecoveryPolicy(max_memory_mib=8192,max_heap_mib=6144)
        spec['resource_recovery']=recovery.describe(spec,resources,{'baseline':4096})
        memory,heap=target(spec,'baseline',resources,4096,'memory')
        self.assertEqual((memory['memory_mib'],heap),(7168,4096))
        larger,heap=target(spec,'baseline',resources,4096,'heap')
        self.assertEqual((larger['memory_mib'],heap),(7168,5120))
        storage,heap=target(spec,'baseline',resources,4096,'storage')
        self.assertGreater(storage['storage_mib'],resources['storage_mib'])
        self.assertEqual(storage['memory_mib'],resources['memory_mib'])


class ProfileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f=test_submission_adapter.SubmissionAdapterTests();self.addCleanup(self.f.doCleanups);self.f.setUp()
        self.policy=fixture_policy()
        self.key,_=self.f.versioned_plan(self.policy)
        self.config=self.f.pending_config();self.config['model_release']=self.key
        self.config['common'].update(population=50000,end_year=2026)
        self.model=self.f.model

    def prepared(self):
        self.f.result();self.f.adapter.validate(self.f.lease,self.f.work)
        target=self.f.adapter.artifacts/self.f.lease.execution_key
        receipt=read_prepared(target)
        return dict(dataset_id='pending-owned',location=str(target),model_digest=self.f.image,
                    prepared_fingerprint=receipt['sha256']),receipt

    def test_review_and_preparation_keep_the_original_profile_after_operator_defaults_change(self):
        original=self.model.experiment(self.f.pending(),dict(configuration=self.config))
        run=original['run_sets'][0]
        self.assertEqual(run['starting_allocation']['heap_mib'],4096)
        self.assertEqual(run['execution']['resources']['memory_mib'],6144)
        self.f.releases[self.key]['resource_policy']=deepcopy(DEFAULT_POLICY)
        resolved,receipt=self.prepared()
        refreshed,resources=self.model.bind_run(resolved,run,original['seed_plan'])
        self.assertEqual(refreshed['starting_allocation'],run['starting_allocation'])
        self.assertEqual(resources.__dict__,run['execution']['resources'])
        self.assertEqual(self.model.heap_limits(dict(run_sets=[refreshed])),{run['id']:4096})
        self.assertEqual(allocation(receipt,resource_policy=DEFAULT_POLICY,**workload(normalise(run['parameters']).as_dict())),resources.__dict__)

    def test_profiled_heap_launches_with_recovery_disabled_and_rejects_changed_annotation(self):
        resolved,_=self.prepared()
        plan=self.model.experiment(resolved,dict(configuration=self.config))
        run=plan['run_sets'][0]
        lease=SimpleNamespace(specification=dict(seeds=plan['seed_plan'],run_sets=[run],
            dataset_id=resolved['dataset_id'],model_digest=self.f.image,prepared_fingerprint=resolved['prepared_fingerprint']),
            configuration_id=run['id'],resources=run['execution']['resources'],heap_mib=4096)
        adapter=SimPathsContainerAdapter(resolved['location'],self.f.image)
        request=self.f.root/'simulation-request';request.mkdir()
        with patch('deploy.multirun.container_adapter.require_workspace_space'):
            command=adapter.container_command(lease,request)
        self.assertEqual(command.argv[-1],'4096m')
        run['starting_allocation']['heap_mib']=3072
        with self.assertRaises(ArtifactError):adapter.container_command(lease,request)

    def test_browser_cannot_choose_allocations_or_expand_existing_service_bounds(self):
        browser=BrowserModel(self.f.releases)
        form=dict(name='test',common=dict(population=100000,start_year=2019,end_year=2070),
            repetitions=3,run_sets=[dict(id='baseline',name='Baseline',model_args={})],baseline='baseline',auto_retry=True)
        with self.assertRaises(DeploymentLimitError) as rejected:
            browser.browser_configuration('pending-owned',form)
        self.assertEqual(rejected.exception.supported_limit,'population')
        form['common'].update(population=50000,end_year=2026)
        form['run_sets'][0]['starting_allocation']={}
        with self.assertRaises(ArtifactError):browser.browser_configuration('pending-owned',form)

    def test_replacement_and_uncovered_outputs_require_a_new_review(self):
        plan=self.model.experiment(self.f.pending(),dict(configuration=self.config))
        run=plan['run_sets'][0]
        run['starting_allocation']['policy_sha256']='0'*64
        with self.assertRaises(ArtifactError):self.model.bind_run(self.f.pending(),run,plan['seed_plan'])
        self.config['run_sets'][0]['collector_args']['persistHouseholds']=False
        with self.assertRaises(ValueError) as rejected:
            self.model.experiment(self.f.pending(),dict(configuration=self.config))
        self.assertEqual(rejected.exception.supported_limit,'unprofiled')


if __name__=='__main__':unittest.main()
