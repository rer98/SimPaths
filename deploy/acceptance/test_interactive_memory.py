"""(C) Copyright 2026, by Ross Richardson
Exercise failure-sensitive Docker telemetry without containers or model runs.
@author ross richardson
"""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.acceptance._interactive_memory import ContainerMemorySampler, GIB


class Container:
    def __init__(self):
        self.id = 'trial-container'
        self.attrs = {'Config': {'Labels': {'jasmine.model_id': 'unique-trial'}},
                      'HostConfig': {'Memory': 5 * GIB, 'MemorySwap': 5 * GIB,
                                     'NanoCpus': 2_000_000_000},
                      'State': {'Running': True, 'Paused': False, 'Status': 'running',
                                'OOMKilled': False, 'StartedAt': '2026-10-15T12:00:00Z'},
                      'RestartCount': 0}
        self.memory = {'limit': 5 * GIB, 'usage': 4 * GIB,
                       'stats': {'inactive_file': GIB, 'swap': 0}}

    def reload(self):
        pass

    def stats(self, *, stream):
        assert stream is False
        return {'memory_stats': copy.deepcopy(self.memory),
                'cpu_stats': {'cpu_usage': {'total_usage': 100}}}


class Containers:
    def __init__(self, container):
        self.items = [container]
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self.items


class NoThread:
    def __init__(self, **kwargs):
        pass

    def start(self):
        pass

    def join(self, **kwargs):
        pass

    def is_alive(self):
        return False


class InteractiveMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'samples.jsonl'
        self.container = Container()
        self.containers = Containers(self.container)
        self.client = SimpleNamespace(containers=self.containers)

    def sampler(self, **kwargs):
        sampler = ContainerMemorySampler(self.client, 'unique-trial', self.path, **kwargs)
        with patch('deploy.acceptance._interactive_memory.threading.Thread', NoThread):
            sampler.start()
        return sampler

    def test_fixed_limits_and_cache_subtraction_with_private_bounded_evidence(self):
        sampler = self.sampler()
        sample = sampler.capture()
        self.assertEqual(sample['working_bytes'], 3 * GIB)
        self.assertEqual(self.containers.calls, [{'all': True,
            'filters': {'label': 'jasmine.model_id=unique-trial'}}])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        report = sampler.stop()
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['peak_usage_bytes'], 4 * GIB)
        self.assertEqual(report['peak_working_bytes'], 3 * GIB)
        self.assertEqual(report['peak_swap_bytes'], 0)
        self.assertEqual(report['samples'], 1)
        self.assertEqual(report, sampler.stop())
        self.assertEqual(json.loads(self.path.read_text())['limits']['MemorySwap'], 5 * GIB)

    def test_cgroup_v1_and_missing_swap_measurement_do_not_invent_zero(self):
        self.container.memory['stats'] = {'total_inactive_file': GIB}
        sampler = self.sampler()
        sample = sampler.capture()
        self.assertEqual(sample['cache_field'], 'total_inactive_file')
        self.assertIsNone(sample['swap_bytes'])
        report = sampler.stop()
        self.assertTrue(report['passed'])  # Docker's enforced swap allowance is still zero.
        self.assertIsNone(report['peak_swap_bytes'])

    def test_changes_to_each_fixed_limit_are_rejected(self):
        for field in ('Memory', 'NanoCpus', 'MemorySwap'):
            with self.subTest(field=field):
                self.path = Path(self.temp.name) / (field + '.jsonl')
                original = self.container.attrs['HostConfig'][field]
                sampler = self.sampler()
                sampler.capture()
                self.container.attrs['HostConfig'][field] = original + 1
                self.assertIsNone(sampler.capture())
                self.assertFalse(sampler.stop()['passed'])
                self.container.attrs['HostConfig'][field] = original

    def test_missing_invalid_memory_cache_and_swap_are_not_silently_accepted(self):
        mutations = [lambda c: c.memory.pop('usage'),
                     lambda c: c.memory.pop('limit'),
                     lambda c: c.memory['stats'].clear(),
                     lambda c: c.memory['stats'].update(inactive_file=5 * GIB),
                     lambda c: c.memory['stats'].update(inactive_file=True),
                     lambda c: c.memory['stats'].update(swap=1)]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.container = Container()
                self.containers.items = [self.container]
                self.path = Path(self.temp.name) / f'invalid-{index}.jsonl'
                mutate(self.container)
                sampler = self.sampler()
                self.assertIsNone(sampler.capture())
                report = sampler.stop()
                self.assertFalse(report['passed'])
                self.assertIsNone(report['peak_working_bytes'])

    def test_oom_restart_pause_and_stopped_states_fail(self):
        changes = [({'OOMKilled': True}, 0), ({'Running': False}, 0),
                   ({'Paused': True}, 0), ({}, 1)]
        for index, (state, restarts) in enumerate(changes):
            with self.subTest(index=index):
                self.container = Container()
                self.container.attrs['State'].update(state)
                self.container.attrs['RestartCount'] = restarts
                self.containers.items = [self.container]
                self.path = Path(self.temp.name) / f'state-{index}.jsonl'
                sampler = self.sampler()
                sampler.capture()
                self.assertFalse(sampler.stop()['passed'])

    def test_replacement_repeated_start_and_disappearance_fail(self):
        for change in ('replacement', 'restarted', 'disappeared', 'duplicate', 'wrong-label'):
            with self.subTest(change=change):
                self.container = Container()
                self.containers.items = [self.container]
                self.path = Path(self.temp.name) / (change + '.jsonl')
                sampler = self.sampler()
                sampler.capture()
                if change == 'replacement':
                    self.container.id = 'replacement'
                elif change == 'restarted':
                    self.container.attrs['State']['StartedAt'] = '2026-10-15T12:01:00Z'
                elif change == 'disappeared':
                    self.containers.items = []
                elif change == 'duplicate':
                    self.containers.items.append(Container())
                else:
                    self.container.attrs['Config']['Labels']['jasmine.model_id'] = 'unrelated'
                sampler.capture()
                self.assertFalse(sampler.stop()['passed'])

    def test_lazy_start_is_allowed_but_empty_measurements_cannot_pass(self):
        self.containers.items = []
        sampler = self.sampler()
        self.assertIsNone(sampler.capture())
        self.assertFalse(sampler.stop()['passed'])
        self.path = Path(self.temp.name) / 'appears.jsonl'
        sampler = self.sampler()
        sampler.capture()
        self.containers.items = [self.container]
        sampler.capture()
        self.assertTrue(sampler.stop()['passed'])

    def test_sampling_budget_and_gaps_invalidate_evidence(self):
        sampler = self.sampler(max_samples=1)
        sampler.capture()
        sampler.capture()
        self.assertFalse(sampler.stop()['passed'])
        self.path = Path(self.temp.name) / 'gap.jsonl'
        sampler = self.sampler()
        sampler.capture()
        sampler._last -= 11
        sampler.capture()
        self.assertFalse(sampler.stop()['passed'])
        self.path = Path(self.temp.name) / 'stop-gap.jsonl'
        sampler = self.sampler()
        sampler.capture()
        sampler._last -= 11
        self.assertFalse(sampler.stop()['passed'])

    def test_native_daemon_stop_is_bounded_when_docker_call_is_blocked(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(**kwargs):
            entered.set()
            release.wait(5)
            return [self.container]
        self.containers.list = blocked
        sampler = ContainerMemorySampler(self.client, 'unique-trial', self.path).start()
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        report = sampler.stop(timeout=.01)
        self.assertLess(time.monotonic() - started, .5)
        self.assertFalse(report['passed'])
        self.assertIn('Sampler did not stop within its bounded wait', report['errors'])
        release.set()
        sampler._thread.join(1)
        self.assertEqual(self.path.read_text(), '')

    def test_docker_error_text_is_private_and_existing_evidence_is_preserved(self):
        self.containers.list = lambda **kwargs: (_ for _ in ()).throw(RuntimeError('secret path'))
        sampler = self.sampler()
        sampler.capture()
        report = sampler.stop()
        self.assertFalse(report['passed'])
        self.assertNotIn('secret path', json.dumps(report))
        with self.assertRaises(FileExistsError):
            self.sampler()


if __name__ == '__main__':
    unittest.main()
