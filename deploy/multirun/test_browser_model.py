"""(C) Copyright 2026, by Ross Richardson

Model form translation, immutable dataset defaults and local cleanup regressions.

@author ross richardson
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from deploy._workflow import frontend_path
from deploy.multirun.artifacts import ArtifactError
from deploy.multirun.browser_model import BrowserModel
from deploy.multirun.schema import ConfigurationError, MODEL_FIELDS
from deploy.multirun import test_prepared_dataset as prepared_fixture


class BrowserModelTests(unittest.TestCase):
    def setUp(self):
        self.fixture=prepared_fixture.PreparedDatasetTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path,self.receipt=self.fixture.dataset()
        self.model=BrowserModel({'approved':dict(image=prepared_fixture.IMAGE,jar=self.path/'model.jar',defaults=self.path/'input')})
        self.resolved=dict(location=str(self.path),dataset_id=self.receipt['revision'],
                           prepared_fingerprint=self.receipt['sha256'],model_digest=prepared_fixture.IMAGE)
        self.form=dict(name='Comparison',common=dict(population=20000,start_year=2019,end_year=2020),
            repetitions=3,run_sets=[dict(id='baseline',name='Baseline',model_args=dict(savingRate=.04)),
                                   dict(id='comparison',name='Comparison',model_args=dict(savingRate=.06))],
            baseline='baseline',auto_retry=True)

    def test_form_keeps_seed_sequence_and_explicit_settings(self):
        request=self.model.browser_configuration(self.receipt['revision'],self.form)
        old=list(sys.path)
        self.addCleanup(lambda:setattr(sys,'path',old))
        sys.path.insert(0,str(frontend_path()))
        plan=self.model.experiment(self.resolved,request)
        self.assertEqual(plan['seed_plan'],['606','607','608'])
        self.assertEqual(plan['baseline'],'baseline')
        self.assertEqual(plan['resources'].memory_mib,4096)
        summary=self.model.browser_summary(request)
        self.assertEqual(summary['different'],['savingRate'])
        self.assertEqual(len(summary['configurations']),2)

    def test_different_populations_freeze_separate_inputs_and_native_settings(self):
        from deploy.multirun.configuration import normalise
        other, receipt = self.fixture.dataset(50000)
        alternative = dict(location=str(other),dataset_id=receipt['revision'],
            prepared_fingerprint=receipt['sha256'],model_digest=prepared_fixture.IMAGE)
        form=deepcopy(self.form)
        form['run_sets'][1].update(dataset_revision=receipt['revision'],
            common=dict(country='UK',population=50000,start_year=2019,end_year=2025))
        # Input changes alone constitute a distinct configuration.
        form['run_sets'][1]['model_args']=form['run_sets'][0]['model_args'].copy()
        request=self.model.browser_configuration(self.receipt['revision'],form)
        snapshot=normalise(request['configuration'])
        from deploy.multirun.configuration import normalise_yaml
        self.assertEqual(normalise_yaml(snapshot.editable_yaml()).as_dict(),snapshot.as_dict())
        self.assertEqual(self.model.dataset_ids(request),{self.receipt['revision'],receipt['revision']})
        old=list(sys.path)
        self.addCleanup(lambda:setattr(sys,'path',old))
        sys.path.insert(0,str(frontend_path()))
        resolved=dict(self.resolved,datasets={self.receipt['revision']:self.resolved,receipt['revision']:alternative})
        plan=self.model.experiment(resolved,request)
        self.assertEqual([r['execution']['resources']['memory_mib'] for r in plan['run_sets']],[4096,5120])
        for run,population,end_year,dataset in zip(plan['run_sets'],[20000,50000],[2020,2025],
                                                  [self.receipt['revision'],receipt['revision']]):
            config=normalise(run['parameters'])
            self.assertEqual(config.as_dict()['dataset_revision'],dataset)
            native=config.native_configuration(run['id'])
            self.assertEqual((native['popSize'],native['endYear'],native['randomSeed']),(population,end_year,606))
            self.assertEqual(config.as_dict()['seed_plan']['seeds'],['606','607','608'])
        self.assertEqual(normalise(request['configuration']).native_configuration('comparison')['popSize'],50000)
        self.assertEqual(self.model.browser_summary(request)['configurations'][1]['common']['population'],50000)
        with self.assertRaisesRegex(ArtifactError,'unavailable'):
            self.model.experiment(self.resolved,request)
        alternative['prepared_fingerprint']='0'*64
        with self.assertRaisesRegex(ArtifactError,'changed'):
            self.model.experiment(resolved,request)

    def test_dataset_name_and_safe_input_comparison_metadata(self):
        description=self.model.describe_dataset(dict(self.resolved,display_name='Baseline policies'))
        self.assertEqual(description['name'],'Baseline policies')
        self.assertIsNotNone(description['inputs']['population'])
        self.assertIn('EUROMODpolicySchedule.xlsx',description['inputs']['workbooks'])
        self.assertNotIn(str(self.path),str(description))
        self.assertEqual(description['inputs']['comparison_state'],'prepared')
        self.assertEqual(description['inputs']['files'],self.receipt['identity']['prepared'])

    def test_pending_input_comparison_exposes_sources_without_claiming_prepared_files(self):
        defaults={'parameters.xls':dict(bytes=12,sha256='a'*64)}
        uploads={'population_initial_UK_2019.csv':dict(bytes=34,sha256='b'*64),
                 'uk_2019_std.txt':dict(bytes=56,sha256='c'*64)}
        definition=dict(uploads=uploads,model=dict(release=dict(defaults=defaults),
            selection=dict(year=2019,schedule=[['uk_2019_std.txt',2019,2019,'']])))
        pending=dict(state='pending',dataset_id='pending-owned',definition=definition,definition_hash='d'*64)
        with patch('deploy.multirun.browser_model.read_prepared',side_effect=AssertionError('Not prepared yet')):
            description=self.model.describe_dataset(pending)
        self.assertEqual(description['inputs']['comparison_state'],'pending')
        self.assertEqual(description['inputs']['files'],{})
        self.assertEqual(description['inputs']['source_files'],{**defaults,**uploads})

    def test_file_comparison_includes_legacy_workbooks_without_exposing_unrelated_files(self):
        receipt=deepcopy(self.receipt)
        manifest=receipt['identity']['prepared']
        manifest['parameters.xls']=dict(bytes=12,sha256='a'*64)
        manifest['private-notes.txt']=dict(bytes=34,sha256='b'*64)
        with patch('deploy.multirun.browser_model.read_prepared',return_value=receipt):
            description=self.model.describe_dataset(self.resolved)
        self.assertEqual(description['inputs']['files']['parameters.xls'],manifest['parameters.xls'])
        self.assertNotIn('private-notes.txt',description['inputs']['files'])

    def test_unknown_fields_and_unsupported_repetitions_rejected(self):
        for change in ({'owner':'other'},{'image':'evil'},{'repetitions':4},{'auto_retry':'yes'}):
            with self.assertRaises(ArtifactError):
                self.model.browser_configuration('dataset',{**self.form,**change})
        malicious=deepcopy(self.form)
        malicious['run_sets'][0]['model_args']['class']='java.lang.Runtime'
        with self.assertRaises(ConfigurationError):
            self.model.browser_configuration('dataset',malicious)

    def test_form_covers_supported_model_fields_in_java_declaration_order(self):
        fields=self.model.browser_form()['fields']
        names=[field['id'] for field in fields]
        self.assertEqual(len(names),len(set(names)))
        self.assertEqual(set(names),set(MODEL_FIELDS))
        source=(Path(__file__).resolve().parents[2]/'src/main/java/simpaths/model/SimPathsModel.java').read_text()
        source=re.sub(r'/\*.*?\*/|//[^\n]*','',source,flags=re.S)
        declared=re.findall(r'\b(?:private|public|protected)\s+\w+\s+(\w+)\s*=',source)
        self.assertEqual(names,[name for name in declared if name in MODEL_FIELDS])
        # Presentation changes preserve every submitted model default.
        form=deepcopy(self.form)
        form['run_sets']=form['run_sets'][:1]
        form['run_sets'][0]['model_args']={field['id']:field['default'] for field in fields}
        request=self.model.browser_configuration(self.receipt['revision'],form)
        self.assertEqual(request['configuration']['run_sets'][0]['model_args'],
                         {key:field.default for key,field in MODEL_FIELDS.items()})

    def test_duplicate_cards_require_a_real_parameter_difference(self):
        form=deepcopy(self.form)
        form['run_sets'][1]['model_args']=form['run_sets'][0]['model_args']
        with self.assertRaisesRegex(ConfigurationError,'duplicate effective'):
            self.model.browser_configuration('dataset',form)

    def test_prepared_defaults_lock_year_and_population_without_paths(self):
        description=self.model.describe_dataset(self.resolved)
        self.assertEqual(description['values']['population'],20000)
        self.assertEqual(description['locked'],['start_year','population'])
        self.assertNotIn(str(self.path),str(description))
        with self.assertRaises(ArtifactError):
            self.model.describe_dataset({**self.resolved,'prepared_fingerprint':'0'*64})

    def test_local_retirement_requires_removal_and_preserves_results(self):
        old=list(sys.path)
        self.addCleanup(lambda:setattr(sys,'path',old))
        sys.path.insert(0,str(frontend_path()))
        from deploy.multirun.local_web import retire_finished
        root=self.fixture.root/'attempt'
        for name in ('request/source.csv','work/input/input.mv.db','work/output/run/input/input.mv.db',
                     'work/output/run/input/options.txt','work/output/run/csv/Person.csv','execution.log'):
            p=root/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text('retained evidence' if name.endswith(('Person.csv','options.txt','execution.log')) else 'temporary')
        queue=Mock()
        queue._connection.return_value.__enter__=Mock(return_value=Mock())
        queue._connection.return_value.__exit__=Mock(return_value=False)
        queue._connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value=[{}]
        executor=Mock()
        executor.workspace.return_value=root
        executor.cleanup.side_effect=RuntimeError('Unconfirmed stop')
        with self.assertRaises(RuntimeError):
            retire_finished(queue,executor)
        self.assertTrue((root/'request/source.csv').exists())
        executor.cleanup.side_effect=None
        retire_finished(queue,executor)
        self.assertFalse((root/'request').exists())
        self.assertFalse((root/'work/input').exists())
        self.assertFalse((root/'work/output/run/input/input.mv.db').exists())
        for name in ('work/output/run/csv/Person.csv','work/output/run/input/options.txt','execution.log'):
            self.assertEqual((root/name).read_text(),'retained evidence')
        self.assertTrue((root/'payload-retired.json').exists())
        executor.cleanup.reset_mock()
        retire_finished(queue,executor)
        executor.cleanup.assert_not_called()

    def test_old_preparation_cleanup_marker_removes_only_successful_upload_copies(self):
        old=list(sys.path)
        self.addCleanup(lambda:setattr(sys,'path',old))
        sys.path.insert(0,str(frontend_path()))
        from deploy.multirun.local_web import retire_finished
        root=self.fixture.root/'old-preparation'
        upload=root/'work/startup/uploads/population.csv'
        upload.parent.mkdir(parents=True)
        upload.write_text('disposable preparation copy')
        marker=root/'payload-retired.json'
        marker.write_text('{"temporary_inputs_removed":true,"outputs_retained":true}')
        diagnostic=root/'work/startup/preparation.log'
        diagnostic.write_text('private diagnostic')
        retained=self.fixture.root/'retained-artifact'
        retained.write_text('published dataset and original uploads are outside the attempt')
        row={'specification':{'operation':'prepare'},'outcome':'model'}
        queue=Mock()
        connection=Mock()
        queue._connection.return_value.__enter__=Mock(return_value=connection)
        queue._connection.return_value.__exit__=Mock(return_value=False)
        connection.execute.return_value.fetchall.return_value=[row]
        executor=Mock()
        executor.workspace.return_value=root
        # Failed preparation retains its files for investigation.
        retire_finished(queue,executor)
        self.assertTrue(upload.exists())
        executor.cleanup.assert_not_called()
        row['outcome']='success'
        executor.cleanup.side_effect=RuntimeError('Unconfirmed container removal')
        with self.assertRaises(RuntimeError):
            retire_finished(queue,executor)
        self.assertTrue(upload.exists())
        executor.cleanup.side_effect=None
        retire_finished(queue,executor)
        self.assertFalse(upload.parent.exists())
        self.assertEqual(diagnostic.read_text(),'private diagnostic')
        self.assertTrue(retained.exists())
        self.assertTrue(json.loads(marker.read_text())['preparation_uploads_removed'])
        executor.cleanup.reset_mock()
        retire_finished(queue,executor)
        executor.cleanup.assert_not_called()


if __name__=='__main__':
    unittest.main()
