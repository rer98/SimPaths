#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Check the monitor in a built model JAR with fictional heap/cgroup pressure.
Uses private temporary classes and disposable Docker containers; touches no
service state, model input, user session, scientific output or resource policy.
@author ross richardson
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']
MIB = 1024**2
PROBE = 'simpaths.experiment.MemoryMonitorProbe'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def jar_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_probe(log, mode, *, initial_limit=None, changed_limit=None):
    require('MEMORY_MONITOR_OK ' + mode in log, 'The fictional MultiRun did not finish its checks')
    samples = {}
    warnings = None
    for line in log.splitlines():
        if line.startswith('MEMORY_MONITOR_SAMPLE '):
            sample = json.loads(line.split(' ', 1)[1])
            phase = sample['phase']
            require(phase not in samples, 'Duplicate monitor evidence phase')
            samples[phase] = sample
        if line.startswith('MEMORY_MONITOR_WARNINGS '):
            require(warnings is None, 'Duplicate warning evidence')
            warnings = json.loads(line.split(' ', 1)[1])
    phases = {'initial'} if mode == 'disabled' else {'initial', 'pressure', 'held'}
    if mode == 'container':
        phases.add('resized')
    require(set(samples) == phases, 'Missing or unexpected monitor evidence phases')
    require(isinstance(warnings, list), 'Missing warning delivery evidence')
    expected_threads = 0 if mode == 'disabled' else 1
    for phase, sample in samples.items():
        require(type(sample['elapsed_ms']) is int and sample['elapsed_ms'] >= 0,
                'Invalid sample timing evidence')
        require(sample['monitor_threads'] == expected_threads, 'Wrong number of monitoring daemons')
        require(0 <= sample['heap_used_bytes'] <= sample['heap_committed_bytes'] <= sample['heap_max_bytes'],
                'Invalid JVM heap measurements')
        require(sample['heap_max_bytes'] > 0, 'Missing JVM heap maximum')
        limit = changed_limit if phase == 'resized' else initial_limit
        if limit is not None:
            require(sample['container_limit_bytes'] == limit, 'Monitor did not read the configured Docker limit')
            used, inactive = sample['container_used_bytes'], sample['inactive_file_bytes']
            require(type(used) is int and used >= 0, 'Missing real cgroup memory usage')
            require(type(inactive) is int and inactive >= 0, 'Missing real cgroup cache measurement')
            require(sample['working_set_bytes'] == max(0, used - inactive), 'Working set calculation differs')
    kinds = {'heap': [], 'container': []}
    for warning in warnings:
        require(warning['kind'] in kinds and type(warning['elapsed_ms']) is int
                and warning['elapsed_ms'] >= 0, 'Invalid warning timing evidence')
        kinds[warning['kind']].append(warning['elapsed_ms'])
    for times in kinds.values():
        require(all(later - earlier >= 29_999 for earlier, later in zip(times, times[1:])),
                'Warnings repeated inside the thirty-second throttle')
    for kind, prefix in [('heap', 'WARNING: Java heap is '),
                         ('container', 'WARNING: Container working set is ')]:
        require(len(kinds[kind]) == sum(line.startswith(prefix) for line in log.splitlines()),
                'Recorded warnings differ from delivered console messages')
    if mode == 'disabled':
        require(not warnings, 'Desktop MultiRun emitted monitoring warnings without opt-in')
    else:
        require(kinds['heap'], 'Actual heap warning did not reach stdout')
        require(samples['held']['elapsed_ms'] - kinds['heap'][0] >= 10_000,
                'Heap pressure evidence did not cover a further monitoring poll')
        for phase in ('pressure', 'held'):
            require(samples[phase]['heap_used_bytes'] / samples[phase]['heap_max_bytes'] > .85,
                    'The heap fixture did not sustain warning-level usage')
    if mode == 'container':
        require(kinds['container'], 'Actual container warning did not reach stdout')
        require(samples['held']['elapsed_ms'] - kinds['container'][0] >= 10_000,
                'Container pressure evidence did not cover a further monitoring poll')
        for phase in ('pressure', 'held'):
            require(samples[phase]['working_set_bytes'] / initial_limit > .85,
                    'The container fixture did not sustain warning-level usage')
            require(samples[phase]['container_used_bytes'] < initial_limit * .95,
                    'The pressure fixture approached the container memory ceiling')
        resized = samples['resized']
        require(resized['heap_max_bytes'] == samples['initial']['heap_max_bytes'],
                'The Java heap maximum changed during a container resize')
        require(resized['working_set_bytes'] / changed_limit < .85,
                'The larger container limit did not reduce measured pressure below the threshold')
        require(resized['direct_allocated_bytes'] == samples['held']['direct_allocated_bytes'],
                'The fixture dropped its live allocation instead of measuring the larger limit')
    return dict(samples=samples, warnings=warnings)


