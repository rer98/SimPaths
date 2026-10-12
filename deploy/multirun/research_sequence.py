#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Run fixed-memory research cases sequentially, preserving audited compact evidence
before reclaiming each case's bulk output. Never resumes or retries failed cases.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy.multirun import research_benchmarks as bench
from deploy.multirun.artifacts import ArtifactError, fingerprint, write_attribution

FORMAT = 'simpaths.research-benchmark-sequence.v1'
COMMON = dict(population=100000, end_year=2070, repetitions=2, storage_mib=12288,
              setup_seconds=900, repetition_seconds=14400, max_attempts=1)
MIB = 1024**2
GIB = 1024**3


def settings(label):
    heap, memory = bench.CASE_LIMITS[label]
    return dict(COMMON, heap_mib=heap, memory_mib=memory, max_memory_mib=memory)


def snapshot(prepared, frontend):
    """Bind commands to small source/receipt files and the selected model JAR.

    The native calibration additionally verifies all prepared input contents
    before and after each model run. This inventory is not a replacement for it.
    """
    paths = [Path(__file__), ROOT/'deploy/multirun/research_calibration.py',
             ROOT/'deploy/multirun/research_storage_audit.py',
             ROOT/'deploy/multirun/research_benchmarks.py',
             ROOT/'deploy/acceptance/SimPathsResourceProbe.java',
             ROOT/'deploy/multirun/requirements.txt', ROOT/'deploy/acceptance/requirements.txt',
             frontend/'scripts/test_batch_queue.py', frontend/'requirements-batch-web.txt',
             prepared/'receipt.json', prepared/'profile.json', prepared/'model.jar']
    return {str(path): fingerprint(bench.ordinary(path)) for path in paths}


