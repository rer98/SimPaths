"""(C) Copyright 2026, by Ross Richardson

Selected-input validation, receipt publication, failure cleanup and adapter tests.
Native parsing/database preparation is exercised by the explicit container proof.
@author ross richardson
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy.multirun.artifacts import ArtifactError, copy_verified, digest, fingerprint, inventory
from deploy.multirun.compare_native import proof_configuration
from deploy.multirun.container_adapter import SimPathsContainerAdapter, container_submission
from deploy.multirun.dataset_service import prepare_owned
from deploy.multirun.import_quickstart import UnconfirmedVerification
from deploy.multirun.prepare_inputs import prepare, selected_sources, selection
from deploy.multirun.prepared_dataset import check_input_receipt, verify_snapshot
from deploy.multirun.queue_adapter import SimPathsLocalAdapter
from deploy.multirun.run_input_proof import public_example_inputs

IMAGE='sha256:'+'a'*64


class InputPreparationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.defaults=self.root/'defaults'
        self.defaults.mkdir()
        (self.defaults/'parameters.xlsx').write_bytes(b'parameters')
        self.jar=self.root/'model.jar'
        self.jar.write_bytes(b'jar')
        self.uploads={}
        for name in ('population_initial_UK_2019.csv','policy.txt'):
            p=self.root/name
            p.write_bytes(b'source')
            self.uploads[name]=p
        self.request=dict(year=2019,schedule=[['policy.txt','2019','2019','Baseline']])
        self.output=self.root/'prepared'

    def worker(self, staged, request, image, output, frontend, timeout):
        self.assertFalse((output/'receipt.json').exists())
        destination=output/'input'
        shutil.copytree(staged/'defaults',destination)
        for p in (staged/'uploads').iterdir():
            rel=('InitialPopulations/' if p.suffix=='.csv' else 'EUROMODoutput/' if p.suffix=='.txt' else '')+p.name
            target=destination/rel
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(p,target)
        for name in ('input.mv.db','tax_donor_population_UK.csv','DatabaseCountryYear.xlsx','EUROMODpolicySchedule.xlsx'):
            (destination/name).write_bytes(b'generated')

    def prepare(self, worker=None):
        with patch('deploy.multirun.prepare_inputs.docker',return_value=json.dumps([{'Id':IMAGE,'Config':{}}])), \
             patch('deploy.multirun.prepare_inputs.run_container',side_effect=worker or self.worker), \
             patch('deploy.multirun.prepare_inputs.shutil.disk_usage',return_value=SimpleNamespace(free=100<<30)):
            return prepare(self.defaults,self.uploads,self.request,self.jar,IMAGE,self.output,self.root)

    def test_preparation_publishes_last_and_removes_temporary_sources(self):
        receipt=self.prepare()
        self.assertEqual(check_input_receipt(receipt),receipt)
        verify_snapshot(self.output,receipt)
        self.assertFalse((self.output/'sources').exists())
        self.assertTrue((self.output/'receipt.json').exists())
        original=inventory(self.output/'input')
        for i in range(2):
            copy_verified(self.output/'input',original,self.root/f'run{i}')
            (self.root/f'run{i}/input.mv.db').write_bytes(b'changed')
            verify_snapshot(self.output,receipt)

    def test_selected_files_and_schedule_reject_unsupported_inputs(self):
        for name in ('input.mv.db','../evil.csv','DatabaseCountryYear.xlsx','unknown.xlsx','wrong.csv'):
            with self.subTest(name=name),self.assertRaises(ArtifactError):
                selected_sources(self.defaults,{**self.uploads,name:self.jar},selection(self.request))
        for change in (dict(year=True),dict(year=1900),dict(extra='bad'),dict(schedule=[]),
                       dict(schedule=[['../policy.txt','2019','2019','']]),
                       dict(schedule=self.request['schedule']*2)):
            with self.subTest(change=change),self.assertRaises(ArtifactError):
                selection({**self.request,**change})

    def test_blank_policy_year_is_omitted_and_missing_active_input_rejected(self):
        request=deepcopy(self.request)
        request['schedule'].append(['unused.txt','','',''])
        self.assertEqual(selection(request)['schedule'],self.request['schedule'])
        with self.assertRaises(ArtifactError):
            selected_sources(self.defaults,{},selection(request))

    def test_failed_preparation_retains_no_ready_receipt_or_large_copies(self):
        def fail(*args):
            self.worker(*args)
            raise ArtifactError('Invalid workbook')
        with self.assertRaises(ArtifactError):
            self.prepare(fail)
        self.assertFalse((self.output/'receipt.json').exists())
        self.assertFalse((self.output/'input').exists())
        self.assertFalse((self.output/'sources').exists())

    def test_uncertain_container_state_retains_sources_and_no_receipt(self):
        def fail(*args):
            raise UnconfirmedVerification('No stop confirmation')
        with self.assertRaises(UnconfirmedVerification):
            self.prepare(fail)
        self.assertTrue((self.output/'sources').exists())
        self.assertFalse((self.output/'receipt.json').exists())

    def test_changed_input_and_added_link_never_publish(self):
        def change(*args):
            self.worker(*args)
            (self.output/'input/InitialPopulations/population_initial_UK_2019.csv').write_bytes(b'changed')
        with self.assertRaises(ArtifactError):
            self.prepare(change)
        self.assertFalse((self.output/'receipt.json').exists())

    def test_wrong_start_year_or_image_rejected_and_large_run_gets_larger_heap(self):
        receipt=self.prepare()
        config=proof_configuration().editable_configuration()
        config['dataset_revision']='server-issued-dataset'
        config['common']['population']=50000
        args=container_submission(config,self.output,IMAGE)
        self.assertEqual(args['dataset_id'],'server-issued-dataset')
        args['prepared_fingerprint']=receipt['sha256']
        with self.assertRaises(ArtifactError):
            SimPathsLocalAdapter(self.output).command(None, self.root)
        lease=SimpleNamespace(specification=args,configuration_id=args['run_sets'][0]['id'],
            resources=dict(cpu_millis=2000,memory_mib=5120,storage_mib=10240))
        # Queue changes seed_plan to seeds in its frozen specification.
        lease.specification['seeds']=lease.specification.pop('seed_plan')
        directory=self.root/'command'
        directory.mkdir()
        lease.resources['memory_mib']=4096
        with self.assertRaises(ArtifactError):
            SimPathsContainerAdapter(self.output,IMAGE).container_command(lease,directory)
        lease.resources['memory_mib']=5120
        with patch('deploy.multirun.container_adapter.require_workspace_space'):
            command=SimPathsContainerAdapter(self.output,IMAGE).container_command(lease,directory)
        self.assertEqual(command.argv[-1],'3g')
        config['common']['start_year']=2018
        with self.assertRaises(ArtifactError):
            container_submission(config,self.output,IMAGE)
        with self.assertRaises(ArtifactError):
            SimPathsContainerAdapter(self.output,'sha256:'+'b'*64)

    def test_model_and_schedule_changes_require_new_identity(self):
        receipt=self.prepare()
        changed=deepcopy(receipt)
        changed['identity']['selection']['schedule'][0][2]='2020'
        with self.assertRaises(ArtifactError):
            check_input_receipt(changed)
        (self.output/'model.jar').chmod(0o600)
        (self.output/'model.jar').write_bytes(b'other')
        with self.assertRaises(ArtifactError):
            verify_snapshot(self.output,receipt)

    def test_platform_bridge_checks_upload_hashes_before_publication(self):
        receipt=self.prepare()
        service=Mock()
        service.resolve_uploads.return_value={name:dict(path=path,**fingerprint(path)) for name,path in self.uploads.items()}
        service.publish.return_value='dataset-opaque'
        with patch('deploy.multirun.dataset_service.prepare',return_value=receipt):
            result=prepare_owned(service,'alice',['id-a','id-b'],defaults=self.defaults,
                request=self.request,jar=self.jar,image=IMAGE,output=self.output,frontend=self.root)
            self.assertEqual(result['dataset_id'],'dataset-opaque')
            service.publish.assert_called_once_with('alice',receipt['sha256'],uploads=['id-a','id-b'])
            service.reset_mock()
            receipt['identity']['sources']['uploads']['policy.txt']['sha256']='b'*64
            with self.assertRaises(ArtifactError):
                prepare_owned(service,'alice',['id-a','id-b'],defaults=self.defaults,
                    request=self.request,jar=self.jar,image=IMAGE,output=self.output,frontend=self.root)
            service.publish.assert_not_called()

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java')
                         and (Path(__file__).resolve().parents[2]/'multirun.jar').is_file(),
                         'Requires Java and the current multirun.jar')
    def test_proof_schedule_matches_native_base_price_requirement(self):
        files, request = public_example_inputs()
        names = {p.name for p in files[1:]}
        self.assertEqual(names, {row[0] for row in request['schedule']})
        self.assertTrue(all(path.is_file() for path in files))
        directory = self.root/'native-schedule'
        directory.mkdir()
        path = self.root/'selection.json'
        path.write_text(json.dumps(request))
        source = Path(__file__).resolve().parent
        jar = source.parents[1]/'multirun.jar'
        result = subprocess.run(['javac', '-proc:none', '-cp', str(jar), '-d', str(directory),
            str(source/'PrepareDataset.java'), str(source/'PrepareDatasetScheduleTest.java')],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        result = subprocess.run(['java', '-Xmx512m', '-Djava.awt.headless=true', '-cp',
            os.pathsep.join((str(directory), str(jar))), 'simpaths.experiment.PrepareDatasetScheduleTest',
            str(directory), str(path)], cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('corrected proof schedule is valid', result.stdout)
