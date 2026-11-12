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
import stat
import sys
import tomllib
import traceback
from uuid import uuid4

from .artifacts import ArtifactError
from .backup_files import file_hash, open_file, read_metadata
from .backup_transport import SFTP, dispose_keyring, operator_file, seal, sealed, unseal
from .releases import atomic_json, existing_directory

FORMAT='simpaths.backup.schedule.v1'
DELIVERY_FORMAT='simpaths.backup.delivery.v1'
ROTATION_FORMAT='simpaths.backup.count-rotation.v1'
ROTATION_JOURNAL='retention-prune.json'
ROTATION_FOLDER='retirement-pending'


def utc(): return datetime.now(timezone.utc)


def local_retention(config):
    """Remote-only disposal is opt-in and never valid without a destination."""
    mode=config['backup'].get('local_retention','retain')
    if mode not in ('retain','remote-only') or mode=='remote-only' and not config.get('remote'):
        raise ArtifactError('Choose retain, or remote-only with a configured remote backup destination')
    return mode


def retention_rule(config):
    """Count-only history is a separate explicit choice, never a new default."""
    rule=config['backup'].get('retention_rule','age-and-count')
    if rule not in ('age-and-count','count'):
        raise ArtifactError('Choose age-and-count or explicit count-only backup retention')
    return rule


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
                  local_retention='retain',retention_rule='age-and-count')
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
    retention_rule(value)
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
        retention_rule=retention_rule(config) if config is not None else None,
        last_success_age_seconds=age,pending_stage=current['stage'] if current else (
            'local-cleanup' if incident and incident['stage']=='local-cleanup' and not incident.get('resolved_at') else None),
        failure=None if incident is None else dict(id=incident['id'],stage=incident['stage'],opened_at=incident['opened_at'],
            alert_sent=incident.get('alert_sent',False),alert_pending=not incident.get('alert_sent',False),
            diagnostic=incident.get('diagnostic')))


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


def _rotation_record(path,config):
    record=read_metadata(path/'complete.json')
    if not isinstance(record,dict):raise ArtifactError('Invalid completed backup record')
    if record.get('config')!=config['_digest']:return None
    fields=('job_id','backup_id','config','finished_at','sha256','off_machine')
    if (not set(fields)<=set(record) or record['job_id']!=path.name or
            not re.fullmatch('[a-f0-9]{32}',str(record['backup_id'])) or
            not re.fullmatch('[a-f0-9]{64}',str(record['sha256'])) or type(record['off_machine']) is not bool):
        raise ArtifactError('Invalid completed backup identity')
    try:
        at=datetime.fromisoformat(record['finished_at'])
        if at.tzinfo is None:raise ValueError()
    except (TypeError,ValueError):raise ArtifactError('Invalid completed backup time') from None
    return record


def _rotation_completed(root,config,status):
    folder=root/'jobs'
    if not folder.exists():return []
    existing_directory(folder);completed=[]
    pending=status['current']['id'] if status['current'] else None
    for child in folder.iterdir():
        if child.is_symlink() or not child.is_dir() or not re.fullmatch('[a-f0-9]{32}',child.name):
            raise ArtifactError('Unexpected scheduled backup storage')
        if child.name==pending or not (child/'complete.json').exists():continue
        record=_rotation_record(child,config)
        if record is not None:completed.append((datetime.fromisoformat(record['finished_at']),child,record))
    return sorted(completed,key=lambda row:(row[0],row[1].name),reverse=True)


def _rotation_kept(root,config,status,kept,*,transport=None):
    """Verify every surviving recovery copy before any count-only removal."""
    ids={row['id'] for row in kept};latest=status['last_success']
    if (len(ids)!=len(kept) or len(kept)!=config['backup']['keep_latest'] or
            not latest or latest.get('config')!=config['_digest'] or latest.get('job_id') not in ids):
        raise ArtifactError('The latest verified backup must remain in the retained set')
    for selected in kept:
        path=root/'jobs'/selected['id'];record=_rotation_record(path,config)
        if (record is None or file_hash(path,'complete.json')['sha256']!=selected['complete_sha256'] or
                any(record.get(k)!=selected['record'].get(k) for k in selected['record'])):
            raise ArtifactError('A retained completion record changed')
        if record['job_id']==latest['job_id'] and any(record[k]!=latest.get(k)
                for k in ('backup_id','config','sha256','finished_at')):
            raise ArtifactError('The latest backup and its completion record differ')
        cipher=path/'recovery.tar.gpg'
        if cipher.exists() or cipher.is_symlink():receipt=sealed(cipher,recipient=config['backup']['recipient'])
        else:
            if not config.get('remote') or record['off_machine'] is not True:
                raise ArtifactError('A retained encrypted backup is missing')
            delivery=delivery_record(config,path,record)
            if delivery['local_state']!='removed':raise ArtifactError('A retained archive has no completed disposal intent')
            receipt=delivery['receipt'];connection=transport or SFTP(**config['remote'])
            actual=connection.remote_hash(connection.directory+'/recovery-'+receipt['backup_id']+'.tar.gpg',receipt)
            if actual!={k:receipt[k] for k in ('bytes','sha256')}:
                raise ArtifactError('A retained remote backup could not be verified')
        if receipt['backup_id']!=record['backup_id'] or receipt['sha256']!=record['sha256']:
            raise ArtifactError('A retained encrypted backup and completion record differ')


