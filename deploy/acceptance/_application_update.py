"""(C) Copyright 2026, by Ross Richardson

Immutable Git bundles and test-only migrations for application update acceptance.
Never writes a checkout, changes Git refs or accepts a production database.
@author ross richardson
"""
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile


HISTORICAL_FRONTEND = 'e6db2a8f84b80f76f355c9b9ff15d1c69fb1507e'
HISTORICAL_HOST = 'bc7e7c465'
SEPARATED_FRONTEND = '82aad9a0c33440aed2f7f7a0c27dcd65bcc64afa'
SEPARATED_HOST = '47dc95a9b8788a308ef3514693b8efe0c62c85ee'
PREVIOUS_FRONTEND = 'e19212bd659e410894530d54ad53cfb5158a34be'
PREVIOUS_HOST = '86b91cb1f87429c15fc579e121020b3ee34ead60'
FIXTURES = ('_application_update.py', '_recovery_fixture.py', '_recovery_model.py',
            '_https_fixture.py', '_supervisor.py', '_postgres_outage.py', '_single_rpc.py', '_update_rpc.py',
            '_update_retained.py')
FRONTEND_FIXTURES = ('tests/browser/single_rpc_fixture.py',)
PUBLIC_ASSETS = ('static/jasmine_main.js', 'jasmine_web/batch/browser_static/index.html',
                 'jasmine_web/batch/browser_static/batch.css')
CONTRACT_FILES = {
    'frontend': (
        'jasmine_web/batch/results.py', 'jasmine_web/batch/downloads.py',
        'jasmine_web/batch/visualiser.py', 'jasmine_web/vm_recovery.py',
        'jasmine_web/batch/service_rpc.py', 'jasmine_web/batch/remote_service.py',
        'jasmine_web/single_rpc.py', 'jasmine_web/single_boundary.py', 'jasmine_web/single_runtime.py',
        'jasmine_web/batch/visualiser_sources.py', 'jasmine_web/batch/visualiser_storage.py',
        'jasmine_web/batch/visualiser_preparation.py', 'jasmine_web/batch/visualiser_summary_retention.py',
        'jasmine_web/batch/visualiser_export.py', 'jasmine_web/batch/processing_handover.py',
        'jasmine_web/batch/performance_archive.py', 'jasmine_web/batch/performance_exports.py',
        'jasmine_web/batch/performance_trace.py', 'jasmine_web/batch/experiment_extensions.py'),
    'host': (
        'deploy/multirun/backup.py', 'deploy/multirun/backup_live.py',
        'deploy/multirun/backup_single.py', 'deploy/multirun/backup_visualiser.py',
        'deploy/multirun/releases.py', 'deploy/multirun/prepared_dataset.py',
        'deploy/multirun/resource_policy.py', 'deploy/multirun/visualiser_backend.py',
        'deploy/multirun/visualiser_summary_cache.py'),
}
PLAN_FORMAT = 'simpaths.application-update-plan.v1'
MAX_FILES, MAX_BYTES, MAX_FILE_BYTES = 4000, 32*1024**2, 16*1024**2


def digest(value):
    return hashlib.sha256(value).hexdigest()


