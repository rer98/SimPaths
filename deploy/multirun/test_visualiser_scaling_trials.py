"""(C) Copyright 2026, by Ross Richardson

Dependency readiness, bounded seed parallelism, cancellation and actual maintained
parser/publication integration checks for the preparation scaling benchmark.
@author ross richardson
"""
import copy
import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from . import visualiser_scaling_trials as driver
from . import visualiser_scaling_worker as worker
from . import test_visualiser_summary_trials as fixtures
from . import test_visualiser_capacity_worker as cgroup_tests
from . import test_visualiser_capacity_comparison as build_tests
from .artifacts import fingerprint
api=worker.api
BUILD=os.environ.get('SIMPATHS_VISUALISER_TEST_BUILD')
FRONTEND=fixtures.FRONTEND


def profile(name):return dict(name=name,round=1,repetitions=2,containers=1,**worker.PROFILES[name])


def complete_report(p,manifest,released=100,backlog=4):
    graph=worker.Graph(1 if p['scope']=='single' else 2);tick=released
    while not graph.complete:
        key=graph.ready()[0];node=graph.nodes[key];graph.start(key)
        value=dict(id=key,kind=node['kind'],config=node['config'],passed=True,
            started_monotonic_ns=tick,ready_monotonic_ns=tick+10,finished_monotonic_ns=tick+11,
            stages=dict(verified=dict(passed=True)))
        if node['kind']=='seed':
            seed=manifest['seeds'][node['run']]
            value.update(configuration=manifest['configurations'][node['config']]['id'],seed=seed,
                summary=dict(bytes=100,sha256=str(node['config']+node['run']+1)*64),
                validation=dict(seed=seed,annual_years=manifest['annual_years'],canonical_role='baseline',rows=52))
        else:
            value.update(**{k:dict(sha256='a'*64) for k in ('publication','index','catalogue','view','validation')})
            value['result_identity']={k:value[k] for k in ('publication','index','catalogue','view','validation')}
        graph.finish(key,value);tick+=20
    wanted=backlog if p['scope']=='experiment' else 0
    record=dict(profile=p['name'],**worker.PROFILES[p['name']],passed=True,cleanup=True,sources_unchanged=True,
        raw_parsing=True,released_monotonic_ns=released,cases=list(graph.results.values()),
        experiment_finished_monotonic_ns=max(v['ready_monotonic_ns'] for v in graph.results.values()),
        cache_statistics=dict(counters=dict(misses=2 if p['scope']=='single' else 4,
            raw_started=2 if p['scope']=='single' else 4,raw_completed=2 if p['scope']=='single' else 4,
            hits=2 if p['scope']=='single' else 8+4*wanted,raw_failed=0,evictions=0)),
        sampled_peaks={'raw-and-ready-publications':dict(working_bytes=100,memory_current_bytes=200)})
    if wanted:
        tasks=[]
        for i in range(wanted):
            value=copy.deepcopy(graph.results['pair']);value.update(id='replay-pair-'+str(i),
                started_monotonic_ns=tick+i*20,ready_monotonic_ns=tick+i*20+10,finished_monotonic_ns=tick+i*20+11)
            tasks.append(value)
        record['publication_backlog']=dict(replayed_pair_tasks=wanted,started_monotonic_ns=tick,tasks=tasks)
    return record


