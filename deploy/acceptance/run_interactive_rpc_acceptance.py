#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Own training/user-data browser acceptance through separate native SingleRun processes.
Uses candidate images, private disposable PostgreSQL and public training inputs.
@author ross richardson
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy._workflow import frontend_path
from deploy.acceptance._single_rpc import copy_frontend,public_environment,drain_process,completed_private_evidence
from deploy.acceptance.run_browser_acceptance import free_port,remove_test_networks


def run_browser_stage(command, log):
    """Reap the owned browser driver before its private services are stopped."""
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)
    try:
        code = process.wait(timeout=12*60*60)
    except BaseException:
        if process.poll() is None:
            try:
                # The driver needs its still-running services to leave the session.
                process.send_signal(signal.SIGINT)
                process.wait(timeout=150)
            except BaseException as error:
                # Chromium/Playwright descendants share this newly owned group.
                # Never kill the wrapper's group, which also holds its services.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
                raise RuntimeError('Browser driver could not finish graceful cleanup') from error
        raise
    if code:
        raise subprocess.CalledProcessError(code, command)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--training-image')
    parser.add_argument('--user-data-image',required=True)
    parser.add_argument('--profile',choices=('both','user-data'),default='both',
                        help='Run all browser workflows, or only user-data against a previously tested training image')
    parser.add_argument('--memory-trial',action='store_true',
                        help='Isolated user-data trial: 4 GiB application heap, 5 GiB container, full runs and memory samples')
    parser.add_argument('--preparation-heap-mib',type=int,choices=(3072,4096))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.profile=='both' and not args.training_image:
        parser.error('--training-image is required for --profile both')
    if args.memory_trial != (args.preparation_heap_mib is not None) or (args.memory_trial and args.profile!='user-data'):
        parser.error('Memory trials require --profile user-data and --preparation-heap-mib')
    import docker,httpx
    from deploy.acceptance.postgres_fixture import start_postgres
    os.umask(0o077)
    args.frontend=args.frontend.expanduser().resolve(strict=True)
    args.output=args.output.expanduser().absolute()
    args.output.mkdir(parents=True,mode=0o700,exist_ok=False)
    token='simpaths-acceptance-'+secrets.token_hex(8)
    ids=[token+'-training',token+'-user-data']
    client=docker.from_env()
    models=json.loads((args.frontend/'deploy/simpaths/models.json').read_text())['models']
    chosen=[]
    for original,identifier,image in zip(('simpaths-uk-training','simpaths-uk-user-data'),ids,
                                        (args.training_image,args.user_data_image)):
        if original=='simpaths-uk-training' and args.profile=='user-data':continue
        item=next(value.copy() for value in models if value['id']==original)
        item['id']=identifier
        item['deployment']={**item['deployment'],'image':client.images.get(image).id}
        if args.memory_trial:
            item['deployment'].update(java_heap_gib=4,memory='5Gi',cpu=2)
        chosen.append(item)
    report=dict(passed=False,cleanup=False,started=datetime.now(timezone.utc).isoformat(),
                same_uid_fixture=True,host_privilege_isolation_tested=False,profile=args.profile,checks=[],
                memory_trial=args.memory_trial,preparation_heap_mib=args.preparation_heap_mib,
                images={item['id']:item['deployment']['image'] for item in chosen})
    database=public=private=None
    logs=[]
    rpc_work=Path(tempfile.mkdtemp(prefix='sr-browser-',dir='/tmp'))
    try:
        work=args.output/'frontend'
        copy_frontend(args.frontend,work)
        (work/'models.json').write_text(json.dumps({'models':chosen},indent=2))
        database,dsn=start_postgres(client,token,free_port(),work)
        port=free_port(); base='http://127.0.0.1:'+str(port)
        environment=public_environment(os.environ)
        environment.update(DEPLOY_MODE='vm',VM_STATE_BACKEND='postgres',VM_POSTGRES_DSN_FILE=str(dsn),
            VM_POSTGRES_SCHEMA='jasmine_vm',VM_SECURITY_MODE='development',COOKIE_SECURE='false',
            VM_MAX_SESSIONS='1',VM_SESSION_MEMORY_BUDGET_MIB='12288',
            ADMIN_PASSWORD=secrets.token_urlsafe(32),SESSION_SECRET=secrets.token_urlsafe(32),
            HARD_RESET_SECRET=secrets.token_urlsafe(32),PYTHONUNBUFFERED='1',PYTHON_DOTENV_DISABLED='1')
        private_settings=dict(origin=base,socket=str(rpc_work/'control.sock'),
                              evidence=str(args.output/'private-rpc.json'))
        public_settings=dict(origin=base,socket=private_settings['socket'],port=port)
        for name,settings in (('private',private_settings),('public',public_settings)):
            path=rpc_work/(name+'.json');path.write_text(json.dumps(settings));path.chmod(0o600)
        helper=work/'tests/browser/single_rpc_fixture.py'
        for role,env in (('executor',environment),('frontend',public_environment(os.environ))):
            log=(args.output/(role+'.log')).open('w');logs.append(log)
            process=subprocess.Popen([sys.executable,str(helper),'--role',role,'--settings',
                str(rpc_work/('private.json' if role=='executor' else 'public.json'))],cwd=work,
                env=env,stdout=log,stderr=subprocess.STDOUT)
            if role=='executor':private=process
            else:public=process
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            if private.poll() is not None or public.poll() is not None:
                raise RuntimeError('Separated interactive fixture exited; inspect service logs')
            try:
                if httpx.get(base+'/health',trust_env=False).status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.2)
        else:raise TimeoutError('Separated interactive fixture did not become ready')
        stages=[('training-'+mode,'run_training_browser_acceptance.py',['--prepare',mode])
                for mode in ('none','population','tax','both')]
        if args.profile=='user-data':stages=[]
        trial_options=(['--memory-trial','--trial-model-id',ids[1],
                        '--preparation-heap-mib',str(args.preparation_heap_mib)] if args.memory_trial else [])
        stages.append(('user-data','run_user_data_browser_acceptance.py',trial_options))
        for name,script,options in stages:
            print('Running '+name+' through the private boundary',flush=True)
            out=args.output/name
            with (args.output/(name+'.log')).open('w') as log:
                run_browser_stage([sys.executable,str(ROOT/'deploy/acceptance'/script),'--url',base,
                    '--output',str(out),*options],log)
            evidence=json.loads((out/'report.json').read_text())
            if evidence['status']!='passed' or evidence.get('cleanup_error'):
                raise RuntimeError(name+' did not confirm passing browser checks and cleanup')
            report['checks'].append(dict(name=name,report=str(out/'report.json'),passed=True))
        report['passed']=True
    except (Exception,KeyboardInterrupt) as error:
        report['error_type']=type(error).__name__
        print('STOPPED: '+type(error).__name__+'; inspect private acceptance logs.',file=sys.stderr)
    finally:
        errors=[]
        for process in (public,private):
            try:drain_process(process)
            except Exception as error:errors.append(type(error).__name__)
        if private is not None:
            try:report['private_rpc']=completed_private_evidence(args.output/'private-rpc.json')
            except Exception as error:errors.append(type(error).__name__)
        try:
            for identifier in ids:
                for container in client.containers.list(all=True,filters={'label':'jasmine.model_id='+identifier}):
                    if container.labels.get('jasmine.model_id')!=identifier:
                        raise RuntimeError('Model cleanup identity changed')
                    container.remove(force=True)
                remove_test_networks(client,identifier)
            if database is not None:
                database.reload()
                if database.labels.get('simpaths.acceptance')!=token:
                    raise RuntimeError('Database cleanup identity changed')
                database.remove(force=True)
        except Exception as error:errors.append(type(error).__name__)
        for log in logs:log.close()
        if not errors:
            try:shutil.rmtree(rpc_work)
            except Exception as error:errors.append(type(error).__name__)
        report['cleanup']=not errors
        if errors:report['cleanup_errors']=errors;report['passed']=False
        report['finished']=datetime.now(timezone.utc).isoformat()
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(('PASSED: ' if report['passed'] else 'FAILED: ')+str(args.output/'report.json'),flush=True)
    return 0 if report['passed'] and report['cleanup'] else 1


if __name__=='__main__':raise SystemExit(main())
