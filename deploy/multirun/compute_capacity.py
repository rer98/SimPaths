"""(C) Copyright 2026, by Ross Richardson

Private administrator commands for installed CPU/RAM detection and live approval.
Status is read-only; preview records a proposal; only apply changes pool budgets.
@author ross richardson
"""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys


def bootstrap(args, queue, capacity):
    from jasmine_web.batch.compute_capacity import initialise, observe
    settings = getattr(args,'compute_capacity',{})
    if settings.get('enabled',False):
        initialise(queue,capacity,**{k:settings[k] for k in
            ('cgroup','reserve_cpu_millis','reserve_memory_mib')})
    # Existing approvals are revalidated even if the initial opt-in is later
    # omitted. Restore onto a smaller host cannot silently resume admission.
    observe(queue)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True,help='Installed private VM configuration')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('status',help='Show approved budgets, observations and recent proposals')
    commands.add_parser('check',help='Refresh host metadata and record any administrator notice; never approve')
    child=commands.add_parser('preview',help='Record a proposal without changing budgets or workloads')
    child.add_argument('--cpu-millis',type=int,help='Optional chosen CPU budget below the safe maximum')
    child.add_argument('--memory-mib',type=int,help='Optional chosen RAM budget below the safe maximum')
    child=commands.add_parser('apply',help='Approve exactly one previously reviewed proposal')
    child.add_argument('--proposal',required=True,help='UUID returned by preview')
    args=parser.parse_args(argv)
    try:
        from .privilege_config import trusted_installation
        from .vm_config import load_config
        from .vm_web import check_storage,database_dsn
        trusted_installation(args.config)
        options=load_config(args.config)
        check_storage(options)
        sys.path.insert(0,str(options.frontend.resolve(strict=True)))
        from jasmine_web.batch.store import Queue
        from jasmine_web.batch.backup import database_guard
        from jasmine_web.batch import compute_capacity as budget
        queue=Queue(database_dsn(options),options.pool_id)
        # No migrations, service construction, Docker commands or SMTP sends.
        from .maintenance import service_state
        with database_guard(queue), (nullcontext() if args.command=='status' else service_state(options.state)):
            if args.command=='status':
                result=budget.status(queue)
            elif args.command=='check':
                budget.observe(queue)
                result=budget.status(queue)
            elif args.command=='preview':
                result=budget.preview(queue,cpu_millis=args.cpu_millis,memory_mib=args.memory_mib)
            else:
                result=budget.apply(queue,args.proposal)
        print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))
        return 0
    except Exception as error:
        print('Compute-capacity operation failed: '+type(error).__name__+
              '. Inspect the private configuration, host capacity and current proposal.',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
