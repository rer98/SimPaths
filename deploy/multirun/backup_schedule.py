"""(C) Copyright 2026, by Ross Richardson

Durable scheduled online backups, encrypted off-machine retries and redacted
operator alerts. Configuration is explicit; no timer, host or mail is enabled by
installing this module. Scheduling never restarts or cancels users' simulations.
@author ross richardson
"""
import argparse
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tomllib
from uuid import uuid4

from .artifacts import ArtifactError
from .backup_files import open_file, read_metadata
from .backup_transport import SFTP, dispose_keyring, operator_file, seal, sealed, unseal
from .releases import atomic_json, existing_directory

FORMAT='simpaths.backup.schedule.v1'
DELIVERY_FORMAT='simpaths.backup.delivery.v1'


def utc(): return datetime.now(timezone.utc)


def local_retention(config):
    """Remote-only disposal is opt-in and never valid without a destination."""
    mode=config['backup'].get('local_retention','retain')
    if mode not in ('retain','remote-only') or mode=='remote-only' and not config.get('remote'):
        raise ArtifactError('Choose retain, or remote-only with a configured remote backup destination')
    return mode


def source_scope(source):
    """Bind a job to the resolved source, including edits at an unchanged path.

    Password/SSH-key rotations are not database changes. Only connection endpoint
    fields participate; no credential value is persisted or returned in status.
    """
    from .vm_web import private_text
    from psycopg.conninfo import conninfo_to_dict
    if 'config' in source:
        from .vm_config import load_config
        options=load_config(source['config'])
        fields=dict(frontend=str(options.frontend),state=str(options.state),
                    pool=options.pool_id,schema='jasmine_batch',dsn_file=str(options.dsn_file))
    else:
        fields={k:source[k] for k in ('frontend','state','pool','schema','dsn_file')}
    endpoint=conninfo_to_dict(private_text(fields['dsn_file']))
    fields['database']={k:endpoint.get(k,'') for k in ('host','hostaddr','port','dbname','user','service','servicefile')}
    return fields


def load_config(path):
    path=Path(path)
    with operator_file(path) as source:
        info=os.fstat(source.fileno())
        if info.st_mode&0o077 or info.st_size>128*1024: raise ArtifactError('Backup configuration must be private and bounded')
        value=tomllib.loads(source.read().decode())
    if set(value)-{'source','backup','remote','alerts'} or not {'source','backup'} <= set(value):
        raise ArtifactError('Invalid backup configuration sections')
    source=value['source']; policy=value['backup']
    if not isinstance(source,dict) or not isinstance(policy,dict): raise ArtifactError('Invalid backup configuration')
    if set(source)-{'config','frontend','state','dsn_file','pool','schema','client_container'}:
        raise ArtifactError('Unknown backup source setting')
    if 'config' in source:
        if set(source)-{'config','client_container'}: raise ArtifactError('Use a VM config or explicit source settings')
    elif not {'frontend','state','dsn_file','pool','schema'} <= set(source):
        raise ArtifactError('Provide all explicit backup source settings')
    defaults=dict(interval_hours=24,retry_minutes=30,keep_days=30,keep_latest=3,minimum_free_gib=1,
                  local_retention='retain')
    if set(policy)-{'directory','public_key','recipient',*defaults} or not {'directory','public_key','recipient'} <= set(policy):
        raise ArtifactError('Invalid encrypted backup settings')
    policy={**defaults,**policy}; value['backup']=policy
    from .backup_transport import fingerprint
    policy['recipient']=fingerprint(policy['recipient'])
    for key,maximum in dict(interval_hours=720,retry_minutes=1440,keep_days=3650,keep_latest=1000,minimum_free_gib=1024).items():
        if type(policy[key]) is not int or not 1<=policy[key]<=maximum: raise ArtifactError('Invalid backup schedule or retention limit')
    directory=existing_directory(policy['directory'])
    public=Path(policy['public_key']).absolute()
    with operator_file(public,private=False): pass
    remote=value.get('remote')
    if remote is not None:
        if not isinstance(remote,dict) or set(remote)!={'host','user','port','directory','identity','known_hosts'}:
            raise ArtifactError('Supply the complete pinned remote backup settings')
        SFTP(**remote)
    local_retention(value)
    alerts=value.setdefault('alerts',dict(enabled=False))
    if not isinstance(alerts,dict) or set(alerts)-{'enabled','recipient'} or type(alerts.get('enabled')) is not bool:
        raise ArtifactError('Invalid backup alert settings')
    if alerts['enabled']:
        email=alerts.get('recipient','')
        if not isinstance(email,str) or not re.fullmatch('[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+',email):
            raise ArtifactError('Configure an operator recipient for backup alerts')
    scope=dict(source=source,resolved_source=source_scope(source),
               backup={k:policy[k] for k in ('directory','public_key','recipient')},remote=remote)
    value['_digest']=hashlib.sha256(json.dumps(scope,sort_keys=True,allow_nan=False).encode()).hexdigest()
    return value


