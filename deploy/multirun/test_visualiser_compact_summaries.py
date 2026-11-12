"""(C) Copyright 2026, by Ross Richardson

Bounded seed-only preparation, protected publication and restart parity proofs.
Small fictional CSVs use the pinned Visualiser calculation; no model is run.
@author ross richardson
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import unittest
import time
from unittest.mock import patch

from deploy.multirun.visualiser_backend import VisualiserBackend
from deploy.multirun.visualiser_summary_holds import SummaryCapacityError
from deploy.multirun import test_visualiser_backend as backend_tests
from deploy.multirun import test_visualiser_summary_cache as cache_tests

BUILD=backend_tests.BUILD


@unittest.skipUnless(BUILD, 'Set SIMPATHS_VISUALISER_TEST_BUILD to a pinned development build')
class CompactSummaryTests(unittest.TestCase):
    def setUp(self):
        cache_tests.SummaryBackendTests.setUp(self)
        from jasmine_web.batch.policy import Resources
        self.budget=Resources(cpu_millis=1000,memory_mib=2560,storage_mib=256)
        self.selected=cache_tests.SummaryBackendTests.selected(self)
        for source in self.selected:
            source['configuration']['completed_at']=datetime.now(timezone.utc).isoformat()
        now=time.time()
        plans=[dict(comparison='retained-'+str(i),created_at=now-1,expires_at=now+3600,
            configurations=[dict(configuration=source['configuration']['id'],job=source['identity']['job'],
                attempt=source['identity']['attempt'],seeds=[str(r['seed']) for r in source['configuration']['runs']])])
                for i,source in enumerate(self.selected)]
        self.backend.retain_summaries('alice','compact-test',plans,owner_entries=6)
        self.operations=[]
        self.ready=[]
        self.progress=[]
        self.raw={f['path']:hashlib.sha256((self.root/f['path']).read_bytes()).hexdigest()
                  for source in self.selected for f in source['files']}

    def command(self, argv, *, pass_fds=()):
        request=json.loads(Path(argv[-1]).read_text())
        self.operations.append(request['operation'])
        # Compact work contains only the current seed; earlier workspace
        # metrics cannot accumulate across repetitions or configurations.
        if request['operation']=='run':
            self.assertEqual(list(Path(request['output']).parent.glob('*.json')),[Path(argv[-1])])
        return backend_tests.VisualiserBackendTests.command(self,argv,pass_fds=pass_fds)

    def prepare(self, **options):
        return self.backend.prepare_summaries(self.selected,self.work,self.command,self.progress.append,
            resource_budget=self.budget,source_ready=self.ready.append,**options)

    def assert_raw_intact(self):
        self.assertEqual(self.raw,{name:hashlib.sha256((self.root/name).read_bytes()).hexdigest() for name in self.raw})
        self.assertEqual(list(self.work.iterdir()),[])

    def test_serial_seed_only_pass_is_protected_and_later_chart_does_not_reparse(self):
        self.assertEqual(self.prepare(),dict(configurations=2,seeds=6))
        self.assertEqual(self.operations,['run']*6)
        self.assertEqual(self.progress,list(range(1,7)))
        self.assertEqual(self.ready,[s['identity']['job'] for s in self.selected])
        self.assertEqual(self.backend.verify_retained_summaries(self.selected),dict(configurations=2,seeds=6))
        self.assert_raw_intact()
        self.operations.clear()
        publication=self.backend.process(self.selected,self.work,self.command,lambda _:None)
        self.assertEqual(self.operations,['aggregate'])
        scenario=next(row for row in publication['rows'] if row['scenario']=='scenario'
                      and row['variable']=='Mental Component Summary (MCS)'
                      and row['stratifier']=='Overall' and row['variable_value']=='Continuous Mean')
        self.assertEqual(scenario['paired_mean_delta'],5)

    def test_failed_second_seed_keeps_first_cache_entry_and_raw_then_restart_resumes(self):
        original=self.command
        calls=0
        def fail(argv, *, pass_fds=()):
            nonlocal calls
            calls+=1
            if calls==2:raise ValueError('Fictional cancellation')
            return original(argv,pass_fds=pass_fds)
        with patch.object(self,'command',side_effect=fail),self.assertRaisesRegex(ValueError,'Fictional cancellation'):
            self.prepare()
        self.assertEqual(self.ready,[])
        self.assert_raw_intact()
        self.operations.clear()
        self.prepare()
        self.assertEqual(self.operations,['run']*5)
        self.assertEqual(self.ready,[s['identity']['job'] for s in self.selected])
        self.assert_raw_intact()

    def test_missing_protected_hold_is_rejected_before_opening_or_processing_raw(self):
        self.backend.retain_summaries('alice','compact-test',[],owner_entries=6)
        with patch.object(self.backend,'_verified_source',side_effect=AssertionError('Raw must not open')):
            with self.assertRaises(SummaryCapacityError):self.prepare()
        self.assertEqual(self.operations,[])
        self.assert_raw_intact()

    def test_removed_dependency_stops_before_the_next_seed_and_keeps_raw(self):
        def release(count):
            self.progress.append(count)
            if count==1:self.backend.retain_summaries('alice','compact-test',[],owner_entries=6)
        with self.assertRaises(SummaryCapacityError):
            self.backend.prepare_summaries(self.selected,self.work,self.command,release,
                resource_budget=self.budget,source_ready=self.ready.append)
        self.assertEqual(self.operations,['run'])
        self.assertEqual(self.ready,[])
        self.assert_raw_intact()

    def test_cached_summaries_do_not_bypass_changed_raw_source(self):
        from jasmine_web.batch.results import OutputUnavailable
        self.prepare();self.operations.clear();self.ready.clear()
        (self.root/self.selected[0]['files'][0]['path']).write_text('changed')
        with self.assertRaises(OutputUnavailable):self.prepare()
        self.assertEqual(self.operations,[])
        self.assertEqual(self.ready,[])
        self.assertEqual(list(self.work.iterdir()),[])

    def test_unfunded_scratch_or_memory_is_rejected_before_processing(self):
        from jasmine_web.batch.policy import Resources
        for budget in (Resources(1000,2560,255),Resources(1000,1279,256),Resources(999,2560,256)):
            with self.subTest(budget=budget),patch.object(self,'budget',budget):
                with self.assertRaisesRegex(ValueError,'admitted finite scratch'):self.prepare()
        self.assertEqual(self.operations,[])
        self.assert_raw_intact()

    def test_oversized_seed_never_signals_readiness_or_keeps_workspace_files(self):
        def oversized(argv, *, pass_fds=()):
            request=json.loads(Path(argv[-1]).read_text())
            with open(request['output'],'wb') as output:output.truncate(64*1024**2+1)
            Path(request['output']).chmod(0o600)
        with patch.object(self,'command',side_effect=oversized):
            with self.assertRaisesRegex(ValueError,'entry budget'):self.prepare()
        self.assertEqual(self.ready,[])
        self.assert_raw_intact()
