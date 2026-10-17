"""(C) Copyright 2026, by Ross Richardson

Check repetition trial isolation, reproducibility, cleanup and tiny real chart
regeneration. Resource measurements require the separate native Docker run.
@author ross richardson
"""
import copy
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy.multirun import visualiser_repetition_trials as driver
from deploy.multirun import visualiser_repetition_worker as worker
from deploy.multirun import visualiser_capacity_worker as api
from deploy.multirun.artifacts import fingerprint
from deploy.multirun.visualiser_summary_cache import SummaryCache

BUILD = os.environ.get('SIMPATHS_VISUALISER_TEST_BUILD')
FRONTEND = Path(os.environ.get('SIMPATHS_VISUALISER_TEST_FRONTEND','/home/rer/git/JAS-mine/JAS-mine-web'))


def passed_worker(counts=None,rounds=1):
    counts = [2,5,10] if counts is None else counts
    cases = []
    for r,n in worker.trial_order(counts,rounds):
        cases.append(dict(round=r,repetitions=n,passed=True,cleanup=True,regeneration_seconds=10+n,
            stages={s:dict(passed=True,elapsed_seconds=.5) for s in worker.STAGES},
            result_identity=dict(repetitions=n,sha256='a'*64),publication=dict(bytes=1024),
            index=dict(section_bytes=2048,index_bytes=64)))
    return dict(passed=True,cleanup=True,cases=cases)


class RepetitionTrialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.args = SimpleNamespace(image_id='sha256:'+'a'*64,image='test',memory_mib=3072,cpu_millis=1000,
            repetitions=[2,5,10],rounds=1,timeout_seconds=60)

    def test_order_rotates_each_count_through_each_position(self):
        plan = worker.trial_order([2,5,10],3)
        self.assertEqual(plan,[(1,2),(1,5),(1,10),(2,5),(2,10),(2,2),(3,10),(3,2),(3,5)])
        for n in (2,5,10):
            self.assertEqual(sorted(i % 3 for i,(_,count) in enumerate(plan) if n==count),[0,1,2])

    def test_invalid_counts_and_rounds_are_rejected(self):
        for counts,rounds in [([2],1),([2,2],1),([1,10],1),([2,11],1),([True,5],1),([2,5],0),([2,5],6)]:
            with self.subTest(counts=counts,rounds=rounds), self.assertRaises(ValueError):
                worker.trial_order(counts,rounds)

    def test_fixture_selection_preserves_source_and_paired_seed_plan(self):
        original = dict(mode='summary-replay',configurations=[dict(id=c,runs=[
            dict(seed='606',source_seed='606',summary=c+'.json'),
            dict(seed='607',source_seed='607',summary=c+'-second.json')]) for c in ('baseline','scenario')])
        before = copy.deepcopy(original)
        selected = worker.selected_fixture(original,5)
        self.assertEqual(original,before)
        self.assertEqual(selected['seeds'],[str(900000001+i) for i in range(5)])
        for c in selected['configurations']:
            self.assertEqual([r['seed'] for r in c['runs']],selected['seeds'])
            self.assertEqual([r['source_seed'] for r in c['runs']],['606','607','606','607','606'])
        with self.assertRaisesRegex(ValueError,'summary-replay'):
            worker.selected_fixture(dict(original,mode='raw-native'),5)
        with self.assertRaises(ValueError):
            worker.selected_fixture(dict(original,configurations=original['configurations']*2),5)

    def test_one_fixed_container_is_network_free_read_only_and_bounded(self):
        self.args.cpu_millis=2000
        value = driver.options(self.args,self.root/'snapshot',{},self.root/'out','identity')
        self.assertEqual(value['entrypoint'],['python3','/tools/visualiser_repetition_worker.py'])
        self.assertEqual(value['nano_cpus'],2_000_000_000)
        self.assertEqual(value['mem_limit'],3*1024**3)
        self.assertEqual(value['memswap_limit'],value['mem_limit'])
        self.assertTrue(value['read_only']); self.assertEqual(value['network_mode'],'none')
        self.assertEqual(value['cap_drop'],['ALL'])
        self.assertIn('no-new-privileges:true',value['security_opt'])
        self.assertEqual([v['bind'] for v in value['volumes'].values() if v['mode']=='rw'],['/out'])
        self.assertFalse(any('docker.sock' in p for p in value['volumes']))
        self.assertFalse(any(v['bind'].startswith('/raw') for v in value['volumes'].values()))

    def test_each_round_must_complete_with_stable_publications_for_each_count(self):
        record = passed_worker(rounds=2)
        self.assertEqual(len(driver.check_results(record,[2,5,10],2)),6)
        record['cases'][-1]['result_identity']['sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'changed between rounds'):
            driver.check_results(record,[2,5,10],2)

    def test_missing_failed_or_unclean_cases_cannot_pass(self):
        for change in ('missing','failed','unclean','worker'):
            record = passed_worker()
            if change=='missing': record['cases'].pop()
            elif change=='failed': record['cases'][0]['passed']=False
            elif change=='unclean': record['cases'][0]['cleanup']=False
            else: record['passed']=False
            with self.subTest(change=change), self.assertRaises(ValueError):
                driver.check_results(record,[2,5,10],1)

    def test_summary_cache_miss_fails_without_any_raw_parser(self):
        cache = SummaryCache(self.root/'cache')
        selected = dict(configurations=[dict(id='baseline',runs=[dict(seed='9001',summary='base.json',source_seed='606')])])
        with self.assertRaisesRegex(ValueError,'no raw parsing'):
            worker.restore_summaries(cache,selected,dict(fixture_manifest={},backend={}),[self.root/'out.json'])
        self.assertEqual(cache.statistics()['counters']['raw_started'],0)

    def test_restore_uses_verified_cached_bytes_and_rejects_corruption(self):
        cache = SummaryCache(self.root/'cache')
        source = self.root/'source.json';source.write_bytes(b'[{"value":3}]');source.chmod(0o600)
        selected = dict(configurations=[dict(id='baseline',runs=[dict(seed='9001',summary='base.json',source_seed='606')])])
        context = dict(fixture_manifest={},backend={})
        c = selected['configurations'][0];key=worker.summary_key(context,c,c['runs'][0])
        with cache.entry(key) as entry:
            self.assertTrue(entry.publish(source))
            cache_path=cache.root/entry.key/'metrics.json'
        target=self.root/'restored.json'
        worker.restore_summaries(cache,selected,context,[target])
        self.assertEqual(target.read_bytes(),source.read_bytes())
        cache_path.write_bytes(b'[{"value":4}]')
        with self.assertRaisesRegex(ValueError,'unavailable'):
            worker.restore_summaries(cache,selected,context,[self.root/'bad.json'])

    def test_html_and_csv_have_stage_labels_units_and_synthetic_limitations(self):
        report=dict(passed=True,cases=passed_worker()['cases'],limitations=driver.LIMITATIONS+['<private & text>'],
                    cpu_millis=1000,memory_mib=3072)
        report['summary']=driver.summaries(report['cases'])
        driver.write_tables(self.root,report)
        page=(self.root/'comparison.html').read_text()
        self.assertIn('Repetitions per scenario',page)
        self.assertIn('Combine statistics (s)',page)
        self.assertIn('First chart read (s)',page)
        self.assertIn('not independent scientific',page)
        self.assertIn('&lt;private &amp; text&gt;',page)
        with (self.root/'trials.csv').open() as source:
            rows=list(csv.DictReader(source))
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['repetitions'],'2')
        self.assertIn('summary_restore',rows[0])

    def test_medians_keep_variation_and_count_separate(self):
        record=passed_worker(rounds=3)
        for i,c in enumerate(record['cases']): c['regeneration_seconds']+=i
        values=driver.summaries(record['cases'])
        self.assertEqual([v['repetitions'] for v in values],[2,5,10])
        self.assertTrue(all(v['trials']==3 and v['minimum_seconds']<v['maximum_seconds'] for v in values))

    def fake_container(self,**changes):
        c=Mock();c.id='fictional'
        c.attrs={'Config':{'Labels':{driver.legacy.LABEL:'placeholder'}},
                 'State':{'Running':False,'ExitCode':0,'OOMKilled':False}}
        c.attrs['State'].update(changes);c.logs.return_value=b'fixture log'
        return c

    def run_container_fixture(self,*,state=None,start_error=None,remove_error=None,clock=None):
        client=Mock();created=[]
        def create(**options):
            directory=Path(next(k for k,v in options['volumes'].items() if v['bind']=='/out'))
            api.write_json(directory/'report.json',passed_worker())
            c=self.fake_container(**(state or {}));c.attrs['Config']['Labels']=options['labels']
            c.start.side_effect=start_error;c.remove.side_effect=remove_error
            created.append(c);return c
        client.containers.create.side_effect=create
        with patch.object(driver.legacy,'preflight_space',return_value={}), \
             patch.object(driver,'metrics',return_value={'oom_events':0}), \
             patch.object(driver.time,'monotonic',side_effect=clock) if clock else patch.object(driver.time,'sleep'):
            result=driver.run_container(client,self.args,self.root/'snapshot',{},self.root/'worker')
        return result,created

    def test_normal_native_runner_checks_and_removes_owned_container(self):
        result,containers=self.run_container_fixture()
        self.assertTrue(result['passed']);self.assertTrue(result['cleanup'])
        containers[0].remove.assert_called_once()
        self.assertEqual(len(result['cases']),3)

    def test_start_failure_still_records_cleanup(self):
        with self.assertRaisesRegex(RuntimeError,'start failure'):
            self.run_container_fixture(start_error=RuntimeError('start failure'))
        report=json.loads((self.root/'worker/container-result.json').read_text())
        self.assertFalse(report['passed']);self.assertTrue(report['cleanup'])

    def test_native_oom_fails_even_when_worker_claimed_success(self):
        with self.assertRaisesRegex(RuntimeError,'memory'):
            self.run_container_fixture(state={'ExitCode':137,'OOMKilled':True})
        report=json.loads((self.root/'worker/container-result.json').read_text())
        self.assertTrue(report['oom_killed']);self.assertTrue(report['cleanup']);self.assertFalse(report['passed'])

    def test_unconfirmed_container_cleanup_stops_success(self):
        with self.assertRaisesRegex(RuntimeError,'cleanup'):
            self.run_container_fixture(remove_error=RuntimeError('unavailable'))
        report=json.loads((self.root/'worker/container-result.json').read_text())
        self.assertFalse(report['passed']);self.assertFalse(report['cleanup'])

    def test_timeout_kills_owned_running_container(self):
        with self.assertRaises(TimeoutError):
            self.run_container_fixture(state={'Running':True},clock=[0,100,101,102])
        report=json.loads((self.root/'worker/container-result.json').read_text())
        self.assertFalse(report['passed']);self.assertTrue(report['cleanup'])

    def test_native_daemon_failure_preserves_tables_and_importable_worker_snapshot(self):
        from deploy.multirun.test_visualiser_capacity_comparison import CapacityComparisonTests
        helper=CapacityComparisonTests();helper.setUp();self.addCleanup(helper.doCleanups)
        fixture,build,_=helper.fixture()
        frontend=self.root/'frontend';(frontend/'jasmine_web').mkdir(parents=True)
        (frontend/'jasmine_web/__init__.py').write_text('# Fictional package\n')
        args=SimpleNamespace(**vars(self.args),frontend=frontend,fixture=fixture,source_build=build,build=build,
                             output=self.root/'evidence')
        with patch('docker.from_env',side_effect=RuntimeError('daemon unavailable')), \
             patch.object(driver.legacy,'preflight_space',return_value={}):
            self.assertEqual(driver.run(args),1)
        report=json.loads((args.output/'report.json').read_text())
        self.assertFalse(report['passed']);self.assertTrue(report['cleanup'])
        self.assertEqual(report['error_type'],'RuntimeError')
        self.assertTrue((args.output/'comparison.html').is_file())
        snapshot=args.output/'snapshot'
        self.assertIn('visualiser_repetition_worker.py',report['sources']['tools'])
        subprocess.run([sys.executable,'-I','-B','-c',
            'import sys;sys.path.insert(0,sys.argv[1]);import visualiser_repetition_worker',
            str(snapshot/'tools')],check=True,timeout=10)


