"""(C) Copyright 2026, by Ross Richardson
Test-only request timings for an isolated SingleRun performance frontend.
Records durations and fixed route categories, never URLs, payloads or credentials.
This module is copied into the disposable test frontend; production does not import it.
@author ross richardson
"""
from contextlib import contextmanager
from contextvars import ContextVar
import functools
import json
import os
from pathlib import Path
import threading
import time


_request = ContextVar('simpaths_performance_request', default=None)
_state_depth = ContextVar('simpaths_performance_state_depth', default=0)


class Measurements:
    def __init__(self):
        self.lock = threading.Lock()
        self.closed = False
        self.values = dict(state_ms=0.0, state_calls=0, java_ms=0.0,
                           java_calls=0, dispatch_ms=0.0, dispatch_calls=0)

    def add(self, kind, elapsed):
        with self.lock:
            if not self.closed:
                self.values[kind+'_ms'] += max(0.0, elapsed*1000)
                self.values[kind+'_calls'] += 1

    def snapshot(self, *, close=False):
        with self.lock:
            self.closed |= close
            return dict(self.values)


@contextmanager
def state_span():
    """Count an outer state operation once, including waits and failures."""
    record = _request.get()
    depth = _state_depth.get()
    token = _state_depth.set(depth+1)
    began = time.perf_counter()
    try:
        yield
    finally:
        _state_depth.reset(token)
        if record is not None and depth == 0:
            record.add('state', time.perf_counter()-began)


def install(site, postgres_class, redis_class, pipeline_class=None):
    """Preserve application results/exceptions; time its existing state and Java calls."""
    original_connection = postgres_class.connection

    @contextmanager
    def connection(self, *args, **kwargs):
        with state_span():
            with original_connection(self, *args, **kwargs) as value:
                yield value

    postgres_class.connection = connection
    original_command = redis_class.execute_command

    @functools.wraps(original_command)
    def command(self, *args, **kwargs):
        with state_span():
            return original_command(self, *args, **kwargs)

    redis_class.execute_command = command
    if pipeline_class is not None:
        # WATCH/GET before MULTI and the eventual EXEC bypass Redis.execute_command.
        for name in ('immediate_execute_command', 'execute'):
            original = getattr(pipeline_class, name)

            def pipeline_call(self, *args, _original=original, **kwargs):
                with state_span():
                    return _original(self, *args, **kwargs)

            setattr(pipeline_class, name, functools.wraps(original)(pipeline_call))
    original_state_call = site._state_call

    @functools.wraps(original_state_call)
    async def state_call(*args, **kwargs):
        record = _request.get()
        began = time.perf_counter()
        try:
            return await original_state_call(*args, **kwargs)
        finally:
            if record is not None:
                record.add('dispatch', time.perf_counter()-began)

    site._state_call = state_call
    original_java = site.send_java_request

    @functools.wraps(original_java)
    async def java_call(*args, **kwargs):
        record = _request.get()
        before = record.snapshot()['state_ms'] if record is not None else 0
        began = time.perf_counter()
        try:
            return await original_java(*args, **kwargs)
        finally:
            if record is not None:
                # Redis credential resolution happens inside the proxy helper;
                # exclude that measured state time from the Java/proxy span.
                state_elapsed = (record.snapshot()['state_ms']-before)/1000
                record.add('java', time.perf_counter()-began-state_elapsed)

    site.send_java_request = java_call


class TimingMiddleware:
    def __init__(self, app, path):
        self.app = app
        self.lock = threading.Lock()
        self.sequence = 0
        self.started = time.perf_counter()
        # Exclusive creation and no link following; the harness owns its directory.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(fd, 'w', buffering=1)

    async def __call__(self, scope, receive, send):
        parts = scope.get('path', '').split('/')
        endpoint = parts[1] if len(parts) == 3 and parts[1] in ('status', 'charts', 'logs') else None
        if scope['type'] != 'http' or endpoint is None:
            return await self.app(scope, receive, send)
        with self.lock:
            self.sequence += 1
            sequence = self.sequence
        record = Measurements()
        token = _request.set(record)
        began = time.perf_counter()
        code = 500
        failure = None

        async def timed_send(message):
            nonlocal code
            if message['type'] == 'http.response.start':
                code = message['status']
                message = {**message, 'headers': [*message.get('headers', []),
                    (b'x-simpaths-performance-id', str(sequence).encode())]}
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        except BaseException as error:
            failure = type(error).__name__
            raise
        finally:
            values = record.snapshot(close=True)
            elapsed = (time.perf_counter()-began)*1000
            row = dict(sequence=sequence, endpoint=endpoint, status=code,
                       start_ms=round((began-self.started)*1000, 3),
                       total_ms=round(elapsed, 3), **values)
            row['other_ms'] = max(0.0, elapsed-values['state_ms']-values['java_ms'])
            if failure:
                row['failure'] = failure
            try:
                with self.lock:
                    self.stream.write(json.dumps(row)+'\n')
            finally:
                _request.reset(token)


def create_app():
    """Uvicorn factory used exclusively by the disposable benchmark harness."""
    if (os.environ.get('DEPLOY_MODE') != 'vm'
            or os.environ.get('PYTHON_DOTENV_DISABLED') != '1'
            or os.environ.get('VM_MAX_SESSIONS') != '1'):
        raise RuntimeError('Performance instrumentation requires the isolated single-session harness')
    path = Path(os.environ['SIMPATHS_PERFORMANCE_LOG'])
    if not path.is_absolute():
        raise ValueError('Private performance evidence path must be absolute')
    import app as site
    from jasmine_web.vm_state import PostgresVMState
    from redis import Redis
    from redis.client import Pipeline
    install(site, PostgresVMState, Redis, Pipeline)
    return TimingMiddleware(site.app, path)
