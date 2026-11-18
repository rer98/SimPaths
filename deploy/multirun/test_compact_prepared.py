"""(C) Copyright 2026, by Ross Richardson
Validate immutable compaction revisions, unchanged provenance and failure cleanup.
@author ross richardson
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import compact_prepared as compaction
from .artifacts import ArtifactError, fingerprint, inventory
from .prepared_dataset import check_compaction, verify_snapshot
from . import test_prepared_dataset as fixture


class CompactionRevisionTests(unittest.TestCase):
    setUp = fixture.PreparedDatasetTests.setUp
    dataset = fixture.PreparedDatasetTests.dataset

    def compact(self, candidate):
        database = candidate/'input/input.mv.db'
        before = fingerprint(database)
        database.write_bytes(b'compact')
        after = fingerprint(database)
        return dict(format_version=1, passed=True, source_database_sha256=before['sha256'],
            database_sha256=after['sha256'], logical_sql_sha256='c'*64, original_bytes=before['bytes'],
            compacted_bytes=after['bytes'], logical_contents_preserved=True)

    def test_new_revision_preserves_original_model_files_profile_and_parameter_requirements(self):
        source, original = self.dataset()
        before = inventory(source)
        with patch.object(compaction, 'run_compactor', side_effect=self.compact):
            target = self.root/'compact'; value = compaction.compact_revision(source, target)
        self.assertEqual(inventory(source), before)
        self.assertNotEqual(value['revision'], original['revision'])
        self.assertEqual(value['identity']['model'], original['identity']['model'])
        self.assertEqual(value['identity']['quickstart_profile'], original['identity']['quickstart_profile'])
        verify_snapshot(target, value); verify_snapshot(source, original)
        for name in ('input/input.mv.db','model.jar','receipt.json'):
            self.assertEqual((target/name).stat().st_mode & 0o777, 0o400)
        self.assertFalse(list(self.root.glob('.compact-revision-*')))
        with self.assertRaises(ArtifactError): compaction.compact_revision(source,target)

    def test_unexpected_input_change_refuses_publication_and_keeps_original(self):
        source, _ = self.dataset(); before = inventory(source)
        def wrong(candidate):
            result = self.compact(candidate)
            (candidate/'input/EUROMODpolicySchedule.xlsx').write_bytes(b'changed')
            return result
        with patch.object(compaction, 'run_compactor', side_effect=wrong):
            with self.assertRaises(ArtifactError): compaction.compact_revision(source,self.root/'bad')
        self.assertEqual(before,inventory(source)); self.assertFalse((self.root/'bad').exists())
        self.assertFalse(list(self.root.glob('.compact-revision-*')))

    def test_wrong_or_failed_logical_proof_is_rejected(self):
        source, _ = self.dataset(); before = inventory(source)
        for name,value in (('passed',False),('logical_contents_preserved',False),
                           ('source_database_sha256','0'*64),('database_sha256','0'*64),('compacted_bytes',0)):
            def wrong(candidate):
                proof = self.compact(candidate); proof[name] = value; return proof
            with patch.object(compaction, 'run_compactor', side_effect=wrong):
                with self.assertRaises(ArtifactError): compaction.compact_revision(source,self.root/'bad')
            self.assertEqual(before,inventory(source)); self.assertFalse((self.root/'bad').exists())

    def test_space_failure_does_not_copy_or_modify_any_file(self):
        source, _ = self.dataset(); before = inventory(source)
        with patch.object(compaction.shutil,'disk_usage',return_value=SimpleNamespace(free=1)), \
             patch.object(compaction,'run_compactor') as run:
            with self.assertRaises(ArtifactError): compaction.compact_revision(source,self.root/'bad')
            run.assert_not_called()
        self.assertEqual(before, inventory(source)); self.assertFalse((self.root/'bad').exists())

    def test_source_or_receipt_tampering_and_linked_output_are_refused(self):
        source, _ = self.dataset()
        link = self.root/'link'; link.symlink_to(source, target_is_directory=True)
        with self.assertRaises(ArtifactError): compaction.compact_revision(source,link/'new')
        (source/'input/input.mv.db').write_bytes(b'tampered')
        with self.assertRaises(ArtifactError): compaction.compact_revision(source,self.root/'bad')

    def test_compaction_certificate_is_bound_to_final_file_and_source_revision(self):
        source, _ = self.dataset()
        with patch.object(compaction,'run_compactor',side_effect=self.compact):
            value = compaction.compact_revision(source,self.root/'compact')
        for name,replacement in (('compacted_bytes',0),('original_bytes',True),('database_sha256','a'*64),
                                 ('source_receipt_sha256','bad'),('format_version',True)):
            candidate = deepcopy(value['identity']); candidate['input_compaction'][name] = replacement
            with self.assertRaises(ArtifactError): check_compaction(candidate)


if __name__ == '__main__': unittest.main()
