#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Exercise UK user-data uploads, preparation and repeated Builds using public examples.
Owns only its newly created session. Does not use an AI provider or send data to one.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shlex
import sys
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlsplit
from playwright.sync_api import sync_playwright, expect

import runpy
load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
workflow = load_tool('_workflow.py')

GIB = 1024**3
TRIAL_MODEL = re.compile(r'simpaths-acceptance-[a-f0-9]{16}-user-data\Z')
TRIAL_PROFILE = dict(popSize=50000, startYear=2019, endYear=2026,
                     randomSeedIfFixed=606, fixRandomSeed=True)


@contextmanager
def timed_stage(report, stage):
    """Record elapsed time and both UTC boundaries, including failed stages."""
    began = monotonic()
    record = dict(stage=stage, started=datetime.now(timezone.utc).isoformat(), status='running')
    report.setdefault('stages', []).append(record)
    try:
        yield record
        record['status'] = 'passed'
    except BaseException:
        record['status'] = 'failed'
        raise
    finally:
        record.update(finished=datetime.now(timezone.utc).isoformat(), seconds=monotonic()-began)


def checked_json(response, operation):
    if not response.ok:
        raise AssertionError(f'{operation} failed: HTTP {response.status}: {response.text()}')
    result = response.json()
    if not isinstance(result, dict) or result.get('error'):
        raise AssertionError(f'{operation} returned an error: {result}')
    return result


def configure_trial_container(client, model_id, sid, preparation_heap_mib):
    """Change swap only after exact ownership, workload and resource checks."""
    if not TRIAL_MODEL.fullmatch(model_id) or preparation_heap_mib not in (3072, 4096):
        raise ValueError('Memory trial requires a unique acceptance model and supported preparation heap')
    containers = client.containers.list(all=True, filters={'label': 'jasmine.model_id='+model_id})
    if len(containers) != 1:
        raise AssertionError('Expected exactly one container with the trial model label')
    container = containers[0]
    container.reload()
    attrs = container.attrs
    labels = attrs.get('Config', {}).get('Labels', {})
    if labels.get('jasmine.model_id') != model_id or labels.get('jasmine.session_id') != sid:
        raise AssertionError('Trial container does not belong to the launched session')
    host = attrs.get('HostConfig', {})
    if host.get('Memory') != 5*GIB or host.get('NanoCpus') != 2_000_000_000:
        raise AssertionError('Trial requires a fixed 5 GiB container and two CPUs')
    env = dict(item.split('=', 1) for item in attrs.get('Config', {}).get('Env', []) if '=' in item)
    heap_options = [arg for arg in shlex.split(env.get('JAVA_OPTS', '')) if arg.startswith('-Xmx')]
    if heap_options not in (['-Xmx4g'], ['-Xmx4096m']):
        raise AssertionError('Trial application maximum heap must be 4 GiB')
    if env.get('SIMPATHS_PREPARATION_HEAP_MIB') != str(preparation_heap_mib):
        raise AssertionError('Preparation heap differs from the selected trial')
    container.update(memswap_limit=5*GIB)
    container.reload()
    host = container.attrs.get('HostConfig', {})
    if any(host.get(key) != value for key, value in
           dict(Memory=5*GIB, MemorySwap=5*GIB, NanoCpus=2_000_000_000).items()):
        raise AssertionError('Trial container limits or disabled swap could not be verified')
    return container


def java_processes(container, preparation_heap_mib):
    """Verify effective Java command heaps; retain only PID/role, never command lines."""
    table = container.top(ps_args='-eo pid,args')
    titles = [str(value).upper() for value in table.get('Titles', [])]
    if 'PID' not in titles or not any(key in titles for key in ('COMMAND', 'CMD', 'ARGS')):
        raise AssertionError('Container process list lacks PID and command columns')
    command_column = next(titles.index(key) for key in ('COMMAND', 'CMD', 'ARGS') if key in titles)
    pid_column = titles.index('PID')
    result = []
    for row in table.get('Processes', []):
        command = shlex.split(row[command_column])
        if not command or Path(command[0]).name != 'java':
            continue
        worker = 'simpaths.experiment.SimPathsUserDataPreparation' in command
        expected_mib = preparation_heap_mib if worker else 4096
        heaps = [arg for arg in command if arg.startswith('-Xmx')]
        allowed = (f'-Xmx{expected_mib}m', f'-Xmx{expected_mib//1024}g')
        if len(heaps) != 1 or heaps[0] not in allowed:
            raise AssertionError('Effective preparation or application Java heap differs from the trial')
        result.append(dict(pid=int(row[pid_column]), role='preparation' if worker else 'application',
                           heap_mib=expected_mib))
    if len([item for item in result if item['role'] == 'application']) != 1:
        raise AssertionError('Expected exactly one application Java process')
    if len([item for item in result if item['role'] == 'preparation']) > 1:
        raise AssertionError('Multiple preparation workers unexpectedly overlap')
    return result


