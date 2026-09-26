"""(C) Copyright 2026, by Ross Richardson

Model review, immutable preparation and interrupted publication regressions.
Small synthetic files exercise bookkeeping; the container proof runs real Java.

@author ross richardson
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from deploy._workflow import frontend_path
from deploy.multirun.artifacts import ArtifactError, digest, fingerprint
from deploy.multirun.compare_native import proof_configuration
from deploy.multirun.prepared_dataset import verify_snapshot
from deploy.multirun.queue_adapter import read_prepared
from deploy.multirun.queued_preparation_proof import retire_preparation_attempt
from deploy.multirun.submission_adapter import SubmissionModel, PreparationAdapter


class SubmissionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.old_path = list(sys.path)
        self.addCleanup(lambda: setattr(sys,'path',self.old_path))
        sys.path.insert(0,str(frontend_path()))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        defaults = self.root/'defaults'
        defaults.mkdir()
        (defaults/'parameters.xlsx').write_bytes(b'parameters')
        jar = self.root/'model.jar'
        jar.write_bytes(b'model')
        self.image = 'sha256:'+'a'*64
        self.releases = {'approved':dict(defaults=defaults,jar=jar,image=self.image)}
        self.model = SubmissionModel(self.releases)
        upload_root = self.root/'uploads'
        upload_root.mkdir()
        self.uploads = {}
        for name in ('population_initial_UK_2019.csv','policy.txt'):
            key = uuid4().hex
            path = upload_root/key
            path.write_bytes(b'source')
            self.uploads[name] = dict(id=key,path=path,**fingerprint(path))
        self.selection = dict(year=2019,schedule=[['policy.txt','2019','2019','Baseline']])
        plan = self.model.preparation('approved',self.uploads,self.selection)
        params = dict(model=plan['parameters'],uploads={k:{a:v[a] for a in ('id','bytes','sha256')} for k,v in self.uploads.items()})
        self.lease = SimpleNamespace(execution_key='batch-'+str(uuid4()),configuration_id='prepare',
            resources=dict(cpu_millis=2000,memory_mib=5120,storage_mib=12288),specification=dict(operation='prepare',
                seeds=['0'],model_digest=self.image,prepared_fingerprint=digest(params),run_sets=[dict(id='prepare',parameters=params)]))
        service = SimpleNamespace(root=upload_root,queue=None)
        self.adapter = PreparationAdapter(service,self.releases,self.root/'artifacts')
        self.request = self.root/'attempt/request'
        self.request.mkdir(parents=True)
        self.work = self.root/'attempt/work'
        self.work.mkdir()

    def command(self):
        with patch('deploy.multirun.submission_adapter.subprocess.run') as compiler, \
             patch('deploy.multirun.submission_adapter.shutil.disk_usage',return_value=SimpleNamespace(free=100<<30)):
            command = self.adapter.container_command(self.lease,self.request)
        self.assertEqual(compiler.call_count,1)
        return command

    def result(self):
        self.command()
        shutil.copytree(self.request/'sources/defaults',self.work/'input')
        for name,value in self.uploads.items():
            sub = 'InitialPopulations' if name.endswith('.csv') else 'EUROMODoutput'
            destination = self.work/'input'/sub/name
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(value['path'],destination)
        for name in ('input.mv.db','tax_donor_population_UK.csv','DatabaseCountryYear.xlsx','EUROMODpolicySchedule.xlsx'):
            (self.work/'input'/name).write_bytes(b'generated')
        (self.work.parent/'execution.log').write_text('MULTIRUN_INPUTS_PREPARED_AND_VALIDATED\n')

    def test_model_review_chooses_image_and_allocation(self):
        plan = self.model.preparation('approved',self.uploads,self.selection)
        self.assertEqual(plan['image'],self.image)
        self.assertEqual(plan['resources'].memory_mib,5120)
        with self.assertRaises(ArtifactError):
            self.model.preparation('unapproved',self.uploads,self.selection)
        with self.assertRaises(ArtifactError):
            self.model.preparation('approved',self.uploads,{**self.selection,'image':'evil'})

    def test_preparation_freezes_schedule_model_and_uploads(self):
        command = self.command()
        self.assertEqual(command.image,self.image)
        self.assertEqual(json.loads((self.request/'selection.json').read_text())['year'],2019)
        self.assertEqual(fingerprint(Path(command.inputs)/'model.jar'),fingerprint(self.releases['approved']['jar']))
        self.assertFalse(any(str(value['path']) in arg for value in self.uploads.values() for arg in command.argv))

    def test_low_space_reports_counts_before_copying_inputs_or_compiling(self):
        from jasmine_web.batch.docker_executor import InsufficientWorkspaceSpace
        with patch('deploy.multirun.submission_adapter.shutil.disk_usage',
                   return_value=SimpleNamespace(free=1 << 30)), \
             patch('deploy.multirun.submission_adapter.subprocess.run') as compiler:
            with self.assertRaises(InsufficientWorkspaceSpace) as rejected:
                self.adapter.container_command(self.lease, self.request)
        self.assertGreater(rejected.exception.required_bytes, 2 << 30)
        self.assertEqual(rejected.exception.available_bytes, 1 << 30)
        self.assertFalse((self.request / 'sources').exists())
        compiler.assert_not_called()

    def test_changed_model_or_upload_stops_before_dispatch(self):
        self.releases['approved']['jar'].write_bytes(b'changed')
        with self.assertRaises(ArtifactError):
            self.command()
        shutil.rmtree(self.request/'sources')
        self.releases['approved']['jar'].write_bytes(b'model')
        next(iter(self.uploads.values()))['path'].write_bytes(b'changed')
        with self.assertRaises(ArtifactError):
            self.command()

    def test_publication_moves_large_output_and_is_repeatable_after_database_failure(self):
        self.result()
        first = self.adapter.validate(self.lease,self.work)
        target = self.adapter.artifacts/self.lease.execution_key
        self.assertFalse((self.work/'input').exists())
        receipt = read_prepared(target)
        verify_snapshot(target,receipt)
        second = self.adapter.validate(self.lease,self.work)
        self.assertEqual(first,second)
        self.assertEqual(first[0]['fingerprint'],receipt['sha256'])
        self.assertEqual(len(list(self.adapter.artifacts.iterdir())),1)

    def test_interrupted_move_recovers_without_a_second_preparation(self):
        self.result()
        with patch('jasmine_web.batch.local_executor.atomic_json',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.adapter.validate(self.lease,self.work)
        target = self.adapter.artifacts/self.lease.execution_key
        self.assertTrue((target/'input').is_dir())
        self.assertFalse((target/'receipt.json').exists())
        self.adapter.validate(self.lease,self.work)
        verify_snapshot(target,read_prepared(target))

    def test_interrupted_model_copy_recovers_from_partial_temporary_file(self):
        self.result()
        original = fingerprint
        def interrupt(path, destination=None):
            if destination is not None and destination.name == '.model.jar.pending':
                destination.write_bytes(b'partial')
                raise OSError('Copy interrupted')
            return original(path,destination)
        with patch('deploy.multirun.submission_adapter.fingerprint',side_effect=interrupt):
            with self.assertRaises(OSError):
                self.adapter.validate(self.lease,self.work)
        target = self.adapter.artifacts/self.lease.execution_key
        self.assertTrue((target/'.model.jar.pending').exists())
        self.assertFalse((target/'model.jar').exists())
        self.adapter.validate(self.lease,self.work)
        self.assertFalse((target/'.model.jar.pending').exists())
        verify_snapshot(target,read_prepared(target))

    def test_bad_output_or_unconfirmed_validation_never_publishes(self):
        self.result()
        (self.work.parent/'execution.log').write_text('failure')
        with self.assertRaises(ArtifactError):
            self.adapter.validate(self.lease,self.work)
        (self.work.parent/'execution.log').write_text('MULTIRUN_INPUTS_PREPARED_AND_VALIDATED')
        (self.work/'input/InitialPopulations/population_initial_UK_2019.csv').write_bytes(b'changed')
        with self.assertRaises(ArtifactError):
            self.adapter.validate(self.lease,self.work)
        self.assertFalse((self.adapter.artifacts/self.lease.execution_key/'receipt.json').exists())

    def test_experiment_uses_registered_dataset_baseline_and_fixed_configurations(self):
        self.result()
        self.adapter.validate(self.lease,self.work)
        prepared = self.adapter.artifacts/self.lease.execution_key
        receipt = read_prepared(prepared)
        resolved = dict(dataset_id='dataset-owned',location=str(prepared),model_digest=self.image,
                        prepared_fingerprint=receipt['sha256'])
        config = proof_configuration().editable_configuration()
        config = normal_fixed(config)
        config['dataset_revision']='dataset-owned'
        plan = self.model.experiment(resolved,dict(configuration=config,baseline=config['run_sets'][0]['id']))
        self.assertEqual(plan['seed_plan'],['606','607','608'])
        self.assertEqual(plan['model_digest'],self.image)
        self.assertEqual(plan['resources'].memory_mib,4096)
        for change in ({'owner':'other'},{'resources':{}},{'image':'evil'}):
            with self.assertRaises(ArtifactError):
                self.model.experiment(resolved,dict(configuration=config,**change))

    def test_proof_releases_temporary_payload_after_removal_and_keeps_dataset(self):
        self.result()
        self.adapter.validate(self.lease,self.work)
        target = self.adapter.artifacts/self.lease.execution_key
        receipt = read_prepared(target)
        workspace = self.work.parent
        evidence = self.root/'evidence'
        evidence.mkdir()
        executor = Mock()
        executor.workspace.return_value = workspace
        def removed(lease):
            self.assertTrue(self.request.exists())
            self.assertTrue(self.work.exists())
            (workspace/'removed.json').write_text('{"id":"test-container"}')
        executor.cleanup.side_effect = removed
        retire_preparation_attempt(executor,self.lease,evidence)
        executor.cleanup.assert_called_once_with(self.lease)
        self.assertFalse(self.request.exists())
        self.assertFalse(self.work.exists())
        self.assertTrue((workspace/'removed.json').is_file())
        self.assertTrue((evidence/'attempt-diagnostics/removed.json').is_file())
        self.assertTrue((evidence/'preparation.log').is_file())
        self.assertTrue(json.loads((evidence/'preparation-cleanup.json').read_text())['temporary_payload_removed'])
        verify_snapshot(target,receipt)

    def test_proof_retains_payload_when_container_removal_is_uncertain(self):
        self.result()
        executor = Mock()
        executor.workspace.return_value = self.work.parent
        executor.cleanup.side_effect = RuntimeError('Container removal unconfirmed')
        with self.assertRaises(RuntimeError):
            retire_preparation_attempt(executor,self.lease,self.root/'evidence')
        self.assertTrue(self.request.is_dir())
        self.assertTrue((self.work/'input/input.mv.db').is_file())
        self.assertFalse((self.root/'evidence').exists())


def normal_fixed(config):
    from deploy.multirun.configuration import normalise
    frozen = normalise(config).as_dict()
    result = deepcopy(config)
    result.pop('sweep',None)
    result['run_sets'] = frozen['run_sets']
    return result
