"""(C) Copyright 2026, by Ross Richardson

Cross-process maintenance fence for native MultiRun launchers and release writes.
Authentication secrets and cookies are neither created nor changed by this fence.
@author ross richardson
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

from .artifacts import ArtifactError
from .releases import existing_directory

LOCK = '.maintenance.lock'
GATE = 'restore-pending.json'


@contextmanager
def state_guard(state, *, exclusive=False):
    state = existing_directory(state)
    fd = os.open(state/LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ArtifactError('Invalid maintenance lock')
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArtifactError('Stop the launcher before maintenance; this state is already in use') from None
        yield
    finally:
        os.close(fd)


@contextmanager
def service_state(state):
    with state_guard(state):
        if (Path(state)/GATE).exists() or (Path(state)/GATE).is_symlink():
            raise ArtifactError('Restored state is inactive; verify and activate it with the backup command first')
        yield
