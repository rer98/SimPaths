#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Private public-training calibration of longer/larger MultiRun research workloads.
Uses disposable PostgreSQL, the production worker/executor and annual CSV checks.
The calibration receipt is deliberately rejected by normal service adapters.
@author ross richardson
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._tool_loader import load_tool
from deploy._workflow import frontend_path
sys.path.insert(0, str(frontend_path()))
from deploy.multirun.artifacts import (
    ArtifactError, digest, fingerprint, inventory, verify, write_attribution, write_json)
from deploy.multirun.compare_native import proof_configuration
from deploy.multirun.configuration import normalise
from deploy.multirun.prepared_dataset import check_manifest, verify_snapshot
from deploy.multirun.queue_adapter import workspace_required_bytes
from deploy.multirun.resource_policy import DEFAULT_POLICY, check_policy
from deploy.multirun.resource_recovery_proof import (
    ObservedAdapter, PROBE, PREFIX, browser_session, compile_probe, retire_attempts, summary, telemetry)
from deploy.multirun.releases import atomic_json
from deploy.multirun import storage_proof

MIB = 1024**2
GIB = 1024**3
FORMAT = 'simpaths.private-research-calibration.v1'
SOURCE = 'maintainer-public-training-calibration'
ENV = 'SIMPATHS_RESEARCH_TRIAL'
FIELDS = {'population', 'end_year', 'repetitions', 'heap_mib', 'memory_mib',
          'max_memory_mib', 'storage_mib', 'setup_seconds', 'repetition_seconds', 'max_attempts'}
VARIANTS = ('baseline', 'lower-saving-rate')
BUILD_TIME = re.compile(r'^Time to complete initialisation ([0-9]+(?:\.[0-9]+)?) minutes\.$')


def json_record(value):
    """Preserve database event times in evidence without hiding unsupported types."""
    def convert(item):
        if isinstance(item, datetime):return item.isoformat()
        raise TypeError('Unsupported calibration evidence type: '+type(item).__name__)
    return json.loads(json.dumps(value, default=convert))


def checked_settings(value):
    bounds = dict(population=(1000, 100000), end_year=(2019, 2070), repetitions=(1, 10),
        heap_mib=(1024, 7168), memory_mib=(5120, 8192), max_memory_mib=(5120, 8192),
        storage_mib=(6144, 65536), setup_seconds=(0, 3600),
        repetition_seconds=(60, 86400), max_attempts=(1, 3))
    if (type(value) is not dict or set(value) != FIELDS or any(type(value[k]) is not int
            or not low <= value[k] <= high for k, (low, high) in bounds.items())):
        raise ArtifactError('Select bounded private research calibration settings')
    if value['heap_mib'] + 1024 > value['memory_mib'] or value['memory_mib'] > value['max_memory_mib']:
        raise ArtifactError('Keep at least 1 GiB native headroom and an adequate memory ceiling')
    if proof_timeout(value) > 172800:
        raise ArtifactError('Trial exceeds the 48-hour proof limit; reduce repetitions, time or attempts')
    return dict(value)


def attempt_seconds(settings):
    return settings['setup_seconds'] + settings['repetitions'] * settings['repetition_seconds']


def proof_timeout(settings):
    return settings['max_attempts'] * attempt_seconds(settings) + 1800


def sample_millis(settings):
    # At most about 1000 telemetry records per attempt, within Docker's existing
    # 2 MiB rotated log budget. This is a sampled peak, never an exact peak claim.
    return max(2000, math.ceil(attempt_seconds(settings) / 1000) * 1000)


def trial_policy(settings):
    result = deepcopy(DEFAULT_POLICY)
    result['simulation'].update(memory_mib=settings['memory_mib'], large_memory_mib=settings['memory_mib'])
    result['simulation']['storage'] = dict(setup_mib=settings['storage_mib'], per_repetition_mib=0)
    return check_policy(result)


def read_calibration(prepared):
    prepared = Path(prepared)
    if fingerprint(prepared/'receipt.json')['bytes'] > MIB:
        raise ArtifactError('Calibration receipt exceeds 1 MiB')
    receipt = json.loads((prepared/'receipt.json').read_text())
    identity = receipt.get('identity', {})
    expected = {'format', 'source', 'country', 'start_year', 'population', 'max_end_year',
                'source_image', 'model', 'prepared', 'profile'}
    if (set(receipt) != {'identity', 'sha256', 'revision'} or set(identity) != expected
            or identity['format'] != FORMAT or identity['source'] != SOURCE or identity['country'] != 'UK'
            or identity['start_year'] != 2019 or identity['max_end_year'] != 2070
            or type(identity['population']) is not int or not 1000 <= identity['population'] <= 100000
            or not isinstance(identity['source_image'], str)
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', identity['source_image'])
            or receipt['sha256'] != digest(identity) or receipt['revision'] != 'calibration-'+digest(identity)):
        raise ArtifactError('Invalid private research calibration receipt')
    check_manifest(identity['prepared'])
    check_manifest({'model.jar': identity['model'], 'profile.json': identity['profile']})
    for name in ('input.mv.db', 'DatabaseCountryYear.xlsx', 'EUROMODpolicySchedule.xlsx'):
        if identity['prepared'].get(name, {}).get('bytes', 0) <= 0:
            raise ArtifactError('Incomplete private prepared inputs')
    if identity['profile']['bytes'] > MIB or fingerprint(prepared/'profile.json') != identity['profile']:
        raise ArtifactError('Prepared population evidence changed')
    profile = json.loads((prepared/'profile.json').read_text())
    expected_profile = load_tool('web-quickstart/prepare_profile.py').profile_for(
        identity['population'], research_calibration=True)
    if (profile.get('calibration_only') is not True or profile.get('profile') != expected_profile
            or profile.get('profile_id') != f"private-research-uk-2019-{identity['population']}-seed606"
            or profile.get('fresh_jvm_loading_verified') is not True or profile.get('simulated_years_run') != 0
            or set(profile.get('actual_counts', {})) != {'person', 'household', 'benefitunit'}
            or any(type(n) is not int or n <= 0 for n in profile['actual_counts'].values())
            or profile.get('jar_sha256') != identity['model']['sha256']):
        raise ArtifactError('Prepared population was not independently verified for this calibration')
    return receipt


