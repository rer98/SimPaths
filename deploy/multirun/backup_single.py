"""(C) Copyright 2026, by Ross Richardson

SingleRun registry and closed-export capture in a shared VM recovery point.
Running output and H2 databases are excluded. Restored sessions retain private
ownership and saved exports, with no attempt to reattach a live Java process.
@author ross richardson
"""
from contextlib import nullcontext
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
from types import SimpleNamespace
import zipfile
from uuid import NAMESPACE_URL, uuid5

from .artifacts import ArtifactError
from .backup_files import MAX_ENTRIES, file_hash, has_space, name
from .releases import atomic_json


def schema_guard(dsn, schema, *, backup=False):
    from jasmine_web.vm_backup import schema_guard as guard
    return guard(dsn,schema,backup=backup)


def discover(queue, *, connection=None):
    from psycopg import sql
    result=[]
    with (nullcontext(connection) if connection is not None else queue._connection()) as c:
        names=c.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' ORDER BY nspname").fetchall()
        for item in names:
            schema=item['nspname']
            if not re.fullmatch('[a-z][a-z0-9_]{0,62}',schema): continue
            columns=c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='metadata'",(schema,)).fetchall()
            if not {'deployment_id','shared_pool_id','shared_batch_schema'} <= {r['column_name'] for r in columns}: continue
            meta=c.execute(sql.SQL('SELECT shared_pool_id,shared_batch_schema FROM {}').format(sql.Identifier(schema,'metadata'))).fetchone()
            if meta and (meta['shared_pool_id'],meta['shared_batch_schema'])==(queue.pool_id,queue.schema): result.append(schema)
        reservations=c.execute(sql.SQL('SELECT DISTINCT vm_schema FROM {} WHERE pool_id=%s').format(
            sql.Identifier(queue.schema,'interactive_reservations')),(queue.pool_id,)).fetchall()
        if not {r['vm_schema'] for r in reservations} <= set(result):
            raise ArtifactError('All shared SingleRun registries must be present in this recovery database')
    return result


class Chunks(io.RawIOBase):
    """Bounded reader for Docker's streamed archive; never buffer a whole file."""
    def __init__(self, chunks):
        self.chunks=iter(chunks); self.pending=b''
    def readable(self): return True
    def readinto(self, destination):
        while not self.pending:
            self.pending=next(self.chunks,b'')
            if not self.pending: return 0
        count=min(len(destination),len(self.pending))
        destination[:count]=self.pending[:count]; self.pending=self.pending[count:]
        return count
    def close(self):
        try:
            if hasattr(self.chunks,'close'): self.chunks.close()
        finally: super().close()


def container_tree(container, source, target):
    """Unpack ordinary Docker files only; every private path stays below target."""
    def inventory():
        result=container.exec_run(['find',source,'-printf','%P\0%y\0%D\0%i\0%s\0%T@\0%C@\0'])
        if result.exit_code or len(result.output)>8*1024**2:
            raise ArtifactError('SingleRun backup source could not be checked')
        return result.output
    before=inventory()
    chunks,_=container.get_archive(source,chunk_size=1024**2)
    target.mkdir(mode=0o700)
    count=0; seen=set()
    with io.BufferedReader(Chunks(chunks),buffer_size=1024**2) as stream, tarfile.open(fileobj=stream,mode='r|') as archive:
        for member in archive:
            count+=1
            if count>MAX_ENTRIES: raise ArtifactError('SingleRun export inventory exceeds its entry limit')
            key=name(member.name)
            if (key.parts[0]!=Path(source).name or not (member.isdir() or member.isfile()) or member.size<0 or
                    key.as_posix() in seen):
                raise ArtifactError('Invalid linked or special SingleRun backup file')
            seen.add(key.as_posix())
            path=target/Path(*key.parts)
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            if member.isdir(): path.mkdir(mode=0o700,exist_ok=True); continue
            if not has_space(target,member.size):
                raise ArtifactError('Insufficient free space for SingleRun exports')
            with archive.extractfile(member) as incoming, path.open('xb') as output:
                os.chmod(path,0o400)
                remaining=member.size
                while remaining:
                    block=incoming.read(min(1024**2,remaining))
                    if not block: raise ArtifactError('Truncated SingleRun export')
                    output.write(block); remaining-=len(block)
                output.flush(); os.fsync(output.fileno())
    if inventory()!=before:
        raise ArtifactError('SingleRun files changed during capture; no backup was published')


