#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson
Qualify full monitoring batches, long chart publications and retained links.
Fictional observations/summaries and captured mail only; no simulations or real email.
@author ross richardson
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

MODEL=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(MODEL))
from deploy._workflow import frontend_path
FRONT=frontend_path()
sys.path[:0]=[str(FRONT),str(FRONT/'tests'),str(FRONT/'tests/batch')]
SOURCES={
 'JAS-mine-web':('jasmine_web/batch/performance_trace.py','jasmine_web/batch/visualiser.py',
  'jasmine_web/batch/aggregate_io.py','jasmine_web/batch/aggregate_sections.py','jasmine_web/batch/aggregate_limits.py',
  'jasmine_web/batch/preparation_commands.py','jasmine_web/batch/processing_scope.py',
  'tests/batch/test_performance_trace.py','tests/batch/test_performance_exports.py',
  'tests/batch/test_visualiser.py','tests/batch/test_visualiser_automatic.py','tests/batch/test_visualiser_parallel.py'),
 'SimPaths':('deploy/multirun/visualiser_backend.py','deploy/multirun/test_visualiser_backend.py',
  'deploy/multirun/test_visualiser_parallel.py','deploy/multirun/test_visualiser_sections.py',
  'deploy/multirun/test_visualiser_envelopes.py','deploy/multirun/test_visualiser_long_horizons.py',
  'deploy/multirun/visualiser/long_horizon_fixture.cjs','deploy/multirun/chart_capacity_fix_proof.py')}


def fingerprints():
    return {project+'/'+name:hashlib.sha256((root/name).read_bytes()).hexdigest()
        for project,root in (('JAS-mine-web',FRONT),('SimPaths',MODEL)) for name in SOURCES[project]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    build=Path(os.environ.get('SIMPATHS_VISUALISER_TEST_BUILD',''))
    if not args.execute_proof or not os.environ.get('JASMINE_BATCH_TEST_DSN') or not (build/'build.json').is_file():
        parser.error('Use the disposable PostgreSQL runner and qualified Visualiser test build')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    os.nice(15)
    if hasattr(os,'sched_setaffinity'):os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    sources=fingerprints()
    calculation={name:hashlib.sha256((build/name).read_bytes()).hexdigest() for name in ('runner.cjs','calculation.cjs')}
    names=['test_performance_trace','test_performance_exports','test_visualiser.VisualiserTests',
      'test_visualiser_automatic','test_visualiser_parallel.ParallelServiceTests',
      'deploy.multirun.test_visualiser_backend','deploy.multirun.test_visualiser_parallel.ParallelBackendTests',
      'deploy.multirun.test_visualiser_envelopes','deploy.multirun.test_visualiser_sections',
      'deploy.multirun.test_visualiser_long_horizons']
    suite=unittest.defaultTestLoader.loadTestsFromNames(names)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report=dict(passed=result.wasSuccessful() and result.testsRun>0 and not result.skipped,
      tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),skipped=len(result.skipped),
      problems=[dict(test=str(test),traceback=trace) for test,trace in result.failures+result.errors],
      sources=sources,sources_unchanged=sources==fingerprints(),calculation=calculation,
      scientific_simulations=False,real_emails=False)
    report['passed']=report['passed'] and report['sources_unchanged']
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
       'Generated fictional monitoring, retained publication and long-horizon proof evidence.\n@author ross richardson\n-->\n')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