def jvm_arguments(classes, jar, setting, mode, initial=0, changed=0):
    command = ['-Xms64m', '-Xmx64m', '-XX:+UseSerialGC', '-XX:MaxDirectMemorySize=192m',
               '-XX:ActiveProcessorCount=1', '-Djava.awt.headless=true']
    if setting is not None:
        command.append('-Djasmine.memory.monitor.enabled=' + setting)
    return [*command, '-cp', str(classes) + ':' + str(jar), PROBE, mode, str(initial), str(changed)]


def run_local(classes, jar, output):
    env = {key: value for key, value in os.environ.items()
           if key not in {'JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS'}}
    evidence = {}
    for name, setting, mode in [('desktop-default', None, 'disabled'),
                                ('explicit-false', 'false', 'disabled'),
                                ('enabled-heap', 'true', 'heap')]:
        log = output / (name + '.log')
        with log.open('w') as stream:
            result = subprocess.run(['java', *jvm_arguments(classes, jar, setting, mode)],
                                    cwd=classes.parent, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=55)
        require(result.returncode == 0, 'Packaged JAR probe failed; inspect ' + str(log))
        evidence[name] = validate_probe(log.read_text(), mode)
    print('PASS: packaged JAR leaves desktop monitoring disabled; explicit opt-in delivers a real heap warning with bounded repeat messages', flush=True)
    return evidence


