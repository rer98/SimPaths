#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Unattended, sequential population/year resource profiling with audited disposal.
Prepare fresh public inputs per population using one frozen model and fixed limits.
Retain numeric evidence, hashes and sizes; never change a production allocation.
@author ross richardson
"""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy._workflow import frontend_path
from deploy.multirun import research_calibration as calibration
from deploy.multirun import research_benchmarks as bench
from deploy.multirun import research_sequence as sequence
from deploy.multirun import research_storage_audit as storage
from deploy.multirun import research_year_profile as years
from deploy.multirun.artifacts import ArtifactError, fingerprint, inventory, write_attribution
from deploy.multirun.prepared_dataset import verify_snapshot

FORMAT='simpaths.population-resource-sequence.v1'
MIB=1024**2
GIB=1024**3


def settings(args,population):
    return calibration.checked_settings(dict(population=population,end_year=args.end_year,
        repetitions=args.repetitions,cpu_millis=args.cpu_millis,heap_mib=args.heap_mib,
        memory_mib=args.memory_mib,max_memory_mib=args.memory_mib,storage_mib=4096+4096*args.repetitions,
        setup_seconds=900,repetition_seconds=14400,max_attempts=1))


def source_files(frontend):
    names=('deploy/multirun/container_run.sh','deploy/multirun/requirements.txt',
        'deploy/acceptance/requirements.txt','multirun.jar')
    files=[ROOT/name for name in names]
    files+=sorted((ROOT/'deploy').rglob('*.py'))
    files+=sorted((ROOT/'deploy').rglob('*.java'))
    files+=list(calibration.load_tool('web-quickstart/prepare_profile.py').selected_inputs(ROOT/'input'))
    files+=[frontend/'scripts/test_batch_queue.py',frontend/'requirements-batch-web.txt']
    files+=sorted((frontend/'jasmine_web').rglob('*.py'))
    files+=sorted((frontend/'jasmine_web').rglob('*.sql'))
    return {str(path):fingerprint(bench.ordinary(path)) for path in files}


def unchanged(plan):
    for name,expected in plan['frozen'].items():
        if fingerprint(bench.ordinary(Path(name)))!=expected:
            raise ArtifactError('Selected source/model/public training input changed: '+name)


def make_plan(args):
    populations=args.populations
    if (not 1<=len(populations)<=5 or len(set(populations))!=len(populations)
            or any(type(n) is not int or not 1000<=n<=100000 for n in populations)):
        raise ArtifactError('Select one to five distinct starting populations in 1000..100000')
    if not 0<=args.pause_seconds<=600:raise ArtifactError('Use a settling pause of 0..600 seconds')
    frontend=bench.ordinary(args.frontend.expanduser().resolve(strict=True))
    output=bench.ordinary(args.output.expanduser().absolute())
    if '..' in output.parts:raise ArtifactError('Use an output path without parent traversal')
    if output.exists() or not output.parent.is_dir():raise ArtifactError('Choose a new output inside an existing directory')
    for source in (ROOT,frontend):
        if source==output or source in output.parents or output in source.parents:
            raise ArtifactError('Keep evidence outside source directories')
    if bool(args.image)==bool(args.runtime_from):raise ArtifactError('Supply exactly one of --image or --runtime-from')
    runtime_receipt=None
    if args.runtime_from:
        reference=bench.ordinary(args.runtime_from.expanduser().absolute())
        if reference==output or reference in output.parents or output in reference.parents:
            raise ArtifactError('Keep new evidence outside the reference preparation')
        receipt=calibration.read_calibration(reference)
        image=receipt['identity']['source_image']
        runtime_receipt=fingerprint(args.runtime_from.expanduser().absolute()/'receipt.json')
    else:image=args.image
    frozen=source_files(frontend)
    cases=[]
    for population in populations:
        name=f'population-{population}'
        selected=settings(args,population)
        prepared=output/'prepared'/name
        completed=output/'trials'/name
        command=[sys.executable,str(ROOT/'deploy/multirun/research_calibration.py'),
            '--frontend',str(frontend),'--prepare',str(prepared),'--image',image,
            '--year-profile','--proof-only','--output',str(completed)]
        for key,value in selected.items():command+=['--'+key.replace('_','-'),str(value)]
        cases.append(dict(name=name,settings=selected,preparation=str(prepared),completed=str(completed),
            audit=str(output/'audits'/name),archive=str(output/'archives'/name),command=command))
    # Calibration itself prepares then immediately launches. Separate its prepare
    # stage in this driver so a settling pause follows verified input preparation.
    for case in cases:
        command=case['command'];position=command.index('--prepare')
        command[position:position+2]=['--prepared',str(Path(case['preparation'])/'package')]
    return dict(format=FORMAT,output=str(output),frontend=str(frontend),image=image,
        runtime_reference_receipt=runtime_receipt,
        runtime_note='Only the runtime image is inherited; all populations use the current frozen multirun.jar and fresh public inputs.',
        frozen=frozen,model=frozen[str(ROOT/'multirun.jar')],cases=cases,pause_seconds=args.pause_seconds,
        limits=dict(resource_growth=False,resource_retries=False,jvm_active_processor_count=2),
        production_changed=False,delete_generated_bulk_after_audit=True,
        note='Run separately from other heavy tests, with networking enabled for isolated proof dependencies.')


def preflight(plan,*,preparing=True):
    largest=max(c['settings']['memory_mib'] for c in plan['cases'])
    maximum_storage=max(c['settings']['storage_mib'] for c in plan['cases'])
    required_disk=(maximum_storage+3072+4096)*MIB if preparing else (maximum_storage+3072)*MIB
    result=dict(free_disk_bytes=shutil.disk_usage(Path(plan['output']).parent).free,
        available_memory_bytes=int(next(line for line in Path('/proc/meminfo').read_text().splitlines()
            if line.startswith('MemAvailable:')).split()[1])*1024,
        required_free_disk_bytes=required_disk,required_available_memory_bytes=(largest+2048)*MIB)
    if result['free_disk_bytes']<required_disk:
        raise ArtifactError(f'Need {required_disk/GIB:g} GiB free before preparing the next population')
    if result['available_memory_bytes']<result['required_available_memory_bytes']:
        raise ArtifactError('Insufficient available RAM for the fixed container and test services')
    return result


def audit(prepared,completed,output):
    prepared,completed,output=map(bench.ordinary,(prepared,completed,output))
    if output.exists() or any(source==output or source in output.parents for source in (prepared,completed,ROOT)):
        raise ArtifactError('Use a new audit directory outside inputs/results/sources')
    receipt=calibration.read_calibration(prepared)
    verify_snapshot(prepared,receipt)
    evidence,(record,frozen,manifest,_)=bench.completion(completed)
    config=calibration.configuration(receipt,record['settings'],'baseline')
    if (record.get('failure') or record['prepared_sha256']!=receipt['sha256'] or config.as_dict()!=frozen
            or record['model']!=receipt['identity']['model'] or record['image']!=receipt['identity']['source_image']):
        raise ArtifactError('Completion does not match this verified population preparation')
    source=dict(folder=completed,proof=completed/'model-proof',record=record,configuration=config,
        output=completed/'model-proof/retained-output',manifest=manifest,
        provenance={Path(name).name:evidence[name]['fingerprint'] for name in bench.EVIDENCE[1:]},
        outer_report=evidence['report.json']['fingerprint'])
    measured=storage.audit_source(source,progress=lambda message:print(message,flush=True))
    storage.unchanged_evidence(source)
    verify_snapshot(prepared,receipt)
    output.mkdir(mode=0o700,parents=True)
    write_attribution(output)
    result=dict(format=storage.FORMAT,passed=True,read_only=True,simulations_started=0,
        microdata_copied=False,original_manifest_hashes_verified=True,sources=[measured],
        note='Population-specific annual counts; no extrapolated storage policy is approved.')
    bench.write_json(output/'report.json',result)
    return result


def archive_profile(plan,case):
    completed=Path(case['completed']);target=Path(case['archive'])
    _,(record,config,_,_)=bench.completion(completed)
    if (record['settings']!=case['settings'] or record['model']!=plan['model'] or record['image']!=plan['image']
            or record['variant']!='baseline' or len(record['attempts'])!=1):
        raise ArtifactError('Completed population differs from the fixed plan')
    expected_sources={name:plan['frozen'][str(ROOT/name)] for name in (
        'deploy/acceptance/SimPathsYearProfileProbe.java','deploy/multirun/research_year_profile.py')}
    if record.get('year_profile_sources')!=expected_sources:raise ArtifactError('Year observation sources differ from the frozen plan')
    attempt=record['attempts'][0]
    path=completed/'model-proof/attempts'/attempt['id']
    samples=years.read_samples(path/'year-samples.jsonl')
    if (fingerprint(path/'year-samples.jsonl')!=attempt['year_profile']['samples']
            or fingerprint(path/'year-profile.json')!=attempt['year_profile']['summary']):
        raise ArtifactError('Original year evidence changed')
    milestones=[y for y in (2030,2040,2050,2060,2070) if y<=case['settings']['end_year']]
    if case['settings']['end_year'] not in milestones:milestones.append(case['settings']['end_year'])
    summary=years.summarise(samples,population=case['settings']['population'],
        seeds=config['seed_plan']['seeds'],end_year=case['settings']['end_year'],milestones=milestones)
    original_summary,_=bench.read_json(path/'year-profile.json')
    if original_summary!=summary:raise ArtifactError('Original year summary differs from its numeric samples')
    if (samples[0]['heap_max_bytes']!=case['settings']['heap_mib']*MIB
            or samples[0]['container_limit_bytes']!=case['settings']['memory_mib']*MIB):
        raise ArtifactError('Year telemetry limits differ from the fixed launch limits')
    audited,_=bench.read_json(Path(case['audit'])/'report.json')
    source=audited['sources'][0]
    for rep in summary['repetitions']:
        run=next(r for r in source['runs'] if r['seed']==rep['seed'])
        exported=source['files'][run['folder']+'/csv/Person.csv']['years']
        for year in rep['annual']:year['exported_person_rows']=exported[str(year['year'])]['rows']
        for interval in rep['intervals']:
            interval['max_exported_person_rows']=max(exported[str(y)]['rows']
                for y in range(interval['start_year'],interval['end_year']+1))
    bench.archive(completed,Path(case['audit'])/'report.json',target)
    copied={}
    for name in ('year-samples.jsonl','year-profile.json'):
        copied[name]=fingerprint(path/name,target/name)
    if copied!={'year-samples.jsonl':attempt['year_profile']['samples'],
                'year-profile.json':attempt['year_profile']['summary']}:
        raise ArtifactError('Year evidence changed while being archived')
    bench.write_json(target/'population-profile.json',summary)
    evidence=dict(passed=True,format=FORMAT,archive=fingerprint(target/'archive.json'),
        original_year_files=copied,population_profile=fingerprint(target/'population-profile.json'),
        prepared_receipt=fingerprint(Path(case['preparation'])/'package/receipt.json'))
    bench.write_json(target/'population-evidence.json',evidence)
    # Re-open the archive and copied numeric stream before any bulk disposal.
    verify_profile_archive(target)
    if years.read_samples(target/'year-samples.jsonl')!=samples:raise ArtifactError('Archived year samples changed')
    return summary,evidence,config


def verify_profile_archive(target):
    """Require both the original completion and numeric profiles before disposal."""
    target=bench.ordinary(target)
    value,_=bench.read_json(target/'population-evidence.json')
    if (value.get('passed') is not True or value.get('format')!=FORMAT
            or set(value.get('original_year_files',{}))!={'year-samples.jsonl','year-profile.json'}
            or fingerprint(target/'archive.json')!=value.get('archive')
            or fingerprint(target/'population-profile.json')!=value.get('population_profile')
            or any(fingerprint(target/name)!=expected for name,expected in value['original_year_files'].items())):
        raise ArtifactError('Archived numeric or completion evidence changed before disposal')
    bench.read_archive(target)
    return value


def retire_prepared(plan,case):
    """Delete only this sequence's generated, still-verified input files."""
    package=bench.ordinary(Path(case['preparation'])/'package')
    expected=Path(plan['output'])/'prepared'/case['name']/'package'
    if package!=expected or package.resolve()!=package:raise ArtifactError('Refuse to retire external prepared inputs')
    receipt=calibration.read_calibration(package)
    evidence=verify_profile_archive(Path(case['archive']))
    if fingerprint(package/'receipt.json')!=evidence['prepared_receipt']:
        raise ArtifactError('Prepared receipt differs from the archived profile')
    verify_snapshot(package,receipt)
    removed=[]
    for name,expected_file in sorted(receipt['identity']['prepared'].items()):
        path=package/'input'/name
        if fingerprint(path)!=expected_file:raise ArtifactError('Generated input changed before retirement')
        with bench.parent_fd(path) as (parent,leaf):os.unlink(leaf,dir_fd=parent)
        removed.append(name)
    if inventory(package/'input'):raise ArtifactError('Unexpected generated inputs remain')
    shutil.rmtree(package/'input')
    result=dict(passed=True,prepared_receipt=evidence['prepared_receipt'],removed=removed,
        bytes=sum(f['bytes'] for f in receipt['identity']['prepared'].values()),model_retained=True,
        note='Only generated bulk inputs retired; this package cannot be reused without fresh preparation.')
    bench.write_json(package/'retired-inputs.json',result)
    return result


