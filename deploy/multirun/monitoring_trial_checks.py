"""(C) Copyright 2026, by Ross Richardson

Matched scientific launch and bounded evidence checks for monitoring-cost trials.
These are private laptop fixtures, not production admission or model changes.
@author ross richardson
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat

from .artifacts import ArtifactError, digest, fingerprint, write_json
from .container_adapter import SimPathsContainerAdapter
from . import research_calibration as calibration

MIB = 1024**2


def scientific_plan(receipt, end_year, *, population=None, job_seconds=7200):
    actual = receipt['identity']['population']
    if population is not None and (type(population) is not int or population != actual):
        raise ArtifactError('Requested population differs from the prepared calibration')
    if type(job_seconds) is not int or not 960 <= job_seconds <= 15300:
        raise ArtifactError('Scientific job timeout must be in 960..15300 seconds')
    return calibration.checked_settings(dict(population=actual, end_year=end_year,
        repetitions=1, cpu_millis=2000, heap_mib=4096, memory_mib=5120,
        max_memory_mib=5120, storage_mib=12288, setup_seconds=900,
        repetition_seconds=job_seconds-900, max_attempts=1))


class ScientificAdapter(calibration.ResearchAdapter):
    """Keep the frozen research profile, using the ordinary production runner."""
    def __init__(self, prepared, plan):
        super().__init__(prepared, None, plan)
        if self.settings != scientific_plan(self.receipt, self.settings['end_year'],
                job_seconds=self.settings['setup_seconds']+self.settings['repetition_seconds']):
            raise ArtifactError('Scientific monitoring trial plan must keep its fixed limits and one repetition')
        from jasmine_web.batch.resource_recovery import RecoveryPolicy
        self.recovery = RecoveryPolicy(max_memory_mib=5120, max_heap_mib=4096, storage_multiplier=1)
        self.heaps = {'baseline':4096}
        self.resources = dict(cpu_millis=2000, memory_mib=5120, storage_mib=12288)
        self.runner = Path(__file__).with_name('container_run.sh')
        self.runner_hash = fingerprint(self.runner)
        config = calibration.configuration(self.receipt, self.settings)
        self.arguments = calibration.arguments(config, self.receipt)
        self.contract = dict(model=self.receipt['identity']['model'], image=self.image,
            prepared_fingerprint=self.receipt['sha256'], configuration=config.as_dict(),
            settings=self.settings, resources=self.resources, heap_mib=4096,
            jvm_processor_count=2, runner=self.runner_hash, recovery=asdict(self.recovery))
        self.contract_fingerprint = digest(self.contract)
        self.verify_sources()

    def verify_sources(self):
        if (calibration.read_calibration(self.prepared) != self.receipt
                or fingerprint(self.runner) != self.runner_hash
                or digest(self.contract) != self.contract_fingerprint):
            raise ArtifactError('Scientific monitoring trial sources or contract changed')
        calibration.verify_snapshot(self.prepared, self.receipt)

    def _configuration(self, lease):
        config = super()._configuration(lease)
        expected_recovery = self.recovery.describe(lease.specification, self.resources, self.heaps)
        if (lease.resources != self.resources or lease.heap_mib != 4096
                or (getattr(lease, 'current_resources', None) or lease.resources) != self.resources
                or lease.specification['resource_recovery'] != expected_recovery
                or lease.specification.get('adaptive_cpu') is not None):
            raise ArtifactError('Scientific monitoring trials require identical fixed allocations')
        return config

    def container_command(self, lease, request):
        self.verify_sources()
        # Bypass the legacy acceptance/profiling probes in ResearchAdapter and
        # ObservedAdapter. Only the selected production observer may be added.
        command = SimPathsContainerAdapter.container_command(self, lease, request)
        if fingerprint(request/'run.sh') != self.runner_hash:
            raise ArtifactError('Scientific trial did not stage the ordinary runner')
        return command


def container_launch_record(container):
    """Select launch/limit evidence without copying container environment values."""
    config, host, state = container['Config'], container['HostConfig'], container['State']
    return dict(image=container.get('Image'), requested_image=config.get('Image'),
        entrypoint=config.get('Entrypoint'), command=config.get('Cmd'),
        cpu_millis=host['NanoCpus']//10**6, memory_bytes=host['Memory'],
        started_at=state.get('StartedAt'), finished_at=state.get('FinishedAt'),
        exit_code=state.get('ExitCode'), oom_killed=state.get('OOMKilled'))


def check_execution(mode, path, container, resources, image, sample_seconds, *, scientific=False, deadline=None):
    """Fail a condition that silently falls back to another monitoring mode."""
    from jasmine_web.batch.performance_trace import java_samples
    launch = json.loads((path/'performance-launch.json').read_text())
    if (launch.get('java_mode') != ('enabled' if mode == 'full' else 'off')
            or launch.get('sample_seconds') != sample_seconds):
        raise AssertionError('Actual Java monitoring launch differs from the requested condition')
    host = container['HostConfig']
    if (container['Config']['Image'] != image or host['NanoCpus'] != resources['cpu_millis']*10**6
            or host['Memory'] != resources['memory_mib']*MIB
            or container['State']['ExitCode'] != 0 or container['State'].get('OOMKilled')):
        raise AssertionError('Actual Docker image, limits or exit differs from the matched trial')
    if scientific:
        runner = '/request/run-reviewed.sh' if mode == 'full' else '/request/run.sh'
        if type(deadline) is not int or deadline<=0:
            raise AssertionError('Scientific trial needs its frozen execution deadline')
        # DockerExecutor adds its ordinary absolute-deadline supervisor around
        # the adapter command. Check the complete launch, including that bound;
        # the adapter's three arguments alone are not Docker's Config.Cmd.
        expected = ['/jasmine-deadline.sh', str(deadline), '/bin/sh', runner, '4096m']
        if (container['Config'].get('Entrypoint') != ['/bin/sh']
                or container['Config'].get('Cmd') != expected):
            raise AssertionError('Scientific deadline wrapper, command or heap changed')
    sidecar = path/'request/performance-observer.jar'
    if sidecar.exists() and fingerprint(sidecar)['sha256'] != launch.get('java_observer_fingerprint'):
        raise AssertionError('Observer sidecar differs from its launch receipt')
    records, offsets = [], {}
    for _ in range(32):
        batch = java_samples(path/'work', offsets)
        records.extend(batch)
        if not batch:break
    files = [path/'work'/name for name in ('.performance-java.previous', '.performance-java.jsonl')]
    size = sum(fingerprint(file)['bytes'] for file in files if file.exists() or file.is_symlink())
    result = dict(launch=launch, java_log_bytes=size, java_samples=len(records))
    if mode != 'full':
        if records or any(f.exists() for f in files) or sidecar.exists() or (path/'request/run-reviewed.sh').exists():
            raise AssertionError('A Java-disabled condition unexpectedly ran the observer')
        result['java_coverage'] = 'absent'
        return result
    if (not records or [r['sequence'] for r in records] != list(range(len(records)))
            or records[-1]['phase'] != 'finished' or any(r.get('dropped', 0) for r in records)):
        raise AssertionError('Detailed Java evidence is incomplete or records were lost')
    result.update(java_coverage='full', phases=sorted({r['phase'] for r in records}),
        annual_years=sorted({r['year'] for r in records if r['phase']=='annual'}))
    for key in ('heap_used_bytes', 'heap_committed_bytes', 'nonheap_used_bytes', 'population'):
        values = [r[key] for r in records if r.get(key) is not None]
        result['sampled_peak_'+key] = max(values) if values else None
    for key in ('gc_ms', 'gc_count', 'process_cpu_ns'):
        first, last = records[0].get(key), records[-1].get(key)
        result[key+'_delta'] = last-first if first is not None and last is not None and last>=first else None
    if scientific:
        config = json.loads((path/'scientific-configuration.json').read_text())
        common = config['common']
        if (result['annual_years'] != list(range(common['start_year'], common['end_year']+1))
                or 'between_repetitions' not in result['phases']
                or not any(r['phase']=='annual' and (r.get('population') or 0)>0 for r in records)
                or any(r.get('heap_max_bytes') != 4096*MIB for r in records)):
            raise AssertionError('Scientific annual, population, repetition or heap observations are missing')
    return result


def settled_measurements(queue, lease, job, mode, resources, *, scientific=False):
    """Verify completion/release and retain the actual external sampling window."""
    if (job['attempts'] != 1 or len(job['history']) != 1
            or job['history'][0]['id'] != lease.attempt_id
            or job['history'][0]['outcome'] != 'success'):
        raise AssertionError('Matched cost trials must complete in one attempt')
    with queue._connection() as c:
        attempt = c.execute('SELECT initial_resources,current_resources,heap_mib FROM attempts WHERE id=%s',
                            (lease.attempt_id,)).fetchone()
        reservation = c.execute('SELECT released_at FROM reservations WHERE attempt_id=%s',
                                (lease.attempt_id,)).fetchone()
        changes = c.execute('SELECT count(*) AS n FROM resource_changes WHERE attempt_id=%s',
                            (lease.attempt_id,)).fetchone()['n']
        sampled = c.execute('''SELECT COALESCE(sum(samples),0) AS samples,sum(observed_seconds) AS observed_seconds,
            max(peak_memory_bytes) AS sampled_peak_memory_bytes,
            max(peak_working_memory_bytes) AS sampled_peak_working_memory_bytes
            FROM performance_reviews WHERE pool_id=%s AND attempt_id=%s''',
            (queue.pool_id,lease.attempt_id)).fetchone()
    if (not reservation or reservation['released_at'] is None or changes
            or attempt['initial_resources'] != resources
            or (attempt['current_resources'] or attempt['initial_resources']) != resources
            or scientific and attempt['heap_mib'] != 4096):
        raise AssertionError('Trial allocation changed or its reservation was not released')
    if ((mode == 'off' and sampled['samples'])
            or (scientific and mode != 'off' and not sampled['samples'])):
        raise AssertionError('Actual external sampling differs from the requested condition')
    return dict(sampled, samples=int(sampled['samples']),
                observed_seconds=float(sampled['observed_seconds']) if sampled['observed_seconds'] is not None else None)


def scientific_outputs(adapter, lease, work):
    """Validate every requested year and seed; retain checksums, not raw CSVs."""
    from .queue_adapter import validate_outputs
    config = adapter._configuration(lease)
    receipts = validate_outputs(work/'output', config, lease.configuration_id, include_files=True)
    return [dict(seed=r['seed'], fingerprint=r['fingerprint'],
        files=[{k:file[k] for k in ('name', 'bytes', 'sha256')} for file in r['files']],
        metadata=[{k:file[k] for k in ('name', 'bytes', 'sha256')} for file in r['metadata']])
        for r in receipts]


def output_comparison(reference, observed):
    left = {(r['seed'], f['name']):f for r in reference for f in r['files']}
    right = {(r['seed'], f['name']):f for r in observed for f in r['files']}
    changed = [dict(seed=seed, name=name) for seed, name in sorted(left.keys() | right.keys())
               if left.get((seed, name)) != right.get((seed, name))]
    return dict(csv_checksums_equal=not changed, changed_files=changed)


def retain_diagnostics(destination, workspace):
    """Keep a small allowlist, including failed launches, before bulk cleanup."""
    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
    names = ('performance-launch.json', 'scientific-configuration.json', 'scientific-outputs.json', 'execution.log',
        'exit.json', 'identity.json', 'container.json', 'container-policy.json', 'container-launch.json',
        'request/run.yml', 'request/run.sh',
        'request/run-reviewed.sh', 'request/inputs.sha256', 'request/input-files.txt',
        'work/.performance-java.previous', 'work/.performance-java.jsonl')
    saved = {}
    for name in names:
        source, target = workspace/name, destination/name
        if target.exists():continue
        try:
            if any(p.is_symlink() for p in (source.parent, *source.parent.parents)):
                raise ValueError('Unsafe diagnostic ancestor')
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:continue
        except (OSError, ValueError) as error:
            saved[name] = dict(error_type=type(error).__name__);continue
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                saved[name] = dict(error_type='UnsafeFile');continue
            limit = MIB if name.startswith('work/.performance-java.') else 128*1024
            data = stream.read(limit)
            truncated = info.st_size > limit
            if truncated:
                stream.seek(max(0, info.st_size-limit//2))
                data = data[:limit//2]+b'\n[diagnostic middle omitted]\n'+stream.read(limit//2)
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with target.open('xb') as out:out.write(data)
        target.chmod(0o600)
        saved[name] = dict(original_bytes=info.st_size, truncated=truncated, **fingerprint(target))
    # Append recovery diagnostics without overwriting earlier, complete evidence.
    manifest = destination/'diagnostics.json'
    previous = json.loads(manifest.read_text()) if manifest.exists() else {}
    manifest.unlink(missing_ok=True)
    write_json(manifest, {**previous, **saved})
