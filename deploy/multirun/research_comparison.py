#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Compare verified retained research runs without rerunning models or copying microdata.
Exercises the pinned calculations, authenticated Results page and chart exports.
Uses public training data and explicit private calibration budgets; not a service profile.
@author ross richardson
"""
import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
from deploy.multirun.artifacts import ArtifactError, fingerprint, verify, write_attribution, write_json
from deploy.multirun.research_calibration import checked_settings, configuration, read_calibration, status_model
from deploy.multirun.prepared_dataset import verify_snapshot

ENV='SIMPATHS_RESEARCH_COMPARISON'
MIB=1024**2
GIB=1024**3
TIMEOUT=3600


def publication_limits():
    from jasmine_web.batch.aggregate_limits import AggregateLimits
    return AggregateLimits(artifact_bytes=96*MIB,comparison_bytes=256*MIB,
                            rows_per_series=200000,comparison_rows=400000)


def read_json(path):
    if fingerprint(path)['bytes']>4*MIB:raise ArtifactError('Research evidence exceeds its bound')
    return json.loads(Path(path).read_text())


def retained_run(folder,receipt,variant,*,check_files=True):
    """Accept native completion or its verified cleanup supplement, never an unfinished run."""
    folder=Path(folder).resolve(strict=True);proof=folder/'model-proof'
    record=read_json(proof/'report.json');outer=read_json(folder/'report.json')
    settings=checked_settings(record['settings'])
    config=configuration(receipt,settings,variant)
    if (record.get('variant','baseline')!=variant or settings['population']!=100000
            or settings['end_year']!=2070 or settings['repetitions']!=1
            or record.get('failure') or outer.get('cleanup') is not True
            or not all(record.get(k) is True for k in
                ('source_unchanged','input_copies_reclaimed','reservations_released'))
            or record['prepared_sha256']!=receipt['sha256']
            or record['image']!=receipt['identity']['source_image']
            or record['model']!=receipt['identity']['model']
            or read_json(proof/'configuration.json')!=config.as_dict()
            or [a['outcome'] for a in record['attempts']]!=['success']
            or len(record['verified_repetitions'])!=1
            or record['verified_repetitions'][0]['seed']!='606'):
        raise ArtifactError('Use the two completed, matched, one-seed research calibrations')
    output=proof/'retained-output'
    if record['output_location']!=str(output):raise ArtifactError('Retained output location changed')
    provenance={name:fingerprint(proof/name) for name in
                ('report.json','configuration.json','output-manifest.json')}
    if not (record.get('passed') is True and record.get('cleanup') is True and outer.get('passed') is True):
        # The original baseline's final cleanup guard failed after scientific
        # completion. Its separately verified cleanup receipt must match every
        # frozen identity; the original failed reports remain unmodified.
        supplement=read_json(proof/'cleanup-verification.json')
        expected=dict(passed=True,cleanup=True,simulation_repeated=False,
            original_report=provenance['report.json'],original_native_database_removed=True,
            prepared_sha256=receipt['sha256'],configuration_sha256=provenance['configuration.json'],
            output_manifest=provenance['output-manifest.json'],output_bytes=record['output_bytes'],
            verified_repetitions=record['verified_repetitions'],output_location=str(output),
            annual_years=list(range(2019,2071)))
        if any(supplement.get(k)!=v for k,v in expected.items()):
            raise ArtifactError('Native completion lacks a matching confirmed cleanup receipt')
        provenance['cleanup-verification.json']=fingerprint(proof/'cleanup-verification.json')
    if record.get('retained_workspace') and Path(record['retained_workspace']).exists():
        raise ArtifactError('Original calibration scratch has not been released')
    manifest=read_json(proof/'output-manifest.json')
    if sum(item['bytes'] for item in manifest.values())!=record['output_bytes']:
        raise ArtifactError('Retained output manifest size changed')
    if check_files:verify(output,manifest)
    return dict(folder=folder,proof=proof,record=record,configuration=config,
                output=output,manifest=manifest,provenance=provenance)


def pair_sources(baseline,alternative):
    """Only savingRate and the human-readable configuration identity may differ."""
    first=baseline['configuration'].as_dict();second=alternative['configuration'].as_dict()
    run=second['run_sets'][0];run.update(id='baseline',name='Baseline')
    run['model_args']['savingRate']=first['run_sets'][0]['model_args']['savingRate']
    if (first!=second or first['seed_plan']['seeds']!=['606']
            or baseline['record']['verified_repetitions']==alternative['record']['verified_repetitions']):
        raise ArtifactError('Research sources are not a genuine matched baseline and alternative')
    return [baseline,alternative]


def load_sources(settings,*,check_files=True):
    receipt=read_calibration(Path(settings['prepared']))
    if check_files:verify_snapshot(Path(settings['prepared']),receipt)
    sources=pair_sources(retained_run(settings['baseline'],receipt,'baseline',check_files=check_files),
                         retained_run(settings['alternative'],receipt,'lower-saving-rate',check_files=check_files))
    return receipt,sources


def source_metadata(source,role):
    config=source['configuration'].as_dict();run=config['run_sets'][0]
    return dict(id=run['id'],name=run['name'],role=role,dataset=config['dataset_revision'],
                model=config['model_release'],runs=[dict(folder=role+'/run_1',seed='606')])


def processing_sources(sources,root):
    selected=[]
    for source,role in zip(sources,('Baseline','Scenario')):
        metadata=source_metadata(source,role);files=[]
        for name,value in source['manifest'].items():
            if name.endswith('/csv/Person.csv') or name.endswith('/csv/BenefitUnit.csv'):
                files.append(dict(name=role+'/run_1/csv/'+Path(name).name,
                    path=str((source['output']/name).relative_to(root)),**value))
        if len(files)!=2:raise ArtifactError('Expected one Person/BenefitUnit pair per retained run')
        selected.append(dict(configuration=metadata,files=files))
    return selected


class RetainedOutputs:
    """Read-only fixture catalogue; retain production hashes and single-link file guards.

    The disposable executor owns only identity files and its aggregate cache.
    This trusted fixture maps its internally generated catalogue paths to the
    two verified original directories. No HTTP caller supplies a source path.
    """
    def __init__(self,sources):
        self.sources={s['configuration'].as_dict()['run_sets'][0]['id']:s for s in sources}
        self.root=Path(os.path.commonpath([s['output'] for s in sources]))

    def catalogue(self,lease,work):
        from deploy.multirun.configuration import normalise
        from deploy.multirun.queue_adapter import validate_outputs
        source=self.sources[lease.configuration_id]
        run=next(r for r in lease.specification['run_sets'] if r['id']==lease.configuration_id)
        config=normalise(run['parameters'])
        if (config.as_dict()!=source['configuration'].as_dict()
                or config.as_dict()['seed_plan']['seeds']!=lease.specification['seeds']):
            raise ArtifactError('Imported completion differs from the retained frozen configuration')
        return validate_outputs(source['output'],config,lease.configuration_id,include_files=True)

    def paths(self,files,row):
        source=self.sources[row['configuration_id']]
        prefix=Path(row['attempt']['execution_key'])/'work'/source['output'].name
        mapped=[]
        for item in files:
            name=Path(item['path']).relative_to(prefix).as_posix()
            if (name not in source['manifest'] or
                    {k:item[k] for k in ('bytes','sha256')}!=source['manifest'][name]):
                raise ArtifactError('Result catalogue differs from the retained native manifest')
            mapped.append(dict(item,path=(source['output']/name).relative_to(self.root).as_posix()))
        return mapped

    def results(self,service,executor,*,name,settings):
        from jasmine_web.batch.results import Results
        retained=self
        class RetainedResults(Results):
            def _catalogue(self,exp,row,role,*,folder=None):
                files,configuration=super()._catalogue(exp,row,role,folder=folder)
                return retained.paths(files,row),configuration
        return RetainedResults(service,executor,self.catalogue,name=name,settings=settings)


def cleanup_action(record,name,action):
    """Record bounded cleanup errors without replacing the primary failure."""
    try:action()
    except Exception as error:
        record.setdefault('cleanup_errors',[]).append(dict(stage=name,
            error_type=type(error).__name__,error=str(error)[:500]))


@contextmanager
def browser_session(factory,record):
    """Close Chromium while Playwright's event loop still exists, including on failure."""
    with factory() as p:
        browser=p.chromium.launch()
        try:yield p,browser
        finally:cleanup_action(record,'browser',browser.close)


