"""(C) Copyright 2026, by Ross Richardson

Explicit count-only backup history, verified survivors and interrupted pruning.
Uses tiny fictional encrypted bytes and real file locks; no VPS or mail activity.
@author ross richardson
"""
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path
from jasmine_web.batch.backup_guard import BackupBusy,file_guard
from . import backup_schedule as schedule
from .artifacts import ArtifactError
from .backup_files import file_hash
from .releases import atomic_json
from .test_backup_transport import MemorySFTP


class Crash(BaseException):pass


class CountRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.now=datetime(2026,11,12,1,tzinfo=timezone.utc)
        (self.root/'jobs').mkdir(mode=0o700)
        self.config=dict(_digest='d'*64,source=dict(frontend=str(frontend_path())),
            backup=dict(directory=str(self.root),recipient='A'*40,keep_latest=2,keep_days=30,
                interval_hours=24,retry_minutes=30,minimum_free_gib=1,local_retention='retain',retention_rule='count'))
        self.status=dict(format=schedule.FORMAT,current=None,last_success=None,incident=None)

    def completed(self,number,*,age_hours=None,config=None):
        key=f'{number:032x}';path=self.root/'jobs'/key;path.mkdir(mode=0o700,parents=True)
        cipher=path/'recovery.tar.gpg';cipher.write_bytes(b'fictional encrypted recovery '+key.encode());cipher.chmod(0o400)
        backup=f'{number+1000:032x}'
        receipt=dict(format='simpaths.backup.encrypted.v1',backup_id=backup,recipient='A'*40,
            **file_hash(path,cipher.name));atomic_json(path/'recovery.tar.gpg.json',receipt)
        record=dict(job_id=key,backup_id=backup,config=config or self.config['_digest'],sha256=receipt['sha256'],
            finished_at=(self.now-timedelta(hours=age_hours if age_hours is not None else 10-number)).isoformat(),
            off_machine=False)
        atomic_json(path/'complete.json',record)
        if record['config']==self.config['_digest'] and (self.status['last_success'] is None or
                record['finished_at']>self.status['last_success']['finished_at']):
            self.status['last_success']=record.copy()
        return path,record

    def rotate(self):
        with file_guard(self.root,backup=True):
            schedule.retention(self.root,self.config,self.status,self.now)

    def test_explicit_count_keeps_two_even_when_every_completed_copy_is_under_thirty_days(self):
        paths=[self.completed(i)[0] for i in range(1,5)]
        self.rotate()
        self.assertEqual([path.exists() for path in paths],[False,False,True,True])
        self.assertFalse((self.root/schedule.ROTATION_JOURNAL).exists())
        self.assertEqual(list((self.root/schedule.ROTATION_FOLDER).iterdir()),[])

    def test_default_rule_keeps_the_existing_age_and_count_floor(self):
        self.config['backup'].pop('retention_rule');paths=[self.completed(i)[0] for i in range(1,4)]
        with patch.object(schedule,'sealed',side_effect=AssertionError('No count-only archive scan')):self.rotate()
        self.assertTrue(all(path.exists() for path in paths))
        old,_=self.completed(99,age_hours=31*24)
        self.rotate();self.assertFalse(old.exists());self.assertTrue(all(path.exists() for path in paths))

    def test_missing_or_corrupt_survivor_prevents_every_older_removal(self):
        for missing in (False,True):
            with self.subTest(missing=missing):
                paths=[self.completed(i)[0] for i in range(1,4)]
                cipher=paths[-1]/'recovery.tar.gpg'
                if missing:cipher.unlink()
                else:cipher.chmod(0o600);cipher.write_bytes(b'corrupted');cipher.chmod(0o400)
                with self.assertRaises((ArtifactError,FileNotFoundError)):self.rotate()
                self.assertTrue(all(path.exists() for path in paths))
                self.assertFalse((self.root/schedule.ROTATION_JOURNAL).exists())
                import shutil
                shutil.rmtree(self.root/'jobs');(self.root/'jobs').mkdir(mode=0o700);self.status['last_success']=None

    def test_linked_survivors_never_authorize_rotation_or_touch_unrelated_files(self):
        paths=[self.completed(i)[0] for i in range(1,4)]
        cipher=paths[-1]/'recovery.tar.gpg';cipher.unlink()
        outside=self.root/'unrelated';outside.write_bytes(b'keep');outside.chmod(0o400)
        for hard in (False,True):
            with self.subTest(hard=hard):
                if hard:os.link(outside,cipher)
                else:cipher.symlink_to(outside)
                with self.assertRaises((ArtifactError,OSError)):self.rotate()
                self.assertEqual(outside.read_bytes(),b'keep');self.assertTrue(paths[0].exists());cipher.unlink()

    def test_every_kept_copy_is_hashed_before_first_old_file_is_removed(self):
        paths=[self.completed(i)[0] for i in range(1,5)];events=[]
        sealed=schedule.sealed;unlink=Path.unlink
        def verified(path,**kwargs):events.append(('verified',path.parent.name));return sealed(path,**kwargs)
        def removed(path,*args,**kwargs):events.append(('removed',path.parent.name));return unlink(path,*args,**kwargs)
        with patch.object(schedule,'sealed',side_effect=verified),patch.object(Path,'unlink',removed):self.rotate()
        first=next(i for i,row in enumerate(events) if row[0]=='removed')
        self.assertEqual({row[1] for row in events[:first] if row[0]=='verified'},{p.name for p in paths[-2:]})
        self.assertEqual(sum(row[0]=='verified' for row in events),2)

    def test_no_eligible_pruning_avoids_rehashing_large_survivors_on_timer_checks(self):
        for i in range(1,3):self.completed(i)
        with patch.object(schedule,'sealed',side_effect=AssertionError('Unnecessary large archive rehash')):
            self.rotate();self.rotate()

    def test_pending_copy_and_other_configuration_scope_are_never_selected(self):
        paths=[self.completed(i)[0] for i in range(1,4)]
        pending,_=self.completed(4);self.status['current']=dict(id=pending.name,stage='capturing')
        self.status['last_success']=schedule.read_metadata(paths[-1]/'complete.json')
        other,_=self.completed(5,config='e'*64)
        self.rotate()
        self.assertFalse(paths[0].exists());self.assertTrue(pending.exists());self.assertTrue(other.exists())
        self.assertTrue(all(path.exists() for path in paths[1:]))

    def test_inconsistent_last_success_cannot_be_pruned_from_surviving_history(self):
        paths=[self.completed(i)[0] for i in range(1,4)]
        self.status['last_success']=schedule.read_metadata(paths[0]/'complete.json')
        with self.assertRaises(ArtifactError):self.rotate()
        self.assertTrue(all(path.exists() for path in paths))

    def test_changed_receipt_cannot_authorize_pruning_even_when_ciphertext_is_present(self):
        paths=[self.completed(i)[0] for i in range(1,4)]
        receipt=paths[-1]/'recovery.tar.gpg.json';value=schedule.read_metadata(receipt);value['sha256']='f'*64
        atomic_json(receipt,value)
        with self.assertRaises(ArtifactError):self.rotate()
        self.assertTrue(paths[0].exists())

    def test_existing_backup_file_fence_refuses_rotation_before_any_maintenance(self):
        paths=[self.completed(i)[0] for i in range(1,4)]
        with file_guard(self.root),self.assertRaises(BackupBusy):schedule.run(self.config,now=self.now)
        self.assertTrue(all(path.exists() for path in paths))

    def test_crash_after_quarantine_rename_replays_exact_identity_once(self):
        paths=[self.completed(i)[0] for i in range(1,4)];rename=os.rename
        def detached(source,destination):rename(source,destination);raise Crash()
        with patch.object(schedule.os,'rename',side_effect=detached),self.assertRaises(Crash):self.rotate()
        self.assertFalse(paths[0].exists());self.assertTrue((self.root/schedule.ROTATION_FOLDER/paths[0].name).exists())
        self.rotate();self.rotate()
        self.assertFalse((self.root/schedule.ROTATION_JOURNAL).exists())
        self.assertTrue(all(path.exists() for path in paths[1:]))

    def interrupt_unlink(self):
        paths=[self.completed(i)[0] for i in range(1,4)];unlink=Path.unlink
        def interrupted(path,*args,**kwargs):
            unlink(path,*args,**kwargs)
            if path.name=='recovery.tar.gpg' and schedule.ROTATION_FOLDER in path.parts:raise Crash()
        with patch.object(Path,'unlink',interrupted),self.assertRaises(Crash):self.rotate()
        return paths

    def test_crash_after_payload_unlink_resumes_without_a_completion_marker(self):
        paths=self.interrupt_unlink();detached=self.root/schedule.ROTATION_FOLDER/paths[0].name
        self.assertFalse((detached/'complete.json').exists());self.assertFalse((detached/'recovery.tar.gpg').exists())
        self.rotate();self.assertFalse(detached.exists());self.assertTrue(all(path.exists() for path in paths[1:]))

    def test_unexpected_file_in_quarantine_is_kept_and_stops_recovery(self):
        paths=self.interrupt_unlink();detached=self.root/schedule.ROTATION_FOLDER/paths[0].name
        unexpected=detached/'unexpected';unexpected.write_bytes(b'do not delete')
        with self.assertRaises(ArtifactError):self.rotate()
        self.assertEqual(unexpected.read_bytes(),b'do not delete');self.assertTrue((self.root/schedule.ROTATION_JOURNAL).exists())

    def test_crash_after_directory_removal_before_journal_unlink_is_idempotent(self):
        paths=[self.completed(i)[0] for i in range(1,4)];unlink=Path.unlink
        def interrupted(path,*args,**kwargs):
            if path.name==schedule.ROTATION_JOURNAL:raise Crash()
            return unlink(path,*args,**kwargs)
        with patch.object(Path,'unlink',interrupted),self.assertRaises(Crash):self.rotate()
        self.assertFalse(paths[0].exists());self.rotate()
        self.assertFalse((self.root/schedule.ROTATION_JOURNAL).exists())

    def test_missing_survivor_after_crash_keeps_remaining_quarantined_files(self):
        paths=self.interrupt_unlink();remaining=self.root/schedule.ROTATION_FOLDER/paths[0].name/'recovery.tar.gpg.json'
        (paths[-1]/'recovery.tar.gpg').unlink()
        with self.assertRaises(ArtifactError):self.rotate()
        self.assertTrue(remaining.exists())

    def test_new_setting_defaults_off_and_does_not_change_the_pending_scope_digest(self):
        key=self.root/'public.asc';key.write_bytes(b'public');key.chmod(0o600)
        path=self.root/'backup.toml'
        text=f'''[source]\nfrontend = "{frontend_path()}"\nstate = "{self.root}"\ndsn_file = "{self.root}/dsn"\npool = "pool"\nschema = "jasmine_batch"\n[backup]\ndirectory = "{self.root}"\npublic_key = "{key}"\nrecipient = "{'A'*40}"\nkeep_latest = 2\n'''
        path.write_text(text);path.chmod(0o600)
        with patch.object(schedule,'source_scope',return_value=dict(fictional='endpoint')):
            before=schedule.load_config(path);self.assertEqual(before['backup']['retention_rule'],'age-and-count')
            path.write_text(text+'retention_rule = "count"\n');after=schedule.load_config(path)
            self.assertEqual(before['_digest'],after['_digest']);self.assertEqual(after['backup']['retention_rule'],'count')
            for invalid in ('"unknown"','true','1'):
                path.write_text(text+'retention_rule = '+invalid+'\n')
                with self.assertRaises(ArtifactError):schedule.load_config(path)

    def remote_history(self):
        self.config['remote']=dict(configured=True);self.config['backup']['local_retention']='remote-only'
        remote=MemorySFTP(self.root);paths=[]
        for number in range(1,4):
            path,record=self.completed(number);paths.append(path)
            record['off_machine']=True;atomic_json(path/'complete.json',record)
            self.status['last_success']=record.copy()
            receipt=schedule.sealed(path/'recovery.tar.gpg',recipient=self.config['backup']['recipient'])
            schedule.verified_delivery(self.config,path,dict(id=path.name),receipt,remote,self.now)
            schedule.remove_local_archive(self.root,self.config,self.status,path,record,self.now,remote)
        return remote,paths

    def test_remote_only_history_verifies_actual_remote_survivors_and_keeps_remote_objects(self):
        remote,paths=self.remote_history();before=dict(remote.remote)
        with file_guard(self.root,backup=True):schedule.retention(self.root,self.config,self.status,self.now,transport=remote)
        self.assertFalse(paths[0].exists());self.assertTrue(all(path.exists() for path in paths[1:]))
        self.assertEqual(remote.remote,before)

    def test_failed_remote_survivor_readback_keeps_all_local_history_records(self):
        remote,paths=self.remote_history();receipt=schedule.read_metadata(paths[-1]/'recovery.tar.gpg.json')
        remote.remote[remote.directory+'/recovery-'+receipt['backup_id']+'.tar.gpg']+=b'corrupt'
        with file_guard(self.root,backup=True),self.assertRaises(ArtifactError):
            schedule.retention(self.root,self.config,self.status,self.now,transport=remote)
        self.assertTrue(all(path.exists() for path in paths))


if __name__=='__main__':unittest.main(verbosity=2)
