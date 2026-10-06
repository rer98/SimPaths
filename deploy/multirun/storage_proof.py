#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Measure full-length public MultiRun storage, optional kernel quotas and cleanup.
# @author ross richardson
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
MIB=1024**2
sys.path.insert(0,str(ROOT))
from deploy.multirun.artifacts import ArtifactError, fingerprint, write_attribution, write_json
from deploy.multirun.compare_native import proof_configuration
from deploy.multirun.configuration import normalise
from deploy.multirun.container_adapter import SimPathsContainerAdapter, container_submission
from deploy.multirun.local_web import retire_finished
from deploy.multirun.prepared_dataset import FORMAT, allocation, verify_snapshot
from deploy.multirun.queue_adapter import read_prepared, workspace_required_bytes
from deploy.multirun.resource_policy import DEFAULT_POLICY


def calibration(repetitions=(1,3),per_repetition_mib=256):
    """Bounded proof-only settings; do not rewrite any registered release policy."""
    if (type(repetitions) not in (list,tuple) or not repetitions
            or any(type(count) is not int or not 1<=count<=24 for count in repetitions)
            or list(repetitions)!=sorted(set(repetitions)) or sum(repetitions)>24
            or type(per_repetition_mib) is not int or not 1<=per_repetition_mib<=1024):
        raise ArtifactError('Use ordered unique proof counts (at most 24 runs total) and 1–1024 MiB per repetition')
    return dict(repetitions=list(repetitions),setup_mib=4096,per_repetition_mib=per_repetition_mib,
        maximum_storage_mib=4096+max(repetitions)*per_repetition_mib,
        proof_timeout_seconds=max(7200,sum(900+3600*count for count in repetitions)+3600))


def checked_calibration(value):
    if type(value) is not dict or set(value)!={'repetitions','setup_mib','per_repetition_mib','maximum_storage_mib','proof_timeout_seconds'}:
        raise ArtifactError('Invalid storage-proof calibration')
    expected=calibration(value['repetitions'],value['per_repetition_mib'])
    if value!=expected or any(type(value[key]) is not int for key in ('setup_mib','maximum_storage_mib','proof_timeout_seconds')):
        raise ArtifactError('Storage-proof bounds differ from the selected calibration')
    return expected


def calibration_policy(profile):
    profile=checked_calibration(profile)
    policy=deepcopy(DEFAULT_POLICY)
    policy['simulation']['storage']=dict(setup_mib=profile['setup_mib'],per_repetition_mib=profile['per_repetition_mib'])
    return policy


def prepared_profile(path):
    receipt=read_prepared(path)
    if receipt['identity']['format']!=FORMAT or receipt['identity']['population']!=50000:
        raise ArtifactError('Use the verified public 50,000-person Quick Start dataset')
    return receipt


def check_prepared(path,profile=None):
    """Read-only preflight, called unprivileged before the native fixture mounts."""
    receipt=prepared_profile(path); verify_snapshot(path,receipt)
    profile=calibration() if profile is None else checked_calibration(profile)
    policy=calibration_policy(profile)
    limits=[allocation(receipt,repetitions=count,resource_policy=policy)['storage_mib'] for count in profile['repetitions']]
    if (limits!=[4096+count*profile['per_repetition_mib'] for count in profile['repetitions']]
            or workspace_required_bytes(receipt['identity'])>limits[0]*MIB):
        raise ArtifactError('Prepared inputs or retained policy do not match the selected storage calibration')
    memory=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    if int(memory['MemAvailable'].split()[0])*1024<6*1024**3:
        raise ArtifactError('Need 6 GiB available RAM; stop local simulations first')
    image=subprocess.check_output(['docker','--host','unix:///var/run/docker.sock','image','inspect',
        receipt['identity']['source_image'],'--format','{{.Id}}'],text=True).strip()
    if image!=receipt['identity']['source_image']: raise ArtifactError('Prepared runtime image changed')
    return dict(image=image,calibration=profile,prepared_sha256=receipt['sha256'])


