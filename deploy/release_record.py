#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Read-only candidate deployment inventory: commits, artifacts and outstanding checks.
Never starts services, calls Docker, changes releases, or reads configuration secrets.
@author ross richardson
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

FORMAT = 'simpaths.deployment-release-record.v1'
MIB = 1024**2
MAX_JSON, MAX_FILE, MAX_HASH_BYTES, MAX_FILES = 2*MIB, 128*MIB, 256*MIB, 2000
REPOSITORIES = ('simpaths', 'core', 'frontend', 'visualiser')
PREPARED_FORMATS = ('simpaths.private-research-calibration.v1',
                    'simpaths.multirun.prepared-quickstart.v1', 'simpaths.multirun.prepared-inputs.v1')
ADAPTERS = ('build.cjs', 'adapt_app.cjs', 'entry.jsx', 'comparison_data.mjs',
            'section_source.mjs', 'section_cache.mjs', 'runner.cjs', 'index.html', 'visualiser.css')
PENDING = [
    'Confirm host, purchasing/billing, handover account, operating system and domain.',
    'Verify installed images, resolved dependencies, advisory inventory and runtime versions on the host.',
    'Establish model JAR build provenance; current checkout commits alone do not identify its build sources.',
    'Confirm licences/notices and permitted redistribution with the component and data maintainers.',
    'Agree supported inputs/populations/horizons/repetitions and permitted aggregate releases.',
    'Finish calibration and hosted load tests before selecting starting profiles, concurrency and runtime budgets.',
    'Set persistent private storage, quota enforcement, physical reserves, caches and retention.',
    'Configure protected keys/DSN/SMTP and test real inbox delivery, TLS, firewall, boot and recovery.',
    'Agree provider/application backup arrangements and independently test the selected recovery method.',
]


class RecordError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sha(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value):
        raise RecordError('Expected a SHA-256 identifier')
    return value


def image(value):
    if type(value) is not str or not re.fullmatch('sha256:[a-f0-9]{64}', value):
        raise RecordError('Use immutable image IDs, not mutable tags')
    return value


def relative(value):
    if (type(value) is not str or not 1 <= len(value) <= 240 or '\\' in value
            or any(ord(c) < 32 for c in value) or any(p in ('', '.', '..') for p in value.split('/'))
            or PurePosixPath(value).is_absolute()):
        raise RecordError('Invalid artifact-relative name')
    return value


def ordinary(path):
    path = Path(path).expanduser().absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise RecordError('Inventory paths must not contain traversal or symlinks')
    return path


def state(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Reader:
    """Bound hashing and reject files changed or replaced during collection."""
    def __init__(self):
        self.bytes = 0
        self.files = {}
        self.states = {}
        self.directories = {}

    def file(self, path, *, limit=MAX_FILE, contents=False):
        path = ordinary(path)
        if path in self.states:
            if state(path.lstat()) != self.states[path]:
                raise RecordError('Artifact changed between reads')
            if self.files[path]['bytes'] > limit:
                raise RecordError('Previously read file exceeds this read limit')
            if not contents:return dict(self.files[path])
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_size > limit
                    or self.bytes + before.st_size > MAX_HASH_BYTES):
                raise RecordError('File type, size or total hash budget exceeds inventory bounds')
            hasher = hashlib.sha256(); parts = []; total = 0
            while block := stream.read(min(MIB, limit + 1 - total)):
                total += len(block)
                if total > limit or self.bytes + total > MAX_HASH_BYTES:
                    raise RecordError('File grew beyond inventory bounds')
                hasher.update(block)
                if contents:parts.append(block)
            after = os.fstat(stream.fileno())
            if state(before) != state(after) or state(before) != state(path.lstat()):
                raise RecordError('Artifact changed during collection')
        self.bytes += total
        self.states[path] = state(before)
        result = {'bytes':total, 'sha256':hasher.hexdigest()}
        self.files[path] = result
        return (b''.join(parts), result) if contents else result

    def json(self, path):
        data, fp = self.file(path, limit=MAX_JSON, contents=True)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:raise RecordError('Duplicate JSON field')
                result[key] = value
            return result
        def invalid(_):raise RecordError('Non-finite JSON value')
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid), fp

    def finish(self):
        for path, expected in self.states.items():
            if state(ordinary(path).lstat()) != expected:
                raise RecordError('Artifact changed before inventory completion')
        for root, expected in self.directories.items():
            if files_under(root) != expected:
                raise RecordError('Artifact directory metadata changed before inventory completion')

    def directory(self, root):
        root = ordinary(root); value = files_under(root)
        if root in self.directories and self.directories[root] != value:
            raise RecordError('Artifact directory metadata changed between reads')
        self.directories[root] = value
        return value


