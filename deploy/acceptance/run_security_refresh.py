#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Build isolated security-update candidates and run SingleRun private-boundary acceptance.
Records exact sources, runtime hashes and image identities; never promotes a catalogue.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import signal
import stat

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy._workflow import frontend_path

RESUME_TOOLING=frozenset({
    'deploy/acceptance/run_security_refresh.py',
    'deploy/acceptance/run_interactive_rpc_acceptance.py',
    'deploy/acceptance/test_single_rpc_rehearsal.py',
    'deploy/MAINTAINER_GUIDE.md',
})


def stages(frontend,core,output,work,maven_repository,stamp,*,resume=False,prune_build_cache=False,
           training_image=None,user_data_image=None):
    prefix=[sys.executable]
    quick=output/'quickstart'
    candidates=['simpaths-interactive:uk-'+name+'-'+stamp for name in ('training','user-data')]
    if training_image:candidates[0]=training_image
    if user_data_image:candidates[1]=user_data_image
    commands=[
        ('quickstart',prefix+[str(ROOT/'deploy/web-quickstart/build_release.py'),
            '--frontend',str(frontend),'--core',str(core),'--work-root',str(work),
            '--output',str(quick),'--candidate-worktree','--private-rpc','--offline',
            '--maven-repository',str(maven_repository)]),
    ]
    for profile in ('training','user-data'):
        if profile=='training' and training_image:continue
        if profile=='user-data' and user_data_image:continue
        commands.append(('interactive-'+profile+'-image',prefix+[str(ROOT/'deploy/build_local_images.py'),
            '--frontend',str(frontend),'--work-root',str(work),'--docker-storage-path','/',
            '--profile',profile,'--tag-suffix',stamp,
            *(['--prune-build-cache'] if prune_build_cache else [])]))
    commands.append(('interactive-browser',prefix+[str(ROOT/'deploy/acceptance/run_interactive_rpc_acceptance.py'),
            '--frontend',str(frontend),'--training-image',candidates[0],'--user-data-image',candidates[1],
            *(['--profile','user-data'] if user_data_image else []),
            '--output',str(output/'interactive')]))
    return commands[1:] if resume else commands,candidates


def retained_sources(directory,sources,release):
    """Validate the original receipt before reusing any model build or acceptance."""
    directory=Path(directory)
    old_report=json.loads((directory/'report.json').read_text())
    inventory_path=directory/'source-inventory.json'
    if release['digest'](inventory_path)!=old_report.get('source_inventory_sha256'):
        raise ValueError('Retained source inventory checksum does not match its receipt')
    previous=json.loads(inventory_path.read_text())
    if set(previous)!=set(sources):
        raise ValueError('Retained source inventory has different repositories')
    changes=[]
    for name,current in sources.items():
        before=previous[name]['files'];after=current['files']
        changed={path for path in set(before)|set(after) if before.get(path)!=after.get(path)}
        allowed=RESUME_TOOLING if name=='simpaths' else frozenset()
        if changed-allowed:
            raise ValueError('Deployed sources or inputs changed: '+name+': '+', '.join(sorted(changed-allowed)))
        changes.extend(dict(repository=name,path=path,before=before.get(path),after=after.get(path))
                       for path in sorted(changed))
    return old_report,changes


def reused_quickstart(directory,sources,client,release,runtime):
    """Reuse complete model/browser evidence only for unchanged deployed sources."""
    directory=Path(directory)
    old_report,changes=retained_sources(directory,sources,release)
    if not any(stage.get('name')=='quickstart' and stage.get('passed') is True
               for stage in old_report.get('stages',[])):
        raise ValueError('Retained workflow did not complete Quick Start acceptance')
    path=directory/'quickstart/status.json'
    quick=json.loads(path.read_text())
    records=quick.get('images',[])
    if (quick.get('status')!='passed' or len(records)!=2
            or {record.get('population') for record in records}!={20000,50000}):
        raise ValueError('Both retained Quick Start profiles must have passed')
    receipt=dict(directory=str(directory),status_sha256=release['digest'](path),
                 source_inventory_sha256=old_report['source_inventory_sha256'],
                 tooling_changes=changes,browser_reports={})
    for record in records:
        if record.get('status')!='validated' or record.get('runtime_jar_sha256')!=runtime:
            raise ValueError('Retained Quick Start runtime does not match the current JAR')
        browser_path=directory/'quickstart'/('browser-'+str(record['population']))/'report.json'
        browser=json.loads(browser_path.read_text())
        private=browser.get('private_rpc_evidence',{})
        if (browser.get('status')!='passed' or browser.get('cleanup') is not True
                or browser.get('image_id')!=record['image_id']
                or private.get('closed') is not True or private.get('errors')!=[]):
            raise ValueError('Retained browser identity or private cleanup is unconfirmed')
        image=client.images.get(record['tag'])
        if image.id!=record['image_id']:
            raise ValueError('Retained Quick Start image tag changed')
        container=client.containers.create(image.id)
        try:
            if release['archive_hash'](container,'/app/app.jar')!=runtime:
                raise ValueError('Retained image contains a different runtime')
        finally:container.remove()
        receipt['browser_reports'][str(record['population'])]=release['digest'](browser_path)
    return quick,receipt


