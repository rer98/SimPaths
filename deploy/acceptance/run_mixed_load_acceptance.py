#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Real SingleRun/MultiRun shared PostgreSQL admission and recovery on a laptop.
Uses installed public training images, disposable state and private workspaces.
No image pulls, email delivery or changes to the user's launchers or datasets.
@author ross richardson
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import runpy
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[2]
load_tool = runpy.run_path(str(ROOT / 'deploy/_tool_loader.py'))['load_tool']
common = load_tool('acceptance/run_two_session_acceptance.py')
performance = load_tool('acceptance/run_state_performance.py')
browser_tools = load_tool('acceptance/run_browser_acceptance.py')
postgres_fixture = load_tool('acceptance/postgres_fixture.py')
GIB = 1024**3
RESOURCE_KEYS = ('cpu_millis', 'memory_mib', 'storage_mib')
require = common.require


def resource_check(host, scratch_free, output_free):
    # Both model limits total 9 GiB; leave room for PostgreSQL, Chromium and OS.
    # Physical launch checks cover input copies, not the logical 20 GiB ledger.
    require(host['available_memory_bytes'] >= 11*GIB,
            'Need 11 GiB available RAM for the two models, database and browser. Close other applications first.')
    require(host['root_free_bytes'] >= 8*GIB and scratch_free >= 6*GIB,
            'Need 8 GiB free on root and 6 GiB on the temporary-workspace filesystem.')
    require(output_free >= GIB//4, 'Need 256 MiB free for private reports and the frontend copy.')


def require_idle(client):
    require(not client.containers.list(filters={'label': 'jasmine.session_id'})
            and not client.containers.list(filters={'label': 'jasmine.batch=true'}),
            'Finish existing simulations and stop their launchers before the mixed-load acceptance test')


def require_original_container(client, label, expected_id):
    containers = client.containers.list(all=True, filters={'label': label})
    require(len(containers) == 1 and containers[0].id == expected_id,
            'Service recovery replaced or duplicated its original model container')
    containers[0].reload()
    require(containers[0].status == 'running', 'Original model container stopped during service recovery')


def mixed_configuration(receipt):
    from deploy.multirun.configuration import normalise
    from deploy.multirun.schema import OUTPUT_CONTRACT, SCHEMA_VERSION
    require(receipt['identity']['population'] == 50000, 'Use the verified 50,000-person Quick Start snapshot')
    return normalise(dict(schema_version=SCHEMA_VERSION, model_release='installed-quickstart',
        dataset_revision=receipt['revision'], experiment={'name': 'Shared VM acceptance'},
        common=dict(country='UK', start_year=2019, end_year=2026, population=50000),
        seed_plan=dict(mode='standard', repetitions=1),
        run_sets=[dict(id='mixed-run', name='50,000-person background configuration')],
        output_contract=OUTPUT_CONTRACT)).editable_configuration()


def ledger(queue):
    """One consistent, read-only snapshot; never hold SQL locks during Docker calls."""
    with queue._connection() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        pool = c.execute('SELECT cpu_millis,memory_mib,storage_mib FROM pools WHERE id=%s',
                         (queue.pool_id,)).fetchone()
        interactive = c.execute('SELECT session_id,cpu_millis,memory_mib,storage_mib '
            'FROM interactive_reservations WHERE pool_id=%s ORDER BY session_id', (queue.pool_id,)).fetchall()
        batch = c.execute('SELECT r.attempt_id::text AS attempt_id,r.cpu_millis,r.memory_mib,r.storage_mib '
            'FROM reservations r JOIN attempts a ON a.id=r.attempt_id '
            'WHERE a.pool_id=%s AND r.released_at IS NULL ORDER BY r.attempt_id', (queue.pool_id,)).fetchall()
        processors = c.execute('SELECT cpu_millis,memory_mib,storage_mib FROM processing_reservations '
                               'WHERE pool_id=%s', (queue.pool_id,)).fetchall()
    used = {key: sum(row[key] for row in interactive+batch+processors) for key in RESOURCE_KEYS}
    require(all(used[key] <= pool[key] for key in RESOURCE_KEYS), 'Shared resource allocations exceed the pool')
    return dict(capacity=pool, used=used, interactive=interactive, batch=batch, processors=processors)


def require_allocation(snapshot, single, batch, *, sid=None, attempt_id=None):
    expected = {key: (single[key] if sid else 0)+(batch[key] if attempt_id else 0) for key in RESOURCE_KEYS}
    require(snapshot['used'] == expected and not snapshot['processors'], 'Unexpected common resource allocation')
    require([r['session_id'] for r in snapshot['interactive']] == ([sid] if sid else []),
            'Interactive reservation disappeared or was duplicated')
    require([r['attempt_id'] for r in snapshot['batch']] == ([attempt_id] if attempt_id else []),
            'MultiRun reservation disappeared or was duplicated')


def job_leases(queue):
    """Trusted proof/cleanup lookup, including a launch interrupted before a receipt."""
    with queue._connection() as c:
        rows = c.execute('SELECT a.*,j.configuration_id,j.resources,j.dataset_id,j.model_digest,'
            'j.prepared_fingerprint,j.execution_run,e.specification FROM attempts a '
            'JOIN jobs j ON j.id=a.job_id JOIN experiments e ON e.id=j.experiment_id '
            'WHERE a.pool_id=%s ORDER BY a.created_at', (queue.pool_id,)).fetchall()
    return [queue._lease(row, row) for row in rows]


def timing_summary(rows):
    return {phase: {endpoint: performance.summarize([row for row in rows
                if row['phase'] == phase and row['endpoint'] == endpoint])
            for endpoint in ('status', 'charts', 'logs')}
        for phase in ('running-alone', 'mixed-running')}


def cpu_overlap(path, identifiers):
    """Require observed CPU progress in both real models during the mixed phase."""
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    together = [row for row in rows if row.get('phase') == 'mixed-running'
        and set(identifiers) <= {c['id'] for c in row.get('containers', [])}]
    require(len(together) >= 2, 'No simultaneous resource samples for the two models')
    progress = {}
    for identifier in identifiers:
        values = [c['cpu_total_ns'] for row in together for c in row['containers']
                  if c['id'] == identifier and c.get('cpu_total_ns') is not None]
        require(len(values) >= 2 and max(values)-min(values) > 1_000_000_000,
                'A model did not consume CPU during the mixed measurement')
        progress[identifier] = round((max(values)-min(values))/1e9, 3)
    return dict(simultaneous_samples=len(together), cpu_seconds_in_interval=progress)


class MixedInventory:
    """Extend the existing sampler only with this proof's batch identity label."""
    def __init__(self, client):
        self.containers = self
        self.client = client
        self.batch_identity = None

    def list(self, **kwargs):
        own = self.client.containers.list(**kwargs)
        identity = self.batch_identity
        if identity:
            own += self.client.containers.list(filters={'label': 'jasmine.batch.identity='+identity}, sparse=True)
        return list({container.id: container for container in own}.values())


def stop_process(process, *, timeout=30):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)
        raise AssertionError('Test service required forced shutdown; inspect its private log')