def preflight(settings, workspace, *, preparing=False):
    settings = checked_settings(settings)
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    required_memory = (settings['max_memory_mib'] + 2048) * MIB
    if int(memory['MemAvailable'].split()[0])*1024 < required_memory:
        raise ArtifactError(f'Need {required_memory/GIB:g} GiB available RAM for the model ceiling and test services')
    workspace = Path(workspace).absolute()
    while not workspace.exists():
        workspace = workspace.parent
    # Preparation's loading copy is reclaimed before execution. Recheck after
    # preparation rather than pretending its entire peak overlaps the run.
    required_disk = max(settings['storage_mib'] + 3072, 7168 if preparing else 0)*MIB
    if shutil.disk_usage(workspace).free < required_disk:
        raise ArtifactError(f'Need {required_disk/GIB:g} GiB free on the calibration filesystem')
    return dict(required_available_memory_bytes=required_memory, required_free_disk_bytes=required_disk)


def installed_image(name):
    from jasmine_web.batch.docker_executor import DockerCLI
    image = json.loads(DockerCLI().call('image', 'inspect', name))[0]
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image['Id']) or image['Config'].get('Volumes'):
        raise ArtifactError('Use an installed immutable Java runtime image without anonymous volumes')
    return image['Id']


def prepare_calibration(destination, population, image, *, timeout=7200):
    """Use existing public-source preparation and fresh-JVM reuse verification."""
    from deploy.multirun.local_process import require_local_runtime
    require_local_runtime()  # H2 AUTO_SERVER needs a loopback socket; fail before copying.
    tool = load_tool('web-quickstart/prepare_profile.py')
    tool.prepare(SimpleNamespace(repo=ROOT, output=destination, population=population,
        timeout=timeout, dry_run=False, research_calibration=True))
    write_attribution(destination)
    prepared = destination/'package'
    model = fingerprint(destination/'tools/simpaths.jar', prepared/'model.jar')
    # Source workbooks may already have maintainer edits. Record and preserve
    # their selected bytes; preparation never opens a source database or writes input/.
    sources = json.loads((destination/'source-inputs.json').read_text())
    if any(fingerprint(ROOT/'input'/name)['sha256'] != checksum for name, checksum in sources.items()):
        raise ArtifactError('Source inputs changed during preparation')
    identity = dict(format=FORMAT, source=SOURCE, country='UK', start_year=2019,
        population=population, max_end_year=2070, source_image=image, model=model,
        prepared=inventory(prepared/'input'), profile=fingerprint(prepared/'profile.json'))
    write_json(prepared/'receipt.json', dict(identity=identity, sha256=digest(identity),
        revision='calibration-'+digest(identity)))
    receipt = read_calibration(prepared)
    verify_snapshot(prepared, receipt)
    for path in prepared.rglob('*'):
        if path.is_file():path.chmod(0o400)
        elif path.is_dir():path.chmod(0o700)
    # Remove only this preparation's disposable loading/work copies. Keep its
    # logs, frozen helper/JAR, provenance and reusable verified population.
    for name in ('loading-check', 'work'):
        shutil.rmtree(destination/name)
    print('Prepared private calibration inputs: '+str(prepared), flush=True)
    return prepared


def configuration(receipt, settings, variant='baseline'):
    settings = checked_settings(settings)
    if variant not in VARIANTS:raise ArtifactError('Select a known private calibration variant')
    if settings['population'] != receipt['identity']['population']:
        raise ArtifactError('Trial population differs from the independently prepared population')
    original = proof_configuration()
    value = original.editable_configuration()
    value.pop('sweep', None)
    value['experiment']['name'] = 'Private research workload calibration'
    value['model_release'] = 'private-research-'+receipt['identity']['model']['sha256'][:16]
    value['dataset_revision'] = receipt['revision']
    value['common'].update(population=settings['population'], start_year=2019, end_year=settings['end_year'])
    value['seed_plan']['repetitions'] = settings['repetitions']
    value['run_sets'] = [dict(id='baseline', name='Baseline')] if variant == 'baseline' else [dict(
        id='alternative', name='Lower saving rate (0.04)', model_args=dict(savingRate=0.04))]
    return normalise(value)


