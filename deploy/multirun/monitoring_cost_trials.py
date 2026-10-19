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
    notice=('<p><strong>Functional smoke test: one run per condition. These timing differences '
            'are not reliable estimates of monitoring overhead.</strong></p>'
            if report['settings']['rounds']==1 else
            '<p>Repeated fictional work measures this fixture on this host. Confirm effects with representative scientific jobs and mixed load.</p>')
    return '<!doctype html>\n<!-- (C) Copyright 2026, by Ross Richardson; Monitoring-cost evidence. @author ross richardson -->\n'+\
        '<html lang="en"><meta charset="utf-8"><title>Monitoring costs</title><style>body{font:16px system-ui;margin:2rem}table{border-collapse:collapse}th,td{padding:9px;border:1px solid #bbc}th{background:#e5edf6}</style>'+\
        '<h1>Monitoring costs</h1>'+notice+'<p>Off: optional review disabled. External: external review enabled. Full: external and Java review enabled. '+\
        'Every condition keeps the same safety checks, inputs and resource limits. Unknown values are not zero.</p><table><thead><tr>'+\
        ''.join('<th>'+html.escape(h)+'</th>' for h in headings)+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table><ul>'+\
        ''.join('<li>'+html.escape(item)+'</li>' for item in report['limitations'])+'</ul></html>'


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
    from jasmine_web.batch.resource_recovery import RecoveryPolicy
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.worker import Worker
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
    classes=work/'classes';classes.mkdir()
    subprocess.run(['javac','--release','17','-proc:none','-d',str(classes),str(ROOT/'deploy/acceptance/MonitoringWorkload.java')],
        check=True,capture_output=True,timeout=30)
    scientific=settings['scientific']
    resources=Resources(2000,5120 if scientific else 512,12288 if scientific else 64)
    queue=Queue(os.environ['JASMINE_BATCH_TEST_DSN'],'monitoring-cost',schema='review_cost_'+uuid4().hex)
    executor=DockerExecutor(work/'execution',approved_images=[image],input_roots=[inputs])
    trace=ReviewTrace(queue,state,application_code_fingerprint=application_fingerprint());executor.performance=trace
    collector=PerformanceReview(queue,state,shared_reader(model_reader(queue,executor)),trace=trace)
    rows=[];leases={};clean=False;passed=False;error=None
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
        from deploy.multirun.container_adapter import SimPathsContainerAdapter
        prepared=Path(settings['prepared']);receipt=calibration.read_calibration(prepared)
        plan=calibration.checked_settings(dict(population=receipt['identity']['population'],end_year=settings['end_year'],
            repetitions=1,cpu_millis=2000,heap_mib=4096,memory_mib=5120,max_memory_mib=5120,storage_mib=12288,
            setup_seconds=900,repetition_seconds=14400,max_attempts=1))
        class ScientificAdapter(calibration.ResearchAdapter):
            def container_command(self,lease,request):
                calibration.verify_snapshot(self.prepared,self.receipt)
                return SimPathsContainerAdapter.container_command(self,lease,request)
        adapter=ScientificAdapter(prepared,classes,plan)
        executor.input_roots=tuple([executor._directory(prepared)])
        arguments=calibration.arguments(calibration.configuration(receipt,plan),receipt)
        recovery=RecoveryPolicy(max_memory_mib=5120,max_heap_mib=4096,storage_multiplier=1)
        heaps={'baseline':4096}
    worker=Worker(queue,executor,adapter,'monitoring-cost')
    try:
        queue.migrate()
        queue.create_pool(resources,policy=Policy(per_user_active=1,max_attempts=1,attempt_seconds=15300 if scientific else 300,
            total_seconds=15300 if scientific else 300))
        dataset=arguments['dataset_id']
        queue.register_dataset(dataset,receipt['sha256'] if scientific else 'a'*64)
        queue.approve('fixture',seconds=86400);queue.grant_dataset('fixture',dataset,seconds=86400)
        collector.start()
        with worker.open():
            for round_number,mode in order(settings['rounds'],settings['modes']):
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
                    if time.monotonic()-started>(15360 if scientific else 360):raise TimeoutError('Bounded cost trial exceeded its runtime')
                    time.sleep(.25)
                if job['state']!='succeeded' or lease is None:raise AssertionError('A monitoring condition did not complete successfully')
                path=executor.workspace(lease)
                container=executor.docker.inspect(executor._name(lease))
                begin=datetime.fromisoformat(container['State']['StartedAt'].replace('Z','+00:00'))
                end=datetime.fromisoformat(container['State']['FinishedAt'].replace('Z','+00:00'))
                result=adapter.validate(lease,path/'work')
                digest=fingerprint(result)
                if rows and rows[0]['result_fingerprint']!=digest and not scientific:raise AssertionError('Monitoring changed the fixture result')
                reference=json.loads((path/'work/reference.json').read_text()) if not scientific else {}
                trace.ingest_java(lease.attempt_id,path/'work',final=True)
                java_bytes=sum(file.stat().st_size for file in (path/'work').glob('.performance-java.*')
                    if file.is_file() and not file.is_symlink())
                row=dict(round=round_number,mode=mode,container_seconds=(end-begin).total_seconds(),
                    driver_seconds=time.monotonic()-started,result_fingerprint=digest,
                    process_cpu_ns=reference.get('process_cpu_ns'),memory_peak_bytes=reference.get('memory_peak_bytes'),
                    collector_cpu_ns=trace.collector_cpu_ns-before_cpu,collector_elapsed_ns=trace.collector_elapsed_ns-before_time,
                    java_log_bytes=java_bytes)
                rows.append(row)
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
            if file_hash(model)!=original:raise AssertionError('Original model changed')
            with queue._connection() as c:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(queue.schema)))
            shutil.rmtree(work);clean=True
        finally:
            report=dict(passed=passed and clean,cleanup=clean,error_type=error,settings=settings,platform=platform,
                sources=dict(observer=file_hash(ROOT/'src/main/java/simpaths/experiment/SimPathsPerformanceRun.java'),
                    fixture=file_hash(ROOT/'deploy/acceptance/MonitoringWorkload.java'),harness=file_hash(Path(__file__))),
                runs=rows,summary=summary(rows),limitations=[
                    'Fictional CPU/allocation/I/O work is a functional/cost screen, not a scientific-model benchmark.' if not scientific else
                    'Scientific public-data trials use the ordinary runner and the new observer; legacy acceptance probes are absent.',
                    'Shared VPS noise requires repetitions at different times. This does not qualify mixed-load website performance.',
                    'No XFS quota broker is used by this laptop fixture; native quota storage remains unknown.',
                    'Scientific baseline CPU and peak RAM remain unknown when no independent lifetime counter is available.',
                    'Compilation and control acknowledgement occur before the measured jobs. Reference counters are common to every fictional condition.'])
            write_json(output/'report.json',report)
            (output/'comparison.html').write_text(render_cost(report))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path)
    parser.add_argument('--runtime-from',type=Path)
    parser.add_argument('--scientific',action='store_true')
    parser.add_argument('--end-year',type=int,default=2021)
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
    order(args.rounds,args.modes)
    if not 1<=args.iterations<=10000000 or not 5<=args.sample_seconds<=300 or not 2019<=args.end_year<=2070:
        parser.error('Use bounded fixture and sampling settings')
    sys.path.insert(0,str(args.frontend.resolve(strict=True)))
    from deploy.multirun.research_calibration import read_calibration
    receipt=read_calibration(args.runtime_from)
    settings=dict(rounds=args.rounds,modes=args.modes,sample_seconds=args.sample_seconds,iterations=args.iterations,
        scientific=args.scientific,prepared=str(args.runtime_from.resolve()),model_jar=str(args.runtime_from.resolve()/'model.jar'),
        image=receipt['identity']['source_image'],end_year=args.end_year)
    if args.output.exists() or args.output.is_symlink():parser.error('Choose a new evidence directory')
    if args.dry_run:
        print(json.dumps(dict(settings=settings,order=order(args.rounds,args.modes)),indent=2));return 0
    needed=(15 if args.scientific else 1)*1024**3
    if shutil.disk_usage(args.output.parent).free<needed:parser.error('Insufficient free space for the selected cost trial')
    command=[sys.executable,str(args.frontend/'scripts/test_batch_queue.py'),'--proof-only','--proof-script',str(Path(__file__).resolve()),
        '--proof-requirements',str(ROOT/'deploy/multirun/requirements.txt'),str(ROOT/'deploy/acceptance/requirements.txt'),
        '--proof-timeout-seconds',str(16000*args.rounds*len(args.modes) if args.scientific else 900*args.rounds*len(args.modes)),
        '--output',str(args.output)]
    return subprocess.call(command,env=dict(os.environ,JASMINE_WEB_REPO=str(args.frontend),**{ENV:json.dumps(settings)}))


if __name__=='__main__':raise SystemExit(main())
