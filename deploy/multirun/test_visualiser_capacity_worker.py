"""(C) Copyright 2026, by Ross Richardson

Guard, measurement and cleanup tests for isolated Visualiser capacity trials.
@author ross richardson
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy.multirun import visualiser_capacity_worker as worker


class CapacityWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def cgroup(self):
        root=self.root/'cgroup';root.mkdir()
        contents={'memory.max':str(3072*worker.MIB),'memory.swap.max':'0','cpu.max':'100000 100000',
            'memory.current':'1048576','memory.peak':'2097152','memory.stat':'inactive_file 262144\n',
            'memory.events':'max 0\noom 0\noom_kill 0\n',
            'cpu.stat':'usage_usec 10\nnr_periods 2\nnr_throttled 0\nthrottled_usec 0\n',
            'io.stat':'8:0 rbytes=16 wbytes=32 rios=1 wios=2 dbytes=0 dios=0\n'}
        for kind in ('cpu','memory','io'):
            contents[kind+'.pressure']='some avg10=0.00 avg60=0.00 avg300=0.00 total=8\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=2\n'
        for name,value in contents.items():(root/name).write_text(value)
        return root

    def fixture(self):
        root=self.root/'fixture';root.mkdir();build=self.root/'build';build.mkdir()
        for name in ('runner.cjs','calculation.cjs'):(build/name).write_text('// pinned '+name)
        hashes={name:worker.receipt(build,name,worker.MIB)['sha256'] for name in ('runner.cjs','calculation.cjs')}
        worker.write_json(build/'build.json',dict(format='simpaths.visualiser.build.v1',mode='development-paired',files=hashes))
        seeds=['9001','9002'];files={};configs=[]
        for index,role in enumerate(('baseline','scenario_1')):
            filename=role+'.json';worker.write_json(root/filename,[dict(run='606',scenario=role,year=2019,metric_value=None)])
            files[filename]=worker.receipt(root,filename,worker.MIB)
            configs.append(dict(id=role,name=role,role=role,runs=[dict(seed=s,source_seed='606',summary=filename) for s in seeds]))
        manifest=dict(format=worker.FORMAT,mode='summary-replay',seeds=seeds,annual_years=list(range(2019,2071)),
            configurations=configs,files=files,backend={name+'_sha256':hashes[name+'.cjs'] for name in ('runner','calculation')})
        worker.write_json(root/'manifest.json',manifest)
        return root,build,manifest

    def test_exact_whole_group_cpu_memory_and_no_swap_are_required(self):
        root=self.cgroup();self.assertEqual(worker.require_limits(3072,root)['cpu_max'],[100000,100000])
        for name,value in [('memory.max','max'),('memory.max',str(4096*worker.MIB)),('memory.swap.max','1'),
                           ('cpu.max','max 100000'),('cpu.max','150000 100000'),('cpu.max','0 0')]:
            original=(root/name).read_text();(root/name).write_text(value)
            with self.subTest(name=name,value=value),self.assertRaises(ValueError):worker.require_limits(3072,root)
            (root/name).write_text(original)

    def test_metrics_distinguish_working_from_charged_memory_and_fail_closed(self):
        root=self.cgroup();value=worker.snapshot(root)
        self.assertEqual(value['working_bytes'],786432);self.assertEqual(value['memory_current_bytes'],1048576)
        self.assertEqual(value['io']['8:0']['rbytes'],16);self.assertEqual(value['pressure_usec']['memory']['some'],8)
        (root/'memory.stat').write_text('anon 1048576\n')
        with self.assertRaises(ValueError):worker.snapshot(root)

    def test_sampler_records_stages_and_reports_missing_metrics(self):
        root=self.cgroup();sampler=worker.Sampler(self.root/'samples.jsonl',root,interval=60).start()
        sampler.stage='aggregate';sampler.capture();sampler.stop()
        samples=[json.loads(line) for line in (self.root/'samples.jsonl').read_text().splitlines()]
        self.assertEqual(samples[1]['stage'],'aggregate');self.assertEqual(sampler.peaks['aggregate']['working_bytes'],786432)
        other=worker.Sampler(self.root/'other.jsonl',root,interval=60).start();other.errors.append('FileNotFoundError')
        with self.assertRaises(RuntimeError):other.stop()
        self.assertTrue(other.target.closed)

    def test_source_inventory_and_build_hashes_are_both_verified(self):
        fixture,build,manifest=self.fixture()
        self.assertEqual(worker.load_fixture(fixture,build,self.root)[0],manifest)
        (fixture/'baseline.json').write_text('[]')
        with self.assertRaisesRegex(ValueError,'checksum'):worker.load_fixture(fixture,build,self.root)
        manifest['files']['baseline.json']=worker.receipt(fixture,'baseline.json',worker.MIB)
        worker.write_json(fixture/'manifest.json',manifest);(build/'runner.cjs').write_text('// changed')
        with self.assertRaisesRegex(ValueError,'Build checksum'):worker.load_fixture(fixture,build,self.root)

    def test_background_sampler_failure_retains_the_exact_counter_error_and_still_fails(self):
        root=self.cgroup();sampler=worker.Sampler(self.root/'samples.jsonl',root,interval=.001).start()
        try:
            (root/'memory.stat').write_text('inactive_file not-a-number\n')
            self.assertTrue(sampler.event.wait(2),'The failing sampler did not stop')
            saved=json.loads((self.root/'samples-errors.json').read_text())
            self.assertEqual(saved['errors'][0]['error_type'],'ValueError')
            self.assertIn('memory.stat',saved['errors'][0]['message'])
            self.assertIn('not-a-number',saved['errors'][0]['message'])
            self.assertEqual(saved['errors'][0]['stage'],'verification')
            # Recovering access permits a final diagnostic counter snapshot;
            # the lost interval must still invalidate the benchmark.
            (root/'memory.stat').write_text('inactive_file 262144\n')
            with self.assertRaisesRegex(RuntimeError,'not-a-number'):sampler.stop()
            self.assertIsNotNone(sampler.final_metrics)
            self.assertTrue(sampler.target.closed)
            with self.assertRaisesRegex(RuntimeError,'not-a-number'):sampler.stop()
        finally:
            if not sampler.stopped:
                try:sampler.stop()
                except RuntimeError:pass

    def test_successful_sampler_stop_is_idempotent_without_a_second_capture(self):
        root=self.cgroup();sampler=worker.Sampler(self.root/'samples.jsonl',root,interval=60).start()
        final=sampler.stop();count=sampler.samples
        self.assertEqual(sampler.stop(),final);self.assertEqual(sampler.samples,count)

    def test_path_traversal_symlinks_and_shared_hardlinks_are_rejected(self):
        (self.root/'good').write_text('private');(self.root/'link').symlink_to('good')
        for path in ('../good','/good','./good','one//good','link'):
            with self.subTest(path=path),self.assertRaises((ValueError,OSError)):
                worker.receipt(self.root,path,worker.MIB)
        os.link(self.root/'good',self.root/'hard')
        with self.assertRaises(ValueError):worker.receipt(self.root,'hard',worker.MIB)

    def test_duplicate_or_nonfinite_json_is_not_accepted(self):
        for value in ('{"a":1,"a":2}','{"a":NaN}','{"a":Infinity}'):
            with self.assertRaises(ValueError):worker.decode(value)

    def test_failure_preserves_failed_report_and_removes_bulk_private_work(self):
        output=self.root/'output';fixture,build,_=self.fixture()
        args=argparse.Namespace(output=output,fixture=fixture,build=build,raw_root=self.root,
            frontend=self.root,memory_mib=3072)
        with patch.object(worker,'require_limits',side_effect=ValueError('wrong quota')):
            with self.assertRaises(ValueError):worker.execute(args)
        report=json.loads((output/'report.json').read_text())
        self.assertFalse(report['passed']);self.assertTrue(report['cleanup']);self.assertFalse((output/'private').exists())
        self.assertEqual(report['error_type'],'ValueError');self.assertEqual((output/'report.json').stat().st_mode&0o777,0o600)
        self.assertTrue((output/'COPYRIGHT.md').is_file())

    def test_nonempty_output_is_never_reused_or_deleted(self):
        output=self.root/'output';output.mkdir(mode=0o700);(output/'keep').write_text('untouched')
        args=argparse.Namespace(output=output,memory_mib=3072)
        with self.assertRaises(ValueError):worker.execute(args)
        self.assertEqual((output/'keep').read_text(),'untouched')

    @unittest.skipUnless(shutil.which('node'),'Node is required for the replay guard test')
    def test_replay_changes_only_role_and_synthetic_seed_and_never_overwrites(self):
        source=self.root/'source.json';rows=[dict(year=2019,run='606',scenario='baseline',metric_value=None,n=30,
            variable='Hours worked',stratifier='Overall')]
        source.write_text(json.dumps(rows));outputs=[self.root/'first.json',self.root/'second.json']
        request=dict(runs=[dict(seed=str(9000+i),source_seed='606',role=role,summary=str(source),output=str(output))
            for i,(role,output) in enumerate(zip(('baseline','scenario_1'),outputs))])
        config=self.root/'request.json';config.write_text(json.dumps(request))
        script=Path(worker.__file__).parent/'visualiser'/'capacity_replay.cjs'
        result=subprocess.run(['node',str(script),str(config)],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        for i,output in enumerate(outputs):
            expected=copy.deepcopy(rows);expected[0].update(run=str(9000+i),scenario=request['runs'][i]['role'])
            self.assertEqual(json.loads(output.read_text()),expected)
        self.assertEqual(json.loads(source.read_text()),rows)
        repeated=subprocess.run(['node',str(script),str(config)],capture_output=True,timeout=10)
        self.assertNotEqual(repeated.returncode,0);self.assertEqual(json.loads(source.read_text()),rows)

    def test_bounded_command_keeps_only_explicit_environment_and_descriptors(self):
        with patch.object(worker.subprocess,'Popen') as called:
            called.return_value.wait.return_value=0
            worker.command(None,['python3','helper.py'],pass_fds=(7,8))
        args,kwargs=called.call_args
        self.assertEqual(args[0][:4],['timeout','--signal=TERM','--kill-after=5','3600'])
        self.assertEqual(kwargs['pass_fds'],(7,8));self.assertEqual(set(kwargs['env']),{'PATH','LANG'})
        called.return_value.wait.assert_called_once_with(timeout=3610);self.assertTrue(kwargs['start_new_session'])

    def test_interrupted_command_terminates_and_reaps_its_process_group(self):
        with patch.object(worker.subprocess,'Popen') as called,patch.object(worker.os,'killpg') as killed:
            called.return_value.pid=123;called.return_value.wait.side_effect=[KeyboardInterrupt(),0]
            with self.assertRaises(KeyboardInterrupt):worker.command(None,['node','runner.cjs'])
            killed.assert_called_once_with(123,worker.signal.SIGTERM)
            self.assertEqual(called.return_value.wait.call_count,2)

    def test_raw_rows_allow_sparse_seed_cells_but_replay_does_not(self):
        paths=[];years=list(range(2019,2071))
        for index in range(2):
            rows=[dict(year=year,n_runs=2,paired_n_runs=2 if index else 0,
                paired_mean_delta=.1 if index else None) for year in years]
            rows.append(dict(year=2019,n_runs=1,paired_n_runs=1 if index else 0,paired_mean_delta=.2 if index else None))
            path=self.root/('aggregate-'+str(index)+'.json');path.write_text(json.dumps(rows));paths.append(path)
        manifest=dict(mode='raw-native',seeds=['606','607'],annual_years=years)
        self.assertEqual(worker.validate_rows(paths,manifest)['complete_seed_paired_cells'],52)
        with self.assertRaisesRegex(ValueError,'Replay repetition'):worker.validate_rows(paths,dict(manifest,mode='summary-replay'))


if __name__=='__main__':unittest.main()
