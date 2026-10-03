#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Disposable native dump/restore acceptance with private fictional files only.
# @author ross richardson
import argparse
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Use scripts/test_batch_queue.py with disposable PostgreSQL')
    if args.output.exists():parser.error('Choose a new evidence directory')
    args.output.mkdir(mode=0o700,parents=True)
    from deploy.multirun.artifacts import write_attribution
    write_attribution(args.output)
    from deploy._workflow import frontend_path
    sys.path.insert(0,str(frontend_path()/'tests'))
    names=['deploy.multirun.test_backup','deploy.multirun.test_backup_live','deploy.multirun.test_backup_single',
        'deploy.multirun.test_backup_transport','deploy.multirun.test_backup_schedule',
        'deploy.multirun.test_releases','deploy.multirun.test_vm_runtime','test_backup_guard',
        'test_vm_state','test_shared_vm_pool']
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    passed=result.wasSuccessful() and not result.skipped
    (args.output/'report.json').write_text(json.dumps(dict(passed=passed,tests=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        problems=[dict(test=str(test),traceback=trace) for test,trace in result.failures+result.errors]),indent=2)+'\n')
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
