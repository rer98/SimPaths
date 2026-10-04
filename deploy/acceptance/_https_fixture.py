"""(C) Copyright 2026, by Ross Richardson

Fictional SingleRun model for a real frontend/PostgreSQL/HTTPS transport proof.
Only Docker provisioning and Java replies are replaced; production imports none
of this module. Private fixture files survive a frontend process restart.
@author ross richardson
"""
import asyncio
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch
from uuid import uuid4
import zipfile

import httpx

from deploy._workflow import frontend_path

sys.path.insert(0, str(frontend_path()))
from jasmine_web.batch.local_executor import atomic_json

RAW = b'FICTIONAL_SINGLE_RUN_PRIVATE_RECORD'
MODEL = dict(id='fictional-https', name='Fictional HTTPS simulation', requiresAuth=False,
    description='Transport fixture; no scientific simulation.', deployment=dict(
        image='sha256:'+'a'*64, memory='512Mi', cpu=1, disk_limit='1Gi'))


def create_archive(path, payload_mib):
    """Write a valid sizeable ZIP without constructing the payload in memory."""
    chunk = (RAW+b',2019,1\n')*2048
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_STORED) as archive:
        with archive.open('run_1/csv/Values.csv', 'w', force_zip64=True) as output:
            remaining = payload_mib*1024**2
            while remaining:
                part = chunk[:min(remaining, len(chunk))]
                output.write(part); remaining -= len(part)
        archive.writestr('run_1/input/options.txt', 'seed=606\nstartYear=2019\nendYear=2026\n')


class FictionalContainers:
    """Identity-checked model stand-in; never connects to the Docker daemon."""
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()

    def path(self, sid):
        from uuid import UUID
        UUID(sid)
        return self.root/('model-'+sid+'.json')

    def read(self, sid):
        with self.lock:
            return json.loads(self.path(sid).read_text())

    def update(self, sid, **fields):
        with self.lock:
            value = self.read(sid); value.update(fields)
            atomic_json(self.path(sid), value)
            return value

    def start_container(self, image, *, session_id, model_id, extra_env, **kwargs):
        if image != MODEL['deployment']['image'] or model_id != MODEL['id']:
            raise ValueError('Unknown fictional model')
        if extra_env['SIM_ID'] != session_id or not extra_env['JASMINE_BACKEND_SECRET']:
            raise ValueError('Missing model identity/credential')
        value = dict(id='fictional-'+session_id, sid=session_id, model=model_id,
            created_at=time.time(), memory_bytes=512*1024**2, removed=False,
            status='paused', built=False, parameters={'seed':606, 'endYear':2026})
        with self.lock:
            if self.path(session_id).exists():
                raise ValueError('Fictional model already exists')
            atomic_json(self.path(session_id), value)
        return value['id'], '172.30.0.2'

    def list_containers(self, **kwargs):
        with self.lock:
            values = [json.loads(path.read_text()) for path in self.root.glob('model-*.json')]
        return [dict(value, labels={'jasmine.session_id':value['sid'], 'jasmine.model_id':value['model']})
                for value in values if not value['removed']]

    def stop_container(self, identifier, *, session_id, model_id, **kwargs):
        value = self.read(session_id)
        if value['id'] != identifier or value['model'] != model_id:
            return False
        self.update(session_id, removed=True)
        return True


class FileStream(httpx.AsyncByteStream):
    """Track upstream close exactly once, including client cancellation."""
    def __init__(self, archive, root, sid):
        self.source = Path(archive).open('rb')
        self.path = Path(root)/('stream-'+uuid4().hex+'.json')
        self.record = dict(session=sid, closed=False, close_count=0, bytes=0)
        atomic_json(self.path, self.record)

    async def __aiter__(self):
        while chunk := self.source.read(65536):
            self.record['bytes'] += len(chunk)
            await asyncio.sleep(.001)
            yield chunk

    async def aclose(self):
        self.record['close_count'] += 1
        self.source.close()
        self.record['closed'] = True
        atomic_json(self.path, self.record)


