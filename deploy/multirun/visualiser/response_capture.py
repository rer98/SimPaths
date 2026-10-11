"""(C) Copyright 2026, by Ross Richardson

Bounded ASGI response evidence for fictional and retained-result browser proofs.
Only response streams are recorded; no model or browser dependencies are imported.
@author ross richardson
"""
import hashlib
import os
from pathlib import Path
import re

from deploy.multirun.artifacts import ArtifactError

MIB=1024**2


class AggregateCapture:
    """Record the actual ASGI response stream without Chromium's inspector cache.

    This wrapper belongs only to the proof. It forwards every message unchanged,
    saves one bounded successful aggregate response privately and checksums later
    responses. Headers/cookies and unrelated or denied response bodies are not saved.
    """
    def __init__(self,app,output,maximum_bytes):
        self.app,self.output,self.maximum_bytes=app,Path(output),maximum_bytes
        self.destination=self.output/'delivered-aggregates.json'
        if self.destination.exists():raise FileExistsError('Aggregate evidence already exists')
        self.responses=[];self.errors=[];self.sequence=0

    async def __call__(self,scope,receive,send):
        if (scope.get('type')!='http' or scope.get('method')!='GET'
                or not re.fullmatch(r'/api/visualiser/[a-f0-9]{64}/(data|catalogue|view)',scope.get('path',''))):
            return await self.app(scope,receive,send)
        query=scope.get('query_string',b'').decode('ascii')
        identity=scope['path']+'?'+query
        destination=(self.destination if scope['path'].endswith('/data') else
                     self.output/('delivered-section-'+hashlib.sha256(identity.encode()).hexdigest()[:24]+'.json'))
        maximum=(512*1024 if scope['path'].endswith('/catalogue') else
                 8*MIB if scope['path'].endswith('/view') else self.maximum_bytes)
        self.sequence+=1
        temporary=self.output/f'.aggregate-capture-{self.sequence}.partial'
        stream=None;status=None;size=0;digest=hashlib.sha256();complete=False;owned=False
        async def observed_send(message):
            nonlocal stream,status,size,complete,owned
            await send(message)
            if message['type']=='http.response.start':status=message['status']
            elif message['type']=='http.response.body' and status==200:
                body=message.get('body',b'');size+=len(body)
                if size>maximum:raise ArtifactError('Captured aggregate response exceeds its bound')
                digest.update(body)
                if stream is None and not destination.exists():
                    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);owned=True
                    stream=os.fdopen(fd,'wb')
                if stream is not None:stream.write(body)
                if not message.get('more_body',False):
                    complete=True
                    if stream is not None:
                        stream.flush();os.fsync(stream.fileno());stream.close();stream=None
                        # No-clobber publication; remove the temporary link before
                        # any verification reads the completed evidence file.
                        try:os.link(temporary,destination,follow_symlinks=False)
                        except FileExistsError:pass
                        temporary.unlink()
                    self.responses.append(dict(path=scope['path'],status=status,bytes=size,
                        query=query,file=destination.name,sha256=digest.hexdigest(),capture='completed-asgi-response-v1'))
        try:
            await self.app(scope,receive,observed_send)
            if status==200 and not complete:raise ArtifactError('Captured aggregate response did not finish')
        except Exception as error:
            self.errors.append(dict(error_type=type(error).__name__,error=str(error)[:500]));raise
        finally:
            if stream is not None:stream.close()
            if owned:temporary.unlink(missing_ok=True)


def observe_browser_response(response,responses):
    """Event callbacks read metadata only; never ask DevTools to retain large bodies."""
    if re.search(r'/api/visualiser/[a-f0-9]{64}/(data|catalogue|view)(\?|$)',response.url):
        responses.append(dict(url=response.url,status=response.status))
