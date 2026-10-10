#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Real public 50,000-person MultiRun resource calibration and automatic recovery.
Private disposable queue, passive JVM probe and existing production worker/API.
No registered release, service default, model calculation or real user is changed.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._workflow import frontend_path
sys.path.insert(0, str(frontend_path()))
from deploy.multirun.artifacts import ArtifactError, fingerprint, write_attribution, write_json
from deploy.multirun.container_adapter import SimPathsContainerAdapter, container_submission
from deploy.multirun.releases import atomic_json
from deploy.multirun import storage_proof

MIB = 1024**2
CASES = ('control', 'heap-trial', 'heap-retry', 'live-growth')
PROBE = ROOT/'deploy/acceptance/SimPathsResourceProbe.java'
PREFIX = 'SIMPATHS_RESOURCE_SAMPLE '
SAMPLE_FIELDS = {'sequence','elapsed_ms','heap_used_bytes','heap_committed_bytes','heap_max_bytes',
    'nonheap_used_bytes','container_limit_bytes','container_used_bytes','inactive_file_bytes',
    'working_set_bytes','cgroup_peak_bytes','gc_ms'}


def case_policy(name):
    """Operator test settings are explicit; the production defaults stay intact."""
    from jasmine_web.batch.resource_recovery import RecoveryPolicy
    if name not in CASES:
        raise ValueError('Select a known bounded resource-recovery case')
    heap = {'control':3072, 'heap-trial':4096, 'heap-retry':512, 'live-growth':3072}[name]
    # Lower the pressure threshold only in the live-growth fixture. This lets a
    # representative workload exercise growth before reaching its normal limit.
    policy = RecoveryPolicy(pressure_percent=50 if name=='live-growth' else 85,
        max_memory_mib=5120 if name in ('control','heap-trial') else 7168,
        max_heap_mib=4096, check_seconds=5)
    return heap, policy


def telemetry(log):
    """Reject inconsistent numeric evidence instead of estimating absent values."""
    rows=[]
    for line in log.splitlines():
        if not line.startswith(PREFIX):
            continue
        row=json.loads(line[len(PREFIX):])
        if (type(row) is not dict or set(row)!=SAMPLE_FIELDS
                or any(type(v) is not int or not -1<=v<2**63 for v in row.values())
                or not 0<=row['heap_used_bytes']<=row['heap_committed_bytes']<=row['heap_max_bytes']
                or row['heap_max_bytes']<=0 or row['container_limit_bytes']<=0
                or min(row[k] for k in ('container_used_bytes','inactive_file_bytes','working_set_bytes',
                    'elapsed_ms','gc_ms','nonheap_used_bytes','sequence'))<0
                or row['working_set_bytes']!=max(0,row['container_used_bytes']-row['inactive_file_bytes'])
                or rows and (row['sequence']!=rows[-1]['sequence']+1
                    or row['elapsed_ms']<rows[-1]['elapsed_ms'] or row['gc_ms']<rows[-1]['gc_ms']
                    or row['heap_max_bytes']!=rows[0]['heap_max_bytes'])):
            raise ValueError('Invalid or incomplete real JVM telemetry')
        rows.append(row)
    if rows and rows[0]['sequence']!=0:
        raise ValueError('Initial JVM telemetry was lost from the bounded container log')
    return rows


def summary(rows, heap_mib):
    if (type(heap_mib) is not int or heap_mib<1 or not rows
            or any(row['heap_max_bytes']!=heap_mib*MIB for row in rows)):
        raise ValueError('Measured JVM maximum differs from its frozen launch allocation')
    return dict(samples=len(rows), elapsed_ms=rows[-1]['elapsed_ms'], gc_ms=rows[-1]['gc_ms'],
        peak={key:max(row[key] for row in rows) for key in SAMPLE_FIELDS-{'sequence','elapsed_ms','gc_ms'}},
        observed_container_limits_bytes=sorted({row['container_limit_bytes'] for row in rows}))


