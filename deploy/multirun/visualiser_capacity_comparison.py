"""(C) Copyright 2026, by Ross Richardson

Equal-work Visualiser trials, explicit fixture provenance and optional cancellation.
Keep native resource measurements separate from production admission decisions.
@author ross richardson
"""
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4

from deploy.multirun import visualiser_capacity as legacy
from deploy.multirun.artifacts import fingerprint, snapshot_files, write_attribution
from deploy.multirun.visualiser_capacity_worker import write_json

MIB, GIB = 1024**2, 1024**3
SCIENTIFIC_SOURCES = ('parseCore.js','useAggregatedData.js','csvParse.js')


def checked_build(path):
    manifest = legacy.read_json(path/'build.json')
    if manifest.get('format')!='simpaths.visualiser.build.v1':
        raise ValueError('Use a reviewed Visualiser build')
    for name, checksum in manifest['files'].items():
        if fingerprint(path/legacy.safe_name(name))['sha256']!=checksum:
            raise ValueError('Visualiser build checksum differs: '+name)
    return manifest


def rebind(args):
    """New fixture only: allow a new bundle of the exact same scientific sources."""
    fixture, root = legacy.check_fixture(args.fixture)
    if fixture['mode']!='summary-replay':
        raise ValueError('Only verified summary replay can be rebound; prepare raw provenance afresh')
    before, after = checked_build(args.source_build), checked_build(args.build)
    for name, key in (('calculation.cjs','calculation_sha256'), ('runner.cjs','runner_sha256')):
        if fixture['backend'][key]!=before['files'][name]:
            raise ValueError('Original fixture does not match its original build')
    for name in SCIENTIFIC_SOURCES:
        value = before.get('source_hashes', {}).get(name)
        if not value or value!=after.get('source_hashes', {}).get(name):
            raise ValueError('Scientific source changed: '+name)
    for key in ('dependencies','toolchain'):
        if not before.get(key) or before[key]!=after.get(key):
            raise ValueError('Calculation dependencies/toolchain changed')
    if 'simpaths.visualiser.role-independent-metrics.v1' not in (args.build/'runner.cjs').read_text():
        raise ValueError('New build needs the reviewed canonical-summary runner')
    if len(fixture['configurations'])!=2:
        raise ValueError('Rebind the original baseline/one-alternative fixture')
    output = args.output.absolute()
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError('Output must not use symlinked directories')
    if any(p.resolve() in output.parents for p in (args.fixture,args.source_build,args.build)):
        raise ValueError('Keep new evidence outside the original fixture and builds')
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    original = fingerprint(args.fixture/'manifest.json')
    copied = snapshot_files({name:root/name for name in fixture['files']}, output)
    write_attribution(output)
    if copied!=fixture['files']:
        raise ValueError('Summary source changed during snapshot')
    configs = [fixture['configurations'][0]]
    for index in range(1, args.alternatives+1):
        configs.append(dict(fixture['configurations'][1], id=f'alternative{index}',
            name=f'Synthetic alternative {index}', role=f'scenario_{index}'))
    manifest = dict(fixture, configurations=configs,
        backend=dict(calculation_sha256=after['files']['calculation.cjs'], runner_sha256=after['files']['runner.cjs']),
        provenance=dict(fixture.get('provenance', {}), rebind=dict(
            original_fixture=original, original_backend=fixture['backend'],
            original_build=fingerprint(args.source_build/'build.json'),
            selected_build=fingerprint(args.build/'build.json'),
            scientific_source_hashes={n:after['source_hashes'][n] for n in SCIENTIFIC_SOURCES},
            dependencies=after['dependencies'], toolchain=after['toolchain'])),
        limitations=fixture['limitations']+[
            'Rebound bundle/runner: scientific source, dependency and toolchain hashes match the original build.',
            'Alternative copies test delivery scale; they are not additional scientific policies.'])
    write_json(output/'manifest.json', manifest)
    legacy.check_fixture(output)
    legacy.check_fixture(args.fixture)
    if fingerprint(args.fixture/'manifest.json')!=original:
        raise ValueError('Original fixture changed during rebind')
    print('Prepared new replay fixture: '+str(output/'manifest.json'))
    return 0


