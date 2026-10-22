#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Native compact-backup encryption, PostgreSQL recovery and interruption proof.
Uses fictional files, temporary keys and captured transports; no real emails,
scientific simulations or external storage connections.
@author ross richardson
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy.multirun.backup_proof import main


if __name__=='__main__':raise SystemExit(main(names=[
    'deploy.multirun.test_backup_stream',
    'deploy.multirun.test_backup_transport',
    'deploy.multirun.test_backup_schedule',
    'deploy.multirun.test_backup_remote_retention',
    'deploy.multirun.test_backup_single',
    'deploy.multirun.test_backup_visualiser',
    'deploy.multirun.test_backup_live',
]))
