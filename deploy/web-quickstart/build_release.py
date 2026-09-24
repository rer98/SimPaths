#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Build both attributed Quick Start release images from committed Java sources,
reuse verified prepared inputs, and validate actual VM deployment allocations.

@author ross richardson
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import runpy
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
loader = runpy.run_path(str(ROOT / 'deploy/_tool_loader.py'))
workflow = loader['load_tool']('_workflow.py')
# Public compatibility mapping; None resolves the selected frontend catalogue.
BASES = {20000: None, 50000: None}


def selected_bases(args):
    populations = list(dict.fromkeys(args.population or BASES))
    result = {}
    for population in populations:
        explicit = getattr(args, f'base_{population}') or BASES.get(population)
        result[population] = workflow.quickstart_image(
            args.frontend, population, explicit, args.catalogue)
    return result


def release_dockerfile(template, base_tag):
    """Compatibility API; release builds use tools from their --simpaths checkout."""
    return quickstart_packager().release_dockerfile(template, base_tag)


def quickstart_packager(repo=None):
    return loader['load_tool']('web-quickstart/package_image.py', repo or ROOT)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def deployment(population, image):
    return {'image': image, 'java_heap_gib': 2 if population == 20000 else 3,
            'memory': '4Gi' if population == 20000 else '5Gi', 'cpu': 2,
            'session_storage': {'allowance_gib': 10, 'warning_free_gib': 3,
                                'build_reserve_gib': 2, 'cleanup_enabled': True}}


def profile_record(population, image):
    return {'id': f'simpaths-quickstart-{population}',
            'name': f'SimPaths UK Quick Start - {population:,} people',
            'requiresAuth': False, 'icon': 'fa-chart-line', 'color': '#26734d',
            'description': 'Public UK training data; prepared population; start 2019.',
            'deployment': deployment(population, image)}


def archive_hash(container, path):
    """Hash file contents, excluding Docker tar ownership and mode metadata."""
    return hashlib.sha256(archive_file(container, path)).hexdigest()


def archive_file(container, path):
    chunks, _ = container.get_archive(path)
    with tarfile.open(fileobj=io.BytesIO(b''.join(chunks))) as archive:
        members = [m for m in archive.getmembers() if m.isfile()]
        if len(members) != 1:
            raise ValueError(f'Expected one file at {path}')
        return archive.extractfile(members[0]).read()


def archive_content_hashes(container, path):
    """Hash each regular file without buffering a database-sized tar in memory."""
    chunks, _ = container.get_archive(path)

    class ChunkReader(io.RawIOBase):
        def __init__(self):
            self.chunks = iter(chunks)
            self.pending = memoryview(b'')

        def readable(self):
            return True

        def readinto(self, target):
            while not self.pending:
                self.pending = memoryview(next(self.chunks, b''))
                if not self.pending:
                    return 0
            size = min(len(target), len(self.pending))
            target[:size] = self.pending[:size]
            self.pending = self.pending[size:]
            return size

    hashes = {}
    with io.BufferedReader(ChunkReader()) as stream:
        with tarfile.open(fileobj=stream, mode='r|') as archive:
            for member in archive:
                if member.isdir():
                    continue
                if not member.isfile() or member.name in hashes:
                    raise ValueError('Prepared archive contains a link, duplicate or unsupported entry')
                source = archive.extractfile(member)
                h = hashlib.sha256()
                while chunk := source.read(1024 * 1024):
                    h.update(chunk)
                hashes[member.name] = h.hexdigest()
    return hashes