def task_plan(tasks, workers):
    if type(tasks) is not int or not 2<=tasks<=8 or workers not in (1,2):
        raise ValueError('Use two to eight equal-work tasks and one/two workers')
    return [list(range(n,tasks,workers)) for n in range(workers)]


def options(args, snapshot, fixture, fixture_path, destination, cache, memory, cpu, identity, tasks):
    value = legacy.container_options(args.image_id, snapshot, fixture_path,
        Path(fixture['raw_root']) if fixture['mode']=='raw-native' else None,
        destination, memory, identity, fixture.get('raw_directories'))
    value['command'] += ['--layout','bounded-pairs','--tasks',*map(str,tasks),
        '--cache','/cache','--cpu-millis',str(cpu),'--view-reads',str(args.view_reads)]
    value['nano_cpus'] = cpu*1_000_000
    value['volumes'][str(cache)] = dict(bind='/cache',mode='rw')
    return value


def metrics(report):
    """Counter differences include descendants; bytes measure kernel I/O, not CSV size."""
    bounds = report.get('metrics') or {}
    before = report.get('initial_metrics') or bounds.get('before')
    after = report.get('final_metrics') or bounds.get('after')
    if not before or not after:
        return {}
    result = dict(cpu_seconds=(after['cpu']['usage_usec']-before['cpu']['usage_usec'])/1e6,
        throttled_seconds=(after['cpu']['throttled_usec']-before['cpu']['throttled_usec'])/1e6,
        oom_events=after['memory_events']['oom']-before['memory_events']['oom'],
        cgroup_peak_bytes=after['memory_peak_bytes'])
    for field in ('rbytes','wbytes','rios','wios'):
        result[field] = sum(v.get(field,0) for v in after['io'].values())-sum(v.get(field,0) for v in before['io'].values())
    result['pressure_seconds'] = {kind:{key:(value-before['pressure_usec'][kind].get(key,0))/1e6
        for key,value in values.items()} for kind,values in after['pressure_usec'].items()}
    return result


def sampled_combined_peak(targets):
    """Align timestamped quarter-second samples; stopped containers contribute zero.

    Sampled peak can miss short spikes. Sum of individual cgroup peaks is also
    reported as a conservative ceiling, rather than called simultaneous usage.
    """
    events = []
    for index, target in enumerate(targets):
        path = target/'samples.jsonl'
        if not path.is_file():
            continue
        if path.stat().st_size>256*MIB:
            raise ValueError('Sample evidence exceeds its bound')
        with path.open() as source:
            for line in source:
                if len(line)>65536:
                    raise ValueError('Sample row exceeds its bound')
                sample = json.loads(line)
                events.append((sample['wall_time_ns'],index,
                    sample['memory_current_bytes'],sample['working_bytes']))
        record = legacy.read_json(target/'report.json')
        events.append((record['finished_wall_time_ns'],index,0,0))
    current = {}
    peaks = dict(charged_bytes=0,working_bytes=0)
    for _, index, charged, working in sorted(events):
        current[index] = (charged,working)
        peaks['charged_bytes'] = max(peaks['charged_bytes'],sum(v[0] for v in current.values()))
        peaks['working_bytes'] = max(peaks['working_bytes'],sum(v[1] for v in current.values()))
    return peaks


