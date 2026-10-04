#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Disposable systemd crash recovery for real SingleRun/MultiRun routes and Docker.
Uses fictional models; no service installation, reboot, real email or simulation.
@author ross richardson
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._workflow import frontend_path
from deploy.multirun.proxy_rehearsal import free_port, require_test_database, until
from deploy.acceptance._supervisor import Supervisor, environment_file, restart_policy


def single_client(origin):
    from deploy.multirun.mail_rehearsal import LocalClient
    class Single(LocalClient):
        def __init__(self):
            super().__init__(origin); self.cookies = {}

        def api(self, path, value=None):
            # This client talks directly to the loopback SingleRun frontend.
            # Its deployed no-store policy is supplied/tested by the HTTPS proxy,
            # unlike MultiRun's API which emits that header itself.
            status, _, body = self.request(path, value=value)
            if status not in (200, 201):
                raise AssertionError(f'Expected SingleRun API success at {path}; HTTP {status}')
            return json.loads(body)

        def form(self, path, fields=None):
            headers = {'Origin':self.origin, 'Content-Type':'application/x-www-form-urlencoded'}
            if self.cookie: headers['Cookie'] = self.cookie
            try:
                response = self.opener.open(Request(self.origin+path, data=urlencode(fields or {}).encode(), headers=headers), timeout=30)
            except HTTPError as error:
                response = error
            with response:
                value = response.status, response.headers, response.read(65537)
            for field in value[1].get_all('Set-Cookie', []):
                parsed = SimpleCookie(field)
                for key, cookie in parsed.items():
                    if not key.startswith('__jasmine_owner_'): continue
                    assert cookie['httponly'] and cookie['samesite'].lower() == 'lax' and cookie['path'] == '/'
                    if cookie['max-age'] == '0': self.cookies.pop(key, None)
                    else: self.cookies[key] = cookie.value
            self.cookie = '; '.join(key+'='+value for key, value in self.cookies.items())
            return value
    return Single()


def sign_in(client, state, email):
    value = client.api('/api/code', dict(email=email))
    code = json.loads((state/'mail.json').read_text())[email]
    status, headers, body = client.request('/api/verify', value=dict(challenge=value['challenge'], code=code))
    signed = json.loads(body)
    assert status == 200 and signed['authorised'], signed
    parsed = SimpleCookie(headers['Set-Cookie']); assert len(parsed) == 1
    cookie = next(iter(parsed.values()))
    assert cookie['httponly'] and cookie['samesite'].lower() == 'strict'
    client.cookie = cookie.key+'='+cookie.value
    client.csrf = signed['csrf']


def submit(client, dataset, name):
    form = dict(name=name, common=dict(population=20, start_year=2019, end_year=2020),
        repetitions=2, first_seed='606', baseline='first', auto_retry=True,
        run_sets=[dict(id='first', name='Fictional alternative', model_args={})])
    value = client.api('/api/review-experiment', dict(dataset=dataset, form=form))
    status, _, body = client.request('/api/submit', value=dict(key=uuid4().hex, review=value['review']))
    value = json.loads(body); assert status == 201, value
    return value['id']


def allocations(queue):
    with queue._connection() as connection:
        batch = connection.execute('SELECT * FROM reservations WHERE released_at IS NULL ORDER BY attempt_id').fetchall()
        single = connection.execute('SELECT * FROM interactive_reservations ORDER BY session_id').fetchall()
    return dict(batch=[str(row['attempt_id']) for row in batch], single=[row['session_id'] for row in single])


def attempt(queue, experiment, owner):
    job = queue.inspect(owner, experiment)['jobs'][0]
    with queue._connection() as connection:
        row = connection.execute('SELECT * FROM attempts WHERE job_id=%s ORDER BY number DESC LIMIT 1', (job['id'],)).fetchone()
        spec = connection.execute('SELECT specification,policy FROM experiments WHERE id=%s', (experiment,)).fetchone()
    return job, row, spec


