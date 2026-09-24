"""(C) Copyright 2026, by Ross Richardson

Verify SimPaths image selection and delegation to the shared deployment builder.

@author ross richardson
"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('simpaths_local_images', Path(__file__).with_name('build_local_images.py'))
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class SimPathsBuildTests(unittest.TestCase):
    def test_profiles_paths_tags_and_options_are_forwarded(self):
        for profile, expected in [('both', build.PROFILES), ('training', ('training',)),
                                  ('user-data', ('user-data',))]:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as root:
                repo = Path(root)
                for name in build.PROFILES:
                    packager = repo / f'deploy/web-{name}/package_image.py'
                    packager.parent.mkdir(parents=True)
                    packager.touch()
                (repo / 'singlerun.jar').touch()
                shared = Mock()
                shared.ImageBuild.side_effect = lambda **kwargs: kwargs
                shared.build_images.return_value = 0
                with patch.object(build, 'load_web_tool', return_value=shared) as load:
                    result = build.main(['--repo', root, '--frontend', '/chosen-web',
                                         '--profile', profile, '--work-root', root,
                                         '--min-free-gib', '9', '--docker-storage-path', '/',
                                         '--prune-build-cache'])
                self.assertEqual(result, 0)
                load.assert_called_once_with('local_images.py', Path('/chosen-web'))
                jobs = shared.build_images.call_args.args[0]
                self.assertEqual([job['name'] for job in jobs], list(expected))
                for job in jobs:
                    name = job['name']
                    self.assertEqual(job['image'], f'simpaths-interactive:uk-{name}')
                    self.assertEqual(job['package_command'],
                                     (sys.executable, str(repo / f'deploy/web-{name}/package_image.py')))
                self.assertEqual(shared.build_images.call_args.kwargs, dict(
                    work_root=repo, min_free_gib=9, docker_storage_path=Path('/'), prune_build_cache=True))

    def test_missing_jar_rejected_before_loading_shared_tools(self):
        with tempfile.TemporaryDirectory() as root:
            packager = Path(root) / 'deploy/web-training/package_image.py'
            packager.parent.mkdir(parents=True)
            packager.touch()
            with patch.object(build, 'load_web_tool') as load:
                with self.assertRaises(SystemExit):
                    build.main(['--repo', root, '--profile', 'training'])
                load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
