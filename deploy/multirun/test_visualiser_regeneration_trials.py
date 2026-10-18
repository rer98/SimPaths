"""(C) Copyright 2026, by Ross Richardson

Resource-matrix coverage, per-worker containment, common-start timing, failure
cleanup and tiny genuine retained-summary pipelines. Full sizing needs Docker.
@author ross richardson
"""
import copy
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from . import visualiser_regeneration_trials as driver
from . import visualiser_regeneration_worker as worker
from . import visualiser_repetition_worker as base
from . import test_visualiser_repetition_trials as fixtures
api=base.api
BUILD=fixtures.BUILD


def worker_report(profile,tasks,released):
    stages={s:dict(passed=True,elapsed_seconds=.5,sampled_peaks=dict(working_bytes=1024,memory_current_bytes=2048))
            for s in base.STAGES}
    return dict(passed=True,cleanup=True,sources_unchanged=True,
        memory_mib=profile['memory_mib'],cpu_millis=profile['cpu_millis'],released_monotonic_ns=released,
        cases=[dict(round=profile['round'],repetitions=profile['repetitions'],task=task,passed=True,cleanup=True,
            started_monotonic_ns=released+task,ready_monotonic_ns=released+task*1_000_000_000,
            regeneration_seconds=1.,stages=copy.deepcopy(stages),result_identity={'publication':'a'*64}) for task in tasks])


class RegenerationMatrixTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.args=SimpleNamespace(image_id='sha256:'+'a'*64,image='test',timeout_seconds=60,overall_timeout_seconds=300)
        self.profile=dict(round=1,repetitions=2,memory_mib=2560,cpu_millis=1000,workers=2)

    def test_complete_factorial_covers_three_ram_sizes_cpu_workers_and_repetitions_in_each_round(self):
        plan=driver.trial_plan([2560,2048,3072],[1000,2000],[1,2],[2,5,10],3)
        self.assertEqual(len(plan),108)
        keys={(p['round'],p['memory_mib'],p['workers'],p['cpu_millis'],p['repetitions']) for p in plan}
        self.assertEqual(len(keys),108)
        self.assertEqual(plan[0]['memory_mib'],2560)
        for m,w,c in {(p['memory_mib'],p['workers'],p['cpu_millis']) for p in plan}:
            orders=[[p['repetitions'] for p in plan if (p['round'],p['memory_mib'],p['workers'],p['cpu_millis'])==(r,m,w,c)] for r in (1,2,3)]
            self.assertEqual(len({tuple(o) for o in orders}),3)

    def test_invalid_matrix_dimensions_cannot_create_partial_or_duplicate_cases(self):
        for m,c,w in [([2500],[1000],[1]),([2048,2048],[1000],[1]),([True],[1000],[1]),
                      ([2048],[1500],[1]),([2048],[1000],[3]),([],[1000],[1])]:
            with self.subTest(m=m,c=c,w=w),self.assertRaises(ValueError):driver.trial_plan(m,c,w,[2,5],1)

    def test_each_worker_has_its_own_2_5g_memory_and_cpu_limits_and_read_only_start_control(self):
        p=dict(self.profile,cpu_millis=2000)
        value=driver.container_options(self.args,self.root/'snapshot',self.root/'worker',self.root/'control',p,1,'token')
        self.assertEqual(value['mem_limit'],2560*1024**2)
        self.assertEqual(value['memswap_limit'],value['mem_limit'])
        self.assertEqual(value['nano_cpus'],2_000_000_000)
        self.assertEqual(value['volumes'][str(self.root/'control')],dict(bind='/control',mode='ro'))
        self.assertEqual([v['bind'] for v in value['volumes'].values() if v['mode']=='rw'],['/out'])
        self.assertEqual(value['network_mode'],'none');self.assertTrue(value['read_only'])
        self.assertEqual(value['cap_drop'],['ALL']);self.assertIn('no-new-privileges:true',value['security_opt'])
        self.assertEqual(value['command'][value['command'].index('--tasks')+1],'1')
        self.assertEqual(value['command'][value['command'].index('--task-start')+1],'2')
        single=driver.container_options(self.args,self.root/'snapshot',self.root/'w',self.root/'c',dict(p,workers=1),0,'id')
        self.assertEqual(single['command'][single['command'].index('--tasks')+1],'2')

    def test_native_limit_reader_accepts_2_5g_only_with_the_exact_quota_and_no_swap(self):
        group=self.root/'cgroup';group.mkdir()
        (group/'memory.max').write_text(str(2560*1024**2))
        (group/'memory.swap.max').write_text('0')
        (group/'cpu.max').write_text('100000 100000')
        self.assertEqual(api.require_limits(2560,group)['memory_max_bytes'],2560*1024**2)
        with self.assertRaises(ValueError):api.require_limits(2048,group)
        (group/'memory.swap.max').write_text('1')
        with self.assertRaises(ValueError):api.require_limits(2560,group)

    def test_first_both_and_throughput_use_two_tasks_and_total_allocated_cpu(self):
        released=100
        result=driver.check_batch([worker_report(self.profile,[1],released),worker_report(self.profile,[2],released)],self.profile,released)
        self.assertEqual(result['first_result_seconds'],1)
        self.assertEqual(result['both_results_seconds'],2)
        self.assertEqual(result['comparisons_per_hour'],3600)
        self.assertEqual(result['comparisons_per_allocated_vcpu_hour'],1800)
        p=dict(self.profile,workers=1,cpu_millis=2000)
        other=driver.check_batch([worker_report(p,[1,2],released)],p,released)
        self.assertEqual(other['comparisons_per_allocated_vcpu_hour'],result['comparisons_per_allocated_vcpu_hour'])

    def test_missing_task_partial_publication_wrong_start_or_changed_result_cannot_pass(self):
        for change in ('missing-task','failed-stage','wrong-start','changed-output','wrong-quota','missing-worker'):
            reports=[worker_report(self.profile,[1],100),worker_report(self.profile,[2],100)]
            if change=='missing-task':reports[1]['cases']=[]
            elif change=='failed-stage':reports[1]['cases'][0]['stages']['index']['passed']=False
            elif change=='wrong-start':reports[1]['released_monotonic_ns']=101
            elif change=='changed-output':reports[1]['cases'][0]['result_identity']={'publication':'b'*64}
            elif change=='wrong-quota':reports[1]['memory_mib']=3072
            else:reports.pop()
            with self.subTest(change=change),self.assertRaises(ValueError):driver.check_batch(reports,self.profile,100)

    def fake_native(self,*,oom=None,remove_error=False,stop=False):
        client=Mock();containers=[]
        def create(**options):
            target=Path(next(k for k,v in options['volumes'].items() if v['bind']=='/out'))
            control=Path(next(k for k,v in options['volumes'].items() if v['bind']=='/control'))
            index=int(target.name.removeprefix('worker-'));c=Mock();c.id='fictional-'+str(index)
            c.attrs=dict(Config={'Labels':options['labels']},State=dict(Running=False,ExitCode=0,OOMKilled=False))
            def start():
                c.attrs['State']['Running']=True
                (target/'private').mkdir();(target/'private/scratch').write_bytes(b'fake')
                api.write_json(target/'armed.json',dict(format='simpaths.visualiser.armed.v1',
                    repetitions=self.profile['repetitions'],task_start=index,tasks=1))
            def reload():
                if not c.attrs['State']['Running']:return
                if index==oom:
                    c.attrs['State'].update(Running=False,ExitCode=137,OOMKilled=True);return
                if (control/'release.json').exists() and not stop:
                    release=json.loads((control/'release.json').read_text())['released_monotonic_ns']
                    api.write_json(target/'report.json',worker_report(self.profile,[index],release))
                    c.attrs['State']['Running']=False
            c.start.side_effect=start;c.reload.side_effect=reload;c.logs.return_value=b'fictional log'
            c.kill.side_effect=lambda:c.attrs['State'].update(Running=False)
            if remove_error:c.remove.side_effect=RuntimeError('daemon unavailable')
            containers.append(c);return c
        client.containers.create.side_effect=create
        return client,containers

    def batch(self,client,*,sleep=None):
        with patch.object(driver.legacy,'preflight_space',return_value={}),patch.object(driver.time,'sleep',side_effect=sleep):
            return driver.run_batch(client,self.args,self.root/'snapshot',self.root/'batch',self.profile)

    def test_native_workers_share_start_after_priming_and_only_owned_containers_are_removed(self):
        client,containers=self.fake_native()
        result=self.batch(client)
        self.assertTrue(result['passed']);self.assertTrue(result['cleanup'])
        self.assertEqual(len(result['tasks']),2)
        for c in containers:c.remove.assert_called_once()
        self.assertTrue((self.root/'batch/control/release.json').exists())
        self.assertFalse(list((self.root/'batch').glob('worker-*/private')))

    def test_native_oom_is_recorded_and_surviving_peer_is_killed_before_scratch_removal(self):
        client,containers=self.fake_native(oom=1)
        result=self.batch(client)
        self.assertFalse(result['passed']);self.assertTrue(result['cleanup'])
        self.assertEqual(result['failure_kind'],'container-oom')
        self.assertTrue(result['workers_evidence'][0]['oom_killed'])
        containers[1].kill.assert_called_once()
        self.assertFalse((self.root/'batch/control/release.json').exists())

    def test_cancellation_removes_both_workers_and_leaves_cleanup_evidence(self):
        client,containers=self.fake_native(stop=True)
        with self.assertRaises(KeyboardInterrupt):self.batch(client,sleep=KeyboardInterrupt())
        record=json.loads((self.root/'batch/report.json').read_text())
        self.assertTrue(record['cleanup']);self.assertFalse(record['passed'])
        for c in containers:c.remove.assert_called_once();c.kill.assert_called_once()

    def test_unconfirmed_native_cleanup_stops_and_preserves_scratch(self):
        client,_=self.fake_native(remove_error=True)
        with self.assertRaisesRegex(RuntimeError,'cleanup'):self.batch(client)
        record=json.loads((self.root/'batch/report.json').read_text())
        self.assertFalse(record['cleanup']);self.assertFalse(record['passed'])
        self.assertTrue((self.root/'batch/worker-1/private/scratch').is_file())

    def test_barrier_rejects_another_run_and_has_a_bounded_wait(self):
        output=self.root/'out';output.mkdir();control=self.root/'control';control.mkdir()
        args=SimpleNamespace(output=output,control=control,repetitions=2,task_start=1,tasks=1,timeout_seconds=60)
        api.write_json(control/'release.json',dict(format='wrong',released_monotonic_ns=1))
        with self.assertRaisesRegex(ValueError,'barrier'):worker.wait_for_release(args,{})
        (control/'release.json').unlink()
        with patch.object(worker.time,'monotonic',side_effect=[0,61]),self.assertRaises(TimeoutError):worker.wait_for_release(args,{})

    def test_two_real_processes_share_the_host_monotonic_start_marker(self):
        control=self.root/'control';control.mkdir()
        program='''
import json,sys,time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from visualiser_regeneration_worker import wait_for_release
args=SimpleNamespace(output=Path(sys.argv[2]),control=Path(sys.argv[3]),repetitions=2,task_start=int(sys.argv[4]),tasks=1,timeout_seconds=60)
record={};wait_for_release(args,record)
print(json.dumps(dict(record,observed_monotonic_ns=time.monotonic_ns())))
'''
        children=[]
        try:
            for i in (1,2):
                output=self.root/('worker-'+str(i));output.mkdir()
                children.append(subprocess.Popen([sys.executable,'-I','-B','-c',program,str(Path(worker.__file__).parent),
                    str(output),str(control),str(i)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
            deadline=time.monotonic()+10
            while not all((self.root/f'worker-{i}/armed.json').is_file() for i in (1,2)):
                if any(p.poll() is not None for p in children) or time.monotonic()>deadline:self.fail('Barrier workers did not arm')
                time.sleep(.01)
            released=time.monotonic_ns()
            api.write_json(control/'release.json',dict(format='simpaths.visualiser.release.v1',released_monotonic_ns=released))
            for p in children:
                out,err=p.communicate(timeout=10);self.assertEqual(p.returncode,0,err)
                value=json.loads(out);self.assertEqual(value['released_monotonic_ns'],released)
                self.assertGreaterEqual(value['observed_monotonic_ns'],released)
        finally:
            for p in children:
                if p.poll() is None:p.kill();p.wait(timeout=10)

    def test_html_csv_label_per_worker_and_total_resources_and_do_not_hide_failed_rows(self):
        result=dict(self.profile,**driver.check_batch([worker_report(self.profile,[1],100),worker_report(self.profile,[2],100)],self.profile,100),passed=True)
        failed=dict(self.profile,round=2,passed=False,failure_kind='worker-failed')
        values=driver.summaries([result,failed],2)
        self.assertFalse(values[0]['passed']);self.assertEqual(values[0]['passed_trials'],1)
        driver.write_tables(self.root,dict(passed=False,completed=True,summary=values,repetitions=[2]))
        page=(self.root/'comparison.html').read_text()
        for label in ('First result (s)','Both results (s)','GiB / worker','Total vCPU','Total GiB',
                      'FAILED / INCOMPLETE','not two alternatives','successful rounds only','same total CPU allowance'):
            self.assertIn(label,page)
        with (self.root/'comparison.csv').open() as f:rows=list(csv.DictReader(f))
        self.assertEqual(rows[0]['passed_trials'],'1');self.assertEqual(rows[0]['expected_trials'],'2')

    def test_worker_failure_does_not_prevent_remaining_profiles_or_final_tables(self):
        from .test_visualiser_capacity_comparison import CapacityComparisonTests
        fixture_helper=CapacityComparisonTests();fixture_helper.setUp();self.addCleanup(fixture_helper.doCleanups)
        fixture,build,_=fixture_helper.fixture()
        frontend=self.root/'frontend';(frontend/'jasmine_web').mkdir(parents=True)
        (frontend/'jasmine_web/__init__.py').write_text('# Fictional package\n')
        args=SimpleNamespace(**vars(self.args),frontend=frontend,fixture=fixture,source_build=build,build=build,
            output=self.root/'evidence',memory_mib=[2560],cpu_millis=[1000],workers=[1],repetitions=[2,5],rounds=1)
        client=Mock();client.info.return_value={'CgroupVersion':'2'};client.images.get.return_value.id=self.args.image_id
        calls=[]
        def batch(_,__,___,directory,profile,**kw):
            calls.append(profile)
            if len(calls)==1:return dict(profile,passed=False,cleanup=True,failure_kind='worker-failed')
            return dict(profile,passed=True,cleanup=True,
                **driver.check_batch([worker_report(profile,[1,2],100)],profile,100))
        with patch('docker.from_env',return_value=client),patch.object(driver,'run_batch',side_effect=batch), \
             patch.object(driver.legacy,'preflight_space',return_value={}):self.assertEqual(driver.run(args),1)
        report=json.loads((args.output/'report.json').read_text())
        self.assertTrue(report['completed']);self.assertFalse(report['passed']);self.assertTrue(report['cleanup'])
        self.assertEqual(len(report['cases']),2);self.assertTrue(report['cases'][1]['passed'])
        self.assertTrue((args.output/'comparison.html').is_file())
        subprocess.run([sys.executable,'-I','-B','-c','import sys;sys.path.insert(0,sys.argv[1]);import visualiser_regeneration_worker',
            str(args.output/'snapshot/tools')],check=True,timeout=10)


@unittest.skipUnless(BUILD and shutil.which('node'),'Set SIMPATHS_VISUALISER_TEST_BUILD for tiny real pipelines')
class RegenerationPipelineTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.RepetitionPipelineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_real_sequential_tasks_use_independent_cache_identities_and_identical_chart_bytes(self):
        f=self.f
        args=SimpleNamespace(**vars(f.args));args.repetitions=2;args.tasks=2;args.task_start=1;args.round=1
        sampler=SimpleNamespace(stage='',peaks={},capture=lambda:{})
        record=dict(fixture_manifest=api.receipt(args.fixture,'manifest.json',api.MIB),backend=f.manifest['backend'])
        released=time.monotonic_ns()
        with patch.object(worker,'wait_for_release',side_effect=lambda a,r:r.update(released_monotonic_ns=released)):
            worker.measure(args,f.manifest,f.built,f.work,sampler,record)
        self.assertEqual([c['task'] for c in record['cases']],[1,2])
        self.assertEqual(record['cases'][0]['result_identity'],record['cases'][1]['result_identity'])
        self.assertLess(record['cases'][0]['ready_monotonic_ns'],record['cases'][1]['started_monotonic_ns'])
        caches=[base.SummaryCache(f.work/f'task-{i}/summaries') for i in (1,2)]
        keys=[]
        for cache in caches:
            with cache._lock('.cache.lock'):keys.append({entry[1] for entry in cache._entries()})
            self.assertEqual(cache.statistics()['counters']['hits'],4)
            self.assertEqual(cache.statistics()['counters']['raw_started'],0)
        self.assertEqual(len(keys[0]),4);self.assertFalse(keys[0]&keys[1])
        self.assertFalse(list(f.work.glob('trial-*')))

    def test_complete_worker_verifies_exact_2_5g_quota_samples_and_cleans_both_tasks(self):
        f=self.f;group=f.root/'cgroup';group.mkdir()
        values={'memory.max':str(2560*api.MIB),'memory.swap.max':'0','cpu.max':'200000 100000',
            'memory.current':'1048576','memory.peak':'2097152','memory.stat':'inactive_file 262144\n',
            'memory.events':'max 0\noom 0\noom_kill 0\n',
            'cpu.stat':'usage_usec 10\nnr_periods 2\nnr_throttled 0\nthrottled_usec 0\n',
            'io.stat':'8:0 rbytes=16 wbytes=32 rios=1 wios=2\n'}
        for kind in ('cpu','memory','io'):values[kind+'.pressure']='some avg10=0.00 avg60=0.00 avg300=0.00 total=8\n'
        for name,value in values.items():(group/name).write_text(value)
        args=SimpleNamespace(**vars(f.args));args.output=f.root/'complete-worker';args.control=f.root/'control'
        args.control.mkdir();args.memory_mib=2560;args.cpu_millis=2000
        args.repetitions=2;args.round=1;args.tasks=2;args.task_start=1;args.timeout_seconds=60
        snapshot,limits,barrier=api.snapshot,api.require_limits,worker.wait_for_release
        def release(a,record):
            api.write_json(a.control/'release.json',dict(format='simpaths.visualiser.release.v1',released_monotonic_ns=time.monotonic_ns()))
            barrier(a,record)
        with patch.object(api,'snapshot',side_effect=lambda _root=api.CGROUP:snapshot(group)), \
             patch.object(api,'require_limits',side_effect=lambda m,**kw:limits(m,group,**kw)), \
             patch.object(worker,'wait_for_release',side_effect=release):worker.execute(args)
        report=json.loads((args.output/'report.json').read_text())
        self.assertTrue(report['passed']);self.assertTrue(report['cleanup']);self.assertTrue(report['sources_unchanged'])
        self.assertEqual(report['limits']['memory_max_bytes'],2560*api.MIB)
        self.assertFalse((args.output/'private').exists());self.assertTrue((args.output/'samples.jsonl').is_file())
        result=driver.check_batch([report],dict(round=1,repetitions=2,memory_mib=2560,cpu_millis=2000,workers=1),report['released_monotonic_ns'])
        self.assertGreater(result['both_results_seconds'],result['first_result_seconds'])


if __name__=='__main__':unittest.main()
