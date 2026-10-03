#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Run the release-transition proof only with an explicitly disposable PostgreSQL.
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
        parser.error('Use JAS-mine-web/scripts/test_batch_queue.py with disposable PostgreSQL')
    if args.output.exists(): parser.error('Choose new proof evidence output')
    args.output.mkdir(mode=0o700,parents=True)
    names=['test_releases','test_submission_adapter','test_browser_model','test_prepared_dataset',
           'test_vm_config','test_release_runtime','test_vm_runtime']
    suite=unittest.defaultTestLoader.loadTestsFromNames(['deploy.multirun.'+name for name in names])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    passed=result.wasSuccessful() and not result.skipped
    from deploy.multirun.artifacts import write_attribution
    write_attribution(args.output)
    (args.output/'report.json').write_text(json.dumps(dict(passed=passed,tests=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        problems=[dict(test=str(test),traceback=trace) for test,trace in
                  result.failures+result.errors]),indent=2)+'\n')
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