class ScalingGuards(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        helper=build_tests.CapacityComparisonTests();helper.setUp();self.addCleanup(helper.doCleanups)
        self.build=helper.build('original');self.fixture,self.raw,self.manifest=fixtures.raw_fixture(self.root,self.build)

    def test_plan_keeps_total_single_budget_fixed_and_scales_one_group(self):
        plan=driver.plan(list(worker.PROFILES),3)
        self.assertEqual(len(plan),18)
        self.assertEqual({p['containers'] for p in plan},{1})
        self.assertEqual({p['repetitions'] for p in plan},{2})
        for name in ('single-serial','single-parallel'):
            p=worker.PROFILES[name];self.assertEqual((p['memory_mib'],p['cpu_millis']),(5120,2000))
        for w in (1,2,4):
            p=worker.PROFILES['scaling-'+str(w)]
            self.assertEqual((p['workers'],p['memory_mib'],p['cpu_millis']),(w,2560*w,1000*w))
        self.assertNotEqual([p['name'] for p in plan[:6]],[p['name'] for p in plan[6:12]])
        for names,rounds in [([],3),(['unknown'],1),(['single-serial']*2,1),(['single-serial'],0)]:
            with self.subTest(names=names,rounds=rounds),self.assertRaises(ValueError):driver.plan(names,rounds)

    def finish(self,g,key):g.start(key);g.finish(key,dict(passed=True))

    def test_alternative_can_publish_alone_but_pair_waits_for_baseline_summaries(self):
        g=worker.Graph(2)
        for key in ('seed-1-0','seed-1-1'):self.finish(g,key)
        self.assertIn('standalone-1',g.ready());self.assertNotIn('pair',g.ready())
        self.finish(g,'seed-0-0');self.assertNotIn('pair',g.ready())
        self.finish(g,'seed-0-1')
        self.assertEqual(g.ready()[0],'pair')
        self.assertEqual(g.states['standalone-1'],'pending')

    def test_late_alternative_summary_immediately_unlocks_pair_before_standalone(self):
        g=worker.Graph(2)
        self.assertEqual(g.ready()[:2],['seed-0-0','seed-0-1'])
        for key in ('seed-0-0','seed-0-1','standalone-0','seed-1-0'):self.finish(g,key)
        self.assertNotIn('pair',g.ready());self.finish(g,'seed-1-1')
        self.assertEqual(g.ready()[:2],['pair','standalone-1'])
        with self.assertRaises(ValueError):g.start('seed-0-0')
        self.finish(g,'pair');self.assertFalse(g.complete);self.finish(g,'standalone-1');self.assertTrue(g.complete)

    def test_unfinished_summaries_or_failed_tasks_cannot_unlock_charts(self):
        g=worker.Graph(1);g.start('seed-0-0')
        with self.assertRaises(ValueError):g.finish('seed-0-0',dict(passed=False))
        with self.assertRaises(ValueError):g.start('standalone-0')
        self.assertNotIn('standalone-0',g.ready())

    def test_whole_group_limits_include_all_children_and_require_zero_swap(self):
        h=cgroup_tests.CapacityWorkerTests();h.setUp();self.addCleanup(h.doCleanups);group=h.cgroup()
        p=worker.PROFILES['scaling-4']
        (group/'memory.max').write_text(str(10240*api.MIB));(group/'cpu.max').write_text('400000 100000')
        self.assertEqual(worker.require_limits(p,group)['memory_max_bytes'],10240*api.MIB)
        for name,value in [('memory.max',str(2560*api.MIB)),('memory.swap.max','1'),('cpu.max','100000 100000')]:
            before=(group/name).read_text();(group/name).write_text(value)
            with self.subTest(name=name),self.assertRaises(ValueError):worker.require_limits(p,group)
            (group/name).write_text(before)

    def test_container_mounts_and_quotas_enforce_total_not_per_worker_budget(self):
        args=SimpleNamespace(image_id='sha256:'+'a'*64,raw_root=self.raw,raw_manifest=self.manifest,
            timeout_seconds=3600,publication_backlog=4)
        p=profile('single-parallel')
        options=driver.options(args,self.root/'snapshot',self.root/'out',self.root/'control',p,0,'identity')
        self.assertEqual(options['mem_limit'],5120*api.MIB)
        self.assertEqual(options['memswap_limit'],options['mem_limit']);self.assertEqual(options['nano_cpus'],2_000_000_000)
        self.assertTrue(options['read_only']);self.assertEqual(options['network_mode'],'none')
        self.assertEqual(options['cap_drop'],['ALL']);self.assertNotIn('docker.sock',str(options))
        self.assertEqual([v['bind'] for v in options['volumes'].values() if v['mode']=='rw'],['/out'])
        self.assertNotIn('--memory-mib',options['command']);self.assertIn('single-parallel',options['command'])
        driver.check_armed(worker.armed('single-parallel'),0,p)
        with self.assertRaises(ValueError):driver.check_armed(worker.armed('single-serial'),0,p)
        with self.assertRaises(ValueError):driver.options(args,self.root/'snapshot',self.root/'out',self.root/'control',p,1,'identity')

    def test_source_identities_dependencies_and_complete_stages_are_checked(self):
        p=profile('scaling-2');good=complete_report(p,self.manifest)
        value=driver.check_results([good],p,100,manifest=self.manifest,backlog=4)
        self.assertEqual(len(value['seed_identity']),4);self.assertEqual(len(value['publication_identity']),3)
        self.assertEqual(value['configurations_per_allocated_vcpu_hour'],value['configurations_per_hour']/2)
        for kind in ('missing','early','seed','raw-skipped','cache-eviction','publication','backlog','quota','stage'):
            report=copy.deepcopy(good)
            if kind=='missing':report['cases'].pop()
            elif kind=='early':next(c for c in report['cases'] if c['id']=='pair')['started_monotonic_ns']=100
            elif kind=='seed':report['cases'][0]['validation']['seed']='608'
            elif kind=='raw-skipped':report['cache_statistics']['counters']['raw_completed']=3
            elif kind=='cache-eviction':report['cache_statistics']['counters']['evictions']=1
            elif kind=='publication':next(c for c in report['cases'] if c['id']=='pair')['result_identity']={}
            elif kind=='backlog':report['publication_backlog']['tasks'].pop()
            elif kind=='quota':report['memory_mib']=10240
            else:report['cases'][0]['stages']['verified']['passed']=False
            with self.subTest(kind=kind),self.assertRaises(ValueError):driver.check_results([report],p,100,manifest=self.manifest,backlog=4)

    def test_two_valid_rounds_use_mean_but_peaks_remain_maximum(self):
        p=profile('single-parallel');rows=[]
        for r,t,m in [(1,10,100),(2,30,200)]:
            rows.append(dict(p,round=r,passed=True,fully_ready_seconds=t,first_chart_seconds=t,
                sampled_working_peak_bytes=m,sampled_memory_peak_bytes=m*2,main_metrics={}))
        rows.append(dict(p,round=3,passed=False,failure_kind='sampler-gap'))
        summary=driver.summaries(rows,3);self.assertEqual(summary[0]['fully_ready_seconds'],20)
        self.assertEqual(summary[0]['timing_statistic'],'mean');self.assertEqual(summary[0]['sampled_working_peak_bytes'],200)
        self.assertFalse(summary[0]['passed'])
        driver.write_tables(self.root,dict(completed=True,passed=False,summary=summary))
        page=(self.root/'comparison.html').read_text()
        for text in ('TOTAL RAM GiB','Group working peak GiB','First standalone charts (min)',
                'FAILED / INCOMPLETE','not four alternative policies','whole-group'):
            self.assertIn(text,page)
        with (self.root/'comparison.csv').open() as f:self.assertEqual(next(csv.DictReader(f))['passed_rounds'],'2')

    def test_preflight_keeps_host_headroom_and_rejects_too_little_disk(self):
        with patch.object(driver.shutil,'disk_usage',return_value=SimpleNamespace(free=9*driver.legacy.GIB)), \
             patch.object(driver.legacy,'available_memory',return_value=11*driver.legacy.GIB):
            with self.assertRaisesRegex(ValueError,'RAM'):driver.preflight(self.root,profile('scaling-4'))
            driver.preflight(self.root,profile('scaling-2'))
        with patch.object(driver.shutil,'disk_usage',return_value=SimpleNamespace(free=7*driver.legacy.GIB)), \
             patch.object(driver.legacy,'available_memory',return_value=32*driver.legacy.GIB):
            with self.assertRaisesRegex(ValueError,'8 GiB'):driver.preflight(self.root,profile('single-compact'))

    def test_child_process_cancellation_joins_and_leaves_no_running_command(self):
        commands=worker.Commands();errors=[]
        with (self.root/'child.log').open('xb') as log:
            def run():
                try:commands.run(log,[sys.executable,'-c','import time;time.sleep(60)'])
                except BaseException as error:errors.append(error)
            thread=threading.Thread(target=run);thread.start();deadline=time.monotonic()+3
            while not commands.running and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(commands.running);pid=next(iter(commands.running));commands.cancel.set();thread.join(3)
            self.assertFalse(thread.is_alive());self.assertFalse(commands.running)
            self.assertEqual(len(errors),1)
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    def test_early_limit_failure_is_retained_without_leaving_scratch(self):
        args=SimpleNamespace(output=self.root/'out',profile='scaling-4')
        with patch.object(worker,'require_limits',side_effect=ValueError('quota')),self.assertRaises(ValueError):worker.execute(args)
        record=api.load_small(args.output,'report.json')
        self.assertTrue(record['cleanup']);self.assertFalse(record['passed']);self.assertEqual(record['failure_kind'],'integrity-failed')
        self.assertFalse((args.output/'private').exists())

    def run_driver(self,*,resource_failure=False,changed=False):
        args=SimpleNamespace(frontend=FRONTEND,fixture=self.fixture,source_build=self.build,build=self.build,
            output=self.root/'output',image='test',profiles=['single-serial','scaling-2'],rounds=1,
            publication_backlog=4,timeout_seconds=60,overall_timeout_seconds=300)
        client=Mock();client.info.return_value={'CgroupVersion':'2'};client.images.get.return_value.id='sha256:'+'a'*64
        calls=[]
        def batch(_,__,snapshot,directory,p,**kwargs):
            calls.append(p);kwargs['armed_checker'](worker.armed(p['name']),0,p)
            if resource_failure and len(calls)==1:
                return dict(p,passed=False,cleanup=True,failure_kind='container-oom',workers_evidence=[])
            record=complete_report(p,self.manifest)
            if changed and len(calls)==2:record['cases'][0]['summary']['sha256']='f'*64
            result=kwargs['result_checker']([record],p,100)
            return dict(p,passed=True,cleanup=True,workers_evidence=[],**result)
        with patch('docker.from_env',return_value=client),patch.object(driver.native,'run_batch',side_effect=batch), \
             patch.object(driver,'preflight',return_value={}):code=driver.run(args)
        return code,api.load_small(args.output,'report.json'),calls,args.output

    def test_driver_snapshots_every_worker_dependency_and_passes_real_callbacks(self):
        code,record,calls,output=self.run_driver()
        self.assertEqual(code,0);self.assertTrue(record['passed']);self.assertEqual(len(calls),2)
        self.assertFalse(list((output/'snapshot').rglob('*.csv')))
        subprocess.run([sys.executable,'-I','-B','-c',
            'import sys;sys.path.insert(0,sys.argv[1]);import visualiser_scaling_worker',
            str(output/'snapshot/tools')],check=True,timeout=10)
        self.assertTrue((output/'comparison.html').is_file())

    def test_resource_failure_preserves_tables_and_continues_remaining_profiles(self):
        code,record,calls,_=self.run_driver(resource_failure=True)
        self.assertEqual(code,1);self.assertTrue(record['completed']);self.assertFalse(record['passed'])
        self.assertEqual(len(calls),2);self.assertTrue(record['cases'][1]['passed'])

    def test_driver_rejects_changed_science_between_serial_and_parallel_profiles(self):
        code,record,_,_=self.run_driver(changed=True)
        self.assertEqual(code,1);self.assertFalse(record['completed'])
        self.assertIn('Calculated output changed',record['error_message'])

    def test_native_controller_launches_one_container_for_four_workers_and_cleans(self):
        p=profile('scaling-4');args=SimpleNamespace(timeout_seconds=60)
        client=Mock();containers=[];directory=self.root/'batch'
        def create(**options):
            out=directory/'worker-1';control=directory/'control';c=Mock();c.id='fictional-scaling'
            c.attrs=dict(Config={'Labels':options['labels']},State=dict(Running=False,ExitCode=0,OOMKilled=False))
            def start():
                c.attrs['State']['Running']=True;(out/'private').mkdir();(out/'private'/'scratch').write_text('test')
                api.write_json(out/'armed.json',worker.armed(p['name']))
            def reload():
                if c.attrs['State']['Running'] and (control/'release.json').exists():
                    released=api.load_small(control,'release.json')['released_monotonic_ns']
                    api.write_json(out/'report.json',complete_report(p,self.manifest,released))
                    c.attrs['State']['Running']=False
            c.start.side_effect=start;c.reload.side_effect=reload;c.logs.return_value=b'fictional scaling'
            containers.append(c);return c
        client.containers.create.side_effect=create
        def options(*values):return dict(labels={driver.legacy.LABEL:values[-1]})
        with patch.object(driver.legacy,'preflight_space',return_value={}),patch.object(driver.native.time,'sleep'):
            record=driver.native.run_batch(client,args,self.root/'snapshot',directory,p,options_factory=options,
                armed_checker=driver.check_armed,result_checker=lambda reports,p,start:
                    driver.check_results(reports,p,start,manifest=self.manifest,backlog=4))
        self.assertTrue(record['passed']);self.assertTrue(record['cleanup']);self.assertEqual(len(containers),1)
        containers[0].remove.assert_called_once();self.assertFalse((directory/'worker-1'/'private').exists())


@unittest.skipUnless(BUILD and shutil.which('node'),'Set SIMPATHS_VISUALISER_TEST_BUILD for maintained parser/chart checks')
class ScalingRealPipeline(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name);self.build=Path(BUILD)
        self.fixture,self.raw,self.manifest=fixtures.raw_fixture(self.root,self.build,real_csv=True)
        h=cgroup_tests.CapacityWorkerTests();h.setUp();self.addCleanup(h.doCleanups);self.group=h.cgroup()

    def execute(self,name,*,backlog=0):
        p=profile(name);control=self.root/(name+'-control');control.mkdir(mode=0o700)
        args=SimpleNamespace(frontend=FRONTEND,fixture=self.fixture,build=self.build,raw_root=self.raw,
            output=self.root/name,control=control,timeout_seconds=60,profile=name,publication_backlog=backlog)
        (self.group/'memory.max').write_text(str(p['memory_mib']*api.MIB))
        (self.group/'cpu.max').write_text(str(p['cpu_millis']*100)+' 100000')
        limits,snapshot,barrier=worker.require_limits,api.snapshot,worker.release
        def release(a,record):
            api.write_json(a.control/'release.json',dict(format='simpaths.visualiser.release.v1',released_monotonic_ns=time.monotonic_ns()))
            barrier(a,record)
        with patch.object(worker,'require_limits',side_effect=lambda value:limits(value,self.group)), \
             patch.object(api,'snapshot',side_effect=lambda _root=api.CGROUP:snapshot(self.group)), \
             patch.object(worker,'release',side_effect=release):worker.execute(args)
        record=api.load_small(args.output,'report.json')
        checked=driver.check_results([record],p,record['released_monotonic_ns'],manifest=self.manifest,backlog=backlog)
        self.assertTrue(record['passed']);self.assertTrue(record['cleanup']);self.assertFalse((args.output/'private').exists())
        self.assertTrue((args.output/'samples.jsonl').is_file())
        return record,checked

    def test_one_configuration_serial_and_parallel_have_identical_seed_and_chart_data(self):
        before={n:fingerprint(self.raw/n) for n in self.manifest['files']}
        serial,s=self.execute('single-serial');parallel,p=self.execute('single-parallel')
        self.assertEqual(s['seed_identity'],p['seed_identity']);self.assertEqual(s['publication_identity'],p['publication_identity'])
        self.assertEqual(serial['cache_statistics']['counters']['raw_completed'],2)
        self.assertEqual(parallel['cache_statistics']['counters']['hits'],2)
        seeds=[c for c in parallel['cases'] if c['kind']=='seed']
        self.assertLess(max(c['started_monotonic_ns'] for c in seeds),min(c['finished_monotonic_ns'] for c in seeds))
        self.assertEqual(before,{n:fingerprint(self.raw/n) for n in self.manifest['files']})

    def test_one_two_four_workers_prepare_real_standalone_and_paired_charts_consistently(self):
        reference=None
        for name in ('scaling-1','scaling-2','scaling-4'):
            record,value=self.execute(name,backlog=4)
            identity=(value['seed_identity'],value['publication_identity'])
            if reference is None:reference=identity
            self.assertEqual(reference,identity)
            self.assertEqual(record['cache_statistics']['counters']['raw_completed'],4)
            self.assertEqual(record['cache_statistics']['counters']['hits'],24)
            self.assertEqual(len(record['cases']),7);self.assertEqual(len(record['publication_backlog']['tasks']),4)
            self.assertEqual({c['seed'] for c in record['cases'] if c['kind']=='seed'},{'606','607'})

    def test_timed_source_verification_rejects_same_length_corruption_and_cleans(self):
        target=self.raw/next(iter(self.manifest['files']));target.write_bytes(b'x'*target.stat().st_size)
        with self.assertRaisesRegex(ValueError,'verification'):self.execute('single-parallel')
        record=api.load_small(self.root/'single-parallel','report.json')
        self.assertFalse(record['passed']);self.assertTrue(record['cleanup'])
        self.assertFalse((self.root/'single-parallel'/'private').exists())
        tasks=list((self.root/'single-parallel'/'tasks').glob('*/report.json'))
        self.assertTrue(tasks);self.assertTrue(any(not api.load_small(p.parent,p.name)['passed'] for p in tasks))

    def test_sampler_gap_preserves_cause_and_fails_even_after_valid_charts(self):
        original=worker.Preparation.graph
        def fail(prep,g):
            original(prep,g);prep.sampler.errors.append('ValueError')
            prep.sampler.error_details.append(dict(error_type='ValueError',message='fictional scaling sample gap',
                stage='raw-and-ready-publications',elapsed_seconds=1.,wall_time_ns=1))
        with patch.object(worker.Preparation,'graph',fail),self.assertRaisesRegex(RuntimeError,'fictional scaling sample gap'):
            self.execute('single-compact')
        record=api.load_small(self.root/'single-compact','report.json')
        self.assertTrue(record['cleanup']);self.assertFalse(record['passed'])
        self.assertEqual(record['sampling_errors'][0]['message'],'fictional scaling sample gap')
        self.assertTrue(all(c['passed'] for c in record['cases']))


if __name__=='__main__':unittest.main()
