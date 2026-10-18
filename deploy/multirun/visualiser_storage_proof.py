#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Native retained Visualiser storage and recovery proof with fictional files.
No scientific simulations, Docker model execution or real emails.
@author ross richardson
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy.multirun.backup_proof import main


if __name__=='__main__':raise SystemExit(main(names=[
    'deploy.multirun.test_backup_visualiser',
    'deploy.multirun.test_visualiser_storage',
    'deploy.multirun.test_vm_config',
    'deploy.multirun.test_operator_status',
    'deploy.multirun.test_download_shutdown',
]))
