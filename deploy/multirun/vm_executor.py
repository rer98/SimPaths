"""(C) Copyright 2026, by Ross Richardson

Private native domain service: durable jobs, Docker, raw data and database ownership.
Only a bounded authenticated Unix RPC interface is exposed; there is no HTTP listener.
@author ross richardson
"""
import argparse
import asyncio
import os
from pathlib import Path
import signal
import sys

from .privilege_config import separate_account, trusted_installation, trusted_python
from .vm_config import load_config
from .vm_web import check_storage, database_dsn, preflight, verification_mail


async def serve_private(bundle, socket_path, web_uid):
    from jasmine_web.async_cleanup import finish_before_cancelling
    from jasmine_web.batch.service_rpc import RPCServer
    stopped=asyncio.Event()
    loop=asyncio.get_running_loop()
    for signum in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(signum,stopped.set)
    server=RPCServer(bundle.service,socket_path,web_uid=web_uid,health=bundle.ready)
    try:
        async with bundle.lifespan(None):
            server.start()
            try:
                print('Private executor ready on the configured Unix socket.',flush=True)
                await stopped.wait()
            finally:
                # Drain owned uploads/downloads before closing file and DB guards.
                await finish_before_cancelling(asyncio.create_task(asyncio.to_thread(server.close)))
    finally:
        for signum in (signal.SIGTERM,signal.SIGINT):
            loop.remove_signal_handler(signum)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('check','preflight','serve'))
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        options=load_config(args.config)
        if args.command=='check':
            print('Private executor configuration is valid. No resources contacted.')
            return 0
        trusted_installation(args.config)
        trusted_python()
        trusted_installation(Path(__file__).resolve().parents[2],directory=True)
        trusted_installation(options.frontend,directory=True)
        if options.visualiser_build:
            trusted_installation(options.visualiser_build,directory=True)
        account=separate_account(options.rpc_frontend_user)
        sys.path.insert(0,str(options.frontend))
        os.umask(0o077)
        check_storage(options)
        from jasmine_web.batch.store import Queue
        from jasmine_web.batch.policy import Resources
        queue=Queue(database_dsn(options),options.pool_id)
        preflight(options,queue)
        if args.command=='preflight':
            print('Private executor storage, quotas, images, SMTP and database verified.')
            return 0
        from .maintenance import service_state
        from jasmine_web.batch.backup import database_guard
        from .runtime import create_application, create_registry
        with service_state(options.state),database_guard(queue):
            queue.migrate()
            access,datasets,preparations,keys=create_registry(options,queue,options.state,verification_mail,
                capacity=Resources(**options.capacity),per_user_active=options.per_user_active)
            bundle=create_application(options,queue,options.state,access,datasets,preparations,keys,
                options.image,options.origin,terminate_on_dispatch_failure=True,private_service=True)
            asyncio.run(serve_private(bundle,options.rpc_socket,account.pw_uid))
        return 0
    except Exception as error:
        # Database exceptions may contain credentials; only setup validation has public text.
        if isinstance(error,(ValueError,FileNotFoundError)):
            print('Executor setup failed: '+str(error),file=sys.stderr)
        else:
            print('Executor setup failed: '+type(error).__name__+'. Check private operator configuration.',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
