"""(C) Copyright 2026, by Ross Richardson

Loopback MultiRun preview with persistent PostgreSQL, private artifacts and worker.
Explicit console-code mode is a local test facility, not production email proof.
The existing SingleRun frontend and Redis store are not imported or changed.

@author ross richardson
"""
import argparse
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time

from deploy._workflow import frontend_path
from .artifacts import ArtifactError, fingerprint
from .browser_model import BrowserModel
from .prepared_dataset import FORMAT, verify_snapshot
from .queue_adapter import read_prepared
from .submission_adapter import DispatchAdapter, PreparationAdapter

ROOT = Path(__file__).resolve().parents[2]


def register_training(datasets, preparations, path):
    """Trusted local import of public training only, without recopying inputs."""
    receipt = read_prepared(path)
    if receipt['identity']['format'] != FORMAT:
        raise ArtifactError('Use a verified prepared Quick Start training directory')
    verify_snapshot(path,receipt)
    key = receipt['revision']
    q = datasets.queue
    with q._transaction() as (c,_,_):
        previous=c.execute('SELECT * FROM datasets WHERE pool_id=%s AND id=%s',(q.pool_id,key)).fetchone()
        if previous and previous['prepared_fingerprint']!=receipt['sha256']:
            raise ArtifactError('Registered training data differs')
        if not previous:
            # Public example revision is intentionally shared; private uploaded
            # data continues to use opaque owner-specific registry identifiers.
            datasets._insert(c,key,receipt['sha256'],None,[['provider',key]])
    preparations.register_location(key,location=str(path),image=receipt['identity']['source_image'])
    return key


def frozen_release(state, image):
    """Freeze the model JAR and parameter workbooks once for this local service."""
    from jasmine_web.batch.local_executor import atomic_json
    release=state/'release'
    config=release/'release.json'
    if not config.exists():
        staging=state/'release.pending'
        if staging.exists():
            raise ArtifactError('Incomplete local release snapshot; inspect release.pending before restarting')
        staging.mkdir(mode=0o700)
        (staging/'defaults').mkdir(mode=0o700)
        model=fingerprint(ROOT/'multirun.jar',staging/'model.jar')
        defaults={}
        for file in (ROOT/'input').glob('*.xls*'):
            if file.name in ('DatabaseCountryYear.xlsx','EUROMODpolicySchedule.xlsx'):
                continue
            defaults[file.name]=fingerprint(file,staging/'defaults'/file.name)
        if not defaults:
            raise ArtifactError('No parameter workbooks found')
        atomic_json(staging/'release.json',dict(image=image,model=model,defaults=defaults))
        staging.rename(release)
    info=json.loads(config.read_text())
    if fingerprint(release/'model.jar')!=info['model']:
        raise ArtifactError('Frozen local model changed')
    for name,expected in info['defaults'].items():
        if fingerprint(release/'defaults'/name)!=expected:
            raise ArtifactError('Frozen parameter workbook changed')
    return {'local-'+info['model']['sha256'][:16]:dict(image=info['image'],jar=release/'model.jar',defaults=release/'defaults')}


