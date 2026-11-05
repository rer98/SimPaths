"""(C) Copyright 2026, by Ross Richardson

Parallel completion verification, stable receipts and bounded private helpers.
These tests use tiny fictional CSVs and never start a scientific simulation.
@author ross richardson
"""
import json
from pathlib import Path
import subprocess
from threading import Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from .artifacts import ArtifactError
from .csv_validation import validate_annual_csv
from .queue_adapter import SimPathsLocalAdapter, validate_outputs
from .submission_adapter import DispatchAdapter
from . import test_queue_adapter


class OutputValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_queue_adapter.QueueAdapterTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.outputs()
        self.adapter = SimPathsLocalAdapter(self.fixture.prepared)

    def validate(self, workers):
        return validate_outputs(self.fixture.work / 'output', self.fixture.configuration,
                                'savings-0001', workers=workers)

    def command(self, workers=2):
        return self.adapter.validation_command(self.fixture.lease, self.fixture.work,
                                                workers=workers)

    def run_helper(self, body=None):
        command = self.command()
        return subprocess.run(command.argv, input=command.input if body is None else body,
                              capture_output=True, timeout=15)

    def test_parallel_repetitions_have_identical_ordered_completion_fingerprints(self):
        expected = self.validate(1)
        actual = self.validate(2)
        self.assertEqual(actual, expected)
        self.assertEqual([row['seed'] for row in actual], ['606', '607', '608'])
        self.assertTrue(all(set(row) == {'seed', 'fingerprint'} for row in actual))

    def test_independent_repetitions_overlap_with_at_most_two_active_checks(self):
        expected = self.validate(1)
        mutex, overlap = Lock(), Event()
        active = maximum = entered = 0

        def check(paths, years):
            nonlocal active, maximum, entered
            with mutex:
                active += 1
                entered += 1
                first_pair = entered <= 2
                maximum = max(maximum, active)
                if active == 2:
                    overlap.set()
            try:
                if first_pair:
                    self.assertTrue(overlap.wait(5), 'Independent repetitions did not overlap')
                return validate_annual_csv(paths, years)
            finally:
                with mutex:
                    active -= 1

        with patch('deploy.multirun.csv_validation.validate_annual_csv', side_effect=check):
            self.assertEqual(self.validate(2), expected)
        self.assertEqual(maximum, 2)
        self.assertEqual(entered, 3)

    def test_parallel_checks_still_reject_changed_settings_duplicate_seeds_and_bad_rows(self):
        options = self.fixture.work / 'output/606/input/options.txt'
        original = options.read_text()
        for body in (original.replace('savingRate: 0.04', 'savingRate: 0.07'),
                     original.replace('randomSeedIfFixed: 606', 'randomSeedIfFixed: 607')):
            options.write_text(body)
            with self.assertRaises(ArtifactError):
                self.validate(2)
        options.write_text(original)
        csv = self.fixture.work / 'output/607/csv/BenefitUnit.csv'
        csv.write_text('run,time,id\nfictional,2019\n')
        with self.assertRaisesRegex(ArtifactError, 'Truncated'):
            self.validate(2)

    def test_parallel_validation_rejects_links_and_keeps_extra_csv_fingerprints(self):
        before = self.validate(2)
        extra = self.fixture.work / 'output/606/csv/private-extra.csv'
        extra.write_text('fictional private supplementary output')
        self.assertNotEqual(self.validate(2), before)
        person = self.fixture.work / 'output/606/csv/Person.csv'
        target = person.with_name('ordinary.csv')
        person.rename(target)
        person.symlink_to(target)
        with self.assertRaises(ArtifactError):
            self.validate(2)

    def test_private_command_binds_the_frozen_configuration_and_reads_no_scientific_files(self):
        with patch('deploy.multirun.queue_adapter.validate_outputs', side_effect=AssertionError('No rows here')):
            command = self.command()
        self.assertEqual(command.argv[1:3], ('-I', '-B'))
        self.assertTrue(Path(command.argv[-1]).is_absolute())
        self.assertEqual(Path(command.argv[-1]).name, 'output_validation.py')
        body = json.loads(command.input)
        self.assertEqual(body['output'], str(self.fixture.work / 'output'))
        self.assertEqual(body['run_set_id'], self.fixture.lease.configuration_id)
        self.assertEqual(body['workers'], 2)
        self.assertEqual(len(body['configuration']['run_sets']), 1)

    def test_actual_isolated_helper_returns_only_the_original_receipts(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), self.validate(1))
        self.assertLessEqual(len(result.stdout), 16 * 1024)
        self.assertEqual(result.stderr, b'')
        self.assertNotIn(str(self.fixture.root).encode(), result.stdout)
        self.assertNotIn(b'run-606', result.stdout)

    def test_actual_helper_failure_never_exports_private_rows_paths_or_exceptions(self):
        path = self.fixture.work / 'output/606/csv/Person.csv'
        path.write_text('run,time,id\nSECRET-ROW,2019\n')
        result = self.run_helper()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {'error': 'Output validation failed'})
        self.assertEqual(result.stderr, b'')
        self.assertNotIn(b'SECRET', result.stdout)
        self.assertNotIn(str(path).encode(), result.stdout)

    def test_private_helper_rejects_bad_worker_counts_extra_fields_and_large_requests(self):
        for workers in (0, 3, True, 1.5):
            with self.subTest(workers=workers), self.assertRaises(ArtifactError):
                self.command(workers)
        original = json.loads(self.command().input)
        values = [dict(original, workers=3), dict(original, output='relative/output'),
                  dict(original, unexpected='SECRET-PRIVATE')]
        for value in values:
            with self.subTest(value=value):
                result = self.run_helper(json.dumps(value).encode())
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout), {'error': 'Output validation failed'})
                self.assertEqual(result.stderr, b'')
        result = self.run_helper(b' ' * (1024**2 + 1))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {'error': 'Output validation failed'})
        self.assertEqual(result.stderr, b'')

    def test_dispatch_routes_only_simulation_validation_to_the_retained_worker(self):
        dispatch = DispatchAdapter(SimpleNamespace())
        dispatch.adapter = Mock(return_value=self.adapter)
        self.assertEqual(dispatch.validation_command(self.fixture.lease, self.fixture.work, workers=2),
                         self.command())
        dispatch.adapter.reset_mock()
        preparation = SimpleNamespace(specification={'operation': 'prepare'})
        self.assertIsNone(dispatch.validation_command(preparation, self.fixture.work, workers=1))
        dispatch.adapter.assert_not_called()


if __name__ == '__main__':
    unittest.main()
