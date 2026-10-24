#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Native account grace, permanent approvals, renewal delivery and recovery acceptance.
Fictional files, synthetic dates and captured mail only; no scientific simulations.
@author ross richardson
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
sys.path.insert(0,str(frontend_path()/'tests'))
sys.path.insert(0,str(frontend_path()/'tests/batch'))
from deploy.multirun.backup_proof import main as run_proof


def source_files():
    frontend=frontend_path();model=Path(__file__).resolve().parents[2]
    paths=[('frontend',frontend,p) for p in (frontend/'jasmine_web/batch').iterdir() if p.suffix in ('.py','.sql')]
    paths += [('frontend',frontend,p) for p in (frontend/'tests/batch').glob('test_*.py')]
    paths += [('frontend',frontend,frontend/'tests'/name) for name in
        ('test_batch_account_policy.py','test_batch_visualiser_summary_retention.py','test_batch_service_rpc.py')]
    paths += [('model',model,model/'deploy/multirun'/name) for name in
        ('runtime.py','local_web.py','vm_web.py','account_admin.py','account_lifecycle_proof.py',
         'test_account_admin.py','test_account_restore.py','test_backup_live.py')]
    return {prefix+'/'+p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for prefix,root,p in sorted(paths,key=lambda item:(item[0],str(item[2])))}


def main():
    parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--output',type=Path,required=True)
    args,_=parser.parse_known_args()
    before=source_files()
    primary=[
        'test_account_lifecycle','test_batch_account_policy',
        'test_visualiser_independent_retention','test_visualiser_automatic',
        'test_batch_service_rpc','test_retention','test_shared_vm_pool',
        'deploy.multirun.test_account_admin',
    ]
    recovery=[
        'deploy.multirun.test_account_restore',
        'deploy.multirun.test_backup_live.LiveBackupTests.test_cancel_retry_opt_out_exhaustion_and_revocation_remain_bounded',
        'deploy.multirun.test_vm_runtime','deploy.multirun.test_download_shutdown',
    ]
    previous=None
    if os.environ.get('SIMPATHS_ACCOUNT_PREVIOUS_PROOF'):
        path=Path(os.environ['SIMPATHS_ACCOUNT_PREVIOUS_PROOF'])
        previous=json.loads(path.read_text())
        if (previous.get('failures') or previous.get('skipped') or not previous.get('source_files_unchanged')
                or not previous.get('problems') or any("ModuleNotFoundError: No module named 'yaml'" not in p['traceback']
                    for p in previous['problems'])):
            raise ValueError('Only the missing-dependency recovery stage can be resumed')
        allowed={'model/deploy/multirun/account_lifecycle_proof.py','model/deploy/multirun/test_account_restore.py'}
        if any(before.get(name)!=sha for name,sha in previous['source_files'].items() if name not in allowed):
            raise ValueError('Previously checked application sources changed')
        previous=dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                      passed_checks=previous['tests']-len(previous['problems']))
    status=run_proof(names=recovery if previous else primary+recovery)
    path=args.output/'report.json';report=json.loads(path.read_text())
    report['source_files']=before;report['source_files_unchanged']=source_files()==before
    if previous:report['previous_stage']=previous
    if not report['source_files_unchanged']:report['passed']=False;status=1
    path.write_text(json.dumps(report,indent=2)+'\n')
    return status


if __name__=='__main__':raise SystemExit(main())
