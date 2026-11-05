#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Qualify supervised output verification with fictional files and captured mail.
Requires the disposable PostgreSQL harness; never use a live service database.
@author ross richardson
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

MODEL=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(MODEL))
from deploy._workflow import frontend_path
FRONT=frontend_path()
sys.path[:0]=[str(FRONT),str(FRONT/'tests'),str(FRONT/'tests/batch')]


def sources():
    result={}
    for name,root,folders in [('JAS-mine-web',FRONT,('jasmine_web/batch','tests/batch','tests/js')),
                              ('SimPaths',MODEL,('deploy/multirun',))]:
        for folder in folders:
            for path in sorted((root/folder).rglob('*')):
                if path.is_file() and path.suffix in ('.py','.js','.jsx','.cjs','.mjs','.sql','.html') and not any(
                        part in ('node_modules','__pycache__','build') for part in path.relative_to(root).parts):
                    body=path.read_bytes();result[name+'/'+path.relative_to(root).as_posix()]=dict(bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
    for path in (FRONT/'tests/test_batch_output_validation.py',FRONT/'tests/test_batch_processing_scope.py'):
        body=path.read_bytes();result['JAS-mine-web/'+path.relative_to(FRONT).as_posix()]=dict(bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Use the disposable PostgreSQL proof runner')
    args.output.mkdir(parents=True,mode=0o700,exist_ok=False)
    before=sources();report=dict(passed=False,sources=before,scientific_simulations=False,real_emails=False)
    names=['test_output_validation_state','test_worker','test_processing_capacity','test_idle_interactive_cpu',
        'test_resource_growth','test_attempt_cleanup','test_results','test_batch_output_validation',
        'test_batch_processing_scope','deploy.multirun.test_output_validation','deploy.multirun.test_csv_validation',
        'deploy.multirun.test_queue_adapter','deploy.multirun.test_vm_config','deploy.multirun.test_backup_validation',
        'deploy.multirun.test_backup_live.LiveBackupTests.test_running_attempt_new_publication_and_heartbeat_continue_while_snapshot_stays_fixed',
        'deploy.multirun.test_backup.BackupRestoreTests.test_real_dump_restore_verifies_all_rows_files_external_deduplication_and_preserved_deadlines']
    try:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
        report.update(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
            problems=[dict(test=str(test),traceback=trace) for test,trace in result.failures+result.errors])
        if result.wasSuccessful() and not result.skipped and result.testsRun>100:
            node=subprocess.run(['node',str(FRONT/'tests/js/batch_visualiser.test.mjs')],capture_output=True,text=True,timeout=120)
            (args.output/'interface.log').write_text(node.stdout+node.stderr)
            report['interface_passed']=node.returncode==0
            report['passed']=report['interface_passed']
    finally:
        report['sources_unchanged']=before==sources()
        report['passed']=report['passed'] and report['sources_unchanged']
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\nFictional output validation and native recovery evidence.\n@author ross richardson\n-->\n')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
