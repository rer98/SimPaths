"""(C) Copyright 2026, by Ross Richardson

External HTTPS privacy checks using harmless private canaries and fictional results.
Read-only HTTP; never copies raw simulation records or stores authentication cookies.
@author ross richardson
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from .vm_config import load_config
from .vm_web import private_text


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward a sign-in cookie to a redirect destination.


def http_reader(origin):
    opener = build_opener(NoRedirect())  # Uses normal certificate/hostname verification.
    def get(path, *, cookie='', headers=None, maximum=65536):
        values = {'Accept': 'application/json', **(headers or {})}
        if cookie:
            values['Cookie'] = cookie
        try:
            response = opener.open(Request(origin+path, headers=values), timeout=30)
        except HTTPError as error:
            response = error
        with response:
            return response.status, dict(response.headers), response.read(maximum+1)
    return get


def check(get, path, *, denied=False, cookie='', headers=None, marker=b'', maximum=65536):
    status, response_headers, data = get(path, cookie=cookie, headers=headers, maximum=maximum)
    if len(data) > maximum:
        raise ValueError('Oversized response at '+path)
    if marker and marker in data:
        raise ValueError('Private canary exposed at '+path)
    if denied and status not in (400, 403, 404):
        raise ValueError(f'Expected denial at {path}; received HTTP {status}')
    if not denied and status != 200:
        raise ValueError(f'Expected success at {path}; received HTTP {status}')
    lower = {k.lower(): v for k, v in response_headers.items()}
    if not denied and 'no-store' not in lower.get('cache-control', ''):
        raise ValueError('Protected response can be cached at '+path)
    return {'path': path, 'status': status}, data


def privacy_checks(get, name, marker, *, cookie='', other_cookie='', experiment=None,
                   restricted_job=None, visualiser_key=None, extra_paths=()):
    checks = []
    paths = ['/'+name, '/private/'+name, '/static/'+name, '/uploads/'+name,
        '/artifacts/'+name, '/execution/'+name, '/release/'+name, '/backups/'+name,
        '/diagnostics/'+name, '/download-cache/'+name, '/visualiser-cache/'+name,
        '/data/'+name, '/files/'+name, '/storage/'+name, '/prepared/'+name,
        '/static/../private/'+name, '/static/%2e%2e/private/'+name,
        '/static/%252e%252e/private/'+name, '/.env', '/.git/config',
        '/visualiser-assets/calculation.cjs', '/visualiser-assets/runner.cjs', *extra_paths]
    for path in paths:
        result, _ = check(get, path, denied=True, marker=marker)
        checks.append(result)
    if cookie:
        result, body = check(get, '/api/session', cookie=cookie)
        if json.loads(body).get('signed_in') is not True:
            raise ValueError('Provide the current approved owner session cookie')
        checks.append(result)
        # Repeat actual private-file probes while signed in as well.
        for path in paths:
            result, _ = check(get, path, denied=True, marker=marker, cookie=cookie)
            checks.append(result)
    if other_cookie:
        result, body = check(get, '/api/session', cookie=other_cookie)
        if json.loads(body).get('signed_in') is not True:
            raise ValueError('Provide a different approved account session cookie')
        checks.append(result)
    if experiment and restricted_job and cookie:
        result, body = check(get, '/api/results/'+experiment, cookie=cookie)
        rows = json.loads(body)['configurations']
        selected = next((r for r in rows if r['id'] == restricted_job), None)
        if (not selected or selected['state'] != 'succeeded' or selected['output_state'] != 'retained'
                or selected['downloadable'] is not False or selected.get('visualisable') is not True):
            raise ValueError('Select completed, retained provider-derived output that this account can visualise')
        checks.append(result)
        for suffix in ('', '?include_inputs=1'):
            for headers in ({}, {'Range': 'bytes=0-63'}, {'Range': 'bytes=-64'}):
                result, _ = check(get, '/downloads/'+restricted_job+suffix, denied=True,
                    cookie=cookie, headers=headers, marker=marker)
                checks.append(result)
    if visualiser_key and cookie:
        from jasmine_web.batch.visualiser import MAX_ARTIFACT, validate_publication
        path = '/api/visualiser/'+visualiser_key+'/data'
        result, body = check(get, path, cookie=cookie, maximum=MAX_ARTIFACT)
        value = json.loads(body)
        if set(value) != {'format', 'backend', 'experiment', 'configurations', 'data'} or value['format'] != 'simpaths.visualiser.v1':
            raise ValueError('Unexpected Visualiser envelope')
        validate_publication(value['data'])
        if marker in body or any(s in body for s in (b'id_Person', b'id_BenefitUnit', b'/srv/', b'/home/', b'/work/')):
            raise ValueError('Raw columns or private paths in Visualiser data')
        checks.append(result)
        for account in ['', *([other_cookie] if other_cookie else [])]:
            result, _ = check(get, path, denied=True, cookie=account)
            checks.append(result)
    if other_cookie and experiment:
        result, _ = check(get, '/api/results/'+experiment, denied=True, cookie=other_cookie)
        checks.append(result)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--cookie-file', type=Path)
    parser.add_argument('--other-cookie-file', type=Path)
    parser.add_argument('--experiment')
    parser.add_argument('--restricted-job')
    parser.add_argument('--visualiser-key')
    args = parser.parse_args(argv)
    options = load_config(args.config)
    if not args.execute:
        print('Plan: private-canary HTTPS probes at '+options.origin+'. Add --execute to run.')
        return 0
    if args.output.exists():
        raise ValueError('Choose a new evidence directory')
    if bool(args.restricted_job) != bool(args.experiment):
        raise ValueError('Provide both experiment and restricted-job for provider checks')
    for value in (args.experiment, args.restricted_job):
        if value:
            UUID(value)
    if args.visualiser_key and (len(args.visualiser_key) != 64 or any(c not in '0123456789abcdef' for c in args.visualiser_key)):
        raise ValueError('Invalid Visualiser key')
    if (args.restricted_job or args.visualiser_key or args.other_cookie_file) and not args.cookie_file:
        raise ValueError('Authenticated checks require an owner cookie file')
    cookie = private_text(args.cookie_file) if args.cookie_file else ''
    other = private_text(args.other_cookie_file) if args.other_cookie_file else ''
    if other and other == cookie:
        raise ValueError('Use different account sessions')
    sys.path.insert(0, str(options.frontend.resolve(strict=True)))
    os.umask(0o077)
    for root in (options.private_root, options.state):
        if not root.is_dir() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError('Private canary roots must exist without symlink ancestors')
    args.output.mkdir(mode=0o700, parents=True)
    name = 'boundary-probe-'+secrets.token_hex(16)+'.txt'
    marker = ('SIMPATHS_PRIVATE_CANARY_'+secrets.token_hex(32)).encode()
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'origin': options.origin,
              'passed': False, 'full_acceptance': False}
    try:
        # Existing private directories only. No service files are overwritten.
        with ExitStack() as cleanup:
            candidates = [options.private_root, options.state, *options.prepared,
                *(options.state/p for p in ('uploads', 'artifacts', 'release', 'execution')),
                *(options.state/'execution'/p for p in ('download-cache', 'visualiser-cache'))]
            extra_paths = set()
            canaries = 0
            for directory in candidates:
                if not directory.is_dir():
                    continue
                if any(p.is_symlink() for p in (directory, *directory.parents)):
                    raise ValueError('Private canary directory must not contain symlinks')
                path = directory/name
                try:
                    with path.open('xb') as source:
                        cleanup.callback(path.unlink)
                        source.write(marker)
                except PermissionError:
                    continue  # Immutable snapshots can remain read-only.
                canaries += 1
                relative = str(path.relative_to(options.private_root))
                extra_paths.update((quote(str(path), safe='/'), '/private/'+quote(relative, safe='/'),
                                    '/'+quote(relative, safe='/')))
            if not canaries:
                raise ValueError('No private canary could be created')
            report['private_canary_count'] = canaries
            report['checks'] = privacy_checks(http_reader(options.origin), name, marker,
                cookie=cookie, other_cookie=other, experiment=args.experiment,
                restricted_job=args.restricted_job, visualiser_key=args.visualiser_key,
                extra_paths=sorted(extra_paths))
            report['passed'] = True
            report['full_acceptance'] = bool(cookie and other and args.restricted_job and args.visualiser_key)
    except Exception as error:
        # Network exceptions/headers might contain secrets; retain the type only.
        report['error_type'] = type(error).__name__
        if type(error) is ValueError:
            report['message'] = str(error)[:500]
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Generated deployment privacy evidence attribution.\n@author ross richardson -->\n\n'
        'Probe and generated evidence: (C) Copyright 2026, by Ross Richardson.\n')
    print(('PASSED' if report['passed'] else 'FAILED')+': '+str(args.output/'report.json'))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
