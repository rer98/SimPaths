# (C) Copyright 2026, by Ross Richardson
#
# Test package image.
#
# @author ross richardson
#

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).with_name('package_image.py')
spec = importlib.util.spec_from_file_location('package_image', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        files = {'input/input.mv.db': 'fixture', 'input/EUROMODoutput/training/EUROMODpolicySchedule.xlsx': 'fixture',
                 'input/population.csv': 'raw source', 'README.md': 'old instructions',
                 'profile.json': json.dumps(dict(format_version=1, profile=module.PROFILE,
                        profile_id='uk-2019-training-50000-seed606', jar_sha256='preparation-binary'))}
        for relative, value in files.items():
            p = self.source / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value)
        (self.source / 'checksums.json').write_text(json.dumps({p: module.digest(self.source / p) for p in files}))
        with zipfile.ZipFile(self.repo / 'singlerun.jar', 'w') as z:
            z.writestr('simpaths/experiment/SimPathsQuickStart.class', b'fixture')
            z.writestr('microsim/web/server/DatabaseQueryAccess.class', b'fixture')
            z.writestr('microsim/web/server/BackendAuth.class', b'fixture')
            z.writestr('microsim/web/server/WorkbookBudget.class', b'fixture')
        for name in ('webserver.properties', 'license.txt', 'COPYRIGHT.md'):
            (self.repo / name).write_text('fixture')

    def test_context_includes_runtime_inputs_and_new_instructions_only(self):
        output = self.root / 'context'
        module.package(self.source, self.repo, output)
        self.assertTrue((output / 'input/input.mv.db').exists())
        self.assertFalse((output / 'input/population.csv').exists())
        self.assertTrue((output / 'COPYRIGHT.md').is_file())
        self.assertIn('runtime_jar_sha256', json.loads((output / 'release.json').read_text()))
        readme = (output / 'README.md').read_text()
        self.assertIn('Run your first simulation', readme)
        self.assertIn('50,000 requested people', readme)
        self.assertNotIn('SimPathsQuickStart --desktop', readme)
        self.assertNotIn('docker build', readme)
        with self.assertRaises(ValueError):
            module.package(self.source, self.repo, output)

    def test_corrupt_package_rejected_before_creating_context(self):
        (self.source / 'input/input.mv.db').write_text('corrupt')
        output = self.root / 'context'
        with self.assertRaises(ValueError):
            module.package(self.source, self.repo, output)
        self.assertFalse(output.exists())

    def test_small_profile_packages_with_distinct_label(self):
        receipt = json.loads((self.source/'profile.json').read_text())
        receipt['profile'] = module.profile_for(20000)
        receipt['profile_id'] = module.profile_id(20000)
        (self.source/'profile.json').write_text(json.dumps(receipt))
        checks = json.loads((self.source/'checksums.json').read_text())
        checks['profile.json'] = module.digest(self.source/'profile.json')
        (self.source/'checksums.json').write_text(json.dumps(checks))
        module.package(self.source, self.repo, self.root/'small')
        self.assertIn('20,000 requested people', (self.root/'small/README.md').read_text())
        receipt['profile_id'] = module.profile_id(50000)
        (self.source/'profile.json').write_text(json.dumps(receipt))
        checks['profile.json'] = module.digest(self.source/'profile.json')
        (self.source/'checksums.json').write_text(json.dumps(checks))
        with self.assertRaises(ValueError):
            module.package(self.source, self.repo, self.root/'mismatch')
        self.assertFalse((self.root/'mismatch').exists())

    def test_reused_base_gets_package_fixes_and_nonroot_runtime(self):
        template = Path(__file__).with_name('Dockerfile').read_text()
        result = module.release_dockerfile(template, 'local:prepared')
        self.assertIn('FROM local:prepared\nUSER root\n', result)
        self.assertIn('apt-get update', result)
        self.assertIn('ge 2.7.4-1ubuntu0.1', result)
        self.assertIn('ge 2.15.2+dfsg-0.1ubuntu0.2', result)
        self.assertIn('USER 10001:10001\nEXPOSE 7070', result)
        self.assertIn('&& chown -R 10001:10001 /app/input', result)
        self.assertIn(template[template.index('RUN mkdir -p output'):], result)
        self.assertLess(result.index('chown -R'), result.index('USER 10001:10001'))
        self.assertNotIn('COPY --chown=10001:10001 input/', result)
        self.assertLess(result.index('apt-get update'), result.index('COPY singlerun.jar'))

    def test_unsupported_population_rejected(self):
        with self.assertRaises(ValueError):
            module.profile_for(30000)


if __name__ == "__main__":
    unittest.main()
