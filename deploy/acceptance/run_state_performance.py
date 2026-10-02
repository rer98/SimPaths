#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Repeated Redis/PostgreSQL SingleRun performance measurements with a real 50k model.
Uses disposable state services, a private frontend copy and one model at a time.
Keeps full-speed execution and completed-chart sampling separate in its evidence.
@author ross richardson
"""
import argparse
import asyncio
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import runpy
import secrets
import shutil
import signal
import statistics
import subprocess
import sys
import time
import uuid
from urllib.parse import urlencode, urlsplit

load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
common = load_tool('acceptance/run_two_session_acceptance.py')
workflow = load_tool('_workflow.py')
browser_tools = load_tool('acceptance/run_browser_acceptance.py')
GIB = 1024**3
POLL_MS = 500


class PerformanceCheckFailed(AssertionError):
    """A controlled harness message that contains no credentials or payloads."""


def require(value, message):
    if not value:
        raise PerformanceCheckFailed(message)


def order(pairs):
    return [(pair+1, backend) for pair in range(pairs)
            for backend in (('redis', 'postgres') if pair % 2 == 0 else ('postgres', 'redis'))]


def resource_check(resources):
    require(resources['available_memory_bytes'] >= 7*GIB,
                   'Need 7 GiB available RAM for one 5 GiB model, state service and test headroom.')
    require(resources['root_free_bytes'] >= 8*GIB,
                   'Need 8 GiB free on root before a full 50k trial. Free space before retrying.')


def simulation_completed(status, end_year, highest_running_time=0):
    require(status.get('status') in ('running', 'paused'), 'Simulation returned an unexpected execution state')
    simulation_time = float(status.get('time', 0))
    require(math.isfinite(simulation_time), 'Simulation returned an invalid time')
    # End runs after final-year cleanup, then SimulationEngine.end() clears the
    # queue and resets its clock to zero. Require observed final-year execution
    # before accepting that reset; a manual pause keeps the year's clock.
    return status['status'] == 'paused' and status.get('built') is True and (
        simulation_time >= end_year+1 or
        simulation_time == 0 and highest_running_time >= end_year)


async def run_to_completion(request, sid, *, end_year, timeout):
    began = time.monotonic()
    highest_running_time = 0
    started = await request('POST', '/start-sim/'+sid)
    # The command acknowledges "started"; status polls report the engine's
    # separate "running"/"paused" state. Completion still requires final cleanup.
    require(started.get('status') == 'started', 'Simulation Start was not acknowledged')
    while True:
        status = await request('GET', '/status/'+sid)
        elapsed = time.monotonic()-began
        require(elapsed < timeout, 'Full simulation exceeded its test time limit')
        if simulation_completed(status, end_year, highest_running_time):
            return status, elapsed, highest_running_time
        if status['status'] == 'running':
            highest_running_time = max(highest_running_time, float(status['time']))
        require(status['status'] == 'running' or elapsed < 10,
                'Simulation stopped before completing its final year')
        await asyncio.sleep(.5)


def summary_evidence(raw, *, start_year, end_year):
    try:
        records = list(csv.reader(io.StringIO(raw.decode('utf-8-sig')), strict=True))
    except (UnicodeDecodeError, csv.Error):
        raise PerformanceCheckFailed('Simulation summary CSV could not be read') from None
    require(records and records[0].count('time') == 1, 'Simulation summary has no unique time column')
    require(all(len(row) == len(records[0]) for row in records[1:]),
            'Simulation summary has malformed rows')
    column = records[0].index('time')
    try:
        years = sorted(float(row[column]) for row in records[1:])
    except ValueError:
        raise PerformanceCheckFailed('Simulation summary has invalid years') from None
    expected = list(range(start_year, end_year+1))
    require(years == expected, 'Simulation summary does not contain exactly one row for every requested year')
    return dict(summary_output_sha256=hashlib.sha256(raw).hexdigest(),
                summary_output_rows=len(years), summary_output_years=expected)


def summarize(rows, field='request_ms'):
    values = sorted(row[field] for row in rows if row.get(field) is not None)
    require(all(math.isfinite(x) and x >= 0 for x in values), 'Invalid timing sample')
    result = dict(requests=len(rows), measured=len(values),
                  failures=sum(bool(row.get('failure') or row.get('body_error')
                                    or row.get('http_status', 200) >= 400) for row in rows))
    if values:
        result.update(mean_ms=round(statistics.mean(values), 3),
                      median_ms=round(statistics.median(values), 3),
                      p95_ms=round(values[math.ceil(.95*len(values))-1], 3),
                      p99_ms=round(values[math.ceil(.99*len(values))-1], 3),
                      max_ms=round(values[-1], 3),
                      over_500_ms=sum(x > POLL_MS for x in values),
                      over_500_percent=round(100*sum(x > POLL_MS for x in values)/len(values), 3))
    return result


def grouped(rows):
    groups = {}
    for phase in sorted({row['phase'] for row in rows} - {'setup', 'chart-warmup', 'closing'}):
        groups[phase] = {}
        for endpoint in ('status', 'charts', 'logs'):
            selected = [row for row in rows if row['phase'] == phase and row['endpoint'] == endpoint]
            if not selected:
                continue
            value = summarize(selected)
            for name in ('total_ms', 'state_ms', 'java_ms', 'other_ms', 'dispatch_ms'):
                value[name] = summarize(selected, name)
            value['java_calls'] = sum(row.get('java_calls', 0) for row in selected)
            value['state_calls'] = sum(row.get('state_calls', 0) for row in selected)
            value['responses_without_java_call'] = sum(row.get('java_calls') == 0 for row in selected)
            groups[phase][endpoint] = value
    return groups


def join_timings(rows, path):
    timings = {row['sequence']: row for row in map(json.loads, path.read_text().splitlines())}
    for row in rows:
        if row.get('request_ms') is None:
            continue
        require(row.get('trace_id') in timings, 'Browser response is missing its server timing record')
        server = timings[row['trace_id']]
        require(server['endpoint'] == row['endpoint'], 'Timing record does not match the browser route')
        for key in ('total_ms', 'state_ms', 'state_calls', 'java_ms', 'java_calls',
                    'other_ms', 'dispatch_ms', 'dispatch_calls'):
            row[key] = server[key]


class BrowserMeasurements:
    def __init__(self):
        self.phase = 'setup'
        self.rows = []
        self.pending = {}
        self.bodies = set()

    def request(self, request):
        parts = urlsplit(request.url).path.split('/')
        if len(parts) == 3 and parts[1] in ('status', 'charts', 'logs'):
            row = dict(endpoint=parts[1], phase=self.phase, http_status=0,
                       request_ms=None, body_error=False, trace_id=None)
            self.pending[request] = row
            self.rows.append(row)

    def response(self, response):
        row = self.pending.get(response.request)
        if row is None:
            return
        row['http_status'] = response.status
        trace = response.headers.get('x-simpaths-performance-id')
        if trace and trace.isdigit():
            row['trace_id'] = int(trace)

        async def check_body():
            try:
                payload = await response.json()
                row['body_error'] = not isinstance(payload, dict) or bool(payload.get('error'))
                if row['endpoint'] == 'charts' and not row['body_error']:
                    row['body_error'] = not isinstance(payload.get('charts'), list)
            except Exception:
                row['body_error'] = True

        task = asyncio.create_task(check_body())
        self.bodies.add(task)
        task.add_done_callback(self.bodies.discard)

    def finished(self, request):
        row = self.pending.pop(request, None)
        if row is not None:
            elapsed = request.timing['responseEnd']
            if math.isfinite(elapsed) and elapsed >= 0:
                row['request_ms'] = elapsed
            else:
                row['failure'] = 'missing_browser_timing'

    def failed(self, request):
        row = self.pending.pop(request, None)
        if row is not None:
            row['failure'] = 'network_failure'

    def chart_count(self, phase):
        return sum(row['endpoint'] == 'charts' and row['phase'] == phase
                   and row['request_ms'] is not None for row in self.rows)

    async def drain(self):
        if self.bodies:
            await asyncio.gather(*tuple(self.bodies))

    async def settle(self):
        self.phase = 'closing'
        deadline = time.monotonic()+15
        while any(row['phase'] != 'closing' for row in self.pending.values()):
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(.1)
        await self.drain()


class Trial:
    """Owns only its disposable Docker labels, processes and credential file."""
    def __init__(self, options, pair, backend, image_id, state_image, entry, out):
        self.options, self.backend, self.image_id, self.entry = options, backend, image_id, entry
        self.state_image, self.out = state_image, out
        self.model = 'simpaths-acceptance-perf-'+secrets.token_hex(6)
        self.owner = None
        self.client = self.process = self.state_container = self.redis = self.sampler = None
        self.dsn_file = self.log = self.model_container = None
        self.rows = []
        self.cycles = []
        self.report = dict(status='running', pair=pair, state_backend=backend, image_id=image_id,
                           state_image_id=state_image, checks=[], page_errors=[], model_id=self.model)

    def save(self):
        (self.out/'report.json').write_text(json.dumps(self.report, indent=2)+'\n')

    def capture_model_log(self):
        container = self.model_container
        container.reload()
        require(common.is_owned(container.labels, self.model, self.owner), 'Model log ownership mismatch')
        logs = container.logs()
        fd = os.open(self.out/'java.log', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(logs)
        return logs

    def __enter__(self):
        self.out.mkdir(mode=0o700)
        try:
            self.setup()
        except BaseException:
            self.report['status'] = 'failed'
            self.__exit__(*sys.exc_info())
            raise
        return self

    def setup(self):
        import docker
        import httpx
        import redis
        resource_check(common.host_resources())
        self.report['host_before'] = common.host_resources()
        self.client = docker.DockerClient(base_url='unix:///var/run/docker.sock', timeout=15)
        self.client.ping()
        work = self.out/'frontend'
        frontend = self.options.redis_frontend if self.backend == 'redis' else self.options.frontend
        common.copy_frontend(frontend, work)
        shim = Path(__file__).with_name('_state_performance_frontend.py')
        shutil.copy2(shim, work/'performance_frontend.py')
        sources = sorted(p for p in work.rglob('*') if p.is_file())
        digest = hashlib.sha256()
        for path in sources:
            digest.update(path.relative_to(work).as_posix().encode()+b'\0'+path.read_bytes())
        self.report['frontend_snapshot_sha256'] = digest.hexdigest()
        selected = json.loads(json.dumps(self.entry))
        selected.update(id=self.model, name='Isolated 50k SingleRun performance', requiresAuth=False)
        selected['deployment']['image'] = self.image_id
        (work/'models.json').write_text(json.dumps(dict(models=[selected])))
        state_port, web_port = browser_tools.free_port(), browser_tools.free_port()
        shared = dict(detach=True, labels={'simpaths.acceptance': self.model},
                      mem_limit='768m', nano_cpus=1_000_000_000, pids_limit=128)
        if self.backend == 'redis':
            self.state_container = self.client.containers.run(self.state_image, **shared,
                ports={'6379/tcp': ('127.0.0.1', state_port)},
                command=['redis-server', '--save', '', '--appendonly', 'no'])
            self.redis = redis.Redis(host='127.0.0.1', port=state_port, decode_responses=True,
                                     socket_timeout=3, socket_connect_timeout=3)
            deadline = time.monotonic()+30
            while True:
                try:
                    if self.redis.ping():
                        break
                except redis.RedisError:
                    pass
                require(time.monotonic() < deadline, 'Disposable Redis did not become ready')
                time.sleep(.25)
            self.owner = str(uuid.uuid4())
            require(self.redis.set('jasmine:deployment-id', self.owner, nx=True), 'State store was not empty')
            env = {k:v for k,v in os.environ.items() if not k.startswith(('REDIS_', 'JASMINE_', 'VM_', 'ADMIN_', 'SESSION_', 'HARD_RESET_'))}
            env.update(DEPLOY_MODE='vm', VM_STATE_BACKEND='redis', VM_SECURITY_MODE='development',
                VM_MAX_SESSIONS='1', VM_SESSION_MEMORY_BUDGET_MIB='8192', VM_MAX_SESSIONS_PER_CLIENT='1',
                REDIS_URL=f'redis://127.0.0.1:{state_port}/0', COOKIE_SECURE='false',
                ADMIN_PASSWORD=secrets.token_urlsafe(32), SESSION_SECRET=secrets.token_urlsafe(32),
                HARD_RESET_SECRET=secrets.token_urlsafe(32), PYTHON_DOTENV_DISABLED='1', PYTHONUNBUFFERED='1')
        else:
            password = secrets.token_hex(32)
            self.state_container = self.client.containers.run(self.state_image, **shared,
                ports={'5432/tcp': ('127.0.0.1', state_port)},
                environment=dict(POSTGRES_PASSWORD=password, POSTGRES_DB='jasmine_proof'),
                tmpfs={'/var/lib/postgresql/data': 'rw,size=512m'},
                command=['postgres', '-c', 'shared_buffers=32MB', '-c', 'max_wal_size=64MB',
                         '-c', 'min_wal_size=32MB', '-c', 'checkpoint_timeout=1min'])
            deadline = time.monotonic()+30
            while self.state_container.exec_run(['pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'jasmine_proof']).exit_code:
                require(time.monotonic() < deadline, 'Disposable PostgreSQL did not become ready')
                time.sleep(.25)
            self.dsn_file = work/'.postgres-proof.dsn'
            fd = os.open(self.dsn_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(f'postgresql://postgres:{password}@127.0.0.1:{state_port}/jasmine_proof')
            sys.path.insert(0, str(self.options.frontend))
            try:
                from jasmine_web.vm_state import PostgresVMState
                state = PostgresVMState(self.dsn_file.read_text())
                try:
                    state.migrate()
                    self.owner = state.deployment_id()
                finally:
                    state.close()
            finally:
                sys.path.pop(0)
            env = common.isolated_environment(state_backend='postgres', postgres_dsn_file=self.dsn_file)
        env.update(VM_MAX_SESSIONS='1', VM_MAX_SESSIONS_PER_CLIENT='1',
                   VM_SESSION_MEMORY_BUDGET_MIB='5120', SIMPATHS_PERFORMANCE_LOG=str(self.out/'server-timings.jsonl'))
        self.report['deployment_id'] = self.owner
        self.log = (self.out/'frontend.log').open('w')
        self.process = subprocess.Popen([sys.executable, '-m', 'uvicorn',
            'performance_frontend:create_app', '--factory', '--host', '127.0.0.1',
            '--port', str(web_port), '--workers', '1', '--no-access-log'], cwd=work, env=env,
            stdout=self.log, stderr=subprocess.STDOUT)
        self.base = f'http://127.0.0.1:{web_port}'
        deadline = time.monotonic()+60
        with httpx.Client(trust_env=False, timeout=3) as http:
            while True:
                require(self.process.poll() is None, 'Test frontend exited; inspect its private log')
                try:
                    if http.get(self.base+'/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                require(time.monotonic() < deadline, 'Test frontend did not become ready')
                time.sleep(.5)
        self.sampler = common.Sampler(self.client, self.model, self.out/'resources.jsonl')
        self.sampler.start()

    def __exit__(self, kind, error, traceback):
        cleanup_errors = []
        if kind:
            self.report.update(status='failed', error=kind.__name__)
            if isinstance(error, PerformanceCheckFailed):
                self.report['error_detail'] = str(error)
        if self.sampler:
            try:
                self.sampler.close()
                self.report['resource_summary'] = common.summarize_samples(self.out/'resources.jsonl')
                self.report['resource_summary']['note'] = (
                    'One model per trial; memory includes cache. Host samples also reflect unrelated activity.')
                self.report['resource_sampling_errors'] = self.sampler.errors
                require(not self.sampler.errors, 'Resource sampling failed')
            except Exception as exc:
                cleanup_errors.append('resource_sample_'+type(exc).__name__)
        # Save diagnostics before frontend/model cleanup, including failed
        # execution trials that never reached the normal final log check.
        if self.model_container is not None and not (self.out/'java.log').exists():
            try:
                self.capture_model_log()
            except Exception as exc:
                cleanup_errors.append('model_log_'+type(exc).__name__)
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if self.client:
            try:
                common.cleanup_models(self.client, self.model, self.owner)
            except Exception as exc:
                cleanup_errors.append('model_cleanup_'+type(exc).__name__)
        if self.state_container:
            try:
                self.state_container.reload()
                require(self.state_container.labels.get('simpaths.acceptance') == self.model,
                               'State cleanup ownership mismatch')
                self.state_container.remove(force=True, v=True)
            except Exception as exc:
                cleanup_errors.append('state_cleanup_'+type(exc).__name__)
        if self.dsn_file:
            try:
                self.dsn_file.unlink(missing_ok=True)
            except Exception as exc:
                cleanup_errors.append('credential_cleanup_'+type(exc).__name__)
        if self.rows:
            try:
                join_timings(self.rows, self.out/'server-timings.jsonl')
                (self.out/'browser-timings.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in self.rows))
                self.report['response_times_by_phase'] = grouped(self.rows)
            except Exception as exc:
                cleanup_errors.append('timing_join_'+type(exc).__name__)
        if self.cycles:
            (self.out/'chart-update-cycles.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in self.cycles))
        if self.log:
            self.log.close()
        if self.redis:
            self.redis.close()
        if self.client:
            self.client.close()
        self.report['host_after'] = common.host_resources()
        self.report['cleanup_errors'] = cleanup_errors
        if cleanup_errors:
            self.report['status'] = 'failed'
        self.save()
        if cleanup_errors and kind is None:
            raise RuntimeError('Trial cleanup or timing validation failed; inspect the report')


async def exercise(trial, target):
    from playwright.async_api import async_playwright, expect
    measurements = BrowserMeasurements()
    trial.rows = measurements.rows
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1200, 'height': 800},
                                           extra_http_headers={'Origin': trial.base})
        page = await context.new_page()
        page.on('request', measurements.request)
        page.on('response', measurements.response)
        page.on('requestfinished', measurements.finished)
        page.on('requestfailed', measurements.failed)
        page.on('pageerror', lambda error: trial.report['page_errors'].append(type(error).__name__))
        try:
            await page.goto(trial.base)
            await page.locator('.model-card').click()
            await page.wait_for_url(lambda url: urlsplit(str(url)).path.startswith('/sim/'), timeout=180000)
            sid = urlsplit(page.url).path.split('/')[-1]
            await expect(page.locator('#param-form')).to_be_visible(timeout=180000)
            models = trial.client.containers.list(filters={'label': 'jasmine.model_id='+trial.model})
            require(len(models) == 1, 'Expected one isolated model')
            container = models[0]
            trial.model_container = container
            container.reload()
            host = container.attrs['HostConfig']
            require(host['Memory'] == 5*GIB and host['NanoCpus'] == 2_000_000_000,
                           'Model allocation differs from reviewed 50k limits')
            await asyncio.to_thread(browser_tools.assert_private_network, container, trial.client)

            async def request(method, path, data=None):
                require(not trial.sampler.low_disk, 'Free root storage fell below 2 GiB')
                response = await context.request.fetch(trial.base+path, method=method, data=data,
                                                       timeout=330000, max_redirects=0)
                require(response.ok, 'Control request failed: HTTP '+str(response.status))
                value = await response.json()
                require(isinstance(value, dict) and not value.get('error'), 'Control returned an error')
                if path == '/status/'+sid:
                    trial.report['last_simulation_status'] = {key: value.get(key) for key in ('status', 'time', 'built')}
                return value

            storage = await request('GET', '/storage/'+sid)
            logs = await asyncio.to_thread(container.logs)
            browser_tools.verify_deployment_settings(50000, container.attrs, logs.decode(errors='replace'), storage)
            await page.locator('[name="endYear"]').fill('2026')
            await page.locator('[name="randomSeedIfFixed"]').fill('606')
            await page.locator('[name="fixRandomSeed"]').check()
            params = await page.evaluate("() => parseFormParams(document.getElementById('param-form'))")
            require(int(params['popSize']) == 50000 and int(params['startYear']) == 2019
                           and int(params['endYear']) == 2026 and int(params['randomSeedIfFixed']) == 606,
                           'Benchmark must use the fixed 50k / 2019–2026 / seed 606 profile')
            trial.report['parameter_sha256'] = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
            measurements.phase = trial.sampler.phase = 'build'
            began = time.monotonic()
            await request('POST', '/build-json/'+sid, params)
            deadline = began+900
            while True:
                status = await request('GET', '/status/'+sid)
                require(status.get('status') not in ('build_error', 'build_rejected', 'parameters_rejected', 'offline'),
                               'Model Build failed')
                if status.get('built'):
                    break
                require(time.monotonic() < deadline, 'Build exceeded 15 minutes')
                await asyncio.sleep(.5)
            trial.report['build_seconds'] = round(time.monotonic()-began, 3)
            print(f"  {trial.backend}: Build finished in {trial.report['build_seconds']:.2f}s; starting full run", flush=True)
            trial.save()
            measurements.phase = trial.sampler.phase = 'ready'
            await expect(page.locator('.js-plotly-plot').first).to_be_visible(timeout=60000)
            configuration = await request('GET', '/java/'+sid+'/simulation/current-params')
            values = {item['name']: item['value'] for item in configuration['modelParameters']}
            require(int(values['popSize']) == 50000 and int(values['endYear']) == 2026
                           and int(values['randomSeedIfFixed']) == 606, 'Java did not retain the chosen profile')
            await request('POST', '/java/'+sid+'/simulation/speed', {'speed': 0})
            measurements.phase = trial.sampler.phase = 'running'
            status, elapsed, highest_running_time = await run_to_completion(request, sid, end_year=2026,
                                                                           timeout=trial.options.run_timeout)
            trial.report['simulation_seconds'] = round(elapsed, 3)
            trial.report['final_simulation_time'] = status['time']
            trial.report['highest_running_simulation_time'] = highest_running_time
            print(f"  {trial.backend}: full run finished in {trial.report['simulation_seconds']:.2f}s; collecting chart samples", flush=True)
            trial.save()
            measurements.phase = trial.sampler.phase = 'output-check'
            listing = await request('GET', '/java/'+sid+'/simulation/export/list')
            candidates = [f for f in listing.get('files', []) if f.get('name') == 'HealthStatistics.csv' and int(f.get('size', 0)) > 0]
            require(candidates, 'Completed simulation did not publish HealthStatistics.csv')
            file = sorted(candidates, key=lambda item: item['timestamp'])[-1]
            response = await context.request.get(trial.base+'/java/'+sid+'/simulation/export/download?'+urlencode(
                dict(timestamp=file['timestamp'], path=file['path'], zip='never')), timeout=60000)
            require(response.ok, 'Summary output download failed')
            raw = await response.body()
            trial.report.update(summary_evidence(raw, start_year=2019, end_year=2026))
            # Full-speed execution is already timed. Collect populated-chart
            # samples independently at the normal interval, without delaying it.
            measurements.phase = trial.sampler.phase = 'chart-warmup'
            await page.wait_for_function("() => runState !== 'running'", timeout=15000)
            period = await page.evaluate("""() => {
                window.performanceChartLoop = {stop: false, finished: false, updates: 0,
                    phase: 'chart-warmup', cycles: [], error: false, paints: [], collecting: false};
                const loop = window.performanceChartLoop;
                const originals = {};
                for (const name of ['react', 'extendTraces']) {
                    originals[name] = window.Plotly[name];
                    window.Plotly[name] = function (...args) {
                        const result = originals[name].apply(this, args);
                        if (loop.collecting && result?.then) loop.paints.push(Promise.resolve(result));
                        return result;
                    };
                }
                (async () => {
                    try {
                        while (!loop.stop) {
                            const began = performance.now();
                            const phase = loop.phase;
                            loop.paints = [];
                            loop.collecting = true;
                            await updateChartsOnce();
                            loop.collecting = false;
                            await Promise.all(loop.paints);
                            loop.cycles.push({phase, request_ms: performance.now()-began});
                            loop.updates++;
                            await new Promise(resolve => setTimeout(resolve,
                                Math.max(0, CONFIG.CHART_POLL_INTERVAL-(performance.now()-began))));
                        }
                    } catch (error) { loop.error = true; }
                    finally {
                        for (const name of Object.keys(originals)) window.Plotly[name] = originals[name];
                        loop.finished = true;
                    }
                })();
                return CONFIG.CHART_POLL_INTERVAL;
            }""")
            require(period == POLL_MS, 'Frontend chart interval changed; review benchmark assumptions')
            warmup_deadline = time.monotonic()+60
            while measurements.chart_count('chart-warmup') < 10:
                require(not trial.sampler.low_disk, 'Free root storage fell below 2 GiB')
                require(time.monotonic() < warmup_deadline, 'Chart warm-up did not finish within one minute')
                require(not await page.evaluate("() => window.performanceChartLoop.error"), 'Chart drawing failed')
                await asyncio.sleep(.5)
            measurements.phase = trial.sampler.phase = 'completed-charts'
            await page.evaluate("() => { window.performanceChartLoop.phase = 'completed-charts'; }")
            deadline = time.monotonic()+max(120, target*POLL_MS/1000*3)
            next_notice = 200
            while measurements.chart_count('completed-charts') < target:
                require(not trial.sampler.low_disk, 'Free root storage fell below 2 GiB')
                require(time.monotonic() < deadline, 'Chart sample target was not reached within the test limit')
                require(not await page.evaluate("() => window.performanceChartLoop.error"), 'Chart drawing failed')
                count = measurements.chart_count('completed-charts')
                if count >= next_notice:
                    print(f"  {trial.backend}: {count}/{target} populated-chart responses", flush=True)
                    next_notice += 200
                await asyncio.sleep(.5)
            await page.evaluate("() => { window.performanceChartLoop.stop = true; }")
            await page.wait_for_function("() => window.performanceChartLoop.finished", timeout=15000)
            require(not await page.evaluate("() => window.performanceChartLoop.error"), 'Chart drawing failed')
            await measurements.drain()
            cycles = await page.evaluate("() => window.performanceChartLoop.cycles")
            trial.cycles = cycles
            trial.report['chart_update_cycles_including_drawing'] = summarize(
                [row for row in cycles if row['phase'] == 'completed-charts'])
            completed = [row for row in measurements.rows if row['phase'] == 'completed-charts' and row['endpoint'] == 'charts']
            require(summarize(completed)['failures'] == 0 and not trial.report['page_errors'],
                           'Chart sampling recorded failures or browser exceptions')
            checked = [row for row in measurements.rows if row['phase'] in
                       ('build', 'ready', 'running', 'chart-warmup', 'completed-charts')]
            require(summarize(checked)['failures'] == 0, 'Measured browser requests recorded failures')
            final_logs = await asyncio.to_thread(trial.capture_model_log)
            require(not browser_tools.chart_errors(final_logs.decode(errors='replace')),
                    'Java reported chart-processing errors; inspect the private java.log')
            trial.report['chart_sample_target'] = target
            trial.report['checks'] = ['reviewed model resources and private network', 'full fixed-seed simulation',
                                      'summary output covers every requested year', 'normal-interval chart sampling without failures']
            trial.report['status'] = 'passed'
        finally:
            await measurements.settle()
            await browser.close()
            for row in measurements.pending.values():
                row['failure'] = 'unfinished_request_at_browser_close'


def combined(trials, target):
    result = dict(by_backend={}, paired_durations=[],
                  interpretation='Measurement completion is not an automatic claim of performance equivalence.')
    for backend in ('redis', 'postgres'):
        selected = [trial for trial in trials if trial.backend == backend]
        rows = [row for trial in selected for row in trial.rows]
        result['by_backend'][backend] = dict(response_times_by_phase=grouped(rows),
            completed_chart_update_cycles_including_drawing=summarize(
                [row for trial in selected for row in trial.cycles if row['phase'] == 'completed-charts']),
            build_seconds=[trial.report['build_seconds'] for trial in selected],
            simulation_seconds=[trial.report['simulation_seconds'] for trial in selected])
        running = [row for row in rows if row['phase'] == 'running' and row['endpoint'] == 'charts'
                   and row.get('request_ms') is not None]
        result['by_backend'][backend]['running_chart_requests'] = len(running)
        result['by_backend'][backend]['running_chart_sample_target_met'] = len(running) >= target
        chart_rows = [row for row in rows if row['phase'] == 'completed-charts' and row['endpoint'] == 'charts']
        require(len(chart_rows) >= target, 'Not enough completed-chart samples for '+backend)
        checked = [row for row in rows if row['phase'] in ('build', 'ready', 'running', 'chart-warmup', 'completed-charts')]
        require(summarize(checked)['failures'] == 0, 'Measured requests failed for '+backend)
    require(len({trial.report['parameter_sha256'] for trial in trials}) == 1, 'Run settings differed across trials')
    require(len({trial.report['frontend_snapshot_sha256'] for trial in trials}) == 1, 'Frontend sources changed during testing')
    require(len({trial.report['summary_output_sha256'] for trial in trials}) == 1, 'Checked summary output differed across trials')
    for pair in sorted({trial.report['pair'] for trial in trials}):
        members = {trial.backend: trial.report for trial in trials if trial.report['pair'] == pair}
        result['paired_durations'].append(dict(pair=pair,
            build_postgres_over_redis=round(members['postgres']['build_seconds']/members['redis']['build_seconds'], 4),
            simulation_postgres_over_redis=round(members['postgres']['simulation_seconds']/members['redis']['simulation_seconds'], 4)))
    result['matched_settings_sources_and_summary_output'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=workflow.frontend_path())
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--redis-frontend', type=Path, required=True,
                        help='Separate pre-migration frontend snapshot for the historical Redis baseline')
    parser.add_argument('--catalogue', type=Path)
    parser.add_argument('--image')
    parser.add_argument('--pairs', type=int, default=3, help='Repeated pairs; starting backend alternates (default: 3)')
    parser.add_argument('--chart-requests', type=int, default=1000, help='Minimum completed-chart samples per backend (default: 1000)')
    parser.add_argument('--run-timeout', type=int, default=1800, help='Full-speed execution limit per trial in seconds')
    options = parser.parse_args()
    if not 1 <= options.pairs <= 10 or not 20 <= options.chart_requests <= 20000 or not 60 <= options.run_timeout <= 7200:
        parser.error('Use 1–10 pairs, 20–20,000 chart requests and a 60–7,200 second execution limit')
    options.frontend = options.frontend.expanduser().resolve()
    options.redis_frontend = options.redis_frontend.expanduser().resolve()
    if options.frontend == options.redis_frontend:
        parser.error('Use a separate pre-migration checkout for --redis-frontend; the current VM runtime is PostgreSQL only')
    out = options.output.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    (out/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Generated private SingleRun state performance evidence.\n@author ross richardson\n-->\n')
    report = dict(status='running', scope='One real 50k model; full-speed 2019–2026 execution and separate chart viewing.',
                  load='No additional MultiRun CPU/disk load.', pairs=options.pairs,
                  minimum_completed_chart_requests_per_backend=options.chart_requests,
                  order=order(options.pairs), trials=[], limitations=[
                      'Completed-chart samples do not establish responsiveness under ongoing model CPU load.',
                      'Operating system caches and thermal state are not controlled; inspect repeated paired timings.',
                      'State spans include pool/connection waits and transactions; dispatch spans also include worker scheduling.',
                      'Java spans include HTTP/body handling. Cached or coalesced responses may have no owned Java call.',
                      'Browser response timing excludes drawing. The separate completed-chart cycle times await Plotly promises.',
                      'Drawing completion is explicitly awaited only in the test viewing loop, not changed in the production UI.'])
    def save():
        (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    def stop(signum, frame):
        raise KeyboardInterrupt
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    trials = []
    try:
        import docker
        resource_check(common.host_resources())
        entry = workflow.catalogue_model(workflow.catalogue_path(options.frontend, options.catalogue), 'simpaths-quickstart-50000')
        deployment = entry['deployment']
        require(deployment.get('memory') == '5Gi' and deployment.get('java_heap_gib') == 3
                       and deployment.get('cpu') == 2, 'Catalogue allocation changed; review benchmark assumptions')
        client = docker.DockerClient(base_url='unix:///var/run/docker.sock', timeout=15)
        try:
            client.ping()
            image_id = client.images.get(options.image or deployment['image']).id
            state_images = {backend: client.images.get(image).id for backend, image in
                            (('redis', 'redis:7-alpine'), ('postgres', 'postgres:17-alpine'))}
        finally:
            client.close()
        report.update(image_id=image_id,
            frontend_revision=subprocess.check_output(['git', '-C', str(options.frontend), 'rev-parse', 'HEAD'], text=True).strip(),
            measurement_note='Warm-up samples excluded; completed-chart target is independent of naturally occurring running samples.')
        save()
        per_trial = math.ceil(options.chart_requests/options.pairs)
        for pair, backend in order(options.pairs):
            print(f'Pair {pair}/{options.pairs}: {backend}, 50,000 people, 2019–2026, seed 606', flush=True)
            trial = Trial(options, pair, backend, image_id, state_images[backend], entry, out/f'pair-{pair}-{backend}')
            trials.append(trial)
            report['trials'].append(trial.report)
            with trial:
                asyncio.run(exercise(trial, per_trial))
            print(f"PASS: {backend}; Build {trial.report['build_seconds']:.2f}s; run {trial.report['simulation_seconds']:.2f}s", flush=True)
            save()
        report['comparison'] = combined(trials, options.chart_requests)
        report['status'] = 'passed'
        for backend, result in report['comparison']['by_backend'].items():
            charts = result['response_times_by_phase']['completed-charts']['charts']
            print(f"{backend}: {charts['measured']} completed-chart responses; median {charts['median_ms']:.2f} ms; "
                  f"p95 {charts['p95_ms']:.2f} ms; p99 {charts['p99_ms']:.2f} ms; "
                  f"over 500 ms {charts['over_500_percent']:.2f}%", flush=True)
            print(f"  Active-run chart responses: {result['running_chart_requests']}; "
                  f"Build median {statistics.median(result['build_seconds']):.2f}s; "
                  f"full-run median {statistics.median(result['simulation_seconds']):.2f}s", flush=True)
    except BaseException as error:
        report.update(status='failed', error=type(error).__name__)
        if isinstance(error, PerformanceCheckFailed):
            report['error_detail'] = str(error)
        print('STOPPED:', report.get('error_detail', type(error).__name__), '; inspect the private reports/logs.', flush=True)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        save()
        print(report['status'].upper()+': '+str(out/'report.json'), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
