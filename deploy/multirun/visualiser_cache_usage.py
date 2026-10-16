"""(C) Copyright 2026, by Ross Richardson

Bounded private cache history and numeric counts of genuine raw-file reprocessing.
Callers serialise updates with the summary cache's existing process lock.
@author ross richardson
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


FORMAT = 'simpaths.visualiser.summary-cache-usage.v1'
STATE = '.cache-usage.json'
MAX_BYTES = 512 * 1024
MAX_HISTORY = 1024
HISTORY_SECONDS = 30 * 86400
MAX_COUNTER = 2**63 - 1
KEY = re.compile(r'[a-f0-9]{64}')
REASONS = ('evicted', 'invalidated', 'rejected', 'unknown')
COUNTERS = frozenset((
    'hits', 'misses', 'evictions', 'evicted_bytes', 'invalidations', 'rejections',
    'history_expired', 'history_overflow',
    *('miss_' + reason for reason in REASONS),
    *(prefix + suffix for prefix in ('raw_', 'eviction_raw_')
      for suffix in ('started', 'completed', 'failed', 'elapsed_ns', 'input_bytes'))))


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def private_read(root):
    """An atomic, bounded, read-only snapshot; never creates cache directories."""
    root = Path(root).absolute()
    if root.resolve(strict=True) != root:
        raise ValueError('Unsafe summary usage location')
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError('Unsafe summary usage directory')
    fd = os.open(root / STATE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > MAX_BYTES):
            raise ValueError('Unsafe summary usage file')
        body = source.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError('Summary usage exceeds its bound')
    value = json.loads(body)
    if (not isinstance(value, dict) or set(value) != {'format', 'started_ns', 'updated_ns', 'counters', 'history'}
            or value['format'] != FORMAT or not isinstance(value['counters'], dict)
            or set(value['counters']) != COUNTERS
            or any(type(n) is not int or not 0 <= n <= MAX_COUNTER for n in value['counters'].values())
            or any(type(value[k]) is not int or not 0 < value[k] <= MAX_COUNTER
                   for k in ('started_ns', 'updated_ns'))
            or not isinstance(value['history'], dict) or len(value['history']) > MAX_HISTORY):
        raise ValueError('Invalid summary usage state')
    for key, item in value['history'].items():
        if (not KEY.fullmatch(key) or not isinstance(item, dict)
                or set(item) != {'reason', 'at_ns', 'owner', 'configuration'}
                or item['reason'] not in REASONS[:-1]
                or type(item['at_ns']) is not int or not 0 < item['at_ns'] <= MAX_COUNTER
                or any(item[k] is not None and (not isinstance(item[k], str) or not KEY.fullmatch(item[k]))
                       for k in ('owner', 'configuration'))):
            raise ValueError('Invalid summary usage history')
    return value


def statistics(root):
    """Export fixed numeric fields only, never private keys, owners or paths.

    Missing instrumentation is unknown, not a zero count. Historical eviction
    attribution is a lower bound once its age/entry cap has discarded markers.
    """
    try:
        value = private_read(root)
    except FileNotFoundError:
        return dict(available=False, recorded=False)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return dict(available=False, recorded=True)
    counters = value['counters']
    starts = counters['raw_started']
    return dict(available=True, recorded=True, format=FORMAT,
        started_ns=value['started_ns'], updated_ns=value['updated_ns'],
        counters=counters, history_entries=len(value['history']),
        history_entry_limit=MAX_HISTORY, history_seconds=HISTORY_SECONDS,
        metadata_byte_limit=MAX_BYTES,
        eviction_fraction_of_raw_starts=(counters['eviction_raw_started']/starts if starts else None),
        unfinished_raw_starts=max(0, starts-counters['raw_completed']-counters['raw_failed']))


class CacheUsage:
    """Best-effort measurements: a tracking failure never rejects scientific work."""
    def __init__(self, root):
        self.root = Path(root)

    def _load(self):
        try:
            return private_read(self.root)
        except FileNotFoundError:
            now = time.time_ns()
            return dict(format=FORMAT, started_ns=now, updated_ns=now,
                        counters={name: 0 for name in COUNTERS}, history={})

    def update(self, increments=None, *, markers=(), forget=(), invalidate=None, lookup=None):
        """Update under the cache lock. Reasons are evidence, never authority."""
        try:
            value = self._load()
            now = time.time_ns()
            history, counters = value['history'], value['counters']
            def add(name, amount):
                if name not in COUNTERS or type(amount) is not int or amount < 0:
                    raise ValueError('Invalid summary usage increment')
                counters[name] = min(MAX_COUNTER, counters[name] + amount)
            for key, item in list(history.items()):
                if item['at_ns'] < now - HISTORY_SECONDS * 10**9:
                    del history[key]
                    add('history_expired', 1)
            if invalidate is not None:
                owner, configuration = invalidate
                owner = hashlib.sha256(encoded(owner)).hexdigest()
                configuration = hashlib.sha256(encoded(configuration)).hexdigest() if configuration is not None else None
                for item in history.values():
                    if item['owner'] == owner and (configuration is None or item['configuration'] == configuration):
                        # Explicit retirement after eviction is not eviction-led
                        # reprocessing. Retain the evidence with its new reason.
                        item.update(reason='invalidated', at_ns=now)
            for key, identity, reason in markers:
                if not KEY.fullmatch(key) or reason not in REASONS[:-1]:
                    raise ValueError('Invalid summary usage marker')
                history[key] = dict(reason=reason, at_ns=now, **{
                    k: hashlib.sha256(encoded(identity[k])).hexdigest() if identity is not None else None
                    for k in ('owner', 'configuration')})
            for name, amount in (increments or {}).items():
                add(name, amount)
            reason = None
            if lookup is not None:
                key, hit = lookup
                if hit:
                    add('hits', 1)
                else:
                    reason = history.get(key, {}).get('reason', 'unknown')
                    add('misses', 1)
                    add('miss_' + reason, 1)
            for key in forget:
                history.pop(key, None)
            overflow = len(history) - MAX_HISTORY
            if overflow > 0:
                for key in sorted(history, key=lambda k: (history[k]['at_ns'], k))[:overflow]:
                    del history[key]
                add('history_overflow', overflow)
            value['updated_ns'] = now
            body = encoded(value)
            if len(body) > MAX_BYTES:
                raise ValueError('Summary usage exceeds its bound')
            temporary = None
            try:
                # A fixed name is safe under the global cache lock and prevents
                # repeated hard kills accumulating unbounded temporary files.
                temporary = self.root / (STATE + '.pending')
                temporary.unlink(missing_ok=True)
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as target:
                    target.write(body)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.root / STATE)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return reason
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return None  # Corrupt tracking stays inspectable; do not invent zeros.