def source_queue(config):
    source=config['source']
    if 'config' in source:
        from .vm_config import load_config
        from .vm_web import database_dsn
        options=load_config(source['config'])
        frontend,state,pool,schema,dsn=options.frontend,options.state,options.pool_id,'jasmine_batch',database_dsn(options)
    else:
        from .vm_web import private_text
        frontend,state,pool,schema=Path(source['frontend']),Path(source['state']),source['pool'],source['schema']
        dsn=private_text(source['dsn_file'])
    sys.path.insert(0,str(frontend.resolve(strict=True)))
    from jasmine_web.batch.store import Queue
    from .backup_postgres import PostgresTools
    return Queue(dsn,pool,schema=schema),existing_directory(state),PostgresTools(dsn,container=source.get('client_container'))


def capture_backup(config, destination):
    from .backup_live import create
    queue,state,tools=source_queue(config)
    return create(queue,state,destination,tools,minimum_free_gib=config['backup']['minimum_free_gib'])


def capture_encrypted_backup(config, destination):
    from .backup_live import create_encrypted
    from .backup_files import reserve_space
    queue,state,tools=source_queue(config)
    policy=config['backup']
    with reserve_space(policy['minimum_free_gib']*1024**3):
        return create_encrypted(queue,state,destination,tools,
            public_key=policy['public_key'],recipient=policy['recipient'],
            minimum_free_gib=policy['minimum_free_gib'])


def send_alert(config, incident, *, recovered=False):
    from .vm_web import check_smtp
    check_smtp(os.environ)
    from jasmine_web.contact import deliver_email
    message=EmailMessage()
    message['From']=os.environ['SMTP_FROM_EMAIL']; message['To']=config['alerts']['recipient']
    message['Subject']='SimPaths Online: '+('backup protection restored' if recovered else 'backup needs attention')
    reference=incident['id']+('-recovered' if recovered else '-failed')
    message['Message-ID']='<simpaths-backup-'+reference+'@'+os.environ['SMTP_FROM_EMAIL'].rsplit('@',1)[1]+'>'
    message.set_content('The scheduled backup '+('has completed and its configured protection checks passed.' if recovered else
        'has not completed. The previous verified backup remains available. Check the operator backup status and private configuration.')+
        '\n\nIncident reference: '+incident['id']+'\nStage: '+incident['stage']+
        '\nNo user data, file paths or diagnostic logs are included in this message.\n')
    deliver_email(message)


def load_status(root):
    path=root/'status.json'
    if not path.exists(): return dict(format=FORMAT,current=None,last_success=None,incident=None)
    value=read_metadata(path)
    if not isinstance(value,dict) or value.get('format')!=FORMAT or set(value)!={'format','current','last_success','incident'}:
        raise ArtifactError('Invalid durable backup schedule status')
    current=value['current']
    if current:
        if not re.fullmatch('[a-f0-9]{32}',str(current.get('id'))) or current.get('stage') not in ('capturing','sealing','copying','complete'):
            raise ArtifactError('Invalid scheduled backup job')
    return value


