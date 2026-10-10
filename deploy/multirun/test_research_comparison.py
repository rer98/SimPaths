"""(C) Copyright 2026, by Ross Richardson

Retained completion, matched settings, original-file ownership and aggregate comparison guards.
@author ross richardson
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from deploy.multirun import research_comparison as comparison
from deploy.multirun.artifacts import ArtifactError, fingerprint, inventory, write_json
from deploy.multirun import test_research_calibration


class ResearchComparisonTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_research_calibration.ResearchCalibrationTests()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root;self.receipt=self.fixture.receipt
        self.paths=[self.make_run('baseline','a'),self.make_run('lower-saving-rate','b')]

    def make_run(self,variant,checksum):
        folder=self.root/variant;proof=folder/'model-proof';proof.mkdir(parents=True)
        output=proof/'retained-output';run=output/'fictional_606_0'
        (run/'csv').mkdir(parents=True);(run/'input').mkdir()
        for name in ('Person.csv','BenefitUnit.csv'):(run/'csv'/name).write_bytes(b'fictional annual fixture')
        (run/'input/options.txt').write_text('randomSeedIfFixed: 606\n')
        manifest=inventory(output)
        config=comparison.configuration(self.receipt,self.fixture.plan,variant)
        write_json(proof/'configuration.json',config.as_dict());write_json(proof/'output-manifest.json',manifest)
        write_json(folder/'report.json',dict(passed=True,cleanup=True))
        write_json(proof/'report.json',dict(settings=self.fixture.plan,variant=variant,passed=True,cleanup=True,
            source_unchanged=True,input_copies_reclaimed=True,reservations_released=True,
            image=self.receipt['identity']['source_image'],model=self.receipt['identity']['model'],
            prepared_sha256=self.receipt['sha256'],output_location=str(output),
            output_bytes=sum(v['bytes'] for v in manifest.values()),
            attempts=[dict(id=checksum*32,outcome='success')],
            verified_repetitions=[dict(seed='606',fingerprint=checksum*64)]))
        return folder

    def load(self,index=0):
        return comparison.retained_run(self.paths[index],self.receipt,
            'baseline' if index==0 else 'lower-saving-rate')

    def change(self,name,changes):
        path=self.paths[0]/'model-proof'/name
        data=json.loads(path.read_text());data.update(changes);path.write_text(json.dumps(data))

    def supplement(self):
        proof=self.paths[0]/'model-proof';record=json.loads((proof/'report.json').read_text())
        value=dict(passed=True,cleanup=True,simulation_repeated=False,original_native_database_removed=True,
            original_report=fingerprint(proof/'report.json'),configuration_sha256=fingerprint(proof/'configuration.json'),
            output_manifest=fingerprint(proof/'output-manifest.json'),output_bytes=record['output_bytes'],
            verified_repetitions=record['verified_repetitions'],output_location=record['output_location'],
            annual_years=list(range(2019,2071)),prepared_sha256=self.receipt['sha256'])
        write_json(proof/'cleanup-verification.json',value)

    def test_both_native_completions_keep_their_exact_prepared_model_and_seed(self):
        first,second=comparison.pair_sources(self.load(),self.load(1))
        self.assertEqual(first['configuration'].as_dict()['seed_plan']['seeds'],['606'])
        self.assertEqual(second['configuration'].as_dict()['run_sets'][0]['model_args']['savingRate'],.04)
        comparison.unchanged_sources([first,second])

    def test_failed_cleanup_requires_the_complete_matching_verification_supplement(self):
        self.change('report.json',dict(passed=False,cleanup=False))
        with self.assertRaises(FileNotFoundError):self.load()
        self.supplement();self.assertIn('cleanup-verification.json',self.load()['provenance'])
        self.change('cleanup-verification.json',dict(original_report={'sha256':'wrong','bytes':1}))
        with self.assertRaises(ArtifactError):self.load()

    def test_changed_output_and_frozen_settings_are_rejected(self):
        first=self.load();person=next(first['output'].glob('*/csv/Person.csv'))
        person.write_bytes(b'changed')
        with self.assertRaises(ArtifactError):self.load()
        person.write_bytes(b'fictional annual fixture')
        data=first['configuration'].as_dict();data['run_sets'][0]['model_args']['savingRate']=.01
        (first['proof']/'configuration.json').write_text(json.dumps(data))
        with self.assertRaises(ArtifactError):self.load()

    def test_missing_release_evidence_multiple_attempts_and_identical_outputs_are_rejected(self):
        original=json.loads((self.paths[0]/'model-proof/report.json').read_text())
        for changes in (dict(reservations_released=False),dict(failure={'type':'failed'}),
                        dict(attempts=[dict(outcome='success')]*2)):
            self.change('report.json',changes)
            with self.assertRaises(ArtifactError):self.load()
            (self.paths[0]/'model-proof/report.json').write_text(json.dumps(original))
        first,second=self.load(),self.load(1)
        second['record']['verified_repetitions']=deepcopy(first['record']['verified_repetitions'])
        with self.assertRaises(ArtifactError):comparison.pair_sources(first,second)

    def file_catalogue(self,source):
        row=dict(configuration_id=source['configuration'].as_dict()['run_sets'][0]['id'],
                 attempt=dict(execution_key='verified-execution'))
        files=[dict(name=name,path='verified-execution/work/retained-output/'+name,**value)
               for name,value in source['manifest'].items()]
        return files,row

    def test_original_sources_pass_production_file_guards_without_links_or_copies(self):
        from jasmine_web.batch.results import verified_file
        sources=[self.load(),self.load(1)];retained=comparison.RetainedOutputs(sources)
        for source in sources:
            files,row=self.file_catalogue(source)
            mapped=retained.paths(files,row)
            for item,original in zip(mapped,files):
                path=retained.root/item['path']
                self.assertEqual(path,source['output']/original['name'])
                self.assertEqual(path.stat().st_nlink,1)
                with verified_file(retained.root,item) as stream:
                    self.assertEqual(stream.read(),path.read_bytes())
        comparison.unchanged_sources(sources)

    def test_existing_shared_hardlink_rejection_is_preserved(self):
        from jasmine_web.batch.results import OutputUnavailable,verified_file
        source=self.load();retained=comparison.RetainedOutputs([source])
        files,row=self.file_catalogue(source);mapped=retained.paths(files,row)
        path=retained.root/mapped[0]['path'];link=self.root/'shared-link'
        os.link(path,link)
        try:
            with self.assertRaises(OutputUnavailable):
                with verified_file(retained.root,mapped[0]):self.fail('Shared inode was accepted')
        finally:link.unlink()
        comparison.unchanged_sources([source])

    def test_catalogue_paths_and_hashes_cannot_escape_the_verified_original_manifest(self):
        source=self.load();retained=comparison.RetainedOutputs([source]);files,row=self.file_catalogue(source)
        for changes in (dict(path='untrusted/file.csv'),dict(path=files[0]['path']+'/../other'),
                        dict(bytes=1),dict(sha256='f'*64)):
            modified=deepcopy(files);modified[0].update(changes)
            with self.subTest(changes=changes),self.assertRaises((ValueError,ArtifactError)):
                retained.paths(modified,row)

    def test_native_catalogue_uses_the_original_directory_and_rejects_changed_submission(self):
        source=self.load();retained=comparison.RetainedOutputs([source]);config=source['configuration']
        lease=SimpleNamespace(configuration_id='baseline',specification=dict(seeds=['606'],
            run_sets=[dict(id='baseline',parameters=config.editable_configuration())]))
        with patch('deploy.multirun.queue_adapter.validate_outputs',return_value=[]) as validate:
            retained.catalogue(lease,self.root/'disposable-work')
            self.assertEqual(validate.call_args.args[0],source['output'])
            lease.specification['seeds']=['607']
            with self.assertRaises(ArtifactError):retained.catalogue(lease,self.root/'disposable-work')
            self.assertEqual(validate.call_count,1)

    def test_results_validates_the_recorded_completion_before_mapping_original_paths(self):
        from jasmine_web.batch.local_executor import LocalExecutor,atomic_json
        from jasmine_web.batch.results import OutputUnavailable,verified_file
        source=self.load();retained=comparison.RetainedOutputs([source]);config=source['configuration']
        lease=SimpleNamespace(configuration_id='baseline',execution_key='batch-'+str(uuid4()),
            attempt_id=str(uuid4()),specification=dict(seeds=['606'],dataset_id='public-training',
                model_digest=source['record']['image'],prepared_fingerprint=self.receipt['sha256'],
                run_sets=[dict(id='baseline',parameters=config.editable_configuration())]))
        executor=LocalExecutor(self.root/'disposable-execution')
        workspace=executor.workspace(lease);workspace.mkdir()
        atomic_json(workspace/'identity.json',executor._identity(lease))
        (workspace/'work').mkdir()
        queue=SimpleNamespace(_lease=lambda attempt,row:lease)
        results=retained.results(SimpleNamespace(queue=queue),executor,name=lambda run:run['id'],settings=None)
        completed=source['record']['verified_repetitions'][0]['fingerprint']
        row=dict(configuration_id='baseline',id=str(uuid4()),dataset_id='public-training',
            model_digest=lease.specification['model_digest'],prepared_fingerprint=self.receipt['sha256'],
            attempt=dict(id=lease.attempt_id,execution_key=lease.execution_key,
                         finished_at=datetime.now(timezone.utc)),
            repetitions=[dict(actual_seed='606',output_fingerprint=completed)])
        files=[dict(name=Path(name).name,path='retained-output/'+name,**value)
               for name,value in source['manifest'].items() if '/csv/' in name]
        catalogue=[dict(seed='606',fingerprint=completed,files=files)]
        with patch('deploy.multirun.queue_adapter.validate_outputs',return_value=catalogue):
            selected,metadata=results._catalogue(dict(specification=lease.specification),row,'Baseline')
            self.assertEqual(metadata['runs'],[dict(folder='Baseline/run_1',seed='606')])
            self.assertFalse((workspace/'work/output').exists())
            for item in selected:
                with verified_file(retained.root,item) as stream:self.assertTrue(stream.read())
            row['repetitions'][0]['output_fingerprint']='f'*64
            with self.assertRaises(OutputUnavailable):
                results._catalogue(dict(specification=lease.specification),row,'Baseline')

    def test_symlinks_and_extra_native_files_cannot_enter_retained_evidence(self):
        source=self.load();person=next(source['output'].glob('*/csv/Person.csv'))
        (source['output']/'unexpected.txt').write_text('not in the native manifest')
        with self.assertRaises(ArtifactError):self.load()
        (source['output']/'unexpected.txt').unlink()
        person.unlink();person.symlink_to(self.root/'other')
        with self.assertRaises(ArtifactError):self.load()


class ComparisonCleanupTests(unittest.TestCase):
    def test_browser_closes_before_playwright_and_cleanup_failure_preserves_primary_error(self):
        for close_fails in (False,True):
            events=[];record={}
            def close():
                events.append('browser-closed')
                self.assertNotIn('playwright-stopped',events)
                if close_fails:raise RuntimeError('Fictional browser cleanup failure')
            @contextmanager
            def factory():
                try:yield SimpleNamespace(chromium=SimpleNamespace(launch=lambda:SimpleNamespace(close=close)))
                finally:events.append('playwright-stopped')
            original=AssertionError('Original processing failure')
            with self.subTest(close_fails=close_fails),self.assertRaises(AssertionError) as caught:
                with comparison.browser_session(factory,record):raise original
            self.assertIs(caught.exception,original)
            self.assertEqual(events,['browser-closed','playwright-stopped'])
            self.assertEqual(bool(record.get('cleanup_errors')),close_fails)


class ResponseCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.scope=dict(type='http',method='GET',path='/api/visualiser/'+'a'*64+'/data')

    def run_now(self,coroutine):
        # These in-process ASGI calls do not suspend; no network/event-loop
        # socket is needed to verify the exact forwarded messages and evidence.
        try:coroutine.send(None)
        except StopIteration:return
        self.fail('The in-process response unexpectedly suspended')

    def invoke(self,messages,*,scope=None,maximum_bytes=1024,fail_after_body=False):
        async def app(scope,receive,send):
            for message in messages:await send(message)
            if fail_after_body:raise RuntimeError('Fictional transport failure')
        tap=comparison.AggregateCapture(app,self.root,maximum_bytes);forwarded=[]
        async def send(message):forwarded.append(message)
        async def receive():raise AssertionError('The response capture must not read requests')
        self.run_now(tap(scope or self.scope,receive,send))
        return tap,forwarded

    def test_streamed_response_and_refresh_have_the_same_bytes_without_inspector_storage(self):
        messages=[dict(type='http.response.start',status=200,headers=[(b'content-type',b'application/json')]),
            dict(type='http.response.body',body=b'{"aggregate":',more_body=True),
            dict(type='http.response.body',body=b'123}',more_body=False)]
        tap,forwarded=self.invoke(messages)
        self.assertEqual(forwarded,messages)
        first=fingerprint(tap.destination);self.assertEqual(tap.destination.read_bytes(),b'{"aggregate":123}')
        self.assertEqual(tap.destination.stat().st_mode&0o777,0o600)
        self.assertEqual(tap.destination.stat().st_nlink,1)
        async def send(message):forwarded.append(message)
        self.run_now(tap(self.scope,None,send))
        self.assertEqual(fingerprint(tap.destination),first)
        self.assertEqual(len(tap.responses),2)
        self.assertTrue(all({k:r[k] for k in ('bytes','sha256')}==first for r in tap.responses))
        self.assertEqual(list(self.root.glob('*.partial')),[])

    def test_denied_and_unrelated_responses_do_not_save_bodies_or_headers(self):
        for status,path in ((403,self.scope['path']),(200,'/static/application.js')):
            messages=[dict(type='http.response.start',status=status,headers=[(b'set-cookie',b'PRIVATE')]),
                dict(type='http.response.body',body=b'private or unrelated',more_body=False)]
            tap,forwarded=self.invoke(messages,scope=dict(self.scope,path=path))
            self.assertEqual(forwarded,messages)
            self.assertFalse(tap.destination.exists());self.assertEqual(tap.responses,[])

    def test_partial_and_oversized_responses_do_not_become_completed_evidence(self):
        for oversized in (False,True):
            messages=[dict(type='http.response.start',status=200,headers=[]),
                dict(type='http.response.body',body=b'partial bytes',more_body=not oversized)]
            with self.subTest(oversized=oversized),self.assertRaises((RuntimeError,ArtifactError)):
                self.invoke(messages,maximum_bytes=4 if oversized else 1024,fail_after_body=not oversized)
            self.assertFalse((self.root/'delivered-aggregates.json').exists())
            self.assertEqual(list(self.root.glob('*.partial')),[])

    def test_existing_evidence_and_partial_files_are_not_overwritten_or_removed(self):
        existing=self.root/'delivered-aggregates.json';existing.write_bytes(b'preserve evidence')
        with self.assertRaises(FileExistsError):comparison.AggregateCapture(None,self.root,1024)
        self.assertEqual(existing.read_bytes(),b'preserve evidence');existing.unlink()
        partial=self.root/'.aggregate-capture-1.partial';partial.write_bytes(b'preserve partial')
        with self.assertRaises(FileExistsError):
            self.invoke([dict(type='http.response.start',status=200,headers=[]),
                dict(type='http.response.body',body=b'body',more_body=False)])
        self.assertEqual(partial.read_bytes(),b'preserve partial')

    def test_browser_event_observer_never_reads_the_inspector_response_body(self):
        response=SimpleNamespace(url='http://localhost'+self.scope['path'],status=200,
            body=lambda:(_ for _ in ()).throw(RuntimeError('Content evicted from inspector cache')))
        observed=[];comparison.observe_browser_response(response,observed)
        self.assertEqual(observed,[dict(url=response.url,status=200)])


class ResearchAggregateTests(unittest.TestCase):
    def publication(self):
        sys_path=comparison.frontend_path()/'tests/batch'
        import sys
        sys.path.insert(0,str(sys_path))
        from test_visualiser import row
        groups=[]
        for role,mean,delta in (('baseline',20,None),('scenario',25,5)):
            rows=[dict(row(role),year=year,n_runs=1,mean_value=mean,
                paired_mean_delta=delta,paired_lower_ci=delta,paired_upper_ci=delta,
                paired_n_runs=0 if role=='baseline' else 1) for year in range(2019,2071)]
            groups.append(dict(configuration='baseline' if role=='baseline' else 'alternative',rows=rows))
        configurations=[dict(id=identifier,name=identifier,role=role,dataset='public-training',model='frozen-model',
            runs=[dict(folder=role+'/run_1',seed='606')]) for identifier,role in
            (('baseline','Baseline'),('alternative','Scenario'))]
        return dict(format='simpaths.visualiser.v2',backend={'revision':'fictional'},experiment='Fictional calibration',
            configurations=configurations,comparison=dict(baseline='baseline',scenarios=['alternative']),
            data=dict(series=groups,comparison_available=True,notice='Fictional one-seed comparison'))

    def test_all_years_actual_seed_and_one_seed_uncertainty_are_recorded(self):
        evidence=comparison.analyse_publication(json.dumps(self.publication()).encode())
        self.assertEqual(evidence['rows_per_configuration'],[52,52])
        self.assertEqual(evidence['nonzero_paired_cells'],52)
        self.assertIn('cannot estimate uncertainty',evidence['uncertainty_note'])

    def test_raw_fields_wrong_pairing_and_missing_years_are_rejected(self):
        value=self.publication()
        for changes in (dict(id_Person='PRIVATE'),dict(paired_mean_delta=7),dict(paired_n_runs=2)):
            changed=deepcopy(value);changed['data']['series'][1]['rows'][0].update(changes)
            with self.subTest(changes=changes),self.assertRaises((ValueError,AssertionError)):
                comparison.analyse_publication(json.dumps(changed).encode())
        value['data']['series'][1]['rows'].pop()
        with self.assertRaises(AssertionError):comparison.analyse_publication(json.dumps(value).encode())


if __name__=='__main__':unittest.main()
