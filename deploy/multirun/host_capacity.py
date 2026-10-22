"""(C) Copyright 2026, by Ross Richardson

Bounded host-filesystem capacity checks and durable administrator notifications.
Reads mount/statvfs metadata only; never scans files or contacts Docker/PostgreSQL.
@author ross richardson
"""
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import tomllib
from uuid import uuid4

FORMAT = 'simpaths.host-capacity.v1'
STATE = 'capacity.json'
MAX_BYTES = 128*1024
NAME = re.compile(r'[a-z][a-z0-9_-]{0,63}')
EMAIL = re.compile(r'[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+')
REASONS = {'space', 'inodes', 'unavailable'}


class MissingMount(ValueError):
    """A configured allocation is not mounted; do not report its parent disk."""


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid bounded host-capacity setting')
    return value


def path(value):
    if (type(value) is not str or len(value) > 1024 or not value.startswith('/') or
            any(not c.isprintable() for c in value) or '..' in Path(value).parts):
        raise ValueError('Use an absolute host-capacity path')
    return Path(value)


def no_links(value):
    value = Path(value)
    if any(p.is_symlink() for p in (value, *value.parents)):
        raise ValueError('Host-capacity paths cannot use symlinks')
    return value


def read_private(file, *, root_owned=False):
    file = no_links(file)
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        owners = {0, os.getuid()} if root_owned else {os.getuid()}
        operator_read = not root_owned and os.getuid() == 0
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_uid not in owners and not operator_read or
                info.st_mode & (0o027 if root_owned else 0o077) or info.st_size > MAX_BYTES):
            raise ValueError('Host-capacity files must be private, bounded and owned')
        return stream.read(MAX_BYTES+1)


def load_config(file):
    value = tomllib.loads(read_private(file, root_owned=True).decode())
    if set(value) != {'monitor', 'alerts', 'filesystems'}:
        raise ValueError('Expected monitor, alerts and filesystems settings')
    defaults = dict(enabled=False, instance='simpaths-online',
        frontend='/opt/simpaths-online/JAS-mine-web', warning_percent=85,
        recovery_percent=80, reminder_hours=24, mail_spacing_minutes=30)
    monitor, alerts, filesystems = value['monitor'], value['alerts'], value['filesystems']
    if type(monitor) is not dict or set(monitor)-set(defaults):
        raise ValueError('Unknown host-capacity monitor setting')
    monitor = {**defaults, **monitor}
    if type(monitor['enabled']) is not bool or not NAME.fullmatch(str(monitor['instance'])):
        raise ValueError('Invalid monitor enablement or instance label')
    monitor['frontend'] = str(path(monitor['frontend']))
    integer(monitor['warning_percent'], 50, 99)
    integer(monitor['recovery_percent'], 1, monitor['warning_percent']-1)
    integer(monitor['reminder_hours'], 1, 168)
    integer(monitor['mail_spacing_minutes'], 5, 1440)
    if (type(alerts) is not dict or set(alerts)-{'enabled', 'recipient'} or
            type(alerts.get('enabled')) is not bool or
            type(alerts.get('recipient', '')) is not str or
            alerts['enabled'] and not EMAIL.fullmatch(alerts.get('recipient', ''))):
        raise ValueError('Configure an administrator recipient before enabling mail')
    if type(filesystems) is not list or not 1 <= len(filesystems) <= 32:
        raise ValueError('Supply 1..32 filesystem allocations')
    names, paths, selected = set(), set(), []
    for item in filesystems:
        if (type(item) is not dict or not {'name', 'path'} <= set(item) or
                set(item)-{'name', 'path', 'require_mount'} or
                not NAME.fullmatch(str(item['name'])) or
                type(item.get('require_mount', True)) is not bool):
            raise ValueError('Invalid filesystem allocation')
        location = str(path(item['path']))
        if item['name'] in names or location in paths:
            raise ValueError('Filesystem labels and paths must be distinct')
        names.add(item['name']); paths.add(location)
        selected.append(dict(name=item['name'], path=location,
                             require_mount=item.get('require_mount', True)))
    return dict(monitor=monitor, alerts=alerts, filesystems=sorted(selected, key=lambda x:x['name']))


def mounted_devices(file=Path('/proc/self/mountinfo')):
    """One bounded kernel inventory; mount paths decode Linux's four escapes."""
    with file.open('rb') as source:
        raw = source.read(1024**2+1)
    if len(raw) > 1024**2:
        raise ValueError('Mount inventory exceeds the monitor budget')
    result = {}
    for line in raw.decode().splitlines():
        fields = line.split(' - ', 1)[0].split()
        major, minor = fields[2].split(':')
        name = re.sub(r'\\(040|011|012|134)', lambda m:chr(int(m[1], 8)), fields[4])
        result.setdefault(name, set()).add(os.makedev(int(major), int(minor)))
    return result


