"""(C) Copyright 2026, by Ross Richardson

Read-only SimPaths Online operator status for existing laptop or VM services.
Reuses verified release and pool inventories without launching or reconciling work.
@author ross richardson
"""
import argparse
from datetime import datetime, timezone
import json
import os
import stat
import threading
import time
from pathlib import Path
import sys

from .releases import ReleaseRegistry, existing_directory, read_json

FORMAT = 'simpaths.operator.status.v1'


def release_inventory(state):
    retained = ReleaseRegistry(state).inventory()
    default = next(iter(retained))
    return dict(selected_default=default, releases=[dict(id=key, name=item['name'],
        image=item['image'],model_sha256=item['identity']['model']['sha256'],
        resource_policy=item['resource_policy']) for key,item in retained.items()])


def launch_settings(state, pool_id, schema):
    """Last successful assembly settings, not a claim that its process is alive."""
    try:
        value = read_json(Path(state)/'operator-settings.json')
    except FileNotFoundError:
        return dict(available=False)
    required={'format','pool_id','schema','recorded_at','model_release',
              'notification_delivery','retention_cleanup','visualiser_enabled'}
    flags=('adaptive_cpu','visualiser_automatic','visualiser_handover','lend_idle_interactive_cpu')
    numbers=('visualiser_workers','visualiser_seed_workers','growth_memory_mib')
    if (type(value) is not dict or not required<=set(value) or set(value)-required-set(flags)-set(numbers)
            or value['format']!='simpaths.operator.settings.v1'
            or (value['pool_id'],value['schema'])!=(pool_id,schema)
            or any(type(value[k]) is not bool for k in ('notification_delivery','retention_cleanup','visualiser_enabled'))
            or not isinstance(value['recorded_at'],str)):
        raise ValueError('Invalid launch settings')
    if (any(type(value[key]) is not bool for key in flags if key in value)
            or any(type(value[key]) is not int or value[key]<0 for key in numbers if key in value)):
        raise ValueError('Invalid launch scheduling settings')
    from .releases import IDENTIFIER
    if not isinstance(value['model_release'],str) or not IDENTIFIER.fullmatch(value['model_release']):
        raise ValueError('Invalid launch release')
    recorded = datetime.fromisoformat(value['recorded_at'])
    if recorded.tzinfo is None:
        raise ValueError('Invalid launch timestamp')
    return dict(available=True, recorded_at=recorded.isoformat(), model_release=value['model_release'],
        notification_delivery=value['notification_delivery'],retention_cleanup=value['retention_cleanup'],
        visualiser_enabled=value['visualiser_enabled'],
        **{key:value[key] for key in (*flags,*numbers) if key in value})


