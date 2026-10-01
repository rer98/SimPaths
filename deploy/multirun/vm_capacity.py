"""(C) Copyright 2026, by Ross Richardson

Read-only measurements of selected MultiRun experiments on their actual Docker host.
No submissions, cancellations, input copying, database migration or broad cleanup.
@author ross richardson
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import UUID

from .vm_config import load_config
from .vm_web import database_dsn


def selected_snapshot(queue, experiments):
    with queue._connection() as c:
        c.execute('SET TRANSACTION READ ONLY')
        rows = c.execute('SELECT id,specification,policy FROM experiments WHERE pool_id=%s AND id=ANY(%s::uuid[])',
                         (queue.pool_id, experiments)).fetchall()
        if len(rows) != len(experiments):
            raise ValueError('Every experiment must belong to this service pool')
        if any(row['specification'].get('operation') == 'prepare' for row in rows):
            raise ValueError('Select simulation experiments; preparations need a separate measurement')
        jobs = c.execute('''SELECT j.id,j.experiment_id,j.configuration_id,j.state,j.dataset_id,
            a.id AS attempt_id,a.execution_key,a.phase,a.outcome,a.started_at,a.finished_at
            FROM jobs j LEFT JOIN attempts a ON a.job_id=j.id
            WHERE j.pool_id=%s AND j.experiment_id=ANY(%s::uuid[]) ORDER BY j.id,a.number''',
                         (queue.pool_id, experiments)).fetchall()
        datasets = c.execute('''SELECT DISTINCT p.dataset_id,p.location FROM prepared_locations p
            JOIN jobs j ON j.pool_id=p.pool_id AND j.dataset_id=p.dataset_id
            WHERE j.pool_id=%s AND j.experiment_id=ANY(%s::uuid[])''',
                             (queue.pool_id, experiments)).fetchall()
        processors = c.execute('SELECT cpu_millis,memory_mib,storage_mib FROM processing_reservations WHERE pool_id=%s',
                               (queue.pool_id,)).fetchall()
    return rows, jobs, datasets, processors


def sample(queue, options, experiments, docker):
    from jasmine_web.batch.storage import measured_bytes
    rows, jobs, datasets, processors = selected_snapshot(queue, experiments)
    attempts = []
    for row in jobs:
        if row['attempt_id'] is None:
            continue
        attempts.append(dict(job=str(row['id']), attempt=str(row['attempt_id']),
            state=row['state'], phase=row['phase'], outcome=row['outcome'],
            started_at=row['started_at'].isoformat() if row['started_at'] else None,
            finished_at=row['finished_at'].isoformat() if row['finished_at'] else None,
            elapsed_seconds=(row['finished_at']-row['started_at']).total_seconds()
                if row['finished_at'] and row['started_at'] else None,
            retained_workspace_bytes=measured_bytes(options.state/'execution'/('batch-'+str(row['attempt_id'])))))
    names = sorted({'jasmine-'+row['execution_key'] for row in jobs if row['execution_key'] and row['phase'] != 'finished'})
    stats, unavailable = [], False
    if names:
        try:
            text = docker.call('container', 'stats', '--no-stream', '--no-trunc', '--format', '{{json .}}', *names)
            stats = [json.loads(line) for line in text.splitlines() if line]
        except Exception:
            unavailable = True
    disk = shutil.disk_usage(options.private_root)
    docker_disk = shutil.disk_usage(options.docker_data_root) if getattr(options, 'docker_data_root', None) else None
    memory = {}
    with Path('/proc/meminfo').open() as source:
        for line in source:
            key, value = line.split(':', 1)
            if key in ('MemTotal', 'MemAvailable'):
                memory[key] = int(value.split()[0])*1024
    cgroup = Path('/sys/fs/cgroup/system.slice/simpaths-multirun.service/memory.current')
    try:
        app_memory = int(cgroup.read_text())
    except OSError:
        app_memory = None
    return dict(at=datetime.now(timezone.utc).isoformat(),
        jobs=[dict(id=str(j['id']), experiment=str(j['experiment_id']), state=j['state'])
              for j in {str(j['id']): j for j in jobs}.values()],
        attempts=attempts, docker_stats=stats, docker_stats_unavailable=unavailable,
        private_storage=dict(total_bytes=disk.total, free_bytes=disk.free),
        docker_storage=dict(total_bytes=docker_disk.total, free_bytes=docker_disk.free) if docker_disk else None,
        host=dict(cpu_count=os.cpu_count(), load_average=list(os.getloadavg()), memory_bytes=memory),
        application_memory_bytes=app_memory,
        inputs=[dict(id=d['dataset_id'], bytes=measured_bytes(d['location'])) for d in datasets],
        # Cache and processor totals are service-wide; measure on an otherwise idle VM.
        download_cache_bytes=measured_bytes(options.state/'execution'/'download-cache'),
        visualiser_cache_bytes=measured_bytes(options.state/'execution'/'visualiser-cache'),
        processing_reservations=processors)


def summary(samples):
    attempts, caches = {}, {'download': [], 'visualiser': [], 'application': []}
    count, unavailable, minimum = 0, 0, None
    for item in samples:
        count += 1
        unavailable += item['docker_stats_unavailable']
        free = item['private_storage']['free_bytes']
        minimum = min(minimum, free) if minimum is not None else free
        for key, field in (('download', 'download_cache_bytes'), ('visualiser', 'visualiser_cache_bytes'),
                           ('application', 'application_memory_bytes')):
            value = item.get(field)
            if value is not None:
                caches[key] = [max(caches[key][0], value) if caches[key] else value]
        for row in item['attempts']:
            old = attempts.setdefault(row['attempt'], dict(row, peak_workspace_bytes=None))
            old.update(row)
            size = row['retained_workspace_bytes']
            if size is not None:
                old['peak_workspace_bytes'] = max(old['peak_workspace_bytes'] or 0, size)
    return dict(samples=count, attempts=list(attempts.values()), minimum_free_bytes=minimum,
        maximum_download_cache_bytes=max(caches['download'], default=None),
        maximum_visualiser_cache_bytes=max(caches['visualiser'], default=None),
        maximum_application_memory_bytes=max(caches['application'], default=None),
        unavailable_docker_samples=unavailable)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--experiment', action='append', default=[])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--interval', type=int, default=30)
    parser.add_argument('--duration', type=int, default=3600)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    options = load_config(args.config)
    if not 5 <= args.interval <= 300 or not args.interval <= args.duration <= 86400:
        parser.error('Use 5–300 second sampling and a duration up to one day')
    if not args.execute:
        print(json.dumps(dict(mode='plan', reference_workload=dict(population=50000,
            start_year=2019, end_year=2026, configurations=2, repetitions=[1, 3], first_seed='606'),
            pool=options.capacity, per_user_active=options.per_user_active,
            note='Submit through the normal reviewed form, export YAML and then monitor its experiment ID. '
                 'Include input ZIP preparation/resume and Visualiser processing during the sampling window. '
                 'This command has not contacted or changed the service.'), indent=2))
        return 0
    if not args.experiment or len(args.experiment) > 20 or not args.output or args.output.exists():
        parser.error('Supply 1–20 experiment IDs and a new output directory')
    experiments = sorted({str(UUID(value)) for value in args.experiment})
    sys.path.insert(0, str(options.frontend.resolve(strict=True)))
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.docker_executor import DockerCLI
    q = Queue(database_dsn(options), options.pool_id)
    docker = DockerCLI()
    options.docker_data_root = Path(docker.call('info', '--format', '{{.DockerRootDir}}'))
    if not options.docker_data_root.is_absolute():
        raise ValueError('Docker data filesystem is unavailable')
    rows, _, _, _ = selected_snapshot(q, experiments)
    os.umask(0o077)
    args.output.mkdir(mode=0o700, parents=True)
    report = dict(started_at=datetime.now(timezone.utc).isoformat(), completed=False,
        pool=options.capacity, model_image=options.image,
        interval_seconds=args.interval, requested_duration_seconds=args.duration,
        experiments=[dict(id=str(row['id']), specification=row['specification'], policy=row['policy']) for row in rows],
        limitations=['Observed Docker usage and sampled filesystem sizes are not hard quotas.',
                     'Sampling can miss brief resource peaks.',
                     'Cache/processor/free-space totals include other service activity.',
                     'Attempt elapsed time includes model setup and output validation, not queue waiting.',
                     'No claims about scientific equivalence or VM capacity are made by this recorder.'])
    def revision(directory):
        result = subprocess.run(['git', '-C', str(directory), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None
    report['source_commits'] = dict(simpaths=revision(Path(__file__).resolve().parents[2]),
                                    frontend=revision(options.frontend))
    count = 0
    deadline = time.monotonic()+args.duration
    try:
        with (args.output/'samples.jsonl').open('x') as output:
            while True:
                item = sample(q, options, experiments, docker)
                count += 1
                output.write(json.dumps(item)+'\n'); output.flush()
                print('Sample '+str(count)+': free '+f'{item["private_storage"]["free_bytes"]/1024**3:.2f}'+' GiB', flush=True)
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(args.interval, remaining))
        report['completed'] = True
    except KeyboardInterrupt:
        report['stopped_by_operator'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
    with (args.output/'samples.jsonl').open() as source:
        report['summary'] = summary(json.loads(line) for line in source)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Generated capacity evidence attribution.\n@author ross richardson -->\n\n'
        'Recorder and generated evidence: (C) Copyright 2026, by Ross Richardson.\n'
        'Simulation software, input data and scientific results retain their own rights.\n')
    print('Recorded: '+str(args.output/'report.json'))
    return 0 if report.get('completed') or report.get('stopped_by_operator') else 1


if __name__ == '__main__':
    raise SystemExit(main())
