"""(C) Copyright 2026, by Ross Richardson

Public HTTP frontend with a bounded domain client and no private execution authority.
Configuration supplies only origin, listener, installed public code and peer identity.
@author ross richardson
"""
import argparse
from contextlib import asynccontextmanager
from pathlib import Path
import sys

from .privilege_config import load_public_config, public_identity_check, trusted_installation, trusted_python


SITE=dict(name='SimPaths Online',subtitle='UK MultiRun',logo='/static/simpaths-logo.svg',
          icon='/static/simpaths-favicon.svg')
FOOTER=(('SimPaths','https://simpaths.org'),('GitHub','https://github.com/simpaths/SimPaths'),
        ('CeMPA','https://www.microsimulation.ac.uk/'),
        ('License','https://github.com/simpaths/SimPaths/blob/main/license.txt'))


def create_public_application(remote,origin):
    from jasmine_web.batch.browser import create_app
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    @asynccontextmanager
    async def lifespan(app):
        remote.start()
        yield  # create_app drains requests and closes the remote facade.
    app=create_app(remote,origin=origin,lifespan=lifespan,worker_status=remote.worker_status,
                   site=SITE,footer_links=FOOTER)
    async def healthz(request):
        try:
            result=await run_in_threadpool(remote.health)
            ready=type(result) is dict and result.get('ready') is True
        except Exception:
            ready=False
        return JSONResponse({'ready':ready},status_code=200 if ready else 503)
    app.router.routes.append(Route('/healthz',healthz,methods=['GET']))
    return app


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('check','preflight','serve'))
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        options=load_public_config(args.config)
        if args.command=='check':
            print('Public frontend configuration is valid. No resources contacted.')
            return 0
        trusted_installation(args.config)
        trusted_python()
        trusted_installation(Path(__file__).resolve().parents[2],directory=True)
        trusted_installation(options.frontend,directory=True)
        account=public_identity_check(options.executor_user)
        sys.path.insert(0,str(options.frontend))
        from jasmine_web.batch.service_rpc import RPCClient
        from jasmine_web.batch.remote_service import RemoteService
        remote=RemoteService(RPCClient(options.rpc_socket,account.pw_uid))
        if args.command=='preflight':
            result=remote.health()
            if type(result) is not dict or result.get('ready') is not True:
                raise ValueError('Private executor is not ready')
            print('Public identity, installed code and private executor connection verified.')
            remote.close()
            return 0
        app=create_public_application(remote,options.origin)
        import uvicorn
        print('SimPaths Online public frontend: '+options.origin+'; private operations use Unix RPC.',flush=True)
        uvicorn.run(app,host='127.0.0.1',port=options.port,workers=1,
                    proxy_headers=True,forwarded_allow_ips='127.0.0.1',access_log=False)
        return 0
    except Exception as error:
        # Remote failures are deliberately generic: no private diagnostics at this boundary.
        print('Public frontend setup failed: '+type(error).__name__+'. Check installation and executor availability.',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