def collect(queue, state, *, limit=20, read_database=True):
    """No service constructors, secret creation, database writes or cleanup."""
    from jasmine_web.batch.operator_status import database_inventory, filesystem_inventory
    existing_directory(state)
    result = dict(format=FORMAT, read_only=True, complete=True,
        measured_at=datetime.now(timezone.utc).isoformat(), warnings=[])
    locations = []
    try:
        if not read_database:
            raise RuntimeError('Database connection unavailable')
        result['database'], locations = database_inventory(queue,limit=limit)
        if any(not row['binding_verified'] for row in result['database']['singlerun']['registries']):
            result['complete']=False
            result['warnings'].append('singlerun_registry_unavailable_or_mismatched')
    except Exception:
        result.update(database=None, complete=False)
        result['warnings'].append('database_unavailable_or_schema_not_current')
    try:
        if result['database'] is None:
            raise RuntimeError('Database connection unavailable')
        from jasmine_web.batch.observability import history
        result['metrics']=history(queue,model_limit=limit)
    except Exception:
        result['metrics']=None
        result['warnings'].append('operational_history_unavailable')
        result['complete']=False
    try:
        result['storage'] = filesystem_inventory(state,locations)
        if result['database'] is None:
            result['storage']['prepared_datasets'] = None
        areas=result['storage']['areas_bytes']
        caches=(result['storage']['downloads'],result['storage']['visualiser'])
        if (any(size is None for size in areas.values()) or result['storage']['state_filesystem'] is None
                or any(disk is None for disk in result['storage']['area_filesystems'].values())
                or any(cache.get('unavailable') or cache['invalid'] for cache in caches)
                or result['storage']['prepared_datasets'] and result['storage']['prepared_datasets']['unmeasured_locations']):
            result['complete']=False
            result['warnings'].append('some_storage_could_not_be_measured')
    except Exception:
        result.update(storage=None,complete=False)
        result['warnings'].append('storage_inventory_unavailable')
    from .visualiser_cache_usage import statistics
    from jasmine_web.batch.storage_budget import read_control as budget_control, read_status as budget_status
    try:
        result['storage_budget']=dict(control=budget_control(state, queue=queue), status=budget_status(state))
        if result['storage_budget']['status'].get('allowed') is False:
            result['complete']=False
            result['warnings'].append('storage_budget_waiting_for_operator')
    except Exception:
        result['storage_budget']=dict(available=False)
        result['complete']=False
        result['warnings'].append('storage_budget_status_unavailable')
    result['summary_cache_usage'] = statistics(Path(state)/'execution/visualiser-cache/summaries')
    if result['summary_cache_usage']['recorded'] and not result['summary_cache_usage']['available']:
        result['complete'] = False
        result['warnings'].append('summary_cache_usage_unavailable')
    from .visualiser_summary_holds import statistics as retention_statistics
    result['summary_retention'] = retention_statistics(Path(state)/'execution/visualiser-cache/summaries')
    if result['summary_retention']['recorded'] and not result['summary_retention']['available']:
        result['complete'] = False
        result['warnings'].append('summary_retention_unavailable')
    elif result['summary_retention'].get('deferred_comparisons') or result['summary_retention'].get('deferred_configurations'):
        result['warnings'].append('summary_retention_waiting_for_capacity')
    try:
        from jasmine_web.batch.visualiser_storage import read_status
        result['visualiser_storage']=read_status(Path(state)/'execution/visualiser-cache')
    except (OSError,ValueError,KeyError,TypeError):
        result['visualiser_storage']=dict(recorded=True,available=False)
        result['warnings'].append('visualiser_storage_control_unavailable')
        result['complete']=False
    try:
        result['models'] = release_inventory(state)
    except Exception:
        result.update(models=None,complete=False)
        result['warnings'].append('release_inventory_unavailable_or_invalid')
    try:
        result['last_launch'] = launch_settings(state,queue.pool_id,queue.schema)
    except Exception:
        result.update(last_launch=dict(available=False),complete=False)
        result['warnings'].append('launch_settings_unavailable_or_invalid')
    return result


def literal(value):
    """Keep terminal labels literal, including escape/control characters."""
    return ''.join(c if c.isprintable() else '?' for c in str(value))


def gib(value):
    return 'unknown' if value is None else f'{value/2**30:.2f} GiB'


def allocation(value):
    return f"{value['cpu_millis']/1000:g} CPU, {value['memory_mib']/1024:g} GiB RAM, {value['storage_mib']/1024:g} GiB storage"


