"""(C) Copyright 2026, by Ross Richardson

Strict, secret-free TOML settings for the native SimPaths Online VM service.
Configuration checks have no database, Docker, SMTP or filesystem side effects.
@author ross richardson
"""
from pathlib import Path
import os
import re
from types import SimpleNamespace
import tomllib
from urllib.parse import urlsplit

from .schema import deployment_configuration_limit, deployment_repetition_limit
from .resource_recovery_settings import DEFAULTS as RECOVERY_DEFAULTS, validate as validate_recovery


DEFAULTS = {
    'rpc': {'socket': '/run/simpaths-executor/control.sock', 'frontend_user': 'simpaths-web'},
    'monitoring': {'enabled': False, 'sample_seconds': 15, 'retention_days': 30, 'max_rows': 20000},
    'compute_capacity': {'enabled': False, 'cgroup': '/sys/fs/cgroup/system.slice',
                         'reserve_cpu_millis': 0, 'reserve_memory_mib': 0},
    'monitoring_exports': {'enabled': False, 'mail_enabled': False, 'archive_seconds': 3600,
        'delivery_seconds': 86400, 'journal_mib': 64, 'journal_rows': 100000,
        'archive_mib': 512, 'archive_days': 30, 'part_mib': 2},
    'recovery': RECOVERY_DEFAULTS,
    'service': {'origin': None, 'port': 5002, 'pool_id': 'simpaths-online',
                'dedicated_storage': True},
    'paths': {'frontend': None, 'private_root': None, 'state': None,
              'dsn_file': None, 'prepared': []},
    'model': {'image': None},
    'pool': {'cpu_millis': 3000, 'memory_mib': 6144, 'storage_mib': 13312,
             'per_user_active': 1, 'hold_cpu_millis': 0, 'hold_memory_mib': 0, 'hold_storage_mib': 0,
             'growth_memory_mib': 0},
    'scheduling': {'adaptive_cpu': False, 'cpu_cooldown_seconds': 30, 'lend_idle_interactive_cpu': False,
                   'preparation_priority_seconds': 60},
    'limits': {'max_configurations': 100, 'max_repetitions': 12,
               'max_unfinished_jobs': 110, 'pool_unfinished_jobs': 220,
               'runtime_setup_minutes': 15, 'runtime_per_repetition_minutes': 60,
               'runtime_budget_multiplier': 3, 'upload_allowance_gib': 4},
    'downloads': {'threshold_mib': 512, 'cache_gib': 10, 'cache_hours': 24},
    'notifications': {'enabled': False, 'retention_cleanup': False, 'admin_email': '',
                      'storage_wait_emails': False, 'storage_wait_hours': 6},
    'visualiser': {'build': '', 'preview': False, 'memory_mib': 1024, 'timeout_seconds': 600,
                  'automatic': False, 'max_workers': 1, 'seed_workers': 1, 'handover': False,
                  'cpu_millis': 1000, 'storage_mib': 512,
                  'protected': False, 'hard_limits': False, 'bounded_comparisons': False,
                  'summaries': False, 'summary_cache_mib': 2048, 'cache_mib': 256,
                  'retain_completed_summaries': True, 'summary_hours': 168,
                  'storage_pressure': False, 'cache_max_mib': 0, 'summary_cache_max_mib': 0,
                  'idle_hours': 72, 'warning_seconds': 300, 'filesystem_headroom_mib': 16384,
                  'cache_hours': 168, 'max_entries': 512, 'per_owner_entries': 256,
                  'artifact_mib': 16, 'comparison_mib': 64,
                  'rows_per_series': 50000, 'comparison_rows': 200000},
    'workspaces': {'quota_socket': '/run/jasmine-workspace-quotas/broker.sock',
                   'guard': '/usr/local/libexec/jasmine-workspace-guard'},
}


