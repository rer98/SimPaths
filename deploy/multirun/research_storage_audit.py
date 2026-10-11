#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Read verified retained research output once, counting encoded CSV bytes by year.
Prepare a scoped storage trial and its space preflight without running models.
Only counts, sizes, hashes and frozen configuration metadata leave the reader.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._workflow import frontend_path
from deploy.multirun.artifacts import (
    ArtifactError, digest, fingerprint, relative_name, write_attribution, write_json)
from deploy.multirun.prepared_dataset import check_manifest, verify_snapshot
from deploy.multirun.queue_adapter import OUTPUT_CSV_FILES, workspace_required_bytes
from deploy.multirun.research_calibration import checked_settings, configuration, proof_timeout, read_calibration
from deploy.multirun.research_comparison import read_json, retained_run
from deploy.multirun.resource_policy import DEFAULT_POLICY, check_policy, simulation_storage

MIB = 1024**2
GIB = 1024**3
FORMAT = 'simpaths.private-research-storage-audit.v1'
MAX_RECORD_BYTES = MIB
MAX_COLUMNS = 256
MAX_FILES = 1000


@contextmanager
def stable_file(path, expected):
    """Never follow links; reject changes or replacements during a read."""
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ArtifactError('Symlink in retained output')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected['bytes']:
            raise ArtifactError('Retained output type or size changed')
        yield stream, before
        after = os.fstat(stream.fileno())
        current = path.lstat()
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
            raise ArtifactError('Retained output changed while being audited')


class RecordLines:
    """csv.reader consumes physical lines; count original bytes per logical row."""
    def __init__(self, stream):
        self.stream = stream
        self.hasher = hashlib.sha256()
        self.total = 0
        self.record = 0
        self.first = True

    def __iter__(self):return self

    def __next__(self):
        line = self.stream.readline(MAX_RECORD_BYTES + 1)
        if not line:raise StopIteration
        self.record += len(line)
        if self.record > MAX_RECORD_BYTES:
            raise ArtifactError('CSV logical record exceeds its bounded reader')
        self.total += len(line)
        self.hasher.update(line)
        try:
            text = line.decode('utf-8-sig' if self.first else 'utf-8')
        except UnicodeDecodeError:
            raise ArtifactError('Retained CSV is not UTF-8') from None
        self.first = False
        return text

    def consumed(self):
        value = self.record
        self.record = 0
        return value


def annual_csv(path, expected, start_year, end_year):
    """Streaming exact byte accounting, including quoted/multiline records."""
    if (type(start_year) is not int or type(end_year) is not int
            or not 1900 <= start_year <= end_year <= 2200):
        raise ArtifactError('Select a bounded annual output horizon')
    years = {str(year):dict(rows=0, bytes=0) for year in range(start_year, end_year+1)}
    with stable_file(path, expected) as (stream, file_stat):
        lines = RecordLines(stream)
        reader = csv.reader(lines, strict=True)
        try:
            header = next(reader)
            header_bytes = lines.consumed()
            if not 1 <= len(header) <= MAX_COLUMNS or len(set(header)) != len(header) or header.count('time') != 1:
                raise ArtifactError('Retained annual CSV needs one time column and unique bounded columns')
            column = header.index('time')
            for row in reader:
                size = lines.consumed()
                if len(row) != len(header) or len(row[column]) > 32:
                    raise ArtifactError('Malformed retained annual CSV record')
                try:
                    year = Decimal(row[column])
                    if not year.is_finite() or year != year.to_integral_value() or not start_year <= year <= end_year:
                        raise InvalidOperation
                except InvalidOperation:
                    raise ArtifactError('Retained CSV has an invalid or out-of-horizon annual time') from None
                value = years[str(int(year))]
                value['rows'] += 1
                value['bytes'] += size
        except (csv.Error, StopIteration):
            raise ArtifactError('Malformed or empty retained CSV') from None
        if any(value['rows'] == 0 for value in years.values()):
            raise ArtifactError('Retained CSV is missing an expected annual year')
        if (lines.total != expected['bytes'] or lines.hasher.hexdigest() != expected['sha256']
                or header_bytes + sum(y['bytes'] for y in years.values()) != lines.total):
            raise ArtifactError('CSV bytes or hash differ from the verified retained manifest')
    return dict(**expected, allocated_bytes=file_stat.st_blocks*512, columns=len(header),
                rows=sum(y['rows'] for y in years.values()), header_bytes=header_bytes, years=years)


