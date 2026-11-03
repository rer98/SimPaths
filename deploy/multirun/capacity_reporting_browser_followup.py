#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Repeat only the administrator browser proof after an explicitly bound fixture fix.
Reuse the unchanged 96-check backend receipt; use fictional data and captured mail.
@author ross richardson
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import traceback

MODEL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODEL))
from deploy.multirun import capacity_reporting_proof as original

FORMAT = 'simpaths.capacity-browser-followup.v1'
BROWSER = 'JAS-mine-web/tests/browser/batch_administration.py'
SELF = 'SimPaths/deploy/multirun/capacity_reporting_browser_followup.py'


def fingerprint(path):
    body = Path(path).read_bytes()
    return dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())


def _receipt(entry):
    if type(entry) is not dict or set(entry) != {'path', 'bytes', 'sha256'}:
        raise ValueError('Require an explicitly fingerprinted prior receipt')
    path = Path(entry['path'])
    if not path.is_absolute() or fingerprint(path) != {key:entry[key] for key in ('bytes', 'sha256')}:
        raise ValueError('The accepted prior receipt changed')
    return path, json.loads(path.read_text())


def validate_reuse(plan, current):
    """Only the reviewed browser fixture may differ from the passing backend run."""
    if (type(plan) is not dict or set(plan) != {'format', 'backend_report', 'runner_report', 'browser'}
            or plan['format'] != FORMAT or type(plan['browser']) is not dict
            or set(plan['browser']) != {'before_sha256', 'after_sha256'}):
        raise ValueError('Require the reviewed browser-only follow-up plan')
    backend_path, backend = _receipt(plan['backend_report'])
    runner_path, runner = _receipt(plan['runner_report'])
    if (backend_path.name != 'report.json' or backend_path.parent.name != 'model-proof'
            or runner_path != backend_path.parent.parent/'report.json'):
        raise ValueError('Backend and cleanup receipts must belong to the same native run')
    if (backend.get('tests') != 96 or any(backend.get(key) != 0 for key in ('errors', 'failures', 'skipped'))
            or backend.get('problems') != [] or backend.get('sources_unchanged') is not True
            or runner.get('cleanup') is not True or runner.get('queue_tests_skipped') is not True):
        raise ValueError('Require all 96 backend checks passed and disposable database cleanup')
    old = backend.get('sources')
    if type(old) is not dict or set(old) != set(current) or BROWSER not in old:
        raise ValueError('The backend source inventory changed')
    if (old[BROWSER] != plan['browser']['before_sha256']
            or current[BROWSER] != plan['browser']['after_sha256']
            or old[BROWSER] == current[BROWSER]):
        raise ValueError('Require exactly the reviewed browser fixture correction')
    if any(old[name] != digest for name, digest in current.items() if name != BROWSER):
        raise ValueError('Application, backend test or original proof changed; rerun the full proof')
    return backend, dict(backend_report=dict(plan['backend_report']),
                         runner_report=dict(plan['runner_report']), tests=96,
                         browser_before=old[BROWSER], browser_after=current[BROWSER])


def sources():
    return dict(original.sources(), **{SELF:fingerprint(__file__)['sha256']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN'):
        parser.error('Use scripts/test_batch_queue.py with a fresh disposable database')
    plan_path = os.environ.get('SIMPATHS_CAPACITY_BROWSER_FOLLOWUP_PLAN')
    if not plan_path:
        parser.error('Use the prepared browser-only follow-up wrapper')
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = sources()
    report = dict(passed=False, sources=before, backend_reused=True,
                  scientific_simulations=False, real_emails=False)
    try:
        plan_fingerprint = fingerprint(plan_path)
        plan = json.loads(Path(plan_path).read_text())
        backend, evidence = validate_reuse(plan, original.sources())
        report.update(tests=backend['tests'], errors=0, failures=0, skipped=0, problems=[],
                      backend_evidence=evidence, followup_plan=plan_fingerprint)
        print('VERIFIED: all 96 backend checks and unchanged application sources; repeating only Chromium.', flush=True)
        spec = importlib.util.spec_from_file_location('capacity_browser_followup', original.FRONT/'tests/browser/batch_administration.py')
        browser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(browser)
        browser.execute(args.output/'browser')
        report['browser'] = json.loads((args.output/'browser/report.json').read_text())
        report['passed'] = (report['browser'].get('passed') is True
                            and report['browser'].get('cleanup') is True
                            and not report['browser'].get('browser_errors')
                            and fingerprint(plan_path) == plan_fingerprint)
    except Exception:
        report['proof_error'] = traceback.format_exc()
        traceback.print_exc()
    finally:
        report['sources_unchanged'] = before == sources()
        report['passed'] = report['passed'] and report['sources_unchanged']
        (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
            'Generated fictional browser follow-up and reused backend qualification evidence.\n'
            '@author ross richardson\n-->\n')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
