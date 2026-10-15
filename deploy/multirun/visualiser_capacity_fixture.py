#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Prepare private, verified summary replay fixtures for Visualiser capacity trials.
Repeated seed labels are synthetic and cannot establish scientific correctness.
No simulations, raw-output copies, dependency downloads or service changes occur.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import time


MIB = 1024 ** 2
SUMMARY_LIMIT = 64 * MIB
EVIDENCE_LIMIT = 4 * MIB
BUILD_LIMIT = 8 * MIB
ANNUAL_YEARS = tuple(range(2019, 2071))
ROW_FIELDS = frozenset(('year', 'scenario', 'run', 'variable', 'variable_value',
    'stratifier', 'stratifier_value', 'metric_type', 'n', 'metric_value'))
METRIC_TYPES = frozenset(('income_bin', 'mean', 'pyramid_bin', 'share', 'wage_bin'))
LIMITATIONS = [
    'Synthetic seed replay: each configuration repeats one original seed summary; '
    'these are not independent simulation repetitions.',
    'Synthetic alternatives replay the one original alternative; no additional '
    'policy configurations have been simulated.',
    'This fixture tests summary combination, publication and indexing capacity; '
    'it does not validate ten-seed scientific results or uncertainty estimates.',
    'Raw-output parsing, annual-block memory and raw-data disk-read timings are '
    'not exercised by this fixture.',
]


class FixtureError(ValueError):
    """Refuse incomplete, changed or incompatible capacity evidence."""


def _path(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise FixtureError('Symlinks are not allowed in fixture paths')
    return path


@contextmanager
def _regular(path, maximum):
    path = _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise FixtureError('Evidence is not a bounded ordinary file')
        yield source
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise FixtureError('Evidence changed while being read')


def _fingerprint(body):
    return dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())


def _read(path, maximum):
    with _regular(path, maximum) as source:
        body = source.read(maximum + 1)
    if len(body) > maximum:
        raise FixtureError('Evidence exceeds its size bound')
    return body


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FixtureError('Duplicate JSON field')
        result[key] = value
    return result


