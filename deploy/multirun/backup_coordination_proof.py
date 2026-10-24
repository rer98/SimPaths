#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Prove bounded SingleRun backup acquisition, ordinary controls and snapshot release.
Disposable PostgreSQL, real GNU find and fictional export records; no real mail.
@author ross richardson
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy._workflow import frontend_path
from deploy.multirun.backup_proof import main as proof


def sources():
    front=frontend_path()
    files=[(ROOT,'model',name) for name in (
        'deploy/multirun/backup_single.py','deploy/multirun/backup_live.py','deploy/multirun/backup_visualiser.py',
        'deploy/multirun/test_backup_single.py','deploy/multirun/test_backup_live.py','deploy/multirun/test_backup_stream.py',
        'deploy/multirun/backup_coordination_proof.py')]
    files.extend((front,'frontend',name) for name in ('jasmine_web/vm_backup.py','tests/batch/test_backup.py'))
    return {prefix+'/'+name:hashlib.sha256((root/name).read_bytes()).hexdigest() for root,prefix,name in files}


def main():
    parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--output',type=Path,required=True)
    args,_=parser.parse_known_args();before=sources()
    front=frontend_path();sys.path[:0]=[str(front/'tests/batch'),str(front/'tests')]
    names=['test_backup.BackupDatabaseTests.'+name for name in (
        'test_single_run_backup_fence_allows_reads_but_defers_controls_and_container_removal',
        'test_backup_acquisition_waits_for_short_singlerun_activity_and_keeps_database_reads_available',
        'test_backup_acquisition_wait_is_bounded_and_other_coordination_errors_are_immediate')]
    names += ['deploy.multirun.test_backup_live.LiveBackupTests.test_backup_adapter_waits_for_short_singlerun_reader_before_snapshot',
        'deploy.multirun.test_backup_live.LiveBackupTests.test_singlerun_controls_resume_before_bulk_multirun_verification',
        'deploy.multirun.test_backup_stream.NativeStreamRestoreTests.test_singlerun_controls_are_released_before_streaming_bulk_multirun_data',
        'deploy.multirun.test_backup_single']
    status=proof(names=names)
    path=args.output/'report.json';report=json.loads(path.read_text())
    report['source_files']=before;report['source_files_unchanged']=sources()==before
    if not report['source_files_unchanged']:report['passed']=False;status=1
    path.write_text(json.dumps(report,indent=2)+'\n')
    return status


if __name__=='__main__':raise SystemExit(main())
