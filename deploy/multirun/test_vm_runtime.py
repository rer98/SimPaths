"""(C) Copyright 2026, by Ross Richardson

Disposable PostgreSQL proof of the actual VM application with a fictional executor.
Checks HTTPS access, worker completion/restart, health and operator pool changes.
@author ross richardson
"""
from contextlib import ExitStack, contextmanager
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from deploy._workflow import frontend_path

FRONTEND = frontend_path()
sys.path.insert(0, str(FRONTEND))
sys.path.insert(0, str(FRONTEND/'tests'/'batch'))
from jasmine_web.batch.local_database import private_directory
from jasmine_web.batch.local_executor import LocalExecutor
from jasmine_web.batch.policy import Conflict, Resources
from jasmine_web.batch.store import Queue
from starlette.testclient import TestClient
from adapter_fixture import DummyAdapter
from browser_fixture import BrowserModel
from . import runtime
from .local_web import parse_args
from .resource_policy import LEGACY_POLICY

DSN = os.environ.get('JASMINE_BATCH_TEST_DSN')


def until(function):
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('VM proof timed out')


class FixtureExecutor(LocalExecutor):
    """A stopped-process cleanup contract for the fictional VM assembly."""
    workspace_quotas=None

    def cleanup(self,lease):
        self._require_guard()
        if self.inspect(lease)['state']!='stopped':
            raise Conflict('Fixture process termination is unconfirmed')


