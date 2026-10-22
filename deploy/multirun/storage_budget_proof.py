#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Disposable PostgreSQL proof of live storage growth and native recovery controls.
Fictional jobs/files only; no scientific model executions or real emails.
@author ross richardson
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy._workflow import frontend_path
sys.path.insert(0, str(frontend_path()))
sys.path.insert(0, str(frontend_path()/'tests'))
sys.path.insert(0, str(frontend_path()/'tests/batch'))
from deploy.multirun.backup_proof import main


if __name__ == '__main__':
    raise SystemExit(main(names=[
        'test_storage_budget', 'test_batch_storage_budget',
        'test_postgres', 'test_storage', 'test_shared_vm_pool',
        'deploy.multirun.test_storage_budget',
        'deploy.multirun.test_vm_runtime', 'deploy.multirun.test_operator_status',
    ]))