def retire_finished(queue, executor):
    """Release finished container/input copies, retain scientific outputs and logs.

    Finished database state and confirmed container removal are prerequisites.
    Safe to repeat after interruption. Does not implement results retention.
    """
    from jasmine_web.batch.local_executor import atomic_json
    with queue._connection() as c:
        rows=c.execute('''SELECT a.*,j.configuration_id,e.specification,e.resources
            FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN experiments e ON e.id=j.experiment_id
            WHERE a.pool_id=%s AND a.phase='finished' ORDER BY a.finished_at''',(queue.pool_id,)).fetchall()
    for row in rows:
        lease=queue._lease(row,row)
        path=executor.workspace(lease)
        if not path.exists() or (path/'payload-retired.json').exists():
            continue
        executor.cleanup(lease)
        # Preserve options.txt in each native run. Everything else in its input
        # snapshot is a disposable copy; outputs, logs and queue specification stay.
        targets=[path/'request',path/'work/input',path/'work/tmp']
        for snapshot in (path/'work/output').glob('*/input'):
            if snapshot.is_symlink() or snapshot.parent.is_symlink():
                raise ArtifactError('Unexpected linked model snapshot')
            targets.extend(p for p in snapshot.iterdir() if p.name!='options.txt')
        for target in targets:
            if any(p.is_symlink() for p in (target.parent,*target.parent.parents)):
                raise ArtifactError('Unexpected linked attempt storage')
            if target.is_symlink():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        atomic_json(path/'payload-retired.json',dict(temporary_inputs_removed=True,outputs_retained=True))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['serve','approve','revoke'])
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--state',type=Path,default=Path.home()/'simpaths-multirun-local')
    parser.add_argument('--prepared',type=Path,action='append',default=[],help='Verified Quick Start import; reused in place')
    parser.add_argument('--email',help='Email to approve or revoke; prompted if omitted')
    parser.add_argument('--port',type=int,default=5002)
    parser.add_argument('--console-codes',action='store_true',help='Required local test mode; codes printed in this terminal')
    args=parser.parse_args(argv)
    sys.path.insert(0,str(args.frontend.resolve(strict=True)))
    from jasmine_web.batch.access import Access
    from jasmine_web.batch.datasets import Datasets
    from jasmine_web.batch.docker_executor import DockerExecutor, DockerCLI
    from jasmine_web.batch.local_database import local_postgres, private_directory, secret_file
    from jasmine_web.batch.local_executor import atomic_json
    from jasmine_web.batch.policy import Policy, Resources
    from jasmine_web.batch.preparation import Preparations
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.submission_service import Submissions
    from jasmine_web.batch.worker import Worker
    if args.command=='serve' and (not args.console_codes or not 1024<=args.port<=65535):
        parser.error('This local preview requires --console-codes and an unprivileged port')
    os.umask(0o077)
    state=private_directory(args.state)
    q=Queue(local_postgres(state),'simpaths-local')
    q.migrate()
    q.create_pool(Resources(2000,5120,12288),policy=Policy(per_user_active=1,per_user_unfinished=10,pool_unfinished=20))
    async def console_mail(email,code):
        print(f'LOCAL TEST CODE for {email}: {code}',flush=True)
    access=Access(q,secret_file(state/'session-secret'),console_mail)
    datasets=Datasets(q,state/'uploads')
    preparations=Preparations(datasets)
    imports=state/'training-imports.json'
    paths=json.loads(imports.read_text()) if imports.exists() else []
    paths=list(dict.fromkeys(paths+[str(p.absolute()) for p in args.prepared]))
    keys=[]
    if args.command=='serve':
        for p in paths:
            print('Verifying prepared training inputs: '+p,flush=True)
            keys.append(register_training(datasets,preparations,Path(p)))
        atomic_json(imports,paths)
    else:
        email=args.email or input('Email address: ').strip()
        owner=access.approve_email(email,seconds=30*86400 if args.command=='approve' else None)
        if args.command=='approve':
            with q._connection() as c:
                keys=[r['id'] for r in c.execute('SELECT id FROM dataset_records WHERE pool_id=%s AND owner_id IS NULL',(q.pool_id,)).fetchall()]
            for key in keys:
                q.grant_dataset(owner,key,seconds=30*86400)
        print('MultiRun access '+('approved for 30 days.' if args.command=='approve' else 'revoked.'),flush=True)
        return 0
    # Grant only these explicitly imported public examples to currently approved
    # local users. Private dataset grants are never inferred or expanded here.
    with q._transaction() as (c,_,now):
        for key in keys:
            c.execute('''INSERT INTO dataset_grants SELECT pool_id,%s,id,approved_until FROM principals
                WHERE pool_id=%s AND approved_until>%s ON CONFLICT(pool_id,dataset_id,owner_id)
                DO UPDATE SET valid_until=excluded.valid_until''',(key,q.pool_id,now))
    docker=DockerCLI()
    image=json.loads(docker.call('image','inspect','simpaths-interactive:uk-user-data'))[0]['Id']
    releases=frozen_release(state,image)
    adapter=DispatchAdapter(PreparationAdapter(datasets,releases,state/'artifacts'))
    execution=private_directory(state/'execution')
    with q._connection() as c:
        locations=c.execute('SELECT location,model_digest FROM prepared_locations WHERE pool_id=%s',(q.pool_id,)).fetchall()
    images={r['image'] for r in releases.values()}|{r['model_digest'] for r in locations}
    executor=DockerExecutor(execution,approved_images=images,input_roots=[execution,state/'artifacts',*[Path(p['location']) for p in locations]])
    stop=threading.Event()
    health={'message':'Dispatcher is starting.'}
    def dispatch():
        try:
            worker=Worker(q,executor,adapter,'local-browser-worker')
            with worker.open():
                while not stop.is_set():
                    try:
                        worker.tick(claim_new=False)
                        retire_finished(q,executor)
                        worker.tick()
                        health['message']=''
                    except Exception as error:
                        health['message']='The dispatcher is waiting for recovery. Submitted jobs remain recorded; check the server terminal.'
                        # Database/connection exceptions can carry credentials.
                        print('Worker waiting for recovery: '+type(error).__name__,flush=True)
                        stop.wait(5)
                    stop.wait(1)
        except Exception as error:
            health['message']='The dispatcher stopped. Submitted jobs remain recorded; check the server terminal and restart.'
            print('Worker stopped: '+type(error).__name__+'. Check that another local worker is not running.',flush=True)
    @asynccontextmanager
    async def lifespan(app):
        thread=threading.Thread(target=dispatch,name='multirun-dispatcher',daemon=True)
        thread.start()
        yield
        stop.set()
        # Containers have independent deadlines; do not kill model work when
        # stopping the frontend. The next start adopts surviving attempts.
        print('Frontend stopped. Submitted jobs remain recorded; restart this command to resume dispatch.',flush=True)
    from jasmine_web.batch.browser import create_app
    import uvicorn
    origin=f'http://127.0.0.1:{args.port}'
    app=create_app(Submissions(access,datasets,BrowserModel(releases)),origin=origin,local_codes=True,
                   lifespan=lifespan,worker_status=lambda:health['message'])
    print('Open '+origin+' — local preview, one active job at a time.',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=args.port,proxy_headers=False,access_log=False)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
