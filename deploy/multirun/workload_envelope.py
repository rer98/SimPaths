"""(C) Copyright 2026, by Ross Richardson
Public MultiRun admission bounds and measured, conservative starting resources.
SingleRun profiles and already recorded execution allocations remain independent.
@author ross richardson
"""
from copy import deepcopy

from .schema import DeploymentLimitError

MAX_POPULATION = 100000
MAX_END_YEAR = 2070
EXTENDED_REPETITION_STORAGE_MIB = 4096


def validate_workload(common, path='common'):
    if common['population'] > MAX_POPULATION or common['end_year'] > MAX_END_YEAR:
        reason = 'population' if common['population'] > MAX_POPULATION else 'horizon'
        raise DeploymentLimitError(path,
            'this deployment supports up to 100,000 people and a last year of 2070',reason)


def extended(common):
    return common['population'] > 50000 or common['end_year'] > 2026


def initial_heap_mib(common):
    """Use the measured reference with headroom for variable CPU and settings.

    The quiet campaigns used 4/5 GiB for the complete envelope and also passed
    smaller fixed allocations at two CPUs. Retain the established short-run
    heaps, use 3 GiB for long 20k runs and 4 GiB above 50k. The corresponding
    existing 4/5 GiB containers leave at least 1 GiB native-memory headroom.
    """
    if common['population'] > 50000:
        return 4096
    return 3072 if common['population'] > 20000 or common['end_year'] > 2026 else 2048


def validate_resource_envelope(common, policy):
    """Do not admit newly enabled work with the old short-preview disk slope.

    Explicit versioned workload profiles retain their own trusted envelopes.
    Existing short jobs retain their original policy, heap and allocations.
    """
    from .resource_policy import FORMAT, check_policy
    checked=check_policy(policy)
    if (extended(common) and checked['format']==FORMAT
            and checked['simulation']['storage']['per_repetition_mib'] < EXTENDED_REPETITION_STORAGE_MIB):
        raise DeploymentLimitError('resources',
            'Choose inputs prepared with the larger-run model release. This older input release '
            'retains its original storage allowance and cannot admit the extended workload.','resources')


def extended_resource_policy(previous):
    """Measured starting allocations for a new immutable preview release.

    The 20 October campaign measured two sequential repetitions at two CPUs.
    Later repetitions get an additional 512 MiB heap/container margin; that is
    an alpha-preview allowance, not a claim of measured ten-repetition stability.
    Storage separates a 5 GiB setup term from a per-repetition allowance above
    the observed CSV totals. Recovery can still increase the recorded resources.
    Other collector combinations use the conservative 4/5 GiB reference so the
    preview does not prohibit optional outputs simply because no small profile
    was measured for that combination. Scientific settings are not changed.
    """
    from .artifacts import ArtifactError
    from .resource_policy import FORMAT, MEASURED_PROFILE_FORMAT, check_policy
    from .schema import COLLECTOR_FIELDS
    result=deepcopy(check_policy(previous))
    if result['format']!=FORMAT:
        raise ArtifactError('Preserve explicit workload profiles; review them separately')
    storage=result['simulation']['storage']
    storage['setup_mib']=max(storage['setup_mib'],5120)
    storage['per_repetition_mib']=max(storage['per_repetition_mib'],5120)
    collectors={key:field.default for key,field in COLLECTOR_FIELDS.items()}
    result['format']=MEASURED_PROFILE_FORMAT
    result['profiles']=[]
    # population, inclusive simulation years, heap, container, output term MiB
    cases=((20000,17,2560,3584,512),(20000,52,2560,3584,1024),
           (50000,17,3072,4096,1024),(50000,52,3072,4096,2560),
           (100000,17,3072,4096,1536),(100000,52,3584,4608,4608))
    for population,years,heap,memory,output in cases:
        for repetitions,margin in ((2,0),(10,512)):
            result['profiles'].append(dict(id=f'laptop-20261020-{population}-{years}-r{repetitions}',
                max_population=population,max_years=years,max_repetitions=repetitions,
                collectors=deepcopy(collectors),cpu_millis=2000,memory_mib=memory+margin,
                heap_mib=heap+margin,native_headroom_mib=1024,
                storage=dict(setup_mib=storage['setup_mib'],per_repetition_mib=output)))
    result['fallback']=dict(id='extended-reference-4g-5g',max_population=MAX_POPULATION,
        max_years=60,max_repetitions=10,collectors=None,cpu_millis=2000,
        memory_mib=5120,heap_mib=4096,native_headroom_mib=1024,storage=deepcopy(storage))
    return check_policy(result)
