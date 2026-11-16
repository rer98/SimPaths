"""(C) Copyright 2026, by Ross Richardson
Exercise backup retry clocks, legacy journals and disappearing temporary keyring files.
Uses fictional recovery files and captured alerts; no host services or recipients.
@author ross richardson
"""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from . import backup_schedule as schedule,backup_transport as transport
from .releases import atomic_json
from . import test_backup_schedule as schedule_fixture
from .test_backup_transport import fake_seal,MemorySFTP

class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.f=schedule_fixture.ScheduleTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_brief_conflict_retries_earlier_and_keeps_one_incident_and_one_notice(self):
        from jasmine_web.batch.backup_guard import BackupBusy
        calls=[]
        def busy(*args):calls.append('busy');raise BackupBusy('private filename must not escape')
        first=self.f.execute(capture=busy)
        self.assertEqual(first['retry_at'],(self.f.now+timedelta(minutes=5)).isoformat())
        self.assertEqual(first['last_attempt_at'],self.f.now.isoformat())
        self.assertEqual(first['failure']['last_failure_at'],self.f.now.isoformat())
        self.assertEqual(first['failure']['failure_count'],1);incident=deepcopy(first['failure'])
        early=self.f.execute(now=self.f.now+timedelta(minutes=1),capture=busy)
        self.assertEqual(calls,['busy']);self.assertEqual(early['failure'],incident)
        self.assertEqual(early['last_attempt_at'],first['last_attempt_at']);self.assertEqual(len(self.f.messages),1)
        recovered=self.f.execute(now=self.f.now+timedelta(minutes=5))
        self.assertTrue(recovered['passed']);self.assertIsNone(recovered['failure']);self.assertEqual(self.f.calls,1)
        self.assertEqual(len(self.f.messages),2);self.assertTrue(self.f.messages[-1][1]['recovered'])
        self.assertNotIn('private filename',json.dumps(first))
    def test_actual_failure_and_retry_use_the_clock_after_a_long_attempt(self):
        entry=self.f.now;finish=entry+timedelta(hours=2);clock=[entry]
        def fail(*args):clock[0]=finish;raise RuntimeError('fictional failure')
        with patch.object(schedule,'utc',side_effect=lambda:clock[0]):
            result=schedule.run(self.f.options,capture=fail,encrypt=fake_seal,alert=self.f.alert)
        self.assertEqual(result['last_attempt_at'],entry.isoformat())
        self.assertEqual(result['failure']['last_failure_at'],finish.isoformat())
        self.assertEqual(result['retry_at'],(finish+timedelta(minutes=30)).isoformat())
        early=self.f.execute(now=finish+timedelta(minutes=1))
        self.assertEqual(self.f.calls,0);self.assertEqual(early['failure']['failure_count'],1)
    def test_completion_time_is_actual_but_snapshot_age_and_next_due_remain_conservative(self):
        entry=self.f.now;clock=[entry]
        def capture(*args):
            result=self.f.capture(*args);clock[0]=entry+timedelta(hours=2);return result
        with patch.object(schedule,'utc',side_effect=lambda:clock[0]):
            result=schedule.run(self.f.options,capture=capture,encrypt=fake_seal,alert=self.f.alert)
        self.assertEqual(result['latest']['finished_at'],clock[0].isoformat())
        self.assertEqual(result['latest']['capture_started_at'],entry.isoformat())
        self.assertEqual(result['last_success_age_seconds'],7200)
        self.f.execute(now=entry+timedelta(hours=24));self.assertEqual(self.f.calls,2)
    def test_retry_resets_snapshot_start_only_when_a_new_capture_really_begins(self):
        from jasmine_web.batch.backup_guard import BackupBusy
        def busy(*args):raise BackupBusy()
        first=self.f.execute(capture=busy)
        result=self.f.execute(now=self.f.now+timedelta(minutes=5))
        self.assertTrue(result['passed']);self.assertEqual(result['latest']['capture_started_at'],(self.f.now+timedelta(minutes=5)).isoformat())
        self.assertNotEqual(result['latest']['capture_started_at'],first['last_attempt_at'])
    def test_transfer_retry_preserves_the_original_snapshot_time_and_encrypted_payload(self):
        self.f.options['remote']={'configured':True};remote=MemorySFTP(self.f.root);remote.fail=True
        first=self.f.execute(transport=remote);job=schedule.load_status(self.f.storage)['current']
        payload=self.f.storage/'jobs'/job['id']/'recovery.tar.gpg';before=payload.read_bytes()
        remote.fail=False
        result=self.f.execute(now=self.f.now+timedelta(hours=2),transport=remote)
        self.assertTrue(result['passed']);self.assertEqual(self.f.calls,1)
        self.assertEqual(payload.read_bytes(),before)
        self.assertEqual(result['latest']['capture_started_at'],self.f.now.isoformat())
        self.assertEqual(result['latest']['finished_at'],(self.f.now+timedelta(hours=2)).isoformat())
    def test_old_pending_transfer_journal_resumes_without_fabricating_a_newer_snapshot(self):
        self.f.options['remote']={'configured':True};remote=MemorySFTP(self.f.root);remote.fail=True
        self.f.execute(transport=remote);status=schedule.load_status(self.f.storage)
        status['current'].pop('capture_started_at');status['current'].pop('last_attempt_at')
        status['incident'].pop('last_failure_at');status['incident'].pop('failure_count')
        atomic_json(self.f.storage/'status.json',status);remote.fail=False
        result=self.f.execute(now=self.f.now+timedelta(hours=2),transport=remote)
        self.assertTrue(result['passed']);self.assertEqual(self.f.calls,1)
        self.assertEqual(result['latest']['capture_started_at'],status['current']['started_at'])
    def test_real_repeated_failures_increment_counts_while_status_reads_do_not(self):
        def fail(*args):raise RuntimeError('fictional failure')
        first=self.f.execute(capture=fail);second=self.f.execute(now=self.f.now+timedelta(minutes=30),capture=fail)
        self.assertEqual(first['failure']['id'],second['failure']['id']);self.assertEqual(second['failure']['failure_count'],2)
        self.assertEqual(second['failure']['last_failure_at'],(self.f.now+timedelta(minutes=30)).isoformat())
        before=(self.f.storage/'status.json').read_bytes()
        schedule.report(schedule.load_status(self.f.storage),config=self.f.options,now=self.f.now+timedelta(hours=1))
        self.assertEqual((self.f.storage/'status.json').read_bytes(),before)
    def test_failure_stage_follows_a_later_transfer_failure_without_renotifying(self):
        def fail(*args):raise RuntimeError('fictional capture failure')
        first=self.f.execute(capture=fail)
        self.f.options['remote']={'configured':True};remote=MemorySFTP(self.f.root);remote.fail=True
        second=self.f.execute(now=self.f.now+timedelta(minutes=30),transport=remote)
        self.assertEqual(second['failure']['id'],first['failure']['id']);self.assertEqual(second['failure']['stage'],'copying')
        self.assertEqual(second['failure']['diagnostic']['stage'],'copying');self.assertEqual(len(self.f.messages),1)
    def test_old_success_records_still_have_the_same_age_and_due_rules(self):
        self.f.execute();status=schedule.load_status(self.f.storage);status['last_success'].pop('capture_started_at')
        atomic_json(self.f.storage/'status.json',status)
        result=self.f.execute(now=self.f.now+timedelta(hours=1));self.assertTrue(result['passed'])
        self.assertEqual(result['last_success_age_seconds'],3600);self.assertEqual(self.f.calls,1)
    def test_keyring_cleanup_is_idempotent_if_an_agent_already_removed_the_directory(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(transport.shutil,'which',return_value=None):
            home=Path(folder)/'public-key';home.mkdir();(home/'public').write_bytes(b'fictional public key')
            transport.dispose_keyring(home);self.assertFalse(home.exists());transport.dispose_keyring(home)
    def test_agent_socket_disappearing_during_the_directory_walk_does_not_mask_the_backup_result(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(transport.shutil,'which',return_value=None):
            home=Path(folder)/'public-key';home.mkdir();(home/'S.gpg-agent').write_bytes(b'fictional socket marker')
            original=transport.os.unlink;seen=[]
            def vanished(path,*args,**kwargs):
                result=original(path,*args,**kwargs)
                if str(path).endswith('S.gpg-agent'):
                    seen.append(path);raise FileNotFoundError('already removed by the agent')
                return result
            with patch.object(transport.os,'unlink',side_effect=vanished):transport.dispose_keyring(home)
            self.assertTrue(seen);self.assertFalse(home.exists())
    def test_real_keyring_permission_errors_and_symlinks_are_not_silenced(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(transport.shutil,'which',return_value=None):
            home=Path(folder)/'public-key';home.mkdir();target=Path(folder)/'elsewhere';target.mkdir()
            (target/'keep').write_bytes(b'keep');(home/'link').symlink_to(target,target_is_directory=True)
            with patch.object(transport.os,'unlink',side_effect=PermissionError('denied')):
                with self.assertRaises(PermissionError):transport.dispose_keyring(home)
            self.assertEqual((target/'keep').read_bytes(),b'keep')
            transport.dispose_keyring(home);self.assertTrue(target.is_dir())

if __name__=='__main__':unittest.main()
