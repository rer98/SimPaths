#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Real application update/rollback with immutable Git code and disposable services.
Fictional models exercise ownership, frozen work and verified schema recovery.
@author ross richardson
"""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
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
sys.path.insert(0, str(ROOT))
from deploy.acceptance import _application_update as releases


def service_policies(frontend):
    """Read both real service templates before validating their restart policies."""
    from deploy.acceptance._supervisor import restart_policy
    return dict(
        batch=restart_policy((ROOT/'deploy/multirun/vm/simpaths-multirun.service').read_text()),
        single=restart_policy((Path(frontend)/'deploy/simpaths/simpaths-singlerun.service').read_text()))


def serve(settings, role):
    releases.configure(settings['bundle'])
    from deploy.acceptance import _recovery_fixture as fixture
    from deploy.multirun import runtime
    from jasmine_web import vm_state
    from jasmine_web.batch import browser, store
    from jasmine_web.batch.local_executor import atomic_json
    fixture.validate(settings, os.environ['JASMINE_BATCH_TEST_DSN'])
    atomic_json(Path(settings['state'])/('boot-'+settings['generation']+'-'+role+'.json'),
        dict(pid=os.getpid(), host_commit=settings['bundle']['host']['commit'],
             frontend_commit=settings['bundle']['frontend']['commit'], sources=dict(
                 runtime=releases.loaded_source(runtime, settings['bundle']['host']['directory']),
                 browser=releases.loaded_source(browser, settings['bundle']['frontend']['directory']),
                 queue=releases.loaded_source(store, settings['bundle']['frontend']['directory']),
                 vm=releases.loaded_source(vm_state, settings['bundle']['frontend']['directory']))))
    {'batch':fixture.serve_batch, 'single':fixture.serve_single}[role](settings, os.environ['JASMINE_BATCH_TEST_DSN'])


def child(settings, *, restored_http=False, imports_only=False):
    releases.configure(settings['bundle'])
    if imports_only:
        from types import SimpleNamespace
        import httpx
        from deploy.acceptance import _recovery_fixture as fixture
        from deploy.multirun import runtime
        from jasmine_web.batch import browser
        assert fixture.FRONTEND == Path(settings['bundle']['frontend']['directory'])
        async def public_assets():
            app = browser.create_app(SimpleNamespace(),origin='http://127.0.0.1:15002')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://127.0.0.1:15002') as client:
                response = await client.get('/'); assert response.status_code == 200
                # Native FileResponse/thread transport is exercised by generation().
                # This socket-free import check reads the selected style source.
                css = Path(browser.__file__).with_name('browser_static')/'batch.css'
                return dict(help_experiment='id="help-experiment"' in response.text,css_sha256=releases.digest(css.read_bytes()),
                    sources=dict(runtime=releases.loaded_source(runtime,settings['bundle']['host']['directory']),
                                 browser=releases.loaded_source(browser,settings['bundle']['frontend']['directory'])))
        result = asyncio.run(public_assets())
    elif restored_http:
        result = restored_routes(settings, os.environ['JASMINE_BATCH_TEST_DSN'])
    else:
        result = releases.probe(settings, os.environ['JASMINE_BATCH_TEST_DSN'])
    path = releases.ordinary(settings['report'])
    with path.open('x') as stream:
        os.chmod(path, 0o600)
        json.dump(result, stream, indent=2)
    return 0


def restored_routes(settings, dsn):
    """Load the previous code's real HTTP application against the restored data."""
    assert releases.probe(settings, dsn)['accepted']  # Includes the strict disposable target guard.
    from starlette.testclient import TestClient
    from jasmine_web.batch.access import Access
    from jasmine_web.batch.browser import COOKIE, create_app
    from jasmine_web.batch.datasets import Datasets
    from jasmine_web.batch.local_executor import LocalExecutor
    from jasmine_web.batch.results import Results
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.submission_service import Submissions
    from deploy.multirun.browser_model import BrowserModel
    from deploy.multirun.queue_adapter import result_catalogue, result_name
    from deploy.multirun.releases import ReleaseRegistry
    state = releases.ordinary(settings['state'])
    async def deliver(email, code):
        mail[email] = code
    mail = {}
    queue = Queue(dsn, settings['pool'], schema=settings['batch_schema'])
    access = Access(queue, (state/'session-secret').read_text(), deliver)
    datasets = Datasets(queue, state/'uploads', reserve_bytes=1)
    executor = LocalExecutor(state/'execution'); queue.bind_executor(executor.identity())
    service = Submissions(access, datasets, BrowserModel(ReleaseRegistry(state).inventory()))
    service.results = Results(service, executor, result_catalogue, name=result_name)
    origin = 'https://restore.example.org'
    with TestClient(create_app(service, origin=origin), base_url=origin, headers={'Origin':origin}) as client:
        client.cookies.set(COOKIE, settings['authentication']['token'])
        signed = client.get('/api/session').json()
        assert signed['signed_in'] and signed['csrf'] == settings['authentication']['csrf']
        own = client.get('/downloads/'+settings['completed_job'])
        assert own.status_code == 200
        with zipfile.ZipFile(io.BytesIO(own.content)) as archive:
            assert archive.testzip() is None
            names = archive.namelist(); assert len(names) == len(set(names))
            people = [name for name in names if name.endswith('/csv/Person.csv')]
            assert len(people) == 1
            assert archive.read(people[0]) == b'run,time,id,value\nrun-606,2019,1,2\nrun-606,2020,1,3\n'
        provider = client.get('/downloads/'+settings['provider_job']); assert provider.status_code == 403
        client.cookies.clear()
        anonymous = client.get('/downloads/'+settings['completed_job']); assert anonymous.status_code in (401,403,404)
        challenge = client.post('/api/code',json={'email':'bob@example.org'}).json()['challenge']
        signed = client.post('/api/verify',json={'challenge':challenge,'code':mail['bob@example.org']})
        assert signed.json()['authorised']
        other = client.get('/downloads/'+settings['completed_job']); assert other.status_code == 403
        return dict(own_status=own.status_code,provider_status=provider.status_code,
                    anonymous_status=anonymous.status_code,other_owner_status=other.status_code,
                    original_cookie=True,original_csv_bytes=True)