def probe(item, mounts):
    location = no_links(path(item['path']))
    before = location.stat()
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError('Monitor a directory on the allocation')
    if item['require_mount'] and before.st_dev not in mounts.get(str(location), set()):
        raise MissingMount('Expected a mounted allocation')
    info = os.statvfs(location)
    after = location.stat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError('Filesystem changed during measurement')
    if (info.f_frsize <= 0 or not 0 <= info.f_bavail <= info.f_bfree <= info.f_blocks or
            info.f_blocks <= 0):
        raise ValueError('Filesystem capacity is unavailable')
    total = info.f_blocks*info.f_frsize
    used = (info.f_blocks-info.f_bfree)*info.f_frsize
    available = info.f_bavail*info.f_frsize
    if used+available <= 0:
        raise ValueError('Filesystem usable capacity is unavailable')
    inodes = info.f_files if info.f_files > 0 and 0 <= info.f_ffree <= info.f_files else None
    return dict(device=before.st_dev, total_bytes=total, used_bytes=used, available_bytes=available,
        space_percent=100*used/(used+available), inode_total=inodes,
        inode_free=info.f_ffree if inodes else None,
        inode_percent=100*(inodes-info.f_ffree)/inodes if inodes else None)


def collect(config, previous=(), *, reader=probe, mount_reader=mounted_devices):
    policy = config['monitor']
    prior = {item['name']:item for item in previous}
    result, seen = [], {}
    try:
        mounts = mount_reader()
        mount_error = None
    except Exception as error:
        mounts, mount_error = {}, error
    for item in config['filesystems']:
        old = prior.get(item['name'], {})
        if old.get('path') != item['path']:
            old = {}
        row = dict(name=item['name'], path=item['path'], available=False, error=None,
            duplicate_of=None, space_warning=old.get('space_warning', False),
            inode_warning=old.get('inode_warning', False))
        try:
            if mount_error is not None and item['require_mount']:
                raise mount_error
            row.update(reader(item, mounts), available=True)
            for metric in ('space', 'inode'):
                percent = row[metric+'_percent']
                if percent is not None and (type(percent) not in (int, float) or
                        not math.isfinite(percent) or not 0 <= percent <= 100):
                    raise ValueError('Invalid filesystem percentage')
                row[metric+'_warning'] = (old.get(metric+'_warning', False) if percent is None else
                    percent >= policy['warning_percent'] or
                    old.get(metric+'_warning', False) and percent >= policy['recovery_percent'])
            if row['device'] in seen:
                row['duplicate_of'] = seen[row['device']]
            else:
                seen[row['device']] = item['name']
        except Exception as error:
            row = {k:row[k] for k in ('name', 'path', 'duplicate_of', 'space_warning', 'inode_warning')}
            row.update(available=False, duplicate_of=None, error=type(error).__name__)
        result.append(row)
    return result


def active(observations):
    result = {}
    for row in observations:
        reasons = (['unavailable'] if not row['available'] else
            [reason for reason, field in (('space', 'space_warning'), ('inodes', 'inode_warning')) if row[field]])
        if reasons and row['duplicate_of'] is None:
            result[row['name']] = dict(path=row['path'], reasons=reasons)
    return result


def timestamp(value):
    if value is None:
        return None
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError('Expected an aware monitor timestamp')
    return at


def fresh(instance):
    return dict(format=FORMAT, instance=instance, revision=0, checked_at=None, observations=[],
        notified={}, last_attempt_at=None, last_sent_at=None, next_mail_at=None,
        pending_id=None, pending_target=None, mail_error=None, state_store='persistent')


