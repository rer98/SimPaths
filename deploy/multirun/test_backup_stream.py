"""(C) Copyright 2026, by Ross Richardson

Bounded archive capture, direct SingleRun compression, streamed SSH verification,
durable compact scheduling and native matching PostgreSQL/GnuPG restoration.
All payloads, recipients and remote processes are fictional.
@author ross richardson
"""
from contextlib import ExitStack
from datetime import timedelta
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from . import backup, backup_files, backup_live, backup_single, backup_transport as transport
from . import backup_schedule as schedule
from . import test_backup as fixtures
from . import test_backup_schedule as schedules
from . import test_backup_single as singles
from . import test_backup_transport as transfers
from .backup_stream import Archive
from .artifacts import ArtifactError
from .releases import atomic_json


class StreamArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name); self.source=self.root/'source'; self.source.mkdir(mode=0o700)

    def write(self, action):
        output=io.BytesIO()
        with gzip.GzipFile(fileobj=output,mode='wb') as compressed:
            with tarfile.open(fileobj=compressed,mode='w|') as tar:
                archive=Archive(tar); action(archive)
        return output.getvalue(),archive

    def test_large_source_is_compressed_without_a_second_raw_tree_and_restores_exact_bytes(self):
        source=self.source/'people.csv'
        with source.open('wb') as output:
            for _ in range(1024): output.write(b'fictional repeated CSV row\n'*1024)
        (self.source/'empty').mkdir(mode=0o700)
        content,archive=self.write(lambda out:out.copy_tree(self.source,'state'))
        self.assertLess(len(content),source.stat().st_size//100)
        self.assertEqual(set(self.root.iterdir()),{self.source})
        with tarfile.open(fileobj=io.BytesIO(content),mode='r:gz') as tar:
            names=tar.getnames()
            self.assertEqual(len(names),len(set(names)))
            self.assertIn('backup/state/empty',names)
            self.assertNotIn('backup',names)  # Existing unseal contract starts below this root.
            digest=hashlib.sha256()
            with tar.extractfile('backup/state/people.csv') as incoming:
                while block:=incoming.read(1024**2): digest.update(block)
        self.assertEqual(digest.hexdigest(),archive.tree('state')['files']['people.csv']['sha256'])

    def test_changed_previously_streamed_source_aborts_the_whole_selection(self):
        (self.source/'a').write_bytes(b'old'); (self.source/'b').write_bytes(b'old')
        def action(out):
            original=out.add_file
            def add(path,key,**kwargs):
                value=original(path,key,**kwargs)
                if path.name=='b': (self.source/'a').write_bytes(b'new')
                return value
            out.add_file=add
            out.copy_selected(self.source,'state',{'a','b'})
        with self.assertRaisesRegex(ArtifactError,'changed'): self.write(action)

    def test_nested_directories_are_opened_from_the_verified_private_root(self):
        nested=self.source/'release'/'defaults'; nested.mkdir(mode=0o755,parents=True)
        nested.parent.chmod(0o755); nested.chmod(0o755)
        data=nested/'parameters.csv'; data.write_bytes(b'fictional model options'); data.chmod(0o644)
        content,archive=self.write(lambda out:out.copy_selected(self.source,'state',{'release/defaults/parameters.csv'}))
        self.assertEqual(archive.tree('state')['files']['release/defaults/parameters.csv'],
                         backup_files.file_hash(self.source,'release/defaults/parameters.csv'))
        self.assertEqual(nested.stat().st_mode&0o777,0o755)
        self.assertEqual(nested.parent.stat().st_mode&0o777,0o755)
        with tarfile.open(fileobj=io.BytesIO(content),mode='r:gz') as tar:
            self.assertEqual(tar.getmember('backup/state/release/defaults/parameters.csv').mode,0o600)

    def test_world_accessible_capture_root_and_linked_nested_directories_still_fail(self):
        (self.source/'file').write_bytes(b'private')
        self.source.chmod(0o755)
        with self.assertRaisesRegex(ArtifactError,'private'):
            self.write(lambda out:out.copy_selected(self.source,'state',{'file'}))
        self.source.chmod(0o700)
        outside=self.root/'outside'; outside.mkdir(mode=0o700); (outside/'file').write_bytes(b'unrelated')
        (self.source/'linked').symlink_to(outside,target_is_directory=True)
        with self.assertRaises((ArtifactError,OSError)):
            self.write(lambda out:out.copy_selected(self.source,'state',{'linked/file'}))

    def test_complete_streamed_recovery_tree_passes_the_original_backup_verifier(self):
        fixture=fixtures.BackupFileTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        original,manifest=fixture.build_backup()
        def capture(out):
            # Production release children may be 0755 beneath the private state.
            out.copy_selected(fixture.state,'state',manifest['state_tree']['files'])
            for key in manifest['state_tree']['directories']: out.mkdir('state/'+key)
            out.mkdir('prepared')
            for key in ('database.dump','COPYRIGHT.md','manifest.json'): out.add_file(original/key,key)
            out.check(manifest)
        content,_=self.write(capture)
        target=self.root/'restored'; target.mkdir(mode=0o700)
        (target/'backup').mkdir(mode=0o700)
        def private_filter(member,path):
            selected=tarfile.data_filter(member,path)
            # Match unseal's private directories and original owner-only file
            # bits; Python's default data filter makes files owner-writable.
            return selected.replace(mode=0o700 if selected.isdir() else member.mode&0o700)
        with tarfile.open(fileobj=io.BytesIO(content),mode='r:gz') as tar:
            tar.extractall(target,filter=private_filter)
        self.assertEqual(backup.verify(target/'backup'),manifest)

    def test_links_special_files_and_duplicate_or_unsafe_members_are_rejected(self):
        (self.source/'link').symlink_to(self.root/'outside')
        with self.assertRaises(ArtifactError): self.write(lambda out:out.copy_tree(self.source,'state'))
        for key in ('../outside','/absolute','state/../escape'):
            with self.subTest(key=key),self.assertRaises(ArtifactError):
                self.write(lambda out:out.add_stream(key,io.BytesIO(b'x'),1))
        def duplicate(out):
            out.add_stream('state/a',io.BytesIO(b'x'),1)
            out.add_stream('state/a',io.BytesIO(b'x'),1)
        with self.assertRaises(ArtifactError): self.write(duplicate)

    def test_truncated_or_conflicting_source_never_claims_a_complete_member(self):
        with self.assertRaises(ArtifactError):
            self.write(lambda out:out.add_stream('state/short',io.BytesIO(b'x'),2))
        def conflict(out):
            out.add_stream('state/a',io.BytesIO(b'x'),1); out.mkdir('state/a/child')
        with self.assertRaises(ArtifactError): self.write(conflict)

    def test_single_run_zip_reads_docker_members_directly_and_preserves_saved_contents(self):
        container=singles.Container(); target=self.root/'saved.zip'
        with patch.object(backup_single,'container_tree',side_effect=AssertionError('No extracted CSV tree')):
            backup_single.container_zip(container,'/app/output/20261001_120000',target)
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read('20261001_120000/csv/Person.csv'),b'closed fictional CSV')
        self.assertEqual(set(self.root.iterdir()),{self.source,target})

    def test_changed_or_linked_docker_members_fail_direct_zip_capture(self):
        for kind in ('changed','linked','duplicate'):
            container=singles.Container()
            if kind=='changed': container.changed=True
            elif kind=='duplicate': container.duplicate=True
            else:
                container.member=tarfile.TarInfo('20261001_120000/link')
                container.member.type=tarfile.SYMTYPE
            with self.subTest(kind=kind),self.assertRaises(ArtifactError):
                backup_single.container_zip(container,'/app/output/20261001_120000',self.root/(kind+'.zip'))


class StreamReadbackTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        key=self.root/'key'; key.write_bytes(b'fictional'); key.chmod(0o600)
        pins=self.root/'pins'; pins.write_bytes(b'fictional'); pins.chmod(0o600)
        self.connection=transport.SFTP(host='example.invalid',user='fictional',port=23,
            directory='/backups',identity=key,known_hosts=pins)
        self.process=None; self.argv=None

    def read(self, script, receipt):
        real=subprocess.Popen
        def launch(argv,**kwargs):
            self.argv=argv
            self.process=real([sys.executable,'-c',script],**kwargs)
            return self.process
        with patch.object(transport.subprocess,'Popen',side_effect=launch):
            return self.connection.remote_hash('/backups/fictional.tar.gpg',receipt)

    def test_real_pipe_readback_hashes_multiple_buffers_without_any_download_file(self):
        data=b'fictional ciphertext\n'*100000
        expected=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        result=self.read('import sys;sys.stdout.buffer.write(b"fictional ciphertext\\n"*100000)',expected)
        self.assertEqual(result,expected)
        self.assertEqual({p.name for p in self.root.iterdir()},{'key','pins'})
        self.assertEqual(self.argv[-1],'cat /backups/fictional.tar.gpg')
        for flag in ('-oStrictHostKeyChecking=yes','-oIdentityAgent=none','-oProxyCommand=none'):
            self.assertIn(flag,self.argv)
        self.assertEqual(self.process.poll(),0)

    def test_failed_partial_read_never_verifies_and_child_is_joined(self):
        with self.assertRaises(ArtifactError):
            self.read('import sys;sys.stdout.buffer.write(b"short");sys.exit(1)',dict(bytes=20))
        self.assertIsNotNone(self.process.poll())
        self.assertIsNone(self.read('raise SystemExit(1)',dict(bytes=20)))
        with self.assertRaises(ArtifactError): self.read('raise SystemExit(255)',dict(bytes=20))

    def test_oversized_or_stalled_readback_is_killed_and_joined(self):
        with self.assertRaises(ArtifactError):
            self.read('import sys,time;sys.stdout.buffer.write(b"x"*10000);sys.stdout.flush();time.sleep(60)',dict(bytes=10))
        self.assertIsNotNone(self.process.poll())
        with patch.object(transport.time,'monotonic',side_effect=[0,7201]),self.assertRaises(ArtifactError):
            self.read('import time;time.sleep(60)',dict(bytes=10))
        self.assertIsNotNone(self.process.poll())

    def test_transfer_never_uses_get_or_creates_transfer_check_directory(self):
        f=fixtures.BackupFileTests(); f.setUp(); self.addCleanup(f.doCleanups)
        source,_=f.build_backup(); cipher=f.root/'recovery.gpg'
        transfers.fake_seal(source,cipher,public_key=None,recipient='A'*40)
        remote=transfers.MemorySFTP(f.root)
        with patch.object(transport.tempfile,'mkdtemp',side_effect=AssertionError('No readback spool')):
            self.assertTrue(remote.copy(cipher)['passed'])
        self.assertFalse(any(command.startswith('get ') for command in remote.commands))


