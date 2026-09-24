# (C) Copyright 2026, by Ross Richardson
#
# Test prepare profile.
#
# @author ross richardson
#

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

SCRIPT = Path(__file__).with_name('prepare_profile.py')
spec = importlib.util.spec_from_file_location('prepare_profile', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PreparationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.inputs = self.repo / 'input'
        self.inputs.mkdir(parents=True)
        (self.inputs / 'parameters.xlsx').write_bytes(b'fixture')
        population = self.inputs / 'InitialPopulations/training/population_initial_UK_2019.csv'
        population.parent.mkdir(parents=True)
        population.write_text('fixture')
        donor = self.inputs / 'EUROMODoutput/training'
        donor.mkdir(parents=True)
        for name in ['EUROMODpolicySchedule.xlsx', 'DatabaseCountryYear.xlsx'] + [
                f'uk_{year}_std.txt' for year in range(2011, 2027)]:
            (donor / name).write_text('fixture')

    def test_existing_and_source_destinations_rejected(self):
        for output in (self.repo, self.repo / 'new', self.inputs / 'new'):
            with self.assertRaises(ValueError):
                module.validate_destination(output, self.repo, self.inputs)

    def test_database_and_nontraining_data_excluded(self):
        (self.inputs / 'input.mv.db').write_text('do not copy')
        (self.inputs / 'InitialPopulations/private.csv').write_text('do not copy')
        selected = module.selected_inputs(self.inputs)
        self.assertFalse(any(p.suffix == '.db' or p.name == 'private.csv' for p in selected))
        self.assertEqual(sum(p.suffix == '.txt' for p in selected), 16)

    def test_missing_or_external_training_file_rejected(self):
        path = self.inputs / 'EUROMODoutput/training/uk_2026_std.txt'
        path.unlink()
        with self.assertRaises(ValueError):
            module.selected_inputs(self.inputs)
        external = self.root / 'external.txt'
        external.write_text('fixture')
        path.symlink_to(external)
        with self.assertRaises(ValueError):
            module.selected_inputs(self.inputs)

    def test_fallback_and_count_mismatch_rejected(self):
        log = self.root / 'load.log'
        counts = dict(person='10', household='4', benefitunit='5')
        log.write_text('ordinary construction')
        with self.assertRaises(RuntimeError):
            module.check_loading(log, counts, counts, counts)
        log.write_text('Found processed dataset - preparing for simulation')
        with self.assertRaises(RuntimeError):
            module.check_loading(log, counts, counts, dict(counts, person='9'))
        module.check_loading(log, counts, counts, counts)

    def test_failed_subprocess_never_publishes_package(self):
        (self.repo / 'singlerun.jar').write_text('fixture')
        args = SimpleNamespace(repo=self.repo, output=self.root / 'failed',
                               timeout=10, dry_run=False)
        with patch.object(module, 'execute', side_effect=RuntimeError('compile failed')), \
                patch.object(module.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**11)):
            with self.assertRaises(RuntimeError):
                module.prepare(args)
        self.assertFalse((args.output / 'package').exists())
        self.assertIn('failed', (args.output / 'status.json').read_text())


if __name__ == '__main__':
    unittest.main()
