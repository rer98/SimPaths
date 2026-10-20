#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Exclude audited, unused Pebble from exact qualified images and check startup.
Keeps the accepted application, data layers and runtime configuration unchanged.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy.acceptance.run_isolated_security_refresh import run_owned

ENTRYPOINT_HASH = '4f41c720f4050a7389a1b6a3b69c322d417d8134338cc07e1be86cf82d650a63'
PEBBLE_HASH = '5e33a1304f207bb275b1b4cc3dc4c05fdd88755f5c705e2fd569924ec72dc061'
ROLES = ('quickstart-20000', 'quickstart-50000', 'training', 'user-data')
NOTICE = ('<!-- (C) Copyright 2026, by Ross Richardson\n'
          'Runtime hardening recipes, startup evidence and derived image receipts.\n'
          '@author ross richardson -->\n\n'
          'Copied runtime, package and upstream metadata retain their original licences.\n')


def digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected an ordinary evidence file')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    os.replace(temporary, path)


def load_base(path):
    root = json.loads(path.read_text())
    if (root.get('format') != 'simpaths.isolated-security-refresh.v1'
            or root.get('passed') is not True or root.get('promoted') is not False
            or root.get('originals_unchanged') is not True):
        raise ValueError('A passed, preserved, unpromoted isolated qualification is required')
    child_path = Path(root['acceptance'])/'report.json'
    if digest(child_path) != root['acceptance_sha256']:
        raise ValueError('Base acceptance receipt changed')
    child = json.loads(child_path.read_text())
    interactive = child.get('interactive', {})
    checks = child.get('acceptance_checks', [])
    if (child.get('passed') is not True or child.get('promoted') is not False
            or child.get('quickstart', {}).get('status') != 'passed'
            or len(checks) != 5 or any(x.get('passed') is not True for x in checks)
            or {x.get('name') for x in checks} != {
                'training-none', 'training-population', 'training-tax', 'training-both', 'user-data'}
            or interactive.get('cleanup') is not True
            or interactive.get('private_rpc', {}).get('closed') is not True
            or interactive.get('private_rpc', {}).get('errors') != []):
        raise ValueError('Base browser acceptance or cleanup is incomplete')
    quick = child['quickstart']['images']
    if len(quick) != 2 or {x.get('population') for x in quick} != {20000, 50000}:
        raise ValueError('Both qualified Quick Start populations are required')
    images = {f"quickstart-{x['population']}": x['image_id'] for x in quick}
    images.update({role: child['interactive_builds'][role]['image_id']
                   for role in ('training', 'user-data')})
    if (set(images) != set(ROLES) or len(set(images.values())) != 4
            or any(not re.fullmatch('sha256:[a-f0-9]{64}', x) for x in images.values())):
        raise ValueError('Expected four distinct immutable base image IDs')
    return root, child, images


def removal_block(repo=ROOT):
    blocks = []
    for profile in ('quickstart', 'training', 'user-data'):
        text = (repo/f'deploy/web-{profile}/Dockerfile').read_text()
        begin = '# Begin audited Pebble exclusion (20 October 2026).\n'
        end = '# End audited Pebble exclusion.\n'
        if text.count(begin) != 1 or text.count(end) != 1:
            raise ValueError('Every model recipe must contain one reviewed exclusion block')
        block = begin+text.split(begin)[1].split(end)[0]+end
        if ENTRYPOINT_HASH not in block or PEBBLE_HASH not in block or block.count('RUN ') != 1:
            raise ValueError('Missing audited runtime identity guards')
        blocks.append(block)
    if len(set(blocks)) != 1:
        raise ValueError('Model exclusion guards disagree')
    return blocks[0]


def verify_lineage(before, after):
    """Require one exclusion layer on the exact parent and identical launch config."""
    left = before['RootFS']['Layers']
    right = after['RootFS']['Layers']
    if not left or right[:-1] != left or len(right) != len(left)+1:
        raise ValueError('Hardening must add exactly one layer to the qualified parent')
    # Docker's legacy builder can update Config.Image; it is parent bookkeeping,
    # not a launch setting. Every other configuration field must stay identical.
    def config(image):
        return {key: value for key, value in image['Config'].items() if key != 'Image'}
    if config(before) != config(after):
        raise ValueError('Hardening changed runtime configuration')
    if config(after).get('User') != '10001:10001':
        raise ValueError('Expected the qualified non-root account')
    if config(after).get('Entrypoint') != ['/__cacert_entrypoint.sh']:
        raise ValueError('Unexpected runtime entrypoint')
    return dict(parent_layers=left, layers=right, runtime_config=config(after))


@contextmanager
def quiet_lock(directory):
    with (directory/'.simpaths-research-sequence.lock').open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another quiet campaign or image refresh is active') from error
        yield


