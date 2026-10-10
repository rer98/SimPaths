"""(C) Copyright 2026, by Ross Richardson

Private research profile integrity, frozen configuration and measurement guards.
@author ross richardson
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy.multirun import research_calibration as trial
from deploy.multirun.artifacts import ArtifactError, digest, fingerprint, inventory, write_json
from deploy.multirun.configuration import normalise
from deploy.multirun.queue_adapter import read_prepared, OPTIONS_NOT_EXPORTED
from deploy.multirun.resource_policy import DEFAULT_POLICY


def settings(**changes):
    return trial.checked_settings({**dict(population=100000, end_year=2070, repetitions=1,
        heap_mib=4096, memory_mib=5120, max_memory_mib=7168, storage_mib=12288,
        setup_seconds=900, repetition_seconds=14400, max_attempts=3), **changes})


class ResearchCalibrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prepared = self.root/'prepared'; self.prepared.mkdir()
        self.inputs = self.prepared/'input'; self.inputs.mkdir()
        for name in ('input.mv.db', 'DatabaseCountryYear.xlsx', 'EUROMODpolicySchedule.xlsx'):
            (self.inputs/name).write_bytes(b'private fictional prepared bytes')
        (self.prepared/'model.jar').write_bytes(b'fictional pinned JAR')
        self.profile = dict(format_version=1, profile_id='private-research-uk-2019-100000-seed606',
            profile=trial.load_tool('web-quickstart/prepare_profile.py').profile_for(100000, research_calibration=True),
            actual_counts=dict(person=100002, household=43000, benefitunit=49000),
            calibration_only=True, fresh_jvm_loading_verified=True, simulated_years_run=0,
            jar_sha256=fingerprint(self.prepared/'model.jar')['sha256'])
        self.image = 'sha256:'+'b'*64
        self.publish()
        self.plan = settings()
        self.classes = self.root/'classes'; self.classes.mkdir()
        (self.classes/'SimPathsResourceProbe.class').write_bytes(b'fictional test wrapper')
        self.request = self.root/'request'; self.request.mkdir()

    def publish(self):
        (self.prepared/'profile.json').write_text(json.dumps(self.profile))
        identity = dict(format=trial.FORMAT, source=trial.SOURCE, country='UK', start_year=2019,
            population=100000, max_end_year=2070, source_image=self.image,
            model=fingerprint(self.prepared/'model.jar'), prepared=inventory(self.inputs),
            profile=fingerprint(self.prepared/'profile.json'))
        self.receipt = dict(identity=identity, sha256=digest(identity), revision='calibration-'+digest(identity))
        (self.prepared/'receipt.json').write_text(json.dumps(self.receipt))

    def adapter_lease(self, variant='baseline'):
        adapter = trial.ResearchAdapter(self.prepared, self.classes, self.plan, variant)
        config = trial.configuration(self.receipt, self.plan, variant)
        args = trial.arguments(config, self.receipt)
        spec = dict(model_digest=self.image, dataset_id=self.receipt['revision'],
            prepared_fingerprint=self.receipt['sha256'], seeds=args['seed_plan'], run_sets=args['run_sets'])
        resources = dict(cpu_millis=2000, memory_mib=5120, storage_mib=12288)
        from jasmine_web.batch.resource_recovery import RecoveryPolicy
        key = config.as_dict()['run_sets'][0]['id']
        spec['resource_recovery'] = RecoveryPolicy(max_memory_mib=7168, max_heap_mib=6144,
            storage_multiplier=1, check_seconds=5).describe(spec, resources, {key:4096})
        lease = SimpleNamespace(specification=spec, configuration_id=key, resources=resources, heap_mib=4096)
        return adapter, lease

    def test_alternative_reuses_inputs_and_seed_and_changes_only_the_declared_parameter(self):
        baseline = trial.configuration(self.receipt, self.plan)
        alternative = trial.configuration(self.receipt, self.plan, 'lower-saving-rate')
        first, second = baseline.as_dict(), alternative.as_dict()
        for key in ('common', 'seed_plan', 'dataset_revision', 'model_release'):
            self.assertEqual(first[key], second[key])
        before = first['run_sets'][0]; after = second['run_sets'][0]
        self.assertEqual(after['id'], 'alternative')
        self.assertEqual(before['collector_args'], after['collector_args'])
        self.assertEqual({k:v for k,v in before['model_args'].items() if k != 'savingRate'},
                         {k:v for k,v in after['model_args'].items() if k != 'savingRate'})
        self.assertEqual((before['model_args']['savingRate'], after['model_args']['savingRate']), (.056, .04))
        self.assertEqual(alternative.native_configuration('alternative')['model_args']['savingRate'], .04)
        with self.assertRaises(ArtifactError):trial.configuration(self.receipt, self.plan, 'unrecorded-policy')

    def test_alternative_adapter_freezes_its_selected_variant(self):
        adapter, lease = self.adapter_lease('lower-saving-rate')
        self.assertEqual(adapter._configuration(lease).as_dict()['run_sets'][0]['id'], 'alternative')
        changed = trial.configuration(self.receipt, self.plan).editable_configuration()
        changed['run_sets'][0].update(id='alternative', name='Lower saving rate (0.04)')
        lease.specification['run_sets'][0]['parameters'] = changed
        with self.assertRaises(ArtifactError):adapter._configuration(lease)

    def test_private_receipt_accepts_actual_counts_but_never_enters_service(self):
        self.assertEqual(trial.read_calibration(self.prepared), self.receipt)
        with self.assertRaises(ArtifactError):read_prepared(self.prepared)
        profile_tool = trial.load_tool('web-quickstart/prepare_profile.py')
        with self.assertRaises(ValueError):profile_tool.profile_for(100000)
        self.assertEqual(profile_tool.profile_for(50000)['requested_population'], 50000)

    def test_large_long_configuration_uses_defaults_and_original_regular_seed_sequence(self):
        for repetitions in (1, 10):
            plan = {**self.plan, 'repetitions':repetitions, 'max_attempts':1}
            config = trial.configuration(self.receipt, plan)
            data = config.as_dict()
            self.assertEqual(data['common'], dict(country='UK', start_year=2019, end_year=2070, population=100000))
            self.assertEqual(data['seed_plan']['seeds'], [str(n) for n in range(606, 606+repetitions)])
            self.assertEqual(data['run_sets'][0]['model_args'], normalise({
                **config.editable_configuration(), 'run_sets':[dict(id='baseline', name='Baseline')]
            }).as_dict()['run_sets'][0]['model_args'])
            native = config.native_configuration('baseline')
            self.assertEqual((native['popSize'], native['endYear'], native['maxNumberOfRuns']), (100000, 2070, repetitions))
            self.assertTrue(native['innovation_args']['randomSeedInnov'])
            self.assertFalse(native['integrationTest'])

    def test_prepared_population_or_calibration_envelope_cannot_be_changed(self):
        with self.assertRaises(ArtifactError):trial.configuration(self.receipt, {**self.plan, 'population':50000})
        config = trial.configuration(self.receipt, self.plan).editable_configuration()
        for changes in (dict(end_year=2071), dict(start_year=2020), dict(population=50000)):
            changed = deepcopy(config); changed['common'].update(changes)
            with self.assertRaises(ArtifactError):trial.arguments(normalise(changed), self.receipt)
        for key in ('useWeights', 'ignoreTargetsAtPopulationLoad'):
            changed = deepcopy(config); changed['run_sets'][0]['model_args'][key] = True
            with self.assertRaises(ArtifactError):trial.arguments(normalise(changed), self.receipt)

    def test_changed_image_inputs_seeds_native_settings_and_resources_are_rejected(self):
        for key, value in (('model_digest', 'sha256:'+'c'*64), ('prepared_fingerprint', 'c'*64),
                ('dataset_id', 'another-dataset'), ('seeds', ['607'])):
            adapter, lease = self.adapter_lease(); lease.specification[key] = value
            with self.subTest(key=key), self.assertRaises(ArtifactError):adapter._configuration(lease)
        adapter, lease = self.adapter_lease()
        lease.specification['run_sets'][0]['parameters']['run_sets'][0]['model_args']['savingRate'] = 0.123
        with self.assertRaises(ArtifactError):adapter._configuration(lease)
        for key in ('cpu_millis', 'memory_mib', 'storage_mib'):
            adapter, lease = self.adapter_lease(); lease.resources[key] -= 1
            with self.subTest(key=key), self.assertRaises(ArtifactError):adapter._configuration(lease)

    def test_hashes_and_fresh_population_reuse_evidence_are_required(self):
        self.profile['fresh_jvm_loading_verified'] = False; self.publish()
        with self.assertRaises(ArtifactError):trial.read_calibration(self.prepared)
        self.profile['fresh_jvm_loading_verified'] = True
        self.profile['calibration_only'] = False; self.publish()
        with self.assertRaises(ArtifactError):trial.read_calibration(self.prepared)
        self.profile['calibration_only'] = True
        self.profile['actual_counts']['person'] = 0; self.publish()
        with self.assertRaises(ArtifactError):trial.read_calibration(self.prepared)
        self.profile['actual_counts']['person'] = 100002; self.publish()
        (self.prepared/'profile.json').write_text('{}')
        with self.assertRaises(ArtifactError):trial.read_calibration(self.prepared)

    def test_staging_reuses_production_script_and_full_space_guard_without_changing_model(self):
        adapter, lease = self.adapter_lease()
        before = inventory(self.prepared)
        with patch('deploy.multirun.queue_adapter.shutil.disk_usage', return_value=SimpleNamespace(free=100*trial.GIB)):
            command = adapter.container_command(lease, self.request)
        self.assertEqual(command.inputs, str(self.prepared))
        self.assertEqual(command.image, self.image)
        self.assertEqual(command.argv, ('/bin/sh', '/request/run.sh', '4096m'))
        self.assertEqual((self.request/'run.yml').read_text(), trial.configuration(self.receipt, self.plan).native_yaml('baseline'))
        script = (self.request/'run.sh').read_text()
        self.assertIn('-Dsimpaths.resource.sample.millis=16000', script)
        self.assertIn('-Djasmine.memory.monitor.enabled=true', script)
        self.assertIn('SimPathsResourceProbe', script)
        self.assertIn('sha256sum --check --status', script)
        self.assertEqual(inventory(self.prepared), before)
        self.assertEqual(adapter.required_space(lease), 12*trial.GIB)
        (self.inputs/'input.mv.db').write_text('changed')
        with self.assertRaises(ArtifactError):adapter.container_command(lease, self.request)

    def test_missing_space_is_rejected_before_a_runner_is_written(self):
        from jasmine_web.batch.docker_executor import InsufficientWorkspaceSpace
        adapter, lease = self.adapter_lease()
        with patch('deploy.multirun.queue_adapter.shutil.disk_usage', return_value=SimpleNamespace(free=11*trial.GIB)):
            with self.assertRaises(InsufficientWorkspaceSpace):adapter.container_command(lease, self.request)
        self.assertFalse((self.request/'run.sh').exists())

    def test_trial_policy_does_not_mutate_production_defaults(self):
        before = deepcopy(DEFAULT_POLICY)
        policy = trial.trial_policy(self.plan)
        self.assertEqual(policy['simulation']['storage'], dict(setup_mib=12288, per_repetition_mib=0))
        self.assertEqual(DEFAULT_POLICY, before)
        self.assertEqual(DEFAULT_POLICY['simulation']['storage'], dict(setup_mib=4096, per_repetition_mib=256))

    def test_invalid_settings_and_unfinishable_long_retry_plan_are_rejected(self):
        for key, value in (('population', True), ('population', 100001), ('end_year', 2071),
                ('repetitions', 11), ('heap_mib', 5120), ('max_memory_mib', 4096),
                ('storage_mib', 6143), ('max_attempts', 4)):
            with self.subTest(key=key), self.assertRaises(ArtifactError):trial.checked_settings({**self.plan, key:value})
        with self.assertRaises(ArtifactError):trial.checked_settings({**self.plan, 'repetitions':10})
        self.assertLessEqual(trial.proof_timeout({**self.plan, 'repetitions':10, 'max_attempts':1}), 172800)
        with self.assertRaises(ArtifactError):trial.checked_settings({**self.plan, 'extra':1})

    def test_preflight_checks_the_actual_filesystem_and_model_ceiling(self):
        memory = 'MemAvailable: 10000000 kB\n'
        with patch.object(trial.Path, 'read_text', return_value=memory), patch.object(trial.shutil, 'disk_usage',
                return_value=SimpleNamespace(free=15*trial.GIB)) as space:
            result = trial.preflight(self.plan, self.root/'nonexistent', preparing=True)
            self.assertEqual(result['required_available_memory_bytes'], 9*trial.GIB)
            self.assertEqual(result['required_free_disk_bytes'], 15*trial.GIB)
            space.assert_called_once_with(self.root)
        with patch.object(trial.Path, 'read_text', return_value='MemAvailable: 9000000 kB\n'):
            with self.assertRaises(ArtifactError):trial.preflight(self.plan, self.root)
        with patch.object(trial.Path, 'read_text', return_value=memory), patch.object(trial.shutil, 'disk_usage',
                return_value=SimpleNamespace(free=14*trial.GIB)):
            with self.assertRaises(ArtifactError):trial.preflight(self.plan, self.root)

    def test_measured_build_and_lifetime_keep_their_distinct_meanings(self):
        result = trial.measured_times('Time to complete initialisation 1.5 minutes.\n',
            dict(State=dict(StartedAt='2026-10-09T10:00:00.123456789Z', FinishedAt='2026-10-09T12:00:00.123456789Z')))
        self.assertEqual(result['model_initialisation_seconds'], [90])
        self.assertEqual(result['container_lifetime_seconds'], 7200)
        with self.assertRaises(ArtifactError):trial.measured_times('no timer',
            dict(State=dict(StartedAt='2026-10-09T10:00:00Z', FinishedAt='2026-10-09T12:00:00Z')))

    def test_database_history_and_recovery_times_survive_saved_reports(self):
        created = datetime(2026, 10, 9, 10, 11, 12, 123456, tzinfo=timezone.utc)
        record = dict(settings=self.plan, passed=False,
            history=[dict(created_at=created, finished_at=None, outcome='heap_limit')],
            resource_events=[dict(created_at=created, action='retry', allocation=dict(memory_mib=6144))])
        saved = trial.json_record(record)
        write_json(self.root/'report.json', saved)
        trial.atomic_json(self.root/'progress.json', saved)
        for name in ('report.json', 'progress.json'):
            value = json.loads((self.root/name).read_text())
            self.assertEqual(value['history'][0]['created_at'], '2026-10-09T10:11:12.123456+00:00')
            self.assertIsNone(value['history'][0]['finished_at'])
            self.assertEqual(value['resource_events'][0]['created_at'], value['history'][0]['created_at'])
            self.assertEqual(value['settings'], self.plan)
        self.assertIsInstance(record['history'][0]['created_at'], datetime)
        with self.assertRaises(TypeError):trial.json_record(dict(unsupported=object()))

    def test_log_rotation_and_boundary_replay_keep_all_samples_and_identical_build_times(self):
        from deploy.multirun.test_resource_recovery_proof import row
        entry = {}
        first = '2026-10-09T10:00:00.000000000Z '+trial.PREFIX+json.dumps(row())
        build = '2026-10-09T10:00:01.000000000Z Time to complete initialisation 1.5 minutes.'
        trial.collect_observations(entry, first+'\n'+build)
        second = '2026-10-09T10:00:02.000000000Z '+trial.PREFIX+json.dumps(row(sequence=1, elapsed_ms=2000))
        trial.collect_observations(entry, build+'\n'+second)
        another_build = '2026-10-09T11:00:01.000000000Z Time to complete initialisation 1.5 minutes.'
        trial.collect_observations(entry, second+'\n'+another_build)
        self.assertEqual([r['sequence'] for r in entry['rows']], [0, 1])
        self.assertEqual(entry['builds'], [90, 90])
        self.assertEqual(entry['cursor'], '2026-10-09T11:00:01.000000000Z')
        # The CLI's stderr buffer can follow later stdout in the returned text.
        next_sample = '2026-10-09T11:00:03.000000000Z '+trial.PREFIX+json.dumps(row(sequence=2, elapsed_ms=4000))
        warning = '2026-10-09T11:00:02.000000000Z native warning on stderr'
        trial.collect_observations(entry, next_sample+'\n'+warning)
        self.assertEqual(entry['rows'][-1]['sequence'], 2)
        trial.collect_observations(entry, '2026-10-09T11:00:04.000000000Z')
        self.assertEqual(len(entry['rows']), 3)

    def test_missing_rotated_samples_and_malformed_timestamps_are_rejected(self):
        from deploy.multirun.test_resource_recovery_proof import row
        entry = {}
        trial.collect_observations(entry, '2026-10-09T10:00:00.000000000Z '+trial.PREFIX+json.dumps(row()))
        with self.assertRaises(ValueError):trial.collect_observations(entry,
            '2026-10-09T10:00:03.000000000Z '+trial.PREFIX+json.dumps(row(sequence=2, elapsed_ms=3000)))
        for line in ('no timestamp', '2026-10-09T09:00:00.000000000Z backward'):
            with self.subTest(line=line), self.assertRaises(ArtifactError):trial.collect_observations(entry, line)

    def test_private_browser_model_rejects_user_submissions(self):
        model = trial.status_model(self.prepared, self.receipt, self.plan)
        description = model.describe_dataset(dict(dataset_id=self.receipt['revision'], prepared_fingerprint=self.receipt['sha256']))
        self.assertEqual(description['values']['population'], 100000)
        with self.assertRaises(ArtifactError):model.browser_configuration({}, {})
        with self.assertRaises(ArtifactError):model.describe_dataset(dict(dataset_id='other', prepared_fingerprint=self.receipt['sha256']))
        imported=trial.status_model(self.prepared,self.receipt,self.plan,dataset_id='provider-id')
        self.assertEqual(imported.describe_dataset(dict(dataset_id='provider-id',
            prepared_fingerprint=self.receipt['sha256']))['id'],'provider-id')
        with self.assertRaises(ArtifactError):imported.describe_dataset(dict(dataset_id=self.receipt['revision'],
            prepared_fingerprint=self.receipt['sha256']))

    def test_private_preparation_forwards_the_opt_in_and_public_model_jar(self):
        from contextlib import redirect_stdout
        from io import StringIO
        tool = trial.load_tool('web-quickstart/prepare_profile.py')
        args = SimpleNamespace(repo=trial.ROOT, output=self.root/'new-preparation', population=100000,
            timeout=7200, dry_run=True, research_calibration=True)
        output = StringIO()
        with redirect_stdout(output):tool.prepare(args)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan['jar'], str(trial.ROOT/'multirun.jar'))
        self.assertEqual(plan['profile']['requested_population'], 100000)
        self.assertIn('InitialPopulations/training/population_initial_UK_2019.csv', plan['inputs'])
        self.assertFalse((self.root/'new-preparation').exists())
        args.research_calibration = False
        with self.assertRaises(ValueError):tool.prepare(args)

    def test_actual_output_helper_requires_all_52_years_and_original_native_settings(self):
        adapter, lease = self.adapter_lease()
        config = trial.configuration(self.receipt, self.plan)
        native = config.native_configuration('baseline')
        work = self.root/'work'; run = work/'output/606'
        (run/'input').mkdir(parents=True); (run/'csv').mkdir()
        fields = dict(country='UK', startYear=2019, endYear=2070, popSize=100000,
            randomSeedIfFixed=606, **native['model_args'])
        option = run/'input/options.txt'
        option.write_text('\n'.join(f'{key}: {str(value).lower() if isinstance(value, bool) else value}'
            for key, value in fields.items() if key not in OPTIONS_NOT_EXPORTED))
        complete = 'run,time,value\n'+''.join(f'fictional, {year},1\n'.replace(', ', ',') for year in range(2019, 2071))
        for name in ('Person.csv', 'BenefitUnit.csv'):(run/'csv'/name).write_text(complete)
        receipts = adapter.validate(lease, work)
        self.assertEqual([r['seed'] for r in receipts], ['606'])
        (run/'csv/Person.csv').write_text(complete.replace('fictional,2070,1\n', ''))
        with self.assertRaises(ArtifactError):adapter.validate(lease, work)
        (run/'csv/Person.csv').write_text(complete)
        option.write_text(option.read_text().replace('popSize: 100000', 'popSize: 50000'))
        with self.assertRaises(ArtifactError):adapter.validate(lease, work)

    def test_failed_scratch_is_reclaimed_before_retry_without_losing_measurements(self):
        from contextlib import nullcontext
        attempt = 'fictional-failed-attempt'; lease = SimpleNamespace(attempt_id=attempt)
        source = self.root/'execution'/attempt; source.mkdir(parents=True)
        (source/'execution.log').write_text('original bounded diagnostic')
        connection = Mock(); connection.execute.return_value.fetchall.return_value = [dict(id=attempt)]
        queue = Mock(); queue._connection.return_value = nullcontext(connection)
        executor = Mock(); executor.workspace.return_value = source; executor.docker.inspect.return_value = None
        entries = {attempt:dict(lease=lease, container_id='c'*64, pids=[123], rows=[dict(sequence=0)])}
        def reclaim():
            self.assertEqual(json.loads((self.root/'evidence/attempts'/attempt/'telemetry.json').read_text()), [dict(sequence=0)])
            (source/'execution.log').unlink()
        with patch.object(trial, 'observe'), patch('jasmine_web.batch.attempt_cleanup.AttemptCleanup') as cleanup:
            cleanup.return_value.retire.side_effect = reclaim
            trial.retire_failed(queue, executor, {attempt:lease}, entries, {}, self.root/'evidence')
            self.assertTrue(entries[attempt]['retired'])
            self.assertEqual((self.root/'evidence/attempts'/attempt/'execution.log').read_text(), 'original bounded diagnostic')
            trial.retire_failed(queue, executor, {attempt:lease}, entries, {}, self.root/'evidence')
            cleanup.return_value.retire.assert_called_once()

    def stopped_executor(self):
        from jasmine_web.batch.docker_executor import DockerExecutor
        adapter, lease = self.adapter_lease()
        lease.attempt_id = '00000000-0000-0000-0000-000000000001'
        lease.execution_key = 'batch-'+lease.attempt_id
        docker = Mock(); docker.inspect.return_value = None
        executor = DockerExecutor(self.root/'executor', approved_images=[self.image], docker=docker)
        path = executor.workspace(lease); path.mkdir(mode=0o700)
        files = {'identity.json':executor._identity(lease), 'create-intent.json':{},
            'container-policy.json':dict(image=self.image), 'container.json':dict(id='a'*64), 'removed.json':dict(id='a'*64),
            'exit.json':dict(state='stopped', outcome='success', returncode=0, container_id='a'*64)}
        for name, value in files.items():write_json(path/name, value)
        return executor, lease, docker

    def test_final_cleanup_reacquires_real_guard_after_worker_exit_and_preserves_success(self):
        from jasmine_web.batch.policy import Conflict
        executor, lease, docker = self.stopped_executor()
        path = executor.workspace(lease)
        original = (path/'exit.json').read_bytes()
        with executor.exclusive():self.assertEqual(executor.inspect(lease)['state'], 'stopped')
        with self.assertRaises(Conflict):executor.inspect(lease)
        self.assertEqual(trial.settle_attempts(executor, [lease]), [])
        self.assertIsNone(executor._guard)
        self.assertEqual((path/'exit.json').read_bytes(), original)
        self.assertFalse((path/'stop.json').exists())
        docker.call.assert_not_called()

    def test_uncertain_removal_keeps_attempt_and_records_cleanup_reason(self):
        executor, lease, docker = self.stopped_executor()
        path = executor.workspace(lease); (path/'removed.json').unlink()
        failures = trial.settle_attempts(executor, [lease])
        self.assertEqual(failures[0]['attempt'], lease.attempt_id)
        self.assertIn('termination is unconfirmed', failures[0]['message'])
        self.assertTrue(path.exists())
        self.assertFalse((path/'removed.json').exists())
        self.assertIsNone(executor._guard)
        docker.call.assert_not_called()

    def test_competing_dispatcher_blocks_cleanup_without_touching_receipts(self):
        from jasmine_web.batch.docker_executor import DockerExecutor
        executor, lease, docker = self.stopped_executor()
        other = DockerExecutor(executor.root, approved_images=[self.image], docker=docker)
        before = inventory(executor.workspace(lease))
        with other.exclusive():
            failures = trial.settle_attempts(executor, [lease])
        self.assertEqual(failures[0]['stage'], 'dispatcher-lock')
        self.assertEqual(failures[0]['error_type'], 'Conflict')
        self.assertEqual(inventory(executor.workspace(lease)), before)
        docker.inspect.assert_not_called()

    def retained_fixture(self):
        from deploy.multirun.queue_adapter import validate_outputs
        executor, lease, docker = self.stopped_executor()
        docker.call.return_value = 'fictional-daemon'
        folder = self.root/'completed-evidence'; folder.mkdir(mode=0o700)
        work = folder/'simpaths-research-fictional'; work.mkdir(mode=0o700)
        executor.root.rename(work/'execution')
        proof = folder/'model-proof'; proof.mkdir(mode=0o700)
        retained = proof/'retained-output'; run = retained/'606'
        (run/'input').mkdir(parents=True); (run/'csv').mkdir()
        config = trial.configuration(self.receipt, self.plan)
        native = config.native_configuration('baseline')
        fields = dict(country='UK', startYear=2019, endYear=2070, popSize=100000,
            randomSeedIfFixed=606, **native['model_args'])
        (run/'input/options.txt').write_text('\n'.join(
            f'{key}: {str(value).lower() if isinstance(value, bool) else value}'
            for key, value in fields.items() if key not in OPTIONS_NOT_EXPORTED))
        rows = 'run,time,value\n'+''.join(f'fictional,{year},1\n' for year in range(2019, 2071))
        for name in ('Person.csv', 'BenefitUnit.csv'):(run/'csv'/name).write_text(rows)
        manifest = inventory(retained)
        record = dict(passed=False, cleanup=False, source_unchanged=True, input_copies_reclaimed=True,
            reservations_released=True, settings=self.plan, model=self.receipt['identity']['model'],
            prepared_sha256=self.receipt['sha256'], image=self.image, retained_workspace=str(work),
            output_location=str(retained), output_bytes=sum(v['bytes'] for v in manifest.values()),
            verified_repetitions=validate_outputs(retained, config, 'baseline'),
            attempts=[dict(id=lease.attempt_id, outcome='success')])
        write_json(proof/'report.json', record); write_json(proof/'configuration.json', config.as_dict())
        write_json(proof/'output-manifest.json', manifest); write_json(folder/'report.json', dict(cleanup=True, passed=False))
        return folder, proof, work, docker, record

    def test_cleanup_only_verifies_actual_csvs_and_preserves_original_reports_and_output(self):
        from contextlib import redirect_stdout
        from io import StringIO
        folder, proof, work, docker, record = self.retained_fixture()
        original_report = (proof/'report.json').read_bytes()
        before = inventory(proof/'retained-output')
        with redirect_stdout(StringIO()):
            result = trial.finish_cleanup(folder, self.prepared, docker=docker)
        self.assertTrue(result['passed']); self.assertTrue(result['cleanup'])
        self.assertFalse(result['simulation_repeated']); self.assertFalse(work.exists())
        self.assertEqual(result['annual_years'], list(range(2019, 2071)))
        self.assertEqual((proof/'report.json').read_bytes(), original_report)
        self.assertFalse(json.loads((folder/'report.json').read_text())['passed'])
        self.assertEqual(inventory(proof/'retained-output'), before)
        self.assertTrue((proof/'cleanup-verification.json').is_file())
        self.assertIn(record['attempts'][0]['id']+'/removed.json', result['journals'])
        docker.call.assert_called_once_with('info', '--format', '{{.ID}}')

    def test_cleanup_only_rejects_unsettled_completion_before_docker_or_file_removal(self):
        folder, proof, work, docker, record = self.retained_fixture()
        record['reservations_released'] = False; trial.atomic_json(proof/'report.json', record)
        with self.assertRaises(ArtifactError):trial.finish_cleanup(folder, self.prepared, docker=docker)
        self.assertTrue(work.exists()); docker.call.assert_not_called()
        self.assertFalse((proof/'cleanup-verification.json').exists())

    def test_cleanup_only_rejects_a_foreign_workspace(self):
        folder, proof, work, docker, record = self.retained_fixture()
        record['retained_workspace'] = str(self.root/'simpaths-research-foreign')
        trial.atomic_json(proof/'report.json', record)
        with self.assertRaises(ArtifactError):trial.finish_cleanup(folder, self.prepared, docker=docker)
        self.assertTrue(work.exists()); docker.call.assert_not_called()

    def test_cleanup_only_keeps_scratch_if_the_original_container_still_exists(self):
        folder, proof, work, docker, record = self.retained_fixture()
        docker.inspect.return_value = dict(State=dict(Running=True))
        with self.assertRaises(ArtifactError):trial.finish_cleanup(folder, self.prepared, docker=docker)
        self.assertTrue(work.exists()); self.assertFalse((proof/'cleanup-verification.json').exists())
        docker.call.assert_called_once_with('info', '--format', '{{.ID}}')

    def test_cleanup_only_keeps_scratch_and_reports_if_retained_output_changed(self):
        folder, proof, work, docker, record = self.retained_fixture()
        (proof/'retained-output/606/csv/Person.csv').write_text('changed')
        with self.assertRaises(ArtifactError):trial.finish_cleanup(folder, self.prepared, docker=docker)
        self.assertTrue(work.exists()); self.assertFalse((proof/'cleanup-verification.json').exists())
        self.assertFalse(json.loads((proof/'report.json').read_text())['passed'])


if __name__ == '__main__':unittest.main()
