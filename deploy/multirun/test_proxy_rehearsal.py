"""(C) Copyright 2026, by Ross Richardson

HTTPS rehearsal isolation, deployment-policy preservation and aggregate validation.
These local checks use no live database, Docker, TLS listener or real simulation.
@author ross richardson
"""
from copy import deepcopy
from contextlib import redirect_stdout
import asyncio
import hashlib
from http.client import HTTPMessage
import io
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy._workflow import frontend_path
from . import proxy_rehearsal as rehearsal
from .vm_boundary import aggregate_envelope, NoRedirect

sys.path.insert(0, str(frontend_path()))


def envelope(multiple=True):
    row = dict(year=2019, scenario='baseline', module='Health', variable='MCS',
        variable_value='Mean', stratifier='Overall', stratifier_value='Overall', metric_type='mean',
        n_runs=3, total_sample=36, min_sample=12, mean_sample=12, mean_value=20,
        sd_value=10, lower_ci=8.7, upper_ci=31.3)
    configurations = [dict(id='baseline', name='Fictional baseline', role='Baseline',
        dataset='fictional', model='a'*64, runs=[dict(folder='Baseline/run_1', seed='606')])]
    data = dict(rows=[row], comparison_available=False, notice='Fictional')
    result = dict(format='simpaths.visualiser.v1', backend={}, experiment='fictional',
                  configurations=configurations, data=data)
    if multiple:
        for number in (1, 2):
            configurations.append(dict(id='alternative-'+str(number), name='Fictional '+str(number), role='Scenario',
                dataset='fictional', model='a'*64, runs=[dict(folder=f'Scenario_{number}/run_1', seed='606')]))
        result.update(format='simpaths.visualiser.v2', comparison=dict(baseline='baseline',
            scenarios=['alternative-1', 'alternative-2']))
        result['data'] = dict(series=[dict(configuration=config['id'], rows=[dict(row,
            scenario='baseline' if index == 0 else 'scenario')]) for index, config in enumerate(configurations)],
            comparison_available=False, notice='Fictional')
    return result


class ProxyRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = rehearsal.TEMPLATE.read_text()
        self.options = dict(http_port=18080, https_port=18443, app_port=15002, base=self.root/'proxy',
                            runtime=self.root/'runtime')

    def test_real_deployment_policy_survives_only_loopback_substitutions(self):
        rendered = rehearsal.proxy_config(self.source, **self.options)
        self.assertEqual(rendered.count('listen 127.0.0.1:18080'), 2)
        self.assertEqual(rendered.count('listen 127.0.0.1:18443'), 2)
        self.assertNotIn('listen [::]', rendered)
        self.assertNotIn('multirun.example.org', rendered)
        self.assertIn('proxy_set_header Host localhost:18443;', rendered)
        self.assertIn('return 308 https://localhost:18443$request_uri;', rendered)
        self.assertIn('ssl_reject_handshake on;', rendered)
        self.assertIn('client_body_timeout 60s;', rendered)
        self.assertIn('proxy_buffering off;', rendered)
        self.assertIn('proxy_max_temp_file_size 0;', rendered)
        self.assertIn('proxy_read_timeout 300s;', rendered)
        self.assertIn('proxy_set_header X-Forwarded-For $remote_addr;', rendered)
        self.assertIn('pid '+rehearsal.nginx_path(self.root/'runtime/nginx.pid')+';', rendered)
        self.assertIn('root '+rehearsal.nginx_path(self.root/'proxy/acme')+';', rendered)
        self.assertNotIn('/tmp/nginx', rendered)
        for directive, folder in (('client_body_temp_path', 'client-body'), ('proxy_temp_path', 'proxy-body'),
                                  ('fastcgi_temp_path', 'fastcgi-body'), ('uwsgi_temp_path', 'uwsgi-body'),
                                  ('scgi_temp_path', 'scgi-body')):
            self.assertIn(directive+' '+rehearsal.nginx_path(self.root/'runtime'/folder)+';', rendered)
        self.assertNotIn('/var/cache/nginx', rendered)

    def test_changed_file_mappings_cache_or_security_anchors_stop_the_rehearsal(self):
        changes = [self.source.replace('proxy_buffering off;', 'proxy_buffering on;'),
            self.source.replace('proxy_cache off;', 'proxy_cache protected;'),
            self.source.replace('ssl_reject_handshake on;', ''),
            self.source.replace('proxy_set_header X-Forwarded-For $remote_addr;',
                                'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;'),
            self.source.replace('proxy_pass http://127.0.0.1:5002;', 'proxy_pass http://0.0.0.0:5002;'),
            self.source.replace('proxy_read_timeout 300s;', 'proxy_read_timeout 30s;'),
            self.source+'\nserver { location /data { alias /private; } }',
            self.source+'\nserver { root /private; }',
            self.source+'\nserver { location /data { try_files $uri /private; } }',
            self.source.replace('listen [::]:443 ssl;', '')]
        for text in changes:
            with self.subTest(text=text[-80:]), self.assertRaises(ValueError):
                rehearsal.proxy_config(text, **self.options)

    def test_privileged_duplicate_and_noninteger_ports_are_rejected(self):
        for port in (80, 443, 0, 65536, True, '18080', 18443):
            with self.subTest(port=port), self.assertRaises(ValueError):
                rehearsal.proxy_config(self.source, **dict(self.options, http_port=port))

    def test_quoted_paths_do_not_allow_nginx_directive_injection(self):
        escaped = rehearsal.nginx_path('/temporary folder/"name"/$variable')
        self.assertEqual(escaped, '"/temporary folder/\\"name\\"/\\$variable"')
        for path in ('/tmp/a\nroot /private;', '/tmp/a\r', '/tmp/a\x00'):
            with self.assertRaises(ValueError): rehearsal.nginx_path(path)

    def test_only_runner_database_is_accepted_without_disclosing_credentials(self):
        good = 'postgresql://postgres:FICTIONAL_SECRET@127.0.0.1:54321/jasmine_queue_test'
        rehearsal.require_test_database(good)
        for dsn in (good.replace('jasmine_queue_test', 'jasmine_multirun'),
                    good.replace('127.0.0.1', 'db.example.org'), good.replace('54321', '543'),
                    good+'?options=-csearch_path%3Dpublic', 'service=production',
                    'host=127.0.0.1 dbname=jasmine_queue_test user=postgres port=54321'):
            with self.subTest(dsn=dsn), self.assertRaises(ValueError) as error:
                rehearsal.require_test_database(dsn)
            self.assertNotIn('FICTIONAL_SECRET', str(error.exception))

    def test_both_aggregate_formats_preserve_identified_series(self):
        for multiple in (False, True):
            value = envelope(multiple)
            self.assertIs(aggregate_envelope(value), value)

    def test_comparison_binding_rejects_wrong_order_roles_missing_and_duplicate_series(self):
        good = envelope()
        changed = []
        wrong = deepcopy(good); wrong['comparison']['scenarios'].reverse(); changed.append(wrong)
        wrong = deepcopy(good); wrong['data']['series'].reverse(); changed.append(wrong)
        wrong = deepcopy(good); wrong['configurations'][1]['role'] = 'Baseline'; changed.append(wrong)
        wrong = deepcopy(good); wrong['data']['series'].pop(); changed.append(wrong)
        wrong = deepcopy(good); wrong['data']['series'][1] = deepcopy(wrong['data']['series'][0]); changed.append(wrong)
        wrong = deepcopy(good); wrong['data']['series'][1]['rows'][0]['scenario'] = 'baseline'; changed.append(wrong)
        for value in changed:
            with self.assertRaises(ValueError): aggregate_envelope(value)

    def test_raw_fields_and_nonfinite_values_are_rejected_in_both_formats(self):
        for multiple in (False, True):
            for change in (dict(id_Person='PRIVATE'), dict(mean_value=float('nan')), dict(mean_value='PRIVATE')):
                value = envelope(multiple)
                rows = value['data']['series'][0]['rows'] if multiple else value['data']['rows']
                rows[0].update(change)
                with self.assertRaises(ValueError): aggregate_envelope(value)

    def test_unknown_envelope_configuration_and_run_fields_are_rejected(self):
        for multiple in (False, True):
            value = envelope(multiple); value['raw'] = ['PRIVATE']
            with self.assertRaises(ValueError): aggregate_envelope(value)
            value = envelope(multiple); value['configurations'][0]['path'] = '/private/raw.csv'
            with self.assertRaises(ValueError): aggregate_envelope(value)
            value = envelope(multiple); value['configurations'][0]['runs'][0]['path'] = '/private/raw.csv'
            with self.assertRaises(ValueError): aggregate_envelope(value)

    def test_cookie_is_not_forwarded_to_a_redirect(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.example.org'))

    def test_https_client_preserves_case_insensitive_headers_and_cache_checks(self):
        client = object.__new__(rehearsal.HTTPSClient)
        client.origin, client.cookie, client.csrf = 'https://localhost:18443', '', ''

        def response(value, **fields):
            result = io.BytesIO(json.dumps(value).encode())
            result.status = 200
            result.headers = HTTPMessage()
            for name, field in fields.items():
                result.headers[name.replace('_', '-')] = field
            return result

        for method in (client.get, client.request):
            with patch.object(client, 'open', return_value=response({}, cache_control='no-store',
                    content_length='123', etag='"fictional"', accept_ranges='bytes')):
                status, headers, _ = method('/fictional')
            self.assertEqual(status, 200)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(headers['Content-Length'], '123')
            self.assertEqual(headers['ETag'], '"fictional"')
            self.assertEqual(headers['Accept-Ranges'], 'bytes')

        with patch.object(client, 'open', return_value=response({'ok':True}, cache_control='no-store')):
            self.assertEqual(client.api('/api/fictional'), {'ok':True})
        for fields in ({}, {'cache_control':'public, max-age=3600'}):
            with patch.object(client, 'open', return_value=response({}, **fields)), self.assertRaisesRegex(
                    AssertionError, 'permits caching'):
                client.api('/api/fictional')

        (self.root/'mail.json').write_text(json.dumps({'alice@example.org':'FICTIONAL_CODE'}))
        with patch.object(client, 'open', side_effect=[
                response({'challenge':'fictional-challenge'}, cache_control='no-store'),
                response({'authorised':True, 'csrf':'fictional-csrf'}, cache_control='no-store',
                         set_cookie='session=fictional-session; Secure; HttpOnly; SameSite=Strict')]):
            client.sign_in('alice@example.org', self.root)
        self.assertEqual(client.cookie, 'session=fictional-session')
        self.assertEqual(client.csrf, 'fictional-csrf')

    def test_cleanup_never_removes_a_container_with_another_label(self):
        process = rehearsal.Processes(self.root, self.root, SimpleNamespace(nginx=None))
        process.container_created = True
        log = io.BytesIO(); process.logs.append(log)
        result = subprocess.CompletedProcess([], 0, stdout='another task\n')
        with patch.object(rehearsal.subprocess, 'run', return_value=result) as run:
            with self.assertRaises(RuntimeError): process.close()
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('rm', run.call_args.args[0])
        self.assertTrue(log.closed)

    def test_owned_container_cleanup_and_failed_inspection_close_logs(self):
        for failed in (False, True):
            process = rehearsal.Processes(self.root, self.root, SimpleNamespace(nginx=None))
            process.container_created = True
            log = io.BytesIO(); process.logs.append(log)
            results = subprocess.TimeoutExpired('docker inspect', 30) if failed else [
                subprocess.CompletedProcess([], 0, stdout=process.name+'\n'),
                subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0)]
            with patch.object(rehearsal.subprocess, 'run', side_effect=results) as run:
                if failed:
                    with self.assertRaises(RuntimeError): process.close()
                else:
                    process.close()
                    self.assertEqual(run.call_args.args[0], ['docker', 'rm', '-f', process.name])
            self.assertTrue(log.closed)

    def test_application_stop_accepts_its_sigterm_but_rejects_failure_and_hangs(self):
        for code in (0, -signal.SIGTERM, 1, -signal.SIGKILL):
            process = rehearsal.Processes(self.root, self.root, SimpleNamespace(nginx=None))
            child = Mock(returncode=code)
            process.app = child
            if code in (0, -signal.SIGTERM):
                process.stop_app()
            else:
                with self.assertRaises(AssertionError): process.stop_app()
            child.terminate.assert_called_once()
            child.kill.assert_not_called()
            self.assertIsNone(process.app)
        process.app = child = Mock(returncode=-signal.SIGKILL)
        child.wait.side_effect = [subprocess.TimeoutExpired('application', 45), -signal.SIGKILL]
        with self.assertRaises(AssertionError): process.stop_app()
        child.kill.assert_called_once()

    def test_output_cleanup_holds_the_real_exclusive_dispatcher_lock(self):
        from jasmine_web.batch.local_executor import LocalExecutor
        from jasmine_web.batch.output_management import OutputManagement
        from jasmine_web.batch.policy import Conflict
        executor = LocalExecutor(self.root/'execution')
        service = SimpleNamespace(queue=Mock(backup_state=None))
        service.outputs = OutputManagement(service, executor, lambda _: [])
        with self.assertRaisesRegex(Conflict, 'Hold the dispatcher lock'):
            service.outputs.retire()
        with patch.object(service.outputs, 'retire', side_effect=executor._require_guard) as retire:
            rehearsal.retire_fixture_outputs(service)
            retire.assert_called_once()
            with self.assertRaises(Conflict): executor._require_guard()
            another = LocalExecutor(executor.root)
            with another.exclusive(), self.assertRaisesRegex(Conflict, 'Another dispatcher'):
                rehearsal.retire_fixture_outputs(service)
            self.assertEqual(retire.call_count, 1)

    def test_wrapper_uses_disposable_runner_and_preserves_operator_options(self):
        mask = os.umask(0o077); self.addCleanup(lambda: os.umask(mask))
        with patch.dict(os.environ), patch.object(rehearsal.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0)) as run:
            result = rehearsal.main(['--frontend', str(frontend_path()), '--output', str(self.root/'evidence'),
                '--payload-mib', '600', '--slow-seconds', '310', '--nginx-image', 'nginx:stable-alpine'])
        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        self.assertIn('--proof-only', command)
        self.assertIn(str(frontend_path()/'scripts/test_batch_queue.py'), command)
        self.assertIn(str(self.root/'evidence'), command)
        self.assertEqual(run.call_args.kwargs['env']['SIMPATHS_PROXY_PAYLOAD_MIB'], '600')
        self.assertEqual(run.call_args.kwargs['env']['SIMPATHS_PROXY_SLOW_SECONDS'], '310')

    def test_synthetic_processor_reads_private_files_without_returning_their_content(self):
        from .proxy_fixture import AggregateBackend, RAW
        path = self.root/'values.csv'; path.write_bytes(RAW*1000)
        backend = AggregateBackend(); backend.root = self.root
        progress = []
        source = dict(configuration=dict(id='baseline', role='Baseline'), files=[dict(
            name='Baseline/run_1/csv/Values.csv', path=path.name, bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest())])
        result = backend.process([source], self.root, None, progress.append)
        self.assertEqual(progress, [1])
        self.assertEqual(result['rows'][0]['scenario'], 'baseline')
        self.assertNotIn(RAW.decode(), str(result))
        path.write_bytes(b'changed')
        from jasmine_web.batch.results import OutputUnavailable
        with self.assertRaises(OutputUnavailable): backend.process([source], self.root, None, progress.append)

    def test_service_fixture_accepts_local_parser_and_captures_codes_privately(self):
        from . import proxy_fixture
        class RegistryReached(Exception): pass
        state = self.root/'private'
        settings = dict(state=str(state), schema='test_proxy_'+'a'*32, app_port=15002)
        with patch.object(proxy_fixture, 'Queue'), patch.object(proxy_fixture, 'create_registry',
                side_effect=RegistryReached) as registry:
            with self.assertRaises(RegistryReached): proxy_fixture.services(settings, 'unused')
        args, _, actual_state, deliver = registry.call_args.args
        self.assertEqual(actual_state, state)
        self.assertEqual(args.state, state)
        self.assertEqual(args.port, settings['app_port'])
        self.assertTrue(args.console_codes)
        self.assertFalse(args.notification_emails)
        self.assertEqual(args.prepared, [])
        output = io.StringIO()
        with redirect_stdout(output): asyncio.run(deliver('alice@example.org', 'FICTIONAL_CODE'))
        self.assertNotIn('FICTIONAL_CODE', output.getvalue())
        self.assertEqual(json.loads((state/'mail.json').read_text()), {'alice@example.org':'FICTIONAL_CODE'})
        self.assertEqual((state/'mail.json').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