def controls(config):
    return dict(common={k:v for k,v in config['common'].items() if k!='population'},
        seed_plan=config['seed_plan'],run_sets=[{k:v for k,v in r.items()
            if k not in ('id','name','dataset_revision','common')} for r in config['run_sets']])


def write_comparison(output,profiles):
    fields=['population_requested','repetition','seed','start_year','end_year','max_observed_person_count',
        'max_exported_person_rows','interval_heap_gib','interval_working_gib','process_cumulative_heap_gib',
        'process_cumulative_working_gib','kernel_process_peak_gib','interval_gc_seconds','interval_seconds']
    table=[]
    for profile in profiles:
        for rep in profile['repetitions']:
            for interval in rep['intervals']:
                table.append(dict(population_requested=profile['population_requested'],repetition=rep['repetition'],
                    seed=rep['seed'],start_year=interval['start_year'],end_year=interval['end_year'],
                    max_observed_person_count=interval['max_observed_person_count'],
                    max_exported_person_rows=interval['max_exported_person_rows'],
                    interval_heap_gib=interval['sampled_peaks']['heap_used_bytes']/GIB,
                    interval_working_gib=interval['sampled_peaks']['working_set_bytes']/GIB,
                    process_cumulative_heap_gib=interval['process_cumulative_peaks']['heap_used_bytes']/GIB,
                    process_cumulative_working_gib=interval['process_cumulative_peaks']['working_set_bytes']/GIB,
                    kernel_process_peak_gib=interval['kernel_process_peak_bytes']/GIB if interval['kernel_process_peak_bytes'] is not None else None,
                    interval_gc_seconds=interval['counters']['gc_ms']/1000,interval_seconds=interval['elapsed_ms']/1000))
    text=io.StringIO();writer=csv.DictWriter(text,fieldnames=fields);writer.writeheader();writer.writerows(table)
    (output/'population-resources.csv').write_text(text.getvalue());os.chmod(output/'population-resources.csv',0o600)
    lines=['# Population/year resource profiles','',
        'Same fixed allocation and scientific controls; rows describe observed demand, not approved smaller limits.','',
        '| Initial population | Seed | Through year | Observed model persons, interval max | Cumulative heap GiB | Cumulative working GiB | Interval seconds |',
        '| ---: | --- | ---: | ---: | ---: | ---: | ---: |']
    for row in table:lines.append(f"| {row['population_requested']} | {row['seed']} | {row['end_year']} | "
        f"{row['max_observed_person_count']} | {row['process_cumulative_heap_gib']:.3f} | "
        f"{row['process_cumulative_working_gib']:.3f} | {row['interval_seconds']:.1f} |")
    lines+=['','Full annual/decade counters and exported population counts are retained in each archive.',
        'Model collection sizes include objects awaiting annual cleanup; they are not an exact intra-year live-person maximum.',
        'Heap/working peaks are sampled; kernel peak includes file cache. Long-run milestones do not replace tests of shorter stopping horizons.']
    (output/'comparison.md').write_text('\n'.join(lines)+'\n')


