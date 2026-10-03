"""(C) Copyright 2026, by Ross Richardson

Loopback MultiRun preview with persistent PostgreSQL, private artifacts and worker.
Explicit console-code mode is a local test facility, not production email proof.
The separate SingleRun frontend and its state registry are not imported or changed.

@author ross richardson
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from deploy._workflow import frontend_path
from .artifacts import ArtifactError, fingerprint
from .prepared_dataset import FORMAT, verify_snapshot
from .queue_adapter import read_prepared
from .schema import (DEFAULT_MAX_CONFIGURATIONS, DEFAULT_MAX_REPETITIONS,
                     deployment_configuration_limit, deployment_repetition_limit)

ROOT = Path(__file__).resolve().parents[2]
MAX_UPLOAD_ALLOWANCE_GIB = (2**63 - 1) // (1 << 30)


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
    """Bootstrap once, then load all retained bundles without recopying source."""
    from .releases import ReleaseRegistry
    registry=ReleaseRegistry(state)
    if (state/'release.pending').exists():
        raise ArtifactError('Incomplete legacy release snapshot; inspect release.pending before restarting')
    if not registry.catalogue.exists() and not (state/'release'/'release.json').exists():
        registry.register(image=image,name='SimPaths UK — initial release',jar=ROOT/'multirun.jar',defaults=ROOT/'input')
    return registry.load(expected_image=image)


def retire_finished(queue, executor):
    """Release finished container/input copies, retain scientific outputs and logs.

    Finished database state and confirmed container removal are prerequisites.
    Safe to repeat after interruption. Does not implement results retention.
    """
    from jasmine_web.batch.local_executor import atomic_json, read_json
    with queue._connection() as c:
        rows=c.execute('''SELECT a.*,j.configuration_id,e.specification,j.resources,j.dataset_id,j.model_digest,j.prepared_fingerprint,j.execution_run,
            EXISTS (SELECT 1 FROM repetitions r WHERE r.attempt_id=a.id AND r.actual_seed IS NOT NULL) AS verified
            FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN experiments e ON e.id=j.experiment_id
            WHERE a.pool_id=%s AND a.phase='finished' AND (a.outcome='success' OR
                (COALESCE(e.specification->>'operation','simulation')<>'prepare' AND EXISTS
                    (SELECT 1 FROM repetitions r WHERE r.attempt_id=a.id AND r.actual_seed IS NOT NULL)
                    AND EXISTS (SELECT 1 FROM attempt_cleanup ac WHERE ac.attempt_id=a.id AND ac.scratch_removed_at IS NOT NULL)))
            ORDER BY a.finished_at''',(queue.pool_id,)).fetchall()
    for row in rows:
        if row.get('outcome') != 'success' and not row.get('verified'):
            continue  # Unsuccessful attempts use the platform diagnostic lifecycle.
        lease=queue._lease(row,row)
        path=executor.workspace(lease)
        if not path.exists():
            continue
        prepared=(row.get('specification',{}).get('operation')=='prepare' and row.get('outcome')=='success')
        marker=read_json(path/'payload-retired.json') if (path/'payload-retired.json').exists() else None
        # Earlier cleanup missed the extra upload copies made by preparation.
        # Upgrade those markers only for successfully published preparation.
        if marker and (not prepared or marker.get('preparation_uploads_removed')):
            continue
        executor.cleanup(lease)
        # Preserve options.txt in each native run. Everything else in its input
        # snapshot is a disposable copy; outputs, logs and queue specification stay.
        targets=[path/'request',path/'work/input',path/'work/tmp']
        if prepared:
            targets.append(path/'work/startup/uploads')
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
        atomic_json(path/'payload-retired.json',dict(temporary_inputs_removed=True,outputs_retained=True,
                                                   preparation_uploads_removed=prepared))


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['serve','approve','revoke'])
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--state',type=Path,default=Path.home()/'simpaths-multirun-local')
    parser.add_argument('--dsn-file',type=Path,
                        help='Existing private PostgreSQL DSN; use for an isolated restored service instead of local database bootstrap')
    parser.add_argument('--prepared',type=Path,action='append',default=[],help='Verified Quick Start import; reused in place')
    parser.add_argument('--email',help='Email to approve or revoke; prompted if omitted')
    parser.add_argument('--port',type=int,default=5002)
    parser.add_argument('--max-repetitions',type=int,default=DEFAULT_MAX_REPETITIONS,
                        help='Maximum repetitions per configuration in new submissions (1–1000; default: %(default)s)')
    parser.add_argument('--max-configurations',type=int,default=DEFAULT_MAX_CONFIGURATIONS,
                        help='Maximum configurations per new experiment (1–100; default: %(default)s)')
    parser.add_argument('--max-unfinished-jobs',type=int,default=110,
                        help='Per-user allowance for unfinished configurations and preparations (default: %(default)s)')
    parser.add_argument('--pool-unfinished-jobs',type=int,default=220,
                        help='Shared allowance for unfinished configurations and preparations (default: %(default)s)')
    parser.add_argument('--runtime-setup-minutes',type=int,default=15,
                        help='Setup allowance per simulation attempt (0–1440 minutes; default: %(default)s)')
    parser.add_argument('--runtime-per-repetition-minutes',type=int,default=60,
                        help='Runtime added per planned repetition, without individual repetition timers (1–1440 minutes; default: %(default)s)')
    parser.add_argument('--runtime-budget-multiplier',type=int,default=3,
                        help='Cumulative execution budget as a multiple of one attempt allowance (1–3; default: %(default)s)')
    parser.add_argument('--upload-allowance-gib',type=int,default=2,
                        help='Retained upload allowance per user in whole GiB (default: %(default)s); does not allocate disk space')
    parser.add_argument('--download-threshold-mib',type=int,default=512,
                        help='Prepare resumable compressed ZIPs at this selected file size (default: %(default)s MiB)')
    parser.add_argument('--download-cache-gib',type=int,default=10,
                        help='Maximum temporary prepared-download storage (default: %(default)s GiB; does not allocate disk)')
    parser.add_argument('--download-cache-hours',type=int,default=24,
                        help='Prepared download lifetime (default: %(default)s hours; never extends source retention)')
    parser.add_argument('--notification-emails',action='store_true',help='Opt in to real completion, problem and expiry emails using platform SMTP settings')
    parser.add_argument('--retention-cleanup',action='store_true',help='Enable automatic expiry deletion; requires --notification-emails. Default: dates and notices only')
    parser.add_argument('--admin-email',help='Operator recipient for shared service problems; required with --notification-emails')
    parser.add_argument('--console-codes',action='store_true',help='Required local test mode; codes printed in this terminal')
    parser.add_argument('--visualiser-build',type=Path,help='Pinned local Visualiser build directory')
    parser.add_argument('--visualiser-preview',action='store_true',
                        help='Enable development aggregate charts for own inputs and verified public Quick Start data')
    parser.add_argument('--visualiser-memory-mib',type=int,default=1024,
                        help='Shared processing reservation; Node heap uses 256 MiB less (512–4096 MiB)')
    parser.add_argument('--visualiser-timeout-seconds',type=int,default=600,
                        help='Maximum time to process one selected visualisation (60–3600 seconds)')
    args=parser.parse_args(argv)
    try:
        deployment_repetition_limit(args.max_repetitions)
        deployment_configuration_limit(args.max_configurations)
    except ValueError as error:
        parser.error(str(error))
    if not args.max_configurations<=args.max_unfinished_jobs<=args.pool_unfinished_jobs<=10000:
        parser.error('Queue allowances must satisfy max-configurations <= max-unfinished-jobs <= pool-unfinished-jobs <= 10000')
    if (not 0<=args.runtime_setup_minutes<=1440 or not 1<=args.runtime_per_repetition_minutes<=1440
            or not 1<=args.runtime_budget_multiplier<=3):
        parser.error('Runtime settings must be 0–1440 setup minutes, 1–1440 minutes per repetition and a budget multiplier of 1–3')
    if not 1 <= args.upload_allowance_gib <= MAX_UPLOAD_ALLOWANCE_GIB:
        parser.error(f'--upload-allowance-gib must be between 1 and {MAX_UPLOAD_ALLOWANCE_GIB}')
    if not 1 <= args.download_threshold_mib <= 1048576 or not 1 <= args.download_cache_gib <= 1024 or not 1 <= args.download_cache_hours <= 168:
        parser.error('Download limits must be 1–1048576 MiB, 1–1024 GiB and 1–168 hours respectively')
    if args.retention_cleanup and not args.notification_emails:
        parser.error('--retention-cleanup requires --notification-emails so expiry warnings can be delivered')
    if bool(args.visualiser_build)!=args.visualiser_preview:
        parser.error('Development visualiser requires both --visualiser-build and --visualiser-preview')
    if not 512<=args.visualiser_memory_mib<=4096 or not 60<=args.visualiser_timeout_seconds<=3600:
        parser.error('Visualiser limits must be 512–4096 MiB and 60–3600 seconds')
    if args.command=='serve' and (not args.console_codes or not 1024<=args.port<=65535):
        parser.error('This local preview requires --console-codes and an unprivileged port')
    return args


def configuration_runtime(args):
    """Validate all permitted repetition counts before touching local state."""
    from jasmine_web.batch.policy import Policy, RuntimeAllowance
    runtime=RuntimeAllowance(args.runtime_setup_minutes*60,args.runtime_per_repetition_minutes*60,
                             args.runtime_budget_multiplier)
    runtime.describe(args.max_repetitions,Policy())
    return runtime


def main(argv=None):
    args=parse_args(argv)
    if args.notification_emails and (not args.admin_email or not os.environ.get('SMTP_HOST') or not os.environ.get('SMTP_FROM_EMAIL')):
        raise ValueError('Notification emails require --admin-email, SMTP_HOST and SMTP_FROM_EMAIL')
    sys.path.insert(0,str(args.frontend.resolve(strict=True)))
    from jasmine_web.batch.docker_executor import DockerCLI
    from jasmine_web.batch.local_database import local_postgres, private_directory
    from jasmine_web.batch.store import Queue
    try:
        configuration_runtime(args)
    except ValueError as error:
        raise SystemExit('The configured maximum repetition count exceeds the runtime limits: '+str(error)) from None
    os.umask(0o077)
    state=private_directory(args.state)
    from .maintenance import service_state
    with service_state(state):
        if args.dsn_file:
            from .vm_web import private_text
            dsn=private_text(args.dsn_file)
        else:
            dsn=local_postgres(state)
        q=Queue(dsn,'simpaths-local')
        from jasmine_web.batch.backup import database_guard
        with database_guard(q):
            return run_service(args,state,q)


def run_service(args,state,q):
    from jasmine_web.batch.docker_executor import DockerCLI
    q.migrate()
    async def console_mail(email,code):
        print(f'LOCAL TEST CODE for {email}: {code}',flush=True)
    from .runtime import create_application, create_registry
    access,datasets,preparations,keys=create_registry(args,q,state,console_mail)
    if args.command!='serve':
        return 0
    from .releases import ReleaseRegistry, installed_image
    registry=ReleaseRegistry(state)
    if registry.catalogue.exists() or (state/'release'/'release.json').exists():
        image=next(iter(registry.load().values()))['image']
    else:
        image=json.loads(DockerCLI().call('image','inspect','simpaths-interactive:uk-user-data'))[0]['Id']
    installed_image(image)
    origin=f'http://127.0.0.1:{args.port}'
    app=create_application(args,q,state,access,datasets,preparations,keys,image,origin,local_codes=True)
    import uvicorn
    print('Open '+origin+' — local preview, one active job at a time.',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=args.port,proxy_headers=False,access_log=False)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
