#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Qualify measured workload admission and recovery with fictional PostgreSQL data.
# @author ross richardson
import argparse
import json
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Use scripts/test_batch_queue.py with disposable PostgreSQL')
    if args.output.exists():parser.error('Choose new evidence output')
    args.output.mkdir(mode=0o700,parents=True)
    from deploy._workflow import frontend_path
    sys.path[:0]=[str(frontend_path()),str(frontend_path()/'tests/batch')]
    names=['deploy.multirun.test_workload_runtime.WorkloadRuntimeTests',
        'deploy.multirun.test_release_runtime.ReleaseRuntimeTests',
        'deploy.multirun.test_backup.BackupRestoreTests',
        'test_experiment_extensions.ExperimentExtensionTests',
        'tests.test_batch_release_browser.BatchReleaseBrowserTests']
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    passed=result.wasSuccessful() and not result.skipped
    from deploy.multirun.artifacts import fingerprint,write_attribution
    write_attribution(args.output)
    sources={str(path.relative_to(ROOT)):fingerprint(path) for path in (ROOT/'deploy/multirun').glob('*.py')}
    sources['deploy/multirun/public_training_sources.json']=fingerprint(ROOT/'deploy/multirun/public_training_sources.json')
    sources['JAS-mine-web/jasmine_web/batch/browser_static/batch.js']=fingerprint(frontend_path()/'jasmine_web/batch/browser_static/batch.js')
    (args.output/'report.json').write_text(json.dumps(dict(passed=passed,tests=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        scientific_simulations=False,real_emails=False,sources=sources,
        problems=[dict(test=str(test),traceback=trace) for test,trace in result.failures+result.errors]),indent=2)+'\n')
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
