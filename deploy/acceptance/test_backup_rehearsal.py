"""(C) Copyright 2026, by Ross Richardson

Offline isolation, interruption evidence and resource-policy checks for the native
backup rehearsal. No listener, systemd unit, Docker resource or database is used.
@author ross richardson
"""
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy._workflow import frontend_path
from deploy.acceptance import _backup_rehearsal as fixture
from deploy.acceptance import run_backup_rehearsal as proof
from deploy.multirun import backup_schedule as schedule
from deploy.multirun.releases import atomic_json


class BackupRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = self.root/'store'; self.store.mkdir(mode=0o700)
        self.tag = 'a'*32
        self.timer = fixture.BackupTimer(self.tag,self.root/'supervisor.log')

    def status(self, current=None, success=None, incident=None):
        value = dict(format=schedule.FORMAT,current=current,last_success=success,incident=incident)
        atomic_json(self.store/'status.json',value)
        return value

    def test_timer_rejects_arbitrary_unit_names_and_never_operates_before_creation(self):
        for value in ('simpaths-backup','../../service','a'*32+'\n',''):
            with self.assertRaises(ValueError): fixture.BackupTimer(value,None)
        with patch.object(fixture,'command') as call:
            self.timer.close(); call.assert_not_called()
            with self.assertRaises(ValueError): self.timer.show()

    def test_persistent_unit_or_unrelated_transient_unit_is_never_killed(self):
        self.timer.created = True
        for text in ('Id=simpaths-backup.service\nTransient=yes\n',
                     'Id='+self.timer.name+'.service\nTransient=no\n'):
            for action in (self.timer.crash,self.timer.stop,self.timer.close):
                with patch.object(fixture,'command',return_value=SimpleNamespace(stdout=text)) as call:
                    with self.assertRaises(ValueError): action()
                    self.assertEqual(1,call.call_count)
                    self.assertNotIn('kill',call.call_args.args[0])
                    self.assertNotIn('stop',call.call_args.args[0])
                    self.assertNotIn('reset-failed',call.call_args.args[0])

    def test_stop_and_close_are_idempotent_when_transient_units_have_disappeared(self):
        self.timer.created = True
        with patch.object(self.timer,'show',return_value=None),patch.object(fixture,'command') as call:
            self.timer.stop(); self.timer.stop(); self.timer.close(); self.timer.close()
            call.assert_not_called()
        self.assertFalse(self.timer.created)

    def test_stop_cancels_timer_and_accepts_its_already_collected_service(self):
        self.timer.created = True
        units = dict(timer=dict(ActiveState='active',MainPID='0'),service=None)
        def stop(argv, **options):
            self.assertEqual(['systemctl','--user','stop',self.timer.name+'.timer'],argv)
            units['timer'] = None
            return SimpleNamespace(returncode=0)
        with (patch.object(self.timer,'show',side_effect=lambda kind='service':units[kind]),
              patch.object(fixture,'command',side_effect=stop) as call):
            self.timer.stop(); self.timer.close()
            call.assert_called_once()
        self.assertFalse(self.timer.created)

    def test_disappearance_between_inspection_and_stop_is_verified_before_accepting_error(self):
        for kind in ('timer','service'):
            with self.subTest(kind=kind):
                self.timer.created = True; self.timer.stopped = False
                units = dict(timer=None,service=None)
                units[kind] = dict(ActiveState='active',MainPID='123' if kind == 'service' else '0')
                def missing(argv, **options):
                    self.assertEqual(['systemctl','--user','stop',self.timer.name+'.'+kind],argv)
                    units[kind] = None
                    return SimpleNamespace(returncode=5)
                with (patch.object(self.timer,'show',side_effect=lambda kind='service':units[kind]),
                      patch.object(fixture,'command',side_effect=missing) as call):
                    self.timer.stop(); self.timer.close()
                    call.assert_called_once()
                self.assertFalse(self.timer.created)

    def test_stop_failure_or_surviving_process_cannot_count_as_clean_shutdown(self):
        for code, state, pid in ((1,'active','123'),(0,'active','123'),(0,'inactive','123'),(1,'inactive','0')):
            with self.subTest(code=code,state=state,pid=pid):
                self.timer.created = True; self.timer.stopped = False
                units = dict(timer=None,service=dict(ActiveState='active',MainPID='123'))
                def failed(argv, **options):
                    units['service'] = dict(ActiveState=state,MainPID=pid)
                    return SimpleNamespace(returncode=code)
                with (patch.object(self.timer,'show',side_effect=lambda kind='service':units[kind]),
                      patch.object(fixture,'command',side_effect=failed)):
                    with self.assertRaises(RuntimeError): self.timer.close()
                self.assertTrue(self.timer.created)
                self.assertFalse(self.timer.stopped)

    def test_close_rechecks_for_surviving_processes_even_after_a_successful_stop(self):
        self.timer.created = True; self.timer.stopped = True
        with (patch.object(self.timer,'show',return_value=dict(ActiveState='active',MainPID='123')),
              patch.object(fixture,'command') as call):
            with self.assertRaises(RuntimeError): self.timer.close()
            call.assert_not_called()
        self.assertTrue(self.timer.created)

    def test_failed_transient_service_is_reset_only_after_verified_shutdown(self):
        self.timer.created = True
        units = dict(timer=None,service=dict(ActiveState='failed',MainPID='0'))
        def reset(argv, **options):
            self.assertEqual(['systemctl','--user','reset-failed',self.timer.name+'.service'],argv)
            units['service'] = None
            return SimpleNamespace(returncode=0)
        with (patch.object(self.timer,'show',side_effect=lambda kind='service':units[kind]),
              patch.object(fixture,'command',side_effect=reset) as call):
            self.timer.close(); call.assert_called_once()
        self.assertFalse(self.timer.created)

    def test_systemctl_repeated_timer_properties_preserve_both_scheduling_rules(self):
        self.timer.created = True
        initial = '{ OnActiveUSec=1s ; next_elapse=1s }'
        after_exit = '{ OnUnitInactiveUSec=5s ; next_elapse=0 }'
        for rules in ((initial,after_exit),(after_exit,initial)):
            with self.subTest(rules=rules):
                text = 'Id='+self.timer.name+'.timer\nTransient=yes\n'
                text += ''.join('TimersMonotonic='+rule+'\n' for rule in rules)
                with patch.object(fixture,'command',return_value=SimpleNamespace(stdout=text)):
                    observed = self.timer.show('timer')
                self.assertEqual(list(rules),observed['TimersMonotonic'].splitlines())

    def test_duplicate_scalar_properties_cannot_mask_unit_identity_or_limits(self):
        self.timer.created = True
        for field, first, second in (('Id','simpaths-backup.service',self.timer.name+'.service'),
                                     ('MemoryMax','infinity',str(512*1024**2))):
            with self.subTest(field=field):
                text = field+'='+first+'\n'+field+'='+second+'\nTransient=yes\n'
                with patch.object(fixture,'command',return_value=SimpleNamespace(stdout=text)):
                    with self.assertRaisesRegex(ValueError,'duplicate scalar'): self.timer.show()

    def test_effective_policy_rejects_relaxed_limits_and_missing_or_changed_timer_rules(self):
        service = dict(Type='oneshot',UMask='0077',MemoryMax=str(512*1024**2),TasksMax='128',
                       Nice='10',IOSchedulingClass='3',KillMode='control-group',TimeoutStartUSec='2h',
                       NoNewPrivileges='yes')
        initial = '{ OnActiveUSec=1s ; next_elapse=1s }'
        after_exit = '{ OnUnitInactiveUSec=5s ; next_elapse=0 }'
        timer = dict(TimersMonotonic=initial+'\n'+after_exit)
        fixture.verify_effective_policy(service,timer)
        fixture.verify_effective_policy({**service,'IOSchedulingClass':'idle'},timer)
        for field, wrong in (('Type','simple'),('UMask','0022'),('MemoryMax','infinity'),
                             ('TasksMax','infinity'),('Nice','0'),('IOSchedulingClass','2'),
                             ('KillMode','process'),('TimeoutStartUSec','infinity'),('NoNewPrivileges','no')):
            with self.subTest(field=field):
                with self.assertRaisesRegex(AssertionError,field):
                    fixture.verify_effective_policy({**service,field:wrong},timer)
        for rules in ('',initial,after_exit,initial+'\n'+after_exit.replace('=5s','=50s'),
                      initial+'\n'+after_exit+'\n'+after_exit):
            with self.subTest(rules=rules):
                with self.assertRaisesRegex(AssertionError,'both backup timer rules'):
                    fixture.verify_effective_policy(service,dict(TimersMonotonic=rules))

    def test_partial_evidence_requires_nonempty_incomplete_ciphertext_upload(self):
        job = dict(id='b'*32,backup_id='c'*32,stage='copying',sha256='d'*64)
        self.status(job)
        folder = self.store/'jobs'/job['id']; folder.mkdir(parents=True)
        (folder/'recovery.tar.gpg').write_bytes(b'12345678')
        remote = self.root/'remote'; remote.mkdir()
        server = SimpleNamespace(data=remote)
        partial = remote/('recovery-'+job['backup_id']+'.tar.gpg.partial')
        self.assertFalse(proof.partial_transfer(server,self.store))
        partial.write_bytes(b''); self.assertFalse(proof.partial_transfer(server,self.store))
        partial.write_bytes(b'123'); self.assertEqual(3,proof.partial_transfer(server,self.store)['partial_bytes'])
        partial.write_bytes(b'12345678'); self.assertFalse(proof.partial_transfer(server,self.store))
        job['stage'] = 'sealing'; self.status(job)
        partial.write_bytes(b'123'); self.assertFalse(proof.partial_transfer(server,self.store))

    def test_pending_or_failed_capture_cannot_count_as_verified_protection(self):
        success = dict(backup_id='b'*32)
        self.status(success=success); self.assertEqual(success,proof.complete_status(self.store))
        self.status(current=dict(id='a'*32,stage='copying'),success=success)
        self.assertFalse(proof.complete_status(self.store))
        self.status(success=success,incident=dict(stage='copying'))
        self.assertFalse(proof.complete_status(self.store))
        self.status(); self.assertFalse(proof.complete_status(self.store))

    def test_log_reader_ignores_incomplete_append_and_non_json_diagnostics(self):
        path = self.root/'schedule.log'
        self.assertEqual([],proof.scheduler_lines(path))
        path.write_text('fictional diagnostic\n{"passed":false}\n{"passed":')
        self.assertEqual([dict(passed=False)],proof.scheduler_lines(path))

    def test_changed_template_policy_requires_explicit_review(self):
        policy = fixture.timer_policy()
        self.assertEqual('512M',policy['service']['MemoryMax'])
        self.assertEqual('hourly',policy['timer']['OnCalendar'])
        self.assertEqual('5s',policy['adaptations']['OnUnitInactiveSec'])
        with patch.object(fixture,'SERVICE_POLICY',{**fixture.SERVICE_POLICY,'MemoryMax':'1G'}):
            with self.assertRaises(ValueError): fixture.timer_policy()

    def test_only_labelled_owned_sftp_container_can_be_stopped_or_removed(self):
        server = fixture.SFTPServer.__new__(fixture.SFTPServer)
        server.name,server.tag,server.image,server.log = 'fixture',self.tag,'sha256:'+'b'*64,None
        value = dict(Config=dict(Labels={'simpaths.backup-rehearsal':'unrelated'}),Image=server.image)
        with patch.object(fixture,'command',return_value=SimpleNamespace(returncode=0,stdout=json.dumps([value]))) as call:
            with self.assertRaises(ValueError): server.stop()
            self.assertEqual(1,call.call_count)
        value['Config']['Labels']['simpaths.backup-rehearsal'] = self.tag
        value['Image'] = 'sha256:'+'c'*64
        with patch.object(fixture,'command',return_value=SimpleNamespace(returncode=0,stdout=json.dumps([value]))):
            with self.assertRaises(ValueError): server.inspect()

    def test_restart_rediscovers_changed_sftp_port_without_recreating_container(self):
        server = fixture.SFTPServer.__new__(fixture.SFTPServer)
        server.name,server.log,server.port = 'owned-sftp',None,15432
        server.config,server.data = self.root/'server-config',self.root/'remote'
        server.host_public = ['ssh-ed25519','YWJjZA==']
        restarted = dict(NetworkSettings=dict(Ports={'2222/tcp':[dict(HostIp='127.0.0.1',HostPort='15433')]}),
                         Mounts=[dict(Type='bind',Destination='/config',Source=str(server.config)),
                                 dict(Type='bind',Destination='/srv/backups',Source=str(server.data))])
        with (patch.object(server,'inspect',side_effect=[dict(State='exited'),restarted]) as inspect,
              patch.object(fixture,'command') as call):
            server.restart()
            self.assertEqual(2,inspect.call_count)
            call.assert_called_once_with(['docker','start','owned-sftp'],log=None)
        self.assertEqual(15433,server.port)
        self.assertEqual(['ssh-ed25519','YWJjZA=='],server.host_public)

    def test_refreshed_sftp_port_still_requires_loopback_and_the_original_mounts(self):
        server = fixture.SFTPServer.__new__(fixture.SFTPServer)
        server.port = 15432
        server.config,server.data = self.root/'server-config',self.root/'remote'
        mounts = [dict(Type='bind',Destination='/config',Source=str(server.config)),
                  dict(Type='bind',Destination='/srv/backups',Source=str(server.data))]
        bindings = [None,[],[dict(HostIp='0.0.0.0',HostPort='15433')],
                    [dict(HostIp='127.0.0.1',HostPort='22')],
                    [dict(HostIp='127.0.0.1',HostPort='65536')]]
        for binding in bindings:
            with self.subTest(binding=binding):
                info = dict(NetworkSettings=dict(Ports={'2222/tcp':binding}),Mounts=mounts)
                with patch.object(server,'inspect',return_value=info):
                    with self.assertRaises(ValueError): server.refresh_port()
                self.assertEqual(15432,server.port)
        changed_mount = {**mounts[1],'Source':str(self.root/'unrelated')}
        info = dict(NetworkSettings=dict(Ports={'2222/tcp':[dict(HostIp='127.0.0.1',HostPort='15433')]}),
                    Mounts=[mounts[0],changed_mount])
        with patch.object(server,'inspect',return_value=info):
            with self.assertRaisesRegex(ValueError,'unrelated private data'): server.refresh_port()
        self.assertEqual(15432,server.port)

    def test_retargeted_relay_preserves_client_address_and_existing_connections(self):
        relay = fixture.Relay.__new__(fixture.Relay)
        relay.lock,relay.port,relay.upstream_port = threading.Lock(),15434,15432
        connected = object(); relay.sockets = {connected}
        relay.retarget(15433)
        self.assertEqual(15433,relay.upstream_port)
        self.assertEqual(15434,relay.port)
        self.assertEqual({connected},relay.sockets)
        for port in (22,0,'15433',True,65536):
            with self.subTest(port=port):
                with self.assertRaises(ValueError): relay.retarget(port)
                self.assertEqual(15433,relay.upstream_port)
        relay.stop,relay.threads,relay.listener = threading.Event(),[],Mock()
        relay.listener.accept.return_value = (Mock(),None)
        def connect(address, timeout):
            relay.stop.set(); return Mock()
        with (patch.object(fixture.socket,'create_connection',side_effect=connect) as opened,
              patch.object(fixture.threading,'Thread')):
            relay.accept()
        opened.assert_called_once_with(('127.0.0.1',15433),timeout=2)
        self.assertIn(connected,relay.sockets)

    def test_host_pin_injection_and_privileged_relay_destination_are_rejected(self):
        for public in (['ssh-rsa','abcd'],['ssh-ed25519','bad\nnew-host'],['ssh-ed25519','']):
            with self.assertRaises(ValueError): fixture.write_pin(self.root/'pin',15000,public)
        fixture.write_pin(self.root/'pin',15000,['ssh-ed25519','YWJjZA=='])
        self.assertEqual(0o600,(self.root/'pin').stat().st_mode & 0o777)
        self.assertEqual('[127.0.0.1]:15000 ssh-ed25519 YWJjZA==\n',(self.root/'pin').read_text())
        for port in (22,0,'15000',65536):
            with self.assertRaises(ValueError): fixture.Relay(port)

    def test_fixture_config_preserves_normal_interval_and_disables_real_mail(self):
        config = self.root/'backup.toml'
        state = self.root/'source'; state.mkdir(mode=0o700)
        public = self.root/'public.asc'; public.write_text('fictional public key'); public.chmod(0o600)
        for name in ('identity','pins'):
            (self.root/name).write_text('fictional'); (self.root/name).chmod(0o600)
        dsn = 'postgresql://postgres:FICTIONAL-PRIVATE@127.0.0.1:15432/jasmine_queue_test'
        proof.private_file(self.root/'dsn',dsn)
        source = SimpleNamespace(state=state,q=SimpleNamespace(pool_id='test',schema='test_backup'),
                                 tools=SimpleNamespace(container=None))
        remote = dict(host='127.0.0.1',user='backup',port=15433,directory='/backups',
                      identity=str(self.root/'identity'),known_hosts=str(self.root/'pins'))
        proof.schedule_config(config,source,self.store,public,'A'*40,remote,frontend_path())
        parsed = schedule.load_config(config)
        self.assertEqual(24,parsed['backup']['interval_hours'])
        self.assertEqual(1,parsed['backup']['retry_minutes'])
        self.assertFalse(parsed['alerts']['enabled'])
        self.assertNotIn('FICTIONAL-PRIVATE',config.read_text())
        self.assertEqual(0o600,config.stat().st_mode & 0o777)

    def test_hidden_proof_flag_cannot_capture_a_live_database(self):
        output = self.root/'new-evidence'
        with patch.dict(os.environ, JASMINE_BATCH_TEST_DSN='postgresql://postgres:FICTIONAL@127.0.0.1:5432/live'):
            with self.assertRaises(ValueError):
                proof.main(['--execute-proof','--output',str(output),'--frontend',str(frontend_path())])
        self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()