class FictionalJava:
    def __init__(self, store, containers, archive):
        self.store, self.containers, self.archive = store, containers, Path(archive)

    async def response(self, request):
        records = await asyncio.to_thread(self.store.get_all_sessions)
        credential = request.headers.get('X-Jasmine-Backend-Token')
        record = next((value for value in records if credential and value.get('backend_secret') == credential), None)
        if record is None:
            return httpx.Response(403, json={'error':'Fictional backend credential required'})
        sid = record['session_id']; value = self.containers.read(sid)
        if value['removed']:
            return httpx.Response(404, json={'error':'Fictional model removed'})
        path = request.url.path
        action = path.rsplit('/', 1)[-1]
        if path == '/simulation/export/zip':
            headers = {'Content-Type':'application/zip', 'Content-Disposition':'attachment; filename="fictional-run.zip"'}
            if request.url.params.get('length') == '1':
                headers['Content-Length'] = str(self.archive.stat().st_size)
            return httpx.Response(200, headers=headers, stream=FileStream(self.archive, self.containers.root, sid))
        if action in ('start', 'pause', 'reset', 'build'):
            fields = dict(status='running' if action == 'start' else 'paused')
            if action == 'build':
                fields.update(built=True, parameters=json.loads(request.content))
            if action == 'reset': fields['built'] = False
            self.containers.update(sid, **fields)
            return httpx.Response(200, json={'status':dict(start='started',pause='paused',reset='reset',build='built')[action]})
        if action == 'status':
            return httpx.Response(200, json=dict(status=value['status'], time=2019, built=value['built']))
        if action == 'charts':
            return httpx.Response(200, json=dict(charts=[dict(name='Fictional outcome', data=[dict(
                x=2019, y=value['parameters'].get('seed',606))])], time=2019))
        if action == 'parameters':
            return httpx.Response(200, json=value['parameters'])
        if path == '/simulation/logs/poll':
            return httpx.Response(200, json=dict(logs=[dict(message='Fictional HTTPS model')],firstIndex=0,nextIndex=1))
        return httpx.Response(404, json={'error':'Unsupported fictional Java route'})


def fixture_environment(dsn, settings):
    """Validate isolation before changing environment or loading the application."""
    from deploy.multirun.proxy_rehearsal import require_test_database
    import re
    require_test_database(dsn)
    for field in ('schema', 'batch_schema'):
        if not re.fullmatch(r'test_single_https_[a-f0-9]{32}', settings[field]):
            raise ValueError('Only disposable HTTPS schemas are permitted')
    return dict(DEPLOY_MODE='vm', VM_STATE_BACKEND='postgres', VM_POSTGRES_DSN=dsn,
        VM_POSTGRES_SCHEMA=settings['schema'], VM_SHARED_POOL_ID='single-https',
        VM_SHARED_BATCH_SCHEMA=settings['batch_schema'], JASMINE_SKIP_BACKEND_INIT='1',
        SESSION_SECRET=settings['secret'], ADMIN_PASSWORD=settings['admin'],
        COOKIE_SECURE='true', ENABLE_HSTS='true', BROWSER_ORIGINS=settings['origin'],
        VM_SECURITY_MODE='development', VM_MAX_SESSIONS='8', VM_MAX_SESSIONS_PER_CLIENT='8',
        VM_SESSION_MEMORY_BUDGET_MIB='2048', JASMINE_CATALOGUE_FILE=settings['catalogue'])


def serve(settings):
    """Load the real app with real state; fake model endpoints exist only here."""
    from jasmine_web.vm_state import get_vm_state
    import uvicorn
    environment = fixture_environment(os.environ['JASMINE_BATCH_TEST_DSN'], settings)
    os.environ.pop('VM_POSTGRES_DSN_FILE', None)
    os.environ.update(environment)
    containers = FictionalContainers(settings['state'])
    sys.modules['container_manager'] = containers
    root = frontend_path()
    with ExitStack() as stack:
        stack.enter_context(patch('dotenv.load_dotenv', return_value=False))
        spec = importlib.util.spec_from_file_location('single_https_app', root/'app.py')
        web = importlib.util.module_from_spec(spec); spec.loader.exec_module(web)
    import session_manager
    from jasmine_web.backends import VMBackend
    web.backend = VMBackend(web.get_model_by_id, session_manager, containers, threading.RLock(), 100, 120)
    web.backend.on_app_startup()
    java = FictionalJava(get_vm_state(), containers, settings['archive'])
    web._vm_http_client = httpx.AsyncClient(transport=httpx.MockTransport(java.response), trust_env=False)
    uvicorn.run(web.app, host='127.0.0.1', port=settings['app_port'], workers=1, access_log=False,
                log_level='warning', proxy_headers=True, forwarded_allow_ips='127.0.0.1')
