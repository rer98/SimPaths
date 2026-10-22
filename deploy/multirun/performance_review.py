"""(C) Copyright 2026, by Ross Richardson

Operator-only live review controls and private, allowlisted SimPaths measurements.
Controls never start applications, contact Docker/SMTP or modify simulation jobs.
@author ross richardson
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys


def application_fingerprint():
    """Fingerprint deployed adapter/runner code without paths or Git metadata."""
    digest=hashlib.sha256()
    try:
        for name in ('runtime.py','container_adapter.py','submission_adapter.py','queue_adapter.py',
                     'configuration.py','schema.py','resource_policy.py','visualiser_backend.py',
                     'java_review.py','performance_review.py','monitoring_exports.py','storage_budget.py','container_run.sh'):
            digest.update(name.encode());digest.update(Path(__file__).with_name(name).read_bytes())
    except OSError:return None
    return digest.hexdigest()


def workload(row):
    """Workload counts and release identities; no labels, seeds or input values."""
    from .schema import COLLECTOR_FIELDS
    spec = row['specification']
    run = row.get('execution_run') or next((r for r in spec.get('run_sets',[])
                                          if r.get('id')==row['configuration_id']),{})
    parameters = run.get('parameters',{})
    common = parameters.get('common',{})
    result = dict(repetitions=len(spec.get('seeds',[])))
    for key, target in (('population','initial_population'),('start_year','start_year'),('end_year','end_year')):
        if type(common.get(key)) is int:
            result[target] = common[key]
    if 'start_year' in result and 'end_year' in result:
        result['simulation_years'] = result['end_year']-result['start_year']+1
    collectors = parameters.get('run_sets',[{}])
    selected = next((r for r in collectors if r.get('id')==run.get('id')),collectors[0] if collectors else {})
    settings = selected.get('collector_args',{})
    result['collectors'] = {key:value for key,value in settings.items() if key in COLLECTOR_FIELDS
        and (type(value) is bool or type(value) in (int,float) and math.isfinite(value))}
    profile = run.get('starting_allocation',{}).get('profile')
    if type(profile) is str and re.fullmatch('[a-z0-9][a-z0-9_.-]{0,63}',profile):
        result['resource_profile'] = profile
    digest = spec.get('model_digest')
    if type(digest) is str and re.fullmatch('sha256:[a-f0-9]{64}',digest):
        result['model_image'] = digest
    release = parameters.get('model_release')
    if type(release) is str and re.fullmatch('release-[a-f0-9]{64}',release):
        result['model_release'] = release
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,help='Existing VM TOML configuration')
    parser.add_argument('--frontend',type=Path,help='Existing JAS-mine-web checkout for local use')
    parser.add_argument('--state',type=Path,help='Existing local private MultiRun state')
    parser.add_argument('--dsn-file',type=Path,help='Existing private local PostgreSQL credential file')
    parser.add_argument('--pool',default='simpaths-local')
    parser.add_argument('--schema',default='jasmine_batch')
    commands=parser.add_subparsers(dest='command',required=True)
    enabled=commands.add_parser('enable',help='Start a fresh review window without restarting services')
    enabled.add_argument('--sample-seconds',type=int,default=15)
    enabled.add_argument('--retention-days',type=int,default=90)
    enabled.add_argument('--max-rows',type=int,default=10000)
    commands.add_parser('disable',help='Stop new sampling; retain completed review records')
    java=commands.add_parser('java-enable',help='Observe Java in newly launched processes; running processes unchanged')
    java.add_argument('--sample-seconds',type=int,default=15)
    commands.add_parser('java-disable',help='Omit the detailed observer from newly launched processes')
    prepare=commands.add_parser('prepare-java',help='Compile the optional observer once, outside user jobs')
    prepare.add_argument('--model-jar',type=Path,required=True)
    platform=commands.add_parser('platform',help='Record non-secret provider and deployment context')
    platform.add_argument('--provider',required=True)
    platform.add_argument('--plan',required=True)
    platform.add_argument('--storage-kind',choices=('ssd','nvme','hdd','unknown'),default='unknown')
    platform.add_argument('--deployment-fingerprint')
    report=commands.add_parser('report',help='Export labelled private timelines and all recent attempts')
    report.add_argument('--limit',type=int,default=1000)
    report.add_argument('--output',type=Path,required=True)
    report.add_argument('--html',action='store_true')
    status=commands.add_parser('status',help='Inspect requested/applied controls and recorded attempts')
    status.add_argument('--json',action='store_true')
    status.add_argument('--limit',type=int,default=100)
    args=parser.parse_args(argv)
    if args.config and any((args.frontend,args.state,args.dsn_file,args.pool!='simpaths-local',args.schema!='jasmine_batch')):
        parser.error('--config cannot be combined with local connection settings')
    if not args.config and (not args.frontend or not args.state):
        parser.error('Supply --config, or --frontend and the existing --state')
    try:
        if args.config:
            from .vm_config import load_config
            options=load_config(args.config)
            state,frontend,pool,schema=options.state,options.frontend,options.pool_id,'jasmine_batch'
        else:
            state,frontend,pool,schema=args.state,args.frontend,args.pool,args.schema
        sys.path.insert(0,str(frontend.resolve(strict=True)))
        from jasmine_web.batch.performance_review import set_control, read_control, read_status, history
        if args.command=='prepare-java':
            from .java_review import prepare
            prepare(state,args.model_jar)
            print('Optional Java observer prepared and verified. No running jobs changed.')
            return 0
        if args.command=='platform':
            from jasmine_web.batch.local_executor import atomic_json
            from jasmine_web.batch.performance_review import root_path
            values={k:getattr(args,k) for k in ('provider','plan','storage_kind','deployment_fingerprint') if getattr(args,k) is not None}
            if any(not re.fullmatch('[A-Za-z0-9_.:+ -]{1,96}',v) for v in values.values()):raise ValueError('Invalid platform description')
            if values.get('deployment_fingerprint') is not None and not re.fullmatch('[a-f0-9]{64}',values['deployment_fingerprint']):
                raise ValueError('Use a SHA-256 deployment fingerprint')
            atomic_json(root_path(state)/'performance-platform.json',values)
            print('Private platform descriptors saved. Running jobs unchanged.')
            return 0
        if args.command in ('enable','disable','java-enable','java-disable'):
            changes=(dict(sample_seconds=args.sample_seconds,retention_days=args.retention_days,max_rows=args.max_rows)
                     if args.command=='enable' else dict(sample_seconds=args.sample_seconds) if args.command=='java-enable' else {})
            control=set_control(state,args.command in ('enable','java-enable'),java=args.command.startswith('java-'),**changes)
            print(('Java review for new processes ' if args.command.startswith('java-') else 'Performance review ')+('enabled' if control['enabled'] else 'disabled')+
                  f"; sample interval {control['sample_seconds']} seconds. Running jobs are unchanged. "+
                  ('New Java launches take this setting when they start.' if args.command.startswith('java-') else
                   'The live private service applies this control within approximately five seconds.'))
            return 0
        control,applied=read_control(state),read_status(state)
        report=dict(control=control,java_launch_control=read_control(state,java=True),collector=applied,
                    control_acknowledged=applied.get('recorded',False) and applied['generation']==control['generation'])
        from jasmine_web.batch.performance_exports import control as export_control
        report['monitoring_exports_control']=export_control(state)
        from jasmine_web.batch.performance_trace import read_small
        try:report['timeline_collector']=json.loads(read_small(state,'performance-trace-status.json'))
        except FileNotFoundError:report['timeline_collector']=dict(recorded=False)
        try:
            if args.config:
                from .vm_web import database_dsn
                dsn=database_dsn(options)
            elif args.dsn_file:
                from .vm_web import private_text
                dsn=private_text(args.dsn_file)
            else:
                from jasmine_web.batch.local_database import existing_local_postgres
                dsn=existing_local_postgres(state)
            from jasmine_web.batch.store import Queue
            queue=Queue(dsn,pool,schema=schema)
            report['history']=history(queue,limit=args.limit,workload=workload)
            from jasmine_web.batch.performance_trace import trace_history,platform_snapshot
            report['timeline']=trace_history(queue,limit=args.limit)
            report['platform']=platform_snapshot(state,application_code_fingerprint=application_fingerprint())
            if args.command=='report':
                from .performance_report import render
                body=render(report) if args.html else json.dumps(report,indent=2,sort_keys=True,allow_nan=False)
                import os
                descriptor=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(descriptor,'w') as target:target.write(body+'\n')
                print('Private labelled performance report written: '+str(args.output))
                return 0
        except Exception as error:
            report['history']=dict(unavailable=True,error_type=type(error).__name__)
        if args.command=='report':
            raise RuntimeError('Review report unavailable; apply migrations and inspect private service status')
        if args.json:
            print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))
        else:
            print('Performance review requested: '+('enabled' if control['enabled'] else 'disabled'))
            print('Current control acknowledged: '+('yes' if report['control_acknowledged'] else 'not yet recorded'))
            history=report['history']
            if history.get('unavailable'):
                print('Review history: unavailable')
            else:
                print(f"Retained attempt windows: {history['retained_rows']}; showing {len(history['attempts'])}")
                print(history['interpretation'])
                for row in history['attempts']:
                    disk=row['peak_storage_bytes']
                    print(f"{row['attempt_id']}: {row['state']}; {row['samples']} samples; "
                          f"peak working RAM {row['peak_working_memory_bytes']/2**30:.3f} GiB; "
                          'peak workspace '+(f'{disk/2**30:.3f} GiB' if disk is not None else 'unknown'))
                print('Use status --json to export workload counts, limits, growth and timing records.')
        return 2 if report['history'].get('unavailable') else 0
    except Exception as error:
        # Connection/file exceptions may contain credentials or private paths.
        print('Performance review command failed: '+type(error).__name__+'. Check the existing configuration and private state.',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
