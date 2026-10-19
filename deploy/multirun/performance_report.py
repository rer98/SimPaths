"""(C) Copyright 2026, by Ross Richardson

Labelled review tables and allocation/progress segments from bounded observations.
Interpret observational data without attributing unknown intervals to a quota.
@author ross richardson
"""
from collections import defaultdict
from datetime import datetime,timezone
import html
import json


def applied_cpu(attempt,start,end):
    resources=attempt.get('initial_resources') or attempt.get('resources') or {}
    for change in attempt.get('changes') or []:
        created=datetime.fromisoformat(change['created_at']).timestamp()
        finished=datetime.fromisoformat(change['finished_at']).timestamp() if change['finished_at'] else None
        if created<=end and (finished is None or finished>=start):return None
        if change['state']=='confirmed' and finished is not None and finished<start:
            resources=change['target_resources']
    return resources.get('cpu_millis',0)/1000 or None


def analyse(trace):
    groups=defaultdict(list)
    for row in trace['observations']:groups[row['kind'],row['subject']].append(row)
    segments=[]
    from uuid import NAMESPACE_URL,uuid5
    attempt_by_subject={str(uuid5(NAMESPACE_URL,'jasmine-review:model:'+row['id'])):row for row in trace['attempts']}
    for (kind,key),rows in groups.items():
        rows.sort(key=lambda row:(row['observed_at'],row['data'].get('sequence',-1),row['id']))
        context={}
        previous_java=None
        for row in rows:
            data=row['data']
            if row['event']=='java':
                if previous_java is not None:
                    before=previous_java['data']
                    elapsed=(data['elapsed_ms']-before['elapsed_ms'])/1000
                    first=datetime.fromisoformat(previous_java['observed_at']).timestamp()
                    last=datetime.fromisoformat(row['observed_at']).timestamp()
                    quota=applied_cpu(attempt_by_subject.get(key,{}),first,last)
                    known=data.get('process_cpu_ns') is not None and before.get('process_cpu_ns') is not None
                    delta=data['process_cpu_ns']-before['process_cpu_ns'] if known else None
                    state=('reset' if elapsed<0 or delta is not None and delta<0 else
                        'gap' if data['sequence']!=before['sequence']+1 else 'transition' if quota is None else 'measured')
                    segments.append(dict(subject=key,source='Java process',at=row['observed_at'],
                        **{k:before.get(k) for k in ('phase','year','repetition','population','population_observed_ms')},
                        interval_state=state,seconds=elapsed if elapsed>=0 else None,
                        cpu_seconds=delta/1e9 if delta is not None and delta>=0 else None,
                        gc_seconds=(data['gc_ms']-before['gc_ms'])/1000 if data.get('gc_ms') is not None and
                            before.get('gc_ms') is not None and data['gc_ms']>=before['gc_ms'] else None,
                        attributed_vcpus=quota if state=='measured' else None,
                        heap_gib=data['heap_used_bytes']/2**30 if data.get('heap_used_bytes') is not None else None))
                previous_java=row
                context={k:data.get(k) for k in ('phase','year','repetition','population','population_observed_ms')}
            if row['event']=='sample' and kind=='model':
                state=data.get('interval_state','initial')
                quota=None
                if state=='measured' and data.get('observed_seconds') is not None:
                    end=datetime.fromisoformat(row['observed_at']).timestamp()
                    quota=applied_cpu(attempt_by_subject.get(key,{}),end-data['observed_seconds'],end)
                    # The queue snapshot may predate a complete boost/reduction
                    # cycle during a slow native read. Recheck its durable journal.
                    if quota is None or quota!=data.get('cpu_millis',0)/1000:state='transition'
                segments.append(dict(subject=key,source='Container',at=row['observed_at'],**context,
                    interval_state=state,seconds=data.get('observed_seconds'),
                    cpu_seconds=data.get('cpu_delta_ns')/1e9 if data.get('cpu_delta_ns') is not None else None,
                    attributed_vcpus=quota if state=='measured' else None,
                    working_gib=max(0,data.get('memory_bytes',0)-data.get('inactive_bytes',0))/2**30))
    ready=defaultdict(list)
    for item in trace['subjects']:
        if item['kind']!='visualiser':continue
        data=item['data']
        if 'ready_epoch_ms' in data:
            for job in data.get('source_jobs',[]):ready[job].append(data['ready_epoch_ms']/1000)
    attempts=[]
    for item in trace['attempts']:
        row=dict(item)
        finished=datetime.fromisoformat(row['finished_at']).timestamp() if row['finished_at'] else None
        charts=ready.get(row['job_id'],[])
        row['first_observed_chart_delay_seconds']=max(0,min(charts)-finished) if charts and finished is not None else None
        row['last_observed_chart_delay_seconds']=max(0,max(charts)-finished) if charts and finished is not None else None
        row['chart_coverage']='observed publications only; missing/truncated tasks may change these bounds'
        attempts.append(row)
    experiments=[]
    ready_subjects=[item['data'] for item in trace['subjects'] if item['kind']=='visualiser' and 'ready_epoch_ms' in item['data']]
    pipeline=[]
    for item in trace['subjects']:
        if item['kind']!='visualiser':continue
        data=item['data']
        def duration(begin,end):
            a,b=data.get(begin+'_epoch_ms'),data.get(end+'_epoch_ms')
            return (b-a)/1000 if a is not None and b is not None and b>=a else None
        pipeline.append(dict(subject=item['subject'],experiment=data.get('experiment'),
            source_jobs=data.get('source_jobs'),comparison=data.get('comparison'),
            queued_seconds=duration('queued','started'),summary_seconds=duration('started','comparisons'),
            comparison_seconds=duration('comparisons','publication'),validation_seconds=duration('publication','indexing'),
            indexing_seconds=duration('indexing','ready'),processing_seconds=duration('started','ready'),
            raw_seconds=data['raw_elapsed_ns']/1e9 if data.get('raw_elapsed_ns') is not None else None,
            hits=data.get('hits'),misses=data.get('misses')))
    for experiment in trace.get('experiments',[]):
        jobs=experiment['jobs'];keys={j['job'] for j in jobs}
        publications=[p for p in ready_subjects if p.get('experiment')==experiment['id']]
        baseline=next((j['job'] for j in jobs if j.get('baseline')),None)
        required=[{baseline,j['job']} for j in jobs if j['job']!=baseline] if baseline and len(jobs)>1 else [{j['job']} for j in jobs]
        times=[]
        for combination in required:
            matching=[p['ready_epoch_ms'] for p in publications if combination<=set(p.get('source_jobs',[]))
                and (len(combination)==1 or p.get('comparison')==1)]
            if matching:times.append(min(matching)/1000)
        all_ready=bool(required) and len(times)==len(required) and all(j['state']=='succeeded' for j in jobs)
        experiments.append(dict(experiment=experiment['id'],configurations=len(keys),
            required_publications=len(required),observed_required_ready=len(times),all_required_ready=all_ready,
            first_charts_at=datetime.fromtimestamp(min(p['ready_epoch_ms'] for p in publications)/1000,timezone.utc).isoformat() if publications else None,
            all_charts_at=datetime.fromtimestamp(max(times),timezone.utc).isoformat() if all_ready else None))
    histograms=defaultdict(lambda:defaultdict(int))
    for row in trace['observations']:
        data=row['data']
        if row['kind']=='http' and 'response_count' in data:
            histograms[data['http_group'],data['http_status'],data['http_metric']][data['response_bucket_le_ms']]+=data['response_count']
    http=[]
    for (group,status,metric),buckets in histograms.items():
        count=sum(buckets.values());result=dict(group=group,status=status,metric=metric,count=count)
        for q in (50,95,99):
            cumulative=0
            for upper,n in sorted(buckets.items()):
                cumulative+=n
                if cumulative>=count*q/100:result['p'+str(q)+'_upper_ms']=upper;break
        http.append(result)
    days=defaultdict(lambda:dict(simulations_completed=0,configurations_with_charts=0))
    completed=set()
    for row in attempts:
        if (row.get('kind')!='input_preparation' and row['outcome']=='success' and row.get('job_state')=='succeeded'
                and row['finished_at'] and row['job_id'] not in completed):
            completed.add(row['job_id']);days[row['finished_at'][:10]]['simulations_completed']+=1
            if row['job_id'] in ready:
                day=datetime.fromtimestamp(min(ready[row['job_id']]),timezone.utc).date().isoformat()
                days[day]['configurations_with_charts']+=1
    return dict(segments=segments,attempts=attempts,experiments=experiments,pipeline=pipeline,http=http,
        throughput=[dict(utc_day=day,**values) for day,values in sorted(days.items())],
        limitations=['Sample boundaries do not locate every phase change precisely.',
            'Matched workload/platform/version fingerprints and repeated controlled trials are needed for allocation comparisons.',
            'Finished jobs without samples remain in the attempt table. Failed jobs are not discarded.',
            'Chart times record successful publication in the past; they do not certify current retention or access permission.',
            'First/last observed chart times are not a guarantee that every required comparison has been retained in this export.'])


