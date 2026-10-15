#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Compare 3/4 GiB user-data preparation heaps in isolated 5 GiB SingleRun sessions.
Uses one locally rebuilt image, public examples, and disposable private services.
Does not promote images, change the catalogue, or use retained research outputs.
@author ross richardson
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._workflow import frontend_path
from deploy.acceptance.run_security_refresh import exclusive_trials, require_browser_space

HEAPS = (3072, 4096)
WORKER_CLASS = 'simpaths/experiment/SimPathsUserDataPreparation.class'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def input_inventory():
    """Record local public sources; bundled workbooks are identified by the image."""
    files = list((ROOT/'input').glob('*.xlsx')) + list((ROOT/'input').glob('*.xls'))
    for name, suffixes in [('InitialPopulations/training', {'.csv'}),
                           ('EUROMODoutput/training', {'.txt', '.xlsx'})]:
        files.extend(path for path in (ROOT/'input'/name).iterdir() if path.suffix.lower() in suffixes)
    if not files or any(path.is_symlink() or not path.is_file() for path in files):
        raise ValueError('Expected regular public example inputs and workbooks')
    return {str(path.relative_to(ROOT)): {'bytes': path.stat().st_size, 'sha256': digest(path)}
            for path in sorted(files)}


def tool_inventory(frontend):
    result = {}
    for label, repo in (('simpaths', ROOT), ('frontend', frontend)):
        names = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '-z',
                                         '--cached', '--others', '--exclude-standard'], text=True).split('\0')
        files = {}
        for name in sorted(set(names)):
            if not name:
                continue
            selected = (name.startswith('deploy/acceptance/') and name.endswith('.py')) if label=='simpaths' else (
                name.endswith(('.py', '.sql')) or name.startswith('static/') or name=='deploy/simpaths/models.json')
            if not selected:
                continue
            path = repo/name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Trial source is not a regular file: '+name)
            files[name] = digest(path)
        result[label] = files
    return result


def write_comparison(output, report):
    lines = ['<!-- (C) Copyright 2026, by Ross Richardson',
             'Generated local user-data preparation memory comparison.', '@author ross richardson -->',
             '', '# Local user-data memory trials', '',
             'Both cases: 4 GiB application heap, 5 GiB container, two CPUs, swap disabled.',
             'Public upload examples; 50,000 people, 2019–2026, fixed seed 606.', '',
             '| Preparation heap | Preparation 1 | Preparation 2 | Peak working RAM | Peak total RAM | Result |',
             '|---|---:|---:|---:|---:|---|']
    for entry in report['cases']:
        path = Path(entry['report']).parent/'user-data/report.json'
        evidence = json.loads(path.read_text()) if path.exists() else {}
        stages = {item['stage']: item for item in evidence.get('stages', [])}
        def seconds(name):
            value = stages.get(name, {})
            return f"{value['seconds']:.1f} s" if value.get('status')=='passed' else '—'
        memory = evidence.get('memory', {})
        def gib(key):
            value = memory.get(key)
            return f'{value/1024**3:.3f} GiB' if type(value) in (int,float) else '—'
        lines.append(f"| {entry['preparation_heap_mib']//1024} GiB | {seconds('preparation-1')} | "
                     f"{seconds('preparation-2')} | {gib('peak_working_bytes')} | {gib('peak_usage_bytes')} | "
                     f"{'Passed' if entry['passed'] else 'Incomplete/failed'} |")
    lines += ['', 'Peaks are sampled across the whole session, including preparation and simulation; '
              'total RAM includes reclaimable file cache. Phase timestamps and numeric samples are retained.',
              'The second preparation has warmed caches and a surviving application process: compare matching passes.',
              'These timings do not measure garbage-collection counters or establish a production minimum.',
              'Each successful case includes two complete simulations and a reset/rebuild; '
              'other datasets, horizons and hosted concurrency require separate validation.', '']
    (output/'comparison.md').write_text('\n'.join(lines))


