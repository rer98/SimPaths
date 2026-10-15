#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Fixed-limit, disposable Visualiser capacity trials; no model executions or promotion.
Snapshot calculation/helper code, compare one/two independent workers, retain evidence.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy.multirun.artifacts import fingerprint, snapshot_files, write_attribution, write_json

MIB = 1024**2
GIB = 1024**3
LABEL = 'org.simpaths.visualiser-capacity'


def read_json(path):
    if Path(path).stat().st_size > 8*MIB:
        raise ValueError('Benchmark metadata exceeds its bound')
    return json.loads(Path(path).read_text())


def safe_name(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.\-/]+', value)
            or any(p in ('', '.', '..') for p in value.split('/')) or value.startswith('/')):
        raise ValueError('Unsafe benchmark relative filename')
    return value


def check_fixture(directory, *, hash_files=True):
    directory = Path(directory).resolve(strict=True)
    record = read_json(directory/'manifest.json')
    if (record.get('format') != 'simpaths.visualiser.capacity-fixture.v1'
            or record.get('mode') not in ('summary-replay', 'raw-native')
            or record.get('annual_years') != list(range(2019, 2071))):
        raise ValueError('Use a verified capacity fixture')
    seeds = record['seeds']
    if (not 1 <= len(seeds) <= 10 or len(set(seeds)) != len(seeds)
            or any(not isinstance(s, str) or not re.fullmatch('[0-9]{1,20}', s) for s in seeds)):
        raise ValueError('Invalid fixture seed plan')
    configs = record['configurations']
    if not 2 <= len(configs) <= 4 or len({c['id'] for c in configs}) != len(configs):
        raise ValueError('Use a baseline and one to three alternatives')
    references = set()
    for index, config in enumerate(configs):
        if config['role'] != ('baseline' if index == 0 else 'scenario_'+str(index)):
            raise ValueError('Fixture roles must have a stable order')
        if [r['seed'] for r in config['runs']] != seeds:
            raise ValueError('Configurations must have matching ordered seeds')
        for run in config['runs']:
            keys = ('summary',) if record['mode'] == 'summary-replay' else ('person', 'benefit')
            references.update(safe_name(run[k]) for k in keys)
    if references != set(record['files']):
        raise ValueError('Fixture source inventory differs from selected files')
    root = directory if record['mode'] == 'summary-replay' else Path(record['raw_root']).resolve(strict=True)
    if root == Path('/') or root == Path.home():
        raise ValueError('Use a specific private source directory')
    if record['mode'] == 'raw-native':
        directories = record.get('raw_directories', [])
        if (not directories or any(safe_name(name) != name for name in directories)
                or any(not any(name.startswith(d+'/') for d in directories) for name in references)):
            raise ValueError('Native fixtures need narrow retained-output mount directories')
    for name, expected in record['files'].items():
        path = root/safe_name(name)
        if hash_files and fingerprint(path) != expected:
            raise ValueError('Fixture source changed: '+name)
    return record, root


