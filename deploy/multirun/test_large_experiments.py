"""(C) Copyright 2026, by Ross Richardson
Qualify a separate baseline and all combinations of ten boolean model parameters.
No scientific simulations, real inputs or mail are used.
@author ross richardson
"""
from copy import deepcopy
import json
import time
import unittest
from .browser_model import BrowserModel
from .configuration import normalise
from .schema import MODEL_FIELDS,Limits,ConfigurationError,deployment_configuration_limit
from . import test_browser_sweep as sweep_fixture

class LargeExperimentTests(unittest.TestCase):
    def setUp(self):
        self.f=sweep_fixture.BrowserSweepTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.model=BrowserModel(self.f.model.releases,max_configurations=1025,max_repetitions=12)
        self.fields=[key for key,field in MODEL_FIELDS.items() if field.kind=='boolean' and key not in ('useWeights','ignoreTargetsAtPopulationLoad')][:10]
        self.assertEqual(len(self.fields),10)

    def sweep(self):
        return self.model.browser_sweep(self.f.dataset,self.f.form,dict(base='comparison',
            dimensions=[dict(field=key,mode='values',values='true, false') for key in self.fields]))

    def test_ten_booleans_and_separate_baseline_expand_to_exactly_1025(self):
        before=deepcopy(self.f.form);started=time.monotonic();sweep=self.sweep()
        self.assertEqual(sweep['combinations'],1024);self.assertEqual(sweep['added'],1023)
        self.assertEqual(sweep['configurations'],1025);self.assertEqual(self.f.form,before)
        print('BOOLEAN_SWEEP_1025: '+json.dumps(dict(configurations=1025,combinations=1024,
            generation_ms=round((time.monotonic()-started)*1000))),flush=True)

    def test_full_sweep_yaml_copy_and_frozen_spec_preserve_values_and_baseline(self):
        from jasmine_web.batch.policy import canonical,submission
        form=deepcopy(self.f.form);form['run_sets']+=self.sweep()['run_sets'];form['repetitions']=1
        request=self.model.browser_configuration(self.f.dataset,form)
        yaml=self.model.browser_export_yaml(self.f.dataset,form)
        imported=self.model.browser_import_yaml(yaml['text'],dataset=self.f.dataset,name='Restored sweep')
        self.assertEqual(len(imported['form']['run_sets']),1025)
        self.assertEqual(imported['form']['baseline'],'baseline')
        plan=self.model.experiment(self.f.fixture.resolved,request)
        frozen=submission(plan['label'],plan['model_digest'],plan['dataset_id'],plan['seed_plan'],plan['run_sets'],plan['baseline'])
        frozen['browser_settings']=self.model.browser_snapshot(request)
        copied=self.model.browser_copy(frozen,replacements={},auto_retry=True)
        self.assertEqual(len(copied['form']['run_sets']),1025);self.assertEqual(copied['form']['baseline'],'baseline')
        self.assertLess(len(canonical(frozen).encode()),16*1024**2)
        self.assertEqual([run['model_args'] for run in imported['form']['run_sets']],
                         [run['model_args'] for run in copied['form']['run_sets']])

    def test_new_ceiling_is_opt_in_and_1026_or_excess_simulations_are_refused(self):
        self.assertEqual(Limits().max_run_sets,1025);self.assertEqual(deployment_configuration_limit(1025),1025)
        for value in (1026,True,0):
            with self.assertRaises(ValueError):deployment_configuration_limit(value)
        self.assertEqual(self.f.model.browser_form()['max_configurations'],100)
        form=deepcopy(self.f.form);form['run_sets']+=self.sweep()['run_sets'];form['repetitions']=11
        with self.assertRaisesRegex(ConfigurationError,'limit'):self.model.browser_configuration(self.f.dataset,form)
        with self.assertRaises(ConfigurationError):self.f.model.browser_configuration(self.f.dataset,form)

    def test_batch_execution_inputs_match_single_configuration_and_do_not_share_mutable_settings(self):
        request=self.model.browser_configuration(self.f.dataset,self.f.form)
        config=normalise(request['configuration'])
        singles=list(config.run_configurations())
        self.assertEqual(singles,[config.run_configuration(item['id']) for item in config.as_dict()['run_sets']])
        before=deepcopy(singles[1]);singles[0]['common']['population']=123
        singles[0]['run_sets'][0]['model_args']['savingRate']=.99
        self.assertEqual(singles[1],before)
        self.assertNotEqual(config.run_configuration('baseline')['common']['population'],123)

    def test_1025_configurations_with_ten_repetitions_preserve_all_seeds_and_resources(self):
        from jasmine_web.batch.policy import canonical,submission,Resources
        form=deepcopy(self.f.form);form['run_sets']+=self.sweep()['run_sets'];form['repetitions']=10
        request=self.model.browser_configuration(self.f.dataset,form)
        plan=self.model.experiment(self.f.fixture.resolved,request)
        frozen=submission(plan['label'],plan['model_digest'],plan['dataset_id'],plan['seed_plan'],plan['run_sets'],plan['baseline'])
        self.assertEqual((len(frozen['run_sets']),len(frozen['seeds'])),(1025,10))
        self.assertEqual(frozen['baseline'],'baseline');self.assertEqual(frozen['seeds'],[str(seed) for seed in range(606,616)])
        self.assertLess(len(canonical(frozen).encode()),16*1024**2)
        self.assertTrue(all(run['parameters']['seed_plan']['repetitions']==10 for run in frozen['run_sets']))
        self.assertTrue(all(len(run['parameters']['run_sets'])==1 for run in frozen['run_sets']))
        self.assertEqual(self.model.browser_form()['max_configurations'],1025)
        self.assertIn('10,250 simulations',self.model.browser_form()['note'])
        original=self.model.experiment(self.f.fixture.resolved,self.model.browser_configuration(self.f.dataset,self.f.form))
        expected=original['run_sets'][0]['execution']['resources']
        self.assertTrue(all((run['execution']['resources']['cpu_millis'],run['execution']['resources']['memory_mib'])==
                            (expected['cpu_millis'],expected['memory_mib']) for run in frozen['run_sets']))