def report(status, *, now=None, config=None):
    now=now or utc(); latest=status['last_success']; current=status['current']; incident=status['incident']
    age=None if latest is None else max(0,(now-datetime.fromisoformat(latest['finished_at'])).total_seconds())
    stale=(latest is None or config is not None and (latest.get('config')!=config['_digest'] or
        age>=(config['backup']['interval_hours']*3600+config['backup']['retry_minutes']*60)))
    return dict(format=FORMAT,passed=not stale and (incident is None or incident.get('resolved_at') is not None),operation='status',latest=latest,
        overdue=stale,
        local_retention=local_retention(config) if config is not None else None,
        last_success_age_seconds=age,pending_stage=current['stage'] if current else (
            'local-cleanup' if incident and incident['stage']=='local-cleanup' and not incident.get('resolved_at') else None),
        failure=None if incident is None else dict(id=incident['id'],stage=incident['stage'],opened_at=incident['opened_at'],
            alert_sent=incident.get('alert_sent',False),alert_pending=not incident.get('alert_sent',False)))


def verified_delivery(config, storage, job, receipt, connection, now):
    """Journal positive archive AND receipt delivery before authorising removal."""
    result=connection.copy(storage/'recovery.tar.gpg')
    if not isinstance(result,dict) or result.get('passed') is not True or result.get('sha256')!=receipt['sha256']:
        raise ArtifactError('Remote backup delivery did not confirm the verified checksum')
    value=dict(format=DELIVERY_FORMAT,job_id=job['id'],config=config['_digest'],receipt=receipt,
               verified_at=now.isoformat(),local_state='retained',removal_started_at=None,removed_at=None)
    atomic_json(storage/'delivery.json',value)
    return value


def delivery_record(config, storage, record):
    value=read_metadata(storage/'delivery.json')
    receipt=read_metadata(storage/'recovery.tar.gpg.json')
    if (not isinstance(value,dict) or set(value)!={'format','job_id','config','receipt','verified_at',
            'local_state','removal_started_at','removed_at'} or value['format']!=DELIVERY_FORMAT or
            value['job_id']!=record['job_id'] or value['config']!=config['_digest'] or
            not isinstance(receipt,dict) or set(receipt)!={'format','backup_id','recipient','bytes','sha256','mode'} or
            receipt['format']!='simpaths.backup.encrypted.v1' or receipt['backup_id']!=record['backup_id'] or
            receipt['recipient']!=config['backup']['recipient'] or receipt['sha256']!=record['sha256'] or
            not re.fullmatch('[a-f0-9]{64}',str(receipt['sha256'])) or
            type(receipt['bytes']) is not int or receipt['bytes']<0 or receipt['mode']!=0o400 or
            value['receipt']!=receipt or value['local_state'] not in ('retained','removing','removed')):
        raise ArtifactError('Invalid durable remote backup delivery record')
    for key in ('verified_at','removal_started_at','removed_at'):
        stamp=value[key]
        required=key=='verified_at' or key=='removal_started_at' and value['local_state']!='retained' or (
            key=='removed_at' and value['local_state']=='removed')
        if not required and stamp is None: continue
        try:
            if not required or datetime.fromisoformat(stamp).tzinfo is None: raise ValueError()
        except (ValueError,TypeError): raise ArtifactError('Invalid remote backup disposal timestamp') from None
    return value


