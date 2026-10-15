"""(C) Copyright 2026, by Ross Richardson

Private executor shutdown retains real filesystem guards through ZIP cancellation.
Uses fictional output and native threads/locks, without PostgreSQL, Docker,
network listeners, email or scientific simulations.
@author ross richardson
"""
import asyncio
from contextlib import asynccontextmanager, ExitStack
import hashlib
from pathlib import Path
import signal
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path

FRONTEND = frontend_path()
sys.path.insert(0, str(FRONTEND))
from jasmine_web.batch.downloads import Downloads, lock, MIB
from jasmine_web.batch.policy import Conflict, NotFound
from jasmine_web.batch.results import Archive
from .maintenance import service_state, state_guard
from .artifacts import ArtifactError
from .runtime import private_processors
from .vm_executor import serve_private


async def until(condition):
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(.001)


class FictionalResults:
    """Real file/guard ownership; access-control tests use the database elsewhere."""
    def __init__(self, root):
        self.executor = SimpleNamespace(root=root)
        self.service = SimpleNamespace(access=self)
        contents = b'seed,value\n606,1\n'*10000
        (root/'data.csv').write_bytes(contents)
        self.files = [dict(name='Scenario/run_1/csv/Values.csv', path='data.csv',
                           bytes=len(contents), sha256=hashlib.sha256(contents).hexdigest())]
        self.released = threading.Event()
        self.release_checks = []
        self.release_errors = []

    def authenticate(self, token, **kwargs):
        if token != 'owner':
            raise NotFound('Unavailable')

    def download_selection(self, token, job, *, baseline=None, include_inputs=False, owner=None):
        if owner is None:
            self.authenticate(token)
        return dict(owner='owner', job=job, baseline=baseline, include_inputs=include_inputs)

    def archive(self, token, job, **kwargs):
        self.authenticate(token)
        guards = ExitStack()
        guards.enter_context(lock(self.executor.root/'source.lock', shared=True))
        def release():
            try:
                for check in self.release_checks:
                    check()
            except BaseException as error:
                # A thread assertion must be observed by the test, not merely
                # printed by threading.excepthook after cleanup has finished.
                self.release_errors.append(error)
            finally:
                guards.close()
                self.released.set()
        return Archive(self.executor.root, self.files, dict(files=self.files),
                       lambda: self.authenticate(token), release, filename='results-test.zip')


class PrivateDownloadShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_zip(self, *, cancel=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = FictionalResults(root)
            downloads = Downloads(results, threshold=1, reserve=0, capacity=8*MIB)
            service = SimpleNamespace(results=SimpleNamespace(downloads=downloads))
            stopped, writing, release, storage_closed = (threading.Event() for _ in range(4))
            maintenance_ready = threading.Event()
            visualiser = SimpleNamespace(stop=threading.Event(), tick=lambda: None, retire=lambda: None)
            visualiser.close = visualiser.stop.set
            service.visualiser = visualiser
            original = downloads._write_zip
            original_retire = downloads.retire
            def retire():
                original_retire()
                maintenance_ready.set()
            def write(*args):
                writing.set()
                if not release.wait(5):
                    raise AssertionError('ZIP gate was not released')
                return original(*args)
            def assert_storage_held():
                with self.assertRaises(ArtifactError):
                    with state_guard(root, exclusive=True):
                        self.fail('Storage fence released before source cleanup')
            results.release_checks.append(assert_storage_held)
            @asynccontextmanager
            async def lifespan():
                try:
                    with service_state(root):
                        async with private_processors(service, stopped):
                            yield
                finally:
                    storage_closed.set()
            context = lifespan()
            shutdown = None
            with patch.object(downloads, '_write_zip', side_effect=write), \
                    patch.object(downloads, 'retire', side_effect=retire):
                await context.__aenter__()
                try:
                    # Wait for the first maintenance pass, avoiding an expected
                    # nonblocking admission conflict with its cache lock.
                    await until(maintenance_ready.is_set)
                    initial = downloads.request('owner', 'csrf', 'first')
                    await until(writing.is_set)
                    self.assertTrue((downloads._path(initial['id'])/'archive.part').exists())
                    shutdown = asyncio.create_task(context.__aexit__(None, None, None))
                    await until(downloads.stop.is_set)
                    with self.assertRaises(Conflict):
                        downloads.request('owner', 'csrf', 'second')
                    assert_storage_held()
                    with self.assertRaises(BlockingIOError):
                        with lock(root/'source.lock'):
                            self.fail('Source lock released before ZIP worker stopped')
                    for _ in range(2):
                        if cancel:
                            shutdown.cancel()
                        await asyncio.sleep(.01)
                        self.assertFalse(shutdown.done())
                        self.assertFalse(storage_closed.is_set())
                    release.set()
                    if cancel:
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(shutdown, 2)
                    else:
                        await asyncio.wait_for(shutdown, 2)
                    self.assertTrue(storage_closed.is_set())
                    self.assertTrue(results.released.is_set())
                    self.assertEqual(results.release_errors, [])
                    self.assertFalse(downloads.threads)
                    self.assertTrue(visualiser.stop.is_set())
                    self.assertFalse(any(t.is_alive() and t.name in (
                        'multirun-download','multirun-private-cache','multirun-private-visualiser')
                        for t in threading.enumerate()))
                    self.assertEqual(downloads.status('owner', initial['id'])['state'], 'interrupted')
                    self.assertFalse(list(downloads._path(initial['id']).glob('archive.*')))
                    with state_guard(root, exclusive=True), lock(root/'source.lock'):
                        pass
                finally:
                    release.set()
                    if shutdown is None:
                        await context.__aexit__(None, None, None)
                    elif not shutdown.done():
                        try:
                            await shutdown
                        except asyncio.CancelledError:
                            pass
                    await asyncio.to_thread(downloads.close)

    async def test_private_shutdown_drains_zip_before_storage_fences(self):
        await self.exercise_zip()

    async def test_repeated_cancellation_cannot_detach_private_zip_cleanup(self):
        await self.exercise_zip(cancel=True)

    async def test_partial_processor_startup_still_closes_started_workers(self):
        stopped, closed = threading.Event(), threading.Event()
        downloads = SimpleNamespace(retire=lambda: None, close=closed.set)
        visualiser = SimpleNamespace(stop=threading.Event(), tick=lambda: None, retire=lambda: None)
        visualiser.close = visualiser.stop.set
        service = SimpleNamespace(results=SimpleNamespace(downloads=downloads), visualiser=visualiser)
        start = threading.Thread.start
        def start_worker(worker):
            if worker.name == 'multirun-private-visualiser':
                raise RuntimeError('No thread capacity')
            return start(worker)
        with patch.object(threading.Thread, 'start', start_worker):
            with self.assertRaises(RuntimeError):
                async with private_processors(service, stopped):
                    self.fail('Incomplete processors reached service startup')
        self.assertTrue(stopped.is_set())
        self.assertTrue(closed.is_set())
        self.assertTrue(visualiser.stop.is_set())
        self.assertFalse(any(t.is_alive() and t.name in (
            'multirun-private-cache','multirun-private-visualiser') for t in threading.enumerate()))

    async def test_two_private_slots_are_started_and_joined_before_lifespan_returns(self):
        stopped,closed=threading.Event(),threading.Event()
        started=[threading.Event(),threading.Event()]
        visualiser=SimpleNamespace(stop=threading.Event(),max_workers=2,
            tick=lambda slot=0:started[slot].set(),retire=lambda:None)
        visualiser.close=visualiser.stop.set
        downloads=SimpleNamespace(retire=lambda:None,close=closed.set)
        service=SimpleNamespace(results=SimpleNamespace(downloads=downloads),visualiser=visualiser)
        async with private_processors(service,stopped):
            self.assertTrue(await asyncio.to_thread(started[0].wait,2))
            self.assertTrue(await asyncio.to_thread(started[1].wait,2))
        self.assertTrue(stopped.is_set())
        self.assertTrue(closed.is_set())
        self.assertFalse(any(t.is_alive() and t.name.startswith('multirun-private-visualiser')
                             for t in threading.enumerate()))

    async def test_executor_cancellation_drains_rpc_before_entering_cache_shutdown(self):
        closing, release, storage_closed = (threading.Event() for _ in range(3))
        loop = asyncio.get_running_loop()
        handlers = {}
        @asynccontextmanager
        async def lifespan(app):
            try:
                yield
            finally:
                storage_closed.set()
        def close():
            closing.set()
            if not release.wait(5):
                raise AssertionError('RPC drain gate was not released')
        server = SimpleNamespace(start=lambda: None, close=close)
        bundle = SimpleNamespace(service=object(), lifespan=lifespan, ready=lambda: dict(ready=True))
        task = None
        try:
            with patch('jasmine_web.batch.service_rpc.RPCServer', return_value=server), \
                    patch.object(loop, 'add_signal_handler', side_effect=lambda signum, handler: handlers.update({signum:handler})), \
                    patch.object(loop, 'remove_signal_handler') as removed:
                task = asyncio.create_task(serve_private(bundle, '/unused/socket', 1001))
                await until(lambda: signal.SIGTERM in handlers)
                handlers[signal.SIGTERM]()
                await until(closing.is_set)
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(.01)
                    self.assertFalse(task.done())
                    self.assertFalse(storage_closed.is_set())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                self.assertTrue(storage_closed.is_set())
                self.assertEqual(removed.call_count, 2)
        finally:
            release.set()
            if task is not None and not task.done():
                try:
                    await task
                except asyncio.CancelledError:
                    pass


if __name__ == '__main__':
    unittest.main()
