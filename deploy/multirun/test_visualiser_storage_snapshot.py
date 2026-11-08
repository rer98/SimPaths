"""(C) Copyright 2026, by Ross Richardson

Read-only Visualiser storage observations preserve summaries and writer history.
Fictional tiny files only; no calculation build, network, database or simulation.
@author ross richardson
"""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from .visualiser_backend import VisualiserBackend
from .visualiser_summary_cache import MIB, SummaryCache


class StorageSnapshotTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.cache=SummaryCache(self.root/'summaries',maximum_bytes=MIB,maximum_entries=4)
        self.backend=SimpleNamespace(summary_cache=self.cache)
        source=self.root/'fictional.json';source.write_bytes(b'{"fictional":true}');source.chmod(0o600)
        with self.cache.entry(dict(owner='fictional',configuration='one',seed='606')) as entry:
            entry.publish(source)
            self.key=entry.key

    def files(self):
        return {str(path.relative_to(self.cache.root)):(path.read_bytes(),path.stat().st_mode,path.stat().st_mtime_ns)
                for path in self.cache.root.rglob('*') if path.is_file()}

    def snapshot(self):
        return VisualiserBackend.storage_readonly_snapshot(self.backend)

    def test_valid_observation_keeps_summary_bytes_history_and_allowances(self):
        before=self.files();limits=(self.cache.maximum_bytes,self.cache.maximum_entries)
        result=self.snapshot()
        self.assertEqual(result['entries'],1)
        self.assertEqual(result['deferred'],0)
        self.assertEqual(self.files(),before)
        self.assertEqual((self.cache.maximum_bytes,self.cache.maximum_entries),limits)

    def test_incomplete_writer_directory_is_left_for_the_writer_to_clean(self):
        pending=self.cache.root/'.pending-fictional';pending.mkdir(mode=0o700)
        (pending/'metrics.json').write_bytes(b'fictional unfinished write')
        before=self.files()
        with self.assertRaisesRegex(ValueError,'writer cleanup'):self.snapshot()
        self.assertEqual(self.files(),before)
        VisualiserBackend.storage_snapshot(self.backend)
        self.assertFalse(pending.exists())

    def test_invalid_summary_is_not_deleted_or_recorded_as_rejected_by_observation(self):
        receipt=self.cache.root/self.key/'receipt.json';receipt.write_bytes(b'{"invalid":true}')
        before=self.files()
        with self.assertRaisesRegex(ValueError,'verify an entry'):self.snapshot()
        self.assertEqual(self.files(),before)
        self.cache._entries()
        self.assertFalse((self.cache.root/self.key).exists())

    def test_changed_file_size_is_unavailable_without_discarding_the_summary(self):
        metrics=self.cache.root/self.key/'metrics.json';metrics.write_bytes(b'x')
        before=self.files()
        with self.assertRaises(ValueError):self.snapshot()
        self.assertEqual(self.files(),before)

    def test_no_cache_has_the_existing_empty_numeric_snapshot(self):
        result=VisualiserBackend.storage_snapshot(SimpleNamespace(summary_cache=None))
        self.assertEqual(result['bytes'],0);self.assertEqual(result['deferred'],0)


if __name__=='__main__':unittest.main()