def fingerprint_value(value):
    if (type(value) is not dict or set(value) != {'bytes', 'sha256'}
            or type(value['bytes']) is not int or not 0 <= value['bytes'] < 2**63):
        raise RecordError('Invalid declared file fingerprint')
    sha(value['sha256'])
    return value


def manifest(value):
    if type(value) is not dict or not 1 <= len(value) <= MAX_FILES:
        raise RecordError('Expected a bounded file manifest')
    for name, fp in value.items():relative(name);fingerprint_value(fp)
    return value


def files_under(root):
    """Inventory names/stat sizes only; never read prepared data contents."""
    root = ordinary(root)
    if not root.is_dir():raise RecordError('Expected an existing artifact directory')
    result = {}; count = 0
    for parent, directories, files in os.walk(root, followlinks=False):
        count += len(directories) + len(files)
        if count > MAX_FILES:raise RecordError('Artifact directory exceeds inventory bounds')
        for name in directories:
            path = Path(parent)/name
            if path.is_symlink() or not stat.S_ISDIR(path.lstat().st_mode):
                raise RecordError('Artifact directories must be ordinary directories')
        for name in files:
            path = ordinary(Path(parent)/name); info = path.lstat()
            if not stat.S_ISREG(info.st_mode):raise RecordError('Artifacts must be ordinary files')
            result[relative(path.relative_to(root).as_posix())] = state(info)
    return result


def git(repo, *args):
    result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
        '-C', str(ordinary(repo)), *args], stdin=subprocess.DEVNULL, capture_output=True,
        timeout=15, env={**os.environ, 'GIT_OPTIONAL_LOCKS':'0', 'GIT_TERMINAL_PROMPT':'0', 'LC_ALL':'C'})
    if result.returncode or len(result.stdout) > MAX_JSON:
        raise RecordError('Bounded local Git metadata is unavailable')
    return result.stdout


def git_state(repo):
    repo = ordinary(repo)
    if Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()) != repo:
        raise RecordError('Select repository roots, not a subdirectory')
    commit = git(repo, 'rev-parse', 'HEAD').decode().strip()
    tree = git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip()
    if not all(re.fullmatch('[a-f0-9]{40}', value) for value in (commit, tree)):
        raise RecordError('Expected full Git commit and tree identifiers')
    tokens = git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=normal',
                 '--ignore-submodules=all').decode().split('\0')
    changes = []; index = 0
    while index < len(tokens) and tokens[index]:
        token = tokens[index]; index += 1
        if len(token) < 4 or token[2] != ' ':raise RecordError('Unexpected Git status format')
        item = {'status':token[:2], 'path':relative(token[3:].rstrip('/'))}
        if any(c in token[:2] for c in 'RC'):
            if index >= len(tokens) or not tokens[index]:
                raise RecordError('Incomplete Git rename status')
            item['previous_path'] = relative(tokens[index]); index += 1
        changes.append(item)
        if len(changes) > MAX_FILES:raise RecordError('Git change inventory exceeds bounds')
    return {'commit':commit, 'tree':tree, 'dirty':bool(changes), 'changes':changes,
            'scope':'Current HEAD and non-ignored status; dirty-file contents and ignored files are not inventoried.'}


