"""(C) Copyright 2026, by Ross Richardson
Exercise real historical chart preparation after every fictional raw CSV is removed.
@author ross richardson
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from threading import BoundedSemaphore
from unittest.mock import Mock, patch

from .test_visualiser_backend import sources
from .visualiser_backend import VisualiserBackend


@unittest.skipUnless(os.getenv('SIMPATHS_HISTORICAL_BUILD') and os.getenv('SIMPATHS_VISUALISER_TEST_BUILD'),
                     'Requires both reviewed calculator releases')
class HistoricalCalculatorTests(unittest.TestCase):
    def test_protected_pair_survives_upgrade_without_raw_files(self):
        from jasmine_web.batch.policy import Conflict, NotFound, Resources
        from jasmine_web.batch.visualiser import Visualiser
        from jasmine_web.batch.visualiser_export import archive
        from jasmine_web.batch.visualiser_sources import SummaryCalculationMismatch
        import subprocess

        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cache=root/'summaries'
            original=VisualiserBackend(os.environ['SIMPATHS_HISTORICAL_BUILD'],root,summary_cache_root=cache)
            active=VisualiserBackend(os.environ['SIMPATHS_VISUALISER_TEST_BUILD'],root,summary_cache_root=cache)
            self.assertNotIn('historical-runner.cjs',active.assets)
            version=original.calculation_identity(original.identity)
            matching=[c for c in active.historical_calculators if c.calculation_identity(c.identity)==version]
            self.assertEqual(len(matching),1)
            now=datetime.now(timezone.utc)
            retained=dict(backend=version,protected_at=now.isoformat(),expires_at=(now+timedelta(days=1)).isoformat())
            # Include a simulated year beyond the starting population so both
            # editions can publish charts when rebuilding from these raw files.
            inputs=sources(root,people_count=48,years=(2019,2020))
            jobs=[];plans=[];rows={}
            for number,source in enumerate(inputs):
                config=source['configuration']
                job=str(number)
                identity=dict(job=job,attempt='attempt-'+job,name=config['name'],configuration=config['id'],model='fixture-model',prepared='fixture-inputs',origins=[['user','alice']],
                    repetitions=[dict(seed=run['seed']) for run in config['runs']])
                jobs.append(identity)
                config.update(completed_at=now.isoformat(),summary_retention=retained)
                source.update(owner='alice',identity=identity)
                rows[job]=dict(origins=identity['origins'],retained_source={
                    'identity':identity,'source':source,'summary_protected_at':now,'summary_expires_at':now+timedelta(days=1),
                    'summary_backend':version})
                plans.append(dict(identity=identity,source=source,established=True))
            work=root/'before';work.mkdir()
            def command(argv,*,pass_fds=()):
                subprocess.run(argv,check=True,pass_fds=pass_fds,capture_output=True,timeout=30)
            expected=original.process(inputs,work,command,lambda count:None,comparison_set=True)
            with original.preserve_summary_sources('alice','exp',plans,owner_entries=10):pass
            raw_files={path:path.read_bytes() for path in root.rglob('*.csv')}
            for path in raw_files:path.unlink()
            for source in inputs:source['retained_only']=True

            def selection(token,job=None,*,baseline=None,scenarios=None,**options):
                ids=[job] if job is not None else [baseline,*scenarios]
                return dict(owner='alice',experiment='exp',pool='test',job=job,baseline=baseline,
                    jobs=[jobs[int(i)] for i in ids],summary_calculations=[version for _ in ids],
                    **({'scenarios':scenarios} if scenarios is not None else {}))
            @contextmanager
            def read_sources(selected):
                yield [deepcopy(inputs[int(j['job'])]) for j in selected['jobs']],lambda:None,()
            results=SimpleNamespace(slots=BoundedSemaphore(2),executor=SimpleNamespace(root=root),queue=SimpleNamespace(pool_id='test'),
                processing_selection=selection,processing_sources=read_sources,
                _selected=lambda token,job,**options:({'id':'exp'},rows[job]),
                processing_identity=lambda exp,row:row['retained_source']['identity'])
            service=Visualiser(results,active,resources=Resources(1000,2048,512))
            pair=service._selection('alice',baseline='0',scenarios=['1'])
            with self.assertRaises(SummaryCalculationMismatch):
                service._enqueue(dict(pair,summary_calculations=[version,None]))
            with self.assertRaises(SummaryCalculationMismatch):
                service._enqueue(dict(pair,summary_calculations=[dict(version,node='unknown')]*2))
            report=service._enqueue(pair,active=True);key=report['id'];record=service._record(key)
            self.assertNotEqual(key,service._key(pair))
            def check_clock(results,owner,identity,clock):
                self.assertEqual((owner,clock),('alice',retained))
            # SQL lookups are the fixture boundary. Keep the worker, command
            # limits, source hashing, paired calculation and publication real.
            with patch('jasmine_web.batch.visualiser_sources.commit_retention',side_effect=check_clock), \
                 patch.object(service,'_sync_summary_retention'), \
                 patch.object(VisualiserBackend,'_verified_source',side_effect=AssertionError('Unexpected raw read')):
                with (root/'worker.lock').open('wb') as guard:
                    service._build(key,record,guard.fileno())
            ready=service._record(key)
            self.assertEqual(ready['state'],'ready',ready.get('message'))
            self.assertEqual(ready['expires_at'],datetime.fromisoformat(retained['expires_at']).timestamp())
            published=json.loads((service._path(key)/'published.json').read_bytes())
            self.assertEqual(published['data']['series'],expected['series'])
            self.assertTrue(published['data']['comparison_available'])
            self.assertIn('Historical Visualiser',published['data']['notice'])
            catalogue=json.loads(service.catalogue_bytes('alice',key))
            self.assertEqual(catalogue['calculator_revision'],matching[0].historical_revision)
            stream=archive(service,'alice',key)
            try:
                with zipfile.ZipFile(io.BytesIO(b''.join(stream.iterator))) as saved:
                    manifest_file=next(n for n in saved.namelist() if n.endswith('/manifest.json'))
                    manifest=json.loads(saved.read(manifest_file))
                    if os.environ.get('SIMPATHS_FIXTURE_EXPORT'):
                        # This ZIP was just generated above using only the fictional experiment.
                        saved.extractall(os.environ['SIMPATHS_FIXTURE_EXPORT'])
            finally:
                stream.close()
            self.assertEqual(manifest['catalogue']['calculator_revision'],catalogue['calculator_revision'])
            self.assertEqual(manifest['provenance']['calculation_files'],version['calculation']['files'])
            self.assertEqual(manifest['provenance']['application_sha256'],active.manifest['files']['visualiser.js'])
            # Recreate a publication written by the earlier service, with no
            # revision hint in its status or index. Its existing URL still opens.
            old_service=Visualiser(results,original,resources=Resources(1000,2048,512))
            old_key=old_service._enqueue(pair,active=True)['id']
            with patch('jasmine_web.batch.visualiser_sources.commit_retention',side_effect=check_clock), \
                 patch.object(old_service,'_sync_summary_retention'), \
                 (root/'old-worker.lock').open('wb') as guard:
                old_service._build(old_key,old_service._record(old_key),guard.fileno())
            self.assertEqual(old_service._record(old_key)['state'],'ready')
            index=old_service._path(old_key)/'views'/'index.json'
            stored=index.read_bytes()
            self.assertNotIn('calculator_revision',json.loads(stored)['catalogue'])
            recovered=json.loads(service.catalogue_bytes('alice',old_key))
            self.assertEqual(recovered['calculator_revision'],catalogue['calculator_revision'])
            self.assertEqual(index.read_bytes(),stored)
            # Restore just the fictional raw files for a second selection workflow.
            for path,data in raw_files.items():path.write_bytes(data)
            configurations=[item['configuration'] for item in jobs]
            context=dict(configurations=configurations,baseline=configurations[0])
            exp=dict(id='exp',owner_id='alice',specification=dict(run_sets=[dict(id=c) for c in configurations]))
            for j,source in zip(jobs,inputs):
                source.pop('retained_only')
                rows[j['job']].update(id=j['job'],configuration_id=j['configuration'],attempt={'id':j['attempt']},
                    state='succeeded',processable=True,aggregate_available=True,output_state='retained',
                    source_summary_retention=retained)
            results.visualiser_snapshot=lambda token,experiment,configs:(exp,list(rows.values()))
            results._snapshot=lambda token,**context:(exp,list(rows.values()))
            access=Mock()
            results.service=SimpleNamespace(access=access)
            def status():
                return service.experiment_status('alice','exp',**context)
            def recalculate():
                return service.recalculate('alice','csrf','exp',**context)
            former=status()['publication']
            self.assertTrue(former)
            rows['0'].update(processable=False,output_state='deleted')
            self.assertEqual(status()['recalculation']['deleted'],1)
            self.assertFalse(service.status('alice',key)['recalculation']['available'])
            with self.assertRaises(Conflict):service.recalculate_key('alice','csrf',key)
            with self.assertRaises(Conflict):recalculate()
            rows['0'].update(processable=True,output_state='retained')
            self.assertTrue(service.status('alice',old_key)['recalculation']['available'])
            self.assertEqual(service.recalculate_key('alice','csrf',old_key)['state'],'pending')
            self.assertEqual(service.recalculate_key('alice','csrf',key)['state'],'pending')
            self.assertEqual(recalculate()['state'],'pending')
            access.authenticate.assert_called_with('alice',csrf='csrf',mutation=True)
            self.assertEqual(recalculate()['state'],'pending')
            tasks=[(k,r) for k,r in service._entries() if r['selection'].get('recalculate')]
            self.assertEqual(len(tasks),3)
            with patch.object(service,'_sync_summary_retention'), (root/'worker.lock').open('wb') as guard:
                for k,r in tasks:
                    self.assertEqual(status()['publication'],former)
                    service._build(k,r,guard.fileno())
                    built=service._record(k)
                    self.assertEqual(built['state'],'ready',built.get('message'))
                    self.assertEqual(built['expires_at'],ready['expires_at'])
            self.assertEqual(status()['recalculation']['state'],'ready')
            self.assertNotEqual(status()['publication'],former)
            new_url=service.status('alice',key)['recalculation']['url']
            self.assertNotIn('calculator_revision',json.loads(service.catalogue_bytes('alice',new_url.split('/')[-1])))
            current=json.loads(service.experiment_catalogue('alice','exp',**context))
            self.assertNotIn('calculator_revision',current)
            self.assertTrue(current['comparison_available'])
            self.assertEqual(json.loads(service.catalogue_bytes('alice',key)),catalogue)
            for row in rows.values():
                self.assertEqual(row['source_summary_retention'],retained)
            self.assertEqual(len(active.summary_cache.holds.load()),4)
            # The additional current edition remains reproducible if the publication
            # is evicted after the raw files have been retired.
            for path in raw_files:path.unlink()
            for source in inputs:source['retained_only']=True
            latest_key=new_url.split('/')[-1]
            latest=service._record(latest_key)
            (service._path(latest_key)/'published.json').unlink()
            with patch.object(service,'_sync_summary_retention'), \
                 patch.object(VisualiserBackend,'_verified_source',side_effect=AssertionError('Unexpected raw read')), \
                 (root/'worker.lock').open('wb') as guard:
                service._build(latest_key,latest,guard.fileno())
            restored=service._record(latest_key)
            self.assertEqual(restored['state'],'ready',restored.get('message'))
            self.assertEqual(restored['expires_at'],latest['publication_expires_at'])
            with self.assertRaises(NotFound):
                with patch.object(active,'allowed',return_value=False):service.catalogue_bytes('alice',key)