class Trial:
    def __init__(self, options, out, report, save):
        self.options, self.out, self.report, self.save = options, out, report, save
        self.client = self.process = self.dispatcher = self.postgres = self.sampler = None
        self.queue = self.executor = self.adapter = self.dsn_file = self.scratch = None
        self.deployment = None
        self.model = 'simpaths-mixed-'+secrets.token_hex(6)
        self.frontend_work = out/'frontend'
        self.frontend_log = (out/'frontend.log').open('w')
        self.worker_log = (out/'worker.log').open('w')
        self.rows = []

    def setup(self):
        import docker
        from deploy.multirun.queue_adapter import read_prepared, workspace_required_bytes
        from deploy.multirun.prepared_dataset import FORMAT, allocation, verify_snapshot
        from deploy.multirun.container_adapter import SimPathsContainerAdapter, container_submission
        from jasmine_web.batch.docker_executor import DockerExecutor
        from jasmine_web.batch.policy import Policy, Resources, RuntimeAllowance
        from jasmine_web.batch.store import Queue
        from jasmine_web.vm_resources import session_resources
        from jasmine_web.vm_state import PostgresVMState
        receipt = read_prepared(self.options.prepared)
        require(receipt['identity']['format'] == FORMAT, 'Only public Quick Start inputs are allowed in this proof')
        configuration = mixed_configuration(receipt)
        scratch_parent = self.options.work_root
        scratch_parent.mkdir(parents=True, exist_ok=True)
        self.report['host_before'] = common.host_resources()
        resource_check(self.report['host_before'], shutil.disk_usage(scratch_parent).free,
                       shutil.disk_usage(self.out).free)
        require(shutil.disk_usage(scratch_parent).free >= workspace_required_bytes(receipt['identity'])+3*GIB,
                'Temporary storage cannot hold the verified input copies plus 3 GiB of headroom')
        verify_snapshot(self.options.prepared, receipt)
        self.client = docker.DockerClient(base_url='unix:///var/run/docker.sock', timeout=15)
        self.client.ping()
        require_idle(self.client)
        entry = common.workflow.catalogue_model(common.workflow.catalogue_path(self.options.frontend, None),
                                                 'simpaths-quickstart-20000')
        image = self.client.images.get(entry['deployment']['image']).id
        batch_image = self.client.images.get(receipt['identity']['source_image']).id
        require(image == self.client.images.get(image).id, 'Interactive image identity differs')
        self.single_resources = session_resources(entry['deployment'], 4*GIB)
        self.batch_resources = allocation(receipt)
        require(self.single_resources == dict(cpu_millis=2000, memory_mib=4096, storage_mib=10240)
                and entry['deployment']['memory'] == '4Gi' and entry['deployment']['java_heap_gib'] == 2
                and self.batch_resources == dict(cpu_millis=2000, memory_mib=5120, storage_mib=10240),
                'Reviewed training allocations changed; review this test before running')
        self.capacity = {key: self.single_resources[key]+self.batch_resources[key] for key in RESOURCE_KEYS}
        self.scratch = Path(tempfile.mkdtemp(prefix=self.model+'-', dir=scratch_parent))
        self.report.update(model_id=self.model, scratch=str(self.scratch), capacity=self.capacity,
            single_resources=self.single_resources, batch_resources=self.batch_resources,
            interactive_image_id=image, batch_image_id=batch_image, prepared_fingerprint=receipt['sha256'],
            frontend_revision=subprocess.check_output(['git', '-C', str(self.options.frontend),
                                                       'rev-parse', 'HEAD'], text=True).strip(),
            simpaths_revision=subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip())
        common.copy_frontend(self.options.frontend, self.frontend_work)
        entry.update(id=self.model, name='Shared-pool acceptance Quick Start 20,000', requiresAuth=False)
        entry['deployment']['image'] = image
        (self.frontend_work/'models.json').write_text(json.dumps({'models': [entry]}))
        self.postgres, self.dsn_file = postgres_fixture.start_postgres(self.client, self.model,
            browser_tools.free_port(), self.frontend_work)
        self.queue = Queue(self.dsn_file.read_text().strip(), self.model)
        self.queue.migrate()
        self.queue.create_pool(Resources(**self.capacity), policy=Policy(per_user_active=1,
            per_user_unfinished=3, pool_unfinished=4, attempt_seconds=4500, total_seconds=13500,
            retry_delay_seconds=1, lease_seconds=60))
        binding = dict(pool_id=self.model, schema=self.queue.schema)
        state = PostgresVMState(self.queue.dsn, shared_pool=binding)
        try:
            state.migrate()
            self.deployment = state.deployment_id()
        finally:
            state.close()
        self.report['deployment_id'] = self.deployment
        arguments = container_submission(configuration, self.options.prepared, batch_image)
        self.queue.register_dataset(arguments['dataset_id'], receipt['sha256'])
        self.experiments = {}
        for owner in ('background', 'waiting'):
            self.queue.approve(owner)
            self.queue.grant_dataset(owner, arguments['dataset_id'])
        self.arguments = arguments
        self.allowance = RuntimeAllowance(900, 3600, 3)
        self.executor = DockerExecutor(self.scratch/'attempts', approved_images=[batch_image],
                                       input_roots=[self.options.prepared])
        self.adapter = SimPathsContainerAdapter(self.options.prepared, batch_image)
        self.env = common.isolated_environment(postgres_dsn_file=self.dsn_file)
        # Separate interactive caps are deliberately larger; common admission
        # must reject the third model, rather than an unrelated session-count cap.
        self.env.update(VM_MAX_SESSIONS='3', VM_MAX_SESSIONS_PER_CLIENT='3',
            VM_SESSION_MEMORY_BUDGET_MIB='12288', VM_SHARED_POOL_ID=self.model,
            VM_SHARED_BATCH_SCHEMA=self.queue.schema)
        self.port = browser_tools.free_port()
        self.base = f'http://127.0.0.1:{self.port}'
        self.worker_config = self.scratch/'worker-config.json'
        self.worker_config.write_text(json.dumps(dict(frontend=str(self.options.frontend),
            dsn_file=str(self.dsn_file), pool_id=self.model, schema=self.queue.schema,
            root=str(self.scratch/'attempts'), prepared=str(self.options.prepared), image=batch_image,
            status=str(self.scratch/'worker-status.json'))))
        self.inventory = MixedInventory(self.client)
        self.sampler = common.Sampler(self.inventory, self.model, self.out/'resources.jsonl')
        self.sampler.start()
        self.start_frontend()
        self.save()

    def start_frontend(self):
        import httpx
        self.process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host',
            '127.0.0.1', '--port', str(self.port)], cwd=self.frontend_work, env=self.env,
            stdout=self.frontend_log, stderr=subprocess.STDOUT)
        deadline = time.monotonic()+60
        with httpx.Client(trust_env=False, timeout=3) as http:
            while True:
                require(self.process.poll() is None, 'Test frontend exited; inspect frontend.log')
                try:
                    if http.get(self.base+'/health').status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                require(time.monotonic() < deadline, 'Test frontend did not become ready')
                time.sleep(.25)

    def start_worker(self):
        self.dispatcher = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
            '--worker-config', str(self.worker_config)], cwd=ROOT, env=self.env,
            stdout=self.worker_log, stderr=subprocess.STDOUT)

    def submit(self, owner):
        from jasmine_web.batch.policy import Resources
        experiment = self.queue.submit(owner, 'mixed-proof', **self.arguments,
            resources=Resources(**self.batch_resources), auto_retry=False, runtime_allowance=self.allowance)
        self.experiments[owner] = experiment
        return self.queue.inspect(owner, experiment)['jobs'][0]

    def background(self, *, active=True):
        snapshot = self.queue.inspect('background', self.experiments['background'])
        job = snapshot['jobs'][0]
        if active:
            require(job['state'] == 'active' and job['attempts'] == 1, 'Background job was stopped or duplicated')
        require(snapshot['specification'] == self.frozen, 'Background model settings or inputs changed')
        return job

    def alive(self):
        require(not self.sampler.low_disk, 'Root free space fell below 2 GiB; stopping the test')
        require(shutil.disk_usage(self.scratch).free >= 2*GIB,
                'Temporary workspace free space fell below 2 GiB; stopping the test')
        require(self.process.poll() is None, 'Test frontend stopped unexpectedly')
        if self.dispatcher:
            require(self.dispatcher.poll() is None, 'Test worker stopped; inspect worker.log')

    def cleanup(self):
        errors = []
        for service in ('dispatcher', 'process'):
            try:
                stop_process(getattr(self, service))
            except Exception as error:
                errors.append(service+': '+type(error).__name__)
        if self.sampler:
            try:
                self.sampler.close()
                self.report['resource_summary'] = common.summarize_samples(self.out/'resources.jsonl')
                self.report['resource_sampling_errors'] = self.sampler.errors
                require(not self.sampler.errors, 'Resource samples failed')
            except Exception as error:
                errors.append('sampling: '+type(error).__name__)
        model_errors = []
        if self.client:
            try:
                common.cleanup_models(self.client, self.model, self.deployment)
            except Exception as error:
                model_errors.append('interactive cleanup: '+type(error).__name__)
        if self.executor:
            try:
                with self.executor.exclusive():
                    for lease in job_leases(self.queue):
                        deadline = time.monotonic()+60
                        while True:
                            observed = self.executor.inspect(lease)
                            if observed['state'] == 'stopped':
                                break
                            self.executor.stop(lease, 'cancelled')
                            require(time.monotonic() < deadline, 'Batch stop could not be confirmed')
                            time.sleep(.25)
                        self.executor.cleanup(lease)
            except Exception as error:
                model_errors.append('batch cleanup: '+type(error).__name__)
        errors += model_errors
        # Keep state and private credentials if model removal is uncertain.
        # Never abandon a running container by deleting its execution journal.
        if not model_errors:
            database_removed = self.postgres is None
            if self.postgres:
                try:
                    self.postgres.reload()
                    require(self.postgres.labels.get('simpaths.acceptance') == self.model,
                            'Disposable database cleanup identity differs')
                    self.postgres.remove(force=True, v=True)
                    database_removed = True
                except Exception as error:
                    errors.append('database cleanup: '+type(error).__name__)
            if database_removed:
                if self.dsn_file:
                    self.dsn_file.unlink(missing_ok=True)
                if self.scratch:
                    shutil.rmtree(self.scratch)
            else:
                self.report['recovery_files_retained'] = True
        else:
            self.report['recovery_files_retained'] = True
        self.frontend_log.close()
        self.worker_log.close()
        if self.client:
            self.client.close()
        return errors


