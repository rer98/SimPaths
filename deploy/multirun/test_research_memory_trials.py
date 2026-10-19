"""(C) Copyright 2026, by Ross Richardson

Smaller-memory campaign planning, real fictional-file archives and failure guards.
No scientific models, PostgreSQL, Docker, browsers or external messages are used.
@author ross richardson
"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from . import research_population_profile as campaign
from . import research_calibration as calibration
from . import research_benchmarks as bench
from . import research_year_profile as years
from .artifacts import ArtifactError, inventory
from . import test_research_population_profile as population_fixtures
from .test_research_year_profile import profile_rows

prepared_fixture=population_fixtures.prepared_fixture


class MemoryTrialCampaignTests(unittest.TestCase):
    def setUp(self):
        self.f=population_fixtures.PopulationProfileTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.args.memory_trials=True;self.f.args.repetitions=2;self.f.args.end_years=[2035,2070]

    def plan(self,populations=(20000,)):
        self.f.args.populations=list(populations)
        result=deepcopy(self.f.plan())
        result['frozen'].pop(str(campaign.ROOT/'multirun.jar'))
        return result

    def failed_fixture(self,plan,case,*,outcome='heap_limit',cleanup=True):
        root=Path(case['completed']);proof=root/'model-proof';key='11111111-1111-1111-1111-111111111111'
        attempts=proof/'attempts'/key;attempts.mkdir(parents=True)
        rows=profile_rows(population=case['settings']['population'],end_year=case['settings']['end_year'],
            heap_mib=case['settings']['heap_mib'],memory_mib=case['settings']['memory_mib'])[:5]
        years.write_samples(attempts/'year-samples.jsonl',rows)
        (attempts/'execution.log').write_text('fictional fixed-limit failure, private diagnostic')
        package=Path(case['preparation'])/'package';receipt=calibration.read_calibration(package)
        record=dict(passed=False,cleanup=cleanup,source_unchanged=True,defaults_changed=False,production_acceptance=False,
            settings=case['settings'],model=plan['model'],image=plan['image'],variant='baseline',
            prepared_sha256=receipt['sha256'],failure=dict(stage='full-simulation',error_type='AssertionError',
                message=calibration.INCOMPLETE_CALIBRATION),
            history=[dict(id=key,phase='finished',outcome=outcome)],resource_events=[],
            memory_failure=dict(attempt_id=key,outcome=outcome,reservation_released=True,
                partial_results_published=False,saved_files=inventory(attempts)))
        bench.write_json(root/'report.json',dict(passed=False,cleanup=cleanup))
        bench.write_json(proof/'report.json',record)
        return record

    def execute(self,plan,runner):
        with patch.object(campaign,'preflight',return_value={}),\
                patch.object(calibration,'installed_image',return_value=self.f.image),redirect_stdout(io.StringIO()):
            return campaign.execute(plan,runner=runner,prepare=prepared_fixture)

    def test_plan_selects_six_distinct_profiles_and_two_repetitions_per_jvm(self):
        plan=self.plan((20000,50000,100000))
        self.assertEqual(len(plan['cases']),6)
        for case in plan['cases']:
            s=case['settings'];expected=campaign.MEMORY_CANDIDATES[s['population'],s['end_year']]
            self.assertEqual((s['heap_mib'],s['memory_mib']),expected)
            self.assertEqual((s['repetitions'],s['cpu_millis'],s['max_attempts']),(2,2000,1))
            self.assertEqual(s['memory_mib'],s['max_memory_mib'])
            self.assertEqual(s['storage_mib'],12288)
            command=case['command']
            self.assertEqual(command[command.index('--repetitions')+1],'2')
            self.assertIn('--year-profile',command);self.assertIn('--proof-only',command)
        self.assertIn('one JVM',plan['repetition_note'])
        self.assertFalse(plan['production_changed'])

    def test_ambiguous_custom_sizes_unsupported_cases_and_one_repetition_are_rejected(self):
        for change in (dict(repetitions=1),dict(heap_mib=3072),dict(memory_mib=4096),
                dict(populations=[1000]),dict(end_years=[2021])):
            original=deepcopy(self.f.args)
            self.f.args.__dict__.update(change)
            with self.subTest(change=change),self.assertRaises(ArtifactError):self.f.plan()
            self.f.args=original

    def test_selected_subset_can_later_qualify_a_lower_cpu_quota_without_changing_the_matrix(self):
        self.f.args.cpu_millis=1500
        plan=self.plan((50000,))
        self.assertEqual([c['settings']['cpu_millis'] for c in plan['cases']],[1500,1500])
        self.assertEqual([c['settings']['repetitions'] for c in plan['cases']],[2,2])
        self.assertEqual([c['settings']['memory_mib'] for c in plan['cases']],[4096,4096])

    def test_successful_cases_archive_same_jvm_reports_and_retire_only_after_the_last_horizon(self):
        plan=self.plan();started=[]
        def run(command,log,env):
            case=next(c for c in plan['cases'] if c['completed']==command[command.index('--output')+1])
            package=Path(case['preparation'])/'package'
            self.assertTrue((package/'input/input.mv.db').is_file())
            started.append(case['name']);self.f.completed_fixture(plan,case,package)
        result=self.execute(plan,run)
        self.assertTrue(result['passed']);self.assertTrue(result['campaign_completed'])
        self.assertEqual(len(started),2);self.assertEqual(result['rejected'],[])
        for p in result['profiles']:
            self.assertTrue(p['repeat_check']['single_jvm_verified'])
            self.assertEqual([r['seed'] for r in p['repeat_check']['repetitions']],['606','607'])
            self.assertEqual(p['container_limit_bytes'],3584*campaign.MIB)
        self.assertFalse((Path(plan['cases'][0]['preparation'])/'package/input').exists())
        self.assertIn('PASS: both repetitions',(self.f.args.output/'memory-comparison.html').read_text())

    def test_a_confirmed_memory_failure_is_preserved_then_the_next_independent_case_runs(self):
        plan=self.plan();launched=[];failed_before={}
        def run(command,log,env):
            case=next(c for c in plan['cases'] if c['completed']==command[command.index('--output')+1])
            launched.append(case['name'])
            if len(launched)==1:
                self.failed_fixture(plan,case)
                failed_before.update(inventory(Path(case['completed'])))
                raise ArtifactError('fictional native command failure')
            self.f.completed_fixture(plan,case,Path(case['preparation'])/'package')
        result=self.execute(plan,run)
        self.assertFalse(result['passed']);self.assertTrue(result['campaign_completed'])
        self.assertEqual(len(result['completed']),2);self.assertEqual(len(result['profiles']),1)
        self.assertEqual(len(result['rejected']),1)
        self.assertEqual(inventory(Path(plan['cases'][0]['completed'])),failed_before)
        failure=campaign.verify_failed_memory_archive(Path(plan['cases'][0]['archive']))
        self.assertEqual(failure['outcome'],'heap_limit')
        self.assertFalse((Path(plan['cases'][1]['preparation'])/'package/input').exists())
        self.assertIn('FAILED: heap_limit',(self.f.args.output/'memory-comparison.html').read_text())

    def test_all_memory_failures_finish_with_no_false_pass_and_reclaim_their_own_verified_inputs(self):
        plan=self.plan()
        def run(command,log,env):
            case=next(c for c in plan['cases'] if c['completed']==command[command.index('--output')+1])
            self.failed_fixture(plan,case,outcome='memory_limit')
            raise ArtifactError('fictional memory limit')
        result=self.execute(plan,run)
        self.assertFalse(result['passed']);self.assertTrue(result['campaign_completed'])
        self.assertEqual(len(result['rejected']),2);self.assertEqual(result['profiles'],[])
        self.assertFalse((Path(plan['cases'][0]['preparation'])/'package/input').exists())
        for case in plan['cases']:campaign.verify_failed_memory_archive(Path(case['archive']))

    def test_unknown_failure_and_uncertain_cleanup_stop_without_retiring_inputs_or_launching_more(self):
        for outcome,cleanup in (('transient',True),('heap_limit',False)):
            self.f.args.output=self.f.root/f'unsafe-{outcome}-{cleanup}'
            plan=self.plan();started=[]
            def run(command,log,env):
                case=plan['cases'][0];started.append(case['name'])
                self.failed_fixture(plan,case,outcome=outcome,cleanup=cleanup)
                raise ArtifactError('fictional uncertain failure')
            with self.subTest(outcome=outcome,cleanup=cleanup),self.assertRaises(ArtifactError):self.execute(plan,run)
            self.assertEqual(len(started),1)
            self.assertTrue((Path(plan['cases'][0]['preparation'])/'package/input/input.mv.db').is_file())
            self.assertFalse(Path(plan['cases'][1]['completed']).exists())

    def test_changed_failed_evidence_blocks_archival_and_input_retirement(self):
        plan=self.plan();case=plan['cases'][0]
        prepared_fixture(Path(case['preparation']),20000,self.f.image)
        self.failed_fixture(plan,case)
        log=next((Path(case['completed'])/'model-proof/attempts').glob('*/execution.log'))
        log.write_text('changed')
        with self.assertRaises(ArtifactError):campaign.archive_memory_failure(plan,case)
        self.assertTrue((Path(case['preparation'])/'package/input').is_dir())

    def test_an_error_during_failure_cleanup_is_not_hidden_by_an_earlier_memory_failure(self):
        plan=self.plan();case=plan['cases'][0]
        prepared_fixture(Path(case['preparation']),20000,self.f.image)
        record=self.failed_fixture(plan,case)
        record['failure'].update(error_type='RuntimeError',message='fictional browser did not stop')
        bench.write_json(Path(case['completed'])/'model-proof/report.json',record,replace=True)
        with self.assertRaises(ArtifactError):campaign.archive_memory_failure(plan,case)
        self.assertTrue((Path(case['preparation'])/'package/input').is_dir())

    def test_failed_archive_reverification_precedes_deleting_generated_inputs(self):
        plan=self.plan();case=plan['cases'][0]
        prepared_fixture(Path(case['preparation']),20000,self.f.image)
        self.failed_fixture(plan,case)
        campaign.archive_memory_failure(plan,case)
        log=next((Path(case['archive'])/'model-proof/attempts').glob('*/execution.log'))
        log.write_text('changed archived diagnostics')
        with self.assertRaises(ArtifactError):campaign.retire_prepared(plan,case,failed_memory_trial=True)
        self.assertTrue((Path(case['preparation'])/'package/input/input.mv.db').is_file())

    def test_smaller_profile_cannot_be_archived_without_verified_single_jvm_identity(self):
        plan=self.plan();case=plan['cases'][0]
        package=prepared_fixture(Path(case['preparation']),20000,self.f.image)
        self.f.completed_fixture(plan,case,package)
        record,_=bench.read_json(Path(case['completed'])/'model-proof/report.json')
        record['attempts'][0].pop('single_jvm_verified')
        bench.write_json(Path(case['completed'])/'model-proof/report.json',record,replace=True)
        with redirect_stdout(io.StringIO()):campaign.audit(package,Path(case['completed']),Path(case['audit']))
        with self.assertRaises(ArtifactError):campaign.archive_profile(plan,case)
        self.assertTrue((package/'input').is_dir());self.assertFalse(Path(case['archive']).exists())


if __name__=='__main__':unittest.main()
