#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Native proof of short SingleRun backup fencing and bounded cache-lock waits.
Fictional files, temporary encryption keys and disposable PostgreSQL only.
@author ross richardson
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy.multirun.backup_proof import main as proof
from deploy._workflow import frontend_path


def sources():
    directory=Path(__file__).resolve().parent
    result={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in
        ('backup_live.py','backup_visualiser.py','backup_single.py','backup_availability_proof.py',
         'test_backup_live.py','test_backup_visualiser.py','test_backup_stream.py','test_backup_single.py','test_account_restore.py')}
    front=frontend_path()
    result.update({'frontend/'+name:hashlib.sha256((front/name).read_bytes()).hexdigest()
                   for name in ('jasmine_web/vm_backup.py','tests/batch/test_backup.py')})
    return result


def main():
    parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--output',type=Path,required=True)
    args,_=parser.parse_known_args();before=sources()
    names=['deploy.multirun.test_backup_single','deploy.multirun.test_backup_stream','deploy.multirun.test_backup_visualiser',
           'deploy.multirun.test_backup_live','deploy.multirun.test_account_restore']
    sys.path.insert(0,str(frontend_path()/'tests/batch'))
    names += ['test_backup.BackupDatabaseTests.'+name for name in (
        'test_single_run_backup_fence_allows_reads_but_defers_controls_and_container_removal',
        'test_backup_acquisition_waits_for_short_singlerun_activity_and_keeps_database_reads_available',
        'test_backup_acquisition_wait_is_bounded_and_other_coordination_errors_are_immediate')]
    previous=None
    if os.environ.get('SIMPATHS_BACKUP_PREVIOUS_PROOF'):
        path=Path(os.environ['SIMPATHS_BACKUP_PREVIOUS_PROOF']);old=json.loads(path.read_text())
        if (old.get('errors') or old.get('skipped') or old.get('failures')!=3 or not old.get('source_files_unchanged')
                or any('AssertionError: NotFound not raised' not in row['traceback'] for row in old['problems'])):
            raise ValueError('Only the three obsolete grant-expiry fixtures may be resumed')
        if any(before.get(name)!=sha for name,sha in old['source_files'].items()
               if name not in ('backup_availability_proof.py','test_backup_visualiser.py')):
            raise ValueError('Previously checked backup sources changed')
        names=['deploy.multirun.test_backup_stream.NativeStreamRestoreTests.test_expired_raw_data_summaries_charts_and_revocation_survive_streamed_restore',
               'deploy.multirun.test_backup_visualiser.NativeVisualiserRestoreTests.test_offline_restore_preserves_summaries_charts_expiry_and_revocation_after_raw_expiry',
               'deploy.multirun.test_backup_visualiser.NativeVisualiserRestoreTests.test_online_restore_preserves_summaries_charts_expiry_and_revocation_after_raw_expiry']
        previous=dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),passed_checks=old['tests']-3)
    status=proof(names=names)
    path=args.output/'report.json';report=json.loads(path.read_text())
    report['source_files']=before;report['source_files_unchanged']=sources()==before
    if previous:report['previous_stage']=previous
    if not report['source_files_unchanged']:report['passed']=False;status=1
    path.write_text(json.dumps(report,indent=2)+'\n')
    return status


if __name__=='__main__':raise SystemExit(main())
