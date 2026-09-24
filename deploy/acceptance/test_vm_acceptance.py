"""(C) Copyright 2026, by Ross Richardson

Check isolation of VM acceptance configuration and private-network assertions.

@author ross richardson
"""
import copy
import hashlib
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import patch, Mock

import runpy
load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
subject = load_tool('acceptance/run_vm_acceptance.py')
isolated_config = subject.isolated_config
assert_private_network = subject.assert_private_network
new_project_name = subject.new_project_name
remove_test_networks = subject.remove_test_networks

class VmAcceptanceTests(unittest.TestCase):
    def test_failed_acceptance_removes_only_its_own_labelled_networks(self):
        name = 'jms-' + hashlib.sha256(b'sid').hexdigest()[:10]
        owned = types.SimpleNamespace(name=name, attrs={'Labels': {
            'jasmine.model_id': 'test-model', 'jasmine.session_id': 'sid'}}, remove=Mock())
        other = types.SimpleNamespace(name=name, attrs={'Labels': {
            'jasmine.model_id': 'other-model', 'jasmine.session_id': 'sid'}}, remove=Mock())
        unrelated = types.SimpleNamespace(name='unrelated', attrs=owned.attrs, remove=Mock())
        client = types.SimpleNamespace(networks=types.SimpleNamespace(list=Mock(return_value=[owned, other, unrelated])))
        remove_test_networks(client, 'test-model')
        owned.remove.assert_called_once()
        other.remove.assert_not_called()
        unrelated.remove.assert_not_called()
        client.networks.list.assert_called_once_with(filters={'label': 'jasmine.model_id=test-model'})

    def test_compose_frontend_is_not_classified_as_a_model(self):
        # Exercise the production classifier, not a copy of its naming rule.
        source = load_tool('_workflow.py').frontend_path()/'jasmine_web/container_identity.py'
        if not source.is_file():
            self.skipTest('JAS-mine-web checkout required for classifier regression')
        spec = importlib.util.spec_from_file_location('vm_test_container_identity', source)
        identity = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(identity)
        self.assertFalse(identity.is_jasmine_container(['jasmine-test-example-web:latest'], {}))
        project = new_project_name()
        for separator in ('-', '_'):
            with self.subTest(separator=separator):
                image = f'{project}{separator}web:latest'
                self.assertFalse(identity.is_jasmine_container([image], {}))
        # Actual model labels must still identify a model with any image name.
        self.assertTrue(identity.is_jasmine_container(
            [f'{project}-web:latest'], {identity.JASMINE_SESSION_ID_LABEL: 's', identity.JASMINE_MODEL_ID_LABEL: 'm'}))

    def test_isolated_config_preserves_production_network_and_limits(self):
        template = {'services': {'redis': {'ports':['127.0.0.1:6379:6379'], 'volumes':['redis-data:/data']},
            'web': {'build':{'context':'..','args':{'REQUIREMENTS_FILE':'requirements-vm.txt'}},
                'network_mode':'host','environment':{'DEPLOY_MODE':'vm'},'env_file':'.env',
                'command':['uvicorn','app:app'],'volumes':[]}}, 'volumes':{'redis-data':None}}
        before = copy.deepcopy(template)
        actual = isolated_config(template, Path('/tmp/codex-rer/test'), 15101, 16379)
        self.assertEqual(template, before)
        self.assertEqual(actual['services']['redis']['ports'], ['127.0.0.1:16379:6379'])
        web = actual['services']['web']
        self.assertEqual(web['network_mode'], 'host')
        self.assertEqual(web['environment']['DEPLOY_MODE'], 'vm')
        self.assertEqual(web['environment']['REDIS_URL'], 'redis://127.0.0.1:16379/0')
        self.assertIn('127.0.0.1', web['command'])
        self.assertEqual(web['command'][-2:], ['--workers','1'])
        self.assertTrue(all('/tmp/codex-rer/test/' in v for v in web['volumes'][1:]))

    def network_fixture(self):
        name = 'jms-' + hashlib.sha256(b'sid').hexdigest()[:10]
        labels = {'jasmine.session_id': 'sid', 'jasmine.model_id': 'model',
                  'jasmine.deployment_id': '1fb49425-de79-4270-a52d-096b3c7da32a'}
        container = types.SimpleNamespace(reload=lambda:None, attrs={
            'Config': {'Labels': labels}, 'HostConfig': {},
            'NetworkSettings': {'Ports': {'7070/tcp': None},
                'Networks': {name: {'NetworkID': 'net', 'IPAddress': '172.30.0.2'}}}})
        network = types.SimpleNamespace(id='net', attrs={
            'Internal': True, 'Driver': 'bridge', 'Labels': labels,
            'IPAM': {'Config': [{'Subnet': '172.30.0.0/24'}]}})
        client = types.SimpleNamespace(networks=types.SimpleNamespace(get=lambda _: network))
        return container, client

    def test_any_published_model_binding_is_rejected(self):
        for address in ('0.0.0.0', '127.0.0.1'):
            with self.subTest(address=address):
                container, client = self.network_fixture()
                container.attrs['NetworkSettings']['Ports']['7070/tcp'] = [{'HostIp':address, 'HostPort':'7100'}]
                with self.assertRaises(AssertionError):
                    assert_private_network(container, client)

    def test_private_address_checks_live_health_and_auth_without_http_proxy(self):
        container, client = self.network_fixture()
        with patch('httpx.get', side_effect=[types.SimpleNamespace(status_code=200), types.SimpleNamespace(status_code=401)]) as get:
            result = assert_private_network(container, client)
        self.assertEqual(result['java_private_endpoint'], 'http://172.30.0.2:7070')
        self.assertFalse(result['published_ports'])
        self.assertEqual(get.call_args_list[0].args, ('http://172.30.0.2:7070/health',))
        self.assertEqual(get.call_args_list[1].args, ('http://172.30.0.2:7070/simulation/status',))
        self.assertTrue(all(c.kwargs == {'timeout':10, 'trust_env':False} for c in get.call_args_list))
