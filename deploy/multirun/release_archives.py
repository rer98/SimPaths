"""(C) Copyright 2026, by Ross Richardson

Verified, private recovery archives for retired immutable MultiRun bundles.
Archive members are bounded and hash checked; restoration never follows links.
@author ross richardson
"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

from .artifacts import ArtifactError, fingerprint
from .releases import IDENTIFIER, atomic_json, check_entry, existing_directory, private_directory, read_json, utc_now

FORMAT='simpaths.multirun.retired-release.v1'


def files(directory,*,allow_empty=False):
    directory=existing_directory(directory)
    result={}
    for path in directory.rglob('*'):
        info=path.lstat()
        if path.is_symlink() or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise ArtifactError('Release archive sources must be private and service-owned')
        if stat.S_ISDIR(info.st_mode):continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ArtifactError('Release archive sources must be ordinary files')
        result[path.relative_to(directory).as_posix()]=fingerprint(path)
    if (not result and not allow_empty) or len(result)>2048:raise ArtifactError('Release archive source count is invalid')
    return result


def paths(registry,key):
    if not isinstance(key,str) or not IDENTIFIER.fullmatch(key):raise ArtifactError('Invalid archived release ID')
    root=registry.root/'retired'
    return root,root/(key+'.zip'),root/(key+'.json')


def receipt(registry,key):
    """Read only bounded control metadata, including the safe original location."""
    root,archive,metadata=paths(registry,key)
    existing_directory(root)
    record=read_json(metadata)
    if (type(record) is not dict or set(record)!={'format','id','entry','files','archive','archived_at','image'}
            or record['format']!=FORMAT or record['id']!=key
            or not re.fullmatch('sha256:[a-f0-9]{64}',str(record['image'])) or type(record['files']) is not dict
            or not 1<=len(record['files'])<=2048):
        raise ArtifactError('Retired release archive changed or is incomplete')
    check_entry(key,record['entry'])
    from .prepared_dataset import check_manifest
    check_manifest(record['files'])
    check_manifest({'archive.zip':record['archive']})
    if record['files'].get('release.json')!=record['entry']['manifest']:
        raise ArtifactError('Archived release metadata differs from its identity')
    return record


def verify(registry,key):
    record=receipt(registry,key)
    archive=paths(registry,key)[1]
    if fingerprint(archive)!=record['archive']:
        raise ArtifactError('Retired release archive changed or is incomplete')
    if archive.stat().st_nlink!=1:raise ArtifactError('Release archives must not be hardlinked')
    with zipfile.ZipFile(archive) as source:
        members=source.infolist()
        if len(members)!=len(record['files']) or {m.filename for m in members}!=record['files'].keys():
            raise ArtifactError('Unexpected or duplicate release archive members')
        for member in members:
            name=member.filename;relative=PurePosixPath(name)
            expected=record['files'][name]
            if (relative.is_absolute() or '..' in relative.parts or str(relative)!=name or '\\' in name
                    or member.is_dir() or member.file_size!=expected['bytes']
                    or stat.S_IFMT(member.external_attr>>16) not in (0,stat.S_IFREG)):
                raise ArtifactError('Unsafe release archive member')
            digest=hashlib.sha256()
            with source.open(member) as stream:
                while chunk:=stream.read(1024**2):digest.update(chunk)
            if digest.hexdigest()!=expected['sha256']:raise ArtifactError('Release archive member changed')
    return record


def inventory(registry):
    """Validate complete recovery archives for operator inventory and backups."""
    root=registry.root/'retired'
    if not root.exists() and not root.is_symlink():return {}
    existing_directory(root)
    records=sorted(root.glob('*.json'))
    if len(records)>10000:raise ArtifactError('Retired release inventory exceeds its safe bound')
    return {path.stem:verify(registry,path.stem) for path in records}


def create(registry,key,entry):
    root,archive,metadata=paths(registry,key)
    private_directory(root)
    bundle=registry.state/'release' if entry['legacy'] else registry.root/key
    registry._entry(key,entry)
    manifest=files(bundle)
    if metadata.exists() or metadata.is_symlink():
        record=verify(registry,key)
        if record['entry']['manifest']!=entry['manifest'] or record['files']!=manifest:
            raise ArtifactError('Release differs from its existing recovery archive')
        return record
    if shutil.disk_usage(root).free<sum(item['bytes'] for item in manifest.values())+256*1024**2:
        raise ArtifactError('Need room for a verified recovery archive before release retirement')
    fd,temporary=tempfile.mkstemp(prefix='.archive-',dir=root);os.close(fd)
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as target:
            for name in sorted(manifest):target.write(bundle/name,name)
        with open(temporary,'rb') as stream:os.fsync(stream.fileno())
        if files(bundle)!=manifest:raise ArtifactError('Release changed during archival')
        os.replace(temporary,archive)
        atomic_json(metadata,dict(format=FORMAT,id=key,entry=entry,files=manifest,
            archive=fingerprint(archive),archived_at=utc_now().isoformat(),image=registry._entry(key,entry)['image']))
        return verify(registry,key)
    finally:
        Path(temporary).unlink(missing_ok=True)


def cleanup(registry,key):
    """Idempotently finish a committed retirement, including interrupted unlink."""
    catalogue=registry._catalogue()
    if catalogue is None or key in catalogue['entries']:return
    record=receipt(registry,key)
    bundle=registry.state/'release' if record['entry']['legacy'] else registry.root/key
    if not bundle.exists() and not bundle.is_symlink():return
    # Settled archives need no repeated decompression on each hourly pass.
    verify(registry,key)
    remaining=files(bundle,allow_empty=True)
    if any(record['files'].get(name)!=item for name,item in remaining.items()):
        raise ArtifactError('Retired bundle contains unexpected files; manual inspection required')
    shutil.rmtree(bundle)


def restore(registry,key):
    with registry.locked():
        record=verify(registry,key)
        catalogue=registry._catalogue()
        if catalogue is None:raise ArtifactError('Restore the active registry before adding an archived release')
        if key in catalogue['entries']:
            registry._entry(key,catalogue['entries'][key]);return key
        if len(catalogue['entries'])>=100:raise ArtifactError('Retained release registry is full')
        bundle=registry.state/'release' if record['entry']['legacy'] else registry.root/key
        if bundle.exists() or bundle.is_symlink():
            raise ArtifactError('Finish pending retirement cleanup before restoring this release')
        staging=Path(tempfile.mkdtemp(prefix='.restore-',dir=registry.root))
        try:
            with zipfile.ZipFile(paths(registry,key)[1]) as source:
                for name in record['files']:
                    path=staging/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                    with source.open(name) as reader,path.open('xb') as writer:
                        shutil.copyfileobj(reader,writer,1024**2);writer.flush();os.fsync(writer.fileno())
                    path.chmod(0o400)
            if files(staging)!=record['files']:raise ArtifactError('Restored release files changed')
            staging.rename(bundle)
            fd=os.open(bundle.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
            entry=dict(record['entry'],availability='recovery',status_changed_at=utc_now().isoformat(),unreferenced_since=None)
            registry._entry(key,entry)
            catalogue['entries'][key]=entry
            atomic_json(registry.catalogue,catalogue)
            return key
        finally:
            if staging.exists():shutil.rmtree(staging)