def _rotation_sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def _rotation_tree(path):
    """Completed scheduler jobs are closed, flat archive/receipt directories."""
    from .backup_files import scan
    observed=scan(path)
    allowed={'complete.json','recovery.tar.gpg','recovery.tar.gpg.json','delivery.json'}
    if (not {'complete.json','recovery.tar.gpg.json'}<=set(observed) or not set(observed)<=allowed
            or any(not stat.S_ISREG(info[2]) for info in observed.values())):
        raise ArtifactError('Unexpected files in a completed backup selected for rotation')
    return {name:list(info) for name,info in observed.items()}


def _rotation_validate(journal,config,status):
    if (not isinstance(journal,dict) or set(journal)!={'format','config','keep_latest','kept','targets'} or
            journal['format']!=ROTATION_FORMAT or journal['config']!=config['_digest'] or
            journal['keep_latest']!=config['backup']['keep_latest'] or retention_rule(config)!='count' or
            not isinstance(journal['kept'],list) or len(journal['kept'])!=journal['keep_latest'] or
            not isinstance(journal['targets'],list) or not 1<=len(journal['targets'])<=10000):
        raise ArtifactError('An existing count-only rotation needs its original verified policy')
    kept_ids=set();target_ids=set()
    for selected in journal['kept']:
        if (not isinstance(selected,dict) or set(selected)!={'id','complete_sha256','record'} or
                not re.fullmatch('[a-f0-9]{32}',str(selected['id'])) or
                not re.fullmatch('[a-f0-9]{64}',str(selected['complete_sha256'])) or
                not isinstance(selected['record'],dict) or selected['id'] in kept_ids):
            raise ArtifactError('Invalid retained rotation identities')
        kept_ids.add(selected['id'])
    for selected in journal['targets']:
        if (not isinstance(selected,dict) or set(selected)!={'id','directory','files','complete_sha256','detached'} or
                not re.fullmatch('[a-f0-9]{32}',str(selected['id'])) or selected['id'] in target_ids or
                selected['id'] in kept_ids or type(selected['detached']) is not bool or
                not re.fullmatch('[a-f0-9]{64}',str(selected['complete_sha256'])) or
                not isinstance(selected['directory'],list) or len(selected['directory'])!=2 or
                any(type(v) is not int or v<0 for v in selected['directory']) or
                not isinstance(selected['files'],dict) or not selected['files'] or
                not set(selected['files'])<={'complete.json','recovery.tar.gpg','recovery.tar.gpg.json','delivery.json'}):
            raise ArtifactError('Invalid exact backup rotation target')
        for info in selected['files'].values():
            if not isinstance(info,list) or len(info)!=6 or any(type(v) is not int or v<0 for v in info) or not stat.S_ISREG(info[2]):
                raise ArtifactError('Invalid selected backup file identity')
        target_ids.add(selected['id'])
    if status['current'] and status['current']['id'] in target_ids:
        raise ArtifactError('Pending backup work cannot be selected for rotation')


def _rotation_resume(root,config,status,journal,*,transport=None,verified=False):
    """Caller owns the configured backup-root exclusive file fence throughout."""
    from .backup_files import scan
    _rotation_validate(journal,config,status)
    if not verified:_rotation_kept(root,config,status,journal['kept'],transport=transport)
    jobs=existing_directory(root/'jobs');retired=root/ROTATION_FOLDER
    retired.mkdir(mode=0o700,exist_ok=True);existing_directory(retired)
    for selected in journal['targets']:
        original=jobs/selected['id'];destination=retired/selected['id']
        before=original.exists() or original.is_symlink();after=destination.exists() or destination.is_symlink()
        if before and after:raise ArtifactError('A selected rotation identity exists in two places')
        if before:
            if selected['detached']:raise ArtifactError('A detached backup identity was reused')
            existing_directory(original);info=original.stat()
            if ([info.st_dev,info.st_ino]!=selected['directory'] or _rotation_tree(original)!=selected['files'] or
                    file_hash(original,'complete.json')['sha256']!=selected['complete_sha256'] or
                    _rotation_record(original,config) is None):
                raise ArtifactError('A backup selected for rotation changed')
            os.rename(original,destination);_rotation_sync(jobs);_rotation_sync(retired);after=True
        if after:
            existing_directory(destination);info=destination.stat()
            if [info.st_dev,info.st_ino]!=selected['directory']:raise ArtifactError('A detached backup directory changed')
            observed={name:list(info) for name,info in scan(destination).items()}
            if (not set(observed)<=set(selected['files']) or
                    any(observed[name]!=selected['files'][name] for name in observed) or
                    not selected['detached'] and observed!=selected['files']):
                raise ArtifactError('Unexpected files appeared in a detached backup')
            if not selected['detached']:
                selected['detached']=True;atomic_json(root/ROTATION_JOURNAL,journal)
            for name in sorted(observed):
                # Each unlink is bound to the journalled inode and prior scan.
                current=(destination/name).lstat()
                from .backup_files import identity
                if list(identity(current))!=selected['files'][name]:raise ArtifactError('A detached backup file changed')
                (destination/name).unlink()
            destination.rmdir();_rotation_sync(retired)
        elif not selected['detached']:
            raise ArtifactError('A selected backup disappeared before detachment')
    (root/ROTATION_JOURNAL).unlink();_rotation_sync(root)


