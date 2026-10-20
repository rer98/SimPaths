#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Counterbalanced optional-review cost trials with the production queue/executor.
Default work is fictional. Scientific mode requires explicit public calibration.
@author ross richardson
"""
import argparse
from datetime import datetime
import hashlib
import html
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
ENV='SIMPATHS_MONITORING_COST_TRIAL'


def order(rounds,modes):
    if type(rounds) is not int or not 1<=rounds<=5 or not modes or len(set(modes))!=len(modes) or set(modes)-{'off','external','full'}:
        raise ValueError('Use one to five rounds and distinct known review modes')
    return [(n+1,mode) for n in range(rounds) for mode in modes[n%len(modes):]+modes[:n%len(modes)]]


def summary(rows):
    groups={}
    for mode in ('off','external','full'):
        selected=[r for r in rows if r['mode']==mode]
        if not selected:continue
        groups[mode]=dict(runs=len(selected),median_seconds=statistics.median(r['container_seconds'] for r in selected),
            min_seconds=min(r['container_seconds'] for r in selected),max_seconds=max(r['container_seconds'] for r in selected))
        for key in ('driver_seconds','process_cpu_ns','memory_peak_bytes','collector_cpu_ns','collector_elapsed_ns','java_log_bytes'):
            values=[r[key] for r in selected if r.get(key) is not None]
            groups[mode]['median_'+key]=statistics.median(values) if values else None
    base=groups.get('off',{}).get('median_seconds')
    for group in groups.values():group['runtime_difference_percent']=100*(group['median_seconds']/base-1) if base else None
    return groups


def render_cost(report):
    def value(number,divisor=1):return 'Unknown' if number is None else f'{number/divisor:.3f}'
    rows=[]
    for mode,item in report['summary'].items():
        cells=[mode,str(item['runs']),value(item['median_seconds']),value(item['median_driver_seconds']),
            value(item['min_seconds'])+'–'+value(item['max_seconds']),value(item['runtime_difference_percent']),
            value(item['median_process_cpu_ns'],1e9),value(item['median_memory_peak_bytes'],1024**2),
            value(item['median_collector_cpu_ns'],1e9),value(item['median_java_log_bytes'],1024)]
        rows.append('<tr>'+''.join('<td>'+html.escape(cell)+'</td>' for cell in cells)+'</tr>')
    headings=['Mode','Runs','Median container time (s)','Median driver completion time (s)',
        'Container time range (s)','Container-time difference from off (%)',
        'Median reference Java CPU (s)','Median reference container peak (MiB)',
        'Median collector CPU during job (s)','Median detailed Java files (KiB)']
    scientific=report['settings']['scientific']
    notice=('<p><strong>Functional smoke test: one run per condition. These timing differences '
            'are not reliable estimates of monitoring overhead.</strong></p>'
            if report['settings']['rounds']==1 else
            '<p>Repeated scientific runs measure this workload on this host. Confirm effects under VPS mixed load.</p>' if scientific else
            '<p>Repeated fictional work measures this fixture on this host. Confirm effects with representative scientific jobs and mixed load.</p>')
    conditions=[]
    for row in report['runs']:
        evidence=row.get('monitoring',{})
        match=row.get('output_comparison',{}).get('csv_checksums_equal')
        cells=[str(row['round']),row['mode'],value(row['container_seconds']),
            evidence.get('java_coverage','Unknown'),str(len(evidence.get('annual_years',[]))),
            str(row.get('external_review',{}).get('samples','Unknown')),
            value(row.get('external_review',{}).get('sampled_peak_working_memory_bytes'),1024**2),
            value(evidence.get('sampled_peak_heap_used_bytes'),1024**2),
            value(evidence.get('gc_ms_delta'),1000),
            'Equal' if match is True else 'Different; see report' if match is False else 'Not applicable']
        conditions.append('<tr>'+''.join('<td>'+html.escape(cell)+'</td>' for cell in cells)+'</tr>')
    detail_headings=['Round','Mode','Container time (s)','Java evidence','Observed annual years',
        'External samples','External sampled working-memory peak (MiB)',
        'Sampled Java heap peak (MiB)','Cumulative Java GC time (s)','CSV checksums versus first run']
    details='<h2>Individual conditions</h2><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in detail_headings)+\
        '</tr></thead><tbody>'+''.join(conditions)+'</tbody></table>'
    return '<!doctype html>\n<!-- (C) Copyright 2026, by Ross Richardson; Monitoring-cost evidence. @author ross richardson -->\n'+\
        '<html lang="en"><meta charset="utf-8"><title>Monitoring costs</title><style>body{font:16px system-ui;margin:2rem}table{border-collapse:collapse}th,td{padding:9px;border:1px solid #bbc}th{background:#e5edf6}</style>'+\
        '<h1>Monitoring costs</h1>'+notice+'<p>Off: optional review disabled. External: external review enabled. Full: external and Java review enabled. '+\
        'Every condition keeps the same safety checks, inputs and resource limits. Unknown values are not zero. '+\
        'Java heap measurements exist only in full mode; GC time is not pause time.</p><table><thead><tr>'+\
        ''.join('<th>'+html.escape(h)+'</th>' for h in headings)+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table><ul>'+\
        ''.join('<li>'+html.escape(item)+'</li>' for item in report['limitations'])+'</ul>'+details+'</html>'


def cleanup_containers(executor,leases,worker,*,timeout=10):
    # A failed start can raise before the measurement loop copies worker.leases.
    # Recover that owned lease before deciding it is safe to delete any scratch.
    leases.update(worker.leases)
    with executor.exclusive():
        for lease in leases.values():
            if executor.inspect(lease)['state']!='stopped':executor.stop(lease,'cancelled')
            end=time.monotonic()+timeout
            while executor.inspect(lease)['state']!='stopped' and time.monotonic()<end:time.sleep(.1)
            if executor.inspect(lease)['state']!='stopped':raise RuntimeError('Trial cleanup unconfirmed')
            executor.cleanup(lease)


def execute(output):
    from deploy.multirun.artifacts import write_attribution,write_json
    from deploy.multirun.java_review import prepare,file_hash
    from deploy.multirun.performance_review import application_fingerprint
    from jasmine_web.batch.docker_executor import DockerExecutor,ContainerCommand
    from jasmine_web.batch.performance_review import PerformanceReview,set_control
    from jasmine_web.batch.performance_trace import ReviewTrace,trace_history,platform_snapshot
    from jasmine_web.batch.observability import model_reader,shared_reader
    from jasmine_web.batch.policy import Resources,Policy,fingerprint
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.worker import Worker
    from deploy.multirun.monitoring_trial_checks import (ScientificAdapter,scientific_plan,
        check_execution,container_launch_record,settled_measurements,scientific_outputs,output_comparison,retain_diagnostics)
    from psycopg import sql
    settings=json.loads(os.environ[ENV])
    output.mkdir(mode=0o700,parents=True)
    write_attribution(output)
    work=output/'scratch';work.mkdir(mode=0o700)
    state=work/'state';state.mkdir(mode=0o700)
    inputs=work/'inputs';inputs.mkdir(mode=0o700)
    model=Path(settings['model_jar']);image=settings['image']
    shutil.copyfile(model,inputs/'model.jar')
    original=file_hash(model)
    platform=platform_snapshot(state,application_code_fingerprint=application_fingerprint())
    observer=prepare(state,inputs/'model.jar')
    scientific=settings['scientific']
    classes=work/'classes';classes.mkdir()
    if not scientific:
        subprocess.run(['javac','--release','17','-proc:none','-d',str(classes),str(ROOT/'deploy/acceptance/MonitoringWorkload.java')],
            check=True,capture_output=True,timeout=30)
    resources=Resources(2000,5120 if scientific else 512,12288 if scientific else 64)
    queue=Queue(os.environ['JASMINE_BATCH_TEST_DSN'],'monitoring-cost',schema='review_cost_'+uuid4().hex)
    executor=DockerExecutor(work/'execution',approved_images=[image],input_roots=[inputs])
    trace=ReviewTrace(queue,state,application_code_fingerprint=application_fingerprint());executor.performance=trace
    collector=PerformanceReview(queue,state,shared_reader(model_reader(queue,executor)),trace=trace)
    rows=[];leases={};clean=False;passed=False;error=None;scientific_reference=None
    class FixtureAdapter:
        def container_command(self,lease,request):
            shutil.copyfile(classes/'MonitoringWorkload.class',request/'MonitoringWorkload.class')
            return ContainerCommand(image,('/opt/java/openjdk/bin/java','-Xmx128m','-XX:ActiveProcessorCount=2',
                '-cp','/request:/inputs/model.jar','MonitoringWorkload',str(settings['iterations'])),str(inputs))
        def configure_review(self,lease,request,command,seconds,**kwargs):
            shutil.copyfile(observer,request/'performance-observer.jar')
            return ContainerCommand(image,('/opt/java/openjdk/bin/java','-Xmx128m','-XX:ActiveProcessorCount=2',
                '-Dsimpaths.performance.seconds='+str(seconds),'-cp',
                '/request/performance-observer.jar:/request:/inputs/model.jar','simpaths.experiment.SimPathsPerformanceRun',
                'MonitoringWorkload',str(settings['iterations'])),str(inputs))
        def validate(self,lease,directory):
            result=(directory/'result.txt').read_text().strip()
            if len(result)!=64:raise ValueError('Fictional work did not complete')
            return [dict(seed='606',fingerprint=fingerprint(result))]
    adapter=FixtureAdapter()
    arguments=dict(label='Fictional monitoring work',model_digest=image,dataset_id='fixture',seed_plan=['606'],
        run_sets=[dict(id='baseline',parameters={'iterations':settings['iterations']})])
    recovery=None;heaps=None
    if scientific:
        from deploy.multirun import research_calibration as calibration
        prepared=Path(settings['prepared']);receipt=calibration.read_calibration(prepared)
        plan=scientific_plan(receipt,settings['end_year'],population=settings['population'],job_seconds=settings['job_seconds'])
        adapter=ScientificAdapter(prepared,plan)
        if adapter.contract_fingerprint!=settings['contract_fingerprint']:
            raise AssertionError('Scientific sources or settings changed after preflight')
        executor.input_roots=tuple([executor._directory(prepared)])
        arguments=adapter.arguments;recovery=adapter.recovery;heaps=adapter.heaps
        write_json(output/'scientific-contract.json',dict(fingerprint=adapter.contract_fingerprint,**adapter.contract))
    worker=Worker(queue,executor,adapter,'monitoring-cost')
    job_seconds=settings['job_seconds'] if scientific else 300
    try:
        queue.migrate()
        queue.create_pool(resources,policy=Policy(per_user_active=1,max_attempts=1,attempt_seconds=job_seconds,
            total_seconds=job_seconds))
        dataset=arguments['dataset_id']
        queue.register_dataset(dataset,receipt['sha256'] if scientific else 'a'*64)
        queue.approve('fixture',seconds=86400);queue.grant_dataset('fixture',dataset,seconds=86400)
        collector.start()
        with worker.open():
            for round_number,mode in order(settings['rounds'],settings['modes']):
                if scientific:
                    calibration.preflight(plan,output)
                    adapter.verify_sources()
                print(f"Running round {round_number}, {mode}"+
                    (f"; {settings['population']:,} people, 2019–{settings['end_year']}, seed 606" if scientific else ''),flush=True)
                requested=set_control(state,mode!='off',sample_seconds=settings['sample_seconds'])
                set_control(state,mode=='full',java=True,sample_seconds=settings['sample_seconds'])
                # Acknowledgement is outside the measured job, just as enabling
                # a production review window before work arrives would be.
                deadline=time.monotonic()+10
                while collector.control['generation']!=requested['generation'] and time.monotonic()<deadline:time.sleep(.05)
                if collector.control['generation']!=requested['generation']:raise RuntimeError('Review control was not acknowledged')
                before_cpu=trace.collector_cpu_ns;before_time=trace.collector_elapsed_ns
                exp=queue.submit('fixture',uuid4().hex,**arguments,resources=resources,
                    resource_recovery=recovery,heap_limits=heaps)
                started=time.monotonic();lease=None
                while True:
                    worker.tick(max_claims=1);leases.update(worker.leases)
                    if lease is None and worker.leases:lease=next(iter(worker.leases.values()))
                    job=queue.inspect('fixture',exp)['jobs'][0]
                    if job['state'] in ('succeeded','review','cancelled'):break
                    if time.monotonic()-started>job_seconds+60:raise TimeoutError('Bounded cost trial exceeded its runtime')
                    time.sleep(.25)
                if job['state']!='succeeded' or lease is None:raise AssertionError('A monitoring condition did not complete successfully')
                driver_seconds=time.monotonic()-started
                collector_cpu=trace.collector_cpu_ns-before_cpu;collector_elapsed=trace.collector_elapsed_ns-before_time
                path=executor.workspace(lease)
                container=executor.docker.inspect(executor._name(lease))
                write_json(path/'container-launch.json',container_launch_record(container))
                begin=datetime.fromisoformat(container['State']['StartedAt'].replace('Z','+00:00'))
                end=datetime.fromisoformat(container['State']['FinishedAt'].replace('Z','+00:00'))
                external=settled_measurements(queue,lease,job,mode,
                    dict(cpu_millis=resources.cpu_millis,memory_mib=resources.memory_mib,storage_mib=resources.storage_mib),
                    scientific=scientific)
                if scientific:
                    write_json(path/'scientific-configuration.json',adapter._configuration(lease).as_dict())
                    result=scientific_outputs(adapter,lease,path/'work')
                    write_json(path/'scientific-outputs.json',result)
                else:result=adapter.validate(lease,path/'work')
                result_digest=fingerprint([dict(seed=r['seed'],fingerprint=r['fingerprint']) for r in result])
                if rows and rows[0]['result_fingerprint']!=result_digest and not scientific:raise AssertionError('Monitoring changed the fixture result')
                reference=json.loads((path/'work/reference.json').read_text()) if not scientific else {}
                trace.ingest_java(lease.attempt_id,path/'work',final=True)
                monitoring=check_execution(mode,path,container,dict(cpu_millis=resources.cpu_millis,
                    memory_mib=resources.memory_mib,storage_mib=resources.storage_mib),image,settings['sample_seconds'],
                    scientific=scientific,deadline=math.floor(lease.deadline.timestamp()))
                row=dict(round=round_number,mode=mode,container_seconds=(end-begin).total_seconds(),
                    driver_seconds=driver_seconds,result_fingerprint=result_digest,attempts=1,
                    process_cpu_ns=reference.get('process_cpu_ns'),memory_peak_bytes=reference.get('memory_peak_bytes'),
                    collector_cpu_ns=collector_cpu,collector_elapsed_ns=collector_elapsed,
                    java_log_bytes=monitoring['java_log_bytes'],monitoring=monitoring,external_review=external,
                    evidence='attempts/'+lease.attempt_id)
                if scientific:
                    if scientific_reference is None:scientific_reference=result
                    row.update(scientific_outputs=result,output_comparison=output_comparison(scientific_reference,result),
                        contract_fingerprint=adapter.contract_fingerprint,verified_annual_years=list(range(2019,settings['end_year']+1)))
                    if not row['output_comparison']['csv_checksums_equal']:
                        print('NOTE: scientific CSV checksums differ; inputs/settings are matched, but timing differences remain provisional.',flush=True)
                retain_diagnostics(output/row['evidence'],path)
                rows.append(row)
                # Save every completed condition immediately, even if a later
                # condition is interrupted. Bulk output is never accumulated.
                write_json(output/row['evidence']/'measurement.json',row)
                executor.cleanup(lease)
                # Each completed native result has passed its adapter's output
                # validation. Keep numeric evidence; delete only this fixture's
                # private bulk before the next matched condition.
                shutil.rmtree(path/'work')
                print(f"PASS: round {round_number}, {mode}: {row['container_seconds']:.3f} seconds",flush=True)
        collector.close()
        history=trace_history(queue,limit=2000)
        write_json(output/'measurements.json',history)
        passed=True
    except BaseException as problem:
        error=type(problem).__name__
        raise
    finally:
        collector.close()
        try:
            cleanup_containers(executor,leases,worker)
            for lease in leases.values():retain_diagnostics(output/'attempts'/lease.attempt_id,executor.workspace(lease))
            if file_hash(model)!=original:raise AssertionError('Original model changed')
            if scientific:adapter.verify_sources()
            with queue._connection() as c:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(queue.schema)))
            shutil.rmtree(work);clean=True
        finally:
            report=dict(passed=passed and clean,cleanup=clean,error_type=error,settings=settings,platform=platform,
                sources=dict(observer=file_hash(ROOT/'src/main/java/simpaths/experiment/SimPathsPerformanceRun.java'),
                    fixture=file_hash(ROOT/'deploy/acceptance/MonitoringWorkload.java'),harness=file_hash(Path(__file__)),
                    checks=file_hash(Path(__file__).with_name('monitoring_trial_checks.py'))),
                runs=rows,summary=summary(rows),timing_comparison_provisional=scientific and
                    any(not r['output_comparison']['csv_checksums_equal'] for r in rows),limitations=[
                    'Fictional CPU/allocation/I/O work is a functional/cost screen, not a scientific-model benchmark.' if not scientific else
                    'Scientific public-data trials use the ordinary runner and the new observer; legacy acceptance probes are absent.',
                    'Shared VPS noise requires repetitions at different times. This does not qualify mixed-load website performance.',
                    'No XFS quota broker is used by this laptop fixture; native quota storage remains unknown.',
                    'Scientific baseline CPU and peak RAM remain unknown when no independent lifetime counter is available.',
                    'Compilation and control acknowledgement occur before the measured jobs. Reference counters are common to every fictional condition.']+
                    (['Scientific CSV hashes are compared and retained. Known model RNG/reproducibility limitations mean changed hashes make timing comparisons provisional; matching launch settings do not prove identical scientific work.'] if scientific else []))
            write_json(output/'report.json',report)
            (output/'comparison.html').write_text(render_cost(report))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path)
    parser.add_argument('--runtime-from',type=Path)
    parser.add_argument('--scientific',action='store_true')
    parser.add_argument('--population',type=int,help='Scientific mode: require this exact prepared population')
    parser.add_argument('--end-year',type=int,default=2021)
    parser.add_argument('--job-seconds',type=int,default=7200,help='Scientific attempt/total time cap (960–15300 seconds)')
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--modes',nargs='+',default=['off','external','full'])
    parser.add_argument('--iterations',type=int,default=750000)
    parser.add_argument('--sample-seconds',type=int,default=15)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.execute_proof:
        if not os.environ.get('JASMINE_BATCH_TEST_DSN') or ENV not in os.environ:parser.error('Use the disposable test driver')
        execute(args.output);return 0
    if not args.frontend or not args.runtime_from:parser.error('Supply --frontend and --runtime-from')
    if args.population is not None and not args.scientific:parser.error('--population requires --scientific')
    order(args.rounds,args.modes)
    if not 1<=args.iterations<=10000000 or not 5<=args.sample_seconds<=300 or not 2019<=args.end_year<=2070:
        parser.error('Use bounded fixture and sampling settings')
    sys.path.insert(0,str(args.frontend.resolve(strict=True)))
    from deploy.multirun.research_calibration import read_calibration
    receipt=read_calibration(args.runtime_from)
    settings=dict(rounds=args.rounds,modes=args.modes,sample_seconds=args.sample_seconds,iterations=args.iterations,
        scientific=args.scientific,prepared=str(args.runtime_from.resolve()),model_jar=str(args.runtime_from.resolve()/'model.jar'),
        image=receipt['identity']['source_image'],end_year=args.end_year,population=receipt['identity']['population'],job_seconds=args.job_seconds)
    if args.scientific:
        from deploy.multirun.monitoring_trial_checks import ScientificAdapter,scientific_plan
        plan=scientific_plan(receipt,args.end_year,population=args.population,job_seconds=args.job_seconds)
        adapter=ScientificAdapter(args.runtime_from,plan)
        settings['contract_fingerprint']=adapter.contract_fingerprint
    proof_seconds=((args.job_seconds+300)*args.rounds*len(args.modes)+300 if args.scientific
        else 900*args.rounds*len(args.modes))
    if proof_seconds>172800:parser.error('Campaign exceeds the disposable driver\'s 48-hour cap; reduce rounds or the job timeout')
    if args.output.exists() or args.output.is_symlink():parser.error('Choose a new evidence directory')
    if args.dry_run:
        print(json.dumps(dict(settings=settings,order=order(args.rounds,args.modes),proof_timeout_seconds=proof_seconds,
            **({'scientific_contract':adapter.contract} if args.scientific else {})),indent=2));return 0
    needed=(15 if args.scientific else 1)*1024**3
    if shutil.disk_usage(args.output.parent).free<needed:parser.error('Insufficient free space for the selected cost trial')
    command=[sys.executable,str(args.frontend/'scripts/test_batch_queue.py'),'--proof-only','--proof-script',str(Path(__file__).resolve()),
        '--proof-requirements',str(ROOT/'deploy/multirun/requirements.txt'),str(ROOT/'deploy/acceptance/requirements.txt'),
        '--proof-timeout-seconds',str(proof_seconds),
        '--output',str(args.output)]
    return subprocess.call(command,env=dict(os.environ,JASMINE_WEB_REPO=str(args.frontend),**{ENV:json.dumps(settings)}))


if __name__=='__main__':raise SystemExit(main())
