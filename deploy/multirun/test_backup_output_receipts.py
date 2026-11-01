"""(C) Copyright 2026, by Ross Richardson

Fictional proof that backup reuses only exact previously validated output bytes.
No simulations, native databases, containers, private inputs or email are used.
@author ross richardson
"""
import shutil
import unittest
from unittest.mock import patch

from .artifacts import ArtifactError
from .backup_output_receipts import verify_completed_output
from .queue_adapter import result_catalogue
from . import test_queue_adapter as fixtures


class BackupOutputReceiptTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.QueueAdapterTests();self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp();self.fixture.outputs()
        self.lease=self.fixture.lease;self.lease.phase='finished'
        self.work=self.fixture.work
        self.original=result_catalogue(self.lease,self.work)
        self.receipts=[dict(actual_seed=row['seed'],output_fingerprint=row['fingerprint'])
                       for row in self.original]

    def verify(self,receipts=None):
        return verify_completed_output(self.lease,self.work,
            self.receipts if receipts is None else receipts)

    def test_exact_previously_validated_bytes_reuse_all_receipts_without_reparsing(self):
        with patch('deploy.multirun.csv_validation.validate_annual_csv',side_effect=AssertionError('Must not reparse')):
            actual=self.verify()
        self.assertEqual(actual,self.original)
        self.assertEqual([row['seed'] for row in actual],self.lease.specification['seeds'])

    def test_changed_values_headers_years_and_truncation_are_denied_without_reparsing(self):
        path=self.work/'output/606/csv/Person.csv';original=path.read_bytes()
        variants=(original.replace(b',1,2',b',1,9'),original.replace(b'run,time',b'bad,time'),
                  original.replace(b'2019',b'2018'),original[:-4],b'')
        with patch('deploy.multirun.csv_validation.validate_annual_csv',side_effect=AssertionError('Must not reparse')):
            for content in variants:
                with self.subTest(content=content):
                    path.write_bytes(content)
                    with self.assertRaisesRegex(ArtifactError,'differs from its PostgreSQL receipts'):self.verify()
                    path.write_bytes(original)

    def test_any_additional_csv_also_changes_the_recorded_fingerprint(self):
        path=self.work/'output/606/csv/new-output.csv';path.write_text('Changed inventory')
        with self.assertRaisesRegex(ArtifactError,'differs from its PostgreSQL receipts'):self.verify()

    def test_changed_options_unknown_metadata_and_seeds_are_denied(self):
        path=self.work/'output/606/input/options.txt';original=path.read_text()
        variants=(original.replace('savingRate: 0.04','savingRate: 0.07'),
                  original+'\nunrecordedMetadata: changed',
                  original.replace('randomSeedIfFixed: 606','randomSeedIfFixed: 607'),
                  original+'\nrandomSeedIfFixed: 606')
        for content in variants:
            with self.subTest(content=content):
                path.write_text(content)
                with self.assertRaises(ArtifactError):self.verify()
                path.write_text(original)

    def test_missing_extra_duplicate_reordered_and_unfinished_receipts_are_rejected_before_file_reads(self):
        variants=([],self.receipts[:-1],self.receipts+[self.receipts[0]],
            [self.receipts[0]]*len(self.receipts),list(reversed(self.receipts)),
            [dict(row,output_fingerprint=None) for row in self.receipts],
            [dict(row,output_fingerprint='x'*64) for row in self.receipts],
            [dict(row,output_fingerprint=True) for row in self.receipts],
            [dict(row,untrusted='extra') for row in self.receipts],
            [dict(row,actual_seed=None) for row in self.receipts])
        with patch('deploy.multirun.backup_output_receipts._catalogue_outputs',side_effect=AssertionError('No file reads')):
            for receipts in variants:
                with self.subTest(receipts=receipts),self.assertRaisesRegex(ArtifactError,'successful output receipts'):
                    self.verify(receipts)

    def test_active_attempt_and_mismatched_frozen_seed_plan_are_refused(self):
        self.lease.phase='running'
        with self.assertRaisesRegex(ArtifactError,'finished successful'):self.verify()
        self.lease.phase='finished';self.lease.specification['seeds']=['606']
        with self.assertRaisesRegex(ArtifactError,'seed plan differs'):self.verify()

    def test_missing_repetition_or_required_csv_is_not_treated_as_success(self):
        person=self.work/'output/606/csv/Person.csv';person.unlink()
        with self.assertRaisesRegex(ArtifactError,'Required scientific output missing'):self.verify()
        shutil.rmtree(self.work/'output/606')
        with self.assertRaisesRegex(ArtifactError,'Missing or extra completed repetitions'):self.verify()

    def test_symlinked_source_is_rejected(self):
        person=self.work/'output/606/csv/Person.csv';outside=self.fixture.root/'outside.csv'
        person.rename(outside);person.symlink_to(outside)
        with self.assertRaisesRegex(ArtifactError,'Symlink'):self.verify()

    def test_ordinary_completion_and_download_still_parse_scientific_rows(self):
        path=self.work/'output/606/csv/Person.csv';path.write_text('run,time,id\nx,2019,1\n')
        with self.assertRaisesRegex(ArtifactError,'missing years'):self.fixture.validate()
        with self.assertRaisesRegex(ArtifactError,'missing years'):result_catalogue(self.lease,self.work)


if __name__=='__main__':unittest.main()