def render(report):
    def cell(value):
        if value is None:return 'Unknown'
        if isinstance(value,(dict,list)):value=json.dumps(value,sort_keys=True)
        return html.escape(str(value))
    def table(title,rows,columns):
        return '<h2>'+html.escape(title)+'</h2><table><thead><tr>'+''.join('<th>'+html.escape(label)+'</th>' for _,label in columns)+\
            '</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+cell(row.get(key))+'</td>' for key,_ in columns)+'</tr>' for row in rows)+\
            '</tbody></table>'
    trace=report['timeline'];analysis=analyse(trace)
    body=table('Attempts, including unmeasured and unsuccessful work',analysis['attempts'],[
        ('id','Attempt'),('number','Attempt number'),('kind','Work kind'),('outcome','Outcome'),
        ('submission_to_admission_seconds','Submission to this admission (seconds)'),
        ('queue_seconds','This attempt queue wait (seconds)'),
        ('input_wait_seconds','Waiting for inputs (seconds)'),('resource_queue_seconds','Queue after inputs ready (seconds)'),
        ('coverage','External coverage'),('java_coverage','Java observation coverage'),
        ('first_observed_chart_delay_seconds','First observed charts after simulation (seconds)'),
        ('last_observed_chart_delay_seconds','Last observed charts after simulation (seconds)'),
        ('last_sample_to_exit_seconds','Last external sample to exit (seconds)')])
    body+=table('Historical chart readiness: required baseline pairs or standalone scenarios',analysis['experiments'],[
        ('experiment','Experiment'),('configurations','Configurations'),('required_publications','Required publications'),
        ('observed_required_ready','Ready publications found'),('all_required_ready','All required charts ready'),
        ('first_charts_at','First charts (UTC)'),('all_charts_at','All required charts (UTC)')])
    body+=table('Observed throughput by UTC calendar day (bounded export)',analysis['throughput'],[
        ('utc_day','UTC day'),('simulations_completed','Successful configurations'),('configurations_with_charts','Configurations with usable charts')])
    body+=table('Visualiser preparation stages (wall time, including source checks and contention)',analysis['pipeline'],[
        ('subject','Task'),('experiment','Experiment'),('source_jobs','Source configurations'),
        ('comparison','Baseline comparison (1=yes)'),('queued_seconds','Task queue (s)'),
        ('summary_seconds','Summaries and source checks (s)'),('comparison_seconds','Aggregation/comparison (s)'),
        ('validation_seconds','Validation before indexing (s)'),('indexing_seconds','Chart indexing (s)'),
        ('processing_seconds','Total processing (s)'),('raw_seconds','Summed raw parser time (s)'),
        ('hits','Seed summary hits'),('misses','Seed summary misses')])
    body+=table('Website response distributions (histogram upper bounds)',analysis['http'],[
        ('group','Request group'),('status','Status group'),('metric','Timing'),('count','Requests'),
        ('p50_upper_ms','p50 upper bound (ms)'),('p95_upper_ms','p95 upper bound (ms)'),('p99_upper_ms','p99 upper bound (ms)')])
    body+=table('Resource and progress intervals',analysis['segments'],[
        ('subject','Subject'),('source','Counter source'),('at','Time'),('phase','Observed phase'),('repetition','Repetition'),
        ('year','Year'),('population','Observed population'),('interval_state','Interval quality'),
        ('seconds','Elapsed seconds'),('cpu_seconds','CPU seconds'),('attributed_vcpus','Confirmed allowance (vCPUs)'),
        ('working_gib','Sampled working memory (GiB)'),('heap_gib','Sampled Java heap (GiB)'),('gc_seconds','GC counter change (seconds)')])
    body+=table('Lifecycle summaries, peaks and processing/cache counters',trace['subjects'],[
        ('kind','Kind'),('subject','Subject'),('first_at','First event'),('last_at','Last event'),('data','Measurements and phase times')])
    body+=table('Host, service, SingleRun, Visualiser and Java observations',trace['observations'],[
        ('kind','Kind'),('event','Event'),('subject','Subject'),('observed_at','Time'),('data','Measurements')])
    return '<!doctype html>\n<!-- (C) Copyright 2026, by Ross Richardson; Private performance review report. @author ross richardson -->\n'+\
        '<html lang="en"><meta charset="utf-8"><title>Performance review</title><style>body{font:15px system-ui;margin:2rem;color:#172334}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #ccd3dc;padding:8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#e6edf6}tr:nth-child(even){background:#f5f7fa}</style>'+\
        '<h1>Performance review</h1><p>Units and observation coverage are labelled. Unknown values are not zero. '+\
        ('<strong>This bounded export is truncated.</strong> ' if trace['truncated'] else '')+\
        'These measurements describe service behaviour; they do not establish causal speed improvements.</p><pre>'+cell(report.get('platform',{}))+'</pre>'+\
        '<ul>'+''.join('<li>'+html.escape(v)+'</li>' for v in analysis['limitations'])+'</ul>'+body+'</html>'
