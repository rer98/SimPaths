"""(C) Copyright 2026, by Ross Richardson

Disposable upload/preparation/queue proof using public training files as uploads.
No private data, app session, source workbook edits or image rebuilds are needed.
@author ross richardson
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy.multirun.artifacts import ArtifactError, write_attribution, write_json
from deploy.multirun.dataset_service import prepare_owned, publish_provider
from deploy.multirun.import_quickstart import UnconfirmedVerification
from deploy.multirun.container_adapter import container_submission
from deploy.multirun.compare_native import proof_configuration
from deploy.multirun.run_queue_proof import execute_proof


def public_example_inputs():
    # Native tax-donor preparation also needs the model's 2015 base-price policy,
    # although the proof only simulates 2019–2020. Keep files and schedule together.
    policy_years = (2015, 2019, 2020)
    files = [ROOT/'input/InitialPopulations/training/population_initial_UK_2019.csv',
             *(ROOT/f'input/EUROMODoutput/training/uk_{year}_std.txt' for year in policy_years)]
    request = dict(year=2019, schedule=[
        [f'uk_{year}_std.txt', str(year), str(year), 'Training example'] for year in policy_years])
    return files, request


def execute(output):
    from jasmine_web.batch.datasets import Datasets
    from jasmine_web.batch.policy import Resources, NotFound
    from jasmine_web.batch.store import Queue
    from psycopg import sql
    output.mkdir(mode=0o700)
    write_attribution(output)
    work=Path(tempfile.mkdtemp(prefix='simpaths-upload-proof-'))
    queue=Queue(os.environ['JASMINE_BATCH_TEST_DSN'],'inputs',schema='proof_inputs_'+uuid4().hex)
    report=dict(passed=False,workspace=str(work),cleanup=False)
    safe=True
    try:
        queue.migrate()
        queue.create_pool(Resources(2000,5120,12288))
        queue.approve('owner')
        service=Datasets(queue,work/'uploads')
        files,request=public_example_inputs()
        ids=[]
        for path in files:
            with path.open('rb') as stream:
                ids.append(service.receive('owner',path.name,stream,expected_bytes=path.stat().st_size))
        kwargs=dict(defaults=ROOT/'input',request=request,jar=ROOT/'multirun.jar',
                    image=os.environ['SIMPATHS_INPUT_IMAGE'],frontend=Path(os.environ['SIMPATHS_INPUT_FRONTEND']))
        print('Checking rejection of an invalid population upload',flush=True)
        import io
        invalid=service.receive('owner',files[0].name,io.BytesIO(b'invalid\n1\n'),expected_bytes=10)
        try:
            prepare_owned(service,'owner',[invalid,*ids[1:]],output=work/'invalid',**kwargs)
        except ArtifactError:
            if (work/'invalid/receipt.json').exists():
                raise RuntimeError('Failed preparation published a receipt')
            report['invalid_population_rejected']=True
        else:
            raise RuntimeError('Invalid population was accepted')
        print('Preparing uploaded public examples with the existing validators',flush=True)
        if os.environ.get('SIMPATHS_QUEUED_PREPARATION') == '1':
            from deploy.multirun.queued_preparation_proof import prepare_queued
            result=prepare_queued(service,'owner',ids,output=work/'prepared',**kwargs)
            report['preparation_dispatcher_restart']=result['dispatcher_restart']
            report['preparation_temporary_payload_removed']=json.loads(
                (work/'prepared/preparation-cleanup.json').read_text())['temporary_payload_removed']
        else:
            result=prepare_owned(service,'owner',ids,output=work/'prepared',**kwargs)
        prepared=result['prepared']
        receipt=result['receipt']
        report['prepared_fingerprint']=receipt['sha256']
        # Exercise the model/platform boundary using the actual prepared receipt.
        config=proof_configuration().editable_configuration()
        config['dataset_revision']=result['dataset_id']
        args=container_submission(config,prepared,receipt['identity']['source_image'])
        experiment=queue.submit('owner','owned-example',**args,resources=Resources(2000,4096,10240))
        raw=service.register_artifact(experiment,'a'*64)
        assert service.download('owner',raw)=='a'*64
        provider=publish_provider(service,prepared)
        queue.grant_dataset('owner',provider)
        config['dataset_revision']=provider
        args=container_submission(config,prepared,receipt['identity']['source_image'])
        exp2=queue.submit('owner','provider-example',**args,resources=Resources(2000,4096,10240))
        raw2=service.register_artifact(exp2,'b'*64)
        try:
            service.download('owner',raw2)
        except NotFound:
            report['provider_raw_download_denied']=True
        else:
            raise RuntimeError('Provider artifact was exposed')
        # Native runs use a second disposable queue and the same retained original.
        os.environ['SIMPATHS_QUEUE_PREPARED']=str(prepared)
        os.environ['SIMPATHS_QUEUE_PROOF_IMAGE']=receipt['identity']['source_image']
        safe=False
        try:
            queued=execute_proof(output/'queued-runs')
        finally:
            status=output/'queued-runs/report.json'
            if status.is_file():
                safe=json.loads(status.read_text()).get('workspaces_cleaned') is True
        report['queued_runs_passed']=queued['passed']
        if not queued['workspaces_cleaned']:
            safe=False
            raise RuntimeError('Queue workspace cleanup was not confirmed')
        report['passed']=True
    except UnconfirmedVerification:
        safe=False
        raise
    finally:
        for sub in ('invalid','prepared'):
            if (work/sub).is_dir():
                evidence=output/sub
                evidence.mkdir(exist_ok=True)
                for name in ('preparation.log','compile.log','receipt.json','preparation-cleanup.json'):
                    if (work/sub/name).is_file():
                        shutil.copyfile(work/sub/name,evidence/name)
                if (work/sub/'attempt-diagnostics').is_dir():
                    shutil.copytree(work/sub/'attempt-diagnostics',evidence/'attempt-diagnostics')
        if safe:
            shutil.rmtree(work)
            with queue._connection() as c:
                c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(queue.schema)))
            report['cleanup']=True
        write_json(output/'report.json',report)
    print(f"PASSED: {output}/report.json")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path)
    parser.add_argument('--image',default='simpaths-interactive:uk-user-data')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--queued-preparation',action='store_true',help='Also prove durable preparation and dispatcher replacement')
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.execute_proof:
        execute(args.output)
        return 0
    if not args.frontend:
        parser.error('--frontend is required')
    image=subprocess.check_output(['docker','image','inspect',args.image,'--format','{{.Id}}'],text=True).strip()
    env=dict(os.environ,SIMPATHS_INPUT_IMAGE=image,SIMPATHS_INPUT_FRONTEND=str(args.frontend.resolve()),
             SIMPATHS_QUEUED_PREPARATION='1' if args.queued_preparation else '0')
    command=[sys.executable,str(args.frontend.resolve()/'scripts/test_batch_queue.py'),
        '--output',str(args.output.resolve()),'--docker-tests','--proof-script',str(Path(__file__).resolve()),
        '--proof-requirements',str(Path(__file__).with_name('requirements.txt'))]
    return subprocess.call(command,env=env)


if __name__=='__main__':
    raise SystemExit(main())
