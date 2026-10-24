#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Native baseline-selection, paired-calculation and encrypted-state recovery qualification.
Fictional data only; no scientific simulations or real emails.
@author ross richardson
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

MODEL=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(MODEL))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute-proof',action='store_true')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):parser.error('Use scripts/test_batch_queue.py')
    from deploy._workflow import frontend_path
    front=frontend_path();model=MODEL
    sys.path[:0]=[str(front),str(front/'tests'),str(front/'tests/batch'),str(front/'tests/browser')]
    build=Path(os.environ['SIMPATHS_VISUALISER_TEST_BUILD'])
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    report=dict(passed=False,scientific_simulations=False,real_emails=False)
    try:
        names=('test_visualiser_experiments','test_visualiser_preparation',
            'test_batch_service_rpc','test_batch_remote_service',
            'deploy.multirun.test_backup_visualiser.VisualiserBackupFilesTests',
            'deploy.multirun.test_backup_visualiser.NativeVisualiserRestoreTests',
            'deploy.multirun.test_visualiser_backend')
        previous=os.environ.get('SIMPATHS_BASELINE_PREVIOUS_PROOF')
        if previous:
            previous=Path(previous);accepted=json.loads(previous.read_text())
            native=json.loads((previous.parents[1]/'report.json').read_text())
            hashes=json.loads((previous.parents[2]/'sources-before.json').read_text())
            if not accepted.get('tests',{}).get('passed') or not native.get('cleanup'):
                raise ValueError('Require the completed prior native tests')
            for name,digest in hashes.items():
                if name.startswith('build/'):root=build;relative=name.split('/',1)[1]
                else:
                    project,relative=name.split('/',1);root=front if project=='JAS-mine-web' else model
                    if relative.startswith('tests/') or '/test_' in relative or relative.endswith('_proof.py'):continue
                    if relative in ('jasmine_web/batch/browser_static/batch.js','jasmine_web/batch/browser_static/batch_visualiser.js'):continue
                if hashlib.sha256((root/relative).read_bytes()).hexdigest()!=digest:
                    raise ValueError('Previously checked application code changed')
            report['reused_tests']=dict(**accepted['tests'],report_sha256=hashlib.sha256(previous.read_bytes()).hexdigest())
            names=('test_visualiser_experiments',)
            if os.environ.get('SIMPATHS_BASELINE_BROWSER_ONLY')=='1':
                # All Python application files and the pinned calculation build
                # matched above. Only the JS controls/browser fixture changed.
                names=()
        if names:
            suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)
            result=unittest.TextTestRunner(verbosity=2).run(suite)
            report['tests']=dict(passed=result.wasSuccessful(),count=result.testsRun,skipped=len(result.skipped))
            if not result.wasSuccessful():raise AssertionError('Native backend/recovery checks failed')
        else:
            print(f"REUSED: {report['reused_tests']['count']} passing Python/recovery checks; application Python and calculation build hashes unchanged.",flush=True)
            report['tests']=dict(passed=True,count=0,skipped=0,reused=True)
        from batch_visualiser_baseline import execute
        execute(args.output/'browser',build,model)
        report['browser']=json.loads((args.output/'browser/report.json').read_text())
        report['passed']=report['browser']['passed'] and report['browser']['cleanup']
    finally:
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\nGenerated fictional baseline qualification records.\n@author ross richardson\n-->\n')
    if not report['passed']:return 1
    print('PASSED: '+str(args.output/'report.json'),flush=True);return 0


if __name__=='__main__':raise SystemExit(main())
