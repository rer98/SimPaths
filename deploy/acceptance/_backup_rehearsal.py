"""(C) Copyright 2026, by Ross Richardson

Owned OpenSSH containers, a bounded encrypted TCP relay and transient backup timers.
All credentials and mounted directories belong to a disposable local rehearsal.
@author ross richardson
"""
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading

from deploy.acceptance._supervisor import safe_path

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = ROOT/'deploy/acceptance/backup_sftp'
SERVICE_POLICY = dict(Type='oneshot', UMask='0077', Nice='10', IOSchedulingClass='idle',
                      TimeoutStartSec='2h', KillMode='control-group', NoNewPrivileges='true',
                      MemoryMax='512M', TasksMax='128')
TIMER_POLICY = dict(OnBootSec='10min', OnCalendar='hourly', RandomizedDelaySec='5min',
                    Persistent='true', Unit='simpaths-backup.service')


def timer_policy():
    """Read operator templates, retaining limits and declaring shortened test ticks."""
    def section(filename, key):
        parser = configparser.ConfigParser(strict=False, interpolation=None); parser.optionxform = str
        parser.read(ROOT/'deploy/multirun/vm'/filename)
        return dict(parser[key])
    service = section('simpaths-backup.service', 'Service')
    timer = section('simpaths-backup.timer', 'Timer')
    if any(service.get(k) != v for k, v in SERVICE_POLICY.items()) or timer != TIMER_POLICY:
        raise ValueError('Backup template policy changed; review the rehearsal explicitly')
    return dict(service=SERVICE_POLICY.copy(), timer=timer,
                adaptations=dict(OnActiveSec='1s', OnUnitInactiveSec='5s',
                                 AccuracySec='100ms', RandomizedDelaySec='0',
                                 host_hardening='Separate installed-host acceptance'))


def verify_effective_policy(service, timer):
    """Check applied limits and both timer rules, with useful failure evidence."""
    expected = dict(Type=('oneshot',), UMask=('0077',), MemoryMax=(str(512*1024**2),),
                    TasksMax=('128',), Nice=('10',), IOSchedulingClass=('idle','3'),
                    KillMode=('control-group',), TimeoutStartUSec=('2h',), NoNewPrivileges=('yes',))
    for field, allowed in expected.items():
        observed = (service or {}).get(field)
        if observed not in allowed:
            raise AssertionError('Effective backup service '+field+': expected '+repr(allowed)+', observed '+repr(observed))
    value = (timer or {}).get('TimersMonotonic','')
    rules = re.findall(r'\{\s*(On[A-Za-z]+USec)=(.*?)\s*;\s*next_elapse=[^}]*\}', value)
    if sorted(rules) != [('OnActiveUSec','1s'),('OnUnitInactiveUSec','5s')]:
        raise AssertionError('Expected both backup timer rules (1s initially, 5s after exit); observed '+repr(value))


def command(argv, *, log=None, check=True, timeout=60):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if log and result.returncode:
        with Path(log).open('a') as stream: stream.write(result.stdout+result.stderr)
    if check and result.returncode:
        raise RuntimeError('Disposable backup rehearsal command failed')
    return result


def key_pair(path):
    command(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'fictional-backup-rehearsal', '-f', str(path)])
    path.chmod(0o600)
    return Path(str(path)+'.pub').read_text().split()[:2]


def write_pin(path, port, public):
    if (type(port) is not int or not 1024 <= port <= 65535 or len(public) != 2 or
            public[0] != 'ssh-ed25519' or not re.fullmatch('[A-Za-z0-9+/=]+', public[1])):
        raise ValueError('Invalid disposable SSH host pin')
    with path.open('x') as stream:
        os.chmod(path, 0o600)
        stream.write('[127.0.0.1]:'+str(port)+' '+' '.join(public)+'\n')