def docker(*args, timeout=20):
    result = subprocess.run([*DOCKER, *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Disposable Docker check failed: ' + result.stderr.strip()[:1200])
    return result.stdout + result.stderr if args[0] == 'logs' else result.stdout


def run_container(classes, jar, image, output):
    metadata = json.loads(docker('image', 'inspect', image))[0]
    require(not metadata['Config'].get('Volumes'), 'Choose a runtime image without implicit volumes')
    image = metadata['Id']
    name = 'simpaths-memory-monitor-' + uuid.uuid4().hex
    log_path = output / 'container.log'
    initial, changed = 256 * MIB, 320 * MIB
    evidence = None
    cleanup_confirmed = False
    try:
        docker('create', '--name', name, '--pull', 'never', '--network', 'none',
               '--read-only', '--user', f'{os.getuid()}:{os.getgid()}',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
               '--ipc', 'private', '--pids-limit', '64', '--cpus', '1',
               '--memory', str(initial), '--memory-swap', str(initial),
               '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777', '--workdir', '/tmp',
               '--mount', f'type=bind,src={classes},dst=/probe,readonly',
               '--mount', f'type=bind,src={jar},dst=/model.jar,readonly',
               '--env', 'JAVA_TOOL_OPTIONS=', '--env', 'JDK_JAVA_OPTIONS=', '--env', '_JAVA_OPTIONS=',
               '--entrypoint', '/opt/java/openjdk/bin/java', image,
               *jvm_arguments('/probe', '/model.jar', 'true', 'container', initial, changed))
        docker('start', name)
        deadline = time.monotonic() + 90
        updated = False
        started_at = None
        while True:
            log = docker('logs', name)
            log_path.write_text(log)
            state = json.loads(docker('inspect', name))[0]
            if started_at is None:
                started_at = state['State']['StartedAt']
            require(state['State']['StartedAt'] == started_at,
                    'Disposable container restarted during memory validation')
            if 'MEMORY_MONITOR_RESIZE_READY' in log and not updated:
                require(state['State']['Running'], 'Probe stopped before its bounded resize')
                docker('update', '--memory', str(changed), '--memory-swap', str(changed), name)
                updated = True
            if not state['State']['Running']:
                require(state['State']['ExitCode'] == 0 and not state['State']['OOMKilled'],
                        'Fictional memory check failed or exhausted its container; inspect ' + str(log_path))
                require(updated and state['HostConfig']['Memory'] == changed,
                        'The disposable container was not resized as requested')
                evidence = validate_probe(log, 'container', initial_limit=initial, changed_limit=changed)
                evidence.update(image=image, oom_killed=False, exit_code=0,
                                initial_limit_bytes=initial, changed_limit_bytes=changed,
                                started_at=started_at)
                break
            require(time.monotonic() < deadline, 'Native memory-monitor check did not finish in time')
            time.sleep(.5)
        print('PASS: real heap and cgroup pressure warnings reach MultiRun logs; container readings and inactive-cache subtraction match the bounded fixture', flush=True)
        print('PASS: increasing only the disposable container from 256 to 320 MiB is observed without a JVM restart or change to its heap maximum', flush=True)
    finally:
        # The unique name also covers a create call whose response was lost.
        existing = docker('container', 'ls', '-aq', '--filter', 'name=^/' + name + '$').strip()
        if existing:
            docker('rm', '-f', name)
        require(not docker('container', 'ls', '-aq', '--filter', 'name=^/' + name + '$').strip(),
                'Disposable memory-check container cleanup could not be confirmed')
        cleanup_confirmed = True
    evidence['cleanup_confirmed'] = cleanup_confirmed
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jar', type=Path, default=ROOT / 'multirun.jar')
    parser.add_argument('--image', default='simpaths-interactive:uk-user-data')
    parser.add_argument('--output', type=Path, default=Path.home() / 'simpaths-benchmarks' /
                        ('memory-monitor-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    parser.add_argument('--local-only', action='store_true', help='JAR/heap checks only; does not claim native cgroup validation')
    args = parser.parse_args(argv)
    os.umask(0o077)
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = dict(format='simpaths.memory-monitor.acceptance.v1', passed=False,
                  native_container_checked=False)
    try:
        jar = args.jar.resolve(strict=True)
        require(jar.is_file(), 'Select an existing rebuilt model JAR')
        with zipfile.ZipFile(jar) as archive:
            require({'microsim/monitoring/MemoryMonitor.class',
                     'microsim/monitoring/MemoryMonitor$Snapshot.class'} <= set(archive.namelist()),
                    'The selected JAR does not contain the shared monitor; rebuild it first')
        original_hash = jar_hash(jar)
        report['model_jar_sha256'] = original_hash
        for executable in ('java', 'javac'):
            require(shutil.which(executable), 'This check needs ' + executable + ' on PATH')
        with tempfile.TemporaryDirectory(prefix='simpaths-memory-check-') as temporary:
            classes = Path(temporary) / 'classes'
            classes.mkdir(mode=0o700)
            with (output / 'compile.log').open('w') as log:
                subprocess.run(['javac', '-proc:none', '-cp', str(jar), '-d', str(classes),
                                str(Path(__file__).with_name('MemoryMonitorProbe.java'))],
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=40)
            report['local'] = run_local(classes, jar, output)
            if not args.local_only:
                report['container'] = run_container(classes, jar, args.image, output)
                report['native_container_checked'] = True
            require(jar_hash(jar) == original_hash, 'The selected model JAR changed during validation')
        report['passed'] = True
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
        print('STOPPED: ' + str(error), flush=True)
    finally:
        (output / 'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Generated memory-monitor acceptance reports and fictional allocation logs.\n'
            '@author ross richardson\n-->\n')
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    label = 'PASSED (local checks only)' if report['passed'] and args.local_only else (
        'PASSED' if report['passed'] else 'FAILED')
    print(label + ': ' + str(output / 'report.json'), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
