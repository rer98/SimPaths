#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Quiet laptop resource matrix for two retained-summary preparation tasks: compare
RAM, per-worker CPU quota and sequential/concurrent workers with native cgroups.
@author ross richardson
"""
import argparse
import csv
from datetime import datetime,timezone
import html
from itertools import product
import json
import os
from pathlib import Path
import shutil
import signal
import statistics
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy.multirun import visualiser_repetition_trials as original
from deploy.multirun import visualiser_repetition_worker as base
from deploy.multirun.artifacts import fingerprint,snapshot_files,write_attribution
legacy=original.legacy
write_json=base.api.write_json

LIMITATIONS=[base.NOTICE,
    'Each batch contains two independent baseline–alternative preparation tasks, not two alternatives in one experiment.',
    'One worker runs both tasks sequentially; two workers each run one, in separate containers.',
    'RAM and CPU values are per worker. Two workers double the total reserved RAM and CPU quota.',
    'Both experiments have separate retained-summary files and cache identities; cache priming precedes a shared start barrier.',
    'Time to first/both results starts at barrier release and ends at catalogue readiness, before the final representative chart read.',
    'Raw parsing, simulation runs, queue waits, HTTP/network and competing production workloads are excluded.',
    'Filesystem caches are not forcibly cleared. Profiles and repetition counts rotate between rounds.',
    'Node maximum old space follows the existing policy: container MiB minus 1024; helper address space is container MiB minus 256.',
    'Memory figures are sampled per-worker peaks; brief spikes may be missed. Native cgroup peaks also include priming.',
    'A failed worker is recorded and all owned containers are removed before the next batch. Unconfirmed cleanup stops the benchmark.',
    'Comparisons/hour are an extrapolation for this two-task batch, not measured sustained VPS throughput.']


def trial_plan(memory,cpu,workers,counts,rounds):
    base.trial_order(counts,rounds)
    for values,allowed in ((memory,{2048,2560,3072}),(cpu,{1000,2000}),(workers,{1,2})):
        if not isinstance(values,list) or not values or len(values)!=len(set(values)) or any(type(v) is not int or v not in allowed for v in values):
            raise ValueError('Use distinct supported RAM, CPU and worker values')
    profiles=list(product(memory,workers,cpu))
    result=[]
    for r in range(rounds):
        offset=r%len(profiles)
        for i,(m,w,c) in enumerate(profiles[offset:]+profiles[:offset]):
            shift=(2*r+i)%len(counts)
            for n in counts[shift:]+counts[:shift]:
                result.append(dict(round=r+1,repetitions=n,memory_mib=m,cpu_millis=c,workers=w))
    return result


def container_options(args,snapshot,output,control,profile,worker_index,identity):
    value=legacy.container_options(args.image_id,snapshot,snapshot/'fixture',None,output,profile['memory_mib'],identity)
    value['entrypoint']=['python3','/tools/visualiser_regeneration_worker.py']
    tasks=2 if profile['workers']==1 else 1
    value['command']+=['--control','/control','--cpu-millis',str(profile['cpu_millis']),
        '--repetitions',str(profile['repetitions']),'--round',str(profile['round']),
        '--tasks',str(tasks),'--task-start',str(worker_index+1),'--timeout-seconds',str(args.timeout_seconds)]
    value['nano_cpus']=profile['cpu_millis']*1_000_000
    value['volumes'][str(control)]=dict(bind='/control',mode='ro')
    return value


def check_batch(reports,profile,released):
    """A fast partial result or changed science must never count as a fast batch."""
    cases=[]
    if len(reports)!=profile['workers']:raise ValueError('Missing worker report')
    for index,report in enumerate(reports):
        wanted=[1,2] if profile['workers']==1 else [index+1]
        rows=report.get('cases',[])
        if (report.get('passed') is not True or report.get('cleanup') is not True or report.get('sources_unchanged') is not True
                or report.get('memory_mib')!=profile['memory_mib'] or report.get('cpu_millis')!=profile['cpu_millis']
                or report.get('released_monotonic_ns')!=released or [c.get('task') for c in rows]!=wanted):
            raise ValueError('Worker result or native resource profile is incomplete')
        for c in rows:
            if (not c.get('passed') or not c.get('cleanup') or c.get('repetitions')!=profile['repetitions']
                    or c.get('round')!=profile['round'] or type(c.get('ready_monotonic_ns')) is not int
                    or type(c.get('started_monotonic_ns')) is not int
                    or not released<=c['started_monotonic_ns']<=c['ready_monotonic_ns']
                    or set(c.get('stages',{}))!=set(base.STAGES) or not all(s.get('passed') for s in c['stages'].values())):
                raise ValueError('An incomplete preparation task cannot be timed as ready')
            cases.append(c)
        if original.metrics(report).get('oom_events',0):raise ValueError('Worker recorded memory exhaustion')
    identities={json.dumps(c['result_identity'],sort_keys=True) for c in cases}
    if len(identities)!=1:raise ValueError('Independent tasks produced different aggregate results')
    first=(min(c['ready_monotonic_ns'] for c in cases)-released)/1e9
    both=(max(c['ready_monotonic_ns'] for c in cases)-released)/1e9
    total_cpus=profile['workers']*profile['cpu_millis']/1000
    return dict(first_result_seconds=first,both_results_seconds=both,
        comparisons_per_hour=7200/both,comparisons_per_allocated_vcpu_hour=7200/both/total_cpus,
        result_identity=cases[0]['result_identity'],tasks=[dict(task=c['task'],
            regeneration_seconds=c['regeneration_seconds'],stages={k:s['elapsed_seconds'] for k,s in c['stages'].items()},
            sampled_working_peak_bytes=max(s['sampled_peaks'].get('working_bytes',0) for s in c['stages'].values()),
            sampled_memory_peak_bytes=max(s['sampled_peaks'].get('memory_current_bytes',0) for s in c['stages'].values())) for c in cases])


def run_batch(client,args,snapshot,directory,profile,*,deadline=None,options_factory=None,result_checker=None,armed_checker=None):
    # Raw-summary trials share native containment/cleanup, with their own work
    # allocation and result validation. Defaults preserve regeneration trials.
    options_factory=options_factory or container_options
    result_checker=result_checker or check_batch
    count=profile.get('containers',profile['workers'])
    directory.mkdir(mode=0o700)
    control=directory/'control';control.mkdir(mode=0o700)
    record=dict(profile,passed=False,cleanup=False,workers_evidence=[],
        preflight=legacy.preflight_space(directory,profile['memory_mib'],count))
    identity=uuid4().hex;owned=[];started=time.monotonic();released=None
    expiry=min(started+args.timeout_seconds,deadline) if deadline is not None else started+args.timeout_seconds
    try:
        for i in range(count):
            target=directory/('worker-'+str(i+1));target.mkdir(mode=0o700)
            container=client.containers.create(**options_factory(args,snapshot,target,control,profile,i,identity))
            owned.append((container,target));container.start()
        while True:
            states=[]
            for c,_ in owned:c.reload();states.append(c.attrs['State'])
            if any(not s['Running'] and (s.get('OOMKilled') or s['ExitCode']!=0) for s in states):
                record['failure_kind']='container-oom' if any(s.get('OOMKilled') for s in states) else 'worker-failed'
                break
            if all(not s['Running'] for s in states):break
            if released is None and all((p/'armed.json').is_file() for _,p in owned):
                for i,(_,p) in enumerate(owned):
                    marker=legacy.read_json(p/'armed.json')
                    if armed_checker is not None:
                        armed_checker(marker,i,profile)
                    elif marker!=dict(format='simpaths.visualiser.armed.v1',repetitions=profile['repetitions'],
                            task_start=i+1,tasks=2 if profile['workers']==1 else 1):
                        raise ValueError('Start barrier belongs to another trial')
                released=time.monotonic_ns()
                write_json(control/'release.json',dict(format='simpaths.visualiser.release.v1',released_monotonic_ns=released))
            if time.monotonic()>=expiry:
                record['failure_kind']='timeout';break
            if shutil.disk_usage(directory).free<legacy.GIB:raise RuntimeError('Less than 1 GiB filesystem headroom')
            time.sleep(.1)
        if 'failure_kind' not in record:
            if released is None:raise ValueError('Workers exited before the common start barrier')
            reports=[legacy.read_json(p/'report.json') for _,p in owned]
            record.update(result_checker(reports,profile,released),passed=True)
    finally:
        errors=[]
        for c,p in owned:
            item=dict(path=p.name+'/report.json',container_id=c.id)
            try:
                c.reload();item.update(exit_code=c.attrs['State'].get('ExitCode'),
                    oom_killed=bool(c.attrs['State'].get('OOMKilled')),running_before_cleanup=c.attrs['State']['Running'])
                (p/'container.log').write_bytes(c.logs()[-2*legacy.MIB:])
            except BaseException as error:errors.append('diagnostics:'+type(error).__name__)
            try:legacy.cleanup_container(c,identity)
            except BaseException as error:errors.append('container:'+type(error).__name__)
            record['workers_evidence'].append(item)
        # Never remove scratch until all native processes are confirmed gone.
        if not errors:
            for _,p in owned:
                scratch=p/'private'
                if scratch.exists():
                    if scratch.is_symlink():errors.append('linked-scratch');break
                    try:shutil.rmtree(scratch)
                    except BaseException as error:errors.append('scratch:'+type(error).__name__)
        for item,(_,p) in zip(record['workers_evidence'],owned):
            if (p/'report.json').is_file():
                try:
                    detail=legacy.read_json(p/'report.json')
                    item.update(error_type=detail.get('error_type'),failure_kind=detail.get('failure_kind'),metrics=original.metrics(detail),
                        failed_stages=[k for c in detail.get('cases',[]) for k,s in c.get('stages',{}).items() if not s.get('passed')])
                except Exception as error:errors.append('evidence:'+type(error).__name__)
        if any(i.get('oom_killed') or i.get('metrics',{}).get('oom_events') for i in record['workers_evidence']):
            record.update(passed=False,failure_kind='container-oom')
        record.update(cleanup=not errors,cleanup_errors=errors,elapsed_including_priming_seconds=time.monotonic()-started)
        if errors:record['passed']=False
        write_json(directory/'report.json',record)
    if not record['cleanup']:raise RuntimeError('Native container cleanup is unconfirmed; remaining trials stopped')
    return record


def summaries(cases,rounds):
    keys=sorted({(c['repetitions'],c['memory_mib'],c['workers'],c['cpu_millis']) for c in cases})
    result=[]
    for n,m,w,c in keys:
        rows=[r for r in cases if (r['repetitions'],r['memory_mib'],r['workers'],r['cpu_millis'])==(n,m,w,c)]
        good=[r for r in rows if r['passed']]
        value=dict(repetitions=n,memory_mib=m,workers=w,cpu_millis=c,trials=len(rows),passed_trials=len(good),
            expected_trials=rounds,passed=len(rows)==rounds and len(good)==rounds,
            failures=[r.get('failure_kind','failed') for r in rows if not r['passed']],
            failed_stages=sorted({stage for r in rows for w in r.get('workers_evidence',[]) for stage in w.get('failed_stages',[])}))
        if good:
            for key in ('first_result_seconds','both_results_seconds','comparisons_per_hour','comparisons_per_allocated_vcpu_hour'):
                value[key]=statistics.median(r[key] for r in good)
            value['both_min_seconds']=min(r['both_results_seconds'] for r in good)
            value['both_max_seconds']=max(r['both_results_seconds'] for r in good)
            value['stages']={s:statistics.median(t['stages'][s] for r in good for t in r['tasks']) for s in base.STAGES}
            value['sampled_working_peak_bytes']=max(t['sampled_working_peak_bytes'] for r in good for t in r['tasks'])
            value['sampled_memory_peak_bytes']=max(t['sampled_memory_peak_bytes'] for r in good for t in r['tasks'])
        result.append(value)
    return result


def write_tables(directory,report):
    values=report.get('summary',[])
    fields=['repetitions','memory_mib','workers','cpu_millis','passed_trials','expected_trials','passed',
        'first_result_seconds','both_results_seconds','both_min_seconds','both_max_seconds','comparisons_per_hour',
        'comparisons_per_allocated_vcpu_hour','sampled_working_peak_bytes','sampled_memory_peak_bytes']
    with (directory/'comparison.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(values)
    def row(items,heading=False):
        tag='th' if heading else 'td'
        return '<tr>'+''.join(f'<{tag}>{html.escape(str(i))}</{tag}>' for i in items)+'</tr>'
    def number(v,key):return f'{v[key]:.2f}' if key in v else '—'
    tables=[]
    for n in report['repetitions']:
        body=[];stages=[]
        for v in [x for x in values if x['repetitions']==n]:
            body.append(row([v['memory_mib']/1024,v['workers'],v['cpu_millis']/1000,v['workers']*v['cpu_millis']/1000,
                v['workers']*v['memory_mib']/1024,('PASS' if v['passed'] else 'FAILED / INCOMPLETE'),
                ', '.join(sorted(set(v['failures'])))+(' ('+', '.join(v['failed_stages'])+')' if v['failed_stages'] else ''),
                f"{v['passed_trials']}/{v['expected_trials']}",number(v,'first_result_seconds'),number(v,'both_results_seconds'),
                '—' if 'both_min_seconds' not in v else f"{v['both_min_seconds']:.2f}–{v['both_max_seconds']:.2f}",
                number(v,'comparisons_per_hour'),number(v,'comparisons_per_allocated_vcpu_hour'),
                '—' if 'sampled_working_peak_bytes' not in v else f"{v['sampled_working_peak_bytes']/legacy.GIB:.2f}",
                '—' if 'sampled_memory_peak_bytes' not in v else f"{v['sampled_memory_peak_bytes']/legacy.GIB:.2f}"]))
            stages.append(row([v['memory_mib']/1024,v['workers'],v['cpu_millis']/1000,
                *[f"{v['stages'][s]:.2f}" if 'stages' in v else '—' for s in base.STAGES]]))
        headers=['GiB / worker','Workers','vCPU / worker','Total vCPU','Total GiB','Status','Failure / stage','Passed rounds',
            'First result (s)','Both results (s)','Both: range (s)','Comparisons/hour*','Comparisons/vCPU-hour*',
            'Sampled working peak / worker (GiB)','Sampled total peak / worker (GiB)']
        tables.append(f'<h2>{n} repetitions per configuration</h2><div class="scroll"><table><thead>'+row(headers,True)
            +'</thead><tbody>'+''.join(body)+'</tbody></table></div><details><summary>Median stage time per task (seconds)</summary>'
            +'<div class="scroll"><table><thead>'+row(['GiB / worker','Workers','vCPU / worker','Restore summaries',
                'Combine statistics','Validate rows','Write publication','Index charts','Catalogue','First chart read'],True)
            +'</thead><tbody>'+''.join(stages)+'</tbody></table></div></details>')
    page=('<!-- (C) Copyright 2026, by Ross Richardson\nGenerated regeneration resource tables.\n@author ross richardson\n-->\n'
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Visualiser regeneration resource trials</title>'
        '<style>body{font:16px system-ui;margin:2em;color:#193249}.scroll{overflow:auto}table{border-collapse:collapse;font-size:13px}'
        'th,td{padding:.55em;border:1px solid #bdcbd5;text-align:right}th{background:#e8f0f5}p,li{max-width:85em}details{margin:1em 0}</style>'
        '<h1>Visualiser regeneration: RAM, CPU and workers</h1><p><strong>'
        +('ALL PASSED' if report.get('passed') else 'COMPLETED WITH FAILURES' if report.get('completed') else 'INCOMPLETE')
        +'</strong>. Every batch contains two independent baseline–alternative comparisons. Each task uses '
        'the indicated repetitions in both configurations.</p><p>One worker prepares the two tasks sequentially; '
        'two workers start together after priming. Compare one worker × 2 vCPU with two workers × 1 vCPU '
        'for the same total CPU allowance; the latter reserves twice as much RAM.</p>'
        +''.join(tables)+'<p>Timing medians use successful rounds only; failed/incomplete rows are not valid performance winners. '
        'Stage medians span both tasks and may not add up to either completion-time median. '
        '* Hourly figures extrapolate the two-task batch; vCPU-hour divides by total configured CPU quota. '
        'Peak columns give the largest sampled value of an individual worker, not combined simultaneous RAM. '
        'Native whole-container peaks and detailed I/O/CPU/memory counters remain in each worker report.</p><ul>'
        +''.join('<li>'+html.escape(s)+'</li>' for s in LIMITATIONS)+'</ul></html>\n')
    (directory/'comparison.html').write_text(page)


def run(args):
    import docker
    if os.getuid()==0:raise ValueError('Use your normal Docker-enabled account')
    plan=trial_plan(args.memory_mib,args.cpu_millis,args.workers,args.repetitions,args.rounds)
    for key in ('frontend','fixture','source_build','build'):setattr(args,key,getattr(args,key).resolve(strict=True))
    args.output=args.output.absolute()
    if any(p.is_symlink() for p in (args.output,*args.output.parents)):raise ValueError('No symlinked evidence directories')
    if any(args.output==p or p in args.output.parents for p in (args.frontend,args.fixture,args.source_build,args.build)):
        raise ValueError('Keep evidence outside inputs and source repositories')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False);write_attribution(args.output)
    report=dict(format='simpaths.visualiser.regeneration-trials.v1',passed=False,completed=False,cleanup=True,
        model_executions=False,raw_parsing=False,production_changed=False,synthetic=True,
        started_at=datetime.now(timezone.utc).isoformat(),repetitions=args.repetitions,rounds=args.rounds,
        memory_mib=args.memory_mib,cpu_millis=args.cpu_millis,workers=args.workers,plan=plan,cases=[],limitations=LIMITATIONS)
    client=None;references={};deadline=time.monotonic()+args.overall_timeout_seconds
    try:
        with legacy.exclusive(args.output.parent):
            report['preflight']=legacy.preflight_space(args.output,max(args.memory_mib),max(args.workers))
            snapshot=args.output/'snapshot';snapshot.mkdir(mode=0o700)
            original.rebind(SimpleNamespace(fixture=args.fixture,source_build=args.source_build,build=args.build,
                output=snapshot/'fixture',alternatives=1))
            fixture,_=legacy.check_fixture(snapshot/'fixture')
            report['sources']=legacy.snapshot_tools(args.frontend,args.build,snapshot,fixture)
            for name in ('visualiser_repetition_worker.py','visualiser_regeneration_worker.py'):
                report['sources']['tools'][name]=fingerprint(ROOT/'deploy/multirun'/name,snapshot/'tools'/name)
            report['sources']['supervisor']=snapshot_files({name:ROOT/'deploy/multirun'/name for name in
                ('visualiser_regeneration_trials.py','visualiser_repetition_trials.py','visualiser_capacity_comparison.py')},snapshot/'supervisor')
            client=docker.from_env(timeout=30)
            if client.info().get('CgroupVersion')!='2':raise ValueError('Native cgroup v2 limits are required')
            args.image_id=client.images.get(args.image).id;report['image_id']=args.image_id
            for i,profile in enumerate(plan,1):
                if time.monotonic()>=deadline:raise TimeoutError('Overall matrix time bound reached')
                name=f"{i:03d}-r{profile['round']}-{profile['repetitions']}seeds-{profile['memory_mib']}m-{profile['workers']}w-{profile['cpu_millis']}cpu"
                print('Running '+name,flush=True)
                result=run_batch(client,args,snapshot,args.output/name,profile,deadline=deadline)
                result['directory']=name;report['cases'].append(result)
                if result['passed']:
                    key=profile['repetitions'];signature=json.dumps(result['result_identity'],sort_keys=True)
                    if references.setdefault(key,signature)!=signature:raise ValueError('Aggregate output changed between resource profiles')
                    print(f"PASS: first {result['first_result_seconds']:.1f} s; both {result['both_results_seconds']:.1f} s",flush=True)
                else:print('FAILED BATCH: '+result.get('failure_kind','failed')+'; cleanup confirmed; continuing',flush=True)
                report['summary']=summaries(report['cases'],args.rounds)
                write_json(args.output/'report.json',report);write_tables(args.output,report)
            legacy.check_fixture(args.fixture);legacy.check_fixture(snapshot/'fixture')
            report['completed']=True;report['passed']=all(c['passed'] for c in report['cases'])
    except (Exception,KeyboardInterrupt) as error:
        report.update(passed=False,error_type=type(error).__name__,error=str(error)[:500])
        # A failed cleanup remains visible even if the batch raised before return.
        for path in args.output.glob('*/report.json'):
            if path.parent.name=='snapshot':continue
            if legacy.read_json(path).get('cleanup') is False:report['cleanup']=False
        print('STOPPED: '+type(error).__name__+'; inspect retained evidence.',file=sys.stderr)
    finally:
        report['summary']=summaries(report['cases'],args.rounds)
        report['finished_at']=datetime.now(timezone.utc).isoformat()
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
    parser.add_argument('--repetitions',type=int,nargs='+',default=[2,5,10])
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--timeout-seconds',type=int,default=900)
    parser.add_argument('--overall-timeout-seconds',type=int,default=28800)
    args=parser.parse_args(argv)
    trial_plan(args.memory_mib,args.cpu_millis,args.workers,args.repetitions,args.rounds)
    if not 60<=args.timeout_seconds<=3600 or not args.timeout_seconds<=args.overall_timeout_seconds<=86400:
        parser.error('Use a 60..3600 second batch bound and an overall bound up to 24 hours')
    os.umask(0o077)
    def interrupted(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    return run(args)


if __name__=='__main__':raise SystemExit(main())