def compile_probe(prepared, classes, log):
    with log.open('wb') as stream:
        subprocess.run(['javac','--release','17','-cp',str(prepared/'model.jar'),'-d',str(classes),str(PROBE)],
            check=True,stdout=stream,stderr=subprocess.STDOUT,timeout=60)


class ObservedAdapter(SimPathsContainerAdapter):
    """An attributed test-only entry wrapper; all model staging stays unchanged."""
    def __init__(self, prepared, image, classes, **options):
        super().__init__(prepared,image,**options)
        self.classes=classes

    def container_command(self, lease, request):
        command=super().container_command(lease,request)
        runner=request/'run.sh'
        original='-cp /inputs/model.jar simpaths.experiment.SimPathsMultiRun'
        text=runner.read_text()
        if text.count(original)!=1:
            raise ArtifactError('Retained runner cannot accept the explicit passive test wrapper')
        runner.write_text(text.replace(original,'-cp /request/probe:/inputs/model.jar SimPathsResourceProbe'))
        shutil.copytree(self.classes,request/'probe')
        return command


def prepared_check(prepared):
    result=storage_proof.check_prepared(prepared,storage_proof.calibration([1],256))
    with zipfile.ZipFile(prepared/'model.jar') as archive:
        if 'microsim/monitoring/MemoryMonitor.class' not in archive.namelist():
            raise ArtifactError('Use the prepared release containing shared memory monitoring')
    memory=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    if int(memory['MemAvailable'].split()[0])*1024<8*1024**3:
        raise ArtifactError('Need 8 GiB available RAM for the bounded recovery ceiling')
    return result


def native_settings(prepared, receipt):
    fixture=os.environ.get('JASMINE_QUOTA_FIXTURE')
    if fixture is None:
        return None
    settings=json.loads(Path(fixture).read_text())
    if settings.get('proof_mode')!='real-model-resource-recovery':
        raise ArtifactError('Use the resource-recovery native wrapper; no fallback is permitted')
    # Reuse the existing strict source/image/receipt and empty-root checks.
    storage_proof.native_fixture(prepared,receipt,proof_mode='real-model-resource-recovery')
    if settings['calibration']!=storage_proof.calibration([1],256):
        raise ArtifactError('Native growth uses one full-length run with the standard allowance')
    return settings


@contextmanager
def browser_session(queue, prepared, receipt, work, evidence, *, model=None):
    """Use real production authentication/routes and JavaScript against this queue."""
    import uvicorn
    from playwright.sync_api import sync_playwright,expect
    from jasmine_web.batch.access import Access
    from jasmine_web.batch.browser import create_app
    from jasmine_web.batch.datasets import Datasets
    from jasmine_web.batch.submission_service import Submissions
    from deploy.multirun.browser_model import BrowserModel
    mail={}
    async def deliver(email,code):mail[email]=code  # In-memory capture; no SMTP.
    access=Access(queue,secrets.token_urlsafe(48),deliver)
    owner=access.approve_email('resource-proof@example.org')
    access.approve_email('other-proof@example.org')
    if model is None:
        release=storage_proof.configuration(receipt,1)['model_release']
        model=BrowserModel({release:dict(jar=prepared/'model.jar',image=receipt['identity']['source_image'],
            defaults=prepared/'input',resource_policy=storage_proof.calibration_policy(storage_proof.calibration([1],256)))})
    service=Submissions(access,Datasets(queue,work/'uploads',reserve_bytes=1),model)
    sock=socket.socket(); sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    origin='http://127.0.0.1:'+str(port)
    app=create_app(service,origin=origin,local_codes=True,site=dict(name='SimPaths Online',subtitle='UK MultiRun'))
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True); thread.start()
    try:
        until=time.monotonic()+15
        while not server.started:
            if not thread.is_alive() or time.monotonic()>until:
                raise RuntimeError('Private proof frontend did not start')
            time.sleep(.05)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            context=browser.new_context(viewport=dict(width=1360,height=900))
            errors=[]; context.on('page',lambda page:page.on('pageerror',lambda error:errors.append(str(error))))
            page=context.new_page()
            def login(page,email):
                page.goto(origin); page.get_by_label('Email address',exact=True).fill(email)
                page.get_by_role('button',name='Request verification code',exact=True).click()
                expect(page.get_by_label('Verification code',exact=True)).to_be_visible()
                page.get_by_label('Verification code',exact=True).fill(mail[email])
                page.get_by_role('button',name='Sign in',exact=True).click()
                expect(page.locator('#workspace')).to_be_visible()
            login(page,'resource-proof@example.org')
            page.locator('[data-page="history"]').click()
            other=browser.new_context(); foreign=other.new_page(); login(foreign,'other-proof@example.org')
            yield owner,page,foreign,origin,errors
            if errors:
                raise AssertionError('Uncaught browser error during the resource rehearsal')
            page.screenshot(path=str(evidence/'completed-jobs.png'),full_page=True)
            other.close(); context.close(); browser.close()
    finally:
        server.should_exit=True; thread.join(timeout=10); sock.close()
        if thread.is_alive():
            raise RuntimeError('Private proof frontend did not stop')


