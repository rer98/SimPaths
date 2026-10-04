#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Disposable HTTPS/Nginx rehearsal of MultiRun permissions, ZIPs and process restart.
Uses fictional files, loopback ports and an explicitly isolated test PostgreSQL.
@author ross richardson
"""
import argparse
from datetime import datetime, timezone
import hashlib
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._workflow import frontend_path
from deploy.multirun.vm_boundary import NoRedirect, aggregate_envelope, privacy_checks

TEMPLATE = Path(__file__).with_name('vm')/'nginx.conf.example'
HOST = 'localhost'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def nginx_path(path):
    value = str(path)
    if any(character in value for character in '\r\n\x00'):
        raise ValueError('Invalid rehearsal path')
    return '"'+value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$')+'"'


def proxy_config(text, *, http_port, https_port, app_port, base, runtime=Path('/tmp'),
                 domain='multirun.example.org', upstream_port=5002, timeout_seconds=300):
    """Substitute only loopback listeners, hostname, certificate and ACME paths.

    Stop if deployment anchors or private-file/cache protections have changed.
    The rehearsal never installs a system configuration or uses privileged ports.
    """
    ports = (http_port, https_port, app_port)
    if (not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', domain)
            or type(upstream_port) is not int or not 1024 <= upstream_port <= 65535
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600):
        raise ValueError('Invalid deployment proxy anchors')
    if any(type(port) is not int or not 1024 <= port <= 65535 for port in ports) or len(set(ports)) != 3:
        raise ValueError('Use three different unprivileged loopback ports')
    policy = re.sub(r'#.*', '', text)
    required = ['autoindex off;', 'proxy_buffering off;', 'proxy_request_buffering off;',
        'proxy_cache off;', 'proxy_max_temp_file_size 0;', 'ssl_reject_handshake on;',
        'proxy_set_header X-Forwarded-For $remote_addr;', 'proxy_set_header X-Forwarded-Proto https;',
        f'proxy_read_timeout {timeout_seconds}s;', f'proxy_send_timeout {timeout_seconds}s;',
        'location ~ (^|/)\\. { return 404; }',
        'location ~ ^/(private|uploads|artifacts|execution|release|backups|diagnostics|download-cache|visualiser-cache)(/|$)']
    if (any(anchor not in policy for anchor in required)
            or re.search(r'\b(alias|try_files|proxy_store|proxy_cache_path)\b', policy)
            or len(re.findall(r'\broot\s', policy)) != 1):
        raise ValueError('Deployment proxy protections changed; review the rehearsal adaptation')
    replacements = {
        'listen 80': f'listen 127.0.0.1:{http_port}',
        'listen 443': f'listen 127.0.0.1:{https_port}',
        f'proxy_pass http://127.0.0.1:{upstream_port};': f'proxy_pass http://127.0.0.1:{app_port};',
        f'proxy_set_header Host {domain};': f'proxy_set_header Host {HOST}:{https_port};',
        f'return 308 https://{domain}$request_uri;': f'return 308 https://{HOST}:{https_port}$request_uri;',
        f'/etc/letsencrypt/live/{domain}/fullchain.pem': nginx_path(Path(base)/'cert.pem'),
        f'/etc/letsencrypt/live/{domain}/privkey.pem': nginx_path(Path(base)/'key.pem'),
        'root /var/lib/letsencrypt;': 'root '+nginx_path(Path(base)/'acme')+';',
    }
    for old, new in replacements.items():
        if text.count(old) != (2 if old in ('listen 80', 'listen 443') else 1):
            raise ValueError('Deployment proxy substitution anchor changed')
        text = text.replace(old, new)
    text, removed = re.subn(r'^\s*listen \[::\]:(80|443)( ssl)?( default_server)?;\n', '', text, flags=re.M)
    if removed != 4:
        raise ValueError('Deployment IPv6 listener anchors changed')
    text = text.replace(domain, HOST)
    return ('worker_processes 1;\npid '+nginx_path(Path(runtime)/'nginx.pid')+';\nerror_log stderr warn;\n'
            'events { worker_connections 128; }\nhttp {\naccess_log off;\n'
            'client_body_temp_path '+nginx_path(Path(runtime)/'client-body')+';\n'
            'proxy_temp_path '+nginx_path(Path(runtime)/'proxy-body')+';\n'
            # These compiled-in modules initialise their default directories
            # even without a FastCGI/uWSGI/SCGI location. Keep every temp path
            # in our writable runtime area, including during nginx -t.
            'fastcgi_temp_path '+nginx_path(Path(runtime)/'fastcgi-body')+';\n'
            'uwsgi_temp_path '+nginx_path(Path(runtime)/'uwsgi-body')+';\n'
            'scgi_temp_path '+nginx_path(Path(runtime)/'scgi-body')+';\n'+text+'\n}\n')


def require_test_database(dsn):
    """Refuse live/remote registries even when the hidden proof flag is supplied."""
    from psycopg.conninfo import conninfo_to_dict
    values = conninfo_to_dict(dsn)
    if (values.get('host') != '127.0.0.1' or values.get('dbname') != 'jasmine_queue_test'
            or values.get('user') != 'postgres' or not values.get('password')
            or not values.get('port', '').isdigit() or not 1024 <= int(values['port']) <= 65535
            or set(values) - {'host', 'port', 'dbname', 'user', 'password'}):
        raise ValueError('Only the runner\'s disposable loopback PostgreSQL is permitted')


class HTTPSClient:
    """Only the ephemeral certificate is trusted; no redirects or proxy env."""
    def __init__(self, origin, certificate):
        self.origin, self.cookie, self.csrf = origin, '', ''
        self.opener = build_opener(NoRedirect(), ProxyHandler({}),
                                   HTTPSHandler(context=ssl.create_default_context(cafile=str(certificate))))

    def open(self, path, *, cookie=None, headers=None, value=None, method=None):
        values = {'Accept': 'application/json', **(headers or {})}
        session = self.cookie if cookie is None else cookie
        if session:
            values['Cookie'] = session
        data = None
        if value is not None:
            data = json.dumps(value, allow_nan=False).encode()
            values.update({'Origin':self.origin, 'Content-Type':'application/json', 'X-CSRF-Token':self.csrf})
            values.update(headers or {})
        try:
            return self.opener.open(Request(self.origin+path, headers=values, data=data, method=method), timeout=60)
        except HTTPError as error:
            return error

    def get(self, path, *, cookie='', headers=None, maximum=65536):
        with self.open(path, cookie=cookie, headers=headers) as response:
            # Retain urllib's case-insensitive HTTPMessage. A plain dict would
            # mistake Nginx's forwarded lowercase headers for missing fields.
            return response.status, response.headers, response.read(maximum+1)

    def request(self, path, *, value=None, headers=None, method=None, maximum=64*1024**2):
        with self.open(path, value=value, headers=headers, method=method) as response:
            data = response.read(maximum+1)
            if len(data) > maximum:
                raise AssertionError('Oversized fictional test response')
            return response.status, response.headers, data

    def api(self, path, value=None):
        status, headers, body = self.request(path, value=value)
        if status not in (200, 201):
            raise AssertionError(f'Expected API success at {path}; HTTP {status}')
        if 'no-store' not in headers.get('Cache-Control', ''):
            raise AssertionError('Protected API response permits caching')
        return json.loads(body)

    def sign_in(self, email, state):
        challenge = self.api('/api/code', dict(email=email))['challenge']
        code = json.loads((state/'mail.json').read_text())[email]
        status, headers, body = self.request('/api/verify', value=dict(challenge=challenge, code=code))
        result = json.loads(body)
        if status != 200 or result.get('authorised') is not True:
            raise AssertionError('Fictional HTTPS sign-in failed')
        parsed = SimpleCookie(headers['Set-Cookie'])
        if len(parsed) != 1:
            raise AssertionError('Unexpected sign-in cookie')
        morsel = next(iter(parsed.values()))
        if not morsel['secure'] or not morsel['httponly'] or morsel['samesite'].lower() != 'strict':
            raise AssertionError('HTTPS cookie protections missing')
        self.cookie, self.csrf = morsel.key+'='+morsel.value, result['csrf']
        if self.cookie in body.decode():
            raise AssertionError('Session cookie appeared in JSON')


def until(function, *, seconds=180):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(.25)
    raise AssertionError('Rehearsal phase did not finish in time')


def retire_fixture_outputs(service):
    """Perform the fixture's worker cleanup under its real dispatcher lock."""
    with service.outputs.executor.exclusive():
        service.outputs.retire()