def prepare_raw(args):
    """Use verified, genuinely distinct native seed outputs; no copies or relabelling."""
    sys.path.insert(0, str(args.frontend.resolve(strict=True)))
    from deploy.multirun.research_calibration import read_calibration
    from deploy.multirun.research_comparison import retained_run
    receipt = read_calibration(args.prepared)
    sources = [retained_run(args.baseline, receipt, 'baseline', repetitions=args.repetitions),
               retained_run(args.alternative, receipt, 'lower-saving-rate', repetitions=args.repetitions)]
    destination = args.output.absolute()
    if any(destination == source['folder'] or source['folder'] in destination.parents for source in sources):
        raise ValueError('Keep new evidence outside the original native proof directories')
    root = Path(os.path.commonpath([s['output'] for s in sources]))
    seeds = sources[0]['configuration'].as_dict()['seed_plan']['seeds']
    if sources[1]['configuration'].as_dict()['seed_plan']['seeds'] != seeds:
        raise ValueError('Native seed plans differ')
    # Require identical experimental controls apart from the intentional policy/name.
    first = sources[0]['configuration'].as_dict()
    second = sources[1]['configuration'].as_dict()
    second['run_sets'][0].update(id=first['run_sets'][0]['id'], name=first['run_sets'][0]['name'])
    second['run_sets'][0]['model_args']['savingRate'] = first['run_sets'][0]['model_args']['savingRate']
    if first != second:
        raise ValueError('Native sources differ beyond the declared savings-rate alternative')
    build = read_json(args.build/'build.json')
    configs, files = [], {}
    for index, source in enumerate(sources):
        folders = {}
        for name, value in source['manifest'].items():
            if name.endswith(('/csv/Person.csv', '/csv/BenefitUnit.csv')):
                folder = name.split('/')[0]
                match = re.fullmatch(r'.*_([0-9]+)_([0-9]+)', folder)
                if not match or match[1] not in seeds:
                    raise ValueError('Native folder is not tied to the verified seed plan')
                role = 'person' if name.endswith('/Person.csv') else 'benefit'
                key = (source['output']/name).relative_to(root).as_posix()
                if role in folders.setdefault(match[1], {}):
                    raise ValueError('Duplicate native seed output')
                folders[match[1]][role] = key
                files[key] = value
        if set(folders) != set(seeds) or any(set(v) != {'person', 'benefit'} for v in folders.values()):
            raise ValueError('Missing native Person/BenefitUnit seed pair')
        configs.append(dict(id='baseline' if index == 0 else 'alternative1',
            name='Baseline' if index == 0 else 'Alternative 1',
            role='baseline' if index == 0 else 'scenario_1',
            runs=[dict(seed=seed, **folders[seed]) for seed in seeds]))
    record = dict(format='simpaths.visualiser.capacity-fixture.v1', mode='raw-native',
        raw_root=str(root), annual_years=list(range(2019, 2071)), seeds=seeds,
        raw_directories=[s['output'].relative_to(root).as_posix() for s in sources],
        configurations=configs, files=files,
        backend=dict(calculation_sha256=build['files']['calculation.cjs'], runner_sha256=build['files']['runner.cjs']),
        provenance=dict(prepared=receipt['sha256'], sources=[s['provenance'] for s in sources]),
        limitations=['Hashes are checked before processing; filesystem caches are not forcibly cleared.',
                     'This local benchmark does not establish VPS mixed-load responsiveness.'])
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_attribution(args.output)
    write_json(args.output/'manifest.json', record)
    check_fixture(args.output)
    print('Prepared native raw-output fixture: '+str(args.output/'manifest.json'))


def snapshot_tools(frontend, build, output, fixture):
    """Only reviewed source and build files enter containers; never repository secrets."""
    wanted = {'visualiser_capacity_worker.py': ROOT/'deploy/multirun/visualiser_capacity_worker.py',
              'visualiser_capacity_bounded.py': ROOT/'deploy/multirun/visualiser_capacity_bounded.py',
              'visualiser_summary_cache.py': ROOT/'deploy/multirun/visualiser_summary_cache.py',
              'visualiser/capacity_replay.cjs': ROOT/'deploy/multirun/visualiser/capacity_replay.cjs'}
    tools = snapshot_files(wanted, output/'tools')
    package = frontend/'jasmine_web'
    python_files = {str(p.relative_to(frontend)): p for p in package.rglob('*.py')}
    if not python_files or package.is_symlink():
        raise ValueError('Use the maintained frontend source checkout')
    helper = snapshot_files(python_files, output/'frontend')
    manifest = read_json(build/'build.json')
    selected = {safe_name(name): build/name for name in manifest['files']}
    selected['build.json'] = build/'build.json'
    copied = snapshot_files(selected, output/'build')
    for name, checksum in manifest['files'].items():
        if copied[name]['sha256'] != checksum:
            raise ValueError('Visualiser build differs from its manifest')
    for name, key in (('calculation.cjs', 'calculation_sha256'), ('runner.cjs', 'runner_sha256')):
        if copied[name]['sha256'] != fixture['backend'][key]:
            raise ValueError('Calculation/runner differs from the fixture provenance')
    return dict(tools=tools, frontend=helper, build=copied,
                supervisor=fingerprint(Path(__file__)))


def available_memory():
    values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return int(values['MemAvailable'].strip().split()[0])*1024


def preflight_space(directory, memory_mib, workers):
    disk = shutil.disk_usage(directory).free
    available = available_memory()
    if disk < (2+3*workers)*GIB:
        raise ValueError('Need '+str(2+3*workers)+' GiB free for bounded worker scratch')
    if available < memory_mib*MIB*workers+2*GIB:
        raise ValueError('Insufficient available RAM for fixed workers plus 2 GiB host headroom')
    return dict(free_disk_bytes=disk, available_memory_bytes=available)


