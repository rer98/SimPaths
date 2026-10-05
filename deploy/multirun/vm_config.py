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


DEFAULTS = {
    'service': {'origin': None, 'port': 5002, 'pool_id': 'simpaths-online',
                'dedicated_storage': True},
    'paths': {'frontend': None, 'private_root': None, 'state': None,
              'dsn_file': None, 'prepared': []},
    'model': {'image': None},
    'pool': {'cpu_millis': 3000, 'memory_mib': 6144, 'storage_mib': 13312,
             'per_user_active': 1, 'hold_cpu_millis': 0, 'hold_memory_mib': 0, 'hold_storage_mib': 0},
    'limits': {'max_configurations': 100, 'max_repetitions': 12,
               'max_unfinished_jobs': 110, 'pool_unfinished_jobs': 220,
               'runtime_setup_minutes': 15, 'runtime_per_repetition_minutes': 60,
               'runtime_budget_multiplier': 3, 'upload_allowance_gib': 4},
    'downloads': {'threshold_mib': 512, 'cache_gib': 10, 'cache_hours': 24},
    'notifications': {'enabled': False, 'retention_cleanup': False, 'admin_email': ''},
    'visualiser': {'build': '', 'preview': False, 'memory_mib': 1024, 'timeout_seconds': 600},
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
            if default is not None and type(values[key]) is not type(default):
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
    deployment_configuration_limit(limits['max_configurations'])
    deployment_repetition_limit(limits['max_repetitions'])
    for key, low, high in (('max_unfinished_jobs', 1, 10000), ('pool_unfinished_jobs', 1, 10000),
            ('runtime_setup_minutes', 0, 1440), ('runtime_per_repetition_minutes', 1, 1440),
            ('runtime_budget_multiplier', 1, 3), ('upload_allowance_gib', 1, 1024)):
        number(limits[key], key, low, high)
    if not limits['max_configurations'] <= limits['max_unfinished_jobs'] <= limits['pool_unfinished_jobs']:
        raise ValueError('Configuration and unfinished-job allowances must increase in that order')
    pool = sections['pool']
    for key, minimum in (('cpu_millis', 2000), ('memory_mib', 5120), ('storage_mib', 12288)):
        number(pool[key], 'pool.'+key, minimum, 2**31-1)
    for key, minimum in (('cpu_millis', 2000), ('memory_mib', 5120), ('storage_mib', 12288)):
        number(pool['hold_'+key], 'pool.hold_'+key, 0, pool[key]-minimum)
    number(pool['per_user_active'], 'pool.per_user_active', 1, 100)
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
    visualiser = sections['visualiser']
    if bool(visualiser['build']) != visualiser['preview']:
        raise ValueError('Visualiser preview requires both a build and explicit preview setting')
    if visualiser['build']:
        visualiser['build'] = absolute(visualiser['build'], 'visualiser.build')
        if visualiser['build'].is_relative_to(root) or root.is_relative_to(visualiser['build']):
            raise ValueError('Install the public Visualiser build outside private storage')
    number(visualiser['memory_mib'], 'visualiser.memory_mib', 512, 4096)
    number(visualiser['timeout_seconds'], 'visualiser.timeout_seconds', 60, 3600)
    if visualiser['preview'] and pool['memory_mib'] < visualiser['memory_mib']:
        raise ValueError('Visualiser reservation exceeds pool memory')
    # Check the largest calculated runtime without importing the hosting repository.
    attempt = 60*(limits['runtime_setup_minutes'] + limits['max_repetitions']*limits['runtime_per_repetition_minutes'])
    if attempt > 90*86400 or attempt*limits['runtime_budget_multiplier'] > 270*86400:
        raise ValueError('Calculated runtime exceeds the technical execution ceiling')
    quota_socket=absolute(sections['workspaces']['quota_socket'],'workspaces.quota_socket')
    if len(os.fsencode(quota_socket))>100:
        raise ValueError('workspaces.quota_socket exceeds the Unix socket path bound')
    options = SimpleNamespace(**limits, **paths, **service, image=image,
        workspace_quota_socket=quota_socket,
        workspace_guard=absolute(sections['workspaces']['guard'],'workspaces.guard'),
        download_threshold_mib=downloads['threshold_mib'], download_cache_gib=downloads['cache_gib'],
        download_cache_hours=downloads['cache_hours'], notification_emails=notifications['enabled'],
        retention_cleanup=notifications['retention_cleanup'], admin_email=notifications['admin_email'] or None,
        visualiser_build=visualiser['build'] or None, visualiser_preview=visualiser['preview'],
        visualiser_memory_mib=visualiser['memory_mib'], visualiser_timeout_seconds=visualiser['timeout_seconds'],
        capacity={k: pool[k] for k in ('cpu_millis', 'memory_mib', 'storage_mib')},
        holdback={k: pool['hold_'+k] for k in ('cpu_millis', 'memory_mib', 'storage_mib')},
        per_user_active=pool['per_user_active'], command='serve', email=None)
    return options
