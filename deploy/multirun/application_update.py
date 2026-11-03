#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Private durable application-update coordination and detached server launcher.
Candidate packages use the reviewed fixed installer; manifests contain no commands.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from uuid import UUID

ROOT = Path('/opt/simpaths-online')
LOCK = Path('/run/simpaths-account-update.lock')
BOOTSTRAP = ("import sys;sys.path[:0]=['/opt/simpaths-online/SimPaths',"
             "'/opt/simpaths-online/JAS-mine-web'];"
             "from deploy.multirun.application_update import main;raise SystemExit(main())")


def digest(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('Require an exact reviewed SHA-256 fingerprint')
    return value


def bounds(wait_seconds, poll_seconds):
    if (type(wait_seconds) is not int or not 1 <= wait_seconds <= 604800
            or type(poll_seconds) is not int or not 1 <= poll_seconds <= 60):
        raise ValueError('Use a 1-second to 7-day wait and a 1–60 second polling interval')


def coordinate(queue, reference, candidate, *, install, verify, rollback=None, ready=None,
               wait_seconds=86400, poll_seconds=5, control=None,
               monotonic=time.monotonic, sleep=time.sleep):
    """Wait without stopping models; resume starts only after verified publication.

    Callbacks come from the fixed trusted installer, never names or shell commands
    selected by a manifest. A timeout keeps its durable drain pending. Installation
    that was already started is not replayed after a coordinator crash.
    """
    bounds(wait_seconds, poll_seconds)
    reference, candidate = str(UUID(reference)), digest(candidate)
    if not callable(install) or not callable(verify) or rollback is not None and not callable(rollback):
        raise ValueError('Require reviewed installation and verification callbacks')
    if control is None:
        from jasmine_web.batch import update_admission as control
    until = monotonic()+wait_seconds
    while True:
        current = control.status(queue).get('current')
        if (not current or current['reference'] != reference or current['candidate'] != candidate):
            raise ValueError('The pending update does not match this exact candidate')
        if current['state'] not in ('pending', 'draining'):
            raise ValueError('Review an already started or settled update before retrying')
        control.mark_draining(queue, reference, candidate)
        snapshot = control.status(queue)
        waiting = snapshot['waiting']
        blocked = any(waiting.get(key,0) for key in ('input_preparations','chart_processors',
                      'receiving_uploads','deleting_uploads','deleting_datasets','deleting_artifacts'))
        if current['mode'] == 'drain':
            blocked = blocked or waiting['models']
        if not blocked and (ready is None or ready()):
            # Recheck under the same pool lock as admissions. Pending handovers
            # have no active helper and remain charged; they are not blockers.
            control.begin_install(queue, reference, candidate)
            break
        if monotonic() >= until:
            return dict(reference=reference, state='draining', timed_out=True,
                        admissions_paused=True, waiting=waiting)
        sleep(min(poll_seconds, max(0, until-monotonic())))
    reason = 'install_failed'
    try:
        install()
        reason = 'verification_failed'
        verification = digest(verify())
        control.complete(queue, reference, candidate, verification=verification)
        return dict(reference=reference, state='complete', admissions_paused=False,
                    verification=verification)
    except BaseException:
        # A failed process must not silently reopen a potentially mixed release.
        control.fail(queue, reference, candidate, reason=reason)
        if rollback is not None:
            try:
                verification = digest(rollback())
                control.recover(queue, reference, candidate, verification=verification)
                return dict(reference=reference, state='cancelled', reason='verified_rollback',
                            admissions_paused=False, verification=verification)
            except BaseException:
                control.fail(queue, reference, candidate, reason='rollback_failed')
                raise
        raise


@contextmanager
def coordinator_lock(path=LOCK):
    """Only one root coordinator can own the waiting/install window on this host."""
    if os.geteuid() != 0:
        raise ValueError('The installation coordinator must run as root')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != 0 or info.st_mode & 0o077):
            raise ValueError('The update coordinator lock must be private and root-owned')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def uploaded(path, limit):
    """Read data from the operator's upload without following links or executing it."""
    path = Path(path)
    if (not path.is_absolute() or '..' in path.parts
            or any(item.is_symlink() for item in (path,*path.parents))):
        raise ValueError('Use an absolute upload path without links')
    fd = os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>limit:
            raise ValueError('Require a bounded ordinary upload file')
        body = stream.read(limit+1)
        after = os.fstat(stream.fileno())
        fields = ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        if len(body)>limit or any(getattr(before,key)!=getattr(after,key) for key in fields):
            raise ValueError('The upload changed while it was read')
        return body