def arguments(config, receipt):
    frozen = config.as_dict()
    if (frozen['dataset_revision'] != receipt['revision'] or frozen['common'] != dict(country='UK',
            population=receipt['identity']['population'], start_year=2019, end_year=frozen['common']['end_year'])
            or not 2019 <= frozen['common']['end_year'] <= receipt['identity']['max_end_year']
            or len(frozen['run_sets']) != 1 or not 1 <= len(frozen['seed_plan']['seeds']) <= 10):
        raise ArtifactError('Configuration is outside this private calibration profile')
    run = frozen['run_sets'][0]
    if (run.get('common', frozen['common']) != frozen['common']
            or run.get('dataset_revision', frozen['dataset_revision']) != frozen['dataset_revision']
            or run['model_args']['useWeights'] or run['model_args']['ignoreTargetsAtPopulationLoad']):
        raise ArtifactError('Calibration must preserve prepared population settings and inputs')
    return dict(label=frozen['experiment']['name'], model_digest=receipt['identity']['source_image'],
        dataset_id=receipt['revision'], seed_plan=frozen['seed_plan']['seeds'],
        run_sets=[dict(id=run['id'], parameters=config.editable_configuration())])


class ResearchAdapter(ObservedAdapter):
    """Private adapter accepts this explicit receipt; service adapters still reject it."""
    def __init__(self, prepared, classes, settings, variant='baseline'):
        self.prepared = Path(prepared).absolute()
        self.receipt = read_calibration(self.prepared)
        self.image = self.receipt['identity']['source_image']
        self.settings = checked_settings(settings)
        configuration(self.receipt, self.settings, variant)
        self.variant = variant
        self.resource_policy = trial_policy(self.settings)
        self.classes = classes

    def _configuration(self, lease):
        spec = lease.specification
        if (spec['model_digest'] != self.image or spec['prepared_fingerprint'] != self.receipt['sha256']
                or spec['dataset_id'] != self.receipt['revision'] or len(spec['run_sets']) != 1
                or spec['run_sets'][0]['id'] != lease.configuration_id):
            raise ArtifactError('Queued calibration image, inputs or configuration changed')
        config = normalise(spec['run_sets'][0]['parameters'])
        translated = arguments(config, self.receipt)
        expected = configuration(self.receipt, self.settings, self.variant)
        if translated['seed_plan'] != spec['seeds'] or config.as_dict() != expected.as_dict():
            raise ArtifactError('Queued calibration settings or original seeds changed')
        if getattr(lease, 'heap_mib', None) is None or spec.get('resource_recovery') is None:
            raise ArtifactError('Calibration requires its explicit frozen heap and recovery policy')
        if any(lease.resources[k] < v for k, v in dict(cpu_millis=2000,
                memory_mib=self.settings['memory_mib'], storage_mib=self.settings['storage_mib']).items()):
            raise ArtifactError('Calibration allocation is below its recorded trial settings')
        return config

    def required_space(self, candidate):
        return max(workspace_required_bytes(self.receipt['identity']), candidate.resources['storage_mib']*MIB)

    def container_command(self, lease, request):
        # Normal receipt dispatch does not recognise private calibration. Verify
        # the complete snapshot here before reusing its existing staging runner.
        read_calibration(self.prepared)
        verify_snapshot(self.prepared, self.receipt)
        command = super().container_command(lease, request)
        runner = request/'run.sh'
        text = runner.read_text()
        token = '-cp /request/probe:/inputs/model.jar SimPathsResourceProbe'
        if text.count(token) != 1:
            raise ArtifactError('Passive calibration runner changed')
        runner.write_text(text.replace(token, f'-Dsimpaths.resource.sample.millis={sample_millis(self.settings)} '+token))
        return command


def status_model(prepared, receipt, settings, *, dataset_id=None):
    """Private fixture describes its dataset but exposes no calibration submission API."""
    from deploy.multirun.browser_model import BrowserModel
    release = configuration(receipt, settings).as_dict()['model_release']
    class StatusModel(BrowserModel):
        def describe_dataset(self, resolved):
            if (resolved['dataset_id'] != (dataset_id or receipt['revision'])
                    or resolved['prepared_fingerprint'] != receipt['sha256']):
                raise ArtifactError('Unknown private calibration dataset')
            return dict(id=dataset_id or receipt['revision'], name='Private public-training calibration',
                values=dict(start_year=2019, end_year=settings['end_year'], population=settings['population']),
                model_release=release, inputs=dict(fingerprint=receipt['sha256']), locked=['start_year', 'population'])

        def browser_configuration(self, dataset, form):
            raise ArtifactError('Calibration submissions are made only by the private test driver')
    return StatusModel({release:dict(jar=prepared/'model.jar', image=receipt['identity']['source_image'],
        defaults=prepared/'input', resource_policy=trial_policy(settings))})


def collect_observations(entry, timestamped):
    """Incremental timestamps preserve telemetry/build timers across log rotation.

    Re-read the last timestamp inclusively, deduplicate only that boundary, and
    fail on a lost sample. No raw CSV is opened and no full unbounded log is held.
    """
    cursor = entry.get('cursor')
    boundary = set(entry.get('boundary', []))
    rows = list(entry.get('rows', [])); builds = list(entry.get('builds', [])); lines = []
    latest = cursor; latest_lines = set(boundary)
    parsed = []
    for line in timestamped.splitlines():
        match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{9}Z)(?: (.*))?', line)
        if match is None:raise ArtifactError('Docker did not return timestamped calibration logs')
        stamp, content = match.groups()
        parsed.append((stamp, content or ''))
    # DockerCLI collects stdout and stderr separately. Their concatenated text
    # can have interleaved timestamps; restore time order before moving a cursor.
    for stamp, content in sorted(parsed, key=lambda item:item[0]):
        if cursor is not None and stamp < cursor:raise ArtifactError('Docker log cursor moved backwards')
        if stamp != latest:latest, latest_lines = stamp, set()
        latest_lines.add(content)
        if stamp == cursor and content in boundary:continue
        lines.append(content)
        if content.startswith(PREFIX):rows.append(json.loads(content[len(PREFIX):]))
        build = BUILD_TIME.fullmatch(content)
        if build:builds.append(float(build.group(1))*60)
    # Reuse the existing strict sequence/heap/cache/GC validation. Rotation
    # cannot silently turn a missing interval into a lower reported peak.
    if rows:telemetry('\n'.join(PREFIX+json.dumps(row) for row in rows))
    entry.update(cursor=latest, boundary=sorted(latest_lines), rows=rows, builds=builds,
        log='\n'.join(lines))


