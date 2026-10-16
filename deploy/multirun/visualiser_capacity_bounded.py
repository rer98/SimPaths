"""(C) Copyright 2026, by Ross Richardson

Container-only capacity measurements of cached summaries and bounded pair delivery.
Use the production summary cache and publication helpers; never run a simulation.
@author ross richardson
"""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

if __package__:
    from .visualiser_summary_cache import SummaryCache
else:
    from visualiser_summary_cache import SummaryCache

MIB = 1024 ** 2


def digest(body):
    return dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())


def identity(task):
    """Only content receipts enter result-parity checks, never timing/cache state."""
    return dict(task=task['task'], parts=[dict(publication=p['publication'], index=p['index'])
        for p in task['parts']], catalogue=task['catalogue'], view=task['view'],
        validation=task['validation'])


def run_tasks(args, manifest, built, work, sampler, record, api):
    """A task is one complete baseline/multi-alternative comparison, in stable order."""
    sys.path.insert(0, str(args.frontend))
    from jasmine_web.batch.aggregate_io import invoke
    from jasmine_web.batch.aggregate_limits import AggregateLimits
    from jasmine_web.batch.results import verified_file

    if 'simpaths.visualiser.role-independent-metrics.v1' not in (args.build/'runner.cjs').read_text():
        raise ValueError('Bounded trials require the reviewed role-independent runner')
    limits = AggregateLimits(96*MIB, 256*MIB, 200000, 400000)
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node is unavailable')
    record['runtime'] = dict(node=subprocess.check_output([node, '--version'], text=True,
        timeout=10, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'}).strip(), python=sys.version)
    heap, helper = max(512, args.memory_mib-1024), args.memory_mib-256
    record.update(layout='bounded-pairs', publication_limits=limits.describe(),
        node_old_space_mib=heap, helper_address_space_mib=helper, tasks=[])
    cache = SummaryCache(args.cache)
    record['summary_cache_budget'] = dict(bytes=cache.maximum_bytes, entries=cache.maximum_entries,
        entry_bytes=cache.entry_bytes)
    common = dict(memory_mib=helper, publication_limits=limits.describe())
    started = time.monotonic()

    def progress(task, name, **extra):
        api.write_json(args.output/'progress.json', dict(task=task, stage=name,
            elapsed_seconds=time.monotonic()-started, resource_sample=sampler.capture(), **extra))

    @contextmanager
    def stage(task, name):
        key = f'task-{task}/{name}'
        sampler.stage = key
        before = sampler.capture()
        tick = time.monotonic()
        value = dict(passed=False)
        record['stages'][key] = value
        progress(task, name)
        try:
            yield
            value['passed'] = True
        finally:
            value.update(elapsed_seconds=time.monotonic()-tick, before=before,
                after=sampler.capture(), sampled_peaks=dict(sampler.peaks.get(key, {})))
            # Capture failures too. This is one shared-cache cumulative snapshot,
            # not a per-worker delta to sum across simultaneous workers.
            record['summary_cache_usage'] = cache.statistics()
            api.write_json(args.output/'report.json', record)

    with (args.output/'processing.log').open('xb') as log:
        def execute(argv, *, pass_fds=()):
            api.command(log, argv, pass_fds=pass_fds)

        def verify():
            current_manifest,current_build=api.load_fixture(args.fixture,args.build,args.raw_root)
            if (current_manifest!=manifest or current_build!=built
                    or api.receipt(args.fixture,'manifest.json',MIB)!=record['fixture_manifest']):
                raise ValueError('Inputs changed between serial tasks')

        for position,task_id in enumerate(args.tasks):
            # The worker's outer guards verify before the first/after the last
            # task. Verify the other boundaries here: the same two whole-source
            # passes per comparison for serial and parallel batches, even on hits.
            if position:
                with stage(task_id,'input-verification'):
                    verify()
            task_work = work/f'task-{task_id}'
            task_work.mkdir(mode=0o700)
            task = dict(task=task_id, parts=[], validation=[],
                started_seconds=time.monotonic()-started, started_wall_time_ns=time.time_ns(),
                cache=dict(hits=0, misses=0, restored_bytes=0, computed_bytes=0,
                    published=0, lock_wait_seconds=0., restore_seconds=0., compute_seconds=0.))
            record['tasks'].append(task)
            metrics = {}

            def node_run(script, request, *, pass_fds=()):
                api.write_json(task_work/'request.json', request)
                execute([node, '--max-old-space-size='+str(heap), script, task_work/'request.json'],
                    pass_fds=pass_fds)

            with stage(task_id, 'summaries'):
                for c_index, config in enumerate(manifest['configurations']):
                    references = []
                    for number, run in enumerate(config['runs']):
                        output = task_work/f'metrics-{c_index}-{number}.json'
                        source_keys = ('summary',) if manifest['mode']=='summary-replay' else ('person', 'benefit')
                        key = dict(owner=f'capacity-task-{task_id}', configuration=config['id'],
                            fixture=record['fixture_manifest'], backend=manifest['backend'],
                            seed=run['seed'], sources={k: manifest['files'][run[k]] for k in source_keys})
                        tick = time.monotonic()
                        with cache.entry(key) as entry:
                            task['cache']['lock_wait_seconds'] += time.monotonic()-tick
                            tick = time.monotonic()
                            restored = entry.restore(output)
                            task['cache']['restore_seconds'] += time.monotonic()-tick
                            if restored:
                                task['cache']['hits'] += 1
                                task['cache']['restored_bytes'] += output.stat().st_size
                            else:
                                task['cache']['misses'] += 1
                                tick = time.monotonic()
                                if manifest['mode']=='summary-replay':
                                    node_run(Path(__file__).parent/'visualiser'/'capacity_replay.cjs',
                                        dict(runs=[dict(summary=str(args.fixture/run['summary']),
                                            source_seed=run['source_seed'], seed=run['seed'], role='baseline',
                                            output=str(output))]))
                                else:
                                    with ExitStack() as stack:
                                        sources = [stack.enter_context(verified_file(args.raw_root,
                                            dict(path=run[k], **manifest['files'][run[k]]))) for k in source_keys]
                                        initial = [os.fstat(s.fileno()) for s in sources]
                                        with entry.raw_processing(sum(manifest['files'][run[k]]['bytes'] for k in source_keys)):
                                            node_run(args.build/'runner.cjs', dict(operation='run',
                                                person_fd=sources[0].fileno(), benefit_fd=sources[1].fileno(),
                                                role='baseline', run=run['seed'], output=str(output)),
                                                pass_fds=tuple(s.fileno() for s in sources))
                                            for first, source in zip(initial, sources):
                                                last = os.fstat(source.fileno())
                                                if (first.st_size,first.st_mtime_ns,first.st_ctime_ns) != (last.st_size,last.st_mtime_ns,last.st_ctime_ns):
                                                    raise ValueError('Raw source changed during parsing')
                                task['cache']['compute_seconds'] += time.monotonic()-tick
                                task['cache']['computed_bytes'] += output.stat().st_size
                                task['cache']['published'] += bool(entry.publish(output))
                        references.append(dict(path=str(output), canonical=True, seed=run['seed']))
                        progress(task_id, 'summaries', cache=task['cache'])
                    metrics[config['id']] = references

            baseline = manifest['configurations'][0]
            for pair_number, alternative in enumerate(manifest['configurations'][1:], 1):
                pair = task_work/f'pair-{pair_number}'
                pair.mkdir(mode=0o700)
                selected = [baseline, alternative]
                outputs = [dict(path=str(pair/f'aggregate-{i}.json'), role=role)
                    for i, role in enumerate(('baseline', 'scenario_1'))]
                with stage(task_id, f'pair-{pair_number}/aggregate'):
                    node_run(args.build/'runner.cjs', dict(operation='aggregate', outputs=outputs,
                        metrics=[dict(ref, role=role) for config, role in zip(selected, ('baseline','scenario_1'))
                            for ref in metrics[config['id']]]))
                paths = [Path(o['path']) for o in outputs]
                with stage(task_id, f'pair-{pair_number}/validate'):
                    validation = api.validate_rows(paths, dict(manifest, configurations=selected))
                    if (any(n>limits.rows_per_series for n in validation['rows_per_configuration'])
                            or sum(validation['rows_per_configuration'])>limits.comparison_rows):
                        raise ValueError('Pair exceeds the unchanged production row budget')
                    task['validation'].append(validation)
                public = dict(format='simpaths.visualiser.v2', backend=manifest['backend'],
                    experiment=f'Private capacity task {task_id}',
                    configurations=[dict(id=c['id'], name=c['name'],
                        role='Baseline' if i==0 else 'Scenario', dataset='capacity-fixture',
                        model='verified-pinned-model', runs=[dict(seed=r['seed']) for r in c['runs']])
                        for i,c in enumerate(selected)],
                    comparison=dict(baseline=baseline['id'], scenarios=[alternative['id']]))
                with stage(task_id, f'pair-{pair_number}/publish'):
                    with ExitStack() as stack:
                        sources = [stack.enter_context(p.open('rb')) for p in paths]
                        body = invoke(dict(common, operation='publish', format=public['format'], public=public,
                            kind='rows', notice=api.NOTICE if manifest['mode']=='summary-replay' else
                            'Verified native outputs; no simulations were rerun.', comparison_available=True),
                            tuple(s.fileno() for s in sources), execute)
                    publication = pair/'published.json'
                    publication.write_bytes(body)
                    part = dict(publication=digest(body), configurations=[c['id'] for c in selected])
                    del body
                views = pair/'views'
                views.mkdir(mode=0o700)
                with stage(task_id, f'pair-{pair_number}/index'):
                    directory = os.open(views, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                    try:
                        with publication.open('rb') as source:
                            part['index'] = api.decode(invoke(dict(common, operation='index',
                                format=public['format'], sha256=part['publication']['sha256']),
                                (source.fileno(), directory), execute))
                    finally:
                        os.close(directory)
                part['ready_seconds'] = time.monotonic()-started
                part['ready_wall_time_ns'] = time.time_ns()
                task['parts'].append(part)
                publication.unlink()
                for path in paths:
                    path.unlink()

            with ExitStack() as stack:
                fds = []
                for number, part in enumerate(task['parts'], 1):
                    views = task_work/f'pair-{number}'/'views'
                    fds.append(stack.enter_context((views/'index.json').open('rb')).fileno())
                    directory = os.open(views, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                    stack.callback(os.close, directory)
                    fds.append(directory)
                parts = [dict(sha256=p['publication']['sha256'], index_sha256=p['index']['index_sha256'],
                    configurations=p['configurations']) for p in task['parts']]
                composite = len(parts)>1
                combined = digest(json.dumps(parts, sort_keys=True, separators=(',', ':')).encode())['sha256']
                request = dict(common, parts=parts, sha256=combined) if composite else dict(common, **{
                    k:v for k,v in parts[0].items() if k!='configurations'})
                with stage(task_id, 'catalogue'):
                    body = invoke(dict(request, operation='composite-catalogue' if composite else 'catalogue'),
                        tuple(fds), execute)
                    catalogue = api.decode(body)
                    if (catalogue['seeds']!=manifest['seeds'] or [c['id'] for c in catalogue['configurations']]
                            !=[c['id'] for c in manifest['configurations']]):
                        raise ValueError('Composite catalogue differs from the selected experiment')
                    task['catalogue'] = digest(body)
                    del body
                selection = dict(variable='Highest Level of Education', stratifier='Overall', kind='levels',
                    configurations=[c['id'] for c in manifest['configurations']])
                durations = []
                with stage(task_id, 'views'):
                    for _ in range(args.view_reads):
                        tick = time.monotonic()
                        body = invoke(dict(request, operation='composite-view' if composite else 'view',
                            selection=selection), tuple(fds), execute)
                        durations.append(time.monotonic()-tick)
                        view = api.decode(body)
                        if (view.get('format')!='simpaths.visualiser.view.v1'
                                or len(view.get('series', []))!=len(manifest['configurations'])
                                or any(sorted({r['year'] for r in s['rows']})!=manifest['annual_years']
                                    for s in view['series'])):
                            raise ValueError('Representative view coverage differs')
                        checksum = digest(body)
                        if 'view' in task and task['view']!=checksum:
                            raise ValueError('Repeated chart reads differ')
                        task['view'] = checksum
                    task['view_read_seconds'] = durations
            task['ready_seconds'] = time.monotonic()-started
            task['ready_wall_time_ns'] = time.time_ns()
            task['elapsed_seconds'] = task['ready_seconds']-task['started_seconds']
            task['cache']['retained'] = cache.prune()
            task['cache']['usage'] = cache.statistics()
            task['result_identity'] = identity(task)
            api.write_json(args.output/'report.json', record)
            shutil.rmtree(task_work)
            if position<len(args.tasks)-1:
                with stage(task_id,'output-verification'):
                    verify()
    record['summary_cache_retained'] = cache.prune()