def summarise(record):
    tasks = [task for r in record['results'] for task in r.get('report',{}).get('tasks',[])]
    record['completed_tasks'] = len(tasks) if record['passed'] else 0
    if record['passed']:
        record['tasks_per_hour'] = len(tasks)*3600/record['elapsed_seconds']
        record['tasks_per_reserved_cpu_hour'] = record['tasks_per_hour']/(record['workers']*record['cpu_millis_per_worker']/1000)
        record['tasks_per_reserved_gib_hour'] = record['tasks_per_hour']/(record['workers']*record['memory_mib_per_worker']/1024)
        record['task_elapsed_seconds'] = [t['elapsed_seconds'] for t in tasks]
        record['task_ready_seconds'] = [(t['ready_wall_time_ns']-record['started_wall_time_ns'])/1e9 for t in tasks]
        record['task_queue_seconds'] = [(t['started_wall_time_ns']-record['started_wall_time_ns'])/1e9 for t in tasks]
        record['pair_ready_seconds'] = [(p['ready_wall_time_ns']-record['started_wall_time_ns'])/1e9
            for t in tasks for p in t['parts']]
        record['first_pair_ready_seconds'] = min(record['pair_ready_seconds'])
        record['all_comparisons_ready_seconds'] = max(record['task_ready_seconds'])
        reads = sorted(s for task in tasks for s in task['view_read_seconds'])
        record['view_read_seconds'] = dict(first=[t['view_read_seconds'][0] for t in tasks],
            count=len(reads),median=reads[len(reads)//2],p95=reads[max(0,(95*len(reads)+99)//100-1)],maximum=reads[-1])
        record['cache'] = {k:sum(t['cache'][k] for t in tasks) for k in (
            'hits','misses','restored_bytes','computed_bytes','published','lock_wait_seconds',
            'restore_seconds','compute_seconds')}
    record['worker_metrics'] = [metrics(r.get('report',{})) for r in record['results']]
    record['source_verification_passes'] = sum(r.get('report',{}).get('source_verification_passes',0) for r in record['results'])
    for key in ('cpu_seconds','throttled_seconds','rbytes','wbytes','rios','wios'):
        record[key] = sum(m.get(key,0) for m in record['worker_metrics'])
    record['sum_individual_cgroup_peaks_bytes'] = sum(m.get('cgroup_peak_bytes',0) for m in record['worker_metrics'])


def run_batch(client, args, snapshot, fixture, directory, cache, memory, cpu, workers,
              *, tasks=None, cancel_stage=None):
    directory.mkdir(mode=0o700)
    preflight = legacy.preflight_space(directory,memory,workers)
    cache.mkdir(mode=0o700, exist_ok=True)
    identity = uuid4().hex
    plan = task_plan(args.tasks_per_case,workers) if tasks is None else [tasks]
    record = dict(passed=False,cleanup=False,results=[],workers=workers,
        memory_mib_per_worker=memory,cpu_millis_per_worker=cpu,task_plan=plan,preflight=preflight,
        started_wall_time_ns=time.time_ns(),host_resource_samples=0,
        host_min_available_memory_bytes=preflight.get('available_memory_bytes',legacy.available_memory()),
        host_min_free_disk_bytes=shutil.disk_usage(directory).free)
    owned = []
    cancellation_started = None
    started = time.monotonic()
    try:
        # Create all workers first; their task assignment and total work are fixed.
        for n, selected in enumerate(plan,1):
            target = directory/f'worker-{n}'
            target.mkdir(mode=0o700)
            c = client.containers.create(**options(args,snapshot,fixture,snapshot/'fixture',
                target,cache,memory,cpu,identity,selected))
            owned.append((c,target))
        for c,_ in owned:
            c.start()
        deadline = started+args.timeout_seconds
        while True:
            running = False
            for c,target in owned:
                c.reload()
                running |= c.attrs['State']['Running']
                if cancel_stage and c.attrs['State']['Running'] and (target/'progress.json').is_file():
                    progress = legacy.read_json(target/'progress.json')
                    if progress['stage'].endswith(cancel_stage):
                        # Forced group cancellation also exercises cleanup when Python
                        # cannot run its finally blocks. Docker waits for PID namespace exit.
                        before = legacy.available_memory()
                        requested = time.time_ns()
                        tick = time.monotonic()
                        cancellation_started = tick
                        c.kill()
                        c.wait(timeout=30)
                        record['cancellation'] = dict(stage=progress['stage'],
                            progress=progress,kill_and_wait_seconds=time.monotonic()-tick,
                            host_available_bytes_before=before,
                            host_available_bytes_after=legacy.available_memory(),
                            requested_wall_time_ns=requested)
                        cancel_stage = None
            record['host_resource_samples'] += 1
            record['host_min_available_memory_bytes'] = min(record['host_min_available_memory_bytes'],legacy.available_memory())
            free = shutil.disk_usage(directory).free
            record['host_min_free_disk_bytes'] = min(record['host_min_free_disk_bytes'],free)
            if not running:
                break
            if time.monotonic()>=deadline:
                raise TimeoutError('Capacity batch exceeded its time bound')
            if free<GIB:
                raise RuntimeError('Low disk reserve; capacity batch stopped')
            time.sleep(.1 if cancel_stage else .5)
        record['elapsed_seconds'] = time.monotonic()-started
        for (c,target), selected in zip(owned,plan):
            c.reload()
            state = c.attrs['State']
            item = dict(exit_code=state['ExitCode'],oom_killed=bool(state.get('OOMKilled')),container_id=c.id,
                report_path=target.name+'/report.json')
            if (target/'report.json').is_file():
                item['report'] = legacy.read_json(target/'report.json')
            item['observed_oom_events'] = metrics(item.get('report',{})).get('oom_events',0)
            item['passed'] = (item['exit_code']==0 and not item['oom_killed']
                and not item['observed_oom_events']
                and item.get('report',{}).get('passed') is True and item['report'].get('cleanup') is True
                and [t['task'] for t in item['report'].get('tasks',[])]==selected)
            record['results'].append(item)
        record['passed'] = bool(record['results']) and all(r['passed'] for r in record['results'])
        if record['passed']:
            record['sampled_combined_peak'] = sampled_combined_peak([t for _,t in owned])
        summarise(record)
    except Exception as error:
        record.update(error_type=type(error).__name__,error=str(error)[:500])
    finally:
        failures = []
        cleanup_started = time.monotonic()
        for c,target in owned:
            try:
                (target/'container.log').write_bytes(c.logs()[-2*MIB:])
            except Exception as error:
                record.setdefault('diagnostic_errors',[]).append(type(error).__name__)
                record['passed'] = False
            try:
                legacy.cleanup_container(c,identity)
            except Exception as error:
                failures.append(dict(container_id=c.id,error_type=type(error).__name__))
        record.update(cleanup=not failures,cleanup_errors=failures,
            remove_and_cleanup_seconds=time.monotonic()-cleanup_started)
        if record['cleanup']:
            for _,target in owned:
                scratch = target/'private'
                if scratch.is_dir() and not scratch.is_symlink():
                    shutil.rmtree(scratch)
        record['cleanup_total_seconds'] = time.monotonic()-cleanup_started
        if record.get('cancellation'):
            record['cancellation']['removal_confirmed'] = record['cleanup']
            record['cancellation']['request_to_cleanup_seconds'] = time.monotonic()-cancellation_started
            record['cancellation']['host_available_bytes_after_cleanup'] = legacy.available_memory()
        write_json(directory/'case.json',record)
    if not record['cleanup']:
        raise RuntimeError('Container removal unconfirmed; no further cases may start')
    # Detailed per-stage counters remain in case.json and worker reports. Avoid
    # embedding them again in every top-level checkpoint of a long matrix.
    return dict(record,results=[dict(item,report={k:v for k,v in item.get('report',{}).items() if k!='stages'})
        for item in record['results']])


def result_identities(cases):
    by_task = defaultdict(set)
    for case in cases:
        if not case['passed']:
            continue
        for worker in case['results']:
            for task in worker['report']['tasks']:
                by_task[task['task']].add(json.dumps(task['result_identity'],sort_keys=True,separators=(',',':')))
    return by_task


def comparisons(cases):
    """Match the same round, memory, CPU and cache state; failed runs have no speedup."""
    rows = []
    for two in cases:
        if two['workers']!=2 or not two['passed']:
            continue
        one = next((c for c in cases if c['workers']==1 and c['passed'] and
            all(c[k]==two[k] for k in ('round','memory_mib_per_worker','cpu_millis_per_worker','cache_mode'))),None)
        if one:
            rows.append(dict(round=two['round'],memory_mib=two['memory_mib_per_worker'],
                cpu_millis=two['cpu_millis_per_worker'],cache_mode=two['cache_mode'],
                two_worker_speedup=one['elapsed_seconds']/two['elapsed_seconds'],
                one_worker_tasks_per_hour=one['tasks_per_hour'],two_worker_tasks_per_hour=two['tasks_per_hour']))
    return rows


def equal_cpu_comparisons(cases):
    """Two 1-CPU workers versus one 2-CPU worker: equal CPU, different RAM needs."""
    rows=[]
    for two in cases:
        if two['workers']!=2 or two['cpu_millis_per_worker']!=1000 or not two['passed']:
            continue
        one=next((c for c in cases if c['workers']==1 and c['cpu_millis_per_worker']==2000 and c['passed']
            and all(c[k]==two[k] for k in ('round','memory_mib_per_worker','cache_mode'))),None)
        if one:
            rows.append(dict(round=two['round'],memory_mib_per_worker=two['memory_mib_per_worker'],
                cache_mode=two['cache_mode'],two_worker_speedup=one['elapsed_seconds']/two['elapsed_seconds'],
                one_worker_reserved_memory_mib=one['memory_mib_per_worker'],
                two_worker_reserved_memory_mib=2*two['memory_mib_per_worker']))
    return rows


def cancellation_check(client,args,snapshot,fixture,output,reference):
    directory = output/'cancellation'
    directory.mkdir(mode=0o700)
    cache = directory/'cache'
    memory = args.cancellation_memory_mib
    cancelled = run_batch(client,args,snapshot,fixture,directory/'interrupted',cache,memory,1000,1,
        tasks=[0],cancel_stage='/aggregate')
    if not cancelled.get('cancellation') or not cancelled['cleanup']:
        raise RuntimeError('Cancellation was not exercised and confirmed; inspect its separate report')
    if any(r['oom_killed'] or r.get('observed_oom_events') for r in cancelled['results']):
        raise RuntimeError('Memory exhaustion is not a successful cancellation check')
    resumed = run_batch(client,args,snapshot,fixture,directory/'retry',cache,memory,1000,1,tasks=[0])
    tasks = [t for r in resumed['results'] for t in r.get('report',{}).get('tasks',[])]
    result = dict(passed=resumed['passed'] and len(tasks)==1 and tasks[0]['result_identity']==reference,
        interrupted=cancelled,retry=resumed,production_loans_enabled=False,
        limitation='Measures cancellation, removal and cached retry only; no model growth or rescue deadline is tested.')
    if result['passed']:
        result['retry_reused_summaries'] = tasks[0]['cache']['hits']>0
        result['passed'] &= result['retry_reused_summaries']
    write_json(directory/'report.json',result)
    # All owned containers must be gone before disposable cached rows are removed.
    shutil.rmtree(cache)
    return result


def run(args):
    import docker
    if os.getuid()==0:
        raise ValueError('Run as your normal Docker-enabled user, not root')
    task_plan(args.tasks_per_case,1)
    for name in ('frontend','fixture','build'):
        setattr(args,name,getattr(args,name).resolve(strict=True))
    args.output = args.output.absolute()
    if any(p.is_symlink() for p in (args.output,*args.output.parents)):
        raise ValueError('Output must not use symlinked directories')
    fixture,_ = legacy.check_fixture(args.fixture)
    checked_build(args.build)
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    write_attribution(args.output)
    record = dict(format='simpaths.visualiser.equal-work-trials.v1',passed=False,cases=[],
        trials_completed=False,production_changed=False,model_executions=False,
        started_at=datetime.now(timezone.utc).isoformat(),tasks_per_case=args.tasks_per_case,
        mode=fixture['mode'],limitations=fixture['limitations']+[
            'Cold means an empty private summary cache, not cold filesystem caches.',
            'Warm means retained bounded summaries in fresh workers; eviction can still cause misses.',
            'Tasks are independent identical-size experiments, not parallel stages within one calculation.',
            'The direct helper pipeline batches summaries before pairs; exact production standalone/pair job '
                'scheduling and its additional cache/source I/O are not simulated.',
            'Chart read times measure local helpers; no network, browser or active website load is tested.',
            'Sampled simultaneous memory can miss spikes; the sum of individual peaks is a separate ceiling.',
            'No production memory loans or model-rescue deadlines are validated.'])
    if fixture['mode']=='raw-native':
        record['limitations'].append('Every fresh worker rehashes raw sources before and after processing; '
            'long-lived production source-hash memoisation is not simulated. Verification times are reported separately.')
    client = None
    try:
        client = docker.from_env(timeout=30)
        args.image_id = client.images.get(args.image).id
        record['image_id'] = args.image_id
        if client.info().get('CgroupVersion')!='2':
            raise ValueError('Whole-worker measurements require cgroup v2')
        with legacy.exclusive(args.output.parent):
            snapshot = args.output/'snapshot'
            snapshot.mkdir(mode=0o700)
            record['sources'] = legacy.snapshot_tools(args.frontend,args.build,snapshot,fixture)
            record['supervisor'] = snapshot_files({'visualiser_capacity_comparison.py':Path(__file__),
                'visualiser_capacity.py':Path(legacy.__file__)},snapshot/'supervisor')
            sources = {'manifest.json':args.fixture/'manifest.json'}
            if fixture['mode']=='summary-replay':
                sources.update({n:args.fixture/n for n in fixture['files']})
            record['fixture_files'] = snapshot_files(sources,snapshot/'fixture')
            for round_number in range(1,args.rounds+1):
                # Reverse on alternate rounds to expose order/host warming effects.
                workers = args.workers if round_number%2 else list(reversed(args.workers))
                cpus = args.cpu_millis if round_number%2 else list(reversed(args.cpu_millis))
                memories = args.memory_mib if round_number%2 else list(reversed(args.memory_mib))
                for memory in memories:
                    for cpu in cpus:
                        for count in workers:
                            group = args.output/f'round-{round_number}-{memory}m-{cpu}cpu-{count}workers'
                            group.mkdir(mode=0o700)
                            cache = group/'cache'
                            cold_passed = True
                            for mode in args.cache_modes:
                                if mode=='warm' and not cold_passed:
                                    record.setdefault('skipped',[]).append(dict(group=group.name,mode=mode,
                                        reason='Cold batch failed; no comparable warm-cache state'))
                                    continue
                                name = group.name+'/'+mode
                                print('Running '+name+f'; {args.tasks_per_case} total comparisons',flush=True)
                                value = run_batch(client,args,snapshot,fixture,group/mode,cache,memory,cpu,count)
                                record['cases'].append(dict(value,name=name,round=round_number,cache_mode=mode))
                                if mode=='cold':
                                    cold_passed = value['passed']
                                write_json(args.output/'report.json',record)
                                print(('PASS: ' if value['passed'] else 'FAILED: ')+name,flush=True)
                            if cache.exists():
                                shutil.rmtree(cache)
            final_fixture,_ = legacy.check_fixture(args.fixture)
            if (final_fixture!=fixture or fingerprint(args.fixture/'manifest.json')!=record['fixture_files']['manifest.json']):
                raise ValueError('Original fixture changed during the comparison sequence')
            record['trials_completed'] = True
            identities = result_identities(record['cases'])
            record['successful_cases_have_identical_results'] = (set(identities)==set(range(args.tasks_per_case))
                and all(len(values)==1 for values in identities.values()))
            record['comparisons'] = comparisons(record['cases'])
            record['equal_cpu_comparisons'] = equal_cpu_comparisons(record['cases'])
            record['passed'] = (bool(record['cases']) and all(c['passed'] for c in record['cases'])
                and not record.get('skipped') and record['successful_cases_have_identical_results'])
            if args.cancellation_check:
                if not record['successful_cases_have_identical_results']:
                    raise ValueError('Cancellation comparison requires matching uninterrupted results')
                record['cancellation'] = cancellation_check(client,args,snapshot,fixture,args.output,
                    json.loads(next(iter(identities[0]))))
                record['passed'] &= record['cancellation']['passed']
    except BaseException as error:
        record.update(passed=False,error_type=type(error).__name__,error=str(error)[:500])
        raise
    finally:
        write_json(args.output/'report.json',record)
        write_summary(args.output,record)
        if client is not None:
            client.close()
    print('Evidence: '+str(args.output/'report.json'))
    return 0 if record['passed'] else 1


def write_summary(output,record):
    lines = ['# Equal-work Visualiser capacity trials','',
        f"Every successful batch completes {record['tasks_per_case']} whole comparisons.",
        'Ready times include queueing behind earlier tasks in the same worker. Timings are local, not browser latency.','',
        '| Case | Passed | Seconds | Comparisons/hour | First pair / all comparisons ready seconds | Cache hits / misses | CPU seconds | Read / write MiB | Sampled working / charged GiB |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for c in record['cases']:
        cache = c.get('cache',{})
        peak = c.get('sampled_combined_peak',{})
        lines.append(f"| {c['name']} | {c['passed']} | {c.get('elapsed_seconds',0):.2f} | "
            f"{c.get('tasks_per_hour',0):.2f} | {c.get('first_pair_ready_seconds',0):.2f} / "
            f"{c.get('all_comparisons_ready_seconds',0):.2f} | {cache.get('hits',0)} / {cache.get('misses',0)} | "
            f"{c.get('cpu_seconds',0):.2f} | {c.get('rbytes',0)/MIB:.1f} / {c.get('wbytes',0)/MIB:.1f} | "
            f"{peak.get('working_bytes',0)/GIB:.3f} / {peak.get('charged_bytes',0)/GIB:.3f} |")
    lines += ['','## Matched worker comparisons','',
        'Speedup is one-worker elapsed time divided by two-worker elapsed time, for equal work.',
        'Values below 1 mean two workers took longer.','',
        '| Round | Memory MiB | CPU per worker | Cache | Two-worker speedup |',
        '| --- | ---: | ---: | --- | ---: |']
    for c in record.get('comparisons',[]):
        lines.append(f"| {c['round']} | {c['memory_mib']} | {c['cpu_millis']/1000:g} | {c['cache_mode']} | {c['two_worker_speedup']:.3f} |")
    lines += ['','## Equal total CPU: one 2-vCPU worker versus two 1-vCPU workers','',
        'Both complete the same work with a total allowance of 2 vCPUs. Two workers reserve twice as much RAM.','',
        '| Round | Memory per worker MiB | Cache | Two-worker speedup |',
        '| --- | ---: | --- | ---: |']
    for c in record.get('equal_cpu_comparisons',[]):
        lines.append(f"| {c['round']} | {c['memory_mib_per_worker']} | {c['cache_mode']} | {c['two_worker_speedup']:.3f} |")
    lines += ['','## Scope','',*('- '+s for s in record['limitations'])]
    (output/'comparison.md').write_text('\n'.join(lines)+'\n')
