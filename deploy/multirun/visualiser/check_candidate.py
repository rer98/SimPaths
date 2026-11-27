"""(C) Copyright 2026, by Ross Richardson
Build and check an exact Visualiser candidate against released calculations and retained results.
@author ross richardson
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODEL = HERE.parents[2]
UPSTREAM = 'rjgreen1029/SimPaths-Policy-Impacts-Visualiser'
SOURCE = f'https://github.com/{UPSTREAM}.git'
# Version deployed when the reproducible qualification workflow was introduced.
REFERENCE = 'e14ab818f065f145213f2f39f0c85f85b1475f1b'
PACKAGE_FILES = ('package.json', 'package-lock.json')
SCIENTIFIC_SOURCES = {'src/parseCore.js', 'src/csvParse.js', 'src/localFolderParser.js',
                      'src/useAggregatedData.js', *PACKAGE_FILES}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def row_changes(differences):
    """Count unmatched row keys and changed values for keys in both calculators."""
    counts = dict(added=0, removed=0, modified=0)
    for entry in differences:
        before, after = entry['before'], entry['after']
        if before is None and after is None:
            raise ValueError('A chart difference must identify a row')
        if before is None:
            counts['added'] += 1
        elif after is None:
            counts['removed'] += 1
        else:
            counts['modified'] += 1
    return counts


def git(directory, *args):
    return subprocess.check_output(['git', '-C', directory, *args])


def source_reference(repository):
    if repository is None:
        return SOURCE, 'refs/heads/main'
    urls = {SOURCE, SOURCE.removesuffix('.git'), f'git@github.com:{UPSTREAM}.git',
            f'ssh://git@github.com/{UPSTREAM}.git'}
    for name in git(repository, 'remote').decode().splitlines():
        if git(repository, 'remote', 'get-url', name).decode().strip() in urls:
            return str(repository.resolve()), f'refs/remotes/{name}/main'
    raise ValueError('The local checkout needs a remote pointing to ' + SOURCE)


def dependencies(cache, directory, manifest_directory, log):
    metadata = {name: (manifest_directory / name).read_bytes() for name in PACKAGE_FILES}
    if cache:
        root = cache.resolve(strict=True)
        if any((root.parent / name).read_bytes() != data for name, data in metadata.items()):
            raise ValueError('Cached dependencies differ from the pinned package files at ' + str(root))
        return root
    directory.mkdir()
    for name, data in metadata.items():
        (directory / name).write_bytes(data)
    subprocess.run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=directory,
                   stdout=log, stderr=subprocess.STDOUT, check=True)
    return directory / 'node_modules'


def previous_service(directory, build, edition):
    """Make a fixture that writes summaries using the exact earlier server binaries.

    Its web assets are fixtures from the candidate. Only the candidate is opened
    in a browser. The archived server bundle and its Node version come from the registry.
    """
    directory.mkdir()
    manifest = json.loads((build / 'build.json').read_bytes())
    manifest.pop('historical_editions')
    files = {name: digest for name, digest in manifest['files'].items() if not name.startswith('historical-')}
    archive = HERE / 'historical' / edition['revision'][:8]
    for name in files:
        original = archive / name if name in edition['files'] else build / name
        if name in edition['files'] and sha(original) != edition['files'][name]:
            raise ValueError('Released calculator failed its checksum')
        shutil.copyfile(original, directory / name)
    manifest.update(revision=edition['revision'], files=files | edition['files'])
    (directory / 'build.json').write_text(json.dumps(manifest))


def examine(args, output, work, report):
    source = work / 'source'
    source.mkdir()
    logdir = output / 'logs'
    logdir.mkdir()

    def check(name, command, env=None):
        with (logdir / (name + '.log')).open('wb') as log:
            result = subprocess.run(command, cwd=MODEL, env=env, stdout=log, stderr=subprocess.STDOUT)
        report['checks'][name] = 'passed' if result.returncode == 0 else 'failed'
        print(f'{name}: {report["checks"][name]}', flush=True)
        return result.returncode == 0

    repository, ref = source_reference(args.repository)
    git(source, 'init', '--quiet')
    git(source, 'fetch', '--quiet', '--no-tags', repository, ref + ':refs/heads/reese-main')
    report['observed_main'] = git(source, 'rev-parse', 'reese-main').decode().strip()
    git(source, 'merge-base', '--is-ancestor', args.revision, 'reese-main')
    git(source, 'merge-base', '--is-ancestor', REFERENCE, args.revision)
    git(source, 'checkout', '--quiet', '--detach', args.revision)
    changes = git(source, 'diff', '--no-renames', '--name-only', '-z', REFERENCE, args.revision).decode().split('\0')
    report['changed_paths'] = sorted(filter(None, changes))

    registry = json.loads((HERE / 'historical/approved.json').read_text())
    readers = work / 'readers'
    node = subprocess.check_output(['node', '--version'], text=True).strip()
    for row in registry:
        if row['node'] != node:
            raise ValueError(f'This build needs {row["node"]} to exercise retained summaries')
        folder = readers / row['revision']
        folder.mkdir(parents=True)
        archive = HERE / 'historical' / row['revision'][:8]
        if sha(archive / 'build.json') != row['manifest_sha256']:
            raise ValueError('Released manifest changed')
        original = json.loads((archive / 'build.json').read_text())
        for name, digest in original['source_hashes'].items():
            if name.endswith('.js'):
                data = git(source, 'show', row['reader_commit'] + ':src/' + name)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError('Released reader source changed at ' + name)
                (folder / name).write_bytes(data)
    def install(name, cache, manifests):
        stage = name + '-dependencies'
        report['checks'][stage] = 'failed'
        with (logdir / (stage + '.log')).open('wb') as log:
            root = dependencies(cache, work / name, manifests, log)
        report['checks'][stage] = 'passed'
        return root

    compiler = install('compiler', args.dependencies, HERE)
    dom = install('dom', args.test_dependencies, HERE / 'dom-tests')
    build = output / 'build'
    if not check('build', ['node', HERE / 'build.cjs', '--source', source, '--dependencies', compiler,
                           '--reader-sources', readers, '--output', build]):
        return
    manifest = json.loads((build / 'build.json').read_bytes())
    if manifest['revision'] != args.revision or any(sha(build / k) != v for k, v in manifest['files'].items()):
        raise ValueError('Candidate build identity or file checksum mismatch')
    approved_history = [{k: row[k] for k in ('revision', 'node', 'files')} for row in registry
                        if any(manifest['files'][name] != value for name, value in row['files'].items())]
    if manifest.get('historical_editions') != approved_history:
        raise ValueError('Candidate omits or changes a registered calculator and reader')
    report['readers'] = {row['revision']: row['reader_commit'] for row in registry}
    report['manifest_sha256'] = sha(build / 'build.json')
    env = dict(os.environ, SIMPATHS_VISUALISER_TEST_BUILD=str(build), SIMPATHS_VISUALISER_DEPENDENCIES=str(dom),
               PYTHONPATH=os.pathsep.join((str(MODEL), str(args.frontend.resolve()))))
    reference = next(row for row in registry if row['revision'] == REFERENCE)
    science = output / 'comparison.json'
    check('compare', ['node', HERE / 'compare_candidate.cjs', source, compiler, build,
                      HERE / 'historical' / REFERENCE[:8], work, science])
    if science.is_file():
        values = json.loads(science.read_bytes())
        differences = values['release_differences']
        report['comparison'] = dict(rows=values['hosted_rows'], local_folder_differences=len(values['upstream_differences']),
                                    differences_from_release=len(differences), row_changes=row_changes(differences),
                                    sha256=sha(science))
    report['calculator_bytes_changed'] = any(manifest['files'][k] != v for k, v in reference['files'].items())
    for name in ('test_runner.cjs', 'test_bundle.cjs', 'test_portable_source.mjs', 'test_section_source.mjs'):
        check(Path(name).stem, ['node', HERE / name], env)
    for edition in manifest['historical_editions']:
        short = edition['revision'][:8]
        original = work / ('previous-' + short)
        previous_service(original, build, edition)
        export = work / ('export-' + short)
        previous_env = dict(env, SIMPATHS_HISTORICAL_BUILD=str(original), SIMPATHS_FIXTURE_EXPORT=str(export))
        if check('retained-' + short, [sys.executable, '-m', 'unittest',
                                       'deploy.multirun.test_historical_calculator'], previous_env):
            folder, = export.glob('*/manifest.json')
            check('download-' + short, ['node', HERE / 'test_saved_reader.cjs', build, folder.parent], env)
    if args.skip_chromium:
        report['checks']['chromium'] = 'not run'
    else:
        check('chromium', [sys.executable, HERE / 'test_candidate_browser.py', build], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True, help='Full upstream commit SHA')
    parser.add_argument('--output', required=True, type=Path, help='New directory for the bundle, report and logs')
    parser.add_argument('--repository', type=Path, help='Use an already fetched local upstream/main')
    parser.add_argument('--dependencies', type=Path, help='Reuse an installation of the locked integration compiler')
    parser.add_argument('--test-dependencies', type=Path, help='Reuse the locked DOM test environment')
    parser.add_argument('--frontend', type=Path, default=Path(os.environ.get('JASMINE_WEB_REPO',
                        Path.home() / 'git/JAS-mine/JAS-mine-web')), help='Compatible JAS-mine-web checkout')
    parser.add_argument('--skip-chromium', action='store_true', help='Produce an incomplete local rehearsal')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.revision):
        parser.error('Supply an exact 40-character Git commit SHA')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = dict(source=SOURCE, revision=args.revision, reference=REFERENCE, checks={}, status='failed')
    (output / 'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Attribution for generated candidate qualification evidence.\n@author ross richardson\n-->\n')
    try:
        with tempfile.TemporaryDirectory(dir=output) as folder:
            examine(args, output, Path(folder), report)
        if 'comparison' in report:
            scientific = bool(set(report['changed_paths']) & SCIENTIFIC_SOURCES or
                              report['comparison']['differences_from_release'])
            report['scientific_review'] = scientific
            # Numeric equality on one experiment cannot certify an unclassified source edit.
            unknown = [p for p in report['changed_paths'] if p not in SCIENTIFIC_SOURCES and
                       not (p.endswith(('.css', '.md', '.png', '.svg')) or p == 'LICENSE')]
            report['review_paths'] = unknown
            report['status'] = ('failed' if 'failed' in report['checks'].values() else
                                'incomplete' if 'not run' in report['checks'].values() else
                                'scientific review' if scientific else
                                'technical review' if report['calculator_bytes_changed'] or unknown else 'checks passed')
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, StopIteration) as exc:
        report['error'] = str(exc)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{report["status"]} — {output / "report.json"}')
    return int(report['status'] in ('failed', 'incomplete'))


if __name__ == '__main__':
    raise SystemExit(main())
