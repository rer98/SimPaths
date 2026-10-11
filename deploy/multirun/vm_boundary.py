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
import re
from pathlib import Path
import secrets
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
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


def aggregate_envelope(value, *, limits=None):
    """Check both published formats without accepting arbitrary/raw fields."""
    from jasmine_web.batch.visualiser import validate_publication
    if not isinstance(value, dict):
        raise ValueError('Unexpected Visualiser envelope')
    multiple = value.get('format') == 'simpaths.visualiser.v2'
    expected = {'format', 'backend', 'experiment', 'configurations', 'data'}
    if multiple:
        expected.add('comparison')
    if set(value) != expected or value['format'] not in ('simpaths.visualiser.v1', 'simpaths.visualiser.v2'):
        raise ValueError('Unexpected Visualiser envelope')
    configurations = value['configurations']
    if not isinstance(configurations, list) or not 1 <= len(configurations) <= (100 if multiple else 2):
        raise ValueError('Unexpected Visualiser configurations')
    for item in configurations:
        if (not isinstance(item, dict) or set(item) != {'id', 'name', 'role', 'dataset', 'model', 'runs'}
                or any(not isinstance(item[k], str) for k in ('id', 'name', 'role', 'dataset', 'model'))
                or item['role'] not in ('Baseline', 'Scenario') or not isinstance(item['runs'], list)
                or not 1 <= len(item['runs']) <= 1000):
            raise ValueError('Unexpected Visualiser configuration metadata')
        for run in item['runs']:
            if (not isinstance(run, dict) or set(run) != {'folder', 'seed'}
                    or any(not isinstance(run[k], str) for k in ('folder', 'seed'))):
                raise ValueError('Unexpected Visualiser run metadata')
    if multiple:
        expected_selection = dict(baseline=configurations[0]['id'],
                                 scenarios=[item['id'] for item in configurations[1:]])
        if value['comparison'] != expected_selection:
            raise ValueError('Visualiser comparison does not match its configurations')
    options = dict(limits=limits) if limits is not None else {}
    validate_publication(value['data'], configurations=configurations if multiple else None, **options)
    return value