def verify_attempts(queue, owner, experiment, attempts, adapter, executor, case):
    """Validate all durable receipts and confirm failed attempts publish no run."""
    from jasmine_web.batch.resource_recovery import effective
    job=queue.inspect(owner,experiment)['jobs'][0]
    if job['state']!='succeeded' or job['attempts']!=len(attempts) or len(attempts)>3:
        raise AssertionError('Original attempt cap or scientific completion differs')
    frozen=next(iter(attempts.values()))['lease'].specification
    outcomes=[]
    for entry in attempts.values():
        lease=entry['lease']
        with queue._connection() as c:
            row=c.execute('SELECT phase,outcome FROM attempts WHERE id=%s',(lease.attempt_id,)).fetchone()
            released=c.execute('SELECT released_at FROM reservations WHERE attempt_id=%s',(lease.attempt_id,)).fetchone()
            receipts=list(c.execute('SELECT ordinal,expected_seed,actual_seed,output_fingerprint FROM repetitions '
                'WHERE attempt_id=%s ORDER BY ordinal',(lease.attempt_id,)))
        if lease.specification!=frozen or row['phase']!='finished' or released['released_at'] is None:
            raise AssertionError('Frozen specification or settled reservation changed')
        outcomes.append(row['outcome'])
        entry['outcome']=row['outcome']; entry['final_resources']=effective(lease)
        if row['outcome']=='success':
            # Long CSV verification must not leave an idle database transaction.
            validated=adapter.validate(lease,executor.workspace(lease)/'work')
            if len(validated)!=1 or len(receipts)!=1 or validated[0]['seed']!='606' or any(
                    r['expected_seed']!='606' or r['actual_seed']!='606'
                    or r['output_fingerprint']!=validated[r['ordinal']]['fingerprint'] for r in receipts):
                raise AssertionError('Completed years, original seeds or stored output hashes differ')
            case['verified_repetitions']=validated
        elif row['outcome']!='heap_limit' or any(r['actual_seed'] is not None for r in receipts):
            raise AssertionError('Unexpected failure or failed attempt published output')
    with queue._connection() as c:
        if c.execute('SELECT count(*) AS n FROM reservations WHERE released_at IS NULL').fetchone()['n']:
            raise AssertionError('Settled model still reserves capacity')
        original=c.execute('SELECT resources FROM jobs WHERE id=%s',(job['id'],)).fetchone()['resources']
    if original!=case['initial_resources'] or frozen['seeds']!=['606']:
        raise AssertionError('Original allocation or seeds changed')
    if case['name']=='heap-retry' and (len(outcomes)<2 or outcomes[:-1]!=['heap_limit']*(len(outcomes)-1)):
        raise AssertionError('Deliberately small real heap did not exercise automatic heap recovery')
    if case['name']!='heap-retry' and outcomes!=['success']:
        raise AssertionError('Unrestricted calibration unexpectedly retried')
    case.update(outcomes=outcomes,attempts=len(attempts),frozen_specification_unchanged=True,
        verified_annual_years=list(range(2019,2027)),reservations_released=True,
        resource_events=job.get('resource_recovery',[]))


