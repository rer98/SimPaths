#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Native encrypted handover recovery and retained Visualiser compatibility proof.
Uses fictional files and ephemeral keys; no scientific models or real emails.
@author ross richardson
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy.multirun.backup_proof import main


if __name__=='__main__':raise SystemExit(main(names=[
    'deploy.multirun.test_backup_handover',
    'deploy.multirun.test_backup_transport',
    'deploy.multirun.test_backup_visualiser',
]))
