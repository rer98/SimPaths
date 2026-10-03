"""(C) Copyright 2026, by Ross Richardson

Versioned model resource policies, copied into immutable release/dataset receipts.
The initial policy preserves existing allocations. Storage calibration is separate.
@author ross richardson
"""
from copy import deepcopy

from .artifacts import ArtifactError


FORMAT = 'simpaths.multirun.resources.v1'
DEFAULT_POLICY = dict(format=FORMAT,
    preparation=dict(cpu_millis=2000, memory_mib=5120, storage_mib=12288),
    simulation=dict(cpu_millis=2000, memory_mib=4096, large_memory_mib=5120,
                    storage=dict(setup_mib=10240, per_repetition_mib=0)))


def check_policy(policy):
    """Only bounded operator settings; no allocations are accepted from browsers."""
    if (type(policy) is not dict or set(policy) != {'format', 'preparation', 'simulation'}
            or policy['format'] != FORMAT or type(policy['preparation']) is not dict
            or set(policy['preparation']) != {'cpu_millis', 'memory_mib', 'storage_mib'}
            or type(policy['simulation']) is not dict
            or set(policy['simulation']) != {'cpu_millis', 'memory_mib', 'large_memory_mib', 'storage'}
            or type(policy['simulation']['storage']) is not dict
            or set(policy['simulation']['storage']) != {'setup_mib', 'per_repetition_mib'}):
        raise ArtifactError('Unsupported model resource policy')
    prep, run = policy['preparation'], policy['simulation']
    values = [(prep[k], v) for k, v in DEFAULT_POLICY['preparation'].items()]
    large_minimum=max(5120,run['memory_mib']) if type(run['memory_mib']) is int else 5120
    values += [(run['cpu_millis'], 2000), (run['memory_mib'], 4096),
               (run['large_memory_mib'], large_minimum),
               (run['storage']['setup_mib'], 10240), (run['storage']['per_repetition_mib'], 0)]
    if any(type(value) is not int or type(low) is not int or not low <= value <= 2**31-1
           for value, low in values):
        raise ArtifactError('Model resource policy is below supported minima or exceeds its bounds')
    return deepcopy(policy)


def simulation_resources(policy, *, population, repetitions):
    checked = check_policy(policy)['simulation']
    if type(population) is not int or population < 1 or type(repetitions) is not int or not 1 <= repetitions <= 1000:
        raise ArtifactError('Invalid population or repetition count for model resources')
    storage = checked['storage']['setup_mib'] + repetitions * checked['storage']['per_repetition_mib']
    if storage > 2**31-1:
        raise ArtifactError('Calculated model storage exceeds the allocation ceiling')
    return dict(cpu_millis=checked['cpu_millis'],
                memory_mib=checked['large_memory_mib'] if population > 20000 else checked['memory_mib'],
                storage_mib=storage)