def remove_local_archive(root, config, status, storage, record, now, transport):
    """Finish a journalled unlink, preserving receipts and never touching a pending job."""
    from .backup_files import scan
    scan(storage)
    if record.get('off_machine') is not True:
        raise ArtifactError('Local backup has no confirmed off-machine delivery')
    if not (storage/'delivery.json').exists():
        # Older completed backups lack the new delivery journal. Recheck their
        # actual destination before allowing the newly selected disposal policy.
        receipt=sealed(storage/'recovery.tar.gpg',recipient=config['backup']['recipient'])
        if receipt['backup_id']!=record['backup_id'] or receipt['sha256']!=record['sha256']:
            raise ArtifactError('Completed backup identity differs from its encrypted receipt')
        verified_delivery(config,storage,dict(id=record['job_id']),receipt,
                          transport or SFTP(**config['remote']),now)
    value=delivery_record(config,storage,record)
    cipher=storage/'recovery.tar.gpg'
    present=cipher.exists()
    if present:
        with open_file(storage,cipher.name) as source:
            info=os.fstat(source.fileno())
        if (value['local_state']=='removed' or info.st_size!=value['receipt']['bytes'] or
                info.st_mode&0o700!=value['receipt']['mode']):
            raise ArtifactError('Local backup differs from the confirmed delivery')
    elif value['local_state']=='retained':
        raise ArtifactError('Local backup disappeared before disposal was authorised')
    if value['local_state']=='retained':
        value.update(local_state='removing',removal_started_at=now.isoformat())
        atomic_json(storage/'delivery.json',value)
    if value['local_state']=='removing':
        if present: cipher.unlink()
        # A restart after unlink is safe: the durable intent and remote receipt
        # already exist. The atomic metadata publication also fsyncs this directory.
        value.update(local_state='removed',removed_at=now.isoformat())
        atomic_json(storage/'delivery.json',value)
    if record.get('local_archive_removed_at')!=value['removed_at']:
        record['local_archive_removed_at']=value['removed_at']
        atomic_json(storage/'complete.json',record)
    latest=status['last_success']
    if latest and latest['job_id']==record['job_id'] and latest!=record:
        status['last_success']=record.copy()
        atomic_json(root/'status.json',status)


def retention(root, config, status, now, *, transport=None):
    """Retire completed payloads/records only; preserve every pending capture."""
    mode=local_retention(config)
    folder=root/'jobs'
    if not folder.exists(): return
    existing_directory(folder)
    completed=[]
    for child in folder.iterdir():
        if child.is_symlink() or not child.is_dir() or not re.fullmatch('[a-f0-9]{32}',child.name):
            raise ArtifactError('Unexpected scheduled backup storage')
        marker=child/'complete.json'
        if not marker.exists(): continue
        record=read_metadata(marker)
        if record.get('job_id')!=child.name or record.get('config')!=config['_digest']:
            continue
        if status['current'] and status['current']['id']==child.name:
            continue
        removing=False
        if (child/'delivery.json').exists():
            delivery=delivery_record(config,child,record)
            removing=delivery['local_state'] in ('removing','removed')
        if mode=='remote-only' or removing:
            remove_local_archive(root,config,status,child,record,now,transport)
        completed.append((datetime.fromisoformat(record['finished_at']),child))
    completed.sort(reverse=True)
    for at,child in completed[config['backup']['keep_latest']:]:
        if at <= now-timedelta(days=config['backup']['keep_days']):
            # Validate the tree first; never traverse unexpected links on cleanup.
            from .backup_files import scan
            scan(child); shutil.rmtree(child)


def record_failure(status, stage, now):
    if not status['incident'] or status['incident'].get('resolved_at'):
        status['incident']=dict(id=uuid4().hex,stage=stage,opened_at=now.isoformat(),alert_sent=False)


def maintain_local(root, config, status, now, transport):
    """Make cleanup failures retryable/visible without creating another archive."""
    try:
        retention(root,config,status,now,transport=transport)
        if status['incident'] and status['incident']['stage']=='local-cleanup':
            status['incident']['resolved_at']=now.isoformat()
            atomic_json(root/'status.json',status)
        return True
    except Exception:
        record_failure(status,'local-cleanup',now)
        atomic_json(root/'status.json',status)
        return False


