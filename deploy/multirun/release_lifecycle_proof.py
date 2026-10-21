#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Native release availability, retirement, backup/restore and private-service proof.
# @author ross richardson
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

if __name__=='__main__':
    from deploy.multirun.backup_proof import main
    raise SystemExit(main(names=[
        'deploy.multirun.test_releases','deploy.multirun.test_release_lifecycle',
        'deploy.multirun.test_submission_adapter','deploy.multirun.test_browser_model',
        'deploy.multirun.test_browser_copy','deploy.multirun.test_release_runtime',
        'deploy.multirun.test_vm_runtime','deploy.multirun.test_backup',
        'deploy.multirun.test_backup_live','deploy.multirun.test_backup_visualiser',
        'test_batch_experiment_extensions','test_batch_release_browser',
        'test_batch_remote_service','test_batch_service_rpc']))
