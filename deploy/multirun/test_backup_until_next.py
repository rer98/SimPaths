"""(C) Copyright 2026, by Ross Richardson

Retain the last local recovery copy until a due replacement verifies its remote
archive and receipt. Exercise outages, changed files and interrupted disposal
with fictional files, captured alerts and the real transport publication logic.
@author ross richardson
"""
from datetime import timedelta
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch
import unittest
from uuid import uuid4

from . import backup_schedule as schedule
from . import test_backup_remote_retention as fixtures
from .artifacts import ArtifactError
from .backup_transport import sealed
from .releases import atomic_json


class UntilNextTests(unittest.TestCase):
    alert=fixtures.RemoteRetentionTests.alert
    execute=fixtures.RemoteRetentionTests.execute
    job=fixtures.RemoteRetentionTests.job

    def setUp(self):
        fixtures.RemoteRetentionTests.setUp(self)
        self.config.write_text(self.config.read_text().replace('keep_days = 1',
            'local_retention = "until-next"\nretention_rule = "count"\nkeep_days = 1'))
        self.options=schedule.load_config(self.config)

    def compact(self,options,path):
        # Distinct fictional captures must publish under distinct remote names.
        marker=self.source/'manifest.json';manifest=json.loads(marker.read_text())
        manifest['id']=uuid4().hex;atomic_json(marker,manifest)
        return fixtures.RemoteRetentionTests.compact(self,options,path)

    def first(self):
        result=self.execute()
        self.assertTrue(result['passed'])
        job=self.job(result)
        cipher=job/'recovery.tar.gpg'
        self.assertTrue(cipher.exists())
        remote=self.remote.directory+'/recovery-'+result['latest']['backup_id']+'.tar.gpg'
        return result,job,cipher,remote

    def test_explicit_policy_requires_a_remote_destination_and_one_counted_copy(self):
        original=schedule.load_config(self.config)['_digest']
        loaded=schedule.load_config(self.config)
        self.assertEqual(loaded['_digest'],original)
        self.assertEqual(loaded['backup']['local_retention'],'until-next')
        for change in ({'keep_latest':2},{'retention_rule':'age-and-count'}):
            with self.subTest(change=change):
                self.options['backup'].update(change)
                with self.assertRaises(ArtifactError):self.execute()
                self.options['backup'].update(keep_latest=1,retention_rule='count')
        self.options.pop('remote')
        with self.assertRaises(ArtifactError):self.execute()
        self.assertFalse((self.storage/'status.json').exists())

    def test_complete_local_copy_survives_delivery_and_every_tick_before_due(self):
        first,job,cipher,_=self.first();before=cipher.read_bytes()
        with patch.object(self.remote,'remote_hash',side_effect=AssertionError('No remote check before due')):
            for hours in (1,6,23):
                result=self.execute(now=self.now+timedelta(hours=hours))
                self.assertTrue(result['passed']);self.assertEqual(result['latest'],first['latest'])
                self.assertEqual(cipher.read_bytes(),before)
        self.assertEqual(self.calls,1)
        self.assertEqual(json.loads((job/'delivery.json').read_text())['local_state'],'retained')

    def test_due_replacement_checks_both_remote_files_before_disposal_and_keeps_new_local_copy(self):
        first,old,cipher,remote=self.first();events=[];old_remote=dict(self.remote.remote)
        original_hash=self.remote.remote_hash;original_unlink=Path.unlink;original_capture=self.compact
        def read(path,receipt):
            events.append(('read',path));return original_hash(path,receipt)
        def unlink(path,*args,**kwargs):
            if path==cipher:
                self.assertEqual(events[:2],[('read',remote),('read',remote+'.json')])
                events.append(('unlink',str(path)))
            return original_unlink(path,*args,**kwargs)
        def capture(options,path):
            self.assertFalse(cipher.exists());self.assertNotEqual(path,cipher)
            events.append(('capture',str(path)));return original_capture(options,path)
        with patch.object(self.remote,'remote_hash',side_effect=read),patch.object(Path,'unlink',unlink):
            result=self.execute(now=self.now+timedelta(days=1),capture_encrypted=capture)
        self.assertTrue(result['passed']);self.assertEqual(self.calls,2)
        self.assertTrue((self.job(result)/'recovery.tar.gpg').exists())
        self.assertFalse(old.exists())
        self.assertEqual({k:self.remote.remote[k] for k in old_remote},old_remote)
        self.assertLess(next(i for i,e in enumerate(events) if e[0]=='unlink'),
                        next(i for i,e in enumerate(events) if e[0]=='capture'))

    def test_outage_keeps_local_copy_and_throttles_rechecks_without_creating_a_job(self):
        first,_,cipher,_=self.first();before=cipher.read_bytes();self.remote.fail=True
        due=self.now+timedelta(days=1)
        result=self.execute(now=due)
        self.assertFalse(result['passed']);self.assertEqual(result['pending_stage'],'replacement-check')
        self.assertEqual(result['retry_at'],(due+timedelta(minutes=30)).isoformat())
        self.assertIsNone(schedule.load_status(self.storage)['current'])
        self.assertEqual(cipher.read_bytes(),before);self.assertEqual(self.calls,1)
        with patch.object(self.remote,'remote_hash',side_effect=AssertionError('Wait for retry')):
            again=self.execute(now=due+timedelta(minutes=1))
        self.assertEqual(again['failure'],result['failure']);self.assertEqual(len(self.messages),1)
        self.remote.fail=False
        result=self.execute(now=due+timedelta(minutes=30))
        self.assertTrue(result['passed']);self.assertEqual(self.calls,2)
        self.assertTrue((self.job(result)/'recovery.tar.gpg').exists())
        self.assertTrue(self.messages[-1][1]['recovered'])

    def test_missing_or_corrupt_remote_archive_or_receipt_never_triggers_reupload_or_unlink(self):
        _,_,cipher,remote=self.first();original=dict(self.remote.remote);before=cipher.read_bytes()
        for index,(suffix,missing) in enumerate((('',True),('',False),('.json',True),('.json',False))):
            with self.subTest(suffix=suffix,missing=missing):
                self.remote.remote=dict(original)
                if missing:self.remote.remote.pop(remote+suffix)
                else:self.remote.remote[remote+suffix]=b'changed remote bytes'
                with patch.object(self.remote,'copy',side_effect=AssertionError('Recheck must only read')):
                    result=self.execute(now=self.now+timedelta(days=1,hours=index))
                self.assertFalse(result['passed']);self.assertEqual(result['pending_stage'],'replacement-check')
                self.assertEqual(cipher.read_bytes(),before);self.assertEqual(self.calls,1)
        self.assertEqual(len(self.messages),1)

    def test_legacy_remote_completion_without_delivery_journal_requires_fresh_readback(self):
        _,old,cipher,remote=self.first();(old/'delivery.json').unlink()
        self.remote.remote.pop(remote+'.json')
        first=self.execute(now=self.now+timedelta(days=1))
        self.assertFalse(first['passed']);self.assertTrue(cipher.exists())
        self.assertFalse((old/'delivery.json').exists())
        self.remote.remote[remote+'.json']=(old/'recovery.tar.gpg.json').read_bytes()
        result=self.execute(now=self.now+timedelta(days=1,minutes=30))
        self.assertTrue(result['passed']);self.assertEqual(self.calls,2)

    def test_unconfirmed_completion_is_not_discarded_even_if_remote_bytes_exist(self):
        _,old,cipher,_=self.first()
        status=schedule.load_status(self.storage);status['last_success']['off_machine']=False
        atomic_json(old/'complete.json',status['last_success']);atomic_json(self.storage/'status.json',status)
        result=self.execute(now=self.now+timedelta(days=1))
        self.assertFalse(result['passed']);self.assertTrue(cipher.exists());self.assertEqual(self.calls,1)

    def test_changed_scope_or_latest_identity_does_not_start_or_remove_work(self):
        _,old,cipher,_=self.first();digest=self.options['_digest']
        self.options['_digest']='b'*64
        result=self.execute(now=self.now+timedelta(days=1))
        self.assertFalse(result['passed']);self.assertTrue(cipher.exists());self.assertEqual(self.calls,1)
        self.options['_digest']=digest
        marker=json.loads((old/'complete.json').read_text());marker['capture_started_at']=self.now.isoformat()+'changed'
        atomic_json(old/'complete.json',marker)
        result=self.execute(now=self.now+timedelta(days=1,hours=1))
        self.assertFalse(result['passed']);self.assertTrue(cipher.exists());self.assertEqual(self.calls,1)

    def test_missing_local_copy_without_disposal_intent_is_reported_instead_of_recaptured(self):
        _,_,cipher,_=self.first();cipher.unlink()
        result=self.execute(now=self.now+timedelta(days=1))
        self.assertFalse(result['passed']);self.assertEqual(result['pending_stage'],'replacement-check')
        self.assertEqual(self.calls,1);self.assertIsNone(schedule.load_status(self.storage)['current'])

    def test_linked_local_archive_cannot_redirect_disposal(self):
        _,_,cipher,_=self.first();cipher.unlink()
        outside=self.root/'unrelated';outside.write_bytes(b'preserve unrelated data');outside.chmod(0o400)
        for index,hard in enumerate((False,True)):
            with self.subTest(hard=hard):
                if hard:os.link(outside,cipher)
                else:cipher.symlink_to(outside)
                result=self.execute(now=self.now+timedelta(days=1,hours=index))
                self.assertFalse(result['passed']);self.assertEqual(self.calls,1)
                self.assertEqual(outside.read_bytes(),b'preserve unrelated data')
                self.assertTrue(cipher.exists());cipher.unlink()

    def test_failed_capture_leaves_previous_external_snapshot_and_retries_one_pending_job(self):
        first,_,cipher,remote=self.first();before=self.remote.remote[remote]
        def failure(*args):raise ArtifactError('fictional capture failure')
        result=self.execute(now=self.now+timedelta(days=1),capture_encrypted=failure)
        self.assertFalse(result['passed']);self.assertEqual(result['pending_stage'],'capturing')
        self.assertFalse(cipher.exists());self.assertEqual(self.remote.remote[remote],before)
        pending=schedule.load_status(self.storage)['current']['id']
        result=self.execute(now=self.now+timedelta(days=1,minutes=30))
        self.assertTrue(result['passed']);self.assertEqual(result['latest']['job_id'],pending)
        self.assertTrue((self.job(result)/'recovery.tar.gpg').exists())

    def test_failed_upload_keeps_new_ciphertext_and_retries_it_without_another_capture(self):
        _,_,old,_=self.first()
        with patch.object(self.remote,'copy',side_effect=ArtifactError('fictional upload outage')):
            first=self.execute(now=self.now+timedelta(days=1))
        pending=self.job(first);before=sealed(pending/'recovery.tar.gpg')
        self.assertFalse(old.exists());self.assertEqual(first['pending_stage'],'copying')
        result=self.execute(now=self.now+timedelta(days=1,minutes=30))
        self.assertTrue(result['passed']);self.assertEqual(self.calls,2)
        self.assertEqual(sealed(pending/'recovery.tar.gpg'),before)

    def test_crash_before_unlink_keeps_local_bytes_when_remote_is_unavailable_on_restart(self):
        _,old,cipher,_=self.first();original=Path.unlink
        def stop(path,*args,**kwargs):
            if path==cipher:raise KeyboardInterrupt('before unlink')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',stop),self.assertRaises(KeyboardInterrupt):
            self.execute(now=self.now+timedelta(days=1))
        self.assertEqual(json.loads((old/'delivery.json').read_text())['local_state'],'removing')
        self.remote.fail=True
        result=self.execute(now=self.now+timedelta(days=1,minutes=1))
        self.assertFalse(result['passed']);self.assertTrue(cipher.exists());self.assertEqual(self.calls,1)
        self.remote.fail=False
        result=self.execute(now=self.now+timedelta(days=1,minutes=31))
        self.assertTrue(result['passed']);self.assertEqual(self.calls,2)

    def test_crash_after_unlink_reconciles_receipts_then_creates_exactly_one_replacement(self):
        _,old,cipher,_=self.first();original=Path.unlink
        def stop(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            if path==cipher:raise KeyboardInterrupt('after unlink')
            return result
        with patch.object(Path,'unlink',stop),self.assertRaises(KeyboardInterrupt):
            self.execute(now=self.now+timedelta(days=1))
        self.assertFalse(cipher.exists());self.assertIsNone(schedule.load_status(self.storage)['current'])
        result=self.execute(now=self.now+timedelta(days=1,minutes=1))
        self.assertTrue(result['passed']);self.assertEqual(self.calls,2)
        self.assertTrue((self.job(result)/'recovery.tar.gpg').exists())

    def test_unlink_failure_and_intent_write_failure_preserve_bytes_and_delay_retry(self):
        _,old,cipher,_=self.first();original_unlink=Path.unlink;original_write=schedule.atomic_json
        def denied(path,*args,**kwargs):
            if path==cipher:raise PermissionError('private path must not escape')
            return original_unlink(path,*args,**kwargs)
        def no_intent(path,value):
            if path==old/'delivery.json' and value['local_state']=='removing':raise OSError('private metadata')
            return original_write(path,value)
        with patch.object(schedule,'atomic_json',side_effect=no_intent):
            first=self.execute(now=self.now+timedelta(days=1))
        self.assertFalse(first['passed']);self.assertTrue(cipher.exists());self.assertEqual(self.calls,1)
        with patch.object(Path,'unlink',denied):
            result=self.execute(now=self.now+timedelta(days=1,minutes=30))
        self.assertFalse(result['passed']);self.assertTrue(cipher.exists());self.assertEqual(self.calls,1)
        self.assertNotIn('private path',json.dumps(result));self.assertNotIn('private metadata',json.dumps(self.messages))
        result=self.execute(now=self.now+timedelta(days=1,hours=1))
        self.assertTrue(result['passed']);self.assertEqual(self.calls,2)

    def test_crash_between_completion_and_schedule_acknowledgement_recovers_the_same_disposal(self):
        _,old,cipher,_=self.first();original=schedule.atomic_json
        def stop(path,value):
            if path==self.storage/'status.json' and value.get('last_success',{}).get('local_archive_removed_at'):
                raise KeyboardInterrupt('after completion marker, before schedule acknowledgement')
            return original(path,value)
        with patch.object(schedule,'atomic_json',side_effect=stop),self.assertRaises(KeyboardInterrupt):
            self.execute(now=self.now+timedelta(days=1))
        self.assertFalse(cipher.exists())
        self.assertIn('local_archive_removed_at',json.loads((old/'complete.json').read_text()))
        self.assertNotIn('local_archive_removed_at',schedule.load_status(self.storage)['last_success'])
        result=self.execute(now=self.now+timedelta(days=1,minutes=1))
        self.assertTrue(result['passed']);self.assertEqual(self.calls,2)
        self.assertTrue((self.job(result)/'recovery.tar.gpg').exists())

    def test_changed_local_delivery_receipt_blocks_disposal(self):
        _,old,cipher,_=self.first();path=old/'delivery.json';value=json.loads(path.read_text())
        value['receipt']['sha256']='b'*64;atomic_json(path,value)
        result=self.execute(now=self.now+timedelta(days=1))
        self.assertFalse(result['passed']);self.assertTrue(cipher.exists());self.assertEqual(self.calls,1)

    def test_operator_snapshot_keeps_replacement_failure_available_without_private_details(self):
        from .operator_status import _backup_snapshot
        self.first();self.remote.fail=True
        self.execute(now=self.now+timedelta(days=1))
        root,value=_backup_snapshot(self.config)
        self.assertEqual(root,self.storage);self.assertTrue(value['available'])
        self.assertEqual(value['pending_stage'],'replacement-check')
        self.assertTrue(value['remote_configured']);self.assertNotIn(str(self.root),json.dumps(value))


@unittest.skipUnless(fixtures.streams.fixtures.DSN,'Use the disposable PostgreSQL backup proof')
class NativeUntilNextTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.NativeRemoteRetentionTests.setUpClass.__func__)
    execute=fixtures.NativeRemoteRetentionTests.execute
    retrieve=fixtures.NativeRemoteRetentionTests.retrieve

    def setUp(self):
        fixtures.NativeRemoteRetentionTests.setUp(self)
        self.options['backup'].update(local_retention='until-next',retention_rule='count',keep_latest=1)

    def test_previous_external_snapshot_restores_after_local_removal_and_failed_replacement(self):
        from . import backup
        first=self.execute();self.assertTrue(first['passed'])
        old=self.storage/'jobs'/first['latest']['job_id']/'recovery.tar.gpg'
        self.assertTrue(old.exists())
        failure=Mock(side_effect=ArtifactError('fictional capture failure after releasing space'))
        result=self.execute(now=self.now+timedelta(days=1),capture_encrypted=failure)
        self.assertFalse(result['passed']);self.assertFalse(old.exists());self.assertEqual(failure.call_count,1)
        decrypted=self.retrieve(first['latest'])
        restored=backup.restore(self.f.target_q,decrypted,self.f.target,self.f.target_tools)
        self.assertTrue(restored['inactive'])
        self.assertEqual(self.f.target_q.inspect(self.f.owner,self.f.exp),self.f.q.inspect(self.f.owner,self.f.exp))

    def test_native_replacement_waits_for_external_recheck_then_keeps_restorable_new_local_copy(self):
        from . import backup, backup_transport
        capture=Mock(wraps=schedule.capture_encrypted_backup)
        first=self.execute(capture_encrypted=capture);self.assertTrue(first['passed'])
        old=self.storage/'jobs'/first['latest']['job_id']/'recovery.tar.gpg';before=sealed(old)
        self.remote.fail=True
        result=self.execute(now=self.now+timedelta(days=1),capture_encrypted=capture)
        self.assertFalse(result['passed']);self.assertEqual(sealed(old),before);self.assertEqual(capture.call_count,1)
        self.remote.fail=False
        result=self.execute(now=self.now+timedelta(days=1,minutes=30),capture_encrypted=capture)
        self.assertTrue(result['passed']);self.assertEqual(capture.call_count,2)
        cipher=self.storage/'jobs'/result['latest']['job_id']/'recovery.tar.gpg'
        decrypted=self.root/'local-recovery'
        backup_transport.unseal(cipher,decrypted,keyring=self.home,expected_sha256=result['latest']['sha256'])
        restored=backup.restore(self.f.target_q,decrypted,self.f.target,self.f.target_tools)
        self.assertTrue(restored['inactive'])
        self.assertEqual(self.f.target_q.inspect(self.f.owner,self.f.exp),self.f.q.inspect(self.f.owner,self.f.exp))
