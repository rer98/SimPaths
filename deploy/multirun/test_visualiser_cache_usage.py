"""(C) Copyright 2026, by Ross Richardson

Eviction-led raw parsing, bounded history and private numeric operator snapshots.
Fictional tiny files only; no database, Docker or scientific simulations.
@author ross richardson
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from . import visualiser_cache_usage as usage
from .visualiser_summary_cache import MIB, SummaryCache


class CacheUsageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = SummaryCache(self.root/'cache', maximum_bytes=MIB, maximum_entries=1)
        self.source = self.root/'data'
        self.source.write_bytes(b'{"fictional":true}')
        self.source.chmod(0o600)

    def identity(self, number=0, owner='private-owner@example.test'):
        return dict(owner=owner, configuration=str(number), seed='606', raw_hash='a'*64)

    def publish(self, number=0, owner='private-owner@example.test'):
        with self.cache.entry(self.identity(number, owner)) as entry:
            self.assertTrue(entry.publish(self.source))
            return self.cache.root/entry.key

    def counters(self):
        result = self.cache.statistics()
        self.assertTrue(result['available'])
        return result['counters']

    def test_eviction_followed_by_actual_raw_parsing_has_separate_costs_after_restart(self):
        with self.cache.entry(self.identity()) as entry:
            self.assertFalse(entry.restore(self.root/'first'))
            self.assertEqual(entry.miss_reason, 'unknown')
            with patch('time.monotonic_ns', side_effect=[10**9, 4*10**9]), entry.raw_processing(1200):
                pass
            entry.publish(self.source)
            self.assertTrue(entry.restore(self.root/'hit'))
        self.publish(1)
        restarted = SummaryCache(self.cache.root, maximum_bytes=MIB, maximum_entries=1)
        with restarted.entry(self.identity()) as entry:
            self.assertFalse(entry.restore(self.root/'evicted'))
            self.assertEqual(entry.miss_reason, 'evicted')
            # A cache miss alone is not evidence that raw parsing occurred.
            self.assertEqual(self.counters()['eviction_raw_started'], 0)
            with patch('time.monotonic_ns', side_effect=[7*10**9, 9*10**9]), entry.raw_processing(1200):
                pass
        counters = self.counters()
        self.assertEqual((counters['hits'], counters['misses'], counters['miss_evicted']), (1, 2, 1))
        self.assertEqual(counters['evictions'], 1)
        self.assertGreater(counters['evicted_bytes'], self.source.stat().st_size)
        self.assertEqual((counters['raw_started'], counters['raw_completed'], counters['raw_failed']), (2, 2, 0))
        self.assertEqual(counters['raw_elapsed_ns'], 5*10**9)
        self.assertEqual((counters['eviction_raw_started'], counters['eviction_raw_completed']), (1, 1))
        self.assertEqual(counters['eviction_raw_elapsed_ns'], 2*10**9)
        self.assertEqual((counters['raw_input_bytes'], counters['eviction_raw_input_bytes']), (2400, 1200))
        self.assertEqual(restarted.statistics()['eviction_fraction_of_raw_starts'], .5)

    def test_rejected_entry_changed_input_and_explicit_invalidation_are_not_eviction_cost(self):
        directory = self.publish()
        (directory/'metrics.json').write_bytes(b'X'*self.source.stat().st_size)
        with self.cache.entry(self.identity()) as entry:
            self.assertFalse(entry.restore(self.root/'bad'))
            self.assertEqual(entry.miss_reason, 'rejected')
            with entry.raw_processing(10):
                pass
            entry.publish(self.source)
        self.cache.prune(owner=self.identity()['owner'])
        with self.cache.entry(self.identity()) as entry:
            self.assertFalse(entry.restore(self.root/'invalidated'))
            self.assertEqual(entry.miss_reason, 'invalidated')
            with entry.raw_processing(10):
                pass
            entry.publish(self.source)
        self.publish(1)
        changed = dict(self.identity(), raw_hash='b'*64)
        with self.cache.entry(changed) as entry:
            self.assertFalse(entry.restore(self.root/'changed-source'))
            self.assertEqual(entry.miss_reason, 'unknown')
            with entry.raw_processing(10):
                pass
        counters = self.counters()
        self.assertEqual((counters['miss_rejected'], counters['miss_invalidated'], counters['miss_unknown']), (1, 1, 1))
        self.assertEqual(counters['raw_completed'], 3)
        self.assertEqual(counters['eviction_raw_started'], 0)

    def test_retirement_after_eviction_prevents_misattributing_a_rebuild_to_eviction(self):
        self.publish(0)
        self.publish(1, 'another-owner')
        self.cache.prune(owner=self.identity()['owner'], configuration='0')
        with self.cache.entry(self.identity()) as entry:
            self.assertFalse(entry.restore(self.root/'retired'))
            self.assertEqual(entry.miss_reason, 'invalidated')
            with entry.raw_processing(10):
                pass
        self.assertEqual(self.counters()['eviction_raw_started'], 0)
        with self.cache.entry(self.identity(1, 'another-owner')) as entry:
            self.assertTrue(entry.restore(self.root/'other-owner'))

    def test_failed_or_cancelled_parse_records_cost_without_claiming_completion(self):
        self.publish(0)
        self.publish(1)
        with self.cache.entry(self.identity()) as entry:
            self.assertFalse(entry.restore(self.root/'retry'))
            with self.assertRaises(KeyboardInterrupt):
                with patch('time.monotonic_ns', side_effect=[1000, 9000]), entry.raw_processing(10):
                    raise KeyboardInterrupt()
        counters = self.counters()
        self.assertEqual((counters['eviction_raw_started'], counters['eviction_raw_failed'], counters['eviction_raw_completed']), (1, 1, 0))
        self.assertEqual(counters['eviction_raw_elapsed_ns'], 8000)
        self.assertEqual(self.cache.statistics()['unfinished_raw_starts'], 0)

    def test_history_is_age_and_entry_bounded_and_discarded_attribution_is_unknown(self):
        with patch.object(usage, 'MAX_HISTORY', 3), patch.object(usage.time, 'time_ns', return_value=10**18):
            for index in range(6):
                self.publish(index)
            value = self.cache.statistics()
            self.assertLessEqual(value['history_entries'], 3)
            self.assertEqual(value['counters']['history_overflow'], 2)
            with self.cache.entry(self.identity()) as entry:
                self.assertFalse(entry.restore(self.root/'forgotten'))
                self.assertEqual(entry.miss_reason, 'unknown')
            with patch.object(usage.time, 'time_ns', return_value=10**18+(usage.HISTORY_SECONDS+1)*10**9):
                with self.cache.entry(self.identity(4)) as entry:
                    self.assertFalse(entry.restore(self.root/'expired'))
                    self.assertEqual(entry.miss_reason, 'unknown')
                self.assertEqual(self.counters()['history_expired'], 3)
        state = self.cache.root/usage.STATE
        self.assertLessEqual(state.stat().st_size, usage.MAX_BYTES)
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_concurrent_updates_and_same_key_reuse_do_not_lose_counts(self):
        self.publish()
        def restore(index):
            # Separate instances use the same native process-shared file locks.
            cache = SummaryCache(self.cache.root, maximum_bytes=MIB, maximum_entries=1)
            with cache.entry(self.identity()) as entry:
                self.assertTrue(entry.restore(self.root/str(index)))
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(restore, range(24)))
        self.assertEqual(self.counters()['hits'], 24)

    def test_read_only_numeric_snapshot_never_exposes_private_history(self):
        self.publish(0)
        self.publish(1)
        before = {str(p): p.read_bytes() for p in self.cache.root.rglob('*') if p.is_file()}
        public = json.dumps(self.cache.statistics())
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.cache.root.rglob('*') if p.is_file()})
        state = (self.cache.root/usage.STATE).read_text()
        for secret in (self.identity()['owner'], str(self.root), 'raw_hash'):
            self.assertNotIn(secret, state)
            self.assertNotIn(secret, public)
        for private in ('owner', 'configuration', 'history":'):
            self.assertNotIn(private, public)
        missing = self.root/'missing'
        self.assertEqual(usage.statistics(missing), dict(available=False, recorded=False))
        self.assertFalse(missing.exists())

    def test_corrupt_oversized_or_linked_tracking_does_not_break_reuse_or_fake_zero_counts(self):
        self.publish()
        state = self.cache.root/usage.STATE
        for contents in (b'not json', b'x'*(usage.MAX_BYTES+1), b'[]',
                         json.dumps(dict(format=usage.FORMAT, counters=list(usage.COUNTERS),
                                         started_ns=1, updated_ns=1, history={})).encode()):
            state.write_bytes(contents)
            with self.cache.entry(self.identity()) as entry:
                self.assertTrue(entry.restore(self.root/'still-usable'))
            self.assertEqual(state.read_bytes(), contents)
            self.assertEqual(self.cache.statistics(), dict(available=False, recorded=True))
        state.unlink()
        state.symlink_to(self.source)
        with self.cache.entry(self.identity()) as entry:
            self.assertTrue(entry.restore(self.root/'linked-tracking'))
        self.assertEqual(self.source.read_bytes(), b'{"fictional":true}')
        self.assertFalse(self.cache.statistics()['available'])

    def test_separate_processes_and_interrupted_parent_retain_honest_counts(self):
        # A hard-killed parent cannot run a finally block; retain an unmatched
        # start rather than marking the raw work complete or assigning zero time.
        code = '''
import os, sys
from pathlib import Path
from deploy.multirun.visualiser_summary_cache import SummaryCache
cache=SummaryCache(sys.argv[1])
with cache.entry(dict(owner='owner',configuration=sys.argv[2])) as entry:
    entry.restore(Path(sys.argv[1]).parent/('missing-'+sys.argv[2]))
    measure=entry.raw_processing(123)
    measure.__enter__()
    os._exit(0)
'''
        processes = [subprocess.Popen([sys.executable, '-c', code, str(self.cache.root), str(i)],
                                     cwd=Path(__file__).resolve().parents[2]) for i in range(3)]
        try:
            self.assertEqual([p.wait(timeout=10) for p in processes], [0, 0, 0])
        finally:
            for p in processes:
                if p.poll() is None:
                    p.kill()
                    p.wait(timeout=10)
        snapshot = self.cache.statistics()
        self.assertEqual(snapshot['counters']['raw_started'], 3)
        self.assertEqual(snapshot['counters']['raw_completed'], 0)
        self.assertEqual(snapshot['unfinished_raw_starts'], 3)
        self.assertEqual(snapshot['counters']['raw_input_bytes'], 369)


if __name__ == '__main__':
    unittest.main()
