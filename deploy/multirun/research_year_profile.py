"""(C) Copyright 2026, by Ross Richardson

Validate passive year/resource observations and summarise annual and decade peaks.
Only numeric resource totals and population collection sizes enter these reports.
@author ross richardson
"""
import json
import os
import re
import stat
from pathlib import Path

PREFIX = 'SIMPATHS_YEAR_RESOURCE_SAMPLE '
FORMAT = 'simpaths.research-year-resources.v1'
MAX_ROWS = 100000
MAX_BYTES = 64*1024**2
KINDS = {'process_start','sample','year_start','year_finish','year_closed','run_finish'}
NUMBERS = {'sequence','elapsed_ms','repetition','year','model_year','person_count','benefit_count',
    'household_count','population_observed_ms','heap_used_bytes','heap_committed_bytes','heap_max_bytes',
    'nonheap_used_bytes','container_limit_bytes','container_used_bytes','inactive_file_bytes','working_set_bytes',
    'cgroup_peak_bytes','gc_ms','process_cpu_ns','cgroup_cpu_usage_usec','cgroup_throttled_usec',
    'cgroup_nr_periods','cgroup_nr_throttled'}
OPTIONAL = {'cgroup_peak_bytes','process_cpu_ns','cgroup_cpu_usage_usec','cgroup_throttled_usec',
    'cgroup_nr_periods','cgroup_nr_throttled'}
PEAKS = ('heap_used_bytes','heap_committed_bytes','nonheap_used_bytes','container_used_bytes',
    'working_set_bytes','inactive_file_bytes')
COUNTERS = ('gc_ms','process_cpu_ns','cgroup_cpu_usage_usec','cgroup_throttled_usec',
    'cgroup_nr_periods','cgroup_nr_throttled')


def decode(body):
    def pairs(items):
        value={}
        for k,v in items:
            if k in value:raise ValueError('Duplicate year-profile field')
            value[k]=v
        return value
    def invalid(_):raise ValueError('Nonfinite year-profile value')
    return json.loads(body,object_pairs_hook=pairs,parse_constant=invalid)


def append(existing, additions):
    """Validate only new samples; inclusive Docker-log cursor dedup happens upstream."""
    if len(existing)+len(additions)>MAX_ROWS:raise ValueError('Year profile exceeds its sample budget')
    previous=existing[-1] if existing else None
    cumulative=(*COUNTERS,'cgroup_peak_bytes')
    last_known={key:next((r[key] for r in reversed(existing) if r[key]>=0),-1) for key in cumulative}
    for row in additions:
        if (type(row) is not dict or set(row)!=NUMBERS|{'kind'}
                or type(row['kind']) is not str or row['kind'] not in KINDS
                or any(type(row[k]) is not int or not -1<=row[k]<2**63 for k in NUMBERS)
                or not 0<=row['heap_used_bytes']<=row['heap_committed_bytes']<=row['heap_max_bytes']
                or min(row[k] for k in ('elapsed_ms','population_observed_ms','gc_ms','nonheap_used_bytes',
                    'container_used_bytes','inactive_file_bytes','working_set_bytes'))<0
                or row['heap_max_bytes']<=0 or row['container_limit_bytes']<=0
                or row['working_set_bytes']!=max(0,row['container_used_bytes']-row['inactive_file_bytes'])
                or row['population_observed_ms']>row['elapsed_ms']
                or row['repetition'] not in range(11)
                or row['year'] not in (0,*range(2019,2071))
                or row['model_year'] not in (0,*range(2019,2072))):
            raise ValueError('Invalid year/resource sample')
        if row['year']==0:
            if (row['repetition']!=0 or row['model_year']!=0
                    or any(row[k]!=-1 for k in ('person_count','benefit_count','household_count'))
                    or row['kind'] not in ('process_start','sample')):
                raise ValueError('Unassigned setup must not invent a year or population')
        elif (row['repetition']<1 or min(row[k] for k in ('person_count','benefit_count','household_count'))<0
                or row['kind']=='process_start'
                or row['model_year']!=row['year']+(row['kind'] in ('year_closed','run_finish'))):
            raise ValueError('Year/population observation has inconsistent model state')
        if previous is None:
            if row['sequence']!=0 or row['kind']!='process_start':raise ValueError('Initial year observation was lost')
        elif (row['sequence']!=previous['sequence']+1 or row['elapsed_ms']<previous['elapsed_ms']
                or row['heap_max_bytes']!=previous['heap_max_bytes']
                or row['container_limit_bytes']!=previous['container_limit_bytes']
                or any(row[k]>=0 and row[k]<last_known[k] for k in cumulative)):
            raise ValueError('Year telemetry lost samples, changed limits or moved backwards')
        for key in cumulative:
            if row[key]>=0:last_known[key]=row[key]
        previous=row
    return existing+additions


def write_samples(path, rows):
    append([],rows)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    size=0
    try:
        with os.fdopen(fd,'wb') as target:
            for row in rows:
                encoded=json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'
                size+=len(encoded)
                if size>MAX_BYTES:raise ValueError('Year sample evidence exceeds 64 MiB')
                target.write(encoded)
            target.flush();os.fsync(target.fileno())
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise


def read_samples(path):
    from deploy.multirun.research_benchmarks import ordinary, parent_fd, state_tuple
    path=ordinary(path)
    rows=[]
    with parent_fd(path) as (parent,name):
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        with os.fdopen(fd,'rb') as source:
            before=os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_BYTES:
                raise ValueError('Expected ordinary year sample evidence at most 64 MiB')
            while line:=source.readline(4097):
                if len(line)>4096 or len(rows)>=MAX_ROWS:raise ValueError('Year sample evidence exceeds its bounds')
                rows.append(decode(line))
            after=os.fstat(source.fileno());current=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if state_tuple(before)!=state_tuple(after) or state_tuple(before)!=state_tuple(current):
                raise ValueError('Year sample evidence changed while being read')
    return append([],rows)


