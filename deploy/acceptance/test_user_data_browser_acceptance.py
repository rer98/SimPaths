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


if __name__ == '__main__':
    unittest.main()
