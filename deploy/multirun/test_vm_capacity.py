"""(C) Copyright 2026, by Ross Richardson

Capacity evidence includes both separated services without hiding missing readings.
Uses small local cgroup fixtures; no host services, simulations or database writes.
@author ross richardson
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .vm_capacity import APPLICATION_UNITS, application_memory, summary


class ApplicationMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.public, self.executor = APPLICATION_UNITS

    def write(self, unit, value):
        path = self.root/unit/'memory.current'
        path.parent.mkdir(exist_ok=True)
        path.write_text(value)

    def test_separate_service_readings_and_complete_total_include_executor(self):
        self.write(self.public, '104857600\n')
        self.write(self.executor, '734003200\n')
        components, total = application_memory(self.root)
        self.assertEqual(components, {self.public: 104857600, self.executor: 734003200})
        self.assertEqual(total, 838860800)

    def test_missing_invalid_or_unreadable_executor_never_becomes_zero(self):
        self.write(self.public, '100\n')
        for value in (None, '-1\n', 'invalid\n'):
            with self.subTest(value=value):
                if value is not None:
                    self.write(self.executor, value)
                components, total = application_memory(self.root)
                self.assertEqual(components[self.public], 100)
                self.assertIsNone(components[self.executor])
                self.assertIsNone(total)
        original = Path.read_text
        def reading(path, *args, **kwargs):
            if path.parent.name == self.executor:
                raise PermissionError('Fictional inaccessible cgroup')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', reading):
            self.assertEqual(application_memory(self.root),
                             ({self.public: 100, self.executor: None}, None))

    def test_missing_public_service_is_unknown_and_zero_is_a_valid_measurement(self):
        self.write(self.executor, '0\n')
        self.assertEqual(application_memory(self.root),
                         ({self.public: None, self.executor: 0}, None))
        self.write(self.public, '0\n')
        self.assertEqual(application_memory(self.root),
                         ({self.public: 0, self.executor: 0}, 0))

    def sample(self, public, executor):
        return dict(docker_stats_unavailable=False, private_storage=dict(free_bytes=1000),
                    attempts=[], application_service_memory_bytes={self.public: public, self.executor: executor},
                    application_memory_bytes=public+executor if public is not None and executor is not None else None)

    def test_summary_uses_complete_sample_total_not_sum_of_independent_peaks(self):
        result = summary([self.sample(100, 700), self.sample(300, 200), self.sample(900, None)])
        self.assertEqual(result['maximum_application_memory_bytes'], 800)
        self.assertEqual(result['maximum_application_service_memory_bytes'],
                         {self.public: 900, self.executor: 700})
        self.assertEqual(result['unavailable_application_memory_samples'], 1)

    def test_summary_keeps_legacy_totals_without_inventing_component_measurements(self):
        legacy = self.sample(100, 700)
        del legacy['application_service_memory_bytes']
        result = summary([legacy])
        self.assertEqual(result['maximum_application_memory_bytes'], 800)
        self.assertEqual(result['maximum_application_service_memory_bytes'],
                         {self.public: None, self.executor: None})
        self.assertEqual(result['unavailable_application_memory_samples'], 0)


if __name__ == '__main__':
    unittest.main()
