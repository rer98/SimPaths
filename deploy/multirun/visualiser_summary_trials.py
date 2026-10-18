#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Quiet native matrix of raw CSV to retained intermediate summaries, using only
two genuine repetitions in each of a baseline and an alternative configuration.
@author ross richardson
"""
import argparse
import csv
from datetime import datetime,timezone
import html
from itertools import product
import json
import math
import os
from pathlib import Path
import re
import signal
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy.multirun import visualiser_regeneration_trials as native
from deploy.multirun import visualiser_capacity_comparison as provenance
from deploy.multirun.visualiser_summary_worker import STAGES
from deploy.multirun.artifacts import fingerprint,snapshot_files,write_attribution
legacy=native.legacy
api=native.base.api
write_json=api.write_json

LIMITATIONS=[
    'Each batch summarises two configurations: one baseline and one alternative, each with its two genuine seeds and all 52 years.',
    'One worker processes baseline then alternative. Two workers process these configurations concurrently, using disjoint raw files.',
    'Only raw-to-intermediate work is timed here. Statistical comparisons, final publications, chart indexing, HTTP and simulations are excluded.',
    'Every batch starts with an empty summary cache. No synthetic seed labels or five/ten-repetition workloads are used.',
    'First/both configurations ready includes per-seed source hashing, parsing, validation and verified retention of intermediates.',
    'Host source verification and worker startup precede the common start barrier. The raw files are mounted read-only without copying.',
    'Filesystem caches are not cleared. Source verification reads files before parsing; physical I/O can differ from logical input bytes.',
    'RAM and CPU are per worker; two workers double both reservations. Compare one 2-vCPU worker with two 1-vCPU workers for equal total CPU.',
    'Node maximum old space is container MiB minus 1024. Container memory and Node heap change together, so RAM effects are not isolated.',
    'Memory is sampled every 0.25 seconds. Sampled peaks may miss spikes; whole-cgroup peaks/counters remain in worker reports.',
    'A resource-failed batch is cleaned up before continuing; integrity failure or unconfirmed cleanup stops the matrix.',
    'Configurations/hour extrapolates this batch, not sustained throughput alongside VPS simulations or other users.']


def trial_plan(memory,cpu,workers,rounds):
    if type(rounds) is not int or not 1<=rounds<=5:raise ValueError('Use one to five rounds')
    for values,allowed in ((memory,{2048,2560,3072}),(cpu,{1000,2000}),(workers,{1,2})):
        if not isinstance(values,list) or not values or len(values)!=len(set(values)) or any(type(v) is not int or v not in allowed for v in values):
            raise ValueError('Use distinct supported RAM, CPU and worker values')
    profiles=list(product(memory,workers,cpu));plan=[]
    for r in range(rounds):
        offset=(r*5)%len(profiles)
        plan.extend(dict(round=r+1,repetitions=2,memory_mib=m,workers=w,cpu_millis=c)
            for m,w,c in profiles[offset:]+profiles[:offset])
    return plan


def prepare_fixture(args,destination):
    """Explicit raw provenance adapter; never rebind/relabel replay summaries."""
    original=fingerprint(args.fixture/'manifest.json')
    fixture,raw_root=legacy.check_fixture(args.fixture)
    if fixture['mode']!='raw-native' or len(fixture['seeds'])!=2 or len(fixture['configurations'])!=2 or len(fixture['files'])!=8:
        raise ValueError('Use the original two-repetition baseline/alternative raw fixture')
    before,after=provenance.checked_build(args.source_build),provenance.checked_build(args.build)
    for name in ('runner','calculation'):
        if fixture['backend'][name+'_sha256']!=before['files'][name+'.cjs']:
            raise ValueError('Original raw fixture does not match its source build')
    for name in provenance.SCIENTIFIC_SOURCES:
        if not before.get('source_hashes',{}).get(name) or before['source_hashes'][name]!=after.get('source_hashes',{}).get(name):
            raise ValueError('Scientific source changed: '+name)
    for name in ('dependencies','toolchain'):
        if not before.get(name) or before[name]!=after.get(name):raise ValueError('Calculation dependencies/toolchain changed')
    if 'simpaths.visualiser.role-independent-metrics.v1' not in (args.build/'runner.cjs').read_text():
        raise ValueError('Use the reviewed canonical-summary runner')
    if any(args.output==raw_root/d or raw_root/d in args.output.parents for d in fixture['raw_directories']):
        raise ValueError('Keep trial evidence outside the retained raw directories')
    destination.mkdir(mode=0o700)
    write_attribution(destination)
    adapted=dict(fixture,backend={name+'_sha256':after['files'][name+'.cjs'] for name in ('runner','calculation')},
        provenance=dict(fixture.get('provenance',{}),raw_summary_trial=dict(
            original_fixture=original,original_backend=fixture['backend'],
            original_build=fingerprint(args.source_build/'build.json'),selected_build=fingerprint(args.build/'build.json'),
            scientific_source_hashes={n:after['source_hashes'][n] for n in provenance.SCIENTIFIC_SOURCES},
            dependencies=after['dependencies'],toolchain=after['toolchain'],raw_files_copied=False)))
    write_json(destination/'manifest.json',adapted)
    if fingerprint(args.fixture/'manifest.json')!=original:raise ValueError('Original fixture changed during snapshot')
    legacy.check_fixture(destination,hash_files=False)
    return adapted,raw_root


def container_options(args,snapshot,output,control,profile,worker_index,identity):
    fixture=args.raw_manifest
    value=legacy.container_options(args.image_id,snapshot,snapshot/'fixture',args.raw_root,output,
        profile['memory_mib'],identity,fixture['raw_directories'])
    value['entrypoint']=['python3','/tools/visualiser_summary_worker.py']
    value['nano_cpus']=profile['cpu_millis']*1_000_000
    value['volumes'][str(control)]=dict(bind='/control',mode='ro')
    value['command']+=['--control','/control','--raw-root','/raw','--cpu-millis',str(profile['cpu_millis']),
        '--repetitions','2','--round',str(profile['round']),'--task-start',str(worker_index+1),
        '--tasks',str(2 if profile['workers']==1 else 1),'--timeout-seconds',str(args.timeout_seconds)]
    return value


def check_batch(reports,profile,released,*,manifest):
    cases=[];metrics=[]
    if len(reports)!=profile['workers']:raise ValueError('Missing summary worker')
    for index,report in enumerate(reports):
        wanted=[1,2] if profile['workers']==1 else [index+1]
        rows=report.get('cases',[])
        if (not all(report.get(k) is True for k in ('passed','cleanup','sources_unchanged','raw_parsing'))
                or report.get('synthetic') is not False or report.get('memory_mib')!=profile['memory_mib']
                or report.get('cpu_millis')!=profile['cpu_millis'] or report.get('released_monotonic_ns')!=released
                or [c.get('task') for c in rows]!=wanted):raise ValueError('Incomplete raw worker or resource profile')
        counters=report.get('cache_statistics',{}).get('counters',{});count=len(wanted)*2
        if (any(counters.get(k)!=0 for k in ('hits','evictions','raw_failed'))
                or any(counters.get(k)!=count for k in ('misses','raw_started','raw_completed'))):
            raise ValueError('Raw benchmark reused/skipped an intermediate or evicted its work')
        if provenance.metrics(report).get('oom_events',0):raise ValueError('Worker recorded memory exhaustion')
        metrics.append(provenance.metrics(dict(initial_metrics=report.get('timed_initial_metrics'),
            final_metrics=report.get('timed_final_metrics'))))
        for c in rows:
            expected=manifest['configurations'][c['task']-1]
            seeds=c.get('seeds',[])
            if (not c.get('passed') or not c.get('cleanup') or c.get('configuration')!=expected['id']
                    or c.get('repetitions')!=2 or c.get('round')!=profile['round']
                    or type(c.get('ready_monotonic_ns')) is not int or type(c.get('started_monotonic_ns')) is not int
                    or not released<=c['started_monotonic_ns']<=c['ready_monotonic_ns']
                    or [s.get('seed') for s in seeds]!=manifest['seeds'] or set(c.get('stages',{}))!=set(STAGES)):
                raise ValueError('Incomplete configuration cannot count as a ready summary')
            for s in seeds:
                receipt=s.get('summary',{});v=s.get('validation',{})
                if (not s.get('passed') or type(receipt.get('bytes')) is not int or not 0<receipt['bytes']<=64*api.MIB
                        or not re.fullmatch('[a-f0-9]{64}',str(receipt.get('sha256')))
                        or v.get('seed')!=s['seed'] or v.get('canonical_role')!='baseline'
                        or v.get('annual_years')!=manifest['annual_years'] or not v.get('rows')
                        or set(s.get('stages',{}))!=set(STAGES) or not all(v.get('passed') for v in s['stages'].values())):
                    raise ValueError('Incomplete per-seed intermediate receipt')
            if c.get('result_identity')!={s['seed']:s['summary'] for s in seeds}:raise ValueError('Summary identity differs')
            for stage in c['stages'].values():
                value=stage.get('elapsed_seconds')
                if not stage.get('passed') or type(value) not in (int,float) or not math.isfinite(value) or value<0:
                    raise ValueError('Incomplete raw-summary stage')
            cases.append(c)
    first=(min(c['ready_monotonic_ns'] for c in cases)-released)/1e9
    both=(max(c['ready_monotonic_ns'] for c in cases)-released)/1e9
    if both<=0:raise ValueError('Invalid summary completion time')
    return dict(first_result_seconds=first,both_results_seconds=both,configurations_per_hour=7200/both,
        configurations_per_allocated_vcpu_hour=7200/both/(profile['workers']*profile['cpu_millis']/1000),
        result_identity={c['configuration']:c['result_identity'] for c in cases},
        tasks=[dict(task=c['task'],configuration=c['configuration'],summary_seconds=c['summary_seconds'],
            stages={s:v['elapsed_seconds'] for s,v in c['stages'].items()},
            summary_bytes=sum(s['summary']['bytes'] for s in c['seeds']),
            sampled_working_peak_bytes=max(v['sampled_peaks'].get('working_bytes',0) for v in c['stages'].values()),
            sampled_memory_peak_bytes=max(v['sampled_peaks'].get('memory_current_bytes',0) for v in c['stages'].values())) for c in cases],
        **{key:sum(m.get(key,0) for m in metrics) for key in ('cpu_seconds','throttled_seconds','rbytes','wbytes')})


NUMERIC=('first_result_seconds','both_results_seconds','configurations_per_hour','configurations_per_allocated_vcpu_hour',
         'cpu_seconds','throttled_seconds','rbytes','wbytes')


def summaries(cases,rounds):
    result=[]
    for m,w,c in sorted({(v['memory_mib'],v['workers'],v['cpu_millis']) for v in cases}):
        rows=[v for v in cases if (v['memory_mib'],v['workers'],v['cpu_millis'])==(m,w,c)]
        good=[v for v in rows if v['passed']]
        value=dict(memory_mib=m,workers=w,cpu_millis=c,passed_trials=len(good),expected_trials=rounds,
            passed=len(rows)==rounds and len(good)==rounds,failures=sorted({r.get('failure_kind','failed') for r in rows if not r['passed']}))
        if good:
            value.update({k:statistics.median(r[k] for r in good) for k in NUMERIC})
            value['stages']={s:statistics.median(t['stages'][s] for r in good for t in r['tasks']) for s in STAGES}
            value['both_min_seconds']=min(r['both_results_seconds'] for r in good)
            value['both_max_seconds']=max(r['both_results_seconds'] for r in good)
            for k in ('sampled_working_peak_bytes','sampled_memory_peak_bytes'):
                value[k]=max(t[k] for r in good for t in r['tasks'])
            value['summary_bytes']=statistics.median(sum(t['summary_bytes'] for t in r['tasks']) for r in good)
        result.append(value)
    return result


def write_tables(directory,report):
    rows=report.get('summary',[])
    fields=['memory_mib','workers','cpu_millis','passed_trials','expected_trials','passed',*NUMERIC,
        'both_min_seconds','both_max_seconds','summary_bytes','sampled_working_peak_bytes','sampled_memory_peak_bytes',*STAGES]
    with (directory/'comparison.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        writer.writerows(dict(v,**v.get('stages',{})) for v in rows)
    def row(values,heading=False):
        tag='th' if heading else 'td'
        return '<tr>'+''.join(f'<{tag}>{html.escape(str(v))}</{tag}>' for v in values)+'</tr>'
    def num(v,key,scale=1):return f'{v[key]/scale:.2f}' if key in v else '—'
    headers=['GiB / worker','Workers','vCPU / worker','Total GiB','Total vCPU','Status','Passed rounds',
        'First configuration ready (s)','Both configurations ready (s)','Both: range (s)',
        'Configurations/hour*','Configurations/vCPU-hour*','Working peak / worker (GiB)',
        'Charged peak / worker (GiB)','CPU seconds / batch','Physical read MiB / batch',
        'Physical write MiB / batch','Two configurations’ summaries (MiB)']
    body=[];stages=[]
    for v in rows:
        label='PASS' if v['passed'] else 'FAILED / INCOMPLETE: '+', '.join(v['failures'])
        body.append(row([v['memory_mib']/1024,v['workers'],v['cpu_millis']/1000,v['workers']*v['memory_mib']/1024,
            v['workers']*v['cpu_millis']/1000,label,f"{v['passed_trials']}/{v['expected_trials']}",
            num(v,'first_result_seconds'),num(v,'both_results_seconds'),
            '—' if 'both_min_seconds' not in v else f"{v['both_min_seconds']:.2f}–{v['both_max_seconds']:.2f}",
            num(v,'configurations_per_hour'),num(v,'configurations_per_allocated_vcpu_hour'),
            num(v,'sampled_working_peak_bytes',legacy.GIB),num(v,'sampled_memory_peak_bytes',legacy.GIB),
            num(v,'cpu_seconds'),num(v,'rbytes',api.MIB),num(v,'wbytes',api.MIB),num(v,'summary_bytes',api.MIB)]))
        stages.append(row([v['memory_mib']/1024,v['workers'],v['cpu_millis']/1000,
            *[num(v.get('stages',{}),s) for s in STAGES]]))
    page=('<!-- (C) Copyright 2026, by Ross Richardson\nGenerated raw-summary resource tables.\n@author ross richardson\n-->\n'
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Visualiser raw-summary resource trials</title>'
        '<style>body{font:16px system-ui;margin:2em;color:#193249}.scroll{overflow:auto}table{border-collapse:collapse;font-size:13px}'
        'th,td{padding:.55em;border:1px solid #bdcbd5;text-align:right}th{background:#e8f0f5}p,li{max-width:85em}</style>'
        '<h1>Raw CSV → intermediate Visualiser summaries</h1><p><strong>'
        +('ALL PASSED' if report.get('passed') else 'COMPLETED WITH FAILURES' if report.get('completed') else 'INCOMPLETE')
        +'</strong>. Two genuine repetitions per configuration; 2019–2070 inclusive.</p>'
        '<p>Each batch summarises one baseline and one alternative. One worker runs them sequentially; two workers '
        'start together and process one configuration each. “First” means either configuration has both seed summaries retained; '
        '“both” means all four seed summaries are retained. Final comparisons and charts are not produced here.</p>'
        '<h2>Completion times and resources</h2><div class="scroll"><table><thead>'+row(headers,True)
        +'</thead><tbody>'+''.join(body)+'</tbody></table></div>'
        '<h2>Median stage time per configuration (two seeds), seconds</h2><div class="scroll"><table><thead>'
        +row(['GiB / worker','Workers','vCPU / worker','Verify source checksums','Parse CSV and calculate statistics',
            'Validate and retain intermediates'],True)+'</thead><tbody>'+''.join(stages)+'</tbody></table></div>'
        '<p>Times are medians of successful rounds only; failed/incomplete profiles are not valid performance winners. '
        'Stage medians span both configurations, so they need not sum to a completion-time median. Source hashing is included '
        'in readiness times and shown separately. The cache is empty, but the filesystem may be warm. '
        'Logical CSV bytes and per-seed timing/counters are retained in worker JSON; physical I/O depends on caching. '
        'Peak columns give the largest sampled individual worker, not combined simultaneous memory. '
        '* Hourly figures extrapolate this two-configuration batch; vCPU-hour divides by total configured CPU quota.</p><ul>'
        +''.join('<li>'+html.escape(s)+'</li>' for s in LIMITATIONS)+'</ul></html>\n')
    (directory/'comparison.html').write_text(page)


def run(args):
    import docker
    if os.getuid()==0:raise ValueError('Use your normal Docker-enabled account')
    plan=trial_plan(args.memory_mib,args.cpu_millis,args.workers,args.rounds)
    for name in ('frontend','fixture','source_build','build'):setattr(args,name,getattr(args,name).resolve(strict=True))
    args.output=args.output.absolute()
    if any(p.is_symlink() for p in (args.output,*args.output.parents)):raise ValueError('No symlinked evidence directories')
    if any(args.output==p or p in args.output.parents for p in (args.frontend,args.fixture,args.source_build,args.build)):
        raise ValueError('Keep evidence outside inputs and source repositories')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False);write_attribution(args.output)
    report=dict(format='simpaths.visualiser.summary-trials.v1',passed=False,completed=False,cleanup=True,
        model_executions=False,raw_parsing=True,synthetic=False,production_changed=False,repetitions=2,
        started_at=datetime.now(timezone.utc).isoformat(),rounds=args.rounds,plan=plan,cases=[],limitations=LIMITATIONS)
    client=None;reference=None;deadline=time.monotonic()+args.overall_timeout_seconds
    try:
        with legacy.exclusive(args.output.parent):
            report['preflight']=legacy.preflight_space(args.output,max(args.memory_mib),max(args.workers))
            snapshot=args.output/'snapshot';snapshot.mkdir(mode=0o700)
            args.raw_manifest,args.raw_root=prepare_fixture(args,snapshot/'fixture')
            report['logical_input_bytes_per_batch']=sum(v['bytes'] for v in args.raw_manifest['files'].values())
            report['sources']=legacy.snapshot_tools(args.frontend,args.build,snapshot,args.raw_manifest)
            for name in ('visualiser_repetition_worker.py','visualiser_regeneration_worker.py','visualiser_summary_worker.py'):
                report['sources']['tools'][name]=fingerprint(ROOT/'deploy/multirun'/name,snapshot/'tools'/name)
            report['sources']['supervisor']=snapshot_files({name:ROOT/'deploy/multirun'/name for name in
                ('visualiser_summary_trials.py','visualiser_regeneration_trials.py','visualiser_repetition_trials.py',
                 'visualiser_capacity_comparison.py')},snapshot/'supervisor')
            client=docker.from_env(timeout=30)
            if client.info().get('CgroupVersion')!='2':raise ValueError('Native cgroup v2 limits are required')
            args.image_id=client.images.get(args.image).id;report['image_id']=args.image_id
            for i,profile in enumerate(plan,1):
                if time.monotonic()>=deadline:raise TimeoutError('Overall raw-summary time bound reached')
                name=f"{i:03d}-r{profile['round']}-2seeds-{profile['memory_mib']}m-{profile['workers']}w-{profile['cpu_millis']}cpu"
                print('Running '+name,flush=True)
                result=native.run_batch(client,args,snapshot,args.output/name,profile,deadline=deadline,
                    options_factory=container_options,result_checker=lambda reports,p,released:check_batch(reports,p,released,manifest=args.raw_manifest))
                if 'elapsed_including_priming_seconds' in result:
                    result['elapsed_including_startup_seconds']=result.pop('elapsed_including_priming_seconds')
                result['directory']=name;report['cases'].append(result)
                if any(w.get('failure_kind')=='integrity-failed' for w in result['workers_evidence']):
                    raise ValueError('Raw-summary integrity failed; inspect the private worker evidence')
                if result['passed']:
                    signature=json.dumps(result['result_identity'],sort_keys=True)
                    if reference is not None and signature!=reference:raise ValueError('Intermediate summaries changed across resource profiles')
                    reference=signature
                    print(f"PASS: first configuration {result['first_result_seconds']:.1f} s; both {result['both_results_seconds']:.1f} s",flush=True)
                else:print('FAILED BATCH: '+result.get('failure_kind','failed')+'; cleanup confirmed; continuing',flush=True)
                report['summary']=summaries(report['cases'],args.rounds)
                write_json(args.output/'report.json',report);write_tables(args.output,report)
            legacy.check_fixture(args.fixture);legacy.check_fixture(snapshot/'fixture')
            report['completed']=True;report['passed']=all(c['passed'] for c in report['cases'])
    except (Exception,KeyboardInterrupt) as error:
        report.update(passed=False,error_type=type(error).__name__,error=str(error)[:500])
        for path in args.output.glob('*/report.json'):
            if legacy.read_json(path).get('cleanup') is False:report['cleanup']=False
        print('STOPPED: '+type(error).__name__+'; inspect retained evidence.',file=sys.stderr)
    finally:
        report['summary']=summaries(report['cases'],args.rounds);report['finished_at']=datetime.now(timezone.utc).isoformat()
        write_json(args.output/'report.json',report);write_tables(args.output,report)
        if client is not None:client.close()
    print('Evidence: '+str(args.output/'report.json'),flush=True)
    print('Readable tables: '+str(args.output/'comparison.html'),flush=True)
    return 0 if report['passed'] else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('frontend','fixture','source-build','build','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--image',default='simpaths-visualiser-capacity:20261015')
    parser.add_argument('--memory-mib',type=int,nargs='+',default=[2560,2048,3072])
    parser.add_argument('--cpu-millis',type=int,nargs='+',default=[1000,2000])
    parser.add_argument('--workers',type=int,nargs='+',default=[1,2])
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--timeout-seconds',type=int,default=3600)
    parser.add_argument('--overall-timeout-seconds',type=int,default=43200)
    args=parser.parse_args(argv)
    trial_plan(args.memory_mib,args.cpu_millis,args.workers,args.rounds)
    if not 60<=args.timeout_seconds<=7200 or not args.timeout_seconds<=args.overall_timeout_seconds<=86400:
        parser.error('Use a 60..7200 second batch bound and an overall bound up to 24 hours')
    os.umask(0o077)
    def interrupted(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    return run(args)


if __name__=='__main__':raise SystemExit(main())
