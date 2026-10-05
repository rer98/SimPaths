"""(C) Copyright 2026, by Ross Richardson

Check storage sampling, native fixture isolation and durable output settlement.
@author ross richardson
"""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from deploy._workflow import frontend_path
from .configuration import normalise
from .artifacts import ArtifactError
from .quota_rehearsal import fixture_plan,proof_command
from . import storage_proof
from .storage_proof import calibration, calibration_policy, checked_calibration, configuration, native_fixture, quota_sample, run_summary, sample, verify_queue_receipts


class StorageProofTests(unittest.TestCase):
    def setUp(self):
        old=list(sys.path); self.addCleanup(lambda:setattr(sys,'path',old))
        sys.path.insert(0,str(frontend_path()))
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)

    def test_full_length_configuration_has_one_card_and_requested_seed_count(self):
        for count in (1,3,12):
            data=normalise(configuration(dict(revision='quickstart-fictional'),count)).as_dict()
            self.assertEqual(data['common'],dict(country='UK',start_year=2019,end_year=2026,population=50000))
            self.assertEqual(len(data['run_sets']),1)
            self.assertEqual(data['seed_plan']['seeds'],[str(606+i) for i in range(count)])

    def test_size_summary_uses_seed_metadata_without_copying_csv_contents(self):
        for directory,seed in (('later-timestamp',606),('earlier-timestamp',607)):
            run=self.path/'work/output'/directory
            (run/'input').mkdir(parents=True); (run/'csv').mkdir()
            (run/'input/options.txt').write_text('randomSeedIfFixed: '+str(seed)+'\n')
            (run/'csv/Person.csv').write_bytes(b'fictional'*1024)
            if seed==606: (run/'input/parameters.xlsx').write_bytes(b'parameters')
        (self.path/'work/input').mkdir(); (self.path/'work/input/input.mv.db').write_bytes(b'input database')
        (self.path/'request').mkdir(); (self.path/'request/run.yml').write_bytes(b'original frozen settings')
        measured=sample(self.path)
        self.assertGreater(measured['work_bytes'],measured['input_bytes'])
        self.assertGreater(measured['csv_bytes'],0)
        self.assertGreater(measured['request_bytes'],0)
        self.assertEqual(measured['work_and_request_bytes'],measured['work_bytes']+measured['request_bytes'])
        runs=run_summary(self.path)
        self.assertEqual([r['seed'] for r in runs],['606','607'])
        self.assertEqual([r['seed'] for r in runs if r['copied_input_bytes']],['606'])
        self.assertNotIn('fictional',str(runs))

    def test_sampler_rejects_linked_files_instead_of_reading_external_paths(self):
        (self.path/'work').mkdir()
        external=self.path/'outside'; external.write_bytes(b'external private file')
        (self.path/'work/link').symlink_to(external)
        with self.assertRaises(ValueError): sample(self.path)

    def test_native_real_model_plan_reuses_wrapper_but_reserves_larger_disposable_volume(self):
        original=fixture_plan()
        real=fixture_plan(dict(image='sha256:'+'a'*64,calibration=calibration(),prepared_sha256='b'*64))
        self.assertEqual(original['roles'],('source','target'))
        self.assertEqual(original['loop_image_mib'],512)
        self.assertEqual(real['roles'],('source',))
        self.assertGreater(real['loop_image_mib']*1024**2,real['max_bytes']+real['reserve_bytes'])
        self.assertGreater(real['temporary_required_bytes'],real['loop_image_mib']*1024**2)
        command=proof_command(Path('/known/python'),Path('/known/frontend'),self.path,real)
        self.assertEqual(command[:2],['/known/python','/known/frontend/scripts/test_batch_queue.py'])
        self.assertEqual(command[command.index('--test-pattern')+1],'test_storage.py')
        self.assertEqual(command[command.index('--test-pattern')+2],'test_worker.py')
        self.assertEqual(Path(command[command.index('--proof-script')+1]).name,'storage_proof.py')
        self.assertNotIn('--proof-only',command)

    def test_native_preflight_rejects_mutable_images_and_unexpected_policy(self):
        correct=dict(image='sha256:'+'a'*64,calibration=calibration(),prepared_sha256='b'*64)
        for field,value in (('image','simpaths:latest'),('calibration',None),
                ('calibration',{**calibration(),'maximum_storage_mib':10240}),('prepared_sha256','wrong')):
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                fixture_plan({**correct,field:value})

    def test_prepared_check_is_read_only_and_requires_original_image_and_available_memory(self):
        receipt=dict(sha256='b'*64,identity=dict(source_image='sha256:'+'a'*64))
        with patch.object(storage_proof,'prepared_profile',return_value=receipt), \
                patch.object(storage_proof,'verify_snapshot') as verify, \
                patch.object(storage_proof,'allocation',side_effect=lambda receipt,**kw:dict(
                    storage_mib=4096+kw['resource_policy']['simulation']['storage']['per_repetition_mib']*kw['repetitions'])), \
                patch.object(storage_proof,'workspace_required_bytes',return_value=3*1024**3), \
                patch.object(Path,'read_text',return_value='MemAvailable: 9000000 kB\n'), \
                patch.object(storage_proof.subprocess,'check_output',return_value=receipt['identity']['source_image']) as docker:
            self.assertEqual(storage_proof.check_prepared(self.path),dict(image='sha256:'+'a'*64,
                calibration=calibration(),prepared_sha256='b'*64))
            verify.assert_called_once_with(self.path,receipt)
            profile=calibration([12],256)
            self.assertEqual(storage_proof.check_prepared(self.path,profile)['calibration'],profile)
            docker.return_value='sha256:'+'c'*64
            with self.assertRaises(ArtifactError):storage_proof.check_prepared(self.path)
            with patch.object(Path,'read_text',return_value='MemAvailable: 100000 kB\n'),self.assertRaises(ArtifactError):
                storage_proof.check_prepared(self.path)
        self.assertEqual(list(self.path.iterdir()),[])

    def test_native_fixture_matches_immutable_inputs_and_never_falls_back_on_mismatch(self):
        for name in ('execution','artifacts'):(self.path/name).mkdir()
        receipt=dict(sha256='b'*64,identity=dict(source_image='sha256:'+'a'*64))
        settings=dict(proof_mode='real-model-storage',image=receipt['identity']['source_image'],
            prepared_sha256=receipt['sha256'],prepared='/known/prepared',source=str(self.path),
            source_socket='/run/private/broker.sock',guard='/run/private/guard',calibration=calibration([12],256))
        fixture=self.path/'fixture.json'; fixture.write_text(json.dumps(settings))
        with patch.dict(storage_proof.os.environ,{},clear=True):self.assertIsNone(native_fixture(Path('/known/prepared'),receipt))
        with patch.dict(storage_proof.os.environ,{'JASMINE_QUOTA_FIXTURE':str(fixture)},clear=True):
            self.assertEqual(native_fixture(Path('/known/prepared'),receipt),settings)
            for field,value in (('proof_mode','fictional-quota'),('image','sha256:'+'c'*64),
                    ('prepared_sha256','d'*64),('prepared','/other/prepared')):
                fixture.write_text(json.dumps({**settings,field:value}))
                with self.subTest(field=field),self.assertRaises(ArtifactError):native_fixture(Path('/known/prepared'),receipt)
            fixture.write_text(json.dumps(settings))
            (self.path/'execution/orphan').mkdir()
            with self.assertRaises(ArtifactError):native_fixture(Path('/known/prepared'),receipt)

    def test_twelve_run_candidate_has_seven_gib_limit_larger_temporary_space_and_longer_timeout(self):
        profile=calibration([12],256)
        self.assertEqual(profile['maximum_storage_mib'],7168)
        plan=fixture_plan(dict(image='sha256:'+'a'*64,calibration=profile,prepared_sha256='b'*64))
        self.assertEqual(plan['loop_image_mib'],10240)
        self.assertEqual(plan['temporary_required_bytes'],12*1024**3)
        self.assertEqual(plan['max_bytes'],7*1024**3)
        command=proof_command(Path('/known/python'),Path('/known/frontend'),self.path,plan)
        timeout=int(command[command.index('--proof-timeout-seconds')+1])
        self.assertGreater(timeout,7200)
        self.assertGreater(timeout,900+12*3600)
        self.assertLessEqual(timeout,172800)

    def test_proof_policy_is_independent_of_default_and_rejects_tampered_size_or_time(self):
        original=deepcopy(storage_proof.DEFAULT_POLICY)
        profile=calibration([12],256)
        policy=calibration_policy(profile)
        self.assertEqual(policy['simulation']['storage'],dict(setup_mib=4096,per_repetition_mib=256))
        policy['simulation']['storage']['setup_mib']=1
        self.assertEqual(storage_proof.DEFAULT_POLICY,original)
        for field,value in (('maximum_storage_mib',5632),('proof_timeout_seconds',7200),
                ('setup_mib',True),('repetitions',[12,12]),('per_repetition_mib',0),('repetitions',[12,24])):
            changed={**profile,field:value}
            with self.subTest(field=field),self.assertRaises(ArtifactError):checked_calibration(changed)
            with self.subTest(root_field=field),self.assertRaises(ValueError):
                fixture_plan(dict(image='sha256:'+'a'*64,calibration=changed,prepared_sha256='b'*64))

    def test_prepared_profile_with_different_frozen_allowance_cannot_be_silently_recalibrated(self):
        receipt=dict(sha256='b'*64,identity=dict(source_image='sha256:'+'a'*64))
        with patch.object(storage_proof,'prepared_profile',return_value=receipt), \
                patch.object(storage_proof,'verify_snapshot'), \
                patch.object(storage_proof,'allocation',return_value=dict(storage_mib=10240)), \
                patch.object(storage_proof,'workspace_required_bytes',return_value=3*1024**3), \
                patch.object(storage_proof.subprocess,'check_output') as docker:
            with self.assertRaises(ArtifactError):storage_proof.check_prepared(self.path,calibration([12],256))
            docker.assert_not_called()

    def test_kernel_readback_must_match_frozen_limit_and_original_project(self):
        lease=SimpleNamespace(resources=dict(storage_mib=4608))
        client=Mock(); client.check.return_value=dict(limit_bytes=4608*1024**2,project_id=9,used_bytes=100)
        case={}; quota_sample(client,lease,case)
        client.check.return_value['used_bytes']=90; quota_sample(client,lease,case)
        self.assertEqual(case['quota_peak_used_bytes'],100)
        for field,value in (('limit_bytes',5632*1024**2),('project_id',10),('used_bytes',True),('used_bytes',5000*1024**2)):
            client.check.return_value={**dict(limit_bytes=4608*1024**2,project_id=9,used_bytes=100),field:value}
            with self.subTest(field=field),self.assertRaises(RuntimeError):quota_sample(client,lease,case)

    def receipt_fixture(self,attempt=None,rows=None,active=0):
        lease=SimpleNamespace(attempt_id='original',resources=dict(cpu_millis=2000,memory_mib=5120,storage_mib=4608),
            specification=dict(seeds=['606']))
        receipt=dict(seed='606',fingerprint='f'*64)
        correct=dict(phase='finished',outcome='success',state='succeeded',attempts=1,
            resources=lease.resources,released_at='recorded')
        cursor1=Mock();cursor1.fetchone.return_value=correct if attempt is None else attempt
        cursor2=Mock();cursor2.fetchall.return_value=[dict(ordinal=0,expected_seed='606',actual_seed='606',
            output_fingerprint='f'*64)] if rows is None else rows
        cursor3=Mock();cursor3.fetchone.return_value=dict(total=active)
        connection=Mock();connection.execute.side_effect=[cursor1,cursor2,cursor3]
        queue=SimpleNamespace(pool_id='fictional',_connection=lambda:nullcontext(connection))
        return queue,lease,[receipt],correct

    def test_durable_output_hashes_original_seeds_and_released_capacity_are_required(self):
        queue,lease,receipts,correct=self.receipt_fixture()
        value=verify_queue_receipts(queue,lease,receipts)
        self.assertTrue(value['reservations_released']);self.assertEqual(value['repetitions'],receipts)
        for field,replacement in (('phase','running'),('outcome','model'),('attempts',2),
                ('resources',{**lease.resources,'storage_mib':5632}),('released_at',None)):
            queue,lease,receipts,_=self.receipt_fixture(attempt={**correct,field:replacement})
            with self.subTest(field=field),self.assertRaises(RuntimeError):verify_queue_receipts(queue,lease,receipts)
        queue,lease,receipts,_=self.receipt_fixture(active=1)
        with self.assertRaises(RuntimeError):verify_queue_receipts(queue,lease,receipts)
        for field,value in (('ordinal',1),('expected_seed','607'),('actual_seed','607'),('output_fingerprint','e'*64)):
            row=dict(ordinal=0,expected_seed='606',actual_seed='606',output_fingerprint='f'*64)
            queue,lease,receipts,_=self.receipt_fixture(rows=[{**row,field:value}])
            with self.subTest(field=field),self.assertRaises(RuntimeError):verify_queue_receipts(queue,lease,receipts)

    def test_original_container_policy_and_confirmed_removal_are_required(self):
        from jasmine_web.batch.workspace_quota import BACKEND
        lease=SimpleNamespace(resources=dict(storage_mib=4608))
        image='sha256:'+'a'*64;guard=dict(source='/root/guard',sha256='b'*64)
        policy=dict(image=image,resources=lease.resources,workspace_quota=BACKEND,workspace_guard=guard)
        for name,data in (('container-policy.json',policy),('container.json',dict(id='original')),
                ('removed.json',dict(id='original'))):(self.path/name).write_text(json.dumps(data))
        executor=Mock();executor.workspace.return_value=self.path
        executor.inspect.return_value=dict(state='stopped',outcome='success',returncode=0,container_id='original')
        executor.docker.inspect.return_value=None
        self.assertEqual(storage_proof.verify_container(executor,lease,image,guard),'original')
        storage_proof.verify_removal(executor,lease,'original')
        altered=deepcopy(policy);altered['workspace_guard']['sha256']='c'*64
        (self.path/'container-policy.json').write_text(json.dumps(altered))
        with self.assertRaises(RuntimeError):storage_proof.verify_container(executor,lease,image,guard)
        executor.docker.inspect.return_value=dict(Id='original')
        with self.assertRaises(RuntimeError):storage_proof.verify_removal(executor,lease,'original')
