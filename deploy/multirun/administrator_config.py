"""(C) Copyright 2026, by Ross Richardson

Private operator setup for the separate MultiRun administrator password and sessions.
This never approves an account, grants an administrator role or sends an email.
@author ross richardson
"""
import argparse
import getpass
from pathlib import Path
import sys


def change_password(options):
    from .maintenance import state_guard, GATE
    from .releases import atomic_json
    from jasmine_web.batch.administrator_password import make_credential, read_credential
    if not sys.stdin.isatty():
        raise ValueError('Run password setup in a private interactive terminal; do not pipe a password')
    with state_guard(options.state, exclusive=True):
        if (options.state/GATE).exists():
            raise ValueError('Verify and activate restored state before changing its password')
        path=options.state/'administrator-password.json'
        if path.exists() or path.is_symlink():
            read_credential(path)
        password=getpass.getpass('New MultiRun administrator password (at least 16 characters): ')
        confirmation=getpass.getpass('Repeat the new administrator password: ')
        if password!=confirmation:
            raise ValueError('The passwords did not match; no credential was changed')
        credential=make_credential(password)
        atomic_json(path,credential)
    print('Administrator credential saved privately. Start the qualified private MultiRun executor to use it. '
          'Account approvals, administrator email roles and SingleRun credentials were not changed.')


def invalidate_sessions(options):
    from .vm_web import database_dsn
    from .maintenance import service_state
    from jasmine_web.batch.backup import check_schema, database_guard
    from jasmine_web.batch.store import Queue
    q=Queue(database_dsn(options),options.pool_id)
    with service_state(options.state),database_guard(q),q._transaction() as (c,_,now):
        check_schema(c,q)
        c.execute('DELETE FROM administrator_sessions WHERE pool_id=%s',(q.pool_id,))
    print('Existing MultiRun administrator sessions invalidated. Ordinary user sessions and approvals were preserved.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('password','invalidate-sessions'))
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args(argv)
    from .vm_config import load_config
    options=load_config(args.config)
    sys.path.insert(0,str(options.frontend))
    if args.command=='password':change_password(options)
    else:invalidate_sessions(options)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,FileNotFoundError) as error:
        print(str(error),file=sys.stderr);raise SystemExit(1)
