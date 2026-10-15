"""(C) Copyright 2026, by Ross Richardson

Check prompt acceptance failures, export readiness and robust evidence cleanup.

@author ross richardson
"""
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from contextlib import nullcontext

import runpy
load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
subject = load_tool('acceptance/run_user_data_browser_acceptance.py')
wait_for_session = subject.wait_for_session
step_until_person_export = subject.step_until_person_export
prepare_and_wait = subject.prepare_and_wait
finish_run = subject.finish_run

class LaunchTests(unittest.TestCase):
    def page_at(self, url):
        page = Mock(url=url)

        def wait(predicate, **kwargs):
            self.assertFalse(predicate('http://localhost:5001/'))
            self.assertTrue(predicate(url))
            self.assertEqual(kwargs['wait_until'], 'domcontentloaded')

        page.wait_for_url.side_effect = wait
        return page

    def test_capacity_and_other_rejections_report_decoded_reason(self):
        for query, message in [
            ('Server+busy%3A+all+simulation+slots+are+occupied.',
             'Server busy: all simulation slots are occupied.'),
            ('Access+denied', 'Access denied'),
            ('', 'Server rejected the launch without an explanation'),
        ]:
            with self.subTest(query=query):
                report = {}
                page = self.page_at('http://localhost:5001/?error=' + query)
                with self.assertRaises(AssertionError) as caught:
                    wait_for_session(page, report)
                self.assertEqual(str(caught.exception), 'Session launch rejected: ' + message)
                self.assertEqual(report['launch_error'], message)
                page.wait_for_url.assert_called_once()

    def test_success_returns_session_id_without_query_string(self):
        page = self.page_at('http://localhost:5001/sim/test-session?view=grid')
        report = {}
        self.assertEqual(wait_for_session(page, report), 'test-session')
        self.assertEqual(report, {})


class ExportStepsTests(unittest.TestCase):
    def page_with_exports(self, sizes):
        page = Mock()
        page.expect_response.side_effect = lambda *a, **kw: nullcontext(Mock(value=Mock(ok=True)))
        page.request.get.side_effect = [Mock(ok=True, json=Mock(return_value={'files': [
            {'timestamp': 'older', 'name': 'Person.csv', 'size': 999},
            {'timestamp': 'recent', 'name': 'Person.csv', 'size': size},
        ]})) for size in sizes]
        return page

    @patch.object(subject, 'expect')
    def test_advances_past_empty_export_ignoring_populated_older_run(self, expect):
        page = self.page_with_exports([0, 0, 120])
        report = {'checks': []}
        step_until_person_export(page, 'http://localhost:5001', 'session', 2, report)
        self.assertEqual(page.locator.return_value.click.call_count, 3)
        self.assertEqual(report['exports'][0]['steps'], 3)
        self.assertEqual(report['exports'][0]['timestamp'], 'recent')

    @patch.object(subject, 'expect')
    def test_empty_export_has_bounded_failure(self, expect):
        page = self.page_with_exports([0, 0])
        with self.assertRaisesRegex(AssertionError, 'remained empty or missing after 2 steps'):
            step_until_person_export(page, 'http://localhost:5001', 'session', 1, {'checks': []}, max_steps=2)
        self.assertEqual(page.locator.return_value.click.call_count, 2)


class PreparationTests(unittest.TestCase):
    def page_with_states(self, states):
        page = Mock()
        page.expect_response.return_value = nullcontext(Mock(value=Mock(ok=True)))
        page.request.get.side_effect = [Mock(ok=True, json=Mock(return_value=state)) for state in states]
        return page

    def test_disk_rejection_stops_without_waiting_for_dialog_timeout(self):
        message = 'Preparation needs space for candidate inputs plus at least 2 GiB working reserve'
        page = self.page_with_states([{'state': 'failed', 'message': message}])
        report = {}
        with self.assertRaisesRegex(AssertionError, '2 GiB working reserve'):
            prepare_and_wait(page, Mock(), 'http://localhost:5001', 'session', report)
        page.wait_for_timeout.assert_not_called()
        self.assertEqual(report['preparation_last_status']['message'], message)

    def test_confirmation_rejection_preserves_http_reason(self):
        page = self.page_with_states([])
        page.expect_response.return_value = nullcontext(Mock(value=Mock(
            ok=False, status=409, text=Mock(return_value='Startup review expired'))))
        with self.assertRaisesRegex(AssertionError, 'HTTP 409: Startup review expired'):
            prepare_and_wait(page, Mock(), 'http://localhost:5001', 'session', {})
        page.request.get.assert_not_called()

    @patch.object(subject, 'expect')
    def test_in_progress_can_reach_ready_and_close_dialog(self, expect):
        page = self.page_with_states([{'state': 'preparing'}, {'state': 'ready'}])
        dialog, report = Mock(), {}
        prepare_and_wait(page, dialog, 'http://localhost:5001', 'session', report)
        self.assertEqual(report['preparation_last_status']['state'], 'ready')
        page.wait_for_timeout.assert_called_once()
        expect.assert_called_once_with(dialog)
        expect.return_value.not_to_be_visible.assert_called_once()


