"""(C) Copyright 2026, by Ross Richardson

Real frontend/worker/Docker state with tiny fictional models for crash recovery.
Fixture patches live only in temporary supervised processes, never production.
@author ross richardson
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import replace
from functools import partial
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from deploy._workflow import frontend_path

FRONTEND = frontend_path()
sys.path[:0] = [str(FRONTEND), str(FRONTEND/'tests/batch')]
from jasmine_web.batch.docker_executor import ContainerCommand, DockerCLI, DockerExecutor, DockerUnavailable
from jasmine_web.batch.local_executor import atomic_json
from jasmine_web.batch.policy import Conflict, Policy, Resources, fingerprint
from jasmine_web.batch.store import Queue
from browser_fixture import BrowserModel

POOL = 'recovery-rehearsal'
CAPACITY = Resources(500, 384, 96)
BATCH = Resources(250, 128, 32)
MODEL = dict(id='fictional-recovery', name='Fictional recovery simulation', requiresAuth=False,
    description='Crash recovery fixture; no scientific simulation.', deployment=dict(
        memory='256Mi', cpu=.25, disk_limit='64Mi'))


def validate(settings, dsn):
    from deploy.multirun.proxy_rehearsal import require_test_database
    from ._supervisor import safe_path
    if 'database_role' in settings:
        from ._postgres_outage import restricted_connection
        restricted_connection(settings, dsn)
    else:
        require_test_database(dsn)
    if not re.fullmatch(r'[a-f0-9]{32}', settings['tag']):
        raise ValueError('Invalid disposable recovery identity')
    for key in ('schema', 'batch_schema'):
        if not re.fullmatch(r'test_recovery_[a-f0-9]{32}', settings[key]):
            raise ValueError('Only disposable recovery schemas are permitted')
    if settings['schema'] == settings['batch_schema']:
        raise ValueError('Use distinct frontend and batch schemas')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', settings['image']):
        raise ValueError('Use an immutable local fixture image')
    ports = [settings[key] for key in ('single_port', 'batch_port')]
    if any(type(port) is not int or not 1024 <= port <= 65535 for port in ports) or len(set(ports)) != 2:
        raise ValueError('Use distinct unprivileged loopback ports')
    for key in ('state', 'single_state', 'inputs'):
        path = safe_path(settings[key])
        if not path.is_dir():
            raise ValueError('Missing private fixture directory')
    if not Path(settings['single_state']).is_relative_to(settings['state']) or not Path(settings['inputs']).is_relative_to(settings['state']):
        raise ValueError('Fixture directories must share a private root')
    return settings


def fixture_policy(**values):
    # Short leases/backoff keep a recovery proof bounded; runtime and retry caps
    # are frozen by the real submission/queue code and never reset on restart.
    return replace(Policy(**values), lease_seconds=5, retry_delay_seconds=1)


class SmallBrowser(BrowserModel):
    def experiment(self, resolved, configuration):
        value = super().experiment(resolved, configuration)
        value['resources'] = BATCH
        for run in value['run_sets']:
            run['execution']['resources'] = vars(BATCH)
        return value


class Adapter:
    def __init__(self, settings):
        self.settings = settings
        self.preparation = SimpleNamespace(retire_failed=lambda *a, **kw: None)

    def container_command(self, lease, request):
        shutil.copyfile(Path(__file__).with_name('_recovery_model.py'), request/'program.py')
        atomic_json(request/'settings.json', dict(seeds=lease.specification['seeds'],
            fingerprint=lease.specification['prepared_fingerprint'], attempt=lease.attempt_id))
        return ContainerCommand(self.settings['image'], ('/usr/local/bin/python', '/request/program.py'), self.settings['inputs'])

    def validate(self, lease, work):
        from deploy.multirun.proxy_fixture import catalogue
        values = json.loads((work/'results.json').read_text())
        if (work/'starts.txt').read_text() != 'started\n':
            raise ValueError('Fictional model was executed more than once')
        if [value['seed'] for value in values] != lease.specification['seeds']:
            raise ValueError('Fictional output seed mismatch')
        return catalogue(lease, work)


class GuardedDocker(DockerCLI):
    """One private fault switch hides daemon inspection, never its real state."""
    def __init__(self, state):
        self.state = Path(state)

    def inspect(self, name):
        if (self.state/'unconfirmed-docker').exists():
            marker = self.state/'unconfirmed-observed.json'
            if not marker.exists():
                atomic_json(marker, dict(observed=True))
            raise DockerUnavailable('Fictional unavailable Docker inspection')
        return super().inspect(name)


@contextmanager
def batch_services(settings, dsn):
    from jasmine_web.batch.worker import Worker
    from deploy.multirun import runtime
    from deploy.multirun.local_web import parse_args
    from deploy.multirun.resource_policy import LEGACY_POLICY
    from deploy.multirun import queue_adapter
    from deploy.multirun.proxy_fixture import catalogue, targets
    from result_fixture import result_name, input_catalogue, result_settings
    validate(settings, dsn)
    state = Path(settings['state'])
    queue = Queue(dsn, POOL, schema=settings['batch_schema']); queue.migrate()
    args = parse_args(['serve', '--console-codes', '--state', str(state), '--port', str(settings['batch_port'])])
    async def deliver(email, code):
        path = state/'mail.json'
        value = json.loads(path.read_text()) if path.exists() else {}
        value[email] = code; atomic_json(path, value)
    releases = {'approved': dict(image=settings['image'], name='Fictional recovery model',
        jar=state/'fictional.jar', defaults=state, resource_policy=LEGACY_POLICY)}
    class FaultWorker(Worker):
        @contextmanager
        def open(self):
            fault = state/'fail-dispatcher-once'
            if fault.exists():
                fault.unlink()
                raise Conflict('Fictional fatal dispatcher startup')
            with super().open() as worker:
                yield worker
    with ExitStack() as stack:
        stack.enter_context(patch.object(runtime, 'Policy', fixture_policy))
        stack.enter_context(patch.object(runtime, 'frozen_release', return_value=releases))
        stack.enter_context(patch.object(runtime, 'BrowserModel', side_effect=lambda *a, **kw: SmallBrowser()))
        stack.enter_context(patch.object(runtime, 'DispatchAdapter', return_value=Adapter(settings)))
        stack.enter_context(patch.object(runtime, 'Worker', FaultWorker))
        stack.enter_context(patch.object(runtime, 'DockerExecutor', side_effect=lambda root, **kwargs:
            DockerExecutor(root, docker=GuardedDocker(state), **kwargs)))
        for name, value in dict(result_catalogue=catalogue, result_name=result_name,
            result_deletion_targets=targets, result_inputs=input_catalogue, result_settings=result_settings).items():
            stack.enter_context(patch.object(queue_adapter, name, value))
        access, datasets, preparations, keys = runtime.create_registry(args, queue, state, deliver, capacity=CAPACITY)
        yield args, queue, access, datasets, preparations, keys


def bootstrap(settings, dsn):
    with batch_services(settings, dsn) as (_, queue, access, datasets, preparations, _):
        owners = {email: access.approve_email(email) for email in ('alice@example.org', 'bob@example.org')}
        source = b'FICTIONAL_RECOVERY_INPUT\n'
        (Path(settings['inputs'])/'input.txt').write_bytes(source)
        upload = datasets.receive(owners['alice@example.org'], 'fictional.csv', io.BytesIO(source), expected_bytes=len(source))
        dataset = datasets.publish(owners['alice@example.org'], fingerprint({'source':source.decode()}), uploads=[upload])
        queue.grant_dataset(owners['bob@example.org'], dataset)
        preparations.register_location(dataset, location=settings['inputs'], image=settings['image'])
        return dataset, owners


def serve_batch(settings, dsn):
    import uvicorn
    from deploy.multirun import runtime
    with batch_services(settings, dsn) as (args, queue, access, datasets, preparations, keys):
        app = runtime.create_application(args, queue, Path(settings['state']), access, datasets, preparations, keys,
            settings['image'], f"http://127.0.0.1:{settings['batch_port']}", local_codes=False,
            terminate_on_dispatch_failure=True)
        uvicorn.run(app, host='127.0.0.1', port=settings['batch_port'], workers=1, log_level='warning', access_log=False)


class Containers:
    """Real Docker identity/lifetime; Java HTTP replies remain fictional."""
    def __init__(self, settings):
        from ._https_fixture import FictionalContainers
        self.files = FictionalContainers(settings['single_state'])
        self.root = self.files.root
        self.settings, self.docker = settings, DockerCLI()

    def read(self, sid):
        return self.files.read(sid)

    def update(self, sid, **fields):
        return self.files.update(sid, **fields)

    def start_container(self, image, *, session_id, model_id, extra_env, **kwargs):
        from uuid import UUID
        UUID(session_id)
        if image != self.settings['image'] or model_id != MODEL['id'] or extra_env['SIM_ID'] != session_id or not extra_env['JASMINE_BACKEND_SECRET']:
            raise ValueError('Invalid fictional model identity')
        name = 'simpaths-recovery-'+self.settings['tag']+'-'+session_id
        identifier = self.docker.call('container', 'run', '-d', '--pull', 'never', '--name', name,
            '--label', 'simpaths.recovery='+self.settings['tag'],
            '--label', 'jasmine.session_id='+session_id, '--label', 'jasmine.model_id='+model_id,
            '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--memory', '256m', '--memory-swap', '256m', '--cpus', '.25', '--pids-limit', '32',
            '--user', f'{os.getuid()}:{os.getgid()}', '--restart', 'no', '--log-driver', 'none',
            '--entrypoint', '/usr/local/bin/python', image, '-c', 'import time; time.sleep(900)')
        atomic_json(self.files.path(session_id), dict(id=identifier, sid=session_id, model=model_id,
            created_at=time.time(), memory_bytes=256*1024**2, removed=False, status='paused', built=False,
            parameters={'seed':606, 'endYear':2026}))
        return identifier, '172.30.0.2'

    def list_containers(self, *, include_stopped=False, **kwargs):
        identifiers = self.docker.call('container', 'ls', '-aq', '--filter', 'label=simpaths.recovery='+self.settings['tag']).splitlines()
        values = []
        for identifier in identifiers:
            value = self.docker.inspect(identifier)
            if value is None or (not include_stopped and not value['State']['Running']):
                continue
            labels = value['Config']['Labels']
            record = self.read(labels['jasmine.session_id'])
            values.append(dict(id=value['Id'], image=[], status=value['State']['Status'],
                labels=labels, created_at=record['created_at']))
        return values

    def stop_container(self, identifier, *, session_id, model_id, **kwargs):
        value = self.docker.inspect(identifier)
        if value is None:
            return True
        expected = {'simpaths.recovery':self.settings['tag'], 'jasmine.session_id':session_id, 'jasmine.model_id':model_id}
        if any(value['Config']['Labels'].get(key) != field for key, field in expected.items()):
            return False
        self.docker.call('container', 'rm', '-f', identifier)
        self.update(session_id, removed=True)
        return self.docker.inspect(identifier) is None


def single_environment(settings, dsn):
    return dict(DEPLOY_MODE='vm', VM_STATE_BACKEND='postgres', VM_POSTGRES_DSN=dsn,
        VM_POSTGRES_SCHEMA=settings['schema'], VM_SHARED_POOL_ID=POOL, VM_SHARED_BATCH_SCHEMA=settings['batch_schema'],
        JASMINE_SKIP_BACKEND_INIT='1', SESSION_SECRET=settings['secret'], ADMIN_PASSWORD=settings['admin'],
        COOKIE_SECURE='false', ENABLE_HSTS='false', BROWSER_ORIGINS=f"http://127.0.0.1:{settings['single_port']}",
        VM_SECURITY_MODE='development', VM_MAX_SESSIONS='4', VM_MAX_SESSIONS_PER_CLIENT='4',
        VM_SESSION_MEMORY_BUDGET_MIB='1024', JASMINE_CATALOGUE_FILE=settings['catalogue'])


def load_single_app(settings):
    from fasthtml.common import fast_app
    # Keep the framework's usual file-backed key inside the disposable fixture.
    # Never read or write the maintainer checkout's .sesskey during a rehearsal.
    with patch('dotenv.load_dotenv', return_value=False), patch('fasthtml.common.fast_app',
            new=partial(fast_app, key_fname=str(Path(settings['single_state'])/'.sesskey'))):
        spec = importlib.util.spec_from_file_location('single_recovery_app', FRONTEND/'app.py')
        web = importlib.util.module_from_spec(spec); spec.loader.exec_module(web)
    return web


def serve_single(settings, dsn):
    from jasmine_web.vm_state import get_vm_state
    from ._https_fixture import FictionalJava
    import httpx
    import uvicorn
    validate(settings, dsn)
    os.environ.pop('VM_POSTGRES_DSN_FILE', None)
    os.environ.update(single_environment(settings, dsn))
    containers = Containers(settings)
    sys.modules['container_manager'] = containers
    web = load_single_app(settings)
    import session_manager
    from jasmine_web.backends import VMBackend
    web.backend = VMBackend(web.get_model_by_id, session_manager, containers, threading.RLock(), 100, 120)
    web.backend.on_app_startup()
    java = FictionalJava(get_vm_state(), containers, settings['archive'])
    web._vm_http_client = httpx.AsyncClient(transport=httpx.MockTransport(java.response), trust_env=False)
    uvicorn.run(web.app, host='127.0.0.1', port=settings['single_port'], workers=1, log_level='warning', access_log=False)


def serve_limit(settings, dsn):
    validate(settings, dsn)
    path = Path(settings['state'])/'failed-starts.json'
    starts = json.loads(path.read_text()) if path.exists() else []
    starts.append(dict(pid=os.getpid(), at=time.time()))
    atomic_json(path, starts)
    raise SystemExit(42)
