"""(C) Copyright 2026, by Ross Richardson

Native launch configuration prevents public access to privileged runtime capabilities.
Uses local fictional configuration and mocked account metadata; no host changes.
@author ross richardson
"""
from contextlib import contextmanager, redirect_stdout
import io
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from .privilege_config import load_public_config, public_identity_check, separate_account, trusted_installation
from .vm_frontend import main as frontend_main
from .vm_web import main as combined_main


class PrivilegeConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'public.toml'
        self.text=(Path(__file__).with_name('vm')/'public.toml.example').read_text()
        self.path.write_text(self.text)

    def test_public_configuration_has_no_private_credentials_or_paths(self):
        value=load_public_config(self.path)
        self.assertEqual(value.rpc_socket,Path('/run/simpaths-executor/control.sock'))
        self.assertFalse(hasattr(value,'state'))
        self.assertFalse(hasattr(value,'dsn_file'))
        for addition in ('\n[database]\ndsn="secret"\n','\n[model]\nimage="secret"\n',
                         '\n[notifications]\nenabled=true\n'):
            self.path.write_text(self.text+addition)
            with self.assertRaises(ValueError):load_public_config(self.path)
        self.path.write_text(self.text.replace('frontend =','dsn_file = "/private/secret"\nfrontend ='))
        with self.assertRaises(ValueError):load_public_config(self.path)

    def test_public_check_does_not_resolve_users_contact_services_or_read_credentials(self):
        with patch('pwd.getpwnam',side_effect=AssertionError('No identity access')), \
                patch('socket.socket',side_effect=AssertionError('No connection')),redirect_stdout(io.StringIO()):
            self.assertEqual(frontend_main(['check','--config',str(self.path)]),0)
        self.assertEqual(list(self.path.parent.iterdir()),[self.path])

    def test_invalid_origin_socket_or_public_setting_is_rejected(self):
        for old,new in (('https://multirun.example.org','http://multirun.example.org'),
                ('https://multirun.example.org','https://user:password@multirun.example.org'),
                ('port = 5002','port = true'),
                ('/run/simpaths-executor/control.sock','../escape'),
                ('executor_user = "simpaths-online"','executor_user = "root; command"')):
            self.path.write_text(self.text.replace(old,new))
            with self.subTest(new=new),self.assertRaises(ValueError):load_public_config(self.path)

    def test_same_uid_root_and_docker_membership_cannot_launch_public_process(self):
        for current,other in ((0,1001),(1001,0),(1001,1001)):
            with patch('os.getuid',return_value=current),patch('pwd.getpwnam',return_value=SimpleNamespace(pw_uid=other)):
                with self.assertRaises(ValueError):separate_account('service')
        with patch('os.getuid',return_value=1001),patch('pwd.getpwnam',return_value=SimpleNamespace(pw_uid=1002)), \
                patch('grp.getgrnam',return_value=SimpleNamespace(gr_gid=999)),patch('os.getgid',return_value=1001), \
                patch('os.getgroups',return_value=[999]):
            with self.assertRaises(ValueError):public_identity_check('executor')

    def test_public_process_rejects_accessible_privileged_socket_or_inherited_secrets(self):
        with patch('os.getuid',return_value=1001),patch('pwd.getpwnam',return_value=SimpleNamespace(pw_uid=1002)), \
                patch('grp.getgrnam',side_effect=KeyError),patch.dict(os.environ,{},clear=True):
            with patch('os.access',return_value=True),self.assertRaises(ValueError):public_identity_check('executor')
            with patch('os.access',return_value=False):
                self.assertEqual(public_identity_check('executor').pw_uid,1002)
                for key in ('PGPASSWORD','SMTP_PASSWORD','DOCKER_HOST'):
                    with patch.dict(os.environ,{key:'private'}),self.assertRaises(ValueError):public_identity_check('executor')

    def test_native_trust_check_rejects_service_owned_or_writable_files(self):
        for uid,mode in ((1001,0o100644),(0,0o100664),(0,0o120777)):
            with patch.object(Path,'lstat',return_value=SimpleNamespace(st_uid=uid,st_mode=mode)):
                with self.assertRaises(ValueError):trusted_installation('/opt/example.py')

    def test_recursive_trust_check_rejects_mutable_code_below_trusted_directory(self):
        root=self.path.parent/'code'
        root.mkdir()
        nested=root/'package'
        nested.mkdir()
        source=nested/'module.py'
        source.write_text('value = 1\n')
        scandir=os.scandir
        @contextmanager
        def root_directories(path):
            with scandir(path) as entries:
                values=[]
                for entry in entries:
                    info=entry.stat(follow_symlinks=False)
                    # Both installation directories are trusted; only the leaf
                    # is runtime-owned, reproducing the former bypass.
                    owner=0 if entry.is_dir(follow_symlinks=False) else 1001
                    metadata=SimpleNamespace(st_uid=owner,st_mode=info.st_mode)
                    values.append(SimpleNamespace(path=entry.path,stat=lambda metadata=metadata,**kw:metadata))
                yield values
        with patch('deploy.multirun.privilege_config._trusted_path',return_value=root.stat()), \
                patch('os.scandir',side_effect=root_directories):
            with self.assertRaises(ValueError):trusted_installation(root,directory=True)
        source.unlink()
        source.symlink_to(self.path)
        with patch('deploy.multirun.privilege_config._trusted_path',return_value=root.stat()), \
                patch('os.scandir',side_effect=root_directories):
            with self.assertRaises(ValueError):trusted_installation(root,directory=True)

    def test_administrator_owned_interpreter_symlink_is_supported(self):
        interpreter=Path('/usr/bin/python3')
        if not interpreter.exists():self.skipTest('Native Linux interpreter unavailable')
        original=Path.lstat
        def administrator(path):
            info=original(path)
            return SimpleNamespace(st_uid=0,st_mode=info.st_mode)
        # The sandbox remaps host root ownership to nobody; preserve the real
        # link layout/modes while supplying the installed root UID.
        with patch.object(Path,'lstat',administrator):
            trusted_installation(interpreter,runtime_links=True)

    def test_old_combined_launcher_cannot_silently_start_hosted_service(self):
        private=(Path(__file__).with_name('vm')/'multirun.toml.example').read_text().replace(
            'sha256:REPLACE_WITH_64_HEXADECIMAL_DIGITS','sha256:'+'a'*64)
        self.path.write_text(private)
        with patch('socket.socket',side_effect=AssertionError('No side effects')), \
                patch('sys.stderr',io.StringIO()):
            self.assertEqual(combined_main(['serve','--config',str(self.path)]),1)
            self.assertEqual(combined_main(['serve','--private-combined','--config',str(self.path)]),1)


if __name__=='__main__':
    unittest.main()