class FinalizationTests(unittest.TestCase):
    def test_crashed_page_keeps_original_failure_and_still_saves_trace_and_leaves(self):
        page, context, browser = Mock(), Mock(), Mock()
        page.screenshot.side_effect = RuntimeError('Target crashed')
        context.request.post.return_value = Mock(ok=True, url='http://localhost:5001/')
        report = {'status': 'failed', 'error': 'Preparation: insufficient storage'}
        with TemporaryDirectory() as directory:
            out = Path(directory)
            finish_run(page, context, browser, 'http://localhost:5001/', 'session', out, report)
            saved = json.loads((out/'report.json').read_text())
        self.assertEqual(saved['error'], 'Preparation: insufficient storage')
        self.assertEqual(saved['artifact_errors']['final.png'], 'Target crashed')
        context.tracing.stop.assert_called_once()
        context.request.post.assert_called_once_with('http://localhost:5001/leave/session', timeout=120000)
        page.goto.assert_not_called()
        browser.close.assert_called_once()

    def test_evidence_and_cleanup_errors_do_not_report_a_pass(self):
        page, context, browser = Mock(), Mock(), Mock()
        context.tracing.stop.side_effect = RuntimeError('Trace failed')
        context.request.post.return_value = Mock(ok=False, status=503)
        report = {'status': 'passed'}
        with TemporaryDirectory() as directory:
            out = Path(directory)
            finish_run(page, context, browser, 'http://localhost:5001', 'session', out, report)
            saved = json.loads((out/'report.json').read_text())
        self.assertEqual(saved['status'], 'failed')
        self.assertIn('trace.zip', saved['artifact_errors'])
        self.assertIn('HTTP 503', saved['cleanup_error'])
        browser.close.assert_called_once()

    def test_cleanup_error_redirect_is_reported_even_with_http_200(self):
        page, context, browser = Mock(), Mock(), Mock()
        context.request.post.return_value = Mock(ok=True,
            url='http://localhost:5001/?error=Session+cleanup+failed')
        report = {'status': 'passed'}
        with TemporaryDirectory() as directory:
            finish_run(page, context, browser, 'http://localhost:5001', 'session', Path(directory), report)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['cleanup_error'], 'Session cleanup rejected: Session cleanup failed')


