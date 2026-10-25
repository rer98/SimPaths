"""(C) Copyright 2026, by Ross Richardson

Shared MultiRun registry and supervised application for laptop and native VM hosts.
Authentication delivery, origin and resource policy are supplied by the launcher.
@author ross richardson
"""
import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import threading
from functools import wraps

from .browser_model import BrowserModel
from .local_web import configuration_runtime, frozen_release, register_training, retire_finished
from .submission_adapter import DispatchAdapter, PreparationAdapter
from jasmine_web.async_cleanup import finish_before_cancelling
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


def maintenance_guard(operation):
    @wraps(operation)
    def guarded(args,q,state,*values,**settings):
        from .maintenance import service_state
        from jasmine_web.batch.backup import database_guard
        with service_state(state), database_guard(q):
            return operation(args,q,state,*values,**settings)
    return guarded


@asynccontextmanager
async def private_processors(service, stop):
    """Keep private cache and source guards until every background worker stops."""
    def maintain():
        while not stop.is_set():
            try:
                service.results.downloads.retire()
                if hasattr(service,'visualiser'):
                    service.visualiser.retire()
            except Exception as error:
                print('Private cache cleanup waiting: '+type(error).__name__,flush=True)
            stop.wait(30)
    def process(slot):
        while not service.visualiser.stop.is_set():
            try:
                if slot==0:
                    service.visualiser.tick()
                else:
                    service.visualiser.tick(slot)
            except Exception as error:
                print('Private Visualiser processor waiting: '+type(error).__name__,flush=True)
            service.visualiser.stop.wait(1)
    maintenance=threading.Thread(target=maintain,name='multirun-private-cache',daemon=True)
    processors=([threading.Thread(target=process,args=(slot,),
                    name='multirun-private-visualiser'+('-'+str(slot) if slot else ''),daemon=True)
                 for slot in range(getattr(service.visualiser,'max_workers',1))]
                if hasattr(service,'visualiser') else [])
    try:
        maintenance.start()
        for processor in processors:
            processor.start()
        yield
    finally:
        async def shutdown():
            stop.set()
            if processors:
                service.visualiser.close()
            # close() now joins builders. Run it off the event loop and retain
            # ownership even if the enclosing shutdown is cancelled again.
            await asyncio.to_thread(service.results.downloads.close)
            if maintenance.ident is not None:
                await asyncio.to_thread(maintenance.join)
            for processor in processors:
                if processor.ident is not None:
                    await asyncio.to_thread(processor.join)
        await finish_before_cancelling(asyncio.create_task(shutdown()))


@maintenance_guard
def create_registry(args,q,state,send_mail,*,capacity=Resources(2000,5120,12288),per_user_active=1):
    q.backup_state=state
    from .storage_budget import bootstrap as storage_budget
    storage_override=storage_budget(args,q,state)
    q.create_pool(capacity,holdback=getattr(args,'holdback',None),policy=Policy(per_user_active=per_user_active,
        per_user_unfinished=args.max_unfinished_jobs,pool_unfinished=args.pool_unfinished_jobs),
        update_admission_limits=True,
        allow_compute_budget=True,
        **({'storage_budget_mib':storage_override} if storage_override is not None else {}))
    protected=(Resources(getattr(args,'visualiser_cpu_millis',1000),args.visualiser_memory_mib,
                         getattr(args,'visualiser_storage_mib',512))
               if getattr(args,'visualiser_protected',False) else None)
    q.configure_processing(protected,growth_memory_mib=getattr(args,'growth_memory_mib',0))
    from jasmine_web.batch.idle_interactive_cpu import configure as configure_idle_cpu
    configure_idle_cpu(q,getattr(args,'lend_idle_interactive_cpu',False))
    from .compute_capacity import bootstrap as compute_bootstrap
    compute_bootstrap(args,q,capacity)
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
            from .public_training import is_public_receipt
            for p in paths:
                receipt=read_prepared(Path(p))
                if receipt['identity']['format']!=FORMAT and not is_public_receipt(receipt):
                    raise ValueError('Approval grants only verified public training inputs')
                verify_snapshot(Path(p),receipt)
                keys.append(receipt['revision'])
        permanent=bool(getattr(args,'permanent',False)) and args.command=='approve'
        days=getattr(args,'approval_days',30)
        owner=access.approve_email(email,seconds=days*86400 if args.command=='approve' and not permanent else None,
                                   permanent=permanent)
        if args.command=='approve':
            for key in keys:
                with q._transaction() as (c,_,_):
                    c.execute('UPDATE datasets SET public_training=true WHERE pool_id=%s AND id=%s',(q.pool_id,key))
        description=('approved without an expiry.' if permanent else f'approved for {days} days.')
        print('MultiRun access '+(description if args.command=='approve' else 'revoked.'),flush=True)
        return access,datasets,preparations,keys
    # register_training marks only verified public examples as available to all
    # approved accounts. No independent expiry or confidential grant is inferred.
    return access,datasets,preparations,keys


