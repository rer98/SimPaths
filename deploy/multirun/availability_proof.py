#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Native SingleRun notification verification, private RPC, capacity and recovery.
Uses fictional sessions and captured mail; no scientific simulations or emails.
@author ross richardson
"""
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy._workflow import frontend_path
from deploy.multirun.backup_proof import main

sys.path[:0]=[str(frontend_path()),str(frontend_path()/'tests'),str(frontend_path()/'tests/batch')]
os.environ['SINGLERUN_NATIVE_PROOF']='1'

if __name__=='__main__':raise SystemExit(main(names=[
    'test_availability',
    'test_availability_interest',
    'test_availability_service',
    'test_single_availability',
    'test_email_verification',
    'test_single_boundary',
    'test_single_lifecycle',
    'test_single_private_app',
    'test_vm_state',
    'test_shared_vm_pool',
    'test_idle_interactive_cpu',
]))
