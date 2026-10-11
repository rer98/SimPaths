"""(C) Copyright 2026, by Ross Richardson

Versioned model resource policies, copied into immutable release/dataset receipts.
New releases scale working storage; legacy releases keep their recorded allowances.
@author ross richardson
"""
from copy import deepcopy
import re

from .artifacts import ArtifactError


FORMAT = 'simpaths.multirun.resources.v1'
PROFILE_FORMAT = 'simpaths.multirun.resources.v2'
LEGACY_POLICY = dict(format=FORMAT,
    preparation=dict(cpu_millis=2000, memory_mib=5120, storage_mib=12288),
    simulation=dict(cpu_millis=2000, memory_mib=4096, large_memory_mib=5120,
                    storage=dict(setup_mib=10240, per_repetition_mib=0)))
DEFAULT_POLICY = deepcopy(LEGACY_POLICY)
DEFAULT_POLICY['simulation']['storage'] = dict(setup_mib=4096, per_repetition_mib=256)


def check_policy(policy):
    """Only bounded operator settings; no allocations are accepted from browsers."""
    profiled = type(policy) is dict and policy.get('format') == PROFILE_FORMAT
    if (type(policy) is not dict or set(policy) != {'format', 'preparation', 'simulation'} | ({'profiles'} if profiled else set())
            or policy['format'] not in (FORMAT,PROFILE_FORMAT) or type(policy['preparation']) is not dict
            or set(policy['preparation']) != {'cpu_millis', 'memory_mib', 'storage_mib'}
            or type(policy['simulation']) is not dict
            or set(policy['simulation']) != {'cpu_millis', 'memory_mib', 'large_memory_mib', 'storage'}
            or type(policy['simulation']['storage']) is not dict
            or set(policy['simulation']['storage']) != {'setup_mib', 'per_repetition_mib'}):
        raise ArtifactError('Unsupported model resource policy')
    prep, run = policy['preparation'], policy['simulation']
    values = [(prep[k], v) for k, v in LEGACY_POLICY['preparation'].items()]
    large_minimum=max(5120,run['memory_mib']) if type(run['memory_mib']) is int else 5120
    values += [(run['cpu_millis'], 2000), (run['memory_mib'], 4096),
               (run['large_memory_mib'], large_minimum),
               (run['storage']['setup_mib'], 4096), (run['storage']['per_repetition_mib'], 0)]
    if any(type(value) is not int or type(low) is not int or not low <= value <= 2**31-1
           for value, low in values):
        raise ArtifactError('Model resource policy is below supported minima or exceeds its bounds')
    if profiled:
        _check_profiles(policy['profiles'])
    return deepcopy(policy)


def _check_profiles(profiles):
    from .schema import COLLECTOR_FIELDS, REQUIRED_OUTPUT
    if type(profiles) is not list or not 1 <= len(profiles) <= 64:
        raise ArtifactError('Supply 1..64 tested workload profiles')
    seen = set()
    for profile in profiles:
        if (type(profile) is not dict or set(profile) != {'id','max_population','max_years','max_repetitions',
                'collectors','cpu_millis','memory_mib','heap_mib','native_headroom_mib','storage'}
                or type(profile['id']) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}',profile['id'])
                or profile['id'] in seen or type(profile['collectors']) is not dict
                or set(profile['collectors']) != set(COLLECTOR_FIELDS)
                or type(profile['storage']) is not dict
                or set(profile['storage']) != {'setup_mib','per_repetition_mib'}):
            raise ArtifactError('Invalid or duplicate workload profile')
        seen.add(profile['id'])
        for key,low,high in (('max_population',1,2**31-1),('max_years',1,1000),
                ('max_repetitions',1,1000),('cpu_millis',2000,2**31-1),('memory_mib',4096,2**31-1),
                ('heap_mib',512,2**31-1),('native_headroom_mib',1024,2**31-1)):
            value=profile[key]
            if type(value) is not int or not low <= value <= high:
                raise ArtifactError('Workload profile exceeds its supported bounds')
        if profile['heap_mib']+profile['native_headroom_mib'] > profile['memory_mib']:
            raise ArtifactError('Workload profile must include native memory headroom')
        for key,low in (('setup_mib',4096),('per_repetition_mib',0)):
            if type(profile['storage'][key]) is not int or not low <= profile['storage'][key] <= 2**31-1:
                raise ArtifactError('Invalid workload storage allowance')
        # Validate the complete collector contract, not just persistPersons.
        for key,field in COLLECTOR_FIELDS.items():
            try:
                field.validate(profile['collectors'][key],'profile.collectors.'+key)
            except ValueError:
                raise ArtifactError('Invalid workload collector settings')
        if any(profile['collectors'][key] != value for key,value in REQUIRED_OUTPUT.items()):
            raise ArtifactError('Workload profile must preserve the required output contract')
    # Envelopes with the same collectors must nest. Otherwise a workload could
    # match two incomparable profiles and depend on arbitrary list ordering.
    axes=('max_population','max_years','max_repetitions')
    for index,left in enumerate(profiles):
        for right in profiles[index+1:]:
            if left['collectors'] != right['collectors']: continue
            less=all(left[k]<=right[k] for k in axes)
            greater=all(left[k]>=right[k] for k in axes)
            if less == greater:
                raise ArtifactError('Workload envelopes are duplicate or ambiguous; use nested bounds')


