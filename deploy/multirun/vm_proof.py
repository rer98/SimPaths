#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Run the VM launcher proof only against explicitly supplied disposable PostgreSQL.
# @author ross richardson
import argparse
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Use scripts/test_batch_queue.py to supply disposable PostgreSQL')
    if args.output.exists():
        parser.error('Choose new evidence output')
    args.output.mkdir(parents=True, mode=0o700)
    suite = unittest.defaultTestLoader.loadTestsFromName('deploy.multirun.test_vm_runtime')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.wasSuccessful() and not result.skipped
    (args.output/'report.json').write_text(json.dumps(dict(passed=passed, tests=result.testsRun,
        failures=len(result.failures), errors=len(result.errors)), indent=2)+'\n')
    (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Generated VM fixture evidence attribution.\n@author ross richardson -->\n\n'
        'Proof and generated evidence: (C) Copyright 2026, by Ross Richardson.\n')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
