#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Real PostgreSQL stop/crash recovery with restricted SingleRun/MultiRun accounts.
Uses owned containers/volumes, fictional models and transient user services only.
@author ross richardson
"""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy._workflow import frontend_path
from deploy.acceptance._postgres_outage import Database
from deploy.acceptance._supervisor import Supervisor,environment_file,restart_policy
from deploy.acceptance.run_recovery_rehearsal import (allocations,attempt,running_attempt,remove_containers,
    single_client,sign_in,start_frontends,submit,wait_ready)
from deploy.multirun.proxy_rehearsal import free_port,require_test_database,until


def archive_hashes(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert len(names)==len(set(names)) and names
        return {name:hashlib.sha256(archive.read(name)).hexdigest() for name in names if not name.endswith('/')}


def failed_response(response, secrets, *, statuses=(503,)):
    status,headers,body = response
    assert status in statuses, 'Database outage unexpectedly allowed a protected request'
    headers = {key.lower():value for key,value in headers.items()}
    payload = body+'\n'.join(headers.values()).encode()
    assert all(value.encode() not in payload for value in secrets if value), 'Private data appeared in an outage response'
    assert 'set-cookie' not in headers, 'An outage changed the browser ownership cookie'
    return status


def recovered_record(store, sid):
    from jasmine_web.vm_state import VMStateUnavailable
    try:
        return store.peek_session(sid)
    except VMStateUnavailable:
        return False  # The proof's own idle pool also needs to replace dead connections.


def model_containers(settings):
    from jasmine_web.batch.docker_executor import DockerCLI
    docker = DockerCLI()
    values = set()
    for label in ('simpaths.recovery='+settings['tag'],'jasmine.batch.identity'):
        for identifier in docker.call('container','ls','-aq','--filter','label='+label).splitlines():
            row = docker.inspect(identifier)
            if row:
                # Batch identities below are restricted to this proof's workspace;
                # do not include another local rehearsal's same-named pool.
                if label.startswith('jasmine.batch') and not any(
                        Path(m.get('Source','/')).is_relative_to(Path(settings['state'])/'execution')
                        for m in row['Mounts'] if m['Type']=='bind'):
                    continue
                values.add(row['Id'])
    return values


def outage_probes(clients, sid, job, active_job, dataset, signed_submission, denied_secrets):
    single,other,alice,bob,anonymous = clients
    checks = []
    review = dict(dataset=dataset,form=dict(name='Valid review during outage',
        common=dict(population=20,start_year=2019,end_year=2020),repetitions=2,first_seed='606',
        baseline='first',auto_retry=True,run_sets=[dict(id='first',name='Fictional alternative',model_args={})]))
    for client,path,value in ((single,'/status/'+sid,None),(single,'/charts/'+sid,None),
        (single,'/logs/'+sid,None),(single,'/pause/'+sid,{}),(single,'/start-sim/'+sid,{}),
        (single,'/reset/'+sid,{}),(single,'/leave/'+sid,{}),
        (single,'/java/'+sid+'/simulation/export/zip',None),(other,'/status/'+sid,None),
        (other,'/reset/'+sid,{}),(alice,'/api/session',None),(alice,'/downloads/'+job,None),
        (bob,'/downloads/'+job,None),(anonymous,'/downloads/'+job,None),
        (alice,'/api/review-experiment',review),(alice,'/api/submit',signed_submission),
        (alice,'/api/jobs/'+active_job,dict(action='cancel',enabled=True))):
        started = time.monotonic()
        # A malformed form or nonexistent route must not count as a safe denial.
        statuses = (403,503) if client is anonymous or client is bob else (503,)
        status = failed_response(client.request(path,value=value),denied_secrets,statuses=statuses)
        checks.append(dict(route=path.split('/')[1],status=status,seconds=round(time.monotonic()-started,3)))
    # Launch may use the established controlled catalogue error redirect.
    response = other.form('/launch',dict(model_key='fictional-recovery'))
    status = failed_response(response,
                             denied_secrets,statuses=(303,503))
    if status == 303:
        assert response[1]['Location'].startswith('/?error='), 'Outage launch redirected to a new model'
    checks.append(dict(route='launch',status=status))
    return checks


def rehearsal(args,report):
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.docker_executor import DockerCLI
    from jasmine_web.batch.local_executor import atomic_json
    from jasmine_web.vm_state import PostgresVMState
    from deploy.acceptance._recovery_fixture import MODEL,POOL,bootstrap
    from deploy.acceptance._https_fixture import create_archive
    from deploy.multirun.artifacts import write_attribution
    from deploy.multirun.mail_rehearsal import LocalClient
    work = Path(tempfile.mkdtemp(prefix='simpaths-postgres-rehearsal-')); write_attribution(work)
    state = work/'private'; state.mkdir(mode=0o700)
    single_state,inputs = state/'single',state/'inputs'
    single_state.mkdir(mode=0o700); inputs.mkdir(mode=0o700)
    tag = uuid4().hex
    supervisor = Supervisor(tag,args.output/'supervisor.log')
    database = None; settings = store = None
    report.update(checks=[],phase='preflight')
    sources = {name:ROOT/name for name in (
        'deploy/acceptance/_postgres_outage.py','deploy/acceptance/run_postgres_rehearsal.py',
        'deploy/acceptance/test_postgres_rehearsal.py','deploy/acceptance/_recovery_fixture.py',
        'deploy/acceptance/_recovery_model.py','deploy/acceptance/_supervisor.py',
        'deploy/multirun/runtime.py','deploy/multirun/vm/simpaths-multirun.service')}
    sources.update({'JAS-mine-web/'+name:args.frontend/name for name in (
        'app.py','session_manager.py','jasmine_web/vm_state.py','jasmine_web/vm_resources.py',
        'jasmine_web/batch/worker.py','jasmine_web/batch/store.py','jasmine_web/batch/browser.py',
        'deploy/simpaths/simpaths-singlerun.service')})
    report['adaptations'] = ['fictional Java HTTP responses and tiny real Docker model processes',
        'loopback HTTP; HTTPS and production cookie flags have separate rehearsals',
        'five-second queue leases and one-second retry delay; frozen runtime and attempt limits retained',
        'transient user units; installed-host hardening and boot behaviour are separate acceptance',
        'a second disposable PostgreSQL with a private persistent volume; enclosing runner supplies isolated dependencies']
    def passed(text):
        report['checks'].append(text); print('PASS: '+text,flush=True)
    try:
        report['source_hashes'] = {name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in sources.items()}
        supervisor.probe()
        if shutil.disk_usage(work).free < 2*1024**3: raise RuntimeError('Need 2 GiB free for the disposable rehearsal')
        database = Database(tag,free_port()); database.start()
        report['database_image'] = database.image
        report['restricted_account'] = database.prepare_role()
        dsn = database.dsn()
        image = json.loads(DockerCLI().call('image','inspect','python:3.12-slim'))[0]['Id']
        ports = {database.port}
        def port():
            while (number:=free_port()) in ports: pass
            ports.add(number); return number
        settings = dict(tag=tag,state=str(state),single_state=str(single_state),inputs=str(inputs),
            schema='test_recovery_'+uuid4().hex,batch_schema='test_recovery_'+uuid4().hex,image=image,
            single_port=port(),batch_port=port(),secret=secrets.token_urlsafe(32),admin=secrets.token_urlsafe(32),
            catalogue=str(single_state/'catalogue.json'),archive=str(single_state/'fictional.zip'),
            database_role=database.role,database_port=database.port)
        model = deepcopy(MODEL); model['deployment']['image'] = image
        atomic_json(Path(settings['catalogue']),dict(models=[model])); create_archive(Path(settings['archive']),1)
        report['phase'] = 'restricted-account-bootstrap'
        dataset,owners = bootstrap(settings,dsn)
        queue = Queue(dsn,POOL,schema=settings['batch_schema'])
        store = PostgresVMState(dsn,schema=settings['schema'],shared_pool=dict(pool_id=POOL,schema=settings['batch_schema']))
        store.migrate()
        settings_file = work/'settings.json'; atomic_json(settings_file,settings)
        environment = work/'private.env'
        environment_file(environment,dict(JASMINE_BATCH_TEST_DSN=dsn,JASMINE_WEB_REPO=str(args.frontend),
            PYTHONPATH=str(ROOT),PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1'))
        policies = {role:restart_policy(path.read_text()) for role,path in dict(
            single=args.frontend/'deploy/simpaths/simpaths-singlerun.service',
            batch=ROOT/'deploy/multirun/vm/simpaths-multirun.service').items()}
        report['phase'] = 'real-frontend-startup'
        start_frontends(supervisor,policies,dict(python=sys.executable,script=Path(__file__).resolve(),
            settings=settings_file,environment=environment,workdir=ROOT),args.frontend,args.output)
        single_origin='http://127.0.0.1:'+str(settings['single_port'])
        batch_origin='http://127.0.0.1:'+str(settings['batch_port'])
        single,other = single_client(single_origin),single_client(single_origin)
        alice,bob,anonymous = [LocalClient(batch_origin) for _ in range(3)]
        wait_ready(single,'/',supervisor=supervisor,role='single')
        wait_ready(alice,'/healthz',supervisor=supervisor,role='batch')
        sign_in(alice,state,'alice@example.org'); sign_in(bob,state,'bob@example.org')
        passed('restricted database owner migrates both schemas and serves real SingleRun/MultiRun routes; administrator role and private administrator data are denied')

        report['phase'] = 'retained-results-and-running-models'
        completed_exp = submit(alice,dataset,'Retained result before database interruption')
        first = until(lambda: running_attempt(queue,settings,completed_exp,owners['alice@example.org']),seconds=45)
        (first['path']/'work/finish').write_text('finish fictional model\n')
        until(lambda: queue.inspect(owners['alice@example.org'],completed_exp)['jobs'][0]['state']=='succeeded',seconds=45)
        completed_job = str(first['job']['id'])
        response = alice.request('/downloads/'+completed_job); assert response[0]==200
        batch_archive = archive_hashes(response[2])
        response = single.form('/launch',dict(model_key=MODEL['id'])); assert response[0]==303
        sid = response[1]['Location'].rsplit('/',1)[-1]
        until(lambda: (store.peek_session(sid) or {}).get('status')=='ready',seconds=30)
        assert single.api('/build-json/'+sid,dict(seed=607,endYear=2026))['status']=='building'
        until(lambda: single.api('/status/'+sid).get('built'),seconds=30)
        assert single.api('/start-sim/'+sid,{})['status']=='started'
        assert single.request('/charts/'+sid)[0]==200
        record = store.peek_session(sid)
        single_container = record['container_id']
        response = single.request('/java/'+sid+'/simulation/export/zip'); assert response[0]==200
        single_archive = hashlib.sha256(response[2]).hexdigest()
        active_exp = submit(alice,dataset,'Batch model across database outages')
        active = until(lambda: running_attempt(queue,settings,active_exp,owners['alice@example.org']),seconds=45)
        queued_exp = submit(bob,dataset,'Queued second owner')
        assert queue.inspect(owners['bob@example.org'],queued_exp)['jobs'][0]['attempts']==0
        form = dict(name='Unsubmitted outage review',common=dict(population=20,start_year=2019,end_year=2020),
            repetitions=2,first_seed='606',baseline='first',auto_retry=True,
            run_sets=[dict(id='first',name='Fictional alternative',model_args={})])
        signed_submission = dict(key=uuid4().hex,review=alice.api('/api/review-experiment',dict(dataset=dataset,form=form))['review'])
        with queue._connection() as c:
            original_experiments = c.execute('SELECT id FROM experiments ORDER BY id').fetchall()
        held = allocations(queue); frozen = active['specification']; deadline = active['attempt']['deadline']
        cookies = (single.cookie,alice.cookie,bob.cookie)
        input_hash = hashlib.sha256((inputs/'input.txt').read_bytes()).hexdigest()
        denied_secrets = [database.password,database.app_password,dsn,str(work),settings['secret'],settings['admin'],
                          record['backend_secret'],record['reset_secret'],*cookies,'FICTIONAL_ADMIN_ONLY','FICTIONAL_SINGLE_RUN_PRIVATE_RECORD']
        passed('completed downloads coexist with running interactive/batch containers and queued work from a second owner in the shared pool')

        report['outages'] = []
        for crash in (False,True):
            report['phase'] = 'abrupt-database-crash' if crash else 'orderly-database-stop'
            old_pid = database.checked_container().attrs['State']['Pid']
            interrupted_at = time.monotonic()
            database.stop(crash=crash)
            assert not database.ready()
            containers_before = model_containers(settings)
            workspaces_before = set((state/'execution').iterdir())
            before_file = (single_state/('model-'+sid+'.json')).read_bytes()
            probes = outage_probes((single,other,alice,bob,anonymous),sid,completed_job,
                str(active['job']['id']),dataset,signed_submission,denied_secrets)
            assert (single_state/('model-'+sid+'.json')).read_bytes()==before_file, 'Rejected outage controls reached the model'
            assert (single.cookie,alice.cookie,bob.cookie)==cookies
            assert DockerCLI().inspect(single_container)['State']['Running']
            assert DockerCLI().inspect(active['container'])['State']['Running']
            assert (active['path']/'work/starts.txt').read_text()=='started\n'
            assert model_containers(settings)==containers_before, 'An outage admitted another model container'
            assert set((state/'execution').iterdir())==workspaces_before, 'An outage created another execution workspace'
            completed_during_outage = False
            if crash:
                (active['path']/'work/finish').write_text('finish while PostgreSQL is unavailable\n')
                until(lambda: not DockerCLI().inspect(active['container'])['State']['Running'],seconds=20)
                completed_during_outage = True
            report['phase'] = 'database-restart-and-reconciliation'
            restart_at = time.monotonic()
            database.restart()
            assert database.checked_container().attrs['State']['Pid']!=old_pid
            wait_ready(alice,'/healthz',supervisor=supervisor,role='batch')
            until(lambda: single.request('/status/'+sid)[0]==200,seconds=30)
            restored = until(lambda: recovered_record(store,sid),seconds=30)
            assert all(restored[k]==record[k] for k in ('container_id','backend_secret','reset_secret','model_id'))
            with queue._connection() as c:
                assert c.execute('SELECT id FROM experiments ORDER BY id').fetchall()==original_experiments
            if not crash:
                recovered = until(lambda: running_attempt(queue,settings,active_exp,owners['alice@example.org']),seconds=45)
                assert recovered['container']==active['container'] and recovered['attempt']['id']==active['attempt']['id']
                assert recovered['specification']==frozen and recovered['attempt']['deadline']==deadline
                assert recovered['job']['attempts']==1 and allocations(queue)==held
                assert recovered['job']['spent_seconds']>=active['job']['spent_seconds']
                assert queue.inspect(owners['bob@example.org'],queued_exp)['jobs'][0]['attempts']==0
            else:
                until(lambda: queue.inspect(owners['alice@example.org'],active_exp)['jobs'][0]['state']=='succeeded',seconds=45)
                job,row,spec = attempt(queue,active_exp,owners['alice@example.org'])
                assert job['attempts']==1 and row['id']==active['attempt']['id'] and spec==frozen
                assert row['deadline']==deadline and job['spent_seconds']>=active['job']['spent_seconds']
            if not crash:
                report['phase'] = 'post-stop-single-controls'
                assert single.api('/pause/'+sid,{})['status']=='paused'
                assert single.api('/start-sim/'+sid,{})['status']=='started'
            report['outages'].append(dict(crash=crash,probes=probes,completed_during_outage=completed_during_outage,
                attempt_count=1,original_cookie_preserved=True,new_models_denied=True,
                database_volume_preserved=True,specification_and_deadline_preserved=True,
                outage_seconds=round(restart_at-interrupted_at,3),
                recovery_seconds=round(time.monotonic()-restart_at,3)))
            passed(('abrupt PostgreSQL crash' if crash else 'orderly PostgreSQL stop')+
                ' denies protected reads/controls and new launches; restart preserves ownership and reconciles the original attempt without duplicate execution')
        report['phase'] = 'post-outage-permissions-and-completion'
        assert alice.api('/api/session')['signed_in'] and bob.api('/api/session')['signed_in']
        assert archive_hashes(alice.request('/downloads/'+completed_job)[2])==batch_archive
        response = single.request('/java/'+sid+'/simulation/export/zip')
        assert response[0]==200 and hashlib.sha256(response[2]).hexdigest()==single_archive
        assert bob.request('/downloads/'+completed_job)[0]==403
        assert anonymous.request('/downloads/'+completed_job)[0]==403
        assert other.request('/status/'+sid)[0] in (403,404)
        assert other.request('/reset/'+sid,value={})[0] in (403,404)
        assert single.api('/java/'+sid+'/simulation/parameters')['seed']==607
        current=store.peek_session(sid)
        assert all(current[k]==record[k] for k in ('container_id','backend_secret','reset_secret','model_id'))
        assert hashlib.sha256((inputs/'input.txt').read_bytes()).hexdigest()==input_hash
        queued = until(lambda: running_attempt(queue,settings,queued_exp,owners['bob@example.org']),seconds=45)
        assert queued['job']['attempts']==1
        (queued['path']/'work/finish').write_text('finish once after recovery\n')
        until(lambda: queue.inspect(owners['bob@example.org'],queued_exp)['jobs'][0]['state']=='succeeded',seconds=45)
        for exp,owner in ((active_exp,owners['alice@example.org']),(queued_exp,owners['bob@example.org'])):
            job,row,spec=attempt(queue,exp,owner)
            assert job['attempts']==1 and (state/'execution'/row['execution_key']/'work/starts.txt').read_text()=='started\n'
            assert (state/'execution'/row['execution_key']/'work/input-sha256.txt').read_text()==input_hash
            with queue._connection() as c:
                rows=c.execute('SELECT actual_seed FROM repetitions WHERE attempt_id=%s ORDER BY ordinal',(row['id'],)).fetchall()
            assert [r['actual_seed'] for r in rows]==['606','607']
        assert single.api('/reset/'+sid,{})['status']=='reset'
        assert single.form('/leave/'+sid,{})[0]==303
        until(lambda: allocations(queue)==dict(batch=[],single=[]),seconds=30)
        assert store.peek_session(sid) is None
        report['final'] = dict(database_privileges=database.verify_role(),original_archives_preserved=True,
            original_attempts=1,queued_attempts=1,seeds=['606','607'],all_capacity_released=True)
        passed('retained archives/settings and owner denials survive both outages; queued work runs its original seeds once and Reset/Leave release only settled capacity')
        report['passed']=True
    finally:
        errors=[]
        try: supervisor.close()
        except Exception as error: errors.append('supervisor:'+type(error).__name__)
        if not errors and settings:
            try: remove_containers(settings)
            except Exception as error: errors.append('models:'+type(error).__name__)
        if store:
            try: store.close()
            except Exception as error: errors.append('pool:'+type(error).__name__)
        if database:
            try: database.logs(args.output/'postgres.log')
            except Exception as error: errors.append('database-logs:'+type(error).__name__)
            if not errors:
                try: database.close()
                except Exception as error: errors.append('database:'+type(error).__name__)
        if not errors: shutil.rmtree(work)
        report['cleanup']=not errors
        if errors: report.update(cleanup_errors=errors,retained_fixture=str(work),passed=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--output',type=Path)
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--serve-fixture',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--role',choices=('single','batch'),help=argparse.SUPPRESS)
    args=parser.parse_args(argv); os.umask(0o077)
    args.frontend=args.frontend.expanduser().resolve(strict=True)
    os.environ['JASMINE_WEB_REPO']=str(args.frontend)
    sys.path[:0]=[str(args.frontend),str(args.frontend/'tests/batch')]
    if args.serve_fixture:
        from deploy.acceptance._recovery_fixture import serve_batch,serve_single
        settings=json.loads(args.serve_fixture.read_text())
        {'batch':serve_batch,'single':serve_single}[args.role](settings,os.environ['JASMINE_BATCH_TEST_DSN'])
        return 0
    if not args.execute_proof:
        output=args.output or Path.home()/'simpaths-benchmarks'/('postgres-recovery-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        return subprocess.run([sys.executable,str(args.frontend/'scripts/test_batch_queue.py'),'--proof-only',
            '--proof-script',str(Path(__file__).resolve()),'--proof-requirements',
            str(ROOT/'deploy/multirun/requirements.txt'),str(args.frontend/'requirements-vm.txt'),'--output',str(output)]).returncode
    if not args.output or not os.environ.get('JASMINE_BATCH_TEST_DSN'): parser.error('Run without --execute-proof')
    require_test_database(os.environ['JASMINE_BATCH_TEST_DSN'])
    if args.output.exists(): parser.error('Choose a new evidence directory')
    args.output.mkdir(mode=0o700,parents=True)
    from deploy.multirun.artifacts import write_attribution
    write_attribution(args.output)
    report=dict(started_at=datetime.now(timezone.utc).isoformat(),passed=False,cleanup=False,
                synthetic=True,production_acceptance=False,phase='offline-regressions')
    try:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames([
            'deploy.acceptance.test_postgres_rehearsal','deploy.acceptance.test_recovery_rehearsal']))
        report['regressions']=dict(tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),skipped=len(result.skipped))
        assert result.wasSuccessful() and not result.skipped
        print('PASS: focused database ownership, permissions and outage-response checks pass without skips',flush=True)
        rehearsal(args,report)
    except (Exception,KeyboardInterrupt,SystemExit) as error:
        report.update(passed=False,error_type=type(error).__name__)
        if isinstance(error,(AssertionError,RuntimeError)): report['message']=str(error)[:500]
        trace=error.__traceback__
        while trace and trace.tb_next: trace=trace.tb_next
        if trace: report['failure_location']=dict(file=Path(trace.tb_frame.f_code.co_filename).name,line=trace.tb_lineno,function=trace.tb_frame.f_code.co_name)
        print('STOPPED: '+type(error).__name__+' during '+report['phase']+'; inspect the private reports/logs.',file=sys.stderr)
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        (args.output/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
    print(('PASSED' if report['passed'] and report['cleanup'] else 'FAILED')+': '+str(args.output/'report.json'))
    return 0 if report['passed'] and report['cleanup'] else 1


if __name__=='__main__': raise SystemExit(main())
