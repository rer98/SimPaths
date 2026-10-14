#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Record native ZIP shutdown, private lifecycle and browser cleanup acceptance.
# Uses fictional data and local threads/sockets; no database, Docker or email.
# @author ross richardson
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deploy._workflow import frontend_path

FRONTEND_SOURCES = (
    'jasmine_web/async_cleanup.py', 'jasmine_web/batch/downloads.py',
    'jasmine_web/batch/browser.py', 'jasmine_web/batch/service_rpc.py',
    'jasmine_web/batch/remote_service.py', 'tests/batch/test_downloads.py',
    'tests/test_batch_browser_admission.py', 'tests/browser/private_rpc_fixture.py',
    'tests/test_browser_private_rpc_fixture.py',
)
HOST_SOURCES = (
    'deploy/multirun/runtime.py', 'deploy/multirun/vm_executor.py',
    'deploy/multirun/maintenance.py', 'deploy/multirun/test_download_shutdown.py',
    'deploy/multirun/run_download_shutdown.py',
)
TESTS = (
    'test_downloads.DownloadTests', 'test_downloads.DownloadLifespanTests',
    'test_batch_browser_admission', 'deploy.multirun.test_download_shutdown',
    'test_browser_private_rpc_fixture',
)


def native_socket_check():
    """Require real native socket I/O, including Python's worker wakeup mechanism."""
    left, right = socket.socketpair()
    with left, right:
        left.settimeout(2)
        right.settimeout(2)
        left.sendall(b'x')
        if right.recv(1) != b'x':
            raise RuntimeError('Native socket check failed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=frontend_path())
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Choose a new evidence directory')
    args.output.mkdir(parents=True, mode=0o700)
    (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Native ZIP shutdown fixture evidence attribution.\n@author ross richardson -->\n\n'
        'Authored reports: (C) Copyright 2026, by Ross Richardson.\n')
    record = dict(format='simpaths.download-shutdown-proof.v1', passed=False,
        recorded_at=datetime.now(timezone.utc).isoformat(), native_sockets=False,
        installed_uid_isolation_tested=False, simulations_run=0)
    try:
        record['sources'] = {
            project: {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
            for project, root, names in (
                ('frontend', args.frontend, FRONTEND_SOURCES), ('host', ROOT, HOST_SOURCES))}
        for path in (args.frontend, args.frontend/'tests', args.frontend/'tests/batch'):
            sys.path.insert(0, str(path))
        native_socket_check()
        record['native_sockets'] = True
        # Do not adapt listeners, worker wakeups or the event loop in this proof.
        suite = unittest.defaultTestLoader.loadTestsFromNames(TESTS)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        record.update(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
            skipped=len(result.skipped), passed=result.wasSuccessful() and not result.skipped)
    except Exception as error:
        record['error'] = type(error).__name__
        print('STOPPED: '+type(error).__name__+' during native ZIP shutdown proof.', flush=True)
    (args.output/'report.json').write_text(json.dumps(record, indent=2)+'\n')
    print(('PASSED: ' if record['passed'] else 'FAILED: ')+str(args.output/'report.json'), flush=True)
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
