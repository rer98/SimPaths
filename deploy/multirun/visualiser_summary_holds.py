"""(C) Copyright 2026, by Ross Richardson

Bounded, persistent comparison dependencies for private per-seed summaries.
The caller holds the summary cache lock; these records never confer read access.
@author ross richardson
"""
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time


FORMAT = 'simpaths.visualiser.summary-holds.v1'
STATE = '.summary-holds.json'
MAX_BYTES = 512 * 1024
MAX_HOLDS = 256
MAX_SECONDS = 365 * 86400
RECEIPT_BYTES = 64 * 1024


class SummaryCapacityError(RuntimeError):
    """Private preparation must wait; existing retention promises stay intact."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def text(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 for c in value)


def timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def scope(identity):
    return digest({key: identity.get(key) for key in ('calculation', 'node')})


def unit(owner, configuration, job, seed, calculation):
    return digest([owner, configuration, job, str(seed), calculation])


def statistics(root):
    """Atomic read-only numeric snapshot; no owners, keys or file contents."""
    from types import SimpleNamespace
    from .visualiser_summary_cache import SummaryCache
    root = Path(root).absolute()
    try:
        info = root.lstat()
        if (root.resolve(strict=True) != root or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('Unsafe summary dependency location')
        (root/STATE).lstat()
    except FileNotFoundError:
        return dict(available=False, recorded=False)
    except (OSError, ValueError):
        return dict(available=False, recorded=True)
    try:
        reader = SummaryHolds(SimpleNamespace(root=root, _open=SummaryCache._open))
        records = reader.load()
        admitted = reader.units(records)
        wanted = reader.units(records, admitted=False)
        pairs=[r for r in records.values() if len(r['configurations'])==2]
        retained=[r for r in records.values() if len(r['configurations'])==1]
        return dict(available=True, recorded=True, pending_comparisons=len(pairs),
                    admitted_comparisons=sum(r['admitted'] for r in pairs),
                    deferred_comparisons=sum(not r['admitted'] for r in pairs),
                    retained_configurations=len(retained),
                    admitted_configurations=sum(r['admitted'] for r in retained),
                    deferred_configurations=sum(not r['admitted'] for r in retained),
                    retained_seed_slots=len(admitted), deferred_seed_slots=len(set(wanted)-set(admitted)),
                    earliest_expiry=min((r['expires_at'] for r in records.values()),default=None),
                    maximum_hold_seconds=MAX_SECONDS, metadata_byte_limit=MAX_BYTES)
    except (OSError, ValueError, TypeError, SummaryCapacityError):
        return dict(available=False, recorded=True)


class SummaryHolds:
    def __init__(self, cache):
        self.cache = cache

    @staticmethod
    def _validate(key, record):
        if (not isinstance(record, dict) or set(record) != {
                'owner', 'experiment', 'comparison', 'created_at', 'expires_at',
                'scope', 'configurations', 'admitted'}
                or any(not text(record[k]) for k in ('owner', 'experiment', 'comparison', 'scope'))
                or key != digest([record['owner'], record['experiment'], record['comparison']])
                or not timestamp(record['created_at']) or not timestamp(record['expires_at'])
                or not record['created_at'] < record['expires_at'] <= record['created_at'] + MAX_SECONDS
                or type(record['admitted']) is not bool
                or not isinstance(record['configurations'], list) or len(record['configurations']) not in (1,2)):
            raise ValueError('Invalid summary dependency')
        seen = set()
        for configuration in record['configurations']:
            if (not isinstance(configuration, dict) or set(configuration) != {'configuration', 'job', 'attempt', 'seeds'}
                    or any(not text(configuration[k]) for k in ('configuration', 'job'))
                    or configuration['attempt'] is not None and not text(configuration['attempt'])
                    or not isinstance(configuration['seeds'], list)
                    or not 1 <= len(configuration['seeds']) <= 100
                    or any(not text(seed) for seed in configuration['seeds'])
                    or len(set(configuration['seeds'])) != len(configuration['seeds'])):
                raise ValueError('Invalid summary dependency configuration')
            if configuration['job'] in seen:
                raise ValueError('Comparison must contain different configurations')
            seen.add(configuration['job'])
        if len(record['configurations'])==2 and record['configurations'][0]['seeds'] != record['configurations'][1]['seeds']:
            raise ValueError('Summary dependencies require matching seeds')

    def load(self):
        try:
            with self.cache._open(self.cache.root / STATE) as source:
                body = source.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise SummaryCapacityError('Summary dependency metadata requires operator review') from error
        if len(body) > MAX_BYTES:
            raise SummaryCapacityError('Summary dependency metadata exceeds its bound')
        try:
            value = json.loads(body)
            if (not isinstance(value, dict) or set(value) != {'format', 'holds'}
                    or value['format'] != FORMAT or not isinstance(value['holds'], dict)
                    or len(value['holds']) > MAX_HOLDS):
                raise ValueError('Invalid summary dependency metadata')
            for key, record in value['holds'].items():
                self._validate(key, record)
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            raise SummaryCapacityError('Summary dependency metadata requires operator review') from error
        now = time.time()
        return {key: record for key, record in value['holds'].items() if record['expires_at'] > now}

    def save(self, records):
        body = canonical(dict(format=FORMAT, holds=records))
        if len(records) > MAX_HOLDS or len(body) > MAX_BYTES:
            raise SummaryCapacityError('Summary dependency metadata allowance is full')
        fd, path = tempfile.mkstemp(prefix='.holds-', dir=self.cache.root)
        try:
            with os.fdopen(fd, 'wb') as target:
                target.write(body)
                target.flush()
                os.fsync(target.fileno())
            os.replace(path, self.cache.root / STATE)
            directory = os.open(self.cache.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @staticmethod
    def units(records, *, admitted=True):
        result = {}
        for record in records.values():
            if admitted and not record['admitted']:
                continue
            for configuration in record['configurations']:
                for seed in configuration['seeds']:
                    key = unit(record['owner'], configuration['configuration'], configuration['job'], seed, record['scope'])
                    result.setdefault(key, []).append(configuration['attempt'])
        return result

    @staticmethod
    def configurations(records):
        return {(record['owner'], configuration['configuration'], configuration['job'], record['scope'])
                for record in records.values() if record['admitted']
                for configuration in record['configurations']}

    @staticmethod
    def identity_unit(identity):
        execution = identity.get('execution', {})
        if not isinstance(execution, dict) or not execution.get('job') or 'seed' not in identity:
            return None
        return unit(identity['owner'], identity['configuration'], execution['job'], identity['seed'], scope(identity))

    def matches(self, identity, units):
        key = self.identity_unit(identity)
        attempts = units.get(key, [])
        # An unfinished/retried partner reserves a full future slot. Summaries
        # from its older attempts cannot satisfy or shrink that reservation.
        return key if any(attempt is not None and attempt == identity.get('execution', {}).get('attempt')
                          for attempt in attempts) else None

    def occupancy(self, records, entries, *, incoming=None, incoming_bytes=0):
        """Reserve missing partners at the maximum supported summary size.

        Already published summaries use their actual sizes. Shared baseline
        seeds count once across comparisons; unrelated writers cannot consume
        space promised to a missing counterpart.
        """
        units = self.units(records)
        protected = set()
        present = set()
        for _, key, _, receipt in entries:
            selected = self.matches(receipt['identity'], units)
            if selected:
                protected.add(key)
                present.add(selected)
        missing = set(units) - present
        total = sum(entry[2] for entry in entries)
        count = len(entries)
        if incoming is not None:
            selected = self.matches(incoming, units)
            if selected in present:
                raise SummaryCapacityError('A retained seed identity changed; refresh its comparison dependencies')
            missing.discard(selected)
            total += incoming_bytes
            count += 1
        total += len(missing) * (self.cache.entry_bytes + RECEIPT_BYTES)
        count += len(missing)
        return total, count, protected

    def fits(self, records, entries):
        _, _, protected = self.occupancy(records, entries)
        retained = [entry for entry in entries if entry[1] in protected]
        size, count, _ = self.occupancy(records, retained)
        return size <= self.cache.maximum_bytes and count <= self.cache.maximum_entries

    def replace(self, owner, experiment, comparisons, *, calculation, owner_entries):
        if (not text(owner) or not text(experiment) or not text(calculation)
                or type(owner_entries) is not int or not 1 <= owner_entries <= self.cache.maximum_entries
                or not isinstance(comparisons, list) or len(comparisons) > MAX_HOLDS):
            raise ValueError('Invalid summary dependency request')
        with self.cache._lock('.cache.lock'):
            records = self.load()
            entries = self.cache._entries()
            previous = {key: record for key, record in records.items()
                        if record['owner'] == owner and record['experiment'] == experiment}
            retained = {key: record for key, record in records.items() if key not in previous}
            requested = {}
            for comparison in comparisons:
                record = dict(comparison, owner=owner, experiment=experiment, scope=calculation, admitted=False)
                key = digest([owner, experiment, record['comparison']])
                self._validate(key, record)
                if key in requested:
                    raise ValueError('Duplicate comparison dependency')
                if record['expires_at'] > time.time():
                    requested[key] = record
            # Existing promises lead; new work cannot evict a still-needed seed.
            order = sorted(requested, key=lambda key: (not previous.get(key, {}).get('admitted', False),
                                                       requested[key]['created_at'], key))
            for key in order:
                record = requested[key]
                if key in previous:
                    record = dict(record, expires_at=min(record['expires_at'], previous[key]['expires_at']))
                candidate = dict(retained, **{key: dict(record, admitted=True)})
                owned = {k: r for k, r in candidate.items() if r['owner'] == owner}
                if len(self.configurations(owned)) <= owner_entries and self.fits(candidate, entries):
                    record = dict(record, admitted=True)
                elif previous.get(key, {}).get('admitted'):
                    raise SummaryCapacityError('Configured summary allowance cannot honour existing dependencies')
                retained[key] = record
            if not self.fits(retained, entries):
                raise SummaryCapacityError('Existing retained summaries exceed the configured allowance')
            self.save(retained)
            self.cache._evict(entries, holds=retained)
            return dict(admitted=sum(retained[key]['admitted'] for key in requested),
                        deferred=sum(not retained[key]['admitted'] for key in requested))

    def invalidate(self, records, owner, configuration=None, *, job=None, attempt=None):
        return {key: record for key, record in records.items()
                if not (record['owner'] == owner and (configuration is None or
                    any(c['configuration'] == configuration and (job is None or
                        c['job'] == job and c['attempt'] == attempt) for c in record['configurations'])))}

    def require_admitted(self, identity, records=None):
        records = self.load() if records is None else records
        admitted = self.units(records)
        wanted = self.units(records, admitted=False)
        key = self.identity_unit(identity)
        if key in wanted and not self.matches(identity, admitted):
            raise SummaryCapacityError('Waiting for summary storage reserved for pending comparisons')
        return self.matches(identity, admitted)

    def before_raw(self, identity, records=None):
        records = self.load() if records is None else records
        admitted = self.require_admitted(identity, records)
        if not records or admitted:
            return
        # Backpressure unrelated misses before expensive CSV parsing when every
        # remaining byte/entry is promised. No summary is silently discarded.
        entries = self.cache._entries()
        size = min(self.cache.maximum_bytes, self.cache.entry_bytes + RECEIPT_BYTES)
        self.cache._evict(entries, size, 1, holds=records, incoming=identity)