async def exercise(trial, passed):
    from playwright.async_api import async_playwright, expect
    from jasmine_web.batch.policy import fingerprint
    measurements = performance.BrowserMeasurements()
    trial.rows = measurements.rows
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1200, 'height': 800},
                                            extra_http_headers={'Origin': trial.base})
        outsider = await browser.new_context(extra_http_headers={'Origin': trial.base})
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
            containers = await asyncio.to_thread(trial.client.containers.list,
                filters={'label': 'jasmine.model_id='+trial.model})
            require(len(containers) == 1, 'Expected one isolated interactive model')
            model = containers[0]
            model.reload()
            require(common.is_owned(model.labels, trial.model, trial.deployment), 'Interactive model identity differs')
            require(model.attrs['HostConfig']['Memory'] == 4*GIB and
                    model.attrs['HostConfig']['NanoCpus'] == 2_000_000_000, 'Interactive Docker limits differ')
            await asyncio.to_thread(browser_tools.assert_private_network, model, trial.client)
            trial.report.update(session_id=sid, interactive_container_id=model.id)

            async def request(method, path, data=None):
                trial.alive()
                response = await context.request.fetch(trial.base+path, method=method, data=data,
                                                       timeout=330000, max_redirects=0)
                require(response.ok, 'Interactive control failed: HTTP '+str(response.status))
                value = await response.json()
                require(isinstance(value, dict) and not value.get('error'), 'Interactive control returned an error')
                return value

            async def wait_state(wanted, seconds=60):
                deadline = time.monotonic()+seconds
                while True:
                    value = await request('GET', '/status/'+sid)
                    if value.get('status') == wanted:
                        return value
                    require(time.monotonic() < deadline, 'Interactive state did not become '+wanted)
                    await asyncio.sleep(.25)

            def allocation(attempt_id=None, *, interactive=True):
                value = ledger(trial.queue)
                require_allocation(value, trial.single_resources, trial.batch_resources,
                    sid=sid if interactive else None, attempt_id=attempt_id)
                return value

            await page.locator('[name="endYear"]').fill('2026')
            await page.locator('[name="randomSeedIfFixed"]').fill('606')
            await page.locator('[name="fixRandomSeed"]').check()
            params = await page.evaluate('() => parseFormParams(document.getElementById("param-form"))')
            require(int(params['popSize']) == 20000 and int(params['startYear']) == 2019
                    and int(params['endYear']) == 2026, 'Interactive training profile differs')
            await request('POST', '/build-json/'+sid, params)
            deadline = time.monotonic()+900
            while True:
                status = await request('GET', '/status/'+sid)
                require(status.get('status') not in ('build_error', 'build_rejected', 'parameters_rejected', 'offline'),
                        'Interactive Build failed')
                if status.get('built'):
                    break
                require(time.monotonic() < deadline, 'Interactive Build exceeded 15 minutes')
                await asyncio.sleep(.5)
            await expect(page.locator('.js-plotly-plot').first).to_be_visible(timeout=60000)
            require(allocation()['used'] == trial.single_resources, 'SingleRun did not reserve the shared pool')
            await request('POST', '/java/'+sid+'/simulation/speed', {'speed': 0})
            await request('POST', '/start-sim/'+sid)
            await wait_state('running')

            async def sample(phase, target, *, batch=False):
                measurements.phase = trial.sampler.phase = phase
                until = time.monotonic()+max(90, target*1.5)
                while measurements.chart_count(phase) < target:
                    trial.alive()
                    require(time.monotonic() < until, 'Populated chart sample target was not reached')
                    if batch:
                        trial.background()
                    require((await request('GET', '/status/'+sid))['status'] == 'running',
                            'Interactive simulation finished before the mixed measurement')
                    await asyncio.sleep(.5)
                await measurements.drain()
                selected = [row for row in measurements.rows if row['phase'] == phase]
                require(not performance.summarize(selected)['failures'], 'Polling failed during '+phase)
                require(await page.evaluate('() => [...document.querySelectorAll(".js-plotly-plot")].some(p => '
                    '(p.data || []).some(t => (t.x || []).length || (t.y || []).length))'),
                    'Browser did not draw populated simulation charts')

            await sample('running-alone', trial.options.baseline_charts)
            await request('POST', '/pause/'+sid)
            await wait_state('paused')
            passed('interactive simulation and populated charts measured without a batch model')
            await asyncio.to_thread(trial.submit, 'background')
            trial.frozen = trial.queue.inspect('background', trial.experiments['background'])['specification']
            trial.start_worker()
            deadline = time.monotonic()+300
            batch = lease = None
            while True:
                trial.alive()
                leases = await asyncio.to_thread(job_leases, trial.queue)
                if leases:
                    require(len(leases) == 1, 'Unexpected extra MultiRun attempt')
                    lease = leases[0]
                    try:
                        batch = await asyncio.to_thread(trial.client.containers.get, 'jasmine-'+lease.execution_key)
                    except __import__('docker').errors.NotFound:
                        batch = None
                    if batch:
                        text = await asyncio.to_thread(batch.logs, tail=10000)
                        if b'Found processed dataset - preparing for simulation' in text:
                            break
                require(time.monotonic() < deadline, 'Real background model did not start within five minutes')
                await asyncio.sleep(.5)
            trial.batch_lease = lease
            trial.inventory.batch_identity = fingerprint(trial.executor._identity(lease))
            batch.reload()
            require(batch.attrs['HostConfig']['Memory'] == 5*GIB and
                    batch.attrs['HostConfig']['NanoCpus'] == 2_000_000_000 and
                    batch.attrs['HostConfig']['NetworkMode'] == 'none', 'Batch Docker limits or network differ')
            require(batch.labels.get('jasmine.batch.identity') == trial.inventory.batch_identity,
                    'Batch identity differs')
            trial.report.update(batch_container_id=batch.id, attempt_id=lease.attempt_id,
                                experiment_id=trial.experiments['background'])
            passed('real interactive and MultiRun containers share one bounded CPU, RAM and storage pool',
                   ledger=allocation(lease.attempt_id))
            waiter = await asyncio.to_thread(trial.submit, 'waiting')
            await asyncio.sleep(2)
            waiting = trial.queue.inspect('waiting', trial.experiments['waiting'])['jobs'][0]
            require(waiting['state'] == 'queued' and waiting['attempts'] == 0, 'Extra batch work bypassed common admission')
            other_page = await outsider.new_page()
            await other_page.goto(trial.base)
            await other_page.locator('.model-card').click()
            await expect(other_page.locator('body')).to_contain_text('insufficient shared', timeout=60000)
            require(len(await asyncio.to_thread(trial.client.containers.list,
                filters={'label': 'jasmine.model_id='+trial.model})) == 1, 'Rejected launch created a model')
            allocation(lease.attempt_id)
            trial.queue.cancel('waiting', waiter['id'])
            require(trial.queue.inspect('waiting', trial.experiments['waiting'])['jobs'][0]['state'] == 'cancelled',
                    'Unstarted queue probe could not be cancelled')
            await other_page.close()
            passed('full shared pool rejects another interactive model and keeps another owner\'s batch job unstarted')

            await request('POST', '/start-sim/'+sid)
            await wait_state('running')
            await sample('mixed-running', trial.options.mixed_charts, batch=True)
            await page.screenshot(path=str(trial.out/'mixed-running.png'), timeout=15000)
            passed('interactive status, populated charts and logs remain available during real batch CPU and disk load',
                   overlap=cpu_overlap(trial.out/'resources.jsonl', (model.id, batch.id)))
            await request('POST', '/pause/'+sid)
            await wait_state('paused')
            before = trial.background()
            allocation(lease.attempt_id)
            trial.sampler.phase = measurements.phase = 'worker-recovery'
            await asyncio.to_thread(stop_process, trial.dispatcher)
            allocation(lease.attempt_id)
            await asyncio.to_thread(batch.reload)
            require(batch.status == 'running', 'Stopping dispatcher stopped its model')
            trial.start_worker()
            until = time.monotonic()+90
            while True:
                trial.alive()
                recovered = (await asyncio.to_thread(job_leases, trial.queue))[0]
                if recovered.generation > lease.generation and recovered.phase == 'running':
                    break
                require(time.monotonic() < until, 'Restarted worker did not adopt the existing attempt')
                allocation(lease.attempt_id)
                await asyncio.sleep(.5)
            require(recovered.attempt_id == lease.attempt_id and recovered.execution_key == lease.execution_key,
                    'Recovery created another attempt or workspace')
            require(trial.background()['history'][0]['id'] == before['history'][0]['id'], 'Recovery changed attempt history')
            trial.batch_lease = recovered
            await asyncio.to_thread(require_original_container, trial.client,
                'jasmine.batch.identity='+trial.inventory.batch_identity, batch.id)
            allocation(lease.attempt_id)
            passed('restarted queue worker adopts the same running container and retains its resource reservation')

            measurements.phase = 'closing'
            await measurements.settle()
            await page.goto('about:blank')
            await asyncio.to_thread(stop_process, trial.process)
            allocation(lease.attempt_id)
            trial.background()
            await asyncio.to_thread(trial.start_frontend)
            measurements.phase = trial.sampler.phase = 'frontend-recovery'
            await page.goto(trial.base+'/sim/'+sid)
            await expect(page.locator('#param-form')).to_be_visible(timeout=60000)
            require((await request('GET', '/status/'+sid))['built'], 'Frontend restart lost the interactive model')
            current = await request('GET', '/java/'+sid+'/simulation/current-params')
            values = {p['name']: p['value'] for p in current['modelParameters']}
            require(int(values['randomSeedIfFixed']) == 606 and int(values['endYear']) == 2026,
                    'Frontend restart lost chosen parameters')
            await asyncio.to_thread(require_original_container, trial.client, 'jasmine.model_id='+trial.model, model.id)
            trial.background()
            allocation(lease.attempt_id)
            passed('frontend restart preserves browser ownership, interactive model, parameters and both reservations')
            await page.screenshot(path=str(trial.out/'frontend-recovered.png'), timeout=15000)

            measurements.phase = trial.sampler.phase = 'interactive-completion'
            final, elapsed, highest = await performance.run_to_completion(request, sid,
                end_year=2026, timeout=trial.options.timeout)
            trial.report.update(interactive_remaining_run_seconds=round(elapsed, 3),
                interactive_final_time=final['time'], interactive_highest_observed_time=highest)
            trial.background()
            allocation(lease.attempt_id)
            # Preserve the complete own-output checksum through ordinary Reset.
            listing = await request('GET', '/java/'+sid+'/simulation/export/list')
            files = [f for f in listing.get('files', []) if f.get('name') == 'HealthStatistics.csv' and int(f.get('size', 0))]
            require(files, 'Interactive model has no annual summary output')
            item = sorted(files, key=lambda f: f['timestamp'])[-1]
            url = trial.base+'/java/'+sid+'/simulation/export/download?'+urlencode(
                dict(timestamp=item['timestamp'], path=item['path'], zip='never'))
            response = await context.request.get(url, timeout=60000)
            require(response.ok, 'Interactive summary download failed')
            raw = await response.body()
            import hashlib
            complete_summary = performance.summary_evidence(raw, start_year=2019, end_year=2026)
            summary_hash = hashlib.sha256(raw).hexdigest()
            denied = await outsider.request.get(url, timeout=30000, max_redirects=0)
            require(denied.status in (401, 403, 404), 'Another browser owner could read interactive output')
            (trial.out/'interactive-HealthStatistics.csv').write_bytes(raw)
            passed('interactive simulation finishes all eight years during batch work; another owner cannot download its output',
                   **complete_summary)
            await request('POST', '/reset/'+sid)
            response = await context.request.get(url, timeout=60000)
            require(response.ok and hashlib.sha256(await response.body()).hexdigest() == summary_hash,
                    'Ordinary Reset changed the saved interactive output')
            trial.background()
            allocation(lease.attempt_id)
            logs = await asyncio.to_thread(model.logs, tail=20000)
            (trial.out/'interactive-java.log').write_bytes(logs)
            require(b'Processor: error processing' not in logs, 'Interactive Java chart-processing error')
            response = await context.request.post(trial.base+'/leave/'+sid, max_redirects=0, timeout=120000)
            require(response.status == 303 and response.headers.get('location') == '/', 'Interactive Leave failed')
            require(not await asyncio.to_thread(trial.client.containers.list,
                all=True, filters={'label': 'jasmine.model_id='+trial.model}), 'Leave did not remove the interactive model')
            trial.background()
            passed('Reset preserves saved output; Leave releases only interactive capacity while MultiRun continues',
                   ledger=allocation(lease.attempt_id, interactive=False), interactive_output_sha256=summary_hash)
            measurements.phase = 'closing'
            await measurements.settle()
            await page.goto('about:blank')

            trial.sampler.phase = 'background-completion'
            until = time.monotonic()+trial.options.timeout
            next_notice = 0
            while True:
                trial.alive()
                finished = trial.background(active=False)
                if finished['state'] == 'succeeded':
                    break
                require(finished['state'] == 'active', 'Background simulation did not finish successfully')
                require(time.monotonic() < until, 'Background simulation exceeded the acceptance timeout')
                if time.monotonic() >= next_notice:
                    print('Waiting for the 50,000-person background simulation to finish', flush=True)
                    next_notice = time.monotonic()+30
                await asyncio.sleep(1)
            require(finished['attempts'] == 1 and len(finished['history']) == 1,
                    'Successful background configuration used extra attempts')
            repetitions = await asyncio.to_thread(trial.adapter.validate, trial.batch_lease,
                trial.executor.workspace(trial.batch_lease)/'work')
            require([r['seed'] for r in repetitions] == trial.frozen['seeds'] == ['606'], 'Native seed differs from the frozen plan')
            with trial.queue._connection() as c:
                actual = c.execute('SELECT actual_seed,output_fingerprint FROM repetitions WHERE attempt_id=%s ORDER BY ordinal',
                                   (lease.attempt_id,)).fetchall()
            require(actual == [dict(actual_seed=r['seed'], output_fingerprint=r['fingerprint']) for r in repetitions],
                    'Database completion hashes differ from the retained scientific files')
            workspace = trial.executor.workspace(trial.batch_lease)
            summaries = list((workspace/'work/output').glob('*/csv/HealthStatistics.csv'))
            require(len(summaries) == 1, 'Background annual summary missing or duplicated')
            summary = performance.summary_evidence(summaries[0].read_bytes(), start_year=2019, end_year=2026)
            (trial.out/'background-HealthStatistics.csv').write_bytes(summaries[0].read_bytes())
            execution_log = (workspace/'execution.log').read_bytes()
            (trial.out/'background-java.log').write_bytes(execution_log)
            require(b'Processor: error processing' not in execution_log, 'Background Java chart-processing error')
            from deploy.multirun.prepared_dataset import verify_snapshot
            await asyncio.to_thread(verify_snapshot, trial.options.prepared, trial.adapter.receipt)
            passed('completed MultiRun retains one attempt, frozen inputs and seed, all eight annual rows and verified output hashes',
                   repetitions=repetitions, **summary, ledger=allocation(interactive=False))
            trial.report['poll_response_times'] = timing_summary(measurements.rows)
            for phase, endpoints in trial.report['poll_response_times'].items():
                for endpoint, value in endpoints.items():
                    require(value['measured'] > 0 and value['failures'] == 0, 'Missing or failed '+phase+' '+endpoint+' samples')
            require(not trial.report['page_errors'], 'Uncaught browser exceptions')
            passed('measured browser requests and drawn charts have no polling or uncaught JavaScript errors')
        finally:
            measurements.phase = 'closing'
            await browser.close()
            await measurements.drain()
            trial.report['poll_response_times'] = timing_summary(measurements.rows)
            (trial.out/'browser-timings.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in measurements.rows))


def worker_main(path):
    """A separate dispatcher process; SIGTERM releases its lock, never its model."""
    config = json.loads(Path(path).read_text())
    sys.path[:0] = [str(ROOT), config['frontend']]
    from deploy.multirun.container_adapter import SimPathsContainerAdapter
    from jasmine_web.batch.docker_executor import DockerExecutor
    from jasmine_web.batch.local_executor import atomic_json
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.worker import Worker
    queue = Queue(Path(config['dsn_file']).read_text().strip(), config['pool_id'], schema=config['schema'])
    executor = DockerExecutor(config['root'], approved_images=[config['image']], input_roots=[config['prepared']])
    adapter = SimPathsContainerAdapter(config['prepared'], config['image'])
    worker = Worker(queue, executor, adapter, 'mixed-worker-'+secrets.token_hex(6))
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    with worker.open():
        while not stopping:
            worker.tick()
            atomic_json(Path(config['status']), dict(worker=worker.worker_id,
                attempts=[dict(id=lease.attempt_id, generation=lease.generation) for lease in worker.leases.values()]))
            time.sleep(.25)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=common.workflow.frontend_path())
    parser.add_argument('--prepared', type=Path, help='Verified public Quick Start 50,000-person snapshot')
    parser.add_argument('--output', type=Path, help='New private evidence directory')
    parser.add_argument('--work-root', type=Path, default=Path(tempfile.gettempdir()),
                        help='Filesystem for temporary MultiRun copies; removed after confirmed cleanup')
    parser.add_argument('--baseline-charts', type=int, default=60)
    parser.add_argument('--mixed-charts', type=int, default=180)
    parser.add_argument('--timeout', type=int, default=1800, help='Seconds to await background completion after Leave')
    parser.add_argument('--worker-config', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_config:
        try:
            return worker_main(args.worker_config)
        except Exception as error:
            print('Test dispatcher failed:', type(error).__name__, flush=True)
            return 1
    if not args.prepared or not args.output:
        parser.error('--prepared and --output are required')
    if args.baseline_charts < 20 or args.mixed_charts < 60 or not 300 <= args.timeout <= 4500:
        parser.error('Use at least 20 baseline / 60 mixed chart samples and a 300–4500 second timeout')
    for key in ('frontend', 'prepared', 'output', 'work_root'):
        setattr(args, key, getattr(args, key).expanduser().resolve())
    sys.path[:0] = [str(ROOT), str(args.frontend)]
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Private mixed-load acceptance evidence and copied frontend snapshot.\n@author ross richardson\n-->\n')
    report = dict(status='running', checks=[], page_errors=[], state_backend='postgres',
        scope='Real 20k interactive / 50k single-repetition MultiRun; 2019–2026; development networking',
        timing_scope='One active SingleRun before and during batch load; different years, not a matched slowdown estimate',
        storage_scope='Logical common admission and free-space guards; not a physical host quota or public firewall proof',
        scientific_reproducibility='Checks native settings, seeds, annual completion and hashes; existing receipt-flag RNG issue not resolved')

    def save():
        (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')

    def passed(name, **evidence):
        report['checks'].append(dict(name=name, status='passed', **evidence))
        save()
        print('PASS:', name, flush=True)

    def stop(signum, frame):
        raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    trial = Trial(args, args.output, report, save)
    try:
        trial.setup()
        asyncio.run(exercise(trial, passed))
        report['status'] = 'passed'
    except (Exception, KeyboardInterrupt) as error:
        report['status'] = 'failed'
        report['error'] = str(error) if isinstance(error, AssertionError) else type(error).__name__
        print('STOPPED:', report['error'], '; inspect the private report and logs', flush=True)
    finally:
        try:
            errors = trial.cleanup()
        except Exception as error:
            errors = ['cleanup: '+type(error).__name__]
        if errors:
            report['cleanup_errors'] = errors
            report['status'] = 'failed'
        save()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print(('PASSED:' if report['status'] == 'passed' else 'FAILED:'), args.output/'report.json', flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
