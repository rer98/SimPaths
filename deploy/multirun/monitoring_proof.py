#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Disposable operational history, migration and starting-profile acceptance.
# @author ross richardson
import argparse
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))


def main():
    parser=argparse.ArgumentParser(description='Operational monitoring and frozen workload profiles; fictional data only')
    parser.add_argument('--execute-proof',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Use scripts/test_batch_queue.py with disposable PostgreSQL')
    if args.output.exists():parser.error('Choose a new evidence directory')
    args.output.mkdir(mode=0o700,parents=True)
    from deploy.multirun.artifacts import write_attribution
    write_attribution(args.output)
    from deploy._workflow import frontend_path
    sys.path[:0]=[str(frontend_path()),str(frontend_path()/'tests'),str(frontend_path()/'tests/batch')]
    names=['test_observability','test_operator_status',
        'test_postgres.PostgresQueueTests.test_atomic_migration_and_checksum',
        'test_postgres.PostgresQueueTests.test_configuration_input_migration_preserves_existing_jobs',
        'test_resource_recovery',
        'deploy.multirun.test_resource_profiles','deploy.multirun.test_releases',
        'deploy.multirun.test_prepared_dataset','deploy.multirun.test_submission_adapter',
        'deploy.multirun.test_container_adapter','deploy.multirun.test_vm_config',
        'deploy.multirun.test_operator_status','deploy.multirun.test_vm_runtime',
        'deploy.multirun.test_backup.BackupRestoreTests.test_operational_history_and_flush_receipts_survive_native_inactive_restore']
    suite=unittest.defaultTestLoader.loadTestsFromNames(names)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    passed=result.wasSuccessful() and not result.skipped
    report=dict(passed=passed,tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
        skipped=len(result.skipped),problems=[dict(test=str(test),traceback=trace)
            for test,trace in result.failures+result.errors])
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if passed:print('PASS: native numeric history, scheduler isolation, immutable profiles and legacy releases verified without simulations or emails',flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
