"""(C) Copyright 2026, by Ross Richardson

Guarded transient user-systemd units for the disposable crash-recovery rehearsal.
Copies restart policy from installed-service templates; never installs a service.
@author ross richardson
"""
import configparser
import json
import os
from pathlib import Path
import re
import subprocess


FIELDS = {'Unit': ('StartLimitIntervalSec', 'StartLimitBurst'),
          'Service': ('Type', 'Restart', 'RestartSec', 'TimeoutStopSec',
                      'KillSignal', 'KillMode', 'UMask')}
EXPECTED = dict(StartLimitIntervalSec='300', StartLimitBurst='5', Type='simple',
    Restart='always', RestartSec='10', TimeoutStopSec='infinity',
    KillSignal='SIGTERM', KillMode='control-group', UMask='0077')
SHOW = ('Id', 'Transient', 'LoadState', 'ActiveState', 'SubState', 'Result', 'MainPID',
        'NRestarts', 'Restart', 'RestartUSec', 'StartLimitIntervalUSec',
        'StartLimitBurst', 'KillMode', 'KillSignal', 'Type', 'TimeoutStopUSec', 'UMask',
        'ExecMainCode', 'ExecMainStatus')


def restart_policy(text):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(text)
    policy = {field: parser[section][field] for section, fields in FIELDS.items() for field in fields}
    # Updating an operator template requires consciously updating this rehearsal
    # and its assertions, instead of silently testing a different restart policy.
    if policy != EXPECTED:
        raise ValueError('Service restart policy changed; update the rehearsal explicitly')
    return policy


def safe_path(value, *, interpreter=False):
    path = Path(value).absolute()
    parents = path.parents if interpreter else (path, *path.parents)
    if any(char in str(path) for char in '\x00\r\n%$') or any(parent.is_symlink() for parent in parents):
        raise ValueError('Use ordinary paths without systemd substitutions')
    return path


def environment_file(path, values):
    path = safe_path(path)
    lines = []
    for key, value in values.items():
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or not isinstance(value, str) or any(c in value for c in '\x00\r\n'):
            raise ValueError('Invalid private fixture environment')
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(key+'="'+escaped+'"\n')
    with path.open('x') as stream:
        os.chmod(path, 0o600)
        stream.writelines(lines)


class Supervisor:
    """Operate only transient services created by this invocation."""
    def __init__(self, tag, log=None):
        if not re.fullmatch(r'[a-f0-9]{32}', tag):
            raise ValueError('Use an opaque disposable supervisor identity')
        self.tag, self.units, self.log = tag, set(), log

    def command(self, argv, *, check=True):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        if self.log and result.returncode:
            with Path(self.log).open('a') as stream:
                stream.write(result.stdout+result.stderr)
        if check and result.returncode:
            raise RuntimeError('Disposable systemd request failed; inspect supervisor.log')
        return result

    def probe(self):
        result = self.command(['systemctl', '--user', 'is-system-running'], check=False)
        if result.stdout.strip() not in {'running', 'degraded', 'starting'}:
            raise RuntimeError('A running user systemd manager is required; run from the normal laptop terminal')

    def name(self, role):
        if role not in {'single', 'batch', 'limit'}:
            raise ValueError('Unknown disposable service role')
        return 'simpaths-recovery-'+self.tag+'-'+role+'.service'

    def start_command(self, role, policy, *, python, script, settings, environment, workdir, log):
        if policy != EXPECTED:
            raise ValueError('Unsupported restart policy')
        argv = ['systemd-run', '--user', '--quiet', '--unit='+self.name(role)]
        argv += ['--property='+key+'='+value for key, value in policy.items()]
        argv += ['--property=WorkingDirectory='+str(safe_path(workdir)),
                 '--property=EnvironmentFile='+str(safe_path(environment)),
                 '--property=StandardOutput=append:'+str(safe_path(log)),
                 '--property=StandardError=append:'+str(safe_path(log))]
        argv += [str(safe_path(python, interpreter=True)), str(safe_path(script)), '--serve-fixture', str(safe_path(settings)), '--role', role]
        return argv

    def start(self, role, policy, **paths):
        unit = self.name(role)
        if unit in self.units:
            raise ValueError('Disposable unit already started')
        # Own the name before requesting startup, including uncertain replies.
        self.units.add(unit)
        self.command(self.start_command(role, policy, **paths))

    def own(self, role):
        unit = self.name(role)
        if unit not in self.units:
            raise ValueError('Service was not created by this invocation')
        return unit

    def show(self, role, *, allow_missing=False):
        unit = self.own(role)
        result = self.command(['systemctl', '--user', 'show', unit, '--no-pager',
                               *['--property='+field for field in SHOW]])
        value = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if allow_missing and value.get('LoadState') == 'not-found':
            return None
        if value.get('Id') != unit or value.get('Transient') != 'yes':
            raise ValueError('Refusing to operate a non-fixture service')
        return value

    def crash(self, role):
        value = self.show(role)
        if value['ActiveState'] != 'active' or int(value['MainPID']) <= 1:
            raise ValueError('Disposable service is not running')
        self.command(['systemctl', '--user', 'kill', '--kill-whom=main', '--signal=SIGKILL', self.own(role)])
        return value

    def start_limit_events(self, role):
        unit = self.own(role)
        result = self.command(['journalctl', '--user', '--unit='+unit, '--no-pager',
            '--output=json', '--output-fields=MESSAGE,USER_UNIT', '--lines=100'])
        events = []
        for line in result.stdout.splitlines():
            value = json.loads(line)
            if (value.get('USER_UNIT') == unit and
                    value.get('MESSAGE') == unit+': Start request repeated too quickly.'):
                # Retain only the manager's denial for this unique owned unit,
                # not process messages, command lines or environment details.
                events.append({key:value[key] for key in ('USER_UNIT','MESSAGE','__REALTIME_TIMESTAMP') if key in value})
        return events

    def stop(self, role):
        self.show(role)
        self.command(['systemctl', '--user', 'stop', self.own(role)])

    def close(self):
        errors = []
        for unit in sorted(self.units):
            # Never kill by a caller-supplied PID or operate existing units.
            if unit != self.name(unit.rsplit('-', 1)[-1].removesuffix('.service')):
                raise ValueError('Invalid owned unit')
            role = unit.rsplit('-', 1)[-1].removesuffix('.service')
            try:
                if self.show(role, allow_missing=True) is None:
                    continue
                self.command(['systemctl', '--user', 'stop', unit])
                self.command(['systemctl', '--user', 'reset-failed', unit], check=False)
                # Transient units disappear after stopping; no daemon-reload,
                # enable, unit file, system-wide service or lingering change.
            except Exception as error:
                errors.append(type(error).__name__)
        if errors:
            raise RuntimeError('Disposable supervisor cleanup was not confirmed')