def native_fixture(prepared,receipt, *, proof_mode='real-model-storage'):
    """An explicit native fixture must match the inputs; never fall back."""
    path=os.environ.get('JASMINE_QUOTA_FIXTURE')
    if path is None: return None
    from jasmine_web.batch.local_executor import read_json
    settings=read_json(Path(path))
    checked_calibration(settings.get('calibration'))
    if (proof_mode not in ('real-model-storage','real-model-resource-recovery')
            or settings.get('proof_mode')!=proof_mode
            or settings.get('image')!=receipt['identity']['source_image']
            or settings.get('prepared_sha256')!=receipt['sha256']
            or settings.get('prepared')!=str(prepared)
            or not all(isinstance(settings.get(key),str) for key in ('source','source_socket','guard'))):
        raise ArtifactError('Native fixture does not match this model and prepared input')
    source=Path(settings['source'])
    if not source.is_absolute() or any(p.is_symlink() for p in (source,*source.parents)):
        raise ArtifactError('Native fixture requires its original unlinked execution root')
    for name in ('execution','artifacts'):
        if not (source/name).is_dir() or (source/name).is_symlink() or any((source/name).iterdir()):
            raise ArtifactError('Native fixture requires new empty execution and artifact roots')
    return settings


def quota_sample(quotas,lease,case):
    value=quotas.check(lease)
    if (value['limit_bytes']!=lease.resources['storage_mib']*MIB
            or type(value.get('used_bytes')) is not int or not 0<=value['used_bytes']<=value['limit_bytes']
            or value['project_id']!=case.get('quota_project_id',value['project_id'])):
        raise RuntimeError('Kernel quota identity, limit or usage differs from this attempt')
    case['quota_project_id']=value['project_id']
    case['quota_peak_used_bytes']=max(case.get('quota_peak_used_bytes',0),value['used_bytes'])
    return value


def verify_queue_receipts(queue,lease,receipts):
    """Compare current files with the durable seed/hash receipts and settlement."""
    with queue._connection() as c:
        attempt=c.execute('''SELECT a.phase,a.outcome,j.state,j.attempts,j.resources,
            r.released_at FROM attempts a JOIN jobs j ON j.id=a.job_id
            JOIN reservations r ON r.attempt_id=a.id WHERE a.id=%s AND a.pool_id=%s''',
            (lease.attempt_id,queue.pool_id)).fetchone()
        rows=c.execute('''SELECT ordinal,expected_seed,actual_seed,output_fingerprint
            FROM repetitions WHERE attempt_id=%s ORDER BY ordinal''',(lease.attempt_id,)).fetchall()
        active=c.execute('''SELECT count(*) AS total FROM reservations r JOIN attempts a ON a.id=r.attempt_id
            WHERE a.pool_id=%s AND r.released_at IS NULL''',(queue.pool_id,)).fetchone()['total']
    if (not attempt or attempt['phase']!='finished' or attempt['outcome']!='success'
            or attempt['state']!='succeeded' or attempt['attempts']!=1 or attempt['resources']!=lease.resources
            or attempt['released_at'] is None or active!=0 or len(rows)!=len(receipts)):
        raise RuntimeError('Original attempt or frozen resource reservation is not settled')
    for ordinal,(row,receipt) in enumerate(zip(rows,receipts)):
        if (row['ordinal']!=ordinal or row['expected_seed']!=lease.specification['seeds'][ordinal]
                or row['actual_seed']!=receipt['seed'] or row['expected_seed']!=receipt['seed']
                or row['output_fingerprint']!=receipt['fingerprint']):
            raise RuntimeError('Stored repetition receipts differ from verified output')
    return dict(attempts=1,reservations_released=True,repetitions=receipts)


