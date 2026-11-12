"""(C) Copyright 2026, by Ross Richardson

Complete, funded seed sets keep fixed reconstruction deadlines across retries.
Tiny fictional summaries; no database, raw scientific rows or external services.
@author ross richardson
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import time
import unittest
from unittest.mock import patch

from . import test_visualiser_raw_retirement as raw_retirement
from .visualiser_summary_holds import SummaryCapacityError, STATE


class FixedSourceRetentionTests(unittest.TestCase):
    def setUp(self):
        self.f=raw_retirement.RawRetirementSummaryTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.backend=self.f.backend
        self.started=time.time()-5;self.deadline=self.started+30*86400
        self.plans=[]
        for original in self.f.sources:
            source=deepcopy(original)
            identity=dict(source['identity'],configuration=source['configuration']['id'],
                repetitions=[dict(seed=run['seed']) for run in source['configuration']['runs']])
            retained=dict(protected_at=self.iso(self.started),expires_at=self.iso(self.deadline),
                backend=deepcopy(self.backend.calculation_identity(self.backend.identity)))
            source['configuration']['summary_retention']=retained
            self.plans.append(dict(identity=identity,source=source,established=False))
        # Compare the exact timezone-aware serialized deadline used by Front.
        self.started=datetime.fromisoformat(self.plans[0]['source']['configuration']['summary_retention']['protected_at']).timestamp()
        self.deadline=datetime.fromisoformat(self.plans[0]['source']['configuration']['summary_retention']['expires_at']).timestamp()

    @staticmethod
    def iso(value):return datetime.fromtimestamp(value,timezone.utc).isoformat()

    def guard(self,**options):
        return self.backend.preserve_summary_sources('alice','experiment',self.plans,owner_entries=4,**options)

    def test_full_set_funds_fixed_thirty_day_deadline_and_numeric_snapshot(self):
        with self.guard() as protected:
            self.assertEqual(protected['expires_at'],self.deadline)
            self.assertEqual(protected['seeds'],4)
            self.assertEqual(protected['storage_snapshot']['entries'],4)
            self.assertEqual(protected['storage_snapshot']['promised_entries'],4)
        fixed=[r for r in self.backend.summary_cache.holds.load().values()
               if r['comparison'].startswith('source-retained:')]
        self.assertEqual(len(fixed),2)
        self.assertTrue(all(r['expires_at']==self.deadline for r in fixed))

    def test_replay_and_later_settings_cannot_renew_a_source(self):
        with self.guard():pass
        self.backend.summary_retention_seconds=60*86400
        for plan in self.plans:plan['established']=True
        with self.guard() as protected:self.assertEqual(protected['expires_at'],self.deadline)
        for plan in self.plans:
            plan['source']['configuration']['summary_retention']['expires_at']=self.iso(self.deadline+86400)
        with self.assertRaises(SummaryCapacityError):
            with self.guard():self.fail('Ordinary protection must not renew')

    def test_cache_admission_recovers_exact_clock_after_sql_commit_failure(self):
        with self.guard():pass
        for plan in self.plans:
            plan['source']['configuration']['summary_retention'].update(
                protected_at=self.iso(self.started+100),expires_at=self.iso(self.deadline+100))
        with self.guard() as protected:self.assertEqual(protected['expires_at'],self.deadline)
        self.assertTrue(all(p['source']['configuration']['summary_retention']['expires_at']==self.iso(self.deadline)
                            for p in self.plans))

    def test_expired_canonical_promise_never_restarts_its_clock(self):
        with self.guard():pass
        for plan in self.plans:plan['established']=True
        with patch('deploy.multirun.visualiser_backend.time.time',return_value=self.deadline+1):
            with self.assertRaisesRegex(ValueError,'summaries have expired'):
                with self.guard():self.fail('Expired source must stay expired')

    def test_canonical_expiry_replaces_completion_cap_only_with_compatible_metadata(self):
        for plan in self.plans:
            plan['source']['configuration']['completed_at']=self.iso(self.started-40*86400)
        with self.guard() as protected:self.assertEqual(protected['expires_at'],self.deadline)
        self.plans[0]['source']['configuration']['summary_retention']['backend']['node']='incompatible'
        with self.assertRaisesRegex(ValueError,'calculation is incompatible'):
            with self.guard():self.fail('Calculation provenance must match')

    def test_incomplete_or_corrupt_set_never_admits_even_one_new_promise(self):
        before=(self.backend.summary_cache.root/STATE).read_bytes()
        (self.backend.summary_cache.root/self.f.keys[-1]/'metrics.json').unlink()
        with self.assertRaises(ValueError):
            with self.guard():self.fail('All exact summaries are required')
        self.assertEqual((self.backend.summary_cache.root/STATE).read_bytes(),before)

    def test_canonical_hold_survives_empty_maintenance_snapshot_and_restart(self):
        with self.guard():pass
        self.backend.retain_summaries('alice','experiment',[],owner_entries=4)
        records=self.backend.summary_cache.holds.load()
        self.assertEqual(len(records),2)
        self.assertTrue(all(r['comparison'].startswith('source-retained:') for r in records.values()))
        self.assertEqual(self.backend.verify_retained_summaries([p['source'] for p in self.plans])['seeds'],4)

    def test_maintenance_cannot_overwrite_a_canonical_calculation_or_deadline(self):
        with self.guard():pass
        cache=self.backend.summary_cache;before=(cache.root/STATE).read_bytes()
        records=[r for r in cache.holds.load().values() if r['comparison'].startswith('source-retained:')]
        plans=[{k:deepcopy(r[k]) for k in ('comparison','created_at','expires_at','configurations')} for r in records]
        with self.assertRaises(SummaryCapacityError):
            cache.holds.replace('alice','experiment',plans,calculation='different-calculation',owner_entries=4)
        self.assertEqual((cache.root/STATE).read_bytes(),before)
        plans[0]['expires_at']+=86400
        with self.assertRaises(SummaryCapacityError):
            cache.holds.replace('alice','experiment',plans,calculation=records[0]['scope'],owner_entries=4)
        self.assertEqual((cache.root/STATE).read_bytes(),before)

    def test_only_explicit_qualification_can_extend_a_verified_promise(self):
        with self.guard():pass
        for plan in self.plans:
            plan['established']=True
            plan['source']['configuration']['summary_retention']['expires_at']=self.iso(self.deadline+86400)
        with self.guard(qualification=True) as protected:self.assertEqual(protected['expires_at'],self.deadline+86400)
        for plan in self.plans:
            plan['source']['configuration']['summary_retention']['expires_at']=self.iso(self.deadline)
        with self.assertRaises(SummaryCapacityError):
            with self.guard(qualification=True):self.fail('Qualification cannot shorten')

    def test_failed_owner_admission_does_not_change_existing_holds(self):
        before=(self.backend.summary_cache.root/STATE).read_bytes()
        with self.assertRaises(SummaryCapacityError):
            with self.backend.preserve_summary_sources('alice','experiment',self.plans,owner_entries=1):
                self.fail('Owner limit must still apply')
        self.assertEqual((self.backend.summary_cache.root/STATE).read_bytes(),before)

    def test_sql_failure_after_preflight_changes_no_files_or_holds(self):
        before=self.f.snapshot();called=[]
        def failed_commit():
            called.append(True)
            raise RuntimeError('Fictional SQL commit failure')
        with self.assertRaises(RuntimeError):
            with self.guard(before_admission=failed_commit):self.fail('Do not admit after failed SQL')
        self.assertEqual(called,[True])
        self.assertEqual(self.f.snapshot(),before)

    def test_incomplete_or_unfunded_set_never_calls_sql_clock_commit(self):
        called=[]
        with self.assertRaises(SummaryCapacityError):
            with self.backend.preserve_summary_sources('alice','experiment',self.plans,owner_entries=1,
                    before_admission=lambda:called.append(True)):
                self.fail('Capacity preflight must precede SQL intent')
        self.assertEqual(called,[])
        (self.backend.summary_cache.root/self.f.keys[-1]/'metrics.json').unlink()
        with self.assertRaises(ValueError):
            with self.guard(before_admission=lambda:called.append(True)):
                self.fail('Content verification must precede SQL intent')
        self.assertEqual(called,[])

    def test_filesystem_failure_leaves_fixed_sql_clock_and_cannot_renew_after_expiry(self):
        cache=self.backend.summary_cache;committed=[]
        def commit():
            committed.extend(deepcopy(p['source']['configuration']['summary_retention']) for p in self.plans)
        with patch.object(cache.holds,'save',side_effect=OSError('Fictional atomic save failure')):
            with self.assertRaises(OSError):
                with self.guard(before_admission=commit):self.fail('Do not publish an unfunded hold')
        self.assertEqual(len(committed),2)
        for plan,fixed in zip(self.plans,committed):
            plan['established']=True
            plan['source']['configuration']['summary_retention']=fixed
        with patch('deploy.multirun.visualiser_backend.time.time',return_value=self.deadline+1):
            with self.assertRaisesRegex(ValueError,'summaries have expired'):
                with self.guard():self.fail('A failed filesystem save cannot reset the SQL clock')


if __name__=='__main__':unittest.main()