class EncryptedOutputPumpTests(unittest.TestCase):
    """Use a compressor process to test pipe/storage failures; native proof uses GPG."""
    def setUp(self):
        fixture=fixtures.BackupFileTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.source,self.manifest=fixture.build_backup(); self.root=fixture.root
        self.keyring=self.root/'temporary-public-keyring'; self.keyring.mkdir(mode=0o700)
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(transport,'public_home',return_value=self.keyring))
        self.stack.enter_context(patch.object(transport,'dispose_keyring',side_effect=shutil.rmtree))
        self.stack.enter_context(patch.object(transport,'gpg_command',return_value=[sys.executable,'-c',
            'import sys,gzip;out=gzip.GzipFile(fileobj=sys.stdout.buffer,mode="wb")\n'
            'while block:=sys.stdin.buffer.read(65536):out.write(block)\n'
            'out.close()']))
        self.destination=self.root/'encrypted.gpg'

    def test_output_pump_inherits_configured_free_space_floor(self):
        floors=[]
        def space(*args): floors.append(backup_files._reserve.get()); return True
        with backup_files.reserve_space(128*1024**2),patch.object(transport,'has_space',side_effect=space):
            receipt=transport.seal(self.source,self.destination,public_key=None,recipient='A'*40)
        self.assertTrue(floors); self.assertEqual(set(floors),{128*1024**2})
        self.assertEqual(receipt['backup_id'],self.manifest['id'])
        self.assertEqual(transport.sealed(self.destination),receipt)

    def test_output_space_failure_cleans_partial_cipher_and_joins_the_pump(self):
        before={t.ident for t in threading.enumerate()}
        with patch.object(transport,'has_space',return_value=False),self.assertRaisesRegex(ArtifactError,'space'):
            transport.seal(self.source,self.destination,public_key=None,recipient='A'*40)
        self.assertFalse(self.destination.exists())
        self.assertFalse(Path(str(self.destination)+'.json').exists())
        self.assertFalse(any(p.name.startswith('.encrypting-') for p in self.root.iterdir()))
        self.assertFalse(any(t.ident not in before and t.name=='backup-encrypted-output' for t in threading.enumerate()))

    def test_source_producer_failure_publishes_neither_archive_nor_receipt(self):
        def fail(archive): raise RuntimeError('fictional source changed')
        with self.assertRaisesRegex(RuntimeError,'source changed'):
            transport.seal_stream(self.destination,fail,public_key=None,recipient='A'*40)
        self.assertFalse(self.destination.exists()); self.assertFalse(Path(str(self.destination)+'.json').exists())


