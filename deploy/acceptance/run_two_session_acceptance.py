#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Two Quick Start 20k browser sessions on an isolated frontend and selected state store.
Checks concurrent Builds/runs, owner isolation, independent Reset/Leave and outputs.
No image builds, pulls, pruning or production configuration changes.
@author ross richardson
"""
import argparse
import asyncio
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import statistics
import signal
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qs

import runpy
load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
workflow = load_tool('_workflow.py')

GIB = 1024**3
# Short 20k concurrency test: measured ~5.4 GiB host RAM increase and
# ~3.6 GiB root-disk use on 2026-09-24. These are test admission thresholds,
# not production reservations or changes to the two 4 GiB container limits.
MIN_AVAILABLE_MEMORY = 8 * GIB
MIN_ROOT_FREE = 6 * GIB


def require(value, message):
    if not value:
        raise AssertionError(message)


def host_resources():
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return {'available_memory_bytes': int(memory['MemAvailable'].split()[0])*1024,
            'root_free_bytes': shutil.disk_usage('/').free}


def resource_check(resources):
    require(resources['available_memory_bytes'] >= MIN_AVAILABLE_MEMORY,
            'Need 8 GiB available RAM for the measured short two-session test plus headroom. Close other applications first.')
    require(resources['root_free_bytes'] >= MIN_ROOT_FREE,
            'Need 6 GiB free on root before the two-session test. Free space before retrying.')


def isolated_environment(*, state_backend='postgres', postgres_dsn_file=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('REDIS_', 'JASMINE_', 'VM_', 'UVICORN_', 'ADMIN_', 'SESSION_', 'HARD_RESET_'))}
    env.update(DEPLOY_MODE='vm', VM_SECURITY_MODE='development', VM_MAX_SESSIONS='2',
               VM_STATE_BACKEND=state_backend,
               VM_MAX_SESSIONS_PER_CLIENT='2', VM_SESSION_MEMORY_BUDGET_MIB='8192',
               COOKIE_SECURE='false',
               ADMIN_PASSWORD=secrets.token_urlsafe(32), SESSION_SECRET=secrets.token_urlsafe(32),
               HARD_RESET_SECRET=secrets.token_urlsafe(32), PYTHON_DOTENV_DISABLED='1', PYTHONUNBUFFERED='1')
    require(state_backend == 'postgres' and postgres_dsn_file is not None, 'Disposable PostgreSQL DSN file required')
    env.update(VM_POSTGRES_DSN_FILE=str(postgres_dsn_file), VM_POSTGRES_SCHEMA='jasmine_vm', VM_POSTGRES_POOL_SIZE='4')
    return env


def copy_frontend(frontend, work):
    tracked = subprocess.check_output(['git', '-C', str(frontend), 'ls-files', '-z'], text=True).split('\0')
    # The migration candidate may still be under test before its first commit.
    tracked += [str(p.relative_to(frontend)) for p in (frontend/'jasmine_web').glob('vm_*.py')]
    tracked += [str(p.relative_to(frontend)) for p in (frontend/'jasmine_web').glob('vm_*.sql')]
    tracked += [str(p.relative_to(frontend)) for p in (frontend/'jasmine_web/batch').glob('*.sql')]
    for name in tracked:
        source = frontend/name
        if (not name or source.is_symlink() or not source.is_file()
                or not (source.suffix in ('.py', '.sql') or name.startswith('static/'))):
            continue
        target = work/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def is_owned(labels, model, owner):
    return (labels.get('jasmine.model_id') == model and bool(labels.get('jasmine.session_id'))
            and labels.get('jasmine.deployment_id') == owner)


def cleanup_models(client, model, owner):
    # Delete by immutable object IDs, with independent deployment/model/session checks.
    for container in client.containers.list(all=True, filters={'label': f'jasmine.model_id={model}'}):
        container.reload()
        require(owner and is_owned(container.labels, model, owner), 'Container cleanup ownership mismatch')
        container.remove(force=True)
    for network in client.networks.list(filters={'label': f'jasmine.model_id={model}'}):
        network.reload()
        labels = network.attrs.get('Labels') or {}
        sid = labels.get('jasmine.session_id', '')
        require(owner and is_owned(labels, model, owner)
                and network.name == 'jms-'+hashlib.sha256(sid.encode()).hexdigest()[:10]
                and not network.attrs.get('Containers'), 'Network cleanup ownership mismatch')
        network.remove()
    require(not client.containers.list(all=True, filters={'label': f'jasmine.model_id={model}'})
            and not client.networks.list(filters={'label': f'jasmine.model_id={model}'}), 'Test resources remain')


class Sampler:
    def __init__(self, client, model, path):
        self.client, self.model, self.path = client, model, path
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.phase = 'setup'
        self.low_disk = False
        self.errors = 0
        self.departed_containers = 0

    def snapshot(self):
        from docker.errors import NotFound
        from requests.exceptions import JSONDecodeError as ResponseJSONError
        host = host_resources()
        self.low_disk |= host['root_free_bytes'] < 2*GIB
        row = {'host': host, 'containers': [], 'departed_containers': []}
        # IDs suffice here; avoid a second inventory-inspection race in the SDK.
        for container in self.client.containers.list(filters={'label': f'jasmine.model_id={self.model}'}, sparse=True):
            try:
                stats = container.stats(stream=False, one_shot=True)
            except NotFound:
                # Leave can remove a container after the inventory was read.
                # Record that change; it is not a failed resource measurement.
                self.departed_containers += 1
                row['departed_containers'].append(container.id)
                continue
            except (json.JSONDecodeError, ResponseJSONError):
                # Docker may close a pending stats response when Leave stops
                # the container. Ignore it only after confirming that state;
                # invalid statistics from a still-running container must fail.
                try:
                    container.reload()
                except NotFound:
                    pass
                else:
                    if container.status not in ('exited', 'dead', 'removing'):
                        raise
                self.departed_containers += 1
                row['departed_containers'].append(container.id)
                continue
            row['containers'].append({'id': container.id,
                'memory_usage_bytes': stats.get('memory_stats', {}).get('usage'),
                'cpu_total_ns': stats.get('cpu_stats', {}).get('cpu_usage', {}).get('total_usage'),
                'block_io': stats.get('blkio_stats', {}).get('io_service_bytes_recursive', [])})
        return row

    def sample(self):
        with self.path.open('w') as stream:
            while not self.stop.is_set():
                row = {'elapsed_seconds': round(time.monotonic()-self.started, 2),
                       'phase': self.phase}
                try:
                    row.update(self.snapshot())
                except Exception as error:
                    self.errors += 1
                    row['error'] = {'type': type(error).__name__}
                    response = getattr(error, 'response', None)
                    if response is not None:
                        row['error']['http_status'] = response.status_code
                stream.write(json.dumps(row)+'\n')
                stream.flush()
                self.stop.wait(5)

    def start(self):
        self.started = time.monotonic()
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=20)
        require(not self.thread.is_alive(), 'Resource sampler did not stop')


def summarize_samples(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    rows = [row for row in rows if 'error' not in row]
    require(rows, 'No resource samples were recorded')
    summary = {'samples': len(rows), 'minimum_root_free_bytes': min(r['host']['root_free_bytes'] for r in rows),
               'minimum_available_memory_bytes': min(r['host']['available_memory_bytes'] for r in rows),
               'peak_combined_container_memory_bytes': max(sum(c.get('memory_usage_bytes') or 0 for c in r['containers']) for r in rows),
               'containers': {}, 'note': 'Docker memory includes cache; host free-space changes include unrelated activity. No single-session slowdown baseline.'}
    for row in rows:
        for container in row['containers']:
            item = summary['containers'].setdefault(container['id'], {'peak_memory_bytes': 0, 'cpu_seconds_observed': 0})
            item['peak_memory_bytes'] = max(item['peak_memory_bytes'], container.get('memory_usage_bytes') or 0)
            item['cpu_seconds_observed'] = max(item['cpu_seconds_observed'], (container.get('cpu_total_ns') or 0)/1e9)
    return summary


def summarize_response_times(samples):
    """Browser request timing in milliseconds, without response bodies or URLs."""
    return {name: dict(requests=len(values), mean_ms=round(statistics.mean(values),3),
                       p95_ms=round(sorted(values)[math.ceil(.95*len(values))-1],3),
                       max_ms=round(max(values),3)) for name, values in samples.items() if values}


async def exercise(base, client, model, out, report, passed, sampler):
    from playwright.async_api import async_playwright, expect
    import httpx
    assert_private_network = load_tool('acceptance/run_browser_acceptance.py').assert_private_network
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        timings = dict(status=[], charts=[], logs=[])
        def finished(request):
            path = urlsplit(request.url).path
            name = path.split('/')[1] if path.startswith('/') else ''
            if name in timings:
                # Playwright Request.timing responseEnd is relative to startTime,
                # available after requestfinished, and includes body transfer.
                # https://playwright.dev/python/docs/api/class-request#request-timing
                elapsed = request.timing['responseEnd']
                if elapsed >= 0 and math.isfinite(elapsed):
                    timings[name].append(elapsed)
        try:
            contexts = [await browser.new_context(viewport={'width': 1200, 'height': 800},
                        extra_http_headers={'Origin': base}) for _ in range(2)]
            pages = [await context.new_page() for context in contexts]
            sessions = []
            for page in pages:
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                page.on('requestfinished', finished)
                await page.goto(base)
                await page.locator('.model-card').click()
                await page.wait_for_url(lambda url: urlsplit(str(url)).path.startswith('/sim/') or
                                        'error' in parse_qs(urlsplit(str(url)).query), timeout=180000)
                parsed = urlsplit(page.url)
                require(parsed.path.startswith('/sim/'), 'Launch rejected: '+str(parse_qs(parsed.query).get('error')))
                sessions.append(parsed.path.split('/')[-1])
                await expect(page.locator('#param-form')).to_be_visible(timeout=180000)
            require(sessions[0] != sessions[1], 'Sessions share an identity')
            report['session_ids'] = sessions
            passed('two independent browser owners launched on the same frontend')
            containers = client.containers.list(filters={'label': f'jasmine.model_id={model}'})
            require(len(containers) == 2, 'Expected two model containers')
            mapping = {container.labels['jasmine.session_id']: container for container in containers}
            models = [mapping[sid] for sid in sessions]
            topology = [await asyncio.to_thread(assert_private_network, container, client) for container in models]
            for container in models:
                require(container.attrs['HostConfig']['Memory'] == 4*GIB and
                        container.attrs['HostConfig']['NanoCpus'] == 2_000_000_000, 'Incorrect resource limits')
            require(topology[0]['network_id'] != topology[1]['network_id'], 'Sessions share a network')
            passed('separate private networks and two 4 GiB / 2 CPU containers')

            async def request(index, method, path, data=None):
                require(not sampler.low_disk, 'Host free space dropped below 2 GiB; stopping test')
                response = await contexts[index].request.fetch(base+path, method=method, data=data,
                                                               timeout=330000, max_redirects=0)
                require(response.ok, f'{method} {path}: HTTP {response.status}')
                payload = await response.json()
                require(not payload.get('error'), f'{path}: {payload.get("error")}')
                return payload

            # Ownership denial is checked in both directions for reads, lifecycle
            # mutations, and the actual output-download path later in this test.
            for i in (0, 1):
                other = sessions[1-i]
                for method, path in [('GET', '/status/'+other), ('GET', '/charts/'+other),
                                     ('GET', '/java/'+other+'/simulation/export/list'),
                                     ('POST', '/reset/'+other), ('POST', '/leave/'+other)]:
                    response = await contexts[i].request.fetch(base+path, method=method, timeout=30000, max_redirects=0)
                    denied = response.status in (401, 403, 404)
                    if path.startswith('/leave/'):
                        denied = response.status == 303 and response.headers.get('location') == '/?error=Access+denied'
                    require(denied, f'Cross-owner access not explicitly denied: {path} ({response.status})')
            # Check Java backend credentials independently of browser ownership.
            secrets_by_session = [dict(x.split('=', 1) for x in c.attrs['Config']['Env'] if '=' in x)['JASMINE_BACKEND_SECRET']
                                  for c in models]
            async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
                for i in (0, 1):
                    url = topology[i]['java_private_endpoint']+'/simulation/status'
                    for token, expected in ((None, 401), (secrets_by_session[1-i], 401), (secrets_by_session[i], 200)):
                        response = await http.get(url, headers={'X-Jasmine-Backend-Token': token} if token else {})
                        require(response.status_code == expected, 'Backend credential isolation failed')
            passed('cross-owner reads, Reset and Leave rejected; backend credentials isolated')

            for page, seed in zip(pages, (606, 707)):
                await page.locator('[name="endYear"]').fill('2022')
                await page.locator('[name="randomSeedIfFixed"]').fill(str(seed))
            params = [await page.evaluate("parseFormParams(document.getElementById('param-form'))") for page in pages]
            require(int(params[0]['randomSeedIfFixed']) == 606 and int(params[1]['randomSeedIfFixed']) == 707,
                    'Browser parameters not independently editable')
            sampler.phase = 'concurrent-build'
            build_start = time.monotonic()
            await asyncio.gather(*(request(i, 'POST', '/build-json/'+sid, params[i]) for i, sid in enumerate(sessions)))
            initial = await asyncio.gather(*(request(i, 'GET', '/status/'+sid) for i, sid in enumerate(sessions)))
            require(all(value['status'] == 'building' for value in initial), 'Concurrent Builds not observed')
            report['overlapping_build_status'] = initial
            async def ready(i):
                end = time.monotonic()+900
                while time.monotonic() < end:
                    status = await request(i, 'GET', '/status/'+sessions[i])
                    require(status.get('status') not in ('build_error', 'build_rejected', 'parameters_rejected', 'offline'),
                            'Build failed: '+str(status))
                    if status.get('built'):
                        return round(time.monotonic()-build_start, 2)
                    await asyncio.sleep(2)
                raise TimeoutError('Concurrent Build did not complete within 15 minutes')
            report['build_seconds'] = await asyncio.gather(ready(0), ready(1))
            for i, seed in enumerate((606, 707)):
                current = await request(i, 'GET', '/java/'+sessions[i]+'/simulation/current-params')
                values = {p['name']: p['value'] for p in current['modelParameters']}
                require(int(values['randomSeedIfFixed']) == seed and int(values['endYear']) == 2022, 'Model parameters leaked between sessions')
                await expect(pages[i].locator('#start-btn')).to_be_enabled(timeout=60000)
                await expect(pages[i].locator('.js-plotly-plot').first).to_be_visible(timeout=60000)
            passed('overlapping Builds completed; each model retained its own seed', seconds=report['build_seconds'])

            sampler.phase = 'concurrent-run'
            for i, sid in enumerate(sessions):
                await request(i, 'POST', '/java/'+sid+'/simulation/speed', {'speed': 0})
            start = time.monotonic()
            await asyncio.gather(*(request(i, 'POST', '/start-sim/'+sid) for i, sid in enumerate(sessions)))
            # Frontend cache can briefly hold pre-Start status. Record real overlap,
            # rather than assuming simultaneous Start requests prove it.
            until = time.monotonic()+40
            while True:
                statuses = await asyncio.gather(*(request(i, 'GET', '/status/'+sid) for i, sid in enumerate(sessions)))
                if all(x.get('status') == 'running' for x in statuses):
                    break
                require(time.monotonic() < until, 'Two running simulations were not observed together')
                await asyncio.sleep(0.5)
            report['overlapping_run_status'] = statuses
            # Pause after a year event; Pause waits for the current event to finish.
            await asyncio.sleep(3)
            await asyncio.gather(*(request(i, 'POST', '/pause/'+sid) for i, sid in enumerate(sessions)))
            report['run_until_pause_seconds'] = round(time.monotonic()-start, 2)
            passed('both simulations ran concurrently and paused successfully')

            async def output(i):
                sid = sessions[i]
                for _ in range(20):
                    listing = await request(i, 'GET', '/java/'+sid+'/simulation/export/list')
                    matches = [f for f in listing.get('files', []) if f.get('name') == 'HealthStatistics.csv' and int(f.get('size', 0)) > 0]
                    if matches:
                        from urllib.parse import urlencode
                        file = sorted(matches, key=lambda f: f['timestamp'])[-1]
                        path = '/java/'+sid+'/simulation/export/download?'+urlencode({
                            'timestamp': file['timestamp'], 'path': file['path'], 'zip': 'never'})
                        response = await contexts[i].request.get(base+path, timeout=60000)
                        require(response.ok, 'Output download failed')
                        raw = await response.body()
                        if len(raw.splitlines()) >= 2:
                            return path, raw
                    await request(i, 'POST', '/step/'+sid)
                raise AssertionError('No populated HealthStatistics.csv after scheduled steps')
            outputs = await asyncio.gather(output(0), output(1))
            for i, (path, raw) in enumerate(outputs):
                (out/f'session-{i+1}-HealthStatistics.csv').write_bytes(raw)
                response = await contexts[1-i].request.get(base+path, timeout=30000, max_redirects=0)
                require(response.status in (401, 403, 404), 'Other owner could download output')
            passed('both sessions generated downloadable output; cross-owner downloads rejected')
            b_before = await request(1, 'GET', '/java/'+sessions[1]+'/simulation/current-params')
            b_status = await request(1, 'GET', '/java/'+sessions[1]+'/simulation/status')
            b_hash = hashlib.sha256(outputs[1][1]).hexdigest()
            sampler.phase = 'reset-and-leave-a'
            await request(0, 'POST', '/reset/'+sessions[0])
            a_listing = await request(0, 'GET', '/java/'+sessions[0]+'/simulation/export/list')
            require(a_listing.get('files'), 'Ordinary Reset lost A outputs')
            require(await request(1, 'GET', '/java/'+sessions[1]+'/simulation/status') == b_status, 'Reset A changed B state')
            require(await request(1, 'GET', '/java/'+sessions[1]+'/simulation/current-params') == b_before, 'Reset A changed B parameters')
            require(hashlib.sha256(await (await contexts[1].request.get(base+outputs[1][0])).body()).hexdigest() == b_hash, 'Reset A changed B output')
            response = await contexts[0].request.post(base+'/leave/'+sessions[0], max_redirects=0, timeout=120000)
            require(response.status == 303 and response.headers.get('location') == '/', 'Leave A failed')
            remaining = client.containers.list(filters={'label': f'jasmine.model_id={model}'})
            require(len(remaining) == 1 and remaining[0].id == models[1].id, 'Leave removed the wrong container')
            require(await request(1, 'GET', '/java/'+sessions[1]+'/simulation/status') == b_status, 'Leave A changed B state')
            require(hashlib.sha256(await (await contexts[1].request.get(base+outputs[1][0])).body()).hexdigest() == b_hash, 'Leave A changed B output')
            passed('Reset and Leave A preserved B state, parameters, container and output hash', b_output_sha256=b_hash)
            sampler.phase = 'b-continues-alone'
            # Confirm B can still execute a scheduled event, and render its charts.
            step_result = await request(1, 'POST', '/step/'+sessions[1])
            require(step_result.get('status') == 'stepped', 'B did not execute Step')
            charts = await request(1, 'GET', '/charts/'+sessions[1])
            require(charts.get('charts'), 'B charts unavailable after leaving A')
            require(await request(1, 'GET', '/java/'+sessions[1]+'/simulation/export/list'), 'B output unavailable')
            await pages[1].screenshot(path=str(out/'session-b-after-leave.png'), full_page=False, timeout=15000)
            passed('B remains operable after A leaves')
            require(not report['page_errors'], 'Uncaught browser errors: '+str(report['page_errors']))
            passed('no uncaught browser exceptions')
            log_text = await asyncio.to_thread(models[1].logs, tail=20000)
            (out/'session-b-java.log').write_bytes(log_text)
            require(b'Processor: error processing' not in log_text, 'Java chart-processing error')
            passed('no Java chart-processing errors in surviving session')
        finally:
            await browser.close()
            report['browser_poll_response_times'] = summarize_response_times(timings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=workflow.frontend_path())
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--catalogue', type=Path, help='Catalogue containing the Quick Start 20,000 allocation')
    parser.add_argument('--image', help='Override the catalogue image for testing a candidate')
    parser.add_argument('--state-backend', choices=['postgres'], default='postgres',
                        help='Disposable state store for the same real-model browser workflow')
    args = parser.parse_args()
    args.frontend = args.frontend.expanduser().resolve()
    import docker
    import httpx
    free_port = load_tool('acceptance/run_browser_acceptance.py').free_port
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    (out/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\nTwo-session acceptance evidence and copied frontend snapshot.\n@author ross richardson\n-->\n')
    report = {'status': 'running', 'checks': [], 'page_errors': [], 'scope': 'Laptop functional concurrency; development networking, not host isolation proof',
              'single_session_baseline': 'Not measured; no slowdown ratio claimed', 'state_backend': args.state_backend}
    model = 'simpaths-acceptance-two-'+secrets.token_hex(6)
    report['model_id'] = model
    client = process = postgres_container = sampler = None
    dsn_file = None
    owner = None
    log = (out/'frontend.log').open('w')
    def save():
        (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    def passed(name, **evidence):
        report['checks'].append({'name': name, 'status': 'passed', **evidence})
        save()
        print('PASS:', name, flush=True)
    def stop(signum, frame):
        raise KeyboardInterrupt
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        report['host_before'] = host_resources()
        resource_check(report['host_before'])
        candidate_client = docker.DockerClient(base_url='unix:///var/run/docker.sock', timeout=15)
        candidate_client.ping()
        client = candidate_client
        entry = workflow.catalogue_model(workflow.catalogue_path(args.frontend, args.catalogue),
                                         'simpaths-quickstart-20000')
        if args.image:
            entry['deployment']['image'] = args.image
        image = client.images.get(entry['deployment']['image'])
        state_image = client.images.get('postgres:17-alpine')
        report['image_id'] = image.id
        report['state_image_id'] = state_image.id
        report['frontend_revision'] = subprocess.check_output(['git', '-C', str(args.frontend), 'rev-parse', 'HEAD'], text=True).strip()
        report['frontend_worktree_status'] = subprocess.check_output(['git', '-C', str(args.frontend), 'status', '--short'], text=True)
        work = out/'frontend'
        copy_frontend(args.frontend, work)
        entry.update(id=model, name='Two-session acceptance Quick Start 20,000', requiresAuth=False)
        entry['deployment']['image'] = image.id
        require(entry['deployment']['memory'] == '4Gi' and entry['deployment']['java_heap_gib'] == 2,
                'Catalogue allocation changed; review two-session test assumptions')
        (work/'models.json').write_text(json.dumps({'models': [entry]}))
        state_port, web_port = free_port(), free_port()
        password = secrets.token_hex(32)
        postgres_container = client.containers.run(state_image.id, detach=True,
            ports={'5432/tcp': ('127.0.0.1',state_port)}, labels={'simpaths.acceptance':model},
            environment=dict(POSTGRES_PASSWORD=password,POSTGRES_DB='jasmine_proof'),
            mem_limit='768m',nano_cpus=1_000_000_000,pids_limit=128,
            tmpfs={'/var/lib/postgresql/data':'rw,size=512m'},
            command=['postgres','-c','shared_buffers=32MB','-c','max_wal_size=64MB','-c','min_wal_size=32MB','-c','checkpoint_timeout=1min'])
        until=time.monotonic()+30
        while postgres_container.exec_run(['pg_isready','-h','127.0.0.1','-U','postgres','-d','jasmine_proof']).exit_code:
            require(time.monotonic()<until,'Test PostgreSQL did not become ready')
            time.sleep(.25)
        dsn_file=work/'.postgres-proof.dsn'
        fd=os.open(dsn_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream:
            stream.write(f'postgresql://postgres:{password}@127.0.0.1:{state_port}/jasmine_proof')
        sys.path.insert(0,str(args.frontend))
        try:
            from jasmine_web.vm_state import PostgresVMState
            state=PostgresVMState(dsn_file.read_text())
            try:
                state.migrate()
                owner=state.deployment_id()
            finally:
                state.close()
        finally:
            sys.path.pop(0)
        env=isolated_environment(state_backend='postgres',postgres_dsn_file=dsn_file)
        report['deployment_id'] = owner
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(web_port)],
            cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        base = f'http://127.0.0.1:{web_port}'
        until = time.monotonic()+60
        with httpx.Client(trust_env=False, timeout=3) as http:
            while True:
                require(process.poll() is None, 'Frontend exited; see frontend.log')
                try:
                    if http.get(base+'/health').status_code == 200: break
                except httpx.HTTPError: pass
                require(time.monotonic() < until, 'Test frontend did not become ready')
                time.sleep(.5)
        require(owner, 'Test deployment identity missing')
        report['deployment_id'] = owner
        sampler = Sampler(client, model, out/'resources.jsonl')
        sampler.start()
        asyncio.run(exercise(base, client, model, out, report, passed, sampler))
        report['status'] = 'passed'
    except (Exception, KeyboardInterrupt) as error:
        report['status'] = 'failed'
        report['error'] = str(error) if isinstance(error, AssertionError) else type(error).__name__
        print('STOPPED:', report['error'], flush=True)
    finally:
        errors = []
        if sampler:
            try: sampler.close()
            except Exception as error: errors.append(type(error).__name__)
            report['resource_sampling_errors'] = sampler.errors
            report['containers_stopped_or_removed_during_sampling'] = sampler.departed_containers
            try: report['resource_summary'] = summarize_samples(out/'resources.jsonl')
            except Exception as error: errors.append('Resource summary: '+type(error).__name__)
            if sampler.errors:
                errors.append('Resource samples failed; inspect resources.jsonl')
        if process:
            process.terminate()
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if client:
            try:
                cleanup_models(client, model, owner)
            except Exception as error: errors.append('model cleanup: '+type(error).__name__)
        if postgres_container:
            try:
                postgres_container.reload()
                require(postgres_container.labels.get('simpaths.acceptance')==model,'PostgreSQL cleanup ownership mismatch')
                postgres_container.remove(force=True,v=True)
            except Exception as error: errors.append('PostgreSQL cleanup: '+type(error).__name__)
        if dsn_file is not None:
            try: dsn_file.unlink(missing_ok=True)
            except Exception as error: errors.append('Credential cleanup: '+type(error).__name__)
        if errors:
            report['cleanup_errors'] = errors
            report['status'] = 'failed'
        report['host_after'] = host_resources()
        log.close()
        if client: client.close()
        for sig, handler in previous.items(): signal.signal(sig, handler)
        save()
        print(f"{report['status'].upper()}: {out/'report.json'}", flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
