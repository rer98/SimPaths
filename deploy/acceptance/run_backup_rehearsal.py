#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Real scheduled capture/encryption/SFTP/restore with disposable PostgreSQL and keys.
No installed service, external backup account, scientific model or real email.
@author ross richardson
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._workflow import frontend_path
from deploy.acceptance._backup_rehearsal import (BackupTimer, Relay, SFTPServer, command,
                                                key_pair, timer_policy, verify_effective_policy, write_pin)
from deploy.acceptance._supervisor import environment_file
from deploy.multirun.proxy_rehearsal import require_test_database, until

CANARY = b'FICTIONAL-PRIVATE-BACKUP-CANARY-NOT-FOR-SFTP-PLAINTEXT\n'


def private_file(path, content):
    with path.open('x') as stream:
        os.chmod(path, 0o600); stream.write(content)


def schedule_config(path, fixture, store, public, recipient, remote, frontend):
    """Write an isolated explicit source; no actual operator config is consulted."""
    source = dict(frontend=str(frontend), state=str(fixture.state), pool=fixture.q.pool_id,
                  schema=fixture.q.schema, dsn_file=str(path.parent/'dsn'))
    if fixture.tools.container: source['client_container'] = fixture.tools.container
    values = dict(source=source, backup=dict(directory=str(store), public_key=str(public),
        recipient=recipient, retry_minutes=1), remote=remote, alerts=dict(enabled=False))
    lines = []
    for section, fields in values.items():
        lines.append('['+section+']\n')
        for key, value in fields.items():
            text = str(value).lower() if type(value) in (bool,int) else json.dumps(value)
            lines.append(key+' = '+text+'\n')
    private_file(path, ''.join(lines))


def encryption_key(root):
    from deploy.multirun.backup_transport import gpg_command
    home = root/'recovery-keyring'; home.mkdir(mode=0o700)
    result = subprocess.run(gpg_command(home)+['--pinentry-mode','loopback','--passphrase','',
        '--quick-generate-key','Fictional Backup Rehearsal <backup@example.invalid>',
        'rsa2048','encrypt','1d'], capture_output=True, timeout=90)
    if result.returncode: raise RuntimeError('Disposable recovery key creation failed')
    listing = subprocess.check_output(gpg_command(home)+['--with-colons','--fingerprint','--list-keys'])
    recipient = next(line.split(':')[9] for line in listing.decode().splitlines() if line.startswith('fpr:'))
    public = root/'public.asc'
    public.write_bytes(subprocess.check_output(gpg_command(home)+['--armor','--export',recipient])); public.chmod(0o600)
    return home, public, recipient


def schedule_status(store):
    from deploy.multirun.backup_schedule import load_status
    return load_status(store)


def partial_transfer(server, store):
    value = schedule_status(store); job = value['current']
    if not job or job['stage'] != 'copying': return False
    cipher = store/'jobs'/job['id']/'recovery.tar.gpg'
    partial = server.data/('recovery-'+job['backup_id']+'.tar.gpg.partial')
    if not partial.exists() or not cipher.exists(): return False
    info = partial.stat()
    size, full = info.st_size, cipher.stat().st_size
    if not 0 < size < full: return False
    return dict(job=job.copy(), partial_bytes=size, ciphertext_bytes=full,partial_mode=info.st_mode&0o777)


def complete_status(store):
    value = schedule_status(store)
    return value['last_success'] if value['last_success'] and not value['current'] and not value['incident'] else False


def scheduler_lines(path):
    if not path.exists(): return []
    return [json.loads(line) for line in path.read_text().splitlines(keepends=True)
            if line.startswith('{') and line.endswith('\n')]


