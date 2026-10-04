#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Disposable SingleRun HTTPS proof: real routes, PostgreSQL and Nginx, fictional Java.
No deployment, real simulation, email, host trust change or persistent Docker state.
@author ross richardson
"""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy._workflow import frontend_path
from deploy.multirun.proxy_rehearsal import (HTTPSClient, Processes, RejectedTLS,
    free_port, proxy_config, require_test_database, until)


def single_proxy_config(text, **options):
    policy = re.sub(r'#.*', '', text)
    if any(anchor not in policy for anchor in ('client_max_body_size 512m;',
            'add_header Cache-Control "no-store" always;', 'proxy_hide_header Strict-Transport-Security;')):
        raise ValueError('SingleRun proxy upload/cache protections changed')
    return proxy_config(text, domain='singlerun.example.org', upstream_port=5001,
                        timeout_seconds=650, **options)


class SingleClient(HTTPSClient):
    def __init__(self, *args):
        super().__init__(*args)
        self.cookies = {}

    def capture(self, headers):
        for field in headers.get_all('Set-Cookie', []):
            parsed = SimpleCookie(field)
            for key, value in parsed.items():
                if not key.startswith('__jasmine_owner_'):
                    continue
                if (not value['secure'] or not value['httponly'] or value['samesite'].lower() != 'lax'
                        or value['domain'] or value['path'] != '/'):
                    raise AssertionError('SingleRun ownership cookie protections changed')
                if value['max-age'] == '0': self.cookies.pop(key, None)
                else: self.cookies[key] = value.value
        self.cookie = '; '.join(key+'='+value for key, value in self.cookies.items())

    def form(self, path, fields=None):
        headers = {'Origin':self.origin, 'Content-Type':'application/x-www-form-urlencoded'}
        if self.cookie: headers['Cookie'] = self.cookie
        try:
            response = self.opener.open(Request(self.origin+path, data=urlencode(fields or {}).encode(), headers=headers), timeout=60)
        except HTTPError as error:
            response = error
        with response:
            result = response.status, response.headers, response.read(65537)
        self.capture(result[1])
        return result


def stream_records(state):
    return [json.loads(path.read_text()) for path in sorted(state.glob('stream-*.json'))]


def streams_closed(state, count):
    values = stream_records(state)
    if len(values) != count or any(not value['closed'] for value in values):
        return False
    if any(value['close_count'] != 1 for value in values):
        raise AssertionError('Upstream response closed more than once')
    return values


def archive_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def receive_archive(client, link, destination, expected):
    with client.open(link) as response, destination.open('wb') as target:
        assert response.status == 200 and 'no-store' in response.headers.get('Cache-Control', '')
        assert response.headers['Content-Type'] == 'application/zip'
        while chunk := response.read(1024**2): target.write(chunk)
    assert archive_hash(destination) == expected
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert archive.read('run_1/input/options.txt').startswith(b'seed=606\n')
    return response.headers


def allocations(queue):
    with queue._connection() as connection:
        return connection.execute('SELECT * FROM interactive_reservations ORDER BY vm_schema,session_id').fetchall()


def rehearsal(args, report):
    from psycopg import sql
    import psycopg
    from jasmine_web.batch.local_executor import atomic_json
    from jasmine_web.batch.policy import Resources, Policy
    from jasmine_web.batch.store import Queue
    from jasmine_web.vm_state import PostgresVMState
    from deploy.multirun.artifacts import write_attribution
    from deploy.acceptance._https_fixture import create_archive, MODEL, RAW
    dsn = os.environ['JASMINE_BATCH_TEST_DSN']
    args.output.mkdir(parents=True, mode=0o700)
    write_attribution(args.output)
    with tempfile.TemporaryDirectory(prefix='simpaths-single-https-') as temporary:
        work = Path(temporary); state = work/'private'; proxy = work/'proxy'
        state.mkdir(mode=0o700); proxy.mkdir(mode=0o700)
        ports = set()
        while len(ports) < 3: ports.add(free_port())
        http_port, https_port, app_port = sorted(ports)
        settings = dict(state=str(state), schema='test_single_https_'+uuid4().hex,
            batch_schema='test_single_https_'+uuid4().hex, origin=f'https://localhost:{https_port}',
            app_port=app_port, secret=secrets.token_urlsafe(32), admin=secrets.token_urlsafe(32),
            catalogue=str(state/'catalogue.json'), archive=str(state/'fictional.zip'))
        processes = Processes(work, args.output, args)
        store = None
        def passed(message):
            report['checks'].append(message); print('PASS: '+message, flush=True)
        try:
            report['phase'] = 'local-checks'
            import unittest
            sys.path.insert(0, str(args.frontend/'tests'))
            with (args.output/'local-checks.log').open('w') as log:
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames([
                    'deploy.acceptance.test_https_rehearsal', 'deploy.multirun.test_proxy_rehearsal',
                    'deploy.multirun.test_vm_config', 'test_proxy_stream_cleanup']))
            report['local_checks'] = dict(tests=result.testsRun, errors=len(result.errors), failures=len(result.failures), skipped=len(result.skipped))
            assert result.wasSuccessful() and not result.skipped, 'Local fixture/streaming checks failed'
            passed('proxy adaptation, isolated fixtures and download-close regressions pass without skips')

            report['phase'] = 'temporary-tls-and-private-state'
            with (args.output/'certificate.log').open('wb') as log:
                subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                    '-keyout',str(proxy/'key.pem'),'-out',str(proxy/'cert.pem'),'-subj','/CN=localhost',
                    '-addext','subjectAltName=DNS:localhost'], check=True, stdout=log, stderr=subprocess.STDOUT, timeout=30)
            (proxy/'key.pem').chmod(0o600)
            source = (args.frontend/'deploy/nginx.vm.https.conf').read_text()
            runtime = work/'nginx-runtime' if args.nginx else Path('/tmp')
            if args.nginx: runtime.mkdir(mode=0o700)
            rendered = single_proxy_config(source, http_port=http_port, https_port=https_port,
                app_port=app_port, base=proxy if args.nginx else Path('/rehearsal'), runtime=runtime)
            (proxy/'nginx.conf').write_text(rendered)
            challenge = proxy/'acme/.well-known/acme-challenge'
            challenge.mkdir(parents=True); (challenge/'fictional').write_text('fictional SingleRun challenge')
            (args.output/'proxy-template.conf').write_text(source); (args.output/'proxy-rendered.conf').write_text(rendered)
            report['template_sha256'] = hashlib.sha256(source.encode()).hexdigest()
            atomic_json(Path(settings['catalogue']), {'models':[MODEL]})
            create_archive(Path(settings['archive']), args.payload_mib)
            expected = archive_hash(Path(settings['archive']))
            report['archive_bytes'] = Path(settings['archive']).stat().st_size
            report['archive_sha256'] = expected
            queue = Queue(dsn, 'single-https', schema=settings['batch_schema']); queue.migrate()
            queue.create_pool(Resources(4000,4096,8192), policy=Policy(attempt_seconds=600,total_seconds=1800,lease_seconds=300))
            queue.register_dataset('fictional','a'*64); queue.approve('fictional-batch'); queue.grant_dataset('fictional-batch','fictional')
            store = PostgresVMState(dsn, schema=settings['schema'], shared_pool=dict(pool_id='single-https',schema=settings['batch_schema']))
            store.migrate()
            processes.start_app(settings, script=Path(__file__).resolve(), workdir=args.frontend)
            processes.start_proxy(proxy/'nginx.conf'); report['nginx_image'] = processes.image
            alice, bob, anonymous = [SingleClient(settings['origin'], proxy/'cert.pem') for _ in range(3)]
            def ready():
                try: return anonymous.request('/')[0] == 200
                except (OSError, URLError): return False
            until(ready, seconds=45)
            status, headers, _ = anonymous.request('/')
            assert status == 200 and len(headers.get_all('Strict-Transport-Security', [])) == 1
            connection = http.client.HTTPConnection('127.0.0.1',http_port,timeout=10)
            connection.request('GET','/?fictional=1',headers={'Host':'localhost'})
            response = connection.getresponse()
            assert response.status == 308 and response.getheader('Location') == settings['origin']+'/?fictional=1'
            response.read(); connection.close()
            connection = http.client.HTTPConnection('127.0.0.1',http_port,timeout=10)
            connection.request('GET','/.well-known/acme-challenge/fictional',headers={'Host':'localhost'})
            response=connection.getresponse(); assert response.status == 200 and response.read() == b'fictional SingleRun challenge'
            connection.close()
            with RejectedTLS(https_port,trusted=False): pass
            with RejectedTLS(https_port,trusted=True,certificate=proxy/'cert.pem',hostname='127.0.0.1'): pass
            passed('real TLS, preserved HTTP redirects/ACME, and rejection of untrusted or wrong-host certificates')

            report['phase'] = 'launch-and-owner-isolation'
            sessions=[]
            for client in (alice,bob):
                status,headers,_=client.form('/launch',{'model_key':MODEL['id']})
                assert status == 303 and headers['Location'].startswith('/sim/')
                sid=headers['Location'].split('/')[-1]; sessions.append(sid)
                until(lambda: client.api('/session-state/'+sid)['status']=='ready')
                assert len(client.cookies)==1
                status, _, body = client.request('/sim/'+sid)
                assert status == 200 and MODEL['name'].encode() in body
            a,b=sessions
            private=[store.peek_session(sid) for sid in sessions]
            credentials=[value[key] for value in private for key in ('backend_secret','reset_secret')]
            assert len(set(credentials))==4
            for client,sid in ((anonymous,a),(bob,a),(alice,b)):
                for path in ('/status/','/charts/','/logs/','/session-state/'):
                    assert client.request(path+sid)[0]==403
                for headers in ({},{'Range':'bytes=0-63'},{'Range':'bytes=-64'}):
                    assert client.request('/java/'+sid+'/simulation/export/zip',headers=headers)[0]==403
                assert client.request('/reset/'+sid,value={})[0]==403
                assert client.form('/leave/'+sid)[0]==303
            bad=SingleClient(settings['origin'],proxy/'cert.pem'); bad.cookie=alice.cookie+'tampered'
            assert bad.request('/status/'+a)[0]==403
            assert alice.request('/pause/'+a,value={},headers={'Origin':'https://wrong.example.org'})[0]==403
            assert not stream_records(state), 'Denied downloads reached the upstream model'
            passed('real launches issue Secure/HttpOnly/Lax ownership cookies; other owners, forged cookies and foreign-origin controls are denied')

            report['phase'] = 'controls-and-shared-capacity'
            batch=queue.submit('fictional-batch','active',label='Fictional background job',model_digest='sha256:'+'b'*64,
                dataset_id='fictional',seed_plan=['606'],run_sets=[dict(id='background',parameters={})],resources=Resources(2000,1024,1024))
            lease=queue.claim('fictional-worker'); assert lease is not None; queue.started(lease)
            assert queue.occupancy()['available']['cpu_millis']==0 and len(allocations(queue))==2
            denied=anonymous.form('/launch',{'model_key':MODEL['id']})
            assert denied[0]==303 and '?error=' in denied[1]['Location'] and len(store.get_all_sessions())==2
            for client,sid,seed in ((alice,a,123),(bob,b,909)):
                assert client.api('/build-json/'+sid,dict(seed=seed,endYear=2026))['status']=='building'
                until(lambda: client.api('/status/'+sid)['built'] is True)
                for endpoint,outcome in (('start-sim','started'),('pause','paused'),('reset','reset')):
                    assert client.api('/'+endpoint+'/'+sid,{})['status']==outcome
                assert archive_hash(Path(settings['archive'])) == expected
                assert client.api('/start-sim/'+sid,{})['status']=='started'
                assert client.api('/charts/'+sid)['charts'][0]['data'][0]['y']==seed
                assert client.api('/logs/'+sid)['logs']
                body=client.request('/session-state/'+sid)[2]
                assert not any(value.encode() in body for value in credentials)
            spoofed=alice.api('/java/'+a+'/simulation/parameters')
            assert spoofed['seed']==123
            status,_,body=alice.request('/java/'+a+'/simulation/parameters',headers={'X-Jasmine-Backend-Token':private[1]['backend_secret']})
            assert status==200 and json.loads(body)['seed']==123
            passed('Build/Start/Pause/Reset and populated charts/logs work over HTTPS; shared PostgreSQL capacity blocks another launch and backend credentials stay private')

            report['phase'] = 'private-file-boundary'
            canary='canary-'+secrets.token_hex(8)+'.txt'; (state/canary).write_bytes(RAW)
            paths=['/'+canary,'/static/'+canary,'/private/'+canary,'/uploads/'+canary,'/execution/'+canary,
                '/backups/'+canary,'/diagnostics/'+canary,'/download-cache/'+canary,'/.env','/.git/config',
                '/static/../private/'+canary,'/static/%2e%2e/private/'+canary,quote(str(state/canary),safe='/')]
            checks=[]
            for client in (anonymous,alice,bob):
                for path in paths:
                    status,_,body=client.request(path,maximum=65536)
                    assert status in(400,403,404) and RAW not in body
                    checks.append(status)
            report['private_path_checks']=len(checks)
            passed('private files, canaries, dotfiles and traversal stay inaccessible anonymously and to both signed-in owners')

            report['phase'] = 'slow-stream-and-disconnect'
            link='/java/'+a+'/simulation/export/zip'; began=time.monotonic(); received=0
            with alice.open(link) as response:
                assert response.status==200 and response.headers.get('Content-Length') is None
                assert 'no-store' in response.headers.get('Cache-Control','')
                while time.monotonic()-began < args.slow_seconds:
                    part=response.read(65536); assert part
                    received+=len(part); time.sleep(.5)
                assert any(not value['closed'] for value in stream_records(state))
                assert alice.api('/status/'+a)['status']=='running'
                assert bob.api('/charts/'+b)['charts'][0]['data'][0]['y']==909
            closed=until(lambda: streams_closed(state,1),seconds=15)
            assert received < report['archive_bytes'] and closed[0]['bytes'] < report['archive_bytes']
            report.update(slow_transfer_seconds=round(time.monotonic()-began,2),interrupted_after_bytes=received)
            headers=receive_archive(alice,link,work/'received.zip',expected)
            assert headers.get('Content-Length') is None
            until(lambda: streams_closed(state,2),seconds=15)
            headers=receive_archive(alice,link+'?length=1',work/'received.zip',expected)
            assert int(headers['Content-Length'])==report['archive_bytes']
            until(lambda: streams_closed(state,3),seconds=15)
            passed('chunked download stays live beyond 30 seconds; disconnect closes upstream once; subsequent chunked and fixed-length ZIPs match the complete checksum')

            report['phase'] = 'frontend-restart'
            before=allocations(queue); model_before=[json.loads((state/('model-'+sid+'.json')).read_text()) for sid in sessions]
            batch_before=queue.inspect('fictional-batch',batch)
            processes.stop_app(); processes.start_app(settings,script=Path(__file__).resolve(),workdir=args.frontend)
            until(ready,seconds=45)
            assert allocations(queue)==before and queue.inspect('fictional-batch',batch)==batch_before
            assert [json.loads((state/('model-'+sid+'.json')).read_text()) for sid in sessions]==model_before
            for sid,value in zip(sessions,private):
                current=store.peek_session(sid)
                assert all(current[key]==value[key] for key in ('container_id','host','backend_secret','reset_secret','resources','created_at'))
            assert alice.api('/status/'+a)['status']=='running' and bob.api('/status/'+b)['status']=='running'
            assert alice.api('/java/'+a+'/simulation/parameters')['seed']==123
            receive_archive(alice,link,work/'received.zip',expected)
            until(lambda: streams_closed(state,4),seconds=15)
            passed('a fresh frontend process retains both ownership cookies, fictional models/settings and SingleRun/MultiRun reservations; the saved download URL still works')

            report['phase'] = 'leave-and-capacity-release'
            assert alice.form('/leave/'+a)[0]==303 and not alice.cookies
            assert store.peek_session(a) is None and len(allocations(queue))==1
            assert bob.api('/status/'+b)['status']=='running' and queue.inspect('fictional-batch',batch)==batch_before
            assert alice.request(link)[0] in(403,404)
            assert bob.form('/leave/'+b)[0]==303 and store.peek_session(b) is None and allocations(queue)==[]
            assert queue.inspect('fictional-batch',batch)==batch_before
            queue.finish(lease,outcome='cancelled',stop_evidence='c'*64)
            assert queue.occupancy()['available']['cpu_millis']==4000
            report['upstream_streams']=len(stream_records(state))
            passed('Leave removes only its owner model/cookie/allocation; the other owner and background attempt remain operable; final capacity is released')
            report['passed']=True
        finally:
            errors=[]
            try: processes.close()
            except Exception as error: errors.append(type(error).__name__)
            if store is not None: store.close()
            try:
                with psycopg.connect(dsn) as connection:
                    for name in (settings['schema'],settings['batch_schema']):
                        connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(name)))
            except Exception as error: errors.append(type(error).__name__)
            report['cleanup']=not errors
            if errors: report['cleanup_errors']=errors; report['passed']=False


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--output',type=Path)
    parser.add_argument('--nginx',type=Path)
    parser.add_argument('--nginx-image',default=os.environ.get('SIMPATHS_SINGLE_HTTPS_IMAGE','nginx:stable-alpine'))
    parser.add_argument('--payload-mib',type=int,default=int(os.environ.get('SIMPATHS_SINGLE_HTTPS_MIB','64')))
    parser.add_argument('--slow-seconds',type=int,default=int(os.environ.get('SIMPATHS_SINGLE_HTTPS_SECONDS','34')))
    parser.add_argument('--serve-fixture',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv); os.umask(0o077)
    if args.serve_fixture:
        from deploy.acceptance._https_fixture import serve
        serve(json.loads(args.serve_fixture.read_text())); return 0
    args.frontend=args.frontend.expanduser().resolve(strict=True)
    os.environ['JASMINE_WEB_REPO']=str(args.frontend)
    sys.path.insert(0,str(args.frontend))
    if not 32<=args.payload_mib<=1024 or not 31<=args.slow_seconds<=300:
        parser.error('Use 32–1024 MiB output and 31–300 seconds of slow transfer')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]*',args.nginx_image): parser.error('Invalid Nginx image')
    if args.nginx: args.nginx=args.nginx.expanduser().resolve(strict=True)
    if not args.execute_proof:
        environment=dict(os.environ,SIMPATHS_SINGLE_HTTPS_IMAGE=args.nginx_image,
            SIMPATHS_SINGLE_HTTPS_MIB=str(args.payload_mib),SIMPATHS_SINGLE_HTTPS_SECONDS=str(args.slow_seconds))
        if args.nginx: environment['SIMPATHS_SINGLE_HTTPS_NGINX']=str(args.nginx)
        output=args.output or Path.home()/'simpaths-benchmarks'/('singlerun-https-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        return subprocess.run([sys.executable,str(args.frontend/'scripts/test_batch_queue.py'),'--proof-only',
            '--proof-script',str(Path(__file__).resolve()),'--proof-requirements',str(args.frontend/'requirements-vm.txt'),
            str(ROOT/'deploy/multirun/requirements.txt'),
            '--output',str(output)],env=environment).returncode
    require_test_database(os.environ['JASMINE_BATCH_TEST_DSN'])
    if args.output is None or args.output.exists(): parser.error('Choose a new evidence directory')
    if os.environ.get('SIMPATHS_SINGLE_HTTPS_NGINX'): args.nginx=Path(os.environ['SIMPATHS_SINGLE_HTTPS_NGINX']).resolve(strict=True)
    if shutil.disk_usage(tempfile.gettempdir()).free < 2*1024**3: parser.error('Need 2 GiB free on the temporary filesystem')
    report=dict(passed=False,cleanup=False,synthetic=True,production_acceptance=False,
        started_at=datetime.now(timezone.utc).isoformat(),checks=[],phase='starting')
    try: rehearsal(args,report)
    except (Exception,KeyboardInterrupt,SystemExit) as error:
        report['passed']=False; report['error_type']=type(error).__name__
        if isinstance(error,AssertionError): report['message']=str(error)[:500]
        print('STOPPED: '+type(error).__name__+' during '+report['phase']+'; inspect the private logs.',file=sys.stderr)
    if args.output.exists():
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(('PASSED' if report['passed'] and report['cleanup'] else 'FAILED')+': '+str(args.output/'report.json'))
    return 0 if report['passed'] and report['cleanup'] else 1


if __name__=='__main__': raise SystemExit(main())
