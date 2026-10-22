#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

SingleRun notification browser, delivery, unified backup and recovery acceptance.
Only fictional data, native disposable SQL and captured email are used.
@author ross richardson
"""
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy._workflow import frontend_path
from deploy.multirun.backup_proof import main

sys.path[:0]=[str(frontend_path()),str(frontend_path()/'tests'),
              str(frontend_path()/'tests/batch'),str(frontend_path()/'tests/browser')]
os.environ['SINGLERUN_NATIVE_PROOF']='1'

NAMES=[
    'test_availability',
    'test_availability_interest',
    'test_availability_service',
    'test_single_availability',
    'test_single_lifecycle',
    'deploy.multirun.test_availability_restore',
    'deploy.multirun.test_backup_single',
    'deploy.multirun.test_backup_live',
    'singlerun_availability',
    'test_vm_state',
    'test_shared_vm_pool',
]

if __name__=='__main__':raise SystemExit(main(names=(['singlerun_availability']
    if os.environ.get('SIMPATHS_AVAILABILITY_BROWSER_ONLY')=='1' else NAMES)))
