#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Selected or paired scientific trials of optional Gini calculations and output.
Reuse the fixed-resource research proof, audit and verified compact archival.
@author ross richardson
"""
import argparse
from copy import deepcopy
import csv
from datetime import datetime
from html import escape
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy._workflow import frontend_path
from deploy.multirun import research_benchmarks as bench
from deploy.multirun import research_calibration as calibration
from deploy.multirun import research_population_profile as population
from deploy.multirun import research_sequence as sequence
from deploy.multirun import research_year_profile as years
from deploy.multirun import research_memory_reporting as memory_reporting
from deploy.multirun.artifacts import ArtifactError, fingerprint, write_attribution
from deploy.multirun.prepared_dataset import verify_snapshot

FORMAT='simpaths.optional-output-trials.v1'
GIB=1024**3
GINI_FIELDS=('statYMktNatGini','statYHhDispEquivNatGini')


def make_plan(args):
    if type(args.rounds) is not int or not 1<=args.rounds<=3:
        raise ArtifactError('Select one to three paired rounds')
    if args.population not in (20000,50000,100000):
        raise ArtifactError('Select a previously measured population: 20000, 50000 or 100000')
    repetitions=getattr(args,'repetitions',1)
    if type(repetitions) is not int or repetitions not in (1,2):
        raise ArtifactError('Select one or two sequential repetitions in the same JVM')
    selected_modes=getattr(args,'modes',None)
    if selected_modes is not None and (type(selected_modes) not in (list,tuple)
            or not 1<=len(selected_modes)<=2 or len(set(selected_modes))!=len(selected_modes)
            or any(mode not in ('off','on') for mode in selected_modes)):
        raise ArtifactError('Select distinct Gini modes: on, off, or both in the requested order')
    heap,memory=population.MEMORY_CANDIDATES[(args.population,2035 if args.end_year<=2035 else 2070)]
    base=population.make_plan(SimpleNamespace(frontend=args.frontend,output=args.output,
        runtime_from=args.runtime_from,image=None,populations=[args.population],end_year=args.end_year,
        repetitions=repetitions,cpu_millis=2000,heap_mib=heap,memory_mib=memory,pause_seconds=args.pause_seconds))
    template=base['cases'][0]
    template['settings']=calibration.checked_settings({**template['settings'],
        'repetition_seconds':getattr(args,'repetition_seconds',1800)})
    cases=[]
    for round_number in range(1,args.rounds+1):
        # Alternate first condition; report paired differences as well as medians.
        modes=selected_modes or (('off','on') if round_number%2 else ('on','off'))
        for mode in modes:
            name=f'round-{round_number}-gini-{mode}'
            case=deepcopy(template)
            case.update(name=name,round=round_number,mode=mode,
                variant='baseline-gini' if mode=='on' else 'baseline',retire_prepared=False)
            for directory,key in (('trials','completed'),('audits','audit'),('archives','archive')):
                case[key]=str(Path(base['output'])/directory/name)
            command=[sys.executable,str(ROOT/'deploy/multirun/research_calibration.py'),
                '--frontend',base['frontend'],'--prepared',case['preparation']+'/package',
                '--image',base['image'],'--variant',case['variant'],'--year-profile','--proof-only',
                '--output',case['completed']]
            for key,value in case['settings'].items():command+=['--'+key.replace('_','-'),str(value)]
            case['command']=command;cases.append(case)
    cases[-1]['retire_prepared']=True
    base.update(format=FORMAT,cases=cases,rounds=args.rounds,modes=list(selected_modes) if selected_modes else None,
        changed_setting='collector_args.calculateGiniCoefficients',
        note='Fresh public inputs shared by all conditions; each condition runs its original seeds sequentially in one JVM. '
             'Output audits and per-repetition reports run only after all repetitions finish; no between-run hashing, CSV parsing or copying. '
             'Gini calculations compare pairs and may scale quadratically. Database export stays disabled.')
    return base


def gini_output(completed,*,enabled,end_year,seeds=('606',)):
    """Check each statistics CSV after the entire MultiRun JVM has finished."""
    root=Path(completed)/'model-proof/retained-output'
    files=list(root.glob('*/csv/WealthIncomeStatistics.csv'))
    if not 1<=len(seeds)<=2 or len(set(seeds))!=len(seeds) or len(files)!=len(seeds):
        raise ArtifactError('Expected one verified statistics file per original seed')
    selected={}
    for path in files:
        parts=path.parent.parent.name.rsplit('_',2)
        if (len(parts)!=3 or parts[1] not in seeds or parts[1] in selected
                or parts[2]!=str(list(seeds).index(parts[1]))):
            raise ArtifactError('Statistics folder does not identify the original seed and ordinal')
        selected[parts[1]]=path
    repetitions=[]
    for number,seed in enumerate(seeds,1):
        path=selected[seed];values=[]
        with bench.ordinary(path).open(newline='',encoding='utf-8-sig') as stream:
            reader=csv.DictReader(stream)
            if not set(('time',*GINI_FIELDS))<=set(reader.fieldnames or []):
                raise ArtifactError('The model statistics do not expose the expected Gini columns')
            for row in reader:
                if len(values)>=52:raise ArtifactError('Unexpected statistics row count')
                year=float(row['time'])
                numbers={name:float(row[name]) for name in GINI_FIELDS}
                if not math.isfinite(year) or not year.is_integer() or any(not math.isfinite(v) for v in numbers.values()):
                    raise ArtifactError('Expected finite annual Gini statistics')
                values.append(dict(year=int(year),**numbers))
        if [v['year'] for v in values]!=list(range(2019,end_year+1)):
            raise ArtifactError('Gini statistics do not cover every requested year')
        if any(v[name]!=0 for v in values for name in GINI_FIELDS)!=enabled:
            raise ArtifactError('The actual Gini statistics do not match the selected on/off condition')
        repetitions.append(dict(repetition=number,seed=seed,annual_values=values,file=fingerprint(path)))
    result=dict(enabled=enabled,columns=list(GINI_FIELDS),repetitions=repetitions)
    # Preserve the original single-repetition fields for existing report readers.
    if len(repetitions)==1:result.update(annual_values=repetitions[0]['annual_values'],file=repetitions[0]['file'])
    return result


def condition(case,profile,record,config,gini):
    if record.get('variant')!=case['variant']:
        raise ArtifactError('Completed optional-output condition differs from its plan')
    actual=config.as_dict() if hasattr(config,'as_dict') else config
    if actual['run_sets'][0]['collector_args']['calculateGiniCoefficients']!=(case['mode']=='on'):
        raise ArtifactError('The frozen collector flag differs from the measured condition')
    annual=[year for rep in profile['repetitions'] for year in rep['annual']];whole=profile['whole_process']
    # Derive every extra report from the existing passive stream after completion.
    repeat_check=memory_reporting.repeated_check(years.read_samples(Path(case['archive'])/'year-samples.jsonl'),
        profile,record['attempts'][0])
    metrics=[]
    for rep in repeat_check['repetitions']:
        measured=rep['including_setup'];peak=measured['sampled_peaks']
        metrics.append(dict(repetition=rep['repetition'],seed=rep['seed'],
            including_setup_seconds=measured['elapsed_ms']/1000,
            annual_work_seconds=sum(y['elapsed_ms'] for y in profile['repetitions'][rep['repetition']-1]['annual'])/1000,
            peak_heap_gib=peak['heap_used_bytes']/GIB,peak_working_gib=peak['working_set_bytes']/GIB,
            peak_charged_gib=peak['container_used_bytes']/GIB,gc_seconds=measured['counters']['gc_ms']/1000,
            mean_vcpus=measured['utilisation']['average_vcpus']))
    return dict(name=case['name'],round=case['round'],mode=case['mode'],configuration=actual,
        identity={key:record[key] for key in ('settings','image','model','prepared_sha256','source_sha256','year_profile_sources','probe_classes')},
        java_seconds=whole['elapsed_ms']/1000,
        annual_work_seconds=sum(a['elapsed_ms'] for a in annual)/1000,
        peak_heap_gib=whole['sampled_peaks']['heap_used_bytes']/GIB,
        peak_working_gib=whole['sampled_peaks']['working_set_bytes']/GIB,
        peak_charged_gib=whole['sampled_peaks']['container_used_bytes']/GIB,
        gc_seconds=whole['counters']['gc_ms']/1000,
        mean_vcpus=whole['utilisation']['average_vcpus'],
        workspace_gib=record['peak_workspace_bytes']/GIB,output_mib=record['output_bytes']/1024**2,
        person_rows_by_year=[dict(repetition=rep['repetition'],seed=rep['seed'],year=a['year'],rows=a['exported_person_rows'])
            for rep in profile['repetitions'] for a in rep['annual']],
        repetitions=metrics,repeat_check=repeat_check,gini=gini,archive=case['archive'])


def paired_results(rows):
    pairs=[]
    for number in sorted({r['round'] for r in rows}):
        selected=[r for r in rows if r['round']==number]
        if len(selected)<2:continue
        if len(selected)!=2 or {r['mode'] for r in selected}!={'off','on'}:
            raise ArtifactError('A round must contain exactly one on and one off result')
        off=next(r for r in selected if r['mode']=='off');on=next(r for r in selected if r['mode']=='on')
        controls=deepcopy(on['configuration'])
        controls['run_sets'][0]['collector_args']['calculateGiniCoefficients']=False
        if controls!=off['configuration'] or on['identity']!=off['identity']:
            raise ArtifactError('Paired inputs, model, seeds, measurements or controls differ beyond the Gini flag')
        pairs.append(dict(round=number,
            java_change_percent=100*(on['java_seconds']/off['java_seconds']-1),
            annual_work_change_percent=100*(on['annual_work_seconds']/off['annual_work_seconds']-1),
            peak_heap_change_gib=on['peak_heap_gib']-off['peak_heap_gib'],
            peak_working_change_gib=on['peak_working_gib']-off['peak_working_gib'],
            matching_person_row_counts=on['person_rows_by_year']==off['person_rows_by_year']))
    return pairs


def write_reports(output,record):
    rows=record['conditions'];record['paired_results']=paired_results(rows)
    summary=[]
    for mode in ('off','on'):
        selected=[r for r in rows if r['mode']==mode]
        if selected:
            summary.append(dict(mode=mode,observations=len(selected),**{
                key:dict(median=statistics.median(r[key] for r in selected),
                    minimum=min(r[key] for r in selected),maximum=max(r[key] for r in selected))
                for key in ('java_seconds','annual_work_seconds','peak_heap_gib','peak_working_gib','gc_seconds','output_mib')}))
    record['summary']=summary
    bench.write_json(output/'report.json',record,replace=True)
    columns=['round','mode','java_seconds','annual_work_seconds','peak_heap_gib','peak_working_gib',
        'peak_charged_gib','gc_seconds','mean_vcpus','workspace_gib','output_mib']
    with (output/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,columns,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    repetition_rows=[dict(round=r['round'],mode=r['mode'],**rep) for r in rows for rep in r['repetitions']]
    repetition_columns=['round','mode','repetition','seed','including_setup_seconds','annual_work_seconds',
        'peak_heap_gib','peak_working_gib','peak_charged_gib','gc_seconds','mean_vcpus']
    with (output/'repetitions.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,repetition_columns);writer.writeheader();writer.writerows(repetition_rows)
    def table(headings,values):
        return '<table><thead><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headings)+\
            '</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in row)+'</tr>' for row in values)+'</tbody></table>'
    details=table(['Round','Gini','Whole JVM total (s)','All repetitions: annual work (s)','Peak heap (GiB)',
        'Peak working RAM (GiB)','GC counter (s)','Output (MiB)'],
        [[r['round'],r['mode'],*[f'{r[k]:.3f}' for k in ('java_seconds','annual_work_seconds',
            'peak_heap_gib','peak_working_gib','gc_seconds','output_mib')]] for r in rows])
    pairs=table(['Round','Java time change (%)','Annual work change (%)','Heap change (GiB)',
        'Working RAM change (GiB)','Matching annual person counts'],
        [[p['round'],*[f'{p[k]:+.3f}' for k in ('java_change_percent','annual_work_change_percent',
            'peak_heap_change_gib','peak_working_change_gib')],p['matching_person_row_counts']] for p in record['paired_results']])
    medians=table(['Gini','Runs','Java median (s)','Java range (s)','Annual work median (s)',
        'Heap median (GiB)','Working RAM median (GiB)'],[[s['mode'],s['observations'],
            f"{s['java_seconds']['median']:.3f}",f"{s['java_seconds']['minimum']:.3f}–{s['java_seconds']['maximum']:.3f}",
            *[f"{s[k]['median']:.3f}" for k in ('annual_work_seconds','peak_heap_gib','peak_working_gib')]] for s in summary])
    repetitions=table(['Round','Gini','Repetition','Seed','Including setup (s)','Annual work (s)',
        'Peak heap (GiB)','Peak working RAM (GiB)','Peak total charged RAM (GiB)','GC counter (s)'],
        [[r['round'],r['mode'],r['repetition'],r['seed'],*[f'{r[k]:.3f}' for k in (
            'including_setup_seconds','annual_work_seconds','peak_heap_gib','peak_working_gib','peak_charged_gib','gc_seconds')]]
         for r in repetition_rows])
    transitions=table(['Round','Gini','Repetition transition','Between-run/build seconds',
        'Lowest sampled heap (GiB)','Lowest sampled working RAM (GiB)'],
        [[r['round'],r['mode'],str(t['after_repetition'])+' → '+str(t['before_repetition']),
            f"{t['elapsed_ms']/1000:.3f}",
            f"{t['lowest_sampled_heap']['heap_used_bytes']/GIB:.3f}" if t['lowest_sampled_heap'] else 'Unavailable',
            f"{t['lowest_sampled_working']['working_set_bytes']/GIB:.3f}" if t['lowest_sampled_working'] else 'Unavailable']
         for r in rows for t in r['repeat_check']['transitions']])
    status='Completed' if record.get('passed') else 'Incomplete: consult the retained report and logs'
    text='<!doctype html>\n<!-- (C) Copyright 2026, by Ross Richardson; Optional Gini comparison. @author ross richardson -->\n'+\
        '<html lang="en"><meta charset="utf-8"><title>Optional Gini calculation costs</title>'+\
        '<style>body{font:16px system-ui;margin:2rem;max-width:1200px}table{border-collapse:collapse;margin:1rem 0}th,td{border:1px solid #bbc;padding:.5rem}th{background:#e8eef6}</style>'+\
        '<h1>Optional Gini calculation costs</h1><p>'+status+'</p><p>'+escape(record['workload'])+'</p>'+\
        '<h2>Summary</h2>'+medians+'<h2>Whole-JVM conditions</h2>'+details+\
        '<h2>Individual repetitions in the same JVM</h2>'+repetitions+\
        '<p>Each repetition includes its build and simulation/output through its run-finish boundary; process shutdown appears only in the whole-JVM total. '+\
        'These tables are derived after completion from existing passive samples. No file checks, copying or forced GC are added between repetitions. '+\
        'Different seeds can have different demand; two repetitions do not prove absence of a memory leak.</p>'+\
        '<h2>Between-repetition samples</h2>'+transitions+\
        '<p>These samples include disposal and the next build. They are not an isolated post-GC live-set measurement.</p>'+\
        '<h2>Matched pairs: on minus off</h2>'+pairs+\
        ('<p>No matched on/off pair was run; a single selected mode is a timing/memory qualification, not a measured Gini overhead comparison.</p>' if not record['paired_results'] else '')+\
        '<h2>Interpretation</h2><ul>'+''.join('<li>'+escape(v)+'</li>' for v in record['limitations'])+'</ul></html>\n'
    (output/'comparison.html').write_text(text,encoding='utf-8')
    for name in ('comparison.csv','comparison.html','repetitions.csv'):(output/name).chmod(0o600)


def execute(plan,*,runner=sequence.run_command,prepare=calibration.prepare_calibration,pause=time.sleep):
    output=Path(plan['output']);frontend=Path(plan['frontend'])
    with sequence.exclusive(output.parent):
        population.preflight(plan)
        population.unchanged(plan)
        image=calibration.installed_image(plan['image'])
        if image!=plan['image']:raise ArtifactError('The prepared runtime identity changed')
        output.mkdir(mode=0o700);write_attribution(output)
        for name in ('prepared','trials','audits','archives','logs'):(output/name).mkdir(mode=0o700)
        bench.write_json(output/'plan.json',plan)
        first=plan['cases'][0];settings=first['settings']
        seeds=[str(606+n) for n in range(settings['repetitions'])]
        record=dict(format=FORMAT,passed=False,production_changed=False,conditions=[],steps=[],
            started_at=bench.instant(),workload=f"{settings['population']:,} requested people; 2019–{settings['end_year']}; "
                f"{settings['repetitions']} sequential repetition(s) in one JVM, seeds {', '.join(seeds)}; "
                f"{settings['heap_mib']/1024:g} GiB heap / {settings['memory_mib']/1024:g} GiB container; 2 vCPUs. "
                f"Model runtime allowance: {settings['setup_seconds']/60:g} setup minutes + "
                f"{settings['repetition_seconds']/3600:g} hours per repetition.",
            limitations=[
                'Only calculateGiniCoefficients changes. Standard CSVs remain enabled; database output remains disabled.',
                'Java total includes initialisation and shutdown. Annual work includes the simulated years and their output, excluding initialisation.',
                'Observed memory peaks are sampled, include native working memory, and are not minimum safe allocations. Charged memory also includes file cache.',
                'The GC counter is cumulative collection time, not application pause time. Every condition uses the same passive observer.',
                'Selected or paired rounds are screening evidence, not a statistical significance test. Results qualify only the completed workload; ten repetitions and VPS concurrency remain separate checks.',
                'All repetitions run sequentially in the original Java MultiRun JVM. Per-repetition output checks and reports run only after the entire condition finishes; no between-run file hashing, copying or forced GC is added.',
                'Interrupting a condition does not guarantee preservation of earlier repetitions: the existing native cleanup can remove their workspace. Let both repetitions finish.',
                'Gini uses pairwise loops in the current source; population growth can increase its cost faster than linearly.',
                'Scientific CSV checksums need not match: Gini statistics change intentionally and the known parallel benefit-receipt random-draw issue can affect other values.',
                'Fresh prepared inputs and the current frozen multirun.jar are used. Earlier campaign timings provide context, not a contemporaneous control.'])
        env=dict(os.environ,TMPDIR=str(output/'trials'),JASMINE_WEB_REPO=str(frontend),
            PYTHONPATH=os.pathsep.join((str(ROOT),str(frontend))),PYTHONUNBUFFERED='1',PIP_NO_INPUT='1')
        env.setdefault('PIP_DEFAULT_TIMEOUT','60')
        def step(case,name,operation):
            population.unchanged(plan)
            record['steps'].append(dict(case=case['name'],stage=name,passed=False,started_at=bench.instant()))
            write_reports(output,record)
            print(f"\n{case['name']}: {name}",flush=True)
            value=operation()
            population.unchanged(plan)
            record['steps'][-1].update(passed=True,finished_at=bench.instant())
            write_reports(output,record)
            return value
        try:
            prepared=step(first,'prepare',lambda:prepare(Path(first['preparation']),settings['population'],image))
            receipt=calibration.read_calibration(prepared)
            if receipt['identity']['model']!=plan['model']:raise ArtifactError('Prepared model differs from the frozen comparison')
            if plan['pause_seconds']:pause(plan['pause_seconds'])
            for case in plan['cases']:
                population.preflight(plan,preparing=False)
                verify_snapshot(prepared,receipt)
                step(case,'run',lambda:runner(case['command'],output/'logs'/(case['name']+'-run.log'),env))
                step(case,'audit',lambda:population.audit(prepared,Path(case['completed']),Path(case['audit']),variant=case['variant']))
                actual_gini=step(case,'check-gini-output',lambda:gini_output(case['completed'],enabled=case['mode']=='on',
                    end_year=settings['end_year'],seeds=seeds))
                profile,_,config=step(case,'archive',lambda:population.archive_profile(plan,case))
                _,(native,_,_,_)=bench.completion(Path(case['completed']))
                row=condition(case,profile,native,config,actual_gini)
                # A mismatch stops before any outputs from that condition are removed.
                paired_results([*record['conditions'],row])
                record['conditions'].append(row);write_reports(output,record)
                def dispose():
                    population.verify_profile_archive(Path(case['archive']))
                    return bench.dispose(Path(case['archive']),Path(case['completed']))
                step(case,'dispose-output',dispose)
                if bench.output_files(Path(case['completed'])/'model-proof/retained-output'):
                    raise ArtifactError('Bulk result disposal remains unconfirmed')
            step(plan['cases'][-1],'retire-generated-inputs',lambda:population.retire_prepared(plan,plan['cases'][-1]))
            record.update(passed=True,finished_at=bench.instant());write_reports(output,record)
        except BaseException as error:
            record['failure']=dict(error_type=type(error).__name__,message=str(error)[:1000])
            write_reports(output,record)
            raise
    print('PASSED: '+str(output/'report.json'),flush=True)
    print('Readable tables: '+str(output/'comparison.html'),flush=True)
    return record


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--runtime-from',type=Path,required=True,help='Existing calibration package: inherit its installed runtime only')
    parser.add_argument('--population',type=int,choices=(20000,50000,100000),default=20000)
    parser.add_argument('--end-year',type=int,choices=range(2020,2071),default=2021)
    parser.add_argument('--rounds',type=int,choices=range(1,4),default=3)
    parser.add_argument('--modes',nargs='+',choices=('off','on'),
        help='Run selected modes in this order; default alternates paired off/on rounds')
    parser.add_argument('--repetitions',type=int,choices=(1,2),default=1,
        help='Sequential seeds in one MultiRun JVM per condition; default one')
    parser.add_argument('--repetition-seconds',type=int,default=1800,
        help='Frozen runtime allowance per repetition, plus 15-minute setup; default 1800')
    parser.add_argument('--pause-seconds',type=int,default=30)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    args.output=args.output or Path('/tmp-codex')/('optional-gini-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    plan=make_plan(args)
    if args.dry_run:print(json.dumps(plan,indent=2));return 0
    execute(plan);return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('STOPPED: '+type(error).__name__+': '+str(error),file=sys.stderr,flush=True)
        raise SystemExit(1)