def trial_command(frontend, image, heap, output):
    if heap not in HEAPS:
        raise ValueError('Only the 3072/4096 MiB preparation comparison is supported')
    return [sys.executable, str(ROOT/'deploy/acceptance/run_interactive_rpc_acceptance.py'),
            '--frontend', str(frontend), '--profile', 'user-data', '--user-data-image', image,
            '--memory-trial', '--preparation-heap-mib', str(heap), '--output', str(output)]


def image_dockerfile(base, heap):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', base) or heap not in HEAPS:
        raise ValueError('Expected immutable image identity and supported preparation heap')
    return ('# (C) Copyright 2026, by Ross Richardson\n'
            '# Isolated preparation-heap trial; identical parent image.\n'
            '# @author ross richardson\n'
            f'FROM {base}\nENV SIMPATHS_PREPARATION_HEAP_MIB={heap}\n')


def verify_jar(content, expected):
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('Candidate image does not contain the current local singlerun.jar')
    with zipfile.ZipFile(io.BytesIO(content)) as jar:
        if b'SIMPATHS_PREPARATION_HEAP_MIB' not in jar.read(WORKER_CLASS):
            raise ValueError('Rebuild the candidate: preparation heap override is missing')


def verify_image(client, image, expected_jar):
    """Inspect a never-started candidate container; do not execute model code."""
    config = image.attrs['Config']
    if config.get('Volumes') or config.get('User') != '10001:10001':
        raise ValueError('Use the non-root, volume-free user-data image built by this repository')
    if (config.get('Labels') or {}).get('org.jasmine.security') != 'session-v1':
        raise ValueError('Candidate is missing the reviewed session image label')
    container = client.containers.create(image.id, command=['true'], network_mode='none')
    try:
        chunks, _ = container.get_archive('/app/app.jar')
        # The application JAR is bounded; no input/output database is copied.
        data = bytearray()
        for chunk in chunks:
            data.extend(chunk)
            if len(data) > 256*1024**2:
                raise ValueError('Candidate JAR archive exceeds the verification bound')
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            files = [entry for entry in archive if entry.isfile()]
            if len(files) != 1:
                raise ValueError('Expected exactly one application JAR')
            verify_jar(archive.extractfile(files[0]).read(), expected_jar)
    finally:
        container.remove()


def require_passed(path):
    report = json.loads(Path(path).read_text())
    if report.get('passed') is not True or report.get('cleanup') is not True:
        raise RuntimeError('Browser workflow or disposable service cleanup was not confirmed')
    return report


def run_browser(command, log):
    """Let the fixture clean up its own containers when interrupted or timed out."""
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        code = process.wait(timeout=12*60*60)
        if code:
            raise subprocess.CalledProcessError(code, command)
    except BaseException:
        if process.poll() is None:
            # Signal the fixture first so its finally block can drain the services.
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=480)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                raise RuntimeError('Trial could not finish cleanup; inspect its private report before retrying')
        raise


