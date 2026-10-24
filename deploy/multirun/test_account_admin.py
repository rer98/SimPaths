"""(C) Copyright 2026, by Ross Richardson

Operator command boundaries for explicit approval periods and independent grants.
Fictional arguments only; no credentials, database, service launch or messages.
@author ross richardson
"""
from contextlib import redirect_stdout,redirect_stderr
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import account_admin,vm_web


class AccountAdminTests(unittest.TestCase):
    def test_duration_and_permanent_modes_are_mutually_exclusive(self):
        parser=account_admin.parser()
        for args in (['--days','365','--permanent'],['--days','0'],['--days','366']):
            with self.subTest(args=args),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                parser.parse_args(['--config','/fictional','approve','--email','alice@example.org',*args])

    def test_revoke_cannot_be_confused_with_permanent_approval(self):
        for command in ('revoke','revoke-dataset','list'):
            argv=['--config','/fictional',command,'--email','alice@example.org','--dataset','data','--permanent']
            with self.subTest(command=command),self.assertRaises(ValueError):account_admin.main(argv)

    def test_vm_compatibility_command_delegates_without_bootstrap_or_migration(self):
        with patch.object(account_admin,'main',return_value=0) as operation,redirect_stdout(io.StringIO()):
            self.assertEqual(vm_web.main(['approve','--config','/fictional','--email','alice@example.org','--permanent']),0)
        operation.assert_called_once_with(['--config','/fictional','approve','--email','alice@example.org','--permanent'])

    def test_private_list_uses_read_only_operator_without_launching_an_application(self):
        value=dict(passed=True,operation='list',accounts=[],mail_sent=False,data_deleted=False)
        with patch('deploy.multirun.vm_config.load_config',return_value=SimpleNamespace(frontend=Path('/fictional'))),\
                patch.object(account_admin,'operate',return_value=value) as operation,redirect_stdout(io.StringIO()) as output:
            self.assertEqual(account_admin.main(['--config','/fictional','list','--json']),0)
        self.assertEqual(operation.call_args.args[1].command,'list')
        self.assertIn('"data_deleted": false',output.getvalue())


if __name__=='__main__':unittest.main()