def run_child(work, bundle, settings, dsn, *, name, restored_http=False, imports_only=False):
    value = dict(settings, bundle=bundle, report=str(work/(name+'-report.json')))
    source = work/(name+'-settings.json')
    source.write_text(json.dumps(value)); source.chmod(0o600)
    env = dict(os.environ, JASMINE_BATCH_TEST_DSN=dsn, PYTHONDONTWRITEBYTECODE='1')
    flag = '--import-fixture' if imports_only else ('--restored-http' if restored_http else '--probe-fixture')
    with (work/(name+'.log')).open('wb') as log:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), flag, str(source)],
            env=env, cwd=bundle['host']['directory'], stdout=log, stderr=subprocess.STDOUT, timeout=30 if imports_only else 90)
    if result.returncode:
        raise RuntimeError('Disposable version probe failed; inspect its private log')
    return json.loads(Path(value['report']).read_text())


def generation(work, output, bundle, settings, environment, policies, *, name, owned, previous=None):
    from deploy.acceptance._supervisor import Supervisor
    from deploy.acceptance.run_recovery_rehearsal import wait_ready
    from deploy.multirun.mail_rehearsal import LocalClient
    releases.verify(bundle)
    if previous:
        for role in ('single', 'batch'):
            previous.stop(role)
        for role in ('single', 'batch'):
            value = previous.show(role, allow_missing=True)
            if value and (value['ActiveState'] not in ('inactive', 'failed') or int(value['MainPID'])):
                raise RuntimeError('The previous application has not stopped; update refused')
    supervisor = Supervisor(uuid4().hex, output/'supervisor.log')
    owned.append(supervisor)  # Keep uncertain/partial startups available to final cleanup.
    selected = dict(settings, bundle=bundle, generation=name)
    source = work/(name+'-settings.json')
    source.write_text(json.dumps(selected)); source.chmod(0o600)
    # Startup itself still uses the real application schema checks and mutex.
    # The distinct owned units avoid editing installed services or live checkouts.
    for role in ('batch', 'single'):
        supervisor.start(role, policies[role], python=sys.executable, script=Path(__file__).resolve(),
            settings=source, environment=environment,
            workdir=bundle['frontend' if role=='single' else 'host']['directory'], log=output/(name+'-'+role+'.log'))
        client = LocalClient('http://127.0.0.1:'+str(settings[role+'_port']))
        wait_ready(client, '/' if role=='single' else '/healthz', supervisor=supervisor, role=role)
        stamp = json.loads((Path(settings['state'])/('boot-'+name+'-'+role+'.json')).read_text())
        assert stamp['pid'] == int(supervisor.show(role)['MainPID'])
        assert stamp['host_commit'] == bundle['host']['commit'] and stamp['frontend_commit'] == bundle['frontend']['commit']
        for key, component in (('runtime','host'), ('browser','frontend'), ('queue','frontend'), ('vm','frontend')):
            source_row = stamp['sources'][key]
            assert bundle[component]['files'][source_row['file']] == source_row['sha256']
    return supervisor


