"""(C) Copyright 2026, by Ross Richardson

Verify summary replay provenance, bounded decoding and private immutable output.
Tiny fictional annual rows exercise the same checks as retained research data.
@author ross richardson
"""
import json
import math
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy.multirun import visualiser_capacity_fixture as fixture


class CapacityFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / 'archive'; self.archive.mkdir()
        self.build = self.root / 'build'; self.build.mkdir()
        self.output = self.root / 'fixture'
        self.code = {'calculation.cjs': b'fictional calculation', 'runner.cjs': b'fictional runner'}
        for name, body in self.code.items():
            (self.build / name).write_bytes(body)
        self.report = dict(passed=True, source_unchanged=True,
            backend=dict(build=dict(files={n:fixture._fingerprint(b)['sha256'] for n,b in self.code.items()})))
        self.write(self.archive / 'report.json', self.report)
        self.rows = {}
        for index in range(2):
            self.rows[index] = [dict(year=year, scenario='baseline' if index == 0 else 'scenario_1',
                run='606', variable='Hours worked', variable_value='Continuous Mean',
                stratifier='Overall', stratifier_value='Overall', metric_type='mean',
                n=100000, metric_value=17.4) for year in fixture.ANNUAL_YEARS]
            self.save_summary(index)
        self.decoder = patch.object(fixture, '_decode', side_effect=lambda source, maximum:source.read(maximum + 1))
        self.decoder.start(); self.addCleanup(self.decoder.stop)

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value))

    def save_summary(self, index):
        body = json.dumps(self.rows[index]).encode()
        source = self.archive / f'configuration-{index}-run-0.json.zst'
        source.write_bytes(body)  # Identity decoder keeps the fixture entirely in stdlib.
        self.write(Path(str(source) + '.receipt.json'), dict(
            archive=fixture._fingerprint(body), original=fixture._fingerprint(body),
            decoded_hash_verified=True, archive_path='/untrusted/ignored/path',
            original_path='/another/ignored/path'))

    def prepare(self, **kwargs):
        return fixture.prepare_fixture(self.archive, self.build, self.output, **kwargs)

    def test_replays_ten_labels_without_copying_each_seed_or_alternative(self):
        manifest = self.prepare(repetitions=10, alternatives=3)
        self.assertEqual(manifest['seeds'], [str(900000001+i) for i in range(10)])
        self.assertEqual(len(manifest['configurations']), 4)
        self.assertEqual(manifest['configurations'][3]['role'], 'scenario_3')
        self.assertEqual({run['summary'] for c in manifest['configurations'] for run in c['runs']},
                         {'baseline.json', 'alternative.json'})
        self.assertEqual({p.name for p in self.output.iterdir()},
                         {'baseline.json', 'alternative.json', 'manifest.json', 'COPYRIGHT.md'})
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads((self.output/'baseline.json').read_text()), self.rows[0])
        self.assertIn('not independent', ' '.join(manifest['limitations']))
        self.assertIn('Raw-output parsing', ' '.join(manifest['limitations']))

    def test_existing_output_is_never_modified(self):
        self.output.mkdir(); sentinel = self.output/'keep'; sentinel.write_text('keep')
        with self.assertRaises(fixture.FixtureError): self.prepare()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_failed_publication_removes_only_its_new_directory(self):
        real_write = fixture._write
        def fail_manifest(path, body):
            if path.name == 'manifest.json':
                raise OSError('fictional write failure')
            real_write(path, body)
        with patch.object(fixture, '_write', side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, 'fictional'):
                self.prepare()
        self.assertFalse(self.output.exists())
        self.assertTrue((self.archive/'report.json').exists())
        self.assertTrue((self.build/'runner.cjs').exists())

    def test_source_symlinks_and_nested_destination_are_rejected(self):
        original = self.archive/'report.json'; target = self.root/'original.json'
        original.rename(target); original.symlink_to(target)
        with self.assertRaises(fixture.FixtureError): self.prepare()
        original.unlink(); target.rename(original)
        self.output = self.archive/'output'
        with self.assertRaises(fixture.FixtureError): self.prepare()

    def test_changed_archive_decoded_receipt_build_and_incomplete_report_fail_closed(self):
        operations = (
            lambda: (self.archive/'configuration-0-run-0.json.zst').write_bytes(b'changed'),
            lambda: (self.build/'runner.cjs').write_bytes(b'changed'),
            lambda: self.write(self.archive/'report.json', dict(self.report, passed=False)),
            lambda: self.write(self.archive/'report.json', dict(self.report, source_unchanged=False)),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self.write(self.archive/'report.json', self.report)
                (self.build/'runner.cjs').write_bytes(self.code['runner.cjs']); self.save_summary(0)
                operation()
                with self.assertRaises(fixture.FixtureError): self.prepare()
                self.assertFalse(self.output.exists())
        self.write(self.archive/'report.json', self.report)
        self.save_summary(0)
        receipt = self.archive/'configuration-0-run-0.json.zst.receipt.json'
        value = json.loads(receipt.read_text()); value['original']['sha256'] = '0'*64
        self.write(receipt, value)
        with self.assertRaises(fixture.FixtureError): self.prepare()

    def test_missing_file_and_oversized_receipt_are_rejected_before_output(self):
        source = self.archive/'configuration-1-run-0.json.zst'
        source.unlink()
        with self.assertRaises(OSError): self.prepare()
        self.save_summary(1)
        receipt = Path(str(source)+'.receipt.json'); value = json.loads(receipt.read_text())
        value['original']['bytes'] = fixture.SUMMARY_LIMIT + 1; self.write(receipt, value)
        with self.assertRaises(fixture.FixtureError): self.prepare()
        self.assertFalse(self.output.exists())

    def test_wrong_seed_role_year_schema_and_nonfinite_values_are_rejected(self):
        changes = ({'run':'607'}, {'scenario':'scenario_2'}, {'year':2018},
                   {'extra':'raw'}, {'metric_value':math.inf}, {'n':True}, {'metric_value':True})
        pristine = dict(self.rows[0][0])
        for change in changes:
            with self.subTest(change=change):
                self.rows[0][0] = dict(pristine, **change); self.save_summary(0)
                with self.assertRaises(fixture.FixtureError): self.prepare()
                self.assertFalse(self.output.exists())
        self.rows[0] = self.rows[0][1:]; self.save_summary(0)
        with self.assertRaises(fixture.FixtureError): self.prepare()

    def test_duplicate_json_keys_and_unverified_decode_are_rejected(self):
        (self.archive/'report.json').write_text('{"passed":true,"passed":false}')
        with self.assertRaises(fixture.FixtureError): self.prepare()
        self.write(self.archive/'report.json', self.report)
        receipt = self.archive/'configuration-0-run-0.json.zst.receipt.json'
        value = json.loads(receipt.read_text()); value['decoded_hash_verified'] = False
        self.write(receipt, value)
        with self.assertRaises(fixture.FixtureError): self.prepare()

    def test_count_bounds_reject_booleans_and_invalid_values(self):
        for kwargs in ({'repetitions':True}, {'repetitions':0}, {'repetitions':11},
                       {'alternatives':True}, {'alternatives':0}, {'alternatives':4}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(fixture.FixtureError): self.prepare(**kwargs)

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is not installed')
    def test_native_decoder_enforces_actual_output_bound_and_reaps_failure(self):
        self.decoder.stop()
        compressed = subprocess.check_output(['zstd', '-q', '-c'], input=b'x'*4096)
        path = self.root/'tiny.zst'; path.write_bytes(compressed)
        with path.open('rb') as source:
            self.assertEqual(fixture._decode(source, 4096), b'x'*4096)
        with path.open('rb') as source:
            with self.assertRaisesRegex(fixture.FixtureError, 'size bound'):
                fixture._decode(source, 1024)
        path.write_bytes(b'not zstd')
        with path.open('rb') as source:
            with self.assertRaisesRegex(fixture.FixtureError, 'failed'):
                fixture._decode(source, 4096)


if __name__ == '__main__':
    unittest.main()