def ordinary(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Linked rehearsal paths are not permitted')
    return path


def git(repo, *args, destination=None):
    result = subprocess.run(['git', '-C', str(ordinary(repo)), *args],
        stdout=destination if destination else subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if result.returncode:
        raise ValueError('The requested local Git snapshot is unavailable')
    return result.stdout


def commit(repo, reference):
    if (not isinstance(reference, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,159}', reference)
            or '..' in reference):
        raise ValueError('Use a local commit or ordinary Git ref')
    value = git(repo, 'rev-parse', '--verify', '--end-of-options', reference+'^{commit}').decode().strip()
    if not re.fullmatch('[a-f0-9]{40}', value):
        raise ValueError('Expected a complete Git commit identifier')
    return value


def inventory(root):
    root = ordinary(root)
    files, total = {}, 0
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Release bundles must contain ordinary, unlinked files')
        total += info.st_size
        if len(files) >= MAX_FILES or info.st_size > MAX_FILE_BYTES or total > MAX_BYTES:
            raise ValueError('Release bundle exceeds the small rehearsal bounds')
        files[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return files


def unpack(stream, destination):
    """Extract ordinary Git files explicitly, without tar path/link extraction."""
    root = ordinary(destination)
    if root.exists():
        raise ValueError('Release bundle destination already exists')
    root.mkdir(mode=0o700, parents=True)
    names, total = set(), 0
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            key = member.name.rstrip('/')
            path = PurePosixPath(key)
            if (not key or path.is_absolute() or any(part in ('', '.', '..', '.git') for part in path.parts)
                    or path.as_posix() != key or '\\' in key or '\x00' in key or key in names
                    or path.name in ('.env', '.sesskey')):
                raise ValueError('Unsafe or duplicated Git archive path')
            names.add(key)
            if len(names) > MAX_FILES:
                raise ValueError('Git archive exceeds the small rehearsal bounds')
            target = root.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            total += member.size
            if not member.isfile() or member.size > MAX_FILE_BYTES or total > MAX_BYTES:
                raise ValueError('Git archive contains links or excessive data')
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, length=65536)
            target.chmod(0o700 if member.mode & 0o111 else 0o600)
    return inventory(root)


def snapshot(repo, reference, destination, *, paths=()):
    revision = commit(repo, reference)
    with tempfile.TemporaryFile() as stream:
        git(repo, 'archive', '--format=tar', revision, '--', *(paths or ('.',)),
            ':(exclude).sesskey', ':(exclude).env', destination=stream)
        stream.seek(0)
        files = unpack(stream, destination)
    return dict(commit=revision, directory=str(ordinary(destination)), files=files)


def migrations(frontend):
    """Read the actual migration lists, rather than assuming every SQL file is used."""
    result = {}
    for component, filename, classname, method in (
            ('batch', 'jasmine_web/batch/store.py', 'Queue', 'migrations'),
            ('single', 'jasmine_web/vm_state.py', 'PostgresVMState', 'migrate')):
        path = ordinary(frontend)/filename
        tree = ast.parse(path.read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == classname)
        fn = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method)
        lists = [node.args[0] for node in ast.walk(fn) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'enumerate' and node.args
                 and isinstance(node.args[0], (ast.List, ast.Tuple))]
        if len(lists) != 1:
            raise ValueError('Review the changed migration declaration before updating the rehearsal')
        names = ast.literal_eval(lists[0])
        if not names or any(not isinstance(name, str) or Path(name).name != name or not name.endswith('.sql') for name in names):
            raise ValueError('Invalid migration declaration')
        result[component] = [dict(version=i, name=name, sha256=digest(path.with_name(name).read_bytes()))
                             for i, name in enumerate(names, 1)]
    return result


def bundle(host, frontend, destination, *, host_ref, frontend_ref):
    root = ordinary(destination)
    if root.exists():
        raise ValueError('Choose a new release bundle')
    root.mkdir(mode=0o700)
    value = dict(host=snapshot(host, host_ref, root/'SimPaths', paths=('deploy',)),
                 frontend=snapshot(frontend, frontend_ref, root/'JAS-mine-web'), synthetic=False)
    # The same isolated model adapters exercise each historical production tree.
    # Record every overlay separately from the committed application files.
    overlays = {}
    for name in FIXTURES:
        key = 'deploy/acceptance/'+name
        source = ordinary(host)/key
        target = root/'SimPaths'/key
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes()); target.chmod(0o600)
        overlays[key] = digest(target.read_bytes())
    value['fixture_overlays'] = overlays
    overlays = {}
    for name in FRONTEND_FIXTURES:
        source = ordinary(frontend)/name
        target = root/'JAS-mine-web'/name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes()); target.chmod(0o600)
        overlays[name] = digest(target.read_bytes())
    value['frontend_fixture_overlays'] = overlays
    value['host']['files'] = inventory(root/'SimPaths')
    value['frontend']['files'] = inventory(root/'JAS-mine-web')
    value['migrations'] = migrations(root/'JAS-mine-web')
    return value


