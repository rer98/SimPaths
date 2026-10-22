"""(C) Copyright 2026, by Ross Richardson

Verified off-machine disposal, interrupted local cleanup and retained recovery
receipts. Uses fictional files, captured mail and the production SFTP algorithm
with an in-memory destination; no external backup account is needed.
@author ross richardson
"""
from datetime import timedelta
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

from . import backup_schedule as schedule
from . import test_backup_schedule as fixtures
from . import test_backup_stream as streams
from .artifacts import ArtifactError
from .backup_transport import sealed
from .releases import atomic_json
from .test_backup_transport import MemorySFTP


class RemoteRetentionTests(unittest.TestCase):
    alert=fixtures.ScheduleTests.alert
    compact=streams.CompactScheduleTests.compact

    def setUp(self):
        fixtures.ScheduleTests.setUp(self)
        for name in ('identity','known_hosts'):
            path=self.root/name; path.write_bytes(b'fictional pinned credential'); path.chmod(0o600)
        self.config.write_text(self.config.read_text()+f'''
[remote]
host = "backup.example.invalid"
user = "backup"
port = 22
directory = "/backups"
identity = "{self.root}/identity"
known_hosts = "{self.root}/known_hosts"
''')
        self.options=schedule.load_config(self.config)
        self.options['backup']['local_retention']='remote-only'
        self.remote=MemorySFTP(self.root)

    def execute(self, **kwargs):
        defaults=dict(now=self.now,capture_encrypted=self.compact,alert=self.alert,transport=self.remote)
        defaults.update(kwargs)
        return schedule.run(self.options,**defaults)

    def job(self, result):
        status=schedule.load_status(self.storage)
        key=status['current']['id'] if status['current'] else result['latest']['job_id']
        return self.storage/'jobs'/key

    def retain(self):
        self.options['backup']['local_retention']='retain'
        result=self.execute()
        self.assertTrue(result['passed'])
        return result,self.job(result)

    def test_default_retains_local_copy_and_remote_only_requires_a_destination(self):
        defaults=schedule.load_config(self.config)
        self.assertEqual(defaults['backup']['local_retention'],'retain')
        self.options.pop('remote')
        with self.assertRaises(ArtifactError): self.execute()
        self.assertEqual(self.calls,0)
        self.assertFalse((self.storage/'status.json').exists())
        text=self.config.read_text().split('\n[remote]')[0]
        self.config.write_text(text.replace('keep_days = 1','local_retention = "remote-only"\nkeep_days = 1'))
        with self.assertRaises(ArtifactError): schedule.load_config(self.config)

    def test_policy_is_strict_and_does_not_change_pending_source_identity(self):
        original=schedule.load_config(self.config)['_digest']
        self.config.write_text(self.config.read_text().replace('keep_days = 1',
            'local_retention = "remote-only"\nkeep_days = 1'))
        changed=schedule.load_config(self.config)
        self.assertEqual(original,changed['_digest'])
        for value in ('"automatic"','false','0','[]'):
            with self.subTest(value=value):
                self.config.write_text(self.config.read_text().replace('local_retention = "remote-only"',
                    'local_retention = '+value))
                with self.assertRaises(ArtifactError): schedule.load_config(self.config)
                self.config.write_text(self.config.read_text().replace('local_retention = '+value,
                    'local_retention = "remote-only"'))

    def test_only_verified_ciphertext_is_removed_and_small_recovery_records_remain(self):
        result=self.execute(); self.assertTrue(result['passed'])
        job=self.job(result)
        self.assertEqual(result['local_retention'],'remote-only')
        self.assertFalse((job/'recovery.tar.gpg').exists())
        self.assertEqual({p.name for p in job.iterdir()},
            {'complete.json','recovery.tar.gpg.json','delivery.json'})
        receipt=json.loads((job/'recovery.tar.gpg.json').read_text())
        completed=self.remote.directory+'/recovery-'+receipt['backup_id']+'.tar.gpg'
        self.assertEqual(receipt['sha256'],self.remote.remote_hash(completed,receipt)['sha256'])
        self.assertEqual(json.loads(self.remote.remote[completed+'.json']),receipt)
        self.assertIsNotNone(result['latest']['local_archive_removed_at'])
        self.assertEqual(json.loads((job/'complete.json').read_text()),result['latest'])
        self.assertLess(sum(p.stat().st_size for p in job.iterdir()),8192)
        self.assertEqual(self.messages,[])
        with patch.object(self.remote,'copy',side_effect=AssertionError('Do not retransfer a removed archive')):
            again=self.execute(now=self.now+timedelta(hours=1))
        self.assertEqual(self.calls,1)
        self.assertEqual(result['latest'],again['latest'])

    def test_remote_success_with_default_policy_keeps_the_local_archive(self):
        result,job=self.retain()
        self.assertTrue((job/'recovery.tar.gpg').exists())
        self.assertNotIn('local_archive_removed_at',result['latest'])

    def test_outage_retains_one_pending_archive_across_days_without_recapture(self):
        self.remote.fail=True
        first=self.execute(); job=self.job(first)
        cipher=job/'recovery.tar.gpg'; before=sealed(cipher)
        for days in (1,2,3):
            result=self.execute(now=self.now+timedelta(days=days))
            self.assertFalse(result['passed']); self.assertEqual(result['pending_stage'],'copying')
            self.assertEqual(sealed(cipher),before)
        self.assertEqual(self.calls,1)
        self.assertEqual(len(list((self.storage/'jobs').iterdir())),1)
        self.assertEqual(len(self.messages),1)
        self.remote.fail=False
        result=self.execute(now=self.now+timedelta(days=3,minutes=31))
        self.assertTrue(result['passed']); self.assertEqual(self.calls,1)
        self.assertFalse(cipher.exists()); self.assertTrue(self.messages[-1][1]['recovered'])

    def test_corrupt_readback_and_conflicting_destination_never_allow_disposal(self):
        self.remote.corrupt=True
        result=self.execute(); job=self.job(result); cipher=job/'recovery.tar.gpg'
        self.assertFalse(result['passed']); self.assertTrue(cipher.exists())
        self.assertFalse((job/'delivery.json').exists())
        self.remote.corrupt=False
        receipt=sealed(cipher)
        completed=self.remote.directory+'/recovery-'+receipt['backup_id']+'.tar.gpg'
        self.remote.remote[completed]=b'different previously published archive'
        result=self.execute(now=self.now+timedelta(hours=1))
        self.assertFalse(result['passed']); self.assertTrue(cipher.exists()); self.assertEqual(self.calls,1)
        self.assertEqual(self.remote.remote[completed],b'different previously published archive')

    def test_partial_upload_retries_the_identical_ciphertext_before_removal(self):
        self.remote.interrupt_next_put=True
        first=self.execute(); job=self.job(first); receipt=sealed(job/'recovery.tar.gpg')
        result=self.execute(now=self.now+timedelta(minutes=31))
        self.assertTrue(result['passed']); self.assertEqual(self.calls,1)
        completed=self.remote.directory+'/recovery-'+receipt['backup_id']+'.tar.gpg'
        self.assertEqual(self.remote.remote_hash(completed,receipt)['sha256'],receipt['sha256'])
        self.assertFalse((job/'recovery.tar.gpg').exists())

    def test_receipt_transfer_must_also_finish_before_archive_removal(self):
        original=self.remote._copy_file
        def fail_receipt(source, remote, receipt):
            if remote.endswith('.json'): raise ArtifactError('Fictional receipt transfer failure')
            return original(source,remote,receipt)
        with patch.object(self.remote,'_copy_file',side_effect=fail_receipt):
            result=self.execute()
        job=self.job(result)
        self.assertFalse(result['passed']); self.assertTrue((job/'recovery.tar.gpg').exists())
        self.assertFalse((job/'delivery.json').exists())
        result=self.execute(now=self.now+timedelta(minutes=31))
        self.assertTrue(result['passed']); self.assertFalse((job/'recovery.tar.gpg').exists())

    def test_unconfirmed_or_mismatched_transport_result_never_authorises_disposal(self):
        for index,reply in enumerate((None,{},dict(passed=False),dict(passed=True,sha256='b'*64))):
            with self.subTest(reply=reply),patch.object(self.remote,'copy',return_value=reply):
                result=self.execute(now=self.now+timedelta(hours=index+1))
                job=self.job(result)
                self.assertFalse(result['passed']); self.assertTrue((job/'recovery.tar.gpg').exists())
                self.assertFalse((job/'delivery.json').exists())
        self.assertEqual(self.calls,1)

    def test_lost_publication_acknowledgement_still_requires_verified_bytes(self):
        self.remote.lost_ack=True
        result=self.execute()
        self.assertTrue(result['passed']); self.assertFalse((self.job(result)/'recovery.tar.gpg').exists())

    def test_retained_payload_is_removed_before_a_new_capture_needs_staging_space(self):
        first,old=self.retain()
        self.options['backup']['local_retention']='remote-only'
        original=self.compact
        def replacement(options,path):
            self.assertFalse((old/'recovery.tar.gpg').exists())
            return original(options,path)
        result=self.execute(now=self.now+timedelta(days=2),capture_encrypted=replacement)
        self.assertTrue(result['passed']); self.assertEqual(self.calls,2)
        self.assertFalse(old.exists())
        self.assertFalse((self.job(result)/'recovery.tar.gpg').exists())

    def test_legacy_completed_remote_copy_is_reverified_and_outage_preserves_it(self):
        first,job=self.retain(); (job/'delivery.json').unlink()
        self.options['backup']['local_retention']='remote-only'; self.remote.fail=True
        result=self.execute(now=self.now+timedelta(days=2))
        self.assertFalse(result['passed']); self.assertEqual(result['pending_stage'],'local-cleanup')
        self.assertTrue((job/'recovery.tar.gpg').exists()); self.assertEqual(self.calls,1)
        self.remote.fail=False
        result=self.execute(now=self.now+timedelta(hours=1))
        self.assertTrue(result['passed']); self.assertFalse((job/'recovery.tar.gpg').exists())
        self.assertEqual(self.calls,1)

    def test_completion_acknowledgement_precedes_any_local_disposal(self):
        original=schedule.atomic_json
        def interrupt(path,value):
            if path==self.storage/'status.json' and value['last_success'] and value['current'] is None:
                raise KeyboardInterrupt('Fictional lost completion acknowledgement')
            original(path,value)
        with patch.object(schedule,'atomic_json',side_effect=interrupt),self.assertRaises(KeyboardInterrupt):
            self.execute()
        state=schedule.load_status(self.storage)
        job=self.storage/'jobs'/state['current']['id']
        self.assertTrue((job/'complete.json').exists()); self.assertTrue((job/'recovery.tar.gpg').exists())
        result=self.execute(now=self.now+timedelta(hours=1))
        self.assertTrue(result['passed']); self.assertFalse((job/'recovery.tar.gpg').exists())
        self.assertEqual(self.calls,1)

    def test_crash_after_unlink_resumes_from_durable_intent_without_recapture(self):
        original=Path.unlink
        def interrupt(path,*args,**kwargs):
            original(path,*args,**kwargs)
            if path.name=='recovery.tar.gpg': raise KeyboardInterrupt('Fictional crash after unlink')
        with patch.object(Path,'unlink',interrupt),self.assertRaises(KeyboardInterrupt): self.execute()
        state=schedule.load_status(self.storage); job=self.storage/'jobs'/state['last_success']['job_id']
        self.assertEqual(json.loads((job/'delivery.json').read_text())['local_state'],'removing')
        self.assertFalse((job/'recovery.tar.gpg').exists())
        with patch.object(self.remote,'copy',side_effect=AssertionError('No upload needed')):
            result=self.execute(now=self.now+timedelta(hours=1))
        self.assertTrue(result['passed']); self.assertIsNotNone(result['latest']['local_archive_removed_at'])
        self.assertEqual(self.calls,1)

    def test_failed_unlink_alerts_and_recovers_without_a_second_capture(self):
        original=Path.unlink
        def denied(path,*args,**kwargs):
            if path.name=='recovery.tar.gpg': raise PermissionError('private path must not appear in mail')
            original(path,*args,**kwargs)
        with patch.object(Path,'unlink',denied): result=self.execute()
        job=self.job(result)
        self.assertFalse(result['passed']); self.assertEqual(result['pending_stage'],'local-cleanup')
        self.assertTrue((job/'recovery.tar.gpg').exists()); self.assertEqual(len(self.messages),1)
        self.assertNotIn('private path',json.dumps(self.messages))
        result=self.execute(now=self.now+timedelta(hours=1))
        self.assertTrue(result['passed']); self.assertEqual(self.calls,1)
        self.assertTrue(self.messages[-1][1]['recovered'])

    def test_disposal_intent_must_be_durable_before_unlink(self):
        original=schedule.atomic_json
        def fail_intent(path,value):
            if path.name=='delivery.json' and value['local_state']=='removing':
                raise OSError('Fictional metadata write failure')
            return original(path,value)
        with patch.object(schedule,'atomic_json',side_effect=fail_intent): result=self.execute()
        job=self.job(result)
        self.assertFalse(result['passed']); self.assertTrue((job/'recovery.tar.gpg').exists())
        self.assertEqual(json.loads((job/'delivery.json').read_text())['local_state'],'retained')
        result=self.execute(now=self.now+timedelta(hours=1))
        self.assertTrue(result['passed']); self.assertEqual(self.calls,1)

    def test_lost_removed_acknowledgement_reconciles_receipts_after_unlink(self):
        original=schedule.atomic_json
        def fail_complete(path,value):
            if path.name=='complete.json' and value.get('local_archive_removed_at'):
                raise OSError('Fictional lost cleanup acknowledgement')
            return original(path,value)
        with patch.object(schedule,'atomic_json',side_effect=fail_complete): result=self.execute()
        job=self.job(result)
        self.assertFalse(result['passed']); self.assertFalse((job/'recovery.tar.gpg').exists())
        self.assertEqual(json.loads((job/'delivery.json').read_text())['local_state'],'removed')
        result=self.execute(now=self.now+timedelta(hours=1))
        self.assertTrue(result['passed']); self.assertIsNotNone(result['latest']['local_archive_removed_at'])
        self.assertEqual(self.calls,1)

    def test_disabling_policy_finishes_existing_intent_but_keeps_future_archives(self):
        original=Path.unlink
        def interrupt(path,*args,**kwargs):
            if path.name=='recovery.tar.gpg': raise KeyboardInterrupt('Fictional crash before unlink')
            original(path,*args,**kwargs)
        with patch.object(Path,'unlink',interrupt),self.assertRaises(KeyboardInterrupt): self.execute()
        self.options['backup']['local_retention']='retain'
        resumed=self.execute(now=self.now+timedelta(hours=1))
        self.assertTrue(resumed['passed']); self.assertIsNotNone(resumed['latest']['local_archive_removed_at'])
        result=self.execute(now=self.now+timedelta(days=2))
        self.assertTrue((self.job(result)/'recovery.tar.gpg').exists())

    def test_missing_ciphertext_without_a_disposal_intent_is_not_labelled_removed(self):
        first,job=self.retain(); (job/'recovery.tar.gpg').unlink()
        self.options['backup']['local_retention']='remote-only'
        result=self.execute(now=self.now+timedelta(hours=1))
        self.assertFalse(result['passed']); self.assertNotIn('local_archive_removed_at',result['latest'])

    def test_unverified_local_backup_and_different_scope_are_never_disposed(self):
        first,job=self.retain()
        marker=job/'complete.json'; record=json.loads(marker.read_text()); record['off_machine']=False
        atomic_json(marker,record)
        self.options['backup']['local_retention']='remote-only'
        result=self.execute(now=self.now+timedelta(days=2))
        self.assertFalse(result['passed']); self.assertTrue((job/'recovery.tar.gpg').exists())
        record['config']='b'*64; atomic_json(marker,record)
        result=self.execute(now=self.now+timedelta(hours=1))
        self.assertTrue((job/'recovery.tar.gpg').exists())

    def test_changed_delivery_record_and_linked_ciphertext_never_delete_another_file(self):
        first,job=self.retain()
        marker=job/'delivery.json'; delivery=json.loads(marker.read_text())
        delivery['receipt']['sha256']='b'*64; atomic_json(marker,delivery)
        self.options['backup']['local_retention']='remote-only'
        result=self.execute(now=self.now+timedelta(hours=1))
        self.assertFalse(result['passed']); self.assertTrue((job/'recovery.tar.gpg').exists())
        # Restore a valid delivery, then substitute a link to unrelated bytes.
        delivery['receipt']['sha256']=first['latest']['sha256']; atomic_json(marker,delivery)
        cipher=job/'recovery.tar.gpg'; cipher.unlink()
        outside=self.root/'unrelated'; outside.write_bytes(b'do not delete'); outside.chmod(0o600)
        for hard in (False,True):
            with self.subTest(hard=hard):
                if hard: os.link(outside,cipher)
                else: cipher.symlink_to(outside)
                result=self.execute(now=self.now+timedelta(hours=1))
                self.assertFalse(result['passed']); self.assertEqual(outside.read_bytes(),b'do not delete')
                self.assertTrue(cipher.is_symlink() if not hard else cipher.exists())
                cipher.unlink()

    def test_metadata_retention_keeps_latest_receipts_and_never_prunes_pending_upload(self):
        first=self.execute(); old=self.job(first)
        self.remote.fail=True
        result=self.execute(now=self.now+timedelta(days=2))
        pending=self.job(result)
        self.assertFalse(result['passed']); self.assertTrue((pending/'recovery.tar.gpg').exists())
        self.assertTrue(old.exists()); self.assertTrue((old/'recovery.tar.gpg.json').exists())
        self.remote.fail=False
        result=self.execute(now=self.now+timedelta(days=2,minutes=31))
        self.assertTrue(result['passed']); self.assertFalse(old.exists())
        self.assertTrue((pending/'recovery.tar.gpg.json').exists())
        self.assertFalse((pending/'recovery.tar.gpg').exists())