def verify_trial_profile(values):
    for key, expected in TRIAL_PROFILE.items():
        actual = values.get(key)
        if key == 'fixRandomSeed':
            valid = actual is True or str(actual).lower() == 'true'
        else:
            valid = str(actual) == str(expected)
        if not valid:
            raise AssertionError(f'Trial parameter {key} differs from the fixed workload')


def set_trial_profile(page):
    for key, value in TRIAL_PROFILE.items():
        field = page.locator(f'[name="{key}"]')
        if key == 'fixRandomSeed':
            field.check()
        elif field.is_enabled():
            field.fill(str(value))
        else:
            expect(field).to_have_value(str(value))
    values = page.evaluate("() => parseFormParams(document.getElementById('param-form'))")
    verify_trial_profile(values)
    return values


def capture_trial_sample(sampler):
    if sampler.capture() is None:
        raise AssertionError('Could not verify the running container memory limits and telemetry')


def stop_trial_sampling(sampler, report):
    """Always stop the collector before session cleanup, preserving the primary error."""
    try:
        capture_trial_sample(sampler)
    except Exception as error:
        report['telemetry_error'] = str(error)
    try:
        report['memory'] = sampler.stop()
        if not report['memory']['passed']:
            report.setdefault('telemetry_error', 'Memory sampling evidence is incomplete or invalid')
    except Exception as error:
        report.setdefault('telemetry_error', str(error))
    if report['status'] == 'passed' and 'telemetry_error' in report:
        report.update(status='failed', error='Memory trial telemetry failed; see report details.')


def run_to_completion(page, base, sid, run, report, timeout_seconds=14400, poll_hook=None):
    """Require final cleanup; an intermediate pause or first CSV is insufficient."""
    prefix = base.rstrip('/')
    checked_json(page.request.post(prefix+'/java/'+sid+'/simulation/speed', data={'speed': 0}), 'Full-speed setting')
    started = checked_json(page.request.post(prefix+'/start-sim/'+sid, timeout=330000), 'Simulation Start')
    if started.get('status') != 'started':
        raise AssertionError('Simulation Start was not acknowledged')
    began, highest = monotonic(), 0
    next_check = began
    while monotonic()-began < timeout_seconds:
        if poll_hook is not None and monotonic() >= next_check:
            poll_hook()
            next_check = monotonic()+10
        status = checked_json(page.request.get(prefix+'/status/'+sid, timeout=30000), 'Simulation status')
        report['last_simulation_status'] = status
        if status.get('status') not in ('running', 'paused'):
            raise AssertionError(f'Build {run}: simulation failed: {status}')
        clock = float(status.get('time', 0))
        if not math.isfinite(clock):
            raise AssertionError('Simulation returned a nonfinite time')
        if status['status'] == 'paused' and status.get('built') is True and (
                clock >= 2027 or clock == 0 and highest >= 2026):
            report.setdefault('full_runs', []).append(dict(run=run, final_status=status,
                highest_running_time=highest, seconds=monotonic()-began))
            return
        if status['status'] == 'running':
            highest = max(highest, clock)
        elif monotonic()-began >= 10:
            raise AssertionError(f'Build {run}: simulation stopped before completing its final year')
        page.wait_for_timeout(500)
    raise AssertionError(f'Build {run}: simulation exceeded {timeout_seconds}s')


