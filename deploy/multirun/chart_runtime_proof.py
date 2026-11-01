#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Prove finite chart deadlines, cache reuse and prompt authority/shutdown cleanup.
Fictional files and a module-local elapsed clock avoid long simulations or waits.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import time as real_time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MODEL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODEL))
from deploy._workflow import frontend_path
FRONT = frontend_path()
sys.path[:0] = [str(FRONT), str(FRONT / 'tests'), str(FRONT / 'tests/batch')]
import test_postgres
import test_visualiser as fixture
import jasmine_web.batch.visualiser as visualiser_module
from jasmine_web.batch.visualiser import Visualiser

SOURCES = {
    'SimPaths': ('deploy/multirun/chart_runtime_proof.py', 'deploy/multirun/vm_config.py',
        'deploy/multirun/local_web.py', 'deploy/multirun/runtime.py'),
    'JAS-mine-web': ('jasmine_web/batch/visualiser.py', 'jasmine_web/batch/visualiser_preparation.py',
        'jasmine_web/batch/visualiser_handover.py', 'jasmine_web/batch/visualiser_experiments.py',
        'jasmine_web/batch/visualiser_sources.py', 'jasmine_web/batch/visualiser_storage.py',
        'jasmine_web/batch/visualiser_summary_retention.py', 'jasmine_web/batch/preparation_commands.py',
        'jasmine_web/batch/processing_scope.py', 'jasmine_web/batch/results.py',
        'jasmine_web/batch/store.py', 'jasmine_web/batch/aggregate_io.py',
        'jasmine_web/batch/aggregate_limits.py', 'tests/batch/test_visualiser.py',
        'tests/batch/test_visualiser_parallel.py', 'tests/batch/test_visualiser_preparation.py',
        'tests/test_batch_preparation_commands.py', 'tests/batch/test_results.py',
        'tests/batch/result_fixture.py', 'tests/batch/test_browser.py',
        'tests/batch/test_submission.py', 'tests/batch/test_postgres.py')}


def fingerprints():
    return {project + '/' + name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for project, root in (('SimPaths', MODEL), ('JAS-mine-web', FRONT))
        for name in SOURCES[project]}


