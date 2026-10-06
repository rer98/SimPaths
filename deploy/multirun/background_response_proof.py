#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Measure protected HTTP polling and short leases during aggregate, ZIP and file work.
Uses disposable PostgreSQL, one real HTTP event loop and entirely fictional files.
The slow-filesystem delays belong only to this proof, never service settings.
@author ross richardson
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Event, Lock, Thread
import time
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy._workflow import frontend_path
from deploy.multirun.artifacts import write_attribution, write_json
from deploy.multirun.database_response_proof import describe


def execute(output):
    sys.path[:0]=[str(frontend_path()),str(frontend_path()/'tests/batch')]
    import httpx
    import psycopg
    import uvicorn
    from jasmine_web.batch.aggregate_io import AggregateFiles
    from jasmine_web.batch.browser import create_app
    from jasmine_web.batch.attempt_cleanup import AttemptCleanup
    from jasmine_web.batch.local_executor import atomic_json, read_json
    from jasmine_web.batch.output_management import OutputManagement, remove_targets
    from jasmine_web.batch.policy import Policy
    from jasmine_web.batch.results import Results
    from jasmine_web.batch.storage import Storage, measured_bytes
    from jasmine_web.batch.visualiser import Visualiser
    from jasmine_web.batch.worker import Worker
    from result_fixture import deletion_targets, result_name
    from test_results import ResultsTests
    from test_visualiser import Backend, row

    output.mkdir(mode=0o700,parents=True)
    write_attribution(output)
    report=dict(passed=False,cleanup=False,fictional_workload=True,defaults_changed=False,
        lease_seconds=5,polling_clients=4,aggregate_rows=120000,
        slow_filesystem_test_delay_seconds=.04,phases=[])
    fixture=ResultsTests()
    server=server_thread=visualiser=None
    clients=[]
    failed_work=None
    try:
        fixture.setUp()
        http=fixture.http
        q,s,executor=fixture.q,fixture.s,fixture.executor
        policy=asdict(Policy(attempt_seconds=300,total_seconds=600,lease_seconds=5,retry_delay_seconds=1))
        fixture.f.fixture.sql('UPDATE pools SET policy=%s::jsonb WHERE id=%s',(json.dumps(policy),q.pool_id))

        def catalogue(lease,work):
            # Like the production adapter, load small sealed receipts. Archives
            # independently hash every source chunk; never reparse raw file data.
            return read_json(work/'fictional-catalogue.json')

        s.results=Results(s,executor,catalogue,name=result_name,
            downloads=dict(threshold=32*1024**2,capacity=512*1024**2,reserve=0))
        fixture.f.a.approve_email('bob@example.org')
        foreign=http.browser('bob@example.org')

        def submit(key,count):
            form=dict(http.form,run_sets=[dict(id='configuration-'+str(i),name='Fictional '+str(i),
                model_args=dict(savingRate=.04+.001*i)) for i in range(count)],baseline='configuration-0')
            reviewed=http.client.post('/api/review-experiment',json=dict(dataset=fixture.dataset,form=form))
            fixture.assertEqual(reviewed.status_code,200,reviewed.text)
            response=http.client.post('/api/submit',json=dict(key=key,review=reviewed.json()['review']))
            fixture.assertEqual(response.status_code,201,response.text)
            return response.json()['id']

        def workspace(lease):
            path=executor.workspace(lease)
            path.mkdir(mode=0o700)
            atomic_json(path/'identity.json',executor._identity(lease))
            work=path/'work';work.mkdir()
            return work

        def finish(*,large=False,outcome='success'):
            lease=q.claim('background-response-fixture');fixture.assertIsNotNone(lease)
            fixture.assertIsNone(q.started(lease))
            work=workspace(lease)
            guard=Worker(q,SimpleNamespace(),SimpleNamespace(),lease.worker)
            guard.leases[lease.attempt_id]=lease
            receipts=[]
            with guard._output_heartbeats():
                for index,seed in enumerate(lease.specification['seeds']):
                    path=work/('output-'+seed+'.csv')
                    digest=hashlib.sha256()
                    with path.open('wb') as stream:
                        if large and index==0:
                            stream.write(b'seed,value\n');digest.update(b'seed,value\n')
                            for _ in range(128):
                                # Hex text is fictional valid CSV, with enough
                                # variation to exercise compression and hashing.
                                chunk=(seed+',').encode()+os.urandom(512*1024).hex().encode()+b'\n'
                                stream.write(chunk);digest.update(chunk)
                        else:
                            chunk=('seed,value\n'+seed+',1\n').encode()
                            stream.write(chunk);digest.update(chunk)
                    receipts.append(dict(seed=seed,fingerprint=digest.hexdigest(),files=[dict(
                        path=path.name,name='Values.csv',bytes=path.stat().st_size,sha256=digest.hexdigest())]))
                atomic_json(work/'fictional-catalogue.json',receipts)
                (work/'private.log').write_text('PRIVATE_DIAGNOSTIC_SENTINEL')
                if outcome=='success':
                    for ordinal,receipt in enumerate(receipts):
                        q.record_repetition(lease,ordinal,receipt['seed'],receipt['fingerprint'])
            q.finish(lease,outcome=outcome,stop_evidence='f'*64)
            return lease,work,receipts

        completed_experiment=submit('completed-comparison',5)
        completed=[finish(large=index==0) for index in range(5)]
        baseline=completed[0][0]
        baseline_hash=completed[0][2][0]['fingerprint']
        report['zip_source_bytes']=completed[0][2][0]['files'][0]['bytes']
        trash=completed[-1]
        for i in range(160):(trash[1]/('shard-'+str(i)+'.csv')).write_text('fictional disposable output')
        submit('failed-scratch',1)
        failed,failed_work,_=finish(outcome='model')
        (executor.workspace(failed)/'execution.log').write_text('PRIVATE_DIAGNOSTIC_SENTINEL')
        scratch=failed_work/'scratch';scratch.mkdir()
        for i in range(1500):(scratch/str(i)).write_bytes(b'fictional scratch')
        active_experiment=submit('active-attempt',1)
        active=q.claim('background-response-worker');fixture.assertIsNotNone(active)
        fixture.assertIsNone(q.started(active))
        active_work=workspace(active)
        worker=Worker(q,executor,SimpleNamespace(),active.worker)
        worker.leases[active.attempt_id]=active
        frozen=q.inspect(fixture.f.owner,active_experiment)['specification']

        class FileBackend(Backend):
            def process_files(self,sources,work,command,progress,*,comparison_set=False):
                fixture.assertTrue(comparison_set)
                paths=[]
                for index,source in enumerate(sources):
                    path=work/('aggregate-'+str(index)+'.json')
                    encoded=json.dumps(row(source['configuration']['role'].lower())).encode()
                    with path.open('wb') as stream:
                        stream.write(b'['+encoded)
                        block=(b','+encoded)*1000
                        for _ in range(29):stream.write(block)
                        stream.write((b','+encoded)*999+b']')
                    paths.append(path)
                    progress((index+1)*3)
                return AggregateFiles(tuple(paths),'Fictional levels only.')

        backend=FileBackend();backend.root=executor.root
        visualiser=Visualiser(s.results,backend)
        s.visualiser=visualiser
        s.storage=Storage(s,executor.root)
        s.outputs=OutputManagement(s,executor,lambda lease:[*deletion_targets(lease),
            *(['shard-'+str(i)+'.csv' for i in range(160)] if lease.attempt_id==trash[0].attempt_id else [])])
        s.attempt_cleanup=AttemptCleanup(q,executor,outputs=deletion_targets)
        requested=http.client.post('/api/visualiser',json=dict(baseline=baseline.job_id,
            scenarios=[v[0].job_id for v in completed[1:4]]))
        fixture.assertEqual(requested.status_code,200,requested.text)
        aggregate_key=requested.json()['id']
        reviewed=http.client.post('/api/review-output-deletion',json=dict(attempt=trash[0].attempt_id))
        fixture.assertEqual(reviewed.status_code,200,reviewed.text)
        confirmed=http.client.post('/api/submit',json=dict(key='trash-removal',review=reviewed.json()['review']))
        fixture.assertEqual(confirmed.status_code,201,confirmed.text)

        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
        base='http://127.0.0.1:'+str(listener.getsockname()[1])
        application=create_app(s,origin=base,local_codes=True)
        server=uvicorn.Server(uvicorn.Config(application,log_level='error',access_log=False,lifespan='off'))
        server_thread=Thread(target=lambda:server.run(sockets=[listener]),name='proof-http-server',daemon=True)
        server_thread.start()
        until=time.monotonic()+10
        while not server.started:
            if time.monotonic()>until or not server_thread.is_alive():raise AssertionError('HTTP server did not start')
            time.sleep(.02)
        for source in [http.client]*4+[foreign]:
            clients.append(httpx.Client(base_url=base,timeout=30,cookies=dict(source.cookies),
                headers={'Origin':base,'X-CSRF-Token':source.headers['X-CSRF-Token']}))
        route='/api/experiments/'+active_experiment
        fixture.assertEqual(clients[0].get(route).status_code,200)
        fixture.assertEqual(clients[4].get(route).status_code,403)
        fixture.assertEqual(clients[0].get(route,headers={'Host':'untrusted.invalid'}).status_code,400)
        report['real_http_event_loop']=True
        mutex=Lock()

        def phase(name,work,*,minimum=0):
            stats=dict(http_seconds=[],http_statuses=Counter(),http_errors=Counter(),
                connection_seconds=[],connection_errors=Counter(),heartbeat_seconds=[],
                scheduler_seconds=[],heartbeat_errors=[])
            stop=Event();connect=psycopg.connect;heartbeat=q.heartbeat
            def timed_connect(*args,**kwargs):
                at=time.monotonic()
                try:return connect(*args,**kwargs)
                except Exception as error:
                    with mutex:stats['connection_errors'][type(error).__name__]+=1
                    raise
                finally:
                    with mutex:stats['connection_seconds'].append(time.monotonic()-at)
            def timed_heartbeat(lease):
                at=time.monotonic()
                try:
                    reason=heartbeat(lease);fixture.assertIsNone(reason)
                    return reason
                finally:
                    with mutex:stats['heartbeat_seconds'].append(time.monotonic()-at)
            def poll(client,expected):
                while not stop.is_set():
                    at=time.monotonic()
                    try:
                        response=client.get(route)
                        with mutex:
                            stats['http_seconds'].append(time.monotonic()-at)
                            stats['http_statuses'][str(response.status_code)]+=1
                            if response.status_code!=expected:stats['http_errors']['wrong_status']+=1
                    except Exception as error:
                        with mutex:stats['http_errors'][type(error).__name__]+=1
                    stop.wait(.1)
            def scheduling():
                at=time.monotonic()
                while not stop.wait(.02):
                    now=time.monotonic()
                    with mutex:stats['scheduler_seconds'].append(now-at)
                    at=now
            print('Phase: '+name,flush=True)
            started=time.monotonic();failure=None
            with patch('psycopg.connect',side_effect=timed_connect),patch.object(q,'heartbeat',side_effect=timed_heartbeat), \
                    ThreadPoolExecutor(max_workers=5) as threads:
                tasks=[threads.submit(scheduling)]
                tasks.extend(threads.submit(poll,client,expected) for client,expected in
                    [(clients[0],200),(clients[1],200),(clients[2],200),(clients[4],403)])
                try:
                    with worker.maintenance():
                        work()
                        stop.wait(max(0,minimum-(time.monotonic()-started)))
                except Exception as error:
                    failure=error;stats['heartbeat_errors'].append(type(error).__name__)
                finally:stop.set()
                for task in tasks:task.result()
            result=dict(name=name,seconds=round(time.monotonic()-started,2),
                http=describe(stats['http_seconds']),connect=describe(stats['connection_seconds']),
                heartbeat=describe(stats['heartbeat_seconds']),scheduler=describe(stats['scheduler_seconds']),
                http_statuses=dict(stats['http_statuses']),http_errors=dict(stats['http_errors']),
                connection_errors=dict(stats['connection_errors']),heartbeat_errors=stats['heartbeat_errors'])
            report['phases'].append(result);print(json.dumps(result),flush=True)
            if failure:raise failure
            fixture.assertFalse(any(result[k] for k in ('http_errors','connection_errors','heartbeat_errors')))
            fixture.assertEqual(set(result['http_statuses']),{'200','403'})
            fixture.assertGreater(result['heartbeat']['samples'],1)

        with worker.open():
            phase('idle-polling',lambda:None,minimum=6)
            phase('large-aggregate-publication',visualiser.tick,minimum=6)
            fixture.assertEqual(visualiser.status(fixture.f.auth['token'],aggregate_key)['state'],'ready')
            report['aggregate_bytes']=(visualiser._path(aggregate_key)/'published.json').stat().st_size
            def aggregate_reads():
                route='/api/visualiser/'+aggregate_key+'/data'
                for _ in range(3):
                    response=clients[3].get(route)
                    fixture.assertEqual(response.status_code,200,response.text[:120] if response.status_code!=200 else '')
                    fixture.assertEqual(response.content.count(b'"year":2019'),120000)
                    for forbidden in (b'id_Person',b'PRIVATE_DIAGNOSTIC_SENTINEL',str(executor.root).encode()):
                        fixture.assertNotIn(forbidden,response.content)
                fixture.assertEqual(clients[4].get(route).status_code,403)
            phase('large-aggregate-http-reads',aggregate_reads,minimum=6)

            def zip_work():
                requested=clients[3].post('/api/downloads',json=dict(job=baseline.job_id,baseline=None,include_inputs=False))
                fixture.assertEqual(requested.status_code,200,requested.text)
                key=requested.json()['id'];deadline=time.monotonic()+60
                while True:
                    status=clients[3].get('/api/downloads/'+key)
                    fixture.assertEqual(status.status_code,200,status.text)
                    state=status.json()['state']
                    if state=='ready':break
                    if state not in ('checking','preparing') or time.monotonic()>deadline:
                        raise AssertionError('ZIP preparation did not complete')
                    time.sleep(.05)
                for thread in list(s.results.downloads.threads):thread.join(10)
                receipt=s.results.downloads._record(key)
                report['zip_bytes']=receipt['bytes']
                archive_path=s.results.downloads._path(key)/'archive.zip'
                digest=hashlib.sha256()
                with archive_path.open('rb') as source:
                    while chunk:=source.read(1024**2):digest.update(chunk)
                fixture.assertEqual(digest.hexdigest(),receipt['sha256'])
                with zipfile.ZipFile(archive_path) as archive:
                    fixture.assertIsNone(archive.testzip())
                    entry=next(n for n in archive.namelist() if '/run_1/csv/' in n)
                    digest=hashlib.sha256()
                    with archive.open(entry) as source:
                        while chunk:=source.read(1024**2):digest.update(chunk)
                    fixture.assertEqual(digest.hexdigest(),baseline_hash)
                downloaded=clients[3].get(status.json()['url'])
                fixture.assertEqual(downloaded.status_code,200)
                fixture.assertEqual(hashlib.sha256(downloaded.content).hexdigest(),receipt['sha256'])
                fixture.assertEqual(clients[4].get(status.json()['url']).status_code,403)
            phase('zip-build-hash-and-http-download',zip_work,minimum=6)

            def cleanup():
                fixture.assertGreater(measured_bytes(executor.root),report['zip_source_bytes'])
                def slow_remove(work,targets):
                    for target in targets:
                        remove_targets(work,[target]);time.sleep(.04)
                with patch('jasmine_web.batch.output_management.remove_targets',side_effect=slow_remove):
                    s.outputs.retire()
                s.attempt_cleanup.retire()
                fixture.assertFalse((trash[1]/'output-606.csv').exists())
                fixture.assertFalse(scratch.exists())
                fixture.assertTrue((trash[1]/'private.log').exists())
                fixture.assertIn('PRIVATE_DIAGNOSTIC_SENTINEL',
                    (executor.workspace(failed)/'diagnostics.log').read_text())
                storage=clients[3].get('/api/storage');fixture.assertEqual(storage.status_code,200)
                fixture.assertNotIn(str(executor.root),storage.text)
                fixture.assertGreater(measured_bytes(active_work.parent),0)
            phase('slow-cleanup-and-storage-scan',cleanup)
            fixture.assertGreater(report['phases'][-1]['seconds'],5)
            fixture.assertIsNone(q.heartbeat(active))
            attempt=fixture.f.fixture.sql('SELECT generation,deadline,lease_owner FROM attempts WHERE id=%s',(active.attempt_id,))[0]
            fixture.assertEqual((attempt['generation'],attempt['deadline'],attempt['lease_owner']),
                                (active.generation,active.deadline,active.worker))
            saved=q.inspect(fixture.f.owner,active_experiment)
            fixture.assertEqual(saved['specification'],frozen)
            fixture.assertEqual(saved['jobs'][0]['attempts'],1)
            fixture.assertEqual(fixture.f.fixture.sql('SELECT policy FROM experiments WHERE id=%s',(active_experiment,))[0]['policy'],policy)
            # End only the fictional active lease; no model has been restarted.
            q.finish(active,outcome='cancelled',stop_evidence='f'*64)
            del worker.leases[active.attempt_id]
        fixture.assertEqual(q.occupancy()['attempts'],0)
        fixture.assertEqual(clients[3].get('/api/visualiser/'+aggregate_key+'/data').status_code,200)
        fixture.assertEqual(clients[3].get('/api/results/'+completed_experiment).status_code,200)
        report.update(passed=True,original_attempt_preserved=True,frozen_settings_and_policy_preserved=True,
            aggregate_reads=3,zip_hashes_verified=True,physical_cleanup_verified=True,capacity_released=True)
        print('PASS: aggregate helpers, ZIP hashing and slow cleanup preserve protected polling and the original five-second lease',flush=True)
    except BaseException as error:
        report['failure']=dict(type=type(error).__name__)
        raise
    finally:
        for client in clients:client.close()
        if server is not None:server.should_exit=True
        if server_thread is not None:server_thread.join(10)
        if visualiser is not None:visualiser.close()
        if hasattr(getattr(fixture,'s',None),'results'):
            downloads=fixture.s.results.downloads;downloads.close()
            for thread in list(downloads.threads):thread.join(10)
        clean=fixture.doCleanups()
        root=getattr(getattr(fixture,'f',None),'root',None)
        report['cleanup']=clean and (root is None or not root.exists()) and (server_thread is None or not server_thread.is_alive())
        report['passed']=report['passed'] and report['cleanup']
        write_json(output/'report.json',report)
    if not report['cleanup']:raise AssertionError('Background proof cleanup is unconfirmed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    parser.add_argument('--output',type=Path)
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.execute_proof:
        if not os.environ.get('JASMINE_BATCH_TEST_DSN') or args.output is None:parser.error('Use the disposable PostgreSQL driver')
        execute(args.output);return 0
    output=args.output or Path.home()/'simpaths-benchmarks'/('background-response-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    if output.exists():parser.error('Choose a new evidence directory')
    frontend=args.frontend.expanduser().resolve(strict=True)
    return subprocess.call([sys.executable,str(frontend/'scripts/test_batch_queue.py'),'--proof-only',
        '--proof-script',str(Path(__file__).resolve()),'--proof-requirements',
        str(Path(__file__).with_name('requirements.txt')),'--output',str(output.resolve())],
        env=dict(os.environ,JASMINE_WEB_REPO=str(frontend)))


if __name__=='__main__':raise SystemExit(main())
