"""(C) Copyright 2026, by Ross Richardson

Capture committed, verified Visualiser summaries/publications while omitting raw
scratch and live processors. Immutable receipts survive inactive restoration.
@author ross richardson
"""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import stat

from .artifacts import ArtifactError
from .backup_files import copy_selected,file_hash,read_metadata,scan

FORMAT='simpaths.visualiser.retained-backup.v1'
ROOT='execution/visualiser-cache'
HEX=re.compile('[a-f0-9]{64}')


def inventory(root):
    return dict(directories=sorted(k for k,v in scan(root).items() if stat.S_ISDIR(v[2])),
                files={k:file_hash(root,k) for k,v in scan(root).items() if stat.S_ISREG(v[2])})


def checked_hash(root,name,expected):
    actual=file_hash(root,name)
    if actual['sha256']!=expected['sha256'] or ('bytes' in expected and actual['bytes']!=expected['bytes']):
        raise ArtifactError('Retained Visualiser content differs from its receipt')


def publication(root,key,record):
    if (record.get('state')!='ready' or not isinstance(record.get('selection'),dict)
            or not isinstance(record.get('backend'),dict)):
        raise ArtifactError('Invalid ready Visualiser provenance')
    keys={key+'/status.json'}
    if record.get('components'):
        if (not isinstance(record['components'],list) or not record['components']
                or any(not isinstance(k,str) or not HEX.fullmatch(k) for k in record['components'])):
            raise ArtifactError('Invalid composite Visualiser dependencies')
        return keys
    if record.get('artifact_hash_format')!='aggregate-json-v1':
        raise ArtifactError('Reprepare legacy Visualiser files before including them in a new backup')
    checked_hash(root,key+'/published.json',dict(sha256=record['artifact_hash']))
    keys.add(key+'/published.json')
    if record.get('index_sha256'):
        index_name=key+'/views/index.json'
        checked_hash(root,index_name,dict(sha256=record['index_sha256']))
        index=read_metadata(root/index_name)
        if (index.get('format')!='simpaths.visualiser.index.v1'
                or index.get('publication')!=record['artifact_hash'] or not isinstance(index.get('sections'),dict)
                or len(index['sections'])>25000):
            raise ArtifactError('Invalid retained chart index')
        keys.add(index_name)
        for section,expected in index['sections'].items():
            if not HEX.fullmatch(section):raise ArtifactError('Invalid retained chart filename')
            name=key+'/views/'+section+'.json';checked_hash(root,name,expected);keys.add(name)
    return keys


def summary(root,key):
    prefix='summaries/'+key
    receipt=read_metadata(root/(prefix+'/receipt.json'))
    from .visualiser_summary_cache import FORMAT as SUMMARY_FORMAT,canonical
    import hashlib
    if (receipt.get('format')!=SUMMARY_FORMAT or receipt.get('key')!=key
            or not isinstance(receipt.get('identity'),dict)
            or hashlib.sha256(canonical(receipt['identity'])).hexdigest()!=key
            or type(receipt.get('bytes')) is not int or not 0<receipt['bytes']<=64*1024**2):
        raise ArtifactError('Invalid retained summary identity')
    checked_hash(root,prefix+'/metrics.json',receipt)
    return {prefix+'/receipt.json',prefix+'/metrics.json'},receipt['identity']


def known_source(c,queue,owner,attempt,identity=None):
    if c is None:return True  # File-only regression fixtures; production passes its SQL snapshot.
    from psycopg import sql
    row=c.execute(sql.SQL('SELECT identity FROM {} WHERE pool_id=%s AND owner_id=%s AND attempt_id=%s').format(
        sql.Identifier(queue.schema,'visualiser_sources')),(queue.pool_id,owner,attempt)).fetchone()
    return bool(row and (identity is None or row['identity']==identity))