def discard_incomplete(storage, job):
    """Only this private, serialized job owns these interrupted staging files."""
    from .backup_files import scan
    if job['stage']=='capturing' and not (storage/'plain').exists():
        prefixes=('.backup-incomplete-','.encrypting-','.backup-public-key-')
        marker=storage/'recovery.tar.gpg.json'
        if marker.exists() and not (storage/'recovery.tar.gpg').exists():
            receipt=read_metadata(marker)
            if (receipt.get('format')!='simpaths.backup.encrypted.v1' or
                    not re.fullmatch('[a-f0-9]{32}',str(receipt.get('backup_id')))):
                raise ArtifactError('Invalid interrupted streaming receipt')
            marker.unlink()
    elif job['stage']=='sealing' and not (storage/'recovery.tar.gpg').exists():
        prefixes=('.encrypting-','.backup-public-key-')
        marker=storage/'recovery.tar.gpg.json'
        if marker.exists():
            receipt=read_metadata(marker)
            if receipt.get('backup_id')!=job['backup_id']: raise ArtifactError('Interrupted encryption identity differs')
            marker.unlink()
    elif job['stage']=='copying': prefixes=('.transfer-check-',)
    else: return
    for path in storage.iterdir():
        if not any(path.name.startswith(p) for p in prefixes): continue
        if path.is_symlink(): raise ArtifactError('Linked interrupted backup storage')
        if path.is_dir():
            if path.name.startswith('.backup-public-key-'):
                # An interrupted GPG keyring can contain its agent socket. The
                # ephemeral ring contains only the public VM encryption key.
                dispose_keyring(path)
            else: scan(path); shutil.rmtree(path)
        else:
            with open_file(storage,path.name): pass
            path.unlink()


