#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Measure full-length public MultiRun storage and verify first-run snapshot cleanup.
# @author ross richardson
import argparse
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
sys.path.insert(0,str(ROOT))
from deploy.multirun.artifacts import ArtifactError, fingerprint, write_attribution, write_json
from deploy.multirun.compare_native import proof_configuration
from deploy.multirun.configuration import normalise
from deploy.multirun.container_adapter import SimPathsContainerAdapter, container_submission
from deploy.multirun.local_web import retire_finished
from deploy.multirun.prepared_dataset import FORMAT, allocation, verify_snapshot
from deploy.multirun.queue_adapter import read_prepared, workspace_required_bytes
from deploy.multirun.resource_policy import DEFAULT_POLICY


def prepared_profile(path):
    receipt=read_prepared(path)
    if receipt['identity']['format']!=FORMAT or receipt['identity']['population']!=50000:
        raise ArtifactError('Use the verified public 50,000-person Quick Start dataset')
    return receipt


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
    except FileNotFoundError:
        return None  # A temporary file disappeared during this read-only sample.
    return dict(work_bytes=total,input_bytes=inputs,csv_bytes=csvs,
                snapshot_bytes=snapshots,other_bytes=max(0,total-inputs-csvs-snapshots))


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
        policy=DEFAULT_POLICY,measurement='allocated bytes, sampled once per second; not a physical quota',cases=[])
    prepared=Path(os.environ['SIMPATHS_STORAGE_PREPARED'])
    receipt=prepared_profile(prepared); verify_snapshot(prepared,receipt)
    report.update(prepared_fingerprint=receipt['sha256'],input_copy_minimum_bytes=workspace_required_bytes(receipt['identity']))
    receipt_bytes=(prepared/'receipt.json').read_bytes()
    work=Path(tempfile.mkdtemp(prefix='simpaths-storage-proof-'))
    queue=Queue(os.environ['JASMINE_BATCH_TEST_DSN'],'storage-proof',schema='storage_proof_'+uuid4().hex)
    image=receipt['identity']['source_image']
    report['runtime_image_id']=image
    executor=DockerExecutor(work/'execution',approved_images=[image],input_roots=[prepared])
    adapter=SimPathsContainerAdapter(prepared,image,resource_policy=DEFAULT_POLICY)
    worker=Worker(queue,executor,adapter,'storage-measurer')
    known={}; safe_cleanup=True; migrated=False
    try:
        queue.migrate(); migrated=True
        maximum=Resources(**allocation(receipt,repetitions=3,resource_policy=DEFAULT_POLICY))
        allowance=RuntimeAllowance(900,3600).describe(3,Policy())
        queue.create_pool(maximum,policy=Policy(per_user_active=1,attempt_seconds=allowance['attempt_seconds'],
                                               total_seconds=allowance['total_seconds']))
        queue.approve('storage-proof')
        queue.register_dataset(receipt['revision'],receipt['sha256'])
        queue.grant_dataset('storage-proof',receipt['revision'])
        until=time.monotonic()+90*60  # Finish cleanup before the outer proof's two-hour timeout.
        safe_cleanup=False
        with worker.open():
            try:
                for repetitions in (1,3):
                    resources=Resources(**allocation(receipt,repetitions=repetitions,resource_policy=DEFAULT_POLICY))
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
                    path=executor.workspace(lease)
                    before=adapter.validate(lease,path/'work')
                    case.update(seconds=round(time.monotonic()-started,2),runs=run_summary(path),attempts=status['attempts'])
                    if (case['attempts']!=1 or len(case['runs'])!=repetitions
                            or [r['seed'] for r in case['runs']]!=lease.specification['seeds']
                            or [r['seed'] for r in case['runs'] if r['copied_input_bytes']]!=['606']):
                        raise RuntimeError('Unexpected repetition or input-snapshot structure')
                    if not case['samples'] or case['peak']['work_bytes']>resources.storage_mib*1024**2:
                        raise RuntimeError('Sampled workspace exceeds its scaled allowance')
                    retire_finished(queue,executor)  # Exercise the production successful-attempt cleanup.
                    if adapter.validate(lease,path/'work')!=before:
                        raise RuntimeError('Cleanup changed verified CSV output or options')
                    if (path/'work/input').exists() or any(r['copied_input_bytes'] for r in run_summary(path)):
                        raise RuntimeError('Cleanup retained large input copies')
                    case['cleanup_preserves_options_and_verified_csv']=True
                    case['headroom_bytes']=resources.storage_mib*1024**2-case['peak']['work_bytes']
                    diagnostics=output/('repetitions-'+str(repetitions)); diagnostics.mkdir(mode=0o700)
                    for name in ('execution.log','exit.json','container-policy.json','payload-retired.json'):
                        if (path/name).is_file(): shutil.copy2(path/name,diagnostics/name)
                    shutil.rmtree(path)  # Confirmed stopped/removed above; free this proof's output before the next case.
                    print(f"PASS: {repetitions} repetition(s); peak {case['peak']['work_bytes']/1024**3:.2f} GiB; input copies reclaimed, options and output verified",flush=True)
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
            shutil.rmtree(work)
            if migrated:
                with queue._connection() as c:
                    c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(queue.schema)))
        report['workspaces_cleaned']=safe_cleanup and not work.exists()
        if not report['workspaces_cleaned']: report['retained_workspace']=str(work)
        write_json(output/'report.json',report)


def main():
    parser=argparse.ArgumentParser(description='Measure public full-length MultiRun working storage; one model at a time.')
    parser.add_argument('--frontend',type=Path)
    parser.add_argument('--prepared',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.execute_proof:
        if not os.environ.get('JASMINE_BATCH_TEST_DSN') or not os.environ.get('SIMPATHS_STORAGE_PREPARED'):
            parser.error('Use the wrapper with disposable PostgreSQL and prepared public inputs')
        execute(args.output); print('PASSED: '+str(args.output/'report.json'),flush=True); return 0
    if not args.frontend or not args.prepared: parser.error('Supply --frontend and --prepared')
    prepared=args.prepared.expanduser().resolve(strict=True); receipt=prepared_profile(prepared)
    if args.output.exists(): parser.error('Choose new evidence output')
    required=allocation(receipt,repetitions=3,resource_policy=DEFAULT_POLICY)['storage_mib']*1024**2+2*1024**3
    if shutil.disk_usage(tempfile.gettempdir()).free<required:
        parser.error(f'Need at least {required/1024**3:.2f} GiB free on the temporary-work filesystem')
    memory=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    if int(memory['MemAvailable'].split()[0])*1024<6*1024**3:
        parser.error('Need 6 GiB available RAM; stop local simulations first')
    image=subprocess.check_output(['docker','--host','unix:///var/run/docker.sock','image','inspect',
        receipt['identity']['source_image'],'--format','{{.Id}}'],text=True).strip()
    if image!=receipt['identity']['source_image']: parser.error('Prepared runtime image changed')
    command=[sys.executable,str(args.frontend.resolve(strict=True)/'scripts/test_batch_queue.py'),
        '--test-pattern','test_storage.py','--proof-script',str(Path(__file__).resolve()),
        '--proof-requirements',str(Path(__file__).with_name('requirements.txt')),'--output',str(args.output.resolve())]
    return subprocess.call(command,env=dict(os.environ,SIMPATHS_STORAGE_PREPARED=str(prepared)))


if __name__=='__main__': raise SystemExit(main())
