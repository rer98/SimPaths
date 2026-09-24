#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Exercise configurable training startup on a local frontend; owns only its new session.

@author ross richardson
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def read_startup_review(page, attempts=20):
    """Retry only explicit lifecycle-lock contention; never compare an error to input state."""
    for attempt in range(attempts):
        result = page.evaluate("""async () => {
            const response = await fetch('/java/' + CONFIG.SESSION_ID + '/simulation/startup/review', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({choices: {}})
            });
            const text = await response.text();
            let body;
            try { body = JSON.parse(text); } catch { body = {error: text}; }
            return {status: response.status, body};
        }""")
        status, body = result['status'], result['body']
        if status == 409 and isinstance(body, dict) and body.get('error') == 'Model is busy':
            if attempt + 1 < attempts:
                page.wait_for_timeout(500)
                continue
            raise RuntimeError(f'Startup review remained busy after {attempts} attempts (HTTP 409)')
        if status != 200 or not isinstance(body, dict) or body.get('error') or not isinstance(body.get('review'), dict):
            raise RuntimeError(f'Startup review request failed: HTTP {status}: {body!r}')
        return body['review']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5001')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare', choices=['none', 'population', 'tax', 'both'], default='none')
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()
    out = args.output.expanduser()
    out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'prepare': args.prepare, 'checks': [], 'page_errors': [],
              'started': datetime.now(timezone.utc).isoformat()}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, extra_http_headers={'Origin': args.url.rstrip('/')})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
        sid = None
        try:
            page.goto(args.url)
            page.locator('.model-card').filter(has_text='SimPaths UK - configurable training session').click()
            page.wait_for_url('**/sim/**', timeout=180000)
            sid = page.url.rstrip('/').split('/')[-1]
            dialog = page.locator('#model-startup-dialog')
            expect(dialog).to_be_visible(timeout=180000)
            before = read_startup_review(page)
            dialog.get_by_role('button', name='Review selected actions').click()
            expect(dialog.get_by_text('Supplied policy schedule (read-only)')).to_be_visible()
            dialog.get_by_role('button', name='Cancel', exact=True).click()
            after = read_startup_review(page)
            report['cancel_review_comparison'] = {'before': before, 'after': after}
            assert before == after, 'Review/cancel changed input state'
            report['checks'].append('review and cancel leave reviewed files unchanged')
            rejected = page.evaluate("async () => { const r=await fetch('/java/'+CONFIG.SESSION_ID+'/simulation/build',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); return {status:r.status,body:await r.json()}; }")
            assert rejected['status'] == 409, rejected
            report['checks'].append('direct Build rejected before startup confirmation')
            # Reopen without submitting Build: parameters must remain available after setup.
            page.evaluate('void ensureModelStartup()')
            expect(dialog).to_be_visible()
            if args.prepare in ('population', 'both'):
                dialog.get_by_label('Rebuild starting-population database from supplied training files').check()
            if args.prepare in ('tax', 'both'):
                dialog.get_by_label('Rebuild tax/benefit database from supplied training files').check()
            dialog.get_by_role('button', name='Review selected actions').click()
            expect(dialog.get_by_role('button', name='Continue', exact=True)).to_be_visible()
            page.screenshot(path=str(out/'startup-review.png'), full_page=True)
            dialog.get_by_role('button', name='Continue', exact=True).click()
            expect(dialog).not_to_be_visible(timeout=1800000)
            report['checks'].append('preparation completes automatically')
            expect(page.locator('#param-form')).to_be_visible()
            page.locator('#toolbar-build-btn').click()
            expect(page.locator('#start-btn')).to_be_enabled(timeout=900000)
            result = page.request.post(args.url.rstrip('/')+'/java/'+sid+'/simulation/db/query',
                data={'role': 'input', 'sql': "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='PUBLIC'"})
            assert result.ok and result.json()['rows'][0][0] > 0, result.text()
            report['checks'].append('prepared input has restricted query access')
            report['checks'].append('ordinary population Build completes')
            with page.expect_response(lambda r: '/step/' in r.url, timeout=330000) as step:
                page.locator('#step-btn').click()
            assert step.value.ok, step.value.text()
            expect(page.locator('#step-btn')).to_be_enabled(timeout=300000)
            with page.expect_response(lambda r: '/reset/' in r.url, timeout=330000) as reset:
                page.locator('#reset-btn').click()
            assert reset.value.ok, reset.value.text()
            expect(page.locator('#toolbar-build-btn')).to_be_enabled(timeout=330000)
            page.locator('#toolbar-build-btn').click()
            expect(page.locator('#start-btn')).to_be_enabled(timeout=900000)
            report['checks'].append('reset/rebuild retains confirmed startup')
            assert not report['page_errors'], report['page_errors']
            report['status'] = 'passed'
        except Exception as error:
            report.update(status='failed', error=str(error))
            raise
        finally:
            try:
                page.screenshot(path=str(out/'final.png'), full_page=True)
                context.tracing.stop(path=str(out/'trace.zip'))
            finally:
                if sid:
                    try:
                        context.request.post(args.url.rstrip('/') + '/leave/' + sid, timeout=120000)
                    except Exception as error:
                        report['cleanup_error'] = str(error)
                (out/'report.json').write_text(json.dumps(report, indent=2) + '\n')
                browser.close()
    print(f"PASSED: {out/'report.json'}")


if __name__ == '__main__':
    main()
