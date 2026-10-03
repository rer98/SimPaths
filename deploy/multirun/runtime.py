"""(C) Copyright 2026, by Ross Richardson

Shared MultiRun registry and supervised application for laptop and native VM hosts.
Authentication delivery, origin and resource policy are supplied by the launcher.
@author ross richardson
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import signal
import threading

from .browser_model import BrowserModel
from .local_web import configuration_runtime, frozen_release, register_training, retire_finished
from .submission_adapter import DispatchAdapter, PreparationAdapter
from jasmine_web.batch.access import Access
from jasmine_web.batch.attempt_cleanup import AttemptCleanup
from jasmine_web.batch.completion_notifications import CompletionNotifications
from jasmine_web.batch.datasets import Datasets
from jasmine_web.batch.docker_executor import DockerExecutor, DockerCLI
from jasmine_web.batch.local_database import private_directory, secret_file
from jasmine_web.batch.local_executor import atomic_json
from jasmine_web.batch.notifications import Notifications, smtp_sender
from jasmine_web.batch.output_management import OutputManagement
from jasmine_web.batch.policy import Policy, Resources
from jasmine_web.batch.preparation import Preparations
from jasmine_web.batch.results import Results
from jasmine_web.batch.retention import Retention
from jasmine_web.batch.storage import Storage
from jasmine_web.batch.submission_service import Submissions
from jasmine_web.batch.worker import Worker


def create_registry(args,q,state,send_mail,*,capacity=Resources(2000,5120,12288),per_user_active=1):
    q.create_pool(capacity,holdback=getattr(args,'holdback',None),policy=Policy(per_user_active=per_user_active,
        per_user_unfinished=args.max_unfinished_jobs,pool_unfinished=args.pool_unfinished_jobs),
        update_admission_limits=True)
    access=Access(q,secret_file(state/'session-secret'),send_mail)
    datasets=Datasets(q,state/'uploads',owner_limit=args.upload_allowance_gib << 30)
    preparations=Preparations(datasets)
    imports=state/'training-imports.json'
    paths=json.loads(imports.read_text()) if imports.exists() else []
    paths=list(dict.fromkeys(paths+[str(p.absolute()) for p in args.prepared]))
    keys=[]
    if args.command=='serve':
        print(f'Retained upload allowance: {args.upload_allowance_gib} GiB per user',flush=True)
        print(f'Configuration runtime: {args.runtime_setup_minutes} minutes setup + '
              f'{args.runtime_per_repetition_minutes} minutes per repetition; cumulative budget '
              f'{args.runtime_budget_multiplier} × one attempt allowance; at most 3 attempts',flush=True)
        for p in paths:
            print('Verifying prepared training inputs: '+p,flush=True)
            keys.append(register_training(datasets,preparations,Path(p)))
        atomic_json(imports,paths)
    else:
        email=args.email or input('Email address: ').strip()
        if args.command=='approve':
            # Approval must not accidentally grant a future confidential provider
            # dataset. Only these explicitly verified public training imports.
            from .queue_adapter import read_prepared
            from .prepared_dataset import FORMAT, verify_snapshot
            for p in paths:
                receipt=read_prepared(Path(p))
                if receipt['identity']['format']!=FORMAT:
                    raise ValueError('Approval grants only verified public training inputs')
                verify_snapshot(Path(p),receipt)
                keys.append(receipt['revision'])
        owner=access.approve_email(email,seconds=30*86400 if args.command=='approve' else None)
        if args.command=='approve':
            for key in keys:
                q.grant_dataset(owner,key,seconds=30*86400)
        print('MultiRun access '+('approved for 30 days.' if args.command=='approve' else 'revoked.'),flush=True)
        return access,datasets,preparations,keys
    # Grant only these explicitly imported public examples to currently approved
    # local users. Private dataset grants are never inferred or expanded here.
    with q._transaction() as (c,_,now):
        for key in keys:
            c.execute('''INSERT INTO dataset_grants SELECT pool_id,%s,id,approved_until FROM principals
                WHERE pool_id=%s AND approved_until>%s ON CONFLICT(pool_id,dataset_id,owner_id)
                DO UPDATE SET valid_until=excluded.valid_until''',(key,q.pool_id,now))
    return access,datasets,preparations,keys


def create_application(args,q,state,access,datasets,preparations,keys,image,origin,*,
                       local_codes=False,terminate_on_dispatch_failure=False):
    releases=frozen_release(state,image)
    default=next(iter(releases))
    print('Default model release: '+releases[default].get('name',default)+
          f' ({default}); {len(releases)} retained version(s)',flush=True)
    adapter=DispatchAdapter(PreparationAdapter(datasets,releases,state/'artifacts'))
    execution=private_directory(state/'execution')
    with q._connection() as c:
        locations=c.execute('SELECT location,model_digest FROM prepared_locations WHERE pool_id=%s',(q.pool_id,)).fetchall()
    images={r['image'] for r in releases.values()}|{r['model_digest'] for r in locations}
    executor=DockerExecutor(execution,approved_images=images,input_roots=[execution,state/'artifacts',*[Path(p['location']) for p in locations]])
    stop=threading.Event()
    health={'message':'Dispatcher is starting.'}
    fatal=threading.Event()
    def dispatch():
        try:
            def before_claim():
                service.attempt_cleanup.retire()
                retire_finished(q,executor)
                service.retention.retire()
                service.lifecycle.retire(state/'artifacts')
                service.outputs.retire()
                service.lifecycle.reconcile()
            worker=Worker(q,executor,adapter,'local-browser-worker' if local_codes else 'vm-browser-worker',
                          before_claim=before_claim)
            with worker.open():
                while not stop.is_set():
                    try:
                        worker.tick(claim_new=False)
                        service.attempt_cleanup.retire()
                        retire_finished(q,executor)
                        service.lifecycle.retire(state/'artifacts')
                        service.outputs.retire()
                        worker.tick()
                        health['message']=''
                    except Exception as error:
                        health['message']=('The dispatcher is waiting for recovery. Submitted jobs remain recorded; check the server terminal.'
                            if local_codes else 'The service is recovering. Your submitted jobs remain recorded.')
                        # Database/connection exceptions can carry credentials.
                        print('Worker waiting for recovery: '+type(error).__name__,flush=True)
                        stop.wait(5)
                    stop.wait(1)
        except Exception as error:
            health['message']=('The dispatcher stopped. Submitted jobs remain recorded; check the server terminal and restart.'
                if local_codes else 'The service is restarting. Your submitted jobs remain recorded.')
            print('Worker stopped: '+type(error).__name__+'. Check that another worker is not running.',flush=True)
            if terminate_on_dispatch_failure:
                fatal.set()
                os.kill(os.getpid(),signal.SIGTERM)
    @asynccontextmanager
    async def lifespan(app):
        thread=threading.Thread(target=dispatch,name='multirun-dispatcher',daemon=True)
        thread.start()
        def notify():
            while not stop.is_set():
                try:
                    service.notifications.reconcile()
                    service.retention.reconcile()
                    service.completions.reconcile()
                    for _ in range(5):
                        if stop.is_set():
                            break
                        problems=service.notifications.deliver_one()
                        expiry=service.retention.deliver_one()
                        completion=service.completions.deliver_one()
                        if not problems and not expiry and not completion:
                            break
                except Exception as error:
                    print('Notification checks waiting for recovery: '+type(error).__name__,flush=True)
                stop.wait(5)
        notifications=threading.Thread(target=notify,name='multirun-notifications',daemon=True)
        notifications.start()
        yield
        stop.set()
        # Containers have independent deadlines; do not kill model work when
        # stopping the frontend. The next start adopts surviving attempts.
        await asyncio.to_thread(thread.join,30)
        await asyncio.to_thread(notifications.join,30)
        print('Frontend stopped. Submitted jobs remain recorded; restart the service to resume dispatch.',flush=True)
    from jasmine_web.batch.browser import create_app
    import uvicorn
    # Explicit, reviewed local recovery from laptop suspension/reconciliation
    # delay. Hosted services keep this disabled unless their operator opts in.
    service=Submissions(access,datasets,BrowserModel(releases,max_repetitions=args.max_repetitions,
                        max_configurations=args.max_configurations),
                        allow_deadline_credit=local_codes,runtime_allowance=configuration_runtime(args))
    from .queue_adapter import result_catalogue, result_name, result_deletion_targets, result_inputs, result_settings
    from jasmine_web.batch.output_management import OutputManagement
    service.results=Results(service,executor,result_catalogue,name=result_name,
                            inputs=result_inputs,settings=result_settings,
                            downloads=dict(threshold=args.download_threshold_mib*1024**2,
                                           capacity=args.download_cache_gib*1024**3,
                                           lifetime=args.download_cache_hours*3600))
    if args.visualiser_preview:
        from .visualiser_backend import VisualiserBackend
        from jasmine_web.batch.visualiser import Visualiser
        backend=VisualiserBackend(args.visualiser_build,execution,public_datasets=keys,
                                  memory_mib=args.visualiser_memory_mib)
        service.visualiser=Visualiser(service.results,backend,
            resources=Resources(1000,args.visualiser_memory_mib,512),runtime=args.visualiser_timeout_seconds)
        print('Online Visualiser: development aggregate preview; paired impacts await the updated release',flush=True)
    service.outputs=OutputManagement(service,executor,result_deletion_targets)
    service.attempt_cleanup=AttemptCleanup(q,executor,outputs=result_deletion_targets,
                                           failed_preparation=adapter.preparation.retire_failed)
    service.storage=Storage(service,execution)
    service.notifications=Notifications(service,admin_email=args.admin_email,
        sender=smtp_sender(os.environ['SMTP_FROM_EMAIL']) if args.notification_emails else None)
    service.retention=Retention(service,origin=origin,cleanup_enabled=args.retention_cleanup,
        sender=smtp_sender(os.environ['SMTP_FROM_EMAIL']) if args.notification_emails else None)
    service.completions=CompletionNotifications(service,origin=origin,
        sender=smtp_sender(os.environ['SMTP_FROM_EMAIL']) if args.notification_emails else None)
    service.retention.reconcile()
    service.completions.reconcile()
    print('Automatic expiry deletion: '+('enabled' if args.retention_cleanup else 'disabled; dates and notices recorded locally'),flush=True)
    print('Completion, problem and expiry email delivery: '+('enabled' if args.notification_emails else 'disabled; notices recorded locally'),flush=True)
    app=create_app(service,origin=origin,local_codes=local_codes,
                   lifespan=lifespan,worker_status=lambda:health['message'],
                   site=dict(name='SimPaths Online',subtitle='UK MultiRun',logo='/static/simpaths-logo.svg',
                             icon='/static/simpaths-favicon.svg'),
                   footer_links=(('SimPaths','https://simpaths.org'),
                                 ('GitHub','https://github.com/simpaths/SimPaths'),
                                 ('CeMPA','https://www.microsimulation.ac.uk/'),
                                 ('License','https://github.com/simpaths/SimPaths/blob/main/license.txt')))
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.concurrency import run_in_threadpool
    async def healthz(request):
        def database_ready():
            with q._connection() as c:
                c.execute('SELECT 1')
        ready=not health['message'] and not fatal.is_set()
        try:
            await run_in_threadpool(database_ready)
        except Exception:
            ready=False
        return JSONResponse({'ready':ready},status_code=200 if ready else 503)
    app.router.routes.append(Route('/healthz',healthz,methods=['GET']))
    app.state.submissions=service
    return app
