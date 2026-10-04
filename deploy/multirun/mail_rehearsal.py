#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Disposable SMTP delivery, notification recovery and automatic retention rehearsal.
Uses a non-relaying loopback STARTTLS capture, fictional files and test PostgreSQL.
@author ross richardson
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import unittest
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._workflow import frontend_path
from deploy.multirun.proxy_rehearsal import HTTPSClient, Processes, free_port, require_test_database, until
from deploy.multirun.vm_boundary import NoRedirect
from deploy.multirun.smtp_capture import Capture, FROM


class LocalClient(HTTPSClient):
    """Actual loopback HTTP routes; SMTP independently uses verified STARTTLS.

    HTTPS and Secure cookies have their separate proxy rehearsals. This test
    changes neither browser policy nor production certificate configuration.
    """
    def __init__(self, origin):
        self.origin, self.cookie, self.csrf = origin, '', ''
        self.opener = build_opener(NoRedirect(), ProxyHandler({}))

    def sign_in(self, recipient, capture):
        from deploy.multirun.mail_fixture import check_message
        before = len(capture.messages())
        result = self.api('/api/code', dict(email=recipient))
        challenge = result['challenge']
        received = capture.messages()[before:]
        assert len(received) == 1 and received[0]['To'] == recipient
        body = check_message(received[0], self.origin)
        code = re.search(r'Your sign-in code is ([0-9]{6})\.', body).group(1)
        assert code not in json.dumps(result) and 'code' not in result
        status, headers, data = self.request('/api/verify', value=dict(challenge=challenge, code=code))
        signed_in = json.loads(data)
        assert status == 200 and signed_in['authorised'] is True
        parsed = SimpleCookie(headers['Set-Cookie'])
        assert len(parsed) == 1
        morsel = next(iter(parsed.values()))
        assert morsel['httponly'] and morsel['samesite'].lower() == 'strict'
        self.cookie, self.csrf = morsel.key+'='+morsel.value, signed_in['csrf']
        assert self.cookie not in data.decode()
        # A received code cannot be replayed to acquire a second session.
        _, _, data = self.request('/api/verify', value=dict(challenge=challenge, code=code))
        assert json.loads(data)['authorised'] is False


def serve_fixture(path):
    from deploy.multirun.mail_fixture import services
    from jasmine_web.batch.browser import create_app
    import uvicorn
    settings = json.loads(path.read_text())
    service, _, _ = services(settings)
    app = create_app(service, origin=settings['origin'], local_codes=False,
                     site=dict(name='SimPaths Online', subtitle='UK MultiRun', logo='', icon=''))
    uvicorn.run(app, host='127.0.0.1', port=settings['app_port'], workers=1,
                access_log=False, log_level='warning', proxy_headers=False)


