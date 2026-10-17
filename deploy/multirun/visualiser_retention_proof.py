#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Focused native backup/restore acceptance for independently retained Visualiser
results, plus legacy backups and SingleRun recovery compatibility.
@author ross richardson
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy.multirun.backup_proof import main


if __name__=='__main__':
    raise SystemExit(main(names=[
        'deploy.multirun.test_backup',
        'deploy.multirun.test_backup_visualiser',
        'deploy.multirun.test_backup_live',
        'deploy.multirun.test_backup_single',
    ]))
