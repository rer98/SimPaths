"""(C) Copyright 2026, by Ross Richardson

Population campaign, private calibration opt-in and evidence-before-cleanup checks.
Scientific launches are fictional; real annual auditing, archival and disposal run.
@author ross richardson
"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.multirun import research_population_profile as campaign
from deploy.multirun import research_calibration as calibration
from deploy.multirun import research_benchmarks as bench
from deploy.multirun import research_year_profile as years
from deploy.multirun import test_research_benchmarks as benchmark_fixture
from deploy.multirun import test_research_calibration as calibration_fixture
from deploy.multirun.test_research_year_profile import profile_rows
from deploy.multirun.artifacts import ArtifactError, digest, fingerprint, inventory


def prepared_fixture(destination,population,image):
    package=destination/'package';inputs=package/'input';inputs.mkdir(parents=True)
    for name in ('input.mv.db','DatabaseCountryYear.xlsx','EUROMODpolicySchedule.xlsx'):
        (inputs/name).write_text('fictional prepared data '+str(population))
    (package/'model.jar').write_bytes(b'one frozen fictional JAR')
    profile=dict(profile=calibration.load_tool('web-quickstart/prepare_profile.py').profile_for(population,research_calibration=True),
        profile_id=f'private-research-uk-2019-{population}-seed606',actual_counts=dict(person=population,household=500,benefitunit=600),
        calibration_only=True,fresh_jvm_loading_verified=True,simulated_years_run=0,
        jar_sha256=fingerprint(package/'model.jar')['sha256'])
    bench.write_json(package/'profile.json',profile)
    identity=dict(format=calibration.FORMAT,source=calibration.SOURCE,country='UK',start_year=2019,
        population=population,max_end_year=2070,source_image=image,model=fingerprint(package/'model.jar'),
        prepared=inventory(inputs),profile=fingerprint(package/'profile.json'))
    bench.write_json(package/'receipt.json',dict(identity=identity,sha256=digest(identity),revision='calibration-'+digest(identity)))
    bench.write_json(destination/'source-inputs.json',{'public.csv':'a'*64})
    return package


class PopulationProfileTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.frontend=self.root/'frontend';self.frontend.mkdir()
        self.image='sha256:'+'b'*64
        self.reference=prepared_fixture(self.root/'reference',100000,self.image)
        self.args=SimpleNamespace(frontend=self.frontend,output=self.root/'campaign',runtime_from=self.reference,
            image=None,populations=[20000,50000,100000],end_year=2070,repetitions=1,
            cpu_millis=2000,heap_mib=4096,memory_mib=5120,pause_seconds=0)
        self.frozen={str(campaign.ROOT/name):fingerprint(campaign.ROOT/name) for name in (
            'deploy/acceptance/SimPathsYearProfileProbe.java','deploy/multirun/research_year_profile.py')}
        self.frozen[str(campaign.ROOT/'multirun.jar')]=fingerprint(self.reference/'model.jar')

    def plan(self):
        with patch.object(campaign,'source_files',return_value=self.frozen):return campaign.make_plan(self.args)

    def small_plan(self,populations=(1000,2000)):
        self.args.populations=list(populations);self.args.end_year=2020;self.args.repetitions=2
        plan=self.plan()
        # The fictional model is immutable too, but is not the repository's JAR.
        plan['frozen'].pop(str(campaign.ROOT/'multirun.jar'))
        return plan

    def completed_fixture(self,plan,case,prepared):
        staging=self.root/('fixture-'+case['name']);staging.mkdir()
        fixture=benchmark_fixture.BenchmarkTests();fixture.root=staging
        old,_=fixture.fixture('A');completed=Path(case['completed']);completed.parent.mkdir(parents=True,exist_ok=True)
        shutil.move(str(old),completed)
        receipt=calibration.read_calibration(prepared)
        record,_=bench.read_json(completed/'model-proof/report.json')
        config=calibration.configuration(receipt,case['settings']).as_dict()
        record.update(settings=case['settings'],model=plan['model'],image=plan['image'],
            prepared_sha256=receipt['sha256'],output_location=str(completed/'model-proof/retained-output'),
            year_profile_sources={str(Path(n).relative_to(campaign.ROOT)):v for n,v in plan['frozen'].items()},
            year_profile_interval_ms=2000)
        attempt=record['attempts'][0];path=completed/'model-proof/attempts'/attempt['id'];path.mkdir(parents=True)
        rows=profile_rows(population=case['settings']['population'],end_year=2020,repetitions=2)
        years.write_samples(path/'year-samples.jsonl',rows)
        bench.write_json(path/'year-profile.json',years.summarise(rows,population=case['settings']['population'],
            seeds=config['seed_plan']['seeds'],end_year=2020,milestones=[2020]))
        attempt['year_profile']=dict(samples=fingerprint(path/'year-samples.jsonl'),summary=fingerprint(path/'year-profile.json'))
        bench.write_json(completed/'model-proof/configuration.json',config,replace=True)
        bench.write_json(completed/'model-proof/report.json',record,replace=True)
        return path

    def ready_case(self):
        plan=self.small_plan((1000,));case=plan['cases'][0]
        prepared=prepared_fixture(Path(case['preparation']),1000,self.image)
        path=self.completed_fixture(plan,case,prepared)
        with redirect_stdout(io.StringIO()):campaign.audit(prepared,Path(case['completed']),Path(case['audit']))
        return plan,case,prepared,path

    def test_default_plan_runs_populations_sequentially_with_fixed_identical_limits(self):
        original=fingerprint(self.reference/'receipt.json');plan=self.plan()
        self.assertEqual([c['settings']['population'] for c in plan['cases']],[20000,50000,100000])
        for case in plan['cases']:
            selected=case['settings'];command=case['command']
            self.assertEqual((selected['heap_mib'],selected['memory_mib'],selected['cpu_millis']),(4096,5120,2000))
            self.assertEqual(selected['max_memory_mib'],selected['memory_mib'])
            self.assertEqual((selected['max_attempts'],selected['repetitions']),(1,1))
            self.assertIn('--year-profile',command);self.assertIn('--proof-only',command)
            self.assertEqual(command[command.index('--prepared')+1],case['preparation']+'/package')
            self.assertNotIn(str(self.reference),command)
        self.assertFalse(plan['production_changed']);self.assertFalse(plan['limits']['resource_growth'])
        self.assertEqual(plan['runtime_reference_receipt'],original)
        self.assertEqual(fingerprint(self.reference/'receipt.json'),original)
        self.assertFalse(self.args.output.exists())

    def test_real_source_inventory_covers_helpers_model_and_public_inputs_without_preparing(self):
        sources=campaign.source_files(calibration.frontend_path())
        for name in ('multirun.jar','deploy/multirun/schema.py','deploy/multirun/artifacts.py',
                'deploy/multirun/research_population_profile.py','deploy/acceptance/SimPathsYearProfileProbe.java',
                'input/InitialPopulations/training/population_initial_UK_2019.csv'):
            self.assertIn(str(campaign.ROOT/name),sources)
        self.assertEqual(sources[str(campaign.ROOT/'multirun.jar')],fingerprint(campaign.ROOT/'multirun.jar'))
        self.assertFalse(self.args.output.exists())

    def test_duplicate_unbounded_populations_unsafe_output_and_unfinishable_memory_are_rejected(self):
        for populations in ([1000,1000],[999],[100001],[],[True]):
            self.args.populations=populations
            with self.subTest(populations=populations),self.assertRaises(ArtifactError):self.plan()
        self.args.populations=[1000]
        for path in (self.reference/'new',campaign.ROOT/'new-profile',self.root/'missing'/'new',self.root/'reference'):
            self.args.output=path
            with self.subTest(path=path),self.assertRaises(ArtifactError):self.plan()
        self.args.output=self.root/'campaign';self.args.heap_mib=5120
        with self.assertRaises(ArtifactError):self.plan()
        self.args.heap_mib=4096;self.args.image=self.image
        with self.assertRaises(ArtifactError):self.plan()

    def test_source_changes_stop_progress_and_headroom_checks_use_actual_filesystem(self):
        path=self.root/'frozen';path.write_text('same')
        plan=self.plan();plan['frozen']={str(path):fingerprint(path)}
        campaign.unchanged(plan);path.write_text('changed')
        with self.assertRaises(ArtifactError):campaign.unchanged(plan)
        with patch.object(campaign.Path,'read_text',return_value='MemAvailable: 9000000 kB\n'),\
                patch.object(campaign.shutil,'disk_usage',return_value=SimpleNamespace(free=16*campaign.GIB)) as disk:
            result=campaign.preflight(plan);disk.assert_called_with(self.root)
            self.assertEqual(result['required_free_disk_bytes'],15*campaign.GIB)
        with patch.object(campaign.shutil,'disk_usage',return_value=SimpleNamespace(free=14*campaign.GIB)),\
                patch.object(campaign.Path,'read_text',return_value='MemAvailable: 9000000 kB\n'):
            with self.assertRaises(ArtifactError):campaign.preflight(plan)

    def test_real_audit_archive_and_disposal_keep_numeric_profiles_and_external_inputs(self):
        plan,case,prepared,_=self.ready_case();before=inventory(self.reference)
        summary,evidence,_=campaign.archive_profile(plan,case)
        first=summary['repetitions'][0]
        self.assertEqual(first['annual'][0]['exported_person_rows'],1)
        self.assertEqual(first['intervals'][0]['max_exported_person_rows'],1)
        self.assertEqual(campaign.verify_profile_archive(Path(case['archive'])),evidence)
        bench.dispose(Path(case['archive']),Path(case['completed']))
        result=campaign.retire_prepared(plan,case)
        self.assertEqual(len(result['removed']),3);self.assertTrue(result['model_retained'])
        self.assertFalse((prepared/'input').exists());self.assertTrue((prepared/'model.jar').is_file())
        self.assertTrue((prepared/'profile.json').is_file());self.assertTrue((prepared/'receipt.json').is_file())
        self.assertEqual(inventory(self.reference),before)
        self.assertEqual(len(years.read_samples(Path(case['archive'])/'year-samples.jsonl')),len(profile_rows(end_year=2020)))
        self.assertFalse(bench.output_files(Path(case['completed'])/'model-proof/retained-output'))

    def test_changed_original_summary_or_sample_limits_blocks_archival(self):
        plan,case,_,path=self.ready_case()
        report=Path(case['completed'])/'model-proof/report.json';record,_=bench.read_json(report)
        summary,_=bench.read_json(path/'year-profile.json');summary['population_requested']=2000
        bench.write_json(path/'year-profile.json',summary,replace=True)
        record['attempts'][0]['year_profile']['summary']=fingerprint(path/'year-profile.json')
        bench.write_json(report,record,replace=True)
        with self.assertRaisesRegex(ArtifactError,'Original year summary'):campaign.archive_profile(plan,case)
        self.assertFalse(Path(case['archive']).exists())
        rows=years.read_samples(path/'year-samples.jsonl')
        for row in rows:row['container_limit_bytes']=6144*campaign.MIB
        (path/'year-samples.jsonl').unlink();years.write_samples(path/'year-samples.jsonl',rows)
        summary=years.summarise(rows,population=1000,seeds=['606','607'],end_year=2020,milestones=[2020])
        bench.write_json(path/'year-profile.json',summary,replace=True)
        record['attempts'][0]['year_profile']=dict(samples=fingerprint(path/'year-samples.jsonl'),summary=fingerprint(path/'year-profile.json'))
        bench.write_json(report,record,replace=True)
        with self.assertRaisesRegex(ArtifactError,'telemetry limits'):campaign.archive_profile(plan,case)

    def test_corrupt_numeric_archive_prevents_generated_input_deletion(self):
        plan,case,prepared,_=self.ready_case();campaign.archive_profile(plan,case)
        inputs=inventory(prepared/'input')
        for name in ('year-samples.jsonl','year-profile.json','population-profile.json','archive.json'):
            target=Path(case['archive'])/name;original=target.read_bytes();target.write_bytes(original+b' ')
            with self.subTest(name=name),self.assertRaises(ArtifactError):campaign.retire_prepared(plan,case)
            self.assertEqual(inventory(prepared/'input'),inputs);target.write_bytes(original)

    def test_external_or_changed_prepared_files_are_never_retired(self):
        plan,case,prepared,_=self.ready_case();campaign.archive_profile(plan,case)
        external=dict(case,preparation=str(self.reference.parent))
        with self.assertRaises(ArtifactError):campaign.retire_prepared(plan,external)
        original=inventory(prepared/'input');target=prepared/'input/input.mv.db';target.write_text('changed')
        with self.assertRaises(ArtifactError):campaign.retire_prepared(plan,case)
        self.assertEqual(set(inventory(prepared/'input')),set(original))

    def test_whole_sequence_runs_real_small_audits_and_reclaims_before_preparing_next_population(self):
        plan=self.small_plan();order=[];original_reference=inventory(self.reference)
        def prepare(destination,population,image):
            if order:
                previous=plan['cases'][0]
                self.assertFalse((Path(previous['preparation'])/'package/input').exists())
                self.assertFalse(bench.output_files(Path(previous['completed'])/'model-proof/retained-output'))
            order.append(('prepare',population));return prepared_fixture(destination,population,image)
        def run(command,log,env):
            case=next(c for c in plan['cases'] if str(c['completed'])==command[command.index('--output')+1])
            order.append(('run',case['settings']['population']))
            self.assertEqual(env['PIP_NO_INPUT'],'1')
            self.completed_fixture(plan,case,Path(case['preparation'])/'package')
        with patch.object(campaign,'preflight',return_value={}),patch.object(calibration,'installed_image',return_value=self.image),\
                redirect_stdout(io.StringIO()):
            result=campaign.execute(plan,runner=run,prepare=prepare,pause=lambda _:None)
        self.assertTrue(result['passed']);self.assertEqual(order,[('prepare',1000),('run',1000),('prepare',2000),('run',2000)])
        self.assertEqual(len(result['completed']),2)
        self.assertEqual(inventory(self.reference),original_reference)
        self.assertTrue((self.args.output/'population-resources.csv').is_file())
        self.assertTrue((self.args.output/'comparison.md').is_file())
        self.assertEqual([s['stage'] for s in result['steps'][:6]],
            ['prepare','run','audit','archive','dispose-output','retire-generated-inputs'])

    def test_failed_launch_preserves_generated_inputs_and_stops_later_cases(self):
        plan=self.small_plan();prepared=[]
        def prepare(destination,population,image):
            path=prepared_fixture(destination,population,image);prepared.append(path);return path
        def fail(*_):raise RuntimeError('fictional failed launch')
        with patch.object(campaign,'preflight',return_value={}),patch.object(calibration,'installed_image',return_value=self.image),\
                redirect_stdout(io.StringIO()),self.assertRaises(RuntimeError):
            campaign.execute(plan,runner=fail,prepare=prepare)
        report,_=bench.read_json(self.args.output/'report.json')
        self.assertFalse(report['passed']);self.assertEqual(report['failure']['error_type'],'RuntimeError')
        self.assertEqual(len(prepared),1);self.assertTrue((prepared[0]/'input/input.mv.db').exists())
        self.assertFalse(Path(plan['cases'][1]['preparation']).exists())

    def test_comparison_reports_only_numeric_counts_and_checksums_not_fictional_raw_values(self):
        plan,case,_,_=self.ready_case();summary,_,_=campaign.archive_profile(plan,case)
        campaign.write_comparison(self.args.output,[summary])
        text=(self.args.output/'population-resources.csv').read_text()
        self.assertIn('max_exported_person_rows',text);self.assertIn('interval_gc_seconds',text)
        self.assertNotIn('fictional_value',text)
        a=calibration.configuration(calibration.read_calibration(self.reference),
            {**case['settings'],'population':100000}).as_dict()
        b=deepcopy(a);b['common']['population']=20000
        self.assertEqual(campaign.controls(a),campaign.controls(b))
        b['run_sets'][0]['model_args']['savingRate']=0.04
        self.assertNotEqual(campaign.controls(a),campaign.controls(b))


class CalibrationYearOptInTests(unittest.TestCase):
    def setUp(self):
        self.fixture=calibration_fixture.ResearchCalibrationTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_only_explicit_fixed_test_mode_changes_runner_and_keeps_original_input_bytes(self):
        fixture=self.fixture;fixture.plan=calibration_fixture.settings(max_attempts=1,max_memory_mib=5120)
        _,lease=fixture.adapter_lease()
        adapter=calibration.ResearchAdapter(fixture.prepared,fixture.classes,fixture.plan,year_profile=True)
        before=inventory(fixture.prepared)
        with patch('deploy.multirun.queue_adapter.shutil.disk_usage',return_value=SimpleNamespace(free=100*calibration.GIB)):
            adapter.container_command(lease,fixture.request)
        script=(fixture.request/'run.sh').read_text()
        self.assertIn('SimPathsYearProfileProbe',script)
        self.assertIn('-XX:ActiveProcessorCount=2',script)
        self.assertEqual(inventory(fixture.prepared),before)
        for changes in (dict(max_attempts=2),dict(max_memory_mib=6144),dict(repetitions=3)):
            with self.subTest(changes=changes),self.assertRaises(ArtifactError):
                calibration.ResearchAdapter(fixture.prepared,fixture.classes,{**fixture.plan,**changes},year_profile=True)
        with self.assertRaises(ArtifactError):calibration.ResearchAdapter(fixture.prepared,fixture.classes,fixture.plan,year_profile=1)

    def test_rotated_year_stream_is_incremental_deduplicated_and_rejects_missing_samples(self):
        rows=profile_rows(end_year=2020,repetitions=1);entry={}
        def lines(selected):
            return '\n'.join(f'2026-10-15T10:00:{r["sequence"]:02d}.000000000Z '+years.PREFIX+json.dumps(r) for r in selected)
        calibration.collect_observations(entry,lines(rows[:5]))
        calibration.collect_observations(entry,lines(rows[4:]))
        self.assertEqual(entry['year_rows'],rows)
        calibration.collect_observations(entry,lines(rows[-1:]))
        self.assertEqual(entry['year_rows'],rows)
        fresh={};calibration.collect_observations(fresh,lines(rows[:2]))
        with self.assertRaises(ValueError):calibration.collect_observations(fresh,lines(rows[3:]))

    def test_cli_forwards_explicit_mode_and_proof_only_to_the_real_disposable_driver(self):
        fixture=self.fixture
        frontend=fixture.root/'frontend';frontend.mkdir()
        for enabled in (False,True):
            argv=['research_calibration.py','--frontend',str(frontend),'--prepared',str(fixture.prepared),
                '--image',fixture.image,'--max-attempts','1','--max-memory-mib','5120',
                '--output',str(fixture.root/('native-'+str(enabled)))]
            if enabled:argv+=['--year-profile','--proof-only']
            with patch.object(sys,'argv',argv),patch.object(calibration,'preflight',return_value={}),\
                    patch.object(calibration,'installed_image',return_value=fixture.image),\
                    patch.object(calibration.subprocess,'call',return_value=0) as child:
                self.assertEqual(calibration.main(),0)
            command=child.call_args.args[0];env=child.call_args.kwargs['env']
            self.assertEqual('--proof-only' in command,enabled)
            self.assertIn('--proof-script',command)
            self.assertEqual(json.loads(env[calibration.ENV]).get('year_profile',False),enabled)
            self.assertEqual(json.loads(env[calibration.ENV])['settings']['max_attempts'],1)


if __name__=='__main__':unittest.main()