def repository(name, repo, reader):
    root = ordinary(repo); result = git_state(root)
    dependency_names = ['pom.xml', 'package.json', 'package-lock.json', 'pyproject.toml']
    dependency_names += [p.name for p in root.glob('requirements*.txt')]
    if name == 'simpaths':dependency_names += ['deploy/multirun/requirements.txt', 'deploy/acceptance/requirements.txt']
    if len(dependency_names) > 32:raise RecordError('Too many dependency declarations')
    result['dependency_declarations'] = {n:reader.file(root/n, limit=MAX_JSON)
        for n in sorted(set(dependency_names)) if (root/n).exists() or (root/n).is_symlink()}
    licences = [p for p in root.iterdir() if re.fullmatch(r'(?:license|licence|notice)(?:[._-].*)?', p.name, re.I)]
    if len(licences) > 32:raise RecordError('Too many licence/notice files')
    result['licence_notice_files'] = {p.name:reader.file(p, limit=MAX_JSON) for p in sorted(licences)}
    result['licence_review'] = 'File presence only; licence terms, permissions and redistribution are not certified.'
    return result


def prepared_package(root, selected_model, reader):
    root = ordinary(root); receipt, fp = reader.json(root/'receipt.json')
    if type(receipt) is not dict or set(receipt) != {'identity', 'sha256', 'revision'}:
        raise RecordError('Expected a prepared receipt with complete identity')
    identity = receipt['identity']
    if (type(identity) is not dict or identity.get('format') not in PREPARED_FORMATS
            or sha(receipt['sha256']) != digest(identity)
            or type(receipt['revision']) is not str or not 1 <= len(receipt['revision']) <= 160):
        raise RecordError('Prepared receipt identity does not match its digest')
    fingerprint_value(identity['model']); manifest(identity['prepared']); image(identity['source_image'])
    actual_model = reader.file(root/'model.jar')
    if actual_model != identity['model'] or actual_model != selected_model:
        raise RecordError('Prepared model does not match the selected JAR')
    inventory = reader.directory(root/'input')
    if set(inventory) != set(identity['prepared']) or any(
            inventory[n][2] != expected['bytes'] for n, expected in identity['prepared'].items()):
        raise RecordError('Prepared input names/sizes differ from their receipt')
    profile_path = root/'profile.json'
    profile = reader.file(profile_path, limit=MAX_JSON) if profile_path.exists() or profile_path.is_symlink() else None
    if 'profile' in identity and profile != identity['profile']:
        raise RecordError('Prepared profile differs from its receipt')
    return {'receipt':fp, 'revision':receipt['revision'], 'identity_sha256':receipt['sha256'],
            'format':identity['format'], 'source_image':identity['source_image'], 'model':actual_model,
            'expected_inputs':identity['prepared'], 'input_count':len(inventory),
            'input_bytes':sum(v['bytes'] for v in identity['prepared'].values()), 'profile':profile,
            'input_names_and_sizes_verified':True, 'input_content_hashes_rechecked':False,
            'calibration_only':identity['format'] == PREPARED_FORMATS[0],
            'production_permissions_verified':False}