class SFTPServer:
    """Operate one labelled container; mount no database, plaintext or recovery key."""
    def __init__(self, root, tag, log):
        if not re.fullmatch('[a-f0-9]{32}', tag) or not 1000 <= os.getuid() < 2**31:
            raise ValueError('Use a disposable identity and an ordinary local user')
        self.root, self.tag, self.log = safe_path(root), tag, log
        self.name = 'simpaths-backup-sftp-'+tag
        self.created = False; self.image = None; self.port = None
        self.config = self.root/'server-config'; self.config.mkdir(mode=0o700)
        self.data = self.root/'remote'; self.data.mkdir(mode=0o700)
        self.host_public = key_pair(self.config/'host_key')
        # The server never receives the client's private identity.
        client = self.root/'client_key'; self.client_public = key_pair(client)
        (self.config/'authorized_keys').write_text('restrict '+' '.join(self.client_public)+'\n')
        (self.config/'authorized_keys').chmod(0o600)
        shutil.copyfile(CONTEXT/'sshd_config', self.config/'sshd_config')
        self.identity = client

    def build(self):
        digest = hashlib.sha256()
        for path in sorted(CONTEXT.iterdir()):
            if path.is_file(): digest.update(path.name.encode()+path.read_bytes())
        digest.update(str(os.getuid()).encode())
        tag = 'simpaths-backup-sftp-rehearsal:'+digest.hexdigest()[:16]
        result = command(['docker', 'image', 'inspect', tag, '--format', '{{.Id}}'], check=False)
        if result.returncode:
            print('Building the disposable OpenSSH SFTP rehearsal image', flush=True)
            with Path(self.log).open('a') as stream:
                subprocess.run(['docker', 'build', '--build-arg', 'REHEARSAL_UID='+str(os.getuid()),
                    '--tag', tag, str(CONTEXT)], stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=600)
            result = command(['docker', 'image', 'inspect', tag, '--format', '{{.Id}}'])
        self.image = result.stdout.strip()
        if not re.fullmatch('sha256:[a-f0-9]{64}', self.image): raise ValueError('Invalid rehearsal image identity')

    def start(self):
        self.build(); self.created = True
        command(['docker', 'run', '-d', '--name', self.name, '--label', 'simpaths.backup-rehearsal='+self.tag,
                 '--memory', '128m', '--cpus', '0.5', '--pids-limit', '64', '--read-only',
                 '--security-opt', 'no-new-privileges:true', '--publish', '127.0.0.1::2222',
                 '--tmpfs', '/run:rw,noexec,nosuid,size=8m',
                 '--mount', 'type=bind,source='+str(self.config)+',target=/config,readonly',
                 '--mount', 'type=bind,source='+str(self.data)+',target=/srv/backups', self.image], log=self.log)
        self.refresh_port()

    def refresh_port(self):
        """Validate the owned endpoint after each start; ephemeral ports may change."""
        info = self.inspect()
        if not info: raise ValueError('SFTP fixture is missing')
        ports = info['NetworkSettings']['Ports'] or {}
        bindings = ports.get('2222/tcp')
        if (set(ports) != {'2222/tcp'} or not isinstance(bindings,list) or len(bindings) != 1 or
                bindings[0].get('HostIp') != '127.0.0.1'):
            raise ValueError('SFTP fixture must publish only on IPv4 loopback')
        port = int(bindings[0]['HostPort'])
        if not 1024 <= port <= 65535: raise ValueError('SFTP fixture port must be unprivileged')
        mounts = {v['Destination']:v['Source'] for v in info['Mounts'] if v['Type'] == 'bind'}
        if mounts != {'/config':str(self.config), '/srv/backups':str(self.data)}:
            raise ValueError('SFTP fixture mounted unrelated private data')
        self.port = port

    def inspect(self):
        result = command(['docker', 'container', 'inspect', self.name], check=False)
        if result.returncode: return None
        value = json.loads(result.stdout)[0]
        if value['Config']['Labels'].get('simpaths.backup-rehearsal') != self.tag or value['Image'] != self.image:
            raise ValueError('Refusing to operate an unrelated SFTP container')
        return value

    def stop(self):
        if not self.inspect(): raise ValueError('SFTP fixture is missing')
        command(['docker', 'kill', self.name], log=self.log)

    def restart(self):
        if not self.inspect(): raise ValueError('SFTP fixture is missing')
        command(['docker', 'start', self.name], log=self.log)
        self.refresh_port()

    def close(self):
        if not self.created: return
        if self.inspect():
            logs = command(['docker', 'logs', self.name], check=False)
            with Path(self.log).open('a') as stream: stream.write(logs.stdout+logs.stderr)
            command(['docker', 'rm', '-f', '--volumes', self.name], log=self.log)
        if self.inspect() is not None: raise RuntimeError('SFTP fixture removal was not confirmed')