def run(config, *, now=None, capture=capture_backup, encrypt=seal, transport=None, alert=send_alert,
        capture_encrypted=capture_encrypted_backup):
    local_retention(config)
    root=existing_directory(config['backup']['directory'])
    from deploy._workflow import frontend_path
    source=config['source']
    if 'config' in source:
        from .vm_config import load_config as vm_config
        frontend=vm_config(source['config']).frontend
    else: frontend=Path(source.get('frontend') or frontend_path())
    sys.path.insert(0,str(frontend.resolve(strict=True)))
    from jasmine_web.batch.backup_guard import file_guard
    now=now or utc()
    from .backup_files import reserve_space
    with reserve_space(config['backup']['minimum_free_gib']*1024**3),file_guard(root,backup=True):
        status=load_status(root)
        job=status['current']; policy=config['backup']
        if job and job['config']!=config['_digest']:
            raise ArtifactError('Finish the pending backup before changing its source, encryption or destination settings')
        # Dispose of previously delivered payloads before starting a replacement,
        # so remote-only mode needs only one archive-sized local staging area.
        ready=maintain_local(root,config,status,now,transport)
        due=(status['last_success'] is None or status['last_success'].get('config')!=config['_digest'] or
             now>=datetime.fromisoformat(status['last_success']['finished_at'])+timedelta(hours=policy['interval_hours']))
        retry=job is None or not job.get('retry_at') or now>=datetime.fromisoformat(job['retry_at'])
        if job and not job.get('retry_at') and job['stage']!='complete' and not status['incident']:
            # A prior process died before recording an ordinary failure. Preserve
            # the job and its already completed stages, and record the incident.
            status['incident']=dict(id=uuid4().hex,stage=job['stage'],opened_at=now.isoformat(),alert_sent=False)
            atomic_json(root/'status.json',status)
        if ready and job is None and due:
            job=dict(id=uuid4().hex,config=config['_digest'],stage='capturing',started_at=now.isoformat(),retry_at=None)
            status['current']=job; atomic_json(root/'status.json',status)
        if ready and job and retry:
            parent=root/'jobs'; parent.mkdir(mode=0o700,exist_ok=True)
            storage=parent/job['id']; storage.mkdir(mode=0o700,exist_ok=True)
            plain=storage/'plain'; cipher=storage/'recovery.tar.gpg'
            try:
                discard_incomplete(storage,job)
                if (job['stage']=='capturing' and not plain.exists()
                        and capture is capture_backup and encrypt is seal):
                    # A completed ciphertext/receipt survives a lost journal
                    # acknowledgement. Reuse it instead of taking a newer SQL
                    # snapshot. Legacy pending plaintext jobs still resume below.
                    receipt=sealed(cipher,recipient=policy['recipient']) if cipher.exists() else capture_encrypted(config,cipher)
                    job.update(stage='copying',backup_id=receipt['backup_id'],sha256=receipt['sha256'])
                    atomic_json(root/'status.json',status)
                if job['stage']=='capturing':
                    if plain.exists():
                        from .backup import verify, summary
                        result=summary(verify(plain),'backup')
                    else: result=capture(config,plain)
                    job.update(stage='sealing',backup_id=result['backup_id'])
                    atomic_json(root/'status.json',status)
                if job['stage']=='sealing':
                    if cipher.exists(): receipt=sealed(cipher,recipient=policy['recipient'])
                    else: receipt=encrypt(plain,cipher,public_key=policy['public_key'],recipient=policy['recipient'])
                    if receipt['backup_id']!=job['backup_id']: raise ArtifactError('Encrypted job identity differs from the captured backup')
                    job.update(stage='copying',sha256=receipt['sha256'])
                    atomic_json(root/'status.json',status)
                if job['stage']=='copying':
                    receipt=sealed(cipher,recipient=policy['recipient'])
                    # Keep a fully verified encrypted local copy before erasing
                    # plaintext, even while a remote outage delays protection.
                    if plain.exists():
                        from .backup import verify
                        verify(plain); shutil.rmtree(plain)
                    if config.get('remote'):
                        connection=transport or SFTP(**config['remote'])
                        verified_delivery(config,storage,job,receipt,connection,now)
                    job['stage']='complete'
                    atomic_json(root/'status.json',status)
                sealed(cipher,recipient=policy['recipient'])
                latest=dict(job_id=job['id'],backup_id=job['backup_id'],config=config['_digest'],finished_at=now.isoformat(),
                    sha256=job['sha256'],off_machine=bool(config.get('remote')))
                atomic_json(storage/'complete.json',latest)
                status.update(current=None,last_success=latest)
                if status['incident']: status['incident']['resolved_at']=now.isoformat()
                atomic_json(root/'status.json',status)
            except Exception:
                job['retry_at']=(now+timedelta(minutes=policy['retry_minutes'])).isoformat()
                record_failure(status,job['stage'],now)
                atomic_json(root/'status.json',status)
        if ready:
            maintain_local(root,config,status,now,transport)
        incident=status['incident']
        if incident and config['alerts']['enabled']:
            try:
                if incident.get('resolved_at'):
                    if incident.get('alert_sent'): alert(config,incident,recovered=True)
                    status['incident']=None
                elif not incident['alert_sent']:
                    alert(config,incident); incident['alert_sent']=True
            except Exception:
                # A durable pending notification is retried by the next timer.
                pass
            atomic_json(root/'status.json',status)
        elif incident and incident.get('resolved_at'):
            status['incident']=None; atomic_json(root/'status.json',status)
        return report(status,now=now,config=config)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('run','status','unseal'))
    parser.add_argument('--config',type=Path)
    parser.add_argument('--encrypted',type=Path)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--keyring',type=Path)
    parser.add_argument('--expected-sha256')
    args=parser.parse_args(argv)
    try:
        os.umask(0o077)
        if args.command=='unseal':
            if not all((args.encrypted,args.destination,args.keyring,args.expected_sha256)):
                raise ArtifactError('Provide encrypted file, new destination, private recovery keyring and trusted ciphertext checksum')
            result=unseal(args.encrypted,args.destination,keyring=args.keyring,expected_sha256=args.expected_sha256)
        else:
            if not args.config: raise ArtifactError('Provide the private backup configuration')
            config=load_config(args.config)
            result=run(config) if args.command=='run' else report(load_status(existing_directory(config['backup']['directory'])),config=config)
        print(json.dumps(result,sort_keys=True))
        return 0 if result['passed'] else 2
    except Exception:
        print(json.dumps(dict(format=FORMAT,passed=False,operation=args.command,error='Backup operation could not finish. Check private operator settings; previous verified backups are unchanged.')))
        return 2


if __name__=='__main__': raise SystemExit(main())
