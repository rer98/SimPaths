"""(C) Copyright 2026, by Ross Richardson
Verified configurable UK training inputs, retaining aggregate-only provider access.
Only the four pinned public source files can acquire this importer certificate.
No browser-uploaded receipt or arbitrary provider dataset becomes public.
@author ross richardson
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from uuid import uuid4

from .artifacts import ArtifactError, digest, fingerprint

FORMAT='simpaths.public-training.sources.v1'
NAME='UK training — configurable population'
SCHEDULE=[[f'uk_{year}_std.txt',str(year),str(year),'Public training example'] for year in (2015,2019,2020)]


def source_manifest():
    path=Path(__file__).with_name('public_training_sources.json')
    if path.is_symlink() or path.stat().st_size>16384:
        raise ArtifactError('Invalid public training source manifest')
    value=json.loads(path.read_text())
    names={'population_initial_UK_2019.csv',*(f'uk_{year}_std.txt' for year in (2015,2019,2020))}
    if (set(value)!={'format','repository','files','paths'} or value['format']!=FORMAT
            or value['repository']!='https://github.com/simpaths/SimPaths'
            or set(value['files'])!=names or set(value['paths'])!=names):
        raise ArtifactError('Preserve the reviewed public training manifest')
    from .prepared_dataset import check_manifest
    check_manifest(value['files'])
    for name,path in value['paths'].items():
        expected='input/'+('InitialPopulations' if name.endswith('.csv') else 'EUROMODoutput')+'/training/'+name
        if path!=expected:raise ArtifactError('Unexpected public training source path')
    return value


def is_public_receipt(receipt):
    """Recognise only a fully bound importer certificate, never a filename alone."""
    from .prepared_dataset import INPUT_FORMAT, check_input_receipt
    identity=receipt['identity']
    if identity.get('format')!=INPUT_FORMAT or 'public_training' not in identity:
        return False
    check_input_receipt(receipt)
    manifest=source_manifest()
    expected=dict(format=FORMAT,files=manifest['files'])
    if (identity['public_training']!=expected or identity['start_year']!=2019
            or identity['sources']['uploads']!=manifest['files'] or 'release' not in identity
            or identity['selection']!=dict(source='uploads',year=2019,schedule=SCHEDULE)):
        raise ArtifactError('The public training certificate does not match the verified sources')
    for name,item in manifest['files'].items():
        relative=('InitialPopulations/' if name.endswith('.csv') else 'EUROMODoutput/')+name
        if identity['prepared'].get(relative)!=item:
            raise ArtifactError('The public training input changed during preparation')
    return True


def certify(receipt):
    """Called only by the importer after normal preparation and release binding."""
    value=deepcopy(receipt)
    value['identity']['public_training']=dict(format=FORMAT,files=source_manifest()['files'])
    value['sha256']=digest(value['identity']);value['revision']='inputs-'+value['sha256']
    if not is_public_receipt(value):raise ArtifactError('Verified public inputs are required')
    return value


def _copy_member(stream,target,expected):
    """Read one ordinary Docker-copy file; refuse links, extra files or inflation."""
    with tarfile.open(fileobj=stream,mode='r|') as archive:
        count=0
        for item in archive:
            count+=1
            if (count!=1 or item.name!=target.name or not item.isfile() or item.sparse
                    or item.size!=expected['bytes'] or not 0<item.size<=512*1024**2):
                raise ArtifactError('Unexpected public source archive member')
            with archive.extractfile(item) as incoming,target.open('xb') as outgoing:
                shutil.copyfileobj(incoming,outgoing,1024**2)
        if count!=1:raise ArtifactError('Public source archive is empty')
    if fingerprint(target)!=expected:raise ArtifactError('The image contains different public training data')


def copy_sources(image, destination):
    """Read a stopped qualified image; no container entrypoint is executed."""
    from .import_quickstart import DOCKER,docker
    manifest=source_manifest();destination=Path(destination)
    destination.mkdir(mode=0o700)
    container='simpaths-public-sources-'+uuid4().hex
    created=False
    try:
        docker('create','--name',container,'--network','none','--read-only','--cap-drop','ALL',
            '--security-opt','no-new-privileges:true','--memory','128m','--cpus','0.5',
            '--pids-limit','32','--user','10001:10001','--entrypoint','/bin/false',image)
        created=True
        for name,item in manifest['files'].items():
            with subprocess.Popen([*DOCKER,'cp',container+':/app/'+manifest['paths'][name],'-'],
                                  stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as process:
                try:
                    _copy_member(process.stdout,destination/name,item)
                    if process.wait(timeout=60):raise ArtifactError('Docker source copy failed')
                finally:
                    if process.poll() is None:process.kill()
                    process.wait()
        return {name:destination/name for name in manifest['files']}
    finally:
        if created:docker('rm',container)


def prepare_example(release_id,release,output,frontend):
    """Prepare once with the existing native validator, then publish a certificate.

    The caller holds the maintenance fence and shared processing reservation.
    Existing matching preparation is reused after an interrupted installation.
    """
    from .prepared_dataset import verify_snapshot
    from .prepare_inputs import prepare
    from .queue_adapter import read_prepared
    from .releases import atomic_json
    from .backup_files import publish
    output=Path(output)
    if output.exists():
        receipt=read_prepared(output);verify_snapshot(output,receipt)
        if not is_public_receipt(receipt) or receipt['identity']['release']['id']!=release_id:
            raise ArtifactError('Preserve a different existing public training preparation')
        return receipt
    temporary=Path(tempfile.mkdtemp(prefix='.public-training-sources-',dir=output.parent))
    staging=output.with_name('.pending-'+output.name+'-'+uuid4().hex)
    try:
        sources=copy_sources(release['image'],temporary/'sources')
        print('Preparing the verified public training population and donor inputs; no simulations are run.',flush=True)
        receipt=prepare(release['defaults'],sources,dict(year=2019,schedule=SCHEDULE),
                        release['jar'],release['image'],staging,frontend)
        identity=receipt['identity']
        if (identity['model']!=release['identity']['model']
                or identity['sources']['defaults']!=release['identity']['defaults']):
            raise ArtifactError('Prepared public inputs differ from the retained model release')
        if fingerprint(release['runner'],staging/'run.sh')!=release['identity']['runner']:
            raise ArtifactError('The retained execution script changed')
        identity['release']=dict(id=release_id,contract=release['contract'],
            resource_policy=release['resource_policy'],runner=release['identity']['runner'])
        # prepare() has already checked the native input tables and file hashes.
        receipt=certify(receipt)
        atomic_json(staging/'receipt.json',receipt)
        verify_snapshot(staging,receipt)
        os.chmod(staging/'run.sh',0o400)
        # Only a complete, certified snapshot gets the stable public-example path.
        # Failed/uncertain staging stays private for diagnosis and cannot block a
        # later confirmed retry by looking like an already published example.
        publish(staging,output)
        return receipt
    finally:
        shutil.rmtree(temporary)
