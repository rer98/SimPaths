#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Verify the user-data context excludes existing databases and ordinary private data.

@author ross richardson
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('user_data_package', Path(__file__).with_name('package_image.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PackagingTests(unittest.TestCase):
    def test_only_supplied_training_sources_and_workbooks_are_copied(self):
        with tempfile.TemporaryDirectory(dir='/tmp/codex-rer') as directory:
            root = Path(directory)
            repo = root / 'repo'
            fixtures = ['input/InitialPopulations/training/population_initial_UK_2019.csv',
                        'input/EUROMODoutput/training/EUROMODpolicySchedule.xlsx',
                        'input/EUROMODoutput/training/UK_2019.txt',
                        'input/input.mv.db', 'input/private.csv', 'input/EUROMODoutput/private.txt',
                        'input/InitialPopulations/private.csv', 'input/parameters.xlsx',
                        'webserver.properties', 'license.txt', 'COPYRIGHT.md',
                        'deploy/web-user-data/Dockerfile', 'deploy/web-user-data/README.md']
            for relative in fixtures:
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            with zipfile.ZipFile(repo / 'singlerun.jar', 'w') as archive:
                archive.writestr('simpaths/experiment/SimPathsUserDataStartup.class', b'fixture')
                archive.writestr('microsim/web/server/DatabaseQueryAccess.class', b'fixture')
                archive.writestr('microsim/web/server/BackendAuth.class', b'fixture')
                archive.writestr('microsim/web/server/WorkbookBudget.class', b'fixture')
            output = root / 'context'
            module.package(repo, output)
            for relative in ['input/input.mv.db', 'input/private.csv', 'input/EUROMODoutput/private.txt',
                             'input/InitialPopulations/private.csv']:
                self.assertFalse((output / relative).exists())
            self.assertTrue((output / 'input/parameters.xlsx').exists())
            self.assertTrue((output / 'input/EUROMODoutput/training/UK_2019.txt').exists())
            with self.assertRaises(ValueError):
                module.package(repo, output)


if __name__ == '__main__':
    unittest.main()
