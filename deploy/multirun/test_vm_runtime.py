"""(C) Copyright 2026, by Ross Richardson

Disposable PostgreSQL proof of the actual VM application with a fictional executor.
Checks HTTPS access, worker completion/restart, health and operator pool changes.
@author ross richardson
"""
from contextlib import ExitStack, contextmanager
import io
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

DSN = os.environ.get('JASMINE_BATCH_TEST_DSN')


def until(function):
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('VM proof timed out')


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
        self.release = {'approved': dict(image=self.image, jar=self.state/'test.jar', defaults=self.state)}
        self.origin = 'https://multirun.example.org'
        self.patches = ExitStack(); self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(runtime, 'frozen_release', return_value=self.release))
        self.patches.enter_context(patch.object(runtime, 'BrowserModel', side_effect=lambda *a, **kw: BrowserModel(self.state)))
        self.patches.enter_context(patch.object(runtime, 'DockerExecutor', side_effect=lambda root, **kw: LocalExecutor(root)))
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
            job = client.get('/api/results/'+experiment).json()['configurations'][0]['id']
            for headers in ({}, {'Range':'bytes=0-10'}):
                self.assertEqual(client.get('/downloads/'+job, headers=headers).status_code, 403)

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
