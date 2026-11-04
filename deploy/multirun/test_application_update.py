"""(C) Copyright 2026, by Ross Richardson

Fictional update coordination checks for drain, live handover and safe failures.
No containers, services, mail, database or deployment programs are started.
@author ross richardson
"""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from . import application_update as update
from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))

REFERENCE = '00000000-0000-4000-8000-000000000001'
CANDIDATE = 'a'*64
VERIFIED = 'b'*64


class ConfiguredQueueTests(unittest.TestCase):
    def setUp(self):
        from . import privilege_config,vm_config,vm_web
        from jasmine_web.batch import store
        temporary=tempfile.TemporaryDirectory(prefix='simpaths-update-context-')
        self.addCleanup(temporary.cleanup);self.front=Path(temporary.name)
        self.config=Path('/etc/simpaths-online/multirun.toml')
        self.options=SimpleNamespace(frontend=self.front,pool_id='fictional-pool')
        self.secret='dbname=fictional user=fictional password=PRIVATE_CREDENTIAL'
        self.queue=object();stack=ExitStack();self.addCleanup(stack.close)
        before=list(sys.path);self.addCleanup(lambda:sys.path.__setitem__(slice(None),before))
        self.trusted=stack.enter_context(patch.object(privilege_config,'trusted_installation'))
        stack.enter_context(patch.object(vm_config,'load_config',return_value=self.options))
        self.storage=stack.enter_context(patch.object(vm_web,'check_storage'))
        self.dsn=stack.enter_context(patch.object(vm_web,'database_dsn',return_value=self.secret))
        self.constructor=stack.enter_context(patch.object(store,'Queue',return_value=self.queue))
        self.uid=stack.enter_context(patch.object(update.os,'getuid',return_value=0))
        self.euid=stack.enter_context(patch.object(update.os,'geteuid',return_value=0))
        self.account=stack.enter_context(patch.object(update.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=999)))
        self.child=stack.enter_context(patch.object(update.subprocess,'run',
            return_value=SimpleNamespace(returncode=0,stdout=json.dumps(dict(dsn=self.secret)),stderr='')))

    def test_root_reads_validated_service_response_without_relaxing_owner_checks(self):
        options,queue=update.configured_queue(self.config)
        self.assertIs(options,self.options);self.assertIs(queue,self.queue)
        self.storage.assert_not_called();self.dsn.assert_not_called()
        self.trusted.assert_any_call(self.config)
        self.trusted.assert_any_call(self.front,directory=True)
        self.account.assert_called_once_with('simpaths-online')
        args=self.child.call_args.args[0]
        self.assertEqual(args[:5],['runuser','-u','simpaths-online','--',str(update.ROOT/'venv/bin/python')])
        self.assertEqual(args[5:8],['-I','-B','-c'])
        self.assertNotIn(self.secret,' '.join(args))
        self.assertNotIn(self.secret,str(self.child.call_args.kwargs))
        self.assertNotIn('shell',self.child.call_args.kwargs)
        self.assertTrue(self.child.call_args.kwargs['capture_output'])
        self.constructor.assert_called_once_with(self.secret,self.options.pool_id)

    def test_ordinary_service_still_uses_the_original_direct_validators(self):
        self.uid.return_value=self.euid.return_value=999
        self.assertEqual(update.configured_queue(self.config),(self.options,self.queue))
        self.storage.assert_called_once_with(self.options);self.dsn.assert_called_once_with(self.options)
        self.child.assert_not_called();self.account.assert_not_called()

    def test_nonroot_service_validation_failure_constructs_no_queue_and_starts_no_child(self):
        self.uid.return_value=self.euid.return_value=997
        self.storage.side_effect=ValueError('Private storage is unavailable')
        with self.assertRaises(ValueError):update.configured_queue(self.config)
        self.constructor.assert_not_called();self.child.assert_not_called();self.dsn.assert_not_called()

    def test_private_credential_reader_still_rejects_a_different_real_uid(self):
        from .vm_web import private_text
        path=self.front/'fictional.dsn';path.write_text(self.secret);path.chmod(0o600)
        self.uid.return_value=path.stat().st_uid+1
        with self.assertRaises(ValueError):private_text(path)
        self.uid.return_value=path.stat().st_uid
        self.assertEqual(private_text(path),self.secret)

    def test_child_failure_and_timeout_keep_private_details_out_of_operator_errors(self):
        failures=[SimpleNamespace(returncode=1,stdout=self.secret,stderr=self.secret),
                  update.subprocess.TimeoutExpired('fixed-service-context',30,output=self.secret,stderr=self.secret)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.child.reset_mock();self.constructor.reset_mock()
                if isinstance(failure,Exception):self.child.side_effect=failure
                else:self.child.side_effect=None;self.child.return_value=failure
                out=io.StringIO();err=io.StringIO()
                with redirect_stdout(out),redirect_stderr(err):
                    result=update.main(['--config',str(self.config),'status'])
                self.assertEqual(result,1)
                self.assertNotIn(self.secret,out.getvalue()+err.getvalue())
                self.assertNotIn('PRIVATE_CREDENTIAL',out.getvalue()+err.getvalue())
                self.constructor.assert_not_called()

    def test_malformed_or_unbounded_child_responses_never_construct_a_queue(self):
        bodies=['not json','null','[]',json.dumps(dict(dsn=self.secret,unexpected=True)),
                json.dumps(dict(dsn='')),json.dumps(dict(dsn=' ')),json.dumps(dict(dsn=['unexpected'])),
                json.dumps(dict(dsn='line\nbreak')),json.dumps(dict(dsn='\x00')),
                json.dumps(dict(dsn='x'*8193)),' '*65537]
        for body in bodies:
            with self.subTest(size=len(body)):
                self.child.return_value=SimpleNamespace(returncode=0,stdout=body,stderr='PRIVATE_CREDENTIAL')
                with self.assertRaises(ValueError) as raised:update.configured_queue(self.config)
                self.assertNotIn('PRIVATE_CREDENTIAL',str(raised.exception))
                self.constructor.assert_not_called()

    def test_root_alias_or_mixed_real_uid_is_refused_before_starting_a_child(self):
        for real_uid,service_uid in ((999,999),(0,0)):
            with self.subTest(real_uid=real_uid,service_uid=service_uid):
                self.uid.return_value=real_uid;self.account.return_value=SimpleNamespace(pw_uid=service_uid)
                with self.assertRaises(ValueError):update.configured_queue(self.config)
                self.child.assert_not_called();self.constructor.assert_not_called()

    def test_runtime_writable_frontend_is_rejected_before_root_imports_or_child(self):
        def trusted(path,**options):
            if options.get('directory'):raise ValueError('Runtime-writable code')
        self.trusted.side_effect=trusted
        with self.assertRaises(ValueError):update.configured_queue(self.config)
        self.child.assert_not_called();self.constructor.assert_not_called()

    def test_cli_root_status_uses_the_context_without_printing_its_credential(self):
        from jasmine_web.batch import update_admission
        out=io.StringIO();err=io.StringIO()
        with (patch.object(update_admission,'status',return_value=dict(current=None)) as status,
              redirect_stdout(out),redirect_stderr(err)):
            result=update.main(['--config',str(self.config),'status'])
        self.assertEqual(result,0);status.assert_called_once_with(self.queue)
        self.assertNotIn(self.secret,out.getvalue()+err.getvalue())
        self.child.assert_called_once();self.constructor.assert_called_once()

    def test_cli_root_start_uses_the_validated_queue_before_fixed_launcher(self):
        out=io.StringIO()
        with patch.object(update,'launch',return_value=dict(state='pending')) as launch,redirect_stdout(out):
            result=update.main(['--config',str(self.config),'start','--package','/root/reviewed',
                                '--candidate',CANDIDATE])
        self.assertEqual(result,0);self.child.assert_called_once()
        self.assertEqual(launch.call_args.args,(self.options,self.queue,Path('/root/reviewed'),CANDIDATE))
        self.assertNotIn(self.secret,out.getvalue())


class Control:
    def __init__(self, mode='drain', waiting=None):
        self.current = dict(reference=REFERENCE,candidate=CANDIDATE,mode=mode,state='pending')
        self.waiting = dict(models=0,input_preparations=0,chart_processors=0,pending_handovers=0,
                            receiving_uploads=0,deleting_datasets=0,deleting_artifacts=0)
        self.waiting.update(waiting or {})
        self.calls = []

    def status(self, queue):
        return dict(current=deepcopy(self.current),waiting=dict(self.waiting))

    def mark_draining(self, queue, reference, candidate):
        self.current['state'] = 'draining'

    def begin_install(self, queue, reference, candidate):
        self.calls.append('begin')
        self.current['state'] = 'installing'

    def complete(self, queue, reference, candidate, *, verification):
        self.calls.append(('complete',verification));self.current['state']='complete'

    def fail(self, queue, reference, candidate, *, reason):
        self.calls.append(('fail',reason));self.current['state']='failed'

    def recover(self, queue, reference, candidate, *, verification):
        self.calls.append(('recover',verification));self.current['state']='cancelled'


class ApplicationUpdateTests(unittest.TestCase):
    def run_update(self, control, **options):
        return update.coordinate(object(),REFERENCE,CANDIDATE,control=control,
                                 verify=lambda:VERIFIED,**options)

    def test_drain_waits_for_running_work_then_verifies_before_resuming(self):
        control=Control(waiting=dict(models=2,chart_processors=1))
        elapsed=[0]
        def sleep(seconds):
            self.assertNotIn('begin',control.calls)
            elapsed[0]+=seconds
            control.waiting.update(models=0,chart_processors=0)
        install=Mock(side_effect=lambda:control.calls.append('install'))
        result=self.run_update(control,install=install,sleep=sleep,monotonic=lambda:elapsed[0])
        self.assertEqual(control.calls,['begin','install',('complete',VERIFIED)])
        self.assertEqual(result['state'],'complete')
        self.assertFalse(result['admissions_paused'])

    def test_live_mode_retains_models_and_charged_pending_handovers(self):
        control=Control('live',waiting=dict(models=4,pending_handovers=2))
        result=self.run_update(control,install=Mock(),sleep=Mock(side_effect=AssertionError('Do not wait for models')))
        self.assertEqual(result['state'],'complete')
        self.assertEqual(control.waiting['models'],4)
        self.assertEqual(control.waiting['pending_handovers'],2)

    def test_backup_readiness_is_waited_without_stopping_controllers_or_starting_install(self):
        control=Control();elapsed=[0];install=Mock()
        def sleep(seconds):
            install.assert_not_called();self.assertNotIn('begin',control.calls);elapsed[0]+=seconds
        result=self.run_update(control,install=install,ready=lambda:elapsed[0]>=1,
            sleep=sleep,monotonic=lambda:elapsed[0],poll_seconds=1)
        self.assertEqual(result['state'],'complete');install.assert_called_once()

    def test_uploads_cleanup_and_existing_processors_are_waited_in_both_modes(self):
        for mode in ('drain','live'):
            for key in ('input_preparations','chart_processors','receiving_uploads','deleting_uploads',
                        'deleting_datasets','deleting_artifacts'):
                with self.subTest(mode=mode,key=key):
                    control=Control(mode,waiting={key:1});elapsed=[0]
                    def sleep(seconds):elapsed[0]+=seconds;control.waiting[key]=0
                    self.run_update(control,install=Mock(),sleep=sleep,monotonic=lambda:elapsed[0])
                    self.assertGreater(elapsed[0],0)

    def test_wait_timeout_preserves_drain_and_never_invokes_installer(self):
        control=Control(waiting=dict(models=1));elapsed=[0];install=Mock()
        def sleep(seconds):elapsed[0]+=seconds
        result=self.run_update(control,install=install,sleep=sleep,monotonic=lambda:elapsed[0],wait_seconds=1)
        self.assertTrue(result['timed_out']);self.assertTrue(result['admissions_paused'])
        self.assertEqual(control.current['state'],'draining')
        install.assert_not_called();self.assertEqual(control.calls,[])

    def test_prior_installation_or_candidate_mismatch_is_not_replayed(self):
        for changes in (dict(state='installing'),dict(state='failed'),dict(state='complete'),
                        dict(candidate='c'*64),dict(reference='00000000-0000-4000-8000-000000000002')):
            with self.subTest(changes=changes):
                control=Control();control.current.update(changes);install=Mock()
                with self.assertRaises(ValueError):self.run_update(control,install=install)
                install.assert_not_called();self.assertEqual(control.calls,[])

    def test_installation_failure_stays_paused_without_verified_rollback(self):
        control=Control()
        with self.assertRaises(RuntimeError):
            self.run_update(control,install=Mock(side_effect=RuntimeError('fictional failure')))
        self.assertEqual(control.calls,['begin',('fail','install_failed')])
        self.assertEqual(control.current['state'],'failed')

    def test_verification_failure_is_distinguished_from_install_failure(self):
        control=Control()
        with self.assertRaises(ValueError):
            update.coordinate(object(),REFERENCE,CANDIDATE,control=control,
                              install=Mock(),verify=lambda:'not verified')
        self.assertEqual(control.calls,['begin',('fail','verification_failed')])

    def test_verified_rollback_reopens_only_after_receipt_validation(self):
        control=Control()
        result=self.run_update(control,install=Mock(side_effect=RuntimeError()),rollback=lambda:VERIFIED)
        self.assertEqual(control.calls,['begin',('fail','install_failed'),('recover',VERIFIED)])
        self.assertEqual(result['reason'],'verified_rollback')
        self.assertFalse(result['admissions_paused'])

    def test_failed_or_invalid_rollback_never_resumes_admissions(self):
        for rollback in (Mock(side_effect=RuntimeError()),lambda:'invalid'):
            control=Control()
            with self.assertRaises((ValueError,RuntimeError)):
                self.run_update(control,install=Mock(side_effect=RuntimeError()),rollback=rollback)
            self.assertEqual(control.calls,['begin',('fail','install_failed'),('fail','rollback_failed')])

    def test_wait_limits_are_finite_and_do_not_accept_boolean_values(self):
        for wait,poll in ((0,5),(604801,5),(True,5),(10,0),(10,61),(10,True)):
            with self.subTest(wait=wait,poll=poll),self.assertRaises(ValueError):
                update.bounds(wait,poll)

    def test_cli_error_does_not_expose_credentials_or_start_services(self):
        from . import privilege_config
        stream=io.StringIO()
        with (patch.object(privilege_config,'trusted_installation',side_effect=ValueError('PRIVATE PASSWORD')),
              patch.object(update.subprocess,'run') as run,redirect_stderr(stream)):
            result=update.main(['--config','/private/settings.toml','status'])
        self.assertEqual(result,1);self.assertNotIn('PRIVATE',stream.getvalue())
        run.assert_not_called()

    def test_launcher_uses_a_fixed_detached_coordinator_without_manifest_commands(self):
        from . import update_package
        from jasmine_web.batch import update_admission
        current=dict(reference=REFERENCE,state='pending')
        with (patch.object(update.os,'geteuid',return_value=0),
              patch.object(update_package,'validate_package',return_value=dict(mode='live')),
              patch.object(update_admission,'request',return_value=current),
              patch.object(update.subprocess,'run',side_effect=[SimpleNamespace(stdout='inactive'),
                                                              SimpleNamespace(returncode=0)]) as run):
            result=update.launch(object(),object(),Path('/root/package'),CANDIDATE,
                                 config=Path('/etc/private.toml'),wait_seconds=60,poll_seconds=5)
        args=run.call_args_list[-1].args[0]
        self.assertEqual(args[0],'systemd-run')
        self.assertIn('--property=RuntimeMaxSec=1860',args)
        self.assertIn(update.BOOTSTRAP,args)
        self.assertNotIn('sh',args);self.assertNotIn('--shell',args)
        self.assertTrue(result['independent_of_ssh'])

    def test_duplicate_starter_does_not_fail_the_existing_waiter(self):
        from . import update_package
        from jasmine_web.batch import update_admission
        current=dict(reference=REFERENCE,state='draining')
        for states in ([SimpleNamespace(stdout='active')],
                       [SimpleNamespace(stdout='inactive'),RuntimeError('duplicate'),SimpleNamespace(stdout='active')]):
            with (self.subTest(states=len(states)),patch.object(update.os,'geteuid',return_value=0),
                  patch.object(update_package,'validate_package',return_value=dict(mode='drain')),
                  patch.object(update_admission,'request',return_value=current),
                  patch.object(update_admission,'fail') as fail,
                  patch.object(update.subprocess,'run',side_effect=states)):
                result=update.launch(object(),object(),Path('/root/package'),CANDIDATE,
                                     config=Path('/etc/private.toml'),wait_seconds=60,poll_seconds=5)
                self.assertTrue(result['already_running']);fail.assert_not_called()

    def test_launch_failure_keeps_durable_fence_in_failed_state(self):
        from . import update_package
        from jasmine_web.batch import update_admission
        current=dict(reference=REFERENCE,state='pending')
        with (patch.object(update.os,'geteuid',return_value=0),
              patch.object(update_package,'validate_package',return_value=dict(mode='drain')),
              patch.object(update_admission,'request',return_value=current),
              patch.object(update_admission,'fail') as fail,
              patch.object(update.subprocess,'run',side_effect=[SimpleNamespace(stdout='inactive'),
                       RuntimeError('launch failed'),SimpleNamespace(stdout='inactive')])):
            with self.assertRaises(RuntimeError):
                update.launch(object(),object(),Path('/root/package'),CANDIDATE,
                              config=Path('/etc/private.toml'),wait_seconds=60,poll_seconds=5)
        self.assertEqual(fail.call_args.kwargs,dict(reason='coordinator_launch_failed'))

    def test_upload_read_refuses_links_and_oversized_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);file=root/'plain';file.write_bytes(b'fixture')
            self.assertEqual(update.uploaded(file,7),b'fixture')
            with self.assertRaises(ValueError):update.uploaded(file,6)
            link=root/'link';link.symlink_to(file)
            with self.assertRaises(ValueError):update.uploaded(link,100)
            with self.assertRaises(ValueError):update.uploaded(Path('relative'),100)

    def test_nonroot_staging_and_launch_are_denied_before_package_access(self):
        with patch.object(update.os,'geteuid',return_value=1000):
            with self.assertRaises(ValueError):update.stage(object(),'/root/package',CANDIDATE)
            with self.assertRaises(ValueError):
                update.launch(object(),object(),Path('/root/package'),CANDIDATE,
                              config=Path('/etc/private.toml'),wait_seconds=60,poll_seconds=5)

    def staging_fixture(self,root):
        name='JAS-mine-web/jasmine_web/batch/notifications.py'
        source=root/'upload';program=source/'programs'/name
        program.parent.mkdir(parents=True);program.write_bytes(b'# fictional candidate\n')
        body=json.dumps(dict(mode='drain',targets={name:{}})).encode()
        (source/'plan.json').write_bytes(body);(source/'qualification.json').write_bytes(b'{}')
        (source/'untrusted.py').write_text('raise AssertionError("Never execute uploaded modules")')
        return source,name,hashlib.sha256(body).hexdigest()

    def test_staging_copies_only_manifest_bytes_and_reuses_verified_existing_directory(self):
        from . import update_package
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source,name,candidate=self.staging_fixture(root)
            def validate(path,expected,queue):
                self.assertEqual(expected,candidate)
                self.assertEqual((path/'programs'/name).read_bytes(),b'# fictional candidate\n')
                self.assertFalse((path/'untrusted.py').exists())
                return dict(mode='drain')
            with (patch.object(update.os,'geteuid',return_value=0),
                  patch.object(update_package,'trusted',side_effect=lambda p,**_:Path(p)),
                  patch.object(update_package,'validate_package',side_effect=validate)):
                result=update.stage(object(),source,candidate,destination=root/'private')
                self.assertFalse(result['already_staged'])
                again=update.stage(object(),source,candidate,destination=root/'private')
                self.assertTrue(again['already_staged'])
            staged=Path(result['package'])
            self.assertEqual(staged.name,candidate)
            self.assertEqual((staged/'plan.json').stat().st_mode & 0o777,0o600)
            self.assertEqual(len(list((root/'private').iterdir())),1)

    def test_failed_staging_validation_discards_partial_candidate_without_touching_upload(self):
        from . import update_package
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source,name,candidate=self.staging_fixture(root)
            with (patch.object(update.os,'geteuid',return_value=0),
                  patch.object(update_package,'trusted',side_effect=lambda p,**_:Path(p)),
                  patch.object(update_package,'validate_package',side_effect=ValueError('Not qualified'))):
                with self.assertRaises(ValueError):update.stage(object(),source,candidate,destination=root/'private')
            self.assertEqual(list((root/'private').iterdir()),[])
            self.assertEqual((source/'programs'/name).read_bytes(),b'# fictional candidate\n')

    def test_staging_plan_mismatch_is_denied_before_creating_private_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source,_,_=self.staging_fixture(root)
            with patch.object(update.os,'geteuid',return_value=0),self.assertRaises(ValueError):
                update.stage(object(),source,CANDIDATE,destination=root/'private')
            self.assertFalse((root/'private').exists())


if __name__ == '__main__':
    unittest.main()