def visualiser_build(root, source, source_commit, reader):
    root = ordinary(root); value, fp = reader.json(root/'build.json')
    fields = {'format', 'revision', 'mode', 'transport', 'source_hashes', 'dependencies', 'toolchain', 'files'}
    if (type(value) is not dict or set(value) != fields or value.get('format') != 'simpaths.visualiser.build.v1'
            or not re.fullmatch('[a-f0-9]{40}', str(value.get('revision')))
            or type(value.get('files')) is not dict or not 1 <= len(value['files']) <= 100
            or type(value.get('source_hashes')) is not dict or not 1 <= len(value['source_hashes']) <= 100):
        raise RecordError('Invalid Visualiser build identity')
    for key in ('mode', 'transport'):
        if type(value[key]) is not str or not re.fullmatch('[a-zA-Z0-9._-]{1,100}', value[key]):
            raise RecordError('Invalid Visualiser build metadata')
    for key in ('dependencies', 'toolchain'):
        if (type(value[key]) is not dict or not 1 <= len(value[key]) <= 32
                or any(type(k) is not str or not re.fullmatch('[a-zA-Z0-9._-]{1,100}', k)
                       or type(v) is not str or not re.fullmatch('[a-zA-Z0-9.+_-]{1,100}', v)
                       for k, v in value[key].items())):
            raise RecordError('Invalid declared dependency/toolchain versions')
    original = reader.directory(root)
    if set(original) != set(value['files']) | {'build.json'}:
        raise RecordError('Visualiser bundle contains missing or unmanifested files')
    actual = {}
    for name, expected in value['files'].items():
        relative(name); sha(expected); actual[name] = reader.file(root/name)
        if actual[name]['sha256'] != expected:raise RecordError('Visualiser build file changed')
    current = {}
    for name, expected in value['source_hashes'].items():
        relative(name);sha(expected)
        path = Path(source)/name if name.startswith('public/') else Path(source)/'src'/name
        current[name] = reader.file(path)['sha256'] if path.exists() or path.is_symlink() else None
    return {'manifest':fp, 'identity':value, 'verified_files':actual,
            'declared_source_hashes_match_checkout':current == value['source_hashes'],
            'current_declared_source_hashes':current,
            'build_revision_matches_checkout':value['revision'] == source_commit,
            'provenance_scope':'Build manifest covers declared upstream source hashes and bundle bytes; '
                               'it does not record every integration/compiler input.'}


def catalogue(path, selected_model, selected_image, reader):
    path = ordinary(path); value, fp = reader.json(path)
    versioned=type(value) is dict and value.get('format')=='simpaths.multirun.releases.v2'
    if (type(value) is not dict or set(value) != ({'format','default','rollback','entries'} if versioned else {'format','default','entries'})
            or value['format'] not in ('simpaths.multirun.releases.v1','simpaths.multirun.releases.v2')
            or type(value['entries']) is not dict or not 1 <= len(value['entries']) <= 100
            or value['default'] not in value['entries']):
        raise RecordError('Invalid retained-release catalogue')
    if versioned and value['rollback'] is not None and value['rollback'] not in value['entries']:
        raise RecordError('Rollback release is absent from the catalogue')
    entries = {}
    for key, item in value['entries'].items():
        fields=({'legacy','manifest','availability','registered_at','status_changed_at','unreferenced_since'}
                if versioned else {'legacy','manifest'})
        if (not re.fullmatch(r'(?:release-[a-f0-9]{64}|local-[a-f0-9]{16})', key)
                or type(item) is not dict or set(item) != fields
                or type(item['legacy']) is not bool or item['legacy'] != key.startswith('local-')):
            raise RecordError('Invalid retained-release catalogue entry')
        if versioned:
            if (item['availability'] not in ('available','existing','recovery')
                    or key==value['default'] and item['availability']!='available'):
                raise RecordError('Invalid retained-release availability')
            for field in ('registered_at','status_changed_at','unreferenced_since'):
                if field=='unreferenced_since' and item[field] is None:continue
                try:
                    if datetime.fromisoformat(item[field]).tzinfo is None:raise ValueError()
                except (TypeError,ValueError):raise RecordError('Invalid release lifecycle timestamp') from None
        fingerprint_value(item['manifest'])
        file = path.parent.parent/'release/release.json' if item['legacy'] else path.parent/key/'release.json'
        release, release_fp = reader.json(file)
        if release_fp != item['manifest']:raise RecordError('Retained release manifest changed')
        if type(release) is not dict:raise RecordError('Invalid retained release manifest')
        if item['legacy']:
            identity = release
        else:
            if set(release) != {'format', 'id', 'name', 'identity'}:
                raise RecordError('Invalid retained release identity fields')
            identity = release['identity']
            if (release['format'] != 'simpaths.multirun.release.v1' or release['id'] != key
                    or key != 'release-'+digest(identity)):
                raise RecordError('Retained release identity changed')
        if type(identity) is not dict or not {'model', 'image', 'defaults'} <= set(identity):
            raise RecordError('Incomplete retained release identity')
        fingerprint_value(identity['model']);image(identity['image']);manifest(identity['defaults'])
        if item['legacy'] and key != 'local-'+identity['model']['sha256'][:16]:
            raise RecordError('Legacy release ID changed')
        entries[key] = {'manifest':release_fp, 'image':identity['image'], 'model':identity['model'],
                        'identity_sha256':digest(identity), 'default':key == value['default'],
                        'matches_selected_model':identity['model'] == selected_model,
                        'matches_selected_image':identity['image'] == selected_image}
        if versioned:entries[key].update({name:item[name] for name in fields-{'legacy','manifest'}})
    result={'manifest':fp, 'default':value['default'], 'entries':entries,
            'scope':'Read-only catalogue/manifest identity checks; retained bundle contents and daemon not revalidated.'}
    if versioned:result.update(format=value['format'],rollback=value['rollback'])
    return result


