"""(C) Copyright 2026, by Ross Richardson

Atomic refresh checkpoints, reserved report space and compact Quick Start images.
Only private workflow files and generated preparation copies are managed here.
@author ross richardson
"""
import errno
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

GIB=1024**3
RESERVE='.checkpoint-reserve'


def reserve_reports(root):
    path=Path(root)/RESERVE
    if path.exists():
        if path.is_symlink() or path.stat().st_uid!=os.getuid() or not path.is_file():
            raise ValueError('Invalid checkpoint reserve')
        return path
    descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        os.posix_fallocate(descriptor,0,4*1024**2)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def _commit_json(path,payload):
    if path.is_symlink():
        raise ValueError('Checkpoint cannot be a symbolic link')
    descriptor,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(payload);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:
        Path(name).unlink(missing_ok=True)


def checkpoint(path,value,*,reserve=None):
    """A failed write preserves the previous JSON; reserved blocks allow a retry."""
    path=Path(path)
    payload=(json.dumps(value,indent=2,allow_nan=False)+'\n').encode()
    try:
        _commit_json(path,payload)
    except OSError as error:
        if error.errno!=errno.ENOSPC or reserve is None or not Path(reserve).is_file():
            raise
        Path(reserve).unlink()
        _commit_json(path,payload)


def retire_copies(directory,*,package=False):
    """Retire owned bulk copies only after their verified package/context exists."""
    directory=Path(directory)
    for name in ('work','loading-check'):
        source=directory/name
        if not source.exists():continue
        if source.is_symlink() or source.resolve().parent!=directory.resolve():
            raise ValueError('Preparation cleanup is not a private child directory')
        target=directory/(name+'-evidence');target.mkdir(exist_ok=True,mode=0o700)
        for path in source.glob('*.properties'):
            if path.is_symlink():raise ValueError('Linked preparation properties')
            shutil.copyfile(path,target/path.name)
        shutil.rmtree(source)
    if package:
        source=directory/'package'
        if not source.exists():return
        if source.is_symlink():raise ValueError('Linked preparation package')
        for name in ('profile.json','checksums.json'):
            # Renaming retains exact metadata bytes without another data copy.
            if (source/name).is_symlink():raise ValueError('Linked package metadata')
            if (source/name).exists():os.replace(source/name,directory/name)
            elif not (directory/name).is_file() or (directory/name).is_symlink():
                raise ValueError('Package metadata is missing during cleanup')
        shutil.rmtree(source)


def compact_dockerfile(template,source_image=None):
    """Publish one final input layer; discard build-only copies/account mutations."""
    before,separator,remainder=template.partition('WORKDIR /app\n')
    if not separator or before.count('FROM ')!=1 or 'RUN mkdir -p output' not in remainder:
        raise ValueError('Unsupported Quick Start Dockerfile structure')
    match=re.search(r'^FROM ([^\s]+)$',before,re.M)
    if not match:raise ValueError('Expected one pinned runtime base')
    before=before[:match.end()]+' AS refresh_dependencies'+before[match.end():]
    if source_image is None:
        preparation='FROM refresh_dependencies AS refresh_prepared\nWORKDIR /app\n'+remainder
    else:
        if not re.fullmatch(r'sha256:[a-f0-9]{64}|[a-z0-9][a-z0-9_./:-]+',source_image):
            raise ValueError('Invalid verified source image reference')
        preparation='FROM '+source_image+' AS refresh_prepared\n'
    tail=remainder[remainder.index('RUN mkdir -p output'):]
    return (before+'\n'+preparation+'\nFROM refresh_dependencies\nWORKDIR /app\n'
        'COPY --from=refresh_prepared --chown=0:0 /app/app.jar /app/webserver.properties '
        '/app/README.md /app/license.txt /app/COPYRIGHT.md /app/profile.json '
        '/app/release.json /app/query-access.json /app/\n'
        'COPY --from=refresh_prepared --chown=10001:10001 /app/input/ /app/input/\n'+tail)


def required_build_space(context):
    """Budget transient stages/export as well as the final image and a reserve."""
    size=sum(path.stat().st_size for path in Path(context).rglob('*')
             if path.is_file() and not path.is_symlink())
    return max(8*GIB,3*size+3*GIB)
