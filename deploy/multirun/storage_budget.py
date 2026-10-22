"""(C) Copyright 2026, by Ross Richardson

Private operator controls for the shared scheduler's live storage allowance.
Filesystem growth can be followed without restarting services or model jobs.
@author ross richardson
"""
import argparse
import json
from pathlib import Path
import sys


def controller(args, queue, state):
    from jasmine_web.batch.storage_budget import StorageBudget
    return StorageBudget(queue, state, getattr(args, 'private_root', None) or Path(state).parent,
                         dedicated=getattr(args, 'dedicated_storage', False))


def bootstrap(args, queue, state):
    return controller(args, queue, state).bootstrap()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='Installed private VM configuration')
    parser.add_argument('--frontend', type=Path)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--private-root', type=Path)
    parser.add_argument('--dsn-file', type=Path)
    parser.add_argument('--pool', default='simpaths-local')
    parser.add_argument('--schema', default='jasmine_batch')
    commands = parser.add_subparsers(dest='command', required=True)
    for command, help_text in (
            ('follow', 'Follow mounted filesystem growth, preserving chosen headroom'),
            ('set', 'Increase to a fixed allowance without restarting running jobs'),
            ('freeze', 'Keep the current budget fixed and stop following growth')):
        child = commands.add_parser(command, help=help_text)
        child.add_argument('--headroom-mib', type=int,
                           help='Reserved non-job capacity; initially defaults to current gap')
        child.add_argument('--ceiling-mib', type=int, default=0,
                           help='Optional absolute job-budget ceiling; zero follows available capacity')
        if command == 'set':
            child.add_argument('--storage-mib', type=int, required=True)
    commands.add_parser('status', help='Read control, application status and actual pool budget')
    args = parser.parse_args(argv)
    if args.config and any((args.frontend, args.state, args.private_root, args.dsn_file,
                            args.pool != 'simpaths-local', args.schema != 'jasmine_batch')):
        parser.error('Use --config or explicit local diagnostic settings')
    if not args.config and not all((args.frontend, args.state, args.private_root, args.dsn_file)):
        parser.error('Supply --config or all four local paths')
    try:
        if args.config:
            from .privilege_config import trusted_installation
            from .vm_config import load_config
            from .vm_web import check_storage, database_dsn
            trusted_installation(args.config)
            options = load_config(args.config)
            check_storage(options)
            frontend, state, pool, schema = options.frontend, options.state, options.pool_id, 'jasmine_batch'
            dsn = database_dsn(options)
        else:
            from types import SimpleNamespace
            from .vm_web import private_text
            frontend, state, pool, schema = args.frontend, args.state, args.pool, args.schema
            dsn = private_text(args.dsn_file)
            options = SimpleNamespace(private_root=args.private_root, dedicated_storage=False)
        sys.path.insert(0, str(frontend.resolve(strict=True)))
        from jasmine_web.batch.storage_budget import read_control, read_status, current
        from jasmine_web.batch.store import Queue
        queue = Queue(dsn, pool, schema=schema)
        from jasmine_web.batch.backup import database_guard
        # Shared service/backup guard excludes concurrent restore or migrations.
        with database_guard(queue):
            if args.command == 'status':
                result = dict(control=read_control(state, queue=queue), status=read_status(state),
                              storage_mib=current(queue))
            else:
                from .maintenance import service_state
                with service_state(state):
                    result = controller(options, queue, state).configure(
                        'follow' if args.command == 'follow' else 'fixed',
                        headroom_mib=args.headroom_mib, ceiling_mib=args.ceiling_mib,
                        storage_mib=getattr(args, 'storage_mib', None))
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except Exception as error:
        # A DB or filesystem exception may include credentials or private paths.
        print('Storage-budget operation failed: '+type(error).__name__+
              '. Check the private configuration, current budget and mounted capacity.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
