#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Qualify preventive resource notices, private browser history and native recovery.
Uses fictional jobs and captured mail; no scientific simulations or real emails.
@author ross richardson
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

MODEL=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(MODEL))
from deploy._workflow import frontend_path
FRONT=frontend_path()
sys.path[:0]=[str(FRONT),str(FRONT/'tests'),str(FRONT/'tests/batch')]
SOURCES={
    'JAS-mine-web':('jasmine_web/batch/resource_recovery.py','jasmine_web/batch/notifications.py',
        'jasmine_web/batch/browser_static/batch.js','jasmine_web/batch/browser_static/batch.css',
        'tests/batch/test_resource_growth.py','tests/batch/test_resource_recovery.py',
        'tests/test_batch_resource_notices.py','tests/browser/batch_resource_recovery.py'),
    'SimPaths':('deploy/multirun/test_backup_live.py','deploy/multirun/resource_growth_proof.py')}


def sources():
    from deploy.multirun.artifacts import fingerprint
    return {project+'/'+name:fingerprint(root/name) for project,root in (('JAS-mine-web',FRONT),('SimPaths',MODEL))
            for name in SOURCES[project]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Use scripts/test_batch_queue.py with disposable PostgreSQL')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    before=sources()
    report=dict(passed=False,sources=before,sources_unchanged=False)
    try:
        names=['test_batch_resource_notices','test_batch_job_visibility',
            'deploy.multirun.test_backup_live.LiveBackupTests.test_preventive_growth_reason_survives_restore_as_history_without_replaying_wait',
            'deploy.multirun.test_backup_live.LiveBackupTests.test_confirmed_growth_survives_restore_without_replaying_pending_changes',
            'deploy.multirun.test_backup.BackupRestoreTests.test_real_dump_restore_verifies_all_rows_files_external_deduplication_and_preserved_deadlines']
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
        report.update(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
            problems=[dict(test=str(test),traceback=trace) for test,trace in result.failures+result.errors])
        if result.wasSuccessful() and not result.skipped and result.testsRun==12:
            spec=importlib.util.spec_from_file_location('resource_history_browser',FRONT/'tests/browser/batch_resource_recovery.py')
            browser=importlib.util.module_from_spec(spec);spec.loader.exec_module(browser)
            browser.execute(args.output/'browser')
            evidence=json.loads((args.output/'browser/report.json').read_text())
            report['browser_checks']=len(evidence['checks'])
            report['passed']=evidence['passed'] and evidence['cleanup'] and report['browser_checks']==5
    finally:
        report['sources_unchanged']=before==sources()
        report['passed']=report['passed'] and report['sources_unchanged']
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Generated fictional resource-growth, history and recovery acceptance evidence.\n@author ross richardson\n-->\n')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
