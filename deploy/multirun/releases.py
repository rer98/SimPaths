"""(C) Copyright 2026, by Ross Richardson

Immutable MultiRun release bundles and an atomic operator-selected default.
Retains legacy release IDs and never updates jobs, datasets, images or credentials.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from .artifacts import ArtifactError, digest, fingerprint, inventory, snapshot_files, write_attribution
from .resource_policy import DEFAULT_POLICY, LEGACY_POLICY, check_policy
from .schema import OUTPUT_CONTRACT, PROFILE_VERSION, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]
FORMAT = 'simpaths.multirun.release.v1'
CATALOGUE_FORMAT = 'simpaths.multirun.releases.v1'
CONTRACT = dict(configuration=SCHEMA_VERSION, profile=PROFILE_VERSION, output=OUTPUT_CONTRACT)
IDENTIFIER = re.compile(r'(?:release-[a-f0-9]{64}|local-[a-f0-9]{16})')


def private_directory(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ArtifactError('Release directories must not contain symlinks')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return existing_directory(path)


def existing_directory(path):
    """Verify a private directory without creating it or changing its mode."""
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ArtifactError('Release directories must not contain symlinks')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ArtifactError('Release directories must be private and service-owned')
    return path


def read_json(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ArtifactError('Release metadata must not contain symlinks')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r', encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 1024**2 or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ArtifactError('Release metadata must be private, bounded and service-owned')
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ArtifactError('Duplicate release metadata field')
                value[key] = item
            return value
        return json.load(source, object_pairs_hook=pairs)


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.release-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ReleaseRegistry:
    def __init__(self, state):
        self.state = Path(state).absolute()
        self.root = self.state/'releases'
        self.catalogue = self.root/'catalogue.json'

    @contextmanager
    def locked(self):
        private_directory(self.state)
        from .maintenance import service_state
        with service_state(self.state):
            with self._registry_lock():
                yield

    @contextmanager
    def _registry_lock(self):
        private_directory(self.root)
        fd = os.open(self.root/'registry.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ArtifactError('Release registry lock must be private and service-owned')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _catalogue(self):
        if self.catalogue.exists() or self.catalogue.is_symlink():
            value = read_json(self.catalogue)
            if (type(value) is not dict or set(value) != {'format', 'default', 'entries'}
                    or value['format'] != CATALOGUE_FORMAT or type(value['entries']) is not dict
                    or not 1 <= len(value['entries']) <= 100 or value['default'] not in value['entries']):
                raise ArtifactError('Invalid release catalogue; restore its verified metadata')
            for key, record in value['entries'].items():
                if (not IDENTIFIER.fullmatch(key) or type(record) is not dict
                        or set(record) != {'legacy', 'manifest'} or type(record['legacy']) is not bool
                        or record['legacy'] != key.startswith('local-')):
                    raise ArtifactError('Invalid release catalogue entry')
                from .prepared_dataset import check_manifest
                check_manifest({'release.json': record['manifest']})
            return value
        return None

    def _entry(self, key, record):
        directory = self.state/'release' if record['legacy'] else self.root/key
        if not directory.is_dir():
            raise ArtifactError('Retained release bundle is missing; restore it before restarting')
        existing_directory(directory)
        path = directory/'release.json'
        if fingerprint(path) != record['manifest']:
            raise ArtifactError('Retained release metadata changed; restore the original bundle')
        info = read_json(path)
        if record['legacy']:
            if type(info) is not dict or set(info) != {'image', 'model', 'defaults'}:
                raise ArtifactError('Invalid legacy release metadata')
            identity = info
            if key != 'local-'+info['model']['sha256'][:16]:
                raise ArtifactError('Legacy release identity changed')
            extra = dict(name='SimPaths UK — retained '+key, resource_policy=check_policy(LEGACY_POLICY))
        else:
            if (type(info) is not dict or set(info) != {'format', 'id', 'name', 'identity'}
                    or info['format'] != FORMAT or info['id'] != key):
                raise ArtifactError('Unsupported release manifest')
            identity = info['identity']
            if (type(identity) is not dict or set(identity) != {'image', 'model', 'defaults', 'helper', 'runner', 'contract', 'resource_policy'}
                    or key != 'release-'+digest(identity) or identity['contract'] != CONTRACT):
                raise ArtifactError('Release requires a compatible retained hosting adapter')
            self._name(info['name'])
            extra = dict(name=info['name'], helper=directory/'PrepareDataset.java',
                         runner=directory/'run.sh', contract=identity['contract'],
                         resource_policy=check_policy(identity['resource_policy']))
            for name, item in (('PrepareDataset.java', identity['helper']), ('run.sh', identity['runner'])):
                if fingerprint(directory/name) != item:
                    raise ArtifactError('Frozen release adapter changed')
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', str(identity['image'])):
            raise ArtifactError('Release runtime must pin an immutable installed image')
        from .prepared_dataset import check_manifest
        check_manifest({'model.jar': identity['model']})
        check_manifest(identity['defaults'])
        if fingerprint(directory/'model.jar') != identity['model'] or inventory(directory/'defaults') != identity['defaults']:
            raise ArtifactError('Frozen release JAR or parameter workbooks changed')
        return dict(image=identity['image'], jar=directory/'model.jar', defaults=directory/'defaults',
                    identity=identity, **extra)

    def _load(self, catalogue):
        # Default comes first for old callers/browser drafts. Retained versions
        # are always available for accepted preparation, retry, copying and YAML.
        order = [catalogue['default'], *sorted(set(catalogue['entries'])-{catalogue['default']})]
        return {key: self._entry(key, catalogue['entries'][key]) for key in order}

    def _legacy(self):
        path = self.state/'release'/'release.json'
        if not path.exists() and not path.is_symlink():
            return None
        info = read_json(path)
        if type(info) is not dict or set(info) != {'image','model','defaults'}:
            raise ArtifactError('Invalid legacy release metadata')
        key = 'local-'+info['model']['sha256'][:16]
        record = dict(legacy=True, manifest=fingerprint(path))
        self._entry(key, record)
        catalogue = dict(format=CATALOGUE_FORMAT, default=key, entries={key: record})
        atomic_json(self.catalogue, catalogue)
        return catalogue

    @staticmethod
    def _name(value):
        if type(value) is not str or not value.strip() or len(value) > 120 or any(ord(c) < 32 for c in value):
            raise ArtifactError('Release name must contain 1–120 printable characters')

    def load(self, *, expected_image=None):
        with self.locked():
            catalogue = self._catalogue() or self._legacy()
            if catalogue is None:
                raise ArtifactError('Register an approved release before starting this service')
            releases = self._load(catalogue)
            if expected_image is not None and next(iter(releases.values()))['image'] != expected_image:
                raise ArtifactError('Configured image differs from the selected release; register/select the reviewed release explicitly')
            return releases

    def inventory(self):
        """Verify a read-only snapshot of retained releases, without upgrading.

        Catalogue publication is atomic and bundles immutable. Reading the old
        catalogue during selection is valid; this creates no locks/directories.
        Legacy inventory does not write a catalogue. Reuse this for status/backup.
        """
        existing_directory(self.state)
        if self.root.exists() or self.root.is_symlink():
            existing_directory(self.root)
        catalogue = self._catalogue()
        if catalogue is None:
            path = self.state/'release'/'release.json'
            info = read_json(path)
            key = 'local-'+info['model']['sha256'][:16]
            catalogue = dict(default=key, entries={key:dict(legacy=True,manifest=fingerprint(path))})
        return self._load(catalogue)

    def register(self, *, image, name, jar, defaults, policy=None, make_default=False):
        self._name(name)
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', str(image)):
            raise ArtifactError('Register an immutable installed runtime image ID')
        policy = check_policy(DEFAULT_POLICY if policy is None else policy)
        from .prepare_inputs import replacement_workbooks
        sources = replacement_workbooks(defaults)
        if any(p.is_symlink() for p in Path(defaults).glob('*.xls*')):
            raise ArtifactError('Release workbooks must not be symlinks')
        with self.locked():
            catalogue = self._catalogue() or self._legacy()
            if catalogue:
                self._load(catalogue)  # Fail closed on any missing/tampered retained bundle.
            staging = Path(tempfile.mkdtemp(prefix='.pending-', dir=self.root))
            try:
                identity = dict(image=image, model=fingerprint(jar, staging/'model.jar'),
                    defaults=snapshot_files(sources, staging/'defaults'),
                    helper=fingerprint(Path(__file__).with_name('PrepareDataset.java'), staging/'PrepareDataset.java'),
                    runner=fingerprint(Path(__file__).with_name('container_run.sh'), staging/'run.sh'),
                    contract=CONTRACT, resource_policy=policy)
                key = 'release-'+digest(identity)
                target = self.root/key
                existing = catalogue and key in catalogue['entries']
                if existing:
                    if read_json(target/'release.json')['name'] != name:
                        raise ArtifactError('This exact release already has a name; retain its recorded name')
                else:
                    if catalogue and len(catalogue['entries']) >= 100:
                        raise ArtifactError('Release registry is full; retain and review its dependencies')
                    atomic_json(staging/'release.json', dict(format=FORMAT, id=key, name=name, identity=identity))
                    write_attribution(staging)
                    for file in staging.rglob('*'):
                        if file.is_file():
                            file.chmod(0o400)
                            with file.open('rb') as source: os.fsync(source.fileno())
                    for path in (staging/'defaults',staging):
                        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
                        try: os.fsync(directory)
                        finally: os.close(directory)
                    if target.exists() or target.is_symlink():
                        # Crash between bundle publication and catalogue commit:
                        # adopt only the identical verified bundle, never replace it.
                        record = dict(legacy=False, manifest=fingerprint(target/'release.json'))
                        self._entry(key, record)
                        if read_json(target/'release.json') != read_json(staging/'release.json'):
                            raise ArtifactError('Unregistered release bundle differs; inspect retained state')
                    else:
                        staging.rename(target)
                    catalogue = catalogue or dict(format=CATALOGUE_FORMAT, default=key, entries={})
                    catalogue['entries'][key] = dict(legacy=False, manifest=fingerprint(target/'release.json'))
                if make_default: catalogue['default'] = key
                atomic_json(self.catalogue, catalogue)
                return key
            finally:
                if staging.exists(): shutil.rmtree(staging)

    def select(self, key):
        with self.locked():
            catalogue = self._catalogue() or self._legacy()
            if catalogue is None or key not in catalogue['entries']:
                raise ArtifactError('Select an existing verified release ID')
            self._load(catalogue)
            catalogue['default'] = key
            atomic_json(self.catalogue, catalogue)


def installed_image(image):
    from .import_quickstart import docker
    metadata = json.loads(docker('image', 'inspect', image))[0]
    if metadata['Id'] != image or metadata['Config'].get('Volumes'):
        raise ArtifactError('Install the approved pinned runtime without implicit volumes')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('list', 'register', 'select'))
    parser.add_argument('--state', type=Path, default=Path.home()/'simpaths-multirun-local')
    parser.add_argument('--image', help='Already installed sha256: image ID; no pulls or model execution')
    parser.add_argument('--name', help='Human-readable approved release name')
    parser.add_argument('--jar', type=Path, default=ROOT/'multirun.jar')
    parser.add_argument('--defaults', type=Path, default=ROOT/'input')
    parser.add_argument('--resource-policy', type=Path, help='Optional versioned operator JSON policy')
    parser.add_argument('--make-default', action='store_true')
    parser.add_argument('--release', help='Existing ID for select')
    args = parser.parse_args(argv)
    registry = ReleaseRegistry(args.state)
    if args.command == 'register':
        if not args.image or not args.name: parser.error('Registration requires --image and --name')
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', args.image): parser.error('Use an immutable sha256: image ID')
        installed_image(args.image)
        policy = read_json(args.resource_policy) if args.resource_policy else None
        key = registry.register(image=args.image, name=args.name, jar=args.jar, defaults=args.defaults,
                                policy=policy, make_default=args.make_default)
        print('Retained release: '+key)
    elif args.command == 'select':
        if not args.release: parser.error('Selection requires --release')
        releases = registry.load()
        if args.release not in releases: parser.error('Release is unavailable')
        installed_image(releases[args.release]['image'])
        registry.select(args.release)
        print('Selected default. Restart the service to apply it; accepted work remains pinned.')
    else:
        releases = registry.load()
        for index, (key, entry) in enumerate(releases.items()):
            storage=entry['resource_policy']['simulation']['storage']
            print(('default ' if index == 0 else 'retained ')+key+' | '+entry['name']+' | '+entry['image']+
                  f" | working storage: {storage['setup_mib']/1024:g} GiB + {storage['per_repetition_mib']} MiB/repetition")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
