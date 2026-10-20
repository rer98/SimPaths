#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Build security candidates in private committed-source clones and a fresh Python
environment, preserving the laptop's scientific JARs, inputs and model catalogue.
Candidate images are never promoted. Existing native acceptance owns cleanup.
@author ross richardson
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._workflow import frontend_path, quickstart_image
from deploy.acceptance.run_security_refresh import exclusive_trials
from deploy.acceptance.isolated_refresh_storage import (GIB, RESERVE, checkpoint,
    compact_dockerfile, required_build_space, reserve_reports, retire_copies)

NOTICE = ('<!-- (C) Copyright 2026, by Ross Richardson\n'
          'Isolated security candidate source, dependency and workflow receipts.\n'
          '@author ross richardson -->\n\nThird-party source/packages retain their original licences and provenance.\n')


def digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected an ordinary protected file')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(root, *arguments):
    return subprocess.check_output(['git', '-C', str(root), *arguments], text=True).strip()


def selected_sources(repositories):
    result = {}
    for name, root in repositories.items():
        revision = git(root, 'rev-parse', '--verify', 'HEAD')
        if not re.fullmatch(r'[a-f0-9]{40,64}', revision):
            raise ValueError('Invalid source commit')
        changed = set(git(root, 'diff', '--name-only', 'HEAD').splitlines())
        allowed = {'input/DatabaseCountryYear.xlsx','input/EUROMODpolicySchedule.xlsx'} if name=='model' else set()
        if changed-allowed:
            raise ValueError('Commit tracked source changes before selecting candidates: '+name)
        result[name] = dict(path=str(root), commit=revision,
                            excluded_worktree_changes=sorted(changed))
    return result


def protected_files(repositories):
    model, frontend = repositories['model'], repositories['frontend']
    paths = [model/name for name in ('singlerun.jar','multirun.jar',
             'input/DatabaseCountryYear.xlsx','input/EUROMODpolicySchedule.xlsx')]
    paths.append(frontend/'deploy/simpaths/models.json')
    return {str(path):digest(path) for path in paths}


def verify_protected(receipt):
    if any(digest(path)!=expected for path, expected in receipt.items()):
        raise RuntimeError('Original scientific JARs, input workbooks or catalogue changed')


def clone_sources(selected, directory, log):
    directory.mkdir(mode=0o700)
    names = dict(core='JAS-mine-core', model='SimPaths', frontend='JAS-mine-web')
    clones = {}
    for name, record in selected.items():
        destination = directory/names[name]
        # Shared Git objects are read from the source repository. Working files,
        # refs and build outputs belong only to the private clone. Never use a
        # worktree, which would write administrative state in the original .git.
        subprocess.run(['git', 'clone', '--shared', '--no-hardlinks', '--no-checkout',
                        '--', record['path'], str(destination)],
                       check=True, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        subprocess.run(['git', '-C', str(destination), '-c', 'core.hooksPath=/dev/null',
                        'checkout', '--detach', record['commit']],
                       check=True, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        if git(destination, 'rev-parse', 'HEAD')!=record['commit'] or git(destination, 'status', '--porcelain'):
            raise RuntimeError('Private clone is not the selected clean commit')
        clones[name] = destination
    return clones


def acceptance_command(python, clones, output, maven, *, prune=False, quickstart=None):
    # Sibling output keeps the existing acceptance lock in the SAME temporary
    # directory as quiet scientific campaigns. Nested acceptance output would
    # acquire a different lock and incorrectly permit overlapping heavy work.
    acceptance = output.with_name(output.name+'-acceptance')
    command = [str(python), str(clones['model']/'deploy/acceptance/run_security_refresh.py'),
        '--frontend', str(clones['frontend']), '--core', str(clones['core']),
        '--maven-repository', str(maven), '--output', str(acceptance)]
    if prune:
        command.append('--prune-build-cache')
    if quickstart:
        command.extend(('--resume-from',str(quickstart)))
    return command, acceptance


def run_owned(command, log, *, environment, cwd=None, timeout=None, guard_space=True):
    space=environment.get('TMPDIR') if guard_space else None
    def check_space():
        if space and shutil.disk_usage(space).free<3*GIB:
            raise RuntimeError('Refresh stopped: less than 3 GiB free; owned child cleanup precedes checkpointing')
    check_space()
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                               env=environment, cwd=cwd, start_new_session=True)
    deadline=time.monotonic()+timeout if timeout is not None else None
    try:
        while True:
            check_space()
            remaining=None if deadline is None else deadline-time.monotonic()
            if remaining is not None and remaining<=0:raise subprocess.TimeoutExpired(command,timeout)
            try:
                code=process.wait(timeout=min(1,remaining) if space and remaining is not None else
                                  1 if space else remaining)
                break
            except subprocess.TimeoutExpired:
                if not space:raise
    except BaseException:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=900)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=30)
        raise
    if code:
        raise subprocess.CalledProcessError(code, command)


