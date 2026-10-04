"""(C) Copyright 2026, by Ross Richardson

SingleRun HTTPS fixture isolation, streaming lifetimes and proxy compatibility.
These checks need no database, Docker daemon, listener or scientific simulation.
@author ross richardson
"""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from http.client import HTTPMessage
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
import zipfile

import httpx

from deploy._workflow import frontend_path
from deploy.acceptance import run_https_rehearsal as proof
from deploy.acceptance import _https_fixture as fixture


class HTTPSRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = (frontend_path()/'deploy/nginx.vm.https.conf').read_text()
        self.options = dict(http_port=18080, https_port=18443, app_port=15001,
                            base=self.root/'proxy', runtime=self.root/'runtime')

    def client(self):
        client = object.__new__(proof.SingleClient)
        client.origin, client.cookie, client.csrf = 'https://localhost:18443', '', ''
        client.cookies = {}
        return client

    def headers(self, *cookies):
        result = HTTPMessage()
        for cookie in cookies: result['set-cookie'] = cookie
        return result

    def test_proxy_preserves_single_run_timeouts_upload_limit_and_private_responses(self):
        value = proof.single_proxy_config(self.source, **self.options)
        self.assertIn('proxy_pass http://127.0.0.1:15001;', value)
        self.assertIn('proxy_read_timeout 650s;', value)
        self.assertIn('proxy_send_timeout 650s;', value)
        self.assertIn('client_max_body_size 512m;', value)
        self.assertIn('add_header Cache-Control "no-store" always;', value)
        self.assertIn('proxy_hide_header Strict-Transport-Security;', value)
        self.assertIn('proxy_set_header Host localhost:18443;', value)
        self.assertIn('return 308 https://localhost:18443$request_uri;', value)
        self.assertIn('ssl_reject_handshake on;', value)
        self.assertNotIn('singlerun.example.org', value)
        self.assertNotIn('listen [::]', value)
        self.assertEqual(value.count('root '), 1)

    def test_changed_upload_cache_timeout_or_file_mapping_is_rejected(self):
        for value in (self.source.replace('512m;', '1m;'),
                      self.source.replace('"no-store"', '"public"'),
                      self.source.replace('proxy_hide_header Strict-Transport-Security;', ''),
                      self.source.replace('650s;', '30s;'),
                      self.source+'\nserver { location /data { alias /private; } }',
                      self.source.replace('proxy_buffering off;', 'proxy_buffering on;')):
            with self.subTest(value=value[-60:]), self.assertRaises(ValueError):
                proof.single_proxy_config(value, **self.options)

    def test_host_only_lax_owner_cookies_preserve_independent_sessions_and_clear_one(self):
        client = self.client()
        flags = '; Path=/; Secure; HttpOnly; SameSite=Lax'
        client.capture(self.headers('__jasmine_owner_a=alice'+flags, '__jasmine_owner_b=bob'+flags))
        self.assertEqual(client.cookies, {'__jasmine_owner_a':'alice', '__jasmine_owner_b':'bob'})
        client.capture(self.headers('__jasmine_owner_a=""; Max-Age=0'+flags))
        self.assertEqual(client.cookie, '__jasmine_owner_b=bob')

    def test_ownership_cookie_protection_regressions_are_detected(self):
        good = '__jasmine_owner_a=value; Path=/; Secure; HttpOnly; SameSite=Lax'
        for cookie in (good.replace('; Secure', ''), good.replace('; HttpOnly', ''),
                       good.replace('Lax', 'None'), good.replace('Path=/', 'Path=/sim'),
                       good+'; Domain=localhost'):
            with self.subTest(cookie=cookie), self.assertRaises(AssertionError):
                self.client().capture(self.headers(cookie))

    def test_launch_form_preserves_origin_redirect_and_owner_cookie(self):
        client = self.client()
        response = io.BytesIO(b'')
        response.status = 303
        response.headers = self.headers('__jasmine_owner_a=alice; Path=/; Secure; HttpOnly; SameSite=Lax')
        response.headers['location'] = '/sim/fictional'
        client.opener = Mock()
        client.opener.open.return_value = response
        status, headers, _ = client.form('/launch', {'model_key':'fictional-https'})
        request = client.opener.open.call_args.args[0]
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/sim/fictional')
        self.assertEqual(request.get_header('Origin'), client.origin)
        self.assertEqual(request.data, b'model_key=fictional-https')
        self.assertEqual(client.cookie, '__jasmine_owner_a=alice')

    def test_archive_is_valid_bounded_output_with_retained_options(self):
        path = self.root/'fictional.zip'
        fixture.create_archive(path, 1)
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.getinfo('run_1/csv/Values.csv').file_size, 1024**2)
            self.assertIn(b'endYear=2026', archive.read('run_1/input/options.txt'))
        self.assertGreater(path.stat().st_size, 1024**2)
        with self.assertRaises(FileExistsError): fixture.create_archive(path, 1)

    def test_wrapper_installs_both_dependency_sets_and_keeps_isolated_options(self):
        mask = os.umask(0o077); self.addCleanup(lambda: os.umask(mask))
        with patch.dict(os.environ), patch.object(proof.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0)) as run:
            result = proof.main(['--frontend', str(frontend_path()), '--output', str(self.root/'evidence'),
                '--payload-mib', '128', '--slow-seconds', '35'])
        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        self.assertIn(str(frontend_path()/'scripts/test_batch_queue.py'), command)
        self.assertIn('--proof-only', command)
        self.assertIn(str(frontend_path()/'requirements-vm.txt'), command)
        self.assertIn(str(proof.ROOT/'deploy/multirun/requirements.txt'), command)
        self.assertEqual(run.call_args.kwargs['env']['SIMPATHS_SINGLE_HTTPS_MIB'], '128')
        self.assertEqual(run.call_args.kwargs['env']['SIMPATHS_SINGLE_HTTPS_SECONDS'], '35')

    def test_app_runner_selects_frontend_working_directory_and_private_settings(self):
        process = proof.Processes(self.root, self.root, SimpleNamespace(nginx=None))
        self.addCleanup(lambda: [log.close() for log in process.logs])
        script = self.root/'single.py'
        settings = dict(secret='FICTIONAL_PRIVATE_SECRET')
        with patch('deploy.multirun.proxy_rehearsal.subprocess.Popen') as spawn:
            process.start_app(settings, script=script, workdir=frontend_path())
        self.assertEqual(spawn.call_args.args[0], [sys.executable, str(script), '--serve-fixture', str(self.root/'fixture.json')])
        self.assertEqual(spawn.call_args.kwargs['cwd'], frontend_path())
        self.assertEqual(json.loads((self.root/'fixture.json').read_text()), settings)
        self.assertEqual((self.root/'fixture.json').stat().st_mode & 0o777, 0o600)

    def test_environment_rejects_live_database_and_schemas_before_application_import(self):
        dsn = 'postgresql://postgres:FICTIONAL_SECRET@127.0.0.1:54321/jasmine_queue_test'
        settings = dict(schema='test_single_https_'+'a'*32, batch_schema='test_single_https_'+'b'*32,
            secret='fictional', admin='fictional-admin', origin='https://localhost:18443', catalogue='/private/catalogue.json')
        value = fixture.fixture_environment(dsn, settings)
        self.assertEqual(value['VM_POSTGRES_DSN'], dsn)
        self.assertEqual(value['VM_POSTGRES_SCHEMA'], settings['schema'])
        self.assertEqual(value['VM_SHARED_BATCH_SCHEMA'], settings['batch_schema'])
        self.assertEqual(value['COOKIE_SECURE'], 'true')
        self.assertEqual(value['BROWSER_ORIGINS'], settings['origin'])
        for changed_dsn, changed_settings in ((dsn.replace('jasmine_queue_test', 'live'), settings),
                (dsn, dict(settings, schema='jasmine_vm')), (dsn, dict(settings, batch_schema='jasmine_batch'))):
            with self.assertRaises(ValueError): fixture.fixture_environment(changed_dsn, changed_settings)

    def test_real_frontend_import_uses_private_catalogue_and_existing_cookie_rules(self):
        # PostgreSQL and serving are replaced only for this import regression.
        # The native rehearsal uses real state and a real Uvicorn child process.
        catalogue = self.root/'catalogue.json'
        catalogue.write_text(json.dumps({'models':[fixture.MODEL]}))
        settings = dict(state=str(self.root), archive=str(self.root/'fictional.zip'),
            schema='test_single_https_'+'a'*32, batch_schema='test_single_https_'+'b'*32,
            secret='fictional', admin='fictional-admin', origin='https://localhost:18443',
            catalogue=str(catalogue), app_port=15001)
        manager = Mock()
        manager.get_all_sessions.return_value = []
        before = Path.cwd()
        self.addCleanup(lambda: os.chdir(before))
        os.chdir(frontend_path())
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, JASMINE_BATCH_TEST_DSN=
                'postgresql://postgres:FICTIONAL_SECRET@127.0.0.1:54321/jasmine_queue_test'))
            stack.enter_context(patch.dict(sys.modules, session_manager=manager))
            stack.enter_context(patch('jasmine_web.vm_state.get_vm_state', return_value=Mock()))
            server = stack.enter_context(patch('uvicorn.run'))
            fixture.serve(settings)
        application = server.call_args.args[0]
        paths = {getattr(route, 'path', '') for route in application.routes}
        self.assertTrue({'/launch', '/status/{sim_id}', '/java/{sim_id}/{path:path}', '/leave/{sim_id}'} <= paths)
        self.assertEqual(server.call_args.kwargs['host'], '127.0.0.1')
        manager.start_cleanup_thread.assert_called_once()


class FictionalModelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root/'fictional.zip'; fixture.create_archive(self.archive, 1)
        self.containers = fixture.FictionalContainers(self.root)
        self.ids = [str(uuid4()), str(uuid4())]
        self.records = []
        for number, sid in enumerate(self.ids):
            credential = 'FICTIONAL_CREDENTIAL_'+str(number)
            self.containers.start_container(fixture.MODEL['deployment']['image'], session_id=sid,
                model_id=fixture.MODEL['id'], extra_env={'SIM_ID':sid, 'JASMINE_BACKEND_SECRET':credential})
            self.records.append(dict(session_id=sid, backend_secret=credential))
        self.java = fixture.FictionalJava(SimpleNamespace(get_all_sessions=lambda: deepcopy(self.records)),
                                         self.containers, self.archive)

    async def asyncSetUp(self):
        # These records are in memory, so no database thread is needed. Avoid
        # relying on sandbox thread-to-event-loop wakeups for fixture checks.
        async def direct(function, *args, **kwargs):
            return function(*args, **kwargs)
        offload = patch.object(fixture.asyncio, 'to_thread', direct)
        offload.start(); self.addCleanup(offload.stop)

    def request(self, sid, path, body=None):
        credential = next(record['backend_secret'] for record in self.records if record['session_id'] == sid)
        return httpx.Request('GET' if body is None else 'POST', 'http://172.30.0.2:7070'+path,
                             headers={'X-Jasmine-Backend-Token':credential}, json=body)

    async def test_private_stream_records_partial_close_and_detect_double_close(self):
        stream = fixture.FileStream(self.archive, self.root, self.ids[0])
        part = await anext(stream.__aiter__())
        self.assertEqual(len(part), 65536)
        self.assertFalse(proof.streams_closed(self.root, 1))
        await stream.aclose()
        closed = proof.streams_closed(self.root, 1)
        self.assertEqual(closed[0]['bytes'], 65536)
        self.assertTrue(stream.source.closed)
        await stream.aclose()
        with self.assertRaises(AssertionError): proof.streams_closed(self.root, 1)

    async def test_chunked_and_fixed_length_streams_are_complete_valid_archives(self):
        for suffix in ('', '?length=1'):
            response = await self.java.response(self.request(self.ids[0], '/simulation/export/zip'+suffix))
            try:
                content = b''.join([chunk async for chunk in response.aiter_raw()])
            finally:
                await response.aclose()
            self.assertEqual(content, self.archive.read_bytes())
            self.assertEqual(response.headers.get('Content-Length'), str(len(content)) if suffix else None)
            with zipfile.ZipFile(io.BytesIO(content)) as archive: self.assertIsNone(archive.testzip())
        self.assertEqual(len(proof.streams_closed(self.root, 2)), 2)

    async def test_per_session_settings_survive_new_fixture_instance_and_reset_keeps_archive(self):
        original = proof.archive_hash(self.archive)
        for sid, seed in zip(self.ids, (123, 909)):
            response = await self.java.response(self.request(sid, '/simulation/build', dict(seed=seed, endYear=2026)))
            self.assertEqual(response.json()['status'], 'built')
        restarted = fixture.FictionalContainers(self.root)
        self.assertEqual([restarted.read(sid)['parameters']['seed'] for sid in self.ids], [123, 909])
        response = await self.java.response(self.request(self.ids[0], '/simulation/reset', {}))
        self.assertEqual(response.json()['status'], 'reset')
        self.assertFalse(restarted.read(self.ids[0])['built'])
        self.assertTrue(restarted.read(self.ids[1])['built'])
        self.assertEqual(proof.archive_hash(self.archive), original)

    async def test_missing_and_wrong_private_credential_never_open_output_stream(self):
        for headers in ({}, {'X-Jasmine-Backend-Token':'WRONG'}):
            response = await self.java.response(httpx.Request('GET', 'http://172.30.0.2:7070/simulation/export/zip', headers=headers))
            self.assertEqual(response.status_code, 403)
        self.assertEqual(proof.stream_records(self.root), [])

    async def test_container_cleanup_checks_owner_and_never_removes_other_model(self):
        a, b = self.ids
        self.assertFalse(self.containers.stop_container('fictional-'+a, session_id=b, model_id=fixture.MODEL['id']))
        self.assertFalse(self.containers.stop_container('fictional-'+a, session_id=a, model_id='wrong'))
        self.assertEqual(len(self.containers.list_containers()), 2)
        self.assertTrue(self.containers.stop_container('fictional-'+a, session_id=a, model_id=fixture.MODEL['id']))
        self.assertEqual([value['sid'] for value in self.containers.list_containers()], [b])
        self.assertEqual((await self.java.response(self.request(a, '/simulation/status'))).status_code, 404)
