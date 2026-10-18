"""(C) Copyright 2026, by Ross Richardson

Production seed-parallel parsing against genuine-shape fictional inputs, with
unchanged scientific output, cache identity, failure cleanup and restart reuse.
@author ross richardson
"""
from concurrent.futures import ThreadPoolExecutor
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest

from . import test_visualiser_backend as fixture
from .visualiser_backend import VisualiserBackend
from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
from jasmine_web.batch.policy import Conflict,Resources
from jasmine_web.batch.preparation_commands import PreparationCommands


@unittest.skipUnless(fixture.BUILD,'Set SIMPATHS_VISUALISER_TEST_BUILD to the reviewed runner')
class ParallelBackendTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.selected=fixture.sources(self.root,alternatives=2)
        for source in self.selected:
            source['owner']='alice';source['identity']=dict(job=source['configuration']['id'],attempt='one')
        self.backend=VisualiserBackend(fixture.BUILD,self.root,memory_mib=1792,summary_cache_root=self.root/'cache')
        self.next_work=0

    def process(self,selected=None,*,workers=2,backend=None,observe=None,source_ready=None):
        self.next_work+=1;work=self.root/('work-'+str(self.next_work));work.mkdir(mode=0o700)
        commands=PreparationCommands(work,timeout=shutil.which('timeout'),runtime=30,
            check=lambda:None,stop=threading.Event(),workers=workers)
        progress=[]
        with commands:
            if observe is not None:observe(commands)
            result=(backend or self.backend).process(selected or self.selected,work,commands,progress.append,
                comparison_set=True,seed_workers=workers,resource_budget=Resources(2000,5120,2048),
                source_ready=source_ready)
        self.assertFalse(commands.children)
        self.assertEqual(progress,list(range(1,sum(len(s['configuration']['runs']) for s in selected or self.selected)+1)))
        return result

    def test_parallel_and_serial_seed_rows_pairing_uncertainty_and_order_match(self):
        baseline=VisualiserBackend(fixture.BUILD,self.root,memory_mib=4352,summary_cache_root=self.root/'serial-cache')
        expected=self.process(workers=1,backend=baseline)
        original=self.backend._seed;gate=threading.Barrier(2);started=[];lock=threading.Lock()
        def seed(*args):
            with lock:started.append(threading.get_ident());number=len(started)
            if number<=2:gate.wait(timeout=5)
            return original(*args)
        self.backend._seed=seed
        actual=self.process()
        self.assertEqual(actual,expected);self.assertGreater(len(set(started)),1)
        self.assertEqual([s['configuration'] for s in actual['series']],['baseline','scenario_1','scenario_2'])
        self.assertEqual(self.backend.summary_cache.statistics()['counters']['raw_completed'],9)

    def test_parallel_reuse_and_baseline_swap_do_not_reparse_or_change_cache_identity(self):
        expected=self.process();before=self.backend.summary_cache.statistics()['counters']['raw_started']
        self.assertEqual(self.process(),expected)
        swapped=copy.deepcopy(self.selected[:2][::-1])
        swapped[0]['configuration']['role']='Baseline';swapped[1]['configuration']['role']='Scenario'
        self.process(swapped)
        self.assertEqual(self.backend.summary_cache.statistics()['counters']['raw_started'],before)

    def test_readiness_notifies_once_only_after_every_seed_of_its_exact_source(self):
        original=self.backend._seed
        for workers in (1,2):
            complete=set();ready=[];guard=threading.Lock()
            def seed(source,run,*args):
                value=original(source,run,*args)
                with guard:complete.add((source['identity']['job'],str(run['seed'])))
                return value
            def notified(job):
                source=next(s for s in self.selected if s['identity']['job']==job)
                with guard:
                    self.assertTrue(all((job,str(r['seed'])) in complete for r in source['configuration']['runs']))
                ready.append(job)
            self.backend._seed=seed
            self.process(workers=workers,source_ready=notified)
            self.assertCountEqual(ready,[s['identity']['job'] for s in self.selected])
        self.backend._seed=original

    def test_failed_seed_stops_live_sibling_before_backend_returns(self):
        original=self.backend._seed;gate=threading.Barrier(2);pids=[]
        def seed(configuration,run,output,role,commands,heap):
            gate.wait(timeout=5)
            if run['seed']=='606':
                marker=self.root/'pid'
                script='import os,pathlib,time;pathlib.Path('+repr(str(marker))+').write_text(str(os.getpid()));time.sleep(30)'
                return commands([sys.executable,'-c',script])
            deadline=time.monotonic()+5
            while not (self.root/'pid').exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue((self.root/'pid').exists());pids.append(int((self.root/'pid').read_text()))
            raise ValueError('Fictional seed rejection')
        self.backend._seed=seed
        with self.assertRaises(ValueError):self.process()
        for pid in pids:
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        self.assertFalse(list(self.root.glob('work-*/configuration-*-aggregate.json')))
        self.backend._seed=original;self.process()

    def test_cache_lock_wait_cancels_while_another_owner_of_the_lock_is_still_running(self):
        cache=self.backend.summary_cache;identity=dict(owner='alice',configuration='baseline',seed='606')
        stop=threading.Event();started=threading.Event()
        def check():
            started.set()
            if stop.is_set():raise Conflict('Stopped')
        def blocked():
            with cache.entry(identity,check=check):raise AssertionError('Blocked lock was acquired')
        with cache.entry(identity),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(blocked);self.assertTrue(started.wait(2));stop.set()
            with self.assertRaises(Conflict):future.result(timeout=2)

    def test_unfunded_or_uncancellable_parallel_requests_are_rejected(self):
        for resources in (Resources(1000,5120,2048),Resources(2000,2560,2048),None):
            with self.subTest(resources=resources),self.assertRaises(ValueError):
                self.backend.process_files(self.selected,self.root,lambda *a,**k:None,lambda _:None,
                    seed_workers=2,resource_budget=resources)

    def test_private_service_runs_the_actual_parallel_backend_through_publication_and_indexing(self):
        old=list(sys.path);self.addCleanup(lambda:setattr(sys,'path',old))
        sys.path.insert(0,str(frontend_path()/'tests/batch'))
        import test_visualiser_preparation as platform
        from jasmine_web.batch.visualiser import Visualiser
        results=platform.Results(self.root)
        results.sources={s['configuration']['id']:dict(owner='alice',state='succeeded',exists=True) for s in self.selected}
        results.baseline='baseline'
        selection=results.processing_selection
        def selected(*args,**kwargs):
            result=selection(*args,**kwargs)
            for job in result['jobs']:
                job['repetitions']=[dict(seed=r['seed'],fingerprint='f'*64)
                    for r in next(s for s in self.selected if s['configuration']['id']==job['configuration'])['configuration']['runs']]
            return result
        results.processing_selection=selected
        @contextmanager
        def sources(chosen):
            value=[copy.deepcopy(next(s for s in self.selected if s['configuration']['id']==j['configuration'])) for j in chosen['jobs']]
            yield value,lambda:None,()
        results.processing_sources=sources;budgets=[]
        # This in-memory fixture has no experiment-retention SQL tables; the
        # native PostgreSQL proof covers that layer independently.
        self.backend.supports_summary_retention=False
        class Scope:
            pass_fds=()
            def wrap(self,argv):return argv
        @contextmanager
        def scoped(resources,processor):
            self.assertEqual(results.queue.reserved[processor][0],resources);budgets.append(resources)
            yield Scope()
        visualiser=Visualiser(results,self.backend,resources=Resources(1000,2560,2048),
            seed_workers=2,resource_scope=scoped)
        key=visualiser.request('alice','valid',None,baseline='baseline',scenarios=['scenario_1','scenario_2'])['id']
        visualiser.tick();record=visualiser._record(key)
        self.assertEqual(record['state'],'ready');self.assertEqual(record['seed_workers'],2)
        self.assertEqual(budgets,[Resources(2000,5120,2048)])
        self.assertFalse(results.queue.reserved);self.assertFalse((visualiser._path(key)/'private').exists())
        publication=json.loads(visualiser.data_bytes('alice',key))
        serial=VisualiserBackend(fixture.BUILD,self.root,memory_mib=1792)
        expected=self.process(workers=1,backend=serial)
        self.assertEqual(publication['data'],expected)
        catalogue=json.loads(visualiser.catalogue_bytes('alice',key))
        self.assertEqual(catalogue['seeds'],['606','607','608'])
        counters=self.backend.summary_cache.statistics()['counters']
        self.assertEqual((counters['raw_started'],counters['raw_completed']),(9,9))


if __name__=='__main__':unittest.main()