def training_build_record(directory):
    """Read a build checkpoint, or a completed export from an older combined build."""
    directory=Path(directory)
    report=json.loads((directory/'report.json').read_text())
    checkpoint=report.get('interactive_builds',{}).get('training')
    if checkpoint:
        if not any(stage.get('name')=='interactive-training-image' and stage.get('passed') is True
                   for stage in report.get('stages',[])):
            raise ValueError('Retained training build checkpoint has no completed stage')
        return checkpoint
    stages=[stage for stage in report.get('stages',[]) if stage.get('name')=='interactive-images']
    if len(stages)!=1:
        raise ValueError('No retained training image build was found')
    command=stages[0]['command']
    if command.count('--tag-suffix')!=1:
        raise ValueError('Retained training build must use a unique candidate suffix')
    suffix=command[command.index('--tag-suffix')+1]
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,64}',suffix):
        raise ValueError('Invalid retained training image suffix')
    tag='simpaths-interactive:uk-training-'+suffix
    log=(directory/'interactive-images.log').read_text().split('\nPreparing to build user-data',1)[0]
    # A failed second build must not turn the successfully exported first image
    # into a failed build. Require its own export identity and completed step.
    named=list(re.finditer(r'^(#\d+) naming to docker\.io/library/'+re.escape(tag)+r' done$',log,re.M))
    if len(named)!=1:
        raise ValueError('Retained training image export is incomplete or ambiguous')
    step=re.escape(named[0].group(1))
    if not re.search(r'^'+step+r' DONE(?:\s|$)',log[named[0].end():],re.M):
        raise ValueError('Retained training image export did not finish')
    manifests=set(re.findall(r'^'+step+r' exporting manifest list (sha256:[a-f0-9]{64})(?:\s|$)',
                             log[:named[0].start()],re.M))
    if len(manifests)!=1:
        raise ValueError('Retained training export has no unambiguous image identity')
    return dict(tag=tag,image_id=manifests.pop(),runtime_jar_sha256=report['runtime_jar_sha256'],
                build_log='interactive-images.log')


def interactive_image_record(tag,client,release,runtime,*,expected_id=None):
    image=client.images.get(tag)
    if expected_id is not None and image.id!=expected_id:
        raise ValueError('Retained interactive image tag changed')
    container=client.containers.create(image.id)
    try:
        if release['archive_hash'](container,'/app/app.jar')!=runtime:
            raise ValueError('Interactive image contains a different runtime')
        inputs=release['archive_content_hashes'](container,'/app/input')
    finally:container.remove()
    return dict(tag=tag,image_id=image.id,runtime_jar_sha256=runtime,input_hashes=inputs)


def reused_training(directory,sources,client,release,runtime):
    """Reuse a built image; its browser checks must still run in this continuation."""
    directory=Path(directory)
    report,changes=retained_sources(directory,sources,release)
    original=training_build_record(directory)
    if original.get('runtime_jar_sha256')!=runtime or report.get('runtime_jar_sha256')!=runtime:
        raise ValueError('Retained training runtime does not match the current JAR')
    record=interactive_image_record(original['tag'],client,release,runtime,expected_id=original['image_id'])
    if 'input_hashes' in original and record['input_hashes']!=original['input_hashes']:
        raise ValueError('Retained training input hashes changed')
    receipt=dict(directory=str(directory),report_sha256=release['digest'](directory/'report.json'),
                 source_inventory_sha256=report['source_inventory_sha256'],tooling_changes=changes,
                 browser_acceptance_reused=False)
    if original.get('build_log'):
        receipt['build_log_sha256']=release['digest'](directory/original['build_log'])
    return record,receipt