def running_attempt(queue, settings, experiment, owner, previous=None):
    from jasmine_web.batch.docker_executor import DockerCLI
    job, row, spec = attempt(queue, experiment, owner)
    if row is None or (previous and str(row['id']) == previous) or job['state'] != 'active':
        return False
    path = Path(settings['state'])/'execution'/row['execution_key']
    if not (path/'work/starts.txt').exists() or not (path/'container.json').exists(): return False
    identifier = json.loads((path/'container.json').read_text())['id']
    docker = DockerCLI().inspect(identifier)
    if not docker or not docker['State']['Running']: return False
    assert (path/'work/starts.txt').read_text() == 'started\n'
    return dict(job=job, attempt=row, specification=spec, path=path, container=identifier)


def wait_ready(client, path, *, supervisor=None, role=None):
    def ready():
        if supervisor and supervisor.show(role)['ActiveState'] == 'failed':
            raise RuntimeError(role+' fixture could not start; inspect '+role+'.log')
        try: return client.request(path)[0] == 200
        except (OSError, URLError): return False
    until(ready, seconds=90)


def start_frontends(supervisor, policies, common, frontend, evidence):
    # The real SingleRun unit runs from JAS-mine-web because its static mount
    # is relative to that directory. MultiRun runs from the SimPaths checkout.
    for role in ('batch', 'single'):
        paths = {**common, 'workdir':frontend if role == 'single' else ROOT,
                 'log':evidence/(role+'.log')}
        supervisor.start(role, policies[role], **paths)


def verify_policy(value):
    expected = dict(Restart='always', RestartUSec='10s', StartLimitIntervalUSec='5min',
        StartLimitBurst='5', KillMode='control-group', KillSignal='15', Type='simple',
        TimeoutStopUSec='infinity', UMask='0077')
    assert all(value.get(key) == field for key, field in expected.items()), 'Effective systemd restart policy differs'


def automatic_restart(supervisor, role, client, path, before, *, extra_restarts=1):
    def restarted():
        value = supervisor.show(role)
        if (value['ActiveState'] != 'active' or value['MainPID'] == before['MainPID']
                or int(value['NRestarts']) < int(before['NRestarts'])+extra_restarts): return False
        return value
    value = until(restarted, seconds=90)
    wait_ready(client, path, supervisor=supervisor, role=role)
    verify_policy(value)
    return value


def restart_limit_state(supervisor, starts_file, report):
    value = supervisor.show('limit')
    verify_policy(value)
    starts = json.loads(starts_file.read_text()) if starts_file.exists() else []
    report['restart_limit_observation'] = dict(properties=value, starts=starts)
    assert len(starts) <= 5, 'Restart limit allowed a sixth fixture execution'
    if (value['ActiveState'] != 'failed' or value['SubState'] != 'failed'
            or value['MainPID'] != '0' or int(value['NRestarts']) < 5):
        return False
    assert int(value['NRestarts']) == 5 and len(starts) == 5
    assert len({start['pid'] for start in starts}) == 5
    assert value['ExecMainCode'] == '1' and value['ExecMainStatus'] == '42'
    # systemd 255 retains the preceding exit-code result when can_start denies
    # a restart. Result alone therefore cannot establish the rate-limit cause.
    events = supervisor.start_limit_events('limit')
    report['restart_limit_observation']['denied_start_events'] = events
    if not events:
        return False  # Allow a journal write to finish before asserting its cause.
    return dict(properties=value, starts=starts, denied_start_events=events)


def remove_containers(settings):
    from jasmine_web.batch.docker_executor import DockerCLI
    from jasmine_web.batch.policy import fingerprint
    docker = DockerCLI()
    for marker in (Path(settings['state'])/'execution').glob('*/container.json'):
        identifier = json.loads(marker.read_text())['id']
        value = docker.inspect(identifier)
        if value is None: continue
        identity = json.loads(marker.with_name('identity.json').read_text())
        if value['Config']['Labels'].get('jasmine.batch.identity') != fingerprint(identity):
            raise ValueError('Refusing removal of an unrelated batch container')
        docker.call('container', 'rm', '-f', identifier)
        assert docker.inspect(identifier) is None
    for identifier in docker.call('container', 'ls', '-aq', '--filter', 'label=simpaths.recovery='+settings['tag']).splitlines():
        value = docker.inspect(identifier)
        if value['Config']['Labels'].get('simpaths.recovery') != settings['tag']:
            raise ValueError('Refusing removal of an unrelated interactive container')
        docker.call('container', 'rm', '-f', identifier)
        assert docker.inspect(identifier) is None