def preserved(clients, settings, queue, store, *, sid, completed_job, archives, cookies, original, active, queued, owners, held, secrets_hashes):
    from jasmine_web.batch.docker_executor import DockerCLI
    from deploy.acceptance.run_recovery_rehearsal import allocations, attempt
    from deploy.acceptance.run_postgres_rehearsal import archive_hashes
    single, other, alice, bob, anonymous = clients
    assert single.api('/status/'+sid)['built']
    assert single.request('/charts/'+sid)[0] == 200 and single.request('/logs/'+sid)[0] == 200
    assert single.api('/java/'+sid+'/simulation/parameters')['seed'] == 607
    assert other.request('/status/'+sid)[0] == 403
    assert other.request('/reset/'+sid, value={})[0] == 403
    response = single.request('/java/'+sid+'/simulation/export/zip'); assert response[0] == 200
    assert hashlib.sha256(response[2]).hexdigest() == archives['single']
    response = alice.request('/downloads/'+completed_job); assert response[0] == 200
    assert archive_hashes(response[2]) == archives['batch']
    assert bob.request('/downloads/'+completed_job)[0] == 403
    assert anonymous.request('/downloads/'+completed_job)[0] in (401,403,404)
    assert alice.api('/api/session')['signed_in'] and bob.api('/api/session')['signed_in']
    assert (single.cookie, alice.cookie, bob.cookie) == cookies
    for name, expected in secrets_hashes.items():
        path = Path(settings['state'])/'session-secret' if name=='batch' else Path(settings['single_state'])/'.sesskey'
        assert releases.digest(path.read_bytes()) == expected, 'A code change replaced persisted fixture credentials'
    record = store.peek_session(sid)
    assert all(record[key] == original[key] for key in ('container_id','backend_secret','reset_secret','model_id'))
    assert DockerCLI().inspect(record['container_id'])['State']['Running']
    job, row, specification = attempt(queue, active['experiment'], owners['alice@example.org'])
    assert job['state'] == 'active' and job['attempts'] == 1
    assert str(row['id']) == str(active['attempt']['id']) and row['deadline'] == active['attempt']['deadline']
    assert specification == active['specification']
    assert DockerCLI().inspect(active['container'])['State']['Running']
    assert (active['path']/'work/starts.txt').read_text() == 'started\n'
    assert queue.inspect(owners['bob@example.org'], queued)['jobs'][0]['attempts'] == 0
    assert allocations(queue) == held


