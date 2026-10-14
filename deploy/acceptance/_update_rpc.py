"""(C) Copyright 2026, by Ross Richardson

Separated native Unix RPC services for disposable application update acceptance.
Only installed listener/peer setup is adapted to one non-root test UID. Production
framing, authentication, admission, streaming and private lifecycles are retained.
This fixture does not establish installed account or filesystem privilege isolation.
@author ross richardson
"""
import asyncio
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import sys
from unittest.mock import patch

from jasmine_web.batch import service_rpc as rpc
from . import _application_update as releases
from ._single_rpc import public_environment

PRIVATE_MODULES=('app','session_manager','container_manager','jasmine_web.vm_state',
                 'jasmine_web.batch.store','deploy.multirun.runtime')
PUBLIC_FIELDS={'bundle','generation','origin','port','socket','boot','role'}


def fixture_path(value, *, existing=False, mode=0o660):
    """Accept only a short owned socket inside a private temporary directory."""
    path=releases.ordinary(value)
    if '..' in path.parts or len(os.fsencode(path))>100 or os.getuid()==0:
        raise ValueError('Use a short socket path under a non-root fixture identity')
    info=path.parent.stat()
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
        raise ValueError('Fixture socket directory must be owned and mode 0700')
    if existing:
        info=path.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid()
                or stat.S_IMODE(info.st_mode)!=mode):
            raise ValueError('Invalid disposable socket')
    return path


def boot_record(settings, role, modules):
    """Record actual loaded code and PID without credentials or private bodies."""
    sources={}
    bundle=settings['bundle']
    for name,module in modules.items():
        path=releases.ordinary(module.__file__)
        component=next((key for key in ('host','frontend')
                        if path.is_relative_to(bundle[key]['directory'])),None)
        if component is None:
            raise ValueError('Fixture imported code outside its selected bundle')
        row=releases.loaded_source(module,bundle[component]['directory'])
        if bundle[component]['files'].get(row['file'])!=row['sha256']:
            raise ValueError('Loaded source differs from its verified release')
        sources[name]=dict(component=component,**row)
    private_imports=[name for name in PRIVATE_MODULES if name in sys.modules]
    private_environment=[name for name in os.environ
        if name not in public_environment(os.environ) and name!='JASMINE_WEB_REPO']
    if role.endswith('_web') and (private_imports or private_environment):
        raise ValueError('Public fixture loaded private authority or environment')
    value=dict(pid=os.getpid(),role=role,host_commit=bundle['host']['commit'],
        frontend_commit=bundle['frontend']['commit'],sources=sources,
        private_imports=private_imports,private_environment_fields=private_environment,
        same_uid_fixture=True,host_privilege_isolation_tested=False)
    from jasmine_web.batch.local_executor import atomic_json
    atomic_json(Path(settings['boot']),value)
    return value


class FixtureBatchServer(rpc.RPCServer):
    def __init__(self, service, socket_path, *, web_uid, **options):
        if web_uid!=os.getuid():
            raise ValueError('Only the current disposable UID is supported')
        super().__init__(service,socket_path,web_uid=os.getuid()+1,**options)
        self.web_uid=os.getuid()
        self.operations=Counter()

    def start(self):
        def checked(path, uid, *, existing_socket):
            if uid!=os.getuid():
                raise ValueError('Wrong disposable socket owner')
            fixture_path(path,existing=existing_socket)
        with patch.object(rpc,'_trusted_path',checked):
            return super().start()

    def dispatch(self, operation, values):
        with self.lock:
            self.operations[operation]+=1
        return super().dispatch(operation,values)


class FixtureBatchClient(rpc.RPCClient):
    def __init__(self, path, timeout=60):
        super().__init__(path,os.getuid()+1,timeout=timeout)

    def _connect(self, operation, values):
        path=fixture_path(self.path,existing=True)
        connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            connection.settimeout(self.timeout)
            connection.connect(str(path))
            if rpc.peer_uid(connection)!=os.getuid():
                raise PermissionError('Wrong disposable private peer')
            data=json.dumps(dict(operation=operation,values=values),allow_nan=False,
                            separators=(',',':')).encode()
            if len(data)>rpc.REQUEST_LIMIT:
                raise ValueError('Request too large')
            rpc._write_frame(connection,data)
            return connection
        except BaseException:
            connection.close()
            raise


