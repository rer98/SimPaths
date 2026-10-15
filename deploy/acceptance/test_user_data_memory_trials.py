"""(C) Copyright 2026, by Ross Richardson
Guard isolated preparation comparisons against changed images and incomplete evidence.
@author ross richardson
"""
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from deploy.acceptance import run_user_data_memory_trials as trial


class MemoryTrialTests(unittest.TestCase):
    def test_images_differ_only_in_the_explicit_worker_heap(self):
        image = 'sha256:'+'a'*64
        smaller = trial.image_dockerfile(image, 3072)
        larger = trial.image_dockerfile(image, 4096)
        self.assertEqual(smaller.replace('3072', '4096'), larger)
        self.assertIn('FROM '+image+'\n', smaller)
        for invalid in ('candidate:latest', image+'\nRUN true'):
            with self.assertRaises(ValueError):
                trial.image_dockerfile(invalid, 3072)
        with self.assertRaises(ValueError):
            trial.image_dockerfile(image, 2048)

    def test_command_cannot_accidentally_run_training_or_promote(self):
        command = trial.trial_command(Path('/web'), 'sha256:'+'b'*64, 4096, Path('/trial'))
        self.assertIn('--memory-trial', command)
        self.assertEqual(command[command.index('--profile')+1], 'user-data')
        self.assertEqual(command[command.index('--preparation-heap-mib')+1], '4096')
        self.assertNotIn('--training-image', command)
        self.assertNotIn('--promote', command)

    def test_old_or_changed_jar_cannot_silently_test_the_default_twice(self):
        def jar_content(worker):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as jar:
                jar.writestr(trial.WORKER_CLASS, worker)
            return stream.getvalue()
        good = jar_content(b'SIMPATHS_PREPARATION_HEAP_MIB')
        trial.verify_jar(good, hashlib.sha256(good).hexdigest())
        with self.assertRaisesRegex(ValueError, 'current local'):
            trial.verify_jar(good, '0'*64)
        old = jar_content(b'hardcoded old heap')
        with self.assertRaisesRegex(ValueError, 'override is missing'):
            trial.verify_jar(old, hashlib.sha256(old).hexdigest())

    def test_a_pass_without_verified_cleanup_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'report.json'
            for evidence in ({}, {'passed': True}, {'passed': True, 'cleanup': False},
                             {'passed': 1, 'cleanup': True}):
                path.write_text(json.dumps(evidence))
                with self.assertRaises(RuntimeError):
                    trial.require_passed(path)
            path.write_text(json.dumps({'passed': True, 'cleanup': True}))
            self.assertTrue(trial.require_passed(path)['passed'])


if __name__ == '__main__':
    unittest.main()