def execute(plan,*,runner=sequence.run_command,prepare=calibration.prepare_calibration,pause=time.sleep):
    output=Path(plan['output']);frontend=Path(plan['frontend'])
    with sequence.exclusive(output.parent):
        preflight(plan)
        image=calibration.installed_image(plan['image'])
        plan=dict(plan,image=image)
        # Resolve tags once; every fresh package and trial uses this exact image ID.
        for case in plan['cases']:
            case['command'][case['command'].index('--image')+1]=image
        output.mkdir(mode=0o700);write_attribution(output)
        for name in ('prepared','trials','audits','archives','logs'):(output/name).mkdir(mode=0o700)
        bench.write_json(output/'plan.json',plan)
        record=dict(format=FORMAT,passed=False,steps=[],completed=[],profiles=[],production_changed=False)
        original_controls=None;source_inputs=None
        env=dict(os.environ,TMPDIR=str(output/'trials'),JASMINE_WEB_REPO=str(frontend),
            PYTHONPATH=os.pathsep.join((str(ROOT),str(frontend))),PYTHONUNBUFFERED='1',PIP_NO_INPUT='1')
        env.setdefault('PIP_DEFAULT_TIMEOUT','60')
        def save():bench.write_json(output/'report.json',record,replace=True)
        def step(case,name,operation):
            unchanged(plan)
            record['steps'].append(dict(case=case['name'],stage=name,passed=False,started_at=bench.instant()));save()
            print(f"\n{case['name']}: {name}",flush=True)
            value=operation()
            unchanged(plan)
            record['steps'][-1].update(passed=True,finished_at=bench.instant());save()
            return value
        try:
            for case in plan['cases']:
                record['preflight']=preflight(plan);save()
                prepared=step(case,'prepare',lambda:prepare(Path(case['preparation']),case['settings']['population'],image))
                receipt=calibration.read_calibration(prepared)
                if receipt['identity']['model']!=plan['model']:raise ArtifactError('Prepared population uses a different model JAR')
                inputs,_=bench.read_json(Path(case['preparation'])/'source-inputs.json')
                if source_inputs is not None and inputs!=source_inputs:raise ArtifactError('Public source inputs differ between populations')
                source_inputs=inputs
                if plan['pause_seconds']:
                    print(f"Settling for {plan['pause_seconds']} seconds before measured simulation.",flush=True)
                    pause(plan['pause_seconds'])
                step(case,'run',lambda:runner(case['command'],output/'logs'/(case['name']+'-run.log'),env))
                step(case,'audit',lambda:audit(prepared,Path(case['completed']),Path(case['audit'])))
                profile,evidence,config=step(case,'archive',lambda:archive_profile(plan,case))
                selected=controls(config)
                if original_controls is not None and selected!=original_controls:
                    raise ArtifactError('Scientific controls differ beyond starting population')
                original_controls=selected
                record['profiles'].append(profile)
                write_comparison(output,record['profiles']);save()
                def dispose_output():
                    verify_profile_archive(Path(case['archive']))
                    return bench.dispose(Path(case['archive']),Path(case['completed']))
                step(case,'dispose-output',dispose_output)
                disposal,_=bench.read_json(Path(case['completed'])/'model-proof/benchmark-disposal.json')
                if not disposal.get('passed') or bench.output_files(Path(case['completed'])/'model-proof/retained-output'):
                    raise ArtifactError('Bulk result disposal remains unconfirmed')
                step(case,'retire-generated-inputs',lambda:retire_prepared(plan,case))
                record['completed'].append(dict(population=case['settings']['population'],archive=case['archive'],evidence=evidence));save()
            record['passed']=True;save()
        except BaseException as error:
            record['failure']=dict(error_type=type(error).__name__,message=str(error)[:1000]);save()
            raise
    print('PASSED: '+str(output/'report.json'),flush=True)
    print('Comparison: '+str(output/'comparison.md'),flush=True)
    return record


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--runtime-from',type=Path,help='Read only the runtime image ID from an existing calibration receipt')
    parser.add_argument('--image',help='Alternatively select an installed runtime image')
    parser.add_argument('--populations',type=int,nargs='+',default=[20000,50000,100000])
    parser.add_argument('--end-year',type=int,choices=range(2020,2071),default=2070)
    parser.add_argument('--repetitions',type=int,choices=(1,2),default=1)
    parser.add_argument('--heap-mib',type=int,default=4096)
    parser.add_argument('--memory-mib',type=int,default=5120)
    parser.add_argument('--cpu-millis',type=int,choices=(1000,1500,2000),default=2000)
    parser.add_argument('--pause-seconds',type=int,default=60)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dry-run',action='store_true',help='Verify sources and print the plan; no preparation, Docker or simulation')
    args=parser.parse_args(argv)
    os.umask(0o077)
    def interrupted(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    try:
        plan=make_plan(args)
        if args.dry_run:
            print(json.dumps(plan,indent=2,allow_nan=False));return 0
        execute(plan);return 0
    except (Exception,KeyboardInterrupt) as error:
        print('STOPPED: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
