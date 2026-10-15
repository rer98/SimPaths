"""(C) Copyright 2026, by Ross Richardson

Private bounded per-seed summaries, with immutable publication and process locks.
Callers retain source/output locks and verify raw descriptors before every lookup.
@author ross richardson
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile


MIB = 1024 ** 2
FORMAT = 'simpaths.visualiser.private-summary.v1'
KEY = re.compile(r'[a-f0-9]{64}')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class SummaryCache:
    """Disk budgets cover data and receipts; locks occupy a fixed, bounded set.

    Calculation runs under one of 64 striped locks, so concurrent requests for
    the same seed compute once. A separate short lock covers reads, eviction and
    publication. No database, raw data copy, or authority token is stored here.
    """
    def __init__(self, root, *, maximum_bytes=2 * 1024 ** 3, maximum_entries=256,
                 entry_bytes=64 * MIB):
        if (type(maximum_bytes) is not int or not MIB <= maximum_bytes <= 64 * 1024 ** 3
                or type(maximum_entries) is not int or not 1 <= maximum_entries <= 4096
                or type(entry_bytes) is not int or not 1 <= entry_bytes <= 64 * MIB):
            raise ValueError('Invalid private summary cache budget')
        self.root = Path(root).absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('Summary cache must be a private owned directory')
        # Reject symlink ancestors too; the configured service directory is trusted.
        if self.root.resolve(strict=True) != self.root:
            raise ValueError('Summary cache directory must not contain symlinks')
        self.maximum_bytes, self.maximum_entries = maximum_bytes, maximum_entries
        self.entry_bytes = entry_bytes

    @contextmanager
    def _lock(self, name):
        fd = os.open(self.root / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
                raise ValueError('Unsafe private summary lock')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    @contextmanager
    def entry(self, identity):
        encoded = canonical(identity)
        if len(encoded) > 64 * 1024 or not identity.get('owner') or not identity.get('configuration'):
            raise ValueError('Summary identity must include its owner and configuration')
        key = hashlib.sha256(encoded).hexdigest()
        with self._lock('.seed-' + str(int(key[:2], 16) % 64) + '.lock'):
            yield SummaryEntry(self, key, identity)

    @staticmethod
    def _open(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
                raise ValueError('Unsafe private summary file')
            return os.fdopen(fd, 'rb')
        except BaseException:
            os.close(fd)
            raise

    def _receipt(self, directory):
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('Unsafe private summary directory')
        with self._open(directory / 'receipt.json') as source:
            encoded = source.read(64 * 1024 + 1)
        if len(encoded) > 64 * 1024:
            raise ValueError('Summary receipt exceeds bound')
        receipt = json.loads(encoded)
        if (receipt.get('format') != FORMAT or receipt.get('key') != directory.name
                or hashlib.sha256(canonical(receipt['identity'])).hexdigest() != directory.name
                or type(receipt.get('bytes')) is not int or not 0 < receipt['bytes'] <= self.entry_bytes
                or not isinstance(receipt.get('sha256'), str) or not KEY.fullmatch(receipt['sha256'])):
            raise ValueError('Invalid private summary receipt')
        with self._open(directory / 'metrics.json') as source:
            if os.fstat(source.fileno()).st_size != receipt['bytes']:
                raise ValueError('Private summary size changed')
        return receipt, receipt['bytes'] + len(encoded), (directory / 'receipt.json').stat().st_mtime_ns

    @staticmethod
    def _remove(path):
        if path.is_symlink() or not path.is_dir():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path)

    def _entries(self):
        entries = []
        for path in self.root.iterdir():
            if path.name.startswith('.pending-'):
                # Publication holds the global lock while creating pending files,
                # so any pending directory found under this lock is abandoned.
                self._remove(path)
            elif KEY.fullmatch(path.name):
                try:
                    receipt, size, used = self._receipt(path)
                    entries.append((used, path.name, size, receipt))
                except (OSError, ValueError, KeyError, TypeError):
                    self._remove(path)
        return sorted(entries)

    def _evict(self, entries, needed_bytes=0, needed_entries=0):
        total = sum(entry[2] for entry in entries)
        while entries and (total + needed_bytes > self.maximum_bytes
                          or len(entries) + needed_entries > self.maximum_entries):
            _, key, size, _ = entries.pop(0)
            self._remove(self.root / key)
            total -= size
        return dict(entries=len(entries), bytes=total)

    def prune(self, *, owner=None, configuration=None):
        """Enforce budgets, or explicitly invalidate an owner's/config's entries."""
        if configuration is not None and owner is None:
            raise ValueError('Configuration invalidation requires an owner')
        with self._lock('.cache.lock'):
            entries = self._entries()
            if owner is not None:
                retained = []
                for entry in entries:
                    identity = entry[3]['identity']
                    if identity['owner'] == owner and (configuration is None or identity['configuration'] == configuration):
                        self._remove(self.root / entry[1])
                    else:
                        retained.append(entry)
                entries = retained
            return self._evict(entries)


class SummaryEntry:
    def __init__(self, cache, key, identity):
        self.cache, self.key, self.identity = cache, key, identity

    def restore(self, output):
        """Verify and copy one bounded summary into the current private workspace."""
        cache = self.cache
        with cache._lock('.cache.lock'):
            cache._evict(cache._entries())
            directory = cache.root / self.key
            if not directory.exists():
                return False
            temporary = None
            try:
                receipt, _, _ = cache._receipt(directory)
                if receipt['identity'] != self.identity:
                    raise ValueError('Private summary identity changed')
                digest = hashlib.sha256()
                fd, temporary = tempfile.mkstemp(prefix='.summary-', dir=Path(output).parent)
                with os.fdopen(fd, 'wb') as target, cache._open(directory / 'metrics.json') as source:
                    size = 0
                    while chunk := source.read(MIB):
                        size += len(chunk)
                        if size > receipt['bytes']:
                            raise ValueError('Private summary size changed')
                        digest.update(chunk)
                        target.write(chunk)
                if size != receipt['bytes'] or digest.hexdigest() != receipt['sha256']:
                    raise ValueError('Private summary hash changed')
                os.replace(temporary, output)
                temporary = None
                os.utime(directory / 'receipt.json', None, follow_symlinks=False)
                return True
            except (OSError, ValueError, KeyError, TypeError):
                cache._remove(directory)
                return False
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)

    def publish(self, source):
        cache = self.cache
        with cache._open(source) as data:
            size = os.fstat(data.fileno()).st_size
            if not 0 < size <= cache.entry_bytes:
                raise ValueError('Private summary exceeds entry budget')
            digest = hashlib.sha256()
            while chunk := data.read(MIB):
                digest.update(chunk)
            receipt = canonical(dict(format=FORMAT, key=self.key, identity=self.identity,
                                     bytes=size, sha256=digest.hexdigest()))
            if size + len(receipt) > cache.maximum_bytes:
                return False
            with cache._lock('.cache.lock'):
                entries = cache._entries()
                if (cache.root / self.key).exists():
                    return False
                cache._evict(entries, size + len(receipt), 1)
                temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=cache.root))
                try:
                    data.seek(0)
                    copied = 0
                    copied_hash = hashlib.sha256()
                    with open(temporary / 'metrics.json', 'xb') as target:
                        os.chmod(temporary / 'metrics.json', 0o600)
                        while chunk := data.read(MIB):
                            copied += len(chunk)
                            if copied > size:
                                raise ValueError('Summary changed during publication')
                            copied_hash.update(chunk)
                            target.write(chunk)
                        target.flush(); os.fsync(target.fileno())
                    if copied != size or copied_hash.hexdigest() != digest.hexdigest():
                        raise ValueError('Summary changed during publication')
                    with open(temporary / 'receipt.json', 'xb') as target:
                        os.chmod(temporary / 'receipt.json', 0o600)
                        target.write(receipt); target.flush(); os.fsync(target.fileno())
                    os.rename(temporary, cache.root / self.key)
                    return True
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