@contextmanager
def exclusive(directory):
    # Same lock as unattended model/SingleRun benchmarks when output parents match.
    fd = os.open(directory/'.simpaths-research-sequence.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Expected a private benchmark lock owned by this user')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def container_options(image, snapshot, fixture, raw_root, destination, memory_mib, identity, raw_directories=None):
    mounts = {str(snapshot/'tools'): {'bind': '/tools', 'mode': 'ro'},
              str(snapshot/'frontend'): {'bind': '/frontend', 'mode': 'ro'},
              str(snapshot/'build'): {'bind': '/build', 'mode': 'ro'},
              str(fixture): {'bind': '/fixture', 'mode': 'ro'},
              str(destination): {'bind': '/out', 'mode': 'rw'}}
    if raw_root is not None:
        for name in raw_directories or ['']:
            if name: safe_name(name)
            mounts[str(raw_root/name)] = {'bind': '/raw'+('/'+name if name else ''), 'mode': 'ro'}
    return dict(image=image, command=['--fixture', '/fixture', '--build', '/build',
        '--frontend', '/frontend', '--output', '/out', '--memory-mib', str(memory_mib)],
        entrypoint=['python3', '/tools/visualiser_capacity_worker.py'],
        user=str(os.getuid())+':'+str(os.getgid()), network_mode='none', read_only=True,
        cap_drop=['ALL'], security_opt=['no-new-privileges:true'], pids_limit=128,
        mem_limit=memory_mib*MIB, memswap_limit=memory_mib*MIB, nano_cpus=1_000_000_000,
        tmpfs={'/tmp': 'rw,noexec,nosuid,size=67108864,mode=1777'}, volumes=mounts,
        environment={'PYTHONDONTWRITEBYTECODE': '1', 'LANG': 'C.UTF-8'},
        labels={LABEL: identity}, log_config={'type': 'json-file', 'config': {'max-size': '2m', 'max-file': '1'}})


def cleanup_container(container, identity):
    container.reload()
    if container.attrs['Config'].get('Labels', {}).get(LABEL) != identity:
        raise RuntimeError('Refusing to clean a container with another identity')
    if container.attrs['State']['Running']:
        container.kill()
        container.wait(timeout=30)
    container.remove(v=True)


def run_case(client, args, snapshot, fixture_path, fixture, memory, count, directory):
    directory.mkdir(mode=0o700)
    preflight = preflight_space(directory, memory, count)
    identity = uuid4().hex
    record = dict(passed=False, cleanup=False, memory_mib_per_worker=memory,
        cpu_millis_per_worker=1000, workers=count, preflight=preflight, results=[])
    owned = []
    started = time.monotonic()
    try:
        for number in range(count):
            target = directory/('worker-'+str(number+1)); target.mkdir(mode=0o700)
            options = container_options(args.image_id, snapshot, fixture_path,
                Path(fixture['raw_root']) if fixture['mode'] == 'raw-native' else None,
                target, memory, identity, fixture.get('raw_directories'))
            container = client.containers.create(**options)
            owned.append((container, target))
        for container, _ in owned:
            container.start()
        deadline = started+args.timeout_seconds
        while True:
            running = False
            for container, _ in owned:
                container.reload(); running |= container.attrs['State']['Running']
            if not running:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Capacity case exceeded its time bound')
            if shutil.disk_usage(directory).free < GIB:
                raise RuntimeError('Low disk reserve; bounded capacity case stopped')
            time.sleep(.5)
        record['elapsed_seconds'] = round(time.monotonic()-started, 3)
        for container, target in owned:
            container.reload()
            state = container.attrs['State']
            (target/'container.log').write_bytes(container.logs()[-2*MIB:])
            item = dict(exit_code=state['ExitCode'], oom_killed=bool(state.get('OOMKilled')),
                        container_id=container.id)
            if (target/'report.json').is_file():
                item['report'] = read_json(target/'report.json')
            item['passed'] = item['exit_code'] == 0 and not item['oom_killed'] and item.get('report', {}).get('passed') is True
            record['results'].append(item)
        record['passed'] = all(item['passed'] for item in record['results'])
        if record['passed']:
            record['comparisons_per_hour'] = count*3600/record['elapsed_seconds']
    except Exception as error:
        record.update(error_type=type(error).__name__, error=str(error)[:500])
    finally:
        errors = []
        for container, target in owned:
            try:
                # Preserve bounded diagnostics even if timeout/OOM prevents a worker report.
                (target/'container.log').write_bytes(container.logs()[-2*MIB:])
            except Exception as error:
                record.setdefault('diagnostic_errors', []).append(type(error).__name__)
                record['passed'] = False
            try:
                cleanup_container(container, identity)
            except Exception as error:
                errors.append(dict(container_id=container.id, error_type=type(error).__name__))
        record['cleanup'] = not errors
        record['cleanup_errors'] = errors
        # An OOM may prevent the worker's finally block. All containers are gone
        # before reclaiming only their generated private scratch directories.
        if record['cleanup']:
            for _, target in owned:
                scratch = target/'private'
                if scratch.exists() and not scratch.is_symlink():
                    shutil.rmtree(scratch)
        write_json(directory/'case.json', record)
    if not record['cleanup']:
        raise RuntimeError('Container removal unconfirmed; inspect '+str(directory))
    return record


