"""(C) Copyright 2026, by Ross Richardson
Measured MultiRun resource selection across review, preparation and native launch.
These use fictional files and no Docker, scientific simulations or real mail.
@author ross richardson
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from .artifacts import ArtifactError
from .browser_model import BrowserModel
from .configuration import normalise
from .container_adapter import SimPathsContainerAdapter
from .queue_adapter import read_prepared
from .resource_policy import DEFAULT_POLICY, check_policy, select_profile, simulation_resources, workload
from .schema import COLLECTOR_FIELDS, DeploymentLimitError
from .workload_envelope import extended_resource_policy
from . import test_submission_adapter
from . import test_browser_model


class MeasuredPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy=extended_resource_policy(DEFAULT_POLICY)
        self.collectors={key:field.default for key,field in COLLECTOR_FIELDS.items()}

    def test_all_six_measured_cases_select_the_recorded_heap_and_container(self):
        for population,years,heap,memory in ((20000,17,2560,3584),(20000,52,2560,3584),
                (50000,17,3072,4096),(50000,52,3072,4096),
                (100000,17,3072,4096),(100000,52,3584,4608)):
            with self.subTest(population=population,years=years):
                chosen=select_profile(self.policy,population=population,years=years,repetitions=2,collectors=self.collectors)
                self.assertEqual((chosen['heap_mib'],chosen['memory_mib']),(heap,memory))
                self.assertGreaterEqual(memory-heap,1024)

    def test_later_repetitions_add_a_bounded_margin_instead_of_multiplying_ram(self):
        context=dict(population=100000,years=52,collectors=self.collectors)
        two=simulation_resources(self.policy,**context,repetitions=2)
        ten=simulation_resources(self.policy,**context,repetitions=10)
        self.assertEqual((two['memory_mib'],ten['memory_mib']),(4608,5120))
        self.assertEqual((two['storage_mib'],ten['storage_mib']),(14336,51200))
        self.assertEqual(DEFAULT_POLICY['simulation']['storage']['per_repetition_mib'],256)

    def test_optional_collectors_remain_available_using_the_conservative_reference(self):
        for changed in (dict(calculateGiniCoefficients=True),dict(persistHouseholds=False)):
            chosen=select_profile(self.policy,population=100000,years=52,repetitions=10,
                                  collectors=self.collectors|changed)
            self.assertEqual(chosen['id'],'extended-reference-4g-5g')
            self.assertEqual((chosen['heap_mib'],chosen['memory_mib']),(4096,5120))
        invalid=self.collectors|dict(exportToDatabase=True)
        with self.assertRaises(ArtifactError):
            select_profile(self.policy,population=20000,years=8,repetitions=1,collectors=invalid)

    def test_start_year_2011_can_use_the_reference_but_larger_envelopes_stay_rejected(self):
        context=dict(population=100000,years=60,repetitions=10,collectors=self.collectors)
        self.assertEqual(select_profile(self.policy,**context)['id'],'extended-reference-4g-5g')
        for change in (dict(population=100001),dict(years=61),dict(repetitions=11)):
            with self.assertRaises(ValueError):select_profile(self.policy,**(context|change))

    def test_grid_and_fallback_validation_cannot_relax_native_headroom_or_output_guards(self):
        for field,value in (('heap_mib',5000),('memory_mib',3072),('collectors',{})):
            policy=deepcopy(self.policy);policy['profiles'][0][field]=value
            with self.assertRaises(ArtifactError):check_policy(policy)
        policy=deepcopy(self.policy);policy['profiles'].append(deepcopy(policy['profiles'][0]))
        with self.assertRaises(ArtifactError):check_policy(policy)
        policy=deepcopy(self.policy);policy['fallback']['max_population']=50000
        with self.assertRaises(ArtifactError):check_policy(policy)


class EnvelopeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f=test_submission_adapter.SubmissionAdapterTests()
        self.addCleanup(self.f.doCleanups);self.f.setUp()
        self.key,_=self.f.versioned_plan(extended_resource_policy(DEFAULT_POLICY))
        self.model=BrowserModel(self.f.releases,max_repetitions=10)
        self.form=dict(name='Large preview',model_release=self.key,
            common=dict(population=100000,start_year=2019,end_year=2070),repetitions=10,
            run_sets=[dict(id='baseline',name='Baseline',model_args={})],baseline='baseline',auto_retry=True)

    def request(self):return self.model.browser_configuration('pending-owned',self.form)

    def test_form_yaml_and_pending_review_share_extended_bounds_and_recorded_resources(self):
        fields={field['id']:field for field in self.model.browser_form()['common']}
        self.assertEqual((fields['population']['max'],fields['end_year']['max']),(100000,2070))
        self.assertEqual(fields['end_year']['default'],2026)
        request=self.request()
        exported=self.model.browser_export_yaml('pending-owned',self.form)
        restored=self.model.browser_import_yaml(exported['text'],dataset='pending-owned',name='unused')
        self.assertEqual(self.model.browser_configuration('pending-owned',restored['form']),request)
        plan=self.model.experiment(self.f.pending(),request)
        self.assertEqual(plan['resources'].__dict__,dict(cpu_millis=2000,memory_mib=5120,storage_mib=51200))
        self.assertEqual(self.model.heap_limits(plan),{'baseline':4096})
        self.assertEqual(plan['seed_plan'],[str(seed) for seed in range(606,616)])

    def test_root_and_configuration_overrides_cannot_exceed_the_new_bounds(self):
        for key,value in (('population',100001),('end_year',2071)):
            for override in (False,True):
                form=deepcopy(self.form)
                target=form['run_sets'][0].setdefault('common',dict(country='UK',**form['common'])) if override else form['common']
                target[key]=value
                with self.assertRaises(DeploymentLimitError):self.model.browser_configuration('pending-owned',form)

    def test_ready_inputs_and_container_launch_keep_the_reviewed_profile_after_a_default_change(self):
        request=self.request();pending=self.model.experiment(self.f.pending(),request)
        self.f.result();self.f.adapter.validate(self.f.lease,self.f.work)
        location=self.f.adapter.artifacts/self.f.lease.execution_key;receipt=read_prepared(location)
        resolved=dict(dataset_id='pending-owned',location=str(location),model_digest=self.f.image,
                      prepared_fingerprint=receipt['sha256'])
        self.f.releases[self.key]['resource_policy']=deepcopy(DEFAULT_POLICY)
        run,resources=self.model.bind_run(resolved,pending['run_sets'][0],pending['seed_plan'])
        self.assertEqual(run,{k:v for k,v in pending['run_sets'][0].items() if k!='execution'})
        self.assertEqual(resources.__dict__,pending['run_sets'][0]['execution']['resources'])
        lease=SimpleNamespace(specification=dict(seeds=pending['seed_plan'],run_sets=[run],
            dataset_id='pending-owned',model_digest=self.f.image,prepared_fingerprint=receipt['sha256']),
            configuration_id='baseline',resources=resources.__dict__,heap_mib=4096)
        directory=self.f.root/'large-request';directory.mkdir()
        with patch('deploy.multirun.container_adapter.require_workspace_space'):
            command=SimPathsContainerAdapter(location,self.f.image).container_command(lease,directory)
        self.assertEqual(command.argv[-1],'4096m')
        self.assertEqual(normalise(run['parameters']).native_configuration('baseline')['endYear'],2070)

    def test_older_short_run_release_keeps_its_budget_and_does_not_admit_underallocated_long_work(self):
        key,_=self.f.versioned_plan()
        self.form['model_release']=key
        self.form['common'].update(population=50000,end_year=2026)
        short=self.model.experiment(self.f.pending(),self.request())
        self.assertEqual(short['resources'].storage_mib,4096+10*256)
        self.form['common']['end_year']=2070
        with self.assertRaises(DeploymentLimitError) as rejected:self.model.experiment(self.f.pending(),self.request())
        self.assertEqual(rejected.exception.supported_limit,'resources')

    def test_legacy_fixed_training_uses_the_selected_release_through_native_launch(self):
        fixture=test_browser_model.BrowserModelTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        policy=extended_resource_policy(DEFAULT_POLICY)
        previous=fixture.model.releases['approved']
        model=BrowserModel({'measured':dict(previous,resource_policy=policy),
                            'approved':dict(previous,resource_policy=DEFAULT_POLICY)},max_repetitions=10)
        described=model.describe_dataset(fixture.resolved)
        self.assertEqual(described['model_release'],'measured')
        self.assertEqual(described['locked'],['start_year','population'])
        form=deepcopy(fixture.form);form.update(model_release='measured',repetitions=2)
        form['common']['end_year']=2070
        plan=model.experiment(fixture.resolved,model.browser_configuration(fixture.resolved['dataset_id'],form))
        lease=SimpleNamespace(specification=dict(seeds=plan['seed_plan'],run_sets=plan['run_sets'],
            dataset_id=plan['dataset_id'],model_digest=plan['model_digest'],
            prepared_fingerprint=fixture.receipt['sha256']),configuration_id='baseline',
            resources=plan['run_sets'][0]['execution']['resources'])
        request=fixture.fixture.root/'legacy-long-request';request.mkdir()
        adapter=SimPathsContainerAdapter(fixture.path,plan['model_digest'],resource_policy=policy)
        with patch('deploy.multirun.container_adapter.require_workspace_space'):
            command=adapter.container_command(lease,request)
        self.assertEqual(command.argv[-1],'2560m')
        self.assertEqual(lease.resources['memory_mib'],3584)


if __name__=='__main__':unittest.main()