def resources(directory, memory_mib):
    available = next(line for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    value = dict(free_disk_bytes=shutil.disk_usage(directory).free,
                 available_memory_bytes=int(available.split()[1])*1024,
                 required_free_disk_bytes=(COMMON['storage_mib']+3072)*MIB,
                 required_available_memory_bytes=(memory_mib+2048)*MIB)
    if value['free_disk_bytes'] < value['required_free_disk_bytes']:
        raise ArtifactError('Need 15 GiB free on the trial filesystem; no case was started')
    if value['available_memory_bytes'] < value['required_available_memory_bytes']:
        raise ArtifactError(f'Need {value["required_available_memory_bytes"]/GIB:g} GiB available RAM; no case was started')
    return value


def make_plan(args):
    if len(set(args.cases)) != len(args.cases):
        raise ArtifactError('Select each case once; use a new sequence for another repeat')
    prepared = bench.ordinary(args.prepared.expanduser().absolute())
    frontend = bench.ordinary(args.frontend.expanduser().absolute())
    output = bench.ordinary(args.output.expanduser().absolute())
    if output.exists() or not output.parent.is_dir():
        raise ArtifactError('Choose a new output directory inside an existing directory')
    references = {}
    for item in args.reference_case:
        label, separator, name = item.partition('=')
        if not separator or label not in bench.CASE_LIMITS or label in references or not name:
            raise ArtifactError('Specify distinct references as C=PATH, A=PATH or B=PATH')
        references[label] = bench.ordinary(Path(name).expanduser().absolute())
    for source in (ROOT, frontend, prepared, *references.values()):
        if output == source or source in output.parents or output in source.parents:
            raise ArtifactError('Keep sequence evidence outside sources, prepared inputs and reference evidence')
    frozen = snapshot(prepared, frontend)
    receipt, _ = bench.read_json(prepared/'receipt.json')
    identity = receipt['identity']
    if frozen[str(prepared/'model.jar')] != identity['model']:
        raise ArtifactError('Prepared model JAR no longer matches its receipt')
    for label, path in references.items():
        case = bench.load_case(label, path)
        if case['state'] != 'complete':raise ArtifactError('Use complete, verified reference cases')
        record = case['record']
        if (record['settings'] != settings(label) or record.get('variant') != 'baseline'
                or record['prepared_sha256'] != receipt['sha256'] or record['model'] != identity['model']
                or record['image'] != identity['source_image']
                or any(frozen[str(ROOT/name)]['sha256'] != digest
                       for name, digest in record['source_sha256'].items())):
            raise ArtifactError('Reference workload/model/inputs/measurement sources differ from tonight\'s cases')
    cases = []
    for label in args.cases:
        directory = output/'trials'/label
        command = [sys.executable, str(ROOT/'deploy/multirun/research_calibration.py'),
                   '--frontend', str(frontend), '--prepared', str(prepared),
                   '--image', identity['source_image'], '--variant', 'baseline']
        for name, value in settings(label).items():command += ['--'+name.replace('_', '-'), str(value)]
        command += ['--output', str(directory)]
        cases.append(dict(label=label, settings=settings(label), completed=str(directory), command=command,
                          audit=str(output/'audits'/label), archive=str(output/'archives'/label)))
    return dict(format=FORMAT, cases=cases, references={k:str(v) for k,v in references.items()},
                output=str(output), prepared=str(prepared), frontend=str(frontend), frozen=frozen,
                limits=dict(cpu_millis=2000, live_expansion=False, resource_retries=False),
                delete_bulk_after_verified_archive=True, pause_seconds=args.pause_seconds,
                note='Run on mains power with sleep disabled and other substantial work stopped. '
                     'Cases execute one at a time; original reference evidence is never deleted.')


@contextmanager
def exclusive(directory):
    path = directory/'.simpaths-research-sequence.lock'
    with bench.parent_fd(path) as (parent, name):
        fd = os.open(name, os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        value = os.fstat(fd)
        if not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid() or value.st_mode & 0o077:
            raise ArtifactError('Expected a private sequence lock owned by the current user')
        fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:os.close(fd)


def run_command(command, log, env):
    """Stream native progress to the terminal and its private per-stage log."""
    with log.open('x', encoding='utf-8') as stream:
        os.chmod(log, 0o600)
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace')
        try:
            for line in process.stdout:
                stream.write(line);stream.flush()
                print(line, end='', flush=True)
            code = process.wait()
        except BaseException:
            # Terminal interrupts also reach the native driver/proof. Give the
            # direct child time to finish its existing cleanup; never start more work.
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:process.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:process.wait(timeout=30)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
            raise
        finally:process.stdout.close()
    if code:raise ArtifactError(f'Command exited {code}; inspect {log}')


def execute(plan, *, runner=run_command, pause=time.sleep):
    output = Path(plan['output']);prepared = Path(plan['prepared']);frontend = Path(plan['frontend'])
    env = dict(os.environ, TMPDIR=str(output/'trials'), JASMINE_WEB_REPO=str(frontend),
               PYTHONPATH=os.pathsep.join((str(ROOT), str(frontend))), PYTHONUNBUFFERED='1', PIP_NO_INPUT='1')
    env.setdefault('PIP_DEFAULT_TIMEOUT', '60')
    with exclusive(output.parent):
        resources(output.parent, max(c['settings']['memory_mib'] for c in plan['cases']))
        output.mkdir(mode=0o700);write_attribution(output)
        for name in ('trials', 'audits', 'archives', 'logs', 'comparisons'):(output/name).mkdir(mode=0o700)
        bench.write_json(output/'plan.json', plan)
        record = dict(format=FORMAT, started_at=bench.instant(), passed=False, steps=[], completed_cases=[])
        selected = dict(plan['references'])
        def save():bench.write_json(output/'report.json', record, replace=True)
        def step(label, stage, command):
            log = output/'logs'/(label+'-'+stage+'.log')
            detail = dict(case=label, stage=stage, started_at=bench.instant(), log=str(log), passed=False)
            record['steps'].append(detail);save()
            print(f'\n{label}: {stage}; log: {log}', flush=True)
            runner(command, log, env)
            detail.update(passed=True, finished_at=bench.instant());save()
        try:
            for index, case in enumerate(plan['cases']):
                label = case['label'];directory = Path(case['completed']);archive = Path(case['archive'])
                if index and plan['pause_seconds']:
                    print(f'Pausing {plan["pause_seconds"]} seconds before {label}.', flush=True)
                    pause(plan['pause_seconds'])
                if snapshot(prepared, frontend) != plan['frozen']:
                    raise ArtifactError('Selected model, receipt or measurement sources changed; sequence stopped')
                record['last_preflight'] = resources(output/'trials', case['settings']['memory_mib']);save()
                step(label, 'run', case['command'])
                _, (native, _, _, _) = bench.completion(directory)
                if native['settings'] != case['settings'] or native.get('variant') != 'baseline':
                    raise ArtifactError('Completed case differs from its planned fixed settings')
                step(label, 'audit', [sys.executable, str(ROOT/'deploy/multirun/research_storage_audit.py'),
                     '--frontend', str(frontend), '--prepared', str(prepared), '--completed', str(directory),
                     '--repetitions', '2', '--output', case['audit']])
                tool = [sys.executable, str(ROOT/'deploy/multirun/research_benchmarks.py')]
                step(label, 'archive', tool+['archive', '--completed', str(directory),
                     '--audit', str(Path(case['audit'])/'report.json'), '--output', str(archive)])
                selected[label] = str(archive)
                comparison = output/'comparisons'/f'{index+1:02d}-after-{label}'
                command = tool+['compare', '--output', str(comparison)]
                for name, path in selected.items():command += ['--case', name+'='+path]
                step(label, 'compare', command)
                compared, _ = bench.read_json(comparison/'report.json')
                if any(compared['cases'][name]['state'] != 'complete' for name in selected):
                    raise ArtifactError('Comparison includes unfinished or failed evidence')
                step(label, 'dispose', tool+['dispose', '--completed', str(directory), '--archive', str(archive)])
                disposal, _ = bench.read_json(directory/'model-proof/benchmark-disposal.json')
                if disposal.get('passed') is not True or bench.output_files(directory/'model-proof/retained-output'):
                    raise ArtifactError('Bulk output removal remains unconfirmed; sequence stopped')
                record['completed_cases'].append(dict(case=label, archive=str(archive), comparison=str(comparison),
                                                      disposal=str(directory/'model-proof/benchmark-disposal.json')))
                save()
            record.update(passed=True, finished_at=bench.instant());save()
        except BaseException as error:
            record.update(finished_at=bench.instant(), failure=dict(error_type=type(error).__name__, message=str(error)[:1000]))
            save();raise
    print('\nPASSED: '+str(output/'report.json'), flush=True)
    print('Final comparison: '+record['completed_cases'][-1]['comparison']+'/comparison.md', flush=True)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=Path.home()/'git/JAS-mine/JAS-mine-web')
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', choices=tuple(bench.CASE_LIMITS), default=['B', 'A'])
    parser.add_argument('--reference-case', action='append', default=[], metavar='C=ARCHIVE',
                        help='Completed/archived comparison reference; never runs or deletes this case')
    parser.add_argument('--output', type=Path, default=Path('/tmp-codex')/
                        ('research-quiet-sequence-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')))
    parser.add_argument('--pause-seconds', type=int, choices=range(0, 601), default=60, metavar='0..600')
    parser.add_argument('--dry-run', action='store_true', help='Show commands without models, writes or deletion')
    args = parser.parse_args(argv)
    try:
        plan = make_plan(args)
        if args.dry_run:
            print(bench.canonical(plan));return 0
        os.umask(0o077)
        execute(plan);return 0
    except (ArtifactError, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        print('STOPPED: '+str(error), file=sys.stderr);return 1


if __name__ == '__main__':raise SystemExit(main())