def verify_query_migration(before, after, receipt):
    database = 'input/input.mv.db'
    if database not in before or database not in after:
        raise ValueError('Prepared input database is missing')
    if ({k: v for k, v in before.items() if k != database}
            != {k: v for k, v in after.items() if k != database}):
        raise ValueError('Prepared non-database files changed during query-account setup')
    if (receipt.get('format_version') != 1
            or receipt.get('principal') != 'JASMINE_WEB_READER'
            or receipt.get('source_database_sha256') != before[database]
            or receipt.get('database_sha256') != after[database]):
        raise ValueError('Query-account migration receipt does not match source and result databases')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, default=ROOT.parent/'JAS-mine-core')
    parser.add_argument('--simpaths', type=Path, default=ROOT)
    parser.add_argument('--frontend', type=Path, default=workflow.frontend_path())
    parser.add_argument('--work-root', type=Path, default=Path('/tmp/codex-rer'))
    parser.add_argument('--output', type=Path, required=True, help='New persistent evidence directory')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--promote', action='store_true', help='After all acceptance checks pass, update the frontend catalogue and retire older releases')
    parser.add_argument('--population', type=int, choices=(20000, 50000), action='append',
                        help='Build only this profile; repeat or omit for both')
    parser.add_argument('--catalogue', type=Path, help='Catalogue supplying default prepared base images')
    parser.add_argument('--base-20000', help='Local prepared image for 20,000; overrides the catalogue')
    parser.add_argument('--base-50000', help='Local prepared image for 50,000; overrides the catalogue')
    parser.add_argument('--offline', action='store_true', help='Run Maven offline using cached dependencies')
    args = parser.parse_args(argv)
    for name in ('core', 'simpaths', 'frontend', 'work_root', 'output'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    args.catalogue = workflow.catalogue_path(args.frontend, args.catalogue)
    base_references = selected_bases(args)
    plan = {'core': str(args.core), 'simpaths': str(args.simpaths), 'frontend': str(args.frontend),
            'temporary_root': str(args.work_root), 'output': str(args.output),
            'profiles': [profile_record(p, base) for p, base in base_references.items()],
            'stages': ['Docker/configuration preflight', 'test/install committed core',
                       'test/package committed SimPaths', 'repackage each prepared profile and verify hashes',
                       'browser acceptance at each recommended allocation, including cleanup'],
            'host_disk_quota': 'Not configured or verified; application allowance only'}
    print(json.dumps(plan, indent=2), flush=True)
    if args.dry_run:
        return 0
    packager = quickstart_packager(args.simpaths)
    if args.promote:
        loader['load_web_tool']('release_images.py', args.frontend)
    import docker
    client = docker.from_env()
    client.ping()  # No source builds or output directories before Docker access succeeds.
    if args.output.exists():
        parser.error('--output must be a new directory')
    for repo in (args.core, args.simpaths):
        allowed = {'input/DatabaseCountryYear.xlsx', 'input/EUROMODpolicySchedule.xlsx'} if repo == args.simpaths else set()
        changed = set(git(repo, 'diff', '--name-only', 'HEAD').splitlines())
        untracked_source = [p for p in git(repo, 'ls-files', '--others', '--exclude-standard', 'src', 'pom.xml', 'deploy').splitlines() if p]
        if untracked_source:
            parser.error(f'Commit new source files first: {repo}: {untracked_source}')
        if changed - allowed:
            parser.error(f'Commit Java repository changes first: {repo}: {sorted(changed - allowed)}')
    if not (args.frontend/'jasmine_web/heap_policy.py').is_file():
        parser.error('Update JAS-mine-web: jasmine_web/heap_policy.py is required')
    # Validate real frontend policy before doing expensive builds.
    code = 'from model_config import validate_models_config; import json,sys; validate_models_config(json.load(sys.stdin)["models"])'
    subprocess.run([sys.executable, '-c', code], cwd=args.frontend,
                   input=json.dumps({'models': plan['profiles']}), text=True, check=True)
    bases = {p: client.images.get(tag) for p, tag in base_references.items()}
    args.work_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.work_root).free < 2*1024**3:
        parser.error('Need at least 2 GiB available for a temporary jar context and build headroom')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'COPYRIGHT.md').write_text(
        '# Release evidence attribution\n\n(C) Copyright 2026, by Ross Richardson.\n\n'
        '@author ross richardson\n\nRelease manifests and generated model catalogue are project artifacts. '
        'Logs and third-party output retain their original notices.\n')
    records = []
    def run(label, command, cwd=None):
        print(label, flush=True)
        with (args.output/(label+'.log')).open('w') as log:
            subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=True,
                           env={**os.environ, 'TMPDIR': str(args.work_root.resolve())})
    status = {'status': 'running', 'images': records}
    try:
        revisions = {name: git(repo, 'rev-parse', 'HEAD') for name, repo in
                     [('core',args.core),('simpaths',args.simpaths),('frontend',args.frontend),('release_tools',ROOT)]}
        maven = ['mvn'] + (['-o'] if args.offline else []) + ['-Djava.awt.headless=true']
        run('core-build', [*maven, 'install'], args.core)
        run('simpaths-build', [*maven, 'package'], args.simpaths)
        jar = args.simpaths/'singlerun.jar'
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        profile_for, profile_id, profile_readme = packager.profile_for, packager.profile_id, packager.profile_readme
        for population, base in bases.items():
            print(f'package-{population}', flush=True)
            base_container = client.containers.create(base.id)
            try:
                receipt = json.loads(archive_file(base_container, '/app/profile.json'))
                if receipt.get('profile') != profile_for(population) or receipt.get('profile_id') != profile_id(population):
                    raise ValueError(f'Unexpected prepared profile in {base_references[population]}')
                hashes = {'profile.json': archive_hash(base_container, '/app/profile.json')}
                input_hashes = archive_content_hashes(base_container, '/app/input')
            finally:
                base_container.remove()
            tag = f'simpaths-quickstart:uk-2019-{population}-release-{stamp}'
            record = {'population': population, 'tag': tag, 'base_image': base.id, 'base_reference': base_references[population],
                      'revisions': revisions, 'runtime_jar_sha256': digest(jar),
                      'frontend_worktree_status': git(args.frontend, 'status', '--short'),
                      'source_prepared_hashes': {**hashes, 'input_files': input_hashes},
                      'query_access_setup': 'restricted account; receipt in /app/query-access.json', 'deployment': deployment(population, tag)}
            with tempfile.TemporaryDirectory(prefix=f'release-{population}-', dir=args.work_root) as temporary:
                context = Path(temporary)
                shutil.copy2(jar, context/'singlerun.jar')
                for name in ['webserver.properties','license.txt','COPYRIGHT.md']:
                    shutil.copy2(args.simpaths/name, context/name)
                props = (context/'webserver.properties').read_text()
                if not any(line.strip() == 'allowDetailedDataAccess=true' for line in props.splitlines()):
                    raise ValueError('Public training release requires allowDetailedDataAccess=true')
                (context/'README.md').write_text(profile_readme(population))
                (context/'release.json').write_text(json.dumps(record, indent=2)+'\n')
                # Use a temporary named local base: Docker does not interpret a raw image ID as a local FROM reliably.
                base_tag = f'simpaths-quickstart:release-base-{population}-{stamp}'
                base.tag(base_tag)
                dockerfile = (packager.HERE/'Dockerfile').read_text()
                dockerfile = packager.release_dockerfile(dockerfile, base_tag)
                (context/'Dockerfile').write_text(dockerfile)
                try:
                    run(f'image-{population}', ['docker','build','--pull=false','-t',tag,str(context)])
                finally:
                    client.images.remove(base_tag)  # Only our temporary tag; never prune user images.
            image = client.images.get(tag)
            candidate = client.containers.create(image.id)
            try:
                updated = {name: archive_hash(candidate, '/app/'+name) for name in hashes}
                if updated != hashes:
                    raise ValueError('Prepared profile changed during image packaging')
                final_inputs = archive_content_hashes(candidate, '/app/input')
                migration = json.loads(archive_file(candidate, '/app/query-access.json'))
                verify_query_migration(input_hashes, final_inputs, migration)
                assert archive_file(candidate, '/app/COPYRIGHT.md') == (args.simpaths/'COPYRIGHT.md').read_bytes()
                assert json.loads(archive_file(candidate, '/app/release.json')) == record
            finally:
                candidate.remove()
            record['query_access_receipt'] = migration
            record['packaged_input_hashes'] = final_inputs
            record['image_id'] = image.id
            record['status'] = 'packaged'
            records.append(record)
            (args.output/'status.json').write_text(json.dumps(status,indent=2)+'\n')
            run(f'browser-{population}', [sys.executable,str(ROOT/'deploy/acceptance/run_browser_acceptance.py'),
                '--frontend',str(args.frontend.resolve()),'--image',tag,'--population',str(population),
                '--detailed-access','--deployment-profile','--storage-check',
                '--output',str((args.output/f'browser-{population}').resolve())])
            record['status'] = 'validated'
        catalogue = {'models': [profile_record(r['population'],r['tag']) for r in records]}
        (args.output/'models.quickstart.json').write_text(json.dumps(catalogue,indent=2)+'\n')
        status['status'] = 'passed'
    except Exception as error:
        status.update(status='failed', error=str(error))
        raise
    finally:
        (args.output/'status.json').write_text(json.dumps(status,indent=2)+'\n')
    if args.promote:
        command = [sys.executable, str(ROOT/'deploy/web-quickstart/promote_release.py'),
                   '--frontend', str(args.frontend),
                   '--catalogue', str(args.catalogue),
                   '--evidence', str(args.output.parent), '--apply']
        for record in records:
            command.extend(['--image', record['tag']])
        run('promotion-cleanup', command)
    print(f'Validated images and catalogue: {args.output}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
