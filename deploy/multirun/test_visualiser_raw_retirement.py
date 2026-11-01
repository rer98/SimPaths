"""(C) Copyright 2026, by Ross Richardson

Atomic proof that retiring raw output preserves every compatible held summary.
Fictional immutable catalogue descriptors; no CSV parsing, database or Docker.
@author ross richardson
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from .visualiser_backend import VisualiserBackend
from .visualiser_summary_cache import MIB, SummaryCache
from .visualiser_summary_holds import STATE, SummaryCapacityError, canonical, digest


class RawRetirementSummaryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.backend=VisualiserBackend.__new__(VisualiserBackend)
        self.backend.summary_cache=SummaryCache(self.root/'summaries',maximum_bytes=MIB,
                                               maximum_entries=4,entry_bytes=4096)
        self.backend.manifest=dict(files={'runner.cjs':'a'*64,'calculation.cjs':'b'*64})
        self.backend.identity=dict(node='v22.test')
        self.backend.summary_retention_seconds=7*86400
        self.created=datetime.now(timezone.utc).timestamp()-60
        self.sources=[self.source('baseline'),self.source('alternative')]
        self.summary=self.root/'metrics.json'
        self.summary.write_bytes(b'[{"fictional":true,"metric_value":1}]');self.summary.chmod(0o600)
        self.retain()
        self.keys=self.publish()

    def source(self, name):
        runs=[dict(folder=f'{name}/run_{seed}',seed=str(seed)) for seed in (606,607)]
        files=[]
        for run in runs:
            for kind in ('Person','BenefitUnit'):
                relative=run['folder']+'/csv/'+kind+'.csv'
                # Raw output deliberately does not exist. Catalogue descriptors
                # remain sufficient after an authorised raw-only deletion.
                files.append(dict(name=relative,path='missing/'+relative,bytes=123,
                                  sha256=hashlib.sha256(relative.encode()).hexdigest()))
        return dict(owner='alice',identity=dict(job='job-'+name,attempt='attempt-1',
                    execution_key='execution-'+name,prepared='prepared-'+name),
                    configuration=dict(id=name,model='sha256:'+'c'*64,dataset='own-inputs',
                        prepared_fingerprint='prepared-'+name,runs=runs,
                        completed_at=datetime.fromtimestamp(self.created,timezone.utc).isoformat()),files=files)

    def retain(self):
        plans=[dict(comparison='completed-'+source['configuration']['id'],created_at=self.created,
            expires_at=self.created+self.backend.summary_retention_seconds,
            configurations=[dict(configuration=source['configuration']['id'],job=source['identity']['job'],
                attempt=source['identity']['attempt'],seeds=[run['seed'] for run in source['configuration']['runs']])])
            for source in self.sources]
        self.assertEqual(self.backend.retain_summaries('alice','experiment',plans,owner_entries=4),
                         dict(admitted=2,deferred=0))

    def identities(self):
        return [self.backend._summary_identity(source,run,self.backend._summary_inputs(source,run))
                for source in self.sources for run in source['configuration']['runs']]

    def publish(self):
        keys=[]
        for identity in self.identities():
            with self.backend.summary_cache.entry(identity) as entry:
                self.assertTrue(entry.publish(self.summary));keys.append(entry.key)
        return keys

    def snapshot(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(),path.stat().st_mtime_ns)
                for path in self.root.rglob('*') if path.is_file()}

    def verify(self):
        return self.backend.verify_retained_summaries(self.sources)

    def preserve(self, *, expires_at=None, owner_entries=4):
        source=self.sources[0]
        identity=dict(source['identity'],configuration=source['configuration']['id'],
                      repetitions=[dict(seed=run['seed']) for run in source['configuration']['runs']])
        return self.backend.preserve_source_summaries('alice','experiment',identity,source,
            created_at=self.created,expires_at=expires_at or self.created+self.backend.summary_retention_seconds,
            owner_entries=owner_entries)

    def test_all_seeds_verify_without_raw_files_or_mutating_receipts_holds_usage_or_deadlines(self):
        before=self.snapshot()
        with patch.object(self.backend,'_verified_source',side_effect=AssertionError('No raw reads')):
            self.assertEqual(self.verify(),dict(configurations=2,seeds=4))
            for source in self.sources:source['retained_only']=True
            self.assertEqual(self.verify(),dict(configurations=2,seeds=4))
        self.assertEqual(self.snapshot(),before)
        self.backend.summary_cache=SummaryCache(self.backend.summary_cache.root,maximum_bytes=MIB,
                                               maximum_entries=4,entry_bytes=4096)
        self.assertEqual(self.verify(),dict(configurations=2,seeds=4))
        self.assertEqual(self.snapshot(),before)

    def test_missing_or_corrupt_single_seed_rejects_the_whole_set_without_cleaning_it(self):
        directory=self.backend.summary_cache.root/self.keys[-1]
        path=directory/'metrics.json';original=path.read_bytes()
        for contents in (b'x'*len(original),b'changed size'):
            with self.subTest(contents=contents):
                path.write_bytes(contents);before=self.snapshot()
                with self.assertRaisesRegex(ValueError,'all compatible seed summaries'):self.verify()
                self.assertEqual(self.snapshot(),before)
        path.write_bytes(original);path.unlink();before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'all compatible seed summaries'):self.verify()
        self.assertEqual(self.snapshot(),before)
        self.assertTrue(directory.is_dir())

    def test_role_and_presentation_changes_reuse_but_each_scientific_identity_change_fails(self):
        original=deepcopy(self.sources)
        for source in self.sources:
            source['configuration'].update(role='Scenario',name='Display name')
        self.assertEqual(self.verify()['seeds'],4)
        for field in ('model','dataset','prepared_fingerprint'):
            with self.subTest(field=field):
                self.sources=deepcopy(original);self.sources[0]['configuration'][field]='changed'
                with self.assertRaises(ValueError):self.verify()
        for field in ('execution_key','prepared'):
            with self.subTest(field=field):
                self.sources=deepcopy(original);self.sources[0]['identity'][field]='changed'
                with self.assertRaises(ValueError):self.verify()
        self.sources=deepcopy(original);self.sources[0]['files'][0]['sha256']='d'*64
        with self.assertRaises(ValueError):self.verify()
        for field in ('owner','attempt'):
            with self.subTest(field=field):
                self.sources=deepcopy(original)
                target=self.sources[0] if field=='owner' else self.sources[0]['identity']
                target[field]='changed'
                with self.assertRaises(SummaryCapacityError):self.verify()
        self.sources=deepcopy(original);self.backend.identity['node']='v23.test'
        with self.assertRaises(SummaryCapacityError):self.verify()
        self.backend.identity['node']='v22.test';self.backend.manifest['files']['calculation.cjs']='d'*64
        with self.assertRaises(SummaryCapacityError):self.verify()

    def test_present_summaries_without_live_admission_are_insufficient(self):
        state=self.backend.summary_cache.root/STATE
        original=state.read_bytes();value=json.loads(original)
        for record in value['holds'].values():record['admitted']=False
        state.write_text(json.dumps(value));before=self.snapshot()
        with self.assertRaises(SummaryCapacityError):self.verify()
        self.assertEqual(self.snapshot(),before)
        state.write_bytes(original);state.unlink();before=self.snapshot()
        with self.assertRaises(SummaryCapacityError):self.verify()
        self.assertEqual(self.snapshot(),before)

    def test_expiry_is_original_and_cannot_be_reset_by_verification(self):
        deadline=self.created+self.backend.summary_retention_seconds
        with patch('deploy.multirun.visualiser_backend.time.time',return_value=deadline+1):
            with self.assertRaisesRegex(ValueError,'summaries have expired'):self.verify()
        # Even a malformed promise with a later horizon cannot extend the
        # source's completion-based deadline.
        state=self.backend.summary_cache.root/STATE;value=json.loads(state.read_bytes())
        for record in value['holds'].values():record['expires_at']+=86400
        state.write_text(json.dumps(value));before=self.snapshot()
        with patch('deploy.multirun.visualiser_backend.time.time',return_value=deadline+1):
            with self.assertRaisesRegex(ValueError,'summaries have expired'):self.verify()
        self.assertEqual(self.snapshot(),before)

    def test_expiry_during_hashing_also_rejects_retirement(self):
        deadline=self.created+self.backend.summary_retention_seconds
        with patch('deploy.multirun.visualiser_summary_holds.time.time',side_effect=[deadline-1,deadline-1,deadline-1,deadline+1]):
            with self.assertRaises(SummaryCapacityError):self.verify()

    def test_linked_metrics_or_receipts_are_rejected_without_following_them(self):
        directory=self.backend.summary_cache.root/self.keys[-1]
        for name in ('metrics.json','receipt.json'):
            with self.subTest(name=name):
                path=directory/name;original=path.read_bytes();path.unlink();path.symlink_to(self.summary)
                before=self.snapshot()
                with self.assertRaises(ValueError):self.verify()
                self.assertEqual(self.snapshot(),before)
                path.unlink();path.write_bytes(original);path.chmod(0o600)

    def test_whole_set_check_excludes_concurrent_invalidation(self):
        cache=self.backend.summary_cache;original=cache._receipt
        entered=threading.Event();release=threading.Event();invalidating=threading.Event()
        def receipt(directory):
            if not entered.is_set():
                entered.set();self.assertTrue(release.wait(3))
            return original(directory)
        def invalidate():
            invalidating.set();return cache.prune(owner='alice')
        with ThreadPoolExecutor(max_workers=2) as workers,patch.object(cache,'_receipt',side_effect=receipt):
            verification=workers.submit(self.verify)
            try:
                self.assertTrue(entered.wait(3))
                invalidation=workers.submit(invalidate);self.assertTrue(invalidating.wait(3))
                self.assertFalse(invalidation.done())
            finally:release.set()
            self.assertEqual(verification.result(timeout=3),dict(configurations=2,seeds=4))
            self.assertEqual(invalidation.result(timeout=3)['entries'],0)

    def test_preservation_installs_a_durable_standalone_hold_and_keeps_other_plans(self):
        cache=self.backend.summary_cache;original=json.loads((cache.root/STATE).read_bytes())['holds']
        deadline=self.created+self.backend.summary_retention_seconds-30
        with self.preserve(expires_at=deadline) as result:
            self.assertEqual(result,dict(configurations=1,seeds=2,expires_at=deadline))
            records=cache.holds.load()
            self.assertEqual(len(records),len(original)+1)
            for key,record in original.items():self.assertEqual(records[key],record)
            comparison=hashlib.sha256(canonical(['retained','job-baseline'])).hexdigest()
            key=digest(['alice','experiment',comparison])
            self.assertTrue(records[key]['admitted'])
            self.assertEqual(records[key]['configurations'][0]['seeds'],['606','607'])
        with self.preserve(expires_at=deadline+100) as result:
            self.assertEqual(result['expires_at'],deadline)
        restarted=SummaryCache(cache.root,maximum_bytes=MIB,maximum_entries=4,entry_bytes=4096)
        self.assertEqual(restarted.holds.load()[key]['expires_at'],deadline)

    def test_preservation_works_without_automatic_holds_and_blocks_eviction_during_deletion(self):
        cache=self.backend.summary_cache
        cache.holds.replace('alice','experiment',[],calculation='unused',owner_entries=4)
        raw=self.root/'raw-output.csv';raw.write_text('fictional raw output')
        entered=threading.Event()
        def invalidate():
            entered.set();return cache.prune(owner='alice')
        with ThreadPoolExecutor(max_workers=1) as worker:
            with self.preserve() as result:
                self.assertEqual(result['seeds'],2)
                pending=worker.submit(invalidate);self.assertTrue(entered.wait(3))
                self.assertFalse(pending.done())
                raw.unlink()
                self.assertTrue(all((cache.root/key).exists() for key in self.keys))
            self.assertEqual(pending.result(timeout=3)['entries'],0)
        self.assertFalse(raw.exists())

    def test_preservation_requires_every_hash_before_admission_and_does_not_expand_owner_capacity(self):
        cache=self.backend.summary_cache;before=self.snapshot()
        with self.assertRaises(SummaryCapacityError):
            with self.preserve(owner_entries=1):self.fail('Owner allowance must stay fixed')
        self.assertEqual(self.snapshot(),before)
        (cache.root/self.keys[0]/'metrics.json').unlink();before=self.snapshot()
        with self.assertRaises(ValueError):
            with self.preserve():self.fail('Missing seed must prevent raw deletion')
        self.assertEqual(self.snapshot(),before)

    def test_frozen_source_deadline_survives_later_retention_setting_increases(self):
        frozen=self.created+120
        for source in self.sources:
            source['configuration']['summary_expires_at']=datetime.fromtimestamp(frozen,timezone.utc).isoformat()
        self.backend.summary_retention_seconds=14*86400
        with self.preserve() as result:self.assertEqual(result['expires_at'],frozen)
        with patch('deploy.multirun.visualiser_backend.time.time',return_value=frozen+1):
            with self.assertRaisesRegex(ValueError,'summaries have expired'):self.verify()
        for source in self.sources:source['configuration']['summary_expires_at']=frozen
        with patch('deploy.multirun.visualiser_backend.time.time',return_value=frozen+1):
            with self.assertRaisesRegex(ValueError,'summaries have expired'):self.verify()

    def test_duplicate_empty_or_disabled_summary_requests_fail_closed(self):
        with self.assertRaises(ValueError):self.backend.verify_retained_summaries([])
        self.sources[0]['configuration']['runs'][1]['seed']='606'
        with self.assertRaises(ValueError):self.verify()
        self.backend.summary_cache=None
        with self.assertRaises(ValueError):self.verify()


if __name__=='__main__':
    unittest.main()