def saved_zip(directory, destination):
    from .backup_files import scan
    listing=scan(directory)
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
        os.chmod(destination,0o400)
        for key in sorted(listing):
            if (directory/key).is_file(): archive.write(directory/key,key)
    # Unpacking is bounded and verifies archive CRCs; publication never claims
    # that a paused scientific run is complete merely because its ZIP is valid.
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip(): raise ArtifactError('Invalid SingleRun backup archive')
    with destination.open('rb') as source: os.fsync(source.fileno())


def json_get(http, host, secret, endpoint, *, forbidden=False):
    response=http.get('http://'+host+':7070/'+endpoint,headers={'X-Jasmine-Backend-Token':secret})
    if forbidden and response.status_code==403: return None
    if response.status_code!=200 or len(response.content)>2*1024**2:
        raise ArtifactError('SingleRun state could not be captured safely')
    value=response.json()
    if not isinstance(value,dict): raise ArtifactError('Invalid SingleRun backup metadata')
    return value


def capture(c, dsn, schema, destination, source):
    from psycopg import sql
    from jasmine_web.container_identity import container_labels_match_session,private_container_host
    from jasmine_web.session_security import network_name
    from jasmine_web.session_lifecycle import valid_container_host
    import docker
    import httpx
    version=c.execute(sql.SQL('SELECT version,checksum FROM {} ORDER BY version').format(sql.Identifier(schema,'schema_version'))).fetchall()
    from jasmine_web import vm_state
    expected=[dict(version=i,checksum=hashlib.sha256(Path(vm_state.__file__).with_name(n).read_bytes()).hexdigest())
              for i,n in enumerate(('vm_state_schema.sql','vm_state_002_shared_pool.sql'),1)]
    if version!=expected: raise ArtifactError('Apply reviewed SingleRun migrations before backup')
    deployment=c.execute(sql.SQL('SELECT deployment_id FROM {}').format(sql.Identifier(schema,'metadata'))).fetchone()['deployment_id']
    records=c.execute(sql.SQL('SELECT * FROM {} ORDER BY id').format(sql.Identifier(schema,'sessions'))).fetchall()
    sessions=[]; images=set()
    with docker.DockerClient(base_url='unix:///var/run/docker.sock',timeout=120) as client, httpx.Client(timeout=10,trust_env=False) as http:
        for record in records:
            key=hashlib.sha256(record['id'].encode()).hexdigest()
            detail=record['detail']
            saved=dict(id=record['id'],directory=f'singlerun/{schema}/{key}',archives=[],parameters=None,
                       active_output_omitted=False,download_allowed=False,expires_at=None)
            if detail.get('recovery'):
                # A later backup retains already restored closed exports too.
                recovery=detail['recovery']; origin=Path(recovery['directory'])
                from .backup_files import copy_tree
                target=destination/saved['directory']; target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                copy_tree(origin,target)
                saved.update(archives=recovery['archives'],parameters=recovery.get('parameters'),
                             download_allowed=recovery['download_allowed'],expires_at=recovery['expires_at'])
                sessions.append(saved); continue
            if record['status']!='ready':
                (destination/saved['directory']).mkdir(mode=0o700,parents=True)
                sessions.append(saved); continue
            container=client.containers.get(detail['container_id']); container.reload()
            labels=container.attrs.get('Config',{}).get('Labels') or {}
            if labels.get('jasmine.deployment_id')!=str(deployment) or not container_labels_match_session(labels,record['id'],record['model_id']):
                raise ArtifactError('SingleRun container identity differs from its private registry')
            network=client.networks.get(network_name(record['id'])); network.reload()
            host=private_container_host(container,network,record['id'],record['model_id'],str(deployment))
            if not valid_container_host(host) or host!=detail['host'] or container.status!='running':
                raise ArtifactError('SingleRun private container is unavailable')
            image=container.attrs['Image']
            if not re.fullmatch('sha256:[a-f0-9]{64}',image): raise ArtifactError('SingleRun image identity is not immutable')
            images.add(image)
            status=json_get(http,host,detail['backend_secret'],'simulation/status')
            parameters=json_get(http,host,detail['backend_secret'],'simulation/current-params') if status.get('built') else {}
            listed=json_get(http,host,detail['backend_secret'],'simulation/export/list',forbidden=True)
            target=destination/saved['directory']; target.mkdir(mode=0o700,parents=True)
            atomic_json(target/'parameters.json',parameters); saved['parameters']=saved['directory']+'/parameters.json'
            # Running CSVs are mutable. The engine can continue writing the newest
            # directory; all older runs are closed. A paused engine is protected
            # against Start/Step/Reset by schema_guard throughout this capture.
            stamps=sorted({r['timestamp'] for r in (listed or {}).get('files',[])})
            if any(not re.fullmatch('[A-Za-z0-9_-]{1,160}',v) for v in stamps): raise ArtifactError('Invalid SingleRun export timestamp')
            if status.get('status')=='running' and stamps:
                saved['active_output_omitted']=True; stamps=stamps[:-1]
            saved['download_allowed']=listed is not None
            for index,stamp in enumerate(stamps):
                temporary=target/'.capturing'
                try:
                    container_tree(container,'/app/output/'+stamp,temporary)
                    archive=target/f'output-{index:04d}.zip'; saved_zip(temporary,archive)
                    saved['archives'].append(dict(id=f'output-{index:04d}',timestamp=stamp,
                        file=saved['directory']+'/'+archive.name,**file_hash(target,archive.name)))
                finally:
                    if temporary.exists(): shutil.rmtree(temporary)
            # Original uploaded text/workbooks can reconstruct user inputs. An
            # open H2 database is deliberately never copied or called a checkpoint.
            inputs=target/'inputs'; inputs.mkdir(mode=0o700)
            input_sources=[]
            for base in ('/app/input','/app/.simpaths-startup/uploads'):
                result=container.exec_run(['find',base,'-type','f','(','-iname','*.csv','-o','-iname','*.txt','-o','-iname','*.xlsx','-o','-iname','*.xls',')','-printf','%P\0'])
                if result.exit_code:
                    if base.endswith('/uploads'): continue
                    raise ArtifactError('SingleRun inputs could not be inspected')
                if len(result.output)>2*1024**2: raise ArtifactError('SingleRun input inventory exceeds its limit')
                names=[v.decode('utf-8') for v in result.output.split(b'\0') if v]
                if len(names)>MAX_ENTRIES: raise ArtifactError('SingleRun input inventory exceeds its limit')
                for index,item in enumerate(names):
                    name(item)
                    temporary=inputs/('source-'+hashlib.sha256((base+'/'+item).encode()).hexdigest())
                    container_tree(container,base+'/'+item,temporary)
                    input_sources.append(dict(source=base+'/'+item,directory=temporary.relative_to(target).as_posix()))
            atomic_json(target/'capture.json',dict(status=status,model_id=record['model_id'],image=image,
                active_output_omitted=saved['active_output_omitted'],inputs='original text/workbooks only; no open H2 database',input_sources=input_sources))
            sessions.append(saved)
    return dict(images=sorted(images),sessions=sessions)