class AggregateCapture:
    """Record the actual ASGI response stream without Chromium's inspector cache.

    This wrapper belongs only to the proof. It forwards every message unchanged,
    saves one bounded successful aggregate response privately and checksums later
    responses. Headers/cookies and unrelated or denied response bodies are not saved.
    """
    def __init__(self,app,output,maximum_bytes):
        self.app,self.output,self.maximum_bytes=app,Path(output),maximum_bytes
        self.destination=self.output/'delivered-aggregates.json'
        if self.destination.exists():raise FileExistsError('Aggregate evidence already exists')
        self.responses=[];self.errors=[];self.sequence=0

    async def __call__(self,scope,receive,send):
        if (scope.get('type')!='http' or scope.get('method')!='GET'
                or not re.fullmatch(r'/api/visualiser/[a-f0-9]{64}/data',scope.get('path',''))):
            return await self.app(scope,receive,send)
        self.sequence+=1
        temporary=self.output/f'.aggregate-capture-{self.sequence}.partial'
        stream=None;status=None;size=0;digest=hashlib.sha256();complete=False;owned=False
        async def observed_send(message):
            nonlocal stream,status,size,complete,owned
            await send(message)
            if message['type']=='http.response.start':status=message['status']
            elif message['type']=='http.response.body' and status==200:
                body=message.get('body',b'');size+=len(body)
                if size>self.maximum_bytes:raise ArtifactError('Captured aggregate response exceeds its bound')
                digest.update(body)
                if stream is None and not self.destination.exists():
                    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);owned=True
                    stream=os.fdopen(fd,'wb')
                if stream is not None:stream.write(body)
                if not message.get('more_body',False):
                    complete=True
                    if stream is not None:
                        stream.flush();os.fsync(stream.fileno());stream.close();stream=None
                        # No-clobber publication; remove the temporary link before
                        # any verification reads the completed evidence file.
                        try:os.link(temporary,self.destination,follow_symlinks=False)
                        except FileExistsError:pass
                        temporary.unlink()
                    self.responses.append(dict(path=scope['path'],status=status,bytes=size,
                        sha256=digest.hexdigest(),capture='completed-asgi-response-v1'))
        try:
            await self.app(scope,receive,observed_send)
            if status==200 and not complete:raise ArtifactError('Captured aggregate response did not finish')
        except Exception as error:
            self.errors.append(dict(error_type=type(error).__name__,error=str(error)[:500]));raise
        finally:
            if stream is not None:stream.close()
            if owned:temporary.unlink(missing_ok=True)


