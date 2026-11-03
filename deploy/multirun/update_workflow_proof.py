#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Source-bound planned-update, real-container handover, browser and restore proof.
Uses disposable PostgreSQL/Docker and captured mail; no scientific simulations.
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

MODEL=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(MODEL))
from deploy._workflow import frontend_path
FRONT=frontend_path()
sys.path[:0]=[str(FRONT),str(FRONT/'tests'),str(FRONT/'tests/batch')]


def sources():
    paths=[('JAS-mine-web',FRONT,p) for p in (FRONT/'jasmine_web/batch').iterdir()
           if p.suffix in ('.py','.sql')]
    paths += [('JAS-mine-web',FRONT,p) for p in (FRONT/'jasmine_web/batch/browser_static').iterdir() if p.is_file()]
    paths += [('JAS-mine-web',FRONT,FRONT/'tests'/name) for name in (
        'batch/test_update_admission.py','test_batch_update_admission.py',
        'batch/test_postgres.py','batch/test_processing_handover.py','batch/test_resource_growth.py',
        'batch/test_processing_capacity.py','batch/test_administration.py','test_batch_administration.py',
        'test_batch_update_reporting_ui.py','test_batch_capacity_reporting_ui.py',
        'browser/batch_administration.py','batch/test_docker_worker.py','batch/container_program.py')]
    paths += [('SimPaths',MODEL,MODEL/'deploy/multirun'/name) for name in (
        'application_update.py','update_package.py','test_application_update.py','test_update_package.py',
        'test_update_workflow.py','update_workflow_proof.py','backup.py','backup_live.py','backup_stream.py')]
    return {project+'/'+p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for project,root,p in paths}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN') or not os.environ.get('JASMINE_BATCH_DOCKER_IMAGE'):
        parser.error('Use scripts/test_batch_queue.py --docker-tests with disposable PostgreSQL')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    before=sources();report=dict(passed=False,sources=before,scientific_simulations=False,real_emails=False)
    try:
        names=['test_update_admission','test_batch_update_admission','test_processing_handover',
            'test_resource_growth','test_processing_capacity','test_administration','test_batch_administration',
            'test_batch_update_reporting_ui','test_batch_capacity_reporting_ui',
            'deploy.multirun.test_application_update','deploy.multirun.test_update_package',
            'deploy.multirun.test_update_workflow']
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
        report.update(tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),skipped=len(result.skipped),
                      problems=[dict(test=str(t),traceback=trace) for t,trace in result.failures+result.errors])
        if not result.wasSuccessful() or result.skipped or not result.testsRun:return 1
        spec=importlib.util.spec_from_file_location('application_update_admin_browser',FRONT/'tests/browser/batch_administration.py')
        browser=importlib.util.module_from_spec(spec);spec.loader.exec_module(browser)
        browser.execute(args.output/'browser')
        evidence=json.loads((args.output/'browser/report.json').read_text());report['browser']=evidence
        report.update(same_container_verified=True,frozen_attempts_verified=True,controller_switch_verified=True)
        report['passed']=bool(evidence.get('passed') and evidence.get('cleanup') and not evidence.get('browser_errors'))
    except Exception:
        report['proof_error']=traceback.format_exc();traceback.print_exc()
    finally:
        report['sources_unchanged']=before==sources();report['passed']=report['passed'] and report['sources_unchanged']
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Generated fictional update fencing, Docker handover, browser and native restore evidence.\n'
            '@author ross richardson\n-->\n')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
