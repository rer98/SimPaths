"""(C) Copyright 2026, by Ross Richardson

Genuine monitoring-trial staging, mode verification and retained evidence guards.
Uses fictional inputs and counters; no Docker or scientific simulation is run.
@author ross richardson
"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock
from uuid import uuid4
import zipfile

from .artifacts import ArtifactError, fingerprint, inventory
from . import monitoring_cost_trials as trial
from .monitoring_trial_checks import (ScientificAdapter, scientific_plan, check_execution,
    container_launch_record, settled_measurements, scientific_outputs, output_comparison, retain_diagnostics, MIB)
from . import test_research_calibration as calibration_fixture


class ScientificTrialTests(unittest.TestCase):
    def setUp(self):
        self.f = calibration_fixture.ResearchCalibrationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def adapter_lease(self, year=2021):
        self.f.plan = scientific_plan(self.f.receipt, year, population=100000)
        adapter = ScientificAdapter(self.f.prepared, self.f.plan)
        _, lease = self.f.adapter_lease()
        lease.specification['resource_recovery'] = adapter.recovery.describe(
            lease.specification, adapter.resources, adapter.heaps)
        lease.attempt_id, lease.job_id = str(uuid4()), str(uuid4())
        return adapter, lease

    def test_smoke_and_quiet_plans_use_original_seed_and_fixed_limits(self):
        before = inventory(self.f.prepared)
        for year in (2021, 2035):
            adapter, lease = self.adapter_lease(year)
            request = self.f.root/str(year);request.mkdir()
            command = adapter.container_command(lease, request)
            self.assertEqual(command.argv, ('/bin/sh', '/request/run.sh', '4096m'))
            self.assertEqual(command.image, self.f.image)
            self.assertEqual(fingerprint(request/'run.sh'), fingerprint(adapter.runner))
            script = (request/'run.sh').read_text()
            self.assertIn('simpaths.experiment.SimPathsMultiRun', script)
            self.assertIn('-XX:ActiveProcessorCount=2', script)
            for forbidden in ('SimPathsResourceProbe', 'SimPathsYearProfileProbe', 'SimPathsPerformanceRun'):
                self.assertNotIn(forbidden, script)
            self.assertEqual((request/'run.yml').read_text(), adapter._configuration(lease).native_yaml('baseline'))
            self.assertEqual(adapter.arguments['seed_plan'], ['606'])
            self.assertEqual(adapter.contract['resources'], dict(cpu_millis=2000, memory_mib=5120, storage_mib=12288))
            self.assertEqual(adapter.settings['repetitions'], 1)
        self.assertEqual(inventory(self.f.prepared), before)

    def test_private_scientific_adapter_rejects_growth_boosts_and_changed_recovery(self):
        for key in ('cpu_millis', 'memory_mib', 'storage_mib'):
            adapter, lease = self.adapter_lease()
            lease.resources[key] += 1
            with self.subTest(key=key), self.assertRaises(ArtifactError):adapter._configuration(lease)
        adapter, lease = self.adapter_lease()
        lease.current_resources = {**lease.resources, 'cpu_millis':1500}
        with self.assertRaises(ArtifactError):adapter._configuration(lease)
        adapter, lease = self.adapter_lease()
        lease.specification['resource_recovery']['policy']['max_memory_mib'] = 6144
        with self.assertRaises(ArtifactError):adapter._configuration(lease)

    def test_replaced_inputs_receipt_or_runner_prevent_a_later_condition(self):
        adapter, lease = self.adapter_lease()
        (self.f.inputs/'input.mv.db').write_bytes(b'replaced')
        with self.assertRaises(ArtifactError):adapter.container_command(lease, self.f.request)
        self.assertFalse((self.f.request/'run.sh').exists())
        self.f.publish()
        adapter, _ = self.adapter_lease()
        self.f.profile['actual_counts']['person'] += 1;self.f.publish()
        with self.assertRaises(ArtifactError):adapter.verify_sources()
        adapter, _ = self.adapter_lease()
        adapter.runner = self.f.root/'different.sh';adapter.runner.write_text('different')
        with self.assertRaises(ArtifactError):adapter.verify_sources()

    def test_original_population_and_bounded_time_are_required(self):
        for values in (dict(population=20000), dict(job_seconds=959), dict(job_seconds=True), dict(job_seconds=15301)):
            with self.subTest(values=values), self.assertRaises(ArtifactError):
                scientific_plan(self.f.receipt, 2035, **values)

    def test_real_csv_validator_retains_original_seed_hashes_and_rejects_missing_years(self):
        adapter, lease = self.adapter_lease()
        native = adapter._configuration(lease).native_configuration('baseline')
        work = self.f.root/'work';run = work/'output/606'
        (run/'input').mkdir(parents=True);(run/'csv').mkdir()
        fields = dict(country='UK', startYear=2019, endYear=2021, popSize=100000,
            randomSeedIfFixed=606, **native['model_args'])
        from .queue_adapter import OPTIONS_NOT_EXPORTED
        (run/'input/options.txt').write_text('\n'.join(
            f'{k}: {str(v).lower() if isinstance(v, bool) else v}' for k, v in fields.items()
            if k not in OPTIONS_NOT_EXPORTED))
        csv = 'run,time,value\n'+''.join(f'fictional,{year},1\n' for year in range(2019, 2022))
        for name in ('Person.csv', 'BenefitUnit.csv'):(run/'csv'/name).write_text(csv)
        receipts = scientific_outputs(adapter, lease, work)
        self.assertEqual(receipts[0]['seed'], '606')
        self.assertEqual(receipts[0]['fingerprint'], adapter.validate(lease, work)[0]['fingerprint'])
        self.assertEqual({r['name'] for r in receipts[0]['files']}, {'Person.csv', 'BenefitUnit.csv'})
        self.assertNotIn('path', receipts[0]['files'][0])
        (run/'csv/Person.csv').write_text(csv.replace('fictional,2021,1\n', ''))
        with self.assertRaises(ArtifactError):scientific_outputs(adapter, lease, work)

    def test_production_launch_switch_wraps_only_full_and_preserves_the_ordinary_script(self):
        # A tiny fictional included class avoids compilation; the real-JVM
        # observer tests separately execute and check actual observer bytecode.
        with zipfile.ZipFile(self.f.prepared/'model.jar', 'w') as jar:
            jar.writestr('simpaths/experiment/SimPathsPerformanceRun.class', b'fixture')
        self.f.profile['jar_sha256'] = fingerprint(self.f.prepared/'model.jar')['sha256'];self.f.publish()
        from jasmine_web.batch.performance_review import set_control
        from jasmine_web.batch.performance_trace import ReviewTrace
        state = self.f.root/'state';state.mkdir(mode=0o700)
        trace = ReviewTrace(Mock(), state)
        for mode in ('off', 'external', 'full'):
            adapter, lease = self.adapter_lease()
            request = self.f.root/mode/'request';request.mkdir(parents=True)
            ordinary = adapter.container_command(lease, request)
            set_control(state, mode != 'off', sample_seconds=15)
            set_control(state, mode == 'full', java=True, sample_seconds=15)
            selected = trace.prepare_launch(lease, adapter, request, ordinary)
            self.assertEqual(fingerprint(request/'run.sh'), adapter.runner_hash)
            launch = json.loads((request.parent/'performance-launch.json').read_text())
            self.assertEqual(launch['java_mode'], 'enabled' if mode == 'full' else 'off')
            if mode == 'full':
                text = (request/'run-reviewed.sh').read_text()
                self.assertIn('SimPathsPerformanceRun simpaths.experiment.SimPathsMultiRun', text)
                self.assertIn('-Dsimpaths.performance.seconds=15', text)
                self.assertEqual(selected.argv[-1], '4096m')
            else:self.assertEqual(selected, ordinary)

    def test_dry_run_preflights_real_scientific_branch_without_docker(self):
        # The actual web location may be separate from the SimPaths checkout.
        from deploy._workflow import frontend_path
        args = ['--frontend', str(frontend_path()), '--runtime-from', str(self.f.prepared),
            '--scientific', '--population', '100000', '--end-year', '2035', '--rounds', '3',
            '--output', str(self.f.root/'evidence'), '--dry-run']
        captured = io.StringIO()
        with redirect_stdout(captured):self.assertEqual(trial.main(args), 0)
        plan = json.loads(captured.getvalue())
        self.assertEqual(len(plan['order']), 9)
        self.assertEqual([plan['order'][n][1] for n in (0, 3, 6)], ['off', 'external', 'full'])
        self.assertEqual(plan['scientific_contract']['configuration']['common']['end_year'], 2035)
        self.assertFalse((self.f.root/'evidence').exists())


class MonitoringEvidenceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root/'request').mkdir();(self.root/'work').mkdir()
        self.resources = dict(cpu_millis=2000, memory_mib=5120, storage_mib=12288)
        self.image = 'sha256:'+'a'*64
        self.deadline = 1900000000
        self.container = self.production_container('/request/run-reviewed.sh')
        self.launch = dict(java_mode='enabled', sample_seconds=15)
        self.rows = []
        for phase, year in [('setup', 0), ('annual', 2019), ('annual', 2020), ('annual', 2021),
                            ('between_repetitions', 0), ('finished', 0)]:
            n = len(self.rows)
            self.rows.append(dict(sequence=n, elapsed_ms=n*1000, phase=phase, year=year, repetition=1,
                population=100000 if year else None, heap_max_bytes=4096*MIB,
                heap_used_bytes=100*MIB+n, gc_ms=n*2, gc_count=n, process_cpu_ns=n*10**9, dropped=0))
        (self.root/'scientific-configuration.json').write_text(json.dumps(dict(common=dict(start_year=2019, end_year=2021))))

    def production_container(self, runner):
        # Build inspect's command fields from the real Docker argument builder,
        # so this fixture cannot accidentally omit the platform supervisor.
        from jasmine_web.batch.docker_executor import DockerExecutor
        lease = SimpleNamespace(attempt_id=str(uuid4()), configuration_id='baseline',
            specification=dict(model_digest=self.image))
        lease.execution_key = 'batch-'+lease.attempt_id
        executor = DockerExecutor(self.root/'executor', approved_images=[self.image], docker=Mock())
        policy = dict(image=self.image, argv=['/bin/sh', runner, '4096m'], mounts=[],
            resources=self.resources, user='1:1', deadline=self.deadline, pids=512)
        args = executor._create_args(lease, policy)
        return dict(Image=self.image,
            Config=dict(Image=self.image, Entrypoint=[args[args.index('--entrypoint')+1]],
                Cmd=args[args.index(self.image)+1:]),
            HostConfig=dict(NanoCpus=int(float(args[args.index('--cpus')+1])*10**9),
                Memory=int(args[args.index('--memory')+1])), State=dict(ExitCode=0, OOMKilled=False))

    def check(self, mode='full'):
        (self.root/'performance-launch.json').write_text(json.dumps(self.launch))
        log = self.root/'work/.performance-java.jsonl'
        if self.rows:log.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        else:log.unlink(missing_ok=True)
        return check_execution(mode, self.root, self.container, self.resources, self.image, 15,
            scientific=True, deadline=self.deadline)

    def test_production_deadline_wrapper_is_accepted_in_all_scientific_modes(self):
        full_rows = deepcopy(self.rows)
        for mode in ('off', 'external', 'full'):
            self.container = self.production_container('/request/run-reviewed.sh' if mode=='full' else '/request/run.sh')
            self.launch['java_mode'] = 'enabled' if mode=='full' else 'off'
            self.rows = full_rows if mode=='full' else []
            with self.subTest(mode=mode):
                self.assertEqual(self.check(mode)['java_coverage'], 'full' if mode=='full' else 'absent')

    def test_changed_deadline_heap_entrypoint_and_extra_arguments_remain_rejected(self):
        original = deepcopy(self.container)
        for changed in ('deadline', 'heap', 'entrypoint', 'extra', 'missing_wrapper'):
            self.container = deepcopy(original)
            config = self.container['Config']
            if changed=='deadline':config['Cmd'][1] = str(self.deadline+1)
            if changed=='heap':config['Cmd'][-1] = '3072m'
            if changed=='entrypoint':config['Entrypoint'] = ['/bin/bash']
            if changed=='extra':config['Cmd'].append('--unexpected')
            if changed=='missing_wrapper':config['Cmd'] = config['Cmd'][2:]
            with self.subTest(changed=changed), self.assertRaises(AssertionError):self.check()

    def test_saved_container_launch_excludes_environment_and_retains_actual_command(self):
        self.container['Config']['Env'] = ['PRIVATE_TOKEN=must-not-be-exported']
        record = container_launch_record(self.container)
        self.assertEqual(record['command'], self.container['Config']['Cmd'])
        self.assertEqual(record['entrypoint'], ['/bin/sh'])
        self.assertEqual(record['memory_bytes'], 5120*MIB)
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(record))
        self.assertNotIn('must-not-be-exported', json.dumps(record))

    def test_full_records_match_years_population_heap_and_complete_sequence(self):
        found = self.check()
        self.assertEqual(found['java_coverage'], 'full')
        self.assertEqual(found['annual_years'], [2019, 2020, 2021])
        self.assertEqual(found['sampled_peak_population'], 100000)
        self.assertEqual(found['process_cpu_ns_delta'], 5*10**9)
        self.assertEqual(found['gc_ms_delta'], 10)

    def test_silent_fallback_wrong_interval_or_docker_limits_fail(self):
        original = deepcopy(self.container)
        for key, value in (('NanoCpus', 1500*10**6), ('Memory', 6144*MIB)):
            self.container = deepcopy(original);self.container['HostConfig'][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):self.check()
        self.container = original
        self.launch['java_mode'] = 'unsupported'
        with self.assertRaises(AssertionError):self.check()
        self.launch.update(java_mode='enabled', sample_seconds=5)
        with self.assertRaises(AssertionError):self.check()

    def test_gaps_drops_missing_annual_callbacks_or_wrong_heap_fail(self):
        original = deepcopy(self.rows)
        for change in ('gap', 'dropped', 'year', 'heap'):
            self.rows = deepcopy(original)
            if change == 'gap':self.rows[2]['sequence'] += 1
            if change == 'dropped':self.rows[-1]['dropped'] = 1
            if change == 'year':self.rows[2]['phase'] = 'setup'
            if change == 'heap':self.rows[0]['heap_max_bytes'] = 3072*MIB
            with self.subTest(change=change), self.assertRaises(AssertionError):self.check()

    def test_disabled_conditions_have_no_java_side_effects(self):
        self.launch['java_mode'] = 'off'
        self.container = self.production_container('/request/run.sh')
        for mode in ('off', 'external'):
            self.rows = []
            self.assertEqual(self.check(mode)['java_coverage'], 'absent')
            self.rows = [dict(sequence=0, elapsed_ms=0, phase='finished')]
            with self.assertRaises(AssertionError):self.check(mode)

    def test_csv_changes_are_retained_as_provisional_evidence_not_silently_equal(self):
        reference = [dict(seed='606', files=[dict(name='Person.csv', bytes=12, sha256='a'*64)])]
        observed = deepcopy(reference);observed[0]['files'][0]['sha256'] = 'b'*64
        self.assertTrue(output_comparison(reference, reference)['csv_checksums_equal'])
        change = output_comparison(reference, observed)
        self.assertFalse(change['csv_checksums_equal'])
        self.assertEqual(change['changed_files'], [dict(seed='606', name='Person.csv')])

    def test_cleanup_preserves_bounded_logs_and_rejects_symlink_diagnostics(self):
        (self.root/'execution.log').write_bytes(b'start\n'+b'x'*(256*1024)+b'\nfinish')
        (self.root/'request/run.sh').symlink_to(self.root/'execution.log')
        destination = self.root/'evidence'
        retain_diagnostics(destination, self.root)
        self.assertTrue((destination/'execution.log').read_bytes().startswith(b'start'))
        self.assertTrue((destination/'execution.log').read_bytes().endswith(b'finish'))
        self.assertLess((destination/'execution.log').stat().st_size, 129*1024)
        manifest = json.loads((destination/'diagnostics.json').read_text())
        self.assertTrue(manifest['execution.log']['truncated'])
        self.assertIn('error_type', manifest['request/run.sh'])
        self.assertFalse((destination/'request/run.sh').exists())

    def test_settled_checks_require_released_capacity_unchanged_limits_and_real_samples(self):
        lease = SimpleNamespace(attempt_id=str(uuid4()))
        job = dict(attempts=1, history=[dict(id=lease.attempt_id, outcome='success')])
        queue = MagicMock(pool_id='fixture')
        connection = queue._connection.return_value.__enter__.return_value
        rows = [dict(initial_resources=self.resources, current_resources=self.resources, heap_mib=4096),
            dict(released_at='done'), dict(n=0), dict(samples=3, observed_seconds=30,
                sampled_peak_memory_bytes=2*MIB, sampled_peak_working_memory_bytes=MIB)]
        connection.execute.side_effect = [Mock(fetchone=lambda r=r:r) for r in rows]
        measured = settled_measurements(queue, lease, job, 'full', self.resources, scientific=True)
        self.assertEqual(measured['samples'], 3)
        for broken in ('reserved', 'changed', 'no_samples', 'off_samples'):
            changed = deepcopy(rows)
            if broken == 'reserved':changed[1]['released_at'] = None
            if broken == 'changed':changed[2]['n'] = 1
            if broken == 'no_samples':changed[3]['samples'] = 0
            connection.execute.side_effect = [Mock(fetchone=lambda r=r:r) for r in changed]
            with self.subTest(broken=broken), self.assertRaises(AssertionError):
                settled_measurements(queue, lease, job, 'off' if broken=='off_samples' else 'full', self.resources, scientific=True)

    def test_scientific_html_labels_individual_runs_and_does_not_call_them_fictional(self):
        runs = [dict(round=1, mode='off', container_seconds=100, driver_seconds=101)]
        page = trial.render_cost(dict(settings=dict(rounds=3, scientific=True), runs=runs,
            summary=trial.summary(runs), limitations=[]))
        self.assertIn('Repeated scientific runs', page)
        self.assertIn('Observed annual years', page)
        self.assertNotIn('Repeated fictional', page)


if __name__ == '__main__':unittest.main()