def permission_check(fixture, recovered, authentication):
    """Use restored HTTP routes, the original cookie and current owner/provider rules."""
    from jasmine_web.batch.access import Access
    from jasmine_web.batch.browser import COOKIE, create_app
    from jasmine_web.batch.datasets import Datasets
    from jasmine_web.batch.local_executor import LocalExecutor
    from jasmine_web.batch.results import Results
    from jasmine_web.batch.submission_service import Submissions
    from starlette.testclient import TestClient
    from deploy.multirun.browser_model import BrowserModel
    from deploy.multirun.queue_adapter import result_catalogue, result_name
    from deploy.multirun.releases import ReleaseRegistry
    executor = LocalExecutor(recovered/'execution'); fixture.target_q.bind_executor(executor.identity())
    access = Access(fixture.target_q, fixture.secret, fixture.deliver)
    datasets = Datasets(fixture.target_q, recovered/'uploads', reserve_bytes=1)
    service = Submissions(access,datasets,BrowserModel(ReleaseRegistry(recovered).inventory()))
    service.results = Results(service,executor,result_catalogue,name=result_name)
    with TestClient(create_app(service,origin='https://restore.example.org'), base_url='https://restore.example.org',
                    headers={'Origin':'https://restore.example.org'}) as client:
        client.cookies.set(COOKIE,authentication['token'])
        session = client.get('/api/session').json()
        assert session['signed_in'] and session['csrf'] == authentication['csrf']
        own = client.get('/downloads/'+fixture.completed.job_id); assert own.status_code == 200
        assert client.get('/downloads/'+fixture.provider_completed.job_id).status_code == 403
        client.cookies.clear()
        assert client.get('/downloads/'+fixture.completed.job_id).status_code in (401,403,404)
        challenge = client.post('/api/code',json={'email':'bob@example.org'}).json()['challenge']
        signed = client.post('/api/verify',json={'challenge':challenge,'code':fixture.mail['bob@example.org']})
        assert signed.json()['authorised']
        assert client.get('/downloads/'+fixture.completed.job_id).status_code == 403