def file_states(directory):
    directory = Path(directory)
    if not directory.is_dir() or any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ArtifactError('Expected an ordinary retained output directory')
    names = {}
    for parent, directories, files in os.walk(directory, followlinks=False):
        if any((Path(parent)/name).is_symlink() for name in directories):
            raise ArtifactError('Symlink in retained output directory')
        for name in files:
            path = Path(parent)/name
            relative = path.relative_to(directory).as_posix()
            relative_name(relative)
            value = path.lstat()
            if not stat.S_ISREG(value.st_mode):
                raise ArtifactError('Retained output must contain ordinary files')
            names[relative] = (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if len(names) > MAX_FILES:raise ArtifactError('Retained file inventory exceeds its audit bound')
    return names


def unchanged_evidence(source):
    for name, expected in source['provenance'].items():
        if fingerprint(source['proof']/name) != expected:
            raise ArtifactError('Native completion evidence changed during the audit')
    if fingerprint(source['folder']/'report.json') != source['outer_report']:
        raise ArtifactError('Outer completion evidence changed during the audit')


def audit_source(source, progress=lambda message:None):
    manifest = source['manifest']
    check_manifest(manifest)
    expected_names = set(manifest)
    original_states = file_states(source['output'])
    if not expected_names or set(original_states) != expected_names:
        raise ArtifactError('Retained files differ from the completed manifest')
    settings = checked_settings(source['record']['settings'])
    files = {}
    runs = {}
    for name, expected in sorted(manifest.items()):
        parts = Path(name).parts
        if len(parts) not in (2, 3):raise ArtifactError('Unexpected native output layout')
        run = runs.setdefault(parts[0], dict(files=[], annual_files=[]))
        path = source['output']/name
        progress('Auditing '+source['record'].get('variant', 'baseline')+': '+name)
        if len(parts) == 3 and parts[1] == 'input' and parts[2] != 'options.txt':
            raise ArtifactError('Completed native input copies have not been reclaimed')
        if len(parts) == 3 and parts[1] == 'csv' and parts[2] in OUTPUT_CSV_FILES:
            measured = annual_csv(path, expected, 2019, settings['end_year'])
            run['annual_files'].append(parts[2])
        else:
            if fingerprint(path) != expected:raise ArtifactError('Retained non-annual file hash changed')
            measured = dict(**expected, allocated_bytes=path.stat().st_blocks*512)
        files[name] = measured
        run['files'].append(name)
    seeds = [r['seed'] for r in source['record']['verified_repetitions']]
    if len(runs) != len(seeds):raise ArtifactError('Retained folder count differs from completed repetitions')
    run_values = []
    for folder, details in sorted(runs.items()):
        if set(details['annual_files']) != set(OUTPUT_CSV_FILES):
            raise ArtifactError('Retained repetition is missing a required annual output file')
        options_name = folder+'/input/options.txt'
        if options_name not in files or files[options_name]['bytes'] > MIB:
            raise ArtifactError('Expected retained, bounded native options')
        options = (source['output']/options_name).read_text()
        matches = [line.split(':', 1)[1].strip() for line in options.splitlines()
                   if line.startswith('randomSeedIfFixed:')]
        if len(matches) != 1 or matches[0] not in seeds:
            raise ArtifactError('Retained options do not identify an original seed')
        seed = matches[0]
        seeds.remove(seed)
        run_values.append(dict(folder=folder, seed=seed,
            bytes=sum(files[n]['bytes'] for n in details['files']),
            allocated_bytes=sum(files[n]['allocated_bytes'] for n in details['files'])))
    if seeds or file_states(source['output']) != original_states:
        raise ArtifactError('Retained original seeds or inventory changed')
    unchanged_evidence(source)
    return dict(variant=source['record'].get('variant', 'baseline'), folder=str(source['folder']),
        provenance=source['provenance'], outer_report=source['outer_report'], settings=settings,
        configuration=source['configuration'].as_dict(), files=files, runs=run_values,
        bytes=sum(f['bytes'] for f in files.values()),
        allocated_bytes=sum(f['allocated_bytes'] for f in files.values()),
        sampled_peak_workspace_bytes=source['record']['peak_workspace_bytes'],
        attempts=source['record']['attempts'])


def scoped_plan(receipt, sources, *, repetitions, workspace, frontend, python):
    """A trial policy, not a production default or a population extrapolation."""
    if not sources or any(not source['runs'] for source in sources):
        raise ArtifactError('Storage projections need completed repetition measurements')
    configurations = [s['configuration'] for s in sources]
    common = configurations[0]['common']
    collectors = configurations[0]['run_sets'][0]['collector_args']
    if (common != dict(country='UK', start_year=2019, end_year=2070, population=100000)
            or any(c['common'] != common or c['run_sets'][0]['collector_args'] != collectors for c in configurations)):
        raise ArtifactError('Storage projections need the same measured population, horizon and collectors')
    maximum = max(max(r['bytes'], r['allocated_bytes']) for s in sources for r in s['runs'])
    # 15% above the largest observed native output, rounded up to 256 MiB.
    # This is a candidate for the next measured trial, not a proven upper bound.
    increment = ((maximum*115 + 100*256*MIB-1)//(100*256*MIB))*256
    policy = deepcopy(DEFAULT_POLICY)
    policy['simulation'].update(memory_mib=5120, large_memory_mib=5120)
    policy['simulation']['storage'] = dict(setup_mib=4096, per_repetition_mib=increment)
    policy = check_policy(policy)
    minimum = workspace_required_bytes(receipt['identity'])
    allocation = simulation_storage(policy, repetitions=repetitions, minimum_setup_bytes=minimum)
    settings = checked_settings({**sources[0]['settings'], 'repetitions':repetitions,
        'storage_mib':allocation['storage_mib'], 'heap_mib':4096, 'memory_mib':5120,
        'max_memory_mib':7168, 'setup_seconds':900, 'repetition_seconds':14400, 'max_attempts':1})
    prepared = Path(workspace['prepared']).absolute()
    destination = Path(workspace['trial_output']).absolute()
    filesystem = destination.parent
    while not filesystem.exists():filesystem = filesystem.parent
    free = shutil.disk_usage(filesystem).free
    required = (settings['storage_mib']+3072)*MIB
    config = configuration(receipt, settings)
    command = [str(python), str(ROOT/'deploy/multirun/research_calibration.py'),
        '--frontend', str(frontend), '--prepared', str(prepared), '--population', '100000',
        '--end-year', '2070', '--repetitions', str(repetitions), '--storage-mib', str(settings['storage_mib']),
        '--heap-mib', '4096', '--memory-mib', '5120', '--max-memory-mib', '7168',
        '--setup-seconds', '900', '--repetition-seconds', '14400', '--max-attempts', '1',
        '--output', str(destination)]
    counts = sorted({1, repetitions, 10})
    projections = []
    for count in counts:
        resources = simulation_storage(policy, repetitions=count, minimum_setup_bytes=minimum)
        projections.append(dict(repetitions=count, estimated_retained_bytes=count*maximum,
            minimum_setup_including_one_gib_reserve_bytes=minimum,
            storage_mib=resources['storage_mib'], required_free_disk_bytes=(resources['storage_mib']+3072)*MIB))
    return dict(candidate_only=True, defaults_changed=False, production_profile_registered=False,
        scope=dict(**common, collector_args=collectors, model=receipt['identity']['model'],
                   prepared_sha256=receipt['sha256']), resource_policy=policy, allocation=allocation,
        largest_observed_repetition_bytes=maximum, increment_headroom_bytes=increment*MIB-maximum,
        settings=settings, configuration=config.as_dict(), proof_timeout_seconds=proof_timeout(settings),
        trial_output=str(destination), command=command,
        preflight=dict(filesystem=str(filesystem), free_disk_bytes=free, required_free_disk_bytes=required,
            additional_free_disk_bytes_needed=max(0, required-free), disk_ready=free>=required,
            required_available_memory_bytes=(settings['max_memory_mib']+2048)*MIB,
            reservation_is_not_preallocated_disk=True),
        projections=projections, ten_seed_baseline_and_one_alternative_retained_bytes=20*maximum,
        four_users_each_with_that_comparison_retained_bytes=80*maximum)


def audit(prepared, folders, output, *, repetitions=2, frontend=None, python=None):
    prepared = Path(prepared).absolute()
    folders = [Path(folder).absolute() for folder in folders]
    output = Path(output).absolute()
    if not 1 <= len(folders) <= 10 or len(set(folders)) != len(folders):
        raise ArtifactError('Select one to ten distinct completed calibration directories')
    if output.exists() or output.is_symlink():raise ArtifactError('Choose a new audit directory')
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ArtifactError('Audit destination must not use symlinks')
    for source in [prepared, *folders, ROOT, Path(frontend or frontend_path())]:
        if output == source or source in output.parents:
            raise ArtifactError('Keep the audit outside source/preparation/completion directories')
    receipt = read_calibration(prepared)
    verify_snapshot(prepared, receipt)
    sources = []
    for folder in folders:
        record = read_json(folder/'model-proof/report.json')
        settings = checked_settings(record['settings'])
        source = retained_run(folder, receipt, record.get('variant', 'baseline'),
                              check_files=False, repetitions=settings['repetitions'])
        source['outer_report'] = fingerprint(folder/'report.json')
        sources.append(source)
    started = time.monotonic()
    measured = [audit_source(source, progress=lambda message:print(message, flush=True)) for source in sources]
    for source in sources:unchanged_evidence(source)
    if read_calibration(prepared) != receipt:raise ArtifactError('Prepared calibration evidence changed')
    plan = scoped_plan(receipt, measured, repetitions=repetitions,
        workspace=dict(prepared=prepared, trial_output=output.parent/(output.name+'-two-seed-trial' if repetitions==2
                                                                      else output.name+'-trial')),
        frontend=Path(frontend or frontend_path()).absolute(), python=python or sys.executable)
    record = dict(format=FORMAT, passed=True, read_only=True, simulations_started=0, microdata_copied=False,
        original_manifest_hashes_verified=True, elapsed_seconds=round(time.monotonic()-started, 3),
        source_sha256=fingerprint(Path(__file__))['sha256'], prepared_sha256=receipt['sha256'],
        measurement='Encoded CSV record bytes grouped by annual time; allocated bytes are current local filesystem blocks. '
                    'Live workspace peaks are previously sampled values, not new kernel peaks. '
                    'Repetition projections use the largest observed complete run and do not bound untested settings.',
        sources=measured, plan=plan)
    output.mkdir(mode=0o700, parents=True)
    write_attribution(output)
    write_json(output/'report.json', record)
    write_json(output/'candidate-resources.json', plan['resource_policy'])
    write_json(output/'test-plan.json', dict(format=FORMAT, audit=fingerprint(output/'report.json'),
        audit_sha256=digest(record), **plan))
    print(f"PASS: original hashes and every annual file verified; largest retained run {plan['largest_observed_repetition_bytes']/GIB:.3f} GiB", flush=True)
    print(f"Candidate storage: {plan['allocation']['setup_mib']/1024:g} GiB + "
          f"{plan['allocation']['per_repetition_mib']/1024:g} GiB per repetition", flush=True)
    print(f"Trial needs {plan['preflight']['required_free_disk_bytes']/GIB:g} GiB free; "
          f"{plan['preflight']['free_disk_bytes']/GIB:.2f} GiB currently free. No model was started.", flush=True)
    print('Dry-run command: TMPDIR='+shlex.quote(str(output.parent))+' '+shlex.join(plan['command']+['--dry-run']), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--completed', type=Path, nargs='+', required=True,
                        help='One or more completed research calibration evidence directories')
    parser.add_argument('--frontend', type=Path, default=frontend_path())
    parser.add_argument('--repetitions', type=int, choices=range(1, 11), default=2,
                        help='Repetition count for a prepared, unexecuted trial plan')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or Path('/tmp-codex')/('research-storage-audit-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    audit(args.prepared, args.completed, output, repetitions=args.repetitions, frontend=args.frontend)
    print('PASSED: '+str(output/'report.json'), flush=True)
    return 0


if __name__ == '__main__':raise SystemExit(main())
