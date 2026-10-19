"""(C) Copyright 2026, by Ross Richardson

Labelled smaller-memory qualification and same-JVM repetition diagnostics.
Derive reports from existing passive samples without adding GC or model work.
@author ross richardson
"""
import csv
from html import escape
import io
import os
from pathlib import Path

from . import research_year_profile as years
from . import research_profile_reporting as reporting
from .artifacts import ArtifactError

GIB=1024**3
FORMAT='simpaths.repeated-memory-check.v1'
POINT_FIELDS=('sequence','elapsed_ms','heap_used_bytes','heap_committed_bytes',
    'working_set_bytes','container_used_bytes','gc_ms')


def repeated_check(rows,profile,attempt):
    """Verify one observed JVM and report setup, runs and unassigned transitions."""
    if (attempt.get('single_jvm_verified') is not True or type(attempt.get('observed_jvm_pid')) is not int
            or attempt['observed_jvm_pid']<=0 or attempt.get('outcome')!='success'):
        raise ArtifactError('Repeated-memory qualification needs a verified single JVM')
    # Revalidate the complete lifecycle; never turn partial failure samples into
    # a completed repetition or silently accept a second process's sequence.
    clean=years.summarise(rows,population=profile['population_requested'],seeds=profile['seeds'],
        end_year=profile['end_year'],milestones=(profile['end_year'],),cpu_millis=profile['cpu_allowance_millis'])
    if (clean['heap_limit_bytes']!=profile['heap_limit_bytes']
            or clean['container_limit_bytes']!=profile['container_limit_bytes']):
        raise ArtifactError('Repeated-memory report limits differ from the original profile')
    result=dict(format=FORMAT,single_jvm_verified=True,observed_jvm_pid=attempt['observed_jvm_pid'],
        forced_gc=False,repetitions=[],transitions=[])
    previous=rows[0]
    def span(first,last):return rows[first['sequence']:last['sequence']+1]
    def point(row):return {key:row[key] for key in POINT_FIELDS}
    for rep in profile['repetitions']:
        ordinal=rep['repetition']
        start=next(r for r in rows if r['kind']=='year_start' and r['repetition']==ordinal)
        end=next(r for r in rows if r['kind']=='run_finish' and r['repetition']==ordinal)
        setup=years.phase_summary([span(previous,start)],profile['cpu_allowance_millis'])
        running=years.phase_summary([span(start,end)],profile['cpu_allowance_millis'])
        whole=years.phase_summary([span(previous,end)],profile['cpu_allowance_millis'])
        result['repetitions'].append(dict(repetition=ordinal,seed=rep['seed'],setup=setup,
            simulation_and_output=running,including_setup=whole,
            first_year_start=point(start),run_finish_before_disposal=point(end)))
        if ordinal>1:
            timers=[r for r in span(previous,start) if r['kind']=='sample' and r['year']==0]
            result['transitions'].append(dict(after_repetition=ordinal-1,before_repetition=ordinal,
                elapsed_ms=start['elapsed_ms']-previous['elapsed_ms'],unassigned_timer_samples=len(timers),
                lowest_sampled_heap=point(min(timers,key=lambda r:r['heap_used_bytes'])) if timers else None,
                lowest_sampled_working=point(min(timers,key=lambda r:r['working_set_bytes'])) if timers else None))
        previous=end
    result['shutdown_sample']=point(rows[-1])
    result['interpretation']='Between-run samples include disposal and the next build, not a forced-GC live-set measurement. '
    result['interpretation']+='Different seeds can have different demand; two repetitions can reveal retained demand but cannot prove absence of a leak.'
    return result