def retire_attempts(queue, executor):
    """Use both production lifecycles after private telemetry has been saved."""
    from jasmine_web.batch.attempt_cleanup import AttemptCleanup
    from deploy.multirun.queue_adapter import result_deletion_targets
    AttemptCleanup(queue,executor,outputs=result_deletion_targets).retire()
    storage_proof.retire_finished(queue,executor)


def execute(output):
    from jasmine_web.batch.docker_executor import DockerExecutor
    from jasmine_web.batch.policy import Policy,Resources,RuntimeAllowance
    from jasmine_web.batch.resource_recovery import effective
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.worker import Worker
    from playwright.sync_api import expect
    from psycopg import sql

    prepared=Path(os.environ['SIMPATHS_RESOURCE_PREPARED']).resolve(strict=True)
    prepared_check(prepared)
    names=json.loads(os.environ.get('SIMPATHS_RESOURCE_CASES',json.dumps(CASES)))
    if (type(names) is not list or not names or len(names)!=len(set(names))
            or any(name not in CASES for name in names)):
        raise ArtifactError('Select unique bounded resource cases')
    output.mkdir(mode=0o700,parents=True); write_attribution(output)
    receipt=storage_proof.prepared_profile(prepared); original_receipt=(prepared/'receipt.json').read_bytes()
    settings=native_settings(prepared,receipt)
    work=Path(settings['source']) if settings else Path(tempfile.mkdtemp(prefix='simpaths-resource-proof-'))
    classes=Path(tempfile.mkdtemp(prefix='simpaths-resource-classes-'))
    queue=Queue(os.environ['JASMINE_BATCH_TEST_DSN'],'resource-proof',schema='resource_proof_'+uuid4().hex)
    image=receipt['identity']['source_image']
    report=dict(passed=False,cleanup=False,production_acceptance=False,
        workload=dict(population=50000,start_year=2019,end_year=2026,repetitions=1,seeds=['606']),
        prepared_sha256=receipt['sha256'],image=image,model=fingerprint(prepared/'model.jar'),cases=[],
        enforcement='xfs-project-v1' if settings else 'application-monitoring',
        measurement='passive JVM/shared monitor samples every two seconds; working set excludes inactive file cache',
        defaults_changed=False,source_sha256={str(path.relative_to(ROOT)):fingerprint(path)['sha256']
            for path in (Path(__file__),PROBE)})
    quotas=None
    if settings:
        from jasmine_web.batch.workspace_quota import WorkspaceQuotas
        quotas=WorkspaceQuotas(work/'execution',settings['source_socket'],settings['guard'])
    executor=DockerExecutor(work/'execution',approved_images=[image],input_roots=[prepared],workspace_quotas=quotas)
    policy=storage_proof.calibration_policy(storage_proof.calibration([1],256))
    adapter=ObservedAdapter(prepared,image,classes,resource_policy=policy)
    worker=Worker(queue,executor,adapter,'real-resource-proof')
    known={}; clean=True; migrated=False; baseline=None; baseline_receipts=None
    stage='preflight'
    try:
        compile_probe(prepared,classes,output/'probe-compile.log')
        report['probe_class_sha256']={str(path.relative_to(classes)):fingerprint(path)['sha256']
            for path in sorted(classes.rglob('*.class'))}
        report['compiler']=subprocess.check_output(['javac','-version'],text=True,stderr=subprocess.STDOUT).strip()
        if quotas:
            report['quota_probe']=quotas.preflight()
        queue.migrate(); migrated=True
        allowance=RuntimeAllowance(900,3600)
        queue.create_pool(Resources(2000,7168,8704),policy=Policy(per_user_active=1,
            attempt_seconds=4500,total_seconds=13500,retry_delay_seconds=1))
        queue.register_dataset(receipt['revision'],receipt['sha256'])
        with worker.open(),browser_session(queue,prepared,receipt,work,output) as (owner,page,foreign,origin,errors):
            queue.grant_dataset(owner,receipt['revision'])
            try:
                for name in names:
                    stage=name
                    heap,recovery=case_policy(name)
                    args=container_submission(storage_proof.configuration(receipt,1),prepared,image)
                    args['label']='Resource rehearsal — '+name
                    resources=Resources(**storage_proof.allocation(receipt,repetitions=1,resource_policy=policy))
                    experiment=queue.submit(owner,'real-'+name,**args,resources=resources,
                        runtime_allowance=allowance,resource_recovery=recovery,
                        heap_limits={r['id']:heap for r in args['run_sets']})
                    case=dict(name=name,initial_heap_mib=heap,initial_resources=asdict(resources),
                        recovery_policy=asdict(recovery),peak_workspace_bytes=0,attempt_details=[],browser_messages=[])
                    report['cases'].append(case)
                    attempt_entries={}; started=time.monotonic(); next_progress=started; next_sample=started
                    print(f'Measuring {name}: {heap/1024:g} GiB heap, 5 GiB container; real 50,000 people, 2019–2026',flush=True)
                    page.get_by_role('button',name='Refresh',exact=True).click()
                    while True:
                        job_ids={j['id'] for j in queue.inspect(owner,experiment)['jobs']}
                        # Save refreshed leases before finish removes them from the worker.
                        known.update(worker.leases)
                        for key,lease in worker.leases.items():
                            known[key]=queue.refresh_resources(lease)
                        worker.tick(); known.update(worker.leases)
                        for key,lease in known.items():
                            if lease.job_id not in job_ids:
                                continue
                            entry=attempt_entries.setdefault(key,dict(lease=lease,container_id=None,pids=[],rows=[]))
                            entry['lease']=lease
                        job=queue.inspect(owner,experiment)['jobs'][0]
                        now=time.monotonic()
                        if now>=next_sample or job['state']=='succeeded':
                            for key,entry in attempt_entries.items():
                                lease=entry['lease']; path=executor.workspace(lease)
                                container=executor.docker.inspect(executor._name(lease))
                                if container:
                                    if entry['container_id'] not in (None,container['Id']):
                                        raise AssertionError('An attempt changed its model container')
                                    entry['container_id']=container['Id']
                                    if container['State']['Running']:
                                        pid=container['State']['Pid']
                                        if pid not in entry['pids']:entry['pids'].append(pid)
                                    log=executor.docker.call('container','logs',container['Id'])
                                else:
                                    log=(path/'execution.log').read_text() if (path/'execution.log').exists() else ''
                                entry['rows']=telemetry(log)
                                measured=storage_proof.sample(path)
                                if measured:case['peak_workspace_bytes']=max(case['peak_workspace_bytes'],measured['work_and_request_bytes'])
                                if quotas:
                                    value=quotas.check(lease)
                                    if value['limit_bytes']!=effective(lease)['storage_mib']*MIB:
                                        raise AssertionError('Kernel limit differs from confirmed growth')
                                    if entry.get('project_id',value['project_id'])!=value['project_id']:
                                        raise AssertionError('Live storage growth changed its quota project')
                                    entry['project_id']=value['project_id']
                                    entry['quota_peak_used_bytes']=max(entry.get('quota_peak_used_bytes',0),value['used_bytes'])
                            block=page.locator('[data-job-id="'+job['id']+'"]')
                            if job.get('resource_recovery'):
                                # Render current real status, including durable retry/growth notices.
                                page.get_by_role('button',name='Refresh',exact=True).click()
                                latest=next((r for r in job['resource_recovery'] if r['action']!='waiting'),None)
                                if latest and latest['action'] in ('retry','increased'):
                                    message=('Retry was planned with a larger allocation' if latest['action']=='retry'
                                        else 'Allocation was increased without restarting the simulation')
                                    expect(block).to_contain_text(message,timeout=15000)
                                    if message not in case['browser_messages']:
                                        case['browser_messages'].append(message)
                                        page.screenshot(path=str(output/(name+'-'+latest['action']+'.png')),full_page=True)
                            next_sample=now+5
                        if job['state']=='succeeded':break
                        if job['state'] in ('review','cancelled','expired','blocked'):
                            case.update(state=job['state'],history=job['history'])
                            raise AssertionError('Real resource case did not complete')
                        if now-started>9000:raise TimeoutError('Real resource case exceeded its bounded rehearsal timeout')
                        if now>=next_progress:
                            peak=max((r['working_set_bytes'] for e in attempt_entries.values() for r in e['rows']),default=0)
                            print(f"  {name}: {job['state']}; attempts {job['attempts']}; working RAM peak {peak/1024**3:.2f} GiB; workspace {case['peak_workspace_bytes']/1024**3:.2f} GiB",flush=True)
                            next_progress=now+30
                        page.wait_for_timeout(500)
                    verify_attempts(queue,owner,experiment,attempt_entries,adapter,executor,case)
                    if name=='live-growth':
                        increases=[r for r in case['resource_events'] if r['action']=='increased']
                        if not {'memory','storage'}<={r['kind'] for r in increases}:
                            raise AssertionError('Real workload did not exercise both live resource increases')
                    if foreign.request.get(origin+'/api/experiments/'+experiment).status!=403:
                        raise AssertionError('Another owner can read resource recovery status')
                    for key,entry in attempt_entries.items():
                        lease=entry['lease']; rows=entry.pop('rows')
                        metrics=summary(rows,lease.heap_mib)
                        if len(entry['pids'])!=1:
                            raise AssertionError('Original JVM PID changed during an attempt')
                        record={k:v for k,v in entry.items() if k!='lease'}
                        record.update(id=key,heap_mib=lease.heap_mib,**metrics)
                        case['attempt_details'].append(record)
                        directory=output/name/key;directory.mkdir(mode=0o700,parents=True)
                        write_json(directory/'telemetry.json',rows)
                        for filename in ('execution.log','exit.json','container-policy.json'):
                            source=executor.workspace(lease)/filename
                            if source.exists():shutil.copy2(source,directory/filename)
                    last=next(reversed(attempt_entries.values()))['lease']
                    before=adapter.validate(last,executor.workspace(last)/'work')
                    if baseline is not None and adapter.validate(baseline,executor.workspace(baseline)/'work')!=baseline_receipts:
                        raise AssertionError('Recovery changed a previously completed configuration')
                    retire_attempts(queue,executor)
                    for entry in attempt_entries.values():
                        lease=entry['lease']
                        if executor.docker.inspect(entry['container_id']) is not None:
                            raise AssertionError('Settled test container was not removed')
                    if adapter.validate(last,executor.workspace(last)/'work')!=before:
                        raise AssertionError('Cleanup changed verified options or output')
                    if (executor.workspace(last)/'work/input').exists() or any(
                            r['copied_input_bytes'] for r in storage_proof.run_summary(executor.workspace(last))):
                        raise AssertionError('Large completed input copies were not reclaimed')
                    if baseline is None:baseline,baseline_receipts=last,before
                    case.update(seconds=round(time.monotonic()-started,2),cleanup_preserves_output=True)
                    # Datetimes are kept in private reports; no credentials or input rows.
                    atomic_json(output/'progress.json',json.loads(json.dumps(report,default=str)))
                    print(f"PASS: {name}; {case['attempts']} attempt(s); original seed/settings, eight annual rows, hashes, browser status and settled capacity verified",flush=True)
                report['browser_errors']=errors
            finally:
                known.update(worker.leases); stopped=True
                for lease in known.values():
                    if not executor.workspace(lease).exists():continue
                    try:
                        executor.stop(lease,'cancelled')
                        deadline=time.monotonic()+30
                        while executor.inspect(lease)['state']=='running' and time.monotonic()<deadline:time.sleep(.2)
                        if executor.inspect(lease)['state']!='stopped':stopped=False
                        else:executor.cleanup(lease)
                    except Exception:stopped=False
                clean=stopped
        storage_proof.verify_snapshot(prepared,receipt)
        if (prepared/'receipt.json').read_bytes()!=original_receipt:
            raise AssertionError('Prepared receipt changed')
        report.update(passed=True,source_unchanged=True)
    except BaseException as error:
        report['failure']=dict(stage=stage,error_type=type(error).__name__,message=str(error)[:500])
        raise
    finally:
        for path in executor.root.glob('batch-*'):
            directory=output/'diagnostics'/path.name;directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            for name in ('execution.log','exit.json','container-policy.json'):
                if (path/name).is_file():shutil.copy2(path/name,directory/name)
        if clean:
            for lease in known.values():
                if executor.workspace(lease).exists():shutil.rmtree(executor.workspace(lease))
            if not settings:shutil.rmtree(work)
            if migrated:
                with queue._connection() as c:
                    c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(queue.schema)))
        shutil.rmtree(classes)
        report['cleanup']=clean and (not any(executor.root.glob('batch-*')) if settings else not work.exists())
        if not report['cleanup']:report['retained_workspace']=str(work)
        write_json(output/'report.json',json.loads(json.dumps(report,default=str)))
    if not report['cleanup']:raise AssertionError('Resource proof cleanup is unconfirmed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--prepared',type=Path)
    parser.add_argument('--cases',nargs='+',choices=CASES,default=list(CASES))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.execute_proof:
        if not os.environ.get('JASMINE_BATCH_TEST_DSN') or not os.environ.get('SIMPATHS_RESOURCE_PREPARED'):
            parser.error('Use the disposable PostgreSQL driver')
        execute(args.output);print('PASSED: '+str(args.output/'report.json'),flush=True);return 0
    if not args.prepared:parser.error('--prepared is required')
    if len(args.cases)!=len(set(args.cases)):parser.error('Select each case at most once')
    prepared=args.prepared.expanduser().resolve(strict=True); prepared_check(prepared)
    if shutil.disk_usage(tempfile.gettempdir()).free<11*1024**3:
        parser.error('Need 11 GiB free on the temporary-work filesystem; runs are sequential')
    output=args.output or Path.home()/'simpaths-benchmarks'/('resource-model-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    if output.exists():parser.error('Choose a new evidence directory')
    command=[sys.executable,str(args.frontend.resolve(strict=True)/'scripts/test_batch_queue.py'),
        '--test-pattern','test_resource_recovery.py','test_worker.py','--proof-script',str(Path(__file__).resolve()),
        '--proof-timeout-seconds',str(9000*len(args.cases)+600),
        '--proof-requirements',str(Path(__file__).with_name('requirements.txt')),
        str(ROOT/'deploy/acceptance/requirements.txt'),'--output',str(output.resolve())]
    return subprocess.call(command,env=dict(os.environ,JASMINE_WEB_REPO=str(args.frontend),
        SIMPATHS_RESOURCE_PREPARED=str(prepared),SIMPATHS_RESOURCE_CASES=json.dumps(args.cases)))


if __name__=='__main__':raise SystemExit(main())