def verify(value):
    for component in ('host', 'frontend'):
        if inventory(value[component]['directory']) != value[component]['files']:
            raise ValueError('Application bundle changed after it was selected')
    if migrations(value['frontend']['directory']) != value['migrations']:
        raise ValueError('Application migration inventory changed')


@contextmanager
def read_only_directories(value):
    """Prevent helper bytecode/cache creation without excluding any inventory files.

    The installed service cannot create files in its code directories. Reproduce
    that here even for helpers whose deliberately minimal environment omits
    PYTHONDONTWRITEBYTECODE. Preserve and restore every original directory mode.
    """
    verify(value)
    modes = []
    try:
        for component in ('host', 'frontend'):
            root = ordinary(value[component]['directory'])
            for path in (root, *sorted(root.rglob('*'))):
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    if info.st_uid != os.geteuid():
                        raise ValueError('Rehearsal code directories must be owned by the test user')
                    mode = stat.S_IMODE(info.st_mode)
                    modes.append((path, mode))
                    path.chmod(mode & ~0o222)
        yield
    finally:
        for path, mode in reversed(modes):
            path.chmod(mode)


def deployment_file(component, name):
    """Ignore documentation/test overlays when identifying a real release change."""
    path = PurePosixPath(name)
    if path.suffix == '.txt' and (path.name.startswith('requirements') or path.name.startswith('constraints')):
        return True
    if component == 'frontend':
        return (name in ('app.py', 'session_manager.py', 'container_manager.py', 'Dockerfile')
                or name.startswith(('jasmine_web/', 'static/'))
                and path.suffix in ('.py', '.sql', '.js', '.css', '.html'))
    return (name.startswith('deploy/multirun/') and path.suffix in ('.py', '.java', '.sh')
            or name.startswith('deploy/web-') and path.name == 'Dockerfile')


def compatible(previous, candidate):
    verify(previous); verify(candidate)
    if previous['migrations'] != candidate['migrations']:
        raise ValueError('Direct rollback requires matching reviewed migration histories')
    if all(previous[key]['commit'] == candidate[key]['commit'] for key in ('host', 'frontend')):
        raise ValueError('Select actual different application snapshots for this rehearsal')
    changes = {}
    for component in ('host', 'frontend'):
        left, right = previous[component]['files'], candidate[component]['files']
        changes[component] = [name for name in sorted(left.keys() | right.keys())
            if left.get(name) != right.get(name) and deployment_file(component, name)]
    if not any(changes.values()):
        raise ValueError('Select different deployment code or dependency declarations, not only documentation or fixtures')
    # Exact equality is a conservative gate for this reviewed pair, not a
    # general claim that matching SQL makes any code-only rollback safe.
    for component, names in CONTRACT_FILES.items():
        for name in names:
            if name not in previous[component]['files'] or name not in candidate[component]['files']:
                raise ValueError('Review missing result/recovery/RPC formats before selecting another rollback pair')
            if previous[component]['files'][name] != candidate[component]['files'][name]:
                raise ValueError('Review changed result/recovery/RPC formats before selecting another rollback pair')
    return dict(changed_deployment_files=changes,
        changed_public_assets=[key for key in PUBLIC_ASSETS
            if previous['frontend']['files'][key] != candidate['frontend']['files'][key]],
        identical_contract_files={key: list(names) for key, names in CONTRACT_FILES.items()},
        dependency_environment='One qualified candidate environment for both code versions; dependency downgrades are not rehearsed')


def plan_sources(host, frontend):
    # Recovery fixtures also import ordinary model/frontend modules and test
    # helpers. Bind that full live dependency footprint, including unit templates;
    # committed bundle inventories independently bind all served assets.
    paths = set()
    for root in (ordinary(host)/'deploy', *(ordinary(frontend)/name
                 for name in ('jasmine_web', 'tests', 'scripts', 'deploy'))):
        paths.update(path for path in root.rglob('*')
                     if path.suffix in ('.py', '.java', '.sh', '.sql', '.service', '.toml', '.txt'))
    paths.update(ordinary(frontend)/name for name in ('app.py', 'session_manager.py', 'container_manager.py'))
    for pattern in ('requirements*.txt', 'constraints*.txt'):
        paths.update(ordinary(frontend).glob(pattern))
    return {str(path): digest(ordinary(path).read_bytes()) for path in sorted(paths)}


