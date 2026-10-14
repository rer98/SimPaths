#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Read-only native two-UID acceptance for an installed private executor boundary.
# @author ross richardson
import argparse
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def denied_read(path):
    try:
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    except PermissionError:
        return True
    else:
        os.close(descriptor)
        return False


def denied_socket(path):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        try:
            connection.connect(path)
        except PermissionError:
            return True
        return False


def child(settings):
    sys.path.insert(0,settings['frontend'])
    from jasmine_web.batch.service_rpc import RPCClient, RPCRemoteError
    client=RPCClient(Path(settings['socket']),settings['executor_uid'],timeout=10)
    if settings['role']=='unrelated':
        try:
            client.call('health')
        except (OSError,EOFError,RuntimeError,ValueError,RPCRemoteError):
            return {'unrelated_peer_rejected':True}
        raise AssertionError('Unrelated Linux account reached the private service')
    checks={name:denied_read(path) for name,path in settings['private_files'].items()}
    checks.update({name:denied_socket(path) for name,path in settings['privileged_sockets'].items()})
    checks['installed_code_not_writable']=not os.access(settings['frontend'],os.W_OK) and not os.access(str(ROOT),os.W_OK)
    health=client.call('health')
    checks['authenticated_private_health']=type(health) is dict and health.get('ready') is True
    try:
        client.call('docker.run',image='arbitrary',mount='/')
    except RPCRemoteError:
        checks['arbitrary_docker_operation_rejected']=True
    else:
        checks['arbitrary_docker_operation_rejected']=False
    if not all(checks.values()):
        raise AssertionError('A native privilege-boundary check failed')
    return checks


def run_as(account,settings):
    def drop():
        os.initgroups(account.pw_name,account.pw_gid)
        if settings['role']=='unrelated':
            # Give only this disposable child the socket group, so the actual
            # SO_PEERCRED rejection is tested beyond ordinary directory denial.
            os.setgroups(sorted(set(os.getgroups())|{settings['rpc_gid']}))
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    environment={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8'}
    result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child'],
        input=json.dumps(settings),text=True,capture_output=True,timeout=60,
        env=environment,cwd=str(ROOT),preexec_fn=drop)
    if result.returncode:
        raise ValueError('Native account proof failed; check installed permissions and executor health')
    value=json.loads(result.stdout)
    if type(value) is not dict or not value or any(v is not True for v in value.values()):
        raise ValueError('Invalid native account proof response')
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-config',type=Path)
    parser.add_argument('--private-config',type=Path)
    parser.add_argument('--unrelated-user',default='nobody')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.child:
        try:
            source=sys.stdin.read(65537)
            if len(source)>65536:raise ValueError('Bounded probe input required')
            print(json.dumps(child(json.loads(source)),sort_keys=True))
            return 0
        except Exception:
            return 1
    if os.geteuid()!=0 or not args.public_config or not args.private_config or not args.output:
        parser.error('Use sudo on the configured host with both configs and a new output directory')
    from deploy.multirun.privilege_config import load_public_config, trusted_installation
    from deploy.multirun.vm_config import load_config
    trusted_installation(args.public_config)
    trusted_installation(args.private_config)
    public=load_public_config(args.public_config)
    private=load_config(args.private_config)
    executor=pwd.getpwnam(public.executor_user)
    web=pwd.getpwnam(private.rpc_frontend_user)
    unrelated=pwd.getpwnam(args.unrelated_user)
    if len({0,executor.pw_uid,web.pw_uid,unrelated.pw_uid})!=4:
        raise ValueError('Probe needs three distinct non-root runtime identities')
    if public.rpc_socket!=private.rpc_socket or public.origin!=private.origin:
        raise ValueError('Frontend and executor configurations disagree')
    files={'private_storage_denied':private.private_root,'database_credential_denied':private.dsn_file,
           'private_configuration_denied':args.private_config}
    smtp=args.private_config.parent/'smtp.env'
    if smtp.exists():files['smtp_credential_denied']=smtp
    sockets={'docker_socket_denied':Path('/var/run/docker.sock'),
             'quota_socket_denied':private.workspace_quota_socket}
    for path in [*files.values(),*sockets.values(),public.rpc_socket]:
        path.stat()  # Absence cannot masquerade as a successful access denial.
    settings=dict(frontend=str(public.frontend),socket=str(public.rpc_socket),executor_uid=executor.pw_uid,
                  rpc_gid=public.rpc_socket.stat().st_gid,
                  private_files={k:str(v) for k,v in files.items()},
                  privileged_sockets={k:str(v) for k,v in sockets.items()})
    web_checks=run_as(web,{**settings,'role':'web'})
    unrelated_checks=run_as(unrelated,{**settings,'role':'unrelated'})
    args.output.mkdir(mode=0o700,parents=False,exist_ok=False)
    report=dict(passed=True,checks={**web_checks,**unrelated_checks},
                scope='Configured-host UID, filesystem and RPC boundary; no model or database mutations')
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    (args.output/'report.json').chmod(0o600)
    (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Native privilege-boundary acceptance evidence.\n@author ross richardson -->\n')
    print('PASSED: '+str(args.output/'report.json'))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