def validate_manifest(value):
    recovery=value['recovery']
    if not isinstance(recovery,dict) or set(recovery)!={'captured_at','interrupted_attempts','receiving_uploads'}:
        raise ArtifactError('Invalid online recovery inventory')
    try:
        at=datetime.fromisoformat(recovery['captured_at'])
        if at.tzinfo is None: raise ValueError()
    except (ValueError,TypeError): raise ArtifactError('Invalid online recovery time') from None
    for key in ('interrupted_attempts','receiving_uploads'):
        if (not isinstance(recovery[key],list) or len(recovery[key])>10000 or
                any(not isinstance(v,str) for v in recovery[key]) or len(set(recovery[key]))!=len(recovery[key])):
            raise ArtifactError('Invalid interrupted work inventory')
    if any(not re.fullmatch('[a-f0-9-]{36}',v) for v in recovery['interrupted_attempts']): raise ArtifactError('Invalid interrupted attempt')
    if not isinstance(value['vm'],dict) or len(value['vm'])>100: raise ArtifactError('Invalid SingleRun recovery schemas')
    for schema,record in value['vm'].items():
        if not re.fullmatch('[a-z][a-z0-9_]{0,62}',schema) or schema==value['schema'] or not isinstance(record,dict) or set(record)!={'database','images','sessions'}:
            raise ArtifactError('Invalid SingleRun recovery inventory')
        if not isinstance(record['sessions'],list) or len(record['sessions'])>10000: raise ArtifactError('Invalid SingleRun session inventory')
        from .backup import validate_database
        validate_database(record['database'])
        if (not isinstance(record['images'],list) or len(set(record['images']))!=len(record['images']) or
                any(v not in value['images'] for v in record['images'])):
            raise ArtifactError('Invalid SingleRun image inventory')
        ids=[]
        for saved in record['sessions']:
            if (not isinstance(saved,dict) or set(saved)!={'id','directory','archives','parameters','active_output_omitted','download_allowed','expires_at'} or
                    not isinstance(saved['id'],str) or not 1<=len(saved['id'])<=160 or
                    type(saved['active_output_omitted']) is not bool or type(saved['download_allowed']) is not bool or
                    not isinstance(saved['archives'],list) or len(saved['archives'])>10000):
                raise ArtifactError('Invalid SingleRun saved session')
            if saved['expires_at'] is not None:
                try:
                    if datetime.fromisoformat(saved['expires_at']).tzinfo is None: raise ValueError()
                except (ValueError,TypeError): raise ArtifactError('Invalid SingleRun recovery expiry') from None
            ids.append(saved['id'])
            if saved['directory']!=f"singlerun/{schema}/"+hashlib.sha256(saved['id'].encode()).hexdigest(): raise ArtifactError('Invalid SingleRun private export directory')
            for entry in saved['archives']:
                if (not isinstance(entry,dict) or set(entry)!={'id','timestamp','file','bytes','sha256','mode'} or
                        not re.fullmatch('output-[0-9]{4}',str(entry['id'])) or
                        not re.fullmatch('[A-Za-z0-9_-]{1,160}',str(entry['timestamp'])) or
                        entry['file']!=saved['directory']+'/'+entry['id']+'.zip'):
                    raise ArtifactError('Invalid SingleRun export reference')
                if entry['file'] not in value['state_tree']['files'] or {k:entry[k] for k in ('bytes','sha256','mode')}!=value['state_tree']['files'][entry['file']]: raise ArtifactError('SingleRun export checksum differs')
            if saved['parameters'] is not None and saved['parameters']!=saved['directory']+'/parameters.json': raise ArtifactError('Invalid saved SingleRun parameters')
            if saved['parameters'] and saved['parameters'] not in value['state_tree']['files']: raise ArtifactError('Missing SingleRun parameter snapshot')
            if saved['directory'] not in value['state_tree']['directories']: raise ArtifactError('Missing SingleRun recovery directory')
            if len(set(e['id'] for e in saved['archives']))!=len(saved['archives']): raise ArtifactError('Duplicate SingleRun archive')
        if len(set(ids))!=len(ids): raise ArtifactError('Duplicate SingleRun session')


