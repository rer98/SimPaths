#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Native administrator browser, private RPC and online/offline recovery acceptance.
All accounts and records are fictional; SMTP is captured and no model is launched.
@author ross richardson
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

MODEL=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(MODEL))
from deploy._workflow import frontend_path
FRONT=frontend_path()
sys.path[:0]=[str(FRONT),str(FRONT/'tests'),str(FRONT/'tests/batch')]


def source_files():
    paths=[('frontend',FRONT,p) for p in (FRONT/'jasmine_web/batch').iterdir() if p.suffix in ('.py','.sql')]
    paths += [('frontend',FRONT,p) for p in (FRONT/'jasmine_web/batch/browser_static').iterdir() if p.is_file()]
    paths += [('frontend',FRONT,FRONT/'tests/browser/batch_administration.py')]
    paths += [('frontend',FRONT,FRONT/'tests'/name) for name in ('test_batch_administration.py','batch/test_administration.py')]
    paths += [('model',MODEL,MODEL/'deploy/multirun'/name) for name in
        ('administrator_proof.py','administrator_config.py','runtime.py','vm_config.py','vm_web.py',
         'backup_live.py','test_administrator_restore.py','test_administrator_config.py')]
    return {prefix+'/'+path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
            for prefix,root,path in paths}


def main():
    parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--output',type=Path,required=True)
    args,_=parser.parse_known_args();before=source_files()
    from deploy.multirun.backup_proof import main as run_checks
    status=run_checks(names=['test_administration','test_batch_administration','test_batch_service_rpc',
        'deploy.multirun.test_administrator_config','deploy.multirun.test_vm_config',
        'deploy.multirun.test_administrator_restore','deploy.multirun.test_account_admin',
        'deploy.multirun.test_vm_runtime'])
    path=args.output/'report.json';report=json.loads(path.read_text())
    try:
        if status==0:
            spec=importlib.util.spec_from_file_location('administrator_browser_proof',FRONT/'tests/browser/batch_administration.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            module.execute(args.output/'browser')
            report['browser']=json.loads((args.output/'browser/report.json').read_text())
    except Exception as error:
        report['passed']=False;report['browser_error']=type(error).__name__;status=1
        raise
    finally:
        report['source_files']=before;report['source_files_unchanged']=before==source_files()
        if not report['source_files_unchanged']:report['passed']=False;status=1
        path.write_text(json.dumps(report,indent=2)+'\n')
    return status


if __name__=='__main__':raise SystemExit(main())