def number(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in {minimum}..{maximum}')
    return value


def absolute(value, name):
    if (not isinstance(value, str) or not value.startswith('/') or
            any(c in value for c in ('\x00', '\n', '\r', ',')) or '..' in Path(value).parts):
        raise ValueError(f'{name} must be an absolute path without parent traversal')
    return Path(value)


def load_config(path):
    path = Path(path)
    if path.stat().st_size > 65536:
        raise ValueError('VM configuration exceeds 64 KiB')
    with path.open('rb') as source:
        supplied = tomllib.load(source)
    if set(supplied) - set(DEFAULTS):
        raise ValueError('Unknown VM configuration section')
    sections = {}
    for name, defaults in DEFAULTS.items():
        values = supplied.get(name, {})
        if not isinstance(values, dict) or set(values) - set(defaults):
            raise ValueError(f'Unknown setting in {name}')
        sections[name] = {**defaults, **values}
    for name, values in sections.items():
        for key, default in DEFAULTS[name].items():
            if default is None and not values[key]:
                raise ValueError(f'{name}.{key} is required')
            decimal_multiplier=name=='recovery' and key=='storage_multiplier' and type(values[key]) in (int,float)
            if default is not None and type(values[key]) is not type(default) and not decimal_multiplier:
                raise ValueError(f'Invalid type for {name}.{key}')
    service, paths = sections['service'], sections['paths']
    origin = service['origin']
    if not isinstance(origin, str) or any(ord(c) < 33 for c in origin):
        raise ValueError('Use a canonical HTTPS origin')
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or
            parsed.fragment or parsed.username or parsed.password or
            not re.fullmatch(r'[a-z0-9.-]+(?::[0-9]+)?', parsed.netloc)):
        raise ValueError('Use a canonical lowercase HTTPS origin without a path or credentials')
    if parsed.port is not None:
        number(parsed.port, 'HTTPS port', 1, 65535)
        if parsed.port == 443:
            raise ValueError('Omit the default HTTPS port from the canonical origin')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', service['pool_id']):
        raise ValueError('Use a bounded pool identifier')
    number(service['port'], 'service.port', 1024, 65535)
    image = sections['model']['image']
    if not isinstance(image, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('model.image must be an installed immutable Docker image ID')
    for key in ('frontend', 'private_root', 'state', 'dsn_file'):
        paths[key] = absolute(paths[key], 'paths.'+key)
    root, state = paths['private_root'], paths['state']
    if root == Path('/') or state == root or not state.is_relative_to(root):
        raise ValueError('State must be a subdirectory of a dedicated private root')
    if any(root == p or root.is_relative_to(p) for p in map(Path, ('/tmp', '/run', '/dev', '/proc', '/sys'))):
        raise ValueError('Use persistent private storage outside temporary/system directories')
    source_root = Path(__file__).resolve().parents[2]
    if any(root.is_relative_to(code) or code.is_relative_to(root) for code in (paths['frontend'], source_root)):
        raise ValueError('Private storage must be outside source and public asset directories')
    if any(paths['dsn_file'].is_relative_to(code) for code in (paths['frontend'], source_root)):
        raise ValueError('Keep the database credential file outside source/public directories')
    if (any(not isinstance(p, str) for p in paths['prepared']) or
            len(paths['prepared']) > 8 or len(set(paths['prepared'])) != len(paths['prepared'])):
        raise ValueError('Supply at most eight distinct public training imports')
    paths['prepared'] = [absolute(p, 'paths.prepared') for p in paths['prepared']]
    if any(p == root or not p.is_relative_to(root) or p.is_relative_to(state) for p in paths['prepared']):
        raise ValueError('Training imports must be under private_root, outside the service state')
    limits = sections['limits']
    validate_recovery(sections['recovery'])
    monitoring = sections['monitoring']
    for key, low, high in (('sample_seconds',5,300),('retention_days',1,90),('max_rows',100,100000)):
        number(monitoring[key],'monitoring.'+key,low,high)
    export=sections['monitoring_exports']
    for key,low,high in (('archive_seconds',30,86400),('delivery_seconds',60,604800),
            ('journal_mib',1,1024),('journal_rows',100,1000000),('archive_mib',1,10240),
            ('archive_days',1,365),('part_mib',1,3)):
        number(export[key],'monitoring_exports.'+key,low,high)
    if export['mail_enabled'] and (not export['enabled'] or not sections['notifications']['enabled'] or not sections['notifications']['admin_email']):
        raise ValueError('Monitoring export mail requires exports, notifications and a private admin recipient')
    deployment_configuration_limit(limits['max_configurations'])
    deployment_repetition_limit(limits['max_repetitions'])
    for key, low, high in (('max_unfinished_jobs', 1, 10000), ('pool_unfinished_jobs', 1, 10000),
            ('runtime_setup_minutes', 0, 1440), ('runtime_per_repetition_minutes', 1, 1440),
            ('runtime_budget_multiplier', 1, 3), ('upload_allowance_gib', 1, 1024)):
        number(limits[key], key, low, high)
    if not limits['max_configurations'] <= limits['max_unfinished_jobs'] <= limits['pool_unfinished_jobs']:
        raise ValueError('Configuration and unfinished-job allowances must increase in that order')
    pool = sections['pool']
    compute = sections['compute_capacity']
    scope = absolute(compute['cgroup'],'compute_capacity.cgroup')
    if not scope.is_relative_to('/sys/fs/cgroup') or len(scope.parts)>20:
        raise ValueError('Use the common execution cgroup below /sys/fs/cgroup')
    for key in ('reserve_cpu_millis','reserve_memory_mib'):
        number(compute[key],'compute_capacity.'+key,0,2**31-1)
    for key, minimum in (('cpu_millis', 2000), ('memory_mib', 5120), ('storage_mib', 12288)):
        number(pool[key], 'pool.'+key, minimum, 2**31-1)
    for key, minimum in (('cpu_millis', 2000), ('memory_mib', 5120), ('storage_mib', 12288)):
        number(pool['hold_'+key], 'pool.hold_'+key, 0, pool[key]-minimum)
    number(pool['per_user_active'], 'pool.per_user_active', 1, 100)
    number(pool['growth_memory_mib'], 'pool.growth_memory_mib', 0, pool['memory_mib']-5120)
    if pool['per_user_active'] > limits['max_unfinished_jobs']:
        raise ValueError('Active allowance exceeds unfinished-job allowance')
    downloads = sections['downloads']
    for key, maximum in (('threshold_mib', 1048576), ('cache_gib', 1024), ('cache_hours', 168)):
        number(downloads[key], 'downloads.'+key, 1, maximum)
    notifications = sections['notifications']
    if notifications['retention_cleanup'] and not notifications['enabled']:
        raise ValueError('Automatic expiry requires notification delivery')
    if notifications['enabled'] and not notifications['admin_email']:
        raise ValueError('Notification delivery requires an administrator recipient')
    number(notifications['storage_wait_hours'], 'notifications.storage_wait_hours', 1, 720)
    if notifications['storage_wait_emails'] and not notifications['enabled']:
        raise ValueError('Storage-wait emails require notification delivery')
    visualiser = sections['visualiser']
    scheduling = sections['scheduling']
    if type(scheduling['lend_idle_interactive_cpu']) is not bool:
        raise ValueError('Choose explicitly whether to lend idle SingleRun CPU')
    if scheduling['lend_idle_interactive_cpu'] and (not scheduling['adaptive_cpu'] or pool['hold_cpu_millis']<500):
        raise ValueError('Idle SingleRun CPU lending needs adaptive CPU and a protected CPU holdback')
    number(scheduling['cpu_cooldown_seconds'], 'scheduling.cpu_cooldown_seconds', 5, 300)
    number(scheduling['preparation_priority_seconds'], 'scheduling.preparation_priority_seconds', 5, 300)
    if scheduling['adaptive_cpu'] and (limits['runtime_per_repetition_minutes'] < 240 or
                                       limits['runtime_setup_minutes'] < 15):
        raise ValueError('Adaptive CPU needs runtime allowances validated for its 1-vCPU floor')
    if bool(visualiser['build']) != visualiser['preview']:
        raise ValueError('Visualiser preview requires both a build and explicit preview setting')
    if visualiser['build']:
        visualiser['build'] = absolute(visualiser['build'], 'visualiser.build')
        if visualiser['build'].is_relative_to(root) or root.is_relative_to(visualiser['build']):
            raise ValueError('Install the public Visualiser build outside private storage')
    number(visualiser['memory_mib'], 'visualiser.memory_mib', 512, 4096)
    number(visualiser['timeout_seconds'], 'visualiser.timeout_seconds', 60, 3600)
    for key, low, high in (('max_workers',1,2), ('seed_workers',1,2), ('cpu_millis',1000,2000),
            ('storage_mib',512,16384), ('summary_cache_mib',1,65536), ('cache_mib',48,131072),
            ('cache_hours',1,8760), ('summary_hours',1,8760), ('max_entries',1,4096), ('per_owner_entries',1,4096),
            ('cache_max_mib',0,131072), ('summary_cache_max_mib',0,65536),
            ('idle_hours',1,8760), ('warning_seconds',1,3600), ('filesystem_headroom_mib',1024,131072),
            ('artifact_mib',1,128), ('comparison_mib',1,256),
            ('rows_per_series',1,200000), ('comparison_rows',1,400000)):
        number(visualiser[key], 'visualiser.'+key, low, high)
    if (visualiser['cpu_millis'] % 500 or
            visualiser['per_owner_entries'] > visualiser['max_entries'] or
            visualiser['comparison_mib'] < visualiser['artifact_mib'] or
            visualiser['comparison_rows'] < visualiser['rows_per_series'] or
            visualiser['cache_mib'] < 3*visualiser['artifact_mib']):
        raise ValueError('Inconsistent bounded Visualiser budgets')
    enhanced=any(visualiser[key] for key in ('automatic','protected','hard_limits','bounded_comparisons','summaries','storage_pressure'))
    if enhanced and not visualiser['preview']:
        raise ValueError('Preparation options require an explicitly enabled Visualiser build')
    if visualiser['automatic'] and not all(visualiser[key] for key in
            ('protected','hard_limits','bounded_comparisons','summaries')):
        raise ValueError('Automatic preparation requires protected, limited workers and bounded reusable summaries')
    if visualiser['max_workers'] > 1 and not (visualiser['automatic'] and visualiser['hard_limits']):
        raise ValueError('A second aggregate worker requires bounded automatic preparation')
    if visualiser['seed_workers']>1 and not all(visualiser[k] for k in ('automatic','hard_limits','summaries')):
        raise ValueError('Parallel seeds require bounded automatic preparation with reusable summaries')
    if visualiser['handover'] and not all(visualiser[k] for k in ('automatic','hard_limits','summaries')):
        raise ValueError('Resource handover requires bounded automatic preparation with reusable summaries')
    if visualiser['storage_pressure']:
        maximum=visualiser['cache_max_mib'] or visualiser['cache_mib']
        summary_maximum=visualiser['summary_cache_max_mib'] or visualiser['summary_cache_mib']
        if (not visualiser['hard_limits'] or not visualiser['summaries'] or
                maximum<visualiser['cache_mib'] or
                not visualiser['summary_cache_mib']<=summary_maximum<=min(maximum,65536) or
                visualiser['cache_mib']<max(visualiser['summary_cache_mib'],3*visualiser['artifact_mib'])+
                    visualiser['max_workers']*(visualiser['storage_mib']+2*visualiser['comparison_mib'])):
            raise ValueError('Storage pressure management needs bounded summaries and consistent approved ceilings')
    if visualiser['seed_workers']>1 and (pool['cpu_millis']-pool['hold_cpu_millis']<2000 or
            pool['memory_mib']-pool['hold_memory_mib']-pool['growth_memory_mib']<5120):
        raise ValueError('Parallel seed processing needs an admissible 5 GiB / 2-vCPU group')
    if visualiser['automatic']:
        minimum_cache=visualiser['summary_cache_mib']+visualiser['max_workers']*visualiser['storage_mib']+3*visualiser['artifact_mib']
        if visualiser['cache_mib'] < minimum_cache:
            raise ValueError('Visualiser cache must cover summaries, active workspaces and publications')
    for key in ('cpu_millis','memory_mib','storage_mib'):
        floor=visualiser[key] if visualiser['protected'] else 0
        growth=pool['growth_memory_mib'] if key=='memory_mib' else 0
        minimum={'cpu_millis':1000 if scheduling['adaptive_cpu'] else 2000,
                 'memory_mib':5120,'storage_mib':12288}[key]
        if pool[key]-pool['hold_'+key]-floor-growth < minimum:
            raise ValueError('Protected allocations must leave capacity for a model configuration')
    if visualiser['preview'] and pool['memory_mib'] < visualiser['memory_mib']:
        raise ValueError('Visualiser reservation exceeds pool memory')
    # Check the largest calculated runtime without importing the hosting repository.
    attempt = 60*(limits['runtime_setup_minutes'] + limits['max_repetitions']*limits['runtime_per_repetition_minutes'])
    if attempt > 90*86400 or attempt*limits['runtime_budget_multiplier'] > 270*86400:
        raise ValueError('Calculated runtime exceeds the technical execution ceiling')
    quota_socket=absolute(sections['workspaces']['quota_socket'],'workspaces.quota_socket')
    if len(os.fsencode(quota_socket))>100:
        raise ValueError('workspaces.quota_socket exceeds the Unix socket path bound')
    rpc_socket=absolute(sections['rpc']['socket'],'rpc.socket')
    if len(os.fsencode(rpc_socket))>100:
        raise ValueError('rpc.socket exceeds the Unix socket path bound')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',sections['rpc']['frontend_user']):
        raise ValueError('rpc.frontend_user must name a dedicated Linux account')
    options = SimpleNamespace(rpc_socket=rpc_socket,rpc_frontend_user=sections['rpc']['frontend_user'],**limits, **paths, **service, image=image,
        compute_capacity=dict(compute),
        monitoring_exports=dict(export),operational_metrics=monitoring['enabled'],metrics_sample_seconds=monitoring['sample_seconds'],
        metrics_retention_days=monitoring['retention_days'],metrics_max_rows=monitoring['max_rows'],
        **{'recovery_'+key:value for key,value in sections['recovery'].items()},
        workspace_quota_socket=quota_socket,
        workspace_guard=absolute(sections['workspaces']['guard'],'workspaces.guard'),
        download_threshold_mib=downloads['threshold_mib'], download_cache_gib=downloads['cache_gib'],
        download_cache_hours=downloads['cache_hours'], notification_emails=notifications['enabled'],
        retention_cleanup=notifications['retention_cleanup'], admin_email=notifications['admin_email'] or None,
        storage_wait_emails=notifications['storage_wait_emails'], storage_wait_hours=notifications['storage_wait_hours'],
        visualiser_build=visualiser['build'] or None, visualiser_preview=visualiser['preview'],
        visualiser_memory_mib=visualiser['memory_mib'], visualiser_timeout_seconds=visualiser['timeout_seconds'],
        **{'visualiser_'+key:visualiser[key] for key in visualiser if key not in ('build','preview','memory_mib','timeout_seconds')},
        adaptive_cpu=scheduling['adaptive_cpu'],cpu_cooldown_seconds=scheduling['cpu_cooldown_seconds'],
        lend_idle_interactive_cpu=scheduling['lend_idle_interactive_cpu'],
        preparation_priority_seconds=scheduling['preparation_priority_seconds'],
        growth_memory_mib=pool['growth_memory_mib'],
        capacity={k: pool[k] for k in ('cpu_millis', 'memory_mib', 'storage_mib')},
        holdback={k: pool['hold_'+k] for k in ('cpu_millis', 'memory_mib', 'storage_mib')},
        per_user_active=pool['per_user_active'], command='serve', email=None)
    return options
