"""(C) Copyright 2026, by Ross Richardson

Fictional encrypted-backup round trips and isolated SFTP protocol regressions.
Native GnuPG tests run in the disposable host proof with an ephemeral test key.
No external host, user keyring or real recipient is contacted.
@author ross richardson
"""
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from . import backup, backup_transport as transport
from .artifacts import ArtifactError
from .backup_files import file_hash
from .releases import atomic_json
from . import test_backup as fixtures
from .test_backup import DSN


def fake_seal(source, destination, *, public_key, recipient):
    """Stage-machine fixture only; native encryption is verified separately."""
    source=Path(source); destination=Path(destination)
    manifest=backup.verify(source)
    destination.write_bytes(b'fictional encrypted bytes '+manifest['id'].encode()); destination.chmod(0o400)
    value=dict(format=transport.FORMAT,backup_id=manifest['id'],recipient=recipient,**file_hash(destination.parent,destination.name))
    atomic_json(Path(str(destination)+'.json'),value)
    return value


class MemorySFTP(transport.SFTP):
    """Exact put/get/chmod/ln/rm semantics, including atomic no-overwrite links."""
    def __init__(self, root):
        self.directory='/backups'; self.remote={}; self.commands=[]; self.corrupt=False; self.lost_ack=False; self.fail=False
    def batch(self, commands):
        self.commands+=commands
        if self.fail: return False
        for command in commands:
            args=shlex.split(command); operation=args[0]
            if operation=='get':
                if args[1] not in self.remote: return False
                path=Path(args[2]); path.write_bytes(self.remote[args[1]]); path.chmod(0o600)
            elif operation=='put': self.remote[args[2]]=Path(args[1]).read_bytes()+(b'corrupt' if self.corrupt else b'')
            elif operation=='chmod': pass
            elif operation=='ln':
                if args[2] in self.remote: return False
                self.remote[args[2]]=self.remote[args[1]]
                if self.lost_ack: self.lost_ack=False; return False
            elif operation=='rm': self.remote.pop(args[1],None)
            else: raise AssertionError(command)
        return True


class TransportTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.BackupFileTests(); f.setUp(); self.addCleanup(f.doCleanups)
        self.root=f.root; self.backup,self.manifest=f.build_backup()
        self.cipher=self.root/'recovery.gpg'
        self.recipient='A'*40
        fake_seal(self.backup,self.cipher,public_key=None,recipient=self.recipient)

    def test_only_encrypted_bytes_and_receipt_are_copied_and_duplicate_transfer_is_idempotent(self):
        remote=MemorySFTP(self.root)
        first=remote.copy(self.cipher); second=remote.copy(self.cipher)
        self.assertFalse(first['already_copied']); self.assertTrue(second['already_copied'])
        self.assertEqual(2,len(remote.remote))
        self.assertTrue(all('private raw' not in data.decode() for data in remote.remote.values()))
        self.assertEqual(self.cipher.read_bytes(),next(v for k,v in remote.remote.items() if k.endswith('.gpg')))
        self.assertTrue(any(k.endswith('.json') for k in remote.remote))

    def test_corrupt_or_conflicting_remote_copy_is_never_published_or_overwritten(self):
        remote=MemorySFTP(self.root); remote.corrupt=True
        with self.assertRaises(ArtifactError): remote.copy(self.cipher)
        self.assertFalse(any(k.endswith('.gpg') for k in remote.remote))
        remote.corrupt=False; remote.copy(self.cipher)
        key=next(k for k in remote.remote if k.endswith('.gpg')); remote.remote[key]=b'different'
        with self.assertRaises(ArtifactError): remote.copy(self.cipher)
        self.assertEqual(b'different',remote.remote[key])

    def test_lost_publication_acknowledgement_recovers_identical_ciphertext(self):
        remote=MemorySFTP(self.root); remote.lost_ack=True
        self.assertTrue(remote.copy(self.cipher)['passed'])
        self.assertTrue(remote.copy(self.cipher)['already_copied'])

    def test_changed_local_ciphertext_and_changed_recipient_are_denied(self):
        with self.assertRaises(ArtifactError): transport.sealed(self.cipher,recipient='B'*40)
        self.cipher.chmod(0o600); self.cipher.write_bytes(b'changed')
        with self.assertRaises(ArtifactError): MemorySFTP(self.root).copy(self.cipher)

    def test_host_pin_and_identity_are_explicit_and_batch_paths_cannot_inject_commands(self):
        key=self.root/'identity'; key.write_bytes(b'fictional'); key.chmod(0o600)
        pins=self.root/'known_hosts'; pins.write_bytes(b'fictional pin'); pins.chmod(0o600)
        connection=transport.SFTP(host='host.example.invalid',user='operator',port=22,directory='/backups',identity=key,known_hosts=pins)
        with patch.object(transport.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as call:
            self.assertTrue(connection.batch(['get "remote" "local"']))
        argv=call.call_args.args[0]
        for expected in ('/dev/null','-oStrictHostKeyChecking=yes','-oIdentityAgent=none','-oProxyCommand=none','-oForwardAgent=no'):
            self.assertIn(expected,argv)
        with self.assertRaises(ArtifactError): transport.sftp_quote('bad\n!command')
        key.chmod(0o644)
        with self.assertRaises(ArtifactError): transport.SFTP(host='host.example.invalid',user='operator',port=22,directory='/backups',identity=key,known_hosts=pins)


@unittest.skipUnless(DSN,'Native GnuPG requires the host backup proof')
class NativeEncryptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='fictional-backup-key-'); cls.root=Path(cls.temporary.name)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.home=cls.root/'keyring'; cls.home.mkdir(mode=0o700)
        cls.addClassCleanup(transport.dispose_keyring,cls.home)
        command=transport.gpg_command(cls.home)
        result=subprocess.run(command+['--pinentry-mode','loopback','--passphrase','',
            '--quick-generate-key','Fictional Backup Proof <backup@example.invalid>','rsa2048','encrypt','1d'],capture_output=True,timeout=90)
        if result.returncode: raise AssertionError('Native GnuPG ephemeral test key could not be created')
        listing=subprocess.check_output(command+['--with-colons','--fingerprint','--list-keys'])
        cls.recipient=next(line.split(':')[9] for line in listing.decode().splitlines() if line.startswith('fpr:'))
        cls.public=cls.root/'public.asc'; cls.public.write_bytes(subprocess.check_output(command+['--armor','--export',cls.recipient])); cls.public.chmod(0o600)

    def setUp(self):
        f=fixtures.BackupFileTests(); f.setUp(); self.addCleanup(f.doCleanups)
        self.root=f.root; self.backup,self.manifest=f.build_backup()

    def encrypt(self):
        cipher=self.root/'recovery.gpg'
        receipt=transport.seal(self.backup,cipher,public_key=self.public,recipient=self.recipient)
        return cipher,receipt

    def test_native_encryption_decryption_and_full_backup_hashes_round_trip(self):
        cipher,receipt=self.encrypt(); recovered=self.root/'decrypted'
        self.assertNotIn(b'fictional model',cipher.read_bytes())
        transport.unseal(cipher,recovered,keyring=self.home,expected_sha256=receipt['sha256'])
        self.assertEqual(backup.verify(self.backup),backup.verify(recovered))
        self.assertEqual(0o400,cipher.stat().st_mode&0o777)

    def test_wrong_key_pin_corrupted_ciphertext_and_wrong_trusted_checksum_fail_closed(self):
        with self.assertRaises(ArtifactError): transport.seal(self.backup,self.root/'wrong.gpg',public_key=self.public,recipient='B'*40)
        cipher,receipt=self.encrypt()
        with self.assertRaises(ArtifactError): transport.unseal(cipher,self.root/'wrong-hash',keyring=self.home,expected_sha256='a'*64)
        cipher.chmod(0o600); data=bytearray(cipher.read_bytes()); data[len(data)//2]^=1; cipher.write_bytes(data)
        # Even when a caller supplies the changed SHA, GnuPG integrity must fail.
        with self.assertRaises(ArtifactError):
            transport.unseal(cipher,self.root/'corrupt',keyring=self.home,expected_sha256=hashlib.sha256(data).hexdigest())
        self.assertFalse((self.root/'corrupt').exists())
