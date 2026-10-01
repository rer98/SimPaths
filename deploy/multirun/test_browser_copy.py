"""(C) Copyright 2026, by Ross Richardson

Recover submitted experiment forms without touching inputs or changing execution history.

@author ross richardson
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from . import test_browser_model
from .configuration import normalise


class BrowserCopyTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_browser_model.BrowserModelTests()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.model=self.fixture.model
        self.form=deepcopy(self.fixture.form)
        self.dataset=self.fixture.receipt['revision']
        self.form.update(first_seed='9007199254740993',auto_retry=False)
        self.form['run_sets'][0]['common']=dict(country='UK',population=20000,start_year=2019,end_year=2022)
        self.form['run_sets'][1].update(dataset_revision='alternative-inputs',
            common=dict(country='UK',population=50000,start_year=2019,end_year=2025),
            collector_args=dict(persistHouseholds=False))
        self.form['baseline']='comparison'

    def specification(self, *, saved=True):
        request=self.model.browser_configuration(self.dataset,self.form)
        snapshot=normalise(request['configuration']);data=snapshot.as_dict()
        spec=dict(label=self.form['name'],baseline=self.form['baseline'],dataset_id=self.dataset,
            seeds=data['seed_plan']['seeds'],run_sets=[dict(id=r['id'],parameters=snapshot.run_configuration(r['id'])) for r in data['run_sets']])
        if saved:spec['browser_settings']=self.model.browser_snapshot(request)
        return spec

    def copy(self,spec,**kwargs):
        return self.model.browser_copy(spec,replacements=kwargs.get('replacements',{}),auto_retry=kwargs.get('auto_retry',True))

    def test_new_submission_restores_default_inheritance_overrides_and_retry_preference(self):
        spec=self.specification();before=deepcopy(spec)
        with patch('deploy.multirun.browser_model.read_prepared',side_effect=AssertionError('No input reads')):
            result=self.copy(spec)
        copied=result['form']
        self.assertEqual(copied['name'],'Comparison (copy)')
        self.assertEqual(copied['common'],self.form['common'])
        self.assertEqual(result['dataset'],self.dataset)
        self.assertNotIn('dataset_revision',copied['run_sets'][0])
        self.assertEqual(copied['baseline'],'comparison')
        self.assertFalse(copied['auto_retry'])
        self.assertEqual(copied['first_seed'],'9007199254740993')
        self.assertFalse(copied['run_sets'][1]['collector_args']['persistHouseholds'])
        copied['name']=self.form['name']
        self.assertEqual(self.model.browser_configuration(result['dataset'],copied),
                         self.model.browser_configuration(self.dataset,self.form))
        copied['run_sets'][0]['model_args']['savingRate']=.9
        self.assertEqual(spec,before)

    def test_legacy_reconstruction_preserves_every_native_configuration(self):
        spec=self.specification(saved=False)
        copied=self.copy(spec,auto_retry=False)
        self.assertIn('older experiment',copied['notes'][0])
        self.assertEqual(copied['form']['common']['end_year'],2022)
        self.assertFalse(copied['form']['auto_retry'])
        new=normalise(self.model.browser_configuration(copied['dataset'],copied['form'])['configuration'])
        for old in spec['run_sets']:
            self.assertEqual(new.native_configuration(old['id']),normalise(old['parameters']).native_configuration(old['id']))
        self.assertEqual(new.as_dict()['run_sets'][1]['dataset_revision'],'alternative-inputs')

    def test_legacy_first_card_with_alternative_inputs_becomes_safe_form_default(self):
        self.form['run_sets'].reverse()
        spec=self.specification(saved=False)
        copied=self.copy(spec)
        self.assertEqual(copied['dataset'],'alternative-inputs')
        self.assertEqual(copied['form']['common']['population'],50000)
        self.assertEqual(copied['form']['run_sets'][1]['dataset_revision'],self.dataset)

    def test_legacy_defaults_follow_first_card_overrides_when_not_flattened(self):
        spec=self.specification(saved=False)
        first=spec['run_sets'][0]['parameters']
        first['run_sets'][0]['dataset_revision']='first-card-inputs'
        first['run_sets'][0]['common']=dict(country='UK',population=45000,start_year=2019,end_year=2026)
        copied=self.copy(spec)
        self.assertEqual(copied['dataset'],'first-card-inputs')
        self.assertEqual(copied['form']['common']['population'],45000)
        self.assertEqual(copied['form']['common']['end_year'],2026)
        self.assertNotIn('common',copied['form']['run_sets'][0])

    def test_actual_submission_adapter_output_agrees_with_saved_form(self):
        import sys
        from deploy._workflow import frontend_path
        from . import test_prepared_dataset
        old=list(sys.path);self.addCleanup(lambda:setattr(sys,'path',old));sys.path.insert(0,str(frontend_path()))
        path,receipt=self.fixture.fixture.dataset(50000)
        alternate=receipt['revision']
        self.form['run_sets'][1]['dataset_revision']=alternate
        request=self.model.browser_configuration(self.dataset,self.form)
        choices={self.dataset:self.fixture.resolved,alternate:dict(location=str(path),dataset_id=alternate,
            prepared_fingerprint=receipt['sha256'],model_digest=test_prepared_dataset.IMAGE)}
        plan=self.model.experiment(dict(self.fixture.resolved,datasets=choices),request)
        plan['seeds']=plan.pop('seed_plan');plan['browser_settings']=self.model.browser_snapshot(request)
        copied=self.copy(plan)
        copied['form']['name']=self.form['name']
        self.assertEqual(self.model.browser_configuration(copied['dataset'],copied['form']),request)

    def test_approved_replacements_are_used_without_modifying_original_snapshot(self):
        spec=self.specification();before=deepcopy(spec)
        replacement=deepcopy(spec['run_sets'][0])
        replacement['parameters']['dataset_revision']='replacement-inputs'
        result=self.copy(spec,replacements={'baseline':replacement})
        self.assertEqual(result['form']['run_sets'][0]['dataset_revision'],'replacement-inputs')
        self.assertEqual(result['form']['run_sets'][0]['common']['end_year'],2022)
        self.assertIn('input replacements',result['notes'][0])
        self.assertEqual(spec,before)
        self.model.browser_configuration(result['dataset'],result['form'])

    def test_lowered_admission_limit_does_not_silently_change_copied_repetitions(self):
        spec=self.specification()
        self.model.max_repetitions=1
        self.model.submission_limits=type(self.model.submission_limits)(max_repetitions=1)
        copied=self.copy(spec)
        self.assertEqual(copied['form']['repetitions'],3)
        self.assertIn('current limit of 1',copied['notes'][0])
        with self.assertRaises(ValueError):self.model.browser_configuration(copied['dataset'],copied['form'])

    def test_large_copy_keeps_every_configuration_when_the_operator_lowers_the_limit(self):
        from dataclasses import replace
        self.form['run_sets']=[dict(id=f'policy-{i}',name=f'Policy {i}',
            model_args=dict(savingRate=(i+1)/100)) for i in range(100)]
        self.form['baseline']='policy-0'
        spec=self.specification();before=deepcopy(spec)
        copied=self.copy(spec)
        self.assertEqual([r['id'] for r in copied['form']['run_sets']],
                         [r['id'] for r in self.form['run_sets']])
        self.assertEqual(copied['form']['baseline'],'policy-0')
        self.assertEqual(copied['notes'],[])
        self.model.max_configurations=10
        self.model.submission_limits=replace(self.model.submission_limits,max_run_sets=10)
        copied=self.copy(spec)
        self.assertEqual(len(copied['form']['run_sets']),100)
        self.assertIn('current limit of 10',copied['notes'][0])
        self.assertEqual(spec,before)
        with self.assertRaisesRegex(ValueError,'1–10 configurations'):
            self.model.browser_configuration(copied['dataset'],copied['form'])

    def test_copy_preserves_edited_sweep_origin_through_yaml(self):
        self.form['run_sets']=self.form['run_sets'][:1];self.form['baseline']='baseline'
        self.form['run_sets'][0].pop('common')
        result=self.model.browser_sweep(self.dataset,self.form,dict(base='baseline',dimensions=[dict(field='savingRate',mode='values',values='.04,.05')]))
        self.form['run_sets'].extend(result['run_sets'])
        self.form['run_sets'][1]['model_args']['savingRate']=.07
        copied=self.copy(self.specification())
        self.assertEqual(copied['form']['run_sets'][1]['generation']['values']['model_args.savingRate'],.05)
        exported=self.model.browser_export_yaml(copied['dataset'],copied['form'])
        imported=self.model.browser_import_yaml(exported['text'],dataset=self.dataset,name='Ignored')
        self.assertEqual(imported['form'],copied['form'])

    def test_inconsistent_saved_settings_ids_and_seeds_fail_instead_of_changing_values(self):
        good=self.specification()
        bad=[]
        spec=deepcopy(good);spec['seeds']=['606','607','608'];bad.append(spec)
        spec=deepcopy(good);spec['browser_settings']['configuration']['run_sets'].reverse();bad.append(spec)
        spec=deepcopy(good);spec['browser_settings']['configuration']['run_sets'][0]['model_args']['savingRate']=.8;bad.append(spec)
        spec=deepcopy(good);spec['browser_settings']['format']='unsupported';bad.append(spec)
        for spec in bad:
            with self.subTest(spec=spec),self.assertRaises(ValueError):self.copy(spec)
        replacement=deepcopy(good['run_sets'][0]);replacement['id']='wrong-id'
        with self.assertRaises(ValueError):self.copy(good,replacements={'baseline':replacement})
        self.model.releases={}
        with self.assertRaisesRegex(ValueError,'model release is unavailable'):self.copy(good)


if __name__=='__main__':
    unittest.main()