def capture(state,destination,c=None,queue=None):
    from jasmine_web.batch.downloads import lock
    root=Path(state)/ROOT
    result=dict(format=FORMAT,publications=0,summaries=0)
    if not root.exists():return result
    if root.is_symlink():raise ArtifactError('Linked Visualiser storage')
    with ExitStack() as guards:
        guards.enter_context(lock(root/'maintenance.lock'))
        keys=set();records={}
        # Ready publication reads keep working: backup takes shared reader locks.
        for path in sorted(root.iterdir()):
            if not HEX.fullmatch(path.name):continue
            if path.is_symlink():raise ArtifactError('Linked Visualiser publication')
            try:guards.enter_context(lock(path/'job.lock',shared=True))
            except BlockingIOError:continue  # Never capture a partial live calculation.
            record=read_metadata(path/'status.json')
            if record.get('state')!='ready':continue
            selected=record.get('selection',{})
            if not isinstance(selected.get('jobs'),list) or not selected['jobs']:
                raise ArtifactError('Missing retained publication source identities')
            if not all(known_source(c,queue,selected.get('owner'),job.get('attempt'),job)
                       for job in selected.get('jobs',[])):
                continue  # Publication belongs to work newer than the SQL snapshot.
            records[path.name]=record
        # A composite is restorable only if every immutable component is included.
        for key,record in list(records.items()):
            if any(k not in records for k in record.get('components',[])):records.pop(key)
        for key,record in records.items():
            keys.update(publication(root,key,record));result['publications']+=1
        summaries=root/'summaries'
        if summaries.exists():
            if summaries.is_symlink():raise ArtifactError('Linked summary storage')
            guards.enter_context(lock(summaries/'.cache.lock'))
            for path in sorted(summaries.iterdir()):
                if not HEX.fullmatch(path.name):continue
                selected,identity=summary(root,path.name)
                attempt=identity.get('execution',{}).get('attempt')
                if known_source(c,queue,identity.get('owner'),attempt):
                    keys.update(selected);result['summaries']+=1
            for name in ('.summary-holds.json','.cache-usage.json'):
                if (summaries/name).exists():
                    read_metadata(summaries/name);keys.add('summaries/'+name)
        destination=Path(destination)/ROOT
        destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        copy_selected(root,destination,keys)
        verify(Path(destination).parents[1],result)
    return result


def verify(state,expected):
    if (not isinstance(expected,dict) or set(expected)!={'format','publications','summaries'}
            or expected['format']!=FORMAT or any(type(expected[k]) is not int or not 0<=expected[k]<=10000
                                                for k in ('publications','summaries'))):
        raise ArtifactError('Invalid Visualiser backup inventory')
    root=Path(state)/ROOT
    if not root.exists():
        if expected['publications'] or expected['summaries']:raise ArtifactError('Missing retained Visualiser files')
        return
    keys=set();counts=dict(publications=0,summaries=0);records={}
    for path in root.iterdir():
        if path.name=='summaries':continue
        if not HEX.fullmatch(path.name):raise ArtifactError('Unexpected Visualiser backup entry')
        record=read_metadata(path/'status.json');records[path.name]=record
        keys.update(publication(root,path.name,record));counts['publications']+=1
    for record in records.values():
        if any(k not in records for k in record.get('components',[])):
            raise ArtifactError('Missing retained composite component')
    summaries=root/'summaries'
    if summaries.exists():
        for path in summaries.iterdir():
            if path.name in ('.summary-holds.json','.cache-usage.json'):
                read_metadata(path);keys.add('summaries/'+path.name);continue
            if not HEX.fullmatch(path.name):raise ArtifactError('Unexpected summary backup entry')
            selected,_=summary(root,path.name);keys.update(selected);counts['summaries']+=1
    actual={k for k,v in scan(root).items() if stat.S_ISREG(v[2])}
    if actual!=keys or any(counts[k]!=expected[k] for k in counts):
        raise ArtifactError('Retained Visualiser inventory differs')