def rehearsal(args, report):
    from psycopg import sql
    from jasmine_web.batch.docker_executor import DockerCLI
    from jasmine_web.batch.local_executor import atomic_json
    from jasmine_web.batch.store import Queue
    from jasmine_web.vm_state import PostgresVMState
    from deploy.multirun.artifacts import write_attribution
    from deploy.multirun.mail_rehearsal import LocalClient
    from deploy.acceptance._recovery_fixture import bootstrap, MODEL, POOL
    from deploy.acceptance._https_fixture import create_archive
    import unittest
    args.output.mkdir(parents=True, mode=0o700); write_attribution(args.output)
    supervisor = Supervisor(uuid4().hex, args.output/'supervisor.log')
    work = Path(tempfile.mkdtemp(prefix='simpaths-recovery-'))
    state = work/'private'; state.mkdir(mode=0o700)
    single_state, inputs = state/'single', state/'inputs'
    single_state.mkdir(mode=0o700); inputs.mkdir(mode=0o700)
    dsn = os.environ['JASMINE_BATCH_TEST_DSN']
    settings, store, queue = None, None, None
    def passed(message):
        report['checks'].append(message); print('PASS: '+message, flush=True)
    try:
        report['phase'] = 'offline-regressions'
        with (args.output/'local-checks.log').open('w') as log:
            tests = unittest.TextTestRunner(stream=log, verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromName('deploy.acceptance.test_recovery_rehearsal'))
        report['local_checks'] = dict(tests=tests.testsRun, errors=len(tests.errors), failures=len(tests.failures), skipped=len(tests.skipped))
        assert tests.wasSuccessful() and not tests.skipped, 'Offline supervisor/isolation checks failed'
        report['phase'] = 'user-systemd-preflight'
        supervisor.probe()
        docker = DockerCLI()
        image = json.loads(docker.call('image', 'inspect', 'python:3.12-slim'))[0]['Id']
        settings = dict(tag=supervisor.tag, state=str(state), single_state=str(single_state), inputs=str(inputs),
            schema='test_recovery_'+uuid4().hex, batch_schema='test_recovery_'+uuid4().hex, image=image,
            single_port=free_port(), batch_port=free_port(), secret=secrets.token_urlsafe(32),
            admin=secrets.token_urlsafe(32), catalogue=str(single_state/'catalogue.json'), archive=str(single_state/'fictional.zip'))
        while settings['single_port'] == settings['batch_port']: settings['batch_port'] = free_port()
        policy_sources = {'single':args.frontend/'deploy/simpaths/simpaths-singlerun.service',
                          'batch':ROOT/'deploy/multirun/vm/simpaths-multirun.service'}
        policies = {role:restart_policy(path.read_text()) for role, path in policy_sources.items()}
        report['supervisor_templates'] = {role:dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            restart_policy=policies[role]) for role, path in policy_sources.items()}
        report['adaptations'] = ['transient user units and loopback ports; no installed/root service or host hardening proof',
            'fictional model/Java replies; real Docker containers and PostgreSQL; five-second queue leases and one-second backoff',
            'local HTTP cookies for fixture transport; deployed HTTPS/cookie policy is unchanged']
        model = deepcopy(MODEL); model['deployment']['image'] = image
        atomic_json(Path(settings['catalogue']), dict(models=[model])); create_archive(Path(settings['archive']), 1)
        dataset, owners = bootstrap(settings, dsn)
        queue = Queue(dsn, POOL, schema=settings['batch_schema'])
        store = PostgresVMState(dsn, schema=settings['schema'], shared_pool=dict(pool_id=POOL, schema=settings['batch_schema']))
        store.migrate()
        settings_file = work/'settings.json'; atomic_json(settings_file, settings)
        env = work/'fixture.env'
        environment_file(env, dict(JASMINE_BATCH_TEST_DSN=dsn, JASMINE_WEB_REPO=str(args.frontend),
            PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT)))
        common = dict(python=sys.executable, script=Path(__file__).resolve(), settings=settings_file,
                      environment=env, workdir=ROOT)
        report['phase'] = 'supervised-frontend-startup'
        start_frontends(supervisor, policies, common, args.frontend, args.output)
        batch_origin = f"http://127.0.0.1:{settings['batch_port']}"
        alice, bob = LocalClient(batch_origin), LocalClient(batch_origin)
        single_origin = f"http://127.0.0.1:{settings['single_port']}"
        interactive, other = single_client(single_origin), single_client(single_origin)
        report['phase'] = 'batch-frontend-readiness'
        wait_ready(alice, '/healthz', supervisor=supervisor, role='batch')
        report['phase'] = 'single-frontend-readiness'
        wait_ready(interactive, '/', supervisor=supervisor, role='single')
        for role in ('single', 'batch'): verify_policy(supervisor.show(role))
        passed('real transient systemd units use the templates’ restart delay, five-start limit and process-group termination policy')

        report['phase'] = 'owner-sign-in'
        sign_in(alice, state, 'alice@example.org'); sign_in(bob, state, 'bob@example.org')
        report['phase'] = 'interactive-model-startup'
        status, headers, _ = interactive.form('/launch', dict(model_key=MODEL['id'])); assert status == 303
        sid = headers['Location'].rsplit('/', 1)[-1]
        until(lambda: store.peek_session(sid).get('status') == 'ready', seconds=30)
        assert interactive.api('/build-json/'+sid, dict(seed=607, endYear=2026))['status'] == 'building'
        until(lambda: interactive.api('/status/'+sid).get('built'), seconds=30)
        assert interactive.api('/start-sim/'+sid, {})['status'] == 'started'
        record = store.peek_session(sid); single_container = record['container_id']
        report['phase'] = 'shared-running-work'
        experiment = submit(alice, dataset, 'Crash recovery fixture')
        active = until(lambda: running_attempt(queue, settings, experiment, owners['alice@example.org']), seconds=45)
        held = allocations(queue); frozen = active['specification']
        source_hash = hashlib.sha256((inputs/'input.txt').read_bytes()).hexdigest()
        assert held == dict(batch=[str(active['attempt']['id'])], single=[sid])
        queued = submit(bob, dataset, 'Other owner waiting')
        assert queue.inspect(owners['bob@example.org'], queued)['jobs'][0]['attempts'] == 0
        denied = other.form('/launch', dict(model_key=MODEL['id']))
        assert denied[0] == 303 and '?error=' in denied[1]['Location']
        assert len(store.get_all_sessions()) == 1
        assert other.request('/status/'+sid)[0] in (403,404)
        passed('real interactive and batch containers fill the shared pool; another owner cannot read the session or launch replacement work')

        report['phase'] = 'abrupt-frontend-crashes'
        cookies = (interactive.cookie, alice.cookie)
        before_single = supervisor.crash('single'); before_batch = supervisor.crash('batch')
        assert allocations(queue) == held, 'Crash released live model capacity'
        assert docker.inspect(single_container)['State']['Running'] and docker.inspect(active['container'])['State']['Running']
        report['single_restart'] = automatic_restart(supervisor, 'single', interactive, '/', before_single)
        report['batch_restart'] = automatic_restart(supervisor, 'batch', alice, '/healthz', before_batch)
        recovered = until(lambda: running_attempt(queue, settings, experiment, owners['alice@example.org']), seconds=30)
        until(lambda: attempt(queue, experiment, owners['alice@example.org'])[1]['generation'] > active['attempt']['generation'], seconds=30)
        assert recovered['container'] == active['container'] and recovered['attempt']['id'] == active['attempt']['id']
        assert recovered['attempt']['deadline'] == active['attempt']['deadline']
        assert allocations(queue) == held and recovered['specification'] == frozen
        assert recovered['job']['attempts'] == 1
        assert (interactive.cookie, alice.cookie) == cookies
        assert interactive.api('/status/'+sid)['status'] == 'running'
        assert interactive.api('/java/'+sid+'/simulation/parameters')['seed'] == 607
        current_single = store.peek_session(sid)
        assert all(current_single[key] == record[key] for key in ('container_id','backend_secret','reset_secret'))
        assert interactive.request('/charts/'+sid)[0] == 200
        assert alice.api('/api/session')['signed_in']
        assert bob.request('/api/experiments/'+experiment)[0] in (403,404)
        assert queue.inspect(owners['bob@example.org'], queued)['jobs'][0]['attempts'] == 0
        passed('automatic restart after SIGKILL preserves owner cookies, controls, parameters and both reservations; the worker adopts the same container and attempt without spending another retry')

        report['phase'] = 'fatal-dispatcher-recovery'
        (state/'fail-dispatcher-once').write_text('Fictional one-shot failure\n')
        before = supervisor.crash('batch')
        report['dispatcher_restart'] = automatic_restart(supervisor, 'batch', alice, '/healthz', before, extra_restarts=2)
        assert not (state/'fail-dispatcher-once').exists()
        recovered = until(lambda: running_attempt(queue, settings, experiment, owners['alice@example.org']), seconds=30)
        assert recovered['container'] == active['container'] and recovered['job']['attempts'] == 1
        assert recovered['specification'] == frozen and allocations(queue) == held
        passed('a fatal dispatcher startup requests process termination and systemd recovers automatically; the surviving model is adopted once')

        report['phase'] = 'failed-attempt-retry'
        uncertain = state/'unconfirmed-docker'; uncertain.write_text('Fictional inspection outage\n')
        until(lambda: (state/'unconfirmed-observed.json').exists(), seconds=15)
        docker.call('container', 'kill', active['container'])
        assert not docker.inspect(active['container'])['State']['Running']
        time.sleep(3)
        assert allocations(queue) == held, 'Unconfirmed termination released capacity'
        assert queue.inspect(owners['alice@example.org'], experiment)['jobs'][0]['attempts'] == 1
        assert queue.inspect(owners['bob@example.org'], queued)['jobs'][0]['attempts'] == 0
        passed('unavailable Docker inspection retains the stopped attempt’s reservation and prevents replacement execution until termination is confirmed')
        queue.cancel(owners['bob@example.org'], queue.inspect(owners['bob@example.org'], queued)['jobs'][0]['id'])
        uncertain.unlink()
        until(lambda: queue.inspect(owners['alice@example.org'], experiment)['jobs'][0]['state'] == 'review', seconds=30)
        failed_job, failed_attempt, retained = attempt(queue, experiment, owners['alice@example.org'])
        assert retained == frozen and failed_job['attempts'] == 1
        assert str(failed_attempt['id']) not in allocations(queue)['batch'] and allocations(queue)['single'] == [sid]
        spent = failed_job['spent_seconds']; assert spent > 0
        queue.retry(owners['alice@example.org'], failed_job['id'])
        retry = until(lambda: running_attempt(queue, settings, experiment, owners['alice@example.org'], previous=str(active['attempt']['id'])), seconds=30)
        assert retry['container'] != active['container'] and retry['job']['attempts'] == 2
        assert retry['specification'] == frozen and retry['job']['spent_seconds'] >= spent
        assert hashlib.sha256((inputs/'input.txt').read_bytes()).hexdigest() == source_hash
        assert (retry['path']/'work/input-sha256.txt').read_text() == source_hash
        report['retry'] = dict(attempts=2, previous_elapsed_seconds=spent, max_attempts=frozen['policy']['max_attempts'],
                              total_seconds=frozen['policy']['total_seconds'])
        passed('confirmed model failure releases only its capacity; an authorised retry keeps the original inputs, seeds, runtime policy and already-spent budget')

        report['phase'] = 'selective-release-and-completion'
        assert interactive.form('/leave/'+sid)[0] in (302,303)
        assert docker.inspect(single_container) is None
        assert allocations(queue) == dict(batch=[str(retry['attempt']['id'])], single=[])
        (retry['path']/'work/finish').write_text('finish\n')
        until(lambda: queue.inspect(owners['alice@example.org'], experiment)['jobs'][0]['state'] == 'succeeded', seconds=30)
        done, _, spec = attempt(queue, experiment, owners['alice@example.org'])
        assert done['attempts'] == 2 and done['spent_seconds'] >= spent and spec == frozen
        assert allocations(queue) == dict(batch=[],single=[])
        assert (retry['path']/'work/starts.txt').read_text() == 'started\n'
        values = json.loads((retry['path']/'work/results.json').read_text()); assert [x['seed'] for x in values] == ['606','607']
        passed('Leave removes only the interactive container; the retry finishes both original seeds once and releases the remaining reservation after confirmed termination')

        report['phase'] = 'restart-rate-limit'
        supervisor.start('limit', policies['batch'], **common, log=args.output/'limit.log')
        limited = until(lambda: restart_limit_state(supervisor, state/'failed-starts.json', report), seconds=90)
        time.sleep(12)
        stable = restart_limit_state(supervisor, state/'failed-starts.json', report)
        assert stable and stable['starts'] == limited['starts']
        report['restart_limit'] = dict(starts=len(stable['starts']), result=stable['properties']['Result'],
            n_restarts=int(stable['properties']['NRestarts']), rate_limit_reached=True,
            denied_start_events=stable['denied_start_events'])
        passed('five repeated failed starts trigger the configured systemd rate limit; no sixth process starts after another restart interval')
        report['passed'] = True
    finally:
        errors = []
        try: supervisor.close()
        except Exception as error: errors.append(type(error).__name__)
        if not errors and settings:
            try: remove_containers(settings)
            except Exception as error: errors.append(type(error).__name__)
        if store:
            try: store.close()
            except Exception as error: errors.append(type(error).__name__)
        if not errors and settings:
            try:
                import psycopg
                with psycopg.connect(dsn) as connection:
                    for key in ('schema', 'batch_schema'):
                        connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(settings[key])))
            except Exception as error: errors.append(type(error).__name__)
        if not errors: shutil.rmtree(work)
        report['cleanup'] = not errors
        if errors:
            report['cleanup_errors'], report['retained_fixture'] = errors, str(work)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=frontend_path())
    parser.add_argument('--output', type=Path)
    parser.add_argument('--execute-proof', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--serve-fixture', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--role', choices=('single','batch','limit'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv); os.umask(0o077)
    args.frontend = args.frontend.expanduser().resolve(strict=True)
    os.environ['JASMINE_WEB_REPO'] = str(args.frontend)
    sys.path[:0] = [str(args.frontend), str(args.frontend/'tests/batch')]
    if args.serve_fixture:
        from deploy.acceptance._recovery_fixture import serve_batch, serve_single, serve_limit
        settings = json.loads(args.serve_fixture.read_text())
        {'batch':serve_batch, 'single':serve_single, 'limit':serve_limit}[args.role](settings, os.environ['JASMINE_BATCH_TEST_DSN'])
        return 0
    if not args.execute_proof:
        evidence = args.output or Path.home()/'simpaths-benchmarks'/('service-recovery-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        return subprocess.run([sys.executable, str(args.frontend/'scripts/test_batch_queue.py'), '--proof-only',
            '--proof-script', str(Path(__file__).resolve()), '--proof-requirements',
            str(ROOT/'deploy/multirun/requirements.txt'), str(args.frontend/'requirements-vm.txt'),
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
        if isinstance(error, (AssertionError, RuntimeError)): report['message'] = str(error)[:500]
        print('STOPPED: '+type(error).__name__+' during '+report['phase']+'; inspect the private logs.', file=sys.stderr)
    finally:
        if args.output.exists():
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            (args.output/'report.json').write_text(json.dumps(report, indent=2, default=str)+'\n')
    print(('PASSED' if report['passed'] and report['cleanup'] else 'FAILED')+': '+str(args.output/'report.json'))
    return 0 if report['passed'] and report['cleanup'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
