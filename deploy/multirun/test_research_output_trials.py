"""(C) Copyright 2026, by Ross Richardson

Paired collector controls, real small-file audits, numeric evidence and cleanup.
Scientific launches are fictional; production archival and disposal run locally.
@author ross richardson
"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.multirun import research_output_trials as trials
from deploy.multirun import research_calibration as calibration
from deploy.multirun import research_population_profile as population
from deploy.multirun import research_benchmarks as bench
from deploy.multirun import test_research_population_profile as profile_fixture
from deploy.multirun.artifacts import ArtifactError, inventory


class OptionalOutputTests(unittest.TestCase):
    def setUp(self):
        self.fixture=profile_fixture.PopulationProfileTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root
        self.args=SimpleNamespace(frontend=self.fixture.frontend,output=self.root/'output',
            runtime_from=self.fixture.reference,population=20000,end_year=2021,rounds=3,pause_seconds=0)

    def plan(self):
        with patch.object(population,'source_files',return_value=deepcopy(self.fixture.frozen)):
            plan=trials.make_plan(self.args)
        # The fictional JAR belongs to the fixture, not to the source checkout.
        plan['frozen'].pop(str(population.ROOT/'multirun.jar'))
        return plan

    def prepare(self,destination,count,image):
        from deploy.multirun.test_research_population_profile import prepared_fixture
        return prepared_fixture(destination,count,image)

    def completed(self,plan,case,*,calculated=None):
        self.fixture.completed_fixture(plan,case,Path(case['preparation'])/'package')
        output=Path(case['completed'])/'model-proof/retained-output'
        enabled=case['mode']=='on' if calculated is None else calculated
        for path in output.glob('*/csv/WealthIncomeStatistics.csv'):
            path.write_text('time,'+','.join(trials.GINI_FIELDS)+'\n'+''.join(
                f'{year},'+('0.42,0.31' if enabled else '0.0,0.0')+'\n' for year in range(2019,case['settings']['end_year']+1)))
        native=Path(case['completed'])/'model-proof/report.json'
        record,_=bench.read_json(native);manifest=inventory(output)
        record['output_bytes']=sum(f['bytes'] for f in manifest.values())
        bench.write_json(Path(case['completed'])/'model-proof/output-manifest.json',manifest,replace=True)
        bench.write_json(native,record,replace=True)

    def execute(self,plan,*,bad_gini=False):
        def run(command,log,env):
            case=next(c for c in plan['cases'] if c['completed']==command[command.index('--output')+1])
            self.assertEqual(env['PIP_NO_INPUT'],'1')
            self.completed(plan,case,calculated=False if bad_gini else None)
        with patch.object(population,'preflight',return_value={}),\
                patch.object(calibration,'installed_image',return_value=self.fixture.image),redirect_stdout(io.StringIO()):
            return trials.execute(plan,runner=run,prepare=self.prepare,pause=lambda _:None)

    def test_plan_alternates_order_reuses_preparation_and_fixes_all_other_limits(self):
        plan=self.plan()
        self.assertEqual([(c['round'],c['mode']) for c in plan['cases']],
            [(1,'off'),(1,'on'),(2,'on'),(2,'off'),(3,'off'),(3,'on')])
        self.assertEqual(len({c['preparation'] for c in plan['cases']}),1)
        self.assertEqual(sum(c['retire_prepared'] for c in plan['cases']),1)
        self.assertFalse(plan['production_changed'])
        for case in plan['cases']:
            self.assertEqual(case['settings'],plan['cases'][0]['settings'])
            self.assertEqual((case['settings']['heap_mib'],case['settings']['memory_mib']),(2560,3584))
            self.assertEqual((case['settings']['cpu_millis'],case['settings']['repetitions'],case['settings']['max_attempts']),(2000,1,1))
            self.assertEqual(case['settings']['memory_mib'],case['settings']['max_memory_mib'])
            self.assertEqual(case['command'][case['command'].index('--variant')+1],case['variant'])
            self.assertEqual(case['command'][case['command'].index('--repetition-seconds')+1],'1800')
            self.assertIn('--year-profile',case['command'])

    def test_plan_uses_the_measured_profile_for_a_larger_followup(self):
        for count,year,limits in ((50000,2021,(3072,4096)),(100000,2021,(3072,4096)),(100000,2070,(3584,4608))):
            self.args.population=count;self.args.end_year=year
            selected=self.plan()['cases'][0]['settings']
            self.assertEqual((selected['heap_mib'],selected['memory_mib']),limits)
        for rounds in (0,4,True):
            self.args.rounds=rounds
            with self.assertRaises(ArtifactError):self.plan()

    def test_real_campaign_audits_and_archives_every_condition_before_disposal(self):
        reference=inventory(self.fixture.reference);plan=self.plan()
        result=self.execute(plan)
        self.assertTrue(result['passed']);self.assertEqual(len(result['conditions']),6)
        self.assertEqual(len(result['paired_results']),3)
        self.assertEqual([s['observations'] for s in result['summary']],[3,3])
        self.assertEqual(inventory(self.fixture.reference),reference)
        for case in plan['cases']:
            population.verify_profile_archive(Path(case['archive']))
            self.assertFalse(bench.output_files(Path(case['completed'])/'model-proof/retained-output'))
        self.assertFalse((Path(plan['cases'][0]['preparation'])/'package/input').exists())
        html=(self.args.output/'comparison.html').read_text()
        for heading in ('Annual work (s)','Peak working RAM (GiB)','Matched pairs','Median','Summary'):
            self.assertIn(heading.lower(),html.lower())
        self.assertEqual(len((self.args.output/'comparison.csv').read_text().splitlines()),7)
        for row in result['conditions']:
            self.assertEqual(row['configuration']['seed_plan']['seeds'],['606'])
            self.assertEqual(row['gini']['enabled'],row['mode']=='on')

    def test_actual_gini_values_are_required_and_failure_keeps_current_outputs(self):
        self.args.rounds=1;plan=self.plan()
        with self.assertRaisesRegex(ArtifactError,'actual Gini statistics'):self.execute(plan,bad_gini=True)
        native=json.loads((self.args.output/'report.json').read_text())
        self.assertFalse(native['passed']);self.assertEqual(len(native['conditions']),1)
        failed=plan['cases'][1]
        self.assertTrue(bench.output_files(Path(failed['completed'])/'model-proof/retained-output'))
        self.assertFalse(Path(failed['archive']).exists())
        self.assertTrue((Path(failed['preparation'])/'package/input/input.mv.db').exists())

    def test_pairs_reject_changed_settings_sources_seeds_or_extra_output_flags(self):
        result=self.execute(self.plan());rows=result['conditions'][:2]
        for modify in (
                lambda r:r['identity']['settings'].update(cpu_millis=1000),
                lambda r:r['identity'].update(image='sha256:'+'c'*64),
                lambda r:r['configuration']['seed_plan'].update(seeds=['608']),
                lambda r:r['configuration']['run_sets'][0]['collector_args'].update(exportToDatabase=True)):
            altered=deepcopy(rows);modify(altered[1])
            with self.assertRaisesRegex(ArtifactError,'differ beyond the Gini flag'):trials.paired_results(altered)

    def test_gini_reader_rejects_missing_years_columns_and_nonfinite_values(self):
        plan=self.plan();case=plan['cases'][0]
        self.prepare(Path(case['preparation']),20000,self.fixture.image);self.completed(plan,case)
        path=next((Path(case['completed'])/'model-proof/retained-output').glob('*/csv/WealthIncomeStatistics.csv'))
        header='time,'+','.join(trials.GINI_FIELDS)+'\n'
        for content in ('time,unknown\n2019,0\n',header+'2019,0,0\n',
                header+'2019,NaN,0\n2020,0,0\n2021,0,0\n'):
            path.write_text(content)
            with self.assertRaises(ArtifactError):trials.gini_output(case['completed'],enabled=False,end_year=2021)

    def test_existing_population_audits_require_explicit_gini_opt_in(self):
        plan=self.plan();case=plan['cases'][1]
        package=self.prepare(Path(case['preparation']),20000,self.fixture.image)
        self.completed(plan,case)
        completed=Path(case['completed'])
        with self.assertRaisesRegex(ArtifactError,'does not match'):
            population.audit(package,completed,Path(case['audit']))
        with redirect_stdout(io.StringIO()):
            result=population.audit(package,completed,Path(case['audit']),variant='baseline-gini')
        self.assertTrue(result['passed'])


if __name__=='__main__':unittest.main()