def count_retention(root,config,status,now,*,transport=None):
    """Explicit count-only rotation; defaults retain their existing age floor."""
    if retention_rule(config)!='count':raise ArtifactError('Finish the pending count-only rotation before changing policy')
    marker=root/ROTATION_JOURNAL
    if marker.exists() or marker.is_symlink():
        _rotation_resume(root,config,status,read_metadata(marker),transport=transport)
    completed=_rotation_completed(root,config,status);keep=config['backup']['keep_latest']
    if len(completed)>keep:
        kept=[dict(id=path.name,complete_sha256=file_hash(path,'complete.json')['sha256'],
            record={k:record[k] for k in ('job_id','backup_id','config','finished_at','sha256','off_machine')})
            for _,path,record in completed[:keep]]
        targets=[]
        for _,path,record in completed[keep:]:
            files=_rotation_tree(path)
            if 'recovery.tar.gpg' not in files:
                delivery=delivery_record(config,path,record)
                if delivery['local_state']!='removed':raise ArtifactError('A selected archive is missing without confirmed disposal')
            receipt=read_metadata(path/'recovery.tar.gpg.json')
            if receipt.get('backup_id')!=record['backup_id'] or receipt.get('sha256')!=record['sha256']:
                raise ArtifactError('A selected completed backup and receipt differ')
            info=path.stat();targets.append(dict(id=path.name,directory=[info.st_dev,info.st_ino],files=files,
                complete_sha256=file_hash(path,'complete.json')['sha256'],detached=False))
        journal=dict(format=ROTATION_FORMAT,config=config['_digest'],keep_latest=keep,kept=kept,targets=targets)
        _rotation_validate(journal,config,status)
        _rotation_kept(root,config,status,kept,transport=transport)
        atomic_json(marker,journal)
        _rotation_resume(root,config,status,journal,transport=transport,verified=True)
        completed=completed[:keep]
    # Existing remote-only disposal remains separate from historical count
    # rotation. Previously committed disposal intents still finish after opt-out.
    for _,path,record in completed:
        removing=False
        if (path/'delivery.json').exists():removing=delivery_record(config,path,record)['local_state'] in ('removing','removed')
        if local_retention(config)=='remote-only' or removing:
            remove_local_archive(root,config,status,path,record,now,transport)


def retention(root, config, status, now, *, transport=None):
    """Retire completed payloads/records only; preserve every pending capture."""
    mode=local_retention(config)
    rule=retention_rule(config)
    if rule=='count' or (root/ROTATION_JOURNAL).exists():
        return count_retention(root,config,status,now,transport=transport)
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


def failure_diagnostic(error,stage):
    """Fixed error classes and public code locations, never exception contents."""
    classes={'ArtifactError','BackupBusy','ValueError','TypeError','KeyError','RuntimeError',
             'OSError','FileNotFoundError','PermissionError','TimeoutExpired','JSONDecodeError',
             'CalledProcessError'}
    modules={'backup_schedule.py','backup_live.py','backup_files.py','backup_transport.py',
             'backup_stream.py','backup_visualiser.py','backup_single.py','backup_postgres.py',
             'queue_adapter.py','backup_output_receipts.py','csv_validation.py','releases.py',
             'artifacts.py','backup_guard.py'}
    sites=[]
    for frame in traceback.extract_tb(error.__traceback__):
        name=Path(frame.filename).name
        if name in modules:
            sites.append(dict(module=name,function=frame.name if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,79}',frame.name) else 'code',line=frame.lineno))
    return dict(stage=stage,error_type=type(error).__name__ if type(error).__name__ in classes else 'OtherError',
                sites=sites[-4:])


def record_failure(status, stage, now, *, error=None):
    if not status['incident'] or status['incident'].get('resolved_at'):
        status['incident']=dict(id=uuid4().hex,stage=stage,opened_at=now.isoformat(),alert_sent=False)
    if error is not None:status['incident']['diagnostic']=failure_diagnostic(error,stage)


def maintain_local(root, config, status, now, transport):
    """Make cleanup failures retryable/visible without creating another archive."""
    try:
        retention(root,config,status,now,transport=transport)
        if status['incident'] and status['incident']['stage']=='local-cleanup':
            status['incident']['resolved_at']=now.isoformat()
            atomic_json(root/'status.json',status)
        return True
    except Exception as error:
        record_failure(status,'local-cleanup',now,error=error)
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
            except Exception as error:
                job['retry_at']=(now+timedelta(minutes=policy['retry_minutes'])).isoformat()
                record_failure(status,job['stage'],now,error=error)
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
