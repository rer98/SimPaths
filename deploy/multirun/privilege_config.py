"""(C) Copyright 2026, by Ross Richardson

Validate separate public frontend settings and installed native trust boundaries.
@author ross richardson
"""
import grp
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import sysconfig
from types import SimpleNamespace
import tomllib
from urllib.parse import urlsplit

from .vm_config import absolute, number


PUBLIC_DEFAULTS = {
    'service': {'origin': '', 'port': 5002},
    'paths': {'frontend': ''},
    'rpc': {'socket': '/run/simpaths-executor/control.sock', 'executor_user': 'simpaths-online'},
}


def load_public_config(path):
    """The public configuration cannot carry private state, DB or SMTP settings."""
    path=Path(path)
    if path.stat().st_size>65536:
        raise ValueError('Public configuration exceeds 64 KiB')
    with path.open('rb') as source:
        supplied=tomllib.load(source)
    if set(supplied)-set(PUBLIC_DEFAULTS):
        raise ValueError('Unknown public configuration section')
    sections={}
    for section,defaults in PUBLIC_DEFAULTS.items():
        values=supplied.get(section,{})
        if type(values) is not dict or set(values)-set(defaults):
            raise ValueError('Unknown public configuration setting')
        sections[section]={**defaults,**values}
        if any(type(sections[section][key]) is not type(value) for key,value in defaults.items()):
            raise ValueError('Invalid public configuration type')
    origin=sections['service']['origin']
    parsed=urlsplit(origin)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment
            or parsed.username or parsed.password or not re.fullmatch(r'[a-z0-9.-]+(?::[0-9]+)?',parsed.netloc)):
        raise ValueError('Use a canonical lowercase HTTPS origin')
    if parsed.port is not None:
        number(parsed.port,'HTTPS port',1,65535)
        if parsed.port==443:
            raise ValueError('Omit the default HTTPS port')
    number(sections['service']['port'],'service.port',1024,65535)
    socket=absolute(sections['rpc']['socket'],'rpc.socket')
    if len(os.fsencode(socket))>100:
        raise ValueError('RPC socket path is too long')
    user=sections['rpc']['executor_user']
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',user):
        raise ValueError('Use a dedicated executor account')
    return SimpleNamespace(origin=origin,port=sections['service']['port'],
        frontend=absolute(sections['paths']['frontend'],'paths.frontend'),rpc_socket=socket,
        executor_user=user)


def _trusted_path(path, *, allow_leaf_link=False):
    path=Path(path).absolute()
    for item in (path,*path.parents):
        info=item.lstat()
        linked=stat.S_ISLNK(info.st_mode)
        if (info.st_uid!=0 or not linked and info.st_mode & 0o022 or
                linked and not (allow_leaf_link and item==path)):
            raise ValueError('Install root-owned code and configuration without writable or linked ancestors')
    return path.lstat()


def _runtime_target(path):
    """Resolve only administrator-owned links through immutable directories."""
    visited=set()
    path=Path(path).absolute()
    for _ in range(32):
        info=_trusted_path(path,allow_leaf_link=True)
        if not stat.S_ISLNK(info.st_mode):
            return path
        if path in visited:
            raise ValueError('Cyclic installed runtime link')
        visited.add(path)
        target=path.readlink()
        path=Path(os.path.normpath(target if target.is_absolute() else path.parent/target))
    raise ValueError('Installed runtime link chain is too long')


def trusted_installation(path, *, directory=False, runtime_links=False):
    """Check every installed code file, not merely its top-level directory."""
    path=_runtime_target(path) if runtime_links else Path(path).absolute()
    info=_trusted_path(path)
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('Invalid installed code or configuration path')
    if not directory:
        return
    pending=[path]
    visited=set()
    count=0
    while pending:
        current=pending.pop()
        info=current.lstat()
        identity=(info.st_dev,info.st_ino)
        if identity in visited:
            continue
        visited.add(identity)
        with os.scandir(current) as entries:
            for entry in entries:
                count+=1
                if count>500000:
                    raise ValueError('Installed code tree exceeds the verification bound')
                item=Path(entry.path)
                if runtime_links and (item.name.endswith('.egg-link') or item.name.startswith('__editable__')):
                    raise ValueError('Install runtime dependencies without editable source references')
                info=entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    if not runtime_links:
                        raise ValueError('Installed application code must not contain symbolic links')
                    item=_runtime_target(item)
                    info=item.stat()
                if info.st_uid!=0 or info.st_mode & 0o022:
                    raise ValueError('Every installed code file must be root-owned and not runtime-writable')
                if stat.S_ISDIR(info.st_mode):
                    pending.append(item)
                elif not stat.S_ISREG(info.st_mode):
                    raise ValueError('Installed code contains a special filesystem entry')


def trusted_python():
    """Verify the interpreter, environment and standard library before backend imports."""
    trusted_installation(Path(sys.executable),runtime_links=True)
    if sys.prefix!=sys.base_prefix:
        trusted_installation(Path(sys.prefix),directory=True,runtime_links=True)
    else:
        for key in ('purelib','platlib'):
            trusted_installation(Path(sysconfig.get_path(key)),directory=True,runtime_links=True)
    trusted_installation(Path(sysconfig.get_path('stdlib')),directory=True,runtime_links=True)
    for entry in sys.path:
        path=Path(entry or os.getcwd()).absolute()
        if path.exists():
            trusted_installation(path,directory=path.is_dir(),runtime_links=True)
        else:
            # Python commonly includes an absent standard-library ZIP. Its
            # parent must still be protected against a runtime user adding it.
            _runtime_target(path.parent)


def separate_account(name):
    """Require a real, distinct non-root account on the other side of the socket."""
    account=pwd.getpwnam(name)
    if os.getuid()==0 or account.pw_uid in (0,os.getuid()):
        raise ValueError('Frontend and executor must use distinct non-root Linux accounts')
    return account


def public_identity_check(executor_user):
    account=separate_account(executor_user)
    try:
        docker_gid=grp.getgrnam('docker').gr_gid
    except KeyError:
        docker_gid=None
    if docker_gid is not None and docker_gid in {*os.getgroups(),os.getgid()}:
        raise ValueError('The public frontend must not belong to the Docker group')
    for socket in ('/var/run/docker.sock','/run/docker.sock','/run/jasmine-workspace-quotas/broker.sock'):
        if os.access(socket,os.W_OK):
            raise ValueError('The public frontend must not access Docker or the quota broker')
    if any(key.startswith(('PG','SMTP_','DOCKER_')) for key in os.environ):
        raise ValueError('Do not give database, Docker or SMTP settings to the public frontend')
    return account
