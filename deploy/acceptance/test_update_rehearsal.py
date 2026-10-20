"""(C) Copyright 2026, by Ross Richardson

Offline archive, release selection and handover guards for update acceptance.
Native database/Docker/systemd checks run separately in run_update_rehearsal.py.
@author ross richardson
"""
import ast
import builtins
from contextlib import redirect_stdout
from copy import deepcopy
import dis
import importlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import shutil
import stat
import tarfile
import tempfile
from types import ModuleType, SimpleNamespace
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

from deploy.acceptance import _application_update as releases
from deploy.acceptance import run_update_rehearsal as proof


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = Path(os.environ.get('JASMINE_WEB_REPO', str(Path.home()/'git/JAS-mine/JAS-mine-web')))


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as output:
        for name, content, kind in entries:
            entry = tarfile.TarInfo(name); entry.mode = 0o755
            entry.type = kind
            if kind == tarfile.REGTYPE:
                entry.size = len(content); output.addfile(entry, io.BytesIO(content))
            else:
                entry.linkname = '../../unrelated'; output.addfile(entry)
    stream.seek(0); return stream


class ArchiveGuards(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_ordinary_files_keep_bytes_and_only_owner_executable_bits(self):
        output = self.root/'bundle'
        result = releases.unpack(archive([('src/run.py',b'fictional code',tarfile.REGTYPE)]), output)
        self.assertEqual(result, {'src/run.py':releases.digest(b'fictional code')})
        self.assertEqual((output/'src/run.py').stat().st_mode & 0o777, 0o700)

    def test_absolute_parent_dot_backslash_and_normalised_paths_are_rejected(self):
        for i, name in enumerate(('/outside','../outside','src/../../outside','src/./run.py','src//run.py','src\\run.py','.git/config')):
            with self.subTest(name=name), self.assertRaises(ValueError):
                releases.unpack(archive([(name,b'fictional',tarfile.REGTYPE)]),self.root/str(i))
        self.assertFalse((self.root/'outside').exists())

    def test_duplicate_archive_members_are_rejected(self):
        stream = archive([('a.py',b'old',tarfile.REGTYPE),('a.py',b'new',tarfile.REGTYPE)])
        with self.assertRaises(ValueError): releases.unpack(stream,self.root/'bundle')
        self.assertEqual((self.root/'bundle/a.py').read_bytes(),b'old')

    def test_link_fifo_and_device_members_are_rejected(self):
        for i, kind in enumerate((tarfile.SYMTYPE,tarfile.LNKTYPE,tarfile.FIFOTYPE,tarfile.CHRTYPE)):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                releases.unpack(archive([('unsafe',b'',kind)]),self.root/str(i))
        self.assertFalse((self.root/'unrelated').exists())

    def test_environment_and_session_key_files_cannot_enter_a_code_bundle(self):
        for i, name in enumerate(('.env','.sesskey','nested/.env','nested/.sesskey')):
            with self.subTest(name=name), self.assertRaises(ValueError):
                releases.unpack(archive([(name,b'FICTIONAL_SECRET',tarfile.REGTYPE)]),self.root/str(i))

    def test_existing_destinations_and_linked_parents_are_rejected(self):
        (self.root/'existing').mkdir(); (self.root/'existing/keep').write_text('keep')
        (self.root/'linked').symlink_to(self.root/'existing',target_is_directory=True)
        for target in (self.root/'existing',self.root/'linked/new'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                releases.unpack(archive([('new',b'data',tarfile.REGTYPE)]),target)
        self.assertEqual((self.root/'existing/keep').read_text(),'keep')

    def test_file_and_total_bounds_precede_copying_the_excess_member(self):
        for option in ('MAX_FILE_BYTES','MAX_BYTES','MAX_FILES'):
            with self.subTest(option=option), patch.object(releases,option,3 if option!='MAX_FILES' else 0), self.assertRaises(ValueError):
                releases.unpack(archive([('a',b'four',tarfile.REGTYPE)]),self.root/option)
            self.assertFalse((self.root/option/'a').exists())

    def test_inventory_refuses_extra_links_hardlinks_and_special_files(self):
        (self.root/'a').write_text('ordinary')
        for kind in ('symlink','hardlink','fifo'):
            extra = self.root/'extra'
            if kind=='symlink': extra.symlink_to(self.root/'a')
            elif kind=='hardlink': os.link(self.root/'a',extra)
            else: os.mkfifo(extra)
            with self.subTest(kind=kind), self.assertRaises(ValueError): releases.inventory(self.root)
            extra.unlink()

    def test_git_refs_cannot_be_options_revisions_with_operators_or_commands(self):
        with patch.object(releases,'git') as git:
            for value in (None,'--upload-pack=anything','HEAD..main','HEAD:app.py','HEAD;pwd','$(pwd)','HEAD^{tree}'):
                with self.subTest(value=value), self.assertRaises(ValueError): releases.commit(ROOT,value)
        git.assert_not_called()

    def test_bad_git_reply_is_not_treated_as_a_commit(self):
        with patch.object(releases,'git',return_value=b'not-a-commit\n'), self.assertRaises(ValueError):
            releases.commit(ROOT,'HEAD')


class ReleaseGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='update-offline-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.previous = releases.bundle(ROOT,FRONTEND,cls.root/'previous',host_ref=releases.PREVIOUS_HOST,frontend_ref=releases.PREVIOUS_FRONTEND)
        cls.candidate = releases.bundle(ROOT,FRONTEND,cls.root/'candidate',host_ref='HEAD',frontend_ref='HEAD')
        cls.future = releases.future(cls.candidate,cls.root/'future')
        cls.historical=releases.bundle(ROOT,FRONTEND,cls.root/'historical',
            host_ref=releases.HISTORICAL_HOST,frontend_ref=releases.HISTORICAL_FRONTEND)
        cls.separated=releases.bundle(ROOT,FRONTEND,cls.root/'separated',
            host_ref=releases.SEPARATED_HOST,frontend_ref=releases.SEPARATED_FRONTEND)

    def test_historical_pair_is_rejected_after_new_schema_versions(self):
        # The retained historical rehearsal used 20 batch migrations. Current
        # releases added recovery and operational history; that old pair must
        # fail the direct-rollback gate until an operator reviews a new pair.
        self.assertNotEqual(self.historical['migrations'], self.candidate['migrations'])
        with self.assertRaisesRegex(ValueError, 'matching reviewed migration histories'):
            releases.compatible(self.historical,self.candidate)
        self.assertNotEqual(self.previous['host']['commit'],self.candidate['host']['commit'])
        self.assertNotEqual(self.previous['frontend']['commit'],self.candidate['frontend']['commit'])
        self.assertEqual({k:len(v) for k,v in self.historical['migrations'].items()},{'single':2,'batch':20})
        self.assertEqual({k:len(v) for k,v in self.separated['migrations'].items()},{'single':2,'batch':22})
        with self.assertRaisesRegex(ValueError,'matching reviewed migration histories'):
            releases.compatible(self.separated,self.candidate)

    def test_current_default_pair_has_32_migrations_and_records_dependency_only_frontend_changes(self):
        review=releases.compatible(self.previous,self.candidate)
        self.assertEqual({k:len(v) for k,v in self.previous['migrations'].items()},{'single':2,'batch':32})
        self.assertEqual(review['changed_public_assets'],[])
        self.assertIn('constraints-runtime.txt',review['changed_deployment_files']['frontend'])
        self.assertIn('requirements-installer.txt',review['changed_deployment_files']['frontend'])
        self.assertIn('dependency downgrades are not rehearsed',review['dependency_environment'])
        self.assertEqual(sum(map(len,review['identical_contract_files'].values())),28)

    def test_changed_result_or_rpc_formats_require_a_new_review(self):
        for component,name in (('frontend','jasmine_web/batch/results.py'),
                ('frontend','jasmine_web/batch/service_rpc.py'),('frontend','jasmine_web/single_rpc.py'),
                ('frontend','jasmine_web/batch/visualiser_storage.py'),
                ('frontend','jasmine_web/batch/performance_exports.py'),
                ('frontend','jasmine_web/batch/processing_handover.py'),
                ('host','deploy/multirun/backup_visualiser.py'),('host','deploy/multirun/releases.py')):
            with self.subTest(file=name),tempfile.TemporaryDirectory() as directory:
                changed=deepcopy(self.candidate)
                root=Path(directory)/component
                shutil.copytree(changed[component]['directory'],root)
                changed[component]['directory']=str(root)
                path=root/name;path.write_bytes(path.read_bytes()+b'\n# fictional format change\n')
                changed[component]['files']=releases.inventory(root)
                with self.assertRaisesRegex(ValueError,'result/recovery/RPC formats'):
                    releases.compatible(self.previous,changed)

    def test_documentation_or_fixture_only_changes_do_not_qualify_as_application_updates(self):
        changed=deepcopy(self.candidate)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'frontend'
            shutil.copytree(changed['frontend']['directory'],root)
            changed['frontend']['directory']=str(root)
            (root/'README.md').write_text('Fictional documentation change\n')
            changed['frontend']['files']=releases.inventory(root)
            changed['frontend']['commit']='f'*40
            with self.assertRaisesRegex(ValueError,'not only documentation or fixtures'):
                releases.compatible(self.candidate,changed)

    def test_sessions_inputs_and_workbooks_are_absent_from_exported_code(self):
        for value in (self.previous,self.candidate):
            self.assertNotIn('.sesskey',value['frontend']['files'])
            self.assertNotIn('.env',value['frontend']['files'])
            self.assertTrue(all(key.startswith('deploy/') for key in value['host']['files']))
            self.assertNotIn('input/DatabaseCountryYear.xlsx',value['host']['files'])

    def test_every_fixture_overlay_has_an_explicit_hash(self):
        for name in releases.FIXTURES:
            key = 'deploy/acceptance/'+name
            expected = releases.digest((ROOT/key).read_bytes())
            for value in (self.previous,self.candidate):
                self.assertEqual(value['fixture_overlays'][key],expected)
                self.assertEqual(value['host']['files'][key],expected)
        for name in releases.FRONTEND_FIXTURES:
            expected=releases.digest((FRONTEND/name).read_bytes())
            for value in (self.previous,self.candidate):
                self.assertEqual(value['frontend_fixture_overlays'][name],expected)
                self.assertEqual(value['frontend']['files'][name],expected)

    def test_synthetic_migrations_append_without_changing_original_sql(self):
        releases.verify(self.future)
        for key, values in self.candidate['migrations'].items():
            self.assertEqual(self.future['migrations'][key][:-1],values)
            self.assertEqual(self.future['migrations'][key][-1]['version'],len(values)+1)
        self.assertEqual({k:len(v) for k,v in self.future['migrations'].items()},
                         {k:len(v)+1 for k,v in self.candidate['migrations'].items()})

    def test_synthetic_code_cannot_pass_the_direct_rollback_gate(self):
        with self.assertRaisesRegex(ValueError,'migration histories'):
            releases.compatible(self.previous,self.future)

    def test_identical_code_cannot_be_presented_as_an_update(self):
        with self.assertRaisesRegex(ValueError,'different application snapshots'):
            releases.compatible(self.candidate,self.candidate)

    def test_changed_or_unlisted_code_is_rejected(self):
        path = Path(self.future['frontend']['directory'])/'unexpected.py'
        path.write_text('# extra fictional file')
        try:
            with self.assertRaisesRegex(ValueError,'bundle changed'): releases.verify(self.future)
        finally: path.unlink()
        path = Path(self.future['frontend']['directory'])/'app.py'; before=path.read_bytes()
        try:
            path.write_bytes(before+b'\n# fictional change\n')
            with self.assertRaisesRegex(ValueError,'bundle changed'): releases.verify(self.future)
        finally: path.write_bytes(before)

    def test_caller_cannot_replace_the_migration_inventory(self):
        changed = deepcopy(self.candidate); changed['migrations']['batch'][0]['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError,'migration inventory'): releases.verify(changed)

    def test_minimal_environment_helper_cannot_create_bytecode_in_selected_code(self):
        from jasmine_web.batch.visualiser import Visualiser
        import threading
        helper=object.__new__(Visualiser)
        helper.runtime=10;helper.stop=threading.Event();helper.timeout=shutil.which('timeout')
        self.assertIsNotNone(helper.timeout)
        # This is the production bounded reader's real minimal environment,
        # which intentionally does not inherit PYTHONDONTWRITEBYTECODE.
        program=('import os,sys; assert "PYTHONDONTWRITEBYTECODE" not in os.environ; '
                 'sys.path.insert(0,sys.argv[1]); '
                 'import jasmine_web.batch.visualiser, jasmine_web.batch.aggregate_sections')
        with releases.read_only_directories(self.previous):
            for component in ('host','frontend'):
                root=Path(self.previous[component]['directory'])
                self.assertFalse(root.stat().st_mode & 0o222)
            helper._read_command([sys.executable,'-c',program,self.previous['frontend']['directory']])
            releases.verify(self.previous)
            self.assertFalse(list(Path(self.previous['frontend']['directory']).rglob('*.pyc')))

    def test_read_only_bundle_allows_separate_synthetic_copy_and_restores_modes_on_error(self):
        roots=[Path(self.candidate[name]['directory']) for name in ('host','frontend')]
        modes={path:stat.S_IMODE(path.stat().st_mode) for root in roots
               for path in (root,*root.rglob('*')) if path.is_dir()}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError,'fictional interruption'):
                with releases.read_only_directories(self.candidate):
                    future=releases.future(self.candidate,Path(directory)/'future')
                    self.assertEqual(len(future['migrations']['batch']),33)
                    releases.verify(self.candidate);releases.verify(future)
                    raise RuntimeError('fictional interruption')
        self.assertEqual({path:stat.S_IMODE(path.stat().st_mode) for path in modes},modes)
        releases.verify(self.candidate)

    def test_loaded_source_must_be_within_the_selected_bundle(self):
        root = Path(self.previous['frontend']['directory'])
        result = releases.loaded_source(SimpleNamespace(__file__=str(root/'app.py')),root)
        self.assertEqual(result['sha256'],self.previous['frontend']['files']['app.py'])
        with self.assertRaises(ValueError): releases.loaded_source(SimpleNamespace(__file__=str(FRONTEND/'app.py')),root)

    def test_import_selection_refuses_application_modules_already_loaded(self):
        with patch.dict(sys.modules,{'jasmine_web':ModuleType('jasmine_web')}), self.assertRaises(ValueError):
            releases.configure(self.previous)

    def test_private_future_bundle_does_not_modify_candidate_or_working_checkouts(self):
        releases.verify(self.candidate); releases.verify(self.previous)
        for component,filename in (('batch','jasmine_web/batch/store.py'),('single','jasmine_web/vm_state.py')):
            name=self.future['migrations'][component][-1]['name']
            self.assertFalse((FRONTEND/filename).with_name(name).exists())

    def test_fresh_processes_import_each_selected_application_serve_its_page_and_read_its_style_source(self):
        with tempfile.TemporaryDirectory() as folder:
            for name,value in (('previous',self.previous),('candidate',self.candidate),('future',self.future)):
                with self.subTest(name=name):
                    result = proof.run_child(Path(folder),value,{},'',name=name,imports_only=True)
                    self.assertTrue(result['help_experiment'])
                    self.assertEqual(result['css_sha256'],value['frontend']['files']['jasmine_web/batch/browser_static/batch.css'])
                    self.assertEqual(result['javascript_sha256'],value['frontend']['files']['static/jasmine_main.js'])
                    for key,component in (('runtime','host'),('browser','frontend')):
                        row = result['sources'][key]
                        self.assertEqual(row['sha256'],value[component]['files'][row['file']])

    def test_public_processes_use_bundled_code_without_private_imports_or_credentials(self):
        program='''
import json,os,sys
from pathlib import Path
from unittest.mock import patch
settings=json.load(sys.stdin)
sys.path.insert(0,settings['bundle']['host']['directory'])
from deploy.acceptance import _application_update as releases
releases.configure(settings['bundle'])
from deploy.acceptance import _update_rpc as fixture
os.environ.update(JASMINE_BATCH_TEST_DSN='FICTIONAL_PRIVATE_DSN',SMTP_PASSWORD='FICTIONAL_PRIVATE_MAIL')
class Client:
    def call(self,operation,**values):
        assert operation=='metadata'
        return {'visualiser':False,'monitoring':False}
with patch.object(fixture,'FixtureBatchClient',return_value=Client()),patch('uvicorn.run') as listener:
    fixture.serve_public(settings,settings['role'])
    assert listener.call_count==1
value=json.loads(Path(settings['boot']).read_text())
assert value['private_imports']==[] and value['private_environment_fields']==[]
assert 'JASMINE_BATCH_TEST_DSN' not in os.environ and 'SMTP_PASSWORD' not in os.environ
print(json.dumps(value))
'''
        for value in (self.previous,self.candidate):
            for role in ('single_web','batch_web'):
                with self.subTest(commit=value['frontend']['commit'],role=role),tempfile.TemporaryDirectory() as directory:
                    settings=dict(bundle=value,generation='test',origin='http://127.0.0.1:15001',port=15001,
                        socket=str(Path(directory)/'control.sock'),boot=str(Path(directory)/'boot.json'),role=role)
                    result=subprocess.run([sys.executable,'-c',program],input=json.dumps(settings),
                        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True,timeout=30)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    boot=json.loads(result.stdout)
                    self.assertEqual(boot['frontend_commit'],value['frontend']['commit'])
                    self.assertEqual(boot['role'],role)
                    for row in boot['sources'].values():
                        self.assertEqual(row['sha256'],value[row['component']]['files'][row['file']])

    def test_single_run_requests_match_paths_and_methods_in_both_actual_code_snapshots(self):
        from starlette.routing import compile_path
        def path(value):
            if isinstance(value,ast.Constant) and isinstance(value.value,str): return value.value
            if isinstance(value,ast.Name) and value.id=='sid': return '00000000-0000-4000-8000-000000000001'
            if isinstance(value,ast.BinOp) and isinstance(value.op,ast.Add): return path(value.left)+path(value.right)
            raise ValueError('Unexpected SingleRun route expression')
        requests = set()
        for node in ast.walk(ast.parse(Path(proof.__file__).read_text())):
            if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                    and isinstance(node.func.value,ast.Name) and node.func.value.id in ('single','other')
                    and node.func.attr in ('api','request','form')):
                method = 'POST' if node.func.attr=='form' or len(node.args)>1 or any(k.arg=='value' for k in node.keywords) else 'GET'
                requests.add((path(node.args[0]),method))
        self.assertGreaterEqual(len(requests),10)
        for bundle in (self.previous,self.candidate):
            registered = []
            for function in ast.walk(ast.parse((Path(bundle['frontend']['directory'])/'app.py').read_text())):
                if not isinstance(function,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
                for decorator in function.decorator_list:
                    if (not isinstance(decorator,ast.Call) or not isinstance(decorator.func,ast.Name)
                            or decorator.func.id!='rt' or not decorator.args
                            or not isinstance(decorator.args[0],ast.Constant)): continue
                    methods = next((ast.literal_eval(k.value) for k in decorator.keywords if k.arg=='methods'),[function.name.upper()])
                    registered.append((compile_path(decorator.args[0].value)[0],methods))
            for target,method in requests:
                if target.startswith('/static/'):
                    self.assertIn(target.removeprefix('/'),bundle['frontend']['files'])
                    continue
                with self.subTest(commit=bundle['frontend']['commit'],target=target,method=method):
                    self.assertTrue(any(regex.fullmatch(target) and method in methods for regex,methods in registered))


class ProbeAndHandoverGuards(unittest.TestCase):
    def setUp(self):
        from psycopg.conninfo import make_conninfo
        self.settings = dict(database='jasmine_queue_test',pool='test',batch_schema='test_batch_'+'a'*32,
                             single_schema='vm_update_'+'b'*32,bundle={})
        self.connection = dict(host='127.0.0.1',port='15432',dbname='jasmine_queue_test',user='postgres',password='FICTIONAL')
        self.make_conninfo = make_conninfo

    def test_native_stage_global_names_resolve_before_starting_services(self):
        missing=[]
        for name in ('run_update_rehearsal','_application_update','_supervisor','_update_rpc'):
            module=importlib.import_module('deploy.acceptance.'+name)
            known=set(vars(module))|set(vars(builtins))
            def inspect(code):
                for op in dis.get_instructions(code):
                    if op.opname=='LOAD_GLOBAL' and op.argval not in known:
                        missing.append((name,code.co_qualname,op.argval,op.positions.lineno))
                for value in code.co_consts:
                    if isinstance(value,types.CodeType):inspect(value)
            inspect(compile(Path(module.__file__).read_text(),module.__file__,'exec'))
        self.assertEqual(missing,[],'Unbound globals in native-only rehearsal stages')

    def test_startup_reads_both_actual_service_templates_and_validates_their_policies(self):
        from deploy.acceptance._supervisor import EXPECTED
        self.assertEqual(proof.service_policies(FRONTEND),
            dict(batch=EXPECTED,single=EXPECTED,batch_web=EXPECTED,single_web=EXPECTED))

    def test_batch_recovery_policy_follows_executor_changes_independently_of_public_unit(self):
        original = Path.read_text
        def changed_executor(path, *args, **kwargs):
            text = original(path, *args, **kwargs)
            return text.replace('RestartSec=10', 'RestartSec=11') if path.name == 'simpaths-executor.service' else text
        with patch.object(Path, 'read_text', changed_executor), self.assertRaises(ValueError):
            proof.service_policies(FRONTEND)
        def changed_public(path, *args, **kwargs):
            text=original(path,*args,**kwargs)
            return text.replace('RestartSec=10','RestartSec=11') if path.name=='simpaths-multirun.service' else text
        with patch.object(Path, 'read_text', changed_public),self.assertRaises(ValueError):
            proof.service_policies(FRONTEND)

    def test_probe_refuses_live_remote_and_non_disposable_databases_before_connections(self):
        import psycopg
        with patch.object(psycopg,'connect') as connect:
            for key,value in dict(host='remote.example.org',dbname='simpaths_live',user='operator',options='-c search_path=public').items():
                with self.subTest(key=key), self.assertRaises(ValueError):
                    releases.probe(self.settings,self.make_conninfo(**dict(self.connection,**{key:value})))
        connect.assert_not_called()

    def test_probe_refuses_other_schemas_pool_and_target_database(self):
        for key,value in dict(batch_schema='jasmine_batch',single_schema='jasmine_vm',pool='live',database='restore_'+'c'*32).items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                releases.probe(dict(self.settings,**{key:value}),self.make_conninfo(**self.connection))

    def test_hidden_runner_refuses_a_live_database_before_creating_evidence(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,JASMINE_BATCH_TEST_DSN='postgresql://postgres@127.0.0.1:15432/live'), \
                patch.object(proof,'rehearsal') as run:
            output = Path(root)/'evidence'
            with self.assertRaises(ValueError): proof.main(['--execute-proof','--frontend',str(FRONTEND),'--output',str(output)])
            self.assertFalse(output.exists()); run.assert_not_called()

    def test_unknown_schema_checks_both_applications_without_dispatch_or_fallback(self):
        from jasmine_web.batch import store
        from jasmine_web import vm_state
        queue,state = Mock(),Mock()
        queue.migrate.side_effect = store.Conflict('Fictional incompatible queue')
        state.migrate.side_effect = ValueError('Fictional incompatible VM registry')
        settings = dict(self.settings,bundle={'frontend':{'directory':str(FRONTEND)}})
        with patch.object(store,'Queue',return_value=queue),patch.object(vm_state,'PostgresVMState',return_value=state):
            result = releases.probe(settings,self.make_conninfo(**self.connection))
        self.assertFalse(result['accepted']); self.assertEqual([r['accepted'] for r in result['checks']],[False,False])
        queue.migrate.assert_called_once(); state.migrate.assert_called_once(); state.close.assert_called_once()

    def test_handover_refuses_to_start_a_candidate_until_both_previous_processes_are_stopped(self):
        with tempfile.TemporaryDirectory() as folder:
            old = Mock(); old.show.return_value = dict(ActiveState='active',MainPID='12345')
            with patch.object(releases,'verify'), patch('deploy.acceptance._supervisor.Supervisor') as supervisor, self.assertRaises(RuntimeError):
                proof.generation(Path(folder),Path(folder),{}, {},None,{},name='updated',owned=[],previous=old)
            self.assertEqual([call.args[0] for call in old.stop.call_args_list],
                ['single_web','batch_web','single','batch']);supervisor.assert_not_called()

    def test_a_stop_failure_never_starts_replacement_application_processes(self):
        old = Mock(); old.stop.side_effect = RuntimeError('Fictional uncertain stop')
        with patch.object(releases,'verify'), patch('deploy.acceptance._supervisor.Supervisor') as supervisor, self.assertRaises(RuntimeError):
            proof.generation(Path('/tmp'),Path('/tmp'),{}, {},None,{},name='updated',owned=[],previous=old)
        supervisor.assert_not_called()

    def test_missing_private_drain_evidence_prevents_replacement_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            old=Mock();old.show.return_value=None
            old.update_private_evidence={'batch':Path(directory)/'missing.json'}
            with patch.object(releases,'verify'),patch('deploy.acceptance._supervisor.Supervisor') as supervisor:
                with self.assertRaises(FileNotFoundError):
                    proof.generation(Path(directory),Path(directory),{}, {},None,{},
                        name='updated',owned=[],previous=old)
            self.assertEqual(old.stop.call_count,4);supervisor.assert_not_called()

    def test_partial_candidate_start_is_recorded_for_final_identity_checked_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            supervisor = Mock(); supervisor.start.side_effect = RuntimeError('Fictional lost start reply')
            owned = []; bundle = {'host':{'directory':folder},'frontend':{'directory':folder}}
            with patch.object(releases,'verify'), patch('deploy.acceptance._supervisor.Supervisor',return_value=supervisor), self.assertRaises(RuntimeError):
                proof.generation(Path(folder),Path(folder),bundle, {},{'private':None,'public':None},
                                 {'batch':{}},name='updated',owned=owned)
            self.assertEqual(owned,[supervisor])

    def test_public_restart_runs_real_readiness_checks_without_restarting_private_units(self):
        pids={'single':101,'batch':102,'single_web':103,'batch_web':104}
        supervisor=Mock()
        supervisor.show.side_effect=lambda role:dict(MainPID=str(pids[role]),ActiveState='active')
        supervisor.restart.side_effect=lambda role:pids.update({role:pids[role]+100})
        single,batch=Mock(),Mock()
        single.request.return_value=batch.request.return_value=(200,{},b'fictional ready')
        result=proof.restart_public(supervisor,single,batch)
        self.assertEqual([call.args[0] for call in supervisor.restart.call_args_list],['single_web','batch_web'])
        single.request.assert_called_once_with('/')
        batch.request.assert_called_once_with('/healthz')
        self.assertEqual(result['private_pids'],{'single':101,'batch':102})
        self.assertEqual(result['public_pids_after'],{'single_web':203,'batch_web':204})

    def test_public_restart_rejects_an_unexpected_private_process_change(self):
        pids={'single':101,'batch':102,'single_web':103,'batch_web':104}
        supervisor=Mock()
        supervisor.show.side_effect=lambda role:dict(MainPID=str(pids[role]),ActiveState='active')
        def changed(role):pids.update({role:pids[role]+100,'batch':999})
        supervisor.restart.side_effect=changed
        single,batch=Mock(),Mock()
        single.request.return_value=batch.request.return_value=(200,{},b'fictional ready')
        with self.assertRaisesRegex(RuntimeError,'private executor'):
            proof.restart_public(supervisor,single,batch)


class UpdatePlanGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='update-plan-guards-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root=Path(cls.temporary.name)
        cls.path=releases.create_plan(ROOT,FRONTEND,cls.root/'selection')
        cls.original=cls.path.read_bytes()

    def tearDown(self):
        self.path.write_bytes(self.original)

    def test_plan_freezes_real_commits_current_formats_and_the_complete_fixture_identity(self):
        value=releases.load_plan(self.path,ROOT,FRONTEND)
        self.assertEqual(value['format'],releases.PLAN_FORMAT)
        self.assertFalse(value['production_acceptance'])
        self.assertEqual(value['previous']['frontend']['commit'],releases.PREVIOUS_FRONTEND)
        self.assertEqual(value['candidate']['frontend']['commit'],releases.commit(FRONTEND,'HEAD'))
        self.assertEqual(value['source_hashes'],releases.plan_sources(ROOT,FRONTEND))
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_modified_review_missing_fixture_or_forged_source_identity_is_rejected(self):
        for mutate,error in (
                (lambda p:p['compatibility'].update(changed_public_assets=['invented.js']),'review changed'),
                (lambda p:p['source_hashes'].pop(next(iter(p['source_hashes']))),'source changed'),
                (lambda p:p['source_hashes'].update({next(iter(p['source_hashes'])):'0'*64}),'source changed'),
                (lambda p:p.update(production_acceptance=True),'Unsupported')):
            value=json.loads(self.original);mutate(value)
            self.path.write_text(json.dumps(value))
            with self.subTest(error=error),self.assertRaisesRegex(ValueError,error):
                releases.load_plan(self.path,ROOT,FRONTEND)

    def test_plan_rejects_duplicate_nonfinite_oversized_and_linked_metadata(self):
        for body,error in (('{"format":1,"format":2}','Duplicate'),
                ('{"format":NaN}','Non-finite'),('x'*(2*1024**2+1),'metadata bound')):
            self.path.write_text(body)
            with self.subTest(error=error),self.assertRaisesRegex(ValueError,error):
                releases.load_plan(self.path,ROOT,FRONTEND)
        linked=self.root/'linked.json';linked.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError,'Linked'):
            releases.load_plan(linked,ROOT,FRONTEND)

    def test_plan_cannot_reuse_an_existing_output_or_write_into_a_checkout(self):
        for directory in (self.path.parent,ROOT/'new-update-plan',FRONTEND/'new-update-plan'):
            with self.subTest(directory=directory),self.assertRaisesRegex(ValueError,'outside the source checkouts'):
                releases.create_plan(ROOT,FRONTEND,directory)
        self.assertFalse((ROOT/'new-update-plan').exists())
        self.assertFalse((FRONTEND/'new-update-plan').exists())

    def test_plan_only_cli_prepares_selection_without_native_rehearsal(self):
        output=self.root/'cli-plan'
        environment=dict(os.environ);environment.pop('SIMPATHS_UPDATE_PLAN',None)
        with patch.dict(os.environ,environment,clear=True),redirect_stdout(io.StringIO()), \
                patch.object(proof,'rehearsal') as native:
            self.assertEqual(proof.main(['--frontend',str(FRONTEND),'--plan-only','--output',str(output)]),0)
        native.assert_not_called()
        self.assertEqual(releases.load_plan(output/'plan.json',ROOT,FRONTEND)['format'],releases.PLAN_FORMAT)

    def test_cli_forwards_exact_plan_to_disposable_child_and_rejects_stale_plan_first(self):
        with patch.object(proof.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as child:
            self.assertEqual(proof.main(['--frontend',str(FRONTEND),'--plan',str(self.path),
                                        '--output',str(self.root/'native-evidence')]),0)
        self.assertEqual(child.call_args.kwargs['env']['SIMPATHS_UPDATE_PLAN'],str(self.path))
        self.path.write_text('{"format":"wrong"}')
        with patch.object(proof.subprocess,'run') as child,self.assertRaises(ValueError):
            proof.main(['--frontend',str(FRONTEND),'--plan',str(self.path)])
        child.assert_not_called()


class RetainedRecoveryFixtureGuards(unittest.TestCase):
    def test_inactive_restore_cannot_be_opened_even_for_cache_verification(self):
        from deploy.acceptance import _update_retained as retained
        from deploy.multirun.maintenance import GATE
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory);(target/GATE).write_text('fictional inactive restore')
            fixture=SimpleNamespace(target=target)
            with patch('deploy.multirun.visualiser_summary_cache.SummaryCache') as cache:
                with self.assertRaisesRegex(ValueError,'Activate the verified restore'):
                    retained.verify_restored(fixture,{})
            cache.assert_not_called()
            self.assertEqual([p.name for p in target.iterdir()],[GATE])

    def test_numeric_fingerprint_is_stable_for_binary_views_dates_and_row_order(self):
        from datetime import datetime, timezone
        from uuid import UUID
        from deploy.acceptance import _update_retained as retained
        first=dict(id=UUID('00000000-0000-4000-8000-000000000001'),
            captured_at=datetime(2026,10,20,tzinfo=timezone.utc),payload=memoryview(b'fictional attachment'))
        second=dict(id=UUID('00000000-0000-4000-8000-000000000002'),payload=b'another attachment')
        connection=MagicMock();context=MagicMock();context.__enter__.return_value=connection
        queue=SimpleNamespace(schema='test_fictional',_connection=lambda:context)
        connection.execute.return_value.fetchall.return_value=[first,second]
        before=retained.numeric_snapshot(queue)
        connection.execute.return_value.fetchall.return_value=[second,{**first,'payload':bytes(first['payload'])}]
        self.assertEqual(before,retained.numeric_snapshot(queue))
        connection.execute.return_value.fetchall.return_value=[second,{**first,'payload':b'changed attachment'}]
        self.assertNotEqual(before,retained.numeric_snapshot(queue))
        self.assertEqual(set(before),set(retained.TABLES))
        self.assertTrue(all(row['rows']==2 for row in before.values()))
        self.assertNotIn('fictional attachment',json.dumps(before))


class SeparatedFixtureGuards(unittest.TestCase):
    def setUp(self):
        from deploy.acceptance import _update_rpc
        self.fixture=_update_rpc
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)

    def test_public_settings_omit_private_state_inputs_keys_and_database(self):
        settings=dict(bundle={'code':'fictional'},generation='original',single_port=15001,batch_port=15002,
            rpc_sockets=dict(single=str(self.root/'single.sock'),batch=str(self.root/'batch.sock')),
            secret='FICTIONAL_PRIVATE_KEY',state='/private/state',database_role='PRIVATE_ROLE')
        for role in ('single_web','batch_web'):
            result=self.fixture.public_settings(settings,role,boot=self.root/'boot.json')
            self.assertEqual(set(result),self.fixture.PUBLIC_FIELDS)
            self.assertNotIn('FICTIONAL_PRIVATE_KEY',json.dumps(result))
            self.assertNotIn('/private/state',json.dumps(result))
            self.assertEqual(result['origin'],f"http://127.0.0.1:{result['port']}")
        with self.assertRaises(ValueError):self.fixture.public_settings(settings,'single',boot=self.root/'boot.json')

    def test_public_launcher_refuses_extra_fields_before_importing_or_starting(self):
        settings={key:None for key in self.fixture.PUBLIC_FIELDS}
        settings.update(role='batch_web',private_key='FICTIONAL_PRIVATE_KEY')
        with patch('uvicorn.run') as listener,self.assertRaisesRegex(ValueError,'Public settings'):
            self.fixture.serve_public(settings,'batch_web')
        listener.assert_not_called()

    def test_socket_directory_must_be_private_owned_and_free_of_links(self):
        path=self.root/'control.sock'
        self.fixture.fixture_path(path)
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):self.fixture.fixture_path(path)
        self.root.chmod(0o700)
        linked=self.root/'linked';linked.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):self.fixture.fixture_path(linked/'control.sock')
        path.write_text('ordinary file')
        with self.assertRaises(ValueError):self.fixture.fixture_path(path,existing=True)

    def test_wrong_native_peer_and_invalid_request_close_without_sending_frames(self):
        from jasmine_web.batch import service_rpc as rpc
        client=self.fixture.FixtureBatchClient(self.root/'control.sock')
        for peer,values,error in ((os.getuid()+1,{},PermissionError),(os.getuid(),{'bad':float('nan')},ValueError),
                                (os.getuid(),{'large':'x'*rpc.REQUEST_LIMIT},ValueError)):
            connection=Mock()
            with self.subTest(peer=peer,kind=error),patch.object(self.fixture,'fixture_path',return_value=client.path), \
                 patch.object(self.fixture.socket,'socket',return_value=connection), \
                 patch.object(rpc,'peer_uid',return_value=peer),patch.object(rpc,'_write_frame') as send:
                with self.assertRaises(error):client._connect('metadata',values)
                connection.close.assert_called_once();send.assert_not_called()

    def test_supervisor_drains_public_roles_before_private_and_restarts_only_owned_units(self):
        from deploy.acceptance._supervisor import Supervisor
        supervisor=Supervisor('a'*32)
        supervisor.units={supervisor.name(role) for role in ('single','batch','single_web','batch_web')}
        with patch.object(supervisor,'show',return_value={'ActiveState':'active','MainPID':'12345'}), \
             patch.object(supervisor,'command') as command:
            supervisor.close()
            stopped=[call.args[0][-1] for call in command.call_args_list if call.args[0][2]=='stop']
            self.assertTrue(all('_web.service' in name for name in stopped[:2]))
            self.assertTrue(all('_web.service' not in name for name in stopped[2:]))
            command.reset_mock();supervisor.restart('single_web')
            command.assert_called_once_with(['systemctl','--user','restart',supervisor.name('single_web')])
        with self.assertRaises(ValueError):Supervisor('b'*32).restart('single_web')


if __name__=='__main__': unittest.main()