def verify_container(executor,lease,image,guard):
    from jasmine_web.batch.local_executor import read_json
    from jasmine_web.batch.workspace_quota import BACKEND
    path=executor.workspace(lease)
    observed=executor.inspect(lease)
    policy=read_json(path/'container-policy.json')
    identifier=read_json(path/'container.json')['id']
    if (observed.get('state')!='stopped' or observed.get('outcome')!='success'
            or observed.get('returncode')!=0 or observed.get('container_id')!=identifier
            or policy['image']!=image or policy['resources']!=lease.resources
            or (guard is not None and (policy.get('workspace_quota')!=BACKEND or policy.get('workspace_guard')!=guard))):
        raise RuntimeError('Successful container does not match the frozen execution policy')
    return identifier


def verify_removal(executor,lease,identifier):
    from jasmine_web.batch.local_executor import read_json
    if (read_json(executor.workspace(lease)/'removed.json')!={'id':identifier}
            or executor.docker.inspect(identifier) is not None):
        raise RuntimeError('Original container removal is unconfirmed')


def configuration(receipt,repetitions):
    original=proof_configuration()
    data=original.editable_configuration()
    data.pop('sweep',None)
    data['dataset_revision']=receipt['revision']
    data['common'].update(population=50000,start_year=2019,end_year=2026)
    data['seed_plan']['repetitions']=repetitions
    data['run_sets']=original.as_dict()['run_sets'][:1]
    return normalise(data).editable_configuration()


def sample(path):
    """Use the executor's allocated-byte measure; read no CSV or input contents."""
    from jasmine_web.batch.docker_executor import ordinary_tree_bytes
    def size(directory):
        return ordinary_tree_bytes(directory) if directory.is_dir() else 0
    work=path/'work'; output=work/'output'
    try:
        total=size(work)
        inputs=size(work/'input')
        csvs=sum(size(p) for p in output.glob('*/csv'))
        snapshots=sum(size(p) for p in output.glob('*/input'))
        request=size(path/'request')
    except FileNotFoundError:
        return None  # A temporary file disappeared during this read-only sample.
    return dict(work_bytes=total,input_bytes=inputs,csv_bytes=csvs,
                snapshot_bytes=snapshots,other_bytes=max(0,total-inputs-csvs-snapshots),
                request_bytes=request,work_and_request_bytes=total+request)


def run_summary(path):
    from jasmine_web.batch.docker_executor import ordinary_tree_bytes
    result=[]
    for option in sorted((path/'work/output').glob('*/input/options.txt')):
        fields=dict(line.split(': ',1) for line in option.read_text().splitlines() if ': ' in line)
        files=[p for p in option.parent.rglob('*') if p.is_file() and p!=option]
        result.append(dict(seed=fields['randomSeedIfFixed'],csv_bytes=ordinary_tree_bytes(option.parent.parent/'csv'),
            copied_input_bytes=sum(p.stat().st_size for p in files),options=fingerprint(option)))
    return sorted(result,key=lambda item:int(item['seed']))


