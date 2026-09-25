"""(C) Copyright 2026, by Ross Richardson

Model-owned container adapter checks for image/JAR binding and prepared requests.

@author ross richardson
"""
from types import SimpleNamespace
import unittest

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
        self.assertEqual(command.argv, ('/bin/sh', '/request/run.sh'))
        self.assertEqual(command.inputs, str(self.fixture.prepared))
        manifest = (self.fixture.work / 'inputs.sha256').read_text()
        self.assertIn('a' * 64 + '  /inputs/model.jar', manifest)
        self.assertEqual((self.fixture.work / 'run.yml').read_text(),
                         self.fixture.configuration.native_yaml('savings-0001'))
        self.assertNotIn(str(self.fixture.prepared), (self.fixture.work / 'run.sh').read_text())

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


if __name__ == '__main__':
    unittest.main()