def observe_browser_response(response,responses):
    """Event callbacks read metadata only; never ask DevTools to retain large bodies."""
    if re.search(r'/api/visualiser/[a-f0-9]{64}/data$',response.url):
        responses.append(dict(url=response.url,status=response.status))


def unchanged_sources(sources):
    for source in sources:
        verify(source['output'],source['manifest'])
        if any(fingerprint(source['proof']/name)!=value for name,value in source['provenance'].items()):
            raise ArtifactError('Original native evidence changed during comparison')


def analyse_publication(body):
    from deploy.multirun.vm_boundary import aggregate_envelope
    value=aggregate_envelope(json.loads(body),limits=publication_limits())
    if (value['format']!='simpaths.visualiser.v2' or not value['data']['comparison_available']
            or [c['id'] for c in value['configurations']]!=['baseline','alternative']
            or [[r['seed'] for r in c['runs']] for c in value['configurations']]!=[['606'],['606']]):
        raise AssertionError('Research publication lost its matched comparison identity')
    first,second=[s['rows'] for s in value['data']['series']]
    key=lambda r:tuple(r[k] for k in ('year','module','variable','variable_value','stratifier','stratifier_value','metric_type'))
    baseline={key(row):row for row in first}
    if len(baseline)!=len(first):raise AssertionError('Duplicate baseline aggregate cells')
    paired=nonzero=0
    for group in (first,second):
        if sorted({row['year'] for row in group})!=list(range(2019,2071)):
            raise AssertionError('Aggregate publication lost an annual year')
        if any(row['n_runs']!=1 for row in group):raise AssertionError('The source has only one native seed')
    for row in second:
        if row['paired_n_runs'] not in (0,1):raise AssertionError('Invented seed pairing')
        if row['paired_n_runs']==1:
            paired+=1;delta=row['paired_mean_delta'];other=baseline.get(key(row))
            if not other or delta is None:raise AssertionError('Paired cell lacks its baseline')
            if row['paired_lower_ci']!=delta or row['paired_upper_ci']!=delta:
                raise AssertionError('Unexpected one-seed confidence calculation')
            if other['mean_value'] is not None and row['mean_value'] is not None:
                if not math.isclose(delta,row['mean_value']-other['mean_value'],rel_tol=1e-9,abs_tol=1e-9):
                    raise AssertionError('Paired delta differs from its actual matched seed levels')
            nonzero+=abs(delta)>1e-10
    if not paired or not nonzero:raise AssertionError('No genuine alternative policy impacts were produced')
    return dict(response_bytes=len(body),response_sha256=hashlib.sha256(body).hexdigest(),
        rows_per_configuration=[len(first),len(second)],annual_years=list(range(2019,2071)),
        paired_cells=paired,nonzero_paired_cells=nonzero,matched_seeds=['606'],
        uncertainty_note='One matched seed cannot estimate uncertainty across repetitions; upstream zero-width intervals are preserved.')


