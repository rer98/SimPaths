"""(C) Copyright 2026, by Ross Richardson
Retire a named dataset revision from new MultiRun configurations under the operator account.
@author ross richardson
"""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', help='Full registered dataset reference')
    parser.add_argument('--replacement', help='Optional full reference for the replacement revision')
    parser.add_argument('--reason', help='Optional operator note')
    parser.add_argument('--config', type=Path, default=Path('/etc/simpaths-online/multirun.toml'))
    args = parser.parse_args()
    from .vm_config import load_config
    config = load_config(args.config)
    sys.path.insert(0, str(config.frontend))
    from .vm_web import database_dsn
    from jasmine_web.batch.lifecycle import retire_dataset
    from jasmine_web.batch.store import Queue
    queue = Queue(database_dsn(config), config.pool_id)
    with queue._transaction() as (connection, _, _):
        changed = retire_dataset(connection, queue.pool_id, args.dataset,
                                 replacement=args.replacement, reason=args.reason)
    print(('Retired ' if changed else 'Already retired ') + args.dataset)


if __name__ == '__main__':
    main()