def validate_state(value, instance):
    if (type(value) is not dict or set(value) != set(fresh(instance)) or value['format'] != FORMAT or
            value['instance'] != instance or type(value['observations']) is not list or
            len(value['observations']) > 32 or value['state_store'] not in ('persistent', 'runtime')):
        raise ValueError('Invalid private host-capacity status')
    integer(value['revision'], 0, 2**63-1)
    for field in ('checked_at', 'last_attempt_at', 'last_sent_at', 'next_mail_at'):
        timestamp(value[field])
    for field in ('notified', 'pending_target'):
        record = value[field]
        if record is None and field == 'pending_target':
            continue
        if type(record) is not dict or len(record) > 32:
            raise ValueError('Invalid monitor notification state')
        for name, row in record.items():
            if (not NAME.fullmatch(name) or type(row) is not dict or set(row) != {'path', 'reasons'} or
                    type(row['reasons']) is not list or not 1 <= len(row['reasons']) <= 3 or
                    any(type(reason) is not str or reason not in REASONS for reason in row['reasons'])):
                raise ValueError('Invalid monitor warning state')
            path(row['path'])
    if (value['pending_id'] is not None and not re.fullmatch('[a-f0-9]{32}', value['pending_id']) or
            value['mail_error'] is not None and not value['mail_error'].isidentifier()):
        raise ValueError('Invalid monitor delivery status')
    for row in value['observations']:
        if (type(row) is not dict or not NAME.fullmatch(str(row.get('name'))) or
                any(type(row.get(k)) is not bool for k in ('available', 'space_warning', 'inode_warning'))):
            raise ValueError('Invalid monitor observation')
        path(row['path'])
    return value


def private_directory(root):
    root = no_links(Path(root).absolute())
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Monitor state must be private and service-owned')
    return root


