"""(C) Copyright 2026, by Ross Richardson

Scientific settings round trips and bounded native/web YAML import regressions.

@author ross richardson
"""
from copy import deepcopy
import unittest

import yaml

from .configuration import normalise
from .schema import COLLECTOR_FIELDS
from . import test_browser_model


class BrowserYamlTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_browser_model.BrowserModelTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.model = self.fixture.model
        self.form = deepcopy(self.fixture.form)
        self.dataset = self.fixture.receipt['revision']

    def import_text(self, text):
        return self.model.browser_import_yaml(text, dataset=self.dataset, name='Native example')

    def test_complete_experiment_round_trip_preserves_order_inputs_baseline_collector_and_seed(self):
        form = self.form
        form.update(first_seed='9007199254740993', auto_retry=False)
        form['run_sets'][0]['collector_args'] = dict(persistHouseholds=False, calculateGiniCoefficients=True)
        form['run_sets'][1].update(dataset_revision='another-dataset',
            common=dict(country='UK',population=50000,start_year=2019,end_year=2025))
        form['baseline'] = form['run_sets'][1]['id']
        exported = self.model.browser_export_yaml(self.dataset, form)
        imported = self.import_text(exported['text'])
        self.assertEqual(imported['dataset'], self.dataset)
        expected = self.model.browser_configuration(self.dataset, form)
        actual = self.model.browser_configuration(imported['dataset'], imported['form'])
        self.assertEqual(actual, expected)
        self.assertEqual(normalise(actual['configuration']).as_dict()['seed_plan']['seeds'],
                         ['9007199254740993','9007199254740994','9007199254740995'])
        self.assertEqual(set(imported['form']['run_sets'][0]['collector_args']), set(COLLECTOR_FIELDS))
        self.assertEqual(exported['filename'], 'Comparison.yaml')
        self.assertNotIn(str(self.fixture.path), exported['text'])

    def test_native_yaml_preserves_supported_values_and_uses_selected_dataset(self):
        native = normalise(self.model.browser_configuration(self.dataset,self.form)['configuration']).native_configuration('baseline')
        native['randomSeed'] = -9223372036854775808
        native['collector_args']['persistHouseholds'] = False
        imported = self.import_text(yaml.safe_dump(native))
        self.assertEqual(imported['dataset'],self.dataset)
        form = imported['form']
        self.assertEqual(form['first_seed'],'-9223372036854775808')
        self.assertIsNone(form['baseline'])
        self.assertEqual(len(form['run_sets']),1)
        self.assertFalse(form['run_sets'][0]['collector_args']['persistHouseholds'])
        self.assertIn('Native SimPaths', imported['notes'][0])

    def test_plain_configuration_and_inherited_common_overrides_round_trip(self):
        self.form['run_sets'][1]['common'] = dict(country='UK',population=20000,start_year=2019,end_year=2024)
        canonical = self.model.browser_configuration(self.dataset,self.form)['configuration']
        imported = self.import_text(yaml.safe_dump(canonical))
        self.assertIsNone(imported['form']['baseline'])
        self.assertEqual(imported['form']['run_sets'][1]['common']['end_year'],2024)
        self.assertEqual(self.model.browser_configuration(self.dataset,imported['form'])['configuration'],canonical)

    def test_invalid_yaml_graphs_and_types_rejected(self):
        for text in ('a: 1\na: 2', 'a: &a [*a]', '!!python/object:evil {}', '---\na: 1\n---\nb: 2',
                     'a: .nan', 'a: '+('['*20)+'0'+(']'*20), 'x'*65537, '- item', ''):
            with self.subTest(text=text[:40]), self.assertRaises(ValueError):
                self.import_text(text)

    def test_unknown_fields_releases_managed_controls_and_invalid_baselines_rejected(self):
        original = yaml.safe_load(self.model.browser_export_yaml(self.dataset,self.form)['text'])
        bad = []
        for key,value in [('owner','other'),('image','external'),('schema_version','unknown')]:
            doc=deepcopy(original);doc['configuration'][key]=value;bad.append(doc)
        for key,value in [('model_release','unavailable'),('model_release',{}),('dataset_revision','/etc/passwd')]:
            doc=deepcopy(original);doc['configuration'][key]=value;bad.append(doc)
        for key,value in [('baseline','missing'),('auto_retry','yes'),('format','unknown'),('extra','ignored')]:
            doc=deepcopy(original);doc[key]=value;bad.append(doc)
        doc=deepcopy(original);doc['configuration']['run_sets'][0]['model_args']['unknown']=True;bad.append(doc)
        for doc in bad:
            with self.subTest(doc=doc), self.assertRaises(ValueError):self.import_text(yaml.safe_dump(doc))

    def test_limits_output_contract_and_precision_checked_on_import_and_export(self):
        for change in ({'repetitions':4},{'first_seed':'9223372036854775807'}, {'first_seed':606}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.model.browser_export_yaml(self.dataset,{**self.form,**change})
        for field,value in [('persistPersons',False),('exportToCSV',False),('dataDumpTimePeriod',2.0)]:
            form=deepcopy(self.form);form['run_sets'][0]['collector_args']={field:value}
            with self.assertRaises(ValueError):self.model.browser_export_yaml(self.dataset,form)
        for common in (dict(population=50001,start_year=2019,end_year=2020),
                       dict(population=20000,start_year=2019,end_year=2027)):
            with self.assertRaises(ValueError):self.model.browser_export_yaml(self.dataset,{**self.form,'common':common})

    def test_native_managed_overrides_and_missing_dataset_are_rejected(self):
        native=normalise(self.model.browser_configuration(self.dataset,self.form)['configuration']).native_configuration('baseline')
        for key,value in [('executeWithGui',True),('workingDirectory','/tmp'),('maxNumberOfRuns',4)]:
            with self.assertRaises(ValueError):self.import_text(yaml.safe_dump({**native,key:value}))
        with self.assertRaises(ValueError):
            self.model.browser_import_yaml(yaml.safe_dump(native),dataset='',name='Native')

    def test_all_supported_collector_values_are_shown_in_review(self):
        self.form['run_sets'][0]['collector_args']=dict(persistHouseholds=False)
        request=self.model.browser_configuration(self.dataset,self.form)
        summary=self.model.browser_summary(request)
        self.assertFalse(summary['configurations'][0]['collector']['persistHouseholds'])
        self.assertEqual({f['id'] for f in self.model.browser_form()['collector_fields']},set(COLLECTOR_FIELDS))


if __name__ == '__main__':
    unittest.main()