def bounded_command(log,argv,*,pass_fds=()):
    # Independent helper timeout and a credential-free environment. Only the
    # verified raw descriptors, never database/login secrets, reach Node.
    subprocess.run(['timeout','--kill-after=5',str(TIMEOUT),*argv],check=True,
        stdin=subprocess.DEVNULL,stdout=log,stderr=log,pass_fds=pass_fds,
        env={'PATH':os.defpath,'LANG':'C.UTF-8'},timeout=TIMEOUT+10)


def aggregate_only(settings,output):
    """Read-only native calculation check, useful before the browser/database proof."""
    from deploy.multirun.visualiser_backend import VisualiserBackend
    from jasmine_web.batch.aggregate_io import invoke
    receipt,sources=load_sources(settings)
    output.mkdir(mode=0o700);write_attribution(output)
    record=dict(passed=False,cleanup=False,models_repeated=False,private_calibration=True,
                defaults_changed=False,publication_limits=publication_limits().describe())
    started=time.monotonic()
    try:
        root=Path(os.path.commonpath([source['output'] for source in sources]))
        backend=VisualiserBackend(settings['build'],root,
            public_datasets=[receipt['revision']],memory_mib=2048)
        record['backend']=backend.identity
        with (output/'processing.log').open('wb') as log:
            def command(argv,*,pass_fds=()):bounded_command(log,argv,pass_fds=pass_fds)
            selected=processing_sources(sources,root)
            files=backend.process_files(selected,output,command,
                lambda count:print(f'Processed retained research configuration {count}/2',flush=True),comparison_set=True)
            public=dict(format='simpaths.visualiser.v2',backend=backend.identity,
                experiment='100,000-person research comparison',configurations=[s['configuration'] for s in selected],
                comparison=dict(baseline='baseline',scenarios=['alternative']))
            with ExitStack() as stack:
                streams=[stack.enter_context(path.open('rb')) for path in files.paths]
                body=invoke(dict(operation='publish',memory_mib=3072,
                    publication_limits=publication_limits().describe(),format=public['format'],kind='rows',
                    notice=files.notice,comparison_available=files.comparison_available,public=public),
                    tuple(stream.fileno() for stream in streams),command)
        record['aggregate']=analyse_publication(body)
        (output/'published-aggregates.json').write_bytes(body)
        unchanged_sources(sources)
        record.update(passed=True,cleanup=True,source_unchanged=True,elapsed_seconds=round(time.monotonic()-started,3))
    except Exception as error:
        record.update(error_type=type(error).__name__,error=str(error)[:1000]);raise
    finally:write_json(output/'report.json',record)
    print('PASS: both retained 52-year runs produce bounded, seed-paired aggregates with unchanged scientific output',flush=True)
    print('PASSED: '+str(output/'report.json'),flush=True)


