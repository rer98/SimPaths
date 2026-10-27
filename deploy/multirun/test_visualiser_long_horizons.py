"""(C) Copyright 2026, by Ross Richardson
Long-horizon publications reuse pinned calculations and keep selected views bounded.
Fictional annual summaries only: no scientific simulation or real user data.
@author ross richardson
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from .visualiser_backend import VisualiserBackend
from .test_visualiser_envelopes import LONG_LIMITS
from deploy._workflow import frontend_path
import sys
sys.path.insert(0,str(frontend_path()))
from jasmine_web.batch.aggregate_sections import build as index_publication,VIEW_LIMIT,VIEW_ROWS
from jasmine_web.batch.visualiser import validate_publication,publication_bytes
from jasmine_web.batch.preparation_commands import PreparationCommands
from jasmine_web.batch.policy import Resources

BUILD=os.environ.get('SIMPATHS_VISUALISER_TEST_BUILD')
FIXTURE=Path(__file__).parent/'visualiser/long_horizon_fixture.cjs'


@unittest.skipUnless(BUILD,'Use the qualified pinned test build')
class LongHorizonTests(unittest.TestCase):
    def check_horizon(self,end,repetitions):
        with tempfile.TemporaryDirectory(prefix='long-chart-proof-') as temporary:
            root=Path(temporary);metrics=root/'metrics';metrics.mkdir(mode=0o700)
            generated=subprocess.run(['node','--max-old-space-size=1536',str(FIXTURE),str(metrics),str(end),str(repetitions)],
                text=True,capture_output=True,timeout=120,check=True)
            fixture=json.loads(generated.stdout)
            backend=VisualiserBackend(BUILD,root,memory_mib=1792)
            work=root/'work';work.mkdir(mode=0o700)
            sources=[dict(configuration=dict(id='baseline',role='Baseline',runs=[dict(seed=row['seed']) for row in fixture['metrics']]))]
            # These files represent already completed, exact per-seed summaries.
            # Any raw parser invocation is a test failure.
            def cached(tasks,command,progress,*args):
                self.assertEqual(len(tasks),repetitions)
                for count in range(1,repetitions+1):progress(count)
                return fixture['metrics']
            commands=PreparationCommands(work,timeout=shutil.which('timeout'),runtime=120,
                check=lambda:None,stop=threading.Event(),workers=2)
            progress=[]
            with patch.object(backend,'_seeds',side_effect=cached),patch.object(backend,'_seed',side_effect=AssertionError('Raw reparsing is forbidden')):
                with commands:
                    files=backend.process_files(sources,work,commands,progress.append,seed_workers=2,
                        resource_budget=Resources(2000,5120,2048))
            self.assertEqual(progress,list(range(1,repetitions+1)))
            self.assertFalse(commands.children)
            rows=json.loads(files.paths[0].read_bytes())
            value=dict(rows=rows,notice=files.notice,comparison_available=files.comparison_available)
            with self.assertRaises(ValueError):validate_publication(value)
            self.assertIs(validate_publication(value,limits=LONG_LIMITS),value)
            self.assertEqual(len(rows),fixture['rows'])
            public=dict(format='simpaths.visualiser.v1',configurations=[dict(id='baseline',name='Reference',role='Baseline')],
                seeds=[row['seed'] for row in fixture['metrics']],data=value)
            encoded=publication_bytes(public,limits=LONG_LIMITS)
            self.assertGreater(len(encoded),16*1024**2);self.assertLess(len(encoded),LONG_LIMITS.artifact_bytes)
            indexed=root/'indexed';indexed.mkdir(mode=0o700)
            fd=os.open(indexed,os.O_RDONLY|os.O_DIRECTORY)
            try:receipt=index_publication(public,hashlib.sha256(encoded).hexdigest(),fd)
            finally:os.close(fd)
            self.assertGreater(receipt['sections'],1)
            catalogue=json.loads((indexed/'index.json').read_bytes())['catalogue']
            self.assertEqual(catalogue['limits'],dict(response_bytes=VIEW_LIMIT,response_rows=VIEW_ROWS))
            mcs=next(variable for variable in catalogue['variables'] if variable['name']=='Mental Component Summary (MCS)')
            self.assertEqual(mcs['years'][0],2019);self.assertEqual(mcs['years'][-1],end)
            section=next(v for v in mcs['views'] if v['stratifier']=='Overall' and v['kind']=='levels')
            self.assertLess(section['bytes'],VIEW_LIMIT);self.assertLess(section['rows'],VIEW_ROWS)
            mean=next(row for row in rows if row['variable']=='Mental Component Summary (MCS)'
                and row['stratifier']=='Overall' and row['metric_type']=='mean')
            self.assertEqual(mean['n_runs'],repetitions)

    def test_seventeen_years_ten_repetitions_finish_without_raw_reparsing(self):self.check_horizon(2035,10)
    def test_fifty_two_years_two_repetitions_finish_without_raw_reparsing(self):self.check_horizon(2070,2)


if __name__=='__main__':unittest.main()