@unittest.skipUnless(DSN, 'Use vm_proof.py with disposable PostgreSQL')
class VMRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='simpaths-vm-test-')
        self.addCleanup(temporary.cleanup)
        self.state = private_directory(Path(temporary.name)/'private')
        self.schema = 'test_vm_'+uuid4().hex
        self.q = Queue(DSN, 'vm-test', schema=self.schema)
        self.q.migrate()
        self.addCleanup(self.drop_schema)
        self.args = parse_args(['serve', '--console-codes'])
        self.args.state = self.state
        self.mail = {}
        async def deliver(email, code): self.mail[email] = code
        self.deliver = deliver
        self.access, self.datasets, self.preparations, self.keys = runtime.create_registry(
            self.args, self.q, self.state, deliver)
        self.owner = self.access.approve_email('alice@example.org')
        self.image = 'sha256:'+'a'*64
        self.release = {'approved': dict(image=self.image, jar=self.state/'test.jar', defaults=self.state,
                                        resource_policy=LEGACY_POLICY)}
        self.origin = 'https://multirun.example.org'
        self.patches = ExitStack(); self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(runtime, 'frozen_release', return_value=self.release))
        self.patches.enter_context(patch.object(runtime, 'BrowserModel', side_effect=lambda *a, **kw: BrowserModel(self.state)))
        self.patches.enter_context(patch.object(runtime, 'DockerExecutor', side_effect=lambda root, **kw: FixtureExecutor(root)))
        adapter = DummyAdapter(delay=.1)
        adapter.preparation = SimpleNamespace(retire_failed=lambda *a, **kw: None)
        self.patches.enter_context(patch.object(runtime, 'DispatchAdapter', return_value=adapter))
        self.patches.enter_context(patch('deploy.multirun.queue_adapter.result_name',
            side_effect=lambda run: run['parameters'].get('name', run['id'])))

    def drop_schema(self):
        from psycopg import sql
        with self.q._connection() as c:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def app(self, **kwargs):
        return runtime.create_application(self.args, self.q, self.state, self.access,
            self.datasets, self.preparations, self.keys, self.image, self.origin, **kwargs)

    def sign_in(self, client):
        client.headers['Origin'] = self.origin
        challenge = client.post('/api/code', json=dict(email='alice@example.org')).json()['challenge']
        result = client.post('/api/verify', json=dict(challenge=challenge, code=self.mail['alice@example.org']))
        self.assertTrue(result.json()['authorised'], result.text)
        client.headers['X-CSRF-Token'] = result.json()['csrf']
        return result

    def dataset(self, provider=False):
        if provider:
            key = self.datasets.register_provider('b'*64)
            self.q.grant_dataset(self.owner, key)
        else:
            upload = self.datasets.receive(self.owner, 'population.csv', io.BytesIO(b'abc'), expected_bytes=3)
            key = self.datasets.publish(self.owner, 'b'*64, uploads=[upload])
        location = self.state/key; location.mkdir()
        self.preparations.register_location(key, location=str(location), image=self.image)
        return key

    def test_private_executor_constructs_no_http_application_and_owns_processors(self):
        import asyncio
        import threading
        with patch('jasmine_web.batch.browser.create_app',side_effect=AssertionError('No private HTTP app')):
            bundle=self.app(private_service=True)
        self.assertEqual(set(bundle.ready()),{'ready','worker_status'})
        self.assertFalse(bundle.ready()['ready'])
        visualiser=SimpleNamespace(stop=threading.Event(),ticks=0,retired=0)
        def tick():visualiser.ticks+=1
        def retire():visualiser.retired+=1
        visualiser.tick=tick
        visualiser.retire=retire
        visualiser.close=visualiser.stop.set
        bundle.service.visualiser=visualiser
        downloads=bundle.service.results.downloads
        with patch.object(downloads,'retire',wraps=downloads.retire) as retired, \
                patch.object(downloads,'close',wraps=downloads.close) as closed:
            async def exercise():
                async with bundle.lifespan(None):
                    for _ in range(300):
                        if bundle.ready()['ready'] and visualiser.ticks and visualiser.retired:
                            break
                        await asyncio.sleep(.05)
                    self.assertTrue(bundle.ready()['ready'])
                    self.assertGreater(visualiser.ticks,0)
                    self.assertGreater(visualiser.retired,0)
                self.assertTrue(visualiser.stop.is_set())
            asyncio.run(exercise())
            self.assertTrue(retired.called)
            closed.assert_called_once()
        self.assertFalse(any(t.is_alive() and t.name in ('multirun-private-cache','multirun-private-visualiser')
                             for t in threading.enumerate()))

    def test_private_executor_health_hides_database_details(self):
        bundle=self.app(private_service=True)
        import asyncio
        async def exercise():
            async with bundle.lifespan(None):
                for _ in range(300):
                    if bundle.ready()['ready']:break
                    await asyncio.sleep(.05)
                with patch.object(self.q,'_connection',side_effect=RuntimeError('PRIVATE_DSN_SECRET')):
                    result=bundle.ready()
                    self.assertFalse(result['ready'])
                    self.assertNotIn('PRIVATE_DSN_SECRET',json.dumps(result))
        asyncio.run(exercise())

    def test_public_http_rpc_pipeline_and_frontend_restart_preserve_active_job(self):
        """Exercise wire framing and real domain/DB state; native UID checks are separate."""
        import asyncio
        import socket
        import threading
        from jasmine_web.batch import service_rpc as rpc
        from jasmine_web.batch.local_executor import Command
        from jasmine_web.batch.remote_service import RemoteService
        from .vm_frontend import create_public_application

        class PairClient(rpc.RPCClient):
            # Only replace connection establishment. Framing, serialization,
            # upload streaming and the private dispatcher remain production code.
            # socketpair is same-UID: privilege_probe.py checks the installed UIDs.
            def __init__(self, server):
                super().__init__('/unused/socket', os.getuid()+1, timeout=15)
                self.server, self.workers = server, []

            def _connect(self, operation, values):
                public, private = socket.socketpair()
                def handle():
                    with private:
                        self.server._handle(private)
                worker = threading.Thread(target=handle, daemon=True)
                self.workers.append(worker)
                worker.start()
                rpc._write_frame(public, json.dumps(dict(operation=operation, values=values)).encode())
                return public

            def drain(self):
                for worker in self.workers:
                    worker.join(5)
                    if worker.is_alive():
                        raise AssertionError('Private RPC operation failed to drain')

        release = self.state/'release-fictional-job'
        class GatedAdapter(DummyAdapter):
            def command(self, lease, work):
                command = super().command(lease, work)
                script = ('import pathlib,runpy,sys,time\n'
                          'deadline=time.monotonic()+30\n'
                          'while not pathlib.Path(sys.argv[1]).exists():\n'
                          ' if time.monotonic()>deadline: raise SystemExit(1)\n'
                          ' time.sleep(.02)\n'
                          'runpy.run_path(sys.argv[2],run_name="__main__")\n')
                return Command((sys.executable, '-c', script, str(release), command.argv[1]), {})
        adapter = GatedAdapter(delay=.01)
        adapter.preparation = SimpleNamespace(retire_failed=lambda *a, **kw: None)
        with patch.object(runtime, 'DispatchAdapter', return_value=adapter):
            bundle = self.app(private_service=True)
        server = rpc.RPCServer(bundle.service, '/unused/socket', web_uid=os.getuid()+1, health=bundle.ready)
        clients = []

        def public_application():
            client = PairClient(server)
            clients.append(client)
            return create_public_application(RemoteService(client), self.origin)

        def attempt_ids(experiment):
            with self.q._connection() as connection:
                return [str(row['id']) for row in connection.execute(
                    'SELECT id FROM attempts WHERE job_id IN '
                    '(SELECT id FROM jobs WHERE experiment_id=%s) ORDER BY id', (experiment,)).fetchall()]

        def workflow():
            with TestClient(public_application(), base_url=self.origin) as public:
                until(lambda: public.get('/healthz').status_code == 200)
                self.sign_in(public)
                self.assertEqual(public.get('/api/dashboard').status_code, 200)
                upload = public.post('/api/uploads', content=b'abc', headers={
                    'Content-Type':'application/octet-stream', 'X-Upload-Name':'population.csv'})
                self.assertEqual(upload.status_code, 201, upload.text)
                # Trusted preparation fixture supplies the small prepared dataset;
                # its source upload reached private storage through the HTTP/RPC path.
                dataset = self.datasets.publish(self.owner, 'b'*64, uploads=[upload.json()['id']])
                location = self.state/dataset
                location.mkdir()
                self.preparations.register_location(dataset, location=str(location), image=self.image)
                form = dict(name='Private executor RPC proof',
                    common=dict(population=20, start_year=2019, end_year=2020), repetitions=2,
                    baseline='first', auto_retry=False,
                    run_sets=[dict(id='first', name='Baseline example', model_args={})])
                review = public.post('/api/review-experiment', json=dict(dataset=dataset, form=form))
                self.assertEqual(review.status_code, 200, review.text)
                submitted = public.post('/api/submit', json=dict(key='rpc-native-proof', review=review.json()['review']))
                self.assertEqual(submitted.status_code, 201, submitted.text)
                experiment = submitted.json()['id']
                def active():
                    status = public.get('/api/experiments/'+experiment).json()
                    return status if status.get('jobs') and status['jobs'][0]['state'] == 'active' else None
                started = until(active)
                original_attempts = attempt_ids(experiment)
                self.assertEqual(len(original_attempts), 1)
                cookies = dict(public.cookies)
            self.assertTrue(bundle.ready()['ready'])
            self.assertFalse(release.exists())
            with TestClient(public_application(), base_url=self.origin) as restarted:
                restarted.cookies.update(cookies)
                restarted.headers['Origin'] = self.origin
                session = restarted.get('/api/session')
                self.assertTrue(session.json()['signed_in'], session.text)
                resumed = restarted.get('/api/experiments/'+experiment)
                self.assertEqual(resumed.status_code, 200, resumed.text)
                self.assertEqual(resumed.json()['jobs'][0]['id'], started['jobs'][0]['id'])
                self.assertEqual(resumed.json()['jobs'][0]['state'], 'active')
                self.assertEqual(attempt_ids(experiment), original_attempts)
                release.touch()
                def completed():
                    status = restarted.get('/api/experiments/'+experiment).json()
                    return status if status.get('jobs') and all(j['state']=='succeeded' for j in status['jobs']) else None
                completed_status = until(completed)
                self.assertEqual(completed_status['jobs'][0]['attempts'], 1)
                self.assertEqual(attempt_ids(experiment), original_attempts)
                self.assertEqual(restarted.get('/api/dashboard').status_code, 200)

        async def exercise():
            async with bundle.lifespan(None):
                try:
                    await asyncio.to_thread(workflow)
                finally:
                    release.touch()
                    for client in clients:
                        client.drain()
        asyncio.run(exercise())

    def test_https_sign_in_secure_cookies_origin_host_and_revocation(self):
        app = self.app()
        self.assertFalse(app.state.submissions.allow_deadline_credit)
        with TestClient(app, base_url=self.origin) as client:
            until(lambda: client.get('/healthz').status_code == 200)
            result = self.sign_in(client)
            cookie = result.headers['set-cookie'].lower()
            for expected in ('secure', 'httponly', 'samesite=strict'): self.assertIn(expected, cookie)
            self.assertFalse(client.get('/api/session').json()['local_codes'])
            self.assertEqual(client.get('/api/session', headers={'Host':'wrong.example.org'}).status_code, 400)
            self.assertEqual(client.post('/api/logout', json={}, headers={'Origin':'https://wrong.example.org'}).status_code, 403)
            self.access.approve_email('alice@example.org', seconds=None)
            self.assertEqual(client.get('/api/dashboard').status_code, 403)

    def test_opt_in_monitor_lifecycle_records_http_and_host_without_owner_data(self):
        from jasmine_web.batch.observability import history
        self.args.operational_metrics=True
        app=self.app()
        monitor=app.state.submissions.monitor
        with TestClient(app,base_url=self.origin) as client:
            until(lambda:client.get('/healthz').status_code==200)
            self.sign_in(client)
            self.assertEqual(client.get('/api/dashboard').status_code,200)
            self.assertEqual(client.get('/api/session?private-canary=secret').status_code,200)
        self.assertFalse(monitor.thread.is_alive())
        record=history(self.q)
        self.assertTrue(record['recorded'])
        self.assertTrue(any(r['kind']=='http' for r in record['measurements']))
        self.assertTrue(any(r['kind']=='host' for r in record['measurements']))
        text=json.dumps(record)
        for value in ('alice@example.org','private-canary','secret',DSN):self.assertNotIn(value,text)

    def test_restore_marker_blocks_application_and_registry_before_assembly_changes(self):
        from .artifacts import ArtifactError
        from .maintenance import GATE
        from jasmine_web.batch.backup import table_inventory
        from jasmine_web.batch.local_executor import atomic_json
        with self.q._connection() as c: before=table_inventory(c,self.q)
        atomic_json(self.state/GATE,dict(phase='verified'))
        with self.assertRaises(ArtifactError):self.app()
        with self.assertRaises(ArtifactError):
            runtime.create_registry(self.args,self.q,self.state,self.deliver)
        with self.q._connection() as c:self.assertEqual(table_inventory(c,self.q),before)
        self.assertFalse((self.state/'operator-settings.json').exists())

    def test_running_application_and_offline_maintenance_exclude_each_other(self):
        from .artifacts import ArtifactError
        from .maintenance import state_guard
        from jasmine_web.batch.backup import database_guard
        with TestClient(self.app(),base_url=self.origin) as client:
            until(lambda:client.get('/healthz').status_code==200)
            with self.assertRaises(ArtifactError):
                with state_guard(self.state,exclusive=True):pass
            with self.assertRaises(Conflict):
                with database_guard(self.q,exclusive=True):pass
        with state_guard(self.state,exclusive=True),self.assertRaises(ArtifactError):self.app()

    def test_actual_dispatcher_completes_frozen_runtime_and_provider_download_stays_denied(self):
        dataset = self.dataset(provider=True)
        with TestClient(self.app(), base_url=self.origin) as client:
            self.sign_in(client)
            form = dict(name='Fictional VM comparison', common=dict(population=20, start_year=2019, end_year=2020),
                repetitions=2, baseline='first', auto_retry=True,
                run_sets=[dict(id='first', name='Baseline example', model_args={})])
            review = client.post('/api/review-experiment', json=dict(dataset=dataset, form=form))
            self.assertEqual(review.status_code, 200, review.text)
            submitted = client.post('/api/submit', json=dict(key='vm-proof', review=review.json()['review']))
            self.assertEqual(submitted.status_code, 201, submitted.text)
            experiment = submitted.json()['id']
            def completed():
                current = client.get('/api/experiments/'+experiment).json()
                return current if current.get('jobs') and all(j['state'] == 'succeeded' for j in current['jobs']) else None
            status = until(completed)
            self.assertEqual(status['jobs'][0]['attempts'], 1)
            with self.q._connection() as c:
                policy = c.execute('SELECT policy FROM experiments WHERE id=%s', (experiment,)).fetchone()['policy']
            self.assertEqual(policy['attempt_seconds'], 8100)
            self.assertEqual(policy['total_seconds'], 24300)
            until(lambda:client.get('/healthz').status_code==200)
            job = client.get('/api/results/'+experiment).json()['configurations'][0]['id']
            for headers in ({}, {'Range':'bytes=0-10'}):
                self.assertEqual(client.get('/downloads/'+job, headers=headers).status_code, 403)

    def test_recovery_opt_in_reaches_review_and_dispatch_without_changing_initial_resources(self):
        self.args.recovery_enabled=True
        def model(*args,**kwargs):
            value=BrowserModel(self.state)
            value.heap_limits=lambda spec:{r['id']:2048 for r in spec['run_sets']}
            return value
        dataset=self.dataset()
        with patch.object(runtime,'BrowserModel',side_effect=model),TestClient(self.app(),base_url=self.origin) as client:
            self.sign_in(client)
            form=dict(name='Recovery review',common=dict(population=20,start_year=2019,end_year=2020),
                repetitions=1,baseline='first',auto_retry=True,run_sets=[dict(id='first',name='Baseline',model_args={})])
            response=client.post('/api/review-experiment',json=dict(dataset=dataset,form=form))
            self.assertEqual(response.status_code,200,response.text)
            reviewed=response.json()
            self.assertEqual(reviewed['resource_recovery']['policy']['max_heap_mib'],4096)
            result=client.post('/api/submit',json=dict(key='recovery-vm-proof',review=reviewed['review']))
            self.assertEqual(result.status_code,201,result.text)
            exp=result.json()['id']
            until(lambda:all(j['state']=='succeeded' for j in client.get('/api/experiments/'+exp).json()['jobs']))
            with self.q._connection() as c:
                row=c.execute('SELECT initial_resources,current_resources,heap_mib FROM attempts WHERE job_id IN '
                    '(SELECT id FROM jobs WHERE experiment_id=%s)',(exp,)).fetchone()
                self.assertEqual(row['initial_resources'],reviewed['resources'])
                self.assertEqual(row['current_resources'],reviewed['resources'])
                self.assertEqual(row['heap_mib'],2048)
            until(lambda:client.get('/healthz').status_code==200)

    def test_restart_preserves_session_secret_and_existing_access(self):
        with TestClient(self.app(), base_url=self.origin) as client:
            self.sign_in(client)
            cookies = dict(client.cookies)
            secret = (self.state/'session-secret').read_bytes()
        self.access, self.datasets, self.preparations, self.keys = runtime.create_registry(
            self.args, self.q, self.state, self.deliver)
        with TestClient(self.app(), base_url=self.origin) as restarted:
            restarted.cookies.update(cookies)
            until(lambda: restarted.get('/healthz').status_code == 200)
            self.assertTrue(restarted.get('/api/session').json()['signed_in'])
        self.assertEqual((self.state/'session-secret').read_bytes(), secret)

    def test_readiness_outage_is_not_healthy_and_does_not_expose_connection_details(self):
        with TestClient(self.app(), base_url=self.origin) as client:
            until(lambda: client.get('/healthz').status_code == 200)
            with patch.object(self.q, '_connection', side_effect=RuntimeError('PRIVATE_DSN_SECRET')):
                response = client.get('/healthz')
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), dict(ready=False))
                self.assertNotIn('PRIVATE_DSN_SECRET', response.text)

    def test_fatal_dispatcher_requests_supervisor_restart(self):
        class FailedWorker:
            def __init__(self, *a, **kw): pass
            @contextmanager
            def open(self):
                raise Conflict('Dispatcher already held')
                yield
        with patch.object(runtime, 'Worker', FailedWorker), patch.object(runtime.os, 'kill') as kill:
            with TestClient(self.app(terminate_on_dispatch_failure=True), base_url=self.origin) as client:
                until(lambda: kill.called)
                kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
                self.assertEqual(client.get('/healthz').status_code, 503)

    def test_pool_concurrency_and_capacity_changes_cannot_silently_rewrite_existing_limits(self):
        for changes in (dict(capacity=Resources(4000,10240,24576)), dict(per_user_active=2)):
            with self.subTest(changes=changes), self.assertRaises(Conflict):
                runtime.create_registry(self.args, self.q, self.state, self.deliver, **changes)

    def test_operator_launch_settings_record_flags_without_credentials_or_owner_data(self):
        import json
        self.app()
        path=self.state/'operator-settings.json'
        data=json.loads(path.read_text())
        self.assertEqual(data['pool_id'],self.q.pool_id)
        self.assertEqual(data['schema'],self.q.schema)
        self.assertFalse(data['notification_delivery'])
        self.assertFalse(data['retention_cleanup'])
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        for secret in (self.q.dsn,'alice@example.org',str(self.state)):
            self.assertNotIn(secret,path.read_text())

    def test_hot_performance_review_runs_with_baseline_monitoring_disabled_and_joins(self):
        import asyncio
        from jasmine_web.batch.performance_review import set_control, read_status
        bundle=self.app(private_service=True)
        self.assertFalse(hasattr(bundle.service,'monitor'))
        reviewer=bundle.service.performance_review
        with patch.object(reviewer,'reader',return_value={}):
            async def exercise():
                async with bundle.lifespan(None):
                    enabled=set_control(self.state,True,sample_seconds=5)
                    for _ in range(200):
                        if reviewer.control['generation']==enabled['generation']:break
                        await asyncio.sleep(.05)
                    self.assertTrue(reviewer.settings.enabled)
                    disabled=set_control(self.state,False)
                    for _ in range(200):
                        if read_status(self.state).get('generation')==disabled['generation']:break
                        await asyncio.sleep(.05)
                    self.assertFalse(reviewer.settings.enabled)
            asyncio.run(exercise())
        self.assertFalse(reviewer.thread.is_alive())

    def test_database_migrations_and_registry_work_without_cluster_superuser_privileges(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        import secrets
        role = 'vm_limited_'+uuid4().hex
        schema = 'test_limited_'+uuid4().hex
        password = secrets.token_hex(32)
        with psycopg.connect(DSN) as admin:
            database = admin.execute('SELECT current_database()').fetchone()[0]
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
                          .format(sql.Identifier(role), sql.Literal(password)))
            admin.execute(sql.SQL('GRANT CREATE ON DATABASE {} TO {}')
                          .format(sql.Identifier(database), sql.Identifier(role)))
        try:
            limited = Queue(make_conninfo(DSN, user=role, password=password), 'limited', schema=schema)
            limited.migrate()
            limited.create_pool(Resources(2000,5120,12288))
            with self.q._connection() as c:
                expected_versions = c.execute('SELECT count(*) AS n FROM schema_version').fetchone()['n']
            with limited._connection() as c:
                flags = c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
                self.assertFalse(any(flags.values()))
                self.assertEqual(c.execute('SELECT count(*) AS n FROM schema_version').fetchone()['n'], expected_versions)
        finally:
            with psycopg.connect(DSN) as admin:
                admin.execute(sql.SQL('DROP OWNED BY {} CASCADE').format(sql.Identifier(role)))
                admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


if __name__ == '__main__':
    unittest.main()
