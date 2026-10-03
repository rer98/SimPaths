"""(C) Copyright 2026, by Ross Richardson

Public-key encrypted backup bundles and pinned-host SFTP publication.
No plaintext leaves the VM. Remote publication is verified by reading encrypted
bytes back and uses a hard link so an existing completed copy is never replaced.
@author ross richardson
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import stat
import tarfile
import tempfile

from .artifacts import ArtifactError
from .backup_files import (MAX_ENTRIES, file_hash, has_space, name, open_file, publish,
                           read_metadata, scan, sync_tree)
from .releases import atomic_json, existing_directory

FORMAT='simpaths.backup.encrypted.v1'


def operator_file(path, *, private=True):
    """Read a pinned file in a root-managed config directory, without links."""
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ArtifactError('Operator backup files must not contain links')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    info=os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or
            info.st_uid not in ({os.getuid()} if private else {0,os.getuid()}) or
            info.st_mode&(0o077 if private else 0o022)):
        os.close(fd); raise ArtifactError('Operator backup file has unsafe ownership or permissions')
    return os.fdopen(fd,'rb')


def fingerprint(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64}',value):
        raise ArtifactError('Pin the complete OpenPGP recipient fingerprint')
    return value.upper()


def gpg_command(home):
    command=shutil.which('gpg')
    if not command: raise ArtifactError('Install GnuPG before encrypting backups')
    return [command,'--no-options','--homedir',str(home),'--batch','--no-tty',
            '--auto-key-locate','clear','--no-auto-key-retrieve']


def dispose_keyring(home):
    executable=shutil.which('gpgconf')
    if executable:
        subprocess.run([executable,'--homedir',str(home),'--kill','gpg-agent'],capture_output=True,timeout=10)
    shutil.rmtree(home)


def public_home(parent, public_key, recipient):
    home=Path(tempfile.mkdtemp(prefix='.backup-public-key-',dir=parent))
    try:
        key=Path(public_key)
        # Operator key files are bounded, owned and pinned. No ambient keyring or
        # configuration can substitute another recipient or enable key discovery.
        with operator_file(key,private=False) as source:
            if os.fstat(source.fileno()).st_size>4*1024**2: raise ArtifactError('Backup public key is too large')
            result=subprocess.run(gpg_command(home)+['--import'],stdin=source,capture_output=True,timeout=30)
        if result.returncode: raise ArtifactError('Backup public key could not be imported')
        result=subprocess.run(gpg_command(home)+['--with-colons','--fingerprint','--list-keys'],capture_output=True,timeout=30)
        primary=[]
        waiting=False
        for line in result.stdout.decode('utf-8').splitlines():
            fields=line.split(':')
            if fields[0]=='pub': waiting=True
            elif fields[0]=='fpr' and waiting:
                primary.append(fields[9]); waiting=False
        if result.returncode or primary!=[fingerprint(recipient)]:
            raise ArtifactError('Backup public key differs from the pinned recipient')
        # A public-key export must not accidentally contain a private key.
        secret=subprocess.run(gpg_command(home)+['--with-colons','--list-secret-keys'],capture_output=True,timeout=30)
        if b'sec:' in secret.stdout: raise ArtifactError('Only an exported public encryption key belongs on the VM')
        return home
    except BaseException:
        dispose_keyring(home); raise


def seal(backup, destination, *, public_key, recipient):
    from .backup import verify
    backup=existing_directory(backup); manifest=verify(backup)
    destination=Path(destination).absolute(); parent=existing_directory(destination.parent)
    descriptor=Path(str(destination)+'.json')
    if destination.exists() or destination.is_symlink() or descriptor.exists() or descriptor.is_symlink():
        raise ArtifactError('Choose a new encrypted backup name')
    home=public_home(parent,public_key,recipient)
    temporary=parent/('.encrypting-'+os.urandom(16).hex())
    before=scan(backup)
    process=None
    try:
        with temporary.open('xb') as encrypted,tempfile.TemporaryFile() as errors:
            os.chmod(temporary,0o400)
            process=subprocess.Popen(gpg_command(home)+['--trust-model','always','--recipient',fingerprint(recipient),
                '--compress-algo','zlib','--encrypt'],stdin=subprocess.PIPE,stdout=encrypted,stderr=errors)
            try:
                with tarfile.open(fileobj=process.stdin,mode='w|',format=tarfile.PAX_FORMAT) as archive:
                    for key in sorted(before):
                        path=backup/key
                        entry=tarfile.TarInfo('backup/'+key)
                        entry.uid=entry.gid=0; entry.uname=entry.gname=''; entry.mtime=0
                        if path.is_dir():
                            entry.type=tarfile.DIRTYPE; entry.mode=0o700; archive.addfile(entry)
                        else:
                            entry.mode=before[key][2]&0o700; entry.size=before[key][3]
                            with open_file(backup,key) as incoming: archive.addfile(entry,incoming)
                        if not has_space(parent):
                            raise ArtifactError('Insufficient free space for encrypted backup preparation')
                process.stdin.close()
                if process.wait(timeout=3600): raise ArtifactError('Backup encryption failed')
                encrypted.flush(); os.fsync(encrypted.fileno())
            except BaseException:
                process.kill(); process.wait(); raise
        if scan(backup)!=before: raise ArtifactError('The verified backup changed during encryption')
        value=dict(format=FORMAT,backup_id=manifest['id'],recipient=fingerprint(recipient),**file_hash(parent,temporary.name))
        # Journal first; a crash between journal and rename never publishes a
        # ciphertext with an unknown checksum/recipient.
        atomic_json(descriptor,value)
        publish(temporary,destination)
        return value
    except BaseException:
        if not destination.exists(): descriptor.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        dispose_keyring(home)


def sealed(path, *, recipient=None):
    path=Path(path)
    value=read_metadata(Path(str(path)+'.json'))
    if (not isinstance(value,dict) or set(value)!={'format','backup_id','recipient','bytes','sha256','mode'} or
            value['format']!=FORMAT or not re.fullmatch('[a-f0-9]{32}',str(value['backup_id'])) or
            value['recipient']!=fingerprint(value['recipient']) or
            recipient and value['recipient']!=fingerprint(recipient) or
            file_hash(path.parent,path.name)!={k:value[k] for k in ('bytes','sha256','mode')}):
        raise ArtifactError('Encrypted backup differs from its verified transfer receipt')
    return value


def unseal(source, destination, *, keyring, expected_sha256):
    """Recover off-machine; authenticate encrypted bytes using a trusted receipt."""
    from .backup import verify
    source=Path(source).absolute(); destination=Path(destination).absolute()
    parent=existing_directory(destination.parent); keyring=existing_directory(keyring)
    if not re.fullmatch('[a-f0-9]{64}',str(expected_sha256)) or file_hash(source.parent,source.name)['sha256']!=expected_sha256:
        raise ArtifactError('Encrypted backup checksum differs from the trusted receipt')
    if destination.exists() or destination.is_symlink(): raise ArtifactError('Choose a new decrypted backup directory')
    staging=Path(tempfile.mkdtemp(prefix='.decrypting-backup-',dir=parent))
    process=None
    try:
        with open_file(source.parent,source.name) as encrypted,tempfile.TemporaryFile() as errors:
            process=subprocess.Popen(gpg_command(keyring)+['--decrypt'],stdin=encrypted,stdout=subprocess.PIPE,stderr=errors)
            count=0; seen=set()
            try:
                with tarfile.open(fileobj=process.stdout,mode='r|') as archive:
                    for member in archive:
                        count+=1; key=name(member.name)
                        if (count>MAX_ENTRIES or key.parts[0]!='backup' or len(key.parts)<2 or member.name in seen or
                                not (member.isfile() or member.isdir()) or member.size<0):
                            raise ArtifactError('Invalid encrypted backup archive entry')
                        seen.add(member.name); path=staging/Path(*key.parts)
                        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                        if member.isdir(): path.mkdir(mode=0o700,exist_ok=True); continue
                        if member.mode&~0o700 or not has_space(parent,member.size):
                            raise ArtifactError('Unsafe archive permissions or insufficient restore space')
                        with archive.extractfile(member) as incoming,path.open('xb') as output:
                            remaining=member.size
                            while remaining:
                                block=incoming.read(min(1024**2,remaining))
                                if not block: raise ArtifactError('Truncated encrypted backup')
                                output.write(block); remaining-=len(block)
                            output.flush(); os.fsync(output.fileno()); os.fchmod(output.fileno(),member.mode)
                # Reading all bytes forces GnuPG to check authenticated encryption
                # before any plaintext is verified or published for restoration.
                while process.stdout.read(1024**2): pass
                if process.wait(timeout=3600): raise ArtifactError('Backup decryption or integrity validation failed')
            except BaseException:
                process.kill(); process.wait(); raise
        manifest=verify(staging/'backup')
        sync_tree(staging/'backup'); publish(staging/'backup',destination)
        return dict(backup_id=manifest['id'],passed=True)
    except (tarfile.TarError,OSError):
        raise ArtifactError('Backup decryption or archive validation failed') from None
    finally:
        shutil.rmtree(staging)


def sftp_quote(value):
    if any(ord(c)<32 for c in str(value)): raise ArtifactError('Invalid SFTP path')
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'


class SFTP:
    def __init__(self, *, host, user, port, directory, identity, known_hosts):
        if (not isinstance(host,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9.-]{0,252}',host) or
                not re.fullmatch('[A-Za-z_][A-Za-z0-9_-]{0,63}',user) or type(port) is not int or not 1<=port<=65535 or
                not re.fullmatch('/[A-Za-z0-9_./-]+',directory) or '..' in Path(directory).parts):
            raise ArtifactError('Invalid pinned SFTP backup destination')
        self.host,self.user,self.port,self.directory=host,user,port,directory.rstrip('/')
        self.identity=Path(identity); self.known_hosts=Path(known_hosts)
        for path in (self.identity,self.known_hosts):
            with operator_file(path) as stream:
                info=os.fstat(stream.fileno())
                if info.st_mode&0o077 or info.st_size>4*1024**2: raise ArtifactError('SFTP credentials and host pins must be private and bounded')

    def batch(self, commands):
        executable=shutil.which('sftp')
        if not executable: raise ArtifactError('Install OpenSSH SFTP for off-machine backup copying')
        result=subprocess.run([executable,'-F','/dev/null','-b','-','-P',str(self.port),'-i',str(self.identity),
            '-oBatchMode=yes','-oStrictHostKeyChecking=yes','-oUserKnownHostsFile='+str(self.known_hosts),
            '-oGlobalKnownHostsFile=/dev/null','-oIdentitiesOnly=yes','-oIdentityAgent=none','-oForwardAgent=no',
            '-oClearAllForwardings=yes','-oPermitLocalCommand=no','-oProxyCommand=none',
            '-oConnectTimeout=10','-oServerAliveInterval=15','-oServerAliveCountMax=3',self.user+'@'+self.host],
            input=('\n'.join(commands)+'\n').encode(),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=7200)
        return result.returncode==0

    def copy(self, source):
        source=Path(source).absolute(); receipt=sealed(source)
        remote=self.directory+'/recovery-'+receipt['backup_id']+'.tar.gpg'
        cipher=self._copy_file(source,remote,receipt)
        descriptor=Path(str(source)+'.json')
        self._copy_file(descriptor,remote+'.json',file_hash(descriptor.parent,descriptor.name))
        return dict(passed=True,already_copied=cipher,sha256=receipt['sha256'])

    def _copy_file(self, source, remote, receipt):
        partial=remote+'.partial'
        temporary=Path(tempfile.mkdtemp(prefix='.transfer-check-',dir=source.parent))
        try:
            checked=temporary/'ciphertext'
            if not has_space(temporary,receipt['bytes']):
                raise ArtifactError('Insufficient space for encrypted transfer verification')
            if self.batch(['get '+sftp_quote(remote)+' '+sftp_quote(checked)]):
                if file_hash(temporary,'ciphertext')['sha256']!=receipt['sha256']:
                    raise ArtifactError('A different remote backup already has this identity')
                return True
            checked.unlink(missing_ok=True)
            if not has_space(temporary,receipt['bytes']):
                raise ArtifactError('Insufficient space for encrypted transfer verification')
            if not self.batch(['put '+sftp_quote(source)+' '+sftp_quote(partial),'chmod 600 '+sftp_quote(partial),
                               'get '+sftp_quote(partial)+' '+sftp_quote(checked)]):
                raise ArtifactError('Encrypted backup transfer could not finish')
            if file_hash(temporary,'ciphertext')['sha256']!=receipt['sha256']:
                raise ArtifactError('Remote encrypted backup checksum differs')
            # OpenSSH's hard-link operation is atomic and refuses an existing
            # destination. No rename extension may overwrite a completed backup.
            if not self.batch(['ln '+sftp_quote(partial)+' '+sftp_quote(remote),'rm '+sftp_quote(partial)]):
                checked.unlink(missing_ok=True)
                if not self.batch(['get '+sftp_quote(remote)+' '+sftp_quote(checked)]) or file_hash(temporary,'ciphertext')['sha256']!=receipt['sha256']:
                    raise ArtifactError('Remote encrypted backup publication could not finish')
            return False
        finally:
            shutil.rmtree(temporary)
