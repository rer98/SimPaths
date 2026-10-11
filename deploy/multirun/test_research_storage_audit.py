"""(C) Copyright 2026, by Ross Richardson

Streaming retained-output accounting, immutable completion and scoped trial guards.
@author ross richardson
"""
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.multirun import research_storage_audit as audit
from deploy.multirun import research_comparison as comparison
from deploy.multirun import test_research_comparison as fixtures
from deploy.multirun.artifacts import ArtifactError, fingerprint, inventory
from deploy.multirun.resource_policy import DEFAULT_POLICY


class AnnualStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root/'Person.csv'

    def read(self, body, *, end=2020, expected=None):
        self.path.write_bytes(body)
        return audit.annual_csv(self.path, expected or fingerprint(self.path), 2019, end)

    def test_original_utf8_crlf_and_multiline_record_bytes_reconcile_exactly(self):
        header = b'\xef\xbb\xbfrun,time,private_value\r\n'
        first = '1,2019.0,"private comma, and £"\r\n'.encode()
        second = b'1,2020,"private\nquoted ""value"""\r\n'
        body = header+first+second
        result = self.read(body)
        self.assertEqual(result['years'], {'2019':dict(rows=1, bytes=len(first)),
                                          '2020':dict(rows=1, bytes=len(second))})
        self.assertEqual(result['header_bytes'], len(header))
        self.assertEqual(result['bytes'], len(body))
        self.assertEqual(result['sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual(result['rows'], 2)
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(self.path.read_bytes(), body)

    def test_hash_mismatch_missing_year_and_invalid_annual_times_are_rejected(self):
        body = b'time,value\n2019,1\n2020,2\n'
        for value in ('NaN', 'Infinity', '2019.5', '2018', '2071', 'x', '9'*33):
            with self.subTest(value=value), self.assertRaises(ArtifactError):
                self.read(('time,value\n2019,1\n'+value+',2\n').encode())
        with self.assertRaises(ArtifactError):self.read(b'time,value\n2019,1\n')
        with self.assertRaises(ArtifactError):
            self.read(body, expected=dict(bytes=len(body), sha256='0'*64))

    def test_empty_duplicate_header_and_malformed_records_are_rejected(self):
        for body in (b'', b'time,time\n2019,2019\n2020,2020\n', b'value\n1\n2\n',
                     b'time,value\n2019\n2020,2\n', b'time,value\n2019,"unclosed\n',
                     b'time,value\n2019,1\n2020,2\n\n', b'time,value\n2019,\xff\n2020,2\n'):
            with self.subTest(body=body), self.assertRaises(ArtifactError):self.read(body)

    def test_logical_record_limit_covers_multiple_short_physical_lines(self):
        body = b'time,value\n2019,"'+b'x\n'*100+b'"\n2020,2\n'
        with patch.object(audit, 'MAX_RECORD_BYTES', 128), self.assertRaises(ArtifactError):self.read(body)

    def test_symlink_same_size_mutation_and_file_replacement_are_rejected(self):
        body = b'time,value\n2019,1\n2020,2\n';self.path.write_bytes(body)
        expected = fingerprint(self.path)
        link = self.root/'linked.csv';link.symlink_to(self.path)
        with self.assertRaises(ArtifactError):audit.annual_csv(link, expected, 2019, 2020)
        with self.assertRaises(ArtifactError):
            with audit.stable_file(self.path, expected):self.path.write_bytes(body.replace(b',1', b',9'))
        self.path.write_bytes(body)
        replacement = self.root/'replacement';replacement.write_bytes(body)
        with self.assertRaises(ArtifactError):
            with audit.stable_file(self.path, expected):replacement.replace(self.path)


class RetainedStorageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResearchComparisonTests()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.receipt = self.fixture.receipt
        self.folder = self.fixture.paths[0]
        self.proof = self.folder/'model-proof'
        self.output = self.proof/'retained-output'
        self.run = self.output/'fictional_606_0'
        for name in audit.OUTPUT_CSV_FILES:
            (self.run/'csv'/name).write_text('time,private_value\n'+''.join(
                f'{year},sensitive fictional value\n' for year in range(2019, 2071)))
        self.record = json.loads((self.proof/'report.json').read_text())
        self.record['peak_workspace_bytes'] = 6*audit.GIB
        self.publish()

    def publish(self):
        manifest = inventory(self.output)
        (self.proof/'output-manifest.json').write_text(json.dumps(manifest))
        self.record['output_bytes'] = sum(v['bytes'] for v in manifest.values())
        (self.proof/'report.json').write_text(json.dumps(self.record))

    def source(self, *, repetitions=1):
        value = comparison.retained_run(self.folder, self.receipt, 'baseline',
                                       check_files=False, repetitions=repetitions)
        value['outer_report'] = fingerprint(self.folder/'report.json')
        return value

    def test_all_annual_files_options_sizes_hashes_and_original_contents_are_preserved(self):
        before = inventory(self.output)
        result = audit.audit_source(self.source())
        self.assertEqual(result['bytes'], sum(f['bytes'] for f in before.values()))
        self.assertEqual(result['runs'][0]['seed'], '606')
        self.assertEqual(sum('years' in f for f in result['files'].values()), 9)
        self.assertNotIn('sensitive fictional value', json.dumps(result))
        self.assertEqual(inventory(self.output), before)

    def test_untracked_files_missing_annual_files_and_unreclaimed_inputs_are_rejected(self):
        extra = self.run/'extra';source = self.source();extra.write_text('extra')
        with self.assertRaises(ArtifactError):audit.audit_source(source)
        extra.unlink()
        annual = self.run/'csv/HealthStatistics.csv';annual.unlink();self.publish()
        with self.assertRaises(ArtifactError):audit.audit_source(self.source())
        annual.write_text('time,value\n'+''.join(f'{year},1\n' for year in range(2019, 2071)))
        copied = self.run/'input/input.mv.db';copied.write_bytes(b'copied input');self.publish()
        with self.assertRaises(ArtifactError):audit.audit_source(self.source())

    def test_completion_evidence_and_already_read_files_cannot_change_mid_audit(self):
        source = self.source()
        original = audit.annual_csv
        changed = False
        def read(*args):
            nonlocal changed
            result = original(*args)
            if not changed:
                Path(args[0]).write_bytes(Path(args[0]).read_bytes().replace(b'fictional', b'altered!'))
                changed = True
            return result
        with patch.object(audit, 'annual_csv', side_effect=read), self.assertRaises(ArtifactError):
            audit.audit_source(source)
        self.publish()
        source = self.source();self.record['changed'] = True;self.publish()
        with self.assertRaises(ArtifactError):audit.audit_source(source)

    def test_repeated_completion_requires_every_original_seed_and_keeps_comparison_one_seed_default(self):
        second = self.output/'fictional_607_1'
        from shutil import copytree
        copytree(self.run, second)
        (second/'input/options.txt').write_text('randomSeedIfFixed: 607\n')
        self.record['settings'] = {**self.record['settings'], 'repetitions':2, 'max_attempts':1}
        self.record['verified_repetitions'].append(dict(seed='607', fingerprint='b'*64))
        config = comparison.configuration(self.receipt, self.record['settings'])
        (self.proof/'configuration.json').write_text(json.dumps(config.as_dict()))
        self.publish()
        with self.assertRaises(ArtifactError):self.source()
        result = audit.audit_source(self.source(repetitions=2))
        self.assertEqual([r['seed'] for r in result['runs']], ['606', '607'])
        self.record['verified_repetitions'][1]['seed'] = '608';self.publish()
        with self.assertRaises(ArtifactError):self.source(repetitions=2)

    def plan(self, sources=None, repetitions=2, free=10*audit.GIB):
        source = sources or [audit.audit_source(self.source())]
        # Fixture represents the measured long-run size without allocating GiB of test data.
        source = deepcopy(source)
        source[0]['runs'][0].update(bytes=3550*audit.MIB, allocated_bytes=3551*audit.MIB)
        with patch.object(audit.shutil, 'disk_usage', return_value=SimpleNamespace(free=free)):
            return audit.scoped_plan(self.receipt, source, repetitions=repetitions,
                workspace=dict(prepared=self.fixture.fixture.prepared, trial_output=self.root/'trial'),
                frontend='/fictional/frontend', python='/fictional/python')

    def test_trial_scaling_frozen_settings_original_seeds_and_space_guard_use_same_allowance(self):
        original = deepcopy(DEFAULT_POLICY)
        plan = self.plan()
        self.assertEqual(plan['allocation']['storage_mib'], 12288)
        self.assertEqual(plan['resource_policy']['simulation']['storage'], dict(setup_mib=4096, per_repetition_mib=4096))
        self.assertEqual(plan['settings']['storage_mib'], 12288)
        self.assertEqual(plan['configuration']['seed_plan']['seeds'], ['606', '607'])
        self.assertEqual(plan['settings']['max_attempts'], 1)
        self.assertEqual(plan['preflight']['required_free_disk_bytes'], 15*audit.GIB)
        self.assertEqual(plan['preflight']['additional_free_disk_bytes_needed'], 5*audit.GIB)
        self.assertFalse(plan['preflight']['disk_ready'])
        self.assertEqual(DEFAULT_POLICY, original)
        self.assertIn('--storage-mib', plan['command'])
        self.assertNotIn('--execute-proof', plan['command'])
        self.assertTrue(plan['candidate_only'])
        self.assertEqual(self.plan(free=15*audit.GIB)['preflight']['additional_free_disk_bytes_needed'], 0)
        self.assertEqual(self.plan(repetitions=10)['settings']['storage_mib'], 45056)

    def test_projections_reject_untested_horizons_collectors_empty_sources_and_boolean_counts(self):
        source = audit.audit_source(self.source())
        for field, value in (('end_year', 2026), ('population', 50000)):
            changed = deepcopy(source);changed['configuration']['common'][field] = value
            with self.subTest(field=field), self.assertRaises(ArtifactError):self.plan([changed])
        changed = deepcopy(source)
        collectors = changed['configuration']['run_sets'][0]['collector_args']
        collectors['exportToCSV'] = not collectors['exportToCSV']
        with self.assertRaises(ArtifactError):self.plan([source, changed])
        with self.assertRaises(ArtifactError):self.plan(repetitions=True)
        with self.assertRaises(ArtifactError):
            audit.scoped_plan(self.receipt, [], repetitions=2, workspace={}, frontend='', python='')

    def test_full_audit_writes_only_private_evidence_and_no_model_execution(self):
        destination = self.root/'audit'
        with redirect_stdout(io.StringIO()), patch('subprocess.call') as run:
            # Ten tiny fictional repetitions clear the private driver's 6 GiB
            # minimum; measured real research outputs yield 12 GiB for two.
            result = audit.audit(self.fixture.fixture.prepared, [self.folder], destination, repetitions=10)
        self.assertTrue(result['read_only'])
        self.assertEqual(result['simulations_started'], 0)
        run.assert_not_called()
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        self.assertEqual((destination/'report.json').stat().st_mode & 0o777, 0o600)
        self.assertTrue((destination/'COPYRIGHT.md').is_file())
        with self.assertRaises(ArtifactError):audit.audit(self.fixture.fixture.prepared, [self.folder], destination)


if __name__ == '__main__':unittest.main()
