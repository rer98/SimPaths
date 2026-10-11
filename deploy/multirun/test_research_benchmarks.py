"""(C) Copyright 2026, by Ross Richardson

Fixed-memory comparisons and evidence-bound, restartable bulk-disposal guards.
@author ross richardson
"""
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy.multirun import research_benchmarks as bench
from deploy.multirun.artifacts import ArtifactError, fingerprint


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    def publish(self,path,value):
        path.write_text(json.dumps(value,sort_keys=True)+'\n')

    def fixture(self,label,*,benefit='same',person='same'):
        root=self.root/label;proof=root/'model-proof';output=proof/'retained-output'
        output.mkdir(parents=True)
        seeds=['606','607'];manifest={}
        for ordinal,seed in enumerate(seeds):
            run=output/f'{label.lower()}_timestamp_{seed}_{ordinal}'
            (run/'csv').mkdir(parents=True);(run/'input').mkdir()
            for name in bench.ANNUAL_FILES:
                value=benefit if name=='BenefitUnit.csv' else person if name=='Person.csv' else 'same'
                path=run/'csv'/name
                path.write_text(f'time,fictional_value\n2019,{seed}-{value}\n2020,{seed}-{value}\n')
                manifest[path.relative_to(output).as_posix()]=fingerprint(path)
            path=run/'input/options.txt';path.write_text('randomSeedIfFixed: '+seed+'\n')
            manifest[path.relative_to(output).as_posix()]=fingerprint(path)
        heap,memory=bench.CASE_LIMITS[label]
        settings=dict(population=100000,end_year=2020,repetitions=2,heap_mib=heap,memory_mib=memory,
            max_memory_mib=memory,storage_mib=12288,setup_seconds=900,repetition_seconds=14400,max_attempts=1)
        config=dict(common=dict(country='UK',population=100000,start_year=2019,end_year=2020),
            seed_plan=dict(seeds=seeds,repetitions=2),model_release='fictional-model',dataset_revision='fictional-inputs',
            run_sets=[dict(id='baseline',collector_args=dict(exportToCSV=True),model_args=dict(savingRate=0.056))])
        peak=dict(heap_max_bytes=heap*bench.MIB,heap_used_bytes=3*bench.GIB,
            working_set_bytes=4*bench.GIB,container_used_bytes=memory*bench.MIB,
            cgroup_peak_bytes=memory*bench.MIB)
        attempt=dict(id='fictional-attempt-'+label,heap_mib=heap,outcome='success',
            observed_container_limits_bytes=[memory*bench.MIB],peak=peak,
            container_lifetime_seconds=1200-(50 if label=='B' else 0),elapsed_ms=1100000,
            gc_ms=20000,samples=20,model_initialisation_seconds=[100.0,50.0])
        record=dict(passed=True,cleanup=True,source_unchanged=True,input_copies_reclaimed=True,
            reservations_released=True,browser_errors=[],resource_events=[],settings=settings,
            attempts=[attempt],start_year=2019,annual_years=[2019,2020],variant='baseline',
            output_location=str(output),output_bytes=sum(f['bytes'] for f in manifest.values()),
            peak_workspace_bytes=8*bench.GIB,elapsed_seconds=1300,sample_interval_ms=30000,
            verified_repetitions=[dict(seed=s,fingerprint='1'*64) for s in seeds],
            status_requests=[dict(status=200,ms=v) for v in (10,20,100,200)],
            model=dict(bytes=10,sha256='a'*64),image='sha256:'+'b'*64,prepared_sha256='c'*64,
            source_sha256={'deploy/multirun/research_calibration.py':'d'*64,
                          'deploy/acceptance/SimPathsResourceProbe.java':'f'*64},
            probe_classes={'Probe.class':dict(bytes=5,sha256='e'*64)},
            enforcement='application-monitoring')
        self.publish(root/'report.json',dict(passed=True,cleanup=True))
        self.publish(proof/'report.json',record);self.publish(proof/'configuration.json',config)
        self.publish(proof/'output-manifest.json',manifest)
        evidence,_=bench.completion(root)
        files={}
        for name,value in manifest.items():
            files[name]=dict(value,allocated_bytes=value['bytes'])
            if name.endswith('.csv'):
                lines=(output/name).read_bytes().splitlines(keepends=True)
                files[name].update(years={str(y):dict(rows=1,bytes=len(lines[i+1]))
                                         for i,y in enumerate((2019,2020))},
                                   rows=2,header_bytes=len(lines[0]))
        source=dict(folder=str(root),provenance={Path(n).name:evidence[n]['fingerprint'] for n in bench.EVIDENCE[1:]},
            outer_report=evidence['report.json']['fingerprint'],configuration=config,settings=settings,
            attempts=[attempt],bytes=record['output_bytes'],files=files,runs=[])
        audit=self.root/(label+'-audit.json')
        self.publish(audit,dict(format='simpaths.private-research-storage-audit.v1',passed=True,
            read_only=True,original_manifest_hashes_verified=True,simulations_started=0,
            microdata_copied=False,sources=[source]))
        return root,audit

    def stored(self,label='A'):
        root,audit=self.fixture(label)
        archive=self.root/(label+'-archive');bench.archive(root,audit,archive)
        return root,archive

    def edit(self,path,change):
        value=json.loads(path.read_text());change(value);self.publish(path,value)

    def test_pair_matches_annual_files_by_seed_not_timestamp_and_keeps_pending_case(self):
        a,_=self.fixture('A');b,_=self.fixture('B')
        with patch.object(bench,'verified_file',side_effect=AssertionError('Must not read bulk CSV')):
            report=bench.comparison({'A':a,'B':b,'C':self.root/'not-started'})
        pair=report['comparisons'][0]
        self.assertEqual(len(pair['annual_files']),18)
        self.assertEqual(pair['unexpected_mismatches'],0)
        self.assertEqual(report['cases']['C']['state'],'pending')
        self.assertFalse(report['complete']);self.assertFalse(report['microdata_read'])
        self.assertEqual(report['cases']['A']['measurements']['status']['p95_ms'],200)
        self.assertIn('Single-observation',pair['note'])

    def test_benefit_warnings_and_unexpected_person_mismatches_are_not_hidden(self):
        a,_=self.fixture('A');b,_=self.fixture('B',benefit='DIFF',person='different')
        pair=bench.comparison({'A':a,'B':b})['comparisons'][0]
        self.assertEqual(pair['reproducibility_warnings'],2)
        self.assertEqual(pair['unexpected_mismatches'],2)
        self.assertIn('Person.csv',bench.render(bench.comparison({'A':a,'B':b})))
        with redirect_stdout(io.StringIO()):
            result=bench.main(['compare','--case','A='+str(a),'--case','B='+str(b),
                               '--output',str(self.root/'comparison')])
        self.assertEqual(result,1)

    def test_benefit_size_change_is_unexpected_rather_than_a_known_pattern_warning(self):
        a,_=self.fixture('A');b,_=self.fixture('B',benefit='different length')
        pair=bench.comparison({'A':a,'B':b})['comparisons'][0]
        self.assertEqual(pair['reproducibility_warnings'],0)
        self.assertEqual(pair['unexpected_mismatches'],2)

    def test_model_input_seed_collector_and_cpu_driver_changes_prevent_comparison(self):
        a,_=self.fixture('A');b,_=self.fixture('B')
        report=b/'model-proof/report.json';config=b/'model-proof/configuration.json'
        original_record=json.loads(report.read_text());original_config=json.loads(config.read_text())
        for key,value in (('image','sha256:'+'0'*64),('prepared_sha256','0'*64),('source_sha256','0'*64)):
            self.publish(report,original_record);self.edit(report,lambda d:d.update({key:value}))
            with self.subTest(key=key),self.assertRaises(ArtifactError):bench.comparison({'A':a,'B':b})
        self.publish(report,original_record)
        changed=deepcopy(original_config);changed['run_sets'][0]['collector_args']['exportToCSV']=False
        self.publish(config,changed)
        with self.assertRaises(ArtifactError):bench.comparison({'A':a,'B':b})
        self.publish(config,original_config)
        self.edit(report,lambda d:d['verified_repetitions'].reverse())
        with self.assertRaises(ArtifactError):bench.comparison({'A':a,'B':b})

    def test_wrong_case_limits_growth_retry_or_unsettled_trial_is_rejected(self):
        a,audit=self.fixture('A');path=a/'model-proof/report.json'
        original=json.loads(path.read_text())
        for modify in (lambda d:d.update(cleanup=False),lambda d:d.update(reservations_released=False),
                       lambda d:d.update(resource_events=[{'growth':True}]),
                       lambda d:d['settings'].update(max_attempts=2),
                       lambda d:d['attempts'][0].update(observed_container_limits_bytes=[6*bench.GIB])):
            self.publish(path,original);self.edit(path,modify)
            with self.assertRaises(ArtifactError):bench.archive(a,audit,self.root/'blocked')
            self.assertFalse((self.root/'blocked').exists())
        self.publish(path,original)
        with self.assertRaises(ArtifactError):bench.comparison({'C':a})

    def test_running_case_without_final_reports_is_not_read_or_archived(self):
        a,_=self.fixture('A');running=self.root/'running';(running/'model-proof').mkdir(parents=True)
        (running/'model-proof/progress.json').write_text('not valid or completed evidence')
        report=bench.comparison({'A':a,'B':running})
        self.assertEqual(report['cases']['B']['state'],'incomplete')
        with self.assertRaises(FileNotFoundError):bench.archive(running,self.root/'absent',self.root/'archive')

    def test_failed_case_is_reported_but_cannot_be_archived_or_used_for_timing(self):
        root,audit=self.fixture('B');self.edit(root/'model-proof/report.json',lambda d:d.update(passed=False))
        result=bench.comparison({'B':root})
        self.assertEqual(result['cases']['B']['state'],'failed-or-unclean')
        self.assertNotIn('measurements',result['cases']['B'])
        with self.assertRaises(ArtifactError):bench.archive(root,audit,self.root/'archive')

    def test_missing_frozen_image_identity_and_unbounded_horizon_are_rejected(self):
        root,_=self.fixture('A');path=root/'model-proof/report.json'
        original=json.loads(path.read_text())
        for change in (lambda d:d.pop('image'),lambda d:d['settings'].update(end_year=100000000)):
            self.publish(path,original);self.edit(path,change)
            with self.assertRaises(ArtifactError):bench.comparison({'A':root})

    def test_waiting_pressure_notice_is_distinguished_from_confirmed_growth(self):
        root,_=self.fixture('A');path=root/'model-proof/report.json'
        event=dict(action='waiting',kind='memory',attempt=1,
                   allocation=dict(cpu_millis=2000,memory_mib=5120,storage_mib=12288))
        self.edit(path,lambda d:d.update(resource_events=[event]))
        case=bench.comparison({'A':root})['cases']['A']
        self.assertEqual(case['measurements']['unchanged_allocation_pressure_notices'],1)
        for change in (lambda d:d['resource_events'][0].update(action='increased'),
                       lambda d:d['resource_events'][0]['allocation'].update(memory_mib=6144)):
            self.edit(path,change)
            with self.assertRaises(ArtifactError):bench.comparison({'A':root})
            self.edit(path,lambda d:d.update(resource_events=[event]))

    def test_duplicate_nonfinite_oversized_and_symlink_json_are_rejected(self):
        path=self.root/'bad.json'
        for text in ('{"a":1,"a":2}','{"a":NaN}','{"a":Infinity}'):
            path.write_text(text)
            with self.assertRaises(ArtifactError):bench.read_json(path)
        with path.open('wb') as stream:stream.truncate(bench.MAX_JSON_BYTES+1)
        with self.assertRaises(ArtifactError):bench.read_json(path)
        path.write_text('{}');link=self.root/'link';link.symlink_to(path)
        with self.assertRaises(ArtifactError):bench.read_json(link)

    def test_archive_binds_original_reports_and_independent_annual_audit(self):
        root,archive=self.stored()
        value,_,_=bench.read_archive(archive)
        self.assertFalse(value['microdata_copied'])
        self.assertNotIn('fictional_value',json.dumps(value))
        self.assertEqual(bench.comparison({'A':archive})['cases']['A']['state'],'complete')
        before=fingerprint(root/'model-proof/report.json')
        self.assertEqual(value['evidence']['model-proof/report.json']['fingerprint'],before)
        self.assertEqual(archive.stat().st_mode&0o777,0o700)
        self.assertEqual((archive/'archive.json').stat().st_mode&0o777,0o600)

    def test_changed_audit_missing_year_and_tampered_archive_are_rejected(self):
        root,audit=self.fixture('A')
        original=json.loads(audit.read_text())
        self.edit(audit,lambda d:d['sources'][0].update(configuration={}))
        with self.assertRaises(ArtifactError):bench.archive(root,audit,self.root/'bad')
        self.publish(audit,original)
        self.edit(audit,lambda d:next(v for k,v in d['sources'][0]['files'].items() if k.endswith('Person.csv'))['years'].pop('2020'))
        with self.assertRaises(ArtifactError):bench.archive(root,audit,self.root/'bad')
        self.publish(audit,original);archive=self.root/'archive';bench.archive(root,audit,archive)
        self.edit(archive/'archive.json',lambda d:d['evidence']['model-proof/report.json']['value'].update(output_bytes=1))
        with self.assertRaises(ArtifactError):bench.read_archive(archive)

    def test_dispose_removes_only_manifest_files_preserves_reports_and_is_repeatable(self):
        root,archive=self.stored();outside=self.root/'prepared';outside.mkdir();(outside/'model.jar').write_bytes(b'keep')
        before={n:fingerprint(root/n) for n in bench.EVIDENCE}
        result=bench.dispose(archive,root)
        self.assertTrue(result['passed']);self.assertEqual(len(result['removed']),20)
        self.assertEqual(bench.output_files(root/'model-proof/retained-output'),set())
        self.assertEqual({n:fingerprint(root/n) for n in bench.EVIDENCE},before)
        self.assertEqual((outside/'model.jar').read_bytes(),b'keep')
        self.assertTrue(bench.dispose(archive,root)['passed'])

    def test_changed_extra_or_missing_output_blocks_all_deletion_before_it_starts(self):
        root,archive=self.stored();output=root/'model-proof/retained-output'
        target=next(output.rglob('Person.csv'));original=target.read_bytes()
        for kind in ('changed','extra','missing'):
            if kind=='changed':target.write_bytes(original.replace(b'same',b'fake'))
            if kind=='extra':(output/'unexpected').write_text('keep')
            if kind=='missing':target.unlink()
            remaining=bench.output_files(output)
            with self.subTest(kind=kind),self.assertRaises(ArtifactError):bench.dispose(archive,root)
            self.assertEqual(bench.output_files(output),remaining)
            self.assertFalse((root/'model-proof/benchmark-disposal.json').exists())
            if kind in ('changed','missing'):target.write_bytes(original)
            if kind=='extra':(output/'unexpected').unlink()

    def test_metadata_change_and_wrong_source_or_nested_archive_block_disposal(self):
        root,archive=self.stored();original=json.loads((root/'report.json').read_text())
        self.edit(root/'report.json',lambda d:d.update(extra='changed'))
        with self.assertRaises(ArtifactError):bench.dispose(archive,root)
        self.publish(root/'report.json',original)
        with self.assertRaises(ArtifactError):bench.dispose(archive,self.root/'wrong')
        nested=root/'archive';nested.mkdir();(nested/'archive.json').write_bytes((archive/'archive.json').read_bytes())
        with self.assertRaises(ArtifactError):bench.dispose(nested,root)
        self.assertEqual(len(bench.output_files(root/'model-proof/retained-output')),20)

    def test_symlink_directory_never_deletes_its_target(self):
        root,archive=self.stored();output=root/'model-proof/retained-output'
        target=self.root/'outside';target.mkdir();(target/'canary').write_text('keep')
        (output/'linked').symlink_to(target,target_is_directory=True)
        with self.assertRaises(ArtifactError):bench.dispose(archive,root)
        self.assertEqual((target/'canary').read_text(),'keep')
        self.assertEqual(len(list(output.rglob('Person.csv'))),2)

    def test_file_replacement_after_hashing_is_detected_before_unlink(self):
        root,archive=self.stored();output=root/'model-proof/retained-output'
        original=bench.verified_file;calls=[]
        def replace(path,expected):
            state=original(path,expected);calls.append(path)
            if len(calls)==20:
                first=calls[0];replacement=first.with_name('replacement')
                replacement.write_bytes(first.read_bytes());replacement.replace(first)
            return state
        with patch.object(bench,'verified_file',side_effect=replace),self.assertRaises(ArtifactError):bench.dispose(archive,root)
        self.assertEqual(len(bench.output_files(output)),20)

    def test_metadata_changed_during_full_hash_check_prevents_any_unlink(self):
        root,archive=self.stored();original=bench.verified_file;calls=[]
        def change(path,expected):
            state=original(path,expected);calls.append(path)
            if len(calls)==20:self.edit(root/'report.json',lambda d:d.update(changed_after_start=True))
            return state
        with patch.object(bench,'verified_file',side_effect=change),self.assertRaises(ArtifactError):bench.dispose(archive,root)
        self.assertEqual(len(bench.output_files(root/'model-proof/retained-output')),20)
        self.assertFalse((root/'model-proof/benchmark-disposal.json').exists())

    def test_interrupt_after_unlink_resumes_with_pending_journal(self):
        root,archive=self.stored();original=bench.os.unlink;deleted=[]
        def interrupted(path,*args,**kwargs):
            original(path,*args,**kwargs)
            if kwargs.get('dir_fd') is not None:
                deleted.append(path)
                raise RuntimeError('fictional process interruption after unlink')
        with patch.object(bench.os,'unlink',side_effect=interrupted),self.assertRaises(RuntimeError):bench.dispose(archive,root)
        journal=json.loads((root/'model-proof/benchmark-disposal.json').read_text())
        self.assertIsNotNone(journal['pending']);self.assertEqual(len(deleted),1)
        result=bench.dispose(archive,root)
        self.assertTrue(result['passed']);self.assertEqual(len(result['removed']),20)

    def test_active_disposal_lock_is_not_bypassed(self):
        root,archive=self.stored();path=root/'model-proof/benchmark-disposal.lock'
        with path.open('w') as stream:
            bench.fcntl.flock(stream,bench.fcntl.LOCK_EX|bench.fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):bench.dispose(archive,root)
        self.assertEqual(len(bench.output_files(root/'model-proof/retained-output')),20)

    def test_cli_requires_explicit_unique_labels_and_never_overwrites_reports(self):
        root,_=self.fixture('A');output=self.root/'report'
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(bench.main(['compare','--case','A='+str(root),'--output',str(output)]),0)
            self.assertEqual(bench.main(['compare','--case','A='+str(root),'--output',str(output)]),2)
            self.assertEqual(bench.main(['compare','--case','A='+str(root),'--case','A='+str(root),
                '--output',str(self.root/'duplicate')]),2)
        self.assertIn('Pending',(output/'comparison.md').read_text())


if __name__=='__main__':unittest.main()