def execute(output):
    from jasmine_web.batch.docker_executor import DockerExecutor
    from jasmine_web.batch.policy import Policy, Resources, RuntimeAllowance
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.worker import Worker
    from psycopg import sql

    output.mkdir(mode=0o700,parents=True); write_attribution(output)
    report=dict(passed=False,profile=dict(population=50000,start_year=2019,end_year=2026),
        production_acceptance=False,
        measurement='file size/allocation estimate sampled once per second; kernel accounting recorded separately when enforced',cases=[])
    prepared=Path(os.environ['SIMPATHS_STORAGE_PREPARED']).resolve(strict=True)
    receipt=prepared_profile(prepared); verify_snapshot(prepared,receipt)
    report.update(prepared_fingerprint=receipt['sha256'],input_copy_minimum_bytes=workspace_required_bytes(receipt['identity']))
    receipt_bytes=(prepared/'receipt.json').read_bytes()
    settings=native_fixture(prepared,receipt)
    profile=checked_calibration(settings['calibration']) if settings else checked_calibration(
        json.loads(os.environ.get('SIMPATHS_STORAGE_CALIBRATION',json.dumps(calibration()))))
    policy=calibration_policy(profile)
    report.update(calibration=profile,policy=policy)
    work=Path(settings['source']) if settings else Path(tempfile.mkdtemp(prefix='simpaths-storage-proof-'))
    queue=Queue(os.environ['JASMINE_BATCH_TEST_DSN'],'storage-proof',schema='storage_proof_'+uuid4().hex)
    image=receipt['identity']['source_image']
    report['runtime_image_id']=image
    quotas=None; guard=None
    if settings:
        from jasmine_web.batch.workspace_quota import WorkspaceQuotas
        quotas=WorkspaceQuotas(work/'execution',settings['source_socket'],settings['guard'])
    report['enforcement']='xfs-project-v1' if quotas else 'application-monitoring'
    report['source_sha256']={name:fingerprint(Path(__file__).with_name(name))['sha256'] for name in
        ('storage_proof.py','container_adapter.py','container_run.sh','queue_adapter.py','resource_policy.py')}
    report['worker_sha256']=fingerprint(Path(sys.modules[Worker.__module__].__file__))['sha256']
    executor=DockerExecutor(work/'execution',approved_images=[image],input_roots=[prepared],workspace_quotas=quotas)
    adapter=SimPathsContainerAdapter(prepared,image,resource_policy=policy)
    worker=Worker(queue,executor,adapter,'storage-measurer')
    known={}; safe_cleanup=True; migrated=False
    try:
        if quotas:
            guard=quotas.guard(); report['workspace_guard']=guard
            report['quota_probe']=quotas.preflight()
            if quotas.ready()['remaining_reserved_bytes']!=0: raise RuntimeError('Native fixture already has active allocations')
            print('PASS: native quota write probe proves enforcement; original guard and empty physical reservations verified',flush=True)
        queue.migrate(); migrated=True
        maximum=Resources(**allocation(receipt,repetitions=max(profile['repetitions']),resource_policy=policy))
        if maximum.storage_mib!=profile['maximum_storage_mib']:
            raise ArtifactError('Allocation differs from the verified proof calibration')
        allowance=RuntimeAllowance(900,3600).describe(max(profile['repetitions']),Policy())
        queue.create_pool(maximum,policy=Policy(per_user_active=1,attempt_seconds=allowance['attempt_seconds'],
                                               total_seconds=allowance['total_seconds']))
        queue.approve('storage-proof')
        queue.register_dataset(receipt['revision'],receipt['sha256'])
        queue.grant_dataset('storage-proof',receipt['revision'])
        until=time.monotonic()+profile['proof_timeout_seconds']-600  # Leave ten minutes for final cleanup.
        safe_cleanup=False
        with worker.open():
            try:
                for repetitions in profile['repetitions']:
                    resources=Resources(**allocation(receipt,repetitions=repetitions,resource_policy=policy))
                    args=container_submission(configuration(receipt,repetitions),prepared,image)
                    exp=queue.submit('storage-proof','measure-'+str(repetitions),**args,resources=resources,auto_retry=False)
                    case=dict(repetitions=repetitions,allowance_mib=resources.storage_mib,samples=0,peak={})
                    report['cases'].append(case)
                    print(f'Measuring {repetitions} repetition(s), 50,000 people, 2019–2026; allowance {resources.storage_mib/1024:.2f} GiB',flush=True)
                    started=time.monotonic(); next_progress=started+30; next_sample=started
                    lease=None
                    while True:
                        known.update(worker.leases)
                        worker.tick()
                        known.update(worker.leases)
                        if lease is None:
                            lease=next((value for value in known.values() if value.specification['dataset_id']==receipt['revision']
                                        and len(value.specification['seeds'])==repetitions),None)
                        if lease is not None and time.monotonic()>=next_sample:
                            measured=sample(executor.workspace(lease))
                            if measured:
                                case['samples']+=1
                                for key,value in measured.items():
                                    case['peak'][key]=max(case['peak'].get(key,0),value)
                                if quotas: quota_sample(quotas,lease,case)
                            next_sample=time.monotonic()+1
                        status=queue.inspect('storage-proof',exp)['jobs'][0]
                        if status['state']=='succeeded': break
                        if status['state'] in ('review','cancelled','expired','blocked'):
                            case.update(state=status['state'],history=status['history'])
                            raise RuntimeError('Storage calibration did not finish; inspect its private diagnostics')
                        if time.monotonic()>until: raise TimeoutError('Storage calibration exceeded its proof time limit')
                        if time.monotonic()>=next_progress:
                            print(f"  {repetitions} repetition(s): {status['state']}; sampled peak {case['peak'].get('work_bytes',0)/1024**3:.2f} GiB",flush=True)
                            next_progress=time.monotonic()+30
                        time.sleep(.5)
                    if lease is None: raise RuntimeError('Completed job has no original lease')
                    path=executor.workspace(lease)
                    identifier=verify_container(executor,lease,image,guard)
                    before=adapter.validate(lease,path/'work')
                    case['verified_completion']=verify_queue_receipts(queue,lease,before)
                    case['verified_annual_years']=list(range(2019,2027))
                    case['verified_annual_csvs']=['Person.csv','BenefitUnit.csv']
                    if quotas: case['quota_before_cleanup']=quota_sample(quotas,lease,case)
                    case.update(seconds=round(time.monotonic()-started,2),runs=run_summary(path),attempts=status['attempts'])
                    if (case['attempts']!=1 or len(case['runs'])!=repetitions
                            or [r['seed'] for r in case['runs']]!=lease.specification['seeds']
                            or [r['seed'] for r in case['runs'] if r['copied_input_bytes']]!=['606']):
                        raise RuntimeError('Unexpected repetition or input-snapshot structure')
                    if not case['samples'] or case['peak']['work_and_request_bytes']>resources.storage_mib*MIB:
                        raise RuntimeError('Sampled workspace exceeds its scaled allowance')
                    retire_finished(queue,executor)  # Exercise the production successful-attempt cleanup.
                    verify_removal(executor,lease,identifier)
                    if adapter.validate(lease,path/'work')!=before:
                        raise RuntimeError('Cleanup changed verified CSV output or options')
                    if (path/'work/input').exists() or any(r['copied_input_bytes'] for r in run_summary(path)):
                        raise RuntimeError('Cleanup retained large input copies')
                    if quotas:
                        case['quota_after_cleanup']=quota_sample(quotas,lease,case)
                        if quotas.ready()['remaining_reserved_bytes']!=0: raise RuntimeError('Unused physical allocation was not released')
                        case['quota_headroom_bytes']=resources.storage_mib*MIB-case['quota_peak_used_bytes']
                    case['cleanup_preserves_options_and_verified_csv']=True
                    case['container_removed']=True
                    case['headroom_bytes']=resources.storage_mib*MIB-case['peak']['work_and_request_bytes']
                    diagnostics=output/('repetitions-'+str(repetitions)); diagnostics.mkdir(mode=0o700)
                    for name in ('execution.log','exit.json','container-policy.json','payload-retired.json','removed.json'):
                        if (path/name).is_file(): shutil.copy2(path/name,diagnostics/name)
                    shutil.rmtree(path)  # Confirmed stopped/removed above; free this proof's output before the next case.
                    physical=(f"; kernel peak {case['quota_peak_used_bytes']/1024**3:.2f} GiB, hard limit {resources.storage_mib/1024:.2f} GiB"
                        if quotas else '')
                    print(f"PASS: {repetitions} repetition(s); sampled workspace peak {case['peak']['work_bytes']/1024**3:.2f} GiB{physical}; inputs reclaimed, options/output verified, container and capacity released",flush=True)
            finally:
                known.update(worker.leases)
                stopped=True
                for lease in known.values():
                    if not executor.workspace(lease).exists(): continue
                    try:
                        executor.stop(lease,'cancelled')
                        deadline=time.monotonic()+30
                        while executor.inspect(lease)['state']=='running' and time.monotonic()<deadline:
                            time.sleep(.2)
                        if executor.inspect(lease)['state']!='stopped': stopped=False
                        else: executor.cleanup(lease)
                    except Exception: stopped=False
                safe_cleanup=stopped
        verify_snapshot(prepared,receipt)
        if (prepared/'receipt.json').read_bytes()!=receipt_bytes:
            raise RuntimeError('Prepared source receipt changed')
        report.update(passed=True,source_unchanged=True)
    except BaseException as error:
        report['error_type']=type(error).__name__
        raise
    finally:
        for path in (work/'execution').glob('batch-*'):
            diagnostics=output/'diagnostics'/path.name; diagnostics.mkdir(mode=0o700,parents=True,exist_ok=True)
            for name in ('execution.log','exit.json','container-policy.json'):
                if (path/name).is_file(): shutil.copy2(path/name,diagnostics/name)
        if safe_cleanup:
            if settings:
                for lease in known.values():
                    path=executor.workspace(lease)
                    if path.exists(): shutil.rmtree(path)
            else: shutil.rmtree(work)
            if migrated:
                with queue._connection() as c:
                    c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(queue.schema)))
        report['workspaces_cleaned']=safe_cleanup and (not any((work/'execution').glob('batch-*')) if settings else not work.exists())
        report['cleanup']=report['workspaces_cleaned']
        if not report['workspaces_cleaned']: report['retained_workspace']=str(work)
        write_json(output/'report.json',report)
    if not report['passed'] or not report['workspaces_cleaned']:
        raise RuntimeError('Storage proof did not finish with confirmed cleanup')