@unittest.skipUnless(streams.fixtures.DSN,'Use backup_stream_proof.py with disposable PostgreSQL')
class NativeRemoteRetentionTests(unittest.TestCase):
    """Real SQL/encryption/recovery through a simulated off-machine destination."""
    @classmethod
    def setUpClass(cls):
        streams.transfers.NativeEncryptionTests.setUpClass.__func__(cls)

    def setUp(self):
        from deploy._workflow import frontend_path
        from .test_backup_live import LiveBackupTests
        self.live=LiveBackupTests(); self.addCleanup(self.live.doCleanups); self.live.setUp()
        self.f=self.live.f; self.root=self.f.root
        self.storage=self.root/'scheduled'; self.storage.mkdir(mode=0o700)
        dsn=self.root/'scheduled.dsn'; dsn.write_text(self.f.q.dsn); dsn.chmod(0o600)
        for name in ('identity','known_hosts'):
            path=self.root/name; path.write_bytes(b'fictional pinned credential'); path.chmod(0o600)
        source=dict(frontend=str(frontend_path()),state=str(self.f.state),pool=self.f.q.pool_id,
                    schema=self.f.q.schema,dsn_file=str(dsn))
        if self.f.tools.container: source['client_container']=self.f.tools.container
        sections=dict(source=source,backup=dict(directory=str(self.storage),public_key=str(self.public),
            recipient=self.recipient,local_retention='remote-only'),remote=dict(host='backup.example.invalid',
            user='backup',port=22,directory='/backups',identity=str(self.root/'identity'),
            known_hosts=str(self.root/'known_hosts')),alerts=dict(enabled=False))
        text=''.join('['+section+']\n'+''.join(key+' = '+json.dumps(value)+'\n' for key,value in fields.items())
                     for section,fields in sections.items())
        config=self.root/'scheduled.toml'; config.write_text(text); config.chmod(0o600)
        self.options=schedule.load_config(config); self.remote=MemorySFTP(self.root)
        self.now=schedule.utc()

    def execute(self, **kwargs):
        from .maintenance import service_state
        defaults=dict(now=self.now,transport=self.remote,alert=Mock(side_effect=AssertionError('No real mail')))
        defaults.update(kwargs)
        with service_state(self.f.state): return schedule.run(self.options,**defaults)

    def retrieve(self, latest):
        from . import backup_transport
        remote=self.remote.directory+'/recovery-'+latest['backup_id']+'.tar.gpg'
        incoming=self.root/'retrieved.tar.gpg'
        incoming.write_bytes(self.remote.remote[remote]); incoming.chmod(0o400)
        atomic_json(Path(str(incoming)+'.json'),json.loads(self.remote.remote[remote+'.json']))
        self.assertEqual(sealed(incoming)['sha256'],latest['sha256'])
        decrypted=self.root/'decrypted'
        backup_transport.unseal(incoming,decrypted,keyring=self.home,expected_sha256=latest['sha256'])
        return decrypted

    def test_remote_retrieval_restores_native_database_and_files_after_local_disposal(self):
        from . import backup
        from .maintenance import GATE
        result=self.execute(); self.assertTrue(result['passed'])
        job=self.storage/'jobs'/result['latest']['job_id']
        self.assertFalse((job/'recovery.tar.gpg').exists())
        decrypted=self.retrieve(result['latest'])
        manifest=backup.verify(decrypted)
        self.assertEqual(manifest['id'],result['latest']['backup_id'])
        restored=backup.restore(self.f.target_q,decrypted,self.f.target,self.f.target_tools)
        self.assertTrue(restored['inactive']); self.assertTrue((self.f.target/GATE).exists())
        self.assertEqual(self.f.target_q.inspect(self.f.owner,self.f.exp),self.f.q.inspect(self.f.owner,self.f.exp))

    def test_native_pending_ciphertext_retries_without_recapture_before_disposal(self):
        self.remote.fail=True
        capture=Mock(wraps=schedule.capture_encrypted_backup)
        first=self.execute(capture_encrypted=capture)
        self.assertFalse(first['passed'])
        pending=schedule.load_status(self.storage)['current']
        cipher=self.storage/'jobs'/pending['id']/'recovery.tar.gpg'
        before=sealed(cipher)
        self.remote.fail=False
        result=self.execute(now=self.now+timedelta(minutes=31),capture_encrypted=capture)
        self.assertTrue(result['passed']); self.assertEqual(capture.call_count,1)
        self.assertFalse(cipher.exists()); self.assertEqual(result['latest']['sha256'],before['sha256'])
        self.assertTrue((self.retrieve(result['latest'])/'database.dump').is_file())