def measured_times(log, container, *, builds=None):
    if builds is None:
        builds = [float(match.group(1))*60 for line in log.splitlines() if (match := BUILD_TIME.fullmatch(line))]
    def instant(value):return datetime.fromisoformat(value.replace('Z', '+00:00'))
    lifetime = (instant(container['State']['FinishedAt']) - instant(container['State']['StartedAt'])).total_seconds()
    if lifetime <= 0 or not builds or any(value <= 0 for value in builds):
        raise ArtifactError('Missing model initialisation or container lifetime measurements')
    return dict(model_initialisation_seconds=builds, container_lifetime_seconds=round(lifetime, 3),
        timing_note='Initialisation is the model\'s own timer; container lifetime also includes staging, JVM startup and simulation.')


def observe(entry, lease, executor, record):
    if entry.get('retired'):return
    path = executor.workspace(lease)
    container = executor.docker.inspect(executor._name(lease))
    if container:
        if entry['container_id'] not in (None, container['Id']):raise AssertionError('Attempt container identity changed')
        entry['container_id'] = container['Id']; entry['container'] = container
        if container['State']['Running']:
            pid = container['State']['Pid']
            if pid not in entry['pids']:entry['pids'].append(pid)
        since = ('--since', entry['cursor']) if entry.get('cursor') else ()
        log = executor.docker.call('container', 'logs', '--timestamps', *since, container['Id'])
        collect_observations(entry, log)
    elif entry['container_id'] is not None:
        raise AssertionError('Calibration model disappeared before it could be measured')
    measured = storage_proof.sample(path)
    if measured:
        record['peak_workspace_bytes'] = max(record['peak_workspace_bytes'], measured['work_and_request_bytes'])


def save_attempt(executor, entry, destination):
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_json(destination/'telemetry.json', entry['rows'])
    for name in ('execution.log', 'exit.json', 'container-policy.json', 'diagnostics.log'):
        source = executor.workspace(entry['lease'])/name
        if source.is_file():shutil.copyfile(source, destination/name)


def retire_failed(queue, executor, known, entries, record, output):
    """Save evidence and reclaim settled failure scratch before admitting a retry."""
    from jasmine_web.batch.attempt_cleanup import AttemptCleanup
    from deploy.multirun.queue_adapter import result_deletion_targets
    with queue._connection() as c:
        rows = c.execute("SELECT id FROM attempts WHERE phase='finished' AND outcome<>'success'").fetchall()
    pending = [str(row['id']) for row in rows if str(row['id']) in known
        and not entries.get(str(row['id']), {}).get('retired')]
    if not pending:return
    for key in pending:
        lease = known[key]
        entry = entries.setdefault(key, dict(container_id=None, pids=[], rows=[]))
        entry['lease'] = lease
        observe(entry, lease, executor, record)
        save_attempt(executor, entry, output/'attempts'/key)
    # This production lifecycle requires confirmed termination and released
    # reservations. A retired failure must not consume another full input copy
    # while its replacement is waiting for physical disk space.
    AttemptCleanup(queue, executor, outputs=result_deletion_targets).retire()
    for key in pending:
        entry = entries[key]
        if entry['container_id'] is not None and executor.docker.inspect(entry['container_id']) is not None:
            raise AssertionError('Settled failed container was not removed before retry')
        entry['retired'] = True


def verify_completion(queue, owner, experiment, entries, adapter, executor, settings):
    job = queue.inspect(owner, experiment)['jobs'][0]
    if job['state'] != 'succeeded' or not 1 <= job['attempts'] == len(entries) <= settings['max_attempts']:
        raise AssertionError('Calibration did not succeed within its original attempt cap')
    frozen = next(iter(entries.values()))['lease'].specification
    successful = []
    for entry in entries.values():
        lease = entry['lease']
        with queue._connection() as c:
            attempt = c.execute('SELECT phase,outcome FROM attempts WHERE id=%s', (lease.attempt_id,)).fetchone()
            release = c.execute('SELECT released_at FROM reservations WHERE attempt_id=%s', (lease.attempt_id,)).fetchone()
            rows = list(c.execute('SELECT ordinal,expected_seed,actual_seed,output_fingerprint FROM repetitions '
                'WHERE attempt_id=%s ORDER BY ordinal', (lease.attempt_id,)))
        if lease.specification != frozen or attempt['phase'] != 'finished' or release['released_at'] is None:
            raise AssertionError('Original settings or settled reservation changed')
        entry['outcome'] = attempt['outcome']
        if attempt['outcome'] == 'success':
            validated = adapter.validate(lease, executor.workspace(lease)/'work')
            if (len(validated) != settings['repetitions'] or len(rows) != len(validated)
                    or [r['seed'] for r in validated] != frozen['seeds']
                    or any(r['ordinal'] != i or r['expected_seed'] != frozen['seeds'][i]
                        or r['actual_seed'] != frozen['seeds'][i]
                        or r['output_fingerprint'] != validated[i]['fingerprint'] for i, r in enumerate(rows))):
                raise AssertionError('Completed annual output, original seed plan or hashes differ')
            successful.append((lease, validated))
        elif attempt['outcome'] not in ('heap_limit', 'memory_limit', 'storage_limit', 'transient'):
            raise AssertionError('Unexpected calibration failure was retried')
        elif any(r['actual_seed'] is not None for r in rows):
            raise AssertionError('Failed calibration attempt published partial output')
    with queue._connection() as c:
        if c.execute('SELECT count(*) AS n FROM reservations WHERE released_at IS NULL').fetchone()['n']:
            raise AssertionError('Finished calibration still holds capacity')
    if len(successful) != 1:raise AssertionError('Expected one successful original configuration')
    return successful[0], job