def stage(queue, source, candidate, *, destination=ROOT/'application-updates'):
    """Copy only checked package data into a root-owned immutable candidate directory."""
    if os.geteuid()!=0:
        raise ValueError('Staging an application candidate requires root')
    from . import update_package
    source,candidate = Path(source),digest(candidate)
    body = uploaded(source/'plan.json',1024**2)
    if hashlib.sha256(body).hexdigest()!=candidate:
        raise ValueError('The reviewed uploaded plan differs')
    plan = json.loads(body)
    targets = plan.get('targets')
    if not isinstance(targets,dict) or not 1<=len(targets)<=128:
        raise ValueError('Require a bounded package target inventory')
    # Validate paths before creating any descendant of the private staging root.
    names = [update_package.relative(name) for name in targets]
    destination = Path(destination)
    update_package.trusted(destination.parent,directory=True)
    destination.mkdir(mode=0o700,exist_ok=True)
    update_package.trusted(destination,directory=True)
    target = destination/candidate
    if target.is_symlink():
        raise ValueError('Preserve an unexpected link in the staging inventory')
    if target.exists():
        checked = update_package.validate_package(target,candidate,queue)
        return dict(package=str(target),candidate=candidate,mode=checked['mode'],already_staged=True)
    temporary = Path(tempfile.mkdtemp(prefix='.candidate-',dir=destination))
    try:
        contents = {'plan.json':body,'qualification.json':uploaded(source/'qualification.json',1024**2)}
        amount = sum(map(len,contents.values()))
        for name in names:
            raw = uploaded(source/'programs'/name,update_package.MAX_FILE)
            amount += len(raw)
            if amount>256*1024**2:
                raise ValueError('The candidate exceeds the staging allowance')
            contents['programs/'+str(name)] = raw
        for name,raw in contents.items():
            path = temporary/name
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            with path.open('xb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            path.chmod(0o600)
        checked = update_package.validate_package(temporary,candidate,queue)
        os.rename(temporary,target)
        fd = os.open(destination,os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        return dict(package=str(target),candidate=candidate,mode=checked['mode'],already_staged=False)
    finally:
        if temporary.exists():shutil.rmtree(temporary)


def launch(options, queue, package, candidate, *, config, wait_seconds, poll_seconds):
    """Start one fixed detached Python coordinator, retaining state on SSH loss."""
    bounds(wait_seconds, poll_seconds)
    if os.geteuid() != 0:
        raise ValueError('Starting a detached installer requires root')
    from .update_package import validate_package
    from jasmine_web.batch import update_admission as control
    plan = validate_package(package, candidate, queue)
    current = control.request(queue, candidate, mode=plan['mode'])
    if current['state'] not in ('pending', 'draining'):
        raise ValueError('Review the existing update before launching another coordinator')
    reference = str(UUID(current['reference']))
    unit = 'simpaths-application-update-'+reference
    def running():
        result = subprocess.run(['systemctl','show',unit+'.service','--property=ActiveState','--value'],
                                capture_output=True,text=True,timeout=15)
        return result.stdout.strip() in ('active','activating','reloading','deactivating')
    if running():
        return dict(reference=reference,unit=unit+'.service',state=current['state'],
                    independent_of_ssh=True,admissions_paused=True,already_running=True)
    command = ['systemd-run', '--quiet', '--collect', '--unit='+unit,
        '--property=Type=exec', '--property=RuntimeMaxSec='+str(wait_seconds+1800),
        '--property=StandardOutput=journal', '--property=StandardError=journal',
        str(ROOT/'venv/bin/python'), '-I', '-B', '-c', BOOTSTRAP,
        '--config', str(config), 'run', '--package', str(package), '--candidate', candidate,
        '--reference', reference, '--wait-seconds', str(wait_seconds), '--poll-seconds', str(poll_seconds)]
    try:
        subprocess.run(command, check=True, timeout=30)
    except Exception:
        # Another starter may have won the unit-name race. Do not mark its
        # valid coordinator failed merely because systemd rejected our duplicate.
        if running():
            return dict(reference=reference,unit=unit+'.service',state=current['state'],
                        independent_of_ssh=True,admissions_paused=True,already_running=True)
        control.fail(queue, reference, candidate, reason='coordinator_launch_failed')
        raise
    return dict(reference=reference, unit=unit+'.service', state=current['state'],
                independent_of_ssh=True, admissions_paused=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='Read pending and recent application updates')
    command = commands.add_parser('stage',help='Validate and copy uploaded package bytes; execute no uploaded code')
    command.add_argument('--source',type=Path,required=True)
    command.add_argument('--candidate',required=True)
    for name in ('request', 'start', 'run'):
        command = commands.add_parser(name)
        command.add_argument('--package', type=Path, required=True)
        command.add_argument('--candidate', required=True)
        if name in ('start', 'run'):
            command.add_argument('--wait-seconds', type=int, default=86400)
            command.add_argument('--poll-seconds', type=int, default=5)
        if name == 'run':
            command.add_argument('--reference', required=True)
    command = commands.add_parser('cancel', help='Cancel an update before installation started')
    command.add_argument('--reference', required=True)
    command.add_argument('--candidate', required=True)
    command = commands.add_parser('recover',help='Verify rollback after an interrupted installation')
    command.add_argument('--reference',required=True)
    command.add_argument('--package',type=Path,required=True)
    command.add_argument('--candidate',required=True)
    args = parser.parse_args(argv)
    try:
        from .privilege_config import trusted_installation
        from .vm_config import load_config
        from .vm_web import check_storage, database_dsn
        trusted_installation(args.config)
        options = load_config(args.config)
        check_storage(options)
        sys.path.insert(0, str(options.frontend.resolve(strict=True)))
        from jasmine_web.batch.store import Queue
        from jasmine_web.batch import update_admission as control
        queue = Queue(database_dsn(options), options.pool_id)
        if args.command == 'status':
            result = control.status(queue)
        elif args.command == 'stage':
            with coordinator_lock():
                result = stage(queue,args.source,args.candidate)
        elif args.command == 'cancel':
            result = control.cancel(queue, str(UUID(args.reference)), digest(args.candidate))
        elif args.command == 'recover':
            from . import update_package
            candidate=digest(args.candidate);reference=str(UUID(args.reference))
            with coordinator_lock():
                current=control.status(queue)['current']
                if (not current or current['reference']!=reference or current['candidate']!=candidate
                        or current['state'] not in ('installing','failed')):
                    raise ValueError('No matching interrupted update owns this pause')
                try:
                    plan=update_package.validate_package(args.package,candidate,queue,allow_recovery=True)
                    callbacks=update_package.recovery_callbacks(options,queue,args.package,plan)
                    verification=digest(callbacks['rollback']())
                    result=control.recover(queue,reference,candidate,verification=verification)
                except BaseException:
                    control.fail(queue,reference,candidate,reason='rollback_failed');raise
        else:
            from . import update_package
            candidate = digest(args.candidate)
            if args.command == 'request':
                plan = update_package.validate_package(args.package, candidate, queue)
                result = control.request(queue, candidate, mode=plan['mode'])
            elif args.command == 'start':
                result = launch(options, queue, args.package, candidate, config=args.config,
                                wait_seconds=args.wait_seconds, poll_seconds=args.poll_seconds)
            else:
                with coordinator_lock():
                    plan = update_package.validate_package(args.package, candidate, queue)
                    callbacks = update_package.build_callbacks(options, queue, args.package, plan)
                    result = coordinate(queue, args.reference, candidate,
                        wait_seconds=args.wait_seconds, poll_seconds=args.poll_seconds, **callbacks)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 2 if result.get('timed_out') else 3 if result.get('reason')=='verified_rollback' else 0
    except Exception as error:
        print('Application-update operation failed: '+type(error).__name__+
              '. Inspect private update status and its journal before retrying.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
