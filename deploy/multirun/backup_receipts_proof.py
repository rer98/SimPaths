#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Qualify receipt-bound online backups and visible reviewed-deletion feedback.
Fictional files, captured mail, disposable PostgreSQL and Chromium; no model runs.
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
sys.path[:0]=[str(FRONT),str(FRONT/'tests'),str(FRONT/'tests/batch')]
SOURCES={
 'SimPaths':('deploy/multirun/backup_live.py','deploy/multirun/backup_output_receipts.py',
  'deploy/multirun/queue_adapter.py','deploy/multirun/csv_validation.py',
  'deploy/multirun/backup.py','deploy/multirun/backup_stream.py','deploy/multirun/backup_transport.py',
  'deploy/multirun/test_backup_output_receipts.py','deploy/multirun/test_queue_adapter.py',
  'deploy/multirun/test_backup_live.py','deploy/multirun/test_backup.py',
  'deploy/multirun/test_backup_stream.py','deploy/multirun/test_backup_visualiser.py',
  'deploy/multirun/backup_receipts_proof.py'),
 'JAS-mine-web':('jasmine_web/batch/browser_static/batch.js','jasmine_web/batch/output_management.py',
  'jasmine_web/batch/results.py','jasmine_web/batch/backup_guard.py',
  'jasmine_web/batch/visualiser_summary_retention.py','jasmine_web/batch/storage.py',
  'jasmine_web/batch/remote_service.py','jasmine_web/batch/service_rpc.py',
  'tests/js/batch_output_deletion.test.mjs','tests/browser/batch_raw_output_retention.py')}


def fingerprints():
    return {project+'/'+name:hashlib.sha256((root/name).read_bytes()).hexdigest()
        for project,root in (('SimPaths',MODEL),('JAS-mine-web',FRONT)) for name in SOURCES[project]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Use the disposable PostgreSQL/browser runner')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    os.nice(15)
    sources=fingerprints()
    names=['deploy.multirun.test_backup_output_receipts','deploy.multirun.test_queue_adapter',
        'deploy.multirun.test_backup_live',
        'deploy.multirun.test_backup_stream.NativeStreamRestoreTests.test_original_running_job_snapshot_and_native_restore_guards_pass_through_streaming',
        'deploy.multirun.test_backup_visualiser.NativeVisualiserRestoreTests.test_online_restore_preserves_raw_only_deletion_deadline_and_held_summaries',
        'deploy.multirun.test_backup_visualiser.NativeVisualiserRestoreTests.test_online_pending_raw_only_missing_summary_keeps_raw_in_recovery',
        'deploy.multirun.test_backup_visualiser.NativeVisualiserRestoreTests.test_online_backup_rejects_pending_raw_only_output_missing_before_acknowledgement']
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    report=dict(passed=result.wasSuccessful() and result.testsRun>0 and not result.skipped,
        tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),skipped=len(result.skipped),
        problems=[dict(test=str(test),traceback=trace) for test,trace in result.failures+result.errors],
        sources=sources,browser_passed=False,scientific_simulations=False,real_emails=False)
    try:
        if report['passed']:
            process=subprocess.run(['node',str(FRONT/'tests/js/batch_output_deletion.test.mjs')],
                cwd=FRONT,capture_output=True,text=True,timeout=120)
            (args.output/'node-checks.log').write_text(process.stdout+process.stderr)
            if process.returncode:raise RuntimeError('The inline Storage feedback checks failed')
            path=FRONT/'tests/browser/batch_raw_output_retention.py'
            spec=importlib.util.spec_from_file_location('backup_receipt_storage_browser',path)
            browser=importlib.util.module_from_spec(spec);spec.loader.exec_module(browser)
            browser.execute(args.output/'browser')
            value=json.loads((args.output/'browser/report.json').read_text())
            report['browser_passed']=bool(value.get('passed') and value.get('cleanup') and not value.get('browser_errors'))
            report['passed']=report['browser_passed']
    except Exception:
        report['passed']=False
        report['problems'].append(dict(stage='node-or-browser',traceback=traceback.format_exc()))
        traceback.print_exc()
    finally:
        report['sources_unchanged']=sources==fingerprints()
        report['passed']=report['passed'] and report['sources_unchanged']
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Generated fictional backup receipt, restore and Storage feedback qualification evidence.\n@author ross richardson\n-->\n')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