def existing_bases(frontend):
    """Reject missing prepared images before copying sources or compiling Java."""
    result = {}
    for population in (20000, 50000):
        reference = quickstart_image(frontend, population)
        try:
            identifier = subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',reference],
                text=True, stderr=subprocess.DEVNULL, timeout=30).strip()
        except subprocess.CalledProcessError as error:
            raise RuntimeError('Prepared Quick Start base is missing: '+reference+
                               '; use --prepare-quickstart to create fresh private bases') from error
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', identifier):
            raise ValueError('Invalid prepared base identity')
        result[str(population)] = dict(tag=reference, image_id=identifier)
    return result


def set_private_bases(frontend, bases):
    """Change only base-image references in the private clone's catalogue."""
    path = frontend/'deploy/simpaths/models.json'
    original = digest(path)
    catalogue = json.loads(path.read_text())
    for population, record in bases.items():
        matches = [model for model in catalogue['models'] if model['id']=='simpaths-quickstart-'+str(population)]
        if len(matches)!=1:
            raise ValueError('Expected one private Quick Start catalogue entry')
        matches[0]['deployment']['image'] = record['tag']
    path.write_text(json.dumps(catalogue,indent=2)+'\n')
    return dict(before_sha256=original, after_sha256=digest(path), bases=bases)


def image_record(client,verifier,tag,runtime,profile,*,before=None,expected=None):
    image=client.images.get(tag)
    if expected and image.id!=expected['image_id']:
        raise ValueError('Retained base image identity changed')
    container=client.containers.create(image.id)
    try:
        inputs=verifier['archive_content_hashes'](container,'/app/input')
        receipt=json.loads(verifier['archive_file'](container,'/app/query-access.json'))
        if before is not None:verifier['verify_query_migration'](before,inputs,receipt)
        hashes={name:verifier['archive_hash'](container,'/app/'+name) for name in
            ('app.jar','profile.json','release.json','query-access.json','webserver.properties',
             'README.md','license.txt','COPYRIGHT.md')}
        if hashes['app.jar']!=runtime or hashes['profile.json']!=profile:
            raise RuntimeError('Fresh base differs from its verified package')
        if expected and (inputs!=expected['input_hashes'] or hashes!=expected['application_hashes']):
            raise ValueError('Retained image contents changed')
    finally:container.remove()
    return dict(tag=tag,image_id=image.id,runtime_jar_sha256=runtime,profile_sha256=profile,
                input_hashes=inputs,application_hashes=hashes,query_access_receipt=receipt)


def completed_export(log,population):
    """Recover a missing checkpoint only from one completed, unambiguous export."""
    if not log.is_file():return None
    text=log.read_text()
    matches=list(re.finditer(r'^(#\d+) naming to docker\.io/library/(simpaths-quickstart:security-base-'
        +str(population)+r'-[0-9-]+) done$',text,re.M))
    if not matches:return None
    if len(matches)!=1:raise ValueError('Ambiguous retained base export')
    step,tag=matches[0].groups()
    if not re.search(r'^'+re.escape(step)+r' DONE(?:\s|$)',text[matches[0].end():],re.M):
        raise ValueError('Retained base export did not finish')
    identifiers=set(re.findall(r'^'+re.escape(step)+r' exporting manifest list (sha256:[a-f0-9]{64})(?:\s|$)',
                               text[:matches[0].start()],re.M))
    if len(identifiers)!=1:raise ValueError('Retained base export identity is incomplete')
    return dict(tag=tag,image_id=identifiers.pop())


