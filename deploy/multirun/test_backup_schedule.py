"""(C) Copyright 2026, by Ross Richardson

Durable capture/encrypt/transfer retries, retention and captured operator alerts.
Uses private fictional files and a protocol fake; no mail or remote host is used.
@author ross richardson
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import backup, backup_schedule as schedule
from .artifacts import ArtifactError
from .releases import atomic_json
from . import test_backup as fixtures
from .test_backup_transport import fake_seal, MemorySFTP


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.BackupFileTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.root=fixture.root; self.source,self.manifest=fixture.build_backup()
        self.storage=self.root/'protected'; self.storage.mkdir(mode=0o700)
        self.key=self.root/'public.asc'; self.key.write_bytes(b'fictional public key'); self.key.chmod(0o600)
        self.dsn=self.root/'private.dsn'
        self.dsn.write_text('host=127.0.0.1 dbname=fictional user=operator password=private-backup-password')
        self.dsn.chmod(0o600)
        self.config=self.root/'schedule.toml'
        from deploy._workflow import frontend_path
        text=f'''[source]
frontend = "{frontend_path()}"
state = "{fixture.state}"
dsn_file = "{self.root}/private.dsn"
pool = "test"
schema = "test_backup"
[backup]
directory = "{self.storage}"
public_key = "{self.key}"
recipient = "{'A'*40}"
keep_days = 1
keep_latest = 1
[alerts]
enabled = true
recipient = "operator@example.invalid"
'''
        self.config.write_text(text); self.config.chmod(0o600)
        self.options=schedule.load_config(self.config)
        self.calls=0; self.messages=[]
        self.now=datetime(2026,10,3,12,tzinfo=timezone.utc)
    def capture(self,options,path):
        self.calls+=1; shutil.copytree(self.source,path)
        for directory in [path,*[p for p in path.rglob('*') if p.is_dir()]]: directory.chmod(0o700)
        return backup.summary(backup.verify(path),'backup')
    def alert(self,*args,**kwargs): self.messages.append((args[1].copy(),kwargs))
    def execute(self,**kwargs):
        defaults=dict(now=self.now,capture=self.capture,encrypt=fake_seal,alert=self.alert)
        defaults.update(kwargs)
        return schedule.run(self.options,**defaults)

    def test_daily_capture_is_encrypted_plaintext_removed_and_status_has_no_private_paths(self):
        self.assertFalse(schedule.report(schedule.load_status(self.storage),config=self.options)['passed'])
        result=self.execute(); self.assertTrue(result['passed']); self.assertEqual(1,self.calls)
        self.execute(now=self.now+timedelta(hours=2)); self.assertEqual(1,self.calls)
        job=self.storage/'jobs'/result['latest']['job_id']
        self.assertFalse((job/'plain').exists()); self.assertTrue((job/'recovery.tar.gpg').exists())
        for private in ('private.dsn','private-backup-password','fictional model','operator@example',str(self.root)):
            self.assertNotIn(private,json.dumps(result))
        overdue=schedule.report(schedule.load_status(self.storage),now=self.now+timedelta(days=2),config=self.options)
        self.assertTrue(overdue['overdue']); self.assertFalse(overdue['passed'])

    def test_remote_outage_retries_existing_encrypted_snapshot_without_recapture_and_sends_resolution(self):
        self.options['remote']={'configured':True}
        remote=MemorySFTP(self.root); remote.fail=True
        result=self.execute(transport=remote)
        self.assertFalse(result['passed']); self.assertEqual('copying',result['pending_stage']); self.assertEqual(1,self.calls)
        self.assertEqual(1,len(self.messages)); incident=result['failure']['id']
        self.execute(now=self.now+timedelta(minutes=10),transport=remote)
        self.assertEqual(1,self.calls); self.assertEqual(1,len(self.messages))
        remote.fail=False
        result=self.execute(now=self.now+timedelta(minutes=31),transport=remote)
        self.assertTrue(result['passed']); self.assertEqual(1,self.calls); self.assertEqual(2,len(self.messages))
        self.assertEqual(incident,self.messages[1][0]['id']); self.assertTrue(self.messages[1][1]['recovered'])

    def test_capture_failure_is_durable_and_disabled_alerts_never_send(self):
        self.options['alerts']['enabled']=False
        def failure(*args): raise RuntimeError('private raw path secret')
        result=self.execute(capture=failure)
        self.assertFalse(result['passed']); self.assertEqual('capturing',result['pending_stage']); self.assertEqual([],self.messages)
        self.assertNotIn('secret',json.dumps(result))
        result=self.execute(now=self.now+timedelta(minutes=31)); self.assertTrue(result['passed'])

    def test_failed_mail_delivery_remains_pending_and_reuses_incident_identity(self):
        def fail_capture(*args): raise RuntimeError()
        def fail_mail(*args,**kwargs): raise RuntimeError('SMTP temporary outage')
        first=self.execute(capture=fail_capture,alert=fail_mail)
        self.assertTrue(first['failure']['alert_pending'])
        second=self.execute(now=self.now+timedelta(minutes=1),capture=fail_capture)
        self.assertEqual(first['failure']['id'],second['failure']['id']); self.assertEqual(1,len(self.messages))

    def test_pending_scope_cannot_change_but_notification_and_timing_settings_can(self):
        def failure(*args): raise RuntimeError()
        self.execute(capture=failure)
        text=self.config.read_text().replace('enabled = true','enabled = false')
        self.config.write_text(text); changed=schedule.load_config(self.config)
        self.assertEqual(self.options['_digest'],changed['_digest'])
        changed['_digest']='b'*64
        with self.assertRaises(ArtifactError): schedule.run(changed,now=self.now,capture=self.capture,encrypt=fake_seal)
        # An edited DSN at the same path must not make an old snapshot count as
        # protection for a new source. Rotating only its password is permitted.
        self.dsn.write_text(self.dsn.read_text().replace('private-backup-password','rotated-private-password'))
        self.assertEqual(self.options['_digest'],schedule.load_config(self.config)['_digest'])
        self.dsn.write_text(self.dsn.read_text().replace('dbname=fictional','dbname=different'))
        changed=schedule.load_config(self.config)
        self.assertNotEqual(self.options['_digest'],changed['_digest'])
        with self.assertRaises(ArtifactError): schedule.run(changed,now=self.now,capture=self.capture,encrypt=fake_seal)
        # The VM TOML path can also stay unchanged while its source fields change.
        vm_source=dict(config=str(self.root/'vm.toml'))
        values=dict(frontend=Path(self.options['source']['frontend']),state=self.root/'source-a',
                    pool_id='test',dsn_file=self.dsn)
        with patch('deploy.multirun.vm_config.load_config',return_value=SimpleNamespace(**values)):
            before=schedule.source_scope(vm_source)
        values['state']=self.root/'source-b'
        with patch('deploy.multirun.vm_config.load_config',return_value=SimpleNamespace(**values)):
            self.assertNotEqual(before,schedule.source_scope(vm_source))

    def test_interrupted_encryption_journal_recovers_without_recapturing(self):
        def interrupt(source,destination,**kwargs):
            result=fake_seal(source,destination,**kwargs)
            destination.unlink()
            raise RuntimeError('lost rename')
        first=self.execute(encrypt=interrupt)
        self.assertEqual('sealing',first['pending_stage']); self.assertEqual(1,self.calls)
        result=self.execute(now=self.now+timedelta(minutes=31))
        self.assertTrue(result['passed']); self.assertEqual(1,self.calls)

    def test_retention_preserves_newest_verified_backup_and_pending_job(self):
        first=self.execute(); old=self.storage/'jobs'/first['latest']['job_id']
        self.execute(now=self.now+timedelta(days=2)); self.assertFalse(old.exists())
        self.assertEqual(1,len(list((self.storage/'jobs').iterdir())))

    def test_captured_smtp_message_has_stable_id_and_only_operator_metadata(self):
        from jasmine_web.contact import deliver_email
        incident=dict(id='b'*32,stage='copying')
        with (patch.dict(schedule.os.environ,dict(SMTP_HOST='example.invalid',SMTP_FROM_EMAIL='alerts@example.invalid',SMTP_USE_TLS='true')),
              patch('jasmine_web.contact.deliver_email') as deliver):
            schedule.send_alert(self.options,incident); schedule.send_alert(self.options,incident)
        messages=[call.args[0] for call in deliver.call_args_list]
        self.assertEqual(messages[0]['Message-ID'],messages[1]['Message-ID'])
        self.assertEqual('operator@example.invalid',messages[0]['To'])
        self.assertFalse(messages[0].is_multipart()); self.assertNotIn(str(self.root),messages[0].as_string())
