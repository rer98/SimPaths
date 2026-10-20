#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Private live monitoring archive controls and offline multipart record collation.
Only the service's explicitly enabled mail worker sends messages; merge is offline.
@author ross richardson
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))


def save_part(folder,name,data):
    """Atomic private copy; verify existing copies before treating them as equal."""
    from jasmine_web.batch import performance_archive as archive
    folder=folder.absolute()
    if any(p.is_symlink() for p in (folder,*folder.parents)):raise ValueError('Linked archive destination')
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    if folder.stat().st_uid!=os.getuid() or folder.stat().st_mode&0o077:raise ValueError('Archive destination must be private')
    part=archive.read_part(data)
    if name!=archive.filename(part['manifest'],part['number']):raise ValueError('Archive filename differs from verified identity')
    target=folder/name
    if target.exists() or target.is_symlink():
        fd=os.open(target,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as source:previous=source.read(archive.MAX_COMPRESSED+1)
        if previous!=data:raise ValueError('Archive destination has conflicting content')
        return
    fd,temporary=tempfile.mkstemp(prefix='.monitoring-',dir=folder)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        # Hard-link publication refuses an existing destination without replacement.
        os.link(temporary,target)
        with target.open('rb') as stream:
            if stream.read(archive.MAX_COMPRESSED+1)!=data:raise ValueError('Copied monitoring archive differs')
        directory=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:Path(temporary).unlink(missing_ok=True)


def merge(args):
    from jasmine_web.batch import performance_archive as archive
    c=archive.connect(args.database)
    try:
        issues=[];count=0
        # Bound one attachment in memory; keep the combined record on disk.
        for path in sorted(args.folder.glob('*.json.gz')):
            try:
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                import stat
                with os.fdopen(fd,'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Not a regular attachment')
                    data=stream.read(archive.MAX_COMPRESSED+1)
                archive.import_part(c,data);count+=1
            except (OSError,ValueError,TypeError,KeyError,EOFError,RecursionError) as error:
                issues.append(dict(kind='rejected_attachment',file=path.name,error=type(error).__name__))
        if not count:issues.append(dict(kind='no_valid_attachments_in_folder'))
        result=archive.write_report(c,args.html,args.csv,since=args.since,until=args.until,additional_problems=issues)
        print(json.dumps(dict(imported_files=count,**result['coverage']),indent=2))
        print('Combined record: '+str(args.database)+'\nReadable report: '+str(args.html)+'\nVersion history: '+str(args.csv))
        return 0 if result['coverage']['complete'] else 2
    finally:c.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,help='Existing private VM TOML configuration')
    parser.add_argument('--frontend',type=Path,help='JAS-mine-web checkout (also used for offline merge)')
    parser.add_argument('--state',type=Path,help='Existing local private MultiRun state')
    parser.add_argument('--dsn-file',type=Path,help='Existing private local PostgreSQL credential file')
    parser.add_argument('--pool',default='simpaths-local');parser.add_argument('--schema',default='jasmine_batch')
    commands=parser.add_subparsers(dest='command',required=True)
    enable=commands.add_parser('enable',help='Enable archival; optionally enable mail to the configured administrator')
    enable.add_argument('--mail',action='store_true')
    for name,default in (('archive-seconds',3600),('delivery-seconds',86400),('journal-mib',64),
            ('journal-rows',100000),('archive-mib',512),('archive-days',30),('part-mib',2)):
        enable.add_argument('--'+name,type=int,default=default)
    commands.add_parser('disable',help='Stop archival and mail; keep the existing journal/outbox')
    commands.add_parser('mail-disable',help='Keep archival running while suspending delivery')
    commands.add_parser('status',help='Show private requested controls and durable delivery counters')
    commands.add_parser('pack',help='Pack one bounded batch now; no emails or job changes')
    dump=commands.add_parser('dump',help='Copy durable numbered attachments; does not acknowledge mail delivery')
    dump.add_argument('--output',type=Path,required=True)
    combine=commands.add_parser('merge',help='Offline folder import, deduplication and labelled HTML/CSV report')
    combine.add_argument('--folder',type=Path,required=True);combine.add_argument('--database',type=Path,required=True)
    combine.add_argument('--html',type=Path,required=True);combine.add_argument('--csv',type=Path,required=True)
    combine.add_argument('--since',help='UTC ISO timestamp, inclusive');combine.add_argument('--until',help='UTC ISO timestamp, exclusive')
    args=parser.parse_args(argv)
    try:
        if args.command=='merge':
            if args.config or args.state or args.dsn_file:parser.error('Offline merge uses no service configuration or credentials')
            if not args.frontend:parser.error('Supply --frontend for the offline archive library')
            sys.path.insert(0,str(args.frontend.resolve(strict=True)))
            return merge(args)
        if args.config:
            if args.frontend or args.state or args.dsn_file or args.pool!='simpaths-local' or args.schema!='jasmine_batch':
                parser.error('--config cannot be combined with local connection settings')
            from deploy.multirun.vm_config import load_config
            from deploy.multirun.vm_web import database_dsn
            options=load_config(args.config);state=options.state;frontend=options.frontend;dsn=database_dsn(options)
            pool,schema=options.pool_id,'jasmine_batch'
        else:
            if not args.frontend or not args.state:parser.error('Supply --config, or --frontend and --state')
            state,frontend,pool,schema=args.state,args.frontend,args.pool,args.schema
        sys.path.insert(0,str(frontend.resolve(strict=True)))
        from jasmine_web.batch import performance_exports as exports,performance_archive as archive
        from jasmine_web.batch.performance_review import root_path
        root_path(state)
        if args.command in ('enable','disable','mail-disable'):
            if args.command=='enable':
                if args.mail and (not args.config or not options.notification_emails or not options.admin_email):
                    raise ValueError('Mail requires VM configuration with notifications and an administrator recipient')
                changes={key:getattr(args,key) for key in asdict(exports.Settings()) if key not in ('enabled','mail_enabled')}
                changes.update(enabled=True,mail_enabled=args.mail)
            else:changes=dict(mail_enabled=False,**(dict(enabled=False) if args.command=='disable' else {}))
            value=exports.set_control(state,**changes)
            print(json.dumps(value,indent=2))
            print('Private service applies this control within approximately five seconds. Running jobs are unchanged.')
            return 0
        if not args.config:
            if args.dsn_file:
                from deploy.multirun.vm_web import private_text
                dsn=private_text(args.dsn_file)
            else:
                from jasmine_web.batch.local_database import existing_local_postgres
                dsn=existing_local_postgres(state)
        from jasmine_web.batch.store import Queue
        queue=Queue(dsn,pool,schema=schema)
        if args.command=='status':
            print(json.dumps(dict(control=exports.control(state),durable=exports.status(queue)),indent=2));return 0
        if args.command=='pack':
            value=exports.control(state);settings=exports.Settings(**{key:value[key] for key in asdict(exports.Settings())})
            manifest=exports.build_batch(queue,settings,force=True)
            print(json.dumps(dict(packed=manifest is not None,manifest=manifest),indent=2));return 0
        count=0
        with queue._connection() as connection:
            # Named cursor streams the bounded outbox without loading it all into RAM.
            with connection.cursor(name='monitoring_archive_copy') as cursor:
                cursor.itersize=1
                cursor.execute('SELECT p.payload,p.sha256,b.manifest,p.number FROM performance_export_parts p JOIN performance_export_batches b ON b.pool_id=p.pool_id AND b.id=p.batch_id WHERE p.pool_id=%s ORDER BY b.created_at,b.id,p.number',(queue.pool_id,))
                for row in cursor:
                    data=bytes(row['payload'])
                    if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Durable attachment checksum differs')
                    save_part(args.output,archive.filename(row['manifest'],row['number']),data);count+=1
        print(f'Verified {count} private attachments. Mail acknowledgements are unchanged.');return 0
    except Exception as error:
        print('Monitoring export stopped: '+type(error).__name__+'; inspect private settings and status.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