def context_hashes(context):
    result={}
    for path in context.rglob('*'):
        if path.is_symlink():raise ValueError('Linked refresh context input')
        if path.is_file():result[str(path.relative_to(context))]=digest(path)
    return result


def prepare_bases(python, clones, output, maven, *, prune=False, resume=False):
    """Checkpoint/reuse private builds; publish one final database layer per image."""
    environment=dict(os.environ,TMPDIR=str(output.parent),JASMINE_WEB_REPO=str(clones['frontend']))
    root=output/'prepared-bases';root.mkdir(mode=0o700,exist_ok=resume)
    reserve=output/RESERVE
    if not resume:
        for name,goal in (('core','install'),('model','package')):
            print('Building private '+name+' JARs; log: '+str(root/(name+'-build.log')),flush=True)
            with (root/(name+'-build.log')).open('x') as log:
                run_owned(['mvn','-o','-Djava.awt.headless=true','-Dmaven.repo.local='+str(maven),goal],
                          log,environment=environment,cwd=clones[name],timeout=3600)
    elif any('BUILD SUCCESS' not in (root/(name+'-build.log')).read_text() for name in ('core','model')):
        raise ValueError('Cannot resume before both private Java builds succeeded')
    runtime=digest(clones['model']/'singlerun.jar')
    build=dict(runtime_jar_sha256=runtime,multirun_sha256=digest(clones['model']/'multirun.jar'))
    if (root/'build-checkpoint.json').exists() and json.loads((root/'build-checkpoint.json').read_text())!=build:
        raise ValueError('Retained Java build artifacts changed')
    checkpoint(root/'build-checkpoint.json',build,reserve=reserve)
    verifier=runpy.run_path(str(clones['model']/'deploy/web-quickstart/build_release.py'))
    template=(clones['model']/'deploy/web-quickstart/Dockerfile').read_text()
    import docker
    client=docker.from_env()
    bases=json.loads((root/'images.json').read_text()) if (root/'images.json').is_file() else {}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    for population in (20000,50000):
        key=str(population);directory=root/key
        if key in bases:
            image_record(client,verifier,bases[key]['tag'],runtime,bases[key]['profile_sha256'],expected=bases[key])
            if (directory/'context').exists():
                receipt=directory/'context-receipt.json'
                if digest(receipt)!=bases[key]['context_receipt_sha256']:
                    raise ValueError('Completed context receipt changed')
                if context_hashes(directory/'context')!=json.loads(receipt.read_text())['files']:
                    raise ValueError('Completed context changed')
                shutil.rmtree(directory/'context')
            retire_copies(directory,package=True)
            continue
        logfile=root/(key+'-resume-'+stamp+'.log' if resume else key+'.log')
        print('Preparing/verifying Quick Start '+key+'; log: '+str(logfile),flush=True)
        with logfile.open('x') as log:
            package=directory/'package';context=directory/'context';context_receipt=directory/'context-receipt.json'
            if not directory.exists():
                if shutil.disk_usage(output.parent).free<12*GIB:
                    raise RuntimeError('Need 12 GiB free before population preparation; completed copies are retired before Docker packaging')
                run_owned([str(python),str(clones['model']/'deploy/web-quickstart/prepare_profile.py'),
                    '--repo',str(clones['model']),'--population',key,'--output',str(directory)],
                    log,environment=environment,timeout=14400)
            if context_receipt.exists():
                info=json.loads(context_receipt.read_text())
                if info['runtime']!=runtime or context_hashes(context)!=info['files']:
                    raise ValueError('Retained context or runtime changed')
                retire_copies(directory,package=True)
            else:
                if context.exists():raise ValueError('Incomplete retained context; inspect it before retrying')
                if json.loads((directory/'status.json').read_text()).get('status')!='complete':
                    raise RuntimeError('Quick Start preparation did not complete')
                profile=json.loads((package/'profile.json').read_text())
                checks=json.loads((package/'checksums.json').read_text())
                if (profile.get('fresh_jvm_loading_verified') is not True or profile.get('jar_sha256')!=runtime
                        or profile.get('profile',{}).get('requested_population')!=population
                        or digest(package/'profile.json')!=checks.get('profile.json')):
                    raise ValueError('Retained preparation does not match this population/runtime')
                before={name:value for name,value in checks.items() if name.startswith('input/') and
                        (name=='input/input.mv.db' or Path(name).suffix.lower() in ('.xls','.xlsx'))}
                if 'input/input.mv.db' not in before or any('..' in Path(name).parts for name in before):
                    raise ValueError('Invalid prepared input paths')
                if any(digest(package/name)!=value for name,value in before.items()):
                    raise ValueError('Prepared package input changed')
                profile_hash=digest(package/'profile.json')
                retire_copies(directory)
                previous=completed_export(root/(key+'.log'),population) if resume else None
                source=None
                if previous:
                    if client.images.get(previous['tag']).id!=previous['image_id']:
                        raise ValueError('Completed export tag changed')
                    source=image_record(client,verifier,previous['tag'],runtime,profile_hash,before=before)
                    checkpoint(directory/'recovered-image.json',source,reserve=reserve)
                    context.mkdir(mode=0o700)
                else:
                    run_owned([str(python),str(clones['model']/'deploy/web-quickstart/package_image.py'),
                        '--repo',str(clones['model']),'--package',str(package),'--output',str(context)],
                        log,environment=environment,timeout=600)
                (context/'Dockerfile').write_text(compact_dockerfile(template,source['tag'] if source else None))
                info=dict(runtime=runtime,profile=profile_hash,before=before,source=source,
                          files=context_hashes(context),tag='simpaths-quickstart:security-base-'+key+'-compact-'+stamp)
                checkpoint(context_receipt,info,reserve=reserve)
                # Only verified, privately owned context/image bytes are now needed.
                retire_copies(directory,package=True)
            needed=required_build_space(context)
            if shutil.disk_usage(output.parent).free<needed:
                raise RuntimeError('Need '+format(needed/GIB,'.2f')+' GiB free for image stages/export; reusable context retained')
            failure=False
            try:
                run_owned(['docker','build','-t',info['tag'],str(context)],log,environment=environment,timeout=3600)
            except BaseException:
                failure=True;raise
            finally:
                if prune:
                    try:run_owned(['docker','builder','prune','--all','--force'],log,
                                  environment=environment,timeout=600,guard_space=False)
                    except Exception:
                        if not failure:raise
            record=image_record(client,verifier,info['tag'],runtime,info['profile'],
                before=None if info['source'] else info['before'])
            if info['source'] and (record['input_hashes']!=info['source']['input_hashes'] or
                                  record['application_hashes']!=info['source']['application_hashes']):
                raise ValueError('Compaction changed application or prepared data bytes')
            record['context_receipt_sha256']=digest(context_receipt)
            bases[key]=record
            checkpoint(root/'images.json',bases,reserve=reserve)
            shutil.rmtree(context)
            if info['source']:
                if client.images.get(info['source']['tag']).id!=info['source']['image_id']:
                    raise ValueError('Intermediate source tag changed; it was not removed')
                # Only this invocation's verified, superseded candidate tag.
                client.images.remove(info['source']['tag'])
    return bases


