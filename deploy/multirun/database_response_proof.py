#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Check PostgreSQL polling and worker leases during large fictional CSV validation.
Uses real protected HTTP routes and the production validator, with no model run,
real accounts, emails or changes to service defaults. All evidence stays private.
@author ross richardson
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
from threading import Event, Lock
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._workflow import frontend_path
from deploy.multirun.artifacts import write_attribution, write_json


def describe(values):
    if not values:
        return dict(samples=0)
    ordered = sorted(values)
    return dict(samples=len(values), median_ms=round(statistics.median(values)*1000, 2),
        p95_ms=round(ordered[min(len(ordered)-1, int(len(ordered)*.95))]*1000, 2),
        max_ms=round(max(values)*1000, 2))


def execute(output):
    sys.path[:0] = [str(frontend_path()), str(frontend_path()/'tests/batch')]
    import psycopg
    from psycopg import pq
    from browser_fixture import BrowserModel
    from test_browser import BrowserTests
    from jasmine_web.batch.policy import Policy
    from jasmine_web.batch.worker import Worker
    from deploy.multirun.compare_native import proof_configuration
    from deploy.multirun.configuration import normalise
    from deploy.multirun.queue_adapter import OPTIONS_NOT_EXPORTED, validate_outputs

    class WorkloadModel(BrowserModel):
        """Only the fictional test descriptor changes to match the CSV settings."""
        def browser_form(self):
            form = super().browser_form()
            next(f for f in form['common'] if f['id']=='population')['max'] = 50000
            return form

        def describe_dataset(self, resolved):
            description = super().describe_dataset(resolved)
            description['values'].update(population=50000, end_year=2026)
            return description

    output.mkdir(mode=0o700, parents=True)
    write_attribution(output)
    report = dict(passed=False, cleanup=False, fictional_workload=True,
        defaults_changed=False, lease_seconds=5, polling_clients=4,
        psycopg=psycopg.__version__, pq_impl=pq.__impl__, libpq=pq.version(), phases=[])
    fixture = BrowserTests()
    try:
        fixture.setUp()
        q = fixture.fixture.q
        dataset = fixture.ready()
        fixture.s.model = WorkloadModel(fixture.fixture.root)
        fixture.fixture.a.approve_email('bob@example.org')
        foreign = fixture.browser('bob@example.org')
        policy = asdict(Policy(attempt_seconds=300, total_seconds=600,
            lease_seconds=5, retry_delay_seconds=1))
        fixture.fixture.fixture.sql('UPDATE pools SET policy=%s::jsonb WHERE id=%s',
            (json.dumps(policy), q.pool_id))
        fixture.form.update(repetitions=1, run_sets=fixture.form['run_sets'][:1])
        fixture.form['common'].update(population=50000, end_year=2026)
        reviewed = fixture.client.post('/api/review-experiment',
            json=dict(dataset=dataset, form=fixture.form))
        fixture.assertEqual(reviewed.status_code, 200)
        response = fixture.client.post('/api/submit',
            json=dict(key='validation-response-proof', review=reviewed.json()['review']))
        fixture.assertEqual(response.status_code, 201)
        experiment = response.json()['id']
        route = '/api/experiments/'+experiment
        frozen = q.inspect(fixture.fixture.owner, experiment)['specification']
        data = proof_configuration().editable_configuration()
        data['common'].update(fixture.form['common'])
        data['seed_plan'] = dict(mode='standard', repetitions=1)
        data.pop('sweep', None)
        data['run_sets'] = fixture.form['run_sets']
        config = normalise(data)
        native = config.native_configuration('first')
        root = fixture.fixture.root/'fictional-output'
        inputs = root/'run/input'
        inputs.mkdir(parents=True)
        fields = dict(country='UK', startYear=2019, endYear=2026, popSize=50000,
            **native['model_args'], randomSeedIfFixed='606')
        fields = {k:v for k,v in fields.items() if k not in OPTIONS_NOT_EXPORTED}
        (inputs/'options.txt').write_text('\n'.join(
            f'{k}: {str(v).lower() if isinstance(v, bool) else v}' for k,v in fields.items()))
        csv_dir = root/'run/csv'
        csv_dir.mkdir()
        for name in ('Person.csv', 'BenefitUnit.csv'):
            with (csv_dir/name).open('w') as stream:
                stream.write('run,time,'+','.join('value'+str(i) for i in range(32))+'\n')
                for year in range(2019, 2027):
                    row = f'fictional,{year},'+','.join(['123.456789']*32)+'\n'
                    for _ in range(25):
                        stream.write(row*2000)
        report['csv_bytes'] = sum(p.stat().st_size for p in csv_dir.iterdir())
        print(f"Fictional CSV workload: {report['csv_bytes']/1024**2:.0f} MiB", flush=True)
        lease = q.claim('validation-response-proof')
        fixture.assertIsNone(q.started(lease))
        worker = Worker(q, SimpleNamespace(), SimpleNamespace(), lease.worker)
        worker.leases[lease.attempt_id] = lease
        connect = psycopg.connect
        mutex, phase = Lock(), None

        def measured_connect(*args, **kwargs):
            started = time.monotonic()
            try:
                return connect(*args, **kwargs)
            except Exception as error:
                with mutex:
                    phase['connection_errors'][type(error).__name__] += 1
                raise
            finally:
                with mutex:
                    phase['connect_seconds'].append(time.monotonic()-started)

        def phase_run(name, work):
            nonlocal phase
            phase = dict(name=name, connect_seconds=[], connection_errors=Counter(),
                http_seconds=[], http_statuses=Counter(), http_errors=Counter(),
                scheduler_seconds=[], heartbeat_errors=[], heartbeat_seconds=[],
                started_at=datetime.now(timezone.utc).isoformat())
            stop = Event()

            def scheduler():
                last = time.monotonic()
                while not stop.wait(.02):
                    now = time.monotonic()
                    with mutex:
                        phase['scheduler_seconds'].append(now-last)
                    last = now

            def requests(client, expected):
                while not stop.is_set():
                    started = time.monotonic()
                    try:
                        result = client.get(route)
                        with mutex:
                            phase['http_seconds'].append(time.monotonic()-started)
                            phase['http_statuses'][str(result.status_code)] += 1
                            if result.status_code != expected:
                                phase['http_errors']['wrong_status'] += 1
                    except Exception as error:
                        with mutex:
                            phase['http_errors'][type(error).__name__] += 1
                    stop.wait(.1)

            heartbeat = q.heartbeat

            def measured_heartbeat(current):
                started = time.monotonic()
                try:
                    reason = heartbeat(current)
                    fixture.assertIsNone(reason)
                    return reason
                finally:
                    with mutex:
                        phase['heartbeat_seconds'].append(time.monotonic()-started)

            started = time.monotonic()
            print('Phase: '+name, flush=True)
            failure = None
            with patch('psycopg.connect', side_effect=measured_connect), \
                    patch.object(q, 'heartbeat', side_effect=measured_heartbeat), \
                    ThreadPoolExecutor(max_workers=5) as threads:
                futures = [threads.submit(scheduler)]
                for client, expected in [(fixture.client, 200)]*3+[(foreign, 403)]:
                    futures.append(threads.submit(requests, client, expected))
                try:
                    with worker._output_heartbeats():
                        work()
                except Exception as error:
                    phase['heartbeat_errors'].append(type(error).__name__)
                    failure = error
                finally:
                    stop.set()
                for future in futures:
                    future.result()
            result = dict(name=name, started_at=phase['started_at'],
                seconds=round(time.monotonic()-started, 2),
                connect=describe(phase['connect_seconds']), http=describe(phase['http_seconds']),
                scheduler=describe(phase['scheduler_seconds']),
                connection_errors=dict(phase['connection_errors']),
                http_statuses=dict(phase['http_statuses']), http_errors=dict(phase['http_errors']),
                heartbeat_errors=phase['heartbeat_errors'], heartbeat=describe(phase['heartbeat_seconds']))
            report['phases'].append(result)
            print(json.dumps(result), flush=True)
            if failure:
                raise failure
            fixture.assertFalse(any(result[k] for k in
                ('connection_errors', 'http_errors', 'heartbeat_errors')))
            fixture.assertTrue(all(result[k]['samples'] for k in
                ('connect', 'http', 'scheduler', 'heartbeat')))
            fixture.assertEqual(set(result['http_statuses']), {'200', '403'})

        phase_run('idle-polling', lambda:time.sleep(10))
        receipts = []

        def validation():
            for _ in range(3):
                value = validate_outputs(root, config, 'first')
                if receipts:
                    fixture.assertEqual(value, receipts)
                receipts[:] = value

        phase_run('csv-verification', validation)
        phase_run('after-validation', lambda:time.sleep(10))
        fixture.assertEqual([r['seed'] for r in receipts], frozen['seeds'])
        for ordinal, receipt in enumerate(receipts):
            q.record_repetition(lease, ordinal, receipt['seed'], receipt['fingerprint'])
        fixture.assertEqual(q.finish(lease, outcome='success', stop_evidence='f'*64), 'succeeded')
        saved = q.inspect(fixture.fixture.owner, experiment)
        fixture.assertEqual(saved['specification'], frozen)
        fixture.assertEqual((saved['jobs'][0]['state'], saved['jobs'][0]['attempts']), ('succeeded', 1))
        fixture.assertEqual([h['id'] for h in saved['jobs'][0]['history']], [lease.attempt_id])
        fixture.assertEqual(fixture.fixture.fixture.sql(
            'SELECT policy FROM experiments WHERE id=%s', (experiment,))[0]['policy'], policy)
        fixture.assertEqual(fixture.fixture.fixture.sql(
            'SELECT generation FROM attempts WHERE id=%s', (lease.attempt_id,))[0]['generation'], lease.generation)
        fixture.assertEqual(q.occupancy()['attempts'], 0)
        report.update(passed=True, original_seed_verified=True, attempts=1,
            frozen_configuration_preserved=True, validation_passes=3, all_phases_error_free=True)
        print('PASS: large output validation preserves polling, owner denials and the original five-second lease', flush=True)
    except BaseException as error:
        report['failure'] = dict(type=type(error).__name__)
        raise
    finally:
        cleaned = fixture.doCleanups()
        root = getattr(getattr(fixture, 'fixture', None), 'root', None)
        report['cleanup'] = cleaned and (root is None or not root.exists())
        report['passed'] = report['passed'] and report['cleanup']
        write_json(output/'report.json', report)
    if not report['cleanup']:
        raise AssertionError('Validation proof cleanup is unconfirmed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=frontend_path())
    parser.add_argument('--output', type=Path)
    parser.add_argument('--execute-proof', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.execute_proof:
        if not os.environ.get('JASMINE_BATCH_TEST_DSN') or args.output is None:
            parser.error('Use the disposable PostgreSQL driver')
        execute(args.output)
        return 0
    output = args.output or Path.home()/'simpaths-benchmarks'/(
        'database-validation-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    if output.exists():
        parser.error('Choose a new evidence directory')
    frontend = args.frontend.expanduser().resolve(strict=True)
    command = [sys.executable, str(frontend/'scripts/test_batch_queue.py'), '--proof-only',
        '--proof-script', str(Path(__file__).resolve()), '--proof-requirements',
        str(Path(__file__).with_name('requirements.txt')), '--output', str(output.resolve())]
    return subprocess.call(command, env=dict(os.environ, JASMINE_WEB_REPO=str(frontend)))


if __name__=='__main__':
    raise SystemExit(main())