def create_plan(host, frontend, destination, *, previous_host=PREVIOUS_HOST,
                previous_frontend=PREVIOUS_FRONTEND):
    """Freeze real commits and separately hashed fixtures without native services."""
    root = ordinary(destination)
    if root.exists() or any(root.is_relative_to(ordinary(repo)) for repo in (host, frontend)):
        raise ValueError('Choose a new private plan directory outside the source checkouts')
    root.mkdir(mode=0o700)
    (root/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Frozen application update selections and qualification evidence.\n'
        '@author ross richardson -->\n\nCopied source retains its existing attribution and licences.\n')
    sources = plan_sources(host, frontend)
    previous = bundle(host, frontend, root/'previous', host_ref=previous_host, frontend_ref=previous_frontend)
    candidate = bundle(host, frontend, root/'candidate', host_ref='HEAD', frontend_ref='HEAD')
    review = compatible(previous, candidate)
    if plan_sources(host, frontend) != sources:
        raise ValueError('Rehearsal fixtures changed during plan creation')
    value = dict(format=PLAN_FORMAT, created_at=datetime.now(timezone.utc).isoformat(),
        previous=previous, candidate=candidate, compatibility=review, source_hashes=sources,
        production_acceptance=False)
    target = root/'plan.json'
    with target.open('x') as stream:
        os.chmod(target, 0o600)
        json.dump(value, stream, indent=2, allow_nan=False); stream.write('\n')
    return target


def load_plan(path, host, frontend):
    path = ordinary(path)
    if path.stat().st_size > 2*1024**2:
        raise ValueError('Update plan exceeds its metadata bound')
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('Duplicate update-plan field')
            value[key] = item
        return value
    with path.open() as source:
        value = json.load(source, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite update-plan value')))
    if (set(value) != {'format', 'created_at', 'previous', 'candidate', 'compatibility',
                       'source_hashes', 'production_acceptance'}
            or value['format'] != PLAN_FORMAT or value['production_acceptance'] is not False):
        raise ValueError('Unsupported update selection plan')
    if compatible(value['previous'], value['candidate']) != value['compatibility']:
        raise ValueError('Update compatibility review changed')
    sources = value['source_hashes']
    if sources != plan_sources(host, frontend):
        raise ValueError('Rehearsal source changed after planning')
    return value


def future(candidate, destination):
    """Append real checksummed migrations only to a private copy of the code."""
    verify(candidate)
    root = ordinary(destination)
    if root.exists():
        raise ValueError('Synthetic future bundle already exists')
    root.mkdir(mode=0o700)
    value = json.loads(json.dumps(candidate))
    for component, folder in (('host', 'SimPaths'), ('frontend', 'JAS-mine-web')):
        target = root/folder
        shutil.copytree(candidate[component]['directory'], target)
        # The selected native bundle has read-only code directories. Only this
        # separate synthetic copy may receive an additional migration.
        for directory in (target, *sorted(target.rglob('*'))):
            if directory.is_dir():
                directory.chmod(stat.S_IMODE(directory.stat().st_mode) | 0o700)
        value[component]['directory'] = str(target)
    frontend = root/'JAS-mine-web'
    for component, filename, classname, method in (
            ('batch', 'jasmine_web/batch/store.py', 'Queue', 'migrations'),
            ('single', 'jasmine_web/vm_state.py', 'PostgresVMState', 'migrate')):
        version=len(candidate['migrations'][component])+1
        name=f'{version:03d}_update_rehearsal.sql' if component=='batch' else f'vm_state_{version:03d}_update_rehearsal.sql'
        path = frontend/filename; source = path.read_text(); tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == classname)
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method)
        node = next(n.args[0] for n in ast.walk(fn) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == 'enumerate'
                    and n.args and isinstance(n.args[0], ast.Tuple))
        offset = sum(len(line) for line in source.splitlines(keepends=True)[:node.end_lineno-1])+node.end_col_offset-1
        if source[offset] != ')':
            raise ValueError('Unexpected migration syntax; review the fixture')
        path.write_text(source[:offset]+', '+repr(name)+source[offset:])
        sql = ('-- (C) Copyright 2026, by Ross Richardson\n'
               '-- Fictional extra schema version for update/rollback acceptance only.\n'
               '-- @author ross richardson\n'
               'CREATE TABLE update_rehearsal_marker (value text NOT NULL);\n'
               "INSERT INTO update_rehearsal_marker VALUES ('FICTIONAL_UPDATE_SCHEMA');\n")
        if path.with_name(name).exists():
            raise ValueError('Synthetic migration would replace an existing file')
        path.with_name(name).write_text(sql)
    value.update(synthetic=True, migrations=migrations(frontend))
    value['frontend']['files'] = inventory(frontend)
    if any(len(value['migrations'][k]) != len(candidate['migrations'][k])+1 for k in ('single', 'batch')):
        raise ValueError('Review the synthetic migration versions before running the rehearsal')
    return value


