"""(C) Copyright 2026, by Ross Richardson

Exercise maintainer release workflows from an independent SimPaths checkout.

@author ross richardson
"""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'model'
        shutil.copytree(ROOT / 'deploy', self.repo / 'deploy',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        self.loader = runpy.run_path(str(self.repo / 'deploy/_tool_loader.py'))
        self.load = self.loader['load_tool']
        self.frontend = self.root / 'web'
        self.catalogue = self.frontend / 'deploy/simpaths/models.json'
        self.catalogue.parent.mkdir(parents=True)
        self.catalogue.write_text(json.dumps({'models': [
            {'id': f'simpaths-quickstart-{p}', 'deployment': {'image': f'prepared:{p}'}}
            for p in (20000, 50000)]}))

    def command(self, relative, *args):
        environment = {**os.environ, 'PYTHONPATH': '', 'SIMPATHS_REPO': '/absent-coordinator',
                       'JASMINE_WEB_REPO': str(self.frontend)}
        return subprocess.run([sys.executable, str(self.repo / 'deploy' / relative), *map(str, args)],
                              cwd=self.root, env=environment, text=True, capture_output=True, timeout=30)

    def test_all_commands_offer_help_without_coordinator_frontend_or_docker(self):
        for relative in ['web-quickstart/build_release.py', 'web-quickstart/promote_release.py',
                         *[f'acceptance/{p.name}' for p in (self.repo / 'deploy/acceptance').glob('run_*.py')]]:
            with self.subTest(command=relative):
                result = self.command(relative, '--help')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--output' if 'promote' not in relative else '--apply', result.stdout)

    def test_release_dry_run_uses_own_checkout_and_selected_catalogue(self):
        result = self.command('web-quickstart/build_release.py', '--dry-run', '--population', '20000',
                              '--output', self.root / 'evidence')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['simpaths'], str(self.repo))
        self.assertEqual(plan['core'], str(self.root / 'JAS-mine-core'))
        self.assertEqual(plan['frontend'], str(self.frontend))
        self.assertEqual([p['deployment']['image'] for p in plan['profiles']], ['prepared:20000'])
        self.assertFalse((self.root / 'evidence').exists())
        self.catalogue.unlink()
        result = self.command('web-quickstart/build_release.py', '--dry-run', '--base-20000', 'fresh:small',
                              '--base-50000', 'fresh:large', '--output', self.root / 'evidence')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p['deployment']['image'] for p in json.loads(result.stdout)['profiles']],
                         ['fresh:small', 'fresh:large'])

    def test_image_selection_is_explicit_or_from_the_requested_catalogue(self):
        workflow = self.load('_workflow.py')
        self.assertEqual(workflow.quickstart_image(self.frontend, 50000), 'prepared:50000')
        other = self.root / 'other.json'
        other.write_text(json.dumps({'models': [
            {'id': 'simpaths-quickstart-20000', 'deployment': {'image': 'chosen:small'}}]}))
        self.assertEqual(workflow.quickstart_image(self.frontend, 20000, catalogue=other), 'chosen:small')
        self.assertEqual(workflow.quickstart_image('/absent', 20000, image='explicit:small'), 'explicit:small')
        with self.assertRaises(ValueError):
            workflow.quickstart_image(self.frontend, 50000, catalogue=other)

    def test_model_tag_policy_rejects_other_models_and_unprepared_tags(self):
        promotion = self.load('web-quickstart/promote_release.py')
        self.assertEqual(promotion.model_for_tag('simpaths-quickstart:uk-2019-20000-release-20260924-120000'),
                         'simpaths-quickstart-20000')
        for tag in ['another-model:uk-2019-20000-release-20260924-120000',
                    'simpaths-quickstart:uk-2019-10000-release-20260924-120000',
                    'simpaths-quickstart:prepared-20000', 'simpaths-readme:other']:
            self.assertIsNone(promotion.model_for_tag(tag))

    def test_release_executes_local_acceptance_and_promotion_and_records_provenance(self):
        release = self.load('web-quickstart/build_release.py')
        core = self.root / 'core'
        core.mkdir()
        (self.frontend / 'jasmine_web').mkdir()
        (self.frontend / 'jasmine_web/heap_policy.py').touch()
        # Copy just the shared offline promotion module; no coordinator checkout.
        shared_source = self.loader['load_web_tool']('release_images.py').__file__
        shared_destination = self.frontend / 'jasmine_web/deployment/release_images.py'
        shared_destination.parent.mkdir()
        shutil.copy2(shared_source, shared_destination)
        for name in ('singlerun.jar', 'license.txt', 'COPYRIGHT.md'):
            (self.repo / name).write_text('fixture')
        (self.repo / 'webserver.properties').write_text('allowDetailedDataAccess=true\n')
        packager = release.quickstart_packager(self.repo)
        profile = {'profile': packager.profile_for(20000), 'profile_id': packager.profile_id(20000)}
        migration = {'format_version': 1, 'principal': 'JASMINE_WEB_READER',
                     'source_database_sha256': 'before-db', 'database_sha256': 'after-db'}
        image = SimpleNamespace(id='sha256:base', tag=Mock())
        candidate = SimpleNamespace(id='sha256:candidate')
        client = SimpleNamespace(ping=Mock(), images=SimpleNamespace(
            get=Mock(side_effect=lambda tag: image if tag == 'prepared:20000' else candidate), remove=Mock()),
            containers=SimpleNamespace(create=Mock(side_effect=lambda identity:
                SimpleNamespace(id=identity, remove=Mock()))))
        manifest = {}
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            if command[:2] == ['docker', 'build']:
                manifest.update(json.loads((Path(command[-1]) / 'release.json').read_text()))
            return SimpleNamespace(returncode=0)

        def file(container, path):
            if path.endswith('profile.json'):
                return json.dumps(profile).encode()
            if path.endswith('query-access.json'):
                return json.dumps(migration).encode()
            if path.endswith('COPYRIGHT.md'):
                return b'fixture'
            if path.endswith('release.json'):
                return json.dumps(manifest).encode()
            raise AssertionError(path)

        out = self.root / 'evidence'
        with patch.dict(sys.modules, {'docker': SimpleNamespace(from_env=lambda: client)}), \
                patch.object(release, 'git', side_effect=lambda repo, *args: 'revision' if args[0] == 'rev-parse' else ''), \
                patch.object(release.subprocess, 'run', side_effect=run), \
                patch.object(release.shutil, 'disk_usage', return_value=SimpleNamespace(free=20*1024**3)), \
                patch.object(release, 'archive_file', side_effect=file), \
                patch.object(release, 'archive_content_hashes', side_effect=lambda c, p: {
                    'input/input.mv.db': 'before-db' if c.id == image.id else 'after-db', 'input/a.xlsx': 'unchanged'}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(release.main(['--core', str(core), '--frontend', str(self.frontend),
                '--population', '20000', '--output', str(out), '--work-root', str(self.root), '--promote']), 0)
        self.assertIn(['mvn', '-Djava.awt.headless=true', 'install'], commands)
        browser = next(c for c in commands if len(c) > 1 and c[1].endswith('run_browser_acceptance.py'))
        self.assertEqual(browser[1], str(self.repo / 'deploy/acceptance/run_browser_acceptance.py'))
        self.assertIn('--storage-check', browser)
        promote = next(c for c in commands if len(c) > 1 and c[1].endswith('promote_release.py'))
        self.assertEqual(promote[1], str(self.repo / 'deploy/web-quickstart/promote_release.py'))
        report = json.loads((out / 'status.json').read_text())
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['images'][0]['revisions']['release_tools'], 'revision')
        self.assertNotIn('coordinator', report['images'][0]['revisions'])
        self.assertEqual(report['images'][0]['base_reference'], 'prepared:20000')


if __name__ == '__main__':
    unittest.main()
