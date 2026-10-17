"""(C) Copyright 2026, by Ross Richardson

Persistent comparison holds, future-partner capacity and private invalidation.
Only tiny fictional files are used; no Docker, database or simulations run.
@author ross richardson
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from .visualiser_summary_cache import SummaryCache, MIB
from .visualiser_summary_holds import MAX_SECONDS, STATE, SummaryCapacityError, scope


class SummaryHoldTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = SummaryCache(self.root/'cache', maximum_bytes=MIB,
                                  maximum_entries=3, entry_bytes=256*1024)
        self.calculation = dict(calculation={'runner.cjs': 'a'*64}, node='v22.test')
        self.created = time.time()-10
        self.source = self.root/'summary'
        self.source.write_bytes(b'x'*(200*1024))
        self.source.chmod(0o600)

    def identity(self, configuration='baseline', *, owner='alice', seed='606', attempt='attempt-1'):
        return dict(owner=owner, configuration=configuration, seed=seed,
                    execution=dict(job='job-'+configuration, attempt=attempt), **self.calculation)

    def pair(self, alternative='alternative', *, name=None, expires=None):
        return dict(comparison=name or alternative, created_at=self.created,
                    expires_at=expires or self.created+MAX_SECONDS,
                    configurations=[dict(configuration=configuration, job='job-'+configuration,
                                         attempt='attempt-1' if configuration == 'baseline' else None,
                                         seeds=['606']) for configuration in ('baseline', alternative)])

    def retain(self, comparisons=None, *, owner='alice', experiment='experiment', owner_entries=3):
        return self.cache.holds.replace(owner, experiment, [self.pair()] if comparisons is None else comparisons,
                                        calculation=scope(self.calculation), owner_entries=owner_entries)

    def publish(self, configuration='baseline', *, owner='alice'):
        identity = self.identity(configuration, owner=owner)
        with self.cache.entry(identity) as entry:
            self.assertTrue(entry.publish(self.source))
            return entry.key

    def restore(self, configuration='baseline', *, owner='alice'):
        with self.cache.entry(self.identity(configuration, owner=owner)) as entry:
            return entry.restore(self.root/'restored')

    def test_completed_scenario_remains_reusable_under_pressure_and_after_restart(self):
        retained=self.pair(name='completed')
        retained['configurations']=retained['configurations'][:1]
        self.assertEqual(self.retain([retained]),dict(admitted=1,deferred=0))
        baseline=self.publish()
        for index in range(8):self.publish('later-'+str(index),owner='bob')
        self.assertTrue((self.cache.root/baseline).is_dir())
        self.cache=SummaryCache(self.cache.root,maximum_bytes=MIB,maximum_entries=3,entry_bytes=256*1024)
        self.assertTrue(self.restore())
        from .visualiser_summary_holds import statistics
        status=statistics(self.cache.root)
        self.assertEqual(status['retained_configurations'],1)
        self.assertEqual(status['pending_comparisons'],0)

    def test_lru_pressure_preserves_baseline_and_space_for_missing_alternative(self):
        self.assertEqual(self.retain(), dict(admitted=1, deferred=0))
        baseline = self.publish()
        for index in range(8):
            self.publish('unrelated-'+str(index), owner='bob')
        self.assertTrue((self.cache.root/baseline).is_dir())
        self.assertTrue(self.restore())
        completed = self.pair()
        completed['configurations'][1]['attempt'] = 'attempt-1'
        self.retain([completed])
        self.assertTrue(self.publish('alternative'))
        self.assertTrue(self.restore('alternative'))
        counts = self.cache.prune()
        self.assertEqual(counts['entries'], 3)
        self.assertLessEqual(counts['bytes'], MIB)

    def test_restart_preserves_holds_and_expired_standalone_does_not_prune_them(self):
        self.retain()
        self.publish()
        self.cache = SummaryCache(self.cache.root, maximum_bytes=MIB,
                                  maximum_entries=3, entry_bytes=256*1024)
        self.cache.prune(owner='alice', configuration='baseline', unless_held=True)
        for index in range(5):
            self.publish('other-'+str(index), owner='bob')
        self.assertTrue(self.restore())
        self.assertEqual((self.cache.root/STATE).stat().st_mode & 0o777, 0o600)

    def test_release_restores_normal_lru_and_shared_baseline_counts_once(self):
        result = self.retain([self.pair('first'), self.pair('second')])
        self.assertEqual(result, dict(admitted=2, deferred=0))
        self.publish()
        completed = [self.pair('first'), self.pair('second')]
        for pair in completed:
            pair['configurations'][1]['attempt'] = 'attempt-1'
        self.retain(completed)
        self.publish('first')
        self.publish('second')
        self.retain([completed[1]])
        self.publish('unrelated', owner='bob')
        self.assertTrue(self.restore())
        self.assertTrue(self.restore('second'))
        self.assertFalse(self.restore('first'))
        self.retain([])
        for index in range(3):
            self.publish('new-'+str(index), owner='bob')
        self.assertFalse(self.restore())

    def test_capacity_defers_new_pair_before_raw_parsing_and_release_allows_it(self):
        self.cache.maximum_entries = 2
        self.retain(owner_entries=2)
        self.publish()
        result = self.retain(owner='bob', experiment='another', owner_entries=2)
        self.assertEqual(result, dict(admitted=0, deferred=1))
        with self.cache.entry(self.identity(owner='bob')) as entry:
            with self.assertRaises(SummaryCapacityError), entry.raw_processing(123):
                self.fail('Deferred comparison must not begin expensive parsing')
        self.assertEqual(self.cache.statistics()['counters']['raw_started'], 0)
        self.assertTrue(self.restore())
        self.retain([], owner_entries=2)
        self.assertEqual(self.retain(owner='bob', experiment='another', owner_entries=2),
                         dict(admitted=1, deferred=0))
        with self.cache.entry(self.identity(owner='bob')) as entry:
            with entry.raw_processing(123):
                entry.publish(self.source)
        self.assertEqual(self.cache.statistics()['counters']['raw_completed'], 1)

    def test_owner_limit_and_global_budget_do_not_silently_expand(self):
        self.assertEqual(self.retain(owner_entries=1), dict(admitted=0, deferred=1))
        self.assertEqual(self.retain(owner_entries=2), dict(admitted=1, deferred=0))
        self.publish()
        before = (self.cache.root/STATE).read_bytes()
        with self.assertRaises(SummaryCapacityError):
            self.retain(owner_entries=1)
        self.assertEqual((self.cache.root/STATE).read_bytes(), before)
        self.assertTrue(self.restore())
        self.assertEqual(self.retain([self.pair('one'), self.pair('two'), self.pair('three')]),
                         dict(admitted=2, deferred=1))

    def test_owner_configuration_limit_does_not_mistake_ten_seeds_for_ten_comparisons(self):
        self.cache.maximum_bytes=16*MIB
        self.cache.maximum_entries=20
        plan=self.pair()
        for configuration in plan['configurations']:
            configuration['seeds']=[str(seed) for seed in range(606,616)]
        self.assertEqual(self.retain([plan],owner_entries=2),dict(admitted=1,deferred=0))
        with self.cache._lock('.cache.lock'):
            records=self.cache.holds.load()
        self.assertEqual(len(self.cache.holds.units(records)),20)
        self.assertEqual(len(self.cache.holds.configurations(records)),2)

    def test_hold_deadline_is_fixed_reads_and_refreshes_do_not_extend_it(self):
        first = self.created+100
        self.retain([self.pair(expires=first)])
        self.publish()
        self.assertTrue(self.restore())
        self.retain([self.pair(expires=first+100)])
        with self.cache._lock('.cache.lock'):
            records = self.cache.holds.load()
        self.assertEqual(next(iter(records.values()))['expires_at'], first)
        with patch('deploy.multirun.visualiser_summary_holds.time.time', return_value=first+1):
            for index in range(3):
                self.publish('expired-'+str(index), owner='bob')
            self.assertFalse(self.restore())
            self.assertEqual(self.retain([self.pair(expires=first)]), dict(admitted=0, deferred=0))

    def test_invalidation_overrides_holds_and_releases_future_partner_reservation(self):
        self.cache.maximum_entries = 4
        self.retain(owner_entries=4)
        self.publish()
        self.retain(owner='bob', experiment='another', owner_entries=4)
        self.publish(owner='bob')
        self.cache.prune(owner='alice', configuration='baseline')
        self.assertFalse(self.restore())
        self.assertTrue(self.restore(owner='bob'))
        with self.cache._lock('.cache.lock'):
            records = self.cache.holds.load()
        self.assertEqual({record['owner'] for record in records.values()}, {'bob'})
        self.cache.prune(owner='bob')
        self.assertFalse(self.restore(owner='bob'))

    def test_source_invalidation_preserves_same_named_configuration_in_another_experiment(self):
        first=self.pair(name='completed');first['configurations']=first['configurations'][:1]
        self.retain([first])
        original=self.publish()
        other=self.pair(name='completed');other['configurations']=other['configurations'][:1]
        other['configurations'][0]['job']='other-baseline'
        self.retain([other],experiment='another')
        identity=self.identity();identity['execution']['job']='other-baseline'
        with self.cache.entry(identity) as entry:
            self.assertTrue(entry.publish(self.source));saved=entry.key
        self.cache.prune(owner='alice',configuration='baseline',job='job-baseline',attempt='attempt-1')
        self.assertFalse((self.cache.root/original).exists())
        self.assertTrue((self.cache.root/saved).exists())
        with self.cache.entry(identity) as entry:self.assertTrue(entry.restore(self.root/'other-restored'))
        with self.cache._lock('.cache.lock'):
            self.assertEqual({r['experiment'] for r in self.cache.holds.load().values()},{'another'})
        with self.assertRaises(ValueError):self.cache.prune(owner='alice',configuration='baseline',job='other-baseline')

    def test_changed_attempt_or_calculation_cannot_replay_an_older_held_seed(self):
        self.retain()
        self.publish()
        plan = self.pair()
        plan['configurations'][0]['attempt'] = 'attempt-2'
        self.retain([plan])
        changed = self.identity(attempt='attempt-2')
        with self.cache.entry(changed) as entry:
            self.assertFalse(entry.restore(self.root/'new-attempt'))
            with entry.raw_processing(10):
                self.assertTrue(entry.publish(self.source))
        self.assertFalse((self.root/'new-attempt').exists())
        self.calculation['node'] = 'v23.test'
        with self.cache.entry(self.identity()) as entry:
            self.assertFalse(entry.restore(self.root/'new-node'))

    def test_corrupt_or_symlink_metadata_fails_closed_without_evicting_summaries(self):
        self.retain()
        key = self.publish()
        path = self.cache.root/STATE
        original = path.read_bytes()
        for body in (b'{', b'{"format":"wrong","holds":{}}', b'x'*(512*1024+1)):
            path.write_bytes(body)
            with self.assertRaises((SummaryCapacityError, ValueError)):
                self.publish('unrelated', owner='bob')
            self.assertTrue((self.cache.root/key).is_dir())
        path.write_bytes(original)
        path.unlink()
        path.symlink_to(self.source)
        with self.assertRaises((SummaryCapacityError, OSError, ValueError)):
            self.cache.prune()
        self.assertTrue((self.cache.root/key).is_dir())

    def test_concurrent_partner_publications_obey_reserved_bytes_and_entries(self):
        pairs = [self.pair('first'), self.pair('second')]
        for pair in pairs:
            pair['configurations'][1]['attempt'] = 'attempt-1'
        self.retain(pairs)
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(self.publish, ('baseline', 'first', 'second')))
        for configuration in ('baseline', 'first', 'second'):
            self.assertTrue(self.restore(configuration))
        counts = self.cache.prune()
        self.assertEqual(counts['entries'], 3)
        self.assertLessEqual(counts['bytes'], MIB)
        self.assertFalse(list(self.cache.root.glob('.holds-*')))
        self.assertFalse(list(self.cache.root.glob('.pending-*')))

    def test_old_attempt_does_not_satisfy_missing_partner_capacity(self):
        old = self.publish('alternative')
        self.retain()
        with self.cache._lock('.cache.lock'):
            records = self.cache.holds.load()
            entries = self.cache._entries()
            _, count, protected = self.cache.holds.occupancy(records, entries)
        self.assertNotIn(old, protected)
        self.assertEqual(count, 3)  # Old unheld data plus two future reservations.
        plan = self.pair()
        plan['configurations'][1]['attempt'] = 'attempt-2'
        self.retain([plan])
        with self.cache.entry(self.identity('alternative', attempt='attempt-2')) as entry:
            with entry.raw_processing(1):
                self.assertTrue(entry.publish(self.source))

    def test_metadata_capacity_rejects_new_dependencies_without_changing_existing_promises(self):
        from .visualiser_summary_holds import MAX_HOLDS
        self.retain([self.pair('alternative-'+str(i)) for i in range(MAX_HOLDS)])
        self.publish()
        before=(self.cache.root/STATE).read_bytes()
        with self.assertRaises(SummaryCapacityError):
            self.retain(owner='bob',experiment='another')
        self.assertEqual((self.cache.root/STATE).read_bytes(),before)
        self.assertTrue(self.restore())

    def test_separate_processes_preserve_both_owners_dependencies_and_publications(self):
        self.cache.maximum_bytes=2*MIB
        self.cache.maximum_entries=4
        script='''
import sys, time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from deploy.multirun.visualiser_summary_cache import SummaryCache, MIB
from deploy.multirun.visualiser_summary_holds import scope
cache=SummaryCache(Path(sys.argv[2]),maximum_bytes=2*MIB,maximum_entries=4,entry_bytes=256*1024)
owner=sys.argv[4]
identity=dict(owner=owner,configuration='baseline',seed='606',
    execution=dict(job='job-baseline',attempt='attempt-1'),
    calculation={'runner.cjs':'a'*64},node='v22.test')
created=float(sys.argv[5])
plan=dict(comparison='pair',created_at=created,expires_at=created+86400,
    configurations=[dict(configuration=name,job='job-'+name,
        attempt='attempt-1' if name=='baseline' else None,seeds=['606'])
        for name in ('baseline','alternative')])
cache.holds.replace(owner,'experiment',[plan],calculation=scope(identity),owner_entries=2)
with cache.entry(identity) as entry:
    assert entry.publish(Path(sys.argv[3]))
'''
        children=[subprocess.Popen([sys.executable,'-I','-B','-c',script,
            str(Path(__file__).resolve().parents[2]),str(self.cache.root),str(self.source),owner,str(self.created)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE) for owner in ('alice','bob')]
        try:
            for child in children:
                stdout,stderr=child.communicate(timeout=20)
                self.assertEqual(child.returncode,0,stderr.decode())
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait()
        with self.cache._lock('.cache.lock'):
            records=self.cache.holds.load()
        self.assertEqual({r['owner'] for r in records.values()},{'alice','bob'})
        self.assertTrue(self.restore(owner='alice'))
        self.assertTrue(self.restore(owner='bob'))


if __name__ == '__main__':
    unittest.main()