def single_fixture(bundle):
    path=Path(bundle['frontend']['directory'])/'tests/browser/single_rpc_fixture.py'
    spec=importlib.util.spec_from_file_location('update_single_rpc_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def public_settings(settings, role, *, boot):
    if role not in ('single_web','batch_web'):
        raise ValueError('Expected a public fixture role')
    component=role.removesuffix('_web')
    return dict(bundle=settings['bundle'],generation=settings['generation'],
        origin=f"http://127.0.0.1:{settings[component+'_port']}",
        port=settings[component+'_port'],socket=settings['rpc_sockets'][component],
        role=role,boot=str(boot))


def serve_public(settings, role):
    if set(settings)!=PUBLIC_FIELDS or settings['role']!=role or role not in ('single_web','batch_web'):
        raise ValueError('Public settings must contain only code and transport information')
    if type(settings['port']) is not int or settings['origin']!=f"http://127.0.0.1:{settings['port']}":
        raise ValueError('Only the disposable loopback origin is supported')
    fixture_path(settings['socket'])
    environment=public_environment(os.environ)
    environment['JASMINE_WEB_REPO']=settings['bundle']['frontend']['directory']
    os.environ.clear();os.environ.update(environment)
    if role=='single_web':
        helper=single_fixture(settings['bundle'])
        import jasmine_web.single_rpc as gateway
        boot_record(settings,role,dict(single_gateway=gateway,fixture=sys.modules[__name__],single_fixture=helper))
        helper.serve_public({key:settings[key] for key in ('origin','port','socket')})
    else:
        from jasmine_web.batch import browser,remote_service
        from deploy.multirun import vm_frontend
        app=vm_frontend.create_public_application(remote_service.RemoteService(
            FixtureBatchClient(settings['socket'])),settings['origin'])
        boot_record(settings,role,dict(browser=browser,remote=remote_service,frontend=vm_frontend,
                                     protocol=rpc,fixture=sys.modules[__name__]))
        import uvicorn
        uvicorn.run(app,host='127.0.0.1',port=settings['port'],workers=1,
                    log_level='warning',access_log=False)


def serve_single_private(settings, dsn):
    from . import _recovery_fixture as fixture
    from ._https_fixture import FictionalJava
    from jasmine_web.vm_state import get_vm_state
    import httpx
    import threading
    fixture.validate(settings,dsn)
    os.environ.pop('VM_POSTGRES_DSN_FILE',None)
    os.environ.update(fixture.single_environment(settings,dsn))
    containers=fixture.Containers(settings)
    sys.modules['container_manager']=containers
    web=fixture.load_single_app(settings)
    import session_manager
    from jasmine_web.backends import VMBackend
    web.backend=VMBackend(web.get_model_by_id,session_manager,containers,threading.RLock(),100,120)
    web.backend.on_app_startup()
    web._vm_http_client=httpx.AsyncClient(transport=httpx.MockTransport(
        FictionalJava(get_vm_state(),containers,settings['archive']).response),trust_env=False)
    helper=single_fixture(settings['bundle'])
    class ReadyServer(helper.FixtureServer):
        async def start(self):
            await super().start()
            import jasmine_web.single_rpc as protocol
            boot_record(settings,'single',dict(single_app=web,session_manager=session_manager,
                protocol=protocol,fixture=sys.modules[__name__]))
    with patch.object(helper,'FixtureServer',ReadyServer):
        asyncio.run(helper.run_private(web,settings['rpc_sockets']['single'],
            f"http://127.0.0.1:{settings['single_port']}",settings['rpc_evidence']))


def serve_batch_private(settings, dsn):
    from . import _recovery_fixture as fixture
    from deploy.multirun import runtime,vm_executor
    from jasmine_web.batch import store
    from jasmine_web import vm_state
    from jasmine_web.batch.local_executor import atomic_json
    value=dict(pid=os.getpid(),closed=False,errors=[],operation_counts={},same_uid_fixture=True,
               host_privilege_isolation_tested=False)
    servers=[]
    class ReadyServer(FixtureBatchServer):
        def start(self):
            result=super().start();servers.append(self)
            boot_record(settings,'batch',dict(runtime=runtime,executor=vm_executor,queue=store,
                vm=vm_state,protocol=rpc,fixture=sys.modules[__name__]))
            return result
    try:
        with fixture.batch_services(settings,dsn) as (args,queue,access,datasets,preparations,keys):
            bundle=runtime.create_application(args,queue,Path(settings['state']),access,datasets,preparations,keys,
                settings['image'],f"http://127.0.0.1:{settings['batch_port']}",local_codes=False,
                terminate_on_dispatch_failure=True,private_service=True)
            with patch.object(rpc,'RPCServer',ReadyServer):
                asyncio.run(vm_executor.serve_private(bundle,settings['rpc_sockets']['batch'],os.getuid()))
    except BaseException as error:
        value['errors'].append(type(error).__name__)
        raise
    finally:
        value['closed']=True
        for server in servers:
            value['operation_counts']=dict(server.operations)
            if server.threads or server.thread.is_alive():
                value['errors'].append('UndrainedRPCHandlers')
        atomic_json(Path(settings['rpc_evidence']),value)
