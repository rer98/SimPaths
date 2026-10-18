#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Measure genuine raw CSV summarisation for two seeds per configuration. Each
configuration owns its source files; fresh caches prevent intermediate reuse.
@author ross richardson
"""
import argparse
from contextlib import contextmanager, ExitStack
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

if __package__:
    from . import visualiser_capacity_worker as api
    from .visualiser_regeneration_worker import wait_for_release
    from .visualiser_summary_cache import SummaryCache
else:
    import visualiser_capacity_worker as api
    from visualiser_regeneration_worker import wait_for_release
    from visualiser_summary_cache import SummaryCache

STAGES=('source_verification','raw_summarisation','validate_and_retain')
FIELDS={'metric_type','metric_value','n','run','scenario','stratifier',
        'stratifier_value','variable','variable_value','year'}


def validate_summary(path,seed,years):
    rows=api.load_small(path.parent,path.name,64*api.MIB)
    if not isinstance(rows,list) or not 1<=len(rows)<=500000:raise ValueError('Invalid intermediate summary row count')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!=FIELDS:raise ValueError('Intermediate summary schema differs')
        if row['scenario']!='baseline' or row['run']!=seed:raise ValueError('Canonical summary seed/role differs')
        if type(row['year']) is not int or row['year'] not in years:raise ValueError('Summary year differs')
        for name in ('metric_type','stratifier','stratifier_value','variable','variable_value'):
            if not isinstance(row[name],str):raise ValueError('Summary label differs')
        for name in ('n','metric_value'):
            value=row[name]
            if value is None and name=='metric_value':continue
            if type(value) not in (int,float) or not math.isfinite(value) or (name=='n' and value<0):
                raise ValueError('Invalid summary statistic')
        seen.add(row['year'])
    if sorted(seen)!=years:raise ValueError('Summary annual coverage differs')
    return dict(rows=len(rows),annual_years=sorted(seen),seed=seed,canonical_role='baseline')


@contextmanager
def stage(sampler,task,seed_record,name):
    key=f"task-{task['task']}-seed-{seed_record['seed']}-{name}"
    sampler.stage=key;before=sampler.capture();tick=time.monotonic()
    value=dict(passed=False);seed_record['stages'][name]=value
    try:
        yield
        value['passed']=True
    finally:
        after=sampler.capture()
        value.update(elapsed_seconds=time.monotonic()-tick,before=before,after=after,
            sampled_peaks=dict(sampler.peaks.get(key,{})))
        total=task['stages'][name]
        total['elapsed_seconds']+=value['elapsed_seconds']
        for k,v in value['sampled_peaks'].items():total['sampled_peaks'][k]=max(total['sampled_peaks'].get(k,0),v)


def measure(args,manifest,work,sampler,record):
    sys.path.insert(0,str(args.frontend))
    from jasmine_web.batch.results import verified_file,OutputUnavailable
    node=shutil.which('node')
    if not node:raise RuntimeError('Node is unavailable')
    record['runtime']=dict(python=sys.version,node=subprocess.check_output([node,'--version'],text=True,
        timeout=10,stdin=subprocess.DEVNULL,env={'PATH':os.defpath,'LANG':'C.UTF-8'}).strip(),
        kernel=platform.release(),machine=platform.machine())
    record['node_old_space_mib']=max(512,args.memory_mib-1024)
    cache=SummaryCache(work/'summaries',maximum_bytes=2*1024**3,maximum_entries=16)
    sampler.stage='start-barrier';wait_for_release(args,record)
    record['timed_initial_metrics']=sampler.capture()
    with (args.output/'processing.log').open('xb') as log:
        for task_number in range(args.task_start,args.task_start+args.tasks):
            config=manifest['configurations'][task_number-1]
            task=dict(task=task_number,configuration=config['id'],name=config['name'],round=args.round,
                repetitions=2,passed=False,cleanup=False,started_monotonic_ns=time.monotonic_ns(),
                seeds=[],stages={s:dict(passed=False,elapsed_seconds=0.,sampled_peaks={}) for s in STAGES})
            record['cases'].append(task)
            for run in config['runs']:
                detail=dict(seed=run['seed'],stages={},passed=False);task['seeds'].append(detail)
                output=work/f"metrics-{task_number}-{run['seed']}.json"
                source_keys=('person','benefit')
                identity=dict(owner='raw-capacity',configuration=config['id'],seed=run['seed'],
                    backend=manifest['backend'],fixture=record['fixture_manifest'],
                    sources={k:manifest['files'][run[k]] for k in source_keys})
                with cache.entry(identity) as entry:
                    if entry.restore(output):raise ValueError('A fresh raw trial reused an intermediate')
                    with ExitStack() as stack:
                        with stage(sampler,task,detail,'source_verification'):
                            try:
                                sources=[stack.enter_context(verified_file(args.raw_root,
                                    dict(path=run[k],**manifest['files'][run[k]]))) for k in source_keys]
                            except (OutputUnavailable,OSError) as error:
                                raise ValueError('Raw source verification failed') from error
                            before=[os.fstat(s.fileno()) for s in sources]
                        detail['logical_input_bytes']=sum(manifest['files'][run[k]]['bytes'] for k in source_keys)
                        with stage(sampler,task,detail,'raw_summarisation'):
                            with entry.raw_processing(detail['logical_input_bytes']):
                                request=dict(operation='run',person_fd=sources[0].fileno(),benefit_fd=sources[1].fileno(),
                                    role='baseline',run=run['seed'],output=str(output))
                                api.write_json(work/'request.json',request)
                                api.command(log,[node,'--max-old-space-size='+str(record['node_old_space_mib']),
                                    args.build/'runner.cjs',work/'request.json'],pass_fds=tuple(s.fileno() for s in sources))
                            for first,source in zip(before,sources):
                                last=os.fstat(source.fileno())
                                if (first.st_size,first.st_mtime_ns,first.st_ctime_ns)!=(last.st_size,last.st_mtime_ns,last.st_ctime_ns):
                                    raise ValueError('Raw source changed during parsing')
                        with stage(sampler,task,detail,'validate_and_retain'):
                            detail['validation']=validate_summary(output,run['seed'],manifest['annual_years'])
                            detail['summary']=api.receipt(work,output.name,64*api.MIB)
                            if not entry.publish(output):raise ValueError('Fresh intermediate publication was rejected')
                            saved=api.load_small(cache.root/entry.key,'receipt.json')
                            if (saved['identity']!=identity or saved['sha256']!=detail['summary']['sha256']
                                    or saved['bytes']!=detail['summary']['bytes']
                                    or api.receipt(cache.root/entry.key,'metrics.json',64*api.MIB)!=detail['summary']):
                                raise ValueError('Retained intermediate receipt differs')
                            detail['retained_key']=entry.key
                detail['passed']=True;output.unlink()
                api.write_json(args.output/'report.json',record)
            task['ready_monotonic_ns']=time.monotonic_ns()
            task['summary_seconds']=(task['ready_monotonic_ns']-task['started_monotonic_ns'])/1e9
            task['result_identity']={s['seed']:s['summary'] for s in task['seeds']}
            for s in task['stages'].values():s['passed']=True
            task.update(passed=True,cleanup=True)
            api.write_json(args.output/'report.json',record)
    record['timed_final_metrics']=sampler.capture()
    record['cache_statistics']=cache.statistics()
    counters=record['cache_statistics']['counters'];expected=2*args.tasks
    if (counters['hits'] or counters['misses']!=expected or counters['raw_started']!=expected
            or counters['raw_completed']!=expected or counters['raw_failed'] or counters['evictions']):
        raise ValueError('Raw trial did not compute every seed from scratch without eviction')


def execute(args):
    os.umask(0o077);args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    if args.output.is_symlink() or any(args.output.iterdir()) or args.output.stat().st_mode&0o077:
        raise ValueError('Choose an empty private worker directory')
    work=args.output/'private';work.mkdir(mode=0o700)
    record=dict(format='simpaths.visualiser.summary-worker.v1',passed=False,cleanup=False,cases=[],
        synthetic=False,raw_parsing=True,model_executions=False,memory_mib=args.memory_mib,
        cpu_millis=args.cpu_millis,repetitions=2,tasks=args.tasks,task_start=args.task_start,
        round=args.round,started_wall_time_ns=time.time_ns())
    sampler=None;started=time.monotonic()
    try:
        record['limits']=api.require_limits(args.memory_mib,cpu_millis=args.cpu_millis)
        sampler=api.Sampler(args.output/'samples.jsonl').start()
        record['initial_metrics']=sampler.capture()
        manifest,built=api.load_fixture(args.fixture,args.build,args.raw_root,verify_raw_hashes=False)
        if manifest['mode']!='raw-native' or len(manifest['seeds'])!=2 or len(manifest['configurations'])!=2 or len(manifest['files'])!=8:
            raise ValueError('Use exactly two genuine raw repetitions in baseline and alternative')
        record.update(fixture_manifest=api.receipt(args.fixture,'manifest.json',api.MIB),
            build_manifest=api.receipt(args.build,'build.json',api.MIB),backend=manifest['backend'])
        measure(args,manifest,work,sampler,record)
        sampler.stage='final-verification'
        again,again_built=api.load_fixture(args.fixture,args.build,args.raw_root,verify_raw_hashes=False)
        if again!=manifest or again_built!=built or api.receipt(args.fixture,'manifest.json',api.MIB)!=record['fixture_manifest']:
            raise ValueError('Benchmark inputs changed')
        record['final_metrics']=sampler.stop();sampler=None
        if any(record['final_metrics']['memory_events'][k]!=record['initial_metrics']['memory_events'][k] for k in ('oom','oom_kill')):
            raise RuntimeError('Worker exhausted container memory')
        api.require_limits(args.memory_mib,cpu_millis=args.cpu_millis)
        record.update(passed=True,sources_unchanged=True)
    except BaseException as error:
        record.update(error_type=type(error).__name__,error_message=str(error)[:1000])
        if isinstance(error,ValueError):record['failure_kind']='integrity-failed'
        raise
    finally:
        if sampler is not None:
            try:record['final_metrics']=sampler.stop()
            except BaseException as error:
                record.update(passed=False,metrics_error=type(error).__name__,metrics_error_message=str(error)[:1000])
                if sampler.final_metrics is not None:record['final_metrics']=sampler.final_metrics
            record['sampling_errors']=list(sampler.error_details)
        if (work/'summaries').exists():
            try:record['cache_statistics']=SummaryCache(work/'summaries',maximum_bytes=2*1024**3,maximum_entries=16).statistics()
            except BaseException as error:record.update(passed=False,cache_evidence_error=type(error).__name__)
        try:shutil.rmtree(work);record['cleanup']=True
        except BaseException as error:record.update(passed=False,cleanup_error=type(error).__name__)
        record.update(elapsed_seconds=time.monotonic()-started,finished_wall_time_ns=time.time_ns())
        api.write_json(args.output/'report.json',record)
    if not record['cleanup']:raise RuntimeError('Worker cleanup is unconfirmed')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('frontend','fixture','build','raw-root','output','control'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--memory-mib',type=int,choices=(2048,2560,3072),required=True)
    parser.add_argument('--cpu-millis',type=int,choices=(1000,2000),required=True)
    parser.add_argument('--repetitions',type=int,choices=(2,),required=True)
    parser.add_argument('--round',type=int,choices=range(1,6),required=True)
    parser.add_argument('--tasks',type=int,choices=(1,2),required=True)
    parser.add_argument('--task-start',type=int,choices=(1,2),required=True)
    parser.add_argument('--timeout-seconds',type=int,default=3600)
    args=parser.parse_args(argv)
    if args.tasks==2 and args.task_start!=1:parser.error('Sequential work starts at configuration 1')
    if not 60<=args.timeout_seconds<=7200:parser.error('Use a 60..7200 second worker bound')
    def interrupted(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    try:execute(args);return 0
    except (Exception,KeyboardInterrupt) as error:
        print('FAILED: '+type(error).__name__+'; inspect private stage evidence.',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