def settle_attempts(executor, leases):
    """Reacquire the dispatcher guard after worker shutdown; retain uncertainty."""
    failures = []
    try:
        with executor.exclusive():
            for lease in leases:
                try:
                    if not executor.workspace(lease).exists():continue
                    observed = executor.inspect(lease)
                    if observed['state'] != 'stopped':
                        executor.stop(lease, 'cancelled')
                        deadline = time.monotonic()+30
                        observed = executor.inspect(lease)
                        while observed['state'] == 'running' and time.monotonic() < deadline:
                            time.sleep(.2)
                            observed = executor.inspect(lease)
                    if observed['state'] != 'stopped':
                        raise ArtifactError('Calibration attempt termination is unconfirmed')
                    executor.cleanup(lease)
                except Exception as error:
                    failures.append(dict(attempt=str(lease.attempt_id),
                        error_type=type(error).__name__, message=str(error)[:500]))
    except Exception as error:
        failures.append(dict(stage='dispatcher-lock', error_type=type(error).__name__, message=str(error)[:500]))
    return failures


def finish_cleanup(folder, prepared, *, docker=None):
    """Verify a completed retained run and finish scratch cleanup, never rerun it.

    The original reports remain unchanged. The native driver's database removal
    and the earlier reservation checks are recorded as original-run evidence;
    this follow-up verifies files and fresh Docker absence under the host lock.
    """
    from jasmine_web.batch.docker_executor import DockerExecutor
    folder = Path(folder).resolve(strict=True)
    proof = folder/'model-proof'
    report_path = proof/'report.json'
    report_hash = fingerprint(report_path)
    record = json.loads(report_path.read_text())
    outer = json.loads((folder/'report.json').read_text())
    if (record.get('failure') or not all(record.get(key) is True for key in
            ('source_unchanged', 'input_copies_reclaimed', 'reservations_released'))
            or outer.get('cleanup') is not True or not record.get('attempts')
            or sum(a['outcome'] == 'success' for a in record['attempts']) != 1):
        raise ArtifactError('Finish cleanup only after verified completion and native database removal')
    settings = checked_settings(record['settings'])
    prepared = Path(prepared).resolve(strict=True)
    receipt = read_calibration(prepared)
    config = configuration(receipt, settings, record.get('variant', 'baseline'))
    configuration_id = config.as_dict()['run_sets'][0]['id']
    if (record['prepared_sha256'] != receipt['sha256'] or record['image'] != receipt['identity']['source_image']
            or record['model'] != receipt['identity']['model']
            or json.loads((proof/'configuration.json').read_text()) != config.as_dict()):
        raise ArtifactError('Retained model, inputs or original configuration changed')
    work = Path(record['retained_workspace']).absolute()
    retained = proof/'retained-output'
    if (work.parent != folder or not re.fullmatch(r'simpaths-research-[a-z0-9_]+', work.name)
            or work.is_symlink() or not work.is_dir()
            or work.stat().st_uid != os.getuid() or work.stat().st_mode & 0o077
            or record['output_location'] != str(retained)):
        raise ArtifactError('Retained scratch must be the private workspace inside this evidence directory')
    executor = DockerExecutor(work/'execution', approved_images=[record['image']], input_roots=[prepared], docker=docker)
    attempts = {a['id']:a for a in record['attempts']}
    if len(attempts) != len(record['attempts']) or {p.name for p in executor.root.glob('batch-*')} != {'batch-'+k for k in attempts}:
        raise ArtifactError('Retained attempt workspaces differ from the measured attempts')
    removed = []
    with executor.exclusive():
        if not executor.docker.call('info', '--format', '{{.ID}}'):
            raise ArtifactError('Docker daemon identity is unconfirmed')
        for key, attempt in attempts.items():
            path = executor.root/('batch-'+key)
            identity = json.loads((path/'identity.json').read_text())
            exit_record = json.loads((path/'exit.json').read_text())
            container = json.loads((path/'container.json').read_text())
            removal = json.loads((path/'removed.json').read_text())
            policy = json.loads((path/'container-policy.json').read_text())
            identifier = container.get('id')
            if (not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{64}', identifier)
                    or removal != container or identity['attempt_id'] != key
                    or identity['execution_key'] != path.name or identity['configuration_id'] != configuration_id
                    or exit_record.get('state') != 'stopped' or exit_record.get('container_id') != identifier
                    or exit_record.get('outcome') != attempt['outcome'] or policy['image'] != record['image']
                    or executor.docker.inspect(identifier) is not None
                    or executor.docker.inspect('jasmine-'+path.name) is not None):
                raise ArtifactError('Original attempt identity or fresh container removal is unconfirmed')
            removed.append(identifier)
        verify_snapshot(prepared, receipt)
        manifest = json.loads((proof/'output-manifest.json').read_text())
        verify(retained, manifest)
        if sum(v['bytes'] for v in manifest.values()) != record['output_bytes']:
            raise ArtifactError('Retained output size changed')
        from deploy.multirun.queue_adapter import validate_outputs
        if validate_outputs(retained, config, configuration_id) != record['verified_repetitions']:
            raise ArtifactError('Retained annual output, original seeds or repetition hashes changed')
        if fingerprint(report_path) != report_hash or any(executor.docker.inspect(identifier) is not None for identifier in removed):
            raise ArtifactError('Recorded completion or container removal changed during verification')
        journal = proof/'cleanup-journals'; journal.mkdir(mode=0o700)
        for key in attempts:
            source = executor.root/('batch-'+key)
            destination = journal/key; destination.mkdir(mode=0o700)
            for name in ('identity.json', 'container.json', 'removed.json', 'exit.json', 'container-policy.json'):
                fingerprint(source/name, destination/name)
        # Only this matched private scratch tree is disposable. Scientific output
        # lives outside it and has just passed the original annual/hash checks.
        shutil.rmtree(work)
    result = dict(passed=True, cleanup=not work.exists(), simulation_repeated=False,
        original_report=report_hash, original_native_database_removed=True,
        reservation_release_evidence='Verified by the original native run before cleanup',
        annual_years=list(range(2019, settings['end_year']+1)), container_removals=removed,
        prepared_sha256=receipt['sha256'], configuration_sha256=fingerprint(proof/'configuration.json'),
        output_manifest=fingerprint(proof/'output-manifest.json'), output_bytes=record['output_bytes'],
        verified_repetitions=record['verified_repetitions'], output_location=str(retained), journals=inventory(journal))
    atomic_json(proof/'cleanup-verification.json', result)
    print('PASS: retained annual output, original seeds/hashes and fresh container removal verified; scratch reclaimed without rerunning the model', flush=True)
    print('PASSED: '+str(proof/'cleanup-verification.json'), flush=True)
    return result


