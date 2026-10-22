#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Disposable CPU/RAM approval, shared admission, runtime and recovery acceptance.
Fictional jobs, captured mail and temporary PostgreSQL only; no scientific work.
@author ross richardson
"""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
sys.path.insert(0,str(frontend_path()/'tests'))
sys.path.insert(0,str(frontend_path()/'tests/batch'))
from deploy.multirun.backup_proof import main


if __name__=='__main__':
    raise SystemExit(main(names=[
        'test_compute_capacity','test_batch_compute_capacity',
        'test_postgres','test_storage_budget','test_shared_vm_pool',
        'test_adaptive_cpu','test_processing_capacity','test_idle_interactive_cpu',
        'deploy.multirun.test_compute_capacity','deploy.multirun.test_vm_config',
        'deploy.multirun.test_vm_runtime','deploy.multirun.test_operator_status',
    ]))
