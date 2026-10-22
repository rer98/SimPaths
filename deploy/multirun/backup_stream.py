"""(C) Copyright 2026, by Ross Richardson

Bounded, hash-checked archive capture without a second uncompressed file tree.
The caller retains source/deletion locks until the matching manifest is complete.
@author ross richardson
"""
import hashlib
import os
from pathlib import Path
import stat
import tarfile

from .artifacts import ArtifactError
from .backup_files import MAX_ENTRIES, identity, name, open_file, scan
from .releases import existing_directory


class HashReader:
    def __init__(self, stream, size):
        self.stream=stream; self.remaining=size; self.digest=hashlib.sha256()

    def read(self, size):
        if not 0<=size<=1024**2: raise ArtifactError('Backup reads must be bounded')
        wanted=min(size,self.remaining); chunks=[]; received=0
        while received<wanted:
            block=self.stream.read(wanted-received)
            if not block: raise ArtifactError('Truncated backup source')
            chunks.append(block); received+=len(block)
        data=b''.join(chunks)
        self.remaining-=len(data); self.digest.update(data)
        return data


class Archive:
    def __init__(self, archive):
        self.archive=archive; self.directories=set(); self.files={}

    def _limit(self):
        if len(self.directories)+len(self.files)>MAX_ENTRIES:
            raise ArtifactError('Backup archive inventory exceeds its entry limit')

    def mkdir(self, key):
        parts=name(key).parts
        for i in range(1,len(parts)+1):
            part='/'.join(parts[:i])
            if part in self.files: raise ArtifactError('Conflicting backup archive paths')
            if part in self.directories: continue
            self.directories.add(part); self._limit()
            entry=tarfile.TarInfo('backup/'+part); entry.type=tarfile.DIRTYPE; entry.mode=0o700
            self.archive.addfile(entry)

    def add_stream(self, key, source, size, mode=0o400):
        parts=name(key).parts
        if key in self.files or key in self.directories:
            raise ArtifactError('Duplicate backup archive file')
        if type(size) is not int or size<0 or type(mode) is not int or mode&~0o700:
            raise ArtifactError('Invalid backup archive file')
        if len(parts)>1: self.mkdir('/'.join(parts[:-1]))
        reader=HashReader(source,size)
        entry=tarfile.TarInfo('backup/'+key); entry.mode=mode; entry.size=size
        self.archive.addfile(entry,reader)
        if reader.remaining: raise ArtifactError('Incomplete backup archive member')
        value=dict(bytes=size,sha256=reader.digest.hexdigest(),mode=mode)
        self.files[key]=value; self._limit()
        return value

    def add_file(self, source, key, *, expected=None, source_root=None):
        source=Path(source)
        root=source.parent if source_root is None else Path(source_root)
        with open_file(root,source.relative_to(root).as_posix()) as incoming:
            before=identity(os.fstat(incoming.fileno()))
            if expected is not None and before!=expected:
                raise ArtifactError('Source files changed during backup')
            value=self.add_stream(key,incoming,before[3],before[2]&0o700)
            if identity(os.fstat(incoming.fileno()))!=before:
                raise ArtifactError('Source files changed during backup')
        return value

    def copy_selected(self, source, prefix, keys):
        source=existing_directory(source); keys=sorted(set(keys)); before={}
        self.mkdir(prefix)
        for key in keys:
            with open_file(source,key) as incoming: before[key]=identity(os.fstat(incoming.fileno()))
        for key in keys:
            self.add_file(source/key,prefix+'/'+key,expected=before[key],source_root=source)
        for key in keys:
            with open_file(source,key) as incoming:
                if identity(os.fstat(incoming.fileno()))!=before[key]:
                    raise ArtifactError('Selected source files changed during backup')
        return self.tree(prefix)

    def copy_tree(self, source, prefix):
        before=scan(source); self.mkdir(prefix)
        for key,info in sorted(before.items()):
            if stat.S_ISDIR(info[2]): self.mkdir(prefix+'/'+key)
        self.copy_selected(source,prefix,[k for k,v in before.items() if stat.S_ISREG(v[2])])
        if scan(source)!=before: raise ArtifactError('Source files changed during backup')
        return self.tree(prefix)

    def tree(self, prefix):
        start=prefix+'/'
        return dict(directories=sorted(k[len(start):] for k in self.directories if k.startswith(start)),
                    files={k[len(start):]:v for k,v in sorted(self.files.items()) if k.startswith(start)})

    def check(self, manifest):
        from .backup import validate_manifest
        validate_manifest(manifest)
        if (self.tree('state')!=manifest['state_tree'] or
                self.files.get('database.dump')!=manifest['dump'] or
                self.files.get('COPYRIGHT.md')!=manifest['attribution']):
            raise ArtifactError('Streamed backup inventory differs from its manifest')
        for root in manifest['prepared']:
            if self.tree(root['directory'])!=root['tree']:
                raise ArtifactError('Streamed prepared inventory differs from its manifest')
        expected={'database.dump','COPYRIGHT.md','manifest.json'}
        expected.update('state/'+key for key in manifest['state_tree']['files'])
        expected.update(root['directory']+'/'+key for root in manifest['prepared'] for key in root['tree']['files'])
        if set(self.files)!=expected:
            raise ArtifactError('Unexpected streamed backup files')