def schema_recovery(work, bundles, report):
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from jasmine_web.batch.backup import table_inventory
    from jasmine_web.vm_state import PostgresVMState
    from deploy.multirun import backup, backup_live
    from deploy.multirun.artifacts import ArtifactError
    from deploy.multirun.maintenance import GATE, service_state
    from deploy.multirun.test_backup import BackupRestoreTests
    from types import SimpleNamespace
    fixture = BackupRestoreTests()
    vm = None
    report['backup_fixture_cleanup'] = False
    try:
        fixture.setUp()
        schema = 'vm_update_'+uuid4().hex
        shared = dict(pool_id=fixture.q.pool_id, schema=fixture.q.schema)
        vm = PostgresVMState(fixture.q.dsn, schema=schema, shared_pool=shared); vm.migrate()
        def drop_vm():
            with fixture.q._connection() as c:
                c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        fixture.addCleanup(drop_vm)
        challenge = asyncio.run(fixture.access.issue('alice@example.org', 'fictional-client'))['challenge']
        authentication = fixture.access.verify(challenge, fixture.mail['alice@example.org'], 'fictional-client')
        assert authentication['authorised']
        def empty_capture(c, dsn, vm_schema, destination, state):
            assert vm_schema == schema
            return dict(images=[], sessions=[])
        with service_state(fixture.state):
            backup_live.create(fixture.q, fixture.state, fixture.backup, fixture.tools, single_capture=empty_capture)
        manifest = backup.verify(fixture.backup)
        assert set(manifest['vm']) == {schema}
        settings = dict(database=conninfo_to_dict(fixture.q.dsn)['dbname'], pool=fixture.q.pool_id,
                        batch_schema=fixture.q.schema, single_schema=schema)
        assert run_child(work, bundles['previous'], settings, fixture.q.dsn, name='original-schema')['accepted']
        synthetic = releases.future(bundles['candidate'], work/'future-code')
        result = run_child(work, synthetic, settings, fixture.q.dsn, name='append-synthetic-migrations')
        assert result['accepted']
        def rows():
            with fixture.q._connection() as c:
                return dict(batch=table_inventory(c, fixture.q), single=table_inventory(c, SimpleNamespace(schema=schema)))
        upgraded = rows()
        refused = run_child(work, bundles['previous'], settings, fixture.q.dsn, name='refused-old-code')
        assert not refused['accepted'] and all(not row['accepted'] for row in refused['checks'])
        assert rows() == upgraded, 'An incompatible startup changed the newer database'
        restored = backup.restore(fixture.target_q, fixture.backup, fixture.target, fixture.target_tools)
        assert restored['inactive'] and (fixture.target/GATE).exists()
        target_settings = dict(settings, database=conninfo_to_dict(fixture.target_q.dsn)['dbname'])
        inactive = run_child(work, bundles['previous'], target_settings, fixture.target_q.dsn, name='inactive-recovery')
        assert not inactive['accepted'] and not inactive['checks'][1]['accepted']
        try:
            with service_state(fixture.target):
                raise AssertionError('Unverified recovered application was allowed to start')
        except ArtifactError:
            pass
        backup.activate(fixture.target_q, fixture.backup, fixture.target, image_check=lambda _:None, source_isolated=True)
        accepted = run_child(work, bundles['previous'], target_settings, fixture.target_q.dsn, name='restored-old-code')
        assert accepted['accepted'] and not (fixture.target/GATE).exists()
        assert (fixture.target/'session-secret').read_text() == fixture.secret
        http = run_child(work, bundles['previous'], dict(target_settings, state=str(fixture.target), authentication=authentication,
            completed_job=fixture.completed.job_id, provider_job=fixture.provider_completed.job_id),
            fixture.target_q.dsn, name='restored-http', restored_http=True)
        with fixture.target_q._connection() as c:
            jobs = c.execute('SELECT id,state,attempts FROM jobs ORDER BY id').fetchall()
            original = c.execute('SELECT specification,policy FROM experiments WHERE id=%s', (fixture.queued,)).fetchone()
            assert sorted(row['attempts'] for row in jobs) == [0,1,1]
        with fixture.q._connection() as c:
            assert c.execute('SELECT specification,policy FROM experiments WHERE id=%s', (fixture.queued,)).fetchone() == original
        lease = fixture.target_q.claim('verified-previous-code-recovery')
        assert lease and lease.specification['seeds'] == ['606']
        assert fixture.target_q.claim('second-recovered-worker') is None
        assert rows() == upgraded, 'Restoration changed the newer source database'
        report['schema_recovery'] = dict(synthetic_migrations=True, original_versions={k:len(v) for k,v in bundles['previous']['migrations'].items()},
            newer_versions={k:len(v) for k,v in synthetic['migrations'].items()}, refused=refused,
            newer_database_unchanged=True, backup_id=manifest['id'], native_restore=True, inactive_gate=True,
            previous_code_accepted=True, original_cookie_preserved=True, queued_seeds=['606'],
            claim_once=True, http=http)
    finally:
        errors = []
        if vm:
            try: vm.close()
            except Exception as error: errors.append(type(error).__name__)
        if not fixture.doCleanups(): errors.append('native-fixture')
        report['backup_fixture_cleanup'] = not errors
        if errors:
            raise RuntimeError('Disposable backup fixture cleanup was not confirmed')


