#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Prototype seed-parallel preparation in one enforced CPU/RAM group. Schedule
standalone and paired chart publication when their actual summaries are ready.
@author ross richardson
"""
import argparse
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from contextlib import ExitStack,contextmanager
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

if __package__:
    from . import visualiser_capacity_worker as api
    from .visualiser_summary_worker import validate_summary
    from .visualiser_summary_cache import SummaryCache
else:
    import visualiser_capacity_worker as api
    from visualiser_summary_worker import validate_summary
    from visualiser_summary_cache import SummaryCache

MIB=1024**2
PROFILES={
    'single-compact':dict(scope='single',workers=1,memory_mib=2560,cpu_millis=1000,process_memory_mib=2560),
    'single-serial':dict(scope='single',workers=1,memory_mib=5120,cpu_millis=2000,process_memory_mib=5120),
    'single-parallel':dict(scope='single',workers=2,memory_mib=5120,cpu_millis=2000,process_memory_mib=2560),
    'scaling-1':dict(scope='experiment',workers=1,memory_mib=2560,cpu_millis=1000,process_memory_mib=2560),
    'scaling-2':dict(scope='experiment',workers=2,memory_mib=5120,cpu_millis=2000,process_memory_mib=2560),
    'scaling-4':dict(scope='experiment',workers=4,memory_mib=10240,cpu_millis=4000,process_memory_mib=2560)}


def require_limits(profile,root=api.CGROUP):
    memory=(root/'memory.max').read_text().strip();swap=(root/'memory.swap.max').read_text().strip()
    cpu=(root/'cpu.max').read_text().split()
    if (memory!=str(profile['memory_mib']*MIB) or swap!='0' or len(cpu)!=2 or not all(x.isdigit() for x in cpu)
            or int(cpu[1])<1 or int(cpu[0])*1000!=profile['cpu_millis']*int(cpu[1])):
        raise ValueError('Require the exact whole-group memory/CPU limits and no swap')
    return dict(memory_max_bytes=int(memory),memory_swap_max_bytes=0,cpu_max=[int(x) for x in cpu])


def armed(profile_name):
    return dict(format='simpaths.visualiser.scaling-armed.v1',profile=profile_name,**PROFILES[profile_name])


def release(args,record):
    api.write_json(args.output/'armed.json',armed(args.profile))
    deadline=time.monotonic()+args.timeout_seconds
    while not (args.control/'release.json').is_file():
        if time.monotonic()>=deadline:raise TimeoutError('Common start barrier was not released')
        time.sleep(.025)
    value=api.load_small(args.control,'release.json')
    if (set(value)!={'format','released_monotonic_ns'} or value['format']!='simpaths.visualiser.release.v1'
            or type(value['released_monotonic_ns']) is not int or not 0<value['released_monotonic_ns']<=time.monotonic_ns()):
        raise ValueError('Invalid common start marker')
    record['released_monotonic_ns']=value['released_monotonic_ns']


class Graph:
    """Dependencies are summary completions, never simulation-finished notices."""
    def __init__(self,configurations):
        if configurations not in (1,2):raise ValueError('Use one or two genuine configurations')
        self.nodes={};self.states={};self.results={}
        for c in range(configurations):
            seeds={f'seed-{c}-{r}' for r in range(2)}
            for r in range(2):self.nodes[f'seed-{c}-{r}']=dict(kind='seed',config=c,run=r,needs=set())
            self.nodes[f'standalone-{c}']=dict(kind='standalone',config=c,needs=seeds)
        if configurations==2:
            self.nodes['pair']=dict(kind='pair',config=1,needs={f'seed-{c}-{r}' for c in range(2) for r in range(2)})
        self.states=dict.fromkeys(self.nodes,'pending')

    def ready(self):
        def order(key):
            n=self.nodes[key]
            return ({'pair':0,'standalone':1,'seed':2}[n['kind']],n['config'],n.get('run',0),key)
        return sorted((k for k,n in self.nodes.items() if self.states[k]=='pending'
            and all(self.states[d]=='done' for d in n['needs'])),key=order)

    def start(self,key):
        if key not in self.ready():raise ValueError('Preparation prerequisites are not ready')
        self.states[key]='running'

    def finish(self,key,result):
        if self.states.get(key)!='running' or not result.get('passed'):raise ValueError('Incomplete graph task')
        self.states[key]='done';self.results[key]=result

    @property
    def complete(self):return all(s=='done' for s in self.states.values())


class Commands:
    """Cancel running child groups promptly before joining threads or cleaning."""
    def __init__(self):
        self.cancel=threading.Event();self.lock=threading.Lock();self.running=set()

    def run(self,log,argv,*,pass_fds=()):
        if self.cancel.is_set():raise RuntimeError('Preparation cancelled')
        child=subprocess.Popen(list(map(str,argv)),stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            pass_fds=pass_fds,start_new_session=True,env={'PATH':os.defpath,'LANG':'C.UTF-8'})
        with self.lock:self.running.add(child.pid)
        try:
            deadline=time.monotonic()+3600
            while child.poll() is None:
                if self.cancel.wait(.05):raise RuntimeError('Preparation cancelled')
                if time.monotonic()>=deadline:raise TimeoutError('Private helper exceeded its time bound')
                if os.fstat(log.fileno()).st_size>2*MIB:raise RuntimeError('Private task log exceeded its bound')
            if child.returncode:raise subprocess.CalledProcessError(child.returncode,argv)
        except BaseException:
            try:os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                child.wait(timeout=5)
            raise
        finally:
            with self.lock:self.running.discard(child.pid)


class TaskSampler:
    """Snapshots cover the whole group; never attribute concurrent RAM to a task."""
    def __init__(self,parent):self.parent=parent;self.stage=''
    @property
    def peaks(self):return {}
    def capture(self):return self.parent.capture()


def summary_identity(manifest,fixture_receipt,config,run):
    return dict(owner='scaling-capacity',configuration=config['id'],seed=run['seed'],
        fixture=fixture_receipt,backend=manifest['backend'],
        sources={k:manifest['files'][run[k]] for k in ('person','benefit')})


def validate_standalone(paths,manifest):
    if len(paths)!=1:raise ValueError('Standalone publication must contain one configuration')
    rows=api.load_small(paths[0].parent,paths[0].name,96*MIB)
    if not isinstance(rows,list) or not rows:raise ValueError('Empty standalone statistics')
    if sorted({r['year'] for r in rows})!=manifest['annual_years']:raise ValueError('Standalone annual coverage differs')
    for row in rows:
        if type(row.get('n_runs')) is not int or not 1<=row['n_runs']<=2 or row.get('paired_n_runs',0):
            raise ValueError('Standalone seed count or paired statistics differ')
    return dict(rows_per_configuration=[len(rows)],annual_years=manifest['annual_years'],seed_labels=manifest['seeds'])


class Preparation:
    def __init__(self,args,manifest,built,work,sampler,record):
        self.args,self.manifest,self.built,self.work,self.sampler,self.record=args,manifest,built,work,sampler,record
        self.profile=PROFILES[args.profile];self.commands=Commands()
        self.cache=SummaryCache(work/'summaries',maximum_bytes=2*1024**3,maximum_entries=16)
        self.node=shutil.which('node')
        if not self.node:raise RuntimeError('Node is unavailable')

    def seed(self,node,folder,result,log):
        from jasmine_web.batch.results import verified_file,OutputUnavailable
        config=self.manifest['configurations'][node['config']];run=config['runs'][node['run']]
        key=summary_identity(self.manifest,self.record['fixture_manifest'],config,run)
        output=folder/'metrics.json';result.update(configuration=config['id'],seed=run['seed'],stages={})
        @contextmanager
        def stage(name):
            value=dict(passed=False);result['stages'][name]=value;tick=time.monotonic()
            try:yield;value['passed']=True
            finally:value['elapsed_seconds']=time.monotonic()-tick
        lock_started=time.monotonic()
        with self.cache.entry(key) as entry:
            result['cache_lock_wait_seconds']=time.monotonic()-lock_started
            if entry.restore(output):raise ValueError('Fresh raw work unexpectedly reused a summary')
            with ExitStack() as stack:
                with stage('source_verification'):
                    try:
                        sources=[stack.enter_context(verified_file(self.args.raw_root,
                            dict(path=run[k],**self.manifest['files'][run[k]]))) for k in ('person','benefit')]
                    except (OutputUnavailable,OSError) as error:raise ValueError('Raw source verification failed') from error
                    initial=[os.fstat(s.fileno()) for s in sources]
                result['logical_input_bytes']=sum(self.manifest['files'][run[k]]['bytes'] for k in ('person','benefit'))
                with stage('raw_summarisation'),entry.raw_processing(result['logical_input_bytes']):
                    api.write_json(folder/'request.json',dict(operation='run',role='baseline',run=run['seed'],
                        person_fd=sources[0].fileno(),benefit_fd=sources[1].fileno(),output=str(output)))
                    self.commands.run(log,[self.node,'--max-old-space-size='+str(self.profile['process_memory_mib']-1024),
                        self.args.build/'runner.cjs',folder/'request.json'],pass_fds=tuple(s.fileno() for s in sources))
                    for before,source in zip(initial,sources):
                        after=os.fstat(source.fileno())
                        if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                            raise ValueError('Raw source changed during parsing')
                with stage('validate_and_retain'):
                    result['validation']=validate_summary(output,run['seed'],self.manifest['annual_years'])
                    result['summary']=api.receipt(folder,output.name,64*MIB)
                    if not entry.publish(output) or api.receipt(self.cache.root/entry.key,'metrics.json',64*MIB)!=result['summary']:
                        raise ValueError('Retained seed receipt differs')
                    result['retained_key']=entry.key
        result['ready_monotonic_ns']=time.monotonic_ns()

    def publication(self,node,folder,evidence,result,log):
        selected=dict(self.manifest)
        configs=self.manifest['configurations'] if node['kind']=='pair' else [dict(self.manifest['configurations'][node['config']],role='baseline')]
        selected['configurations']=configs
        def restore(paths):
            for path,(config,run) in zip(paths,[(c,r) for c in configs for r in c['runs']]):
                identity=summary_identity(self.manifest,self.record['fixture_manifest'],config,run)
                with self.cache.entry(identity) as entry:
                    if not entry.restore(path):raise ValueError('A required intermediate summary is unavailable')
        settings=argparse.Namespace(**vars(self.args));settings.memory_mib=self.profile['process_memory_mib'];settings.output=evidence
        # The same maintained combination/publication/index/catalogue/read code
        # used by earlier tests; commands remain inside this one cgroup budget.
        def command(task_log,argv,*,pass_fds=()):return self.commands.run(task_log,argv,pass_fds=pass_fds)
        api.run_pipeline(settings,selected,self.built,folder,TaskSampler(self.sampler),result,
            summary_restore=restore,rows_validator=validate_standalone if node['kind']=='standalone' else None,
            command_runner=command)
        result['result_identity']={k:result[k] for k in ('publication','index','catalogue','view','validation')}
        # Keep genuine published/indexed data available until the case ends;
        # release only transient metric copies, request and combined-row files.
        for path in folder.iterdir():
            if path.is_file() and path.name!='published.json':path.unlink()

    def task(self,key,node,*,ephemeral=False):
        folder=self.work/key;folder.mkdir(mode=0o700)
        evidence=self.args.output/'tasks'/key;evidence.mkdir(mode=0o700,parents=True)
        result=dict(id=key,kind=node['kind'],config=node['config'],passed=False,stages={},
            started_monotonic_ns=time.monotonic_ns())
        try:
            with (evidence/'task.log').open('xb') as log:
                if node['kind']=='seed':self.seed(node,folder,result,log)
                else:self.publication(node,folder,evidence,result,log)
            result.update(passed=True,finished_monotonic_ns=time.monotonic_ns())
            return result
        except BaseException as error:
            result.update(error_type=type(error).__name__,error_message=str(error)[:1000])
            raise
        finally:
            if node['kind']=='seed' or ephemeral:shutil.rmtree(folder)
            api.write_json(evidence/'report.json',result)

    def graph(self,graph):
        executor=ThreadPoolExecutor(max_workers=self.profile['workers']);pending={}
        try:
            while not graph.complete:
                if self.sampler.errors:raise RuntimeError('Resource sampler failed; see samples-errors.json')
                for key in graph.ready()[:self.profile['workers']-len(pending)]:
                    graph.start(key);pending[executor.submit(self.task,key,graph.nodes[key])]=key
                if not pending:raise ValueError('Preparation dependency graph is blocked')
                done,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                for future in done:
                    key=pending.pop(future);graph.finish(key,future.result())
                self.record['cases']=list(graph.results.values())
                if done:api.write_json(self.args.output/'report.json',self.record)
        except BaseException:
            self.commands.cancel.set()
            raise
        finally:executor.shutdown(wait=True,cancel_futures=True)

    def publication_backlog(self,count):
        """Extra capacity probe, clearly separate from genuine experiment readiness.

        Repeat the verified pair under separate output directories. These are
        repeated publication jobs, not additional alternatives or simulations.
        """
        if not count:return
        phase=dict(replayed_pair_tasks=count,started_monotonic_ns=time.monotonic_ns(),tasks=[])
        self.record['publication_backlog']=phase;self.sampler.stage='retained-publication-backlog'
        executor=ThreadPoolExecutor(max_workers=self.profile['workers'])
        pending={executor.submit(self.task,'replay-pair-'+str(i),dict(kind='pair',config=1),ephemeral=True):i for i in range(count)}
        try:
            while pending:
                if self.sampler.errors:raise RuntimeError('Resource sampler failed; see samples-errors.json')
                done,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future);phase['tasks'].append(future.result())
        except BaseException:
            self.commands.cancel.set()
            raise
        finally:executor.shutdown(wait=True,cancel_futures=True)
        phase['finished_monotonic_ns']=time.monotonic_ns()


def execute(args):
    os.umask(0o077);args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    if args.output.is_symlink() or any(args.output.iterdir()) or args.output.stat().st_mode&0o077:raise ValueError('Use an empty private output')
    work=args.output/'private';work.mkdir(mode=0o700);profile=PROFILES[args.profile]
    record=dict(format='simpaths.visualiser.scaling-worker.v1',profile=args.profile,**profile,
        passed=False,cleanup=False,cases=[],synthetic=False,raw_parsing=True,model_executions=False,
        started_wall_time_ns=time.time_ns(),sampling_scope='whole container including coordinator and all child processes')
    sampler=None;prep=None;started=time.monotonic()
    try:
        record['limits']=require_limits(profile)
        sampler=api.Sampler(args.output/'samples.jsonl').start();record['initial_metrics']=sampler.capture()
        manifest,built=api.load_fixture(args.fixture,args.build,args.raw_root,verify_raw_hashes=False)
        if manifest['mode']!='raw-native' or len(manifest['seeds'])!=2 or len(manifest['configurations'])!=2 or len(manifest['files'])!=8:
            raise ValueError('Use the two genuine configurations and their two real seeds')
        record.update(fixture_manifest=api.receipt(args.fixture,'manifest.json',api.MIB),
            build_manifest=api.receipt(args.build,'build.json',api.MIB),backend=manifest['backend'])
        sys.path.insert(0,str(args.frontend))
        prep=Preparation(args,manifest,built,work,sampler,record)
        release(args,record);record['timed_initial_metrics']=sampler.capture();sampler.stage='raw-and-ready-publications'
        prep.graph(Graph(1 if profile['scope']=='single' else 2))
        record['experiment_finished_monotonic_ns']=max(c['ready_monotonic_ns'] for c in record['cases'])
        record['experiment_final_metrics']=sampler.capture()
        if profile['scope']=='experiment':prep.publication_backlog(args.publication_backlog)
        record['timed_final_metrics']=sampler.capture()
        counters=prep.cache.statistics()['counters'];raw=2 if profile['scope']=='single' else 4
        hits=2 if raw==2 else 8+4*args.publication_backlog
        if counters['evictions'] or counters['hits']!=hits or any(counters[k]!=raw for k in ('misses','raw_started','raw_completed')) or counters['raw_failed']:
            raise ValueError('Expected fresh seed work and retained-summary-only publication')
        again,again_built=api.load_fixture(args.fixture,args.build,args.raw_root,verify_raw_hashes=False)
        if again!=manifest or again_built!=built:raise ValueError('Benchmark inputs changed')
        record['final_metrics']=sampler.stop();record['sampled_peaks']=dict(sampler.peaks);sampler=None
        if any(record['final_metrics']['memory_events'][k]!=record['initial_metrics']['memory_events'][k] for k in ('oom','oom_kill')):
            raise RuntimeError('Preparation group exhausted memory')
        require_limits(profile);record.update(passed=True,sources_unchanged=True)
    except BaseException as error:
        record.update(error_type=type(error).__name__,error_message=str(error)[:1000])
        if isinstance(error,ValueError):record['failure_kind']='integrity-failed'
        raise
    finally:
        if prep is not None:
            prep.commands.cancel.set()
            try:record['cache_statistics']=prep.cache.statistics()
            except BaseException as error:
                record.update(passed=False,cache_statistics_error=type(error).__name__)
        if sampler is not None:
            try:record['final_metrics']=sampler.stop()
            except BaseException as error:
                record.update(passed=False,metrics_error=type(error).__name__,metrics_error_message=str(error)[:1000])
                if sampler.final_metrics is not None:record['final_metrics']=sampler.final_metrics
            record['sampling_errors']=list(sampler.error_details)
            record['sampled_peaks']=dict(sampler.peaks)
        try:shutil.rmtree(work);record['cleanup']=True
        except BaseException as error:record.update(passed=False,cleanup_error=type(error).__name__)
        record.update(elapsed_seconds=time.monotonic()-started,finished_wall_time_ns=time.time_ns())
        api.write_json(args.output/'report.json',record)
    if not record['cleanup']:raise RuntimeError('Preparation cleanup is unconfirmed')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('frontend','fixture','build','raw-root','output','control'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--profile',choices=PROFILES,required=True)
    parser.add_argument('--publication-backlog',type=int,choices=(0,4),default=4)
    parser.add_argument('--timeout-seconds',type=int,default=3600)
    args=parser.parse_args(argv)
    if not 60<=args.timeout_seconds<=7200:parser.error('Use a 60..7200 second bound')
    def interrupted(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    try:execute(args);return 0
    except (Exception,KeyboardInterrupt) as error:
        print('FAILED: '+type(error).__name__+'; inspect private scaling evidence.',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