@unittest.skipUnless(test_postgres.DSN, 'Use the disposable PostgreSQL proof runner')
class ChartRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.VisualiserTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.backend = self.f.backend

    def counts(self):
        with self.f.q._connection() as connection:
            connection.execute('SET TRANSACTION READ ONLY')
            attempts = connection.execute('SELECT count(*) AS count FROM attempts WHERE pool_id=%s',
                (self.f.q.pool_id,)).fetchone()['count']
            reservations = connection.execute('SELECT count(*) AS count FROM processing_reservations WHERE pool_id=%s',
                (self.f.q.pool_id,)).fetchone()['count']
        return attempts, reservations

    def install(self, runtime):
        value = Visualiser(self.f.s.results, self.backend, runtime=runtime)
        self.f.visualiser = self.f.s.visualiser = value
        self.assertEqual(value.runtime, runtime)
        return value

    def run_clock(self, runtime, *, work_seconds=4200, source_seconds=0):
        value = self.install(runtime)
        requested = self.f.request()
        key = requested['id']
        record = value._record(key)
        files = sum(len(item['repetitions']) for item in record['selection']['jobs'])
        self.assertGreater(files, 1)
        elapsed = [0.0]
        clock = SimpleNamespace(monotonic=lambda: elapsed[0], time=real_time.time,
                                sleep=real_time.sleep)
        original_sources = self.f.s.results.processing_sources
        @contextmanager
        def sources(selection):
            with original_sources(selection) as selected:
                elapsed[0] += source_seconds
                yield selected
        def calculate(_, command):
            elapsed[0] += work_seconds / files
            # Each fictional seed boundary performs the real worker authority
            # and global-deadline callback, without changing the standard clock.
            command.check(force=True)
        self.backend.hook = calculate
        with (patch.object(visualiser_module, 'time', clock),
              patch.object(self.f.s.results, 'processing_sources', sources)):
            value.tick()
        self.assertIs(visualiser_module.time, real_time)
        self.assertEqual(self.counts()[1], 0)
        return key, value._record(key), elapsed[0]

    def test_same_work_exceeding_one_hour_succeeds_with_three_hour_allowance(self):
        attempts = self.counts()[0]
        old_key, old, old_elapsed = self.run_clock(3600)
        self.assertEqual(old['state'], 'failed')
        self.assertGreater(old_elapsed, 3600)
        new_key, new, new_elapsed = self.run_clock(10800)
        self.assertEqual(new['state'], 'ready')
        self.assertEqual(old_key, new_key)
        self.assertAlmostEqual(new_elapsed, 4200)
        self.assertEqual(self.backend.calls, 2)
        self.assertEqual(self.counts(), (attempts, 0))

    def test_three_hour_allowance_remains_finite_and_releases_processing_capacity(self):
        attempts = self.counts()[0]
        key, record, elapsed = self.run_clock(10800, work_seconds=11000)
        self.assertGreater(elapsed, 10800)
        self.assertEqual(record['state'], 'failed')
        self.assertFalse((self.f.visualiser._path(key) / 'published.json').exists())
        self.assertFalse((self.f.visualiser._path(key) / 'private').exists())
        self.assertEqual(self.counts(), (attempts, 0))

    def test_source_checks_share_the_same_deadline_as_calculations(self):
        attempts = self.counts()[0]
        key, record, elapsed = self.run_clock(3600, work_seconds=3000, source_seconds=1200)
        self.assertEqual(record['state'], 'failed')
        self.assertGreaterEqual(elapsed, 3600)
        new_key, new, elapsed = self.run_clock(10800, work_seconds=3000, source_seconds=1200)
        self.assertEqual(key, new_key)
        self.assertEqual(new['state'], 'ready')
        self.assertAlmostEqual(elapsed, 4200)
        self.assertEqual(self.counts(), (attempts, 0))

    def test_runtime_change_reuses_ready_bytes_identity_deadline_and_attempt_history(self):
        key, ready, _ = self.run_clock(10800)
        self.assertEqual(ready['state'], 'ready')
        path = self.f.visualiser._path(key) / 'published.json'
        publication = path.read_bytes()
        calls = self.backend.calls
        counts = self.counts()
        restored = self.install(3600)
        self.assertEqual(self.f.request()['id'], key)
        restored.tick()
        self.assertEqual(restored._record(key)['state'], 'ready')
        self.assertEqual(restored._record(key)['expires_at'], ready['expires_at'])
        self.assertEqual(path.read_bytes(), publication)
        self.assertEqual(self.backend.calls, calls)
        self.assertEqual(self.counts(), counts)


EXISTING = (
    'test_visualiser.VisualiserTests.test_shutdown_stops_child_and_leaves_work_queued_for_restart',
    'test_visualiser_parallel.ParallelServiceTests.test_revocation_stops_both_children_before_refunding_capacity',
    'test_visualiser_parallel.ParallelServiceTests.test_shutdown_joins_both_children_and_restart_finishes_without_model_work',
    'test_batch_preparation_commands.CommandTests.test_revocation_kills_and_joins_both_live_commands')


def suite():
    return unittest.TestSuite((unittest.defaultTestLoader.loadTestsFromTestCase(ChartRuntimeTests),
        unittest.defaultTestLoader.loadTestsFromNames(EXISTING)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof', action='store_true')
    parser.add_argument('--list-tests', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    selected = suite()
    if args.list_tests:
        print(json.dumps(dict(tests=selected.countTestCases(), focused_runtime_checks=4,
                              existing_containment_checks=len(EXISTING))))
        return 0
    if not args.execute_proof or not test_postgres.DSN or args.output is None:
        parser.error('Use --proof-only with scripts/test_batch_queue.py and this trusted proof')
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.nice(15)
    sources = fingerprints()
    result = unittest.TextTestRunner(verbosity=2).run(selected)
    unchanged = sources == fingerprints()
    report = dict(passed=result.wasSuccessful() and result.testsRun == 8 and not result.skipped and unchanged,
        tests=result.testsRun, focused_runtime_checks=4, existing_containment_checks=len(EXISTING),
        errors=len(result.errors), failures=len(result.failures), skipped=len(result.skipped),
        problems=[dict(test=str(test), traceback=trace) for test, trace in result.errors + result.failures],
        sources=sources, sources_unchanged=unchanged, scientific_simulations=False,
        real_emails=False, module_local_elapsed_clock=True, standard_clock_unchanged=True)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.output / 'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Generated fictional chart runtime, cache reuse and contained-child proof evidence.\n'
        '@author ross richardson\n-->\n')
    print(('PASSED: ' if report['passed'] else 'FAILED: ') + str(args.output / 'report.json'), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