def peaks(rows):
    return {key:max(row[key] for row in rows) for key in PEAKS}


def deltas(first,last):
    return {key:last[key]-first[key] if min(first[key],last[key])>=0 else None for key in COUNTERS}


def summarise(rows,*,population,seeds,end_year=2070,milestones=(2030,2040,2050,2060,2070)):
    rows=append([],rows)
    if (not rows or type(population) is not int or not 1000<=population<=100000
            or type(end_year) is not int or not 2019<=end_year<=2070
            or not 1<=len(seeds)<=2 or any(type(s) is not str or not re.fullmatch('[0-9]{1,20}',s) for s in seeds)
            or len(set(seeds))!=len(seeds)
            or not milestones or list(milestones)!=sorted(set(milestones)) or milestones[-1]!=end_year
            or any(type(y) is not int or not 2019<=y<=end_year for y in milestones)):
        raise ValueError('Select bounded populations, one/two seeds and ordered year milestones')
    active=None;finished=False;closed=False;completed=0
    starts={};closes={};ends={};run_ends={}
    for row in rows:
        key=(row['repetition'],row['year']);kind=row['kind']
        if kind=='process_start':
            if row['sequence']!=0:raise ValueError('Duplicate process start')
        elif kind=='sample':
            expected=active if active is not None else (0,0)
            if key!=expected or active is not None and closed:
                raise ValueError('Timer observation is assigned to the wrong annual interval')
        elif kind=='year_start':
            if active is None:
                if key!=(completed+1,2019):raise ValueError('Missing or unexpected repetition start')
            elif not closed or key!=(active[0],active[1]+1):
                raise ValueError('Missing, duplicate or noncontiguous annual boundary')
            if row['year']>end_year:raise ValueError('Year observation exceeds the requested horizon')
            active=key;finished=False;closed=False;starts[key]=row
        elif kind=='year_finish':
            if active!=key or finished:raise ValueError('Annual finish lacks its unique start')
            finished=True;ends[key]=row
        elif kind=='year_closed':
            if active!=key or not finished or closed:raise ValueError('Annual close lacks finished model work')
            closed=True;closes[key]=row
        else:
            if active!=key or not closed or row['year']!=end_year:raise ValueError('Incomplete repetition finish')
            completed+=1;run_ends[completed]=row;active=None
    if active is not None or completed!=len(seeds):raise ValueError('Missing complete year/repetition evidence')
    result=dict(format=FORMAT,population_requested=population,seeds=list(seeds),end_year=end_year,
        sample_interval_ms=2000,samples=len(rows),repetitions=[],
        population_measurement='Model collection sizes observed on the model thread at annual boundaries. '
            'Not an exact intra-year live-person maximum; timers retain the last boundary observation.',
        limitations=['Sampled heap/working-memory peaks can miss short spikes; no forced garbage collection.',
            'Kernel memory.peak is cumulative for the whole process and includes file cache.',
            'A long run at year Y is not a verified substitute for a separate run terminating at Y.',
            'Observed demand under this heap does not certify a smaller heap or ten repetitions.'])
    prior=rows[0]
    for rep,seed in enumerate(seeds,1):
        annual=[]
        for year in range(2019,end_year+1):
            key=(rep,year)
            if key not in closes or key not in ends:raise ValueError('Missing annual output boundary')
            samples=[r for r in rows[starts[key]['sequence']:closes[key]['sequence']+1]]
            observed=[r for r in samples if r['kind']!='sample']
            annual.append(dict(year=year,started_ms=starts[key]['elapsed_ms'],closed_ms=closes[key]['elapsed_ms'],
                elapsed_ms=closes[key]['elapsed_ms']-starts[key]['elapsed_ms'],samples=len(samples),
                sampled_peaks=peaks(samples),counters=deltas(starts[key],closes[key]),
                max_observed_person_count=max(r['person_count'] for r in observed),
                person_count_at_start=starts[key]['person_count'],person_count_at_finish=ends[key]['person_count'],
                person_count_at_close=closes[key]['person_count']))
        intervals=[];first_year=2019;first=prior
        for year in milestones:
            last=closes[(rep,year)]
            samples=rows[first['sequence']:last['sequence']+1]
            cumulative=rows[:last['sequence']+1]
            observed=[r for r in samples if r['repetition']==rep and r['year'] and r['kind']!='sample']
            intervals.append(dict(start_year=first_year,end_year=year,samples=len(samples),
                elapsed_ms=last['elapsed_ms']-first['elapsed_ms'],sampled_peaks=peaks(samples),
                process_cumulative_peaks=peaks(cumulative),counters=deltas(first,last),
                kernel_process_peak_bytes=max((r['cgroup_peak_bytes'] for r in cumulative if r['cgroup_peak_bytes']>=0),default=None),
                max_observed_person_count=max(r['person_count'] for r in observed),
                cumulative_elapsed_ms=last['elapsed_ms'],cumulative_gc_ms=last['gc_ms']))
            first_year=year+1;first=last
        timers=[r['elapsed_ms'] for r in rows if r['kind']=='sample' and r['repetition']==rep]
        result['repetitions'].append(dict(repetition=rep,seed=seed,annual=annual,intervals=intervals,
            timer_samples=len(timers),maximum_timer_gap_ms=max((b-a for a,b in zip(timers,timers[1:])),default=None)))
        prior=run_ends[rep]
    return result