def recover(c, schema, record, target, backup_id, captured):
    from psycopg import sql
    table=sql.Identifier(schema,'sessions')
    existing=c.execute(sql.SQL('SELECT id,detail FROM {} ORDER BY id').format(table)).fetchall()
    if [r['id'] for r in existing] != [r['id'] for r in record['sessions']]: raise ArtifactError('SingleRun recovery sessions differ from the database')
    for row,saved in zip(existing,record['sessions']):
        detail=dict(row['detail'])
        for key in ('container_id','host','backend_secret','reset_secret','build_token','build_started_at'):
            detail.pop(key,None)
        detail.update(status='failed',error='This session was interrupted by recovery from a backup. Saved exports remain available; launch a new session to run again.',
            recovery=dict(backup_id=backup_id,directory=str(target/saved['directory']),archives=saved['archives'],
                          parameters=saved['parameters'],download_allowed=saved['download_allowed'],active_output_omitted=saved['active_output_omitted'],
                          expires_at=saved['expires_at'] or (captured+timedelta(days=30)).isoformat()))
        c.execute(sql.SQL("UPDATE {} SET status='failed',status_changed_at=%s,detail=%s::jsonb,revision=revision+1 WHERE id=%s").format(table),
                  (captured,json.dumps(detail,allow_nan=False),row['id']))
    # A restored deployment must never claim or clean up source-host containers.
    # This changes only executor ownership in the isolated target, not cookies.
    c.execute(sql.SQL('UPDATE {} SET deployment_id=%s').format(sql.Identifier(schema,'metadata')),
              (uuid5(NAMESPACE_URL,'simpaths-recovery:'+backup_id+':'+schema),))
    # Old transient locks/rate windows remain as history until their normal expiry;
    # this nonexpiring marker prevents another frontend bypassing the inactive gate.
    c.execute(sql.SQL("DELETE FROM {} WHERE purpose='backup-restore-gate'").format(sql.Identifier(schema,'coordination')))
    c.execute(sql.SQL("INSERT INTO {}(kind,purpose,digest,expires_at,detail) VALUES ('lease','backup-restore-gate',%s,'9999-12-31',%s::jsonb) "
                      'ON CONFLICT(kind,purpose,digest) DO UPDATE SET detail=excluded.detail').format(sql.Identifier(schema,'coordination')),
              (hashlib.sha256(backup_id.encode()).hexdigest(),json.dumps(dict(root=str(target)))))