def annual_summary(raw):
    rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig')), strict=True))
    if not rows or rows[0].count('time') != 1 or any(len(row) != len(rows[0]) for row in rows[1:]):
        raise AssertionError('Annual summary has missing or malformed time data')
    column = rows[0].index('time')
    years = sorted(float(row[column]) for row in rows[1:])
    if years != list(range(2019, 2027)):
        raise AssertionError('Annual summary must contain exactly one row for every year 2019–2026')
    return dict(years=years, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def verify_full_output(page, base, sid, run, out, report):
    prefix = base.rstrip('/')+'/java/'+sid+'/simulation/export'
    files = checked_json(page.request.get(prefix+'/list', timeout=60000), 'Output listing').get('files', [])
    latest = max((item['timestamp'] for item in files), default=None)
    summaries = [item for item in files if item['timestamp'] == latest and item.get('name') == 'HealthStatistics.csv']
    if len(summaries) != 1 or not 0 < int(summaries[0].get('size', 0)) <= 8*1024**2:
        raise AssertionError('Current run lacks a bounded, nonempty annual summary')
    item = summaries[0]
    response = page.request.get(prefix+'/download?'+urlencode(dict(timestamp=latest, path=item['path'], zip='never')),
                                timeout=60000)
    if not response.ok:
        raise AssertionError(f'Annual summary download failed: HTTP {response.status}')
    raw = response.body()
    evidence = annual_summary(raw)
    (out/f'full-run-{run}-HealthStatistics.csv').write_bytes(raw)
    report.setdefault('annual_outputs', []).append(dict(run=run, timestamp=latest, **evidence))
    report['checks'].append(f'Build {run}: full 50,000-person simulation and all eight annual summary rows verified')


def wait_for_session(page, report):
    """Stop on either a session redirect or the catalogue's launch error."""
    def launch_finished(url):
        parsed = urlsplit(url)
        return parsed.path.startswith('/sim/') or 'error' in parse_qs(parsed.query, keep_blank_values=True)

    page.wait_for_url(launch_finished, timeout=180000, wait_until='domcontentloaded')
    parsed = urlsplit(page.url)
    errors = parse_qs(parsed.query, keep_blank_values=True).get('error')
    if errors is not None:
        message = errors[0] or 'Server rejected the launch without an explanation'
        report['launch_error'] = message
        raise AssertionError(f'Session launch rejected: {message}')
    return parsed.path.rstrip('/').split('/')[-1]


def step_until_person_export(page, base, sid, run, report, max_steps=20):
    """A Step executes a scheduled event, not necessarily the collector export."""
    files = []
    for step in range(1, max_steps + 1):
        with page.expect_response(lambda r: '/step/' in r.url or '/step-sim/' in r.url,
                                  timeout=330000) as response:
            page.locator('#step-btn').click()
        assert response.value.ok, response.value.text()
        expect(page.locator('#step-btn')).to_be_enabled(timeout=330000)
        listing = page.request.get(base.rstrip('/')+'/java/'+sid+'/simulation/export/list', timeout=60000)
        assert listing.ok, listing.text()
        data = listing.json()
        assert not data.get('error'), data
        files = data.get('files', [])
        latest = max((f['timestamp'] for f in files), default=None)
        matches = [f for f in files if f['timestamp'] == latest and f.get('name') == 'Person.csv']
        if any(int(f.get('size', 0)) > 0 for f in matches):
            report.setdefault('exports', []).append({'run': run, 'steps': step,
                'timestamp': latest, 'files': matches})
            report['checks'].append(f'Build {run}: Person CSV export written after {step} scheduled steps')
            return
    raise AssertionError(f'Build {run}: Person.csv remained empty or missing after {max_steps} steps; '
                         f'latest run: {latest}')


def download_run_output(page, out, report, label, run_index, filename='Person.csv'):
    """Download real microdata through Output, independently of AI permission."""
    page.locator('#export-btn').click()
    runs = page.locator('#export-modal details')
    expect(runs).to_have_count(2, timeout=60000)
    run = runs.nth(run_index)
    if run.get_attribute('open') is None:
        run.locator('summary').click()
    with page.expect_download(timeout=60000) as pending:
        run.locator(f'button.export-file-download[data-path$="{filename}"]').first.click()
    download = pending.value
    assert download.failure() is None, download.failure()
    target = out / f'{label}-{filename}'
    download.save_as(target)
    with target.open(encoding='utf-8-sig', newline='') as stream:
        rows = csv.reader(stream)
        header, first = next(rows, []), next(rows, [])
        assert len(header) > 1 and len(first) == len(header), 'Expected CSV header and data record'
    if filename == 'HealthStatistics.csv':
        annual_summary(target.read_bytes())
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report.setdefault('downloads', []).append({'phase': label, 'run_index': run_index,
        'filename': download.suggested_filename, 'bytes': target.stat().st_size, 'sha256': digest})
    page.locator('#export-close-btn').click()
    return digest


def check_ai_consent(page, context, base, out, report):
    """Exercise real controls with a dummy key and synthetic chat; no provider calls."""
    unexpected = []
    origin = urlsplit(base).netloc

    def isolate_ai(route):
        url = urlsplit(route.request.url)
        if url.netloc == origin:
            route.continue_()
        elif url.hostname == 'openrouter.ai' and url.path == '/api/v1/models' and route.request.method == 'GET':
            route.fulfill(json={'data': []})
        else:
            unexpected.append({'method': route.request.method, 'url': route.request.url})
            route.abort()

    # Installed before opening the panel. Only the frontend may be contacted;
    # the provider's model catalogue is fulfilled locally, never fetched.
    context.route('**/*', isolate_ai)
    try:
        page.evaluate("""() => {
            setAiProvider('openrouter');
            sessionStorage.setItem(aiKeyName(), 'acceptance-test-dummy-key');
        }""")
        page.locator('#ai-assistant-btn').click()
        button = page.locator('#ai-data-permission')
        expect(button).to_be_enabled(timeout=60000)

        def assert_access(enabled):
            state = page.evaluate("""() => ({user: aiModelDetailedDataAllowed,
                ai: aiDetailedDataAccessAllowed, consent: aiConsentEnabled(),
                detailedTools: aiToolDeclarationList().some(tool => tool.name === 'run_sql')})""")
            assert state == {'user': True, 'ai': enabled, 'consent': enabled, 'detailedTools': enabled}, state

        def confirm_toggle(accept, warning):
            messages = []
            def answer(dialog):
                messages.append(dialog.message)
                dialog.accept() if accept else dialog.dismiss()
            page.once('dialog', answer)
            button.click()
            assert len(messages) == 1 and warning in messages[0], messages

        assert_access(False)
        confirm_toggle(False, 'selected AI provider')
        assert_access(False)
        confirm_toggle(True, 'selected AI provider')
        assert_access(True)
        # Seed synthetic conversation data rather than sending a provider prompt.
        page.evaluate("""() => {
            aiTranscript.push({role:'assistant', text:'Synthetic acceptance conversation'});
            saveAiTranscript(); renderAiTranscript();
        }""")
        page.locator('#ai-input').fill('Unsent acceptance draft')
        confirm_toggle(False, 'restart the assistant conversation')
        assert_access(True)
        expect(page.locator('#ai-chat')).to_contain_text('Synthetic acceptance conversation')
        expect(page.locator('#ai-input')).to_have_value('Unsent acceptance draft')
        page.locator('#ai-close-dialog-btn').click()
        page.locator('#ai-assistant-btn').click()
        expect(button).to_be_enabled(timeout=60000)
        assert_access(True)
        expect(page.locator('#ai-chat')).to_contain_text('Synthetic acceptance conversation')
        confirm_toggle(True, 'restart the assistant conversation')
        assert_access(False)
        expect(page.locator('#ai-chat')).not_to_contain_text('Synthetic acceptance conversation')
        expect(page.locator('#ai-input')).to_have_value('')
        cleared = page.evaluate("""() => ({transcript: aiTranscript.length,
            saved: sessionStorage.getItem(aiTranscriptStorageKey()), draft: aiInputDraft,
            native: Object.keys(aiNativeStates).length, retry: aiPendingEditRetry})""")
        assert cleared == {'transcript': 0, 'saved': None, 'draft': '', 'native': 0, 'retry': None}, cleared
        page.locator('#ai-close-dialog-btn').click()
        page.locator('#ai-assistant-btn').click()
        expect(button).to_be_enabled(timeout=60000)
        assert_access(False)
        expect(page.locator('#ai-chat')).not_to_contain_text('Synthetic acceptance conversation')
        page.screenshot(path=str(out/'ai-revoked.png'), full_page=True)
        page.locator('#ai-close-dialog-btn').click()
        report['checks'].append('AI consent cancellation, opt-in, cancelled revocation, conversation clearing and permission persistence on reopen')
    finally:
        context.unroute('**/*', isolate_ai)
        report['unexpected_ai_network_requests'] = unexpected
    assert not unexpected, unexpected


def build_and_wait(page, base, sid, run, report, timeout_seconds=1200):
    """Check admission and stop promptly on explicit build rejection or failure."""
    storage_response = page.request.get(base.rstrip('/')+'/storage/'+sid)
    if not storage_response.ok:
        raise AssertionError(f'Build {run}: storage check returned HTTP {storage_response.status}')
    storage = storage_response.json()
    report.setdefault('builds', []).append({'run': run, 'storage_before': storage})
    evidence = report['builds'][-1]
    if storage.get('buildBlocked'):
        free = storage.get('remainingBytes', 0) / (1024**3)
        raise AssertionError(f'Build {run} blocked by insufficient storage ({free:.2f} GiB available). '
                             'Free disk space before retrying; see storage_before in report.json.')
    with page.expect_response(lambda r: '/build-json/'+sid in r.url, timeout=30000) as accepted:
        page.locator('#toolbar-build-btn').click()
    response = accepted.value
    if not response.ok:
        raise AssertionError(f'Build {run} request failed: HTTP {response.status}: {response.text()}')
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = page.request.get(base.rstrip('/')+'/status/'+sid, timeout=30000)
        if not response.ok:
            raise AssertionError(f'Build {run} status request failed: HTTP {response.status}: {response.text()}')
        status = response.json()
        evidence['last_status'] = status
        if status.get('status') in ('build_rejected', 'parameters_rejected', 'build_error'):
            raise AssertionError(f'Build {run}: {status.get("error") or status.get("display") or status}')
        if status.get('built') is True and page.locator('#start-btn').is_enabled():
            return
        page.wait_for_timeout(1000)
    raise AssertionError(f'Build {run} did not complete within {timeout_seconds}s; last status: {evidence.get("last_status")}')


def prepare_and_wait(page, dialog, base, sid, report, timeout_seconds=3700, poll_hook=None):
    """Fail promptly on rejected/failed preparation, retaining the server's reason."""
    endpoint = base.rstrip('/') + '/java/' + sid + '/simulation/startup'
    with page.expect_response(lambda r: r.url == endpoint + '/confirm'
                              and r.request.method == 'POST', timeout=60000) as accepted:
        dialog.get_by_role('button', name='Continue', exact=True).click()
    response = accepted.value
    if not response.ok:
        raise AssertionError(f'Preparation request failed: HTTP {response.status}: {response.text()}')
    wait_for_preparation(page, dialog, base, sid, report, timeout_seconds, poll_hook)


def wait_for_preparation(page, dialog, base, sid, report, timeout_seconds=3700, poll_hook=None):
    endpoint = base.rstrip('/') + '/java/' + sid + '/simulation/startup'
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = page.request.get(endpoint, timeout=60000)
        if not response.ok:
            raise AssertionError(f'Preparation status failed: HTTP {response.status}: {response.text()}')
        status = response.json()
        report['preparation_last_status'] = {key: status.get(key) for key in ('state', 'message')}
        if poll_hook:
            poll_hook(status)
        if status.get('state') == 'ready':
            if dialog is not None:
                expect(dialog).not_to_be_visible(timeout=60000)
            return
        if status.get('state') != 'preparing':
            raise AssertionError(f'Preparation did not complete: {status.get("message") or status}')
        page.wait_for_timeout(1500)
    raise AssertionError(f'Preparation did not complete within {timeout_seconds}s; '
                         f'last status: {report.get("preparation_last_status")}')


def trial_preparation(page, dialog, base, sid, report, container, sampler, heap_mib, number):
    """Repeat the real review/confirm operation before Build and verify worker exit."""
    seen = set()
    endpoint = base.rstrip('/')+'/java/'+sid+'/simulation/startup'
    def poll(status):
        workers = [item for item in java_processes(container, heap_mib) if item['role'] == 'preparation']
        seen.update(item['pid'] for item in workers)
        if status.get('state') == 'ready' and workers:
            raise AssertionError('Preparation reported ready while its Java worker is still present')

    capture_trial_sample(sampler)
    with timed_stage(report, f'preparation-{number}') as evidence:
        if number == 1:
            prepare_and_wait(page, dialog, base, sid, report, poll_hook=poll)
        else:
            current = checked_json(page.request.get(endpoint, timeout=60000), 'Preparation state')
            if current.get('state') != 'ready' or current.get('locked') is not False:
                raise AssertionError('Repeat preparation requires ready, unbuilt startup state')
            selection = current.get('selection')
            if not isinstance(selection, dict) or selection.get('source') != 'uploads':
                raise AssertionError('Repeat preparation did not retain the uploaded dataset selection')
            review = checked_json(page.request.post(endpoint+'/review', data={'choices': selection}, timeout=60000),
                                  'Repeat preparation review')
            if not isinstance(review.get('token'), str) or not review['token']:
                raise AssertionError('Repeat preparation review omitted its confirmation token')
            checked_json(page.request.post(endpoint+'/confirm', data={'token': review['token']}, timeout=60000),
                         'Repeat preparation confirmation')
            wait_for_preparation(page, None, base, sid, report, poll_hook=poll)
        processes = java_processes(container, heap_mib)
        if not seen or any(item['role'] == 'preparation' for item in processes):
            raise AssertionError('Did not observe the selected preparation worker start and then exit')
        evidence.update(worker_pids=sorted(seen), worker_heap_mib=heap_mib, worker_exit_verified=True,
                        remaining_java_processes=processes)
        capture_trial_sample(sampler)


def finish_run(page, context, browser, base, sid, out, report):
    """Keep evidence/cleanup failures from hiding the original test failure."""
    def save_report():
        try:
            (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        except OSError as error:
            report['report_write_error'] = str(error)
            if report['status'] == 'passed':
                report.update(status='failed', error='Could not save the acceptance report.')
            print(f'Could not save report: {error}\n{json.dumps(report, indent=2)}', file=sys.stderr)

    save_report()
    for name, collect in (
        ('final.png', lambda: page.screenshot(path=str(out/'final.png'), full_page=False, timeout=15000)),
        ('trace.zip', lambda: context.tracing.stop(path=str(out/'trace.zip'))),
    ):
        try:
            collect()
        except Exception as error:
            report.setdefault('artifact_errors', {})[name] = str(error)
    if sid:
        try:
            # Uses the same session cookies without requiring a working page renderer.
            response = context.request.post(base.rstrip('/')+'/leave/'+sid, timeout=120000)
            if not response.ok:
                raise AssertionError(f'Session cleanup returned HTTP {response.status}')
            errors = parse_qs(urlsplit(response.url).query, keep_blank_values=True).get('error')
            if errors is not None:
                raise AssertionError(f'Session cleanup rejected: {errors[0]}')
        except Exception as error:
            report['cleanup_error'] = str(error)
    try:
        browser.close()
    except Exception as error:
        report['browser_close_error'] = str(error)
    if report['status'] == 'passed' and any(key in report for key in
            ('artifact_errors', 'cleanup_error', 'browser_close_error', 'report_write_error')):
        report.update(status='failed', error='Functional checks passed, but evidence collection or cleanup failed; see report details.')
    save_report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5001')
    parser.add_argument('--repo', type=Path, default=workflow.ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--memory-trial', action='store_true', help='Measure two preparations and two full fixed 50k runs')
    parser.add_argument('--trial-model-id', help='Unique model label created by the isolated acceptance runner')
    parser.add_argument('--preparation-heap-mib', type=int, choices=(3072, 4096))
    args = parser.parse_args()
    if args.memory_trial:
        if not args.trial_model_id or not TRIAL_MODEL.fullmatch(args.trial_model_id) or args.preparation_heap_mib is None:
            parser.error('--memory-trial requires the unique acceptance --trial-model-id and --preparation-heap-mib')
    elif args.trial_model_id is not None or args.preparation_heap_mib is not None:
        parser.error('Trial identity and preparation heap are only valid with --memory-trial')
    out = args.output.expanduser(); out.mkdir(parents=True, exist_ok=False)
    source = args.repo.expanduser()/'input'
    report = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(), 'checks': [], 'page_errors': []}
    if args.memory_trial:
        report['memory_trial'] = dict(model_id=args.trial_model_id, preparation_heap_mib=args.preparation_heap_mib,
            application_heap_mib=4096, container_memory_mib=5120, cpu_millis=2000,
            profile=TRIAL_PROFILE, swap_disabled=True, production_defaults_changed=False)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={'width':1440,'height':1000}, extra_http_headers={'Origin': args.url.rstrip('/')})
        context.tracing.start(screenshots=not args.memory_trial, snapshots=not args.memory_trial, sources=True)
        page = context.new_page(); sid = None
        docker_client = sampler = container = None
        page.on('pageerror',lambda error: report['page_errors'].append(str(error)))
        try:
            page.goto(args.url)
            page.locator('.model-card').filter(has_text='SimPaths UK - user-supplied data').click()
            sid = wait_for_session(page, report)
            if args.memory_trial:
                import docker
                docker_client = docker.from_env(timeout=5)
                container = configure_trial_container(docker_client, args.trial_model_id, sid, args.preparation_heap_mib)
                report['memory_trial']['container_id'] = container.id
                report['memory_trial']['initial_java_processes'] = java_processes(container, args.preparation_heap_mib)
                sampler_type = load_tool('acceptance/_interactive_memory.py').ContainerMemorySampler
                sampler = sampler_type(docker_client, args.trial_model_id, out/'memory.jsonl',
                    expected_memory_bytes=5*GIB, expected_cpu_nanos=2_000_000_000)
                sampler.start()
                capture_trial_sample(sampler)
            dialog=page.locator('#model-startup-dialog'); expect(dialog).to_be_visible(timeout=180000)
            endpoint=args.url+'/java/'+sid+'/simulation/input/download?path=EUROMODpolicySchedule.xlsx'
            original=page.request.get(endpoint); assert original.ok,original.text()
            original_schedule=original.body()
            dialog.get_by_role('button',name='Use bundled example schedule',exact=True).click()
            # Keep source=uploads: these public fixtures must exercise the non-training upload route.
            dialog.get_by_label('Starting-population CSV',exact=True).set_input_files(source/'InitialPopulations/training/population_initial_UK_2019.csv')
            expect(dialog.get_by_role('button',name='Review selected actions')).to_be_enabled(timeout=180000)
            donors=sorted((source/'EUROMODoutput/training').glob('*.txt'))
            dialog.get_by_label('UKMOD output files',exact=True).set_input_files(donors)
            expect(dialog.get_by_role('button',name='Review selected actions')).to_be_enabled(timeout=600000)
            dialog.get_by_role('button',name='Review selected actions').click()
            expect(dialog.get_by_role('button',name='Continue',exact=True)).to_be_visible(timeout=180000)
            # Give the uploaded schedule a distinct description so unintended template copies are detectable.
            dialog.get_by_label('Description',exact=True).first.fill('User-data acceptance: preserve this schedule')
            dialog.get_by_role('button',name='Review selected actions').click()
            expect(dialog.get_by_role('button',name='Continue',exact=True)).to_be_visible(timeout=180000)
            page.screenshot(path=str(out/'review.png'),full_page=True)
            dialog.get_by_role('button',name='Cancel',exact=True).click()
            assert page.request.get(endpoint).body()==original_schedule, 'Review/cancel changed active schedule'
            blocked=page.request.post(args.url+'/java/'+sid+'/simulation/build',data={})
            assert blocked.status==409,blocked.text()
            report['checks'].append('uploaded candidates and cancelled review do not enable Build')
            page.evaluate('void ensureModelStartup()'); expect(dialog).to_be_visible()
            dialog.get_by_role('button',name='Use bundled example schedule',exact=True).click()
            dialog.get_by_label('Description',exact=True).first.fill('User-data acceptance: preserve this schedule')
            dialog.get_by_role('button',name='Review selected actions').click()
            expect(dialog.get_by_role('button',name='Continue',exact=True)).to_be_visible(timeout=180000)
            if args.memory_trial:
                for preparation in (1, 2):
                    trial_preparation(page, dialog, args.url, sid, report, container, sampler,
                                      args.preparation_heap_mib, preparation)
                report['checks'].append('Both preparation workers used the selected heap and exited before Build')
            else:
                prepare_and_wait(page, dialog, args.url, sid, report)
            report['checks'].append('uploaded training fixtures prepare in non-training worker')
            endpoint=args.url+'/java/'+sid+'/simulation/input/download?path=EUROMODpolicySchedule.xlsx'
            response=page.request.get(endpoint); assert response.ok,response.text()
            schedule=response.body()
            (out/'confirmed-schedule.xlsx').write_bytes(schedule)
            # No provider call: check the effective permission state with the page's own helpers.
            permission=page.evaluate('async () => { await fetchAiDetailedDataAccess(); return {user:aiModelDetailedDataAllowed, ai:aiDetailedDataAccessAllowed}; }')
            assert permission=={'user':True,'ai':False},permission
            report['checks'].append('user detailed access enabled; AI restricted by default')
            for run in (1,2):
                if args.memory_trial:
                    processes = java_processes(container, args.preparation_heap_mib)
                    if any(item['role'] == 'preparation' for item in processes):
                        raise AssertionError('Preparation worker remains present before Build')
                    values = set_trial_profile(page)
                    report.setdefault('submitted_parameters', []).append(dict(run=run, values=values))
                    capture_trial_sample(sampler)
                    with timed_stage(report, f'build-{run}'):
                        build_and_wait(page, args.url, sid, run, report)
                    effective = checked_json(page.request.get(args.url.rstrip('/')+'/java/'+sid+'/simulation/current-params'),
                                             'Effective model parameters')
                    parameters = {item['name']: item['value'] for item in effective['modelParameters']}
                    verify_trial_profile(parameters)
                    report.setdefault('effective_parameters', []).append(dict(run=run, values=parameters))
                else:
                    build_and_wait(page, args.url, sid, run, report)
                query_url = args.url.rstrip('/')+'/java/'+sid+'/simulation/db/query'
                result = page.request.post(query_url, data={'role': 'input',
                    'sql': "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='PUBLIC'"})
                assert result.ok and result.json()['rows'][0][0] > 0, result.text()
                rejected_sql = page.request.post(query_url, data={'role': 'input',
                    'sql': "SELECT FILE_READ('input/system_bu_names.xlsx')"})
                assert rejected_sql.status == 400, rejected_sql.text()
                expect(page.locator('#start-btn')).to_be_enabled()
                report['checks'].append(f'Build {run}: restricted input queries work and unsafe SQL leaves the model usable')
                response=page.request.get(endpoint); assert response.ok and response.body()==schedule
                report['checks'].append(f'Build {run} preserves the confirmed user schedule')
                rejected=page.request.post(args.url+'/java/'+sid+'/simulation/startup/upload?path=EUROMODoutput/extra.txt',data=b'blocked')
                assert rejected.status>=400,rejected.text()
                if args.memory_trial:
                    capture_trial_sample(sampler)
                    with timed_stage(report, f'simulation-{run}'):
                        run_to_completion(page, args.url, sid, run, report,
                                          poll_hook=lambda: capture_trial_sample(sampler))
                    capture_trial_sample(sampler)
                    verify_full_output(page, args.url, sid, run, out, report)
                else:
                    step_until_person_export(page, args.url, sid, run, report)
                if run==1:
                    page.locator('#reset-btn').click()
                    expect(page.locator('#toolbar-build-btn')).to_be_enabled(timeout=330000)
            # Both archived and current outputs remain available while AI is restricted.
            output_file = 'HealthStatistics.csv' if args.memory_trial else 'Person.csv'
            download_run_output(page, out, report, 'restricted-first-listed-run', 0, output_file)
            before = download_run_output(page, out, report, 'restricted-second-listed-run', 1, output_file)
            report['checks'].append(f'both runs provide nonempty {output_file} downloads while AI is restricted')
            check_ai_consent(page, context, args.url, out, report)
            after = download_run_output(page, out, report, 'revoked-second-listed-run', 1, output_file)
            assert before == after, 'AI revocation changed simulation output'
            expect(page.locator('#step-btn')).to_be_enabled()
            report['checks'].append('AI revocation preserves user output downloads, file contents and usable simulation controls')
            assert not report['page_errors'],report['page_errors']
            report['status']='passed'
        except Exception as error:
            report.update(status='failed',error=str(error)); raise
        finally:
            if sampler is not None:
                stop_trial_sampling(sampler, report)
            if docker_client is not None:
                try:
                    docker_client.close()
                except Exception as error:
                    report['docker_client_close_error'] = str(error)
            if report['status'] == 'passed' and any(key in report for key in ('telemetry_error', 'docker_client_close_error')):
                report.update(status='failed', error='Memory trial telemetry or Docker client cleanup failed; see report details.')
            finish_run(page, context, browser, args.url, sid, out, report)
    if report['status'] != 'passed':
        raise AssertionError(report['error'])
    print(f"PASSED: {out/'report.json'}")

if __name__=='__main__': main()