@contextmanager
def locked(root):
    fd = os.open(root/'monitor.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('Invalid monitor lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def load_state(root, instance, runtime=None):
    selected = []
    for folder in (root, runtime):
        if folder is None:
            continue
        try:
            raw = read_private(folder/STATE)
        except FileNotFoundError:
            continue
        selected.append(validate_state(json.loads(raw), instance))
    return max(selected, key=lambda x:x['revision']) if selected else fresh(instance)


def atomic_json(file, value):
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()+b'\n'
    if len(raw) > MAX_BYTES:
        raise ValueError('Monitor status exceeds its fixed budget')
    fd, temporary = tempfile.mkstemp(prefix='.capacity-', dir=file.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, file)
        directory = os.open(file.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save(root, state, runtime):
    state['revision'] += 1
    state['state_store'] = 'persistent'
    try:
        atomic_json(root/STATE, state)
    except OSError:
        if runtime is None:
            raise
        state['state_store'] = 'runtime'
        atomic_json(runtime/STATE, state)
    else:
        if runtime is not None:
            (runtime/STATE).unlink(missing_ok=True)


def notification(config, state, current):
    policy = config['monitor']
    body = [f"Instance: {policy['instance']}", f"Checked: {state['checked_at']}",
        f"Warning at {policy['warning_percent']}%; recovery below {policy['recovery_percent']}%.", '']
    rows = {row['name']:row for row in state['observations']}
    for name, item in current.items():
        row = rows[name]
        body.append(f"{name}: {item['path']}")
        if not row['available']:
            body.append('  Measurement unavailable: '+row['error']+'. Check the mount and monitor permissions.')
        else:
            body.append(f"  Space: {row['space_percent']:.1f}% used; {row['available_bytes']/2**30:.2f} GiB available.")
            if row['inode_percent'] is not None:
                body.append(f"  File entries (inodes): {row['inode_percent']:.1f}% used.")
            elif row['inode_warning']:
                body.append('  Inode capacity is unavailable; the earlier inode warning remains unresolved.')
    for name, item in state['notified'].items():
        if name not in current:
            row = rows.get(name)
            if row and row['path'] == item['path']:
                note = ('same filesystem as '+row['duplicate_of']+'; see that allocation.' if row['duplicate_of'] else
                        'recovered.' if row['available'] and not row['space_warning'] and not row['inode_warning'] else
                        'capacity remains unresolved.')
            else:
                note = 'monitor configuration changed; recovery was not established.'
            body.append(f"{name}: "+note)
    body += ['', 'Review capacity and retention, or allocate additional space where available.',
        'This monitor only reports filesystem metadata. It does not delete data, resize volumes or control jobs.',
        'Notice reference: '+state['pending_id']]
    subject = 'filesystem capacity warning' if current else 'filesystem capacity recovered'
    return 'SimPaths Online: '+subject+' ['+policy['instance']+']', '\n'.join(body)+'\n'


def send(config, state, current, *, test=False):
    """Lazy reuse of the existing bounded SMTP transport; no application startup."""
    frontend = no_links(path(config['monitor']['frontend'])).resolve(strict=True)
    sys.path.insert(0, str(frontend))
    from jasmine_web.contact import deliver_email
    origin = os.environ.get('SMTP_FROM_EMAIL', '')
    if not EMAIL.fullmatch(origin):
        raise ValueError('Configure the verified SMTP sender')
    if test:
        subject = 'SimPaths Online: host capacity email test ['+config['monitor']['instance']+']'
        body = ('This is the explicitly requested administrator delivery test for filesystem capacity alerts.\n'
                'The test does not represent a storage incident.\nInstance: '+config['monitor']['instance']+'\n')
    else:
        subject, body = notification(config, state, current)
    message = EmailMessage()
    message['From'], message['To'] = origin, config['alerts']['recipient']
    message['Subject'] = subject
    message['Message-ID'] = '<'+state['pending_id']+'@'+origin.rsplit('@', 1)[1]+'>'
    message.set_content(body)
    deliver_email(message)


def run(config, root, *, runtime=None, now=None, reader=probe, mount_reader=mounted_devices, sender=send):
    if not config['monitor']['enabled']:
        return dict(format=FORMAT, enabled=False)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Use an aware monitor time')
    root = private_directory(root)
    runtime = private_directory(runtime) if runtime is not None else None
    if root == runtime:
        raise ValueError('Use distinct persistent and runtime state directories')
    with ExitStack() as locks:
        if runtime is not None:
            locks.enter_context(locked(runtime))
        try:
            # The common persistent lock also excludes a manual invocation
            # that omitted runtime state. Its existing empty file works on a
            # full filesystem; fallback is needed only if it cannot be created.
            locks.enter_context(locked(root))
        except OSError as error:
            if runtime is None or error.errno not in (errno.ENOSPC, errno.EDQUOT):
                raise
        state = load_state(root, config['monitor']['instance'], runtime)
        state['observations'] = collect(config, state['observations'], reader=reader, mount_reader=mount_reader)
        state['checked_at'] = now.isoformat()
        current = active(state['observations'])
        reminder = (bool(current) and state['last_sent_at'] is not None and
            now-timestamp(state['last_sent_at']) >= timedelta(hours=config['monitor']['reminder_hours']))
        pending = current != state['notified'] or reminder
        if pending:
            if state['pending_id'] is None or state['pending_target'] != current:
                state['pending_id'], state['pending_target'] = uuid4().hex, current
        else:
            state['pending_id'] = state['pending_target'] = None
            state['mail_error'] = None
        spacing = timedelta(minutes=config['monitor']['mail_spacing_minutes'])
        if state['next_mail_at'] is not None and timestamp(state['next_mail_at']) > now+spacing:
            state['next_mail_at'] = (now+spacing).isoformat()
        if state['last_sent_at'] is not None and timestamp(state['last_sent_at']) > now:
            state['last_sent_at'] = now.isoformat()
        due = state['next_mail_at'] is None or now >= timestamp(state['next_mail_at'])
        if pending and due and config['alerts']['enabled']:
            # Reserve delivery spacing before SMTP. An interrupted/uncertain
            # delivery can retry the same notice after this bounded quiet period.
            state['last_attempt_at'] = now.isoformat()
            state['next_mail_at'] = (now+spacing).isoformat()
            save(root, state, runtime)
            try:
                sender(config, state, current)
            except Exception as error:
                state['mail_error'] = type(error).__name__
            else:
                state['notified'], state['last_sent_at'] = current, now.isoformat()
                state['pending_id'] = state['pending_target'] = None
                state['mail_error'] = None
        save(root, state, runtime)
        return dict(state, enabled=True, active=current,
                    mail_pending=state['pending_id'] is not None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'run', 'status', 'test-mail'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state', type=Path, default=Path('/var/lib/simpaths-capacity'))
    parser.add_argument('--runtime-state', type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == 'check':
            rows = collect(config)
            result = dict(format=FORMAT, instance=config['monitor']['instance'],
                checked_at=datetime.now(timezone.utc).isoformat(), observations=rows, active=active(rows))
        elif args.command == 'status':
            result = load_state(no_links(args.state), config['monitor']['instance'],
                                no_links(args.runtime_state) if args.runtime_state else None)
        elif args.command == 'test-mail':
            if not config['monitor']['enabled'] or not config['alerts']['enabled']:
                raise ValueError('Explicitly enable administrator mail before testing delivery')
            state = fresh(config['monitor']['instance'])
            state['pending_id'] = uuid4().hex
            send(config, state, {}, test=True)
            result = dict(format=FORMAT, test_mail_sent=True)
        else:
            result = run(config, args.state, runtime=args.runtime_state)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except Exception as error:
        print('Host-capacity check failed: '+type(error).__name__+
              '. Review the private monitor configuration and status.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