def run(args):
    import docker
    client = docker.from_env(timeout=30)
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = dict(format='simpaths.user-data-memory-trials.v1', passed=False,
                  started=datetime.now(timezone.utc).isoformat(), cases=[],
                  application_heap_mib=4096, container_memory_mib=5120, cpu_millis=2000,
                  population=50000, start_year=2019, end_year=2026, seed=606,
                  simulation_rebuilds_per_case=2, preparations_per_case=2,
                  production_defaults_changed=False, garbage_collection_counters_measured=False,
                  limitations=['Public example inputs; other data sizes and horizons need separate validation.',
                               'Local fixed-memory results do not establish hosted concurrency or timing.'])
    (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Authored memory-trial reports, timing records and generated trial Dockerfiles.\n'
        '@author ross richardson\n-->\n'
        'Public model inputs and third-party software retain their existing notices.\n')
    try:
        with exclusive_trials(args.output.parent):
            require_browser_space(args.output)
            image = client.images.get(args.image)
            jar_hash = digest(ROOT/'singlerun.jar')
            verify_image(client, image, jar_hash)
            inputs = input_inventory()
            sources = tool_inventory(args.frontend)
            report.update(base_image=image.id, model_jar_sha256=jar_hash, local_public_source_files=inputs,
                          uploaded_files={key:value for key,value in inputs.items()
                                          if key.startswith(('input/InitialPopulations/training/',
                                                             'input/EUROMODoutput/training/'))
                                          and key.endswith(('.csv','.txt'))},
                          bundled_workbooks_from_base_image=image.id, tool_sources=sources,
                          case_order=args.preparation_heap_mib)
            for heap in args.preparation_heap_mib:
                require_browser_space(args.output)
                if (digest(ROOT/'singlerun.jar') != jar_hash or input_inventory() != inputs
                        or tool_inventory(args.frontend) != sources):
                    raise RuntimeError('Model JAR, public inputs or test/service sources changed between cases')
                name = 'prep-'+str(heap)
                case = args.output/name
                tag = 'simpaths-memory-trial:'+args.output.name.lower()[:60]+'-'+str(heap)
                if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]*', args.output.name.lower()[:60]):
                    raise ValueError('Use an output basename containing letters, numbers, dots, hyphens or underscores')
                dockerfile = image_dockerfile(image.id, heap)
                (args.output/(name+'.Dockerfile')).write_text(dockerfile)
                candidate, _ = client.images.build(fileobj=io.BytesIO(dockerfile.encode()),
                                                   tag=tag, pull=False, rm=True, network_mode='none')
                command = trial_command(args.frontend, candidate.id, heap, case)
                entry = dict(preparation_heap_mib=heap, image=candidate.id, tag=tag,
                             report=str(case/'report.json'), command=command, passed=False)
                report['cases'].append(entry)
                print(f'Running preparation heap {heap//1024} GiB; log: {args.output/(name+".log")}', flush=True)
                with (args.output/(name+'.log')).open('w') as log:
                    run_browser(command, log)
                require_passed(case/'report.json')
                browser = json.loads((case/'user-data/report.json').read_text())
                if browser.get('status') != 'passed' or not browser.get('memory_trial'):
                    raise RuntimeError('Missing successful fixed-memory browser trial evidence')
                entry['passed'] = True
                print(f'PASS: preparation heap {heap//1024} GiB workflow and cleanup', flush=True)
            if (digest(ROOT/'singlerun.jar') != jar_hash or input_inventory() != inputs
                    or tool_inventory(args.frontend) != sources):
                raise RuntimeError('Model JAR, public inputs or test/service sources changed during trials')
            report['passed'] = True
    except (Exception, KeyboardInterrupt) as error:
        report.update(error_type=type(error).__name__, error=str(error))
        print('STOPPED: '+str(error)+'; existing evidence is retained.', file=sys.stderr)
    finally:
        report['finished'] = datetime.now(timezone.utc).isoformat()
        (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        write_comparison(args.output, report)
        client.close()
    print(('PASSED: ' if report['passed'] else 'FAILED: ')+str(args.output/'report.json'), flush=True)
    return 0 if report['passed'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=frontend_path())
    parser.add_argument('--image', required=True, help='Newly rebuilt user-data candidate image; never a production alias')
    parser.add_argument('--preparation-heap-mib', nargs='+', type=int, choices=HEAPS, default=list(HEAPS))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if len(set(args.preparation_heap_mib)) != len(args.preparation_heap_mib):
        parser.error('Use each heap once per output directory; use a fresh directory for repeat trials')
    args.frontend = args.frontend.expanduser().resolve(strict=True)
    args.output = args.output.expanduser().absolute()
    os.umask(0o077)
    return run(args)


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
