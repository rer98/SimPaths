"""(C) Copyright 2026, by Ross Richardson

Read-only SimPaths Online operator status for existing laptop or VM services.
Reuses verified release and pool inventories without launching or reconciling work.
@author ross richardson
"""
import argparse
from datetime import datetime, timezone
import json
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
    if (type(value) is not dict or set(value) != {'format','pool_id','schema','recorded_at',
                'model_release','notification_delivery','retention_cleanup','visualiser_enabled'}
            or value['format']!='simpaths.operator.settings.v1'
            or (value['pool_id'],value['schema'])!=(pool_id,schema)
            or any(type(value[k]) is not bool for k in ('notification_delivery','retention_cleanup','visualiser_enabled'))
            or not isinstance(value['recorded_at'],str)):
        raise ValueError('Invalid launch settings')
    from .releases import IDENTIFIER
    if not isinstance(value['model_release'],str) or not IDENTIFIER.fullmatch(value['model_release']):
        raise ValueError('Invalid launch release')
    recorded = datetime.fromisoformat(value['recorded_at'])
    if recorded.tzinfo is None:
        raise ValueError('Invalid launch timestamp')
    return dict(available=True, recorded_at=recorded.isoformat(), model_release=value['model_release'],
        notification_delivery=value['notification_delivery'],retention_cleanup=value['retention_cleanup'],
        visualiser_enabled=value['visualiser_enabled'])


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
        for kind,row in pool['reservations'].items():
            lines.append(f"  {kind}: {row['count']} reservation(s); "+allocation(row))
        lines.append('Jobs: '+(', '.join(f"{r['kind']}/{r['state']} {r['count']}" for r in db['jobs']['counts']) or 'none'))
        for row in db['jobs']['details']:
            lines.append(f"  {literal(row['id'])}: {row['kind']}, {row['state']}; {row['reason']}; attempts {row['attempts']}"+
                         (f"; last outcome {row['last_outcome']}" if row['last_outcome'] else ''))
            if row.get('storage_wait'):
                wait=row['storage_wait']
                lines.append('    Disk at last check: needs '+gib(wait['required_bytes'])+'; available '+gib(wait['available_bytes']))
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
    models=report['models']
    if models:
        lines.append(f"Model releases: {len(models['releases'])} retained; selected default {models['selected_default']}")
        for row in models['releases']:
            policy=row['resource_policy']['simulation']['storage']
            lines.append('  '+literal(row['name'])+f" [{row['id']}]: {policy['setup_mib']/1024:g} GiB fixed + {policy['per_repetition_mib']} MiB/repetition")
        if last.get('available') and last['model_release']!=models['selected_default']:
            lines.append('  Selected default differs from last application launch; restart loads the selection for new work')
    else:
        lines.append('Model releases: unavailable or invalid')
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


if __name__=='__main__':
    raise SystemExit(main())
