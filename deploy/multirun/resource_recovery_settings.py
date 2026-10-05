"""(C) Copyright 2026, by Ross Richardson

Secret-free operator settings for opt-in MultiRun resource recovery.
@author ross richardson
"""
DEFAULTS=dict(enabled=False,pressure_percent=85,memory_step_mib=1024,max_memory_mib=8192,
              heap_step_mib=1024,max_heap_mib=4096,storage_step_percent=25,
              storage_multiplier=2,check_seconds=10)


def validate(settings):
    if type(settings.get('enabled')) is not bool:
        raise ValueError('Recovery must be explicitly enabled by the operator')
    bounds=dict(pressure_percent=(50,95),storage_step_percent=(1,100),storage_multiplier=(1,4),
                check_seconds=(5,300))
    for key,value in settings.items():
        if key=='enabled': continue
        low,high=bounds.get(key,(1,1048576))
        if type(value) is not int or not low<=value<=high:
            raise ValueError('Invalid resource recovery setting: '+key)
    return settings


def arguments(parser):
    parser.add_argument('--resource-recovery',dest='recovery_enabled',action='store_true',
                        help='Enable bounded live growth and resource-specific retries for new reviews')
    for key,value in DEFAULTS.items():
        if key!='enabled':
            parser.add_argument('--recovery-'+key.replace('_','-'),type=int,default=value,
                                help='Operator recovery setting (default: %(default)s)')


def policy(args):
    settings=validate({key:getattr(args,'recovery_'+key,value) for key,value in DEFAULTS.items()})
    if not settings.pop('enabled'):
        return None
    from jasmine_web.batch.resource_recovery import RecoveryPolicy
    return RecoveryPolicy(**settings)