class CompactScheduleTests(unittest.TestCase):
    setUp=schedules.ScheduleTests.setUp
    alert=schedules.ScheduleTests.alert

    def compact(self, options, path):
        self.calls+=1
        return transfers.fake_seal(self.source,path,public_key=self.key,recipient=options['backup']['recipient'])

    def execute(self, **kwargs):
        defaults=dict(now=self.now,capture_encrypted=self.compact,alert=self.alert)
        defaults.update(kwargs)
        return schedule.run(self.options,**defaults)

    def test_default_scheduler_uses_direct_capture_without_plaintext_directory(self):
        result=self.execute(); self.assertTrue(result['passed']); self.assertEqual(self.calls,1)
        directory=self.storage/'jobs'/result['latest']['job_id']
        self.assertFalse((directory/'plain').exists())
        self.assertEqual({p.name for p in directory.iterdir()},
            {'recovery.tar.gpg','recovery.tar.gpg.json','complete.json'})

    def test_remote_outage_reuses_exact_ciphertext_and_does_not_recapture(self):
        self.options['remote']={'configured':True}
        remote=transfers.MemorySFTP(self.root); remote.fail=True
        first=self.execute(transport=remote); self.assertEqual(first['pending_stage'],'copying')
        pending=schedule.load_status(self.storage)['current']
        cipher=self.storage/'jobs'/pending['id']/'recovery.tar.gpg'
        before=cipher.read_bytes(); remote.fail=False
        result=self.execute(now=self.now+timedelta(minutes=31),transport=remote)
        self.assertTrue(result['passed']); self.assertEqual(self.calls,1)
        self.assertEqual(cipher.read_bytes(),before)

    def test_lost_capture_acknowledgement_reuses_published_ciphertext(self):
        def lost(options,path):
            self.compact(options,path); raise RuntimeError('lost journal acknowledgement')
        first=self.execute(capture_encrypted=lost)
        self.assertEqual(first['pending_stage'],'capturing')
        second=self.execute(now=self.now+timedelta(minutes=31))
        self.assertTrue(second['passed']); self.assertEqual(self.calls,1)

    def test_partial_capture_and_orphan_receipt_are_removed_before_recapture(self):
        def interrupted(options,path):
            self.compact(options,path); path.unlink()
            temporary=path.parent/'.backup-incomplete-fictional'; temporary.mkdir(mode=0o700)
            (temporary/'small.dump').write_bytes(b'partial')
            raise RuntimeError('interrupted before ciphertext publication')
        first=self.execute(capture_encrypted=interrupted)
        self.assertEqual(first['pending_stage'],'capturing')
        result=self.execute(now=self.now+timedelta(minutes=31))
        self.assertTrue(result['passed']); self.assertEqual(self.calls,2)
        directory=self.storage/'jobs'/result['latest']['job_id']
        self.assertFalse(any(p.name.startswith('.') for p in directory.iterdir()))

    def test_existing_legacy_plaintext_job_still_finishes_without_new_capture(self):
        first=schedule.run(self.options,now=self.now,capture=schedules.ScheduleTests.capture.__get__(self),
            encrypt=lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('old sealing interrupted')),alert=self.alert)
        self.assertEqual(first['pending_stage'],'sealing')
        result=schedule.run(self.options,now=self.now+timedelta(minutes=31),encrypt=transfers.fake_seal,
            capture_encrypted=lambda *args:(_ for _ in ()).throw(AssertionError('Do not recapture')),alert=self.alert)
        self.assertTrue(result['passed']); self.assertEqual(self.calls,1)


@unittest.skipUnless(fixtures.DSN,'Use backup_stream_proof.py with disposable PostgreSQL')
class NativeStreamRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        transfers.NativeEncryptionTests.setUpClass.__func__(cls)

    def setUp(self):
        from . import test_backup_live as live
        self.live=live.LiveBackupTests(); self.live.setUp(); self.addCleanup(self.live.doCleanups)
        self.f=self.live.f

    def streamed_create(self, queue, state, destination, tools, **kwargs):
        cipher=destination.parent/(destination.name+'.tar.gpg')
        with (patch.object(backup_live,'copy_selected',side_effect=AssertionError('No plaintext selected tree')),
              patch.object(backup_live,'copy_tree',side_effect=AssertionError('No plaintext prepared tree'))):
            receipt=backup_live.create_encrypted(queue,state,cipher,tools,
                public_key=self.public,recipient=self.recipient,**kwargs)
        self.assertFalse(any(p.name.startswith('.backup-incomplete-') for p in destination.parent.iterdir()))
        transport.unseal(cipher,destination,keyring=self.home,expected_sha256=receipt['sha256'])
        return backup.summary(backup.verify(destination),'backup',online=True)

    def test_original_running_job_snapshot_and_native_restore_guards_pass_through_streaming(self):
        with patch.object(backup_live,'create',side_effect=self.streamed_create):
            self.live.test_running_attempt_new_publication_and_heartbeat_continue_while_snapshot_stays_fixed()

    def test_shared_singlerun_permissions_and_saved_exports_restore_from_stream(self):
        with patch.object(backup_live,'create',side_effect=self.streamed_create):
            self.live.test_shared_single_run_ownership_saved_exports_and_inactive_gate_survive_native_restore()

    def test_expired_raw_data_summaries_charts_and_revocation_survive_streamed_restore(self):
        from .test_backup_visualiser import NativeVisualiserRestoreTests
        fixture=NativeVisualiserRestoreTests(); fixture.f=self.f
        with patch.object(backup_live,'create',side_effect=self.streamed_create):
            fixture.check_roundtrip(True)