class Relay:
    """Pass real SSH bytes without decrypting; bounded rate makes faults observable."""
    def __init__(self, upstream_port, rate=1024**2):
        if type(upstream_port) is not int or not 1024 <= upstream_port <= 65535 or rate <= 0:
            raise ValueError('Relay destination must be an unprivileged local port')
        self.upstream_port, self.rate = upstream_port, rate
        self.stop = threading.Event(); self.lock = threading.Lock(); self.sockets = set(); self.threads = []
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0)); self.listener.listen(8); self.listener.settimeout(.25)
        self.port = self.listener.getsockname()[1]; self.errors = []
        self.thread = threading.Thread(target=self.accept, daemon=True); self.thread.start()

    def retarget(self, upstream_port):
        """Keep the SSH client address/pin stable when Docker changes its host port."""
        if type(upstream_port) is not int or not 1024 <= upstream_port <= 65535:
            raise ValueError('Relay destination must be an unprivileged local port')
        with self.lock: self.upstream_port = upstream_port

    def accept(self):
        while not self.stop.is_set():
            try: incoming, _ = self.listener.accept()
            except socket.timeout: continue
            except OSError: break
            with self.lock: upstream_port = self.upstream_port
            try: outgoing = socket.create_connection(('127.0.0.1', upstream_port), timeout=2)
            except OSError: incoming.close(); continue
            incoming.settimeout(.5); outgoing.settimeout(.5)
            with self.lock:
                if len(self.sockets) >= 16:
                    incoming.close(); outgoing.close(); continue
                self.sockets.update((incoming, outgoing))
            for source, target in ((incoming,outgoing),(outgoing,incoming)):
                thread = threading.Thread(target=self.pump, args=(source,target), daemon=True)
                self.threads.append(thread); thread.start()

    def pump(self, source, target):
        try:
            while not self.stop.is_set():
                try: block = source.recv(32768)
                except socket.timeout: continue
                if not block: break
                if self.stop.wait(len(block)/self.rate): break
                target.sendall(block)
        except OSError:
            pass  # Real interruption/disconnect is an expected transport fault.
        except Exception as error:
            self.errors.append(type(error).__name__)
        finally:
            for stream in (source, target):
                try: stream.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                stream.close()
                with self.lock: self.sockets.discard(stream)

    def close(self):
        self.stop.set(); self.listener.close(); self.thread.join(timeout=3)
        with self.lock: streams = list(self.sockets)
        for stream in streams:
            try: stream.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            stream.close()
        for thread in self.threads: thread.join(timeout=3)
        if self.thread.is_alive() or any(t.is_alive() for t in self.threads) or self.errors:
            raise RuntimeError('Encrypted transport relay did not close cleanly')


