"""(C) Copyright 2026, by Ross Richardson

Verify SimPaths guides and publication choices passed to shared README tooling.

@author ross richardson
"""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('simpaths_readmes', Path(__file__).with_name('update_model_readmes.py'))
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class SimPathsReadmeTests(unittest.TestCase):
    def test_selected_checkout_supplies_all_four_guides(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            shutil.copytree(update.ROOT / 'deploy', repo / 'deploy',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            for name in ('quickstart', 'training', 'user-data'):
                path = repo / f'deploy/web-{name}/README.md'
                path.write_text(path.read_text() + f'\nSelected {name} checkout guide.\n')
            guides = update.guides(repo)
            self.assertEqual(tuple(guides), update.PROFILES)
            for key, content in guides.items():
                name = 'quickstart' if 'quickstart' in key else ('training' if 'training' in key else 'user-data')
                self.assertIn(f'Selected {name} checkout guide.'.encode(), content)
                if 'quickstart' in key:
                    self.assertIn(('20,000' if '20000' in key else '50,000').encode(), content)

    def test_wrapper_retains_simpaths_selection_aliases_and_preview_default(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                shared = Mock()
                shared.update_readmes.return_value = 0
                guides = dict.fromkeys(update.PROFILES, b'# SimPaths guide')
                argv = ['--repo', '/chosen-model', '--frontend', '/chosen-web',
                        '--catalogue', '/chosen-catalogue.json', '--output', '/evidence']
                if apply:
                    argv.append('--apply')
                with patch.object(update, 'guides', return_value=guides) as render, \
                        patch.object(update, 'load_web_tool', return_value=shared) as load:
                    self.assertEqual(update.main(argv), 0)
                render.assert_called_once_with(Path('/chosen-model'))
                load.assert_called_once_with('readme_images.py', Path('/chosen-web'))
                shared.update_readmes.assert_called_once_with(
                    Path('/chosen-catalogue.json'), guides, apply=apply, output=Path('/evidence'),
                    output_root=Path.home() / 'simpaths-benchmarks', image_repository='simpaths-readme',
                    retained_aliases=('simpaths-interactive:uk-training', 'simpaths-interactive:uk-user-data'))


if __name__ == '__main__':
    unittest.main()
