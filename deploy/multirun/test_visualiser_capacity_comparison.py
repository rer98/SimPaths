"""(C) Copyright 2026, by Ross Richardson

Equal-work, provenance, cancellation and bounded-pair capacity regression checks.
Tiny helper integration is calculation correctness evidence, not a resource trial.
@author ross richardson
"""
import copy
import csv
import io
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from deploy.multirun import visualiser_capacity as legacy
from deploy.multirun import visualiser_capacity_comparison as comparison
from deploy.multirun import visualiser_capacity_worker as worker
from deploy.multirun import visualiser_capacity_bounded as bounded
from deploy.multirun.artifacts import fingerprint


BUILD = os.environ.get('SIMPATHS_VISUALISER_TEST_BUILD')
FRONTEND = Path('/home/rer/git/JAS-mine/JAS-mine-web')


def task(number=0):
    return dict(task=number,elapsed_seconds=5,ready_seconds=5+number*5,view_read_seconds=[.1,.2],
        started_wall_time_ns=1_000_000_000+number*5_000_000_000,
        ready_wall_time_ns=6_000_000_000+number*5_000_000_000,
        parts=[dict(ready_wall_time_ns=5_000_000_000+number*5_000_000_000)],
        cache=dict(hits=0,misses=6,restored_bytes=0,computed_bytes=120,published=6,
            lock_wait_seconds=0.,restore_seconds=.01,compute_seconds=1.),
        result_identity=dict(task=number,sha256='a'*64))


def batch(workers=1,*,cpu=1000,mode='cold',seconds=10,passed=True):
    return dict(passed=passed,cleanup=True,workers=workers,cpu_millis_per_worker=cpu,
        memory_mib_per_worker=3072,cache_mode=mode,round=1,elapsed_seconds=seconds,
        tasks_per_hour=720,results=[dict(passed=passed,report=dict(passed=passed,cleanup=True,
            tasks=[task(n) for n in range(w,2,workers)])) for w in range(workers)])


class CapacityComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self):
        root = self.root/'fixture'
        root.mkdir(mode=0o700)
        for name in ('baseline.json','alternative.json'):
            worker.write_json(root/name,[dict(year=2019,run='606',scenario='baseline')])
        configs = [dict(id=role,name=role,role=role,runs=[dict(seed=s,source_seed='606',summary=name)
            for s in ('9001','9002')]) for role,name in
            [('baseline','baseline.json'),('scenario_1','alternative.json')]]
        old = self.build('old')
        built = legacy.read_json(old/'build.json')
        record = dict(format=worker.FORMAT,mode='summary-replay',annual_years=list(range(2019,2071)),
            seeds=['9001','9002'],configurations=configs,limitations=['Synthetic fixture.'],
            files={n:fingerprint(root/n) for n in ('baseline.json','alternative.json')},
            backend={name+'_sha256':built['files'][name+'.cjs'] for name in ('calculation','runner')})
        worker.write_json(root/'manifest.json',record)
        return root,old,record

    def build(self,name):
        root = self.root/name
        root.mkdir(mode=0o700)
        (root/'calculation.cjs').write_text('// '+name)
        (root/'runner.cjs').write_text('// simpaths.visualiser.role-independent-metrics.v1 '+name)
        record = dict(format='simpaths.visualiser.build.v1',
            files={n:fingerprint(root/n)['sha256'] for n in ('calculation.cjs','runner.cjs')},
            source_hashes={'parseCore.js':'a'*64,'useAggregatedData.js':'b'*64,'csvParse.js':'c'*64},
            dependencies={'d3':'7.9.0'},toolchain={'node':'v22.23.1'})
        worker.write_json(root/'build.json',record)
        return root

    def args(self):
        fixture,old,_ = self.fixture()
        return SimpleNamespace(frontend=self.root,fixture=fixture,build=old,image='test',image_id='sha256:abc',
            output=self.root/'output',memory_mib=[3072],cpu_millis=[1000,2000],workers=[1,2],
            cache_modes=['cold','warm'],tasks_per_case=2,rounds=2,view_reads=5,timeout_seconds=60,
            cancellation_check=False,cancellation_memory_mib=3072)

    def test_total_task_count_is_identical_for_both_worker_counts(self):
        for count in (2,3,8):
            for workers in (1,2):
                plan = comparison.task_plan(count,workers)
                self.assertEqual(sorted(n for group in plan for n in group),list(range(count)))
                self.assertTrue(all(plan))
        for count in (True,1,9):
            with self.assertRaises(ValueError):
                comparison.task_plan(count,2)

    def test_new_options_change_only_explicit_cpu_tasks_and_private_cache_mount(self):
        args = SimpleNamespace(image_id='image',view_reads=3)
        options = comparison.options(args,self.root,dict(mode='summary-replay'),self.root/'fixture',
            self.root/'out',self.root/'cache',2048,2000,'identity',[0,1])
        self.assertEqual(options['nano_cpus'],2_000_000_000)
        self.assertEqual(options['mem_limit'],2048*legacy.MIB)
        self.assertEqual(options['memswap_limit'],options['mem_limit'])
        self.assertEqual({v['bind'] for v in options['volumes'].values() if v['mode']=='rw'},{'/out','/cache'})
        self.assertEqual(options['network_mode'],'none')
        self.assertNotIn('/var/run/docker.sock',str(options))
        self.assertIn('--layout',options['command'])

    def test_updated_fixture_preserves_original_and_records_both_builds(self):
        fixture,old,record = self.fixture()
        original = (fixture/'manifest.json').read_bytes()
        new = self.build('new')
        args = SimpleNamespace(fixture=fixture,source_build=old,build=new,output=self.root/'rebound',alternatives=2)
        comparison.rebind(args)
        rebound,_ = legacy.check_fixture(args.output)
        self.assertEqual((fixture/'manifest.json').read_bytes(),original)
        self.assertEqual(len(rebound['configurations']),3)
        self.assertEqual(rebound['files'],record['files'])
        self.assertNotEqual(rebound['backend'],record['backend'])
        self.assertEqual(rebound['provenance']['rebind']['original_fixture'],fingerprint(fixture/'manifest.json'))
        with self.assertRaises(FileExistsError):
            comparison.rebind(args)

    def test_rebind_rejects_changed_science_dependencies_toolchain_and_corrupt_files(self):
        fixture,old,_ = self.fixture()
        new = self.build('new')
        initial = legacy.read_json(new/'build.json')
        args = SimpleNamespace(fixture=fixture,source_build=old,build=new,output=self.root/'rebound',alternatives=2)
        changes = [('source_hashes',{'parseCore.js':'x','useAggregatedData.js':'b'*64}),
            ('source_hashes',dict(initial['source_hashes'],**{'csvParse.js':'x'*64})),
            ('dependencies',{'d3':'different'}),('toolchain',{'node':'different'})]
        for key,value in changes:
            worker.write_json(new/'build.json',dict(initial,**{key:value}))
            with self.subTest(key=key),self.assertRaises(ValueError):
                comparison.rebind(args)
            self.assertFalse(args.output.exists())
        worker.write_json(new/'build.json',initial)
        (new/'runner.cjs').write_text('// mutated')
        with self.assertRaisesRegex(ValueError,'checksum'):
            comparison.rebind(args)

    def test_matching_equal_work_speedup_excludes_failed_or_different_cache_states(self):
        cases = [batch(seconds=20),batch(2,seconds=12),batch(2,mode='warm',seconds=1),
            batch(2,cpu=2000,seconds=1,passed=False)]
        values = comparison.comparisons(cases)
        self.assertEqual(len(values),1)
        self.assertAlmostEqual(values[0]['two_worker_speedup'],20/12)

    def test_equal_total_cpu_comparison_accounts_for_larger_memory_reservation(self):
        values=comparison.equal_cpu_comparisons([batch(cpu=2000,seconds=20),batch(2,seconds=12)])
        self.assertEqual(len(values),1)
        self.assertAlmostEqual(values[0]['two_worker_speedup'],20/12)
        self.assertEqual(values[0]['two_worker_reserved_memory_mib'],6144)
        self.assertEqual(values[0]['one_worker_reserved_memory_mib'],3072)

    def test_summary_includes_queue_wait_and_local_view_latency_without_hiding_sample_count(self):
        record=batch()
        record['started_wall_time_ns']=1_000_000_000
        comparison.summarise(record)
        self.assertEqual(record['task_ready_seconds'],[5,10])
        self.assertEqual(record['task_queue_seconds'],[0,5])
        self.assertEqual((record['first_pair_ready_seconds'],record['all_comparisons_ready_seconds']),(4,10))
        self.assertEqual(record['view_read_seconds']['count'],4)
        self.assertEqual(record['view_read_seconds']['p95'],.2)
        self.assertEqual(record['cache']['misses'],12)

    def test_result_identity_compares_same_task_across_cache_and_worker_changes(self):
        cases = [batch(),batch(2,mode='warm')]
        values = comparison.result_identities(cases)
        self.assertEqual({n:len(v) for n,v in values.items()},{0:1,1:1})
        cases[1]['results'][1]['report']['tasks'][0]['result_identity']['sha256']='changed'
        self.assertEqual(len(comparison.result_identities(cases)[1]),2)

    def test_simultaneous_peak_is_not_sum_of_individual_peaks(self):
        targets = []
        for i,samples in enumerate(([(10,100),(20,10)],[(25,100),(30,10)])):
            path = self.root/str(i)
            path.mkdir()
            targets.append(path)
            (path/'samples.jsonl').write_text(''.join(json.dumps(dict(wall_time_ns=t,
                memory_current_bytes=v,working_bytes=v//2))+'\n' for t,v in samples))
            worker.write_json(path/'report.json',dict(finished_wall_time_ns=40))
        self.assertEqual(comparison.sampled_combined_peak(targets),dict(charged_bytes=110,working_bytes=55))

    def test_metrics_are_counter_deltas_not_absolute_or_logical_source_bytes(self):
        before = dict(cpu=dict(usage_usec=100,nr_periods=1,nr_throttled=0,throttled_usec=20),
            memory_events=dict(oom=0),io={'8:0':dict(rbytes=10,wbytes=50,rios=1,wios=2)},
            pressure_usec={'io':dict(some=10)})
        after = copy.deepcopy(before)
        after['cpu'].update(usage_usec=1_000_100,throttled_usec=500_020)
        after['memory_peak_bytes']=300
        after['io']['8:0'].update(rbytes=70,wbytes=90)
        after['pressure_usec']['io']['some']=1_000_010
        value = comparison.metrics(dict(metrics=dict(before=before,after=after)))
        self.assertEqual((value['cpu_seconds'],value['throttled_seconds'],value['rbytes'],value['wbytes']),
            (1.,.5,60,40))
        self.assertEqual(value['pressure_seconds']['io']['some'],1.)

    def test_matrix_reverses_order_reuses_cache_and_writes_checkpoints(self):
        args = self.args()
        client = Mock()
        client.images.get.return_value.id='sha256:abc'
        client.info.return_value={'CgroupVersion':'2'}
        calls=[]
        def run_batch(_client,_args,_snapshot,_fixture,directory,cache,memory,cpu,count):
            calls.append((directory.name,cache,memory,cpu,count))
            return batch(count,cpu=cpu)
        with patch('docker.from_env',return_value=client),patch.object(legacy,'snapshot_tools',return_value={}),\
                patch.object(comparison,'run_batch',side_effect=run_batch):
            self.assertEqual(comparison.run(args),0)
        self.assertEqual(len(calls),16)
        self.assertEqual([(c[3],c[4]) for c in calls[::2]],
            [(1000,1),(1000,2),(2000,1),(2000,2),(2000,2),(2000,1),(1000,2),(1000,1)])
        self.assertTrue(all(calls[n][1]==calls[n+1][1] for n in range(0,len(calls),2)))
        self.assertTrue(legacy.read_json(args.output/'report.json')['passed'])
        client.close.assert_called_once()

    def test_failed_cold_case_is_not_presented_as_a_warm_cache_pass(self):
        args = self.args()
        args.rounds=1;args.workers=[1];args.cpu_millis=[1000]
        client = Mock()
        client.info.return_value={'CgroupVersion':'2'}
        client.images.get.return_value.id='sha256:abc'
        with patch('docker.from_env',return_value=client),patch.object(legacy,'snapshot_tools',return_value={}),\
                patch.object(comparison,'run_batch',return_value=batch(passed=False)) as run:
            self.assertEqual(comparison.run(args),1)
        self.assertEqual(run.call_count,1)
        self.assertEqual(len(legacy.read_json(args.output/'report.json')['skipped']),1)

    def test_both_cpu_limits_are_verified_against_cgroup(self):
        root = self.root/'cgroup'
        root.mkdir()
        for name,value in [('memory.max',str(3072*legacy.MIB)),('memory.swap.max','0'),('cpu.max','200000 100000')]:
            (root/name).write_text(value)
        self.assertEqual(worker.require_limits(3072,root,cpu_millis=2000)['cpu_max'],[200000,100000])
        with self.assertRaises(ValueError):
            worker.require_limits(3072,root,cpu_millis=1000)

    def fake_client(self,*,oom=False,missing_task=False,running=False):
        client=Mock();created=[]
        def create(**options):
            target=next(Path(p) for p,v in options['volumes'].items() if v['bind']=='/out')
            command=options['command'];offset=command.index('--tasks')+1
            selected=[]
            while offset<len(command) and not command[offset].startswith('--'):
                selected.append(int(command[offset]));offset+=1
            rows=[task(n) for n in selected]
            if missing_task:rows=rows[:1]
            worker.write_json(target/'report.json',dict(passed=True,cleanup=True,tasks=rows,
                finished_wall_time_ns=20_000_000_000))
            (target/'samples.jsonl').write_text(json.dumps(dict(wall_time_ns=1_000_000_000,
                memory_current_bytes=100,working_bytes=80))+'\n')
            worker.write_json(target/'progress.json',dict(task=selected[0],stage='pair-1/aggregate'))
            (target/'private').mkdir();(target/'private'/'scratch').write_text('disposable')
            c=Mock();c.id=f'container-{len(created)}'
            c.attrs=dict(Config=dict(Labels=options['labels']),State=dict(Running=running,ExitCode=0,OOMKilled=oom))
            c.logs.return_value=b'bounded log'
            def kill():c.attrs['State'].update(Running=False,ExitCode=137)
            c.kill.side_effect=kill
            created.append(c)
            return c
        client.containers.create.side_effect=create
        return client,created

    def native_batch(self,args,client,*,count=1,cancel=None):
        with patch.object(legacy,'preflight_space',return_value={}),patch.object(legacy,'available_memory',return_value=100):
            return comparison.run_batch(client,args,self.root/'snapshot',dict(mode='summary-replay'),
                self.root/'case',self.root/'cache',3072,1000,count,cancel_stage=cancel)

    def test_batch_verifies_exact_work_and_cleans_only_scratch_after_removal(self):
        args=self.args();client,created=self.fake_client()
        value=self.native_batch(args,client)
        self.assertTrue(value['passed']);self.assertTrue(value['cleanup'])
        self.assertEqual(value['completed_tasks'],2)
        self.assertEqual(value['sampled_combined_peak']['working_bytes'],80)
        self.assertFalse((self.root/'case'/'worker-1'/'private').exists())
        self.assertTrue((self.root/'case'/'worker-1'/'report.json').exists())
        self.assertTrue((self.root/'cache').exists())
        created[0].remove.assert_called_once_with(v=True)

    def test_batch_rejects_incomplete_task_assignment_even_with_a_success_report(self):
        args=self.args();client,_=self.fake_client(missing_task=True)
        value=self.native_batch(args,client)
        self.assertFalse(value['passed']);self.assertTrue(value['cleanup'])

    def test_oom_cannot_pass_or_supply_a_throughput_measurement(self):
        args=self.args();client,_=self.fake_client(oom=True)
        value=self.native_batch(args,client)
        self.assertFalse(value['passed']);self.assertNotIn('tasks_per_hour',value)
        self.assertTrue(value['results'][0]['oom_killed'])

    def test_failed_helper_retains_counter_deltas_without_success_metrics(self):
        before=dict(cpu=dict(usage_usec=10,throttled_usec=0),memory_events=dict(oom=0),
            io={},pressure_usec={})
        after=dict(cpu=dict(usage_usec=1_000_010,throttled_usec=0),memory_events=dict(oom=1),
            io={},pressure_usec={},memory_peak_bytes=3*legacy.GIB)
        value=comparison.metrics(dict(initial_metrics=before,final_metrics=after,passed=False))
        self.assertEqual(value['oom_events'],1)
        self.assertEqual(value['cpu_seconds'],1.)

    def test_partial_start_failure_removes_all_created_workers(self):
        args=self.args();client,created=self.fake_client()
        def create(**options):
            c=original(**options)
            if len(created)==2:c.start.side_effect=RuntimeError('start failed')
            return c
        original=client.containers.create.side_effect
        client.containers.create.side_effect=create
        value=self.native_batch(args,client,count=2)
        self.assertFalse(value['passed']);self.assertTrue(value['cleanup'])
        self.assertEqual(len(created),2)
        for c in created:c.remove.assert_called_once_with(v=True)

    def test_uncertain_removal_preserves_scratch_and_stops_later_work(self):
        args=self.args();client,created=self.fake_client()
        original=client.containers.create.side_effect
        def create(**options):
            c=original(**options);c.remove.side_effect=RuntimeError('daemon unavailable');return c
        client.containers.create.side_effect=create
        with self.assertRaisesRegex(RuntimeError,'removal unconfirmed'):
            self.native_batch(args,client)
        self.assertTrue((self.root/'case'/'worker-1'/'private'/'scratch').is_file())
        self.assertFalse(legacy.read_json(self.root/'case'/'case.json')['cleanup'])

    def test_cancellation_waits_for_exit_then_confirms_removal_and_scratch_cleanup(self):
        args=self.args();client,created=self.fake_client(running=True)
        value=self.native_batch(args,client,cancel='/aggregate')
        self.assertFalse(value['passed'])
        self.assertTrue(value['cancellation']['removal_confirmed'])
        self.assertGreaterEqual(value['cancellation']['request_to_cleanup_seconds'],
            value['cancellation']['kill_and_wait_seconds'])
        names=[call[0] for call in created[0].mock_calls]
        self.assertLess(names.index('kill'),names.index('wait'))
        self.assertLess(names.index('wait'),names.index('remove'))
        self.assertFalse((self.root/'case'/'worker-1'/'private').exists())

    def test_cli_rejects_warm_without_priming_before_docker_access(self):
        common=['compare','--frontend',str(self.root),'--build',str(self.root),
            '--fixture',str(self.root),'--output',str(self.root/'out')]
        with patch('sys.stderr'),patch.object(comparison,'run') as run:
            with self.assertRaises(SystemExit) as error:
                legacy.main(common+['--cache-modes','warm'])
            self.assertEqual(error.exception.code,2)
            run.assert_not_called()

    def test_cancelled_task_retries_only_after_cleanup_and_reuses_the_same_cache(self):
        args=self.args();args.output.mkdir(mode=0o700)
        stopped=dict(cleanup=True,cancellation={'removal_confirmed':True},results=[dict(oom_killed=False)])
        retry=batch();retry['results'][0]['report']['tasks']=[task(0)]
        retry['results'][0]['report']['tasks'][0]['cache']['hits']=6
        values=[stopped,retry];calls=[]
        def run(*values_,**kwargs):
            cache=values_[5];cache.mkdir(mode=0o700,exist_ok=True)
            calls.append((cache,kwargs))
            return values.pop(0)
        with patch.object(comparison,'run_batch',side_effect=run):
            result=comparison.cancellation_check(None,args,self.root/'snapshot',{},args.output,task(0)['result_identity'])
        self.assertTrue(result['passed']);self.assertTrue(result['retry_reused_summaries'])
        self.assertEqual(calls[0][0],calls[1][0])
        self.assertEqual(calls[0][1]['tasks'],calls[1][1]['tasks'])
        self.assertFalse(calls[0][0].exists())

    def test_uncertain_cancellation_never_starts_a_retry(self):
        args=self.args();args.output.mkdir(mode=0o700)
        with patch.object(comparison,'run_batch',return_value=dict(cleanup=False)) as run:
            with self.assertRaisesRegex(RuntimeError,'Cancellation was not exercised'):
                comparison.cancellation_check(None,args,self.root/'snapshot',{},args.output,{})
        self.assertEqual(run.call_count,1)

    def test_helper_oom_is_not_accepted_as_a_successful_cancellation(self):
        args=self.args();args.output.mkdir(mode=0o700)
        value=dict(cleanup=True,cancellation={'removal_confirmed':True},
            results=[dict(oom_killed=False,observed_oom_events=1)])
        with patch.object(comparison,'run_batch',return_value=value) as run:
            with self.assertRaisesRegex(RuntimeError,'Memory exhaustion'):
                comparison.cancellation_check(None,args,self.root/'snapshot',{},args.output,{})
        self.assertEqual(run.call_count,1)


@unittest.skipUnless(BUILD and shutil.which('node'),'Set SIMPATHS_VISUALISER_TEST_BUILD for tiny real helper integration')
class BoundedPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = self.root/'fixture'
        self.fixture.mkdir(mode=0o700)
        build = Path(BUILD)
        built = comparison.checked_build(build)
        configs=[];files={}
        for index in range(3):
            rows=[]
            for year in range(2019,2071):
                rows += [dict(year=year,scenario='baseline' if index==0 else 'scenario_1',run='606',
                    variable='Highest Level of Education',variable_value='High',stratifier='Overall',
                    stratifier_value='Overall',metric_type='share',n=10,metric_value=.2+index*.1)]
            name=f'configuration-{index}.json'
            worker.write_json(self.fixture/name,rows)
            files[name]=fingerprint(self.fixture/name)
            configs.append(dict(id='baseline' if index==0 else f'alternative{index}',name=f'Example {index}',
                role='baseline' if index==0 else f'scenario_{index}',
                runs=[dict(seed=seed,source_seed='606',summary=name) for seed in ('9001','9002')]))
        self.manifest=dict(format=worker.FORMAT,mode='summary-replay',annual_years=list(range(2019,2071)),
            seeds=['9001','9002'],configurations=configs,files=files,
            backend={n+'_sha256':built['files'][n+'.cjs'] for n in ('calculation','runner')})
        worker.write_json(self.fixture/'manifest.json',self.manifest)
        self.built=built
        self.args=SimpleNamespace(frontend=FRONTEND,build=build,fixture=self.fixture,raw_root=self.root,
            cache=self.root/'cache',memory_mib=2048,tasks=[0],view_reads=2)

    def run_pipeline(self,name,command=None):
        self.args.output=self.root/name
        self.args.output.mkdir(mode=0o700)
        work=self.args.output/'private';work.mkdir(mode=0o700)
        sampler=SimpleNamespace(stage='',peaks={},capture=lambda:{})
        record=dict(stages={},fixture_manifest=fingerprint(self.fixture/'manifest.json'))
        with patch.object(worker,'command',wraps=worker.command) if command is None else patch.object(worker,'command',side_effect=command):
            bounded.run_tasks(self.args,self.manifest,self.built,work,sampler,record,worker)
        return record

    def test_cold_warm_and_serial_task_results_match_actual_composite_helpers(self):
        cold=self.run_pipeline('cold')['tasks'][0]
        warm=self.run_pipeline('warm')['tasks'][0]
        self.assertEqual(cold['result_identity'],warm['result_identity'])
        self.assertEqual((cold['cache']['hits'],cold['cache']['misses']),(0,6))
        self.assertEqual((warm['cache']['hits'],warm['cache']['misses']),(6,0))
        self.assertEqual(len(cold['parts']),2)
        self.assertTrue(all(v['rows_per_configuration']==[52,52] for v in cold['validation']))
        self.args.tasks=[0,1]
        serial=self.run_pipeline('serial')['tasks']
        self.assertEqual(serial[0]['result_identity'],cold['result_identity'])
        self.assertEqual(serial[1]['cache']['misses'],6)
        self.assertNotEqual(serial[0]['result_identity'],serial[1]['result_identity'])
        self.assertFalse(any((self.args.output/'private').iterdir()))
        self.assertIn('task-0/output-verification',legacy.read_json(self.args.output/'report.json')['stages'])
        self.assertIn('task-1/input-verification',legacy.read_json(self.args.output/'report.json')['stages'])

    def test_completed_summaries_survive_interrupted_combination_and_retry(self):
        original=worker.command
        def interrupt(log,argv,**kwargs):
            if str(argv[-2]).endswith('/runner.cjs'):
                request=worker.decode(Path(argv[-1]).read_bytes())
                if request.get('operation')=='aggregate':
                    raise InterruptedError('simulated cancellation after complete summaries')
            return original(log,argv,**kwargs)
        with self.assertRaises(InterruptedError):
            self.run_pipeline('interrupted',interrupt)
        retry=self.run_pipeline('retry')['tasks'][0]
        self.assertEqual((retry['cache']['hits'],retry['cache']['misses']),(6,0))
        self.assertEqual(len(retry['parts']),2)

    def test_full_worker_emits_bounded_receipts_and_cleans_scratch_with_fake_cgroup(self):
        cgroup=self.root/'cgroup';cgroup.mkdir()
        contents={'memory.max':str(2048*worker.MIB),'memory.swap.max':'0','cpu.max':'100000 100000',
            'memory.current':'1048576','memory.peak':'2097152','memory.stat':'inactive_file 262144\n',
            'memory.events':'max 0\noom 0\noom_kill 0\n',
            'cpu.stat':'usage_usec 10\nnr_periods 2\nnr_throttled 0\nthrottled_usec 0\n',
            'io.stat':'8:0 rbytes=16 wbytes=32 rios=1 wios=2\n'}
        for kind in ('cpu','memory','io'):
            contents[kind+'.pressure']='some avg10=0.00 avg60=0.00 avg300=0.00 total=8\n'
        for name,value in contents.items():(cgroup/name).write_text(value)
        self.args.output=self.root/'full-worker';self.args.layout='bounded-pairs';self.args.cpu_millis=1000
        snapshot,limits=worker.snapshot,worker.require_limits
        with patch.object(worker,'snapshot',side_effect=lambda _root=worker.CGROUP:snapshot(cgroup)),\
                patch.object(worker,'require_limits',side_effect=lambda memory,**kwargs:limits(memory,cgroup,**kwargs)):
            worker.execute(self.args)
        report=legacy.read_json(self.args.output/'report.json')
        receipt=legacy.read_json(self.args.output/'receipt.json')
        self.assertTrue(report['passed']);self.assertTrue(report['cleanup'])
        self.assertTrue(report['sources_unchanged'])
        self.assertFalse((self.args.output/'private').exists())
        self.assertEqual(receipt['tasks'],[report['tasks'][0]['result_identity']])

    def test_tiny_raw_seed_uses_verified_descriptors_and_warm_cache_for_single_pair(self):
        from deploy.multirun.test_visualiser_backend import native_texts,csv_text
        configs=[];files={}
        for index in range(2):
            selected={}
            for kind,text in zip(('person','benefit'),native_texts(10+index*5)):
                first=list(csv.DictReader(io.StringIO(text)))
                rows=[dict(row,time=str(year)+'.0') for year in range(2019,2071) for row in first]
                name=f'raw-{index}-{kind}.csv'
                path=self.root/name;path.write_text(csv_text(rows));os.chmod(path,0o600)
                files[name]=fingerprint(path);selected[kind]=name
            configs.append(dict(id='baseline' if index==0 else 'alternative1',name=f'Fictional {index}',
                role='baseline' if index==0 else 'scenario_1',runs=[dict(seed='606',**selected)]))
        self.manifest=dict(self.manifest,mode='raw-native',seeds=['606'],configurations=configs,files=files)
        worker.write_json(self.fixture/'manifest.json',self.manifest)
        cold=self.run_pipeline('raw-cold')['tasks'][0]
        warm=self.run_pipeline('raw-warm')['tasks'][0]
        self.assertEqual(len(cold['parts']),1)
        self.assertEqual(cold['result_identity'],warm['result_identity'])
        self.assertEqual((warm['cache']['hits'],warm['cache']['misses']),(2,0))
        self.assertTrue(cold['validation'][0]['nonzero_paired_cells'])


if __name__=='__main__':
    unittest.main()