def section_envelope(value, *, catalogue=None):
    """Independently check small catalogues/views, their fields and selection."""
    from jasmine_web.batch.visualiser import validate_publication
    from jasmine_web.batch.aggregate_sections import query, VIEW_LIMIT, VIEW_ROWS, CATALOGUE_LIMIT
    if type(value) is not dict or not re.fullmatch('[a-f0-9]{64}',str(value.get('publication',''))):
        raise ValueError('Unexpected Visualiser section')
    if value.get('format')=='simpaths.visualiser.catalogue.v1':
        if (set(value)!={'format','publication','configurations','seeds','notice','comparison_available','limits','variables'}
                or type(value['configurations']) is not list or not 1<=len(value['configurations'])<=100
                or type(value['variables']) is not list or len(value['variables'])>256
                or type(value['seeds']) is not list or len(value['seeds'])>1000
                or any(not isinstance(s,str) or not re.fullmatch('[0-9]{1,20}',s) for s in value['seeds'])
                or type(value['comparison_available']) is not bool or not isinstance(value['notice'],str)
                or len(value['notice'])>500 or value['limits']!=dict(response_bytes=VIEW_LIMIT,response_rows=VIEW_ROWS)):
            raise ValueError('Unexpected Visualiser catalogue')
        ids,roles=set(),set()
        for config in value['configurations']:
            if (type(config) is not dict or set(config)!={'id','name','role','key'}
                    or any(not isinstance(config[k],str) or not 1<=len(config[k])<=(200 if k=='name' else 160) for k in config)
                    or config['role'] not in ('Baseline','Scenario') or config['id'] in ids or config['key'] in roles
                    or (config['role']=='Baseline' and config['key']!='baseline')
                    or (config['role']=='Scenario' and not re.fullmatch('scenario(?:_[1-9][0-9]*)?',config['key']))):
                raise ValueError('Unexpected Visualiser configuration')
            ids.add(config['id']);roles.add(config['key'])
        names=set()
        for variable in value['variables']:
            if (type(variable) is not dict or set(variable)!={'name','module','years','views'}
                    or any(not isinstance(variable[k],str) for k in ('name','module'))
                    or variable['name'] in names or type(variable['years']) is not list
                    or len(variable['years'])>1000 or any(type(y) is not int for y in variable['years'])
                    or type(variable['views']) is not list or len(variable['views'])>100):
                raise ValueError('Unexpected Visualiser variable')
            names.add(variable['name'])
            for view in variable['views']:
                if (type(view) is not dict or set(view)!={'stratifier','kind','bytes','rows'}
                        or not isinstance(view['stratifier'],str) or view['kind'] not in ('levels','wage_bin','income_bin','pyramid_bin')
                        or any(type(view[k]) is not int or view[k]<0 for k in ('bytes','rows'))):
                    raise ValueError('Unexpected Visualiser view')
        maximum=CATALOGUE_LIMIT
    elif value.get('format')=='simpaths.visualiser.view.v1':
        if (set(value)!={'format','publication','selection','series'} or catalogue is None
                or value['publication']!=catalogue['publication'] or type(value['series']) is not list):
            raise ValueError('Unexpected Visualiser view')
        selected=query(value['selection'],catalogue)
        if (len(value['series'])!=len(selected['configurations'])
                or any(type(s) is not dict or set(s)!={'configuration','rows'} for s in value['series'])
                or [s['configuration'] for s in value['series']]!=selected['configurations']):
            raise ValueError('Unexpected Visualiser series')
        count=0
        for series in value['series']:
            rows=series['rows']
            if type(rows) is not list:raise ValueError('Unexpected Visualiser rows')
            count+=len(rows)
            if rows:
                validate_publication(dict(rows=rows,notice='',comparison_available=catalogue['comparison_available']))
                config=next(c for c in catalogue['configurations'] if c['id']==series['configuration'])
                if any(r['variable']!=selected['variable'] or r['stratifier']!=selected['stratifier']
                       or r['scenario']!=config['role'].lower()
                       or (r['metric_type'] not in ('mean','share') if selected['kind']=='levels' else r['metric_type']!=selected['kind'])
                       for r in rows):
                    raise ValueError('Visualiser rows differ from the selected chart')
        if count>VIEW_ROWS:raise ValueError('Too many Visualiser rows')
        maximum=VIEW_LIMIT
    else:raise ValueError('Unexpected Visualiser section format')
    if len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode())>maximum:
        raise ValueError('Oversized Visualiser section')
    return value


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
        from jasmine_web.batch.visualiser import MAX_COMPARISON_ARTIFACT
        base='/api/visualiser/'+visualiser_key
        response=get(base+'/catalogue',cookie=cookie,maximum=512*1024)
        if response[0]==200:
            result,body=check(lambda *a,**kw:response,base+'/catalogue',cookie=cookie,maximum=512*1024)
            catalogue=section_envelope(json.loads(body));checks.append(result)
            paths=[base+'/catalogue']
            variable=next((v for v in catalogue['variables'] if any(s['kind']=='levels' and s['stratifier']=='Overall' for s in v['views'])),None)
            if variable is None:raise ValueError('Select a comparison with an Overall chart')
            path=base+'/view?'+urlencode([('variable',variable['name']),('stratifier','Overall'),('kind','levels'),
                ('configuration',catalogue['configurations'][0]['id'])])
            result,view=check(get,path,cookie=cookie,maximum=8*1024**2)
            section_envelope(json.loads(view),catalogue=catalogue);checks.append(result);paths.append(path)
            bodies=[body,view]
        elif response[0] in (400,404):
            # Older pinned applications retain their original full-data API.
            path=base+'/data';result,body=check(get,path,cookie=cookie,maximum=MAX_COMPARISON_ARTIFACT)
            aggregate_envelope(json.loads(body));checks.append(result);paths=[path];bodies=[body]
        else:raise ValueError('Owned Visualiser catalogue is unavailable')
        for body in bodies:
            if marker in body or any(s in body for s in (b'id_Person',b'id_BenefitUnit',b'/srv/',b'/home/',b'/work/')):
                raise ValueError('Raw columns or private paths in Visualiser data')
        for path in paths:
            for account in ['', *([other_cookie] if other_cookie else [])]:
                result,_=check(get,path,denied=True,cookie=account);checks.append(result)
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
