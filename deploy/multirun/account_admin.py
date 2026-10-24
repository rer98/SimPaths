"""(C) Copyright 2026, by Ross Richardson

Private, live account and per-dataset approval commands for installed MultiRun.
No migration, launcher construction, scientific dispatch, deletion or SMTP.
@author ross richardson
"""
import argparse
import json
from pathlib import Path
import sys


def approval_days(value):
    try:days=int(value)
    except (ValueError,TypeError):raise argparse.ArgumentTypeError('Use 1–365 whole days') from None
    if not 1<=days<=365:raise argparse.ArgumentTypeError('Use 1–365 whole days')
    return days


def parser():
    result=argparse.ArgumentParser(description=__doc__)
    result.add_argument('--config',type=Path,required=True)
    result.add_argument('command',choices=('list','approve','revoke','grant-dataset','revoke-dataset'))
    result.add_argument('--email')
    result.add_argument('--dataset')
    durations=result.add_mutually_exclusive_group()
    durations.add_argument('--days',type=approval_days,metavar='1..365')
    durations.add_argument('--permanent',action='store_true')
    result.add_argument('--json',action='store_true',help='Private account metadata, including email addresses')
    return result


def operate(options,args):
    from .vm_web import database_dsn,private_text
    from .maintenance import service_state
    from jasmine_web.batch.access import Access,email_address
    from jasmine_web.batch.backup import check_schema,database_guard
    from jasmine_web.batch.store import Queue
    q=Queue(database_dsn(options),options.pool_id)
    with service_state(options.state),database_guard(q):
        with q._connection() as c:check_schema(c,q)
        access=Access(q,private_text(options.state/'session-secret'),None)
        if args.command!='list':
            email=email_address(args.email)
            permanent=args.permanent and args.command in ('approve','grant-dataset')
            seconds=None if permanent or args.command in ('revoke','revoke-dataset') else (args.days or 30)*86400
            if args.command in ('approve','revoke'):
                access.approve_email(email,seconds=seconds,permanent=permanent)
            else:
                with q._connection() as c:
                    row=c.execute('SELECT owner_id FROM email_principals WHERE pool_id=%s AND email=%s',
                        (q.pool_id,email)).fetchone()
                    if not row:raise ValueError('Approve the account before granting a dataset')
                    data=c.execute('SELECT public_training FROM datasets WHERE pool_id=%s AND id=%s',
                        (q.pool_id,args.dataset)).fetchone()
                    if not data:raise ValueError('Unknown dataset')
                    if data['public_training']:raise ValueError('Public training examples are automatically available to approved accounts')
                q.grant_dataset(row['owner_id'],args.dataset,seconds=seconds,permanent=permanent)
        grant=None
        with q._connection() as c:
            rows=c.execute('''SELECT e.email,p.id AS owner,p.approved_until,p.permanent,p.approval_revision,
                account_active(p.pool_id,p.id,clock_timestamp()) AS active
                FROM email_principals e JOIN principals p ON p.pool_id=e.pool_id AND p.id=e.owner_id
                WHERE p.pool_id=%s'''+(' AND e.email=%s' if args.command!='list' else '')+' ORDER BY e.email',
                (q.pool_id,email) if args.command!='list' else (q.pool_id,)).fetchall()
            if args.command in ('grant-dataset','revoke-dataset'):
                grant=c.execute('''SELECT g.dataset_id,g.owner_id,g.valid_until,g.permanent,
                    account_active(g.pool_id,g.owner_id,clock_timestamp())
                    AND dataset_authorised(g.pool_id,g.owner_id,g.dataset_id,clock_timestamp()) AS active
                    FROM dataset_grants g WHERE g.pool_id=%s AND g.owner_id=%s AND g.dataset_id=%s''',
                    (q.pool_id,row['owner_id'],args.dataset)).fetchone()
        return dict(passed=True,operation=args.command,accounts=rows,dataset_permission=grant,mail_sent=False,data_deleted=False)


def main(argv=None):
    args=parser().parse_args(argv)
    if args.command!='list' and not args.email:raise ValueError('Supply the exact account email')
    if args.command in ('grant-dataset','revoke-dataset') and not args.dataset:raise ValueError('Supply a dataset ID')
    if args.command in ('revoke','revoke-dataset','list') and (args.days is not None or args.permanent):
        raise ValueError('Durations apply only to approvals')
    from .vm_config import load_config
    options=load_config(args.config)
    sys.path.insert(0,str(options.frontend))
    result=operate(options,args)
    if args.json:print(json.dumps(result,default=str,sort_keys=True))
    else:
        for row in result['accounts']:
            status=('no expiry' if row['permanent'] else 'until '+str(row['approved_until']) if row['active'] else 'inactive')
            print(row['email']+': '+status)
        if result.get('dataset_permission'):
            grant=result['dataset_permission']
            status=('no expiry' if grant['permanent'] else 'until '+str(grant['valid_until'])) if grant['active'] else 'inactive'
            print(grant['dataset_id']+': '+status)
        print('No mail sent or data deleted.')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('Account administration failed: '+type(error).__name__,file=sys.stderr);raise SystemExit(1)
