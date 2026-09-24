"""(C) Copyright 2026, by Ross Richardson

Verify cancellation evidence handles transient busy responses without hiding failures.

@author ross richardson
"""
import unittest
from unittest.mock import Mock
import runpy
from pathlib import Path
load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
subject = load_tool('acceptance/run_training_browser_acceptance.py')
read_startup_review = subject.read_startup_review

class ReviewTests(unittest.TestCase):
    def test_busy_then_success_returns_only_review(self):
        page = Mock()
        page.evaluate.side_effect = [
            {'status': 409, 'body': {'error': 'Model is busy'}},
            {'status': 200, 'body': {'review': {'topDigest': 'unchanged'}, 'token': 'new'}},
        ]
        self.assertEqual(read_startup_review(page), {'topDigest': 'unchanged'})
        page.wait_for_timeout.assert_called_once_with(500)

    def test_other_errors_and_missing_review_fail_without_retry(self):
        for result in [
            {'status': 409, 'body': {'error': 'Review expired'}},
            {'status': 503, 'body': {'error': 'Unavailable'}},
            {'status': 200, 'body': {}},
            {'status': 200, 'body': {'error': 'Unavailable'}},
            {'status': 200, 'body': None},
        ]:
            with self.subTest(result=result):
                page = Mock()
                page.evaluate.return_value = result
                with self.assertRaisesRegex(RuntimeError, 'Startup review request failed: HTTP'):
                    read_startup_review(page)
                page.wait_for_timeout.assert_not_called()

    def test_persistent_busy_has_bounded_retries(self):
        page = Mock()
        page.evaluate.return_value = {'status': 409, 'body': {'error': 'Model is busy'}}
        with self.assertRaisesRegex(RuntimeError, 'remained busy after 3 attempts'):
            read_startup_review(page, attempts=3)
        self.assertEqual(page.evaluate.call_count, 3)
        self.assertEqual(page.wait_for_timeout.call_count, 2)
