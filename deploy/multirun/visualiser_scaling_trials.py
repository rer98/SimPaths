#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Compare single-configuration parallel repetitions under a fixed budget and
one/two/four-worker preparation scaling, with native group-wide containment.
@author ross richardson
"""
import argparse
import csv
from datetime import datetime,timezone
import html
import json
import os
from pathlib import Path
import re
import signal
import shutil
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy.multirun import visualiser_summary_trials as raw
from deploy.multirun import visualiser_scaling_worker as worker
from deploy.multirun.artifacts import fingerprint,snapshot_files,write_attribution
native=raw.native
legacy=raw.legacy
api=worker.api
PROFILES=worker.PROFILES

LIMITATIONS=[
    'Raw inputs are two genuine repetitions per configuration, covering all 52 annual years; no simulations run.',
    'Single profiles process the baseline only. Experiment profiles process baseline and alternative, their standalone charts and one paired comparison.',
    'Workers are concurrent tasks within ONE container. CPU/RAM limits and memory peaks cover the entire group, including the coordinator.',
    'Per-process Node old space follows the stated policy; workers do not each receive an independently enforced CPU/RAM cgroup.',
    'The fixed-budget single-serial/single-parallel comparison uses the same TOTAL 5 GiB and 2 vCPUs, not 5 GiB per worker.',
    'Scaling profiles complete the same four raw seed tasks; whole-group quotas scale from 2.5 GiB/1 vCPU to 10 GiB/4 vCPUs.',
    'Summary-ready events, not simulation-finished events, unlock standalone and paired preparation. No whole-experiment simulation barrier is used.',
    'The optional extra backlog repeats the same retained-summary pair four times; it is a publication capacity probe, not four different policies.',
    'Every main case starts with an empty summary cache. No source CSVs are copied or deleted; filesystem caches are not forcibly flushed.',
    'Stage resource snapshots cover concurrent group activity and cannot be summed or treated as individual worker usage.',
    'This is an isolated preparation prototype: database reservation handover, active model contention, HTTPS/browser responsiveness and hosted policy activation are separate work.',
    'Quarter-second samples may miss spikes. Native cgroup memory includes file cache; passing does not certify every model setting or ten genuine seeds.']


def plan(names,rounds):
    if (not isinstance(names,list) or not names or len(names)!=len(set(names)) or any(n not in PROFILES for n in names)
            or type(rounds) is not int or not 1<=rounds<=5):raise ValueError('Use distinct known profiles and one to five rounds')
    result=[]
    for r in range(rounds):
        offset=(r*2)%len(names)
        for name in names[offset:]+names[:offset]:
            result.append(dict(name=name,round=r+1,repetitions=2,containers=1,**PROFILES[name]))
    return result


def preflight(directory,profile):
    free=shutil.disk_usage(directory).free;available=legacy.available_memory()
    if free<8*legacy.GIB:raise ValueError('Need 8 GiB free for bounded shared summary/publication scratch')
    if available<profile['memory_mib']*api.MIB+2*legacy.GIB:
        raise ValueError('Need whole-group RAM plus 2 GiB available host headroom')
    return dict(free_disk_bytes=free,available_memory_bytes=available)


def options(args,snapshot,output,control,profile,index,identity):
    if index:raise ValueError('Scaling uses one whole-group container')
    value=legacy.container_options(args.image_id,snapshot,snapshot/'fixture',args.raw_root,output,
        profile['memory_mib'],identity,args.raw_manifest['raw_directories'])
    value['entrypoint']=['python3','/tools/visualiser_scaling_worker.py']
    # The legacy convenience arguments described one worker; this worker reads
    # the immutable named group profile instead.
    value['command']=value['command'][:-2]
    value['command']+=['--control','/control','--raw-root','/raw','--profile',profile['name'],
        '--timeout-seconds',str(args.timeout_seconds),'--publication-backlog',str(args.publication_backlog)]
    value['nano_cpus']=profile['cpu_millis']*1_000_000
    value['volumes'][str(control)]=dict(bind='/control',mode='ro')
    return value


def check_armed(marker,index,profile):
    if index or marker!=worker.armed(profile['name']):raise ValueError('Start marker belongs to another scaling profile')


def check_results(reports,profile,released,*,manifest,backlog):
    if len(reports)!=1:raise ValueError('Missing whole-group result')
    report=reports[0]
    if (not all(report.get(k) is True for k in ('passed','cleanup','sources_unchanged','raw_parsing'))
            or report.get('profile')!=profile['name'] or report.get('released_monotonic_ns')!=released
            or any(report.get(k)!=v for k,v in PROFILES[profile['name']].items())):
        raise ValueError('Incomplete scaling result or wrong resource group')
    count=1 if profile['scope']=='single' else 2
    graph=worker.Graph(count);cases=report.get('cases',[])
    if len(cases)!=len(graph.nodes) or {c.get('id') for c in cases}!=set(graph.nodes):raise ValueError('Missing graph task')
    by_id={c['id']:c for c in cases};seed_identity={};publication_identity={}
    def timed(task):
        if (not task.get('passed') or any(type(task.get(k)) is not int for k in
            ('started_monotonic_ns','ready_monotonic_ns','finished_monotonic_ns'))
                or not released<=task['started_monotonic_ns']<=task['ready_monotonic_ns']<=task['finished_monotonic_ns']
                or not task.get('stages') or not all(s.get('passed') for s in task['stages'].values())):
            raise ValueError('Incomplete task cannot count as ready')
    for key,node in graph.nodes.items():
        task=by_id[key];timed(task)
        if task.get('kind')!=node['kind'] or task.get('config')!=node['config']:
            raise ValueError('Task graph identity differs')
        if any(task['started_monotonic_ns']<by_id[d]['ready_monotonic_ns'] for d in node['needs']):
            raise ValueError('Publication started before its seed summaries were ready')
        if node['kind']=='seed':
            config=manifest['configurations'][node['config']];seed=manifest['seeds'][node['run']]
            r=task.get('summary',{});v=task.get('validation',{})
            if (task.get('configuration')!=config['id'] or task.get('seed')!=seed
                    or type(r.get('bytes')) is not int or not 0<r['bytes']<=64*api.MIB
                    or not re.fullmatch('[a-f0-9]{64}',str(r.get('sha256')))
                    or v.get('seed')!=seed or v.get('annual_years')!=manifest['annual_years']
                    or v.get('canonical_role')!='baseline' or not v.get('rows')):raise ValueError('Seed identity/coverage differs')
            seed_identity[config['id']+'/'+seed]=r
        else:
            if task.get('result_identity')!={k:task.get(k) for k in ('publication','index','catalogue','view','validation')}:
                raise ValueError('Published chart identity is incomplete')
            publication_identity[key]=task['result_identity']
    summaries=[max(by_id[f'seed-{c}-{r}']['ready_monotonic_ns'] for r in range(2)) for c in range(count)]
    standalone=[by_id[f'standalone-{c}']['ready_monotonic_ns'] for c in range(count)]
    ready=max(c['ready_monotonic_ns'] for c in cases)
    if report.get('experiment_finished_monotonic_ns')!=ready:raise ValueError('Experiment readiness differs')
    extra=report.get('publication_backlog',{});wanted=backlog if count==2 else 0
    if wanted:
        tasks=extra.get('tasks',[])
        if (extra.get('replayed_pair_tasks')!=wanted or len(tasks)!=wanted
                or {t['id'] for t in tasks}!={'replay-pair-'+str(i) for i in range(wanted)}
                or type(extra.get('started_monotonic_ns')) is not int or extra['started_monotonic_ns']<ready):
            raise ValueError('Extra publication backlog is incomplete')
        for t in tasks:
            timed(t)
            if t['started_monotonic_ns']<extra['started_monotonic_ns'] or t['result_identity']!=publication_identity['pair']:
                raise ValueError('Repeated publication changed its science or readiness')
    elif extra:raise ValueError('Unexpected replay backlog')
    counters=report.get('cache_statistics',{}).get('counters',{})
    expected_hits=2 if count==1 else 8+4*wanted
    if (any(counters.get(k)!=count*2 for k in ('misses','raw_started','raw_completed'))
            or counters.get('hits')!=expected_hits or counters.get('evictions')!=0 or counters.get('raw_failed')!=0):
        raise ValueError('Expected fresh raw work and summary-only publication reuse')
    main_metrics=raw.provenance.metrics(dict(initial_metrics=report.get('timed_initial_metrics'),final_metrics=report.get('experiment_final_metrics')))
    if raw.provenance.metrics(report).get('oom_events',0):raise ValueError('Preparation group exhausted memory')
    peak=report.get('sampled_peaks',{}).get('raw-and-ready-publications',{})
    result=dict(first_summary_seconds=(min(summaries)-released)/1e9,all_summary_seconds=(max(summaries)-released)/1e9,
        first_chart_seconds=(min(standalone)-released)/1e9,all_standalone_seconds=(max(standalone)-released)/1e9,
        fully_ready_seconds=(ready-released)/1e9,pair_seconds=(by_id['pair']['ready_monotonic_ns']-released)/1e9 if count==2 else None,
        seed_identity=seed_identity,publication_identity=publication_identity,
        sampled_working_peak_bytes=peak.get('working_bytes',0),sampled_memory_peak_bytes=peak.get('memory_current_bytes',0),
        main_metrics=main_metrics)
    result['configurations_per_hour']=count*3600/result['fully_ready_seconds']
    result['configurations_per_allocated_vcpu_hour']=result['configurations_per_hour']/(profile['cpu_millis']/1000)
    if wanted:
        result['backlog_first_seconds']=(min(t['ready_monotonic_ns'] for t in extra['tasks'])-extra['started_monotonic_ns'])/1e9
        result['backlog_all_seconds']=(max(t['ready_monotonic_ns'] for t in extra['tasks'])-extra['started_monotonic_ns'])/1e9
    return result


FIELDS=('first_summary_seconds','all_summary_seconds','first_chart_seconds','all_standalone_seconds',
        'pair_seconds','fully_ready_seconds','backlog_first_seconds','backlog_all_seconds',
        'configurations_per_hour','configurations_per_allocated_vcpu_hour')


def summaries(cases,rounds):
    result=[]
    for name in PROFILES:
        rows=[c for c in cases if c['name']==name]
        if not rows:continue
        good=[r for r in rows if r['passed']];stat=statistics.mean if len(good)==2 else statistics.median
        value=dict(name=name,**PROFILES[name],passed=len(rows)==rounds and len(good)==rounds,
            passed_rounds=len(good),expected_rounds=rounds,timing_statistic='mean' if len(good)==2 else 'median',
            failures=sorted({r.get('failure_kind','failed') for r in rows if not r['passed']}))
        for key in FIELDS:
            values=[r[key] for r in good if r.get(key) is not None]
            if values:value[key]=stat(values)
        if good:
            value['ready_min_seconds']=min(r['fully_ready_seconds'] for r in good)
            value['ready_max_seconds']=max(r['fully_ready_seconds'] for r in good)
            for k in ('sampled_working_peak_bytes','sampled_memory_peak_bytes'):value[k]=max(r[k] for r in good)
            value['cpu_seconds']=stat(r['main_metrics'].get('cpu_seconds',0) for r in good)
            value['throttled_seconds']=stat(r['main_metrics'].get('throttled_seconds',0) for r in good)
            value['io_pressure_seconds']=stat(r['main_metrics'].get('pressure_seconds',{}).get('io',{}).get('some',0) for r in good)
            value['cpu_pressure_seconds']=stat(r['main_metrics'].get('pressure_seconds',{}).get('cpu',{}).get('some',0) for r in good)
            for k in ('rbytes','wbytes'):value[k]=stat(r['main_metrics'].get(k,0) for r in good)
        result.append(value)
    return result


def write_tables(directory,report):
    rows=report.get('summary',[])
    with (directory/'comparison.csv').open('w',newline='') as f:
        fields=['name','scope','workers','memory_mib','cpu_millis','process_memory_mib','passed','passed_rounds','expected_rounds','timing_statistic',
            *FIELDS,'ready_min_seconds','ready_max_seconds','sampled_working_peak_bytes','sampled_memory_peak_bytes',
            'cpu_seconds','throttled_seconds','io_pressure_seconds','cpu_pressure_seconds','rbytes','wbytes']
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    def row(values,heading=False):
        tag='th' if heading else 'td'
        return '<tr>'+''.join(f'<{tag}>{html.escape(str(v))}</{tag}>' for v in values)+'</tr>'
    def num(v,key,scale=1):return f'{v[key]/scale:.2f}' if key in v else '—'
    tables=[]
    for scope,title in [('single','One configuration: same-budget parallel repetitions'),('experiment','Two configurations: one, two and four workers')]:
        body=[];extras=[]
        for v in [v for v in rows if v['scope']==scope]:
            body.append(row([v['name'],v['workers'],v['memory_mib']/1024,v['cpu_millis']/1000,
                'PASS' if v['passed'] else 'FAILED / INCOMPLETE: '+', '.join(v['failures']),
                f"{v['passed_rounds']}/{v['expected_rounds']} ({v['timing_statistic']})",
                *[num(v,k,60) for k in ('first_summary_seconds','all_summary_seconds','first_chart_seconds','fully_ready_seconds')],
                num(v,'sampled_working_peak_bytes',legacy.GIB),num(v,'sampled_memory_peak_bytes',legacy.GIB)]))
            extras.append(row([v['name'],num(v,'pair_seconds'),num(v,'backlog_first_seconds'),num(v,'backlog_all_seconds'),
                num(v,'configurations_per_hour'),num(v,'configurations_per_allocated_vcpu_hour'),
                num(v,'cpu_seconds'),num(v,'throttled_seconds'),num(v,'io_pressure_seconds'),num(v,'cpu_pressure_seconds'),
                num(v,'rbytes',legacy.GIB),num(v,'wbytes',api.MIB)]))
        tables.append('<h2>'+title+'</h2><div class="scroll"><table><thead>'+row(['Profile','Parallel workers',
            'TOTAL RAM GiB','TOTAL vCPU','Status','Valid rounds / timing basis','First config summaries (min)',
            'All config summaries (min)','First standalone charts (min)','All required charts ready (min)',
            'Group working peak GiB','Group charged peak GiB'],True)+'</thead><tbody>'+''.join(body)+'</tbody></table></div>'
            +'<details><summary>Pair/publication backlog and group counters</summary><div class="scroll"><table><thead>'
            +row(['Profile','Genuine pair ready (s from start)','Extra backlog first pair (s)','Extra backlog four pairs (s)',
                'Configs/hour (extrapolated)','Configs/allocated vCPU-hour','Main CPU seconds','CPU throttled (s)',
                'I/O pressure some (s)','CPU pressure some (s)','Main physical read GiB','Main write MiB'],True)
            +'</thead><tbody>'+''.join(extras)+'</tbody></table></div></details>')
    page=('<!-- (C) Copyright 2026, by Ross Richardson\nGenerated preparation scaling tables.\n@author ross richardson\n-->\n'
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Visualiser preparation scaling</title>'
        '<style>body{font:16px system-ui;margin:2em;color:#193249}.scroll{overflow:auto}table{border-collapse:collapse;font-size:13px}'
        'th,td{padding:.55em;border:1px solid #bdcbd5;text-align:right}th{background:#e8f0f5}p,li{max-width:85em}</style>'
        '<h1>Visualiser preparation scaling</h1><p><strong>'
        +('ALL PASSED' if report.get('passed') else 'COMPLETED WITH FAILURES' if report.get('completed') else 'INCOMPLETE')
        +'</strong>. Two genuine repetitions per configuration; 2019–2070. '
        'CPU/RAM and peaks describe the WHOLE processing group, not each worker.</p>'
        '<p>The single test ends when its standalone charts are ready. The experiment test ends when both standalone publications '
        'and the baseline–alternative comparison are ready. A separate extra phase then republishes the same pair four times '
        'from retained summaries to probe publication concurrency; those are not four alternative policies. '
        'Each task verifies its catalogue and a representative chart read after recording catalogue readiness.</p>'
        +''.join(tables)+'<p>Times use successful rounds only; two valid rounds use their arithmetic mean. '
        'Incomplete profiles are not qualified performance winners. Peak columns show the largest sampled whole-group value, '
        'not an average or an individual worker. Configurations/hour extrapolates this finite main phase, including charts; '
        'compare profiles within the same scope, not as sustained production throughput. Task stages and native counters remain in worker reports.</p><ul>'
        +''.join('<li>'+html.escape(s)+'</li>' for s in LIMITATIONS)+'</ul></html>\n')
    (directory/'comparison.html').write_text(page)


def run(args):
    import docker
    if os.getuid()==0:raise ValueError('Use your normal Docker-enabled account')
    trials=plan(args.profiles,args.rounds)
    for n in ('frontend','fixture','source_build','build'):setattr(args,n,getattr(args,n).resolve(strict=True))
    args.output=args.output.absolute()
    if any(p.is_symlink() for p in (args.output,*args.output.parents)):raise ValueError('No symlinked evidence directories')
    if any(args.output==p or p in args.output.parents for p in (args.frontend,args.fixture,args.source_build,args.build)):
        raise ValueError('Keep evidence outside source/input directories')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False);write_attribution(args.output)
    report=dict(format='simpaths.visualiser.scaling-trials.v1',passed=False,completed=False,cleanup=True,rounds=args.rounds,
        started_at=datetime.now(timezone.utc).isoformat(),plan=trials,cases=[],limitations=LIMITATIONS,
        model_executions=False,production_changed=False,raw_repetitions=2,publication_backlog_replays=args.publication_backlog)
    client=None;references={};deadline=time.monotonic()+args.overall_timeout_seconds
    try:
        with legacy.exclusive(args.output.parent):
            report['preflight']=preflight(args.output,max(trials,key=lambda p:p['memory_mib']))
            snapshot=args.output/'snapshot';snapshot.mkdir(mode=0o700)
            args.raw_manifest,args.raw_root=raw.prepare_fixture(args,snapshot/'fixture')
            report['sources']=legacy.snapshot_tools(args.frontend,args.build,snapshot,args.raw_manifest)
            for n in ('visualiser_repetition_worker.py','visualiser_regeneration_worker.py','visualiser_summary_worker.py','visualiser_scaling_worker.py'):
                report['sources']['tools'][n]=fingerprint(ROOT/'deploy/multirun'/n,snapshot/'tools'/n)
            report['sources']['supervisor']=snapshot_files({n:ROOT/'deploy/multirun'/n for n in
                ('visualiser_scaling_trials.py','visualiser_summary_trials.py','visualiser_regeneration_trials.py',
                 'visualiser_repetition_trials.py','visualiser_capacity_comparison.py')},snapshot/'supervisor')
            client=docker.from_env(timeout=30)
            if client.info().get('CgroupVersion')!='2':raise ValueError('Native cgroup v2 is required')
            args.image_id=client.images.get(args.image).id;report['image_id']=args.image_id
            for i,profile in enumerate(trials,1):
                if time.monotonic()>=deadline:raise TimeoutError('Overall scaling time bound reached')
                preflight(args.output,profile)
                name=f"{i:03d}-r{profile['round']}-{profile['name']}";print('Running '+name,flush=True)
                result=native.run_batch(client,args,snapshot,args.output/name,profile,deadline=deadline,
                    options_factory=options,armed_checker=check_armed,
                    result_checker=lambda reports,p,start:check_results(reports,p,start,manifest=args.raw_manifest,backlog=args.publication_backlog))
                result['directory']=name;report['cases'].append(result)
                if any(w.get('failure_kind')=='integrity-failed' for w in result['workers_evidence']):
                    raise ValueError('Scaling integrity failed; inspect task reports')
                if result['passed']:
                    for kind in ('seed_identity','publication_identity'):
                        for key,value in result[kind].items():
                            signature=json.dumps(value,sort_keys=True);identity=kind+'/'+key
                            if references.setdefault(identity,signature)!=signature:raise ValueError('Calculated output changed: '+identity)
                    print(f"PASS: first charts {result['first_chart_seconds']/60:.2f} min; all required charts {result['fully_ready_seconds']/60:.2f} min",flush=True)
                else:print('FAILED BATCH: '+result.get('failure_kind','failed')+'; cleanup confirmed; continuing',flush=True)
                report['summary']=summaries(report['cases'],args.rounds);api.write_json(args.output/'report.json',report);write_tables(args.output,report)
            legacy.check_fixture(args.fixture);legacy.check_fixture(snapshot/'fixture')
            report.update(completed=True,passed=all(c['passed'] for c in report['cases']))
    except (Exception,KeyboardInterrupt) as error:
        report.update(passed=False,error_type=type(error).__name__,error_message=str(error)[:1000])
        for p in args.output.glob('*/report.json'):
            if legacy.read_json(p).get('cleanup') is False:report['cleanup']=False
        print('STOPPED: '+type(error).__name__+'; inspect private scaling evidence.',file=sys.stderr)
    finally:
        report['summary']=summaries(report['cases'],args.rounds);report['finished_at']=datetime.now(timezone.utc).isoformat()
        api.write_json(args.output/'report.json',report);write_tables(args.output,report)
        if client is not None:client.close()
    print('Evidence: '+str(args.output/'report.json'),flush=True);print('Readable tables: '+str(args.output/'comparison.html'),flush=True)
    return 0 if report['passed'] else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for n in ('frontend','fixture','source-build','build','output'):parser.add_argument('--'+n,type=Path,required=True)
    parser.add_argument('--profiles',choices=PROFILES,nargs='+',default=list(PROFILES))
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--image',default='simpaths-visualiser-capacity:20261015')
    parser.add_argument('--publication-backlog',type=int,choices=(0,4),default=4)
    parser.add_argument('--timeout-seconds',type=int,default=3600)
    parser.add_argument('--overall-timeout-seconds',type=int,default=21600)
    args=parser.parse_args(argv);plan(args.profiles,args.rounds)
    if not 60<=args.timeout_seconds<=7200 or not args.timeout_seconds<=args.overall_timeout_seconds<=86400:
        parser.error('Use a 60..7200 second case bound and an overall bound up to 24 hours')
    os.umask(0o077)
    def interrupted(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    return run(args)


if __name__=='__main__':raise SystemExit(main())