def execute(settings,output):
    import uvicorn
    from playwright.sync_api import sync_playwright,expect
    from deploy.multirun.queue_adapter import result_name,result_settings
    from deploy.multirun.visualiser_backend import VisualiserBackend
    from jasmine_web.batch.browser import create_app
    from jasmine_web.batch.local_executor import LocalExecutor,atomic_json
    from jasmine_web.batch.policy import Resources
    from jasmine_web.batch.visualiser import Visualiser
    sys.path.insert(0,str(frontend_path()/'tests/batch'))
    import test_submission

    receipt,sources=load_sources(settings)
    output.mkdir(mode=0o700);write_attribution(output)
    record=dict(passed=False,cleanup=False,models_repeated=False,private_calibration=True,
        defaults_changed=False,checks=[],publication_limits=publication_limits().describe(),
        original_runs=[dict(variant=s['record'].get('variant','baseline'),attempts=s['record']['attempts'],
                            report=s['provenance']['report.json']) for s in sources])
    fixture=test_submission.SubmissionTests();fixture.setUp()
    server=thread=sock=capture=None;started=time.monotonic()
    def passed(message):record['checks'].append(message);print('PASS: '+message,flush=True)
    try:
        service,q=fixture.s,fixture.q
        retained=RetainedOutputs(sources)
        executor=LocalExecutor(fixture.root/'execution');q.bind_executor(executor.identity())
        provider=service.datasets.register_provider(receipt['sha256'])
        service.preparations.register_location(provider,location=settings['prepared'],image=receipt['identity']['source_image'])
        q.grant_dataset(fixture.owner,provider)
        service.model=status_model(Path(settings['prepared']),receipt,sources[0]['record']['settings'],dataset_id=provider)
        runs=[dict(id=s['configuration'].as_dict()['run_sets'][0]['id'],parameters=s['configuration'].editable_configuration()) for s in sources]
        experiment=q.submit(fixture.owner,'retained-research-comparison',label='100,000 people through 2070',
            dataset_id=provider,model_digest=receipt['identity']['source_image'],seed_plan=['606'],
            baseline='baseline',run_sets=runs,resources=Resources(1000,1024,1024))
        leases=[]
        for source in sources:
            lease=q.claim('retained-output-import');assert lease is not None;q.started(lease)
            workspace=executor.workspace(lease);workspace.mkdir(mode=0o700)
            atomic_json(workspace/'identity.json',executor._identity(lease))
            work=workspace/'work';work.mkdir(mode=0o700)
            q.record_repetition(lease,0,'606',source['record']['verified_repetitions'][0]['fingerprint'])
            q.finish(lease,outcome='success',stop_evidence=source['provenance']['report.json']['sha256'])
            leases.append(lease)
        record['disposable_queue_receipts']='Imported verified retained completion receipts; no model execution was requested.'
        service.results=retained.results(service,executor,name=result_name,settings=result_settings)
        backend=VisualiserBackend(settings['build'],retained.root,public_datasets=[provider],memory_mib=2048)
        service.visualiser=Visualiser(service.results,backend,resources=Resources(1000,3072,1024),
            runtime=TIMEOUT,lifetime=86400,capacity=768*MIB,publication_limits=publication_limits())
        record['backend']=service.visualiser.identity
        passed('the original native completions are registered in disposable Results; verified originals are read directly without simulations, links or microdata copies')
        sock=socket.socket();sock.bind(('127.0.0.1',0));origin='http://127.0.0.1:'+str(sock.getsockname()[1])
        app=create_app(service,origin=origin,local_codes=True,site=dict(name='SimPaths Online',subtitle='UK MultiRun',
            logo='/static/simpaths-logo.svg',icon='/static/simpaths-favicon.svg'))
        capture=AggregateCapture(app,output,publication_limits().comparison_bytes)
        server=uvicorn.Server(uvicorn.Config(capture,log_level='warning',access_log=False))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        deadline=time.monotonic()+15
        while not server.started:
            if not thread.is_alive() or time.monotonic()>deadline:raise RuntimeError('Comparison frontend did not start')
            time.sleep(.05)
        with browser_session(sync_playwright,record) as (p,browser):
            context=browser.new_context(viewport=dict(width=1360,height=900))
            errors=[];requests=[];responses=[];record['browser_errors']=errors
            context.on('page',lambda page:page.on('pageerror',lambda error:errors.append(str(error))))
            context.on('request',lambda request:requests.append(request.url))
            context.on('response',lambda response:observe_browser_response(response,responses))
            def login(page,email):
                page.goto(origin);page.get_by_label('Email address',exact=True).fill(email)
                page.get_by_role('button',name='Request verification code').click()
                expect(page.get_by_label('Verification code',exact=True)).to_be_visible()
                page.get_by_label('Verification code',exact=True).fill(fixture.mail[email])
                page.get_by_role('button',name='Sign in',exact=True).click()
                expect(page.locator('#workspace')).to_be_visible()
            page=context.new_page();login(page,'alice@example.org');page.goto(origin+'/results/'+experiment)
            expect(page.locator('#vm-visualiser-controls')).to_be_visible()
            csrf=page.request.get(origin+'/api/session').json()['csrf']
            denied=page.request.post(origin+'/api/visualiser',headers={'x-csrf-token':csrf,'origin':origin},
                data=dict(baseline=leases[0].job_id,scenarios=[leases[1].job_id],
                          publication_limits=publication_limits().describe()))
            assert denied.status==400,'Browser requests must not choose publication budgets'
            page.get_by_label('Baseline for visualisation',exact=True).select_option(leases[0].job_id)
            page.get_by_label('Compare several alternatives',exact=True).check()
            page.get_by_role('button',name='Select all available alternatives',exact=True).click()
            page.get_by_role('button',name='Prepare Visualiser',exact=True).click()
            link=page.get_by_role('link',name='Open Visualiser',exact=True)
            # Poll the actual status to fail promptly with private diagnostics
            # rather than waiting an hour after a calculation has already failed.
            deadline=time.monotonic()+TIMEOUT
            last_notice=0
            while not link.is_visible():
                statuses=[read_json(path) for path in service.visualiser.root.glob('*/status.json')]
                failed=next((state for state in statuses if state['state']=='failed'),None)
                if failed:
                    raise AssertionError('Research comparison processing failed during '+
                        failed.get('failure_stage','processing')+'; inspect the private report')
                if time.monotonic()>deadline:raise AssertionError('Research comparison processing exceeded its bound')
                if time.monotonic()-last_notice>30:
                    print('Preparing both retained research runs for the Visualiser',flush=True);last_notice=time.monotonic()
                page.wait_for_timeout(1000)
            with page.expect_popup() as pending:link.click()
            charts=pending.value;charts.set_default_timeout(60000)
            expect(charts.get_by_label('Displayed data source')).to_contain_text('Lower saving rate (0.04)',timeout=120000)
            expect(charts.get_by_label('Displayed data source')).to_contain_text('Baseline')
            charts.get_by_role('button',name=re.compile(r'^Health\s*▼$')).click()
            charts.get_by_role('button',name='Mental Component Summary (MCS)',exact=True).click()
            expect(charts.locator('svg').first).to_be_visible()
            # The full 52-year dataset needs time for the maintained dashboard's
            # variable filters to settle after switching from education to MCS.
            charts.wait_for_timeout(2000)
            def export(name):
                with charts.expect_download() as pending:charts.get_by_role('button',name='↓ CSV',exact=True).first.click()
                download=pending.value
                if download.failure() is not None:raise AssertionError('Chart CSV export failed')
                download.save_as(output/name)
                return list(csv.DictReader((output/name).read_text().splitlines()))
            levels=export('research-levels.csv')
            if ({r['scenario'] for r in levels}!={'baseline','scenario_1'}
                    or {int(float(r['year'])) for r in levels}!=set(range(2019,2071))
                    or not all(any(name in r['configuration_name'] for r in levels)
                               for name in ('Baseline','Lower saving rate (0.04)'))):
                raise AssertionError('Chart export lost names, scenarios or years')
            toggle=charts.get_by_role('button',name=re.compile(r'Lower saving rate \(0\.04\)$'))
            toggle.click();hidden=export('research-baseline-only.csv')
            if {r['scenario'] for r in hidden}!={'baseline'}:raise AssertionError('Scenario toggle did not change the chart')
            toggle.click();charts.get_by_role('button',name='Δ Baseline → Scenario',exact=True).click()
            impacts=export('research-paired-impacts.csv')
            if {r['scenario'] for r in impacts}!={'scenario_1'}:raise AssertionError('Paired chart lost its scenario')
            charts.screenshot(path=str(output/'research-comparison.png'))
            passed('all 52 annual years draw in the maintained charts; named level/paired exports and scenario toggles work with the real outputs')
            key=charts.url.split('/visualiser/',1)[1].split('?',1)[0]
            published=service.visualiser.root/key/'published.json';before=fingerprint(published)
            charts.reload();expect(charts.get_by_label('Displayed data source')).to_contain_text('Lower saving rate (0.04)',timeout=120000)
            if fingerprint(published)!=before:raise AssertionError('Refresh reprocessed the native output')
            body=(output/'delivered-aggregates.json').read_bytes();record['aggregate']=analyse_publication(body)
            expected_path='/api/visualiser/'+key+'/data'
            delivered=[r for r in capture.responses if r['path']==expected_path]
            observed=[r for r in responses if r['url']==origin+expected_path and r['status']==200]
            if (capture.errors or len(delivered)<2 or len(observed)<2
                    or any(r['bytes']!=len(body) or r['sha256']!=record['aggregate']['response_sha256'] for r in delivered)):
                raise AssertionError('Initial and refreshed browser requests lack matching complete response evidence')
            for private in (b'id_Person',b'id_BenefitUnit',b'Person.csv',b'BenefitUnit.csv',str(fixture.root).encode()):
                if private in body:raise AssertionError('Private raw-data information reached the browser')
            if any(not url.startswith(origin+'/') for url in requests):raise AssertionError('External network request')
            if any('/downloads/' in url or '.csv' in url for url in requests):raise AssertionError('Browser requested raw outputs')
            passed('authenticated HTTP response streams contain only bounded aggregates and seed metadata; initial and refreshed browser loads use the same checksum-verified publication')
            for lease in leases:
                assert page.request.get(origin+'/downloads/'+lease.job_id).status==403
            for name in ('runner.cjs','calculation.cjs','build.json','SimPaths_All_Aggregated_Outputs.csv'):
                assert page.request.get(origin+'/visualiser-assets/'+name).status==404
            fixture.a.approve_email('bob@example.org');other=browser.new_context();bob=other.new_page();login(bob,'bob@example.org')
            assert bob.request.get(origin+'/api/visualiser/'+key+'/data').status==403
            bob.goto(charts.url);expect(bob.locator('.vm-source-message')).to_contain_text('Sign in with an approved email')
            expect(bob.locator('svg')).to_have_count(0)
            anonymous=p.request.new_context()
            assert anonymous.get(origin+'/api/visualiser/'+key+'/data').status==403
            anonymous.dispose();other.close()
            if errors:raise AssertionError('Uncaught browser errors: '+str(errors))
            assert all(row['attempts']==1 for row in fixture.fixture.sql('SELECT attempts FROM jobs'))
            record['browser_responses']=responses
            passed('anonymous and second-owner chart reads are denied; provider raw downloads remain denied and no model retries occur')
        unchanged_sources(sources)
        record.update(passed=True,source_unchanged=True,elapsed_seconds=round(time.monotonic()-started,3))
    except Exception as error:
        record.update(error_type=type(error).__name__,error=str(error)[:1000]);raise
    finally:
        if capture:
            record['http_response_captures']=capture.responses
            record['http_capture_errors']=capture.errors
        def preserve_processing():
            visualiser=getattr(fixture.s,'visualiser',None)
            if visualiser is None:return
            record['processing_status']=[read_json(path) for path in visualiser.root.glob('*/status.json')]
            for index,path in enumerate(visualiser.root.glob('*/private/diagnostics.log')):
                if path.stat().st_size<=MIB:shutil.copyfile(path,output/f'processing-diagnostics-{index}.log')
        cleanup_action(record,'processing-evidence',preserve_processing)
        if hasattr(fixture.s,'visualiser'):
            cleanup_action(record,'visualiser',fixture.s.visualiser.close)
        if server:server.should_exit=True
        if thread:cleanup_action(record,'frontend',lambda:thread.join(timeout=15))
        if sock:cleanup_action(record,'socket',sock.close)
        # Only fixture identities/cache and the disposable schema are removed.
        # The retained native source directories were never linked or changed.
        def finish_fixture():
            if not fixture.doCleanups():raise RuntimeError('Disposable fixture cleanup reported a failure')
        cleanup_action(record,'fixture',finish_fixture)
        if not record.get('source_unchanged'):
            def check_originals():
                unchanged_sources(sources);record['source_unchanged']=True
            cleanup_action(record,'original-output-verification',check_originals)
        record['cleanup']=(not fixture.root.exists() and (thread is None or not thread.is_alive())
                           and not record.get('cleanup_errors'))
        if not record['cleanup']:record['passed']=False
        write_json(output/'report.json',record)
    if not record['cleanup']:raise AssertionError('Disposable comparison cleanup is unconfirmed')
    print('PASSED: '+str(output/'report.json'),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,default=frontend_path())
    for name in ('prepared','baseline','alternative','build'):parser.add_argument('--'+name,type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--aggregate-only',action='store_true',help='Read-only calculations without a database or browser')
    parser.add_argument('--execute-proof',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.execute_proof:
        if not os.environ.get('JASMINE_BATCH_TEST_DSN') or ENV not in os.environ:parser.error('Use the disposable database driver')
        execute(json.loads(os.environ[ENV]),args.output);return 0
    if any(getattr(args,name) is None for name in ('prepared','baseline','alternative','build')):
        parser.error('Provide the prepared package, both retained evidence directories and a pinned build')
    settings={name:str(getattr(args,name).resolve(strict=True)) for name in ('prepared','baseline','alternative','build')}
    output=(args.output or Path('/tmp-codex')/('research-comparison-'+datetime.now().strftime('%Y%m%d-%H%M%S'))).absolute()
    if output.exists():parser.error('Choose a new output directory; existing evidence will not be overwritten')
    if args.dry_run:
        load_sources(settings,check_files=False)
        print(json.dumps(dict(**settings,output=str(output),models_repeated=False,
            publication_limits=publication_limits().describe(),defaults_changed=False),indent=2));return 0
    output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if shutil.disk_usage(output.parent).free<2*GIB:raise ArtifactError('Need 2 GiB free for aggregate evidence and disposable services')
    if args.aggregate_only:aggregate_only(settings,output);return 0
    # The normal harness creates only a disposable PostgreSQL container. Raw
    # inputs/outputs stay local; sign-in mail is captured in memory.
    command=[sys.executable,str(args.frontend/'scripts/test_batch_queue.py'),'--test-pattern',
        'test_aggregate_limits.py','test_aggregate_io.py','--proof-script',str(Path(__file__).resolve()),
        '--proof-timeout-seconds',str(2*TIMEOUT),'--output',str(output),'--proof-requirements',
        str(ROOT/'deploy/multirun/requirements.txt'),str(args.frontend/'tests/browser/requirements-batch.txt')]
    return subprocess.call(command,cwd=args.frontend,env=dict(os.environ,TMPDIR=str(output.parent),
        JASMINE_WEB_REPO=str(args.frontend),**{ENV:json.dumps(settings)}))


if __name__=='__main__':raise SystemExit(main())
