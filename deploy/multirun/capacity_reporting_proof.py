#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Qualify capacity incidents, captured recovery mail, private administration and restore.
Uses fictional metadata, disposable PostgreSQL and Chromium; no scientific work.
@author ross richardson
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import traceback
import unittest

MODEL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODEL))
from deploy._workflow import frontend_path
FRONT = frontend_path()
sys.path[:0] = [str(FRONT), str(FRONT/'tests'), str(FRONT/'tests/batch')]


def sources():
    paths = [('JAS-mine-web', FRONT, path) for path in (FRONT/'jasmine_web/batch').iterdir()
             if path.suffix in ('.py', '.sql')]
    paths += [('JAS-mine-web', FRONT, path)
              for path in (FRONT/'jasmine_web/batch/browser_static').iterdir() if path.is_file()]
    paths += [('JAS-mine-web', FRONT, path) for path in (FRONT/'tests').rglob('*capacity*')
              if path.is_file() and path.suffix in ('.py', '.mjs')]
    paths += [('JAS-mine-web', FRONT, FRONT/'tests'/name) for name in (
        'test_batch_administration.py', 'batch/test_administration.py',
        'test_batch_service_rpc.py', 'browser/batch_administration.py',
        'test_batch_storage_notifications.py', 'batch/test_storage_notifications.py')]
    paths += [('SimPaths', MODEL, MODEL/'deploy/multirun'/name) for name in (
        'capacity_reporting_proof.py', 'test_compute_capacity.py', 'compute_capacity.py',
        'backup.py', 'backup_live.py', 'backup_transport.py', 'backup_stream.py')]
    return {project+'/'+path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for project, root, path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Use scripts/test_batch_queue.py with its disposable database')
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = sources()
    report = dict(passed=False, sources=before, scientific_simulations=False, real_emails=False)
    try:
        names = ['test_compute_capacity', 'test_batch_compute_capacity', 'test_batch_capacity_reporting_ui',
                 'test_administration', 'test_batch_administration', 'test_batch_service_rpc',
                 'test_storage_notifications', 'test_batch_storage_notifications',
                 'deploy.multirun.test_compute_capacity']
        suite = unittest.defaultTestLoader.loadTestsFromNames(names)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        report.update(tests=result.testsRun, errors=len(result.errors), failures=len(result.failures),
                      skipped=len(result.skipped), problems=[dict(test=str(test), traceback=trace)
                      for test, trace in result.failures+result.errors])
        if not result.wasSuccessful() or result.skipped or not result.testsRun:
            return 1
        spec = importlib.util.spec_from_file_location('capacity_administration_browser',
                                                      FRONT/'tests/browser/batch_administration.py')
        browser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(browser)
        browser.execute(args.output/'browser')
        report['browser'] = json.loads((args.output/'browser/report.json').read_text())
        report['passed'] = (report['browser'].get('passed') is True
                            and report['browser'].get('cleanup') is True
                            and not report['browser'].get('browser_errors'))
    except Exception:
        report['passed'] = False
        report['proof_error'] = traceback.format_exc()
        traceback.print_exc()
    finally:
        report['sources_unchanged'] = before == sources()
        report['passed'] = report['passed'] and report['sources_unchanged']
        (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Generated fictional capacity-incident, captured-mail, browser and recovery evidence.\n'
            '@author ross richardson\n-->\n')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