def rehearsal(args, report):
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.docker_executor import DockerCLI
    from jasmine_web.vm_state import PostgresVMState
    from deploy.acceptance._postgres_outage import Database
    from deploy.acceptance._supervisor import Supervisor, environment_file
    from deploy.acceptance._recovery_fixture import MODEL, POOL, bootstrap
    from deploy.acceptance._https_fixture import create_archive
    from deploy.acceptance.run_recovery_rehearsal import allocations, attempt, running_attempt, remove_containers, single_client, sign_in, submit
    from deploy.acceptance.run_postgres_rehearsal import archive_hashes
    from deploy.multirun.artifacts import write_attribution
    from deploy.multirun.mail_rehearsal import LocalClient
    from deploy.multirun.proxy_rehearsal import free_port, until
    work = Path(tempfile.mkdtemp(prefix='simpaths-application-update-')); write_attribution(work)
    report.update(checks=[], phase='snapshot-preflight')
    supervisors = []; database = store = settings = None
    def passed(message):
        report['checks'].append(message); print('PASS: '+message, flush=True)
    try:
        Supervisor(uuid4().hex).probe()
        if shutil.disk_usage(work).free < 2*1024**3:
            raise RuntimeError('Need 2 GiB free for the application update rehearsal')
        bundles = {name:releases.bundle(ROOT, args.frontend, work/name, host_ref=host, frontend_ref=frontend)
                   for name,host,frontend in (('previous', args.previous_host, args.previous_frontend), ('candidate','HEAD','HEAD'))}
        releases.compatible(bundles['previous'], bundles['candidate'])
        report['versions'] = {name:dict(host=bundle['host']['commit'], frontend=bundle['frontend']['commit'],
             migrations=bundle['migrations'], fixture_overlays=bundle['fixture_overlays'],
             file_inventory={key:bundle[key]['files'] for key in ('host','frontend')}) for name,bundle in bundles.items()}
        report['adaptations'] = ['same recorded fictional-model adapters overlay each committed hosting tree',
            'loopback HTTP and transient user units; deployed HTTPS/boot hardening have separate acceptance',
            'same isolated dependency environment; no package/image/repository update or service installation',
            'compatible Git snapshots have unchanged SQL, result/cache/recovery formats; compatibility is not inferred for arbitrary releases',
            'extra checksummed batch/SingleRun migrations exist only in a private synthetic future bundle',
            'schema recovery uses an independent native backup fixture; its empty SingleRun registry needs no model export capture',
            'fictional backup model-image availability is stubbed; real image inventory was tested separately']
        passed('two real Git release bundles have matching schema/result formats; code and fixture overlays are separately verified')
        report['phase'] = 'previous-code-startup'
        tag = uuid4().hex
        database = Database(tag, free_port()); database.start(); database.prepare_role()
        dsn = database.dsn()
        state = work/'private'; state.mkdir(mode=0o700)
        single_state, inputs = state/'single', state/'inputs'
        single_state.mkdir(mode=0o700); inputs.mkdir(mode=0o700)
        ports = {database.port}
        def port():
            while (value:=free_port()) in ports: pass
            ports.add(value); return value
        image = json.loads(DockerCLI().call('image','inspect','python:3.12-slim'))[0]['Id']
        settings = dict(tag=tag,state=str(state),single_state=str(single_state),inputs=str(inputs),
            schema='test_recovery_'+uuid4().hex,batch_schema='test_recovery_'+uuid4().hex,image=image,
            single_port=port(),batch_port=port(),secret=secrets.token_urlsafe(32),admin=secrets.token_urlsafe(32),
            catalogue=str(single_state/'catalogue.json'),archive=str(single_state/'fictional.zip'),
            database_role=database.role,database_port=database.port)
        model = deepcopy(MODEL); model['deployment']['image'] = image
        Path(settings['catalogue']).write_text(json.dumps(dict(models=[model]))); create_archive(settings['archive'], 1)
        dataset, owners = bootstrap(settings, dsn)
        queue = Queue(dsn, POOL, schema=settings['batch_schema'])
        store = PostgresVMState(dsn, schema=settings['schema'], shared_pool=dict(pool_id=POOL,schema=settings['batch_schema'])); store.migrate()
        environment = work/'services.env'
        environment_file(environment, dict(JASMINE_BATCH_TEST_DSN=dsn,PYTHONDONTWRITEBYTECODE='1'))
        policies = service_policies(args.frontend)
        current = generation(work,args.output,bundles['previous'],settings,environment,policies,name='previous',owned=supervisors)
        single_origin, batch_origin = ['http://127.0.0.1:'+str(settings[k+'_port']) for k in ('single','batch')]
        single, other = single_client(single_origin), single_client(single_origin)
        alice, bob, anonymous = [LocalClient(batch_origin) for _ in range(3)]
        sign_in(alice,state,'alice@example.org'); sign_in(bob,state,'bob@example.org')
        assert b'id="help-experiment"' not in alice.request('/')[2]
        completed = submit(alice,dataset,'Retained result across application versions')
        first = until(lambda: running_attempt(queue,settings,completed,owners['alice@example.org']),seconds=45)
        (first['path']/'work/finish').write_text('finish fictional model\n')
        until(lambda: queue.inspect(owners['alice@example.org'],completed)['jobs'][0]['state']=='succeeded',seconds=45)
        completed_job = str(first['job']['id'])
        response = alice.request('/downloads/'+completed_job); assert response[0] == 200
        batch_archive = archive_hashes(response[2])
        response = single.form('/launch',dict(model_key=MODEL['id'])); assert response[0] == 303
        sid = response[1]['Location'].rsplit('/',1)[-1]
        until(lambda: (store.peek_session(sid) or {}).get('status')=='ready',seconds=30)
        assert single.api('/build-json/'+sid,dict(seed=607,endYear=2026))['status']=='building'
        until(lambda: single.api('/status/'+sid).get('built'),seconds=30)
        assert single.api('/start-sim/'+sid,{})['status']=='started'
        response = single.request('/java/'+sid+'/simulation/export/zip'); assert response[0] == 200
        archives = dict(single=hashlib.sha256(response[2]).hexdigest(), batch=batch_archive)
        original = store.peek_session(sid)
        active_exp = submit(alice,dataset,'Running configuration across code updates')
        active = until(lambda: running_attempt(queue,settings,active_exp,owners['alice@example.org']),seconds=45)
        active['experiment'] = active_exp
        queued = submit(bob,dataset,'Queued second owner across code updates')
        assert queue.inspect(owners['bob@example.org'],queued)['jobs'][0]['attempts'] == 0
        held, cookies = allocations(queue), (single.cookie,alice.cookie,bob.cookie)
        secrets_hashes = dict(batch=releases.digest((state/'session-secret').read_bytes()),
                             framework=releases.digest((single_state/'.sesskey').read_bytes()))
        clients = single,other,alice,bob,anonymous
        snapshot_checks = dict(sid=sid,completed_job=completed_job,archives=archives,cookies=cookies,original=original,
                               active=active,queued=queued,owners=owners,held=held,secrets_hashes=secrets_hashes)
        preserved(clients,settings,queue,store,**snapshot_checks)
        passed('previous application versions serve owned results while interactive/batch containers run and the second owner queues')
        report['transitions'] = []
        for name, selected in (('updated','candidate'), ('rolled-back','previous')):
            report['phase'] = name+'-code-startup'
            started = time.monotonic()
            current = generation(work,args.output,bundles[selected],settings,environment,policies,name=name,owned=supervisors,previous=current)
            from deploy.multirun.proxy_rehearsal import until
            until(lambda: attempt(queue,active_exp,owners['alice@example.org'])[0]['state']=='active',seconds=45)
            preserved(clients,settings,queue,store,**snapshot_checks)
            page = alice.request('/')[2]
            assert (b'id="help-experiment"' in page) == (selected == 'candidate')
            response = alice.request('/static/batch.css'); assert response[0] == 200
            assert releases.digest(response[2]) == bundles[selected]['frontend']['files']['jasmine_web/batch/browser_static/batch.css']
            # Start and Pause after each transition confirm the existing controls,
            # not just cached pages/status; the model and secret stay the same.
            assert single.api('/pause/'+sid,{})['status']=='paused'
            assert single.api('/start-sim/'+sid,{})['status']=='started'
            report['transitions'].append(dict(name=name, seconds=round(time.monotonic()-started,3),
                host=bundles[selected]['host']['commit'],frontend=bundles[selected]['frontend']['commit'],
                original_attempt=True,original_containers=True,cookies_preserved=True,archives_preserved=True))
            passed(name+': real source/assets change; cookies, controls, result hashes, permissions and the original frozen attempt/reservations survive')
        report['phase'] = 'original-work-completion'
        (active['path']/'work/finish').write_text('finish original model after rollback\n')
        until(lambda: queue.inspect(owners['alice@example.org'],active_exp)['jobs'][0]['state']=='succeeded',seconds=45)
        waiting = until(lambda: running_attempt(queue,settings,queued,owners['bob@example.org']),seconds=45)
        (waiting['path']/'work/finish').write_text('finish queued model after rollback\n')
        until(lambda: queue.inspect(owners['bob@example.org'],queued)['jobs'][0]['state']=='succeeded',seconds=45)
        for experiment, owner, model in ((active_exp,owners['alice@example.org'],active),(queued,owners['bob@example.org'],waiting)):
            job,row,spec = attempt(queue,experiment,owner)
            assert job['attempts'] == 1 and (model['path']/'work/starts.txt').read_text() == 'started\n'
            with queue._connection() as c:
                seeds = c.execute('SELECT actual_seed FROM repetitions WHERE attempt_id=%s ORDER BY ordinal',(row['id'],)).fetchall()
            assert [v['actual_seed'] for v in seeds] == ['606','607']
        assert single.api('/reset/'+sid,{})['status']=='reset'
        assert single.form('/leave/'+sid,{})[0] == 303
        until(lambda: allocations(queue) == dict(batch=[],single=[]),seconds=30)
        report['completion'] = dict(attempts_per_configuration=1,seeds=['606','607'],all_capacity_released=True)
        passed('running and queued configurations complete their original seeds once after rollback; Reset/Leave release only settled capacity')
        current.close()
        report['phase'] = 'incompatible-schema-and-native-restore'
        schema_recovery(work,bundles,report)
        passed('old batch/SingleRun code rejects extra schema versions without changing data; verified native restore remains gated until activation and serves results with the original cookie')
        for value in bundles.values(): releases.verify(value)
        report['passed'] = True
    finally:
        errors = []
        if report.get('backup_fixture_cleanup') is False:
            errors.append('backup-fixture')
        for supervisor in reversed(supervisors):
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
        for path in work.glob('*.log'):
            try: shutil.copyfile(path,args.output/path.name)
            except Exception as error: errors.append('evidence:'+type(error).__name__)
        if not errors:
            shutil.rmtree(work)
        report['cleanup'] = not errors
        if errors: report.update(passed=False,cleanup_errors=errors,retained_fixture=str(work))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=Path(os.environ.get('JASMINE_WEB_REPO',str(Path.home()/'git/JAS-mine/JAS-mine-web'))))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--previous-frontend',default=os.environ.get('SIMPATHS_UPDATE_PREVIOUS_FRONTEND',releases.PREVIOUS_FRONTEND))
    parser.add_argument('--previous-host',default=os.environ.get('SIMPATHS_UPDATE_PREVIOUS_HOST',releases.PREVIOUS_HOST))
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--serve-fixture',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--role',choices=('single','batch'),help=argparse.SUPPRESS)
    parser.add_argument('--probe-fixture',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--restored-http',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--import-fixture',type=Path,help=argparse.SUPPRESS)
    args = parser.parse_args(argv); os.umask(0o077)
    if args.serve_fixture:
        if not args.role: parser.error('A fixture role is required')
        serve(json.loads(args.serve_fixture.read_text()),args.role); return 0
    if args.probe_fixture or args.restored_http or args.import_fixture:
        return child(json.loads((args.probe_fixture or args.restored_http or args.import_fixture).read_text()),
                     restored_http=bool(args.restored_http),imports_only=bool(args.import_fixture))
    args.frontend = releases.ordinary(args.frontend.expanduser()).resolve(strict=True)
    os.environ['JASMINE_WEB_REPO'] = str(args.frontend)
    sys.path[:0] = [str(args.frontend),str(args.frontend/'tests/batch')]
    if not args.execute_proof:
        output = args.output or Path.home()/'simpaths-benchmarks'/('application-update-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        return subprocess.run([sys.executable,str(args.frontend/'scripts/test_batch_queue.py'),'--proof-only',
            '--proof-script',str(Path(__file__).resolve()),'--proof-requirements',str(ROOT/'deploy/multirun/requirements.txt'),
            str(args.frontend/'requirements-vm.txt'),'--output',str(output)],env=dict(os.environ,
                SIMPATHS_UPDATE_PREVIOUS_FRONTEND=args.previous_frontend,SIMPATHS_UPDATE_PREVIOUS_HOST=args.previous_host)).returncode
    from deploy.multirun.proxy_rehearsal import require_test_database
    if not args.output or not os.environ.get('JASMINE_BATCH_TEST_DSN'): parser.error('Run without --execute-proof')
    require_test_database(os.environ['JASMINE_BATCH_TEST_DSN'])
    if args.output.exists(): parser.error('Choose a new evidence directory')
    args.output.mkdir(mode=0o700,parents=True)
    from deploy.multirun.artifacts import write_attribution
    write_attribution(args.output)
    report = dict(started_at=datetime.now(timezone.utc).isoformat(),passed=False,cleanup=False,
                  synthetic=True,production_acceptance=False,phase='offline-regressions')
    report['source_hashes'] = {name:releases.digest((ROOT/name).read_bytes()) for name in (
        'deploy/acceptance/run_update_rehearsal.py','deploy/acceptance/_application_update.py',
        'deploy/acceptance/test_update_rehearsal.py',*(f'deploy/acceptance/{name}' for name in releases.FIXTURES[1:]))}
    try:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('deploy.acceptance.test_update_rehearsal'))
        report['regressions'] = dict(tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),skipped=len(result.skipped))
        assert result.wasSuccessful() and not result.skipped
        print('PASS: immutable release, migration, import and update guards pass without skips',flush=True)
        rehearsal(args,report)
    except (Exception,KeyboardInterrupt,SystemExit) as error:
        report.update(passed=False,error_type=type(error).__name__)
        if isinstance(error,(AssertionError,RuntimeError)): report['message'] = str(error)[:500]
        trace = error.__traceback__
        while trace and trace.tb_next: trace = trace.tb_next
        if trace: report['failure_location'] = dict(file=Path(trace.tb_frame.f_code.co_filename).name,line=trace.tb_lineno,function=trace.tb_frame.f_code.co_name)
        print('STOPPED: '+type(error).__name__+' during '+report['phase']+'; inspect the private reports/logs.',file=sys.stderr)
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (args.output/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
    print(('PASSED' if report['passed'] and report['cleanup'] else 'FAILED')+': '+str(args.output/'report.json'))
    return 0 if report['passed'] and report['cleanup'] else 1


if __name__ == '__main__': raise SystemExit(main())
