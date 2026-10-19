"""(C) Copyright 2026, by Ross Richardson

Safe workload descriptions and operator-only hot controls without live services.
@author ross richardson
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path
from .performance_review import main, workload


class PerformanceReviewTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.frontend=frontend_path()

    def args(self, *tail):
        return ['--frontend',str(self.frontend),'--state',str(self.root),*tail]

    def test_workload_has_only_counts_collectors_and_verified_release_identifiers(self):
        row=dict(configuration_id='baseline',execution_run=None,specification=dict(
            label='PRIVATE NAME',seeds=['PRIVATE SEED'],model_digest='sha256:'+'a'*64,run_sets=[dict(
                id='baseline',starting_allocation=dict(profile='research-100000'),parameters=dict(
                    name='PRIVATE LABEL',common=dict(population=100000,start_year=2019,end_year=2070),
                    input='/PRIVATE/INPUT',run_sets=[dict(id='baseline',collector_args=dict(
                        persistPersons=True,dataDumpTimePeriod=1.0,private='PRIVATE VALUE',exportToCSV='PRIVATE'))]))]))
        result=workload(row)
        self.assertEqual((result['initial_population'],result['simulation_years'],result['repetitions']),(100000,52,1))
        self.assertEqual(result['collectors'],dict(persistPersons=True,dataDumpTimePeriod=1.0))
        self.assertEqual(result['resource_profile'],'research-100000')
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_hot_controls_do_not_connect_to_database_docker_or_smtp(self):
        from jasmine_web.batch.performance_review import read_control
        with patch('socket.socket',side_effect=AssertionError('No network')),redirect_stdout(io.StringIO()):
            self.assertEqual(main(self.args('enable','--sample-seconds','5')),0)
            first=read_control(self.root)
            self.assertEqual(first['sample_seconds'],5)
            self.assertEqual(main(self.args('disable')),0)
            self.assertFalse(read_control(self.root)['enabled'])
            self.assertEqual(main(self.args('enable','--sample-seconds','30')),0)
            self.assertNotEqual(read_control(self.root)['generation'],first['generation'])

    def test_invalid_settings_fail_before_creating_a_control_file(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(self.args('enable','--sample-seconds','1')),2)
        self.assertFalse((self.root/'performance-review.json').exists())

    def test_java_launch_switch_and_platform_descriptors_do_not_contact_services(self):
        from jasmine_web.batch.performance_review import read_control
        with patch('socket.socket',side_effect=AssertionError('No network')),redirect_stdout(io.StringIO()):
            self.assertEqual(main(self.args('java-enable','--sample-seconds','30')),0)
            self.assertTrue(read_control(self.root,java=True)['enabled'])
            self.assertFalse(read_control(self.root)['enabled'])
            self.assertEqual(main(self.args('java-disable')),0)
            self.assertFalse(read_control(self.root,java=True)['enabled'])
            self.assertEqual(main(self.args('platform','--provider','Contabo','--plan','VPS12','--storage-kind','ssd')),0)

    def test_vm_and_local_control_settings_cannot_be_mixed(self):
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            main(self.args('--config','/etc/fictional.toml','enable'))
