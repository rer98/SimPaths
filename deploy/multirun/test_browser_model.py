"""(C) Copyright 2026, by Ross Richardson

Model form translation, immutable dataset defaults and local cleanup regressions.

@author ross richardson
"""
from copy import deepcopy
import sys
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from deploy._workflow import frontend_path
from deploy.multirun.artifacts import ArtifactError
from deploy.multirun.browser_model import BrowserModel
from deploy.multirun.schema import ConfigurationError
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

    def test_unknown_fields_and_unsupported_repetitions_rejected(self):
        for change in ({'owner':'other'},{'image':'evil'},{'repetitions':4},{'auto_retry':'yes'}):
            with self.assertRaises(ArtifactError):
                self.model.browser_configuration('dataset',{**self.form,**change})
        malicious=deepcopy(self.form)
        malicious['run_sets'][0]['model_args']['class']='java.lang.Runtime'
        with self.assertRaises(ConfigurationError):
            self.model.browser_configuration('dataset',malicious)

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


if __name__=='__main__':
    unittest.main()
