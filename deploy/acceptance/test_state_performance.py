"""(C) Copyright 2026, by Ross Richardson
Verify measurement attribution, private evidence, sampling and comparison safeguards.
Uses fictional requests; real PostgreSQL/Java/browser performance requires the runner.
@author ross richardson
"""
import asyncio
import ast
from contextlib import chdir, contextmanager
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch
import types

load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
runner = load_tool('acceptance/run_state_performance.py')
timing = load_tool('acceptance/_state_performance_frontend.py')


class SummaryTests(unittest.TestCase):
    def test_large_sample_reports_median_percentiles_and_slow_request_frequency(self):
        rows = [dict(request_ms=n, http_status=200) for n in range(1, 1001)]
        summary = runner.summarize(rows)
        self.assertEqual(summary['requests'], 1000)
        self.assertEqual(summary['median_ms'], 500.5)
        self.assertEqual(summary['p95_ms'], 950)
        self.assertEqual(summary['p99_ms'], 990)
        self.assertEqual(summary['over_500_percent'], 50)
        self.assertEqual(summary['failures'], 0)

    def test_failed_and_missing_requests_remain_in_the_report(self):
        rows = [dict(request_ms=None, failure='network_failure'),
                dict(request_ms=12, http_status=503), dict(request_ms=20, body_error=True)]
        summary = runner.summarize(rows)
        self.assertEqual(summary['requests'], 3)
        self.assertEqual(summary['measured'], 2)
        self.assertEqual(summary['failures'], 3)
        for value in (float('nan'), float('inf'), -1):
            with self.assertRaises(AssertionError):
                runner.summarize([dict(request_ms=value)])

    def test_running_samples_are_never_combined_with_completed_charts(self):
        rows = [dict(endpoint='charts', phase=phase, request_ms=elapsed)
                for phase, elapsed in [('running', 1200), ('completed-charts', 20), ('chart-warmup', 100)]]
        result = runner.grouped(rows)
        self.assertEqual(result['running']['charts']['mean_ms'], 1200)
        self.assertEqual(result['completed-charts']['charts']['mean_ms'], 20)

    def test_pair_order_alternates_and_each_pair_has_both_backends(self):
        self.assertEqual(runner.order(3), [(1, 'redis'), (1, 'postgres'),
                                        (2, 'postgres'), (2, 'redis'), (3, 'redis'), (3, 'postgres')])

    def test_single_model_headroom_checks_do_not_use_two_session_allocation(self):
        runner.resource_check(dict(available_memory_bytes=7*runner.GIB, root_free_bytes=8*runner.GIB))
        for memory, disk in ((7*runner.GIB-1, 8*runner.GIB), (7*runner.GIB, 8*runner.GIB-1)):
            with self.assertRaises(AssertionError):
                runner.resource_check(dict(available_memory_bytes=memory, root_free_bytes=disk))

    def test_full_run_requires_stopped_engine_after_the_final_year_cleanup(self):
        self.assertFalse(runner.simulation_completed(dict(status='paused', time=2026), 2026))
        self.assertFalse(runner.simulation_completed(dict(status='running', time=2027), 2026))
        self.assertTrue(runner.simulation_completed(dict(status='paused', time=2027, built=True), 2026))
        self.assertTrue(runner.simulation_completed(dict(status='paused', time=0, built=True), 2026, 2026))
        self.assertFalse(runner.simulation_completed(dict(status='paused', time=0, built=True), 2026, 2025))
        self.assertFalse(runner.simulation_completed(dict(status='paused', time=0, built=False), 2026, 2026))
        for status in (dict(status='offline', time=2027), dict(status='paused', time=float('inf'))):
            with self.assertRaises(AssertionError):
                runner.simulation_completed(status, 2026)

    def test_join_requires_matching_server_category_and_preserves_browser_latency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'trace'
            row = dict(sequence=7, endpoint='charts', total_ms=10, state_ms=2, state_calls=3,
                       java_ms=5, java_calls=1, other_ms=3, dispatch_ms=4, dispatch_calls=3)
            path.write_text(json.dumps(row)+'\n')
            browser = dict(trace_id=7, endpoint='charts', request_ms=13)
            runner.join_timings([browser], path)
            self.assertEqual(browser['request_ms'], 13)
            self.assertEqual(browser['state_ms'], 2)
            self.assertEqual(browser['java_calls'], 1)
            for trace, endpoint in ((8, 'charts'), (7, 'status')):
                with self.assertRaises(AssertionError):
                    runner.join_timings([dict(trace_id=trace, endpoint=endpoint, request_ms=13)], path)

    def test_comparison_checks_settings_sources_output_and_sample_minimum(self):
        def trial(backend, **changes):
            report = dict(pair=1, parameter_sha256='params', frontend_snapshot_sha256='source',
                          summary_output_sha256='same', build_seconds=100, simulation_seconds=200)
            report.update(changes)
            return SimpleNamespace(backend=backend, report=report, cycles=[],
                rows=[dict(endpoint='charts', phase='completed-charts', request_ms=10, http_status=200)])
        result = runner.combined([trial('redis'), trial('postgres', simulation_seconds=210)], 1)
        self.assertEqual(result['paired_durations'][0]['simulation_postgres_over_redis'], 1.05)
        for change in ('parameter_sha256', 'frontend_snapshot_sha256', 'summary_output_sha256'):
            with self.assertRaises(AssertionError):
                runner.combined([trial('redis'), trial('postgres', **{change: 'different'})], 1)
        with self.assertRaises(AssertionError):
            runner.combined([trial('redis'), trial('postgres')], 2)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def run_simulation(self, responses, *, seconds_per_request=.5, timeout=60):
        request = AsyncMock(side_effect=responses)
        clock = SimpleNamespace(monotonic=lambda: request.await_count*seconds_per_request)
        with patch.object(runner, 'time', clock), patch.object(runner.asyncio, 'sleep', AsyncMock()) as sleep:
            result = await runner.run_to_completion(request, 'fictional', end_year=2026, timeout=timeout)
        return result, request, sleep

    async def test_started_acknowledgement_then_status_polls_complete_the_full_run(self):
        # A cached initial paused response is possible; neither it nor the
        # command acknowledgement establishes completion of the final year.
        final = dict(status='paused', time=0, built=True)
        (status, elapsed, highest_time), request, sleep = await self.run_simulation([
            dict(status='started'), dict(status='paused', time=2019),
            dict(status='running', time=2026), final])
        self.assertEqual(status, final)
        self.assertEqual(elapsed, 2)
        self.assertEqual(highest_time, 2026)
        self.assertEqual(request.await_args_list, [call('POST', '/start-sim/fictional'),
            *[call('GET', '/status/fictional')]*3])
        self.assertEqual(sleep.await_count, 2)

    async def test_stopped_engine_before_final_year_cleanup_is_rejected(self):
        with self.assertRaisesRegex(runner.PerformanceCheckFailed, 'stopped before completing'):
            await self.run_simulation([dict(status='started'), dict(status='paused', time=2026)],
                                      seconds_per_request=6)

    async def test_completion_after_execution_limit_does_not_make_trial_pass(self):
        with self.assertRaisesRegex(runner.PerformanceCheckFailed, 'exceeded its test time limit'):
            await self.run_simulation([dict(status='started'), dict(status='running', time=2026),
                dict(status='paused', time=0, built=True)], seconds_per_request=2, timeout=5)

    async def test_clock_reset_before_observed_final_year_is_not_completion(self):
        with self.assertRaisesRegex(runner.PerformanceCheckFailed, 'stopped before completing'):
            await self.run_simulation([dict(status='started'), dict(status='running', time=2025),
                dict(status='paused', time=0, built=True)], seconds_per_request=4)

    async def test_reset_of_unbuilt_model_is_not_natural_completion(self):
        with self.assertRaisesRegex(runner.PerformanceCheckFailed, 'stopped before completing'):
            await self.run_simulation([dict(status='started'), dict(status='running', time=2026),
                dict(status='paused', time=0, built=False)], seconds_per_request=4)

    async def test_unacknowledged_start_is_rejected_before_any_status_poll(self):
        request = AsyncMock(return_value=dict(status='running'))
        with self.assertRaisesRegex(runner.PerformanceCheckFailed, 'Start was not acknowledged'):
            await runner.run_to_completion(request, 'fictional', end_year=2026, timeout=60)
        request.assert_awaited_once_with('POST', '/start-sim/fictional')


