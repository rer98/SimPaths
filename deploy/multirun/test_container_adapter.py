"""(C) Copyright 2026, by Ross Richardson

Model-owned container adapter checks for image/JAR binding and prepared requests.

@author ross richardson
"""
from types import SimpleNamespace
import json
import shlex
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from deploy._workflow import frontend_path
from deploy.multirun.artifacts import ArtifactError
from deploy.multirun.container_adapter import SimPathsContainerAdapter, container_submission
from deploy.multirun import test_queue_adapter

IMAGE = 'sha256:' + 'b' * 64


class ContainerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_queue_adapter.QueueAdapterTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.adapter = SimPathsContainerAdapter(self.fixture.prepared, IMAGE)
        self.spec = {**self.fixture.spec, 'model_digest': IMAGE}
        self.lease = SimpleNamespace(specification=self.spec, configuration_id='savings-0001',
                                    resources=dict(cpu_millis=2000, memory_mib=4096, storage_mib=6144))

    def test_submission_pins_runtime_image_and_keeps_native_seed_plan(self):
        arguments = container_submission(self.fixture.configuration.editable_configuration(),
                                         self.fixture.prepared, IMAGE)
        self.assertEqual(arguments['model_digest'], IMAGE)
        self.assertEqual(arguments['seed_plan'], ['606', '607', '608'])
        self.assertEqual(self.adapter.receipt['identity']['model']['sha256'], 'a' * 64)

    def test_request_is_readonly_input_plan_with_jar_hash_and_native_yaml(self):
        command = self.adapter.container_command(self.lease, self.fixture.work)
        self.assertEqual(command.image, IMAGE)
        self.assertEqual(command.argv, ('/bin/sh', '/request/run.sh', '2g'))
        self.assertEqual(command.inputs, str(self.fixture.prepared))
        manifest = (self.fixture.work / 'inputs.sha256').read_text()
        self.assertIn('a' * 64 + '  /inputs/model.jar', manifest)
        self.assertEqual((self.fixture.work / 'input-files.txt').read_text(), '')
        self.assertEqual((self.fixture.work / 'run.yml').read_text(),
                         self.fixture.configuration.native_yaml('savings-0001'))
        script = (self.fixture.work / 'run.sh').read_text()
        self.assertNotIn(str(self.fixture.prepared), script)
        java = next(line for line in script.replace('\\\n', ' ').splitlines() if line.startswith('exec '))
        arguments = shlex.split(java)
        option = '-Djasmine.memory.monitor.enabled=true'
        self.assertEqual(arguments.count(option), 1)
        self.assertLess(arguments.index(option), arguments.index('-cp'))

    def test_changed_image_or_insufficient_resources_rejected(self):
        self.spec['model_digest'] = 'sha256:' + 'c' * 64
        with self.assertRaises(ArtifactError):
            self.adapter.container_command(self.lease, self.fixture.work)
        self.spec['model_digest'] = IMAGE
        self.lease.resources['memory_mib'] = 2048
        with self.assertRaises(ArtifactError):
            self.adapter.container_command(self.lease, self.fixture.work)

    def test_native_completion_validation_is_preserved(self):
        self.fixture.outputs()
        receipts = self.adapter.validate(self.lease, self.fixture.work)
        self.assertEqual([r['seed'] for r in receipts], ['606', '607', '608'])
        (self.fixture.work / 'output/606/csv/Person.csv').write_text('')
        with self.assertRaises(ArtifactError):
            self.adapter.validate(self.lease, self.fixture.work)

    def test_low_space_is_durable_storage_rejection_without_docker_launch(self):
        old_path = list(sys.path)
        self.addCleanup(lambda: setattr(sys, 'path', old_path))
        sys.path.insert(0, str(frontend_path()))
        from jasmine_web.batch.docker_executor import DockerExecutor
        docker = Mock()
        executor = DockerExecutor(self.fixture.root / 'executor', approved_images=[IMAGE], docker=docker)
        attempt = str(uuid4())
        lease = SimpleNamespace(**vars(self.lease), attempt_id=attempt, execution_key='batch-' + attempt)
        with patch('deploy.multirun.queue_adapter.shutil.disk_usage',
                   return_value=SimpleNamespace(free=1 << 20)), executor.exclusive():
            rejected = executor.start(lease, self.adapter)
            self.assertEqual(rejected['outcome'], 'storage_limit')
            self.assertGreater(rejected['required_bytes'], 1 << 30)
            self.assertEqual(rejected['available_bytes'], 1 << 20)
            workspace = executor.workspace(lease)
            self.assertFalse((workspace / 'create-intent.json').exists())
            self.assertFalse((workspace / 'request/run.yml').exists())
            # Recovery reports the same reason without trying another launch.
            self.assertEqual(executor.inspect(lease), rejected)
            self.assertEqual(executor.start(lease, self.adapter), rejected)
            self.assertEqual(json.loads((workspace / 'exit.json').read_text()), rejected)
        docker.call.assert_not_called()
        docker.inspect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