@unittest.skipUnless(BUILD and shutil.which('node'),'Set SIMPATHS_VISUALISER_TEST_BUILD for tiny real helpers')
class RepetitionPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        fixture=self.root/'fixture';fixture.mkdir(mode=0o700)
        built=json.loads((Path(BUILD)/'build.json').read_text())
        configurations=[];files={}
        for index in range(2):
            rows=[dict(year=year,scenario='baseline',run='606',variable='Highest Level of Education',
                variable_value='High',stratifier='Overall',stratifier_value='Overall',metric_type='share',
                n=10,metric_value=.2+index*.1) for year in range(2019,2071)]
            name=f'configuration-{index}.json';api.write_json(fixture/name,rows)
            files[name]=fingerprint(fixture/name)
            configurations.append(dict(id='baseline' if index==0 else 'alternative1',name=f'Fixture {index}',
                role='baseline' if index==0 else 'scenario_1',runs=[dict(seed='9001',source_seed='606',summary=name)]))
        self.manifest=dict(format=api.FORMAT,mode='summary-replay',annual_years=list(range(2019,2071)),
            seeds=['9001'],configurations=configurations,files=files,
            backend={n+'_sha256':built['files'][n+'.cjs'] for n in ('runner','calculation')})
        api.write_json(fixture/'manifest.json',self.manifest)
        self.args=SimpleNamespace(frontend=FRONTEND,fixture=fixture,build=Path(BUILD),output=self.root/'out',
            memory_mib=3072,cpu_millis=1000,repetitions=[2,5,10],rounds=2)
        self.args.output.mkdir(mode=0o700)
        self.work=self.args.output/'private';self.work.mkdir(mode=0o700)
        self.built=built

    def test_actual_cache_combination_publication_index_and_chart_match_between_rounds(self):
        sampler=SimpleNamespace(stage='',peaks={},capture=lambda:{})
        record=dict(fixture_manifest=fingerprint(self.args.fixture/'manifest.json'),backend=self.manifest['backend'])
        before=fingerprint(self.args.fixture/'manifest.json')
        worker.run_trials(self.args,self.manifest,self.built,self.work,sampler,record)
        record.update(passed=True,cleanup=True)
        cases=driver.check_results(record,[2,5,10],2)
        self.assertEqual(len(cases),6)
        self.assertEqual(record['cache_statistics']['counters']['hits'],68)
        self.assertEqual(record['cache_statistics']['counters']['raw_started'],0)
        for case in cases:
            self.assertEqual(case['summary_count'],2*case['repetitions'])
            self.assertEqual(case['validation']['rows_per_configuration'],[52,52])
            self.assertEqual(case['validation']['seed_labels'],[str(900000001+i) for i in range(case['repetitions'])])
            self.assertGreater(case['regeneration_seconds'],0)
            self.assertGreaterEqual(case['regeneration_seconds'],case['stage_total_seconds'])
            self.assertTrue(all(stage['passed'] for stage in case['stages'].values()))
        self.assertEqual(fingerprint(self.args.fixture/'manifest.json'),before)
        self.assertEqual([p.name for p in self.work.iterdir()],['summaries'])

    def test_interrupted_calculation_keeps_source_and_cleans_trial_scratch(self):
        sampler=SimpleNamespace(stage='',peaks={},capture=lambda:{})
        record=dict(fixture_manifest=fingerprint(self.args.fixture/'manifest.json'),backend=self.manifest['backend'])
        original=api.command
        def fail(log,argv,**kwargs):
            if str(argv[-2]).endswith('/runner.cjs'):
                raise RuntimeError('fictional interruption')
            return original(log,argv,**kwargs)
        with patch.object(api,'command',side_effect=fail), self.assertRaisesRegex(RuntimeError,'interruption'):
            worker.run_trials(self.args,self.manifest,self.built,self.work,sampler,record)
        self.assertFalse(record['cases'][0]['passed']);self.assertTrue(record['cases'][0]['cleanup'])
        self.assertEqual([p.name for p in self.work.iterdir()],['summaries'])
        self.assertEqual(SummaryCache(self.work/'summaries').prune()['entries'],20)


if __name__=='__main__':
    unittest.main()