class SummaryEvidenceTests(unittest.TestCase):
    def test_complete_summary_records_years_and_hash_without_data_values(self):
        raw = b'\xef\xbb\xbfrun,time,label\n1,2019.0,"a,b"\n1,2020.0,fictional\n'
        evidence = runner.summary_evidence(raw, start_year=2019, end_year=2020)
        self.assertEqual(evidence['summary_output_years'], [2019, 2020])
        self.assertEqual(evidence['summary_output_rows'], 2)
        self.assertEqual(len(evidence['summary_output_sha256']), 64)
        self.assertNotIn('fictional', json.dumps(evidence))

    def test_partial_duplicate_extra_and_invalid_years_cannot_pass(self):
        for years in ([2019, 2020], [2019, 2021], [2019, 2020, 2020, 2021],
                      [2019, 2020, 2021, 2022], [2019, 2020.5, 2021], [2019, 'nan', 2021],
                      [2019, 'inf', 2021], [2019, 'private-value', 2021]):
            with self.subTest(years=years), self.assertRaises(runner.PerformanceCheckFailed) as failure:
                raw = ('run,time\n'+''.join('1,'+str(year)+'\n' for year in years)).encode()
                runner.summary_evidence(raw, start_year=2019, end_year=2021)
            self.assertNotIn('private-value', str(failure.exception))

    def test_missing_ambiguous_or_malformed_columns_are_rejected(self):
        for raw in (b'', b'run,year\n1,2019\n', b'time,time\n2019,2019\n',
                    b'run,time\n1,2019,extra\n', b'run,time\n1\n',
                    b'run,time\n1,"2019', b'run,time\n1,\xff\n'):
            with self.subTest(raw=raw), self.assertRaises(runner.PerformanceCheckFailed):
                runner.summary_evidence(raw, start_year=2019, end_year=2019)


class ModelLogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.trial = runner.Trial(SimpleNamespace(), 1, 'redis', 'image', 'state', {}, Path(self.directory.name))
        self.trial.owner = 'fictional-owner'
        self.trial.model_container = Mock(labels={'jasmine.model_id': self.trial.model,
            'jasmine.session_id': 'fictional-session', 'jasmine.deployment_id': self.trial.owner})
        self.trial.model_container.logs.return_value = b'Fictional private model log\n'
        self.trial.client = Mock()

    def finish_failed_trial(self, cleanup):
        error = runner.PerformanceCheckFailed('Original execution failure')
        with patch.object(runner.common, 'cleanup_models', side_effect=cleanup) as remove, \
             patch.object(runner.common, 'host_resources', return_value={}):
            self.trial.__exit__(type(error), error, None)
        remove.assert_called_once_with(self.trial.client, self.trial.model, self.trial.owner)
        self.assertEqual(self.trial.report['status'], 'failed')
        self.assertEqual(self.trial.report['error_detail'], 'Original execution failure')

    def test_failed_trial_saves_private_log_before_model_removal(self):
        path = self.trial.out/'java.log'
        def cleanup(*args):
            self.assertEqual(path.read_bytes(), b'Fictional private model log\n')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.finish_failed_trial(cleanup)
        self.assertEqual(self.trial.report['cleanup_errors'], [])

    def test_log_read_failure_preserves_original_failure_and_still_cleans_up(self):
        self.trial.model_container.logs.side_effect = RuntimeError('private-error-details')
        self.finish_failed_trial(lambda *args: None)
        self.assertEqual(self.trial.report['cleanup_errors'], ['model_log_RuntimeError'])
        self.assertNotIn('private-error-details', json.dumps(self.trial.report))

    def test_log_of_other_deployment_is_never_read(self):
        self.trial.model_container.labels['jasmine.deployment_id'] = 'other-owner'
        self.finish_failed_trial(lambda *args: None)
        self.trial.model_container.logs.assert_not_called()
        self.assertFalse((self.trial.out/'java.log').exists())


class TimingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'timings.jsonl'
        class Postgres:
            @contextmanager
            def connection(self):
                yield 'connection'
        class Redis:
            def execute_command(self, command):
                if command == 'fail':
                    raise ValueError('postgresql://private-password')
                return 42
        class Pipeline:
            def immediate_execute_command(self, command):
                return Redis().execute_command(command)
            def execute(self):
                return [Redis().execute_command('queued')]
        async def state_call(function, *args, **kwargs):
            return await asyncio.to_thread(function, *args, **kwargs)
        async def java_call(value):
            Redis().execute_command('private-key')
            await asyncio.sleep(.002)
            return value
        self.site = SimpleNamespace(_state_call=state_call, send_java_request=java_call)
        timing.install(self.site, Postgres, Redis, Pipeline)
        self.postgres, self.redis, self.pipeline = Postgres(), Redis(), Pipeline()

    async def exercise(self, application, path='/charts/private-owner'):
        wrapped = timing.TimingMiddleware(application, self.path)
        self.addCleanup(wrapped.stream.close)
        sent = []
        async def send(message):
            sent.append(message)
        await wrapped(dict(type='http', path=path, query_string=b'secret'), Mock(), send)
        return sent, [json.loads(line) for line in self.path.read_text().splitlines()]

    async def test_state_thread_and_java_child_task_are_attributed_to_the_request(self):
        async def app(scope, receive, send):
            def state():
                with self.postgres.connection() as connection:
                    self.assertEqual(connection, 'connection')
            await self.site._state_call(state)
            result = await asyncio.create_task(self.site.send_java_request('unchanged-result'))
            self.assertEqual(result, 'unchanged-result')
            await send(dict(type='http.response.start', status=200, headers=[]))
            await send(dict(type='http.response.body', body=b'private-response'))
        sent, rows = await self.exercise(app)
        self.assertEqual(rows[0]['state_calls'], 2)
        self.assertEqual(rows[0]['java_calls'], 1)
        self.assertEqual(rows[0]['dispatch_calls'], 1)
        self.assertGreater(rows[0]['java_ms'], 0)
        self.assertEqual(sent[-1]['body'], b'private-response')
        self.assertIn((b'x-simpaths-performance-id', b'1'), sent[0]['headers'])
        self.assertIsNone(timing._request.get())
        trace = self.path.read_text()
        for private in ('private-owner', 'secret', 'private-response', 'private-key'):
            self.assertNotIn(private, trace)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    async def test_pipeline_operations_are_measured_without_double_counting_nested_commands(self):
        async def app(scope, receive, send):
            self.assertEqual(self.pipeline.immediate_execute_command('watched'), 42)
            self.assertEqual(self.pipeline.execute(), [42])
            await send(dict(type='http.response.start', status=200, headers=[]))
        _, rows = await self.exercise(app)
        self.assertEqual(rows[0]['state_calls'], 2)
        self.assertEqual(rows[0]['java_calls'], 0)

    async def test_failure_preserves_exception_and_never_logs_private_exception_text(self):
        async def app(scope, receive, send):
            self.redis.execute_command('fail')
        with self.assertRaisesRegex(ValueError, 'private-password'):
            await self.exercise(app)
        row = json.loads(self.path.read_text())
        self.assertEqual(row['failure'], 'ValueError')
        self.assertEqual(row['state_calls'], 1)
        self.assertNotIn('private-password', self.path.read_text())
        self.assertIsNone(timing._request.get())

    async def test_concurrent_requests_have_independent_timing_records(self):
        async def app(scope, receive, send):
            if scope['path'].startswith('/charts'):
                await self.site.send_java_request(None)
            self.redis.execute_command('common')
            await send(dict(type='http.response.start', status=200, headers=[]))
        wrapped = timing.TimingMiddleware(app, self.path)
        self.addCleanup(wrapped.stream.close)
        async def send(message):
            pass
        await asyncio.gather(*(wrapped(dict(type='http', path='/'+name+'/owner'), Mock(), send)
                               for name in ('charts', 'status')))
        rows = {row['endpoint']: row for row in map(json.loads, self.path.read_text().splitlines())}
        self.assertEqual(rows['charts']['java_calls'], 1)
        self.assertEqual(rows['status']['java_calls'], 0)
        self.assertEqual(rows['charts']['state_calls'], 2)
        self.assertEqual(rows['status']['state_calls'], 1)

    async def test_non_poll_routes_and_lifespan_are_passed_through(self):
        async def app(scope, receive, send):
            self.redis.execute_command('private')
            await send(dict(type='http.response.start', status=200, headers=[]))
        _, rows = await self.exercise(app, '/java/private-owner/input/download')
        self.assertEqual(rows, [])

    async def test_closed_request_cannot_gain_background_measurements(self):
        future = asyncio.Future()
        task = None
        async def app(scope, receive, send):
            nonlocal task
            async def late():
                await future
                self.redis.execute_command('late')
            task = asyncio.create_task(late())
            await send(dict(type='http.response.start', status=200, headers=[]))
        await self.exercise(app)
        before = self.path.read_text()
        future.set_result(None)
        await task
        self.assertEqual(self.path.read_text(), before)


