"""(C) Copyright 2026, by Ross Richardson

Private password setup refuses noninteractive input, active services and mismatches.
Fictional credentials only; no database, SMTP or public services are contacted.
@author ross richardson
"""
from contextlib import redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path
import sys
sys.path.insert(0,str(frontend_path()))
from jasmine_web.batch.administrator_password import make_credential,read_credential,check_password
from .administrator_config import change_password
from .artifacts import ArtifactError
from .maintenance import state_guard
from .releases import atomic_json


class AdministratorConfigTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.state=Path(tmp.name);self.state.chmod(0o700)
        self.options=SimpleNamespace(state=self.state)

    def test_password_must_be_entered_privately_in_an_interactive_terminal(self):
        with patch('sys.stdin.isatty',return_value=False),patch('getpass.getpass') as prompt:
            with self.assertRaises(ValueError):change_password(self.options)
        prompt.assert_not_called();self.assertFalse((self.state/'administrator-password.json').exists())

    def test_active_service_blocks_rotation_before_requesting_a_password(self):
        with state_guard(self.state),patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass') as prompt:
            with self.assertRaises(ArtifactError):change_password(self.options)
        prompt.assert_not_called()

    def test_mismatched_passwords_preserve_the_original_credential(self):
        path=self.state/'administrator-password.json';atomic_json(path,make_credential('existing fictional password'))
        before=path.read_bytes()
        with patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',side_effect=['a'*20,'b'*20]):
            with self.assertRaises(ValueError):change_password(self.options)
        self.assertEqual(before,path.read_bytes())

    def test_password_creation_is_private_and_outputs_neither_password_nor_hash(self):
        password='new fictional administrator password'
        with patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',side_effect=[password,password]),redirect_stdout(io.StringIO()) as output:
            change_password(self.options)
        credential=read_credential(self.state/'administrator-password.json')
        self.assertTrue(check_password(password,credential))
        self.assertNotIn(password,output.getvalue());self.assertNotIn(credential['digest'],output.getvalue())


if __name__=='__main__':unittest.main()