TRAINING_CHECKS=tuple('training-'+mode for mode in ('none','population','tax','both'))


def interactive_resume_record(directory):
    """A completed pair of image records is required before a browser-only retry."""
    report=json.loads((Path(directory)/'report.json').read_text())
    records=report.get('interactive_builds',{})
    if set(records)!={'training','user-data'}:
        raise ValueError('Browser resume requires both retained interactive image records')
    for profile in records:
        built=any(stage.get('name')=='interactive-'+profile+'-image' and stage.get('passed') is True
                  for stage in report.get('stages',[]))
        if not built and not (profile=='training' and report.get('reused_training')):
            raise ValueError('Retained interactive image has no completed build or reuse receipt')
    return records


def reused_interactive(directory,sources,client,release,runtime):
    """Verify four completed training checks, then repeat only user-data acceptance."""
    directory=Path(directory)
    report,changes=retained_sources(directory,sources,release)
    records=interactive_resume_record(directory)
    if report.get('runtime_jar_sha256')!=runtime:
        raise ValueError('Retained interactive runtime differs from the current JAR')
    path=directory/'interactive/report.json'
    previous=json.loads(path.read_text())
    private=previous.get('private_rpc',{})
    if (previous.get('cleanup') is not True or private.get('closed') is not True
            or private.get('errors')!=[]):
        raise ValueError('Retained interactive cleanup or private shutdown is unconfirmed')
    for profile,record in records.items():
        ids=[value for name,value in previous.get('images',{}).items() if name.endswith('-'+profile)]
        if ids!=[record['image_id']] or record.get('runtime_jar_sha256')!=runtime:
            raise ValueError('Retained browser profile image identity is inconsistent')
    checks=previous.get('checks',[])
    training=[]
    for name in TRAINING_CHECKS:
        matching=[check for check in checks if check.get('name')==name and check.get('passed') is True]
        if len(matching)!=1:
            raise ValueError('All four retained training modes must have passed')
        browser_path=directory/'interactive'/name/'report.json'
        browser=json.loads(browser_path.read_text())
        if (browser.get('status')!='passed' or browser.get('prepare')!=name.removeprefix('training-')
                or browser.get('page_errors')
                or any(browser.get(key) for key in ('cleanup_error','artifact_errors','browser_close_error','report_write_error'))):
            raise ValueError('Retained training browser evidence is incomplete')
        training.append(dict(name=name,report=str(browser_path),sha256=release['digest'](browser_path),
                             passed=True,reused=True))
    verified={}
    for profile,record in records.items():
        current=interactive_image_record(record['tag'],client,release,runtime,expected_id=record['image_id'])
        if not record.get('input_hashes') or current['input_hashes']!=record['input_hashes']:
            raise ValueError('Retained interactive input hashes changed or are missing')
        verified[profile]=current
    receipt=dict(directory=str(directory),report_sha256=release['digest'](directory/'report.json'),
                 interactive_report_sha256=release['digest'](path),
                 source_inventory_sha256=report['source_inventory_sha256'],tooling_changes=changes,
                 training_checks=training,user_data_acceptance_reused=False)
    return verified,receipt


def require_browser_space(output,minimum_gib=8):
    """Preparation needs working space after image builds have consumed disk space."""
    for path in (Path(output).parent,Path('/')):
        free=shutil.disk_usage(path).free/1024**3
        if free<minimum_gib:
            raise RuntimeError(f'Browser preparation needs at least {minimum_gib:g} GiB free before launch; '
                               f'{path} has {free:.2f} GiB. No interactive acceptance models were started.')