class RealRouteInstrumentationTests(unittest.IsolatedAsyncioTestCase):
    async def check_routes(self, kind):
        # Exercise the actual frontend/proxy with its existing fictional state
        # fixture. This validates wiring, not PostgreSQL or model performance.
        frontend = runner.workflow.frontend_path()
        path_patch = patch.object(sys, 'path', [str(frontend), str(frontend/'tests'), *sys.path])
        path_patch.start()
        self.addCleanup(path_patch.stop)
        from test_vm_state_proof import HTTPFixture, PostgresStub
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            # FastHTML creates its session key in the working directory. Keep
            # that generated key and all timing evidence in disposable storage.
            (Path(directory)/'static').symlink_to(frontend/'static', target_is_directory=True)
            fixture = HTTPFixture(kind, store=PostgresStub())
            self.assertTrue((Path(directory)/'.sesskey').is_file())
            class UnusedPostgres:
                @contextmanager
                def connection(self):
                    yield
            class UnusedRedis:
                def execute_command(self):
                    pass
            timing.install(fixture.web, UnusedPostgres, UnusedRedis)
            with tempfile.TemporaryDirectory() as directory:
                middleware = timing.TimingMiddleware(fixture.web.app, Path(directory)/'timings')
                original = fixture.web.app
                fixture.web.app = middleware
                try:
                    async with fixture.browser(fixture.sids[0]) as browser:
                        paused = await browser.post('/pause/'+fixture.sids[0])
                        self.assertEqual(paused.json()['status'], 'paused')
                        started = await browser.post('/start-sim/'+fixture.sids[0])
                        self.assertEqual(started.status_code, 200)
                        self.assertEqual(started.json()['status'], 'started')
                        status = await browser.get('/status/'+fixture.sids[0])
                        self.assertEqual(status.json()['status'], 'running')
                        response = await browser.get('/charts/'+fixture.sids[0])
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()['charts'], [])
                        self.assertIn('x-simpaths-performance-id', response.headers)
                        for credential in fixture.secrets:
                            self.assertNotIn(credential, response.text)
                    rows = list(map(json.loads, (Path(directory)/'timings').read_text().splitlines()))
                    self.assertEqual([row['endpoint'] for row in rows], ['status', 'charts'])
                    for row in rows:
                        self.assertEqual(row['java_calls'], 1)
                        self.assertGreater(row['java_ms'], 0)
                        self.assertGreater(row['dispatch_calls'], 0)
                    self.assertIsNone(timing._request.get())
                finally:
                    fixture.web.app = original
                    middleware.stream.close()
                    await fixture.close()

    async def test_postgres_real_routes_keep_thread_context_with_instrumentation(self):
        await self.check_routes('postgres')


@unittest.skipUnless(shutil.which('node'), 'Node required for the chart-loop promise regression')
class DrawingLoopTests(unittest.TestCase):
    def test_viewing_loop_awaits_drawing_and_restores_plotly_methods(self):
        # Run the actual browser-loop source against delayed fictional Plotly
        # promises; no browser or model dependencies are needed for this check.
        source = Path(runner.__file__).read_text()
        blocks = [node.value for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.Constant) and isinstance(node.value, str)
                  and 'window.performanceChartLoop = {' in node.value]
        self.assertEqual(len(blocks), 1)
        script = """
            const vm = require('node:vm');
            const {performance} = require('node:perf_hooks');
            const assert = require('node:assert/strict');
            let drawings = 0;
            const original = () => new Promise(resolve => setTimeout(() => {drawings++; resolve();}, 5));
            const window = {Plotly: {react: original, extendTraces: original}};
            const context = {window, performance, setTimeout, CONFIG: {CHART_POLL_INTERVAL: 1},
                updateChartsOnce: async () => {window.Plotly.react(); window.Plotly.extendTraces();}};
            const block = BLOCK;
            vm.runInNewContext('('+block+')()', context);
            (async () => {
                const loop = window.performanceChartLoop;
                while (loop.updates < 2) await new Promise(resolve => setTimeout(resolve, 1));
                loop.stop = true;
                while (!loop.finished) await new Promise(resolve => setTimeout(resolve, 1));
                assert.equal(loop.error, false);
                assert.equal(drawings, loop.updates*2);
                assert(loop.cycles.every(row => row.request_ms >= 4));
                assert.equal(window.Plotly.react, original);
                assert.equal(window.Plotly.extendTraces, original);
            })().catch(error => {console.error(error); process.exitCode = 1;});
        """.replace('BLOCK', json.dumps(blocks[0]))
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