def qualify_bases(python,clones,output,bases):
    """Run actual release/browser checks on fresh images without repackaging DBs."""
    verifier=runpy.run_path(str(clones['model']/'deploy/web-quickstart/build_release.py'))
    sources={key:verifier['source_inventory'](clones[name],include_inputs=True)
             for name,key in (('core','core'),('model','simpaths'),('frontend','frontend'))}
    directory=output.with_name(output.name+'-quickstart')
    quick=directory/'quickstart'
    reserve=output/RESERVE
    if directory.exists():
        if json.loads((directory/'source-inventory.json').read_text())!=sources:
            raise ValueError('Quick Start qualification sources changed')
    else:
        directory.mkdir(mode=0o700);quick.mkdir(mode=0o700)
        (directory/'COPYRIGHT.md').write_text(NOTICE)
        checkpoint(directory/'source-inventory.json',sources,reserve=reserve)
    runtime=digest(clones['model']/'singlerun.jar')
    record=dict(passed=False,promoted=False,runtime_jar_sha256=runtime,
        source_inventory_sha256=digest(directory/'source-inventory.json'),stages=[])
    checkpoint(directory/'report.json',record,reserve=reserve)
    status=dict(status='running',images=[])
    for population in (20000,50000):
        base=bases[str(population)]
        item=dict(base,population=population,status='packaged')
        status['images'].append(item)
        browser=quick/('browser-'+str(population))
        def passed():
            try:report=json.loads((browser/'report.json').read_text())
            except (OSError,ValueError):return False
            private=report.get('private_rpc_evidence',{})
            return (report.get('status')=='passed' and report.get('cleanup') is True
                and report.get('image_id')==base['image_id'] and private.get('closed') is True
                and private.get('errors')==[])
        if browser.exists() and not passed():
            browser.rename(browser.with_name(browser.name+'-failed-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')))
        if not browser.exists():
            if shutil.disk_usage(output.parent).free<8*GIB:
                raise RuntimeError('Need 8 GiB free for real Quick Start browser/session checks')
            print('Checking compact Quick Start '+str(population)+' through private RPC',flush=True)
            with (quick/('browser-'+str(population)+'-'+str(time.time_ns())+'.log')).open('x') as log:
                run_owned([str(python),str(clones['model']/'deploy/acceptance/run_browser_acceptance.py'),
                    '--frontend',str(clones['frontend']),'--image',base['tag'],'--population',str(population),
                    '--detailed-access','--deployment-profile','--storage-check','--private-rpc','--output',str(browser)],
                    log,environment=dict(os.environ,TMPDIR=str(output.parent),
                                         JASMINE_WEB_REPO=str(clones['frontend'])),timeout=10800)
        if not passed():raise RuntimeError('Quick Start browser identity or cleanup is incomplete')
        item['status']='validated'
        checkpoint(quick/'status.json',status,reserve=reserve)
    current={key:verifier['source_inventory'](clones[name],include_inputs=True)
             for name,key in (('core','core'),('model','simpaths'),('frontend','frontend'))}
    if current!=sources:raise RuntimeError('Sources changed during Quick Start qualification')
    status['status']='passed'
    checkpoint(quick/'status.json',status,reserve=reserve)
    record.update(passed=True,stages=[dict(name='quickstart',passed=True)],quickstart=status)
    checkpoint(directory/'report.json',record,reserve=reserve)
    return directory


