#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Measure retained-summary regeneration for synthetic repetition counts, one pair
at a time. Reuse production cache restoration, calculations and chart helpers.
@author ross richardson
"""
import argparse
import copy
import os
from pathlib import Path
import shutil
import signal
import sys
import time

if __package__:
    from . import visualiser_capacity_worker as api
    from .visualiser_summary_cache import SummaryCache
else:
    import visualiser_capacity_worker as api
    from visualiser_summary_cache import SummaryCache

NOTICE = ('Synthetic performance repetitions reuse verified summaries; they are '
          'not independent scientific simulations or uncertainty estimates.')
STAGES = ('summary_restore', 'aggregate', 'validate', 'publish', 'index', 'catalogue', 'view')


def trial_order(counts, rounds):
    if (not isinstance(counts, list) or not 2 <= len(counts) <= 10
            or any(type(n) is not int or not 2 <= n <= 10 for n in counts)
            or len(counts) != len(set(counts)) or type(rounds) is not int or not 1 <= rounds <= 5):
        raise ValueError('Use distinct repetition counts in 2..10 and one to five rounds')
    return [(r + 1, n) for r in range(rounds)
            for n in counts[r % len(counts):] + counts[:r % len(counts)]]


def selected_fixture(manifest, count):
    if (manifest.get('mode') != 'summary-replay' or len(manifest.get('configurations', [])) != 2
            or type(count) is not int or not 2 <= count <= 10):
        raise ValueError('Use a summary-replay baseline and one alternative, with 2..10 repetitions')
    result = copy.deepcopy(manifest)
    result['seeds'] = [str(900000001 + i) for i in range(count)]
    for original, configuration in zip(manifest['configurations'], result['configurations']):
        if not original.get('runs'):
            raise ValueError('Missing source summaries')
        configuration['runs'] = [dict(original['runs'][i % len(original['runs'])], seed=seed)
                                 for i, seed in enumerate(result['seeds'])]
    return result


def summary_key(record, configuration, run):
    result=dict(owner='repetition-performance-fixture', configuration=configuration['id'],
                fixture=record['fixture_manifest'], seed=run['seed'],
                source_summary=run['summary'], source_seed=run['source_seed'], backend=record['backend'])
    if 'summary_namespace' in record:result['experiment']=record['summary_namespace']
    return result


def prime(args, selected, work, cache, record):
    """Prepare all synthetic identities once, outside every measured trial."""
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node is unavailable')
    folder = work/'prime'; folder.mkdir(mode=0o700)
    runs, sources = [], []
    for configuration in selected['configurations']:
        for run in configuration['runs']:
            output = folder/(str(len(sources)) + '.json')
            sources.append((summary_key(record, configuration, run), output))
            runs.append(dict(summary=str(args.fixture/run['summary']), source_seed=run['source_seed'],
                             seed=run['seed'], role='baseline', output=str(output)))
    try:
        api.write_json(folder/'request.json', dict(runs=runs))
        with (args.output/'priming.log').open('xb') as log:
            api.command(log, [node, '--max-old-space-size='+str(max(512,args.memory_mib-1024)),
                              Path(__file__).parent/'visualiser/capacity_replay.cjs', folder/'request.json'])
        for key, source in sources:
            with cache.entry(key) as entry:
                if not entry.publish(source):
                    raise ValueError('Could not retain every synthetic summary')
            source.unlink()
        retained = cache.prune()
        if retained['entries'] != len(sources):
            raise ValueError('Primed summary cache evicted a required repetition')
        return retained
    finally:
        shutil.rmtree(folder)


def restore_summaries(cache, manifest, record, paths):
    selected = [(configuration, run) for configuration in manifest['configurations']
                for run in configuration['runs']]
    if len(paths) != len(selected):
        raise ValueError('Summary restoration plan differs')
    for (configuration, run), path in zip(selected, paths):
        with cache.entry(summary_key(record, configuration, run)) as entry:
            if not entry.restore(path):
                # A cache miss must fail this benchmark, never turn into raw work.
                raise ValueError('Retained summary unavailable; no raw parsing was attempted')


def run_trial(args, manifest, built, work, sampler, record, cache, round_number, count, number, *, task=None):
    """One genuine regeneration pipeline, shared by repetition and resource trials."""
    selected = selected_fixture(manifest, count)
    folder = work/('trial-'+str(number)); folder.mkdir(mode=0o700)
    identity = f'round-{round_number}-{count}-repetitions'+('' if task is None else '-task-'+str(task))
    evidence = args.output/identity; evidence.mkdir(mode=0o700)
    case = dict(round=round_number, repetitions=count, passed=False, cleanup=False, stages={}, synthetic=True)
    if task is not None:case['task']=task
    record['cases'].append(case)
    class TrialSampler:
        @property
        def stage(self):return sampler.stage.removeprefix(identity+'/')
        @stage.setter
        def stage(self, value):sampler.stage = identity+'/'+value
        @property
        def peaks(self):
            return {k.removeprefix(identity+'/'):v for k,v in sampler.peaks.items() if k.startswith(identity+'/')}
        def capture(self):return sampler.capture()
    settings = argparse.Namespace(**vars(args)); settings.output = evidence
    started = time.monotonic()
    try:
        case['started_monotonic_ns']=time.monotonic_ns()
        api.run_pipeline(settings, selected, built, folder, TrialSampler(), case,
            summary_restore=lambda paths: restore_summaries(cache, selected, record, paths))
        if set(case['stages']) != set(STAGES) or not all(s['passed'] for s in case['stages'].values()):
            raise ValueError('Incomplete regeneration stages')
        case['regeneration_seconds'] = case['ready_pipeline_seconds']
        case['stage_total_seconds'] = sum(case['stages'][s]['elapsed_seconds'] for s in STAGES if s != 'view')
        case['measured_pipeline_seconds'] = time.monotonic()-started
        case['summary_count'] = 2*count
        case['summary_bytes_restored'] = sum(path.stat().st_size for path in folder.glob('metrics-*.json'))
        case['result_identity'] = {k:case[k] for k in ('publication','index','catalogue','view','validation')}
        case['passed'] = True
    except BaseException as error:
        case['error_type'] = type(error).__name__
        raise
    finally:
        try:shutil.rmtree(folder); case['cleanup'] = True
        except BaseException as error:case.update(passed=False, cleanup_error=type(error).__name__)
        api.write_json(evidence/'report.json', case)
        api.write_json(args.output/'report.json', record)
    if not case['cleanup']:raise RuntimeError('Trial scratch cleanup was not confirmed')
    return case


def run_trials(args, manifest, built, work, sampler, record):
    order = trial_order(args.repetitions, args.rounds)
    largest = selected_fixture(manifest, max(args.repetitions))
    cache = SummaryCache(work/'summaries', maximum_bytes=2*1024**3, maximum_entries=64)
    sampler.stage = 'priming'
    tick = time.monotonic()
    record['primed_cache'] = prime(args, largest, work, cache, record)
    record['priming_seconds'] = time.monotonic()-tick
    record['trial_order'] = [dict(round=r, repetitions=n) for r,n in order]
    record['cases'] = []
    for number, (round_number, count) in enumerate(order, 1):
        case=run_trial(args,manifest,built,work,sampler,record,cache,round_number,count,number)
        print(f'PASS: {count} repetitions; round {round_number}; '
              f"{case['regeneration_seconds']:.1f} seconds", flush=True)
    record['cache_statistics'] = cache.statistics()
    counters = record['cache_statistics']['counters']
    expected = sum(2*n for _,n in order)
    if counters['hits'] != expected or counters['misses'] or counters['raw_started'] or counters['evictions']:
        raise ValueError('Every measured lookup must reuse a retained summary without eviction or raw parsing')


def execute(args):
    os.umask(0o077)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.output.is_symlink() or any(args.output.iterdir()) or args.output.stat().st_mode & 0o077:
        raise ValueError('Choose an empty private output directory')
    work = args.output/'private'; work.mkdir(mode=0o700)
    (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Generated synthetic repetition performance measurements.\n@author ross richardson\n-->\n')
    record = dict(format='simpaths.visualiser.repetition-worker.v1', passed=False, cleanup=False,
                  synthetic=True, model_executions=False, raw_parsing=False,
                  memory_mib=args.memory_mib, cpu_millis=args.cpu_millis, notice=NOTICE,
                  started_wall_time_ns=time.time_ns())
    sampler = None
    started = time.monotonic()
    api.write_json(args.output/'report.json',record)
    try:
        record['limits'] = api.require_limits(args.memory_mib, cpu_millis=args.cpu_millis)
        sampler = api.Sampler(args.output/'samples.jsonl').start()
        record['initial_metrics'] = sampler.capture()
        tick = time.monotonic()
        manifest, built = api.load_fixture(args.fixture, args.build, args.fixture)
        selected_fixture(manifest, max(args.repetitions))
        record.update(fixture_manifest=api.receipt(args.fixture,'manifest.json',api.MIB),
                      build_manifest=api.receipt(args.build,'build.json',api.MIB), backend=manifest['backend'])
        record['verification_before_seconds'] = time.monotonic()-tick
        run_trials(args, manifest, built, work, sampler, record)
        sampler.stage = 'final-verification'; tick = time.monotonic()
        again, again_built = api.load_fixture(args.fixture, args.build, args.fixture)
        if (again != manifest or again_built != built
                or api.receipt(args.fixture,'manifest.json',api.MIB) != record['fixture_manifest']
                or api.receipt(args.build,'build.json',api.MIB) != record['build_manifest']):
            raise ValueError('Benchmark inputs changed')
        record['verification_after_seconds'] = time.monotonic()-tick
        after = sampler.stop(); sampler = None
        record['final_metrics'] = after
        before = record['initial_metrics']
        if any(after['memory_events'][key] != before['memory_events'][key] for key in ('oom','oom_kill')):
            raise ValueError('Worker exhausted its memory allowance')
        api.require_limits(args.memory_mib, cpu_millis=args.cpu_millis)
        record.update(passed=True, sources_unchanged=True)
    except BaseException as error:
        record.update(passed=False,error_type=type(error).__name__)
        raise
    finally:
        if sampler is not None:
            try:
                record['final_metrics'] = sampler.stop()
            except BaseException as error:
                record.update(passed=False,metrics_error=type(error).__name__)
        try:
            shutil.rmtree(work); record['cleanup'] = True
        except BaseException as error:
            record.update(passed=False,cleanup_error=type(error).__name__)
        record['elapsed_seconds'] = time.monotonic()-started
        record['finished_wall_time_ns'] = time.time_ns()
        api.write_json(args.output/'report.json', record)
    if not record['passed'] or not record['cleanup']:
        raise RuntimeError('Repetition trial incomplete')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('frontend','fixture','build','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--memory-mib',type=int,choices=(2048,2560,3072,4096),default=3072)
    parser.add_argument('--cpu-millis',type=int,choices=(1000,2000),default=1000)
    parser.add_argument('--repetitions',type=int,nargs='+',default=[2,5,10])
    parser.add_argument('--rounds',type=int,default=3)
    args = parser.parse_args(argv)
    trial_order(args.repetitions,args.rounds)
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    try:
        execute(args); return 0
    except (Exception,KeyboardInterrupt) as error:
        print('FAILED: '+type(error).__name__+'; inspect private evidence.',file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