def evidence(path, reader):
    path = ordinary(path)
    if path.name != 'report.json':raise RecordError('Select completed report.json files, not logs or progress')
    value, fp = reader.json(path)
    if type(value) is not dict:raise RecordError('Evidence must be a JSON object')
    result = {'fingerprint':fp, 'scope':'Historical report reference; not certification of this candidate.'}
    for key in ('passed', 'cleanup', 'production_acceptance'):
        if key in value:
            if type(value[key]) is not bool:raise RecordError('Invalid evidence status')
            result[key] = value[key]
    if 'model' in value:result['model'] = fingerprint_value(value['model'])
    if 'image' in value:result['image'] = image(value['image'])
    if 'prepared_sha256' in value:result['prepared_sha256'] = sha(value['prepared_sha256'])
    return result


def normalise_selection(selection):
    if type(selection) is not dict or set(selection) != {'repositories', 'jar', 'image', 'prepared', 'visualiser_build', 'catalogue', 'evidence'}:
        raise RecordError('Invalid release-record selection')
    repos = selection['repositories']
    if type(repos) is not dict or set(repos) != set(REPOSITORIES):raise RecordError('Select all four source repositories')
    for key in ('prepared', 'evidence'):
        if (type(selection[key]) is not list or len(selection[key]) > 8
                or any(type(p) is not str for p in selection[key])
                or len(set(selection[key])) != len(selection[key])):
            raise RecordError('Select at most eight distinct packages/reports')
    paths = {k:str(ordinary(repos[k])) for k in REPOSITORIES}
    if len(set(paths.values())) != len(REPOSITORIES):raise RecordError('Select four distinct repositories')
    return {'repositories':paths,
            'jar':str(ordinary(selection['jar'])), 'image':image(selection['image']),
            'prepared':[str(ordinary(p)) for p in selection['prepared']],
            'visualiser_build':str(ordinary(selection['visualiser_build'])),
            'catalogue':str(ordinary(selection['catalogue'])) if selection['catalogue'] else None,
            'evidence':[str(ordinary(p)) for p in selection['evidence']]}


def declared_migrations(frontend, reader):
    """Parse bounded migration declarations and hash SQL without importing the app."""
    result = {}
    for component, filename, classname, method in (
            ('batch', 'jasmine_web/batch/store.py', 'Queue', 'migrations'),
            ('single', 'jasmine_web/vm_state.py', 'PostgresVMState', 'migrate')):
        path = frontend/filename
        data, _ = reader.file(path, limit=MAX_JSON, contents=True)
        tree = ast.parse(data)
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == classname)
        fn = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method)
        lists = [node.args[0] for node in ast.walk(fn) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'enumerate' and node.args
                 and isinstance(node.args[0], (ast.List, ast.Tuple))]
        if len(lists) != 1:raise RecordError('Review the changed migration declaration')
        names = ast.literal_eval(lists[0])
        if (type(names) not in (list, tuple) or not 1 <= len(names) <= 128
                or any(type(n) is not str or Path(n).name != n or not n.endswith('.sql') for n in names)
                or len(set(names)) != len(names)):
            raise RecordError('Invalid migration declaration')
        result[component] = [{'version':i, 'name':relative(n),
                              'sha256':reader.file(path.with_name(n), limit=MAX_JSON)['sha256']}
                             for i, n in enumerate(names, 1)]
    return result