def workload(data):
    """Normalised execution settings; no inferred or browser-supplied limits."""
    return dict(population=data['common']['population'],
                years=data['common']['end_year']-data['common']['start_year']+1,
                repetitions=data['seed_plan']['repetitions'],collectors=data['run_sets'][0]['collector_args'])


def select_profile(policy, *, population, repetitions, years=None, collectors=None):
    checked=check_policy(policy)
    if checked['format'] != PROFILE_FORMAT:
        return None
    if (type(population) is not int or population<1 or type(repetitions) is not int
            or not 1<=repetitions<=1000 or type(years) is not int or not 1<=years<=1000
            or type(collectors) is not dict):
        raise ArtifactError('Profile selection requires the complete normalised workload')
    matches=[p for p in checked['profiles'] if population<=p['max_population']
             and years<=p['max_years'] and repetitions<=p['max_repetitions'] and collectors==p['collectors']]
    if not matches:
        from jasmine_web.batch.observability import SupportedLimitError
        raise SupportedLimitError('unprofiled','No tested starting allocation covers these population, years, repetitions and output settings. Contact the service operator.')
    return min(matches,key=lambda p:(p['max_population'],p['max_years'],p['max_repetitions']))


def starting_allocation(policy, **context):
    from .artifacts import digest
    profile=select_profile(policy,**context)
    if profile is None: return None
    return dict(version='jasmine.starting.v1',profile=profile['id'],policy_sha256=digest(check_policy(policy)),
                heap_mib=profile['heap_mib'],native_headroom_mib=profile['native_headroom_mib'])


def simulation_storage(policy, *, repetitions, minimum_setup_bytes=0, population=None, years=None, collectors=None):
    profile=select_profile(policy,population=population,repetitions=repetitions,years=years,collectors=collectors)
    checked = profile['storage'] if profile else check_policy(policy)['simulation']['storage']
    if (type(repetitions) is not int or not 1 <= repetitions <= 1000
            or type(minimum_setup_bytes) is not int or minimum_setup_bytes < 0):
        raise ArtifactError('Invalid repetition count or input size for model storage')
    setup = max(checked['setup_mib'], (minimum_setup_bytes + 1024**2 - 1)//1024**2)
    storage = setup + repetitions * checked['per_repetition_mib']
    if storage > 2**31-1:
        raise ArtifactError('Calculated model storage exceeds the allocation ceiling')
    return dict(configured_setup_mib=checked['setup_mib'], setup_mib=setup,
                per_repetition_mib=checked['per_repetition_mib'], repetitions=repetitions,
                storage_mib=storage)


def simulation_resources(policy, *, population, repetitions, minimum_setup_bytes=0, years=None, collectors=None):
    checked = check_policy(policy)['simulation']
    if type(population) is not int or population < 1:
        raise ArtifactError('Invalid population for model resources')
    profile=select_profile(policy,population=population,repetitions=repetitions,years=years,collectors=collectors)
    storage = simulation_storage(policy,repetitions=repetitions,minimum_setup_bytes=minimum_setup_bytes,
                                 population=population,years=years,collectors=collectors)
    return dict(cpu_millis=profile['cpu_millis'] if profile else checked['cpu_millis'],
                memory_mib=profile['memory_mib'] if profile else checked['large_memory_mib'] if population > 20000 else checked['memory_mib'],
                storage_mib=storage['storage_mib'])
