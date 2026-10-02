"""(C) Copyright 2026, by Ross Richardson
Test local concurrency runner resource checks, configuration and scoped cleanup.
@author ross richardson
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import runpy
load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
subject = load_tool('acceptance/run_two_session_acceptance.py')
resource_check = subject.resource_check
isolated_environment = subject.isolated_environment
cleanup_models = subject.cleanup_models
summarize_samples = subject.summarize_samples
GIB = subject.GIB

class TwoSessionTests(unittest.TestCase):
    def test_headroom_required_before_launch(self):
        resource_check({'available_memory_bytes': 8*GIB, 'root_free_bytes': 6*GIB})
        for memory, disk in ((8*GIB-1, 6*GIB), (8*GIB, 6*GIB-1)):
            with self.assertRaises(AssertionError):
                resource_check({'available_memory_bytes': memory, 'root_free_bytes': disk})

    def test_isolated_server_does_not_inherit_existing_redis_or_capacity(self):
        with patch.dict(os.environ, {'REDIS_URL': 'redis://other:123/5', 'VM_MAX_SESSIONS': '1',
                                     'JASMINE_CATALOGUE_FILE': '/production/catalogue', 'SESSION_SECRET': 'old'}):
            env = isolated_environment(postgres_dsn_file='/private/disposable.dsn')
        self.assertFalse(any(key.startswith('REDIS_') for key in env))
        self.assertEqual(env['VM_MAX_SESSIONS'], '2')
        self.assertEqual(env['VM_MAX_SESSIONS_PER_CLIENT'], '2')
        self.assertEqual(env['VM_SESSION_MEMORY_BUDGET_MIB'], '8192')
        self.assertEqual(env['PYTHON_DOTENV_DISABLED'], '1')
        self.assertNotIn('JASMINE_CATALOGUE_FILE', env)
        self.assertNotEqual(env['SESSION_SECRET'], 'old')
        self.assertEqual(env['VM_STATE_BACKEND'], 'postgres')

    def test_postgres_candidate_uses_only_its_private_disposable_configuration(self):
        with patch.dict(os.environ, {'REDIS_URL':'redis://production', 'VM_POSTGRES_DSN':'private-old',
                                     'VM_POSTGRES_SCHEMA':'production', 'VM_STATE_BACKEND':'redis'}):
            env=isolated_environment(state_backend='postgres',postgres_dsn_file='/private/disposable.dsn')
        self.assertEqual(env['VM_STATE_BACKEND'],'postgres')
        self.assertEqual(env['VM_POSTGRES_SCHEMA'],'jasmine_vm')
        self.assertEqual(env['VM_POSTGRES_DSN_FILE'],'/private/disposable.dsn')
        self.assertNotIn('VM_POSTGRES_DSN',env)
        self.assertFalse(any(key.startswith('REDIS_') for key in env))

    def test_browser_response_summary_keeps_millisecond_units_and_tail_latency(self):
        result=subject.summarize_response_times(dict(status=list(range(1,101)),charts=[],logs=[12.5]))
        self.assertEqual(result['status'],dict(requests=100,mean_ms=50.5,p95_ms=95,max_ms=100))
        self.assertNotIn('charts',result)
        self.assertEqual(result['logs']['p95_ms'],12.5)

    def test_foreign_container_never_removed(self):
        client = Mock()
        container = Mock(labels={'jasmine.model_id': 'test', 'jasmine.session_id': 's', 'jasmine.deployment_id': 'foreign'})
        client.containers.list.return_value = [container]
        with self.assertRaises(AssertionError): cleanup_models(client, 'test', 'mine')
        container.remove.assert_not_called()

    def test_cleanup_deletes_only_owned_containers_and_empty_expected_network(self):
        import hashlib
        labels = {'jasmine.model_id': 'test', 'jasmine.session_id': 's', 'jasmine.deployment_id': 'mine'}
        container = Mock(labels=labels)
        network = Mock()
        network.name = 'jms-'+hashlib.sha256(b's').hexdigest()[:10]
        network.attrs = {'Labels': labels, 'Containers': {}}
        client = Mock()
        client.containers.list.side_effect = [[container], []]
        client.networks.list.side_effect = [[network], []]
        cleanup_models(client, 'test', 'mine')
        container.remove.assert_called_once_with(force=True)
        network.remove.assert_called_once_with()

    def test_cleanup_refuses_network_with_attached_container(self):
        import hashlib
        client = Mock()
        client.containers.list.return_value = []
        network = Mock()
        network.name = 'jms-'+hashlib.sha256(b's').hexdigest()[:10]
        network.attrs = {'Labels': {'jasmine.model_id': 'test', 'jasmine.session_id': 's', 'jasmine.deployment_id': 'mine'},
                         'Containers': {'foreign': {}}}
        client.networks.list.return_value = [network]
        with self.assertRaises(AssertionError): cleanup_models(client, 'test', 'mine')
        network.remove.assert_not_called()

    def test_summary_reports_observed_combined_peak_not_sum_of_separate_peaks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'samples.jsonl'
            rows = [{'host': {'root_free_bytes': 10, 'available_memory_bytes': 20},
                     'containers': [{'id': 'a', 'memory_usage_bytes': 5, 'cpu_total_ns': 10**9},
                                    {'id': 'b', 'memory_usage_bytes': 2, 'cpu_total_ns': 2*10**9}]},
                    {'host': {'root_free_bytes': 9, 'available_memory_bytes': 19},
                     'containers': [{'id': 'a', 'memory_usage_bytes': 2, 'cpu_total_ns': 3*10**9},
                                    {'id': 'b', 'memory_usage_bytes': 5, 'cpu_total_ns': 4*10**9}]}]
            path.write_text('\n'.join(map(json.dumps, rows)))
            result = summarize_samples(path)
            self.assertEqual(result['peak_combined_container_memory_bytes'], 7)
            self.assertEqual(result['minimum_root_free_bytes'], 9)
            self.assertEqual(result['containers']['b']['cpu_seconds_observed'], 4)

    def test_sampler_keeps_survivor_when_leave_removes_listed_container(self):
        from docker.errors import NotFound
        from requests.exceptions import JSONDecodeError
        for transition in ('removed', 'empty-then-removed', 'empty-then-stopped'):
            with self.subTest(transition=transition):
                client = Mock()
                removed = Mock(id='removed', status='exited')
                if transition == 'removed':
                    removed.stats.side_effect = NotFound('removed during Leave')
                else:
                    removed.stats.side_effect = JSONDecodeError('empty stats', '', 0)
                    if transition == 'empty-then-removed':
                        removed.reload.side_effect = NotFound('removed during Leave')
                survivor = Mock(id='survivor')
                survivor.stats.return_value = {'memory_stats': {'usage': 123},
                                             'cpu_stats': {'cpu_usage': {'total_usage': 456}}}
                client.containers.list.return_value = [removed, survivor]
                sampler = subject.Sampler(client, 'fictional', Path('/unused'))
                with patch.object(subject, 'host_resources', return_value={'available_memory_bytes': 8*GIB,
                                                                          'root_free_bytes': 6*GIB}):
                    row = sampler.snapshot()
                client.containers.list.assert_called_once_with(filters={'label': 'jasmine.model_id=fictional'}, sparse=True)
                removed.stats.assert_called_once_with(stream=False, one_shot=True)
                self.assertEqual([{'id': 'survivor', 'memory_usage_bytes': 123,
                                   'cpu_total_ns': 456, 'block_io': []}], row['containers'])
                self.assertEqual(['removed'], row['departed_containers'])
                self.assertEqual(1, sampler.departed_containers)
                self.assertEqual(0, sampler.errors)

    def test_sampler_records_real_docker_failure_and_still_fails_acceptance(self):
        from docker.errors import APIError
        from requests.exceptions import JSONDecodeError
        for failure in ('daemon', 'invalid-running-stats', 'unknown-state', 'failed-state-recheck'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                client = Mock()
                error = APIError('private daemon detail', response=Mock(status_code=500))
                expected = {'type': 'APIError', 'http_status': 500}
                if failure == 'daemon':
                    client.containers.list.side_effect = error
                else:
                    container = Mock(id='running', status=None if failure == 'unknown-state' else 'running')
                    container.stats.side_effect = JSONDecodeError('private daemon detail', '', 0)
                    client.containers.list.return_value = [container]
                    if failure == 'failed-state-recheck':
                        container.reload.side_effect = error
                    else:
                        expected = {'type': 'JSONDecodeError'}
                path = Path(directory)/'samples.jsonl'
                sampler = subject.Sampler(client, 'fictional', path)
                sampler.started = 0
                sampler.stop = Mock()
                sampler.stop.is_set.side_effect = [False, True]
                with patch.object(subject, 'host_resources', return_value={'available_memory_bytes': 8*GIB,
                                                                          'root_free_bytes': 6*GIB}):
                    sampler.sample()
                self.assertEqual(1, sampler.errors)
                self.assertEqual(expected, json.loads(path.read_text())['error'])
                self.assertNotIn('private daemon detail', path.read_text())
                with self.assertRaisesRegex(AssertionError, 'No resource samples'):
                    summarize_samples(path)


if __name__ == '__main__':
    unittest.main()
