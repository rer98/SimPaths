"""(C) Copyright 2026, by Ross Richardson

Prepare selected population/UKMOD/workbook files using the SingleRun validators.
Server/maintainer paths only; browser adapters resolve opaque upload IDs first.
Ownership and provider restrictions live in the platform database, not receipts.

@author ross richardson
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4

from deploy._workflow import frontend_path
from .artifacts import ArtifactError, digest, fingerprint, inventory, snapshot_files, verify, write_attribution, write_json
from .import_quickstart import docker, UnconfirmedVerification
from .prepared_dataset import INPUT_FORMAT, check_input_receipt

ROOT = Path(__file__).resolve().parents[2]
RESERVED = {'DatabaseCountryYear.xlsx', 'EUROMODpolicySchedule.xlsx'}


def selection(request):
    if not isinstance(request, dict) or set(request) != {'year', 'schedule'}:
        raise ArtifactError('Supply exactly year and schedule')
    year = request['year']
    if type(year) is not int or not 2011 <= year <= 2024:
        raise ArtifactError('UK start year must be an integer in 2011–2024')
    supplied = request['schedule']
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= 100:
        raise ArtifactError('Select 1–100 policy rows')
    rows, years = [], set()
    for row in supplied:
        if not isinstance(row, list) or len(row) != 4 or any(not isinstance(x, str) for x in row):
            raise ArtifactError('Supply four text columns per policy')
        name, start, system, description = [x.strip() for x in row]
        if not start:
            continue
        if (not re.fullmatch(r'[A-Za-z0-9_-]+\.txt', name) or len(name) > 164
                or not re.fullmatch(r'[0-9]{4}', start) or not re.fullmatch(r'[0-9]{4}', system)
                or not 1900 <= int(start) <= 2500 or not 1900 <= int(system) <= 2500
                or int(start) in years or len(description) > 1000):
            raise ArtifactError('Invalid or repeated policy year, filename or description')
        years.add(int(start))
        rows.append([name, start, system, description])
    if not rows:
        raise ArtifactError('At least one policy must have a start year')
    return dict(source='uploads', year=year, schedule=rows)


def selected_sources(defaults, uploads, request):
    """Names are logical filenames; values are resolved private server paths."""
    default = {p.name: p for p in Path(defaults).glob('*.xls*')
               if re.fullmatch(r'[A-Za-z0-9_-]+\.xlsx?', p.name) and p.name not in RESERVED}
    if not default:
        raise ArtifactError('Model parameter workbooks are missing')
    required = {f"population_initial_UK_{request['year']}.csv"}
    required.update(r[0] for r in request['schedule'])
    if not required <= set(uploads):
        raise ArtifactError('Selected population or policy file is missing')
    if len(uploads) > 100:
        raise ArtifactError('Too many selected files')
    for name in uploads:
        if name not in required and (name not in default or name in RESERVED):
            raise ArtifactError('Select the required population/policies or a declared parameter workbook')
    return default, dict(uploads)


def run_container(staged, request, image, output, frontend, timeout_seconds):
    sys.path.insert(0, str(frontend))
    from jasmine_web.batch.docker_executor import DockerExecutor, ContainerCommand
    control = Path(tempfile.mkdtemp(prefix='simpaths-input-preparation-', dir=output.parent))
    classes = control / 'classes'
    classes.mkdir(mode=0o700)
    try:
        with (output/'compile.log').open('w') as log:
            subprocess.run(['javac','-proc:none','-cp',str(staged/'model.jar'),'-d',str(classes),
                            str(Path(__file__).with_name('PrepareDataset.java'))],
                           stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
    except BaseException:
        shutil.rmtree(control)
        raise
    attempt = str(uuid4())
    lease = SimpleNamespace(attempt_id=attempt,execution_key='batch-'+attempt,
        configuration_id='prepare-inputs', specification={'model_digest':image},
        resources=dict(cpu_millis=2000,memory_mib=5120,storage_mib=12288),
        deadline=datetime.now(timezone.utc)+timedelta(seconds=timeout_seconds))
    class Adapter:
        def container_command(self, lease, directory):
            shutil.copytree(classes,directory/'classes')
            write_json(directory/'selection.json',request)
            return ContainerCommand(image,('/opt/java/openjdk/bin/java','-Xmx512m',
                '-XX:ActiveProcessorCount=2','-Djava.awt.headless=true','-cp',
                '/request/classes:/inputs/model.jar','simpaths.experiment.PrepareDataset'),str(staged))
    executor = DockerExecutor(control/'attempts',approved_images=[image],input_roots=[staged])
    with executor.exclusive():
        try:
            executor.start(lease,Adapter())
            end, progress = time.monotonic()+timeout_seconds+30, time.monotonic()+30
            while time.monotonic() < end:
                state = executor.inspect(lease)
                if state['state']=='stopped':
                    break
                if time.monotonic() >= progress:
                    print('Isolated input preparation is still running',flush=True)
                    progress=time.monotonic()+30
                time.sleep(.5)
            else:
                raise ArtifactError('Preparation deadline exceeded')
            log = executor.workspace(lease)/'execution.log'
            shutil.copyfile(log,output/'preparation.log')
            if (state['outcome']!='success' or
                    'MULTIRUN_INPUTS_PREPARED_AND_VALIDATED' not in log.read_text()):
                raise ArtifactError('Input preparation failed; see preparation.log')
            # Stopped containers cannot alter this tree during verification/move.
            candidate = executor.workspace(lease)/'work/input'
            inventory(candidate)  # Reject links/non-ordinary files before publishing.
            candidate.rename(output/'input')
        finally:
            try:
                if executor.inspect(lease)['state']!='stopped':
                    executor.stop(lease,'cancelled')
                    end=time.monotonic()+15
                    while time.monotonic()<end and executor.inspect(lease)['state']=='running':
                        time.sleep(.2)
                executor.cleanup(lease)
            except Exception as error:
                raise UnconfirmedVerification('Retain preparation state at '+str(control)) from error
            shutil.rmtree(control)


def prepare(defaults, uploads, request, jar, image, output, frontend, *, timeout_seconds=3600):
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ArtifactError('Preparation deadline must be 1–3600 seconds')
    request = selection(request)
    default, uploads = selected_sources(defaults,uploads,request)
    output = Path(output).absolute()
    if output.exists() or any(p.is_symlink() for p in (output,*output.parents)) or output.is_relative_to(ROOT):
        raise ArtifactError('Choose a new private output directory outside the checkout')
    metadata=json.loads(docker('image','inspect',image))[0]
    image=metadata['Id']
    if metadata['Config'].get('Volumes'):
        raise ArtifactError('Runtime must not declare writable volumes')
    # Limits match the existing upload flow. The container repeats format/budget
    # validation; files are hashed while copying and verified again afterwards.
    sizes = [fingerprint(p)['bytes'] for p in uploads.values()]
    if any(n > 512<<20 or n <= 0 for n in sizes) or sum(sizes)>2<<30:
        raise ArtifactError('Selected uploads exceed 512 MiB/file or 2 GiB total')
    required=3*sum(sizes)+sum(p.stat().st_size for p in default.values())+(2<<30)
    if shutil.disk_usage(output.parent).free<required:
        raise ArtifactError('Insufficient space for preparation copies and 2 GiB working reserve')
    output.mkdir(mode=0o700)
    write_attribution(output)
    staged=output/'sources'
    staged.mkdir(mode=0o700)
    uncertain=False
    try:
        model=fingerprint(jar,staged/'model.jar')
        source=dict(defaults=snapshot_files(default,staged/'defaults'),
                    uploads=snapshot_files(uploads,staged/'uploads'))
        run_container(staged,request,image,output,frontend,timeout_seconds)
        for kind in ('defaults','uploads'):
            verify(staged/kind,source[kind])
        if fingerprint(staged/'model.jar')!=model:
            raise ArtifactError('Model changed during preparation')
        prepared=inventory(output/'input')
        # All supplied files must survive preparation unchanged at their model path.
        for name, item in source['uploads'].items():
            relative=('InitialPopulations/' if name.endswith('.csv') else
                      'EUROMODoutput/' if name.endswith('.txt') else '')+name
            if prepared.get(relative)!=item:
                raise ArtifactError('Preparation changed a selected input')
        for name,item in source['defaults'].items():
            if name not in uploads and prepared.get(name)!=item:
                raise ArtifactError('Preparation changed a parameter workbook')
        (staged/'model.jar').rename(output/'model.jar')
        identity=dict(format=INPUT_FORMAT,source='validated-selected-files',country='UK',
            start_year=request['year'],source_image=image,model=model,sources=source,
            selection=request,prepared=prepared)
        receipt=dict(identity=identity,sha256=digest(identity),revision='inputs-'+digest(identity))
        check_input_receipt(receipt)
        for path in [output/'model.jar',*(output/'input').rglob('*')]:
            if path.is_file():
                os.chmod(path,0o400)
        write_json(output/'receipt.json',receipt)
        return receipt
    except UnconfirmedVerification:
        uncertain=True
        raise
    except BaseException:
        shutil.rmtree(output/'input',ignore_errors=True)
        (output/'model.jar').unlink(missing_ok=True)
        raise
    finally:
        if not uncertain:
            shutil.rmtree(staged)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--defaults',type=Path,default=ROOT/'input')
    parser.add_argument('--uploads',type=Path,required=True,help='Private directory of selected ordinary files')
    parser.add_argument('--selection',type=Path,required=True,help='JSON year and four-column policy schedule')
    parser.add_argument('--jar',type=Path,default=ROOT/'multirun.jar')
    parser.add_argument('--image',required=True,help='Maintainer-approved installed Java runtime')
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if fingerprint(args.selection)['bytes']>128*1024:
        parser.error('Selection exceeds 128 KiB')
    request=json.loads(args.selection.read_text())
    uploads={p.name:p for p in args.uploads.iterdir()}
    selected_sources(args.defaults,uploads,selection(request))
    if not args.apply:
        print('Preview only. Add --apply to validate and prepare in an isolated container.')
        return
    result=prepare(args.defaults,uploads,request,args.jar,args.image,args.output,args.frontend)
    print(f"Ready: {args.output}/receipt.json\nDataset revision: {result['revision']}")


if __name__=='__main__':
    main()
