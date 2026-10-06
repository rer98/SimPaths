"""(C) Copyright 2026, by Ross Richardson

Validate JVM measurements, native rehearsal bounds and both container retirement steps.
@author ross richardson
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

if not __package__:
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    __package__='deploy.multirun'

from . import resource_recovery_proof as proof
from .quota_rehearsal import fixture_plan,proof_command
from .storage_proof import calibration


def row(**changes):
    result=dict(sequence=0,elapsed_ms=0,heap_used_bytes=32*proof.MIB,heap_committed_bytes=64*proof.MIB,
        heap_max_bytes=128*proof.MIB,nonheap_used_bytes=8*proof.MIB,container_limit_bytes=256*proof.MIB,
        container_used_bytes=160*proof.MIB,inactive_file_bytes=32*proof.MIB,working_set_bytes=128*proof.MIB,
        cgroup_peak_bytes=170*proof.MIB,gc_ms=0)
    return {**result,**changes}


def log(rows):
    return '\n'.join(proof.PREFIX+json.dumps(value) for value in rows)


class ResourceRecoveryProofTests(unittest.TestCase):
    def test_live_limit_increase_is_measured_without_changing_heap_or_pid_evidence(self):
        first=row(); second={**first,'sequence':1,'elapsed_ms':1000,'container_limit_bytes':320*proof.MIB,'gc_ms':2}
        observed=proof.telemetry('native model log\n'+log([first,second]))
        self.assertEqual(observed,[first,second])
        result=proof.summary(observed,128)
        self.assertEqual(result['observed_container_limits_bytes'],[256*proof.MIB,320*proof.MIB])
        self.assertEqual(result['peak']['working_set_bytes'],128*proof.MIB)
        self.assertEqual(result['gc_ms'],2)

    def test_corrupt_negative_missing_and_inconsistent_metrics_are_rejected(self):
        good=row()
        for key,value in [('heap_used_bytes',129*proof.MIB),('working_set_bytes',160*proof.MIB),
                ('container_limit_bytes',-1),('inactive_file_bytes',True),('gc_ms',-1),('sequence',3)]:
            with self.subTest(key=key),self.assertRaises(ValueError):proof.telemetry(log([{**good,key:value}]))
        for bad in ({k:v for k,v in good.items() if k!='heap_max_bytes'},{**good,'extra':1}):
            with self.assertRaises(ValueError):proof.telemetry(log([bad]))

    def test_truncated_duplicate_backwards_and_changed_heap_samples_are_rejected(self):
        first=row(); second={**first,'sequence':1,'elapsed_ms':1000,'gc_ms':5}
        for key,value in [('sequence',0),('sequence',2),('elapsed_ms',-1),('heap_max_bytes',256*proof.MIB)]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                proof.telemetry(log([first,{**second,key:value}]))
        with self.assertRaises(ValueError):proof.telemetry(log([second]))
        with self.assertRaises(ValueError):proof.summary([],1)
        with self.assertRaises(ValueError):proof.summary([first],1)
        with self.assertRaises(ValueError):proof.summary([first],128.0)

    def test_case_settings_separate_calibration_from_deliberate_recovery_pressure(self):
        control,baseline=proof.case_policy('control')
        trial,candidate=proof.case_policy('heap-trial')
        retry,recovery=proof.case_policy('heap-retry')
        growth,live=proof.case_policy('live-growth')
        self.assertEqual((control,trial,retry,growth),(3072,4096,512,3072))
        self.assertEqual((baseline.max_memory_mib,candidate.max_memory_mib),(5120,5120))
        self.assertEqual((recovery.pressure_percent,live.pressure_percent),(85,50))
        self.assertEqual((recovery.max_memory_mib,live.max_memory_mib),(7168,7168))
        with self.assertRaises(ValueError):proof.case_policy('unbounded')

    def test_passive_entry_overlay_preserves_frozen_yaml_input_hashes_and_jvm_flags(self):
        from .test_container_adapter import ContainerAdapterTests,IMAGE
        fixture=ContainerAdapterTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with tempfile.TemporaryDirectory() as temporary:
            classes=Path(temporary);(classes/'SimPathsResourceProbe.class').write_bytes(b'fixture class')
            adapter=proof.ObservedAdapter(fixture.fixture.prepared,IMAGE,classes)
            command=adapter.container_command(fixture.lease,fixture.fixture.work)
            self.assertEqual(command.argv,('/bin/sh','/request/run.sh','2g'))
            request=fixture.fixture.work
            self.assertEqual((request/'run.yml').read_text(),fixture.fixture.configuration.native_yaml('savings-0001'))
            self.assertIn('a'*64+'  /inputs/model.jar',(request/'inputs.sha256').read_text())
            original=(Path(proof.__file__).with_name('container_run.sh')).read_text()
            restored=(request/'run.sh').read_text().replace(
                '-cp /request/probe:/inputs/model.jar SimPathsResourceProbe',
                '-cp /inputs/model.jar simpaths.experiment.SimPathsMultiRun')
            self.assertEqual(restored,original)
            self.assertEqual((request/'probe/SimPathsResourceProbe.class').read_bytes(),b'fixture class')

    def test_native_growth_has_space_for_ceiling_and_fixed_normal_repetition(self):
        checked=dict(image='sha256:'+'a'*64,prepared_sha256='b'*64,calibration=calibration([1],256))
        plan=fixture_plan(checked,resource_recovery=True)
        self.assertEqual(plan['roles'],('source',))
        self.assertEqual(plan['max_bytes'],8704*proof.MIB)
        self.assertGreater(plan['loop_image_mib']*proof.MIB,plan['max_bytes']+plan['reserve_bytes'])
        command=proof_command(Path('/known/python'),Path('/frontend'),Path('/evidence'),plan)
        self.assertIn(str(Path(proof.__file__)),command)
        self.assertIn(str(proof.ROOT/'deploy/acceptance/requirements.txt'),command)
        for changed in (None,{**checked,'calibration':calibration([3],256)},
                {**checked,'calibration':calibration([1],512)}):
            with self.assertRaises(ValueError):fixture_plan(changed,resource_recovery=True)

    def test_wrong_native_fixture_mode_or_dirty_root_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)
            for name in ('execution','artifacts'):(path/name).mkdir()
            receipt=dict(sha256='b'*64,identity=dict(source_image='sha256:'+'a'*64))
            settings=dict(proof_mode='real-model-resource-recovery',image=receipt['identity']['source_image'],
                prepared_sha256=receipt['sha256'],prepared='/prepared',source=str(path),
                source_socket='/broker.sock',guard='/guard',calibration=calibration([1],256))
            fixture=path/'fixture.json';fixture.write_text(json.dumps(settings))
            with patch.dict(proof.os.environ,{'JASMINE_QUOTA_FIXTURE':str(fixture)},clear=True):
                self.assertEqual(proof.native_settings(Path('/prepared'),receipt),settings)
                for key,value in [('proof_mode','fictional-quota'),('prepared_sha256','c'*64),
                        ('calibration',calibration([3],256))]:
                    fixture.write_text(json.dumps({**settings,key:value}))
                    with self.subTest(key=key),self.assertRaises(proof.ArtifactError):
                        proof.native_settings(Path('/prepared'),receipt)
                fixture.write_text(json.dumps(settings));(path/'execution/orphan').mkdir()
                with self.assertRaises(proof.ArtifactError):proof.native_settings(Path('/prepared'),receipt)


def native_cleanup_check(output):
    """Small real Docker models catch an omitted failed-attempt cleanup stage."""
    if not os.environ.get('JASMINE_BATCH_TEST_DSN') or not os.environ.get('JASMINE_BATCH_DOCKER_IMAGE'):
        raise RuntimeError('Use the disposable database driver with --docker-tests')
    sys.path.insert(0,str(proof.frontend_path()/'tests/batch'))
    from test_docker_worker import DockerWorkerTests,until
    output.mkdir(mode=0o700,parents=True);proof.write_attribution(output)
    report=dict(passed=False,cleanup=False,scientific_workload=False)
    fixture=DockerWorkerTests();fixture.setUp()
    try:
        worker=fixture.worker(bad_output=True)
        with worker.open():
            failed=fixture.submit()
            until(lambda:fixture.done(worker,'alice',failed))
            fixture.assertEqual(fixture.fixture.job('alice',failed)['state'],'review')
            worker.adapter.settings['bad_output']=False
            completed=fixture.submit('bob')
            until(lambda:fixture.done(worker,'bob',completed))
            fixture.assertEqual(fixture.fixture.job('bob',completed)['state'],'succeeded')
            entries={lease.job_id:lease for lease in fixture.leases.values()}
            success=entries[fixture.fixture.job('bob',completed)['id']]
            unsuccessful=entries[fixture.fixture.job('alice',failed)['id']]
            good=fixture.executor.workspace(success)/'work/results.json'
            original=good.read_bytes();source=(fixture.inputs/'marker.txt').read_bytes()
            proof.atomic_json(output/'progress.json',dict(stage='before-retirement'))
            proof.retire_attempts(fixture.q,fixture.executor)
            proof.retire_attempts(fixture.q,fixture.executor)  # Idempotent after restart.
            for lease in entries.values():
                fixture.assertIsNone(fixture.executor.docker.inspect(fixture.executor._name(lease)))
            bad=fixture.executor.workspace(unsuccessful)
            fixture.assertEqual(list((bad/'work').iterdir()),[])
            fixture.assertFalse((bad/'request').exists())
            fixture.assertGreater((bad/'diagnostics.log').stat().st_size,0)
            fixture.assertEqual(good.read_bytes(),original)
            fixture.assertEqual((fixture.inputs/'marker.txt').read_bytes(),source)
            fixture.assertEqual(fixture.fixture.job('alice',failed)['state'],'review')
            fixture.assertEqual(fixture.fixture.job('bob',completed)['state'],'succeeded')
            proof.atomic_json(output/'progress.json',dict(stage='retirement-verified'))
            report.update(passed=True,failed_container_removed=True,successful_container_removed=True,
                private_diagnostics_retained=True,completed_output_and_sources_unchanged=True,
                repeated_retirement_safe=True,progress_replaced=True)
    finally:
        fixture.doCleanups();report['cleanup']=fixture.containers_clean
        proof.write_json(output/'report.json',report)
    if not report['cleanup']:raise AssertionError('Unconfirmed native cleanup-check termination')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true')
    parser.add_argument('--output',type=Path)
    args,remaining=parser.parse_known_args()
    if args.execute_proof:
        if not args.output or remaining:parser.error('Use the trusted disposable proof driver')
        native_cleanup_check(args.output)
        print('PASS: failed/successful containers retire through both production lifecycles; diagnostics and completed output survive',flush=True)
    else:unittest.main(argv=[sys.argv[0],*remaining])