def rehearsal(args, report):
    from deploy.multirun.artifacts import write_attribution
    from deploy.multirun.mail_fixture import services, close, clock, check_message
    from deploy.multirun.proxy_fixture import bootstrap, finish, RAW
    from psycopg import sql
    dsn = os.environ['JASMINE_BATCH_TEST_DSN']
    args.output.mkdir(mode=0o700, parents=True)
    write_attribution(args.output)
    def passed(message):
        report['checks'].append(message); print('PASS: '+message, flush=True)
    report['phase'] = 'local-and-postgresql-regressions'
    names = ['deploy.multirun.test_mail_rehearsal', 'test_completion_notifications', 'test_retention', 'test_storage']
    with (args.output/'regressions.log').open('w') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    report['regressions'] = dict(tests=result.testsRun, errors=len(result.errors),
                                 failures=len(result.failures), skipped=len(result.skipped))
    if not result.wasSuccessful() or result.skipped:
        raise AssertionError('Mail/retention regressions failed; inspect regressions.log')
    passed('notification and retention regressions pass without skipped cases')
    with tempfile.TemporaryDirectory(prefix='simpaths-mail-rehearsal-') as temporary:
        work = Path(temporary)
        state = work/'private'; state.mkdir(mode=0o700)
        capture = Capture(work/'mail', login=FROM, password=secrets.token_urlsafe(32))
        processes = Processes(work, args.output, args)
        service = queue = None
        settings = dict(state=str(state), schema='test_mail_'+uuid4().hex,
            app_port=free_port(), clock=datetime.now(timezone.utc).isoformat(), cleanup_enabled=True)
        settings['origin'] = 'http://127.0.0.1:'+str(settings['app_port'])
        def execute_pump(channel, *, retire=False):
            from jasmine_web.batch.local_executor import atomic_json
            atomic_json(work/'pump.json', settings)
            command = [sys.executable, str(Path(__file__).resolve()), '--pump-fixture', str(work/'pump.json'),
                       '--channel', channel]
            if retire: command.append('--retire-fixture')
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
            with (args.output/'pump.log').open('a') as log:
                log.write(result.stderr)
            if result.returncode:
                raise AssertionError('Fresh-process mail/cleanup pump failed; inspect pump.log')
            report['pump_processes'] = report.get('pump_processes', 0)+1
            return json.loads(result.stdout)
        def query(statement, values=()):
            with queue._connection() as connection:
                cursor = connection.execute(statement, values)
                return cursor.fetchall() if cursor.description else []
        def ready(client):
            try: return client.request('/')[0] == 200
            except (OSError, URLError): return False
        try:
            report['phase'] = 'temporary-smtp-certificate'
            with (args.output/'certificate.log').open('wb') as log:
                subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-keyout', str(work/'key.pem'), '-out', str(work/'cert.pem'), '-subj', '/CN=localhost',
                    '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                    stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
            (work/'key.pem').chmod(0o600)
            port = capture.start(work/'cert.pem', work/'key.pem')
            settings['smtp'] = dict(SMTP_HOST='127.0.0.1', SMTP_PORT=str(port), SMTP_USE_TLS='true',
                SMTP_LOGIN_EMAIL=FROM, SMTP_PASSWORD=capture.password, SMTP_FROM_EMAIL=FROM,
                SSL_CERT_FILE=str(work/'cert.pem'))
            inputs, owners = bootstrap(settings, dsn)
            service, queue, executor = services(settings)
            for recipient in ('alice@example.org', 'bob@example.org'):
                service.access.approve_email(recipient, seconds=90*86400)
            queue.grant_dataset(owners['alice@example.org'], inputs[-1], seconds=90*86400)
            settings['clock'] = query('SELECT clock_timestamp() AS now')[0]['now'].isoformat()
            processes.start_app(settings, script=Path(__file__).resolve())
            alice, bob, anonymous = (LocalClient(settings['origin']) for _ in range(3))
            until(lambda: ready(alice), seconds=45)
            report['phase'] = 'real-sign-in-email'
            alice.sign_in('alice@example.org', capture); bob.sign_in('bob@example.org', capture)
            assert not (state/'mail.json').exists()  # No callback or console-code fallback.
            capture.reject()
            status, _, data = anonymous.request('/api/code', value=dict(email='alice@example.org'))
            assert status == 503
            assert 'challenge' not in json.loads(data)
            assert capture.password not in data.decode() and '451' not in data.decode()
            assert alice.api('/api/session')['signed_in'] and bob.api('/api/session')['signed_in']
            passed('real sign-in mail uses authenticated STARTTLS; codes stay out of HTTP responses, cannot be replayed, and SMTP rejection returns a controlled error')

            report['phase'] = 'completion-outbox-and-process-restart'
            form = dict(name='PRIVATE_MAIL_LABEL_DO_NOT_SEND', common=dict(population=20, start_year=2019, end_year=2020),
                repetitions=3, baseline='baseline', auto_retry=True,
                run_sets=[dict(id='baseline', name='PRIVATE_BASELINE_LABEL', model_args={}),
                          dict(id='scenario', name='PRIVATE_SCENARIO_LABEL', model_args=dict(savingRate=.03),
                               dataset_revision=inputs[1])])
            review = alice.api('/api/review-experiment', dict(dataset=inputs[0], form=form))
            experiment = alice.api('/api/submit', dict(key='fictional-mail-rehearsal', review=review['review']))['id']
            first = finish(queue, executor)
            settings['clock'] = query('SELECT clock_timestamp() AS now')[0]['now'].isoformat()
            assert execute_pump('completion')['delivered'] == 0
            second = finish(queue, executor)
            base = query('SELECT clock_timestamp() AS now')[0]['now']
            settings['clock'] = base.isoformat()
            with clock(queue, base): service.completions.reconcile()
            notice = query('SELECT * FROM completion_notices WHERE experiment_id=%s', (experiment,))[0]
            identifier = f'<multirun-{notice["id"]}@example.org>'
            capture.reject(message_id=identifier)
            assert execute_pump('completion')['delivered'] == 0
            failed = query('SELECT * FROM completion_notices WHERE id=%s', (notice['id'],))[0]
            assert failed['attempts'] == 1 and failed['sent_at'] is None and failed['claimed_until'] is None
            assert execute_pump('completion')['delivered'] == 0
            processes.stop_app(); processes.start_app(settings, script=Path(__file__).resolve())
            until(lambda: ready(alice), seconds=45)
            assert alice.api('/api/session')['signed_in'] and bob.api('/api/session')['signed_in']
            settings['clock'] = (base+timedelta(seconds=61)).isoformat()
            assert execute_pump('completion')['delivered'] == 1
            assert execute_pump('completion')['delivered'] == 0
            delivered = [m for m in capture.messages() if m['Message-ID'] == identifier]
            assert len(delivered) == 1 and delivered[0]['To'] == 'alice@example.org'
            body = check_message(delivered[0], settings['origin'], forbidden=(RAW.decode(), str(state),
                form['name'], 'PRIVATE_BASELINE_LABEL', 'PRIVATE_SCENARIO_LABEL', alice.cookie, capture.password))
            assert 'Successful: 2; failed: 0; cancelled: 0' in body
            assert settings['origin']+'/results/'+experiment in body
            for client in (bob, anonymous):
                assert client.request('/api/results/'+experiment)[0] == 403
                # The bookmark opens a public sign-in shell. All experiment
                # data is obtained separately through the authenticated API.
                status, _, page = client.request('/results/'+experiment)
                assert status == 200 and RAW not in page and form['name'].encode() not in page
                assert client.request('/downloads/'+first.job_id)[0] == 403
            assert alice.api('/api/results/'+experiment)['configurations'][0]['downloadable']
            assert query('SELECT count(*) AS n FROM attempts')[0]['n'] == 2
            assert [e['accepted'] for e in capture.events if e['message_id'] == identifier] == [False, True]
            bob.sign_in('bob@example.org', capture)  # Sender still works after the failed delivery/restart.
            passed('completion waits for all configurations; fresh sender/frontend processes retry the same notice once, preserve owner cookies and enforce permissions on email links')

            report['phase'] = 'scheduled-retention-and-physical-cleanup'
            settings['clock'] = (base+timedelta(days=3.1)).isoformat()
            assert execute_pump('retention')['delivered'] > 0
            settings['clock'] = (base+timedelta(days=6.1)).isoformat()
            assert execute_pump('retention')['delivered'] > 0
            for lease in (first, second):
                notices = [m for m in capture.messages() if lease.attempt_id in m.get_content()]
                assert len(notices) == 2
                assert [m['Subject'] for m in notices] == ['MultiRun: scheduled data deletion', 'MultiRun: final data deletion reminder']
                for message in notices:
                    assert message['To'] == 'alice@example.org'
                    check_message(message, settings['origin'], forbidden=(RAW.decode(), str(state), capture.password))
                assert (executor.workspace(lease)/'work/output-606.csv').exists()
            settings['clock'] = (base+timedelta(days=7.1)).isoformat()
            execute_pump('retention', retire=True)
            for lease in (first, second):
                assert not (executor.workspace(lease)/'work/output-606.csv').exists()
                assert (executor.workspace(lease)/'work/private.log').read_bytes() == RAW
                assert alice.request('/downloads/'+lease.job_id)[0] == 403
                assert query('SELECT state FROM artifact_retention WHERE kind=%s AND artifact_id=%s',
                             ('output', lease.attempt_id))[0]['state'] == 'deleted'
            overview = alice.api('/api/results/'+experiment)
            assert all(row['state'] == 'succeeded' and not row['downloadable'] for row in overview['configurations'])
            assert (state/'prepared-2').exists()  # Provider catalogue is exempt from user expiry.
            assert query('SELECT count(*) AS n FROM attempts')[0]['n'] == 2
            passed('96-hour and final 24-hour notices precede real file removal; saved download links stop working, history and private diagnostics remain, and provider data is exempt')

            report['phase'] = 'native-smtp-retention-edge-cases'
            from deploy.multirun.mail_delivery_tests import MailDeliveryTests
            MailDeliveryTests.capture = capture
            with (args.output/'smtp-retention.log').open('w') as log:
                tests = unittest.TextTestRunner(stream=log, verbosity=2).run(
                    unittest.defaultTestLoader.loadTestsFromTestCase(MailDeliveryTests))
            report['native_tests'] = dict(tests=tests.testsRun, errors=len(tests.errors),
                                         failures=len(tests.failures), skipped=len(tests.skipped))
            if not tests.wasSuccessful() or tests.skipped:
                raise AssertionError('Native SMTP/retention edge cases failed; inspect smtp-retention.log')
            assert not capture.failures
            report['smtp'] = dict(accepted=len(capture.messages()),
                rejected=sum(not event['accepted'] for event in capture.events),
                all_accepted_tls=all(event['tls'] and event['authenticated'] for event in capture.events if event['accepted']),
                attachments=sum(len(list(m.iter_attachments())) for m in capture.messages()))
            passed('real SMTP checks cover TLS/authentication/relay rejection, warning retries, renewed deadlines, failed-job expiry, preview grace, reader locks, quota release, operator routing and competing senders')
            report['passed'] = True
        finally:
            cleanup_errors = []
            for cleanup in (processes.close, capture.close, lambda: close(service) if service else None):
                try: cleanup()
                except Exception as error: cleanup_errors.append(type(error).__name__)
            if queue:
                try:
                    with queue._connection() as connection:
                        connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(settings['schema'])))
                except Exception as error: cleanup_errors.append(type(error).__name__)
            report['cleanup'] = not cleanup_errors
            if cleanup_errors: report['cleanup_errors'] = cleanup_errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=frontend_path())
    parser.add_argument('--output', type=Path)
    parser.add_argument('--execute-proof', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--serve-fixture', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--pump-fixture', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--channel', choices=['completion', 'retention', 'problem'], default='completion', help=argparse.SUPPRESS)
    parser.add_argument('--retire-fixture', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    os.umask(0o077)
    args.frontend = args.frontend.expanduser().resolve(strict=True)
    os.environ['JASMINE_WEB_REPO'] = str(args.frontend)
    sys.path[:0] = [str(args.frontend), str(args.frontend/'tests/batch')]
    if args.serve_fixture or args.pump_fixture:
        require_test_database(os.environ.get('JASMINE_BATCH_TEST_DSN', ''))
        if args.serve_fixture:
            serve_fixture(args.serve_fixture)
        else:
            from deploy.multirun.mail_fixture import pump
            with redirect_stdout(sys.stderr):
                value = pump(json.loads(args.pump_fixture.read_text()), args.channel, retire=args.retire_fixture)
            print(json.dumps(value))
        return 0
    if not args.execute_proof:
        evidence = args.output or Path.home()/'simpaths-benchmarks'/('mail-retention-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        return subprocess.run([sys.executable, str(args.frontend/'scripts/test_batch_queue.py'), '--proof-only',
            '--proof-script', str(Path(__file__).resolve()), '--proof-requirements',
            str(Path(__file__).with_name('requirements.txt')), str(args.frontend/'requirements-vm.txt'),
            '--output', str(evidence)]).returncode
    if not args.output or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Run without --execute-proof to create a disposable test database')
    if args.output.exists(): parser.error('Choose a new evidence directory')
    require_test_database(os.environ['JASMINE_BATCH_TEST_DSN'])
    report = dict(started_at=datetime.now(timezone.utc).isoformat(), passed=False, cleanup=False,
                  synthetic=True, production_acceptance=False, checks=[], phase='starting')
    try:
        rehearsal(args, report)
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        report['passed'], report['error_type'] = False, type(error).__name__
        if isinstance(error, AssertionError): report['message'] = str(error)[:500]
        print('STOPPED: '+type(error).__name__+' during '+report['phase']+'; inspect the private logs.', file=sys.stderr)
    finally:
        if args.output.exists():
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(('PASSED' if report['passed'] and report['cleanup'] else 'FAILED')+': '+str(args.output/'report.json'))
    return 0 if report['passed'] and report['cleanup'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
