#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Prove maintenance responses in an isolated native Nginx without touching services.
Use fictional loopback upstreams, a private configuration and the actual page.
@author ross richardson
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import tempfile
import threading
import time


PAGE_ALIAS = '/opt/simpaths-online/JAS-mine-web/static/service_unavailable.html'
INTERNAL_URI = '/__simpaths_service_unavailable'


def check_response(status, headers, body, page):
    if (status != 503 or body != page or headers.get('retry-after') != '30'
            or headers.get('cache-control') != 'no-store'
            or not headers.get('content-type', '').startswith('text/html')
            or headers.get('location') or headers.get('refresh')):
        raise AssertionError('A proxy outage must serve the page once with retry/non-cache headers')


def execute(nginx, snippet, page, output):
    """Bind only ephemeral loopback ports; never load the system Nginx config."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = snippet.read_text()
    body = page.read_bytes()
    if source.count(PAGE_ALIAS) != 1 or b'The service is temporarily unavailable' not in body:
        raise ValueError('Require the reviewed page and its single fixed alias')
    if 'proxy_intercept_errors off;' not in source:
        raise ValueError('Intentional application errors must retain their own response bodies')
    checks = []
    process = None
    backend = None
    thread = None
    root = Path(tempfile.mkdtemp(prefix='simpaths-nginx-maintenance-'))
    report = dict(passed=False, source_files={str(snippet): hashlib.sha256(snippet.read_bytes()).hexdigest(),
                  str(page): hashlib.sha256(body).hexdigest()}, checks=checks)
    try:
        # The real worker identity must be able to read only these public fixtures.
        root.chmod(0o755)
        (root / 'page.html').write_bytes(body)
        (root / 'page.html').chmod(0o444)
        (root / 'maintenance.conf').write_text(source.replace(PAGE_ALIAS, str(root / 'page.html')))
        (root / 'maintenance.conf').chmod(0o444)
        posts = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                status = {'/busy': 503, '/application-502': 502, '/application-504': 504}.get(self.path, 200)
                payload = ('fictional application response ' + str(status)).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                posts.append((self.path, self.rfile.read(int(self.headers.get('Content-Length', '0')))))
                self.do_GET()

        backend = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        backend.daemon_threads = True
        thread = threading.Thread(target=backend.serve_forever, daemon=True)
        thread.start()
        with socket.socket() as unavailable, socket.socket() as reserved:
            # A bound, non-listening socket keeps the unavailable endpoint unused.
            unavailable.bind(('127.0.0.1', 0))
            reserved.bind(('127.0.0.1', 0))
            port = reserved.getsockname()[1]
            worker = 'user www-data;\n' if os.geteuid() == 0 else ''
            if worker:
                pwd.getpwnam('www-data')
            config = root / 'nginx.conf'
            config.write_text(worker + f'''
daemon off;
master_process on;
worker_processes 1;
pid {root}/nginx.pid;
error_log {root}/error.log warn;
events {{ worker_connections 32; }}
http {{
access_log off;
client_body_temp_path {root}/client-temp;
proxy_temp_path {root}/proxy-temp;
server {{
    listen 127.0.0.1:{port};
    server_name localhost;
    include {root}/maintenance.conf;
    location ~ ^/(private|uploads|artifacts)(/|$) {{ return 404; }}
    location /gone {{
        proxy_pass http://127.0.0.1:{unavailable.getsockname()[1]};
        proxy_connect_timeout 1s;
    }}
    location / {{ proxy_pass http://127.0.0.1:{backend.server_port}; }}
}}
}}
''')
            checked = subprocess.run([str(nginx), '-t', '-p', str(root) + '/', '-c', str(config)],
                                     capture_output=True, timeout=15)
            (output / 'syntax.log').write_bytes(checked.stdout + checked.stderr)
            if checked.returncode:
                raise RuntimeError('The isolated Nginx syntax check failed')
            reserved.close()
            with (output / 'nginx.log').open('wb') as log:
                process = subprocess.Popen([str(nginx), '-p', str(root) + '/', '-c', str(config)],
                                           stdout=log, stderr=log)

                def request(path, method='GET', data=None):
                    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                    try:
                        connection.request(method, path, data)
                        response = connection.getresponse()
                        return response.status, dict((k.lower(), v) for k, v in response.getheaders()), response.read()
                    finally:
                        connection.close()

                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('The isolated Nginx exited before readiness')
                    try:
                        response = request('/healthy')
                        break
                    except (OSError, http.client.HTTPException):
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
                if response[0] != 200 or response[2] != b'fictional application response 200':
                    raise AssertionError('Healthy application traffic changed')
                checks.append('healthy upstream response preserved')
                for path, status in (('/busy', 503), ('/application-502', 502), ('/application-504', 504)):
                    response = request(path)
                    if (response[0] != status or response[2] != ('fictional application response ' + str(status)).encode()
                            or 'retry-after' in response[1]):
                        raise AssertionError('An intentional application error was replaced')
                checks.append('application busy and error responses preserved')
                response = request('/busy', 'POST', b'fictional form')
                if response[0] != 503 or posts != [('/busy', b'fictional form')]:
                    raise AssertionError('The proxy replayed or hid an application POST')
                checks.append('application POST handled once')
                for method in ('GET', 'POST'):
                    response = request('/gone', method, b'fixture' if method == 'POST' else None)
                    check_response(*response, body)
                checks.append('unavailable upstream GET and POST use non-cacheable 503 page')
                if posts != [('/busy', b'fictional form')]:
                    raise AssertionError('A failed POST was replayed to another upstream')
                for path in (INTERNAL_URI, '/private/settings', '/uploads/input.csv', '/artifacts/result.csv'):
                    response = request(path)
                    if response[0] != 404 or response[2] == body:
                        raise AssertionError('Internal maintenance or private paths became public')
                checks.append('internal maintenance and private paths remain inaccessible')
                response = request('/healthy')
                if response[0] != 200 or response[2] != b'fictional application response 200':
                    raise AssertionError('An earlier maintenance response persisted after recovery')
                checks.append('healthy traffic still works after the outage response')
                report['passed'] = True
    finally:
        if process is not None and process.poll() is None:
            # Stop only this proof's master; it never shares the system PID file.
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if backend is not None:
            backend.shutdown()
            backend.server_close()
        if thread is not None:
            thread.join(timeout=5)
        shutil.rmtree(root)
        report['cleanup'] = process is None or process.poll() is not None
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        (output / 'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Generated fictional Nginx maintenance proof records.\n@author ross richardson\n-->\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx', type=Path, default=Path('/usr/sbin/nginx'))
    parser.add_argument('--snippet', type=Path, default=Path(__file__).with_name('nginx_maintenance.conf'))
    parser.add_argument('--page', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = execute(args.nginx, args.snippet, args.page, args.output)
    for check in report['checks']:
        print('PASS: ' + check, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
