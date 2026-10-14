"""(C) Copyright 2026, by Ross Richardson

Verify that native release qualification freezes adapters, runtime and seed budgets.
Uses tiny fictional bundles; no models, database, Docker or network access.
@author ross richardson
"""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import Mock,patch

from .artifacts import ArtifactError,fingerprint
from .configuration import normalise
from .prepared_dataset import INPUT_FORMAT
from .releases import ReleaseRegistry
from .resource_policy import DEFAULT_POLICY,LEGACY_POLICY
from .run_input_proof import selected_release,main,proof_preflight,receive_public_inputs,public_example_inputs
from .run_queue_proof import proof_execution
from .queued_preparation_proof import prepare_queued

IMAGE='sha256:'+'a'*64


class ReleaseRefreshTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.jar=self.root/'model.jar';self.jar.write_bytes(b'fictional frozen model')
        self.defaults=self.root/'input';self.defaults.mkdir()
        (self.defaults/'scenario_CPI.xlsx').write_bytes(b'fictional frozen workbook')
        self.registry=ReleaseRegistry(self.root/'state')
        self.key=self.registry.register(image=IMAGE,name='Reviewed candidate',jar=self.jar,defaults=self.defaults)

    def test_selection_uses_frozen_bytes_and_leaves_the_service_default_unchanged(self):
        second=self.registry.register(image=IMAGE,name='Other candidate',jar=self.jar,defaults=self.defaults,
            policy={**DEFAULT_POLICY,'simulation':{**DEFAULT_POLICY['simulation'],
                'storage':{'setup_mib':5120,'per_repetition_mib':256}}})
        catalogue=self.registry.catalogue.read_bytes()
        self.jar.write_bytes(b'later checkout edits')
        record=selected_release(self.registry.state,second,IMAGE)
        self.assertEqual(record['jar'].read_bytes(),b'fictional frozen model')
        self.assertEqual(self.registry.catalogue.read_bytes(),catalogue)
        self.assertEqual(next(iter(self.registry.inventory())),self.key)
        self.assertTrue(record['runner'].is_file())

    def test_missing_release_wrong_runtime_and_modified_adapter_are_rejected(self):
        with self.assertRaises(ArtifactError):selected_release(self.registry.state,'release-'+'b'*64,IMAGE)
        with self.assertRaises(ArtifactError):selected_release(self.registry.state,self.key,'sha256:'+'b'*64)
        runner=self.registry.inventory()[self.key]['runner'];runner.chmod(0o600);runner.write_bytes(b'changed')
        with self.assertRaises(ArtifactError):selected_release(self.registry.state,self.key,IMAGE)

    def test_cli_requires_the_complete_release_selection_and_queued_preparation(self):
        base=['proof','--frontend',str(self.root),'--output',str(self.root/'evidence')]
        for options in (['--release-state',str(self.registry.state)],['--release',self.key],
                        ['--release-state',str(self.registry.state),'--release',self.key]):
            with self.subTest(options=options),patch('sys.argv',base+options), \
                 patch('deploy.multirun.run_input_proof.subprocess.check_output') as inspect:
                with self.assertRaises(SystemExit):main()
                inspect.assert_not_called()

    def test_cli_pins_the_registered_runtime_and_preserves_proof_only_selection(self):
        args=['proof','--frontend',str(self.root),'--output',str(self.root/'evidence'),
              '--release-state',str(self.registry.state),'--release',self.key,'--queued-preparation','--proof-only']
        with patch('sys.argv',args),patch('deploy.multirun.run_input_proof.proof_preflight'), \
             patch('deploy.multirun.run_input_proof.subprocess.check_output',return_value=IMAGE+'\n') as inspect, \
             patch('deploy.multirun.run_input_proof.subprocess.call',return_value=0) as run:
            self.assertEqual(main(),0)
        self.assertEqual(inspect.call_args.args[0][3],IMAGE)
        command=run.call_args.args[0];environment=run.call_args.kwargs['env']
        self.assertIn('--proof-only',command)
        self.assertEqual(environment['SIMPATHS_INPUT_RELEASE_ID'],self.key)
        self.assertEqual(environment['SIMPATHS_INPUT_RELEASE_STATE'],str(self.registry.state))
        self.assertEqual(environment['SIMPATHS_INPUT_IMAGE'],IMAGE)

    def test_preparation_rejects_a_different_model_before_any_worker_is_created(self):
        record=selected_release(self.registry.state,self.key,IMAGE)
        with patch('jasmine_web.batch.worker.Worker') as worker:
            with self.assertRaises(ArtifactError):
                prepare_queued(Mock(), 'owner', [], defaults=record['defaults'],jar=self.jar,
                    image=IMAGE,output=self.root/'preparation',frontend=self.root,request={},
                    retained_release=(self.key,record))
            worker.assert_not_called()

    def public_upload_fixture(self):
        examples,request=public_example_inputs()
        sources=self.root/'public-inputs';sources.mkdir()
        files=[]
        for example in examples:
            path=sources/example.name;path.write_bytes(b'fictional valid input\n');files.append(path)
        bodies=self.root/'uploaded';bodies.mkdir()
        uploads={}
        service=Mock()
        def receive(owner,name,stream,*,expected_bytes):
            from jasmine_web.batch.policy import Conflict
            if any(item['name'].lower()==name.lower() for item in uploads.values()):
                raise Conflict('Duplicate upload name')
            body=stream.read();self.assertEqual(len(body),expected_bytes)
            key='upload-'+str(service.receive.call_count)
            path=bodies/key;path.write_bytes(body)
            uploads[key]=dict(name=name,path=path,**fingerprint(path))
            return key
        def resolve(owner,keys):
            return {uploads[key]['name']:{k:v for k,v in uploads[key].items() if k!='name'} for key in keys}
        def clear(owner,keys):
            for key in keys:uploads.pop(key)['path'].unlink()
            return dict(removed=keys,retained=[])
        service.receive.side_effect=receive
        service.resolve_uploads.side_effect=resolve
        service.clear_uploads.side_effect=clear
        return service,files,request,uploads

    def test_invalid_content_reaches_preparation_and_clears_its_name_before_valid_upload(self):
        service,files,request,uploads=self.public_upload_fixture()
        output=self.root/'invalid'
        def reject(staged,selection,image,directory,frontend,timeout):
            self.assertEqual(selection['year'],2019)
            selected=staged/'uploads'
            self.assertEqual({p.name for p in selected.iterdir()},{p.name for p in files})
            self.assertEqual((selected/files[0].name).read_bytes(),b'invalid\n1\n')
            (directory/'preparation.log').write_text('Preparation failed while validating selected files.\n')
            raise ArtifactError('Input preparation failed; see preparation.log')
        with patch('deploy.multirun.prepare_inputs.docker',return_value=json.dumps([dict(Id=IMAGE,Config={})])), \
             patch('deploy.multirun.prepare_inputs.shutil.disk_usage',return_value=SimpleNamespace(free=8*1024**3)), \
             patch('deploy.multirun.prepare_inputs.run_container',side_effect=reject) as native:
            ids=receive_public_inputs(service,'owner',files,output=output,defaults=self.defaults,
                request=request,jar=self.jar,image=IMAGE,frontend=self.root)
        native.assert_called_once()
        service.publish.assert_not_called()
        service.clear_uploads.assert_called_once_with('owner',['upload-4'])
        self.assertNotIn('upload-4',uploads)
        self.assertEqual([uploads[key]['name'] for key in ids],[p.name for p in files])
        self.assertEqual(uploads[ids[0]]['path'].read_bytes(),files[0].read_bytes())
        self.assertFalse((output/'receipt.json').exists())

    def test_other_preparation_failures_do_not_count_as_invalid_content_rejection(self):
        service,files,request,uploads=self.public_upload_fixture()
        output=self.root/'invalid'
        with patch('deploy.multirun.run_input_proof.prepare_owned',
                   side_effect=ArtifactError('Insufficient space for preparation copies')):
            with self.assertRaisesRegex(ArtifactError,'Insufficient space'):
                receive_public_inputs(service,'owner',files,output=output,defaults=self.defaults,
                    request=request,jar=self.jar,image=IMAGE,frontend=self.root)
        self.assertEqual(service.receive.call_count,4)
        self.assertTrue(any(item['path'].read_bytes()==b'invalid\n1\n' for item in uploads.values()))
        service.clear_uploads.assert_not_called()

    def receipt(self,policy):
        return dict(revision='inputs-'+'f'*64,identity=dict(format=INPUT_FORMAT,start_year=2019,
            model=fingerprint(self.jar),prepared={'input.mv.db':{'bytes':100,'sha256':'f'*64}},
            release=dict(id=self.key,resource_policy=policy)))

    def test_queue_budget_covers_three_seeds_and_uses_the_prepared_release(self):
        configuration,resources=proof_execution(self.receipt(DEFAULT_POLICY),reused=True)
        data=normalise(configuration).as_dict()
        self.assertEqual(data['model_release'],self.key)
        self.assertEqual(data['seed_plan']['seeds'],['606','607','608'])
        self.assertEqual(data['common']['population'],2000)
        self.assertEqual([data['common']['start_year'],data['common']['end_year']],[2019,2020])
        self.assertEqual(resources['storage_mib'],4096+3*256)
        self.assertEqual(resources['memory_mib'],DEFAULT_POLICY['simulation']['memory_mib'])

    def test_legacy_receipts_retain_their_fixed_storage_policy(self):
        receipt=self.receipt(LEGACY_POLICY);del receipt['identity']['release']
        _,resources=proof_execution(receipt,reused=True)
        self.assertEqual(resources['storage_mib'],LEGACY_POLICY['simulation']['storage']['setup_mib'])

    def test_preflight_refuses_low_disk_or_ram_before_native_execution(self):
        with patch('deploy.multirun.run_input_proof.shutil.disk_usage') as usage, \
             patch('deploy.multirun.run_input_proof.Path.read_text',return_value='MemAvailable: 8388608 kB\n'):
            usage.return_value=SimpleNamespace(free=5*1024**3)
            with self.assertRaisesRegex(ArtifactError,'free'):proof_preflight()
            usage.return_value=SimpleNamespace(free=8*1024**3)
            proof_preflight()
        with patch('deploy.multirun.run_input_proof.shutil.disk_usage',return_value=SimpleNamespace(free=8*1024**3)), \
             patch('deploy.multirun.run_input_proof.Path.read_text',return_value='MemAvailable: 1048576 kB\n'):
            with self.assertRaisesRegex(ArtifactError,'RAM'):proof_preflight()


if __name__=='__main__':unittest.main()