def collect(selection):
    selection = normalise_selection(selection); reader = Reader()
    repos = {name:repository(name, path, reader) for name, path in selection['repositories'].items()}
    model = reader.file(selection['jar'])
    packages = [prepared_package(p, model, reader) for p in selection['prepared']]
    build = visualiser_build(selection['visualiser_build'], selection['repositories']['visualiser'],
                             repos['visualiser']['commit'], reader)
    frontend = Path(selection['repositories']['frontend'])
    schemas = declared_migrations(frontend, reader)
    adapters = {n:reader.file(Path(selection['repositories']['simpaths'])/'deploy/multirun/visualiser'/n)
                for n in ADAPTERS}
    retained = catalogue(selection['catalogue'], model, selection['image'], reader) if selection['catalogue'] else None
    reports = [evidence(p, reader) for p in selection['evidence']]
    reader.finish()
    for name in REPOSITORIES:
        expected = {k:repos[name][k] for k in ('commit', 'tree', 'dirty', 'changes', 'scope')}
        if git_state(selection['repositories'][name]) != expected:
            raise RecordError('Source Git state changed during collection')
    notes = list(PENDING)
    if any(v['dirty'] for v in repos.values()):notes.append('Dirty checkouts are recorded; commit/select reproducible sources before deployment.')
    for name, repo in repos.items():
        if not repo['licence_notice_files']:
            notes.append(name+': no standalone root licence/notice file found; this does not establish absence of licence terms.')
    if not build['declared_source_hashes_match_checkout']:notes.append('Visualiser declared source hashes differ from current checkout.')
    if any(p['calibration_only'] for p in packages):notes.append('Selected research preparation is calibration-only, not an approved production dataset.')
    if any(p['source_image'] != selection['image'] for p in packages):notes.append('Prepared input source image differs from the selected runtime image; validate compatibility.')
    if any(p.get('passed') is not True for p in reports):notes.append('At least one referenced report lacks a successful passed flag; review its scope and status.')
    return {'repositories':repos, 'selected_model':model,
            'image_references':sorted({selection['image'], *(p['source_image'] for p in packages)}),
            'installed_images_verified':False, 'prepared':packages, 'visualiser':build,
            'current_integration_files':adapters, 'historical_integration_inputs_verified':False,
            'migration_declarations':schemas, 'retained_releases':retained, 'historical_reports':reports,
            'installed_dependencies_verified':False, 'deployment_approved':False,
            'installed_host_validated':False, 'outstanding_checks':notes,
            'scope':'Candidate metadata/artifact inventory, not a source archive, SBOM, permission grant or deployment acceptance.'}


def render(record):
    value = record['inventory']
    lines = ['# Candidate deployment release record', '', '**Not deployed or approved for production.**', '',
             'Current checkout commits are separate from historical artifact build provenance.', '',
             '| Component | Current Git commit | Local changes | Root licence/notice file |',
             '| --- | --- | --- | --- |']
    for name, repo in value['repositories'].items():
        lines.append(f"| {name} | {repo['commit']} | {'Yes' if repo['dirty'] else 'No'} | {'Present' if repo['licence_notice_files'] else 'Not found'} |")
    lines += ['', '## Selected artifacts', '',
              '- Model JAR SHA-256: '+value['selected_model']['sha256']+'.',
              '- Referenced immutable images: '+', '.join(value['image_references'])+'.',
              '- Visualiser recorded build revision: '+value['visualiser']['identity']['revision']+'.',
              '- Visualiser bundle hashes verified; declared source hashes match checkout: '+
                  str(value['visualiser']['declared_source_hashes_match_checkout'])+'.',
              '- Prepared receipt/JAR and input names/sizes checked; bulk input hashes were not reread.',
              '- Dependency declarations and migration checksums recorded; installed dependency/image validation remains pending.',
              '- Historical reports are references, not certification of this candidate.', '', '## Outstanding checks', '']
    lines += ['- '+item for item in value['outstanding_checks']]
    lines += ['', 'See record.json for exact fingerprints, declarations, catalogue IDs, evidence references and verification scope.', '']
    return '\n'.join(lines)


