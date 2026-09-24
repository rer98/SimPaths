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
            env = isolated_environment(12345)
        self.assertEqual(env['REDIS_URL'], 'redis://127.0.0.1:12345/0')
        self.assertEqual(env['VM_MAX_SESSIONS'], '2')
        self.assertEqual(env['VM_MAX_SESSIONS_PER_CLIENT'], '2')
        self.assertEqual(env['VM_SESSION_MEMORY_BUDGET_MIB'], '8192')
        self.assertEqual(env['PYTHON_DOTENV_DISABLED'], '1')
        self.assertNotIn('JASMINE_CATALOGUE_FILE', env)
        self.assertNotEqual(env['SESSION_SECRET'], 'old')

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


if __name__ == '__main__':
    unittest.main()