def render(report):
    lines=['SimPaths Online operator status (read-only)', 'Measured: '+report['measured_at']]
    db=report['database']
    if db:
        pool=db['resources']
        lines += ['Pool: '+literal(db['pool_id']),
            '  Capacity: '+allocation(pool['capacity']), '  Reserved: '+allocation(pool['reserved']),
            '  Available to batch: '+allocation(pool['available_for_batch']),
            '  Interactive holdback: '+allocation(pool['interactive_holdback'])]
        loan=pool.get('idle_interactive_cpu')
        compute=pool.get('compute_capacity',{})
        if compute.get('enabled'):
            lines.append('  Live CPU/RAM approvals: enabled; new admissions '+
                ('allowed' if compute['admission_allowed'] else 'paused for host capacity review'))
            observation=compute['observation']
            if observation.get('host'):
                host=observation['host']
                lines.append(f"    Detected common execution capacity: {host['cpu_millis']/1000:g} CPU; {host['memory_mib']/1024:.2f} GiB RAM")
            lines.append(f"    Outside-pool headroom: {compute['headroom']['cpu_millis']/1000:g} CPU; {compute['headroom']['memory_mib']/1024:.2f} GiB RAM")
        if loan:
            lines.append('  Idle SingleRun CPU loans: '+('enabled' if loan['enabled'] else 'disabled')+
                f"; borrowed {loan['borrowed_millis']/1000:g} CPU; recall "+
                ('pending' if loan['recall_pending'] else 'clear'))
        for kind,row in pool['reservations'].items():
            lines.append(f"  {kind}: {row['count']} reservation(s); "+allocation(row))
        lines.append('Jobs: '+(', '.join(f"{r['kind']}/{r['state']} {r['count']}" for r in db['jobs']['counts']) or 'none'))
        for row in db['jobs']['details']:
            lines.append(f"  {literal(row['id'])}: {row['kind']}, {row['state']}; {row['reason']}; attempts {row['attempts']}"+
                         (f"; last outcome {row['last_outcome']}" if row['last_outcome'] else ''))
            if row.get('storage_wait'):
                wait=row['storage_wait']
                lines.append('    Disk at last check: needs '+gib(wait['required_bytes'])+'; available '+gib(wait['available_bytes']))
            if row.get('resource_recovery'):
                recovery=row['resource_recovery']
                lines.append('    Allocation: '+allocation(row['resources'])+
                    (f"; Java heap {recovery['heap_mib']/1024:g} GiB" if recovery['heap_mib'] is not None else '')+
                    ('; resource change pending, reservation retained' if recovery['change_pending'] else ''))
                if recovery['wait']:
                    lines.append('    Resource increase: '+literal(recovery['wait']['kind'])+'; '+literal(recovery['wait']['reason']))
        if db['jobs']['details_truncated']:
            lines.append(f"  Showing {len(db['jobs']['details'])} of {db['jobs']['unfinished']} unfinished jobs; increase --limit for more.")
        lines.append(f"Attempts: {db['attempts']['unfinished']} unfinished; {db['attempts']['expired_leases']} expired lease(s), reservations retained")
        for registry in db['singlerun']['registries']:
            states=', '.join(f"{r['status']} {r['count']}" for r in registry['states'] or []) or 'state unknown'
            lines.append(f"SingleRun registry: {literal(registry['schema'])}; {states}"+
                         ('' if registry['binding_verified'] else '; binding/session records need inspection'))
        cleanup=db['cleanup']
        lines += [f"Cleanup: {cleanup['outputs']['pending']} output deletion(s) pending, {cleanup['outputs']['failed']} failed; "
                  f"{cleanup['attempts']['scratch_pending']} failed-attempt cleanup(s) pending, {cleanup['attempts']['failed']} flagged; "
                  f"{cleanup['attempts']['diagnostics_due']} diagnostic expiry(s) due",
                  'Automatic expiry deletion recorded in database: '+('unknown' if db['retention']['cleanup_enabled'] is None
                       else 'enabled' if db['retention']['cleanup_enabled'] else 'disabled')]
        for row in db['retention']['inventory']:
            if row['state']=='live':
                lines.append(f"  Retention/{row['kind']}: {row['due']} due, {row['protected']} protected")
        for kind,row in db['notifications'].items():
            lines.append(f"Email/{kind}: {row['pending']} pending, {row['awaiting_retry']} awaiting retry, {row['claimed']} claimed")
    else:
        lines.append('Database: unavailable; job/resource/notification counts unknown')
    last=report['last_launch']
    lines.append('Email delivery at last application launch: '+('enabled' if last.get('notification_delivery') else
                 'disabled' if last.get('available') else 'unknown (no launch settings recorded)'))
    if last.get('available'):
        lines.append('  Launch settings recorded: '+last['recorded_at']+'; process availability is not inferred')
    storage=report['storage']
    if storage:
        disk=storage['state_filesystem']
        lines.append('State filesystem: '+(gib(disk['free_bytes'])+' free of '+gib(disk['total_bytes']) if disk else 'unknown'))
        for kind,disk in storage['area_filesystems'].items():
            lines.append('  '+kind+' filesystem: '+(gib(disk['free_bytes'])+' free' if disk else 'unknown'))
        lines.append('Measured file sizes (categories overlap):')
        for kind,size in storage['areas_bytes'].items():
            lines.append('  '+kind+': '+gib(size))
        prepared=storage['prepared_datasets']
        lines.append('  Prepared datasets: '+(gib(prepared['bytes'])+f" across {prepared['locations']} distinct location(s)" if prepared else 'unknown'))
        for kind in ('downloads','visualiser'):
            cache=storage[kind]
            lines.append('  '+kind+': '+gib(cache['bytes'])+'; recorded states '+
                         (', '.join(f'{key} {n}' for key,n in cache['recorded_states'].items() if n) or 'none')+
                         f"; {cache['expired']} expired receipt(s), {cache['invalid']} invalid")
    else:
        lines.append('Storage measurements: unknown')
    budget=report.get('storage_budget',{})
    control=budget.get('control')
    if control:
        lines.append('Scheduler storage budget: '+control['mode']+
                     f"; {control['headroom_mib']/1024:g} GiB filesystem headroom; "+
                     (f"{control['ceiling_mib']/1024:g} GiB ceiling" if control['ceiling_mib'] else 'no configured ceiling'))
        status=budget['status']
        if status.get('recorded'):
            lines.append('  Last budget check: '+status['checked_at']+
                         ('; new batch admissions paused ('+status['last_error']+')' if not status['allowed'] else '; valid'))
    else:
        lines.append('Scheduler storage budget: '+('unavailable' if budget.get('available') is False
                     else 'fixed startup configuration; live control not enabled'))
    usage = report.get('summary_cache_usage', {})
    if usage.get('available'):
        counters = usage['counters']
        lines.append(f"Summary cache: {counters['hits']} hits, {counters['misses']} misses, "
                     f"{counters['evictions']} capacity evictions")
        lines.append(f"  Raw parsing attempts: {counters['raw_started']}; after known eviction: "
                     f"{counters['eviction_raw_started']} ({counters['eviction_raw_completed']} completed, "
                     f"{counters['eviction_raw_failed']} failed); "
                     f"{counters['eviction_raw_elapsed_ns']/1e9:.1f}s; "
                     f"{gib(counters['eviction_raw_input_bytes'])} logical CSV input")
        lines.append('  Eviction attribution retains at most 1,024 markers for 30 days; '
                     f"{counters['history_expired']} aged out, {counters['history_overflow']} overflowed")
        if usage['unfinished_raw_starts']:
            lines.append(f"  {usage['unfinished_raw_starts']} raw attempts have no completion record (running or interrupted)")
    else:
        lines.append('Summary cache usage: unavailable' if usage.get('recorded') else 'Summary cache usage: not recorded')
    retained = report.get('summary_retention', {})
    if retained.get('available'):
        lines.append(f"Summary retention: {retained['admitted_comparisons']} pending comparisons admitted; "
                     f"{retained['retained_seed_slots']} shared seed slots; "
                     f"{retained['deferred_comparisons']} comparisons waiting for storage")
        lines.append(f"  Completed configurations retained: {retained.get('admitted_configurations',0)}; "
                     f"{retained.get('deferred_configurations',0)} waiting for retention space")
    else:
        lines.append('Summary retention: unavailable' if retained.get('recorded') else 'Summary retention: not recorded')
    models=report['models']
    if models:
        lines.append(f"Model releases: {len(models['releases'])} retained; selected default {models['selected_default']}")
        for row in models['releases']:
            profiles=row['resource_policy'].get('profiles')
            if profiles:
                lines.append('  '+literal(row['name'])+f" [{row['id']}]: {len(profiles)} tested workload profiles")
                continue
            policy=row['resource_policy']['simulation']['storage']
            lines.append('  '+literal(row['name'])+f" [{row['id']}]: {policy['setup_mib']/1024:g} GiB fixed + {policy['per_repetition_mib']} MiB/repetition")
        if last.get('available') and last['model_release']!=models['selected_default']:
            lines.append('  Selected default differs from last application launch; restart loads the selection for new work')
    else:
        lines.append('Model releases: unavailable or invalid')
    metrics=report.get('metrics')
    if metrics:
        collector=metrics['collector']
        state=('stale' if collector and collector['stale'] else 'recent samples' if collector else 'not recorded')
        lines.append(f'Operational history: {state}; {metrics["days"]} days; CPU/RAM/storage are actual use, not reservations')
        if metrics['row_cap_reached']:
            lines.append('  Row cap reached; oldest retained bucket '+str(metrics['oldest_retained_bucket'])+'; historical coverage may be shortened')
        for row in metrics['measurements']:
            if row['kind'] in ('host','model') and row['metric'] != 'unavailable':
                lines.append(f'  {row["kind"]} {row["subject"]} {row["metric"]}: peak {row["peak"]:.1f}%; '
                    f'>=80% {row["above80_seconds"]:.0f}s; >=90% {row["above90_seconds"]:.0f}s; sampled {row["measured_seconds"]:.0f}s')
            elif row['kind']=='http':
                unit=' ms' if row['metric'].endswith('_ms') else ''
                lines.append(f'  HTTP {row["subject"]} {row["metric"]}: {row["count"]} observations; '
                    f'mean {row["mean"]:.1f}{unit}; peak {row["peak"]:.1f}{unit}')
            else:
                lines.append(f'  {row["kind"]} {row["metric"]}: {row["total"]:g}')
        jobs=metrics['jobs']
        lines.append(f'  Configurations requested {jobs["requested"]}, succeeded {jobs["succeeded"]}, not started {jobs["not_started"]}')
        for field in ('mean_queue_seconds','max_queue_seconds','mean_completion_seconds'):
            value=jobs[field]
            lines.append('  '+field+': '+(f'{value:.1f}s' if value is not None else 'unknown'))
        for row in metrics['recovery']:
            lines.append(f'  Recovery {row["kind"]} {row["action"]}: {row["count"]}')
        for row in metrics['outcomes']:
            lines.append(f'  Attempt outcome {row["outcome"]}: {row["count"]}')
        if metrics['models_truncated']:
            lines.append('  Model detail truncated; raise --limit to inspect more attempts')
    else:
        lines.append('Operational history: unavailable')
    if report['warnings']:
        lines.append('Incomplete sections: '+', '.join(report['warnings']))
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,help='Existing native VM TOML; reads its private database credential file')
    parser.add_argument('--frontend',type=Path,help='JAS-mine-web checkout for laptop inspection')
    parser.add_argument('--state',type=Path,help='Existing laptop state; default: ~/simpaths-multirun-local')
    parser.add_argument('--dsn-file',type=Path,help='Private credential file; otherwise inspect the running local database')
    parser.add_argument('--pool',help='Existing laptop pool; default: simpaths-local')
    parser.add_argument('--schema',help='Existing laptop queue schema; default: jasmine_batch')
    parser.add_argument('--limit',type=int,default=20,help='Maximum unfinished job details (1–200; counts remain complete)')
    parser.add_argument('--json',action='store_true',help='Machine-readable simpaths.operator.status.v1 report')
    args=parser.parse_args(argv)
    if not 1<=args.limit<=200:
        parser.error('--limit must be 1..200')
    if args.config and any((args.frontend,args.state,args.dsn_file,args.pool,args.schema)):
        parser.error('--config cannot be combined with laptop connection settings')
    if not args.config and not args.frontend:
        parser.error('Supply --config for the VM or --frontend for an existing laptop service')
    try:
        if args.config:
            from .vm_config import load_config
            options=load_config(args.config)
            state,frontend,pool,schema=options.state,options.frontend,options.pool_id,'jasmine_batch'
        else:
            state,frontend=args.state or Path.home()/'simpaths-multirun-local',args.frontend
            pool,schema=args.pool or 'simpaths-local',args.schema or 'jasmine_batch'
        existing_directory(state)
        sys.path.insert(0,str(frontend.resolve(strict=True)))
        from jasmine_web.batch.store import Queue
        read_database=True
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
        except Exception:
            dsn=''
            read_database=False
        report=collect(Queue(dsn,pool,schema=schema),state,limit=args.limit,read_database=read_database)
    except Exception:
        # Database errors and filesystem exceptions can contain credentials,
        # owner names or private paths. Report a fixed diagnostic only.
        report=dict(format=FORMAT,read_only=True,complete=False,
            measured_at=datetime.now(timezone.utc).isoformat(),warnings=['operator_status_unavailable'],
            database=None,storage=None,models=None,last_launch=dict(available=False))
    print(json.dumps(report,sort_keys=True,indent=2,allow_nan=False) if args.json else render(report),end='\n' if args.json else '')
    return 0 if report['complete'] else 2