def main():
    parser=argparse.ArgumentParser(description='Measure public full-length MultiRun working storage; one model at a time.')
    parser.add_argument('--frontend',type=Path)
    parser.add_argument('--prepared',type=Path)
    parser.add_argument('--repetitions',type=int,nargs='+',default=[1,3],help='Ordered calibration cases; at most 24 runs total')
    parser.add_argument('--storage-per-repetition-mib',type=int,default=256,
        help='Proof-only allowance with 4 GiB setup; registered release policies are unchanged')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--check-prepared',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    try:profile=calibration(args.repetitions,args.storage_per_repetition_mib)
    except ArtifactError as error:parser.error(str(error))
    if args.check_prepared:
        if not args.prepared: parser.error('--prepared is required for the read-only check')
        print(json.dumps(check_prepared(args.prepared.resolve(strict=True),profile),sort_keys=True)); return 0
    if not args.output: parser.error('--output is required')
    if args.execute_proof:
        if not os.environ.get('JASMINE_BATCH_TEST_DSN') or not os.environ.get('SIMPATHS_STORAGE_PREPARED'):
            parser.error('Use the wrapper with disposable PostgreSQL and prepared public inputs')
        execute(args.output); print('PASSED: '+str(args.output/'report.json'),flush=True); return 0
    if not args.frontend or not args.prepared: parser.error('Supply --frontend and --prepared')
    prepared=args.prepared.expanduser().resolve(strict=True); check_prepared(prepared,profile)
    if args.output.exists(): parser.error('Choose new evidence output')
    required=profile['maximum_storage_mib']*MIB+2*1024**3
    if shutil.disk_usage(tempfile.gettempdir()).free<required:
        parser.error(f'Need at least {required/1024**3:.2f} GiB free on the temporary-work filesystem')
    command=[sys.executable,str(args.frontend.resolve(strict=True)/'scripts/test_batch_queue.py'),
        '--test-pattern','test_storage.py','test_worker.py','--proof-script',str(Path(__file__).resolve()),
        '--proof-timeout-seconds',str(profile['proof_timeout_seconds']),
        '--proof-requirements',str(Path(__file__).with_name('requirements.txt')),'--output',str(args.output.resolve())]
    return subprocess.call(command,env=dict(os.environ,SIMPATHS_STORAGE_PREPARED=str(prepared),
        SIMPATHS_STORAGE_CALIBRATION=json.dumps(profile)))


if __name__=='__main__': raise SystemExit(main())