def _json(body):
    def invalid(_):
        raise FixtureError('Nonfinite JSON number')
    try:
        return json.loads(body, object_pairs_hook=_object, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise FixtureError('Invalid fixture JSON') from error


def _receipt(value, maximum):
    if (type(value) is not dict or set(value) != {'bytes', 'sha256'}
            or type(value['bytes']) is not int or not 0 < value['bytes'] <= maximum
            or not isinstance(value['sha256'], str)
            or re.fullmatch('[a-f0-9]{64}', value['sha256']) is None):
        raise FixtureError('Invalid bounded file receipt')
    return value


def _decode(source, maximum, *, timeout=60):
    """Read bounded zstd stdout and always reap the decompressor."""
    binary = shutil.which('zstd')
    if not binary:
        raise FixtureError('Install zstd before preparing this fixture')
    child = subprocess.Popen([binary, '-d', '-c'], stdin=source,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
    selector = selectors.DefaultSelector()
    blocks = []
    size = 0
    deadline = time.monotonic() + timeout
    try:
        selector.register(child.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FixtureError('Summary decompression timed out')
            if not selector.select(min(remaining, 1)):
                continue
            block = os.read(child.stdout.fileno(), min(MIB, maximum + 1 - size))
            if not block:
                break
            size += len(block)
            if size > maximum:
                raise FixtureError('Decoded summary exceeds its size bound')
            blocks.append(block)
        try:
            code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            raise FixtureError('Summary decompression timed out') from error
        if code:
            raise FixtureError('Summary decompression failed')
        return b''.join(blocks)
    finally:
        selector.close()
        child.stdout.close()
        if child.poll() is None:
            child.kill()
        child.wait()


def _summary(archive, index):
    source_name = f'configuration-{index}-run-0.json.zst'
    path = archive / source_name
    receipt_path = archive / (source_name + '.receipt.json')
    receipt_body = _read(receipt_path, EVIDENCE_LIMIT)
    receipt = _json(receipt_body)
    if type(receipt) is not dict or receipt.get('decoded_hash_verified') is not True:
        raise FixtureError('Summary lacks a verified decoded receipt')
    expected_archive = _receipt(receipt.get('archive'), SUMMARY_LIMIT)
    expected_original = _receipt(receipt.get('original'), SUMMARY_LIMIT)
    # Receipt paths document the old location only; never follow them as inputs.
    with _regular(path, SUMMARY_LIMIT) as source:
        compressed = source.read(SUMMARY_LIMIT + 1)
        if _fingerprint(compressed) != expected_archive:
            raise FixtureError('Compressed summary differs from its receipt')
        del compressed
        source.seek(0)
        body = _decode(source, expected_original['bytes'])
    if _fingerprint(body) != expected_original:
        raise FixtureError('Decoded summary differs from its receipt')
    rows = _json(body)
    role = 'baseline' if index == 0 else 'scenario_1'
    _validate_rows(rows, role)
    provenance = dict(path=str(receipt_path), **_fingerprint(receipt_body),
        source_name=source_name, archive=expected_archive, original=expected_original,
        source_rows=len(rows))
    return body, provenance


def _validate_rows(rows, role):
    if type(rows) is not list or not rows or len(rows) > 200000:
        raise FixtureError('Summary must contain bounded metric rows')
    years = set()
    for row in rows:
        if type(row) is not dict or set(row) != ROW_FIELDS:
            raise FixtureError('Summary row has an incompatible schema')
        if (type(row['year']) is not int or row['year'] not in ANNUAL_YEARS
                or row['scenario'] != role or row['run'] != '606'):
            raise FixtureError('Summary years, original seed or role differ')
        for field in ('variable', 'variable_value', 'stratifier', 'stratifier_value', 'metric_type'):
            value = row[field]
            if (not isinstance(value, str) or not 1 <= len(value) <= 200
                    or any(ord(char) < 32 for char in value)):
                raise FixtureError('Invalid summary metric label')
        if row['metric_type'] not in METRIC_TYPES:
            raise FixtureError('Invalid summary metric type')
        if type(row['n']) is not int or row['n'] < 0:
            raise FixtureError('Invalid summary sample count')
        value = row['metric_value']
        if value is not None:
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                valid = False
            if not valid:
                raise FixtureError('Invalid summary metric value')
        years.add(row['year'])
    if years != set(ANNUAL_YEARS):
        raise FixtureError('Summary must cover every annual year from 2019 through 2070')


def _write(path, body):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(body)


def prepare_fixture(archive, build, output, *, repetitions=10, alternatives=1):
    """Return the manifest after creating a new private summary replay directory."""
    if type(repetitions) is not int or not 1 <= repetitions <= 10:
        raise FixtureError('Select one to ten synthetic repetitions')
    if type(alternatives) is not int or not 1 <= alternatives <= 3:
        raise FixtureError('Select one to three synthetic alternatives')
    archive, build, output = map(_path, (archive, build, output))
    if not archive.is_dir() or not build.is_dir():
        raise FixtureError('Archive and build must be existing directories')
    if output.exists() or not output.parent.is_dir():
        raise FixtureError('Output must be a new directory with an existing parent')
    if archive in output.parents or build in output.parents:
        raise FixtureError('Output must not be inside archived evidence or the build')
    report_path = archive / 'report.json'
    report_body = _read(report_path, EVIDENCE_LIMIT)
    report = _json(report_body)
    if (type(report) is not dict or report.get('passed') is not True
            or report.get('source_unchanged') is not True):
        raise FixtureError('Use a passed aggregation report with unchanged sources')
    try:
        recorded_files = report['backend']['build']['files']
        backend = {}
        for name in ('calculation.cjs', 'runner.cjs'):
            actual = _fingerprint(_read(build / name, BUILD_LIMIT))
            if actual['sha256'] != recorded_files[name]:
                raise FixtureError('Build differs from the archived calculation')
            backend[name.split('.')[0] + '_sha256'] = actual['sha256']
    except (KeyError, TypeError) as error:
        raise FixtureError('Archived report lacks the calculation identity') from error
    summary_bodies = []
    receipts = []
    for index in range(2):
        body, provenance = _summary(archive, index)
        summary_bodies.append(body)
        receipts.append(provenance)
    seeds = [str(900000001 + index) for index in range(repetitions)]
    configurations = []
    for index in range(alternatives + 1):
        configurations.append(dict(id='baseline' if index == 0 else f'alternative{index}',
            name='Replay baseline' if index == 0 else f'Replay alternative {index}',
            role='baseline' if index == 0 else f'scenario_{index}',
            runs=[dict(seed=seed, summary='baseline.json' if index == 0 else 'alternative.json',
                       source_seed='606') for seed in seeds]))
    files = dict(zip(('baseline.json', 'alternative.json'), map(_fingerprint, summary_bodies)))
    manifest = dict(format='simpaths.visualiser.capacity-fixture.v1', mode='summary-replay',
        annual_years=list(ANNUAL_YEARS), seeds=seeds, configurations=configurations,
        files=files, backend=backend,
        provenance=dict(report=dict(path=str(report_path), **_fingerprint(report_body)),
                        receipts=receipts, build=str(build)), limitations=LIMITATIONS)
    output.mkdir(mode=0o700)
    try:
        for name, body in zip(files, summary_bodies):
            _write(output / name, body)
        _write(output / 'manifest.json',
            (json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + '\n').encode())
        _write(output / 'COPYRIGHT.md', (
            '<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Attribution for generated Visualiser capacity replay evidence.\n'
            '@author ross richardson\n-->\n\n'
            'The generated manifest is attributed here. Archived scientific summaries\n'
            'retain their original attribution and licences. This notice does not relicense them.\n'
        ).encode())
    except BaseException:
        shutil.rmtree(output)
        raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--repetitions', type=int, choices=range(1, 11), default=10)
    parser.add_argument('--alternatives', type=int, choices=range(1, 4), default=1)
    args = parser.parse_args(argv)
    try:
        prepare_fixture(args.archive, args.build, args.output,
            repetitions=args.repetitions, alternatives=args.alternatives)
    except (FixtureError, OSError) as error:
        parser.exit(1, f'STOPPED: {error}\n')
    print('Prepared synthetic summary replay: ' + str(args.output / 'manifest.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