def run(args):
    import docker
    if os.getuid() == 0:
        raise ValueError('Run as your normal Docker-enabled user, not root')
    for name in ('frontend', 'fixture', 'build'):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.output = args.output.absolute()
    if any(p.is_symlink() for p in (args.output, *args.output.parents)):
        raise ValueError('Output must not use symlinked directories')
    fixture, raw_root = check_fixture(args.fixture)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_attribution(args.output)
    result = dict(format='simpaths.visualiser.capacity-trials.v1', passed=False,
        trials_completed=False, production_changed=False, model_executions=False,
        mode=fixture['mode'], started_at=datetime.now(timezone.utc).isoformat(), cases=[],
        limitations=fixture['limitations']+[
            'One/two workers are independent whole comparisons, not parallel seeds within one comparison.',
            'No active website or simulation load; no HTTP latency claim.',
            'Input verification and repeated trials warm filesystem caches; physical I/O is recorded separately.'])
    client = None
    try:
        client = docker.from_env(timeout=30)
        with exclusive(args.output.parent):
            image = client.images.get(args.image)
            args.image_id = image.id
            result['image_id'] = image.id
            if client.info().get('CgroupVersion') != '2':
                raise ValueError('These whole-worker memory/CPU measurements require cgroup v2')
            snapshot = args.output/'snapshot'; snapshot.mkdir(mode=0o700)
            result['sources'] = snapshot_tools(args.frontend.resolve(strict=True), args.build.resolve(strict=True), snapshot, fixture)
            fixture_sources = {'manifest.json': args.fixture/'manifest.json'}
            if fixture['mode'] == 'summary-replay':
                fixture_sources.update({name: args.fixture/name for name in fixture['files']})
            result['fixture_files'] = snapshot_files(fixture_sources, snapshot/'fixture')
            result['fixture'] = fixture
            for round_number in range(1, args.rounds+1):
                for memory in args.memory_mib:
                    for count in args.workers:
                        name = f'round-{round_number}-{memory}m-{count}workers'
                        print('Running '+name+'; each worker has 1 vCPU', flush=True)
                        record = run_case(client, args, snapshot, snapshot/'fixture', fixture,
                                          memory, count, args.output/name)
                        result['cases'].append(dict(name=name, **record))
                        print(('PASS: ' if record['passed'] else 'FAILED: ')+name, flush=True)
            # Immutable fixture identity must survive every case, including failed ones.
            check_fixture(args.fixture)
            result['trials_completed'] = True
            identities = {(w['report']['publication_sha256'], w['report']['view_sha256'],
                           w['report']['index']['index_sha256'], w['report']['catalogue']['sha256'])
                for c in result['cases'] for w in c['results'] if w['passed']}
            result['successful_cases_have_identical_results'] = len(identities) == 1
            result['passed'] = all(c['passed'] for c in result['cases']) and len(identities) == 1
    except BaseException as error:
        result.update(error_type=type(error).__name__, error=str(error)[:500])
        raise
    finally:
        write_json(args.output/'report.json', result)
        lines = ['# Visualiser capacity trials', '',
            'Mode: '+fixture['mode']+'. No production settings changed.', '',
            '| Case | Passed | Seconds | Comparisons/hour |', '| --- | --- | ---: | ---: |']
        for c in result['cases']:
            lines.append(f"| {c['name']} | {c['passed']} | {c.get('elapsed_seconds', '—')} | {c.get('comparisons_per_hour', 0):.3f} |")
        lines += ['', *('- '+item for item in result['limitations'])]
        (args.output/'comparison.md').write_text('\n'.join(lines)+'\n')
        if client is not None:
            client.close()
    print('Evidence: '+str(args.output/'report.json'))
    return 0 if result['passed'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    raw = commands.add_parser('prepare-raw', help='Verify retained native research sources without copying them')
    raw.add_argument('--frontend', type=Path, required=True)
    raw.add_argument('--prepared', type=Path, required=True)
    raw.add_argument('--baseline', type=Path, required=True)
    raw.add_argument('--alternative', type=Path, required=True)
    raw.add_argument('--repetitions', type=int, choices=range(1, 11), default=10)
    raw.add_argument('--build', type=Path, required=True)
    raw.add_argument('--output', type=Path, required=True)
    rebound = commands.add_parser('rebind-replay', help='New fixture using an updated bundle of identical scientific sources')
    for name in ('fixture','source-build','build','output'):
        rebound.add_argument('--'+name, type=Path, required=True)
    rebound.add_argument('--alternatives', type=int, choices=(1,2,3), default=2)
    compare = commands.add_parser('compare', help='Equal work, bounded pairs, cold/warm summaries and CPU sensitivity')
    for name in ('frontend','fixture','build','output'):
        compare.add_argument('--'+name, type=Path, required=True)
    compare.add_argument('--image', default='simpaths-visualiser-capacity:20261015')
    compare.add_argument('--memory-mib', type=int, nargs='+', choices=(2048,3072,4096), default=[3072,2048,4096])
    compare.add_argument('--workers', type=int, nargs='+', choices=(1,2), default=[1,2])
    compare.add_argument('--cpu-millis', type=int, nargs='+', choices=(1000,2000), default=[1000,2000])
    compare.add_argument('--cache-modes', nargs='+', choices=('cold','warm'), default=['cold','warm'])
    compare.add_argument('--tasks-per-case', type=int, choices=range(2,9), default=2)
    compare.add_argument('--view-reads', type=int, choices=range(1,21), default=5)
    compare.add_argument('--rounds', type=int, choices=(1,2,3), default=1)
    compare.add_argument('--timeout-seconds', type=int, default=10800)
    compare.add_argument('--cancellation-check', action='store_true', help='Separate forced-stop/cached-retry check after timing trials')
    compare.add_argument('--cancellation-memory-mib', type=int, choices=(2048,3072,4096), default=3072)
    trial = commands.add_parser('run', help='Run bounded independent Docker workers; networking disabled')
    for name in ('frontend', 'fixture', 'build', 'output'):
        trial.add_argument('--'+name, type=Path, required=True)
    trial.add_argument('--image', default='simpaths-visualiser-capacity:20261015')
    trial.add_argument('--memory-mib', type=int, nargs='+', choices=(2048, 3072, 4096), default=[3072, 2048, 4096])
    trial.add_argument('--workers', type=int, nargs='+', choices=(1, 2), default=[1, 2])
    trial.add_argument('--rounds', type=int, choices=(1, 2, 3), default=1)
    trial.add_argument('--timeout-seconds', type=int, default=10800)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.command == 'prepare-raw':
        prepare_raw(args)
        return 0
    if args.command == 'rebind-replay':
        from deploy.multirun.visualiser_capacity_comparison import rebind
        return rebind(args)
    if not 60 <= args.timeout_seconds <= 43200:
        parser.error('Use a per-case time bound of 60..43200 seconds')
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    if args.command == 'compare':
        if args.cache_modes not in (['cold'], ['cold','warm']):
            parser.error('Run cold alone, or cold followed by warm; never imply an unprimed cache is warm')
        for name in ('workers','cpu_millis','memory_mib'):
            if len(getattr(args,name))!=len(set(getattr(args,name))):
                parser.error('Do not repeat matrix values: '+name)
        from deploy.multirun.visualiser_capacity_comparison import run as compare_run
        return compare_run(args)
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