class MemoryTrialTests(unittest.TestCase):
    model_id = 'simpaths-acceptance-0123456789abcdef-user-data'

    def container(self, *, memory=5*1024**3, cpu=2_000_000_000, session='owned', heap='4g', worker='3072'):
        return Mock(id='container-id', attrs={
            'HostConfig': dict(Memory=memory, MemorySwap=memory, NanoCpus=cpu),
            'Config': dict(Labels={'jasmine.model_id': self.model_id, 'jasmine.session_id': session},
                          Env=['JAVA_OPTS=-Xmx'+heap, 'SIMPATHS_PREPARATION_HEAP_MIB='+worker])})

    def test_only_the_exact_owned_container_can_have_swap_disabled(self):
        client = Mock()
        container = self.container()
        client.containers.list.return_value = [container]
        self.assertIs(subject.configure_trial_container(client, self.model_id, 'owned', 3072), container)
        container.update.assert_called_once_with(memswap_limit=5*1024**3)
        client.containers.list.assert_called_once_with(all=True, filters={'label':'jasmine.model_id='+self.model_id})

    def test_mismatched_session_heap_or_limits_rejected_before_mutation(self):
        for settings in (dict(session='other'), dict(memory=8*1024**3), dict(cpu=1_000_000_000),
                         dict(heap='3g'), dict(worker='4096'), dict(heap='4g -Xmx5g')):
            with self.subTest(settings=settings):
                client = Mock()
                container = self.container(**settings)
                client.containers.list.return_value = [container]
                with self.assertRaises(AssertionError):
                    subject.configure_trial_container(client, self.model_id, 'owned', 3072)
                container.update.assert_not_called()

    def test_nonfixture_identity_and_ambiguous_matches_are_rejected(self):
        client = Mock()
        with self.assertRaises(ValueError):
            subject.configure_trial_container(client, 'production-user-data', 'owned', 3072)
        client.containers.list.assert_not_called()
        client.containers.list.return_value = [self.container(), self.container()]
        with self.assertRaisesRegex(AssertionError, 'exactly one'):
            subject.configure_trial_container(client, self.model_id, 'owned', 3072)

    def test_swap_update_must_take_effect(self):
        client, container = Mock(), self.container()
        container.attrs['HostConfig']['MemorySwap'] = 10*1024**3
        client.containers.list.return_value = [container]
        with self.assertRaisesRegex(AssertionError, 'disabled swap'):
            subject.configure_trial_container(client, self.model_id, 'owned', 3072)

    def test_telemetry_stops_even_when_final_capture_fails_without_hiding_model_error(self):
        sampler = Mock()
        sampler.capture.side_effect = RuntimeError('Docker disconnected')
        sampler.stop.return_value = dict(passed=False, errors=['disconnected'])
        report = dict(status='failed', error='Model failed first')
        subject.stop_trial_sampling(sampler, report)
        sampler.stop.assert_called_once()
        self.assertEqual(report['error'], 'Model failed first')
        self.assertEqual(report['telemetry_error'], 'Docker disconnected')

    def test_invalid_or_missing_memory_samples_cannot_report_success(self):
        sampler = Mock()
        sampler.capture.return_value = None
        sampler.stop.return_value = dict(passed=False)
        report = dict(status='passed')
        subject.stop_trial_sampling(sampler, report)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('telemetry', report['error'])
        sampler.stop.assert_called_once()

    def test_effective_process_heaps_checked_and_command_lines_not_retained(self):
        container = Mock()
        container.top.return_value = dict(Titles=['PID', 'COMMAND'], Processes=[
            ['1', '/opt/java/bin/java -Xmx4g -Dprivate=not-for-report simpaths.experiment.SimPathsUserDataStartup'],
            ['20', '/opt/java/bin/java -Xmx3072m simpaths.experiment.SimPathsUserDataPreparation 2019']])
        rows = subject.java_processes(container, 3072)
        self.assertEqual(rows, [dict(pid=1, role='application', heap_mib=4096),
                                dict(pid=20, role='preparation', heap_mib=3072)])
        self.assertNotIn('not-for-report', json.dumps(rows))
        with self.assertRaisesRegex(AssertionError, 'heap differs'):
            subject.java_processes(container, 4096)

    def test_profile_rejects_nonfixed_seed_and_changed_workloads(self):
        subject.verify_trial_profile(subject.TRIAL_PROFILE)
        for field, value in [('popSize', 20000), ('endYear', 2070), ('randomSeedIfFixed', 607),
                             ('fixRandomSeed', False), ('startYear', 2020)]:
            with self.subTest(field=field), self.assertRaisesRegex(AssertionError, field):
                subject.verify_trial_profile(dict(subject.TRIAL_PROFILE, **{field:value}))

    @patch.object(subject, 'monotonic', side_effect=[100, 103.5])
    def test_failed_stages_retain_elapsed_time_and_original_error(self, clock):
        report = {}
        with self.assertRaisesRegex(RuntimeError, 'original problem'):
            with subject.timed_stage(report, 'preparation-1'):
                raise RuntimeError('original problem')
        stage = report['stages'][0]
        self.assertEqual(stage['seconds'], 3.5)
        self.assertEqual(stage['status'], 'failed')
        self.assertTrue(stage['started'].endswith('+00:00'))
        self.assertTrue(stage['finished'].endswith('+00:00'))

    def simulation_page(self, statuses):
        page = Mock()
        page.request.post.side_effect = [Mock(ok=True, json=Mock(return_value={})),
                                        Mock(ok=True, json=Mock(return_value={'status':'started'}))]
        page.request.get.side_effect = [Mock(ok=True, json=Mock(return_value=status)) for status in statuses]
        return page

    @patch.object(subject, 'monotonic', return_value=100)
    def test_completion_requires_final_cleanup_or_observed_final_year_before_clock_reset(self, clock):
        for statuses in (
            [dict(status='running', time=2026, built=True), dict(status='paused', time=2027, built=True)],
            [dict(status='running', time=2026, built=True), dict(status='paused', time=0, built=True)],
        ):
            page = self.simulation_page(statuses)
            report = {}
            subject.run_to_completion(page, 'http://localhost', 'sid', 1, report)
            self.assertEqual(page.request.get.call_count, 2)
            self.assertEqual(report['full_runs'][0]['highest_running_time'], 2026)

    def test_paused_early_or_unbuilt_clock_reset_cannot_pass(self):
        for status in (dict(status='paused', time=2026, built=True),
                       dict(status='paused', time=0, built=False),
                       dict(status='paused', time=0, built=True)):
            with self.subTest(status=status), patch.object(subject, 'monotonic', side_effect=[0, 11, 11]):
                page = self.simulation_page([status])
                with self.assertRaisesRegex(AssertionError, 'before completing'):
                    subject.run_to_completion(page, 'http://localhost', 'sid', 1, {})

    @patch.object(subject, 'monotonic', return_value=100)
    def test_failed_model_or_http_stops_immediately(self, clock):
        for status in (dict(status='build_error', time=2026), dict(status='offline'),
                       dict(status='running', time=float('nan'))):
            with self.subTest(status=status):
                page = self.simulation_page([status])
                with self.assertRaises(AssertionError):
                    subject.run_to_completion(page, 'http://localhost', 'sid', 1, {})
                page.wait_for_timeout.assert_not_called()
        page = self.simulation_page([])
        page.request.get.side_effect = None
        page.request.get.return_value = Mock(ok=False, status=503, text=Mock(return_value='unavailable'))
        with self.assertRaisesRegex(AssertionError, 'HTTP 503'):
            subject.run_to_completion(page, 'http://localhost', 'sid', 1, {})
        page.wait_for_timeout.assert_not_called()

    @patch.object(subject, 'monotonic', side_effect=[0, 14401])
    def test_full_run_has_four_hour_bound(self, clock):
        page = self.simulation_page([])
        with self.assertRaisesRegex(AssertionError, 'exceeded 14400s'):
            subject.run_to_completion(page, 'http://localhost', 'sid', 1, {})
        page.request.get.assert_not_called()

    def test_annual_output_requires_all_eight_years_without_duplicates(self):
        raw = ('time,value\n'+''.join(f'{year},42\n' for year in range(2019, 2027))).encode()
        self.assertEqual(subject.annual_summary(raw)['years'], list(range(2019, 2027)))
        for wrong in (raw+b'2026,42\n', raw.replace(b'2026,42\n', b''),
                      raw.replace(b'2026,42\n', b'2026,42,extra\n'), raw.replace(b'time', b'year')):
            with self.subTest(raw=wrong), self.assertRaises(AssertionError):
                subject.annual_summary(wrong)

    @patch.object(subject, 'java_processes')
    def test_repeat_preparation_reuses_confirmed_uploads_and_requires_worker_exit(self, process_list):
        app = dict(pid=1, role='application', heap_mib=4096)
        worker = dict(pid=20, role='preparation', heap_mib=3072)
        process_list.side_effect = [[app, worker], [app], [app]]
        selection = dict(source='uploads', year=2019, schedule=[['policy.txt', '2019', '2019', 'fixture']])
        page = Mock()
        page.request.get.side_effect = [Mock(ok=True, json=Mock(return_value=status)) for status in (
            dict(state='ready', locked=False, selection=selection), dict(state='preparing'), dict(state='ready'))]
        page.request.post.side_effect = [Mock(ok=True, json=Mock(return_value={'token':'fresh'})),
                                        Mock(ok=True, json=Mock(return_value={}))]
        report, sampler = {}, Mock()
        subject.trial_preparation(page, None, 'http://localhost', 'sid', report, Mock(), sampler, 3072, 2)
        self.assertEqual(page.request.post.call_args_list[0].kwargs['data'], {'choices':selection})
        self.assertEqual(page.request.post.call_args_list[1].kwargs['data'], {'token':'fresh'})
        self.assertEqual(report['stages'][0]['worker_pids'], [20])
        self.assertTrue(report['stages'][0]['worker_exit_verified'])
        self.assertEqual(sampler.capture.call_count, 2)

    @patch.object(subject, 'prepare_and_wait')
    @patch.object(subject, 'java_processes')
    def test_preparation_cannot_pass_without_observing_worker_or_while_worker_alive(self, process_list, prepare):
        app = dict(pid=1, role='application', heap_mib=4096)
        worker = dict(pid=20, role='preparation', heap_mib=3072)
        for processes in ([app], [app, worker]):
            with self.subTest(processes=processes):
                process_list.return_value = processes
                with self.assertRaisesRegex(AssertionError, 'observe'):
                    subject.trial_preparation(Mock(), Mock(), 'http://localhost', 'sid', {}, Mock(), Mock(), 3072, 1)


if __name__ == '__main__':
    unittest.main()
