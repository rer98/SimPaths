"""(C) Copyright 2026, by Ross Richardson

Bounded background attribution of allocated file bytes to selected MultiRun
accounts. Never read scientific rows, lock execution, or perform cleanup.
@author ross richardson
"""
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
from types import SimpleNamespace

FIELDS=('raw_bytes','workspace_other_bytes','uploads_bytes','prepared_bytes',
        'visualiser_bytes','downloads_bytes')
FORMAT='simpaths.account-storage.v1'


def empty():return dict.fromkeys((*FIELDS,'total_bytes'))


class AccountStorage:
    """One asynchronous sampler; at most eight bounded page snapshots in memory."""
    def __init__(self,service,state,executor,*,cache_seconds=300,scan_seconds=5,max_entries=200000):
        if (type(cache_seconds) not in (int,float) or not 1<=cache_seconds<=3600
                or type(scan_seconds) not in (int,float) or not 0<scan_seconds<=30
                or type(max_entries) is not int or not 1<=max_entries<=200000):
            raise ValueError('Invalid account storage observation bounds')
        self.service,self.state,self.executor=service,Path(state),executor
        self.cache_seconds,self.scan_seconds,self.max_entries=cache_seconds,scan_seconds,max_entries
        self._lock=threading.Lock();self._cache=OrderedDict();self._running=False;self._thread=None

    def __call__(self,owners):
        if (type(owners) is not tuple or len(owners)>50 or len(set(owners))!=len(owners)
                or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}',v) for v in owners)):
            raise ValueError('Require at most fifty selected account identities')
        if not owners:
            return dict(format=FORMAT,observed_at=datetime.now(timezone.utc).isoformat(),
                        measure_state='ready',accounts={})
        key=tuple(sorted(owners))
        with self._lock:
            cached=self._cache.get(key)
            fresh=cached is not None and time.monotonic()-cached[0]<self.cache_seconds
            if fresh:
                self._cache.move_to_end(key)
                return json.loads(json.dumps(cached[1]))
            if not self._running:
                self._running=True
                self._thread=threading.Thread(target=self._sample,args=(key,),
                    name='administrator-account-storage-observer',daemon=True)
                try:self._thread.start()
                except Exception:self._running=False
            return dict(format=FORMAT,observed_at=None,measure_state='pending',
                        accounts={owner:empty() for owner in owners})

    def _metadata(self,owners):
        q=self.service.queue
        with q._connection() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            c.execute("SET LOCAL statement_timeout='1s'")
            attempts=c.execute('''SELECT e.owner_id,a.execution_key,e.specification->>'operation' AS operation
                FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN experiments e ON e.id=j.experiment_id
                WHERE a.pool_id=%s AND e.owner_id=ANY(%s::text[]) ORDER BY a.id LIMIT 10001''',
                (q.pool_id,list(owners))).fetchall()
            uploads=c.execute('''SELECT owner_id,id FROM uploads WHERE pool_id=%s AND owner_id=ANY(%s::text[])
                ORDER BY id LIMIT 10001''',(q.pool_id,list(owners))).fetchall()
            prepared=c.execute('''SELECT r.owner_id,p.location FROM prepared_locations p JOIN dataset_records r
                ON r.pool_id=p.pool_id AND r.id=p.dataset_id WHERE p.pool_id=%s
                AND r.owner_id=ANY(%s::text[]) ORDER BY p.dataset_id LIMIT 10001''',
                (q.pool_id,list(owners))).fetchall()
        if any(len(rows)>10000 for rows in (attempts,uploads,prepared)):
            raise ValueError('Account storage metadata exceeds the observation bound')
        return attempts,uploads,prepared

    def _sample(self,owners):
        report=dict(format=FORMAT,observed_at=None,measure_state='incomplete',accounts={o:empty() for o in owners})
        try:report=self.measure(owners)
        except Exception:pass
        finally:
            with self._lock:
                self._cache[owners]=(time.monotonic(),report);self._cache.move_to_end(owners)
                while len(self._cache)>8:self._cache.popitem(last=False)
                self._running=False

    def measure(self,owners):
        deadline=time.monotonic()+self.scan_seconds
        attempts,uploads,prepared=self._metadata(owners)
        rows={owner:{name:0 for name in FIELDS} for owner in owners}
        entries=[0];inodes={};paths={};scopes={}

        def check():
            if time.monotonic()>=deadline or entries[0]>=self.max_entries:
                raise TimeoutError('Account storage observation reached its bound')

        check()

        def parents(path,*,missing=False):
            for parent in Path(path).parents:
                try:info=parent.lstat()
                except FileNotFoundError:
                    if missing:continue
                    raise
                if not stat.S_ISDIR(info.st_mode):raise ValueError('Linked storage parent is refused')

        def directory(path):
            path=Path(path)
            parents(path)
            fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            info=os.fstat(fd)
            if info.st_uid!=os.getuid() or info.st_mode&0o077:
                os.close(fd);raise ValueError('Require private service-owned storage')
            return fd

        def walk(path,owner,classify,*,missing=True,expected=None):
            check();path=Path(path)
            def ambiguous(other):
                for selected in (owner,other):
                    for field in FIELDS:rows[selected][field]=None
                raise ValueError('Physical storage has ambiguous account ownership')
            for parent in (path,*path.parents):
                assigned=paths.get(parent)
                if assigned is not None and assigned!=owner:ambiguous(assigned)
            for assigned in scopes.get(path,()):
                if assigned!=owner:ambiguous(assigned)
            if path in paths:
                return
            paths[path]=owner
            for parent in (path,*path.parents):scopes.setdefault(parent,set()).add(owner)
            parents(path,missing=True)
            try:info=path.lstat()
            except FileNotFoundError:
                if missing:return
                raise
            if expected is not None and (info.st_dev,info.st_ino)!=expected:
                raise ValueError('Account cache identity changed after reading its owner')
            def add(info,relative):
                check();entries[0]+=1
                if info.st_uid!=os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                    raise ValueError('Linked or nonordinary account files are refused')
                identity=(info.st_dev,info.st_ino)
                if identity in inodes:
                    if inodes[identity]!=owner:ambiguous(inodes[identity])
                    return
                inodes[identity]=owner
                category=classify(relative)
                if rows[owner][category] is not None:rows[owner][category]+=info.st_blocks*512
            if stat.S_ISREG(info.st_mode):
                parents(path)
                add(info,());return
            fd=directory(path);device=info.st_dev
            def descend(parent,relative=(),depth=0):
                if depth>64:raise ValueError('Account storage directory depth exceeds its bound')
                with os.scandir(parent) as children:
                    for child in children:
                        check();observed=child.stat(follow_symlinks=False)
                        if observed.st_dev!=device:raise ValueError('Nested mounts are not attributed to accounts')
                        selected=(*relative,child.name)
                        if stat.S_ISDIR(observed.st_mode):
                            entries[0]+=1
                            nested=os.open(child.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                            try:
                                current=os.fstat(nested)
                                if (current.st_dev,current.st_ino)!=(observed.st_dev,observed.st_ino) or current.st_uid!=os.getuid():
                                    raise ValueError('Account storage directory changed')
                                descend(nested,selected,depth+1)
                            finally:os.close(nested)
                        else:add(observed,selected)
            try:
                opened=os.fstat(fd)
                if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino):
                    raise ValueError('Account storage directory changed before observation')
                descend(fd)
            finally:os.close(fd)

        def counted(path,owner,fields,classify,*,expected=None):
            try:walk(path,owner,classify,expected=expected)
            except Exception:
                for field in fields:rows[owner][field]=None

        execution=Path(self.executor.root)
        try:fd=directory(execution);os.close(fd)
        except Exception:
            for owner in owners:rows[owner]['raw_bytes']=rows[owner]['workspace_other_bytes']=None
        else:
            for row in attempts:
                owner,key=row['owner_id'],row['execution_key']
                if owner not in rows:continue
                try:
                    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}',key) or key in ('.','..'):
                        raise ValueError('Invalid attempt identity')
                    if row['operation']=='prepare':targets=[]
                    else:
                        from jasmine_web.batch.output_management import target_names
                        targets=target_names(self.service.outputs.targets(SimpleNamespace(
                            specification=dict(operation=row['operation']))))
                    raw={('work',target) for target in targets}
                except Exception:
                    rows[owner]['raw_bytes']=rows[owner]['workspace_other_bytes']=None;continue
                counted(execution/key,owner,('raw_bytes','workspace_other_bytes'),
                    lambda relative,raw=raw:'raw_bytes' if relative[:2] in raw else 'workspace_other_bytes')
                counted(self.state/'artifacts'/('pipeline-'+key),owner,('workspace_other_bytes',),
                        lambda relative:'workspace_other_bytes')

        upload_root=Path(self.service.datasets.root)
        try:fd=directory(upload_root);os.close(fd)
        except Exception:
            for owner in owners:rows[owner]['uploads_bytes']=None
        else:
            for row in uploads:
                owner,key=row['owner_id'],row['id']
                if owner not in rows:continue
                if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',key):
                    rows[owner]['uploads_bytes']=None;continue
                counted(upload_root/key,owner,('uploads_bytes',),lambda relative:'uploads_bytes')
        for row in prepared:
            owner=row['owner_id']
            if owner not in rows:continue
            path=Path(row['location'])
            if (not path.is_absolute() or '..' in path.parts or path==self.state/'artifacts'
                    or not path.is_relative_to(self.state/'artifacts')):
                rows[owner]['prepared_bytes']=None;continue
            counted(path,owner,('prepared_bytes',),lambda relative:'prepared_bytes')

        def receipt_owner(path,receipt_name):
            check()
            fd=directory(path)
            try:
                opened=os.fstat(fd)
                pinned=(opened.st_dev,opened.st_ino)
                handle=os.open(receipt_name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                with os.fdopen(handle,'rb') as source:
                    info=os.fstat(source.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid()
                            or info.st_size>4*1024**2):raise ValueError('Untrusted cache ownership receipt')
                    value=json.loads(source.read(4*1024**2+1))
            finally:os.close(fd)
            identity=value.get('identity' if receipt_name=='receipt.json' else 'selection') if type(value) is dict else None
            owner=identity.get('owner') if type(identity) is dict else None
            if not isinstance(owner,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}',owner):
                raise ValueError('Cache ownership is unavailable')
            if receipt_name=='receipt.json':
                canonical=json.dumps(identity,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
                if (value.get('format')!='simpaths.visualiser.private-summary.v1'
                        or value.get('key')!=path.name or hashlib.sha256(canonical).hexdigest()!=path.name):
                    raise ValueError('Summary ownership receipt does not match its entry')
            return owner,pinned

        # These are disjoint entry roots; summaries nested in the Visualiser
        # root are traversed once separately, never included twice.
        visualiser=getattr(self.service,'visualiser',None)
        downloads=getattr(getattr(self.service,'results',None),'downloads',None)
        summary=getattr(getattr(visualiser,'backend',None),'summary_cache',None)
        caches=[(Path(getattr(visualiser,'root',execution/'visualiser-cache')),'status.json','visualiser_bytes',visualiser is not None),
                (Path(getattr(summary,'root',execution/'visualiser-cache'/'summaries')),'receipt.json','visualiser_bytes',summary is not None),
                (Path(getattr(downloads,'root',execution/'download-cache')),'status.json','downloads_bytes',downloads is not None)]
        for root,receipt_name,field,required in caches:
            try:
                parents(root,missing=True)
                try:root.lstat()
                except FileNotFoundError:
                    if required:raise
                    # Disabled optional caches can legitimately be absent.
                    continue
                fd=directory(root)
                try:
                    with os.scandir(fd) as items:
                        for item in items:
                            check();entries[0]+=1
                            if not re.fullmatch('[a-f0-9]{64}',item.name):continue
                            path=root/item.name
                            try:
                                owner,pinned=receipt_owner(path,receipt_name)
                                if owner in rows:counted(path,owner,(field,),lambda relative,field=field:field,expected=pinned)
                            except FileNotFoundError:
                                # A missing whole entry is normal concurrent
                                # removal; a surviving entry without its owner
                                # receipt must never be reported as empty.
                                try:path.lstat()
                                except FileNotFoundError:continue
                                for owner in owners:rows[owner][field]=None
                            except Exception:
                                for owner in owners:rows[owner][field]=None
                finally:os.close(fd)
            except Exception:
                for owner in owners:rows[owner][field]=None
        complete=True
        for row in rows.values():
            row['total_bytes']=sum(row.values()) if all(type(v) is int for v in row.values()) else None
            complete=complete and row['total_bytes'] is not None
        return dict(format=FORMAT,observed_at=datetime.now(timezone.utc).isoformat(),
                    measure_state='ready' if complete else 'incomplete',accounts=rows)