@contextmanager
def exclusive_trials(directory):
    """Use the quiet benchmark's private lock; refuse overlapping heavy work."""
    fd=os.open(directory/'.simpaths-research-sequence.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
            raise ValueError('Expected a private benchmark lock owned by the current user')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:os.close(fd)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--core',type=Path,default=ROOT.parent/'JAS-mine-core')
    parser.add_argument('--maven-repository',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume-from',type=Path,
                        help='Reuse passed Quick Start checks; verify original sources, runtime and image IDs')
    parser.add_argument('--reuse-training-from',type=Path,
                        help='With --resume-from, reuse a completed training image from a retained build')
    parser.add_argument('--resume-interactive-from',type=Path,
                        help='With --resume-from, verify both images and four passed training modes; rerun user-data only')
    parser.add_argument('--prune-build-cache',action='store_true',
                        help='Explicitly clear unused shared Docker build cache before/after interactive image builds')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    if (args.reuse_training_from or args.resume_interactive_from) and not args.resume_from:
        parser.error('Interactive reuse requires --resume-from')
    if args.reuse_training_from and args.resume_interactive_from:
        parser.error('Choose --reuse-training-from or --resume-interactive-from')
    for name in ('frontend','core','maven_repository'):
        setattr(args,name,getattr(args,name).expanduser().resolve(strict=True))
    args.output=args.output.expanduser().absolute()
    if args.resume_from:
        args.resume_from=args.resume_from.expanduser().resolve(strict=True)
    if args.reuse_training_from:
        args.reuse_training_from=args.reuse_training_from.expanduser().resolve(strict=True)
    training_image=training_build_record(args.reuse_training_from)['tag'] if args.reuse_training_from else None
    user_data_image=None
    if args.resume_interactive_from:
        args.resume_interactive_from=args.resume_interactive_from.expanduser().resolve(strict=True)
        records=interactive_resume_record(args.resume_interactive_from)
        training_image=records['training']['tag'];user_data_image=records['user-data']['tag']
    stamp='security-'+datetime.now().strftime('%Y%m%d-%H%M%S')
    work=args.output.parent/'security-image-contexts'
    commands,candidates=stages(args.frontend,args.core,args.output,work,args.maven_repository,stamp,
                              resume=bool(args.resume_from),prune_build_cache=args.prune_build_cache,
                              training_image=training_image,user_data_image=user_data_image)
    if args.dry_run:
        print(json.dumps(dict(stages=commands,candidates=candidates,promote=False,
                              resume_from=str(args.resume_from) if args.resume_from else None,
                              reuse_training_from=str(args.reuse_training_from) if args.reuse_training_from else None,
                              resume_interactive_from=str(args.resume_interactive_from) if args.resume_interactive_from else None),indent=2));return 0
    import docker
    client=docker.from_env();client.ping()
    exclusion=exclusive_trials(args.output.parent)
    exclusion.__enter__()
    report=dict(passed=False,promoted=False,started=datetime.now(timezone.utc).isoformat(),stages=[])
    os.umask(0o077)
    try:
        args.output.mkdir(parents=True,mode=0o700,exist_ok=False)
    except BaseException:
        exclusion.__exit__(*sys.exc_info());raise
    try:
        (args.output/'COPYRIGHT.md').write_text(
            '<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Security refresh manifests, source inventories and browser evidence.\n'
            '@author ross richardson\n-->\n\nThird-party logs retain their original notices.\n')
        import runpy
        release=runpy.run_path(str(ROOT/'deploy/web-quickstart/build_release.py'))
        repositories=dict(core=args.core,simpaths=ROOT,frontend=args.frontend)
        def inventory():
            return {name:release['source_inventory'](repo,include_inputs=True) for name,repo in repositories.items()}
        sources=inventory()
        (args.output/'source-inventory.json').write_text(json.dumps(sources,indent=2)+'\n')
        with (args.output/'source-inventory.json').open('rb') as stream:
            report['source_inventory_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
        with (ROOT/'singlerun.jar').open('rb') as stream:
            runtime=hashlib.file_digest(stream,'sha256').hexdigest()
        report['runtime_jar_sha256']=runtime
        if args.resume_from:
            report['quickstart'],report['reused_quickstart']=reused_quickstart(
                args.resume_from,sources,client,release,runtime)
            print('Reusing verified Quick Start images and passed browser checks; no Java build or Quick Start model reruns.',flush=True)
        report['interactive_builds']={}
        if args.reuse_training_from:
            report['interactive_builds']['training'],report['reused_training']=reused_training(
                args.reuse_training_from,sources,client,release,runtime)
            print('Reusing verified training image; its browser checks will still run.',flush=True)
        if args.resume_interactive_from:
            report['interactive_builds'],report['reused_interactive']=reused_interactive(
                args.resume_interactive_from,sources,client,release,runtime)
            print('Reusing both verified interactive images and four passed training modes; rerunning user-data only.',flush=True)
        for name,command in commands:
            if inventory()!=sources:
                raise RuntimeError('Source files changed during security refresh')
            if name=='interactive-browser':require_browser_space(args.output)
            print('Starting '+name+'; log: '+str(args.output/(name+'.log')),flush=True)
            detail=dict(name=name,passed=False,command=command)
            report['stages'].append(detail)
            with (args.output/(name+'.log')).open('w') as log:
                process=subprocess.Popen(command,cwd=ROOT,env=dict(os.environ,TMPDIR=str(args.output.parent)),
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
                try:
                    for line in process.stdout:
                        log.write(line);log.flush();print(line,end='',flush=True)
                    if process.wait():raise RuntimeError(name+' failed; inspect its log')
                except BaseException:
                    if process.poll() is None:
                        # Only this owned stage has a new process group. Let its
                        # Python fixtures run their cleanup before escalation.
                        os.killpg(process.pid,signal.SIGINT)
                        try:process.wait(timeout=720)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid,signal.SIGKILL);process.wait()
                    raise
                finally:process.stdout.close()
            detail['passed']=True
            if name=='quickstart':
                runtime=release['digest'](ROOT/'singlerun.jar')
                report['runtime_jar_sha256']=runtime
            elif name in ('interactive-training-image','interactive-user-data-image'):
                profile='training' if name=='interactive-training-image' else 'user-data'
                tag=candidates[0 if profile=='training' else 1]
                report['interactive_builds'][profile]=interactive_image_record(tag,client,release,runtime)
            (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        if inventory()!=sources:
            raise RuntimeError('Source files changed during security acceptance')
        if not args.resume_from:
            report['quickstart']=json.loads((args.output/'quickstart/status.json').read_text())
        report['interactive']=json.loads((args.output/'interactive/report.json').read_text())
        if report['quickstart']['status']!='passed' or not report['interactive']['passed'] or not report['interactive']['cleanup']:
            raise RuntimeError('Image/browser evidence is incomplete')
        checks=report.get('reused_interactive',{}).get('training_checks',[])+report['interactive'].get('checks',[])
        if (len(checks)!=5 or {check.get('name') for check in checks}!=set(TRAINING_CHECKS)|{'user-data'}
                or any(check.get('passed') is not True for check in checks)):
            raise RuntimeError('All four training modes and the user-data browser workflow must pass')
        report['acceptance_checks']=checks
        with (ROOT/'singlerun.jar').open('rb') as stream:
            runtime=hashlib.file_digest(stream,'sha256').hexdigest()
        if args.resume_from and runtime!=report['runtime_jar_sha256']:
            raise RuntimeError('Runtime JAR changed during resumed acceptance')
        report['runtime_jar_sha256']=runtime
        report['interactive_image_ids']={}
        for tag in candidates:
            record=next(record for record in report['interactive_builds'].values() if record['tag']==tag)
            if client.images.get(tag).id!=record['image_id'] or record['runtime_jar_sha256']!=runtime:
                raise RuntimeError('Interactive image or runtime changed during acceptance')
            report.setdefault('interactive_input_hashes',{})[tag]=record['input_hashes']
            report['interactive_image_ids'][tag]=record['image_id']
        if set(report['interactive']['images'].values())!=set(report['interactive_image_ids'].values()):
            raise RuntimeError('Browser acceptance used different interactive image IDs')
        report['passed']=True
    except (Exception,KeyboardInterrupt) as error:
        report['error_type']=type(error).__name__
        if isinstance(error,(ValueError,RuntimeError)):
            report['error']=str(error)
        print('STOPPED: '+type(error).__name__+'; candidate images and evidence retained.',file=sys.stderr)
    finally:
        exclusion.__exit__(None,None,None)
        report['finished']=datetime.now(timezone.utc).isoformat()
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(('PASSED: ' if report['passed'] else 'FAILED: ')+str(args.output/'report.json'),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    def interrupted(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    raise SystemExit(main())
