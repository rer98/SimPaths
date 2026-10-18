"""(C) Copyright 2026, by Ross Richardson

Two genuine-seed raw-summary matrix guards, isolated native containment and tiny
real parser/retention pipelines. Full resource results require native Docker.
@author ross richardson
"""
import copy
import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from . import visualiser_summary_trials as driver
from . import visualiser_summary_worker as worker
from . import test_visualiser_capacity_comparison as old_tests
from . import test_visualiser_capacity_worker as cgroup_tests
from . import test_visualiser_backend as raw_fixtures
from . import test_visualiser_regeneration_trials as native_tests
from .artifacts import fingerprint
api=worker.api
BUILD=os.environ.get('SIMPATHS_VISUALISER_TEST_BUILD')
FRONTEND=Path(os.environ.get('SIMPATHS_VISUALISER_TEST_FRONTEND','/home/rer/git/JAS-mine/JAS-mine-web'))


def raw_fixture(root,build,*,real_csv=False):
    fixture=root/'fixture';fixture.mkdir(mode=0o700)
    raw=root/'raw';raw.mkdir(mode=0o700)
    built=api.load_small(build,'build.json');files={};configs=[]
    for i,role in enumerate(('baseline','scenario_1')):
        runs=[]
        for seed in ('606','607'):
            entry=dict(seed=seed)
            texts=raw_fixtures.native_texts(10+i*5+int(seed)-606) if real_csv else ('person\n1\n','benefit\n2\n')
            for key,name,text in zip(('person','benefit'),('Person.csv','BenefitUnit.csv'),texts):
                if real_csv:
                    rows=list(csv.DictReader(io.StringIO(text)))
                    text=raw_fixtures.csv_text([dict(row,time=str(year)+'.0') for year in range(2019,2071) for row in rows])
                relative=f'{role}/{seed}/{name}';target=raw/relative
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);target.write_text(text)
                files[relative]=fingerprint(target);entry[key]=relative
            runs.append(entry)
        configs.append(dict(id=role,role=role,name=role,runs=runs))
    record=dict(format=api.FORMAT,mode='raw-native',seeds=['606','607'],annual_years=list(range(2019,2071)),
        configurations=configs,files=files,raw_root=str(raw),raw_directories=['baseline','scenario_1'],
        backend={n+'_sha256':built['files'][n+'.cjs'] for n in ('runner','calculation')},limitations=[])
    api.write_json(fixture/'manifest.json',record)
    return fixture,raw,record


def worker_report(profile,tasks,released,manifest):
    record=native_tests.worker_report(profile,tasks,released)
    record.update(raw_parsing=True,synthetic=False,
        cache_statistics=dict(counters=dict(hits=0,misses=len(tasks)*2,raw_started=len(tasks)*2,
            raw_completed=len(tasks)*2,raw_failed=0,evictions=0)))
    for c in record['cases']:
        cid=manifest['configurations'][c['task']-1]['id']
        c.update(configuration=cid,summary_seconds=1.,stages={s:dict(passed=True,elapsed_seconds=.1,
            sampled_peaks=dict(working_bytes=1024,memory_current_bytes=2048)) for s in worker.STAGES})
        c['seeds']=[dict(seed=seed,passed=True,summary=dict(bytes=100,sha256=str(c['task'])*64),
            validation=dict(seed=seed,canonical_role='baseline',rows=52,annual_years=manifest['annual_years']),
            stages={s:dict(passed=True) for s in worker.STAGES}) for seed in manifest['seeds']]
        c['result_identity']={s['seed']:s['summary'] for s in c['seeds']}
    return record


class SummaryMatrixTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.helper=old_tests.CapacityComparisonTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.build=self.helper.build('original');self.fixture,self.raw,self.manifest=raw_fixture(self.root,self.build)
        self.profile=dict(round=1,repetitions=2,memory_mib=2560,cpu_millis=1000,workers=2)
        self.args=SimpleNamespace(fixture=self.fixture,source_build=self.build,build=self.build,frontend=FRONTEND,
            output=self.root/'output',raw_root=self.raw,raw_manifest=self.manifest,image_id='sha256:'+'a'*64,
            image='test',timeout_seconds=3600,overall_timeout_seconds=43200)

    def test_three_rounds_cover_all_twelve_profiles_with_only_two_seeds(self):
        plan=driver.trial_plan([2560,2048,3072],[1000,2000],[1,2],3)
        self.assertEqual(len(plan),36)
        self.assertEqual({p['repetitions'] for p in plan},{2})
        self.assertEqual(len({(p['round'],p['memory_mib'],p['cpu_millis'],p['workers']) for p in plan}),36)
        self.assertEqual(len({tuple((p['memory_mib'],p['cpu_millis'],p['workers']) for p in plan if p['round']==r) for r in (1,2,3)}),3)

    def test_invalid_or_duplicate_profiles_fail_before_launch(self):
        for m,c,w,r in [([2000],[1000],[1],1),([2048,2048],[1000],[1],1),([2048],[1500],[1],1),
                        ([2048],[1000],[3],1),([],[1000],[1],1),([True],[1000],[1],1),([2048],[1000],[1],6)]:
            with self.subTest(m=m,c=c,w=w,r=r),self.assertRaises(ValueError):driver.trial_plan(m,c,w,r)

    def test_raw_adapter_records_both_builds_but_never_copies_or_relabels_raw_files(self):
        before=fingerprint(self.fixture/'manifest.json');raw_before={n:fingerprint(self.raw/n) for n in self.manifest['files']}
        self.args.build=self.helper.build('new')
        adapted,root=driver.prepare_fixture(self.args,self.root/'adapted')
        self.assertEqual(root,self.raw);self.assertEqual(adapted['files'],self.manifest['files'])
        self.assertEqual(adapted['configurations'],self.manifest['configurations'])
        self.assertEqual(fingerprint(self.fixture/'manifest.json'),before)
        self.assertEqual({n:fingerprint(self.raw/n) for n in self.manifest['files']},raw_before)
        self.assertFalse(adapted['provenance']['raw_summary_trial']['raw_files_copied'])
        self.assertEqual({p.name for p in (self.root/'adapted').iterdir()},{'manifest.json','COPYRIGHT.md'})

    def test_changed_science_dependencies_toolchain_or_original_build_are_rejected(self):
        for name in ('science','dependencies','toolchain','original-backend'):
            new=self.helper.build(name);built=api.load_small(new,'build.json')
            if name=='science':built['source_hashes']['parseCore.js']='f'*64
            elif name in ('dependencies','toolchain'):built[name]={'changed':'yes'}
            else:self.args.source_build=new
            api.write_json(new/'build.json',built);self.args.build=new
            with self.subTest(name=name),self.assertRaises(ValueError):driver.prepare_fixture(self.args,self.root/('bad-'+name))
            self.args.source_build=self.build

    def test_replay_or_extra_raw_seed_is_not_silently_used(self):
        for change in ('mode','seed'):
            manifest=copy.deepcopy(self.manifest)
            if change=='mode':manifest['mode']='summary-replay'
            else:
                manifest['seeds'].append('608')
                for c in manifest['configurations']:c['runs'].append(dict(c['runs'][0],seed='608'))
            api.write_json(self.fixture/'manifest.json',manifest)
            with self.subTest(change=change),self.assertRaises((ValueError,KeyError)):
                driver.prepare_fixture(self.args,self.root/('bad-'+change))

    def test_narrow_read_only_raw_mounts_exact_per_worker_limits_and_no_network(self):
        value=driver.container_options(self.args,self.root/'snapshot',self.root/'worker',self.root/'control',self.profile,1,'identity')
        self.assertEqual(value['mem_limit'],2560*api.MIB);self.assertEqual(value['memswap_limit'],value['mem_limit'])
        self.assertEqual(value['nano_cpus'],1_000_000_000);self.assertTrue(value['read_only'])
        self.assertEqual(value['network_mode'],'none');self.assertEqual(value['cap_drop'],['ALL'])
        raw_mounts={v['bind']:v['mode'] for v in value['volumes'].values() if v['bind'].startswith('/raw')}
        self.assertEqual(raw_mounts,{'/raw/baseline':'ro','/raw/scenario_1':'ro'})
        self.assertEqual([v['bind'] for v in value['volumes'].values() if v['mode']=='rw'],['/out'])
        self.assertEqual(value['command'][value['command'].index('--task-start')+1],'2')
        self.assertNotIn('docker.sock',str(value));self.assertNotIn(str(self.raw),[p for p in value['volumes']])

    def test_distinct_baseline_and_alternative_hashes_are_checked_independently(self):
        reports=[worker_report(self.profile,[i],100,self.manifest) for i in (1,2)]
        result=driver.check_batch(reports,self.profile,100,manifest=self.manifest)
        self.assertNotEqual(result['result_identity']['baseline'],result['result_identity']['scenario_1'])
        self.assertEqual(result['first_result_seconds'],1);self.assertEqual(result['both_results_seconds'],2)
        self.assertEqual(result['configurations_per_hour'],3600)
        self.assertEqual(result['configurations_per_allocated_vcpu_hour'],1800)

    def test_cached_missing_seed_partial_stage_or_wrong_configuration_cannot_count_as_fast(self):
        for change in ('cache-hit','miss','seed','year','stage','configuration','result','quota','start'):
            reports=[worker_report(self.profile,[i],100,self.manifest) for i in (1,2)];r=reports[1];c=r['cases'][0]
            if change=='cache-hit':r['cache_statistics']['counters']['hits']=1
            elif change=='miss':r['cache_statistics']['counters']['raw_completed']=1
            elif change=='seed':c['seeds'].pop()
            elif change=='year':c['seeds'][0]['validation']['annual_years']=[2019]
            elif change=='stage':c['stages']['raw_summarisation']['passed']=False
            elif change=='configuration':c['configuration']='baseline'
            elif change=='result':c['result_identity']={}
            elif change=='quota':r['cpu_millis']=2000
            else:c['started_monotonic_ns']=99
            with self.subTest(change=change),self.assertRaises(ValueError):driver.check_batch(reports,self.profile,100,manifest=self.manifest)

    def test_hash_deferral_is_raw_only_and_still_checks_paths_sizes_and_build(self):
        built=api.load_small(self.build,'build.json');built['mode']='development-paired';api.write_json(self.build/'build.json',built)
        name=next(iter(self.manifest['files']));target=self.raw/name
        target.write_text('changed\n1\n')
        # Keep a same-length corruption to distinguish metadata preflight from timed verification.
        target.write_bytes(b'x'*self.manifest['files'][name]['bytes'])
        api.load_fixture(self.fixture,self.build,self.raw,verify_raw_hashes=False)
        with self.assertRaisesRegex(ValueError,'checksum'):api.load_fixture(self.fixture,self.build,self.raw)
        target.write_bytes(b'x')
        with self.assertRaisesRegex(ValueError,'size'):api.load_fixture(self.fixture,self.build,self.raw,verify_raw_hashes=False)
        target.unlink();target.symlink_to(self.raw/next(n for n in self.manifest['files'] if n!=name))
        with self.assertRaises(OSError):api.load_fixture(self.fixture,self.build,self.raw,verify_raw_hashes=False)

    def test_native_controller_uses_raw_work_and_still_cleans_before_return(self):
        f=native_tests.RegenerationMatrixTests();f.setUp();self.addCleanup(f.doCleanups)
        f.profile=self.profile
        client,containers=f.fake_native()
        def reload_for(c):
            original=c.reload.side_effect
            def reload():
                original()
                target=f.root/'batch'/('worker-'+c.id.removeprefix('fictional-'))
                if (target/'report.json').exists():
                    old=api.load_small(target,'report.json')
                    api.write_json(target/'report.json',worker_report(self.profile,[int(c.id[-1])],old['released_monotonic_ns'],self.manifest))
            c.reload.side_effect=reload
        # Containers are created inside run_batch, so wrap the factory first.
        create=client.containers.create.side_effect
        def factory(**options):
            c=create(**options);reload_for(c);return c
        client.containers.create.side_effect=factory
        with patch.object(driver.legacy,'preflight_space',return_value={}),patch.object(driver.native.time,'sleep'):
            result=driver.native.run_batch(client,self.args,f.root/'snapshot',f.root/'batch',self.profile,
                options_factory=driver.container_options,result_checker=lambda reports,p,t:driver.check_batch(reports,p,t,manifest=self.manifest))
        self.assertTrue(result['passed']);self.assertTrue(result['cleanup'])
        for c in containers:c.remove.assert_called_once()
        self.assertFalse(list((f.root/'batch').glob('worker-*/private')))

    def test_failure_table_has_headings_and_does_not_hide_incomplete_profiles(self):
        reports=[worker_report(self.profile,[i],100,self.manifest) for i in (1,2)]
        success=dict(self.profile,passed=True,**driver.check_batch(reports,self.profile,100,manifest=self.manifest))
        failure=dict(self.profile,round=2,passed=False,failure_kind='container-oom')
        summary=driver.summaries([success,failure],2)
        self.assertFalse(summary[0]['passed'])
        driver.write_tables(self.root,dict(summary=summary,completed=True,passed=False))
        page=(self.root/'comparison.html').read_text()
        for phrase in ('First configuration ready (s)','Both configurations ready (s)','Verify source checksums',
                       'Parse CSV and calculate statistics','FAILED / INCOMPLETE','successful rounds only','filesystem may be warm'):
            self.assertIn(phrase,page)
        with (self.root/'comparison.csv').open() as f:rows=list(csv.DictReader(f))
        self.assertEqual(rows[0]['passed_trials'],'1');self.assertEqual(rows[0]['expected_trials'],'2')

    def test_bad_quota_failure_retains_report_and_removes_generated_scratch(self):
        args=SimpleNamespace(**vars(self.args));args.output=self.root/'worker'
        args.memory_mib=2560;args.cpu_millis=1000;args.tasks=1;args.task_start=1;args.round=1
        with patch.object(api,'require_limits',side_effect=ValueError('quota')),self.assertRaises(ValueError):worker.execute(args)
        result=api.load_small(args.output,'report.json')
        self.assertTrue(result['cleanup']);self.assertFalse(result['passed']);self.assertEqual(result['failure_kind'],'integrity-failed')
        self.assertFalse((args.output/'private').exists())

    def run_driver(self,*,failure=False,integrity=False,changed=False):
        args=SimpleNamespace(**vars(self.args));args.memory_mib=[2560];args.cpu_millis=[1000]
        args.workers=[1,2];args.rounds=1
        client=Mock();client.info.return_value={'CgroupVersion':'2'};client.images.get.return_value.id=args.image_id
        calls=[]
        def batch(_,__,snapshot,directory,profile,**kw):
            calls.append(profile)
            if len(calls)==1 and (failure or integrity):
                return dict(profile,passed=False,cleanup=True,failure_kind='worker-failed',
                    workers_evidence=[dict(failure_kind='integrity-failed' if integrity else None)])
            reports=[worker_report(profile,[1,2],100,self.manifest)] if profile['workers']==1 else [worker_report(profile,[i],100,self.manifest) for i in (1,2)]
            result=kw['result_checker'](reports,profile,100)
            if changed and len(calls)>1:result['result_identity']['baseline']['606']['sha256']='f'*64
            return dict(profile,passed=True,cleanup=True,workers_evidence=[],**result)
        with patch('docker.from_env',return_value=client),patch.object(driver.native,'run_batch',side_effect=batch), \
             patch.object(driver.legacy,'preflight_space',return_value={}):
            code=driver.run(args)
        return code,api.load_small(args.output,'report.json'),calls

    def test_resource_failure_continues_remaining_profiles_and_keeps_failure_tables(self):
        code,report,calls=self.run_driver(failure=True)
        self.assertEqual(code,1);self.assertTrue(report['completed']);self.assertFalse(report['passed'])
        self.assertEqual(len(calls),2);self.assertTrue(report['cases'][1]['passed'])
        self.assertTrue((self.args.output/'comparison.html').exists())

    def test_integrity_failure_stops_before_further_profiles(self):
        code,report,calls=self.run_driver(integrity=True)
        self.assertEqual(code,1);self.assertEqual(len(calls),1);self.assertFalse(report['completed'])
        self.assertIn('integrity',report['error'])

    def test_changed_summary_between_resource_profiles_stops_the_matrix(self):
        code,report,_=self.run_driver(changed=True)
        self.assertEqual(code,1);self.assertFalse(report['completed'])
        self.assertIn('changed across resource profiles',report['error'])

    def test_successful_driver_snapshots_only_tools_build_and_raw_metadata(self):
        code,report,calls=self.run_driver()
        self.assertEqual(code,0);self.assertTrue(report['completed']);self.assertTrue(report['passed'])
        self.assertEqual(len(calls),2);self.assertEqual(report['repetitions'],2)
        self.assertFalse(list((self.args.output/'snapshot').rglob('*.csv')))
        subprocess.run([sys.executable,'-I','-B','-c','import sys;sys.path.insert(0,sys.argv[1]);import visualiser_summary_worker',
            str(self.args.output/'snapshot/tools')],check=True,timeout=10)

    def test_deferred_hash_mode_does_not_skip_replay_or_build_hashes(self):
        helper=cgroup_tests.CapacityWorkerTests();helper.setUp();self.addCleanup(helper.doCleanups)
        fixture,build,_=helper.fixture()
        (fixture/'baseline.json').write_text('[]')
        with self.assertRaisesRegex(ValueError,'checksum'):api.load_fixture(fixture,build,fixture,verify_raw_hashes=False)
        built=api.load_small(self.build,'build.json');built['mode']='development-paired'
        api.write_json(self.build/'build.json',built);(self.build/'runner.cjs').write_text('// replaced')
        with self.assertRaisesRegex(ValueError,'Build checksum'):api.load_fixture(self.fixture,self.build,self.raw,verify_raw_hashes=False)


