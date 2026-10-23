#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Focused progressive experiment HTTP, retained-data and compiled private RPC proof.
Fictional CSVs and captured mail only; use the disposable PostgreSQL harness.
@author ross richardson
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

MODEL=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(MODEL))
from deploy._workflow import frontend_path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Run this trusted proof through scripts/test_batch_queue.py')
    frontend=frontend_path();sys.path[:0]=[str(frontend),str(frontend/'tests/batch'),str(frontend/'tests/browser')]
    args.output.mkdir(parents=True,exist_ok=False)
    report=dict(passed=False,fictional=True)
    try:
        suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in (
            'test_visualiser_automatic.AutomaticVisualiserTests.test_experiment_http_opens_early_reuses_pairs_and_rejects_changed_or_foreign_selections',
            'test_visualiser_independent_retention','test_visualiser_experiments'))
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        report['tests']=result.testsRun
        if not result.wasSuccessful():return 1
        spec=importlib.util.spec_from_file_location('experiment_browser_proof',frontend/'tests/browser/batch_visualiser.py')
        browser=importlib.util.module_from_spec(spec);spec.loader.exec_module(browser)
        browser.execute(args.output/'browser',private_rpc=True)
        report['passed']=True;return 0
    finally:
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':raise SystemExit(main())
