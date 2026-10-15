"""(C) Copyright 2026, by Ross Richardson

Verify fixed-budget Docker isolation, fixture provenance, cleanup and admission guards.
No models, Docker daemon or full-size aggregation run in these tests.
@author ross richardson
"""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy.multirun import visualiser_capacity as trial


class CapacityDriverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root/'fixture'; self.fixture.mkdir()
        for name in ('baseline.json', 'alternative.json'):
            (self.fixture/name).write_text('[]')
        self.record = dict(format='simpaths.visualiser.capacity-fixture.v1', mode='summary-replay',
            annual_years=list(range(2019, 2071)), seeds=['900000001'],
            configurations=[dict(id='baseline' if i == 0 else 'alternative1',
                name='Fixture', role='baseline' if i == 0 else 'scenario_1',
                runs=[dict(seed='900000001', summary=name, source_seed='606')])
                for i, name in enumerate(('baseline.json', 'alternative.json'))],
            files={name:trial.fingerprint(self.fixture/name) for name in ('baseline.json', 'alternative.json')},
            backend=dict(calculation_sha256='a'*64, runner_sha256='b'*64), limitations=['Capacity only.'])
        self.save()

    def save(self):
        (self.fixture/'manifest.json').write_text(json.dumps(self.record))

    def test_fixture_inventory_seeds_and_content_must_match(self):
        self.assertEqual(trial.check_fixture(self.fixture)[0], self.record)
        (self.fixture/'baseline.json').write_text('[1]')
        with self.assertRaisesRegex(ValueError, 'changed'):
            trial.check_fixture(self.fixture)
        (self.fixture/'baseline.json').write_text('[]')
        self.record['configurations'][1]['runs'][0]['seed'] = '2'; self.save()
        with self.assertRaisesRegex(ValueError, 'matching'):
            trial.check_fixture(self.fixture)

    def test_paths_extra_sources_and_links_fail_closed(self):
        for name in ('../private', '/etc/passwd', 'x//y', 'x/./y'):
            with self.subTest(name=name), self.assertRaises(ValueError): trial.safe_name(name)
        self.record['files']['unselected'] = dict(bytes=0, sha256='a'*64); self.save()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            trial.check_fixture(self.fixture)
        self.record['files'].pop('unselected'); self.save()
        (self.fixture/'baseline.json').unlink()
        (self.fixture/'baseline.json').symlink_to(self.fixture/'alternative.json')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            trial.check_fixture(self.fixture)

    def test_docker_budget_covers_all_descendants_and_never_mounts_socket(self):
        options = trial.container_options('sha256:'+'a'*64, self.root/'snapshot',
            self.fixture, None, self.root/'out', 3072, 'test')
        self.assertEqual(options['nano_cpus'], 10**9)
        self.assertEqual(options['mem_limit'], 3*trial.GIB)
        self.assertEqual(options['memswap_limit'], options['mem_limit'])
        self.assertTrue(options['read_only'])
        self.assertEqual(options['network_mode'], 'none')
        self.assertEqual(options['cap_drop'], ['ALL'])
        self.assertIn('no-new-privileges:true', options['security_opt'])
        self.assertEqual([v['bind'] for v in options['volumes'].values() if v['mode']=='rw'], ['/out'])
        self.assertFalse(any('docker.sock' in value for value in options['volumes']))
        self.assertEqual(options['user'], f'{os.getuid()}:{os.getgid()}')

    def test_raw_mount_is_read_only_and_additional_to_tools(self):
        options = trial.container_options('image', self.root/'snapshot', self.fixture,
            self.root/'raw', self.root/'out', 2048, 'test')
        self.assertEqual(options['volumes'][str(self.root/'raw')], {'bind':'/raw', 'mode':'ro'})

    def test_admission_checks_total_ram_for_two_workers_and_disk(self):
        with patch.object(trial, 'available_memory', return_value=9*trial.GIB), \
             patch.object(trial.shutil, 'disk_usage', return_value=SimpleNamespace(free=20*trial.GIB)):
            with self.assertRaisesRegex(ValueError, 'RAM'):
                trial.preflight_space(self.root, 4096, 2)
            self.assertEqual(trial.preflight_space(self.root, 3072, 2)['available_memory_bytes'], 9*trial.GIB)
        with patch.object(trial, 'available_memory', return_value=16*trial.GIB), \
             patch.object(trial.shutil, 'disk_usage', return_value=SimpleNamespace(free=7*trial.GIB)):
            with self.assertRaisesRegex(ValueError, '8 GiB'):
                trial.preflight_space(self.root, 3072, 2)

    def test_existing_benchmark_lock_prevents_overlap(self):
        with trial.exclusive(self.root):
            with self.assertRaises(BlockingIOError):
                with trial.exclusive(self.root): pass

    def fake_container(self, identity='identity', running=True):
        container = Mock()
        container.id = 'fictional-container'
        container.attrs = {'Config':{'Labels':{trial.LABEL:identity}},
                           'State':{'Running':running, 'ExitCode':0, 'OOMKilled':False}}
        container.logs.return_value = b'fictional private log'
        return container

    def test_cleanup_only_kills_owned_container_and_reaps_before_removal(self):
        container = self.fake_container()
        trial.cleanup_container(container, 'identity')
        names = [call[0] for call in container.mock_calls]
        self.assertLess(names.index('kill'), names.index('wait'))
        self.assertLess(names.index('wait'), names.index('remove'))
        foreign = self.fake_container('other')
        with self.assertRaisesRegex(RuntimeError, 'another identity'):
            trial.cleanup_container(foreign, 'identity')
        foreign.kill.assert_not_called(); foreign.remove.assert_not_called()

    def test_partial_start_failure_still_removes_every_created_container(self):
        client = Mock(); containers = []
        def create(**options):
            c = self.fake_container(options['labels'][trial.LABEL], running=False)
            containers.append(c)
            if len(containers) == 2: c.start.side_effect = RuntimeError('fictional start failure')
            return c
        client.containers.create.side_effect = create
        args = SimpleNamespace(image_id='image', timeout_seconds=60)
        with patch.object(trial, 'preflight_space', return_value={}):
            report = trial.run_case(client, args, self.root/'snapshot', self.fixture,
                self.record, 3072, 2, self.root/'case')
        self.assertFalse(report['passed']); self.assertTrue(report['cleanup'])
        for c in containers: c.remove.assert_called_once()
        self.assertEqual(report['error_type'], 'RuntimeError')
        self.assertTrue((self.root/'case/worker-1/container.log').is_file())

    def test_oom_is_not_a_pass_even_if_worker_report_says_passed(self):
        client = Mock()
        def create(**options):
            target = Path(next(k for k,v in options['volumes'].items() if v['bind']=='/out'))
            (target/'report.json').write_text('{"passed":true}')
            c = self.fake_container(options['labels'][trial.LABEL], running=False)
            c.attrs['State'].update(ExitCode=137, OOMKilled=True)
            return c
        client.containers.create.side_effect = create
        args = SimpleNamespace(image_id='image', timeout_seconds=60)
        with patch.object(trial, 'preflight_space', return_value={}):
            report = trial.run_case(client, args, self.root/'snapshot', self.fixture,
                self.record, 2048, 1, self.root/'case')
        self.assertFalse(report['passed']); self.assertTrue(report['cleanup'])
        self.assertTrue(report['results'][0]['oom_killed'])

    def test_unconfirmed_container_removal_stops_sequence(self):
        client = Mock()
        def create(**options):
            c = self.fake_container(options['labels'][trial.LABEL], running=False)
            c.remove.side_effect = RuntimeError('daemon unavailable')
            return c
        client.containers.create.side_effect = create
        args = SimpleNamespace(image_id='image', timeout_seconds=60)
        with patch.object(trial, 'preflight_space', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                trial.run_case(client, args, self.root/'snapshot', self.fixture,
                    self.record, 2048, 1, self.root/'case')
        record = json.loads((self.root/'case/case.json').read_text())
        self.assertFalse(record['cleanup'])

    def test_native_manifest_uses_verified_original_paths_without_copies(self):
        from deploy.multirun.test_research_comparison import ResearchComparisonTests
        fixture = ResearchComparisonTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        build = self.root/'build'; build.mkdir()
        (build/'build.json').write_text(json.dumps({'files':{
            'calculation.cjs':'a'*64, 'runner.cjs':'b'*64}}))
        args = SimpleNamespace(frontend=fixture.root,
            prepared=fixture.root/'prepared', baseline=fixture.paths[0], alternative=fixture.paths[1],
            repetitions=1, build=build, output=self.root/'native-fixture')
        with patch('deploy.multirun.research_calibration.read_calibration', return_value=fixture.receipt):
            trial.prepare_raw(args)
        record, root = trial.check_fixture(args.output)
        self.assertEqual(record['mode'], 'raw-native')
        self.assertEqual(record['seeds'], ['606'])
        self.assertEqual(len(record['files']), 4)
        self.assertEqual({p.name for p in args.output.iterdir()}, {'manifest.json', 'COPYRIGHT.md'})
        self.assertTrue(all((root/name).is_file() for name in record['files']))

    def trial_args(self):
        frontend = self.root/'frontend'; package = frontend/'jasmine_web'; package.mkdir(parents=True)
        (package/'__init__.py').write_text('# tiny mocked frontend')
        build = self.root/'build'; build.mkdir()
        for name in ('runner.cjs', 'calculation.cjs'):
            (build/name).write_text('// tiny mocked '+name)
        hashes = {name:trial.fingerprint(build/name)['sha256'] for name in ('runner.cjs', 'calculation.cjs')}
        (build/'build.json').write_text(json.dumps(dict(files=hashes)))
        self.record['backend'] = dict(runner_sha256=hashes['runner.cjs'], calculation_sha256=hashes['calculation.cjs'])
        self.save()
        return SimpleNamespace(frontend=frontend, fixture=self.fixture, build=build,
            output=self.root/'trials', image='fictional', rounds=1,
            memory_mib=[3072,2048,4096], workers=[1,2], timeout_seconds=60)

    def test_daemon_failure_still_writes_a_failed_report(self):
        args = self.trial_args()
        with patch('docker.from_env', side_effect=RuntimeError('daemon unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'daemon unavailable'):
                trial.run(args)
        report = json.loads((args.output/'report.json').read_text())
        self.assertFalse(report['passed']); self.assertFalse(report['trials_completed'])
        self.assertEqual(report['error_type'], 'RuntimeError')

    def test_matrix_rejects_output_drift_between_successful_cases(self):
        args = self.trial_args(); client = Mock()
        client.info.return_value = {'CgroupVersion':'2'}
        client.images.get.return_value.id = 'sha256:'+'a'*64
        calls = []
        def run_case(*values):
            memory, workers = values[-3:-1]; calls.append((memory,workers))
            value = 'changed' if memory == 4096 else 'stable'
            report = dict(publication_sha256=value, view_sha256='same',
                index={'index_sha256':'same'}, catalogue={'sha256':'same'})
            return dict(passed=True, cleanup=True, elapsed_seconds=1,
                        results=[dict(passed=True, report=report)])
        with patch('docker.from_env', return_value=client), patch.object(trial, 'run_case', side_effect=run_case):
            self.assertEqual(trial.run(args), 1)
        report = json.loads((args.output/'report.json').read_text())
        self.assertEqual(calls, [(m,n) for m in args.memory_mib for n in args.workers])
        self.assertTrue(report['trials_completed']); self.assertFalse(report['passed'])
        self.assertFalse(report['successful_cases_have_identical_results'])
        client.close.assert_called_once()

    def test_log_error_does_not_skip_owned_container_cleanup(self):
        client = Mock(); created = []
        def create(**options):
            c = self.fake_container(options['labels'][trial.LABEL], running=False)
            c.logs.side_effect = RuntimeError('log retrieval failure'); created.append(c)
            return c
        client.containers.create.side_effect = create
        args = SimpleNamespace(image_id='image', timeout_seconds=60)
        with patch.object(trial, 'preflight_space', return_value={}):
            report = trial.run_case(client, args, self.root/'snapshot', self.fixture,
                self.record, 2048, 1, self.root/'case')
        self.assertFalse(report['passed']); self.assertTrue(report['cleanup'])
        created[0].remove.assert_called_once()


if __name__ == '__main__':
    unittest.main()
