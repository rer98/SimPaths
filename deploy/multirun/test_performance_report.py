"""(C) Copyright 2026, by Ross Richardson

Coverage-aware chart readiness, confirmed resource intervals and labelled reports.
@author ross richardson
"""
from datetime import datetime,timezone
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from uuid import uuid4

from .performance_report import analyse,applied_cpu,render
from .monitoring_cost_trials import order,summary,cleanup_containers


class PerformanceReportTests(unittest.TestCase):
    def trace(self):
        self.exp,self.base,self.alt,self.extra=[str(uuid4()) for _ in range(4)]
        return dict(observations=[],attempts=[],subjects=[],truncated=False,
            experiments=[dict(id=self.exp,jobs=[dict(job=self.base,state='succeeded',baseline=True),
                dict(job=self.alt,state='succeeded',baseline=False),dict(job=self.extra,state='active',baseline=False)])])

    def publication(self,trace,*jobs,when=1000):
        trace['subjects'].append(dict(kind='visualiser',subject=str(uuid4()),first_at='',last_at='',
            data=dict(experiment=self.exp,source_jobs=list(jobs),comparison=int(len(jobs)>1),ready_epoch_ms=when)))

    def test_first_pair_can_be_ready_while_another_scenario_is_active(self):
        trace=self.trace();self.publication(trace,self.base,self.alt)
        row=analyse(trace)['experiments'][0]
        self.assertIsNotNone(row['first_charts_at']);self.assertIsNone(row['all_charts_at'])
        self.assertEqual(row['observed_required_ready'],1);self.assertFalse(row['all_required_ready'])
        trace['experiments'][0]['jobs'][-1]['state']='succeeded'
        self.publication(trace,self.base,self.extra,when=2000)
        row=analyse(trace)['experiments'][0]
        self.assertTrue(row['all_required_ready']);self.assertNotEqual(row['first_charts_at'],row['all_charts_at'])

    def test_standalone_levels_do_not_claim_that_baseline_pairs_are_ready(self):
        trace=self.trace()
        for job in (self.base,self.alt,self.extra):self.publication(trace,job)
        self.assertEqual(analyse(trace)['experiments'][0]['observed_required_ready'],0)

    def test_successful_input_preparation_is_not_counted_as_a_completed_simulation(self):
        trace=self.trace();trace['experiments']=[]
        for kind,job in (('input_preparation',self.base),('simulation',self.alt)):
            trace['attempts'].append(dict(id=str(uuid4()),kind=kind,job_id=job,outcome='success',
                job_state='succeeded',finished_at='2026-10-19T10:00:00+00:00'))
        self.assertEqual(analyse(trace)['throughput'],[dict(utc_day='2026-10-19',
            simulations_completed=1,configurations_with_charts=0)])

    def test_java_progress_interval_uses_confirmed_quota_and_previous_phase(self):
        from uuid import NAMESPACE_URL,uuid5
        trace=self.trace();trace['experiments']=[]
        attempt=str(uuid4());key=str(uuid5(NAMESPACE_URL,'jasmine-review:model:'+attempt))
        trace['attempts']=[dict(id=attempt,kind='simulation',job_id=self.base,outcome=None,
            job_state='active',finished_at=None,initial_resources=dict(cpu_millis=1500),changes=[])]
        for sequence,second,phase in ((0,0,'annual'),(1,5,'output')):
            trace['observations'].append(dict(id=str(uuid4()),kind='model',subject=key,event='java',
                observed_at=f'2026-10-19T10:00:0{second}+00:00',data=dict(sequence=sequence,elapsed_ms=second*1000,
                    process_cpu_ns=second*10**9,gc_ms=second,phase=phase,year=2035,repetition=1)))
        segment=analyse(trace)['segments'][0]
        self.assertEqual(segment['source'],'Java process');self.assertEqual(segment['phase'],'annual')
        self.assertEqual(segment['attributed_vcpus'],1.5);self.assertEqual(segment['cpu_seconds'],5)

    def test_native_interval_rechecks_journal_when_snapshot_misses_a_complete_boost_cycle(self):
        from uuid import NAMESPACE_URL,uuid5
        trace=self.trace();trace['experiments']=[]
        attempt=str(uuid4());key=str(uuid5(NAMESPACE_URL,'jasmine-review:model:'+attempt))
        changes=[dict(state='confirmed',created_at='2026-10-19T10:00:10+00:00',
            finished_at='2026-10-19T10:00:12+00:00',target_resources=dict(cpu_millis=1500)),
            dict(state='confirmed',created_at='2026-10-19T10:00:14+00:00',
            finished_at='2026-10-19T10:00:15+00:00',target_resources=dict(cpu_millis=1000))]
        trace['attempts']=[dict(id=attempt,kind='simulation',job_id=self.base,outcome=None,
            job_state='active',finished_at=None,initial_resources=dict(cpu_millis=1000),changes=changes)]
        trace['observations']=[dict(id=str(uuid4()),kind='model',subject=key,event='sample',
            observed_at='2026-10-19T10:00:20+00:00',data=dict(interval_state='measured',
                observed_seconds=20,cpu_delta_ns=10**9,cpu_millis=1000))]
        row=analyse(trace)['segments'][0]
        self.assertEqual(row['interval_state'],'transition');self.assertIsNone(row['attributed_vcpus'])

    def test_cpu_change_interval_is_unattributed_and_confirmed_values_apply_afterwards(self):
        record=dict(initial_resources=dict(cpu_millis=1000),changes=[dict(
            created_at='2026-10-19T10:00:10+00:00',finished_at='2026-10-19T10:00:12+00:00',state='confirmed',
            target_resources=dict(cpu_millis=1500))])
        epoch=lambda n:datetime(2026,10,19,10,0,n,tzinfo=timezone.utc).timestamp()
        self.assertEqual(applied_cpu(record,epoch(0),epoch(9)),1)
        self.assertIsNone(applied_cpu(record,epoch(9),epoch(13)))
        self.assertEqual(applied_cpu(record,epoch(13),epoch(20)),1.5)

    def test_histogram_percentiles_are_upper_bounds_and_html_is_escaped(self):
        trace=self.trace();trace['experiments']=[]
        for upper,count in ((25,90),(100,5),(1000,5)):
            trace['observations'].append(dict(kind='http',subject='x',event='sample',observed_at='2026-10-19T10:00:00+00:00',id=str(uuid4()),
                data=dict(http_group='status',http_status='2xx',http_metric='complete_ms',response_bucket_le_ms=upper,response_count=count)))
        row=analyse(trace)['http'][0]
        self.assertEqual((row['p50_upper_ms'],row['p95_upper_ms'],row['p99_upper_ms']),(25,100,1000))
        page=render(dict(timeline=trace,platform={'provider':'<script>private</script>'}))
        self.assertIn('p95 upper bound (ms)',page);self.assertNotIn('<script>',page)
        self.assertIn('&lt;script&gt;',page)

    def test_pipeline_stage_times_are_wall_times_and_keep_parallel_parser_time_distinct(self):
        trace=self.trace();trace['experiments']=[]
        trace['subjects'].append(dict(kind='visualiser',subject=str(uuid4()),first_at='',last_at='',
            data=dict(experiment=self.exp,source_jobs=[self.base],queued_epoch_ms=1000,
                started_epoch_ms=2000,comparisons_epoch_ms=6000,publication_epoch_ms=7000,
                indexing_epoch_ms=7500,ready_epoch_ms=8000,raw_elapsed_ns=7_000_000_000,hits=0,misses=2)))
        row=analyse(trace)['pipeline'][0]
        self.assertEqual((row['queued_seconds'],row['summary_seconds'],row['processing_seconds']),(1,4,6))
        self.assertEqual(row['raw_seconds'],7)
        self.assertEqual((row['comparison_seconds'],row['validation_seconds'],row['indexing_seconds']),(1,.5,.5))

    def test_cost_conditions_are_counterbalanced_and_outcomes_have_ranges(self):
        planned=order(3,['off','external','full'])
        self.assertEqual([planned[n][1] for n in (0,3,6)],['off','external','full'])
        results=summary([dict(mode='off',container_seconds=10),dict(mode='off',container_seconds=12),
            dict(mode='full',container_seconds=11),dict(mode='full',container_seconds=13)])
        self.assertEqual(results['off']['median_seconds'],11)
        self.assertEqual(results['full']['runs'],2)
        self.assertGreater(results['full']['runtime_difference_percent'],0)
        with self.assertRaises(ValueError):order(3,['off','off'])

    def test_cost_cleanup_recovers_a_lease_after_a_lost_launch_response(self):
        lease=object();executor=Mock()
        executor.exclusive.return_value=nullcontext()
        states=[{'state':'running'}]
        executor.inspect.side_effect=lambda value:states[-1]
        executor.stop.side_effect=lambda *a:states.append({'state':'stopped'})
        saved={}
        cleanup_containers(executor,saved,SimpleNamespace(leases={'current':lease}))
        self.assertEqual(saved,{'current':lease})
        executor.stop.assert_called_once_with(lease,'cancelled')
        executor.cleanup.assert_called_once_with(lease)

    def test_cost_cleanup_rejects_unconfirmed_termination_before_scratch_can_be_deleted(self):
        executor=Mock();executor.exclusive.return_value=nullcontext()
        executor.inspect.return_value={'state':'uncertain'}
        with self.assertRaisesRegex(RuntimeError,'unconfirmed'):
            cleanup_containers(executor,{},SimpleNamespace(leases={'current':object()}),timeout=0)
        executor.cleanup.assert_not_called()
