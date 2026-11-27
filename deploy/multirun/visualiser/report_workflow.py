"""(C) Copyright 2026, by Ross Richardson
Select a Visualiser revision for CI and report its qualification to maintainers.
@author ross richardson
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .check_candidate import MODEL, SCIENTIFIC_SOURCES, SOURCE

WORKFLOW = '.github/workflows/visualiser-check.yml'


def revision(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('An exact 40-character commit SHA is required')
    return value


def gh_api(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', endpoint], timeout=45))


def previous_run(repo, branch, name):
    """Only a successful run of this workflow on the same branch can supply the build."""
    base = f'repos/{repo}/actions'
    artifacts = gh_api(f'{base}/artifacts?name={name}&per_page=100')['artifacts']
    for artifact in artifacts:
        origin = artifact.get('workflow_run') or {}
        if artifact['name'] != name or artifact['expired'] or origin.get('head_branch') != branch:
            continue
        run_id = int(origin['id'])
        run = gh_api(f'{base}/runs/{run_id}')
        if (run['path'] == WORKFLOW and run['head_branch'] == branch and
                run['head_repository'] and run['head_repository']['full_name'] == repo and
                run['event'] in ('schedule', 'workflow_dispatch') and run['conclusion'] == 'success'):
            return run_id
    return None


def select(requested, repo, branch):
    if requested is not None:
        selected = revision(requested)
    else:
        remote = subprocess.check_output(['git', 'ls-remote', '--exit-code', SOURCE, 'refs/heads/main'],
                                         text=True, timeout=60)
        sha, ref = remote.strip().split('\t')
        if ref != 'refs/heads/main':
            raise ValueError('Expected the upstream main branch')
        selected = revision(sha)
    # The Git objects include the checker, approved editions, fixture, workflow and frontend pin.
    inputs = subprocess.check_output(['git', '-C', MODEL, 'ls-tree', 'HEAD', '--', 'deploy', WORKFLOW])
    if len(inputs.splitlines()) != 2:
        raise ValueError('The workflow and deployment code must both be committed')
    destination = os.environ.get('MULTIRUN_ORIGIN','').encode()
    key = 'visualiser-' + selected + '-' + hashlib.sha256(inputs + destination).hexdigest()
    prior = previous_run(repo, branch, key) if requested is None else None
    return selected, key, prior


def summary(directory, selected, project_commit, frontend_commit):
    """Write the project revisions alongside the checker’s original evidence."""
    context = dict(format='simpaths.visualiser.workflow.v1', simpaths=revision(project_commit), frontend=revision(frontend_commit),
                   visualiser=revision(selected))
    report = json.loads((directory / 'report.json').read_text())
    if report['revision'] != selected or report['source'] != SOURCE:
        raise ValueError('Qualification report does not identify the requested revision')
    outcomes = {'checks passed', 'technical review', 'scientific review', 'failed', 'incomplete'}
    if report['status'] not in outcomes:
        raise ValueError('Unknown qualification result')
    if report['status'] not in ('failed', 'incomplete') and (
            'manifest_sha256' not in report or 'comparison' not in report or
            report['checks'].get('chromium') != 'passed' or set(report['checks'].values()) != {'passed'}):
        raise ValueError('Successful qualification requires a complete report and browser check')
    lines = ['## Visualiser update check', '', f"Result **{report['status']}**", '',
             f'Visualiser commit `{selected}`', '', f'SimPaths commit `{project_commit}`',
             '', f'JAS-mine-web commit `{frontend_commit}`']
    reference = revision(report['reference'])
    lines += ['', f'[Compare changes in the Visualiser]({SOURCE.removesuffix(".git")}/compare/{reference}...{selected})']
    scientific_paths = sorted(SCIENTIFIC_SOURCES.intersection(report.get('changed_paths', [])))
    if scientific_paths:
        lines += ['', 'Calculation or dependency changes in ' + ', '.join(f'`{path}`' for path in scientific_paths)]
    if report.get('review_paths'):
        lines += ['', f"Other paths requiring technical review **{len(report['review_paths'])}**"]
    if 'manifest_sha256' in report:
        manifest = directory / 'build/build.json'
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if digest != report['manifest_sha256']:
            raise ValueError('Build manifest differs from the qualification report')
        if report['status'] not in ('failed', 'incomplete'):
            files = json.loads(manifest.read_bytes())['files']
            contents = set(manifest.parent.iterdir())
            if {p.name for p in contents} != (set(files) | {'build.json'}):
                raise ValueError('Unexpected or missing file in the checked bundle')
            for path in contents:
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Expected regular files in the checked bundle')
                if path != manifest and hashlib.sha256(path.read_bytes()).hexdigest() != files[path.name]:
                    raise ValueError('Build file changed after qualification')
        context['manifest_sha256'] = digest
        lines.extend(['', f'Build manifest SHA-256 `{digest}`'])
    if 'comparison' in report:
        comparison = report['comparison']
        if hashlib.sha256((directory / 'comparison.json').read_bytes()).hexdigest() != comparison['sha256']:
            raise ValueError('Comparison evidence changed after qualification')
        lines += ['', f"Fixture rows **{int(comparison['rows'])}**",
                  '', f"Rows differing from Reese’s local-folder reader **{int(comparison['local_folder_differences'])}**",
                  '', f"Rows differing from the released calculator **{int(comparison['differences_from_release'])}**"]
    if report.get('calculator_bytes_changed'):
        lines += ['', 'The compiled calculator differs from the reference release.']
    failed = [k for k, v in report['checks'].items() if v != 'passed']
    if failed and all(re.fullmatch('[a-z0-9_-]+', k) for k in failed):
        lines += ['', 'Checks requiring attention include ' + ', '.join(f'`{k}`' for k in failed)]
    (directory / 'workflow.json').write_text(json.dumps(context, indent=2) + '\n')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True, dest='command')
    discover = commands.add_parser('select')
    discover.add_argument('--revision', default='')
    report = commands.add_parser('summary')
    report.add_argument('directory', type=Path)
    report.add_argument('--revision', required=True)
    report.add_argument('--frontend', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'select':
        repo = os.environ['GITHUB_REPOSITORY']
        event = os.environ['GITHUB_EVENT_NAME']
        if event not in ('schedule', 'workflow_dispatch'):
            raise ValueError('Expected a scheduled or manually requested check')
        requested = args.revision if event == 'workflow_dispatch' else None
        selected, key, prior = select(requested, repo, os.environ['GITHUB_REF_NAME'])
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
            print(f'revision={selected}\nartifact={key}\ncheck={str(prior is None).lower()}', file=stream)
        if prior is None:
            message = f'Checking Visualiser commit `{selected}`.\n'
        else:
            message = (f'Visualiser commit `{selected}` already has a checked build with these inputs. '
                       f'[Open the report](https://github.com/{repo}/actions/runs/{prior}).\n')
    else:
        frontend = subprocess.check_output(['git', '-C', args.frontend, 'rev-parse', 'HEAD'], text=True).strip()
        message = summary(args.directory, args.revision, os.environ['GITHUB_SHA'], frontend)
        report = json.loads((args.directory/'report.json').read_text())
        if report['status'] not in ('failed','incomplete'):
            compact = dict(outcome=report['status'], manifest=report['manifest_sha256'], frontend=frontend,
                           reference=report['reference'], rows=report['comparison']['rows'],
                           changed=report['comparison']['differences_from_release'])
            with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
                print('review='+json.dumps(compact,separators=(',',':')),file=stream)
    with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
        stream.write(message)


if __name__ == '__main__':
    main()