@maintenance_guard
def create_application(args,q,state,access,datasets,preparations,keys,image,origin,*,
                       local_codes=False,terminate_on_dispatch_failure=False,private_service=False):
    q.backup_state=state
    releases=frozen_release(state,image)
    default=next(iter(releases))
    print('Default model release: '+releases[default].get('name',default)+
          f' ({default}); {len(releases)} retained version(s)',flush=True)
    storage=releases[default]['resource_policy']['simulation']['storage']
    profiled=releases[default]['resource_policy'].get('profiles')
    if profiled and releases[default]['resource_policy'].get('fallback'):
        print(f'Starting resources: {len(profiled)} workload profiles based on laptop measurements; '
              'other supported settings use the bounded reference allowance',flush=True)
    elif profiled:
        print(f'Starting resources: {len(profiled)} tested workload profiles; unmatched settings require operator review',flush=True)
    else:
        print(f"Default working storage: {storage['setup_mib']/1024:g} GiB fixed + "
              f"{storage['per_repetition_mib']} MiB per repetition"+
              ('; larger input copies can raise the fixed term' if storage['per_repetition_mib'] else '; retained fixed policy'),
              flush=True)
    adapter=DispatchAdapter(PreparationAdapter(datasets,releases,state/'artifacts'))
    execution=private_directory(state/'execution')
    with q._connection() as c:
        locations=c.execute('SELECT location,model_digest FROM prepared_locations WHERE pool_id=%s',(q.pool_id,)).fetchall()
    images={r['image'] for r in releases.values()}|{r['model_digest'] for r in locations}
    quotas=None
    if getattr(args,'dedicated_storage',False):
        from .vm_web import workspace_quotas
        quotas=workspace_quotas(args)
        quotas.guard(); quotas.preflight()
    executor=DockerExecutor(execution,approved_images=images,workspace_quotas=quotas,
        input_roots=[execution,state/'artifacts',*[Path(p['location']) for p in locations]])
    stop=threading.Event()
    from .storage_budget import controller as storage_controller
    storage_budget=storage_controller(args,q,state)
    health={'message':'Dispatcher is starting.'}
    fatal=threading.Event()
    def dispatch():
        try:
            def before_claim():
                if not storage_budget.tick():
                    return False
                service.attempt_cleanup.retire()
                retire_finished(q,executor)
                service.retention.retire()
                service.lifecycle.retire(state/'artifacts')
                service.outputs.retire()
                service.lifecycle.reconcile()
                if hasattr(service,'preparation_scheduler'):
                    return service.preparation_scheduler.before_claim(worker)
            scheduler=getattr(service,'preparation_scheduler',None)
            worker=Worker(q,executor,adapter,'local-browser-worker' if local_codes else 'vm-browser-worker',
                          before_claim=before_claim,
                          cpu_hold=scheduler.cpu_hold if scheduler else None,
                          admission_guard=(lambda:scheduler.guard) if scheduler else None,
                          after_claim=scheduler.claimed if scheduler else None,
                          completion_handover=service.visualiser.completion_handover if scheduler else None)
            from jasmine_web.batch.idle_interactive_cpu import RecallListener
            recall=(RecallListener(q,lambda:worker.rebalance_cpu(reserve_cpu_millis=0,allow_queued=False))
                    if getattr(args,'lend_idle_interactive_cpu',False) else nullcontext())
            with worker.open(),recall:
                while not stop.is_set():
                    try:
                        worker.tick(claim_new=False)
                        with worker.maintenance():
                            service.attempt_cleanup.retire()
                            retire_finished(q,executor)
                            service.lifecycle.retire(state/'artifacts')
                            service.outputs.retire()
                        worker.tick(max_claims=1 if scheduler else 32)
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
        from .maintenance import service_state
        from jasmine_web.batch.backup import database_guard
        # Covers hosts embedding this application as well as native launchers.
        with service_state(state), database_guard(q):
            exports=getattr(service,'performance_exports',None)
            try:
                if exports is not None:exports.start()
                async with running(app):
                    if private_service:
                        async with private_processors(service,stop):
                            yield
                    else:
                        yield
            finally:
                if exports is not None:await finish_before_cancelling(asyncio.create_task(asyncio.to_thread(exports.close)))
    @asynccontextmanager
    async def running(app):
        service.performance_review.start()
        if hasattr(service,'monitor'):
            service.monitor.start()
        thread=threading.Thread(target=dispatch,name='multirun-dispatcher',daemon=True)
        thread.start()
        def notify():
            from jasmine_web.batch.compute_capacity import Monitor
            capacity_monitor=Monitor(q)
            while not stop.is_set():
                try:
                    capacity_monitor.tick()
                    service.notifications.reconcile()
                    service.retention.reconcile()
                    service.completions.reconcile()
                    service.account_reminders.reconcile(timed=True)
                    service.release_retirement.tick()
                    for _ in range(5):
                        if stop.is_set():
                            break
                        problems=service.notifications.deliver_one()
                        expiry=service.retention.deliver_one()
                        completion=service.completions.deliver_one()
                        renewal=service.account_reminders.deliver_one()
                        if not problems and not expiry and not completion and not renewal:
                            break
                except Exception as error:
                    print('Notification checks waiting for recovery: '+type(error).__name__,flush=True)
                stop.wait(5)
        notifications=threading.Thread(target=notify,name='multirun-notifications',daemon=True)
        notifications.start()
        try:
            yield
        finally:
            async def shutdown():
                stop.set()
                # Keep lifecycle fences until in-flight publication/cleanup finishes.
                # Containers retain independent deadlines and survive this process.
                await asyncio.to_thread(thread.join)
                await asyncio.to_thread(notifications.join)
                if hasattr(service,'monitor'):
                    await asyncio.to_thread(service.monitor.close)
                await asyncio.to_thread(service.performance_review.close)
            await finish_before_cancelling(asyncio.create_task(shutdown()))
            print(('Executor' if private_service else 'Frontend')+' stopped. Submitted jobs remain recorded; restart the service to resume dispatch.',flush=True)
    # Explicit, reviewed local recovery from laptop suspension/reconciliation
    # delay. Hosted services keep this disabled unless their operator opts in.
    from .resource_recovery_settings import policy as recovery_policy
    recovery=recovery_policy(args)
    recovery_storage_ceiling=(executor.workspace_quotas.recovery_ceiling()
                              if recovery is not None and executor.workspace_quotas is not None else None)
    adaptive=None
    if getattr(args,'adaptive_cpu',False):
        from jasmine_web.batch.adaptive_cpu import AdaptiveCPUPolicy
        adaptive=AdaptiveCPUPolicy(cooldown_seconds=getattr(args,'cpu_cooldown_seconds',30))
    service=Submissions(access,datasets,BrowserModel(releases,max_repetitions=args.max_repetitions,
                        max_configurations=args.max_configurations),
                        allow_deadline_credit=local_codes,runtime_allowance=configuration_runtime(args),
                        resource_recovery=recovery,recovery_storage_ceiling_mib=recovery_storage_ceiling,
                        adaptive_cpu=adaptive)
    print('Automatic resource recovery: '+('enabled for new reviews; frozen ceilings and original retry/time budgets apply'
                                          if recovery else 'disabled'),flush=True)
    from jasmine_web.batch.performance_review import PerformanceReview
    from jasmine_web.batch.observability import model_reader,shared_reader
    from jasmine_web.batch.performance_trace import ReviewTrace,interactive_reader,service_groups
    from .performance_review import application_fingerprint
    trace=ReviewTrace(q,state,application_code_fingerprint=application_fingerprint())
    if getattr(executor,'docker',None) is not None:
        trace.single_reader=interactive_reader(q,executor.docker)
    trace.services=service_groups()
    executor.performance=trace
    native_reader=shared_reader(model_reader(q,executor))
    service.performance_review=PerformanceReview(q,state,native_reader,trace=trace)
    from jasmine_web.batch.performance_exports import PerformanceExports,Settings as ExportSettings,set_control as export_control,smtp_sender as export_sender
    export_settings=ExportSettings(**getattr(args,'monitoring_exports',{}))
    if not (state/'performance-exports.json').exists():export_control(state,**asdict(export_settings))
    service.performance_exports=PerformanceExports(q,state,recipient=args.admin_email,
        sender=export_sender(os.environ['SMTP_FROM_EMAIL']) if args.notification_emails else None)
    if getattr(args,'operational_metrics',False):
        from jasmine_web.batch.observability import OperationalMonitor, Settings, model_reader
        service.monitor=OperationalMonitor(q,state,settings=Settings(
            sample_seconds=getattr(args,'metrics_sample_seconds',15),
            retention_days=getattr(args,'metrics_retention_days',30),
            max_rows=getattr(args,'metrics_max_rows',20000)),model_reader=native_reader)
        service.monitor.performance=trace
        print('Operational metrics: background numeric summaries enabled',flush=True)
    else:
        from jasmine_web.batch.performance_trace import HTTPReview
        service.monitor=HTTPReview(trace)
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
        automatic=getattr(args,'visualiser_automatic',False)
        scoped=getattr(args,'visualiser_hard_limits',False)
        if (automatic or scoped) and not private_service:
            raise ValueError('Bounded automatic preparation requires the separated private executor')
        scopes=None
        workers=getattr(args,'visualiser_max_workers',1)
        seed_workers=getattr(args,'visualiser_seed_workers',1)
        if scoped:
            from jasmine_web.batch.processing_scope import DelegatedScopes
            group_memory=max(args.visualiser_memory_mib,5120) if seed_workers>1 else args.visualiser_memory_mib
            scopes=DelegatedScopes(minimum_memory_mib=workers*group_memory+2048)
            if getattr(scopes,'root',None) is not None:
                trace.services.append(('private-manager',scopes.root/'manager'))
        backend_options={}
        if getattr(args,'visualiser_summaries',False):
            backend_options=dict(summary_cache_root=execution/'visualiser-cache'/'summaries',
                summary_cache_bytes=getattr(args,'visualiser_summary_cache_mib',2048)*1024**2)
        backend=VisualiserBackend(args.visualiser_build,execution,public_datasets=keys,
            memory_mib=max(256,args.visualiser_memory_mib-768) if scoped else args.visualiser_memory_mib,
            **backend_options)
        backend.summary_retention_seconds=getattr(args,'visualiser_summary_hours',168)*3600
        if backend.summary_cache is not None:
            from .visualiser_cache_usage import statistics
            from jasmine_web.batch.performance_trace import NUMBERS
            trace.cache_reader=lambda:{key:value for key,value in statistics(backend.summary_cache.root).get('counters',{}).items()
                                       if key in NUMBERS}
        from jasmine_web.batch.aggregate_limits import AggregateLimits
        limits=AggregateLimits(getattr(args,'visualiser_artifact_mib',16)*1024**2,
            getattr(args,'visualiser_comparison_mib',64)*1024**2,
            getattr(args,'visualiser_rows_per_series',50000),getattr(args,'visualiser_comparison_rows',200000))
        storage_policy=None
        if getattr(args,'visualiser_storage_pressure',False):
            from jasmine_web.batch.visualiser_storage import DiskHeadroom
            storage_policy=dict(enabled=True,
                maximum_bytes=(getattr(args,'visualiser_cache_max_mib',0) or args.visualiser_cache_mib)*1024**2,
                summary_maximum_bytes=(getattr(args,'visualiser_summary_cache_max_mib',0) or args.visualiser_summary_cache_mib)*1024**2,
                idle_seconds=getattr(args,'visualiser_idle_hours',72)*3600,
                warning_seconds=getattr(args,'visualiser_warning_seconds',300),
                physical=DiskHeadroom(q,executor,reserve_bytes=getattr(args,'visualiser_filesystem_headroom_mib',16384)*1024**2))
        service.visualiser=Visualiser(service.results,backend,
            resources=Resources(getattr(args,'visualiser_cpu_millis',1000),args.visualiser_memory_mib,
                                getattr(args,'visualiser_storage_mib',512)),
            runtime=args.visualiser_timeout_seconds,automatic=automatic,max_workers=workers,
            seed_workers=seed_workers,
            handover=getattr(args,'visualiser_handover',False),
            resource_scope=scopes,bounded_comparisons=getattr(args,'visualiser_bounded_comparisons',False),
            capacity=getattr(args,'visualiser_cache_mib',256)*1024**2,
            lifetime=getattr(args,'visualiser_cache_hours',168)*3600,
            max_entries=getattr(args,'visualiser_max_entries',64),
            retain_completed_summaries=getattr(args,'visualiser_retain_completed_summaries',True),
            per_owner_entries=getattr(args,'visualiser_per_owner_entries',256) if automatic else 10,
            publication_limits=limits,storage_policy=storage_policy)
        service.visualiser.performance=trace
        if automatic:
            from jasmine_web.batch.preparation_scheduler import PreparationScheduler
            service.preparation_scheduler=PreparationScheduler(q,service.visualiser,
                priority_seconds=getattr(args,'preparation_priority_seconds',60))
            service.visualiser.processing_admission=service.preparation_scheduler.reserve
        print('Online Visualiser: bounded selected-chart delivery'+
              ('; automatic configuration/baseline preparation enabled' if automatic else ''),flush=True)
    service.outputs=OutputManagement(service,executor,result_deletion_targets)
    service.attempt_cleanup=AttemptCleanup(q,executor,outputs=result_deletion_targets,
                                           failed_preparation=adapter.preparation.retire_failed)
    service.storage=Storage(service,execution)
    service.notifications=Notifications(service,admin_email=args.admin_email,
        sender=smtp_sender(os.environ['SMTP_FROM_EMAIL']) if args.notification_emails else None,
        storage_wait_emails=getattr(args,'storage_wait_emails',False),
        storage_wait_hours=getattr(args,'storage_wait_hours',6))
    service.retention=Retention(service,origin=origin,cleanup_enabled=args.retention_cleanup,
        sender=smtp_sender(os.environ['SMTP_FROM_EMAIL']) if args.notification_emails else None)
    service.completions=CompletionNotifications(service,origin=origin,
        sender=smtp_sender(os.environ['SMTP_FROM_EMAIL']) if args.notification_emails else None)
    from jasmine_web.batch.account_reminders import AccountReminders
    service.account_reminders=AccountReminders(service,origin=origin,admin_email=args.admin_email,
        sender=smtp_sender(os.environ['SMTP_FROM_EMAIL']) if args.notification_emails else None)
    from .release_retirement import ReleaseRetirement
    service.release_retirement=ReleaseRetirement(q,state,protected=(default,))
    service.retention.reconcile()
    service.completions.reconcile()
    print('Automatic expiry deletion: '+('enabled' if args.retention_cleanup else 'disabled; dates and notices recorded locally'),flush=True)
    print('Completion, problem and expiry email delivery: '+('enabled' if args.notification_emails else 'disabled; notices recorded locally'),flush=True)
    from datetime import datetime, timezone
    atomic_json(state/'operator-settings.json',dict(format='simpaths.operator.settings.v1',
        pool_id=q.pool_id,schema=q.schema,recorded_at=datetime.now(timezone.utc).isoformat(),
        model_release=default,notification_delivery=args.notification_emails,
        retention_cleanup=args.retention_cleanup,visualiser_enabled=args.visualiser_preview,
        adaptive_cpu=adaptive is not None,visualiser_automatic=getattr(args,'visualiser_automatic',False),
        lend_idle_interactive_cpu=getattr(args,'lend_idle_interactive_cpu',False),
        visualiser_workers=getattr(args,'visualiser_max_workers',1),
        visualiser_seed_workers=getattr(args,'visualiser_seed_workers',1),
        visualiser_handover=getattr(args,'visualiser_handover',False),
        growth_memory_mib=getattr(args,'growth_memory_mib',0)))
    def ready():
        result={'ready':False,'worker_status':health['message']}
        if health['message'] or fatal.is_set():
            return result
        try:
            with q._connection() as connection:
                connection.execute('SELECT 1')
        except Exception:
            result['worker_status']='The service is recovering. Your submitted jobs remain recorded.'
            return result
        result['ready']=True
        return result
    if private_service:
        from types import SimpleNamespace
        return SimpleNamespace(service=service,lifespan=lifespan,ready=ready,
                               worker_status=lambda:health['message'])
    from jasmine_web.batch.browser import create_app
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