class Processes:
    def __init__(self, work, output, args):
        self.work, self.output, self.args = work, output, args
        self.app = self.proxy = None
        self.logs = []
        self.name = 'simpaths-proxy-rehearsal-'+secrets.token_hex(8)
        self.container_created = False
        self.image = None

    def start_app(self, settings, *, script=None, workdir=None):
        from jasmine_web.batch.local_executor import atomic_json
        atomic_json(self.work/'fixture.json', settings)
        log = (self.output/'application.log').open('ab'); self.logs.append(log)
        self.app = subprocess.Popen([sys.executable, str(script or Path(__file__).resolve()), '--serve-fixture',
                                     str(self.work/'fixture.json')], stdout=log, stderr=subprocess.STDOUT,
                                     cwd=workdir)

    def stop_app(self):
        if self.app is None:
            return
        process, self.app = self.app, None
        process.terminate()
        try:
            process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=10)
            raise AssertionError('Application did not stop gracefully after interrupted transfer')
        # Uvicorn restores and re-raises the received signal after its graceful
        # lifespan shutdown. Accept only success or the SIGTERM we sent.
        if process.returncode not in (0, -signal.SIGTERM):
            raise AssertionError('Rehearsal application exited abnormally')

    def start_proxy(self, config):
        log = (self.output/'nginx.log').open('ab'); self.logs.append(log)
        if self.args.nginx:
            self.image = 'native executable'
            command = [str(self.args.nginx), '-e', 'stderr', '-c', str(config), '-g', 'daemon off;']
            subprocess.run([str(self.args.nginx), '-e', 'stderr', '-t', '-c', str(config)], check=True,
                           stdout=log, stderr=subprocess.STDOUT, timeout=30)
            self.proxy = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        else:
            image = subprocess.run(['docker', 'image', 'inspect', self.args.nginx_image, '--format', '{{.Id}}'],
                                   capture_output=True, text=True, timeout=30)
            if image.returncode:
                print('Installing the Nginx rehearsal image', flush=True)
                subprocess.run(['docker', 'pull', self.args.nginx_image], check=True,
                               stdout=log, stderr=subprocess.STDOUT, timeout=300)
                image = subprocess.run(['docker', 'image', 'inspect', self.args.nginx_image, '--format', '{{.Id}}'],
                                       check=True, capture_output=True, text=True, timeout=30)
            self.image = image.stdout.strip()
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', self.image):
                raise ValueError('Could not pin the rehearsal image')
            common = ['docker', 'run', '--network', 'host', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges:true', '--user', f'{os.getuid()}:{os.getgid()}',
                '--memory', '128m', '--cpus', '.5', '--pids-limit', '64',
                '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m',
                '--mount', f'type=bind,source={config.parent},target=/rehearsal,readonly', '--entrypoint', 'nginx']
            subprocess.run([*common, '--rm', self.image, '-e', 'stderr', '-t', '-c', '/rehearsal/nginx.conf'], check=True,
                           stdout=log, stderr=subprocess.STDOUT, timeout=30)
            self.container_created = True
            subprocess.run([*common, '--name', self.name, '--label', 'simpaths.proxy-rehearsal='+self.name,
                '-d', self.image, '-e', 'stderr', '-c', '/rehearsal/nginx.conf', '-g', 'daemon off;'], check=True,
                stdout=log, stderr=subprocess.STDOUT, timeout=30)

    def close(self):
        errors = []
        try:
            self.stop_app()
        except Exception as error:
            errors.append(type(error).__name__)
        if self.proxy is not None:
            self.proxy.terminate()
            try:
                self.proxy.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proxy.kill(); self.proxy.wait(timeout=10); errors.append('NginxStopTimeout')
            if self.proxy.returncode != 0:
                errors.append('NginxAbnormalExit')
        if self.container_created:
            try:
                inspected = subprocess.run(['docker', 'inspect', '--format',
                    '{{index .Config.Labels "simpaths.proxy-rehearsal"}}', self.name], capture_output=True, text=True, timeout=30)
                if inspected.returncode == 0 and inspected.stdout.strip() == self.name:
                    with (self.output/'nginx.log').open('ab') as log:
                        subprocess.run(['docker', 'logs', self.name], stdout=log, stderr=subprocess.STDOUT, timeout=30)
                    removed = subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True, timeout=30)
                    if removed.returncode:
                        errors.append('NginxContainerCleanup')
                else:
                    errors.append('NginxContainerIdentityUnknown')
            except Exception as error:
                errors.append(type(error).__name__)
        for log in self.logs:
            log.close()
        if errors:
            raise RuntimeError(','.join(errors))


