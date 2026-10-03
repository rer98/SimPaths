"""(C) Copyright 2026, by Ross Richardson

Private, bounded, hash-checked directory snapshots for offline service backups.
Rejects links and special files; publication never replaces an existing directory.
@author ross richardson
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat

from .artifacts import ArtifactError
from .releases import existing_directory

MAX_ENTRIES = 100000
MAX_METADATA = 64*1024**2
RESERVE = 64*1024**2


def name(value):
    if (not isinstance(value,str) or not value or len(value)>4096 or
            value.startswith('/') or any(p in ('','.','..') for p in value.split('/')) or
            any(ord(c)<32 for c in value)):
        raise ArtifactError('Invalid backup-relative path')
    return PurePosixPath(value)


def identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def scan(root, *, exclude=()):
    root = existing_directory(root)
    result = {}
    for parent,dirs,files in os.walk(root,followlinks=False):
        for entry in sorted(dirs+files):
            path = Path(parent)/entry
            key = path.relative_to(root).as_posix()
            name(key)
            if any(key==excluded or key.startswith(excluded+'/') for excluded in exclude):
                if entry in dirs: dirs.remove(entry)
                continue
            info = path.lstat()
            if (info.st_uid!=os.getuid() or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or stat.S_ISREG(info.st_mode) and info.st_nlink!=1):
                raise ArtifactError('Backups require service-owned ordinary files and directories without links')
            result[key] = identity(info)
            if len(result)>MAX_ENTRIES:
                raise ArtifactError('Backup file inventory exceeds its entry limit')
    return result


def open_file(root, key):
    parts = name(key).parts
    fd = os.open(existing_directory(root),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd); fd=child
        target = os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        info = os.fstat(target)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid():
            os.close(target)
            raise ArtifactError('Invalid ordinary backup file')
        return os.fdopen(target,'rb')
    finally:
        os.close(fd)


def file_hash(root, key, *, destination=None, expected_identity=None):
    with open_file(root,key) as source:
        before = os.fstat(source.fileno())
        if expected_identity is not None and identity(before)!=expected_identity:
            raise ArtifactError('Source files changed during backup')
        digest = hashlib.sha256()
        output = None
        try:
            if destination is not None:
                fd = os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                output = os.fdopen(fd,'wb')
            while block := source.read(1024**2):
                digest.update(block)
                if output:
                    if shutil.disk_usage(destination.parent).free<len(block)+RESERVE:
                        raise ArtifactError('Insufficient free space for the backup or restore')
                    output.write(block)
            if identity(os.fstat(source.fileno()))!=identity(before):
                raise ArtifactError('Source files changed during backup')
            if output:
                output.flush(); os.fsync(output.fileno())
                os.fchmod(output.fileno(),before.st_mode&0o700)
        finally:
            if output: output.close()
        return dict(bytes=before.st_size,sha256=digest.hexdigest(),mode=before.st_mode&0o700)


def copy_tree(source, destination, *, exclude=()):
    before = scan(source,exclude=exclude)
    destination.mkdir(mode=0o700)
    directories = [key for key,info in before.items() if stat.S_ISDIR(info[2])]
    for key in sorted(directories,key=lambda k:(k.count('/'),k)):
        (destination/key).mkdir(mode=0o700)
    files = {}
    for key,info in sorted(before.items()):
        if stat.S_ISREG(info[2]):
            files[key]=file_hash(source,key,destination=destination/key,expected_identity=info)
    if scan(source,exclude=exclude)!=before:
        raise ArtifactError('Source files changed during backup')
    sync_tree(destination)
    return dict(directories=sorted(directories),files=files), before


def check_tree(root, expected, *, exclude=()):
    if (type(expected) is not dict or set(expected)!={'directories','files'} or
            type(expected['directories']) is not list or type(expected['files']) is not dict or
            len(expected['files'])+len(expected['directories'])>MAX_ENTRIES):
        raise ArtifactError('Invalid backup file inventory')
    if len(set(expected['directories']))!=len(expected['directories']):
        raise ArtifactError('Duplicate backup directory')
    for key in expected['directories']: name(key)
    for key,value in expected['files'].items():
        name(key)
        if (type(value) is not dict or set(value)!={'bytes','sha256','mode'} or
                type(value['bytes']) is not int or value['bytes']<0 or
                type(value['sha256']) is not str or len(value['sha256'])!=64 or
                any(c not in '0123456789abcdef' for c in value['sha256']) or
                type(value['mode']) is not int or value['mode']&~0o700):
            raise ArtifactError('Invalid backup file checksum')
    current = scan(root,exclude=exclude)
    dirs = sorted(key for key,info in current.items() if stat.S_ISDIR(info[2]))
    files = sorted(key for key,info in current.items() if stat.S_ISREG(info[2]))
    if dirs!=sorted(expected['directories']) or files!=sorted(expected['files']):
        raise ArtifactError('Backup or restored file inventory differs')
    for key in files:
        if file_hash(root,key)!=expected['files'][key]:
            raise ArtifactError('Backup or restored file checksums differ')


def read_metadata(path):
    path = Path(path)
    with open_file(path.parent,path.name) as stream:
        info = os.fstat(stream.fileno())
        if info.st_mode&0o077 or info.st_size>MAX_METADATA:
            raise ArtifactError('Backup metadata must be private and bounded')
        data = stream.read(MAX_METADATA+1)
    def pairs(items):
        value={}
        for key,item in items:
            if key in value: raise ArtifactError('Duplicate backup metadata key')
            value[key]=item
        return value
    return json.loads(data,object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ArtifactError('Nonfinite backup metadata')))


def sync_tree(root):
    for directory,_,_ in os.walk(root,topdown=False):
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)


def publish(source, destination):
    """Linux rename with RENAME_NOREPLACE, including races with another writer."""
    libc=ctypes.CDLL(None,use_errno=True)
    rename=libc.renameat2
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    if rename(-100,os.fsencode(source),-100,os.fsencode(destination),1):
        raise OSError(ctypes.get_errno(),'Backup destination could not be published')
    fd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)
