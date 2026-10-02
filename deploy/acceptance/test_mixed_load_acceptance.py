"""(C) Copyright 2026, by Ross Richardson
Check mixed-load admission evidence, timing failures and conservative cleanup.
Fictional resources only; the acceptance runner performs real model/browser checks.
@author ross richardson
"""
from contextlib import contextmanager, nullcontext
import asyncio
import json
from pathlib import Path
import runpy
import subprocess
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_tool = runpy.run_path(str(ROOT/'deploy/_tool_loader.py'))['load_tool']
runner = load_tool('acceptance/run_mixed_load_acceptance.py')
SINGLE = dict(cpu_millis=2000, memory_mib=4096, storage_mib=10240)
BATCH = dict(cpu_millis=2000, memory_mib=5120, storage_mib=10240)


class EvidenceTests(unittest.TestCase):
    def test_admission_preserves_both_reservations_and_releases_only_the_departing_session(self):
        both = dict(used={key: SINGLE[key]+BATCH[key] for key in SINGLE}, processors=[],
                    interactive=[dict(session_id='single')], batch=[dict(attempt_id='attempt')])
        runner.require_allocation(both, SINGLE, BATCH, sid='single', attempt_id='attempt')
        after_leave = dict(used=BATCH, processors=[], interactive=[], batch=[dict(attempt_id='attempt')])
        runner.require_allocation(after_leave, SINGLE, BATCH, attempt_id='attempt')
        for bad in (dict(both, batch=[]), dict(both, interactive=[]),
                    dict(both, batch=[dict(attempt_id='replacement')]),
                    dict(both, processors=[SINGLE]), dict(both, used=BATCH)):
            with self.assertRaises(AssertionError):
                runner.require_allocation(bad, SINGLE, BATCH, sid='single', attempt_id='attempt')

    def test_ledger_checks_all_three_resources_including_processing(self):
        c = Mock()
        c.execute.return_value.fetchone.return_value = dict(cpu_millis=4000, memory_mib=9216, storage_mib=20480)
        c.execute.return_value.fetchall.side_effect = [
            [dict(session_id='single', **SINGLE)], [dict(attempt_id='attempt', **BATCH)], []]
        queue = SimpleNamespace(_connection=lambda: nullcontext(c), pool_id='proof')
        self.assertEqual(runner.ledger(queue)['used'], dict(cpu_millis=4000, memory_mib=9216, storage_mib=20480))
        for key in SINGLE:
            capacity = dict(cpu_millis=4000, memory_mib=9216, storage_mib=20480)
            capacity[key] -= 1
            c.execute.return_value.fetchone.return_value = capacity
            c.execute.return_value.fetchall.side_effect = [[SINGLE], [BATCH], []]
            with self.assertRaises(AssertionError):
                runner.ledger(queue)
        c.execute.return_value.fetchone.return_value = dict(cpu_millis=4000, memory_mib=9216, storage_mib=20480)
        c.execute.return_value.fetchall.side_effect = [[SINGLE], [BATCH], [SINGLE]]
        with self.assertRaises(AssertionError):
            runner.ledger(queue)

    def test_mixed_timings_do_not_hide_failed_requests_or_merge_the_baseline(self):
        rows = [
            dict(phase='running-alone', endpoint='charts', request_ms=10, http_status=200),
            dict(phase='mixed-running', endpoint='charts', request_ms=45, http_status=200),
            dict(phase='mixed-running', endpoint='charts', request_ms=None, failure='network_failure'),
            dict(phase='pausing', endpoint='status', request_ms=10000, body_error=True),
            dict(phase='closing', endpoint='charts', request_ms=None, failure='intentional_navigation')]
        result = runner.timing_summary(rows)
        self.assertEqual(result['running-alone']['charts']['mean_ms'], 10)
        self.assertEqual(result['mixed-running']['charts']['mean_ms'], 45)
        self.assertEqual(result['mixed-running']['charts']['requests'], 2)
        self.assertEqual(result['mixed-running']['charts']['failures'], 1)
        controls = runner.timing_summary(rows, phases=('pausing', 'closing'))
        self.assertEqual(controls['pausing']['status']['failures'], 1)
        self.assertEqual(controls['closing']['charts']['failures'], 1)

    def test_real_cpu_progress_requires_both_models_in_the_same_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'resources.jsonl'
            def sample(a, b, phase='mixed-running'):
                return dict(phase=phase, containers=[dict(id='a', cpu_total_ns=a), dict(id='b', cpu_total_ns=b)])
            def write(rows):
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            write([sample(0, 0), sample(2_000_000_000, 3_000_000_000)])
            value = runner.cpu_overlap(path, ('a', 'b'))
            self.assertEqual(value['cpu_seconds_in_interval'], dict(a=2, b=3))
            for rows in ([sample(0, 0), sample(2_000_000_000, 0)],
                         [sample(0, 0, 'running-alone'), sample(2_000_000_000, 3_000_000_000)],
                         [dict(phase='mixed-running', containers=[dict(id='a', cpu_total_ns=2_000_000_000)])]):
                write(rows)
                with self.assertRaises(AssertionError):
                    runner.cpu_overlap(path, ('a', 'b'))

    def test_sampler_inventory_selects_only_own_single_and_pinned_batch_labels(self):
        client = Mock()
        a, b = SimpleNamespace(id='a'), SimpleNamespace(id='b')
        client.containers.list.side_effect = [[a], [a], [b, a]]
        inventory = runner.MixedInventory(client)
        filters = dict(filters={'label': 'jasmine.model_id=proof'}, sparse=True)
        self.assertEqual(inventory.list(**filters), [a])
        inventory.batch_identity = 'f'*64
        self.assertEqual(inventory.list(**filters), [a, b])
        self.assertEqual(client.containers.list.call_args.kwargs,
                         dict(filters={'label': 'jasmine.batch.identity='+'f'*64}, sparse=True))

    def test_public_50k_configuration_has_one_full_length_repetition_and_frozen_seed(self):
        config = runner.mixed_configuration(dict(revision='public-revision', identity=dict(population=50000)))
        self.assertEqual(config['common'], dict(country='UK', start_year=2019, end_year=2026, population=50000))
        from deploy.multirun.configuration import normalise
        frozen = normalise(config).as_dict()
        self.assertEqual(frozen['seed_plan']['seeds'], ['606'])
        self.assertEqual(len(frozen['run_sets']), 1)
        with self.assertRaises(AssertionError):
            runner.mixed_configuration(dict(revision='public-revision', identity=dict(population=20000)))

    def test_memory_root_scratch_and_evidence_headroom_are_checked_separately(self):
        host = dict(available_memory_bytes=11*runner.GIB, root_free_bytes=8*runner.GIB)
        runner.resource_check(host, 6*runner.GIB, runner.GIB//4)
        for key in host:
            with self.assertRaises(AssertionError):
                runner.resource_check(dict(host, **{key: host[key]-1}), 6*runner.GIB, runner.GIB//4)
        for scratch, output in ((6*runner.GIB-1, runner.GIB//4), (6*runner.GIB, runner.GIB//4-1)):
            with self.assertRaises(AssertionError):
                runner.resource_check(host, scratch, output)

    def test_busy_preflight_refuses_to_start_or_remove_any_model(self):
        client = Mock()
        client.containers.list.side_effect = [[], []]
        runner.require_idle(client)
        for inventory in ([['existing-session']], [[], ['existing-batch']]):
            client.containers.list.side_effect = inventory
            with self.assertRaises(AssertionError):
                runner.require_idle(client)
        client.containers.run.assert_not_called()
        client.containers.remove.assert_not_called()

    def test_recovery_rejects_replaced_duplicate_and_stopped_containers(self):
        client = Mock()
        original, replacement = Mock(), Mock()
        original.id, original.status = 'original', 'running'
        replacement.id = 'replacement'
        client.containers.list.return_value = [original]
        runner.require_original_container(client, 'jasmine.batch.identity=proof', 'original')
        original.reload.assert_called_once()
        for inventory in ([], [replacement], [original, replacement]):
            client.containers.list.return_value = inventory
            with self.assertRaises(AssertionError):
                runner.require_original_container(client, 'jasmine.batch.identity=proof', 'original')
        client.containers.list.return_value = [original]
        original.status = 'exited'
        with self.assertRaises(AssertionError):
            runner.require_original_container(client, 'jasmine.batch.identity=proof', 'original')


class CleanupTests(unittest.TestCase):
    def make_trial(self, directory):
        out = Path(directory)/'evidence'
        out.mkdir()
        trial = runner.Trial(SimpleNamespace(), out, {}, lambda: None)
        trial.client = Mock()
        trial.postgres = Mock(labels={'simpaths.acceptance': trial.model})
        trial.scratch = Path(directory)/'private-work'
        trial.scratch.mkdir()
        trial.dsn_file = trial.scratch/'private.dsn'
        trial.dsn_file.write_text('fictional secret')
        trial.executor = Mock()
        trial.executor.exclusive.return_value = nullcontext()
        trial.executor.inspect.return_value = dict(state='stopped')
        trial.queue = Mock()
        return trial

    def test_confirmed_removal_erases_only_private_work_and_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = self.make_trial(directory)
            source = Path(directory)/'prepared'
            source.mkdir()
            (source/'source.txt').write_text('retained')
            with patch.object(runner, 'job_leases', return_value=['owned-lease']), \
                 patch.object(runner.common, 'cleanup_models'):
                self.assertEqual(trial.cleanup(), [])
            trial.executor.cleanup.assert_called_once_with('owned-lease')
            trial.executor.stop.assert_not_called()
            trial.postgres.remove.assert_called_once_with(force=True, v=True)
            self.assertFalse(trial.scratch.exists())
            self.assertEqual((source/'source.txt').read_text(), 'retained')

    def test_uncertain_execution_retains_database_credentials_and_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = self.make_trial(directory)
            trial.executor.inspect.return_value = dict(state='running')
            trial.executor.stop.side_effect = RuntimeError('fictional daemon outage')
            with patch.object(runner, 'job_leases', return_value=['owned-lease']), \
                 patch.object(runner.common, 'cleanup_models'):
                self.assertTrue(trial.cleanup())
            trial.executor.cleanup.assert_not_called()
            trial.postgres.remove.assert_not_called()
            self.assertTrue(trial.dsn_file.exists())
            self.assertTrue(trial.report['recovery_files_retained'])

    def test_wrong_database_label_prevents_removal_and_retains_recovery_files(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = self.make_trial(directory)
            trial.postgres.labels = {'simpaths.acceptance': 'somebody-else'}
            with patch.object(runner, 'job_leases', return_value=[]), \
                 patch.object(runner.common, 'cleanup_models'):
                self.assertTrue(trial.cleanup())
            trial.postgres.remove.assert_not_called()
            self.assertTrue(trial.dsn_file.exists())

    def test_sampler_failure_fails_report_but_does_not_keep_large_stopped_workspaces(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = self.make_trial(directory)
            trial.sampler = Mock(errors=1)
            with patch.object(runner, 'job_leases', return_value=[]), \
                 patch.object(runner.common, 'cleanup_models'), \
                 patch.object(runner.common, 'summarize_samples', return_value={}):
                self.assertTrue(trial.cleanup())
            self.assertFalse(trial.scratch.exists())
            self.assertEqual(trial.report['resource_sampling_errors'], 1)

    def test_forced_dispatcher_shutdown_is_an_explicit_failure(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('fictional', 30), 0]
        with self.assertRaises(AssertionError):
            runner.stop_process(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()


class PollingBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def measurements(self):
        value = runner.performance.BrowserMeasurements()
        value.phase = 'mixed-running'
        return value

    def request(self, measurements, endpoint='status', elapsed=12):
        request = Mock(url='http://localhost/'+endpoint+'/fictional', timing={'responseEnd': elapsed})
        measurements.request(request)
        return request

    def response(self, measurements, request, payload, *, status=200):
        response = Mock(request=request, status=status, headers={})
        response.json = AsyncMock(return_value=payload)
        measurements.response(response)
        return response

    async def test_running_request_finishes_before_pause_failures_are_recorded_separately(self):
        measurements = self.measurements()
        request = self.request(measurements)
        self.response(measurements, request, {'status': 'running'})
        finish = asyncio.create_task(runner.finish_polling_phase(measurements, 'mixed-running'))
        await asyncio.sleep(0)
        self.assertFalse(finish.done())
        self.assertEqual(measurements.phase, 'measurement-drain')
        measurements.finished(request)
        await finish
        measurements.phase = 'pausing'
        control_request = self.request(measurements, 'charts')
        measurements.failed(control_request)
        self.assertEqual(measurements.rows[0]['phase'], 'mixed-running')
        self.assertEqual(measurements.rows[0]['request_ms'], 12)
        self.assertEqual(measurements.rows[1]['phase'], 'pausing')
        self.assertEqual(measurements.rows[1]['failure'], 'network_failure')
        summary = runner.timing_summary(measurements.rows, phases=('mixed-running', 'pausing'))
        self.assertEqual(summary['mixed-running']['status']['failures'], 0)
        self.assertEqual(summary['pausing']['charts']['failures'], 1)

    async def test_finished_request_body_is_checked_before_the_running_window_passes(self):
        measurements = self.measurements()
        request = self.request(measurements)
        body = asyncio.get_running_loop().create_future()
        async def read_body():
            return await body
        response = self.response(measurements, request, None)
        response.json.side_effect = read_body
        measurements.finished(request)
        finish = asyncio.create_task(runner.finish_polling_phase(measurements, 'mixed-running'))
        await asyncio.sleep(0)
        self.assertFalse(finish.done())
        body.set_result({'error': 'fictional upstream timeout'})
        with self.assertRaisesRegex(AssertionError, 'Polling failed during mixed-running'):
            await finish
        self.assertTrue(measurements.rows[0]['body_error'])
        self.assertEqual(measurements.rows[0]['phase'], 'mixed-running')

    async def test_running_network_http_and_timing_failures_cannot_be_hidden_by_phase_change(self):
        for failure in ('network', 'http', 'timing'):
            with self.subTest(failure=failure):
                measurements = self.measurements()
                request = self.request(measurements, elapsed=float('nan') if failure == 'timing' else 12)
                if failure == 'network':
                    measurements.failed(request)
                else:
                    self.response(measurements, request, {'status': 'running'}, status=503 if failure == 'http' else 200)
                    measurements.finished(request)
                with self.assertRaisesRegex(AssertionError, 'Polling failed during mixed-running'):
                    await runner.finish_polling_phase(measurements, 'mixed-running')
                self.assertEqual(measurements.rows[0]['phase'], 'mixed-running')
                self.assertEqual(runner.timing_summary(measurements.rows)['mixed-running']['status']['failures'], 1)

    async def test_outstanding_request_blocks_the_pause_and_is_not_relabelled(self):
        measurements = self.measurements()
        request = self.request(measurements)
        with self.assertRaisesRegex(AssertionError, 'Outstanding polling did not finish during mixed-running'):
            await runner.finish_polling_phase(measurements, 'mixed-running', timeout=0)
        self.assertEqual(measurements.pending[request]['phase'], 'mixed-running')
        self.assertIsNone(measurements.rows[0]['request_ms'])


class WorkerLifecycleTests(unittest.TestCase):
    def run_worker(self, directory, *, fail=False):
        path = Path(directory)/'worker.json'
        dsn = Path(directory)/'private.dsn'
        dsn.write_text('fictional-private-connection')
        config = dict(frontend=directory, dsn_file=str(dsn), pool_id='proof', schema='proof_batch',
            root=directory, prepared=directory, image='sha256:'+'f'*64, status=str(Path(directory)/'status.json'))
        path.write_text(json.dumps(config))
        executor, worker, publish = Mock(), Mock(), Mock()
        worker.worker_id = 'worker-generation'
        worker.leases = {'a': SimpleNamespace(attempt_id='original-attempt', generation=2)}
        events, handlers = [], {}

        @contextmanager
        def guard():
            events.append('locked')
            try:
                yield
            finally:
                events.append('released')

        worker.open.return_value = guard()
        if fail:
            worker.tick.side_effect = ValueError('fictional processing error')
        else:
            worker.tick.side_effect = lambda: handlers[signal.SIGTERM](None, None)
        modules = {
            'deploy.multirun.container_adapter': SimpleNamespace(SimPathsContainerAdapter=Mock()),
            'jasmine_web.batch.docker_executor': SimpleNamespace(DockerExecutor=Mock(return_value=executor)),
            'jasmine_web.batch.local_executor': SimpleNamespace(atomic_json=publish),
            'jasmine_web.batch.store': SimpleNamespace(Queue=Mock()),
            'jasmine_web.batch.worker': SimpleNamespace(Worker=Mock(return_value=worker))}
        with patch.dict(sys.modules, modules), patch.object(sys, 'path', list(sys.path)), \
             patch.object(runner.signal, 'signal', side_effect=lambda sig, handler: handlers.update({sig: handler})), \
             patch.object(runner.time, 'sleep'):
            if fail:
                with self.assertRaises(ValueError):
                    runner.worker_main(path)
            else:
                self.assertEqual(runner.worker_main(path), 0)
        self.assertEqual(events, ['locked', 'released'])
        worker.tick.assert_called_once()
        executor.stop.assert_not_called()
        executor.cleanup.assert_not_called()
        if not fail:
            self.assertEqual(publish.call_args.args[1], dict(worker='worker-generation',
                attempts=[dict(id='original-attempt', generation=2)]))
            self.assertNotIn('fictional-private-connection', json.dumps(publish.call_args.args[1]))

    def test_dispatcher_exit_releases_its_lock_without_stopping_the_model_or_releasing_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_worker(directory)

    def test_dispatcher_error_releases_its_lock_without_unconfirmed_model_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_worker(directory, fail=True)


if __name__ == '__main__':
    unittest.main()
