"""(C) Copyright 2026, by Ross Richardson
Reject misleading native memory-monitor proof evidence: missing readings,
undelivered warnings, premature repeats and fake recovery by dropping allocations.
@author ross richardson
"""
import copy
import json
import unittest

from deploy.acceptance.run_memory_monitor_acceptance import MIB, validate_probe


def fixture():
    def sample(phase, *, heap=56, limit=256, used=237, inactive=12, direct=110):
        elapsed = dict(initial=0, pressure=100, held=12_100, resized=12_200)[phase]
        return dict(phase=phase, elapsed_ms=elapsed,
                    heap_used_bytes=heap*MIB, heap_committed_bytes=64*MIB,
                    heap_max_bytes=64*MIB, container_limit_bytes=limit*MIB,
                    container_used_bytes=used*MIB, inactive_file_bytes=inactive*MIB,
                    working_set_bytes=(used-inactive)*MIB, monitor_threads=1,
                    direct_allocated_bytes=direct*MIB)
    return [sample('initial', heap=10, used=90, inactive=10, direct=0),
            sample('pressure'), sample('held'), sample('resized', limit=320)], [
        dict(kind='heap', elapsed_ms=1000), dict(kind='container', elapsed_ms=1005)]


def log_text(samples, warnings, *, delivered=True):
    lines = ['MEMORY_MONITOR_SAMPLE ' + json.dumps(value) for value in samples]
    if delivered:
        for warning in warnings:
            lines.append('WARNING: Java heap is 88.0%' if warning['kind'] == 'heap'
                         else 'WARNING: Container working set is 88.0%')
    lines.extend(['MEMORY_MONITOR_WARNINGS ' + json.dumps(warnings), 'MEMORY_MONITOR_OK container'])
    return '\n'.join(lines)


class MemoryMonitorEvidenceTests(unittest.TestCase):
    def validate(self, samples, warnings, **options):
        return validate_probe(log_text(samples, warnings, **options), 'container',
                              initial_limit=256*MIB, changed_limit=320*MIB)

    def test_complete_native_evidence_keeps_live_allocations_and_heap_maximum(self):
        samples, warnings = fixture()
        result = self.validate(samples, warnings)
        self.assertEqual(result['samples']['resized']['container_limit_bytes'], 320*MIB)
        self.assertEqual(result['warnings'], warnings)

    def test_claimed_warning_without_console_delivery_fails(self):
        samples, warnings = fixture()
        with self.assertRaisesRegex(AssertionError, 'delivered console'):
            self.validate(samples, warnings, delivered=False)

    def test_repeat_before_throttle_fails_but_later_warning_passes(self):
        samples, warnings = fixture()
        early = warnings + [dict(kind='heap', elapsed_ms=15_000)]
        with self.assertRaisesRegex(AssertionError, 'thirty-second throttle'):
            self.validate(samples, early)
        self.validate(samples, warnings + [dict(kind='heap', elapsed_ms=31_000)])

    def test_low_pressure_or_nearly_exhausted_container_is_not_accepted(self):
        samples, warnings = fixture()
        low = copy.deepcopy(samples)
        low[1].update(container_used_bytes=200*MIB, working_set_bytes=188*MIB)
        with self.assertRaisesRegex(AssertionError, 'warning-level usage'):
            self.validate(low, warnings)
        high = copy.deepcopy(samples)
        high[1].update(container_used_bytes=250*MIB, working_set_bytes=238*MIB)
        with self.assertRaisesRegex(AssertionError, 'ceiling'):
            self.validate(high, warnings)

    def test_missing_readings_and_wrong_working_set_fail(self):
        samples, warnings = fixture()
        missing = copy.deepcopy(samples)
        missing[1]['inactive_file_bytes'] = None
        with self.assertRaisesRegex(AssertionError, 'cache measurement'):
            self.validate(missing, warnings)
        wrong = copy.deepcopy(samples)
        wrong[1]['working_set_bytes'] = wrong[1]['container_used_bytes']
        with self.assertRaisesRegex(AssertionError, 'Working set'):
            self.validate(wrong, warnings)

    def test_resize_cannot_claim_recovery_by_changing_heap_or_dropping_allocations(self):
        samples, warnings = fixture()
        changed_heap = copy.deepcopy(samples)
        changed_heap[3]['heap_max_bytes'] = 80*MIB
        with self.assertRaisesRegex(AssertionError, 'heap maximum changed'):
            self.validate(changed_heap, warnings)
        dropped = copy.deepcopy(samples)
        dropped[3]['direct_allocated_bytes'] = 0
        with self.assertRaisesRegex(AssertionError, 'dropped its live allocation'):
            self.validate(dropped, warnings)

    def test_wrong_limit_and_missing_resize_phase_fail(self):
        samples, warnings = fixture()
        wrong = copy.deepcopy(samples)
        wrong[3]['container_limit_bytes'] = 256*MIB
        with self.assertRaisesRegex(AssertionError, 'configured Docker limit'):
            self.validate(wrong, warnings)
        with self.assertRaisesRegex(AssertionError, 'evidence phases'):
            self.validate(samples[:-1], warnings)

    def test_multiple_monitor_threads_fail(self):
        samples, warnings = fixture()
        samples[1]['monitor_threads'] = 2
        with self.assertRaisesRegex(AssertionError, 'monitoring daemons'):
            self.validate(samples, warnings)

    def test_pressure_must_be_held_across_another_monitor_poll(self):
        samples, warnings = fixture()
        samples[2]['elapsed_ms'] = 1500
        with self.assertRaisesRegex(AssertionError, 'further monitoring poll'):
            self.validate(samples, warnings)


if __name__ == '__main__':
    unittest.main()
