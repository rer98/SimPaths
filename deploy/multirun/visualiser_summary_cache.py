"""(C) Copyright 2026, by Ross Richardson

Private bounded per-seed summaries, with immutable publication and process locks.
Callers verify source authority; raw descriptors are checked before reuse unless
recorded routine expiry explicitly permits retained-summary-only processing.
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
import time

if __package__:
    from .visualiser_cache_usage import CacheUsage, statistics
    from .visualiser_summary_holds import SummaryHolds, SummaryCapacityError
else:
    from visualiser_cache_usage import CacheUsage, statistics
    from visualiser_summary_holds import SummaryHolds, SummaryCapacityError


MIB = 1024 ** 2
FORMAT = 'simpaths.visualiser.private-summary.v1'
KEY = re.compile(r'[a-f0-9]{64}')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class SummaryCache:
    """Disk budgets cover data and receipts; locks occupy a fixed, bounded set.

    Calculation runs under one of 64 striped locks, so concurrent requests for
    the same seed compute once. A separate short lock covers reads, eviction and
    publication. Numeric usage/history has its own fixed 512-KiB metadata bound.
    No database, raw data copy, or authority token is stored here.
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
        self.usage = CacheUsage(self.root)
        self.holds = SummaryHolds(self)

    def statistics(self):
        """Read cumulative numeric usage; the operator needs no raw-file access."""
        return statistics(self.root)

    def preflight_entry(self, identity):
        """Apply dependency backpressure before expensive raw-source hash reads."""
        with self._lock('.cache.lock'):
            records = self.holds.load()
            if not records:
                return
            self.holds.require_admitted(identity, records)
            key = hashlib.sha256(canonical(identity)).hexdigest()
            if not any(entry[1] == key for entry in self._entries()):
                self.holds.before_raw(identity, records)

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
        rejected = []
        for path in self.root.iterdir():
            if path.name.startswith(('.pending-', '.holds-')):
                # Publication holds the global lock while creating pending files,
                # so any pending directory found under this lock is abandoned.
                self._remove(path)
            elif KEY.fullmatch(path.name):
                try:
                    receipt, size, used = self._receipt(path)
                    entries.append((used, path.name, size, receipt))
                except (OSError, ValueError, KeyError, TypeError):
                    self._remove(path)
                    rejected.append((path.name, None, 'rejected'))
        if rejected:
            self.usage.update(dict(rejections=len(rejected)), markers=rejected)
        return sorted(entries)

    def _evict(self, entries, needed_bytes=0, needed_entries=0, *, holds=None, incoming=None):
        holds = self.holds.load() if holds is None else holds
        retained = list(entries)
        def occupancy():
            total, count, protected = self.holds.occupancy(holds, retained,
                incoming=incoming, incoming_bytes=needed_bytes)
            if incoming is None:
                total += needed_bytes
                count += needed_entries
            return total, count, protected
        evicted = []
        removed_bytes = 0
        while True:
            total, count, protected = occupancy()
            if total <= self.maximum_bytes and count <= self.maximum_entries:
                break
            candidate = next((entry for entry in retained if entry[1] not in protected), None)
            if candidate is None:
                raise SummaryCapacityError('Summary storage is reserved for pending comparisons')
            retained.remove(candidate)
            evicted.append(candidate)
        for _, key, size, receipt in evicted:
            self._remove(self.root / key)
            removed_bytes += size
        if evicted:
            self.usage.update(dict(evictions=len(evicted), evicted_bytes=removed_bytes),
                markers=[(key, receipt['identity'], 'evicted') for _, key, _, receipt in evicted])
        return dict(entries=len(retained), bytes=sum(entry[2] for entry in retained))

    def prune(self, *, owner=None, configuration=None, job=None, attempt=None, unless_held=False):
        """Enforce budgets, or explicitly invalidate an owner's/config's entries."""
        if configuration is not None and owner is None:
            raise ValueError('Configuration invalidation requires an owner')
        scoped = job is not None or attempt is not None
        if scoped and (not owner or not configuration or not isinstance(job,str) or not job
                       or not isinstance(attempt,str) or not attempt):
            raise ValueError('Source invalidation requires owner, configuration, job and attempt')
        with self._lock('.cache.lock'):
            entries = self._entries()
            holds = self.holds.load()
            if owner is not None:
                if not unless_held:
                    holds = self.holds.invalidate(holds, owner, configuration, job=job, attempt=attempt)
                    self.holds.save(holds)
                protected = self.holds.occupancy(holds, entries)[2] if unless_held else set()
                retained = []
                invalidated = []
                for entry in entries:
                    identity = entry[3]['identity']
                    execution = identity.get('execution', {})
                    if (identity['owner'] == owner and (configuration is None or identity['configuration'] == configuration)
                            and (not scoped or execution.get('job') == job and execution.get('attempt') == attempt)
                            and entry[1] not in protected):
                        self._remove(self.root / entry[1])
                        invalidated.append((entry[1], identity, 'invalidated'))
                    else:
                        retained.append(entry)
                entries = retained
                self.usage.update(dict(invalidations=len(invalidated)), markers=invalidated,
                                  invalidate=(owner, configuration) if not unless_held and not scoped else None)
            return self._evict(entries, holds=holds)


class SummaryEntry:
    def __init__(self, cache, key, identity):
        self.cache, self.key, self.identity = cache, key, identity
        self.miss_reason = 'unknown'

    @contextmanager
    def raw_processing(self, input_bytes):
        """Count only actual CSV parsing, never summary replay or aggregation.

        The input size is logical CSV bytes offered to the parser, not measured
        physical disk I/O. A killed parent can leave an unmatched start; do not
        disguise it as a completed parse or fabricate its duration.
        """
        if type(input_bytes) is not int or not 0 <= input_bytes < 2**63:
            raise ValueError('Invalid raw summary input size')
        prefixes = ['raw_'] + (['eviction_raw_'] if self.miss_reason == 'evicted' else [])
        with self.cache._lock('.cache.lock'):
            self.cache.holds.before_raw(self.identity)
            self.cache.usage.update({prefix + suffix: amount for prefix in prefixes
                                     for suffix, amount in (('started', 1), ('input_bytes', input_bytes))})
        started = time.monotonic_ns()
        completed = False
        try:
            yield
            completed = True
        finally:
            elapsed = max(0, time.monotonic_ns() - started)
            with self.cache._lock('.cache.lock'):
                self.cache.usage.update({prefix + suffix: amount for prefix in prefixes
                    for suffix, amount in (('completed' if completed else 'failed', 1), ('elapsed_ns', elapsed))})

    def restore(self, output):
        """Verify and copy one bounded summary into the current private workspace."""
        cache = self.cache
        with cache._lock('.cache.lock'):
            cache._evict(cache._entries())
            directory = cache.root / self.key
            if not directory.exists():
                self.miss_reason = cache.usage.update(lookup=(self.key, False)) or 'unknown'
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
                self.miss_reason = None
                cache.usage.update(lookup=(self.key, True), forget=(self.key,))
                return True
            except (OSError, ValueError, KeyError, TypeError):
                cache._remove(directory)
                cache.usage.update(dict(rejections=1), markers=[(self.key, self.identity, 'rejected')])
                self.miss_reason = cache.usage.update(lookup=(self.key, False)) or 'unknown'
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
                cache._evict(entries, size + len(receipt), 1, incoming=self.identity)
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
                    cache.usage.update(forget=(self.key,))
                    return True
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
