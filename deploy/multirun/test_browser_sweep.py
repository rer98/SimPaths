"""(C) Copyright 2026, by Ross Richardson

Sweep limits, exact expansion, inheritance and fixed execution after browser edits.

@author ross richardson
"""
from copy import deepcopy
import sys
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path
from .configuration import normalise
from . import test_browser_model


class BrowserSweepTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_browser_model.BrowserModelTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.model=self.fixture.model
        self.form=deepcopy(self.fixture.form)
        self.dataset=self.fixture.receipt['revision']

    def preview(self, *dimensions, base='baseline'):
        return self.model.browser_sweep(self.dataset,self.form,dict(base=base,dimensions=list(dimensions)))

    def values(self, values='.04,.05,.06', field='savingRate'):
        return dict(field=field,mode='values',values=values)

    def range(self, start='.1', end='.3', step='.1', field='savingRate'):
        return dict(field=field,mode='range',start=start,end=end,step=step)

    def test_matches_existing_cards_without_replacing_them_or_changing_baseline(self):
        before=deepcopy(self.form)
        result=self.preview(self.values())
        self.assertEqual((result['combinations'],result['matches'],result['added']), (3,2,1))
        self.assertEqual((result['configurations'],result['simulations'],result['added_simulations']), (3,9,3))
        self.assertEqual([r['configuration'] for r in result['rows']],['baseline','sweep-1','comparison'])
        self.assertEqual(self.form,before)
        self.assertNotIn('dataset_revision',result['run_sets'][0])
        self.assertEqual(result,self.preview(self.values()))
        self.assertEqual(self.preview(self.values('.04,.06'))['run_sets'],[])

    def test_ranges_are_exact_and_dimension_order_does_not_change_combinations(self):
        dimensions=[self.range(),self.values('true,false','useWeights')]
        result=self.preview(*dimensions)
        self.assertEqual([r['model_args']['savingRate'] for r in result['run_sets']],[.1,.1,.2,.2,.3,.3])
        self.assertEqual(result,self.preview(*reversed(dimensions)))
        self.assertEqual([r['model_args']['savingRate'] for r in self.preview(self.range('.3','.1','-.1'))['run_sets']],[.3,.2,.1])
        integers=self.preview(self.range('100','104','2','maxAge'))
        self.assertEqual([r['model_args']['maxAge'] for r in integers['run_sets']],[100,102,104])
        self.assertEqual(self.preview(self.values('.07\n.08'))['added'],2)

    def test_generated_cards_inherit_alternative_inputs_years_and_collector_settings(self):
        base=self.form['run_sets'][0]
        base.update(dataset_revision='alternative-inputs',common=dict(country='UK',population=50000,start_year=2019,end_year=2025),
                    collector_args=dict(persistHouseholds=False))
        result=self.preview(self.values())
        # The existing .06 card uses other inputs, so it is not a match.
        self.assertEqual(result['matches'],1)
        for run in result['run_sets']:
            self.assertEqual(run['dataset_revision'],'alternative-inputs')
            self.assertEqual(run['common'],base['common'])
            self.assertFalse(run['collector_args']['persistHouseholds'])
            self.assertEqual(run['generation']['base']['dataset_revision'],'alternative-inputs')
        # Explicitly selecting the effective experiment default still matches.
        base['dataset_revision']=self.dataset
        base.pop('common');base.pop('collector_args')
        self.assertEqual(self.preview(self.values())['matches'],2)

    def test_duplicate_detection_includes_common_and_collector_overrides(self):
        self.form['run_sets'][1]['collector_args']=dict(persistHouseholds=False)
        self.assertEqual(self.preview(self.values())['matches'],1)
        self.form['run_sets'][1].pop('collector_args')
        self.form['run_sets'][1]['common']=dict(country='UK',population=20000,start_year=2019,end_year=2022)
        self.assertEqual(self.preview(self.values())['matches'],1)

    def test_invalid_dimensions_and_large_expansions_leave_source_unchanged(self):
        before=deepcopy(self.form)
        invalid=[[],[self.values('.04,.040')],[self.values('true,1','useWeights')],
            [self.values('NaN')],[self.values('1e999')],[self.values('1.5','maxAge')],
            [self.values('')],[self.values('.1,')],[self.values('.1')]*2,
            [self.values('606','randomSeed')],[self.values('false','persistPersons')],
            [self.range(step='0')],[self.range(step='-.1')],[self.range(step='.03')],
            [self.range('1','10000000','1')],
            [dict(self.values(),extra='ignored')],[self.range('true','false','true','useWeights')]]
        for dimensions in invalid:
            with self.subTest(dimensions=dimensions),self.assertRaises(ValueError):self.preview(*dimensions)
            self.assertEqual(self.form,before)
        with self.assertRaises(ValueError):self.preview(self.values(),base='missing')
        with self.assertRaises(ValueError):self.model.browser_sweep(self.dataset,self.form,{'base':'baseline','dimensions':[],'owner':'other'})

    def test_limit_counts_existing_cards_before_creating_any_new_cards(self):
        from .browser_model import BrowserModel
        self.model=BrowserModel(self.model.releases,max_configurations=10)
        with self.assertRaisesRegex(ValueError,'12 configurations'):
            self.preview(self.range('1','10','1'))
        with patch('deploy.multirun.configuration.itertools.product',side_effect=AssertionError('Too large to expand')):
            with self.assertRaisesRegex(ValueError,'limit'):self.preview(self.range('0','1000000','.01'))
        self.form['repetitions']=4
        with self.assertRaises(ValueError):self.preview(self.values())

    def test_three_parameters_with_three_values_generate_twenty_seven_configurations(self):
        result=self.preview(self.values('.01,.02,.03'),self.values('.95,.96,.97','sIndexDelta'),self.values('1,2,3','sIndexAlpha'))
        self.assertEqual((result['combinations'],result['added'],result['configurations'],result['simulations']),(27,27,29,87))
        self.assertEqual(len({tuple(sorted(row['values'].items())) for row in result['rows']}),27)
        self.assertEqual(self.form['baseline'],'baseline')
        request=self.model.browser_configuration(self.dataset,{**self.form,'run_sets':[*self.form['run_sets'],*result['run_sets']]})
        self.assertEqual([r['id'] for r in request['configuration']['run_sets']],
                         [r['id'] for r in self.form['run_sets']]+[r['id'] for r in result['run_sets']])

    def test_full_hundred_combination_sweep_exports_and_imports_its_generation_metadata(self):
        self.form['run_sets']=self.form['run_sets'][:1]
        result=self.preview(self.range('.01','1','.01'))
        self.assertEqual((result['combinations'],result['matches'],result['added']),(100,1,99))
        self.form['run_sets'].extend(result['run_sets'])
        exported=self.model.browser_export_yaml(self.dataset,self.form)
        imported=self.model.browser_import_yaml(exported['text'],dataset=self.dataset,name='Import')
        self.assertEqual(len(imported['form']['run_sets']),100)
        self.assertEqual(imported['form']['run_sets'][-1]['generation'],result['run_sets'][-1]['generation'])

    def test_generated_ids_avoid_collisions_and_names_remain_bounded(self):
        self.form['run_sets'][1]['id']='sweep-1'
        self.form['run_sets'][0]['name']='A'*120
        result=self.preview(self.values())
        self.assertEqual(result['run_sets'][0]['id'],'sweep-2')
        self.assertLessEqual(len(result['run_sets'][0]['name']),120)

    def test_edited_generation_round_trips_and_execution_uses_current_fixed_values(self):
        self.form['first_seed']='9007199254740993'
        added=self.preview(self.values())['run_sets'][0]
        self.form['run_sets'].append(added)
        added['model_args']['savingRate']=.075
        exported=self.model.browser_export_yaml(self.dataset,self.form)
        imported=self.model.browser_import_yaml(exported['text'],dataset=self.dataset,name='Import')
        request=self.model.browser_configuration(self.dataset,imported['form'])
        frozen=normalise(request['configuration'])
        run=frozen.as_dict()['run_sets'][-1]
        self.assertEqual(run['generation']['values'],{'model_args.savingRate':.05})
        self.assertEqual(run['model_args']['savingRate'],.075)
        self.assertEqual(frozen.native_configuration(run['id'])['model_args']['savingRate'],.075)
        self.assertNotIn('sweep',frozen.as_dict())
        old=list(sys.path);self.addCleanup(lambda:setattr(sys,'path',old));sys.path.insert(0,str(frontend_path()))
        plan=self.model.experiment(self.fixture.resolved,request)
        self.assertEqual(plan['seed_plan'],['9007199254740993','9007199254740994','9007199254740995'])
        bound=normalise(plan['run_sets'][-1]['parameters'])
        self.assertEqual(bound.native_configuration(run['id'])['maxNumberOfRuns'],3)
        self.assertEqual(bound.as_dict()['run_sets'][0]['generation'],run['generation'])
        self.assertEqual(plan['baseline'],'baseline')

    def test_untrusted_origin_cannot_inject_fields_or_change_execution(self):
        run=self.preview(self.values())['run_sets'][0]
        good=deepcopy(run['generation'])
        changes=[dict(good,version='unknown'),dict(good,values={'model_args.savingRate':999}),
                 dict(good,parameters={'randomSeed':[1]}),dict(good,owner='other'),
                 dict(good,base={**good['base'],'path':'/tmp/evil'})]
        for generation in changes:
            with self.subTest(generation=generation),self.assertRaises(ValueError):
                self.model.browser_configuration(self.dataset,{**self.form,'run_sets':[*self.form['run_sets'],{**run,'generation':generation}]})
        # Provenance does not make otherwise identical cards different jobs.
        self.form['run_sets'].extend([run,{**run,'id':'another-id'}])
        with self.assertRaisesRegex(ValueError,'duplicate effective'):
            self.model.browser_configuration(self.dataset,self.form)


if __name__=='__main__':
    unittest.main()
