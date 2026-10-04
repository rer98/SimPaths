"""(C) Copyright 2026, by Ross Richardson

Offline supervisor policy, fixture isolation and recovery evidence checks.
No systemd bus, Docker daemon, database or listener is contacted by these tests.
@author ross richardson
"""
from copy import deepcopy
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

from deploy._workflow import frontend_path
from deploy.acceptance import _supervisor as supervision
from deploy.acceptance import _recovery_fixture as fixture
from deploy.acceptance import run_recovery_rehearsal as proof


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.single = self.root/'single'; self.single.mkdir()
        self.inputs = self.root/'inputs'; self.inputs.mkdir()
        self.tag = 'a'*32
        self.supervisor = supervision.Supervisor(self.tag)
        self.source = (Path(__file__).resolve().parents[1]/'multirun/vm/simpaths-multirun.service').read_text()
        self.settings = dict(tag=self.tag, state=str(self.root), single_state=str(self.single), inputs=str(self.inputs),
            schema='test_recovery_'+'b'*32, batch_schema='test_recovery_'+'c'*32,
            image='sha256:'+'d'*64, single_port=15001, batch_port=15002)
        self.dsn = 'postgresql://postgres:FICTIONAL@127.0.0.1:15432/jasmine_queue_test'
        self.paths = dict(python=self.root/'python', script=self.root/'proof.py', settings=self.root/'settings.json',
            environment=self.root/'fixture.env', workdir=self.root, log=self.root/'log.txt')

    def unit(self, role='single', **values):
        return dict(Id=self.supervisor.name(role), Transient='yes', LoadState='loaded', ActiveState='active',
            SubState='running', Result='success', MainPID='12345', NRestarts='0', **values)

    def owned(self, role='single'):
        self.supervisor.units.add(self.supervisor.name(role))

    def test_both_native_templates_use_the_asserted_restart_policy(self):
        self.assertEqual(supervision.restart_policy(self.source), supervision.EXPECTED)
        single = (frontend_path()/'deploy/simpaths/simpaths-singlerun.service').read_text()
        self.assertEqual(supervision.restart_policy(single), supervision.EXPECTED)
        self.assertIn('ProtectHome=true', single)
        self.assertIn('EnvironmentFile=/etc/simpaths-online/singlerun.env', single)
        self.assertNotIn('SESSION_SECRET=', single)

    def test_changed_delay_burst_window_group_or_mode_requires_explicit_update(self):
        for field, value in dict(Restart='on-failure', RestartSec='1', StartLimitBurst='6',
            StartLimitIntervalSec='60', KillMode='process', TimeoutStopSec='30', UMask='0022').items():
            changed = self.source.replace(field+'='+supervision.EXPECTED[field], field+'='+value)
            with self.subTest(field=field), self.assertRaises(ValueError): supervision.restart_policy(changed)

    def test_transient_command_copies_policy_and_contains_no_credentials(self):
        argv = self.supervisor.start_command('single', supervision.EXPECTED, **self.paths)
        self.assertEqual(argv[:3], ['systemd-run','--user','--quiet'])
        for field, value in supervision.EXPECTED.items(): self.assertIn('--property='+field+'='+value, argv)
        self.assertIn('--unit='+self.supervisor.name('single'), argv)
        self.assertIn('--property=EnvironmentFile='+str(self.paths['environment']), argv)
        self.assertEqual(argv[-2:], ['--role','single'])
        self.assertNotIn(self.dsn, ' '.join(argv))
        self.assertNotIn('ProtectHome', ' '.join(argv))  # Not a production hardening proof.

    def test_supervised_single_startup_loads_the_real_static_mount_with_a_private_key(self):
        frontend = frontend_path()
        supervisor = Mock()
        proof.start_frontends(supervisor, {'single':supervision.EXPECTED, 'batch':supervision.EXPECTED},
            self.paths, frontend, self.root)
        starts = {call.args[0]:call.kwargs for call in supervisor.start.call_args_list}
        self.assertEqual(starts['batch']['workdir'], proof.ROOT)
        settings = dict(self.settings, secret='fictional-owner-key', admin='fictional-admin',
            catalogue=str(self.single/'catalogue.json'))
        Path(settings['catalogue']).write_text(json.dumps({'models':[fixture.MODEL]}))
        checkout_key = frontend/'.sesskey'
        before = checkout_key.stat() if checkout_key.exists() else None
        # A separate process loads the actual app without a listener/database.
        # Use the exact working directory selected for the supervised child.
        program = '''
import json, os, sys
from pathlib import Path
from deploy.acceptance._recovery_fixture import single_environment, load_single_app, FRONTEND
settings = json.load(sys.stdin)
os.environ.pop('VM_POSTGRES_DSN_FILE', None)
os.environ.update(single_environment(settings, settings.pop('dsn')))
web = load_single_app(settings)
assert Path(web.app.key_fname) == Path(settings['single_state'])/'.sesskey'
assert Path(web.app.key_fname).stat().st_mode & 0o777 == 0o600
assert load_single_app(settings).app.secret_key == web.app.secret_key
mount = next(route for route in web.app.router.routes if route.path == '/static')
path, info = mount.app.lookup_path('favicon.png')
assert Path(path) == FRONTEND/'static/favicon.png'
assert info.st_size == (FRONTEND/'static/favicon.png').stat().st_size > 0
'''
        environment = dict(os.environ, JASMINE_WEB_REPO=str(frontend), PYTHONPATH=str(proof.ROOT),
                           PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-c', 'import os; os.umask(0o077)\n'+program],
            input=json.dumps(dict(settings, dsn=self.dsn)), cwd=starts['single']['workdir'],
            env=environment, capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = checkout_key.stat() if checkout_key.exists() else None
        self.assertEqual(before, after)

    def test_readiness_reports_a_failed_unit_without_waiting_for_http(self):
        supervisor = Mock(); supervisor.show.return_value = {'ActiveState':'failed'}
        client = Mock()
        with self.assertRaisesRegex(RuntimeError, 'single.log'):
            proof.wait_ready(client, '/', supervisor=supervisor, role='single')
        client.request.assert_not_called()

    def test_readiness_waits_for_a_successful_http_response(self):
        supervisor = Mock(); supervisor.show.return_value = {'ActiveState':'active'}
        client = Mock(); client.request.side_effect = [OSError('not listening yet'), (503, {}, b''), (200, {}, b'')]
        def poll(check, **kwargs):
            self.assertFalse(check())
            self.assertFalse(check())
            self.assertTrue(check())
        with patch.object(proof, 'until', side_effect=poll) as wait:
            proof.wait_ready(client, '/healthz', supervisor=supervisor, role='batch')
        self.assertEqual(client.request.call_count, 3)
        wait.assert_called_once()

    def test_direct_single_client_accepts_json_without_proxy_headers_but_rejects_denial(self):
        client = proof.single_client('http://127.0.0.1:15001')
        with patch.object(client, 'request', return_value=(200, {}, b'{"status":"building"}')):
            self.assertEqual(client.api('/build-json/fictional', {}), {'status':'building'})
        with patch.object(client, 'request', return_value=(403, {}, b'{"error":"denied"}')):
            with self.assertRaisesRegex(AssertionError, 'HTTP 403'):
                client.api('/status/fictional')

    def test_batch_client_still_requires_the_application_no_store_header(self):
        from deploy.multirun.mail_rehearsal import LocalClient
        client = LocalClient('http://127.0.0.1:15002')
        with patch.object(client, 'request', return_value=(200, {}, b'{}')):
            with self.assertRaisesRegex(AssertionError, 'permits caching'):
                client.api('/api/session')
        with patch.object(client, 'request', return_value=(200, {'Cache-Control':'no-store'}, b'{}')):
            self.assertEqual(client.api('/api/session'), {})

    def test_venv_interpreter_link_is_preserved_without_allowing_linked_state(self):
        executable = self.root/'real-python'; executable.write_text('fictional executable')
        link = self.root/'python'; link.symlink_to(executable)
        argv = self.supervisor.start_command('single', supervision.EXPECTED, **self.paths)
        self.assertIn(str(link), argv)
        with self.assertRaises(ValueError): supervision.safe_path(link)

    def test_substitution_newline_or_linked_directory_is_rejected(self):
        for path in (self.root/'a%b', self.root/'a$b', self.root/'a\nb'):
            with self.assertRaises(ValueError): supervision.safe_path(path)
        linked = self.root/'linked'; linked.symlink_to(self.inputs, target_is_directory=True)
        with self.assertRaises(ValueError): supervision.safe_path(linked/'child')

    def test_environment_is_private_quoted_and_never_shell_interpolated(self):
        path = self.root/'private.env'
        supervision.environment_file(path, dict(JASMINE_BATCH_TEST_DSN=self.dsn, EXAMPLE='literal "$value" \\ test'))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        text = path.read_text()
        self.assertIn('EXAMPLE="literal \\"$value\\" \\\\ test"', text)
        self.assertIn(self.dsn, text)
        with self.assertRaises(FileExistsError): supervision.environment_file(path, {})

    def test_invalid_environment_never_creates_a_file(self):
        for values in ({'bad-name':'x'}, {'KEY':'x\ny'}, {'KEY':123}):
            with self.assertRaises(ValueError): supervision.environment_file(self.root/'bad.env', values)
            self.assertFalse((self.root/'bad.env').exists())

    def test_only_unique_opaque_fixture_units_can_start(self):
        for tag in ('user.service', '', 'a'*31):
            with self.assertRaises(ValueError): supervision.Supervisor(tag)
        with self.assertRaises(ValueError): self.supervisor.name('existing.service')
        with patch.object(self.supervisor, 'command') as call:
            self.supervisor.start('single', supervision.EXPECTED, **self.paths)
            with self.assertRaises(ValueError): self.supervisor.start('single', supervision.EXPECTED, **self.paths)
            self.assertEqual(call.call_count, 1)

    def test_uncertain_start_reply_keeps_cleanup_ownership(self):
        with patch.object(self.supervisor, 'command', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError): self.supervisor.start('batch', supervision.EXPECTED, **self.paths)
        self.assertIn(self.supervisor.name('batch'), self.supervisor.units)

    def test_probe_accepts_running_or_degraded_manager_but_not_missing_bus(self):
        for status in ('running', 'degraded', 'starting'):
            with patch.object(self.supervisor, 'command', return_value=SimpleNamespace(stdout=status)):
                self.supervisor.probe()
        with patch.object(self.supervisor, 'command', return_value=SimpleNamespace(stdout='offline')):
            with self.assertRaisesRegex(RuntimeError, 'user systemd manager'): self.supervisor.probe()

    def test_nonfixture_units_and_missing_units_cannot_be_crashed(self):
        with self.assertRaises(ValueError): self.supervisor.crash('single')
        self.owned()
        for fields in ({'Transient':'no'}, {'Id':'pre-existing.service'}):
            value = self.unit(); value.update(fields)
            output = '\n'.join(key+'='+field for key, field in value.items())
            with patch.object(self.supervisor, 'command', return_value=SimpleNamespace(stdout=output)) as call:
                with self.assertRaises(ValueError): self.supervisor.crash('single')
                self.assertEqual(call.call_count, 1)

    def test_crash_targets_only_main_process_of_owned_unit(self):
        self.owned()
        with patch.object(self.supervisor, 'show', return_value=self.unit()), patch.object(self.supervisor, 'command') as call:
            self.supervisor.crash('single')
            call.assert_called_once_with(['systemctl','--user','kill','--kill-whom=main','--signal=SIGKILL',self.supervisor.name('single')])

    def test_rate_limit_journal_is_scoped_to_owned_unit_and_discards_other_details(self):
        self.owned('limit')
        unit = self.supervisor.name('limit')
        denied = dict(USER_UNIT=unit, MESSAGE=unit+': Start request repeated too quickly.',
                      __REALTIME_TIMESTAMP='1791104480549686', PRIVATE='fictional private detail')
        unrelated = dict(denied, USER_UNIT='existing.service')
        other_message = dict(USER_UNIT=unit, MESSAGE='Fictional process output')
        output = '\n'.join(json.dumps(value) for value in (unrelated, other_message, denied))
        with patch.object(self.supervisor, 'command', return_value=SimpleNamespace(stdout=output)) as call:
            events = self.supervisor.start_limit_events('limit')
        self.assertEqual(events, [{key:denied[key] for key in ('USER_UNIT','MESSAGE','__REALTIME_TIMESTAMP')}])
        argv = call.call_args.args[0]
        self.assertIn('--unit='+unit, argv)
        self.assertIn('--lines=100', argv)

    def test_journal_cannot_be_read_for_a_service_not_created_by_the_rehearsal(self):
        with patch.object(self.supervisor, 'command') as call:
            with self.assertRaises(ValueError): self.supervisor.start_limit_events('limit')
        call.assert_not_called()

    def limited_unit(self):
        value = self.unit('limit')
        value.update(Restart='always', RestartUSec='10s',StartLimitIntervalUSec='5min',StartLimitBurst='5',
            KillMode='control-group',KillSignal='15',Type='simple',TimeoutStopUSec='infinity',UMask='0077',
            ActiveState='failed',SubState='failed',MainPID='0',NRestarts='5',Result='exit-code',
            ExecMainCode='1',ExecMainStatus='42')
        return value

    def test_rate_limit_accepts_retained_exit_code_only_with_five_starts_and_manager_denial(self):
        starts = [dict(pid=100+ordinal, at=10*ordinal) for ordinal in range(5)]
        path = self.root/'starts.json'; path.write_text(json.dumps(starts))
        supervisor = Mock(); supervisor.start_limit_events.return_value = [dict(MESSAGE='manager denial')]
        for result in ('exit-code','start-limit-hit'):
            supervisor.show.return_value = dict(self.limited_unit(), Result=result)
            report = {}
            state = proof.restart_limit_state(supervisor, path, report)
            self.assertEqual(state['starts'], starts)
            self.assertEqual(state['properties']['Result'], result)
            self.assertEqual(report['restart_limit_observation']['denied_start_events'], state['denied_start_events'])

    def test_failed_state_or_result_label_without_manager_denial_does_not_prove_rate_limit(self):
        path = self.root/'starts.json'; path.write_text(json.dumps([dict(pid=i) for i in range(5)]))
        supervisor = Mock(); supervisor.start_limit_events.return_value = []
        for fields in (dict(NRestarts='4',ActiveState='activating',SubState='auto-restart'),
                       dict(NRestarts='0'), dict(Result='start-limit-hit')):
            supervisor.show.return_value = dict(self.limited_unit(), **fields)
            report = {}
            self.assertFalse(proof.restart_limit_state(supervisor, path, report))
            self.assertIn('properties', report['restart_limit_observation'])

    def test_rate_limit_rejects_sixth_execution_duplicate_pid_and_wrong_exit(self):
        supervisor = Mock(); supervisor.show.return_value = self.limited_unit()
        supervisor.start_limit_events.return_value = [dict(MESSAGE='manager denial')]
        path = self.root/'starts.json'
        for starts in ([dict(pid=i) for i in range(6)], [dict(pid=1) for i in range(5)]):
            path.write_text(json.dumps(starts))
            with self.assertRaises(AssertionError): proof.restart_limit_state(supervisor, path, {})
        path.write_text(json.dumps([dict(pid=i) for i in range(5)]))
        supervisor.show.return_value = dict(self.limited_unit(), ExecMainStatus='0')
        with self.assertRaises(AssertionError): proof.restart_limit_state(supervisor, path, {})

    def test_inactive_or_invalid_pid_cannot_receive_crash_signal(self):
        self.owned()
        for fields in ({'ActiveState':'failed'}, {'MainPID':'0'}, {'MainPID':'1'}):
            value = self.unit(); value.update(fields)
            with patch.object(self.supervisor, 'show', return_value=value), patch.object(self.supervisor, 'command') as call:
                with self.assertRaises(ValueError): self.supervisor.crash('single')
                call.assert_not_called()

    def test_cleanup_stops_and_resets_only_owned_units(self):
        self.owned()
        with patch.object(self.supervisor, 'show', return_value=self.unit()), patch.object(self.supervisor, 'command') as call:
            self.supervisor.close()
        self.assertEqual([call.args[0][-1] for call in call.call_args_list], [self.supervisor.name('single')]*2)
        self.assertNotIn('disable', str(call.call_args_list)); self.assertNotIn('daemon-reload', str(call.call_args_list))

    def test_collected_transient_unit_is_safe_to_clean_but_other_errors_fail(self):
        self.owned()
        with patch.object(self.supervisor, 'show', return_value=None), patch.object(self.supervisor, 'command') as call:
            self.supervisor.close(); call.assert_not_called()
        with patch.object(self.supervisor, 'show', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError): self.supervisor.close()

    def test_guard_accepts_only_disposable_loopback_database_and_matching_directories(self):
        self.assertEqual(fixture.validate(self.settings, self.dsn), self.settings)
        for dsn in (self.dsn.replace('127.0.0.1','remote.example.org'), self.dsn.replace('jasmine_queue_test','production')):
            with self.assertRaises(ValueError): fixture.validate(self.settings, dsn)
        for changes in (dict(schema='jasmine_vm'), dict(batch_schema=self.settings['schema']), dict(tag='existing'),
            dict(image='python:latest'), dict(single_port=80), dict(batch_port=15001), dict(single_port=True),
            dict(inputs=str(self.root.parent)), dict(state=str(self.root/'missing'))):
            value = dict(self.settings, **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError): fixture.validate(value, self.dsn)

    def test_short_fixture_leases_do_not_change_attempt_cap_or_runtime_budget(self):
        policy = fixture.fixture_policy()
        self.assertEqual((policy.lease_seconds,policy.retry_delay_seconds), (5,1))
        self.assertEqual((policy.max_attempts,policy.attempt_seconds,policy.total_seconds), (3,3600,10800))

    def test_small_model_uses_one_frozen_resource_binding_for_each_configuration(self):
        model = fixture.SmallBrowser()
        value = model.experiment(dict(dataset_id='example',model_digest=self.settings['image'],
            datasets={'example':dict(model_digest=self.settings['image'])}), dict(name='Fictional',
            run_sets=[dict(id='first',name='First')],repetitions=2,baseline='first',auto_retry=True))
        self.assertEqual(value['seed_plan'], ['606','607'])
        self.assertEqual(value['resources'], fixture.BATCH)
        self.assertEqual(value['run_sets'][0]['execution']['resources'], vars(fixture.BATCH))

    def test_batch_validation_rejects_double_execution_or_wrong_seeds(self):
        adapter = fixture.Adapter(self.settings); work = self.root/'work'; work.mkdir()
        lease = SimpleNamespace(specification=dict(seeds=['606','607']))
        (work/'starts.txt').write_text('started\nstarted\n')
        (work/'results.json').write_text('[{"seed":"606"},{"seed":"607"}]')
        with self.assertRaisesRegex(ValueError, 'more than once'): adapter.validate(lease,work)
        (work/'starts.txt').write_text('started\n'); (work/'results.json').write_text('[{"seed":"999"}]')
        with self.assertRaisesRegex(ValueError, 'seed mismatch'): adapter.validate(lease,work)

    def test_known_stopped_container_does_not_allow_another_session_to_remove_it(self):
        containers = fixture.Containers(self.settings)
        metadata = {'Config':{'Labels':dict(**{'simpaths.recovery':self.tag,'jasmine.session_id':'other','jasmine.model_id':'fictional'})}}
        containers.docker = Mock(); containers.docker.inspect.return_value = metadata
        self.assertFalse(containers.stop_container('id', session_id='mine',model_id='fictional'))
        containers.docker.call.assert_not_called()

    def test_batch_cleanup_refuses_a_changed_container_identity(self):
        path = self.root/'execution/owned'; path.mkdir(parents=True)
        (path/'container.json').write_text('{"id":"fictional-container"}')
        (path/'identity.json').write_text('{"execution_key":"owned"}')
        docker = Mock(); docker.inspect.return_value = {'Config':{'Labels':{'jasmine.batch.identity':'changed'}}}
        with patch('jasmine_web.batch.docker_executor.DockerCLI', return_value=docker):
            with self.assertRaisesRegex(ValueError, 'unrelated batch'): proof.remove_containers(self.settings)
        docker.call.assert_not_called()

    def test_effective_supervisor_property_mismatch_is_rejected(self):
        properties = dict(Restart='always', RestartUSec='10s',StartLimitIntervalUSec='5min',StartLimitBurst='5',
            KillMode='control-group',KillSignal='15',Type='simple',TimeoutStopUSec='infinity',UMask='0077')
        proof.verify_policy(properties)
        for field in properties:
            with self.subTest(field=field), self.assertRaises(AssertionError): proof.verify_policy(dict(properties, **{field:'wrong'}))

    def test_inspection_outage_hides_confirmation_without_stopping_a_container(self):
        docker = fixture.GuardedDocker(self.root)
        with patch.object(fixture.DockerCLI, 'inspect', return_value={'State':{'Running':True}}) as inspect:
            self.assertTrue(docker.inspect('fictional')['State']['Running'])
            (self.root/'unconfirmed-docker').write_text('fault\n')
            with self.assertRaises(fixture.DockerUnavailable): docker.inspect('fictional')
            self.assertEqual(inspect.call_count, 1)


if __name__ == '__main__':
    unittest.main()