def configure(value):
    """Select imports in a fresh process before loading either application's code."""
    verify(value)
    if any(name == 'jasmine_web' or name.startswith('jasmine_web.') or name == 'deploy.multirun'
           or name.startswith('deploy.multirun.') for name in __import__('sys').modules):
        raise ValueError('Select release imports before loading application modules')
    import sys
    paths = [value['host']['directory'], value['frontend']['directory'],
             str(Path(value['frontend']['directory'])/'tests/batch')]
    sys.path[:0] = paths
    # The runner's stdlib-only helper may already have loaded the deploy package.
    # No production submodule may survive from the maintainer checkout.
    if 'deploy' in sys.modules:
        sys.modules['deploy'].__path__ = [str(Path(paths[0])/'deploy')]
    if 'deploy.acceptance' in sys.modules:
        sys.modules['deploy.acceptance'].__path__ = [str(Path(paths[0])/'deploy/acceptance')]
    os.environ.update(JASMINE_WEB_REPO=paths[1], PYTHONDONTWRITEBYTECODE='1')
    sys.dont_write_bytecode = True


def loaded_source(module, root):
    path = ordinary(module.__file__)
    if not path.is_relative_to(ordinary(root)):
        raise ValueError('Application imports escaped the selected Git bundle')
    return dict(file=path.relative_to(root).as_posix(), sha256=digest(path.read_bytes()))


def probe(settings, dsn):
    """Exercise the actual startup migrations, without dispatching any models."""
    from psycopg.conninfo import conninfo_to_dict
    values = conninfo_to_dict(dsn)
    if (values.get('host') != '127.0.0.1' or values.get('user') != 'postgres'
            or values.get('dbname') != settings['database']
            or not re.fullmatch(r'(jasmine_queue_test|restore_[a-f0-9]{32})', settings['database'])
            or not re.fullmatch(r'test_batch_[a-f0-9]{32}', settings['batch_schema'])
            or not re.fullmatch(r'vm_update_[a-f0-9]{32}', settings['single_schema'])
            or settings['pool'] != 'test'
            or set(values)-{'host', 'port', 'dbname', 'user', 'password'}):
        raise ValueError('Only the isolated update backup fixture is eligible')
    from jasmine_web.batch import store as batch
    from jasmine_web import vm_state as single
    result = dict(checks=[], sources=dict(
        batch=loaded_source(batch, settings['bundle']['frontend']['directory']),
        single=loaded_source(single, settings['bundle']['frontend']['directory'])))
    queue = batch.Queue(dsn, settings['pool'], schema=settings['batch_schema'])
    state = single.PostgresVMState(dsn, schema=settings['single_schema'],
        shared_pool=dict(pool_id=settings['pool'], schema=settings['batch_schema']))
    try:
        for component, operation in (('batch', queue.migrate), ('single', state.migrate)):
            try:
                operation()
                result['checks'].append(dict(component=component, accepted=True))
            except (batch.Conflict, ValueError, single.VMStateUnavailable) as error:
                result['checks'].append(dict(component=component, accepted=False, error=type(error).__name__))
    finally:
        state.close()
    result['accepted'] = all(v['accepted'] for v in result['checks'])
    return result
