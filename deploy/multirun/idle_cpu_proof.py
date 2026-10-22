#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Disposable native idle SingleRun CPU loans, real Docker recall, runtime shutdown
and matching PostgreSQL recovery. Fictional helper jobs and captured mail only.
@author ross richardson
"""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy._workflow import frontend_path
from deploy.multirun.backup_proof import main

sys.path[:0]=[str(frontend_path()),str(frontend_path()/'tests'),str(frontend_path()/'tests/batch')]

if __name__=='__main__':raise SystemExit(main(names=[
    'test_idle_interactive_cpu',
    'test_idle_interactive_cpu_docker',
    'test_adaptive_cpu',
    'test_adaptive_cpu_docker',
    'test_postgres',
    'test_shared_vm_pool',
    'test_processing_capacity',
    'deploy.multirun.test_idle_cpu_restore',
    'deploy.multirun.test_vm_runtime',
    'deploy.multirun.test_vm_config.VMConfigTests',
    'deploy.multirun.test_operator_status',
    'deploy.multirun.test_download_shutdown',
]))
