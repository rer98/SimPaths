"""(C) Copyright 2026, by Ross Richardson

Offline archive, release selection and handover guards for update acceptance.
Native database/Docker/systemd checks run separately in run_update_rehearsal.py.
@author ross richardson
"""
import ast
from copy import deepcopy
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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

    def test_real_previous_and_current_git_snapshots_have_reviewed_matching_formats(self):
        releases.compatible(self.previous,self.candidate)
        self.assertNotEqual(self.previous['host']['commit'],self.candidate['host']['commit'])
        self.assertNotEqual(self.previous['frontend']['commit'],self.candidate['frontend']['commit'])
        self.assertEqual({k:len(v) for k,v in self.previous['migrations'].items()},{'single':2,'batch':20})

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

    def test_synthetic_migrations_append_without_changing_original_sql(self):
        releases.verify(self.future)
        for key, values in self.candidate['migrations'].items():
            self.assertEqual(self.future['migrations'][key][:-1],values)
            self.assertEqual(self.future['migrations'][key][-1]['version'],len(values)+1)
        self.assertEqual({k:len(v) for k,v in self.future['migrations'].items()},{'single':3,'batch':21})

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
        self.assertFalse((FRONTEND/'jasmine_web/batch/021_update_rehearsal.sql').exists())
        self.assertFalse((FRONTEND/'jasmine_web/vm_state_003_update_rehearsal.sql').exists())

    def test_fresh_processes_import_each_selected_application_serve_its_page_and_read_its_style_source(self):
        with tempfile.TemporaryDirectory() as folder:
            for name,value in (('previous',self.previous),('candidate',self.candidate),('future',self.future)):
                with self.subTest(name=name):
                    result = proof.run_child(Path(folder),value,{},'',name=name,imports_only=True)
                    self.assertEqual(result['help_experiment'],name!='previous')
                    self.assertEqual(result['css_sha256'],value['frontend']['files']['jasmine_web/batch/browser_static/batch.css'])
                    for key,component in (('runtime','host'),('browser','frontend')):
                        row = result['sources'][key]
                        self.assertEqual(row['sha256'],value[component]['files'][row['file']])

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
                with self.subTest(commit=bundle['frontend']['commit'],target=target,method=method):
                    self.assertTrue(any(regex.fullmatch(target) and method in methods for regex,methods in registered))


class ProbeAndHandoverGuards(unittest.TestCase):
    def setUp(self):
        from psycopg.conninfo import make_conninfo
        self.settings = dict(database='jasmine_queue_test',pool='test',batch_schema='test_batch_'+'a'*32,
                             single_schema='vm_update_'+'b'*32,bundle={})
        self.connection = dict(host='127.0.0.1',port='15432',dbname='jasmine_queue_test',user='postgres',password='FICTIONAL')
        self.make_conninfo = make_conninfo

    def test_startup_reads_both_actual_service_templates_and_validates_their_policies(self):
        from deploy.acceptance._supervisor import EXPECTED
        self.assertEqual(proof.service_policies(FRONTEND),dict(batch=EXPECTED,single=EXPECTED))

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
            self.assertEqual(old.stop.call_count,2); supervisor.assert_not_called()

    def test_a_stop_failure_never_starts_replacement_application_processes(self):
        old = Mock(); old.stop.side_effect = RuntimeError('Fictional uncertain stop')
        with patch.object(releases,'verify'), patch('deploy.acceptance._supervisor.Supervisor') as supervisor, self.assertRaises(RuntimeError):
            proof.generation(Path('/tmp'),Path('/tmp'),{}, {},None,{},name='updated',owned=[],previous=old)
        supervisor.assert_not_called()

    def test_partial_candidate_start_is_recorded_for_final_identity_checked_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            supervisor = Mock(); supervisor.start.side_effect = RuntimeError('Fictional lost start reply')
            owned = []; bundle = {'host':{'directory':folder},'frontend':{'directory':folder}}
            with patch.object(releases,'verify'), patch('deploy.acceptance._supervisor.Supervisor',return_value=supervisor), self.assertRaises(RuntimeError):
                proof.generation(Path(folder),Path(folder),bundle, {},None,{'batch':{}},name='updated',owned=owned)
            self.assertEqual(owned,[supervisor])


if __name__=='__main__': unittest.main()