# The administrator reader exposes bounded numeric storage observations only.
ADMINISTRATOR_STORAGE_FORMAT = 'simpaths.administrator.storage.v1'
AREA_IDS = ('job_workspaces', 'uploads', 'prepared_inputs', 'visualiser',
            'visualiser_summaries', 'downloads', 'backups')
MAX_NUMBER = 2**63-1


def _number(value):
    if type(value) is not int or not 0 <= value <= MAX_NUMBER:
        raise ValueError('Invalid storage counter')
    return value


def _timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Expected an aware storage timestamp')
    return parsed.isoformat()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _directory(path):
    """Open every configured directory component without following links."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Require a configured absolute directory')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def _filesystem(path):
    fd = _directory(path)
    try:
        before = os.fstat(fd)
        space = os.fstatvfs(fd)
        after = os.fstat(fd)
        if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or space.f_frsize <= 0 or space.f_blocks <= 0
                or not 0 <= space.f_bavail <= space.f_bfree <= space.f_blocks):
            raise ValueError('Filesystem capacity changed or is unavailable')
        return before.st_dev, dict(available=True,
            total_bytes=_number(space.f_blocks*space.f_frsize),
            used_bytes=_number((space.f_blocks-space.f_bfree)*space.f_frsize),
            available_bytes=_number(space.f_bavail*space.f_frsize))
    finally:
        os.close(fd)


def _backup_snapshot(config_path):
    """Reuse the installed schedule's validation; export an explicit subset."""
    from .backup_schedule import FORMAT as backup_format, load_config, report
    empty = dict(available=False, configured=False, pending_stage=None,
                 last_success_at=None, overdue=None, remote_configured=None,
                 minimum_free_bytes=None)
    if config_path is None:
        return None, empty
    path = Path(config_path)
    try:
        path.lstat()
    except FileNotFoundError:
        return None, empty
    empty['configured'] = True
    root = None
    try:
        config = load_config(path)
        root = Path(config['backup']['directory'])
        # The normal backup reader permits large integrity metadata. This page
        # needs only the small schedule record, bounded on the opened descriptor
        # so an atomic replacement cannot bypass a prior path-size check.
        directory = _directory(root)
        try:
            try:
                fd = os.open('status.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
            except FileNotFoundError:
                status = dict(format=backup_format, current=None, last_success=None, incident=None)
            else:
                with os.fdopen(fd, 'rb') as source:
                    info = os.fstat(source.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or info.st_uid != os.getuid() or info.st_mode & 0o077
                            or info.st_size > 128*1024):
                        raise ValueError('Invalid bounded backup status')
                    body = source.read(128*1024+1)
                    if len(body) > 128*1024:
                        raise ValueError('Backup status exceeds its observation bound')
                    status = json.loads(body)
        finally:
            os.close(directory)
        if (not isinstance(status, dict) or status.get('format') != backup_format
                or set(status) != {'format', 'current', 'last_success', 'incident'}):
            raise ValueError('Invalid backup schedule observation')
        value = report(status, config=config)
        latest = value.get('latest')
        last = _timestamp(latest['finished_at']) if latest else None
        stage = value.get('pending_stage')
        if stage not in (None, 'capturing', 'sealing', 'copying', 'complete', 'local-cleanup'):
            raise ValueError('Invalid backup stage')
        overdue = value.get('overdue')
        if type(overdue) is not bool:
            raise ValueError('Invalid backup freshness')
        minimum = _number(config['backup']['minimum_free_gib']*1024**3)
        return root, dict(available=True, configured=True, pending_stage=stage,
            last_success_at=last, overdue=overdue,
            remote_configured=bool(config.get('remote')), minimum_free_bytes=minimum)
    except Exception:
        return root, empty


class AdministratorStorage:
    """Private read callback; constructing or viewing it never controls work.

    Category totals use allocated regular-file blocks, including working input
    copies in job workspaces. Visualiser summaries are a subset of Visualiser
    storage. Neither category totals nor scheduler promises are free disk space.
    """
    def __init__(self, service, state, executor, *,
                 backup_config=Path('/etc/simpaths-online/backup.toml'),
                 cache_seconds=300, scan_seconds=5, max_entries=200000):
        if (type(cache_seconds) not in (int, float) or not 1 <= cache_seconds <= 3600
                or type(scan_seconds) not in (int, float) or not 0 < scan_seconds <= 30
                or type(max_entries) is not int or not 1 <= max_entries <= 200000):
            raise ValueError('Invalid bounded storage observation settings')
        self.service, self.state, self.executor = service, Path(state), executor
        self.backup_config = backup_config
        self.cache_seconds, self.scan_seconds, self.max_entries = cache_seconds, scan_seconds, max_entries
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._binding = None
        self._sample = None
        self._sampled = None

    def _roots(self, backup):
        visualiser = getattr(self.service, 'visualiser', None)
        summaries = getattr(getattr(visualiser, 'backend', None), 'summary_cache', None)
        downloads = getattr(getattr(self.service, 'results', None), 'downloads', None)
        # Generated input datasets live under artifacts. Imported provider and
        # training datasets can live elsewhere; filesystem totals include them,
        # but this category deliberately does not claim a full dataset inventory.
        return dict(job_workspaces=Path(self.executor.root),
            uploads=Path(self.service.datasets.root), prepared_inputs=self.state/'artifacts',
            visualiser=Path(visualiser.root) if visualiser is not None else None,
            visualiser_summaries=Path(summaries.root) if summaries is not None else None,
            downloads=Path(downloads.root) if downloads is not None else None,
            backups=backup)

    def _scan_tree(self, root, excluded, deadline, count, *, entry_limit=None):
        fd = _directory(root)
        device = os.fstat(fd).st_dev
        maximum = self.max_entries if entry_limit is None else min(self.max_entries, entry_limit)
        def walk(directory, relative=(), depth=0):
            if depth > 64:
                raise ValueError('Storage tree exceeds its depth bound')
            total = 0
            with os.scandir(directory) as entries:
                for entry in entries:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Storage sample reached its time bound')
                    count[0] += 1
                    if count[0] > maximum:
                        raise ValueError('Storage sample exceeds its entry bound')
                    current = (*relative, entry.name)
                    if current in excluded:
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if info.st_dev != device:
                        raise ValueError('Storage tree contains another filesystem')
                    if stat.S_ISREG(info.st_mode):
                        if info.st_nlink != 1:
                            raise ValueError('Storage tree contains linked files')
                        total += _number(info.st_blocks*512)
                    elif stat.S_ISDIR(info.st_mode):
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=directory)
                        try:
                            opened = os.fstat(child)
                            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                                raise ValueError('Storage directory changed during observation')
                            total += walk(child, current, depth+1)
                        finally:
                            os.close(child)
                    else:
                        raise ValueError('Storage tree contains an unsupported entry')
            return _number(total)
        try:
            return walk(fd)
        finally:
            os.close(fd)

    def _scan(self, roots, binding):
        measured = {}
        deadline, count = time.monotonic()+self.scan_seconds, [0]
        # Small caches/backup archive inventories should remain useful when a
        # large job tree cannot be measured within this observer's total budget.
        # Each category receives a share of the remaining time and entries;
        # inexpensive categories leave their unused share for later ones.
        order = ('backups', 'downloads', 'visualiser_summaries', 'visualiser',
                 'uploads', 'prepared_inputs', 'job_workspaces')
        remaining = sum(root is not None for root in roots.values())
        for area in order:
            root = roots[area]
            size, complete = None, False
            if root is not None:
                excluded = set()
                if area == 'job_workspaces':
                    for child in (roots['visualiser'], roots['downloads']):
                        if child is not None:
                            try:
                                relative = child.relative_to(root).parts
                                if relative:
                                    excluded.add(relative)
                            except ValueError:
                                pass
                try:
                    now = time.monotonic()
                    entries = self.max_entries-count[0]
                    if now >= deadline or entries <= 0:
                        raise TimeoutError('Storage sample reached its time bound')
                    area_deadline = min(deadline, now+(deadline-now)/remaining)
                    entry_limit = count[0]+max(1, entries//remaining)
                    size = self._scan_tree(root, excluded, area_deadline, count,
                                           entry_limit=entry_limit)
                    complete = True
                except Exception:
                    pass
                remaining -= 1
            measured[area] = dict(id=area, bytes=size, complete=complete,
                                  measured_at=_now() if root is not None else None)
        result = [measured[area] for area in roots]
        with self._lock:
            if self._binding == binding:
                self._sample, self._sampled = result, time.monotonic()
            self._running = False

    def _areas(self, roots):
        binding = tuple((key, str(value) if value is not None else None)
                        for key, value in roots.items())
        with self._lock:
            if self._binding != binding:
                self._binding, self._sample, self._sampled = binding, None, None
            if (not self._running and
                    (self._sampled is None or time.monotonic()-self._sampled >= self.cache_seconds)):
                self._running = True
                self._thread = threading.Thread(target=self._scan, args=(dict(roots), binding),
                                                name='administrator-storage-observer', daemon=True)
                try:
                    self._thread.start()
                except Exception:
                    self._running = False
            sample = ([dict(row) for row in self._sample] if self._sample is not None else
                      [dict(id=area, bytes=None, measured_at=None, complete=False) for area in AREA_IDS])
            state = ('pending' if self._running else
                     'ready' if all(row['complete'] or roots[row['id']] is None for row in sample)
                     else 'incomplete')
        return sample, state

    def _allowances(self, roots):
        result = dict(visualiser_bytes=None, summary_bytes=None, download_bytes=None)
        if roots['visualiser'] is not None:
            try:
                from jasmine_web.batch.visualiser_storage import read_status
                value = read_status(roots['visualiser'])
                if value.get('available') is True and value.get('recorded') is True:
                    budget = value['budget']
                    result.update(visualiser_bytes=_number(budget['capacity_bytes']),
                                  summary_bytes=_number(budget['summary_bytes']))
            except Exception:
                pass
        try:
            downloads = self.service.results.downloads
            result['download_bytes'] = _number(downloads.capacity)
        except Exception:
            pass
        return result

    def _commitments(self):
        result = dict(available=False, measured_at=None, workspace_remaining_bytes=None,
            workspace_safety_bytes=None, visualiser_safety_bytes=None, visualiser_helper_bytes=None)
        try:
            broker = self.executor.workspace_quotas
            value = broker.ready()
            result.update(workspace_remaining_bytes=_number(value['remaining_reserved_bytes']),
                          workspace_safety_bytes=_number(value['reserve_bytes']),
                          available=True, measured_at=_now())
        except Exception:
            pass
        try:
            policy = self.service.visualiser.disk_storage
            # Do not call physical(): DiskHeadroom also opens a pool transaction.
            result['visualiser_safety_bytes'] = _number(policy.physical.reserve)
            result['visualiser_helper_bytes'] = _number(policy._reserve())
        except Exception:
            pass
        return result

    def __call__(self):
        backup_root, backup = _backup_snapshot(self.backup_config)
        roots = self._roots(backup_root)
        areas, state = self._areas(roots)
        filesystems, devices, references = [], {}, {}
        for area, root in [('system', Path('/')), *roots.items()]:
            if root is None:
                references[area] = None
                continue
            try:
                device, values = _filesystem(root)
                existing = devices.get(device)
                if existing is not None:
                    existing['areas'].append(area)
                    references[area] = existing['id']
                    continue
                row = dict(id='fs-'+str(len(filesystems)+1), areas=[area], **values)
                devices[device] = row
            except Exception:
                row = dict(id='fs-'+str(len(filesystems)+1), areas=[area], available=False,
                           total_bytes=None, used_bytes=None, available_bytes=None)
            filesystems.append(row)
            references[area] = row['id'] if row['available'] else None
        for row in areas:
            row['filesystem'] = references[row['id']]
        result = dict(format=ADMINISTRATOR_STORAGE_FORMAT, observed_at=_now(), measure_state=state,
            filesystems=filesystems, areas=areas, allowances=self._allowances(roots),
            commitments=self._commitments(), backup=backup)
        # A final defensive format bound protects the private-RPC response even
        # when a future internal dependency adds unexpected fields.
        if len(json.dumps(result, allow_nan=False).encode()) > 16384:
            raise ValueError('Storage observation exceeds its response bound')
        return result


if __name__=='__main__':
    raise SystemExit(main())
