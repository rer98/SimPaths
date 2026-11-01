#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Qualify reviewed raw-output removal, retained comparisons and inactive recovery.
Fictional files, captured mail and disposable PostgreSQL; no scientific models.
@author ross richardson
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import unittest

MODEL=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(MODEL))
from deploy._workflow import frontend_path
FRONT=frontend_path()
sys.path[:0]=[str(FRONT),str(FRONT/'tests'),str(FRONT/'tests/batch'),str(FRONT/'tests/browser')]
SOURCES={
 'JAS-mine-web':(
  'jasmine_web/batch/037_raw_only_output_deletion.sql','jasmine_web/batch/store.py',
  'jasmine_web/batch/output_management.py','jasmine_web/batch/results.py','jasmine_web/batch/retention.py',
  'jasmine_web/batch/storage.py','jasmine_web/batch/visualiser_summary_retention.py',
  'jasmine_web/batch/browser.py','jasmine_web/batch/browser_static/batch.js',
  'jasmine_web/batch/remote_service.py','jasmine_web/batch/service_rpc.py',
  'tests/batch/test_postgres.py','tests/batch/test_output_management.py',
  'tests/batch/test_visualiser.py','tests/batch/test_results.py','tests/batch/test_submission.py',
  'tests/batch/test_visualiser_independent_retention.py','tests/batch/test_retention.py',
  'tests/test_batch_service_rpc.py',
  'tests/test_batch_raw_only_outputs.py','tests/test_batch_visualiser_summary_retention.py',
  'tests/js/batch_output_deletion.test.mjs','tests/browser/batch_raw_output_retention.py'),
 'SimPaths':(
  'deploy/multirun/visualiser_backend.py','deploy/multirun/visualiser_summary_cache.py',
  'deploy/multirun/visualiser_summary_holds.py','deploy/multirun/backup_live.py',
  'deploy/multirun/backup.py','deploy/multirun/queue_adapter.py',
  'deploy/multirun/test_visualiser_raw_retirement.py','deploy/multirun/test_visualiser_summary_cache.py',
  'deploy/multirun/test_visualiser_backend.py','deploy/multirun/test_backup_visualiser.py',
  'deploy/multirun/test_backup_live.py','deploy/multirun/raw_output_retention_proof.py')}


def fingerprints():
    return {project+'/'+name:hashlib.sha256((root/name).read_bytes()).hexdigest()
        for project,root in (('JAS-mine-web',FRONT),('SimPaths',MODEL)) for name in SOURCES[project]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    build=Path(os.environ.get('SIMPATHS_VISUALISER_TEST_BUILD',''))
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN') or not (build/'build.json').is_file():
        parser.error('Use the disposable PostgreSQL runner with the qualified Visualiser test build')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    os.nice(15)
    sources=fingerprints()
    names=['test_postgres','test_output_management','test_visualiser_independent_retention','test_retention',
        'test_batch_raw_only_outputs','test_batch_visualiser_summary_retention',
        'deploy.multirun.test_visualiser_raw_retirement',
        'deploy.multirun.test_visualiser_summary_cache.SummaryCacheTests',
        'deploy.multirun.test_visualiser_summary_cache.SummaryBackendTests.test_physical_raw_deletion_keeps_all_seed_summaries_for_a_later_pair_and_baseline_swap',
        'deploy.multirun.test_visualiser_summary_cache.SummaryBackendTests.test_held_summaries_do_not_bypass_changed_source_checks_or_explicit_invalidation',
        'deploy.multirun.test_backup_visualiser','deploy.multirun.test_backup_live']
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    report=dict(passed=result.wasSuccessful() and result.testsRun>0 and not result.skipped,
        tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),skipped=len(result.skipped),
        problems=[dict(test=str(test),traceback=trace) for test,trace in result.failures+result.errors],
        sources=sources,scientific_simulations=False,real_emails=False,browser_passed=False)
    try:
        if report['passed']:
            process=subprocess.run(['node',str(FRONT/'tests/js/batch_output_deletion.test.mjs')],
                cwd=FRONT,capture_output=True,text=True,timeout=120)
            (args.output/'node-checks.log').write_text(process.stdout+process.stderr)
            if process.returncode:raise RuntimeError('The Storage interface checks failed')
            path=FRONT/'tests/browser/batch_raw_output_retention.py'
            spec=importlib.util.spec_from_file_location('raw_output_retention_browser',path)
            browser=importlib.util.module_from_spec(spec);spec.loader.exec_module(browser)
            browser.execute(args.output/'browser')
            value=json.loads((args.output/'browser/report.json').read_text())
            report['browser_passed']=bool(value.get('passed') and value.get('cleanup') and not value.get('browser_errors'))
            report['passed']=report['passed'] and report['browser_passed']
    except Exception as error:
        report['passed']=False
        report['problems'].append(dict(stage='node-or-browser',error_type=type(error).__name__,traceback=traceback.format_exc()))
        traceback.print_exc()
    finally:
        report['sources_unchanged']=sources==fingerprints()
        report['passed']=report['passed'] and report['sources_unchanged']
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Generated fictional raw-output deletion, chart and recovery qualification evidence.\n@author ross richardson\n-->\n')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