def harden(client, role, parent, output, token, block, release, runtime):
    source_tag = f'simpaths-hardening:{token}-{role}-source'
    target_tag = f'simpaths-hardening:{token}-{role}'
    source = client.images.get(parent)
    if source.id != parent:
        raise ValueError('Base image identity differs from qualification')
    before = source.attrs
    context = output/role
    context.mkdir(mode=0o700)
    recipe = ('# (C) Copyright 2026, by Ross Richardson\n'
              '# Exclude only the audited unused executable from a qualified image.\n'
              '# @author ross richardson\n'
              f'FROM {source_tag}\nUSER root\n'+block+'USER 10001:10001\n')
    (context/'Dockerfile').write_text(recipe)
    (context/'.dockerignore').write_text('# (C) Copyright 2026, by Ross Richardson\n'
        '# Hardening context allowlist.\n# @author ross richardson\n*\n!Dockerfile\n')
    tagged = False
    try:
        if not source.tag(source_tag):
            raise RuntimeError('Cannot create the private build source reference')
        tagged = True
        print(f'Excluding inherited Pebble: {role}', flush=True)
        with (context/'build.log').open('x') as log:
            run_owned(['docker', 'build', '--network', 'none', '-t', target_tag, str(context)],
                      log, environment=dict(os.environ), timeout=600)
        image = client.images.get(target_tag)
        lineage = verify_lineage(before, image.attrs)
        container = client.containers.create(image.id, entrypoint=['/bin/true'], network_disabled=True)
        try:
            if release['archive_hash'](container, '/__cacert_entrypoint.sh') != ENTRYPOINT_HASH:
                raise ValueError('Entrypoint changed during hardening')
            if release['archive_hash'](container, '/app/app.jar') != runtime:
                raise ValueError('Application JAR changed during hardening')
            try:
                stream, _ = container.get_archive('/usr/bin/pebble')
            except Exception as error:
                if getattr(error, 'status_code', None) != 404:
                    raise
            else:
                close = getattr(stream, 'close', None)
                if close:
                    close()
                raise ValueError('Excluded executable is still present')
        finally:
            container.remove(force=True)
        print(f'Checking original entrypoint and Java startup: {role}', flush=True)
        probe = client.containers.create(image.id, command=['java', '-version'],
            network_disabled=True, read_only=True, cap_drop=['ALL'],
            security_opt=['no-new-privileges:true'], mem_limit='256m', memswap_limit='256m',
            nano_cpus=1_000_000_000, pids_limit=64)
        try:
            probe.start()
            status = probe.wait(timeout=90)
            logs = probe.logs(stdout=True, stderr=True)
            (context/'startup.log').write_bytes(logs)
            if status['StatusCode'] != 0 or not re.search(rb'openjdk version "25[.\"]', logs):
                raise RuntimeError('Original non-root entrypoint/Java startup failed')
        finally:
            probe.remove(force=True)
        return dict(parent_image_id=parent, image_id=image.id, tag=target_tag,
            excluded_path='/usr/bin/pebble', excluded_sha256=PEBBLE_HASH,
            entrypoint_sha256=ENTRYPOINT_HASH, runtime_jar_sha256=runtime,
            recipe_sha256=digest(context/'Dockerfile'), startup_sha256=digest(context/'startup.log'),
            lineage=lineage, passed=True, cleanup=True)
    finally:
        if tagged:
            if client.images.get(source_tag).id != parent:
                raise RuntimeError('Temporary source reference changed; it was not removed')
            client.images.remove(source_tag)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    qualification = args.qualification.expanduser().resolve(strict=True)
    base, child, images = load_base(qualification)
    block = removal_block()
    if args.dry_run:
        print(json.dumps(dict(images=images, guard_sha256=hashlib.sha256(block.encode()).hexdigest(),
            scope='One exclusion layer, unchanged launch configuration, JAR and startup checks; no model runs'), indent=2))
        return 0
    output = args.output.expanduser().absolute()
    temporary_root = Path(os.environ.get('TMPDIR', '/tmp')).resolve(strict=True)
    if output.parent.resolve(strict=True) != temporary_root or output.exists():
        raise ValueError('Choose a fresh output directly inside TMPDIR')
    if shutil.disk_usage(output.parent).free < 3*1024**3:
        raise ValueError('Need 3 GiB free for the small hardening build and reserve')
    for path, expected in base['protected'].items():
        if digest(path) != expected:
            raise ValueError('Protected benchmark artifact changed since base qualification')
    os.umask(0o077)
    output.mkdir(mode=0o700)
    (output/'COPYRIGHT.md').write_text(NOTICE)
    record = dict(format='simpaths.runtime-hardening.v1', passed=False, promoted=False,
        started=datetime.now(timezone.utc).isoformat(), base_qualification=str(qualification),
        base_qualification_sha256=digest(qualification), driver_sha256=digest(Path(__file__)),
        driver_path=str(Path(__file__).resolve()),
        guard_sha256=hashlib.sha256(block.encode()).hexdigest(), images={},
        scope='Base browser acceptance is reused with verified single-layer and runtime-configuration preservation; new native entrypoint/Java startup checks run for each image.')
    save(output/'report.json', record)
    client = None
    try:
        import docker
        release = runpy.run_path(str(ROOT/'deploy/web-quickstart/build_release.py'))
        with quiet_lock(temporary_root):
            client = docker.from_env(timeout=30)
            token = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)
            for role in ROLES:
                record['images'][role] = harden(client, role, images[role], output, token,
                    block, release, child['runtime_jar_sha256'])
                save(output/'report.json', record)
            for path, expected in base['protected'].items():
                if digest(path) != expected:
                    raise ValueError('Protected benchmark artifact changed during hardening')
            if digest(qualification) != record['base_qualification_sha256'] or removal_block() != block:
                raise ValueError('Qualification or hardening source changed during execution')
            record.update(passed=True, originals_unchanged=True)
    except BaseException as error:
        record.update(passed=False, error=type(error).__name__)
        print(f'STOPPED: {type(error).__name__}; evidence and candidate images retained.', flush=True)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                record.update(passed=False, error=type(error).__name__)
        record['finished'] = datetime.now(timezone.utc).isoformat()
        save(output/'report.json', record)
    print(('PASSED: ' if record['passed'] else 'FAILED: ')+str(output/'report.json'))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