def write_report(output,profiles,rejected=()):
    """Keep candidate outcomes and first/second repetition values plainly labelled."""
    output=Path(output);number=reporting.number
    fields=['population','end_year','heap_limit_gib','container_limit_gib','cpu_allowance',
        'repetition','seed','setup_minutes','simulation_and_output_minutes','including_setup_minutes',
        'peak_heap_gib','peak_working_gib','peak_total_container_gib','gc_seconds','mean_vcpus',
        'first_year_heap_gib','end_before_disposal_heap_gib']
    data=[];summary_rows=[];transitions=[]
    for p in profiles:
        repeat=p['repeat_check']
        for rep in repeat['repetitions']:
            whole=rep['including_setup'];peaks=whole['sampled_peaks']
            data.append(dict(population=p['population_requested'],end_year=p['end_year'],
                heap_limit_gib=p['heap_limit_bytes']/GIB,container_limit_gib=p['container_limit_bytes']/GIB,
                cpu_allowance=p['cpu_allowance_millis']/1000,repetition=rep['repetition'],seed=rep['seed'],
                setup_minutes=rep['setup']['elapsed_ms']/60000,
                simulation_and_output_minutes=rep['simulation_and_output']['elapsed_ms']/60000,
                including_setup_minutes=whole['elapsed_ms']/60000,peak_heap_gib=peaks['heap_used_bytes']/GIB,
                peak_working_gib=peaks['working_set_bytes']/GIB,
                peak_total_container_gib=peaks['container_used_bytes']/GIB,gc_seconds=whole['counters']['gc_ms']/1000,
                mean_vcpus=whole['utilisation']['average_vcpus'],
                first_year_heap_gib=rep['first_year_start']['heap_used_bytes']/GIB,
                end_before_disposal_heap_gib=rep['run_finish_before_disposal']['heap_used_bytes']/GIB))
        peak=p['whole_process']['sampled_peaks']
        summary_rows.append([f"{p['population_requested']:,}",p['end_year'],number(p['heap_limit_bytes']/GIB),
            number(p['container_limit_bytes']/GIB),number(p['cpu_allowance_millis']/1000),'PASS: both repetitions',
            number(p['whole_process']['elapsed_ms']/60000,1),number(peak['heap_used_bytes']/GIB,3),
            number(peak['working_set_bytes']/GIB,3),number(peak['container_used_bytes']/GIB,3)])
        for t in repeat['transitions']:
            heap=t['lowest_sampled_heap'];working=t['lowest_sampled_working']
            transitions.append([f"{p['population_requested']:,}",p['end_year'],
                f"{t['after_repetition']} → {t['before_repetition']}",number(t['elapsed_ms']/1000),
                t['unassigned_timer_samples'],number(heap['heap_used_bytes']/GIB,3) if heap else 'Unavailable',
                number(heap['heap_committed_bytes']/GIB,3) if heap else 'Unavailable',
                number(working['working_set_bytes']/GIB,3) if working else 'Unavailable'])
    for failure in rejected:
        s=failure['settings']
        summary_rows.append([f"{s['population']:,}",s['end_year'],number(s['heap_mib']/1024),
            number(s['memory_mib']/1024),number(s['cpu_millis']/1000),
            'FAILED: '+failure['outcome'],'—','—','—','—'])
    csv_text=io.StringIO();writer=csv.DictWriter(csv_text,fieldnames=fields);writer.writeheader();writer.writerows(data)
    (output/'memory-repetitions.csv').write_text(csv_text.getvalue());os.chmod(output/'memory-repetitions.csv',0o600)
    body=['<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<!-- (C) Copyright 2026, by Ross Richardson. Repeated-memory qualification report. @author ross richardson -->',
        '<title>Smaller-memory and repeated-JVM trials</title><meta name="viewport" content="width=device-width, initial-scale=1">',
        '<style>body{font:15px system-ui,sans-serif;max-width:1500px;margin:2em auto;padding:0 1em;color:#182332}',
        'table{border-collapse:collapse;margin:1em 0;width:100%;font-size:13px}th,td{border:1px solid #ccd3dc;padding:.5em;text-align:right}',
        'th{background:#e9eff7}td:first-child,th:first-child{text-align:left}.scroll{overflow-x:auto}</style></head><body>',
        '<h1>Smaller-memory and repeated-JVM trials</h1>',
        '<p>Each candidate runs seeds 606 and 607 sequentially in one verified JVM. These are different scientific repetitions, '
        'not two independent JVM launches. Memory growth and retries are disabled; production settings are unchanged.</p>',
        '<p><a href="memory-repetitions.csv">Per-repetition CSV</a> · <a href="comparison.html">Annual, phase and storage detail</a></p>',
        '<h2>Candidate outcomes and whole-JVM peaks</h2>',reporting.table(['Initial people','Ending year','Maximum heap (GiB)',
            'Container (GiB)','vCPUs','Outcome','Java lifetime (min)','Sampled heap peak (GiB)',
            'Sampled working peak (GiB)','Sampled total container peak (GiB)'],summary_rows),
        '<p>Whole-JVM peaks include finalisation. Container limits cover total charged memory, including file cache; working '
        'memory subtracts inactive file cache. Neither sampled peak is a minimum safe allocation. Failed candidates have no completed-run peak claimed here.</p>',
        '<h2>First versus second repetition</h2><div class="scroll">']
    rows=[]
    for d in data:
        rows.append([f"{d['population']:,}",d['end_year'],d['repetition'],d['seed'],
            number(d['setup_minutes'],2),number(d['simulation_and_output_minutes'],2),number(d['including_setup_minutes'],2),
            number(d['peak_heap_gib'],3),number(d['peak_working_gib'],3),number(d['gc_seconds'],1),
            number(d['mean_vcpus']),number(d['first_year_heap_gib'],3),number(d['end_before_disposal_heap_gib'],3)])
    body += [reporting.table(['Initial people','Ending year','Repetition','Seed','Setup / transition (min)',
        'Simulation + annual output (min)','Including setup (min)','Peak heap incl. setup (GiB)',
        'Peak working incl. setup (GiB)','GC counters (s)','Mean vCPUs used',
        'Heap at first year after build (GiB)','Heap at run finish before disposal (GiB)'],rows),'</div>',
        '<p>The second setup interval starts at the first run’s finish marker: it includes disposal and the next population build. '
        'Per-repetition values exclude final shutdown. GC counters are not application pause durations. '
        'A higher second-run peak with a different seed does not by itself establish a memory leak.</p>',
        '<h2>Memory between repetitions</h2>',reporting.table(['Initial people','Ending year','Transition',
            'Transition duration (s)','Unassigned timer samples','Lowest sampled heap (GiB)',
            'Committed heap at that sample (GiB)','Lowest sampled working RAM (GiB)'],transitions),
        '<p>These samples cover cleanup and the next build. They are not taken at a guaranteed post-GC idle point. '
        'Committed heap can remain available to the same JVM even after objects are reclaimed; it need not return to the OS. '
        'No extra garbage collections are requested. Two repetitions can expose accumulating demand, but cannot rule out a slower leak or certify ten repetitions.</p>']
    if rejected:
        body+=['<h2>Failed candidate evidence</h2><ul>']
        for failure in rejected:
            body.append('<li>'+escape(failure['name']+': '+failure['outcome'])+
                ' — partial samples and diagnostics are retained in '+escape(failure['archive'])+'.</li>')
        body.append('</ul><p>Only classified, settled heap/container-memory failures permit the next candidate to run. '
            'Missing evidence, cleanup uncertainty, changed sources or any other failure stops the campaign.</p>')
    body+=['<h2>Next qualification</h2><p>Keep 2 vCPUs for this memory comparison. Before adopting a chosen smaller-memory '
        'profile at 1.5 or 1 vCPU, run two sequential repetitions at that CPU quota. A full memory-matrix rerun is unnecessary. '
        'Lower CPU availability can change GC timing and memory peaks. Hosted concurrency and mixed-load responsiveness require separate acceptance.</p>',
        '</body></html>']
    (output/'memory-comparison.html').write_text('\n'.join(body)+'\n');os.chmod(output/'memory-comparison.html',0o600)
