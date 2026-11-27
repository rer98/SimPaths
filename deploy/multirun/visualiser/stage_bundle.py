"""(C) Copyright 2026, by Ross Richardson
Stage the reviewed bytes from a Visualiser qualification artifact for the host installer.
@author ross richardson
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import UUID
from zipfile import ZipFile

from .. import update_package as package
from .bundle_installation import FORMAT, MAX_ASSET, checked_bundle
from .check_candidate import SOURCE, row_changes


def write_file(path, data, mode):
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def copy_artifact(archive, evidence, directory):
    def read(name, limit):
        info = archive.getinfo(name)
        if info.is_dir() or info.file_size > limit:
            raise ValueError('Invalid Visualiser artifact member: ' + name)
        with archive.open(info) as stream:
            body = stream.read(limit + 1)
        if len(body) != info.file_size:
            raise ValueError('Incomplete Visualiser artifact member: ' + name)
        return body

    names = archive.namelist()
    if len(set(names)) != len(names):
        raise ValueError('Duplicate Visualiser artifact member')
    expected = dict(format='simpaths.visualiser.workflow.v1', simpaths=evidence['project'],
                    frontend=evidence['frontend'], visualiser=evidence['visualiser'],
                    manifest_sha256=evidence['manifest'])
    if json.loads(read('workflow.json', 65536)) != expected:
        raise ValueError('Artifact revisions differ from the MultiRun review')
    report = json.loads(read('report.json', 1024**2))
    comparison = report['comparison']
    comparison_bytes = read('comparison.json', 32 * 1024**2)
    if (report['source'] != SOURCE or report['revision'] != evidence['visualiser'] or
            report['reference'] != evidence['reference'] or report['manifest_sha256'] != evidence['manifest'] or
            report['status'] != evidence['outcome'] or report['status'] not in
            ('checks passed', 'technical review', 'scientific review') or
            report['checks'].get('chromium') != 'passed' or set(report['checks'].values()) != {'passed'} or
            comparison['rows'] != evidence['rows'] or comparison['differences_from_release'] != evidence['changed'] or
            comparison['local_folder_differences'] != 0 or
            comparison['sha256'] != package.digest(comparison_bytes)):
        raise ValueError('Qualification evidence differs from the MultiRun review')
    if 'row_changes' in evidence or 'row_changes' in comparison:
        counts = row_changes(json.loads(comparison_bytes)['release_differences'])
        if counts != evidence.get('row_changes') or counts != comparison.get('row_changes', counts):
            raise ValueError('Chart row changes differ from the MultiRun review')
    manifest = read('build/build.json', 1024**2)
    if package.digest(manifest) != evidence['manifest']:
        raise ValueError('Visualiser artifact manifest differs from the reviewed digest')
    metadata = json.loads(manifest)
    files = metadata['files']
    if metadata['revision'] != evidence['visualiser'] or not isinstance(files, dict) or len(files) > 1024:
        raise ValueError('Unexpected Visualiser artifact inventory')
    members = {name for name in names if name.startswith('build/') and name != 'build/'}
    if members != {'build/' + name for name in files} | {'build/build.json'}:
        raise ValueError('Unexpected or missing file in the Visualiser artifact')
    if sum(archive.getinfo(name).file_size for name in members) > 256 * 1024**2:
        raise ValueError('Visualiser build exceeds the unpacked size limit')
    for name, digest in files.items():
        if name == 'build.json' or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', name):
            raise ValueError('Invalid Visualiser artifact filename')
        data = read('build/' + name, MAX_ASSET)
        if package.digest(data) != digest:
            raise ValueError('Visualiser artifact asset differs: ' + name)
        write_file(directory / name, data, 0o644)
    write_file(directory / 'build.json', manifest, 0o644)
    directory.chmod(0o755)
    checked_bundle(directory, evidence['manifest'])
    sync_directory(directory)


def unpack_download(archive_path, archive_sha256, evidence, work, directory):
    frozen = work / 'artifact.zip'
    hasher = hashlib.sha256()
    fd = os.open(archive_path, os.O_NOFOLLOW | os.O_NONBLOCK | os.O_RDONLY)
    with os.fdopen(fd, 'rb') as source, frozen.open('xb') as dest:
        limit = 256 * 1024**2
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Expected a bounded Visualiser artifact download')
        total = 0
        while block := source.read(1024**2):
            total += len(block)
            if total > limit:
                raise ValueError('Visualiser download exceeds the artifact limit')
            hasher.update(block)
            dest.write(block)
    if hasher.hexdigest() != archive_sha256:
        raise ValueError('Download differs from the expected GitHub artifact digest')
    directory.mkdir(mode=0o700)
    with ZipFile(frozen) as archive:
        copy_artifact(archive, evidence, directory)


def stage(archive_path, archive_sha256, evidence, before, reference):
    """Caller authenticates the GitHub artifact digest and serializes staging with the coordinator lock."""
    if not re.fullmatch('[0-9a-f]{64}', before) or before == evidence['manifest']:
        raise ValueError('Expected a distinct predecessor Visualiser manifest')
    store = package.trusted(package.ROOT / 'visualiser-updates', directory=True)
    transaction = store / str(UUID(reference))
    plan = json.dumps(dict(format=FORMAT, before=before, after=evidence['manifest']), sort_keys=True).encode()
    if transaction.exists():
        if package.trusted(transaction / 'plan.json').read_bytes() != plan:
            raise ValueError('Another Visualiser plan owns this transaction')
        return transaction, package.digest(plan)
    # Copy once into a private file before inspecting the ZIP. A producer that
    # still owns the download cannot change the bytes after digest verification.
    with tempfile.TemporaryDirectory(dir=store) as temp:
        work = Path(temp)
        release = work / 'release'
        release.mkdir(mode=0o700)
        directory = release / 'bundle'
        unpack_download(archive_path, archive_sha256, evidence, work, directory)
        write_file(release / 'plan.json', plan, 0o600)
        sync_directory(release)
        release.rename(transaction)
        sync_directory(store)
    return transaction, package.digest(plan)