def rehearsal(args, report):
    from deploy.multirun import backup, backup_transport as transport
    from deploy.multirun.artifacts import ArtifactError, write_attribution
    from deploy.multirun.backup_files import file_hash
    from deploy.multirun.test_backup import BackupRestoreTests, IMAGE
    from jasmine_web.batch.policy import Resources, RuntimeAllowance
    from jasmine_web.batch.backup import table_inventory
    from deploy.multirun.maintenance import GATE
    from deploy.multirun.releases import atomic_json
    work = Path(tempfile.mkdtemp(prefix='simpaths-backup-rehearsal-'))
    write_attribution(work)
    tag = secrets.token_hex(16)
    timer = BackupTimer(tag, args.output/'supervisor.log')
    fixture = None; server = relay = keyring = store = None; writer = None
    stopping = threading.Event(); writes = []; writer_errors = []
    report.update(template_policy=timer_policy(), phases=[], phase='preflight')
    sources = ('deploy/multirun/backup.py','deploy/multirun/backup_live.py',
               'deploy/multirun/backup_schedule.py','deploy/multirun/backup_transport.py',
               'deploy/multirun/vm/simpaths-backup.service','deploy/multirun/vm/simpaths-backup.timer',
               'deploy/acceptance/run_backup_rehearsal.py','deploy/acceptance/_backup_rehearsal.py',
               'deploy/acceptance/backup_sftp/Dockerfile','deploy/acceptance/backup_sftp/entrypoint.sh',
               'deploy/acceptance/backup_sftp/sshd_config')
    report['source_hashes'] = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources}

    def passed(message):
        report['phases'].append(message); print('PASS: '+message, flush=True)

    try:
        timer.probe()
        for tool in ('gpg','gpgconf','ssh-keygen','sftp','docker'):
            if not shutil.which(tool): raise RuntimeError('Install '+tool+' before running this rehearsal')
        if shutil.disk_usage(work).free < 2*1024**3:
            raise RuntimeError('Need at least 2 GiB free in temporary storage for the backup rehearsal')
        # Reuse the private native-restore fixture: original uploads, two finished
        # owner/provider jobs, release registry, deadlines and a separate target DB.
        fixture = BackupRestoreTests(); fixture.setUp()
        old = fixture.q.inspect(fixture.owner,fixture.queued)['jobs'][0]
        fixture.q.cancel(fixture.owner,old['id'])
        experiment = fixture.q.submit(fixture.owner,uuid4().hex,label='Active fictional backup workload',
            model_digest=IMAGE,dataset_id=fixture.dataset,seed_plan=['606'],
            run_sets=[dict(id='baseline',parameters=fixture.configuration.editable_configuration())],
            resources=Resources(2000,4000,8000),runtime_allowance=RuntimeAllowance(0,1800))
        lease = fixture.q.claim('backup-rehearsal-writer'); assert lease
        assert fixture.q.started(lease) is None
        mutable = fixture.executor.workspace(lease)/'work/mutable-person.csv'
        mutable.parent.mkdir(mode=0o700,parents=True)
        mutable.write_bytes(b'fictional active output')
        # Eight MiB of incompressible fictional bytes exercise real transfer without
        # a large RAM/disk requirement. The canary is an owned ready upload.
        payload = work/'fictional-input.csv'
        with payload.open('xb') as stream:
            stream.write(CANARY)
            for _ in range(128): stream.write(os.urandom(65536))
        with payload.open('rb') as stream:
            upload = fixture.datasets.receive(fixture.owner,payload.name,stream,expected_bytes=payload.stat().st_size)
        upload_hash = file_hash(fixture.state/'uploads',upload)['sha256']
        challenge = asyncio.run(fixture.access.issue('alice@example.org','fictional-client'))['challenge']
        authentication = fixture.access.verify(challenge,fixture.mail['alice@example.org'],'fictional-client')
        assert authentication['authorised']
        with fixture.q._connection() as c:
            frozen = c.execute('SELECT e.policy,j.execution_run FROM jobs j JOIN experiments e ON e.id=j.experiment_id WHERE j.id=%s',
                               (lease.job_id,)).fetchone()

        def heartbeat():
            try:
                while not stopping.wait(.2):
                    reason = fixture.q.heartbeat(lease)
                    if reason: raise AssertionError('Fictional writer exceeded its frozen fixture allowance')
                    mutable.write_text('fictional active output '+str(len(writes)))
                    writes.append(time.monotonic())
            except Exception as error: writer_errors.append(type(error).__name__)
        writer = threading.Thread(target=heartbeat,daemon=True); writer.start()
        keyring = work/'recovery-keyring'
        keyring, public, recipient = encryption_key(work)
        server = SFTPServer(work,tag,args.output/'sftp.log'); server.start()
        report['sftp_image_id'] = server.image
        relay = Relay(server.port)
        pins = work/'known_hosts'; write_pin(pins,relay.port,server.host_public)
        remote = dict(host='127.0.0.1',user='simpaths_backup_test',port=relay.port,directory='/backups',
                      identity=str(server.identity),known_hosts=str(pins))
        connection = transport.SFTP(**remote)
        until(lambda: connection.batch(['ls "/backups"']),seconds=30)
        report['phase'] = 'host-key-and-authentication'
        wrong_public = key_pair(work/'wrong-ssh-key')
        wrong_pins = work/'wrong-known-hosts'; write_pin(wrong_pins,relay.port,wrong_public)
        assert not transport.SFTP(**{**remote,'known_hosts':str(wrong_pins)}).batch(['ls "/backups"'])
        assert not transport.SFTP(**{**remote,'identity':str(work/'wrong-ssh-key')}).batch(['ls "/backups"'])
        assert not connection.batch(['get "/etc/passwd" '+transport.sftp_quote(work/'forbidden')])
        assert not (work/'forbidden').exists()
        passed('real OpenSSH accepts the disposable key and pinned host; wrong identities/pins and files outside the SFTP chroot are denied')

        store = work/'backup-store'; store.mkdir(mode=0o700)
        private_file(work/'dsn',fixture.q.dsn)
        config = work/'backup.toml'; schedule_config(config,fixture,store,public,recipient,remote,args.frontend)
        environment = work/'backup.env'
        environment_file(environment,dict(PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1',
                                         JASMINE_WEB_REPO=str(args.frontend)))
        log = args.output/'schedule.log'
        report['phase'] = 'scheduled-live-capture'
        timer.start(config,environment,log)
        active = until(lambda: partial_transfer(server,store),seconds=120)
        service = timer.show(); ticks = timer.show('timer')
        report['effective_policy'] = dict(service=service,timer=ticks)
        verify_effective_policy(service,ticks)
        job = active['job']; cipher = store/'jobs'/job['id']/'recovery.tar.gpg'
        receipt = transport.sealed(cipher,recipient=recipient)
        assert not (cipher.parent/'plain').exists(), 'Verified encryption did not reclaim backup plaintext'
        assert len(writes) >= 2 and not writer_errors, 'Fictional writer stopped during live backup capture'
        assert not list(server.data.glob('*.tar.gpg')), 'Partial transfer was published before it completed'
        report['capture'] = dict(backup_id=job['backup_id'],job_id=job['id'],sha256=receipt['sha256'],
                                ciphertext_bytes=receipt['bytes'],partial_bytes=active['partial_bytes'],
                                remote_partial_mode=format(active['partial_mode'],'04o'))
        passed('a real transient timer runs the online backup command under its resource limits while the fictional simulation continues writing')

        report['phase'] = 'abrupt-backup-process-interruption'
        crashed_pid = timer.crash()
        # Shut the actual SFTP endpoint down before the next tick. The subsequent
        # fresh process must preserve its ciphertext, record failure and back off.
        server.stop()
        def failure_recorded():
            value = schedule_status(store)
            current = value['current']
            return value if current and current.get('retry_at') and value['incident'] else False
        pending = until(failure_recorded,seconds=40)
        assert pending['current']['id'] == job['id'] and pending['current']['stage'] == 'copying'
        assert pending['current']['sha256'] == receipt['sha256'] and pending['last_success'] is None
        assert transport.sealed(cipher) == receipt
        assert len(list((store/'jobs').iterdir())) == 1
        replacement = timer.show()
        assert replacement and int(replacement['ExecMainPID']) > 1 and int(replacement['ExecMainPID']) != crashed_pid
        report['interruption'] = dict(process_id=crashed_pid,replacement_process_id=int(replacement['ExecMainPID']),
                                     retry_at=pending['current']['retry_at'],
                                     stage='copying',same_ciphertext=True)
        passed('SIGKILL during a partial upload and an SFTP outage leave the same encrypted snapshot pending; fresh timer processes record a bounded retry')

        report['phase'] = 'restarting-sftp-and-reconnecting'
        old_port = server.port
        server.restart()
        relay.retarget(server.port)
        report['transport_restart'] = dict(previous_server_port=old_port,current_server_port=server.port,
                                           client_port=relay.port)
        until(lambda: connection.batch(['ls "/backups"']),seconds=30)
        report['transport_restart']['original_host_pin_verified'] = True
        report['phase'] = 'scheduled-retry-delay'
        next_tick = len(scheduler_lines(log))
        until(lambda: len(scheduler_lines(log)) > next_tick,seconds=15)
        assert schedule_status(store)['last_success'] is None, 'Retry ran before its configured delay'
        assert transport.sealed(cipher) == receipt
        # The fixture config uses the supported one-minute retry; no machine clock
        # or private journal is edited to make the retry succeed early.
        report['phase'] = 'scheduled-retry-and-verification'
        completed = until(lambda: complete_status(store),seconds=120)
        assert completed['job_id'] == job['id'] and completed['backup_id'] == job['backup_id']
        assert completed['sha256'] == receipt['sha256'] and completed['off_machine']
        remote_name = 'recovery-'+job['backup_id']+'.tar.gpg'
        final = server.data/remote_name
        assert hashlib.sha256(final.read_bytes()).hexdigest() == receipt['sha256']
        assert final.stat().st_mode & 0o777 == 0o600
        assert set(p.name for p in server.data.iterdir()) == {remote_name,remote_name+'.json'}
        assert (final.stat().st_nlink,Path(str(final)+'.json').stat().st_nlink) == (1,1)
        assert CANARY not in final.read_bytes()
        before = len(scheduler_lines(log))
        until(lambda: len(scheduler_lines(log)) >= before+2,seconds=20)
        assert complete_status(store) == completed and len(list((store/'jobs').iterdir())) == 1
        lines = scheduler_lines(log)
        assert any(not v['passed'] for v in lines) and sum(v['passed'] for v in lines) >= 2
        report['scheduler'] = dict(invocations=len(lines),failed_checks=sum(not v['passed'] for v in lines),
                                  successful_checks=sum(v['passed'] for v in lines),captured_snapshots=1)
        report['phase'] = 'stopping-backup-timer'
        timer.stop()
        passed('automatic timer retries respect the delay and complete native encrypted readback/hard-link publication; later checks do not recapture or overwrite it')

        report['phase'] = 'transport-conflict-and-retrieval'
        assert connection.copy(cipher)['already_copied']
        conflicting = work/'conflicting.gpg'
        data = bytearray(cipher.read_bytes()); data[len(data)//2] ^= 1
        conflicting.write_bytes(data); conflicting.chmod(0o400)
        atomic_json(Path(str(conflicting)+'.json'),{**receipt,**file_hash(work,conflicting.name)})
        try: connection.copy(conflicting)
        except ArtifactError: pass
        else: raise AssertionError('Different ciphertext overwrote a completed backup')
        assert hashlib.sha256(final.read_bytes()).hexdigest() == receipt['sha256']
        recovery = work/'recovery-machine'; recovery.mkdir(mode=0o700)
        retrieved = recovery/'retrieved.tar.gpg'
        assert connection.batch(['get '+transport.sftp_quote('/backups/'+remote_name)+' '+transport.sftp_quote(retrieved),
            'get '+transport.sftp_quote('/backups/'+remote_name+'.json')+' '+transport.sftp_quote(Path(str(retrieved)+'.json'))])
        assert hashlib.sha256(retrieved.read_bytes()).hexdigest() == receipt['sha256']
        assert json.loads(Path(str(retrieved)+'.json').read_text()) == receipt
        try: transport.unseal(retrieved,recovery/'wrong-checksum',keyring=keyring,expected_sha256='0'*64)
        except ArtifactError: pass
        else: raise AssertionError('Wrong trusted ciphertext checksum was accepted')
        assert not (recovery/'wrong-checksum').exists()
        passed('completed remote copies are idempotent and cannot be replaced by conflicting bytes; independent SFTP retrieval matches the trusted receipt')

        report['phase'] = 'native-decryption-and-inactive-restore'
        decrypted = recovery/'backup'
        transport.unseal(retrieved,decrypted,keyring=keyring,expected_sha256=receipt['sha256'])
        manifest = backup.verify(decrypted)
        assert manifest['id'] == receipt['backup_id']
        assert str(lease.attempt_id) in manifest['recovery']['interrupted_attempts']
        assert all(lease.execution_key not in name for name in manifest['state_tree']['files'])
        assert manifest['state_tree']['files']['uploads/'+upload]['sha256'] == upload_hash
        stopping.set(); writer.join(timeout=5)
        assert not writer.is_alive() and not writer_errors
        # No actual model/frontend was launched; stop the sole fictional writer
        # before explicitly isolating this source for fixture activation.
        restored = backup.restore(fixture.target_q,decrypted,fixture.target,fixture.target_tools)
        assert restored['inactive'] and (fixture.target/GATE).exists()
        with fixture.target_q._connection() as c:
            held = table_inventory(c,fixture.target_q)
            active_job = c.execute('SELECT j.*,e.policy FROM jobs j JOIN experiments e ON e.id=j.experiment_id WHERE j.id=%s',
                                   (lease.job_id,)).fetchone()
            assert active_job['attempts'] == 1 and active_job['state'] == 'retry_wait'
            assert active_job['policy'] == frozen['policy'] and active_job['execution_run'] == frozen['execution_run']
            assert c.execute('SELECT count(*) AS n FROM reservations WHERE released_at IS NULL').fetchone()['n'] == 0
        backup.restore(fixture.target_q,decrypted,fixture.target,fixture.target_tools,resume=True)
        with fixture.target_q._connection() as c: assert table_inventory(c,fixture.target_q) == held
        assert file_hash(fixture.target/'uploads',upload)['sha256'] == upload_hash
        assert (fixture.target/'session-secret').read_text() == fixture.secret
        assert not (fixture.target/'execution'/lease.execution_key).exists()
        passed('native GnuPG decryption and PostgreSQL restore verify files/rows, remain inactive and preserve original seeds, policy and retry count while omitting live output')

        report['phase'] = 'restored-http-permissions'
        backup.activate(fixture.target_q,decrypted,fixture.target,image_check=lambda _:None,source_isolated=True)
        permission_check(fixture,fixture.target,authentication)
        source = fixture.q.inspect(fixture.owner,experiment)['jobs'][0]
        assert source['attempts'] == 1 and source['state'] == 'active'
        with fixture.q._connection() as c:
            source_policy = c.execute('SELECT e.policy,j.execution_run FROM jobs j JOIN experiments e ON e.id=j.experiment_id WHERE j.id=%s',
                                      (lease.job_id,)).fetchone()
        assert source_policy == frozen and hashlib.sha256(payload.read_bytes()).hexdigest() == upload_hash
        report['recovery'] = dict(backup_id=manifest['id'],files=len(manifest['state_tree']['files']),
            tables=len(manifest['database']),writer_heartbeats=len(writes),attempts=1,
            fictional_image_check=True,original_owner_cookie_works=True,provider_raw_denied=True)
        passed('restored routes accept the original owner cookie, deny anonymous/other-owner/provider raw downloads and leave the source attempt unchanged')
        report['passed'] = True
    finally:
        errors = []
        if store and store.exists():
            try: report['last_scheduler_status'] = schedule_status(store)
            except Exception as error: report['status_read_error'] = type(error).__name__
        if timer.created:
            try: report['last_supervisor_state'] = dict(service=timer.show(),timer=timer.show('timer'))
            except Exception as error: report['supervisor_read_error'] = type(error).__name__
        if server and server.data.exists():
            report['remote_files'] = {}
            try:
                for path in server.data.iterdir():
                    try: info = path.lstat()
                    except FileNotFoundError: continue  # A transfer may just have removed its partial.
                    report['remote_files'][path.name] = dict(bytes=info.st_size,mode=format(info.st_mode&0o777,'04o'),
                                                           links=info.st_nlink)
            except Exception as error: report['remote_metadata_error'] = type(error).__name__
        stopping.set()
        if writer:
            writer.join(timeout=5)
            if writer.is_alive(): errors.append('writer')
        for resource in (timer,relay,server):
            if resource:
                try: resource.close()
                except Exception as error: errors.append(type(error).__name__)
        if keyring and keyring.exists():
            try: transport.dispose_keyring(keyring)
            except Exception as error: errors.append(type(error).__name__)
        if not errors and fixture:
            target = getattr(fixture,'target_q',None); source = getattr(fixture,'q',None)
            target_dsn = target.dsn if target else None
            source_schema = source.schema if source else None
            source_root = getattr(fixture,'root',None)
            if not fixture.doCleanups(): errors.append('fixture-cleanup')
            try:
                import psycopg
                from psycopg.conninfo import conninfo_to_dict
                with psycopg.connect(os.environ['JASMINE_BATCH_TEST_DSN']) as connection:
                    target_name = conninfo_to_dict(target_dsn)['dbname'] if target_dsn else None
                    if (target_name and connection.execute('SELECT 1 FROM pg_database WHERE datname=%s',(target_name,)).fetchone() or
                            source_schema and connection.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(source_schema,)).fetchone() or
                            source_root and source_root.exists()):
                        errors.append('fixture-storage-still-present')
            except Exception as error: errors.append(type(error).__name__)
        if not errors: shutil.rmtree(work)
        report['cleanup'] = not errors
        if errors:
            report.update(cleanup_errors=errors,retained_fixture=str(work),passed=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--output',type=Path)
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    args = parser.parse_args(argv); os.umask(0o077)
    args.frontend = args.frontend.expanduser().resolve(strict=True)
    os.environ['JASMINE_WEB_REPO'] = str(args.frontend)
    sys.path[:0] = [str(args.frontend),str(args.frontend/'tests/batch')]
    if not args.execute_proof:
        evidence = args.output or Path.home()/'simpaths-benchmarks'/('backup-rehearsal-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        return subprocess.run([sys.executable,str(args.frontend/'scripts/test_batch_queue.py'),'--proof-only',
            '--proof-script',str(Path(__file__).resolve()),'--proof-requirements',
            str(ROOT/'deploy/multirun/requirements.txt'),str(args.frontend/'requirements-vm.txt'),
            '--output',str(evidence)]).returncode
    if not args.output or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Run without --execute-proof to create a disposable database')
    if args.output.exists(): parser.error('Choose a new evidence directory')
    require_test_database(os.environ['JASMINE_BATCH_TEST_DSN'])
    report = dict(started_at=datetime.now(timezone.utc).isoformat(),passed=False,cleanup=False,
                  synthetic=True,production_acceptance=False,phase='regressions')
    args.output.mkdir(mode=0o700,parents=True)
    from deploy.multirun.artifacts import write_attribution
    write_attribution(args.output)
    try:
        # Explicit classes avoid environment-dependent skips and unrelated DB tests.
        names = ['deploy.acceptance.test_backup_rehearsal','deploy.multirun.test_backup.BackupFileTests',
                 'deploy.multirun.test_backup_transport.TransportTests','deploy.multirun.test_backup_schedule.ScheduleTests']
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
        report['regressions'] = dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped))
        if not result.wasSuccessful() or result.skipped: raise AssertionError('Focused backup regressions must pass without skips')
        print('PASS: focused backup, scheduling and transfer regressions pass without skips',flush=True)
        rehearsal(args,report)
    except (Exception,KeyboardInterrupt,SystemExit) as error:
        report['passed'],report['error_type'] = False,type(error).__name__
        if isinstance(error,(AssertionError,RuntimeError)): report['message'] = str(error)[:500]
        trace = error.__traceback__
        while trace and trace.tb_next: trace = trace.tb_next
        if trace:
            report['failure_location'] = dict(file=Path(trace.tb_frame.f_code.co_filename).name,
                                             line=trace.tb_lineno,function=trace.tb_frame.f_code.co_name)
        detail = ': '+report['message'] if report.get('message') else ''
        print('STOPPED: '+type(error).__name__+' during '+report['phase']+detail+'; inspect the private logs.',file=sys.stderr)
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (args.output/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
    print(('PASSED' if report['passed'] and report['cleanup'] else 'FAILED')+': '+str(args.output/'report.json'))
    return 0 if report['passed'] and report['cleanup'] else 1


if __name__ == '__main__': raise SystemExit(main())