def execute(output):
    from jasmine_web.batch.docker_executor import DockerExecutor
    from jasmine_web.batch.policy import Policy, Resources, RuntimeAllowance
    from jasmine_web.batch.resource_recovery import RecoveryPolicy
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.worker import Worker
    from psycopg import sql
    from playwright.sync_api import expect

    value = json.loads(os.environ[ENV])
    settings = checked_settings(value['settings'])
    variant = value.get('variant', 'baseline')
    prepared = Path(value['prepared']).resolve(strict=True)
    receipt = read_calibration(prepared)
    verify_snapshot(prepared, receipt)
    output.mkdir(mode=0o700, parents=True); write_attribution(output)
    preflight(settings, output)
    work = Path(tempfile.mkdtemp(prefix='simpaths-research-', dir=output.parent))
    classes = work/'classes'; classes.mkdir()
    queue = Queue(os.environ['JASMINE_BATCH_TEST_DSN'], 'research-calibration', schema='research_'+uuid4().hex)
    executor = DockerExecutor(work/'execution', approved_images=[receipt['identity']['source_image']], input_roots=[prepared])
    adapter = ResearchAdapter(prepared, classes, settings, variant)
    worker = Worker(queue, executor, adapter, 'private-research-calibration')
    record = dict(passed=False, cleanup=False, production_acceptance=False, defaults_changed=False,
        settings=settings, variant=variant, start_year=2019, annual_years=list(range(2019, settings['end_year']+1)),
        prepared_sha256=receipt['sha256'], image=adapter.image, model=receipt['identity']['model'],
        sample_interval_ms=sample_millis(settings), enforcement='application-monitoring',
        measurement='Sampled JVM/shared monitor peaks; working RAM excludes inactive file cache. No kernel workspace quota.',
        source_sha256={str(p.relative_to(ROOT)):fingerprint(p)['sha256'] for p in (Path(__file__), PROBE)},
        peak_workspace_bytes=0, attempts=[], status_requests=[])
    known = {}; entries = {}; migrated = False; clean = True; stage = 'preflight'
    worker.before_claim = lambda:retire_failed(queue, executor, known, entries, record, output)
    try:
        compile_probe(prepared, classes, output/'probe-compile.log')
        record['probe_classes'] = inventory(classes)
        queue.migrate(); migrated = True
        seconds = attempt_seconds(settings)
        queue.create_pool(Resources(2000, settings['max_memory_mib'], settings['storage_mib']), policy=Policy(
            per_user_active=1, max_attempts=settings['max_attempts'], attempt_seconds=seconds,
            total_seconds=seconds*settings['max_attempts'], retry_delay_seconds=1))
        queue.register_dataset(receipt['revision'], receipt['sha256'])
        model = status_model(prepared, receipt, settings)
        with worker.open(), browser_session(queue, prepared, receipt, work, output, model=model) as (owner, page, foreign, origin, errors):
            queue.grant_dataset(owner, receipt['revision'])
            args = arguments(configuration(receipt, settings, variant), receipt)
            recovery = RecoveryPolicy(max_memory_mib=settings['max_memory_mib'],
                max_heap_mib=settings['max_memory_mib']-(settings['memory_mib']-settings['heap_mib']),
                storage_multiplier=1, check_seconds=5)
            experiment = queue.submit(owner, 'private-research', **args,
                resources=Resources(2000, settings['memory_mib'], settings['storage_mib']),
                runtime_allowance=RuntimeAllowance(settings['setup_seconds'], settings['repetition_seconds'], settings['max_attempts']),
                resource_recovery=recovery, heap_limits={r['id']:settings['heap_mib'] for r in args['run_sets']})
            write_json(output/'configuration.json', configuration(receipt, settings, variant).as_dict())
            record['recovery_policy'] = asdict(recovery)
            print(f"Measuring {variant}: {settings['population']:,} people, 2019–{settings['end_year']}, "
                f"{settings['repetitions']} repetition(s); {settings['heap_mib']/1024:g} GiB heap, "
                f"{settings['memory_mib']/1024:g}–{settings['max_memory_mib']/1024:g} GiB container", flush=True)
            stage = 'full-simulation'; started = time.monotonic(); next_sample = started; next_progress = started
            page.get_by_role('button', name='Refresh', exact=True).click()
            while True:
                for key, lease in worker.leases.items():known[key] = queue.refresh_resources(lease)
                worker.tick(); known.update(worker.leases)
                for key, lease in known.items():
                    entries.setdefault(key, dict(container_id=None, pids=[], rows=[]))['lease'] = lease
                job = queue.inspect(owner, experiment)['jobs'][0]; now = time.monotonic()
                if now >= next_sample or job['state'] == 'succeeded':
                    for entry in entries.values():
                        observe(entry, entry['lease'], executor, record)
                    request_start = time.monotonic()
                    response = page.request.get(origin+'/api/experiments/'+experiment, timeout=15000)
                    record['status_requests'].append(dict(status=response.status, ms=round((time.monotonic()-request_start)*1000, 3)))
                    if response.status != 200:raise AssertionError('Owner status request failed during real model work')
                    next_sample = now+10
                if job['state'] == 'succeeded':break
                if job['state'] in ('review', 'cancelled', 'expired', 'blocked'):
                    record.update(job_state=job['state'], history=job['history'])
                    raise AssertionError('Calibration stopped before verified completion; inspect diagnostics')
                if now-started > proof_timeout(settings)-600:raise TimeoutError('Private calibration exceeded its recorded proof deadline')
                if now >= next_progress:
                    peak = max((r['working_set_bytes'] for e in entries.values() for r in e['rows']), default=0)
                    print(f"  {job['state']}; attempts {job['attempts']}; sampled working RAM {peak/GIB:.2f} GiB; "
                        f"workspace {record['peak_workspace_bytes']/GIB:.2f} GiB", flush=True)
                    atomic_json(output/'progress.json', json_record(record))
                    next_progress = now+30
                page.wait_for_timeout(500)
            stage = 'completion-verification'
            (last, validated), job = verify_completion(queue, owner, experiment, entries, adapter, executor, settings)
            if foreign.request.get(origin+'/api/experiments/'+experiment).status != 403:
                raise AssertionError('Foreign owner can read this calibration')
            page.get_by_role('button', name='Refresh', exact=True).click()
            expect(page.locator('[data-job-id="'+job['id']+'"]')).to_contain_text('completed', timeout=15000)
            record['resource_events'] = job.get('resource_recovery', [])
            for key, entry in entries.items():
                lease = entry['lease']; directory = output/'attempts'/key
                measured = summary(entry['rows'], lease.heap_mib)
                if len(entry['pids']) != 1:raise AssertionError('Original JVM process changed within an attempt')
                detail = dict(id=key, outcome=entry['outcome'], heap_mib=lease.heap_mib, **measured)
                if entry['outcome'] == 'success':
                    detail.update(measured_times(entry['log'], entry['container'], builds=entry.get('builds')))
                    if len(detail['model_initialisation_seconds']) != settings['repetitions']:
                        raise AssertionError('Missing model build timer for a completed repetition')
                record['attempts'].append(detail)
                save_attempt(executor, entry, directory)
            stage = 'settled-cleanup'
            path = executor.workspace(last)
            record['runs_before_cleanup'] = storage_proof.run_summary(path)
            retire_attempts(queue, executor)
            if any(executor.docker.inspect(e['container_id']) is not None for e in entries.values()):
                raise AssertionError('Finished calibration container was not removed')
            if (path/'work/input').exists() or any(r['copied_input_bytes'] for r in storage_proof.run_summary(path)):
                raise AssertionError('Large completed input copies were not reclaimed')
            if adapter.validate(last, path/'work') != validated:
                raise AssertionError('Cleanup changed verified options or output')
            # Move, never duplicate, the verified output for the subsequent
            # server aggregation calibration. No service endpoint publishes it.
            manifest = inventory(path/'work/output')
            (path/'work/output').rename(output/'retained-output')
            verify(output/'retained-output', manifest)
            write_json(output/'output-manifest.json', manifest)
            record.update(output_bytes=sum(v['bytes'] for v in manifest.values()),
                verified_repetitions=validated, output_location=str(output/'retained-output'),
                input_copies_reclaimed=True, reservations_released=True, browser_errors=errors,
                elapsed_seconds=round(time.monotonic()-started, 3))
        verify_snapshot(prepared, receipt)
        if read_calibration(prepared) != receipt:raise AssertionError('Private prepared receipt changed')
        record.update(passed=True, source_unchanged=True)
    except BaseException as error:
        record['failure'] = dict(stage=stage, error_type=type(error).__name__, message=str(error)[:500])
        raise
    finally:
        known.update(worker.leases)
        failures = settle_attempts(executor, known.values())
        if failures:
            clean = False
            record['cleanup_failures'] = failures
        for path in executor.root.glob('batch-*'):
            directory = output/'diagnostics'/path.name; directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name in ('execution.log', 'exit.json', 'container-policy.json'):
                if (path/name).is_file():shutil.copyfile(path/name, directory/name)
        if clean:
            try:
                if migrated:
                    with queue._connection() as c:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(queue.schema)))
                shutil.rmtree(work)
            except Exception as error:
                clean = False
                record['cleanup_failure'] = dict(error_type=type(error).__name__, message=str(error)[:500])
        if not clean and work.exists():record['retained_workspace'] = str(work)
        record['cleanup'] = clean and not work.exists()
        if not record['cleanup']:record['passed'] = False
        write_json(output/'report.json', json_record(record))
    if not record['cleanup']:raise AssertionError('Research calibration cleanup is unconfirmed')
    print(f"PASS: {settings['population']:,} people through {settings['end_year']}; "
        f"all {len(record['annual_years'])} annual years, original seeds/settings, hashes and released capacity verified", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=frontend_path())
    parser.add_argument('--prepared', type=Path, help='Existing private research package; normal service datasets are rejected')
    parser.add_argument('--prepare', type=Path, help='New persistent preparation directory, outside source trees')
    parser.add_argument('--image', default='simpaths-interactive:uk-user-data', help='Already installed Java runtime')
    parser.add_argument('--population', type=int, default=100000)
    parser.add_argument('--end-year', type=int, default=2070)
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--variant', choices=VARIANTS, default='baseline',
        help='Private calibration configuration; lower-saving-rate changes only savingRate to 0.04')
    parser.add_argument('--heap-mib', type=int, default=4096)
    parser.add_argument('--memory-mib', type=int, default=5120)
    parser.add_argument('--max-memory-mib', type=int, default=7168)
    parser.add_argument('--storage-mib', type=int, default=12288)
    parser.add_argument('--setup-seconds', type=int, default=900)
    parser.add_argument('--repetition-seconds', type=int, default=14400)
    parser.add_argument('--max-attempts', type=int, default=3)
    parser.add_argument('--preparation-timeout', type=int, default=7200)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--finish-cleanup', type=Path, help='Verify a completed evidence directory and reclaim its retained scratch without another model run')
    parser.add_argument('--dry-run', action='store_true', help='Show the bounded plan without Docker, preparation or simulation')
    parser.add_argument('--execute-proof', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.execute_proof:
        if not os.environ.get('JASMINE_BATCH_TEST_DSN') or not os.environ.get(ENV):parser.error('Use the disposable PostgreSQL driver')
        execute(args.output); print('PASSED: '+str(args.output/'report.json'), flush=True); return 0
    if args.finish_cleanup:
        if not args.prepared or args.prepare or args.output or args.dry_run:
            parser.error('--finish-cleanup requires --prepared and excludes --prepare, --output and --dry-run')
        finish_cleanup(args.finish_cleanup, args.prepared); return 0
    settings = checked_settings({name:getattr(args, name) for name in FIELDS})
    if bool(args.prepared) == bool(args.prepare):parser.error('Supply exactly one of --prepare or --prepared')
    if not 60 <= args.preparation_timeout <= 14400:parser.error('Preparation timeout must be 60–14400 seconds')
    output = (args.output or Path(tempfile.gettempdir())/('research-calibration-'+datetime.now().strftime('%Y%m%d-%H%M%S'))).absolute()
    if output.exists() or output.is_symlink():parser.error('Choose a new evidence directory')
    load_tool('web-quickstart/prepare_profile.py').validate_destination(output, args.frontend, ROOT/'input')
    if args.dry_run:
        print(json.dumps(dict(settings=settings, variant=args.variant, sample_interval_ms=sample_millis(settings),
            proof_timeout_seconds=proof_timeout(settings), output=str(output),
            preparation=str(args.prepare) if args.prepare else None,
            prepared=str(args.prepared) if args.prepared else None,
            stages=['public-input preparation and independent reuse check' if args.prepare else 'verify private prepared inputs',
                'one sequential configuration in disposable PostgreSQL', 'measure and verify all annual output',
                'reclaim input copies and retain verified output for aggregation'], defaults_changed=False), indent=2))
        return 0
    # Both run scratch and evidence stay on the explicitly chosen filesystem.
    # TMPDIR also contains the disposable driver's environment and PostgreSQL logs.
    preflight(settings, output.parent, preparing=bool(args.prepare))
    image = installed_image(args.image)
    if args.prepare:
        prepared = prepare_calibration(args.prepare.expanduser().absolute(), settings['population'], image,
            timeout=args.preparation_timeout)
    else:prepared = args.prepared.expanduser().resolve(strict=True)
    receipt = read_calibration(prepared)
    if receipt['identity']['source_image'] != image:raise ArtifactError('Use the same pinned runtime image as preparation')
    verify_snapshot(prepared, receipt); configuration(receipt, settings, args.variant)
    command = [sys.executable, str(args.frontend.resolve(strict=True)/'scripts/test_batch_queue.py'),
        '--test-pattern', 'test_worker.py', 'test_resource_recovery.py', '--proof-script', str(Path(__file__).resolve()),
        '--proof-timeout-seconds', str(proof_timeout(settings)), '--proof-requirements',
        str(ROOT/'deploy/multirun/requirements.txt'), str(ROOT/'deploy/acceptance/requirements.txt'),
        '--output', str(output)]
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    preflight(settings, output.parent)
    return subprocess.call(command, env=dict(os.environ, JASMINE_WEB_REPO=str(args.frontend), TMPDIR=str(output.parent),
        **{ENV:json.dumps(dict(prepared=str(prepared), settings=settings, variant=args.variant))}))


if __name__ == '__main__':raise SystemExit(main())
