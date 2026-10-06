"""(C) Copyright 2026, by Ross Richardson

Regression coverage for isolated CSV verification and private bounded responses.
@author ross richardson
"""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from .artifacts import ArtifactError
from .csv_validation import validate_annual_csv


class CSVValidationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.paths=[self.root/name for name in ('Person.csv','BenefitUnit.csv')]
        self.good='run,time,value\nfictional,2019,1\nfictional,2020,2\n'
        for path in self.paths:path.write_text(self.good)

    def validate(self):return validate_annual_csv(self.paths,{2019,2020})

    def test_complete_annual_files_with_one_shared_run_are_accepted(self):
        self.validate()
        for path in self.paths:path.write_text(self.good.replace('fictional','"literal <b>label</b>"'))
        self.validate()

    def test_headers_and_incomplete_rows_are_rejected_by_the_actual_child(self):
        for content,message in [('', 'Invalid scientific CSV header'),
                ('run,time,time\nfictional,2019,2019\n','Invalid scientific CSV header'),
                ('run,time,value\nfictional,2019\n','Truncated scientific CSV'),
                ('run,time\nfictional,2019,extra\n','Truncated scientific CSV')]:
            with self.subTest(content=content):
                self.paths[0].write_text(content)
                with self.assertRaisesRegex(ArtifactError,message):self.validate()

    def test_nonfinite_fractional_invalid_and_wrong_years_are_rejected(self):
        for year in ('NaN','Infinity','2019.5','2021','not-a-year'):
            self.paths[0].write_text(self.good.replace('2019',year))
            with self.subTest(year=year),self.assertRaises(ArtifactError):self.validate()
        self.paths[0].write_text('run,time\nfictional,2019\n')
        with self.assertRaisesRegex(ArtifactError,'missing years'):self.validate()

    def test_run_identity_is_shared_across_both_files(self):
        self.paths[1].write_text(self.good.replace('fictional','another'))
        with self.assertRaisesRegex(ArtifactError,'mixes native runs'):self.validate()
        self.paths[1].write_text(self.good.replace('fictional',''))
        with self.assertRaisesRegex(ArtifactError,'mixes native runs'):self.validate()

    def test_missing_linked_and_special_files_fail_without_exposing_paths(self):
        self.paths[0].unlink()
        with self.assertRaisesRegex(ArtifactError,'could not be read'):self.validate()
        self.paths[0].symlink_to(self.paths[1])
        with self.assertRaisesRegex(ArtifactError,'could not be read'):self.validate()
        self.paths[0].unlink();os.mkfifo(self.paths[0])
        with self.assertRaisesRegex(ArtifactError,'could not be read'):self.validate()

    def test_corrupt_or_unexpected_child_responses_do_not_become_receipts_or_diagnostics(self):
        private='private raw row and connection credentials'
        for returncode,stdout in [(1,''),(0,'not json'),(0,json.dumps({'ok':1})),
                (0,json.dumps({'ok':True,'rows':[private]})),(0,json.dumps({'error':private})),
                (0,'x'*2049)]:
            result=SimpleNamespace(returncode=returncode,stdout=stdout,stderr=private)
            with self.subTest(returncode=returncode,stdout=stdout),patch(
                    'deploy.multirun.csv_validation.subprocess.run',return_value=result),self.assertRaises(ArtifactError) as raised:
                self.validate()
            self.assertEqual(str(raised.exception),'Scientific output could not be read')


if __name__=='__main__':unittest.main()
