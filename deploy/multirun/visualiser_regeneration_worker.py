#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

One isolated resource-trial worker, restoring independent experiment summaries
after a shared host start barrier. No scientific runs, raw parsing or networking.
@author ross richardson
"""
import argparse
import os
from pathlib import Path
import shutil
import signal
import sys
import time

if __package__:
    from . import visualiser_repetition_worker as base
else:
    import visualiser_repetition_worker as base
api=base.api


def wait_for_release(args, record):
    """Priming finishes before either container may enter its measured pipeline."""
    api.write_json(args.output/'armed.json',dict(format='simpaths.visualiser.armed.v1',
        repetitions=args.repetitions,task_start=args.task_start,tasks=args.tasks))
    deadline=time.monotonic()+args.timeout_seconds
    while not (args.control/'release.json').is_file():
        if time.monotonic()>=deadline:raise TimeoutError('Host start barrier was not released')
        time.sleep(.025)
    release=api.decode((args.control/'release.json').read_bytes())
    now=time.monotonic_ns()
    if (set(release)!={'format','released_monotonic_ns'} or release['format']!='simpaths.visualiser.release.v1'
            or type(release['released_monotonic_ns']) is not int
            or not 0<release['released_monotonic_ns']<=now):
        raise ValueError('Invalid shared monotonic start barrier')
    record['released_monotonic_ns']=release['released_monotonic_ns']


def prepare_caches(args, manifest, work, sampler, record):
    selected=base.selected_fixture(manifest,args.repetitions)
    caches={};sampler.stage='priming'
    started=time.monotonic()
    for task in range(args.task_start,args.task_start+args.tasks):
        folder=work/('task-'+str(task));folder.mkdir(mode=0o700)
        cache=base.SummaryCache(folder/'summaries',maximum_bytes=2*1024**3,maximum_entries=64)
        settings=argparse.Namespace(**vars(args));settings.output=args.output/('prime-task-'+str(task))
        settings.output.mkdir(mode=0o700)
        primed=base.prime(settings,selected,folder,cache,dict(record,summary_namespace='independent-'+str(task)))
        caches[task]=cache
        record.setdefault('primed_caches',{})[str(task)]=primed
    record['priming_seconds']=time.monotonic()-started
    return caches


def measure(args, manifest, built, work, sampler, record):
    caches=prepare_caches(args,manifest,work,sampler,record)
    sampler.stage='start-barrier';wait_for_release(args,record)
    record['timed_initial_metrics']=sampler.capture()
    record['cases']=[]
    for number,(task,cache) in enumerate(caches.items(),1):
        record['summary_namespace']='independent-'+str(task)
        base.run_trial(args,manifest,built,work,sampler,record,cache,args.round,args.repetitions,number,task=task)
    record['timed_final_metrics']=sampler.capture()
    record['cache_statistics']={str(task):cache.statistics() for task,cache in caches.items()}
    for status in record['cache_statistics'].values():
        c=status['counters']
        if c['hits']!=2*args.repetitions or c['misses'] or c['raw_started'] or c['evictions']:
            raise ValueError('Every experiment must restore its own retained summaries')


def execute(args):
    os.umask(0o077)
    args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    if args.output.is_symlink() or any(args.output.iterdir()) or args.output.stat().st_mode&0o077:
        raise ValueError('Choose an empty private worker directory')
    work=args.output/'private';work.mkdir(mode=0o700)
    record=dict(format='simpaths.visualiser.regeneration-worker.v1',passed=False,cleanup=False,cases=[],
        synthetic=True,raw_parsing=False,model_executions=False,notice=base.NOTICE,
        memory_mib=args.memory_mib,cpu_millis=args.cpu_millis,repetitions=args.repetitions,
        tasks=args.tasks,task_start=args.task_start,round=args.round,started_wall_time_ns=time.time_ns())
    sampler=None;started=time.monotonic()
    try:
        record['limits']=api.require_limits(args.memory_mib,cpu_millis=args.cpu_millis)
        sampler=api.Sampler(args.output/'samples.jsonl').start()
        record['initial_metrics']=sampler.capture()
        manifest,built=api.load_fixture(args.fixture,args.build,args.fixture)
        record.update(fixture_manifest=api.receipt(args.fixture,'manifest.json',api.MIB),
            build_manifest=api.receipt(args.build,'build.json',api.MIB),backend=manifest['backend'])
        measure(args,manifest,built,work,sampler,record)
        sampler.stage='final-verification'
        again,again_built=api.load_fixture(args.fixture,args.build,args.fixture)
        if (again!=manifest or again_built!=built or api.receipt(args.fixture,'manifest.json',api.MIB)!=record['fixture_manifest']
                or api.receipt(args.build,'build.json',api.MIB)!=record['build_manifest']):
            raise ValueError('Benchmark inputs changed')
        record['final_metrics']=sampler.stop();sampler=None
        if any(record['final_metrics']['memory_events'][k]!=record['initial_metrics']['memory_events'][k]
               for k in ('oom','oom_kill')):raise ValueError('Worker exhausted its memory allowance')
        api.require_limits(args.memory_mib,cpu_millis=args.cpu_millis)
        record.update(passed=True,sources_unchanged=True)
    except BaseException as error:
        record['error_type']=type(error).__name__
        raise
    finally:
        if sampler is not None:
            try:record['final_metrics']=sampler.stop()
            except BaseException as error:record.update(passed=False,metrics_error=type(error).__name__)
        try:shutil.rmtree(work);record['cleanup']=True
        except BaseException as error:record.update(passed=False,cleanup_error=type(error).__name__)
        record.update(elapsed_seconds=time.monotonic()-started,finished_wall_time_ns=time.time_ns())
        api.write_json(args.output/'report.json',record)
    if not record['cleanup']:raise RuntimeError('Worker cleanup is unconfirmed')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('frontend','fixture','build','output','control'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--memory-mib',type=int,choices=(2048,2560,3072),required=True)
    parser.add_argument('--cpu-millis',type=int,choices=(1000,2000),required=True)
    parser.add_argument('--repetitions',type=int,choices=range(2,11),required=True)
    parser.add_argument('--round',type=int,choices=range(1,6),required=True)
    parser.add_argument('--tasks',type=int,choices=(1,2),required=True)
    parser.add_argument('--task-start',type=int,choices=(1,2),required=True)
    parser.add_argument('--timeout-seconds',type=int,default=900)
    args=parser.parse_args(argv)
    if args.tasks==2 and args.task_start!=1:parser.error('The two-task sequential worker starts at task 1')
    if not 60<=args.timeout_seconds<=3600:parser.error('Use a 60..3600 second worker bound')
    def interrupted(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    try:execute(args);return 0
    except (Exception,KeyboardInterrupt) as error:
        print('FAILED: '+type(error).__name__+'; retained private evidence identifies the stage.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