def capture(selection, output):
    selection = normalise_selection(selection); output = ordinary(output)
    sources = [*selection['repositories'].values(), str(Path(selection['jar']).parent),
               selection['visualiser_build'], *selection['prepared'],
               *(str(Path(p).parent) for p in selection['evidence'])]
    if selection['catalogue']:sources.append(str(Path(selection['catalogue']).parent.parent))
    if output.exists() or any(output == Path(p) or Path(p) in output.parents for p in sources):
        raise RecordError('Choose a new output directory outside repositories and selected artifacts')
    value = collect(selection)
    record = {'format':FORMAT, 'created_at':datetime.now(timezone.utc).isoformat(),
              'selection':selection, 'selection_sha256':digest(selection), 'inventory':value,
              'inventory_sha256':digest(value), 'simulations_started':0,
              'limit_scope':{'json_bytes':MAX_JSON, 'file_bytes':MAX_FILE, 'hashed_bytes':MAX_HASH_BYTES,
                             'bulk_input_contents_read':False},
              'integrity_note':'Checksums detect changes; this unsigned record does not prove authorship or grant authority.'}
    encoded = (json.dumps(record, indent=2, sort_keys=True, allow_nan=False)+'\n').encode()
    if len(encoded) > MAX_JSON:raise RecordError('Generated inventory exceeds JSON bounds')
    output.mkdir(mode=0o700)
    for name, text in (('record.json', encoded), ('SUMMARY.md', (
            '<!-- (C) Copyright 2026, by Ross Richardson\nGenerated candidate deployment summary.\n@author ross richardson\n-->\n\n'+render(record)).encode()),
            ('COPYRIGHT.md', b'<!-- (C) Copyright 2026, by Ross Richardson\nGenerated release-record attribution.\n@author ross richardson\n-->\n\n# Attribution\n\n(C) Copyright 2026, by Ross Richardson.\n\n@author ross richardson\n\nGenerated metadata and summary only; binaries, data and upstream notices retain their existing licences.\n')):
        fd = os.open(output/name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:stream.write(text)
    return record


def verify(path):
    value, _ = Reader().json(ordinary(path))
    if (type(value) is not dict or value.get('format') != FORMAT
            or not {'selection', 'inventory'} <= set(value)
            or value.get('selection_sha256') != digest(value['selection'])
            or value.get('inventory_sha256') != digest(value['inventory'])):
        raise RecordError('Release record identity or checksum changed')
    if collect(value['selection']) != value['inventory']:
        raise RecordError('Selected commits, artifacts or metadata no longer match this record')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('capture', help='Write a new private candidate record; source inputs stay read-only')
    for name in REPOSITORIES:create.add_argument('--'+name, type=Path, required=True)
    create.add_argument('--jar', type=Path, required=True)
    create.add_argument('--image', required=True)
    create.add_argument('--prepared', type=Path, action='append', default=[])
    create.add_argument('--visualiser-build', type=Path, required=True)
    create.add_argument('--catalogue', type=Path)
    create.add_argument('--evidence', type=Path, action='append', default=[])
    create.add_argument('--output', type=Path, required=True)
    check = commands.add_parser('verify', help='Recheck local selected commits/artifacts; never activates anything')
    check.add_argument('record', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify':
            verify(args.record);print('Verified recorded local identities and artifacts; host checks remain pending.')
        else:
            selection = {'repositories':{n:str(getattr(args,n)) for n in REPOSITORIES},
                'jar':str(args.jar), 'image':args.image, 'prepared':[str(p) for p in args.prepared],
                'visualiser_build':str(args.visualiser_build),
                'catalogue':str(args.catalogue) if args.catalogue else None,
                'evidence':[str(p) for p in args.evidence]}
            capture(selection, args.output);print('Wrote candidate release record: '+str(args.output/'record.json'))
        return 0
    except (RecordError, OSError, ValueError, KeyError, TypeError, StopIteration, SyntaxError, subprocess.TimeoutExpired) as error:
        print('STOPPED: '+str(error), file=sys.stderr);return 2


if __name__ == '__main__':raise SystemExit(main())