def serve_fixture(path):
    sys.path[:0] = [str(frontend_path()), str(frontend_path()/'tests'/'batch')]
    from deploy.multirun.proxy_fixture import services
    from jasmine_web.batch.browser import create_app
    import uvicorn
    settings = json.loads(path.read_text())
    if not re.fullmatch(r'test_proxy_[a-f0-9]{32}', settings['schema']):
        raise ValueError('Only a rehearsal schema is permitted')
    require_test_database(os.environ['JASMINE_BATCH_TEST_DSN'])
    service, _, _ = services(settings, os.environ['JASMINE_BATCH_TEST_DSN'])
    app = create_app(service, origin=settings['origin'], local_codes=False,
                     site=dict(name='SimPaths Online', subtitle='UK MultiRun', logo='', icon=''))
    uvicorn.run(app, host='127.0.0.1', port=settings['app_port'], workers=1, access_log=False,
                log_level='warning', proxy_headers=True, forwarded_allow_ips='127.0.0.1')


def rehearsal(args, report):
    sys.path[:0] = [str(args.frontend), str(args.frontend/'tests'/'batch')]
    from deploy.multirun.proxy_fixture import bootstrap, services, finish, digest_file, RAW
    from deploy.multirun.artifacts import write_attribution
    from psycopg import sql
    dsn = os.environ['JASMINE_BATCH_TEST_DSN']
    args.output.mkdir(mode=0o700, parents=True)
    write_attribution(args.output)
    with tempfile.TemporaryDirectory(prefix='simpaths-https-rehearsal-') as temporary:
        work = Path(temporary)
        proxy = work/'proxy'; proxy.mkdir(mode=0o700)
        state = work/'private'; state.mkdir(mode=0o700)
        ports = []
        while len(ports) < 3:
            port = free_port()
            if port not in ports: ports.append(port)
        settings = dict(state=str(state), schema='test_proxy_'+uuid4().hex,
                        origin=f'https://{HOST}:{ports[1]}', app_port=ports[2])
        processes = Processes(work, args.output, args)
        service = queue = None
        def passed(message):
            report['checks'].append(message); print('PASS: '+message, flush=True)
        try:
            report['phase'] = 'local-policy-and-fixture-checks'
            import unittest
            names = ['deploy.multirun.test_proxy_rehearsal', 'deploy.multirun.test_vm_config']
            with (args.output/'local-checks.log').open('w') as log:
                tests = unittest.TextTestRunner(stream=log, verbosity=2).run(
                    unittest.defaultTestLoader.loadTestsFromNames(names))
            report['local_checks'] = dict(tests=tests.testsRun, errors=len(tests.errors),
                failures=len(tests.failures), skipped=len(tests.skipped))
            if not tests.wasSuccessful() or tests.skipped:
                raise AssertionError('Local policy/fixture checks failed; inspect local-checks.log')
            passed('local proxy adaptation, isolation and aggregate-boundary checks pass in the isolated dependency environment')
            report['phase'] = 'temporary-certificate-and-proxy'
            with (args.output/'certificate.log').open('wb') as log:
                subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-keyout', str(proxy/'key.pem'), '-out', str(proxy/'cert.pem'), '-subj', '/CN=localhost',
                    '-addext', 'subjectAltName=DNS:localhost'], check=True,
                    stdout=log, stderr=subprocess.STDOUT, timeout=30)
            (proxy/'key.pem').chmod(0o600)
            source = TEMPLATE.read_text()
            base = proxy if args.nginx else Path('/rehearsal')
            runtime = work/'nginx-runtime' if args.nginx else Path('/tmp')
            if args.nginx: runtime.mkdir(mode=0o700)
            config = proxy_config(source, http_port=ports[0], https_port=ports[1], app_port=ports[2], base=base, runtime=runtime)
            (proxy/'nginx.conf').write_text(config)
            challenge = proxy/'acme/.well-known/acme-challenge'
            challenge.mkdir(parents=True); (challenge/'fictional').write_text('fictional ACME challenge')
            (args.output/'proxy-template.conf').write_text(source)
            (args.output/'proxy-rendered.conf').write_text(config)
            report['template_sha256'] = hashlib.sha256(source.encode()).hexdigest()
            report['origin'] = settings['origin']
            inputs, owners = bootstrap(settings, dsn)
            service, queue, executor = services(settings, dsn)
            processes.start_app(settings); processes.start_proxy(proxy/'nginx.conf')
            report['nginx_image'] = processes.image
            alice = HTTPSClient(settings['origin'], proxy/'cert.pem')
            bob = HTTPSClient(settings['origin'], proxy/'cert.pem')
            def ready():
                try: return alice.request('/')[0] == 200
                except (URLError, OSError): return False
            until(ready, seconds=45)
            report['phase'] = 'https-sign-in'
            alice.sign_in('alice@example.org', state); bob.sign_in('bob@example.org', state)
            status, _, body = HTTPSClient(settings['origin'], proxy/'cert.pem').request('/api/session')
            assert status == 200 and json.loads(body)['signed_in'] is False
            status, _, _ = alice.request('/api/logout', value={}, headers={'Origin':'https://wrong.example.org'})
            assert status == 403
            assert alice.api('/api/session')['signed_in'] is True
            passed('real TLS, HTTPS-only protected cookies, two approved owners and rejected foreign-origin mutation')

            report['phase'] = 'redirect-and-tls-denials'
            import http.client
            connection = http.client.HTTPConnection('127.0.0.1', ports[0], timeout=10)
            connection.request('GET', '/?fictional=1', headers={'Host':f'{HOST}:{ports[0]}'})
            response = connection.getresponse()
            assert response.status == 308 and response.getheader('Location') == settings['origin']+'/?fictional=1'
            response.read(); connection.close()
            connection = http.client.HTTPConnection('127.0.0.1', ports[0], timeout=10)
            connection.request('GET', '/.well-known/acme-challenge/fictional', headers={'Host':HOST})
            response = connection.getresponse(); assert response.status == 200 and response.read() == b'fictional ACME challenge'
            connection.close()
            # Normal system trust must reject the ephemeral certificate. Trust
            # only that certificate for successful test requests, never verify=False.
            with RejectedTLS(ports[1], trusted=False):
                pass
            with RejectedTLS(ports[1], trusted=True, certificate=proxy/'cert.pem', hostname='127.0.0.1'):
                pass
            passed('HTTP redirects preserve the path, only the ACME fixture is public, and untrusted/wrong-name TLS is rejected')

            form = dict(name='Fictional HTTPS comparison', common=dict(population=20, start_year=2019, end_year=2020),
                repetitions=3, baseline='baseline', auto_retry=True,
                run_sets=[dict(id='baseline', name='Fictional baseline', model_args={}),
                          dict(id='alternative-1', name='Fictional alternative one', model_args=dict(savingRate=.03), dataset_revision=inputs[1]),
                          dict(id='alternative-2', name='Fictional alternative two', model_args=dict(savingRate=.04)),
                          dict(id='provider', name='Fictional approved aggregate provider', model_args={}, dataset_revision=inputs[2])])
            review = alice.api('/api/review-experiment', dict(dataset=inputs[0], form=form))
            experiment = alice.api('/api/submit', dict(key='fictional-https', review=review['review']))['id']
            report['phase'] = 'fictional-output'
            print('Creating bounded-memory fictional output above the normal 512 MiB download threshold', flush=True)
            leases = [finish(queue, executor, payload_mib=args.payload_mib if number == 0 else 0) for number in range(4)]
            jobs = {lease.configuration_id:lease.job_id for lease in leases}
            overview = alice.api('/api/results/'+experiment)
            assert len(overview['configurations']) == 4
            assert [entry['downloadable'] for entry in overview['configurations']] == [True, True, True, False]
            for headers in ({}, {'Range':'bytes=0-63'}, {'Range':'bytes=-64'}):
                for suffix in ('', '?include_inputs=1'):
                    assert alice.request('/downloads/'+jobs['provider']+suffix, headers=headers)[0] == 403
                assert alice.request('/downloads/'+jobs['alternative-1']+'?baseline='+jobs['provider'], headers=headers)[0] == 403
            assert bob.request('/api/results/'+experiment)[0] == 403
            passed('real reviewed submissions retain four completed configurations; provider and mixed raw downloads remain denied through the proxy')

            report['phase'] = 'aggregate-publication-and-private-paths'
            selection = dict(baseline=jobs['baseline'], scenarios=[jobs[key] for key in ('alternative-1', 'alternative-2', 'provider')])
            visualiser = alice.api('/api/visualiser', selection)
            visualiser = until(lambda: ready_status(alice, '/api/visualiser/'+visualiser['id']))
            value = alice.api('/api/visualiser/'+visualiser['id']+'/data')
            aggregate_envelope(value)
            assert value['format'] == 'simpaths.visualiser.v2' and len(value['data']['series']) == 4
            assert RAW.decode() not in json.dumps(value)
            name = 'private-canary-'+secrets.token_hex(8)+'.txt'
            for directory in (state, state/'uploads', state/'execution', state/'execution/download-cache',
                              state/'execution/visualiser-cache', *[state/('prepared-'+str(i)) for i in range(3)]):
                (directory/name).write_bytes(RAW)
            extra = [quote(str(state/name), safe='/'), '/execution/'+leases[0].execution_key+'/work/output-606.csv',
                     '/static/'+leases[0].execution_key+'/work/private.log']
            report['privacy_checks'] = privacy_checks(alice.get, name, RAW, cookie=alice.cookie,
                other_cookie=bob.cookie, experiment=experiment, restricted_job=jobs['provider'],
                visualiser_key=visualiser['id'], extra_paths=extra)
            passed('anonymous and signed-in private/traversal probes are denied; aggregate sets contain fixed fields and reject anonymous/other-owner reads')

            pair = alice.api('/api/visualiser', dict(job=jobs['provider'], baseline=jobs['baseline']))
            pair = until(lambda: ready_status(alice, '/api/visualiser/'+pair['id']))
            aggregate_envelope(alice.api('/api/visualiser/'+pair['id']+'/data'))
            passed('existing pair and new multiple-alternative aggregate formats both pass the external privacy validator')

            report['phase'] = 'verified-large-archive'
            download_selection = dict(baseline=jobs['baseline'], scenarios=[jobs['alternative-1'], jobs['alternative-2']], include_inputs=True)
            download = alice.api('/api/downloads', download_selection)
            download = until(lambda: ready_status(alice, '/api/downloads/'+download['id']))
            assert download['state'] == 'ready'
            link = download['url']; assert link.startswith('/prepared-downloads/')
            status, headers, body = alice.request(link, method='HEAD')
            size, etag = int(headers['Content-Length']), headers['ETag']
            assert status == 200 and not body and headers['Accept-Ranges'] == 'bytes'
            assert 'no-store' in headers['Cache-Control'] and size > 1024**2
            report['archive_bytes'] = size
            for client in (bob, HTTPSClient(settings['origin'], proxy/'cert.pem')):
                for method, request_headers in [('GET', {}), ('HEAD', {}), ('GET', {'Range':'bytes=0-63', 'If-Range':etag}),
                                                 ('GET', {'If-None-Match':etag})]:
                    assert client.request(link, headers=request_headers, method=method)[0] == 403
                assert client.request('/api/downloads/'+download['id'])[0] == 403
            assert alice.request(link, headers={'Range':f'bytes={size}-'})[0] == 416
            suffix = alice.request(link, headers={'Range':'bytes=-64'})
            assert suffix[0] == 206 and len(suffix[2]) == 64
            with alice.open(link, headers={'Range':'bytes=0-63', 'If-Range':'"different"'}) as stale:
                assert stale.status == 200 and stale.headers['ETag'] == etag
                assert int(stale.headers['Content-Length']) == size
                assert stale.headers.get('Content-Range') is None and len(stale.read(64)) == 64
            passed('normal large-file preparation creates a verified compressed ZIP; HEAD/ranges/cache validators preserve authentication for every client')

            report['phase'] = 'slow-interrupted-transfer'
            received = work/'received.zip'
            started = time.monotonic()
            with alice.open(link) as response, received.open('wb') as target:
                assert response.status == 200
                while time.monotonic()-started < args.slow_seconds:
                    chunk = response.read(min(65536, max(1, size//(args.slow_seconds*4))))
                    if not chunk:
                        raise AssertionError('Slow-transfer fixture ended before its observation window')
                    target.write(chunk)
                    time.sleep(.5)
            prefix = received.stat().st_size
            assert 0 < prefix < size
            report['slow_transfer_seconds'] = round(time.monotonic()-started, 2)
            report['interrupted_after_bytes'] = prefix
            passed('a deliberately slow HTTPS transfer stays open beyond 30 seconds and can be interrupted before completion')

            report['phase'] = 'application-restart-and-resume'
            before = queue.inspect(owners['alice@example.org'], experiment)
            pending = alice.api('/api/submit', dict(key='queued-after-restart', review=review['review']))['id']
            pending_before = queue.inspect(owners['alice@example.org'], pending)
            processes.stop_app()
            processes.start_app(settings); until(ready, seconds=45)
            assert alice.api('/api/session')['signed_in'] is True and bob.api('/api/session')['signed_in'] is True
            assert queue.inspect(owners['alice@example.org'], experiment) == before
            assert queue.inspect(owners['alice@example.org'], pending) == pending_before
            assert alice.api('/api/visualiser/'+visualiser['id']+'/data') == value
            restarted = alice.api('/api/downloads/'+download['id'])
            assert restarted['state'] == 'ready' and restarted['url'] == link
            with alice.open(link, headers={'Range':f'bytes={prefix}-', 'If-Range':etag}) as response, received.open('ab') as target:
                assert response.status == 206 and response.headers['ETag'] == etag
                assert response.headers['Content-Range'] == f'bytes {prefix}-{size-1}/{size}'
                while chunk := response.read(1024**2): target.write(chunk)
            assert received.stat().st_size == size and '"'+digest_file(received)+'"' == etag
            with zipfile.ZipFile(received) as archive:
                names = archive.namelist()
                manifest = json.loads(archive.read(next(name for name in names if name.endswith('/manifest.json'))))
                assert manifest['format'] == 'jasmine-results-v2' and len(manifest['configurations']) == 3
                assert any('/Scenario_2/run_3/' in name for name in names)
                assert sum(name.endswith('/input/options.txt') for name in names) == 9
                assert all(entry.compress_type == zipfile.ZIP_DEFLATED for entry in archive.infolist())
                for entry in manifest['files']:
                    with archive.open(next(name for name in names if name.endswith('/'+entry['name']))) as stream:
                        assert hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256']
                assert sum(name.endswith('/population.csv') and '/Inputs_' in name for name in names) == 2
            report['archive_sha256'] = etag.strip('"')
            passed('a fresh application process preserves both owner cookies, queued/completed jobs and aggregate links; resumed bytes form the same checksum-verified ZIP')

            report['phase'] = 'source-deletion-and-revocation'
            deletion = alice.api('/api/review-output-deletion', dict(attempt=leases[0].attempt_id))
            alice.api('/api/submit', dict(key='delete-fictional-output', review=deletion['review']))
            def retired():
                # A just-finished response may still be releasing its reader
                # lock. Retry as the real dispatcher does, without bypassing it.
                retire_fixture_outputs(service)
                overview = alice.api('/api/results/'+experiment)
                return overview if overview['configurations'][0]['output_state'] == 'deleted' else None
            after = until(retired, seconds=15)
            removed = executor.workspace(leases[0])/'work'
            assert not any((removed/name).exists() for name in service.outputs.targets(leases[0]))
            assert (removed/'private.log').read_bytes() == RAW
            for path in (link, '/api/downloads/'+download['id'], '/api/visualiser/'+visualiser['id']+'/data'):
                assert alice.request(path, headers={'Range':'bytes=0-63'})[0] in (400, 403, 404)
            assert after['configurations'][1]['downloadable'] is True
            service.access.approve_email('alice@example.org', seconds=None)
            assert alice.request('/api/results/'+experiment)[0] == 403
            assert alice.request('/api/visualiser/'+pair['id']+'/data')[0] == 403
            assert alice.request('/downloads/'+jobs['alternative-1'])[0] == 403
            passed('source deletion and account revocation invalidate saved/resumed links through the proxy while other retained output remains intact')
            report['passed'] = True
        finally:
            report['phase_at_stop'] = report['phase']
            cleanup_errors = []
            try: processes.close()
            except Exception as error: cleanup_errors.append(type(error).__name__)
            if service is not None:
                try:
                    service.results.downloads.close(); service.visualiser.close()
                except Exception as error: cleanup_errors.append(type(error).__name__)
            # Bootstrap can fail before returning its Queue. The schema name is
            # still ours; never drop any other schema or persistent local state.
            try:
                import psycopg
                with psycopg.connect(dsn) as connection:
                    connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(settings['schema'])))
            except Exception as error: cleanup_errors.append(type(error).__name__)
            report['cleanup'] = not cleanup_errors
            if cleanup_errors:
                report['cleanup_errors'] = cleanup_errors
                report['passed'] = False


class RejectedTLS:
    """A negative TLS check; a successful connection is a test failure."""
    def __init__(self, port, *, trusted, certificate=None, hostname=HOST):
        self.port, self.hostname = port, hostname
        self.context = ssl.create_default_context(cafile=str(certificate) if trusted else None)

    def __enter__(self):
        with socket.create_connection(('127.0.0.1', self.port), timeout=10) as connection:
            try:
                with self.context.wrap_socket(connection, server_hostname=self.hostname):
                    raise AssertionError('Untrusted or wrong-name TLS handshake was accepted')
            except ssl.SSLError:
                pass

    def __exit__(self, *args):
        return False


def ready_status(client, path):
    value = client.api(path)
    if value['state'] in ('failed', 'expired', 'direct'):
        raise AssertionError('Fictional preparation did not produce the required ready publication')
    return value if value['state'] == 'ready' else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=frontend_path())
    parser.add_argument('--output', type=Path)
    parser.add_argument('--nginx', type=Path, help='Optional installed Nginx executable; otherwise use an isolated Docker image')
    parser.add_argument('--nginx-image', default=os.environ.get('SIMPATHS_PROXY_NGINX_IMAGE', 'nginx:stable-alpine'))
    parser.add_argument('--payload-mib', type=int, default=int(os.environ.get('SIMPATHS_PROXY_PAYLOAD_MIB', '520')))
    parser.add_argument('--slow-seconds', type=int, default=int(os.environ.get('SIMPATHS_PROXY_SLOW_SECONDS', '34')))
    parser.add_argument('--execute-proof', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--serve-fixture', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.serve_fixture:
        if not os.environ.get('JASMINE_BATCH_TEST_DSN'):
            parser.error('The private fixture requires the disposable test database')
        serve_fixture(args.serve_fixture); return 0
    if not 513 <= args.payload_mib <= 1024 or not 31 <= args.slow_seconds <= 600:
        parser.error('Use 513–1024 MiB fictional output and 31–600 seconds of slow transfer')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]*', args.nginx_image):
        parser.error('Invalid rehearsal Docker image')
    args.frontend = args.frontend.expanduser().resolve(strict=True)
    if args.nginx: args.nginx = args.nginx.expanduser().resolve(strict=True)
    os.environ['JASMINE_WEB_REPO'] = str(args.frontend)
    if not args.execute_proof:
        command = [sys.executable, str(args.frontend/'scripts/test_batch_queue.py'), '--proof-only',
            '--proof-script', str(Path(__file__).resolve()), '--proof-requirements', str(Path(__file__).with_name('requirements.txt'))]
        evidence = args.output or Path.home()/'simpaths-benchmarks'/('https-rehearsal-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        command.extend(['--output', str(evidence)])
        environment = dict(os.environ, SIMPATHS_PROXY_NGINX_IMAGE=args.nginx_image,
            SIMPATHS_PROXY_PAYLOAD_MIB=str(args.payload_mib), SIMPATHS_PROXY_SLOW_SECONDS=str(args.slow_seconds))
        if args.nginx: environment['SIMPATHS_PROXY_NGINX'] = str(args.nginx)
        return subprocess.run(command, env=environment).returncode
    if not os.environ.get('JASMINE_BATCH_TEST_DSN') or args.output is None:
        parser.error('Use this command without --execute-proof to create an isolated test database')
    if os.environ.get('SIMPATHS_PROXY_NGINX'): args.nginx = Path(os.environ['SIMPATHS_PROXY_NGINX']).resolve(strict=True)
    if args.output.exists(): parser.error('Choose a new evidence directory')
    if shutil.disk_usage(tempfile.gettempdir()).free < 2*1024**3:
        parser.error('Need 2 GiB free on the temporary-files filesystem for the fictional rehearsal')
    sys.path.insert(0, str(args.frontend))
    require_test_database(os.environ['JASMINE_BATCH_TEST_DSN'])
    report = dict(started_at=datetime.now(timezone.utc).isoformat(), passed=False, cleanup=False,
                  synthetic=True, production_acceptance=False, checks=[], phase='starting')
    try:
        rehearsal(args, report)
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        report['passed'] = False
        report['error_type'] = type(error).__name__
        if isinstance(error, AssertionError): report['message'] = str(error)[:500]
        print('STOPPED: '+type(error).__name__+' during '+report['phase']+'; inspect the private logs.', file=sys.stderr)
    finally:
        if args.output.exists():
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(('PASSED' if report['passed'] and report['cleanup'] else 'FAILED')+': '+str(args.output/'report.json'))
    return 0 if report['passed'] and report['cleanup'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
