"""(C) Copyright 2026, by Ross Richardson

VM configuration, credential, proxy-probe and sampled-capacity boundary tests.
No mail delivery, live PostgreSQL, Docker operations or simulation data required.
@author ross richardson
"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path
from .vm_config import load_config
from .vm_web import main, private_text
from .vm_boundary import privacy_checks
from .vm_capacity import summary

TEMPLATE = Path(__file__).with_name('vm')/'multirun.toml.example'


class VMConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.text = TEMPLATE.read_text().replace('sha256:REPLACE_WITH_64_HEXADECIMAL_DIGITS', 'sha256:'+'a'*64)
        self.path = self.root/'settings.toml'

    def config(self, text=None):
        self.path.write_text(text or self.text)
        return load_config(self.path)

    def test_configuration_check_has_no_side_effects_and_scales_runtime(self):
        options = self.config()
        self.assertEqual(options.capacity, dict(cpu_millis=3000, memory_mib=6144, storage_mib=13312))
        self.assertEqual(options.runtime_per_repetition_minutes, 60)
        self.assertEqual(options.workspace_quota_socket,Path('/run/jasmine-workspace-quotas/broker.sock'))
        self.assertEqual(options.workspace_guard,Path('/usr/local/libexec/jasmine-workspace-guard'))
        with patch('socket.socket', side_effect=AssertionError('No network')), redirect_stdout(io.StringIO()):
            self.assertEqual(main(['check', '--config', str(self.path)]), 0)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['settings.toml'])

    def test_untrusted_or_accidentally_incompatible_configuration_is_rejected(self):
        replacements = [('https://multirun.example.org', 'http://multirun.example.org'),
            ('https://multirun.example.org', 'https://user:secret@multirun.example.org'),
            ('https://multirun.example.org', 'https://multirun.example.org/path'),
            ('https://multirun.example.org', 'https://multirun.example.org:443'),
            ('port = 5002', 'port = true'), ('cpu_millis = 3000', 'cpu_millis = 1000'),
            ('per_user_active = 1', 'per_user_active = 111'),
            ('private_root = "/srv/simpaths-online/private"', 'private_root = "/tmp/private"'),
            ('state = "/srv/simpaths-online/private/multirun"', 'state = "/srv/elsewhere"'),
            ('retention_cleanup = false', 'retention_cleanup = true'),
            ('max_configurations = 100', 'max_configurations = 101'),
            ('max_repetitions = 12', 'max_repetitions = 1001'),
            ('runtime_budget_multiplier = 3', 'runtime_budget_multiplier = 4'),
            ('max_repetitions = 12', 'max_repetitions = 1000\nunknown_limit = 1'),
            ('preview = false', 'preview = true'),
            ('prepared = []', 'prepared = ["/tmp/inputs"]'),
            ('prepared = []', 'prepared = [{id="bad"}]'),
            ('cache_gib = 10', 'cache_gib = 0'),
            ('quota_socket = "/run/jasmine-workspace-quotas/broker.sock"', 'quota_socket = "relative/socket"'),
            ('quota_socket = "/run/jasmine-workspace-quotas/broker.sock"', 'quota_socket = "/run/'+('a'*101)+'"'),
            ('guard = "/usr/local/libexec/jasmine-workspace-guard"', 'guard = "../guard"')]
        for old, new in replacements:
            with self.subTest(new=new), self.assertRaises(ValueError):
                self.config(self.text.replace(old, new))
        with self.assertRaises(ValueError):
            self.config(self.text+'\n[unknown]\nvalue = 1\n')
        with self.assertRaises(ValueError):
            self.config(self.text.replace('max_repetitions = 12', 'max_repetitions = 1000')
                .replace('runtime_per_repetition_minutes = 60', 'runtime_per_repetition_minutes = 1440'))

    def test_interactive_headroom_keeps_a_batch_workload_possible(self):
        configured = self.config(self.text.replace('cpu_millis = 3000', 'cpu_millis = 6000\nhold_cpu_millis = 2000')
            .replace('memory_mib = 6144', 'memory_mib = 16384\nhold_memory_mib = 8192')
            .replace('storage_mib = 13312', 'storage_mib = 32768\nhold_storage_mib = 10240'))
        self.assertEqual(configured.holdback, dict(cpu_millis=2000, memory_mib=8192, storage_mib=10240))
        with self.assertRaises(ValueError):
            self.config(self.text.replace('cpu_millis = 3000', 'cpu_millis = 3000\nhold_cpu_millis = 2000'))

    def test_credential_permissions_links_and_bounded_contents(self):
        file = self.root/'credential'
        file.write_text('secret value\n'); file.chmod(0o600)
        self.assertEqual(private_text(file), 'secret value')
        linked = self.root/'link'; linked.symlink_to(file)
        with self.assertRaises(ValueError): private_text(linked)
        file.chmod(0o644)
        with self.assertRaises(ValueError): private_text(file)
        file.chmod(0o600)
        for value in ('', 'one\ntwo', 'x'*8193):
            file.write_text(value)
            with self.assertRaises(ValueError): private_text(file)
        file.write_text('secret')
        os.link(file, self.root/'hardlink')
        with self.assertRaises(ValueError): private_text(file)

    def test_remote_database_needs_verified_tls_and_never_prints_credentials(self):
        old = list(sys.path); self.addCleanup(lambda: setattr(sys, 'path', old))
        sys.path.insert(0, str(frontend_path()))
        from .vm_web import database_dsn
        options = self.config()
        options.dsn_file = self.root/'dsn'
        options.dsn_file.touch(mode=0o600)
        for value in ('dbname=sample user=sample host=127.0.0.1 password=PRIVATE_SECRET',
                      'dbname=sample user=sample host=database.example.org sslmode=verify-full'):
            options.dsn_file.write_text(value)
            self.assertEqual(database_dsn(options), value)
        for value in ('dbname=sample user=sample host=database.example.org password=PRIVATE_SECRET',
                      'dbname=sample user=sample hostaddr=192.0.2.1 password=PRIVATE_SECRET',
                      'invalid password=PRIVATE_SECRET'):
            options.dsn_file.write_text(value)
            with self.assertRaises(ValueError) as error: database_dsn(options)
            self.assertNotIn('PRIVATE_SECRET', str(error.exception))

    def test_smtp_checks_and_verification_mail_use_online_brand_without_console_codes(self):
        old = list(sys.path); self.addCleanup(lambda: setattr(sys, 'path', old))
        sys.path.insert(0, str(frontend_path()))
        from .vm_web import check_smtp, verification_mail
        env = dict(SMTP_HOST='smtp.example.org', SMTP_FROM_EMAIL='team@example.org')
        check_smtp(env)
        for changes in (dict(SMTP_HOST=''), dict(SMTP_USE_TLS='false'), dict(SMTP_PORT='0'),
                        dict(SMTP_LOGIN_EMAIL='only-login')):
            with self.subTest(changes=changes), self.assertRaises(ValueError): check_smtp({**env, **changes})
        import asyncio
        with patch.dict(os.environ, env), patch('jasmine_web.contact.deliver_email') as send:
            asyncio.run(verification_mail('user@example.org', '123456'))
            message = send.call_args.args[0]
            self.assertIn('SimPaths Online', message['Subject'])
            self.assertEqual(message['To'], 'user@example.org')
            self.assertIn('123456', message.get_content())

    def test_private_paths_are_checked_anonymously_and_while_signed_in(self):
        calls = []
        def get(path, **kwargs):
            calls.append((path, kwargs['cookie']))
            if path == '/api/session':
                return 200, {'Cache-Control': 'no-store'}, b'{"signed_in":true}'
            return 404, {}, b'Not found'
        checks = privacy_checks(get, 'test.txt', b'PRIVATE', cookie='owner', other_cookie='other')
        self.assertGreater(len(checks), 30)
        self.assertIn(('/execution/test.txt', 'owner'), calls)
        for status, body in ((200, b'PRIVATE'), (200, b'<html>directory listing</html>'), (302, b'')):
            def leak(path, **kwargs): return status, {}, body
            with self.assertRaises(ValueError): privacy_checks(leak, 'test.txt', b'PRIVATE')

    def test_provider_ranges_and_cross_owner_aggregate_denials_are_required(self):
        old = list(sys.path); self.addCleanup(lambda: setattr(sys, 'path', old))
        sys.path.insert(0, str(frontend_path()))
        row = dict(year=2019, scenario='baseline', module='Health', variable='MCS',
            variable_value='Mean', stratifier='Overall', stratifier_value='Overall', metric_type='mean',
            n_runs=3, total_sample=36, min_sample=12, mean_sample=12, mean_value=20,
            sd_value=10, lower_ci=8.7, upper_ci=31.3)
        aggregate = dict(format='simpaths.visualiser.v1', backend={}, experiment='experiment',
            configurations=[dict(id='baseline', name='Fictional baseline', role='Baseline',
                dataset='fictional', model='a'*64, runs=[dict(folder='Baseline/run_1', seed='606')])],
            data=dict(rows=[row], comparison_available=False, notice='Test'))
        calls = []
        def get(path, cookie='', headers=None, **kwargs):
            calls.append((path, cookie, headers))
            value = None
            if path == '/api/session': value = dict(signed_in=True)
            if path == '/api/results/experiment' and cookie == 'owner':
                value = dict(configurations=[dict(id='restricted', state='succeeded', output_state='retained',
                    downloadable=False, visualisable=True)])
            if path == '/api/visualiser/key/data' and cookie == 'owner': value = aggregate
            return (200, {'Cache-Control': 'no-store'}, json.dumps(value).encode()) if value else (403, {}, b'Denied')
        privacy_checks(get, 'test.txt', b'PRIVATE', cookie='owner', other_cookie='other',
            experiment='experiment', restricted_job='restricted', visualiser_key='key')
        self.assertIn(('/downloads/restricted', 'owner', {'Range':'bytes=0-63'}), calls)
        self.assertIn(('/api/visualiser/key/data', 'other', None), calls)
        aggregate['data']['rows'][0]['id_Person'] = 'private'
        with self.assertRaises(ValueError):
            privacy_checks(get, 'test.txt', b'PRIVATE', cookie='owner', visualiser_key='key')

    def test_capacity_summary_keeps_transient_peak_after_working_files_are_released(self):
        first = dict(attempts=[dict(attempt='one', retained_workspace_bytes=100, elapsed_seconds=None)],
            private_storage=dict(free_bytes=200), download_cache_bytes=10,
            visualiser_cache_bytes=0, docker_stats_unavailable=False)
        last = deepcopy(first)
        last['attempts'][0].update(retained_workspace_bytes=2, elapsed_seconds=60)
        last['private_storage']['free_bytes'] = 300
        result = summary([first, last])
        self.assertEqual(result['attempts'][0]['peak_workspace_bytes'], 100)
        self.assertEqual(result['attempts'][0]['retained_workspace_bytes'], 2)
        self.assertEqual(result['attempts'][0]['elapsed_seconds'], 60)
        self.assertEqual(result['minimum_free_bytes'], 200)


if __name__ == '__main__':
    unittest.main()
