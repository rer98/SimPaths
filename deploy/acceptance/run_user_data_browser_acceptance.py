#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Exercise UK user-data uploads, preparation and repeated Builds using public examples.
Owns only its newly created session. Does not use an AI provider or send data to one.
@author ross richardson
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import sync_playwright, expect

import runpy
load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
workflow = load_tool('_workflow.py')


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


def download_run_output(page, out, report, label, run_index):
    """Download real microdata through Output, independently of AI permission."""
    page.locator('#export-btn').click()
    runs = page.locator('#export-modal details')
    expect(runs).to_have_count(2, timeout=60000)
    run = runs.nth(run_index)
    if run.get_attribute('open') is None:
        run.locator('summary').click()
    with page.expect_download(timeout=60000) as pending:
        run.locator('button.export-file-download[data-path$="Person.csv"]').first.click()
    download = pending.value
    assert download.failure() is None, download.failure()
    target = out / f'{label}-Person.csv'
    download.save_as(target)
    with target.open(encoding='utf-8-sig', newline='') as stream:
        rows = csv.reader(stream)
        header, first = next(rows, []), next(rows, [])
        assert len(header) > 1 and len(first) == len(header), 'Expected CSV header and population record'
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


def prepare_and_wait(page, dialog, base, sid, report, timeout_seconds=3700):
    """Fail promptly on rejected/failed preparation, retaining the server's reason."""
    endpoint = base.rstrip('/') + '/java/' + sid + '/simulation/startup'
    with page.expect_response(lambda r: r.url == endpoint + '/confirm'
                              and r.request.method == 'POST', timeout=60000) as accepted:
        dialog.get_by_role('button', name='Continue', exact=True).click()
    response = accepted.value
    if not response.ok:
        raise AssertionError(f'Preparation request failed: HTTP {response.status}: {response.text()}')
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = page.request.get(endpoint, timeout=60000)
        if not response.ok:
            raise AssertionError(f'Preparation status failed: HTTP {response.status}: {response.text()}')
        status = response.json()
        report['preparation_last_status'] = {key: status.get(key) for key in ('state', 'message')}
        if status.get('state') == 'ready':
            expect(dialog).not_to_be_visible(timeout=60000)
            return
        if status.get('state') != 'preparing':
            raise AssertionError(f'Preparation did not complete: {status.get("message") or status}')
        page.wait_for_timeout(1500)
    raise AssertionError(f'Preparation did not complete within {timeout_seconds}s; '
                         f'last status: {report.get("preparation_last_status")}')


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
    args = parser.parse_args()
    out = args.output.expanduser(); out.mkdir(parents=True, exist_ok=False)
    source = args.repo.expanduser()/'input'
    report = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(), 'checks': [], 'page_errors': []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={'width':1440,'height':1000}, extra_http_headers={'Origin': args.url.rstrip('/')})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page(); sid = None
        page.on('pageerror',lambda error: report['page_errors'].append(str(error)))
        try:
            page.goto(args.url)
            page.locator('.model-card').filter(has_text='SimPaths UK - user-supplied data').click()
            sid = wait_for_session(page, report)
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
                step_until_person_export(page, args.url, sid, run, report)
                if run==1:
                    page.locator('#reset-btn').click()
                    expect(page.locator('#toolbar-build-btn')).to_be_enabled(timeout=330000)
            # Both archived and current outputs remain available while AI is restricted.
            download_run_output(page, out, report, 'restricted-first-listed-run', 0)
            before = download_run_output(page, out, report, 'restricted-second-listed-run', 1)
            report['checks'].append('both runs provide nonempty Person CSV downloads while AI is restricted')
            check_ai_consent(page, context, args.url, out, report)
            after = download_run_output(page, out, report, 'revoked-second-listed-run', 1)
            assert before == after, 'AI revocation changed simulation output'
            expect(page.locator('#step-btn')).to_be_enabled()
            report['checks'].append('AI revocation preserves user output downloads, file contents and usable simulation controls')
            assert not report['page_errors'],report['page_errors']
            report['status']='passed'
        except Exception as error:
            report.update(status='failed',error=str(error)); raise
        finally:
            finish_run(page, context, browser, args.url, sid, out, report)
    if report['status'] != 'passed':
        raise AssertionError(report['error'])
    print(f"PASSED: {out/'report.json'}")

if __name__=='__main__': main()
