"""(C) Copyright 2026, by Ross Richardson

Own disposable, independently restartable SingleRun public/private subprocesses.
The native fixture uses production framing and same-UID kernel peer credentials.
@author ross richardson
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from deploy.multirun.proxy_rehearsal import Processes


def copy_frontend(frontend, destination):
    """Copy executable web/static sources, never local secrets or model data."""
    import shutil
    frontend, destination = Path(frontend), Path(destination)
    names = subprocess.check_output(['git','-C',str(frontend),'ls-files','-z'],text=True).split('\0')
    names += ['tests/browser/single_rpc_fixture.py']
    names += [str(path.relative_to(frontend)) for path in (frontend/'jasmine_web').glob('single_*.py')]
    for name in sorted(set(names)):
        source=frontend/name
        if not name or source.is_symlink() or not source.is_file():
            continue
        if source.suffix not in ('.py','.sql') and not name.startswith('static/'):
            continue
        target=destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)


def public_environment(environment):
    private = ('JASMINE_', 'VM_', 'PG', 'SMTP_', 'SESSION_', 'ADMIN_', 'HARD_RESET_',
               'TURNSTILE_', 'DOCKER_', 'OPENAI_', 'GOOGLE_', 'GITHUB_', 'REDIS_')
    return {key:value for key,value in environment.items()
            if not key.startswith(private) and key not in ('DEPLOY_MODE', 'EMAIL_PASSWORD', 'PYTHONPATH')}


def drain_process(process, timeout=90):
    """Require graceful termination of an owned process before releasing its data."""
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        raise RuntimeError('Fixture process did not drain gracefully') from None
    if process.returncode not in (0, -signal.SIGTERM):
        raise RuntimeError('Fixture process exited abnormally')


def completed_private_evidence(path):
    """Missing or malformed private shutdown evidence must fail acceptance."""
    evidence = json.loads(Path(path).read_text())
    if (not isinstance(evidence, dict) or evidence.get('closed') is not True
            or evidence.get('errors') != []):
        raise RuntimeError('Private fixture cleanup was not confirmed')
    return evidence


class SingleRPCProcesses(Processes):
    def __init__(self, work, output, args):
        super().__init__(work, output, args)
        self.executor = None
        self.rpc_directory = Path(tempfile.mkdtemp(prefix='sr-rpc-', dir='/tmp'))
        self.rpc_directory.chmod(0o700)
        self.socket = self.rpc_directory/'control.sock'

    def start_app(self, settings, *, script=None, workdir=None):
        from jasmine_web.batch.local_executor import atomic_json
        if self.app is not None:
            raise ValueError('Public fixture already running')
        if self.executor is None:
            private = dict(settings, rpc_socket=str(self.socket),
                           rpc_evidence=str(self.output/'private-rpc.json'))
            atomic_json(self.work/'private-fixture.json', private)
            log = (self.output/'executor.log').open('ab'); self.logs.append(log)
            self.executor = subprocess.Popen([sys.executable, str(script), '--serve-fixture',
                str(self.work/'private-fixture.json')], cwd=workdir, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic()+45
            while not self.socket.exists():
                if self.executor.poll() is not None:
                    raise RuntimeError('Private fixture exited; inspect executor.log')
                if time.monotonic()>deadline:
                    raise TimeoutError('Private listener did not become ready')
                time.sleep(.1)
        public = dict(origin=settings['origin'], port=settings['app_port'], socket=str(self.socket))
        atomic_json(self.work/'public-fixture.json', public)
        log = (self.output/'application.log').open('ab'); self.logs.append(log)
        helper = self.args.frontend/'tests/browser/single_rpc_fixture.py'
        self.app = subprocess.Popen([sys.executable, str(helper), '--role', 'frontend', '--settings',
            str(self.work/'public-fixture.json')], cwd=workdir, env=public_environment(os.environ),
            stdout=log, stderr=subprocess.STDOUT)

    def stop_executor(self):
        if self.executor is None:
            return
        process, self.executor = self.executor, None
        drain_process(process)
        completed_private_evidence(self.output/'private-rpc.json')

    def close(self):
        errors=[]
        try:
            super().close()
        except Exception as error:
            errors.append(type(error).__name__)
        try:
            self.stop_executor()
        except Exception as error:
            errors.append(type(error).__name__)
        # Keep the socket/lock directory when uncertain shutdown needs diagnosis.
        if not errors:
            import shutil
            shutil.rmtree(self.rpc_directory)
        if errors:
            raise RuntimeError('Separated fixture cleanup failed: '+','.join(errors))
