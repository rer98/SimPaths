"""(C) Copyright 2026, by Ross Richardson
Install and restore a staged Visualiser directory under the MultiRun update coordinator.
@author ross richardson
"""
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit
from uuid import UUID

from .. import update_package as package
from ..visualiser_backend import VisualiserBackend
from ..visualiser_editions import CALCULATOR_FILES, historical_editions


FORMAT = 'simpaths.visualiser.install.v1'
REQUIRED = {'index.html', 'visualiser.js', 'visualiser.css', *CALCULATOR_FILES}
MAX_ASSET = 8 * 1024**2  # Same bound as the hosted reader.


def manifest_digest(directory):
    path = package.trusted(directory / 'build.json')
    if path.stat().st_size > 1024**2:
        raise ValueError('Visualiser manifest exceeds the reader limit')
    return package.digest(path.read_bytes())


def checked_bundle(directory, expected):
    package.trusted(directory, directory=True)
    if manifest_digest(directory) != expected:
        raise ValueError('Visualiser manifest differs from the expected release')
    manifest = json.loads((directory / 'build.json').read_bytes())
    files = manifest['files']
    if (manifest['format'] != 'simpaths.visualiser.build.v1' or
            manifest['mode'] not in ('development-levels', 'development-paired') or
            not re.fullmatch('[0-9a-f]{40}', manifest['revision']) or
            not isinstance(files, dict) or not REQUIRED <= files.keys() or
            'build.json' in files or len(files) > 1024):
        raise ValueError('Unexpected Visualiser release inventory')
    if {p.name for p in directory.iterdir()} != files.keys() | {'build.json'}:
        raise ValueError('Unexpected or missing file in the Visualiser release')
    for name, digest in files.items():
        if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', name):
            raise ValueError('Invalid Visualiser asset name')
        path = package.trusted(directory / name)
        if path.stat().st_size > MAX_ASSET or package.digest(path.read_bytes()) != digest:
            raise ValueError('Visualiser asset differs from the manifest: ' + name)
    historical_editions(manifest)
    return manifest


def exchange_directories(first, second):
    """Linux RENAME_EXCHANGE keeps the configured name present across a crash."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    first_parent = os.open(first.parent, flags)
    try:
        second_parent = os.open(second.parent, flags)
        try:
            if rename(first_parent, os.fsencode(first.name), second_parent, os.fsencode(second.name), 2):
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            os.fsync(first_parent)
            os.fsync(second_parent)
        finally:
            os.close(second_parent)
    finally:
        os.close(first_parent)


def verify_assets(options, manifest):
    paths = {'/visualiser/' + '0' * 64: 'index.html'}
    for name in manifest['files']:
        if name != 'index.html' and (name in VisualiserBackend.APPLICATION_ASSETS or
                re.fullmatch(r'\d+\.visualiser\.js', name)):
            paths['/visualiser-assets/' + name] = name
    for route, name in paths.items():
        body = subprocess.check_output([
            'curl', '--disable', '--noproxy', '*', '--silent', '--show-error', '--fail',
            '--max-time', '15', '--max-filesize', str(MAX_ASSET), options.origin + route
        ], timeout=20, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
        if package.digest(body) != manifest['files'][name]:
            raise ValueError('HTTPS served a different Visualiser asset: ' + name)


class BundleInstallation(package.Installation):
    """Requires a root-staged package, a verified decision and the coordinator lock."""

    def __init__(self, options, queue, source, plan_digest):
        source = Path(source)
        if (source.parent != package.ROOT / 'visualiser-updates' or
                str(UUID(source.name)) != source.name):
            raise ValueError('Expected a Visualiser update transaction directory')
        self.live = Path(options.visualiser_build)
        if (self.live.parent != package.ROOT or self.live == source.parent or
                not self.live.name.startswith('visualiser-')):
            raise ValueError('Unexpected configured Visualiser build location')
        self.slot = source / 'bundle'
        package.trusted(self.live, directory=True)
        package.trusted(self.slot, directory=True)
        body = package.trusted(source / 'plan.json').read_bytes()
        if package.digest(body) != plan_digest:
            raise ValueError('Staged Visualiser plan differs from the requested plan')
        plan = json.loads(body)
        if (set(plan) != {'format', 'before', 'after'} or plan['format'] != FORMAT or
                not all(re.fullmatch('[a-f0-9]{64}', plan[k]) for k in ('before', 'after')) or
                plan['before'] == plan['after']):
            raise ValueError('Expected two distinct pinned Visualiser manifests')
        self.before, self.after = plan['before'], plan['after']
        self.restoring = False
        origin = urlsplit(options.origin)
        host = '['+origin.hostname+']' if ':' in origin.hostname else origin.hostname
        self.health_urls = (f'https://{host}:{origin.port or 443}/healthz',f'https://{host}:8443/health')
        # Keep the existing drain, service health, backup fence and durable phase receipt.
        super().__init__(options, queue, source, dict(mode='drain', targets={}))

    def check_visualiser_calculation(self):
        old = checked_bundle(self.live, self.before)
        new = checked_bundle(self.slot, self.after)
        if self.live.stat().st_dev != self.slot.stat().st_dev:
            raise ValueError('Stage the Visualiser on the installed filesystem')
        self.check_retained_calculations(old, new, historical_editions(old), historical_editions(new))

    def publish_programs(self):
        checked_bundle(self.live, self.before)
        checked_bundle(self.slot, self.after)
        exchange_directories(self.live, self.slot)

    def restore_programs(self):
        # The durable host snapshot precedes publication. Either orientation is
        # possible after an interruption, including a restart during rollback.
        if manifest_digest(self.live) != self.before:
            if manifest_digest(self.live) != self.after:
                raise ValueError('The installed Visualiser differs from this transaction')
            checked_bundle(self.slot, self.before)
            exchange_directories(self.live, self.slot)
        checked_bundle(self.live, self.before)

    def verify(self):
        checked_bundle(self.slot, self.before)
        return super().verify()

    def verify_common(self):
        expected = self.before if self.restoring else self.after
        verify_assets(self.options, checked_bundle(self.live, expected))
        return super().verify_common()

    def rollback(self):
        if (self.private_stopped or self.public_stopped) and self.backup_fd is None:
            self.backup_fence()
        if self.snapshot is None:
            checked_bundle(self.live, self.before)
        self.restoring = True
        try:
            return super().rollback()
        finally:
            self.restoring = False

    def restore_and_verify(self):
        """Coordinator recovery starts from the persisted plan and host snapshot."""
        if (self.source / 'install-progress.json').exists():
            self.load_progress()
        return self.rollback()