class BackupTimer:
    """Create no unit files; operate only this invocation's timer and service."""
    def __init__(self, tag, log):
        if not re.fullmatch('[a-f0-9]{32}', tag): raise ValueError('Invalid disposable timer identity')
        self.name, self.log = 'simpaths-backup-rehearsal-'+tag, log
        self.created = False; self.stopped = False

    def probe(self):
        value = command(['systemctl', '--user', 'is-system-running'], check=False)
        if value.stdout.strip() not in ('running','degraded','starting'):
            raise RuntimeError('Run the rehearsal from the normal laptop terminal with its user systemd manager')

    def start(self, config, environment, output):
        from sys import executable
        policy = timer_policy()['service']
        argv = ['systemd-run', '--user', '--quiet', '--unit='+self.name, '--on-active=1s', '--on-unit-inactive=5s',
                '--timer-property=AccuracySec=100ms', '--timer-property=RandomizedDelaySec=0']
        argv += ['--property='+k+'='+v for k,v in policy.items()]
        argv += ['--property=WorkingDirectory='+str(safe_path(ROOT)),
                 '--property=EnvironmentFile='+str(safe_path(environment)),
                 '--property=StandardOutput=append:'+str(safe_path(output)),
                 '--property=StandardError=append:'+str(safe_path(output)),
                 str(safe_path(executable, interpreter=True)), '-m', 'deploy.multirun.backup_schedule',
                 'run', '--config', str(safe_path(config))]
        self.created = True
        command(argv, log=self.log)

    def show(self, kind='service'):
        if not self.created or kind not in ('service','timer'): raise ValueError('Unowned backup timer unit')
        fields = ('Id','LoadState','Transient','ActiveState','SubState','MainPID','ExecMainPID','ExecMainStatus',
                  'UMask','Type','Nice','IOSchedulingClass','MemoryMax','TasksMax','KillMode',
                  'TimeoutStartUSec','NoNewPrivileges','TimersMonotonic','LastTriggerUSec')
        value = command(['systemctl', '--user', 'show', self.name+'.'+kind, '--no-pager',
                         *['--property='+field for field in fields]], log=self.log)
        values = {}
        for line in value.stdout.splitlines():
            if '=' not in line: continue
            field, observed = line.split('=',1)
            # systemctl prints one TimersMonotonic line per timer rule. Preserve
            # every rule; replacing the earlier line silently loses a schedule.
            if field == 'TimersMonotonic' and field in values:
                values[field] += '\n'+observed
            elif field in values:
                raise ValueError('Unexpected duplicate scalar unit property: '+field)
            else: values[field] = observed
        if values.get('LoadState') == 'not-found': return None
        if values.get('Id') != self.name+'.'+kind or values.get('Transient') != 'yes':
            raise ValueError('Refusing to operate a persistent or unrelated timer unit')
        return values

    def crash(self):
        value = self.show()
        if not value or int(value['MainPID']) <= 1: raise ValueError('Backup service is not running')
        command(['systemctl', '--user', 'kill', '--kill-whom=all', '--signal=SIGKILL', self.name+'.service'], log=self.log)
        return int(value['MainPID'])

    @staticmethod
    def inactive(value):
        return value is None or (value.get('ActiveState') in ('inactive','failed') and not int(value.get('MainPID','0')))

    def require_stopped(self):
        for kind in ('timer','service'):
            if not self.inactive(self.show(kind)):
                raise RuntimeError('Disposable backup units have not stopped')

    def stop(self):
        if not self.created or self.stopped: return
        # Validate both identities before changing either. Stop the timer first,
        # so it cannot start another service while the current process stops.
        for kind in ('timer','service'): self.show(kind)
        for kind in ('timer','service'):
            if self.inactive(self.show(kind)): continue
            result = command(['systemctl', '--user', 'stop', self.name+'.'+kind], log=self.log, check=False)
            value = self.show(kind)
            # An inactive transient service may be collected when its timer is
            # stopped, including between inspection and the stop command. Only
            # verified absence can excuse a failed command; other failures stay fatal.
            if result.returncode and value is not None:
                raise RuntimeError('Could not stop the owned disposable backup '+kind)
            if not self.inactive(value):
                raise RuntimeError('Disposable backup units have not stopped')
        self.require_stopped()
        self.stopped = True

    def close(self):
        if not self.created: return
        self.stop()
        self.require_stopped()
        value = self.show()
        if value and value.get('ActiveState') == 'failed':
            result = command(['systemctl', '--user', 'reset-failed', self.name+'.service'], log=self.log, check=False)
            if result.returncode and self.show() is not None:
                raise RuntimeError('Could not release the owned failed backup service')
        self.require_stopped()
        # Only transient units are owned; stopping them releases them for GC.
        self.created = False