@unittest.skipUnless(BUILD and shutil.which('node'),'Set SIMPATHS_VISUALISER_TEST_BUILD for tiny real raw pipelines')
class RawSummaryPipelineTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.build=Path(BUILD);fixture,raw,self.manifest=raw_fixture(self.root,self.build,real_csv=True)
        self.args=SimpleNamespace(frontend=FRONTEND,fixture=fixture,raw_root=raw,build=self.build,
            output=self.root/'worker',control=self.root/'control',memory_mib=2560,cpu_millis=2000,
            repetitions=2,round=1,tasks=2,task_start=1,timeout_seconds=60)
        self.args.control.mkdir(mode=0o700)

    def execute(self):
        helper=cgroup_tests.CapacityWorkerTests();helper.setUp();self.addCleanup(helper.doCleanups)
        group=helper.cgroup();(group/'memory.max').write_text(str(self.args.memory_mib*api.MIB))
        (group/'cpu.max').write_text(str(self.args.cpu_millis*100)+' 100000')
        limits,snapshot,barrier=api.require_limits,api.snapshot,worker.wait_for_release
        def release(args,record):
            api.write_json(args.control/'release.json',dict(format='simpaths.visualiser.release.v1',released_monotonic_ns=time.monotonic_ns()))
            barrier(args,record)
        with patch.object(api,'require_limits',side_effect=lambda m,**kw:limits(m,group,**kw)), \
             patch.object(api,'snapshot',side_effect=lambda _root=api.CGROUP:snapshot(group)), \
             patch.object(worker,'wait_for_release',side_effect=release):worker.execute(self.args)
        return api.load_small(self.args.output,'report.json')

    def test_four_actual_raw_parses_keep_real_seeds_all_years_and_clean_their_cache(self):
        before={n:fingerprint(self.args.raw_root/n) for n in self.manifest['files']}
        report=self.execute()
        self.assertTrue(report['passed']);self.assertTrue(report['cleanup']);self.assertTrue(report['sources_unchanged'])
        self.assertFalse(report['synthetic']);self.assertEqual(report['cache_statistics']['counters']['raw_completed'],4)
        self.assertEqual(report['cache_statistics']['counters']['hits'],0)
        profile=dict(round=1,repetitions=2,memory_mib=2560,cpu_millis=2000,workers=1)
        result=driver.check_batch([report],profile,report['released_monotonic_ns'],manifest=self.manifest)
        self.assertNotEqual(result['result_identity']['baseline'],result['result_identity']['scenario_1'])
        self.assertGreater(result['both_results_seconds'],result['first_result_seconds'])
        self.assertLess(report['cases'][0]['ready_monotonic_ns'],report['cases'][1]['started_monotonic_ns'])
        self.assertEqual({s['seed'] for c in report['cases'] for s in c['seeds']},{'606','607'})
        self.assertEqual(before,{n:fingerprint(self.args.raw_root/n) for n in self.manifest['files']})
        self.assertFalse((self.args.output/'private').exists());self.assertTrue((self.args.output/'samples.jsonl').is_file())
        # Every snapshotted import must work without the source checkout on sys.path.
        snap=self.root/'snapshot';snap.mkdir()
        driver.legacy.snapshot_tools(FRONTEND,self.build,snap,self.manifest)
        for name in ('visualiser_repetition_worker.py','visualiser_regeneration_worker.py','visualiser_summary_worker.py'):
            fingerprint(Path(worker.__file__).parent/name,snap/'tools'/name)
        subprocess.run([sys.executable,'-I','-B','-c','import sys;sys.path.insert(0,sys.argv[1]);import visualiser_summary_worker',
            str(snap/'tools')],check=True,timeout=10)

    def test_same_length_raw_corruption_is_rejected_in_timed_verification(self):
        name=next(iter(self.manifest['files']));target=self.args.raw_root/name
        target.write_bytes(b'x'*target.stat().st_size)
        with self.assertRaisesRegex(ValueError,'verification'):self.execute()
        report=api.load_small(self.args.output,'report.json')
        self.assertEqual(report['failure_kind'],'integrity-failed');self.assertTrue(report['cleanup'])
        self.assertFalse(report['passed']);self.assertFalse(report['cases'][0]['seeds'][0]['stages']['source_verification']['passed'])

    def test_parser_failure_is_recorded_and_cache_cleanup_is_still_verified(self):
        with patch.object(api,'command',side_effect=subprocess.CalledProcessError(134,['node'])),self.assertRaises(subprocess.CalledProcessError):self.execute()
        report=api.load_small(self.args.output,'report.json')
        self.assertFalse(report['passed']);self.assertTrue(report['cleanup'])
        self.assertFalse(report['cases'][0]['seeds'][0]['stages']['raw_summarisation']['passed'])
        self.assertEqual(report['cache_statistics']['counters']['raw_failed'],1)
        self.assertFalse((self.args.output/'private').exists())

    def test_sampler_gap_keeps_valid_summaries_but_fails_the_worker_with_full_diagnostics(self):
        original=worker.measure
        def failed_sampler(args,manifest,work,sampler,record):
            original(args,manifest,work,sampler,record)
            sampler.errors.append('ValueError')
            sampler.error_details.append(dict(error_type='ValueError',message='fictional counter failure',
                stage='raw_summarisation',elapsed_seconds=1.,wall_time_ns=1))
        with patch.object(worker,'measure',side_effect=failed_sampler),self.assertRaisesRegex(RuntimeError,'fictional counter failure'):
            self.execute()
        report=api.load_small(self.args.output,'report.json')
        self.assertFalse(report['passed']);self.assertTrue(report['cleanup'])
        self.assertTrue(all(c['passed'] for c in report['cases']))
        self.assertEqual(report['sampling_errors'][0]['message'],'fictional counter failure')
        self.assertIn('fictional counter failure',report['metrics_error_message'])
        self.assertNotIn('closed file',report['metrics_error_message'])
        self.assertIsNotNone(report.get('final_metrics'))

    def test_validation_rejects_wrong_seed_schema_years_role_and_nonfinite_statistics(self):
        row=dict(year=2019,run='606',scenario='baseline',metric_value=1,n=12,
            variable='Example',variable_value='Overall',stratifier='Overall',stratifier_value='All',metric_type='mean')
        path=self.root/'summary.json'
        for kind in ('seed','year','role','schema','value'):
            value=dict(row)
            if kind=='seed':value['run']='607'
            elif kind=='year':value['year']=2018
            elif kind=='role':value['scenario']='scenario_1'
            elif kind=='schema':value['private_id']='hidden'
            else:value['metric_value']=float('inf')
            path.write_text(json.dumps([value]))
            with self.subTest(kind=kind),self.assertRaises(ValueError):worker.validate_summary(path,'606',[2019])
        api.write_json(path,[row])
        with self.assertRaisesRegex(ValueError,'annual coverage'):worker.validate_summary(path,'606',list(range(2019,2071)))


if __name__=='__main__':unittest.main()
