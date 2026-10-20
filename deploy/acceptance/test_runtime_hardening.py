"""(C) Copyright 2026, by Ross Richardson

Check exclusion guards, parent-layer provenance and preserved runtime settings.
@author ross richardson
"""
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from deploy.acceptance import runtime_hardening as h


class HardeningGuards(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.binary = self.root/'pebble'
        self.entry = self.root/'entrypoint'
        self.binary.write_text('fictional inherited executable\n')
        self.entry.write_text('exec "$@"\n')
        self.bin = self.root/'bin'
        self.bin.mkdir()
        self.query = self.bin/'dpkg-query'

    def execute(self, query_status=1):
        self.query.write_text(f'#!/bin/sh\nexit {query_status}\n')
        self.query.chmod(0o700)
        block = h.removal_block()
        script = block[block.index('RUN ')+4:block.index('# End audited')]
        script = script.replace(h.PEBBLE_HASH, hashlib.sha256(b'fictional inherited executable\n').hexdigest())
        script = script.replace(h.ENTRYPOINT_HASH, hashlib.sha256(b'exec "$@"\n').hexdigest())
        script = script.replace('/usr/bin/pebble', str(self.binary))
        script = script.replace('/__cacert_entrypoint.sh', str(self.entry))
        self.assertNotIn('/usr/bin/pebble', script)
        self.assertNotIn('/__cacert_entrypoint.sh', script)
        return subprocess.run(['/bin/sh', '-c', script],
            env=dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ['PATH']),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)

    def test_unowned_audited_binary_is_removed_and_reapplication_is_safe(self):
        self.assertEqual(self.execute().returncode, 0)
        self.assertFalse(self.binary.exists())
        self.assertEqual(self.execute().returncode, 0)
        self.assertTrue(self.entry.exists())

    def test_package_owned_or_unreadable_ownership_preserves_binary(self):
        for status in (0, 2, 127):
            with self.subTest(status=status):
                self.assertNotEqual(self.execute(status).returncode, 0)
                self.assertTrue(self.binary.exists())

    def test_changed_entrypoint_or_executable_stops_before_removal(self):
        for target in (self.entry, self.binary):
            original = target.read_bytes()
            target.write_text('unreviewed change\n')
            self.assertNotEqual(self.execute().returncode, 0)
            self.assertTrue(self.binary.exists())
            target.write_bytes(original)

    def test_symlink_is_rejected_without_removing_target(self):
        target = self.root/'different-file'
        self.binary.rename(target)
        self.binary.symlink_to(target)
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertTrue(self.binary.is_symlink())
        self.assertTrue(target.exists())


class HardeningProvenance(unittest.TestCase):
    def setUp(self):
        self.before = dict(RootFS=dict(Layers=['sha256:base', 'sha256:app']),
            Config=dict(User='10001:10001', Entrypoint=['/__cacert_entrypoint.sh'],
                        Cmd=['java', '-cp', 'app.jar', 'Main'], Env=['JAVA_OPTS='], WorkingDir='/app'))
        self.after = copy.deepcopy(self.before)
        self.after['RootFS']['Layers'].append('sha256:exclusion')

    def test_exact_parent_and_original_nonroot_launch_are_required(self):
        result = h.verify_lineage(self.before, self.after)
        self.assertEqual(result['runtime_config'], self.before['Config'])
        self.assertEqual(result['parent_layers'], self.before['RootFS']['Layers'])

    def test_replaced_reordered_or_extra_layers_are_rejected(self):
        for layers in (['sha256:different', 'sha256:app', 'sha256:exclusion'],
                       ['sha256:app', 'sha256:base', 'sha256:exclusion'],
                       self.before['RootFS']['Layers'],
                       self.after['RootFS']['Layers']+['sha256:another']):
            with self.subTest(layers=layers):
                changed = copy.deepcopy(self.after)
                changed['RootFS']['Layers'] = layers
                with self.assertRaises(ValueError):
                    h.verify_lineage(self.before, changed)

    def test_launch_limits_environment_and_entrypoint_changes_are_rejected(self):
        for key, value in (('User', 'root'), ('Entrypoint', ['/bin/sh']),
                           ('Env', ['JAVA_OPTS=-Xmx9g']), ('Cmd', ['different']),
                           ('WorkingDir', '/'), ('Healthcheck', {'Test': ['NONE']})):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.after)
                changed['Config'][key] = value
                with self.assertRaises(ValueError):
                    h.verify_lineage(self.before, changed)

    def test_legacy_parent_bookkeeping_is_not_a_runtime_setting(self):
        self.before['Config']['Image'] = 'legacy-parent'
        self.after['Config']['Image'] = 'qualified-parent'
        self.assertNotIn('Image', h.verify_lineage(self.before, self.after)['runtime_config'])

    def test_fresh_and_prepared_quickstart_keep_the_same_exclusion(self):
        import runpy
        package = runpy.run_path(str(h.ROOT/'deploy/web-quickstart/package_image.py'))
        template = (h.ROOT/'deploy/web-quickstart/Dockerfile').read_text()
        block = h.removal_block()
        derived = package['release_dockerfile'](template, 'fixture:prepared')
        self.assertIn(block, derived)
        from deploy.acceptance.isolated_refresh_storage import compact_dockerfile
        self.assertIn(block, compact_dockerfile(template))


class QualifiedBaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        child = dict(passed=True, promoted=False, quickstart=dict(status='passed', images=[
            dict(population=20000, image_id='sha256:'+'1'*64),
            dict(population=50000, image_id='sha256:'+'2'*64)]),
            interactive_builds={name:dict(image_id='sha256:'+str(i)*64)
                                for name, i in (('training', 3), ('user-data', 4))},
            acceptance_checks=[dict(name=name, passed=True) for name in (
                'training-none', 'training-population', 'training-tax', 'training-both', 'user-data')],
            interactive=dict(cleanup=True, private_rpc=dict(closed=True, errors=[])))
        self.child = self.root/'report.json'
        self.child.write_text(json.dumps(child))
        self.base = self.root/'base.json'
        self.save_base()

    def save_base(self, **changes):
        value = dict(format='simpaths.isolated-security-refresh.v1', passed=True,
            promoted=False, originals_unchanged=True, acceptance=str(self.root),
            acceptance_sha256=h.digest(self.child))
        value.update(changes)
        self.base.write_text(json.dumps(value))

    def test_incomplete_or_promoted_base_is_rejected(self):
        for changes in (dict(passed=False), dict(promoted=True), dict(originals_unchanged=False)):
            self.save_base(**changes)
            with self.assertRaises(ValueError):
                h.load_base(self.base)

    def test_changed_child_or_failed_cleanup_cannot_inherit_acceptance(self):
        original = self.child.read_bytes()
        self.child.write_text('{}')
        with self.assertRaises(ValueError):
            h.load_base(self.base)
        value = json.loads(original)
        value['interactive']['cleanup'] = False
        self.child.write_text(json.dumps(value))
        self.save_base()
        with self.assertRaises(ValueError):
            h.load_base(self.base)

    def test_dry_run_uses_qualified_ids_without_creating_output(self):
        output = self.root/'new'
        with redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(h.main(['--qualification', str(self.base), '--output', str(output), '--dry-run']), 0)
        self.assertFalse(output.exists())
        self.assertEqual(set(json.loads(captured.getvalue())['images']), set(h.ROLES))


if __name__ == '__main__':
    unittest.main()