def resume_clones(selected,output):
    names=dict(core='JAS-mine-core',model='SimPaths',frontend='JAS-mine-web')
    result={name:output/'sources'/directory for name,directory in names.items()}
    for name,path in result.items():
        if path.is_symlink() or git(path,'rev-parse','HEAD')!=selected[name]['commit']:
            raise ValueError('Recovery clones must match the selected original commits')
        changed=set(git(path,'diff','--name-only','HEAD').splitlines())
        allowed={'deploy/simpaths/models.json'} if name=='frontend' else set()
        if changed-allowed or git(path,'ls-files','--others','--exclude-standard'):
            raise ValueError('Recovery source files changed')
        if changed:
            original=json.loads(git(path,'show','HEAD:deploy/simpaths/models.json'))
            current=json.loads((path/'deploy/simpaths/models.json').read_text())
            for model in current['models']:
                if model['id'] in ('simpaths-quickstart-20000','simpaths-quickstart-50000'):
                    match=next(item for item in original['models'] if item['id']==model['id'])
                    model['deployment']['image']=match['deployment']['image']
            if current!=original:raise ValueError('Only private Quick Start base references may differ')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=frontend_path())
    parser.add_argument('--core', type=Path, default=ROOT.parent/'JAS-mine-core')
    parser.add_argument('--maven-repository', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-quickstart', action='store_true',
                        help='Build fresh 20k/50k public-training bases in the private clones when old bases are unavailable')
    parser.add_argument('--resume-preparation',action='store_true',
                        help='Resume this private output, revalidating clones, environment, contexts and completed images')
    parser.add_argument('--prune-build-cache', action='store_true',
                        help='Explicitly allow the existing interactive builder to prune unused shared build cache')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    repositories = dict(core=args.core.expanduser().resolve(strict=True), model=ROOT,
                        frontend=args.frontend.expanduser().resolve(strict=True))
    maven = args.maven_repository.expanduser().resolve(strict=True)
    output = args.output.expanduser().absolute()
    if output.parent.resolve()!=Path(tempfile.gettempdir()).resolve():
        parser.error('Put --output directly inside TMPDIR so quiet trials share the same heavy-work lock')
    if (output.is_symlink() or output.with_name(output.name+'-acceptance').exists()
            or (output.exists() and not args.resume_preparation)):
        parser.error('Choose a new output and acceptance directory')
    if args.resume_preparation and (not args.prepare_quickstart or not output.is_dir()
            or output.stat().st_uid!=os.getuid() or output.stat().st_mode&0o077):
        parser.error('Preparation recovery requires --prepare-quickstart and its existing private --output directory')
    selected = selected_sources(repositories)
    if args.dry_run:
        print(json.dumps(dict(sources=selected, minimum_initial_free_gib=3 if args.resume_preparation else 14,
            source_policy='committed files only; existing uncommitted workbooks are preserved',
            output=str(output), acceptance=str(output.with_name(output.name+'-acceptance')),
            prepare_quickstart=args.prepare_quickstart,
            resume_preparation=args.resume_preparation,
            original_jars_overwritten=False, promoted=False), indent=2))
        return 0
    subprocess.run(['docker','info','--format','{{.ServerVersion}}'], check=True, timeout=30,
                   stdout=subprocess.DEVNULL)
    bases = None if args.prepare_quickstart else existing_bases(repositories['frontend'])
    initial=3 if args.resume_preparation else 14
    if shutil.disk_usage(output.parent).free<initial*GIB:
        raise RuntimeError('Need '+str(initial)+' GiB free initially; per-stage preparation/build/browser guards also apply')
    os.umask(0o077)
    originals = protected_files(repositories)
    report = dict(format='simpaths.isolated-security-refresh.v1', passed=False, promoted=False,
                  started_at=datetime.now(timezone.utc).isoformat(), sources=selected,
                  protected=originals, driver_sha256=digest(Path(__file__)),maven_repository=str(maven),
                  storage_helper_sha256=digest(Path(__file__).with_name('isolated_refresh_storage.py')),
                  resumed_preparation=args.resume_preparation)
    def save(path,value):checkpoint(path,value,reserve=output/RESERVE)
    with exclusive_trials(output.parent):
        output.mkdir(mode=0o700,exist_ok=args.resume_preparation)
        reserve_reports(output)
        if args.resume_preparation and (output/'report.json').exists():
            previous=(output/'report.json').read_bytes()
            if previous and json.loads(previous).get('passed') is True:
                raise ValueError('Do not overwrite a passed isolated qualification')
            (output/'report.json').rename(output/('report-before-recovery-'+str(time.time_ns())+'.json'))
        if args.resume_preparation and (output/'space-recovery.json').exists():
            report['earlier_report_lost_to_disk_full']=True
            report['preservation_scope']='Verified from start of this recovery; original failed report was truncated'
        (output/'COPYRIGHT.md').write_text(NOTICE)
        try:
            if args.resume_preparation:
                clones=resume_clones(selected,output)
            else:
                with (output/'source-clones.log').open('x') as log:
                    clones = clone_sources(selected, output/'sources', log)
            report['clones']={name:str(path) for name,path in clones.items()}
            save(output/'report.json',report)
            spec = importlib.util.spec_from_file_location('candidate_python_environment',
                clones['frontend']/'scripts/python_environment.py')
            helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
            requirements = [clones['frontend']/name for name in
                ('requirements-vm-migration.txt','requirements-cloudrun.txt','tests/browser/requirements-batch.txt')]
            requirements.append(clones['model']/'deploy/multirun/requirements.txt')
            if args.resume_preparation:
                python=output/'venv/bin/python'
                packages=json.loads((output/'python-packages.json').read_text())
                current=helper.inventory(python)
                if any(current[key]!=packages[key] for key in ('prefix','python','packages')):
                    raise ValueError('Recovery Python environment changed')
                if packages.get('installer',{}).get('sha256')!=digest(clones['frontend']/'requirements-installer.txt'):
                    raise ValueError('Recovery installer receipt changed')
            else:
                with (output/'dependencies.log').open('x') as log:
                    python, packages = helper.create_environment(output/'venv', requirements, log, root=clones['frontend'])
            versions = {p['name'].lower():p['version'] for p in packages['packages']}
            if versions.get('pip')!=helper.installer_version(clones['frontend']) or versions.get('oauthlib')!='4.0.0':
                raise RuntimeError('Candidate Python environment differs from the qualified versions')
            if not args.resume_preparation:save(output/'python-packages.json',packages)
            report['python_packages_sha256']=digest(output/'python-packages.json')
            save(output/'report.json',report)
            quickstart=None
            if args.prepare_quickstart:
                bases=prepare_bases(python,clones,output,maven,prune=args.prune_build_cache,resume=args.resume_preparation)
                report['private_catalogue_override']=set_private_bases(clones['frontend'],bases)
                report['prepared_bases']=bases
                save(output/'report.json',report)
                quickstart=qualify_bases(python,clones,output,bases)
                report['quickstart_qualification']=str(quickstart)
            else:
                # The legacy driver hashes its starting JAR before compiling.
                # Seed that private path only; Maven then replaces it locally.
                for name in ('singlerun.jar','multirun.jar'):
                    shutil.copyfile(repositories['model']/name,clones['model']/name)
            report['prepared_bases']=bases
            command, acceptance = acceptance_command(python, clones, output, maven,
                prune=args.prune_build_cache,quickstart=quickstart)
            report.update(command=command,acceptance=str(acceptance))
            verify_protected(originals)
        except BaseException as error:
            report['error_type']=type(error).__name__
            if isinstance(error,(ValueError,RuntimeError,OSError)):report['error']=str(error)
            try:
                verify_protected(originals)
                report['originals_unchanged']=True
            except Exception:
                report['originals_unchanged']=False
            report['finished_at']=datetime.now(timezone.utc).isoformat()
            save(output/'report.json',report)
            print('STOPPED: '+type(error).__name__+'; '+report.get('error','inspect private preparation logs')+'. Evidence retained.',flush=True)
            print('FAILED: '+str(output/'report.json'),flush=True)
            return 1
        save(output/'report.json',report)
    # The existing acceptance now acquires this same lock itself. If another
    # campaign starts in the small transition gap it refuses to run, never overlaps.
    print('Running isolated image/browser qualification; log: '+str(output/'acceptance.log'), flush=True)
    try:
        with (output/'acceptance.log').open('x') as log:
            run_owned(command, log, environment=dict(os.environ,TMPDIR=str(output.parent),
                                                     JASMINE_WEB_REPO=str(clones['frontend'])))
        native = json.loads((acceptance/'report.json').read_text())
        if native.get('passed') is not True or native.get('promoted') is not False:
            raise RuntimeError('Candidate image/browser acceptance is incomplete')
        report['acceptance_sha256']=digest(acceptance/'report.json')
        report['passed']=True
    except (Exception, KeyboardInterrupt) as error:
        report['error_type']=type(error).__name__
        print('STOPPED: '+type(error).__name__+'; private clones, images and evidence retained.',flush=True)
    finally:
        try:
            verify_protected(originals)
            report['originals_unchanged']=True
        except Exception:
            report['originals_unchanged']=False;report['passed']=False
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        save(output/'report.json',report)
    print(('PASSED: ' if report['passed'] else 'FAILED: ')+str(output/'report.json'),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    def interrupted(signum,frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    raise SystemExit(main())
