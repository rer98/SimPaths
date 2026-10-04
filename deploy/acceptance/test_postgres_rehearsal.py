"""(C) Copyright 2026, by Ross Richardson

Offline guards for database outage probes and owned PostgreSQL resources.
These tests contact no database, listener, Docker daemon or systemd manager.
@author ross richardson
"""
import ast
from copy import deepcopy
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from docker.errors import APIError, NotFound
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from deploy._workflow import frontend_path
from deploy.acceptance import _postgres_outage as fixture
from deploy.acceptance import _recovery_fixture as recovery
from deploy.acceptance import run_postgres_rehearsal as proof


class CredentialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('single','inputs'): (self.root/name).mkdir()
        self.settings = dict(tag='a'*32,state=str(self.root),single_state=str(self.root/'single'),
            inputs=str(self.root/'inputs'),schema='test_recovery_'+'b'*32,batch_schema='test_recovery_'+'c'*32,
            image='sha256:'+'d'*64,single_port=15001,batch_port=15002,
            database_role='simpaths_rehearsal_'+'a'*32,database_port=15432)
        self.values = dict(host='127.0.0.1',port=15432,dbname=fixture.DATABASE,
            user=self.settings['database_role'],password='FICTIONAL_APP_CREDENTIAL')
        self.dsn = make_conninfo(**self.values)

    def test_restricted_account_is_accepted_only_for_its_own_fixture(self):
        self.assertEqual(recovery.validate(self.settings,self.dsn),self.settings)
        changed = dict(self.settings,tag='e'*32,database_role='simpaths_rehearsal_'+'e'*32)
        with self.assertRaises(ValueError): recovery.validate(changed,self.dsn)

    def test_admin_live_remote_and_other_port_connections_are_rejected(self):
        for field,value in dict(user='postgres',host='remote.example.org',dbname='jasmine_multirun',
                                port=15433,password='').items():
            with self.subTest(field=field),self.assertRaises(ValueError):
                fixture.restricted_connection(self.settings,make_conninfo(**dict(self.values,**{field:value})))

    def test_service_socket_options_and_extra_connection_targets_are_rejected(self):
        for extra in (dict(service='production'),dict(options='-c search_path=public'),
                      dict(hostaddr='192.0.2.1'),dict(sslmode='disable')):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                fixture.restricted_connection(self.settings,make_conninfo(**dict(self.values,**extra)))

    def test_malformed_identity_and_unprivileged_port_checks_precede_bootstrap(self):
        for fields in (dict(tag='../existing'),dict(database_role='postgres'),dict(database_port=True),
                       dict(database_port=543),dict(database_port=65536)):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                fixture.restricted_connection(dict(self.settings,**fields),self.dsn)

    def test_existing_recovery_admin_guard_is_preserved(self):
        settings = {key:value for key,value in self.settings.items() if not key.startswith('database_')}
        admin = make_conninfo(**dict(self.values,user='postgres'))
        recovery.validate(settings,admin)
        with self.assertRaises(ValueError): recovery.validate(settings,self.dsn)
        with self.assertRaises(ValueError): recovery.validate(self.settings,admin)

    def test_restricted_metadata_does_not_allow_production_schemas_or_linked_state(self):
        for fields in (dict(schema='jasmine_vm'),dict(batch_schema=self.settings['schema'])):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                recovery.validate(dict(self.settings,**fields),self.dsn)
        link = self.root/'linked'; link.symlink_to(self.root/'inputs',target_is_directory=True)
        with self.assertRaises(ValueError): recovery.validate(dict(self.settings,inputs=str(link)),self.dsn)

    def test_hidden_runner_refuses_an_existing_database_before_creating_evidence(self):
        output = self.root/'evidence'
        with patch.dict(os.environ,JASMINE_BATCH_TEST_DSN='postgresql://postgres@127.0.0.1:15432/jasmine_multirun'), \
             patch.object(proof,'rehearsal') as run:
            with self.assertRaises(ValueError):
                proof.main(['--execute-proof','--frontend',str(frontend_path()),'--output',str(output)])
        run.assert_not_called(); self.assertFalse(output.exists())


class DatabaseOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.database = fixture.Database('a'*32,15432,client=self.client)
        self.image = 'sha256:'+'b'*64
        self.database.image = self.image
        self.client.images.get.return_value.id = self.image
        self.container,self.volume = Mock(),Mock()
        self.container.attrs = dict(Name='/'+self.database.name,Image=self.image,
            Config=dict(Labels={fixture.LABEL:self.database.tag}),
            HostConfig=dict(PortBindings={'5432/tcp':[dict(HostIp='127.0.0.1',HostPort='15432')]}),
            Mounts=[dict(Type='volume',Name=self.database.volume,Destination='/var/lib/postgresql/data')],
            State=dict(Running=True,Pid=12345))
        self.volume.attrs = dict(Name=self.database.volume,Labels={fixture.LABEL:self.database.tag})
        self.have_container = self.have_volume = False
        self.client.containers.get.side_effect = lambda _: self.present('container')
        self.client.volumes.get.side_effect = lambda _: self.present('volume')
        self.client.volumes.create.side_effect = lambda **_: self.create('volume')
        self.client.containers.create.side_effect = lambda *a,**kw: self.create('container')
        self.container.remove.side_effect = lambda **_: self.remove('container')
        self.volume.remove.side_effect = lambda **_: self.remove('volume')
        self.container.stop.side_effect = lambda **_: self.container.attrs['State'].update(Running=False)
        self.container.kill.side_effect = lambda **_: self.container.attrs['State'].update(Running=False)
        self.container.start.side_effect = lambda: self.container.attrs['State'].update(Running=True)

    def present(self, kind):
        if not getattr(self,'have_'+kind): raise NotFound('Fictional missing resource')
        return getattr(self,kind)

    def create(self, kind):
        setattr(self,'have_'+kind,True); return getattr(self,kind)

    def remove(self, kind):
        setattr(self,'have_'+kind,False)

    def owned(self):
        self.have_container = self.have_volume = True
        self.database.container_owned = self.database.volume_owned = True

    def test_arbitrary_names_privileged_ports_and_boolean_ports_are_rejected(self):
        for tag,port in (('existing',15432),('a'*32,543),('a'*32,65536),('a'*32,True)):
            with self.subTest(tag=tag,port=port),self.assertRaises(ValueError):
                fixture.Database(tag,port,client=self.client)
        self.client.assert_not_called()

    def test_generated_connections_separate_admin_from_application_credentials(self):
        app,admin = conninfo_to_dict(self.database.dsn()),conninfo_to_dict(self.database.dsn(admin=True))
        self.assertEqual(app['user'],'simpaths_rehearsal_'+'a'*32)
        self.assertEqual(admin['user'],'postgres')
        self.assertNotEqual(app['password'],admin['password'])
        for value in (app,admin):
            self.assertEqual(value['host'],'127.0.0.1'); self.assertEqual(value['port'],'15432')
            self.assertEqual(value['dbname'],'jasmine_queue_test')

    def test_cleanup_and_interruption_never_take_ownership_of_existing_resources(self):
        self.database.close()
        self.client.containers.get.assert_not_called(); self.client.volumes.get.assert_not_called()
        self.client.close.assert_not_called()  # Injected client belongs to its caller.
        for action in (self.database.stop,self.database.restart):
            with self.assertRaises(ValueError): action()

    def test_creation_refuses_an_existing_container_or_volume(self):
        for kind in ('container','volume'):
            setattr(self,'have_'+kind,True)
            with self.subTest(kind=kind),self.assertRaises(ValueError): self.database.start()
            setattr(self,'have_'+kind,False)
        self.assertFalse(self.database.container_owned or self.database.volume_owned)
        self.client.containers.create.assert_not_called(); self.client.volumes.create.assert_not_called()

    def test_daemon_failure_is_not_mistaken_for_an_absent_resource(self):
        self.client.containers.get.side_effect = APIError('Fictional denied daemon access')
        with self.assertRaises(APIError): self.database.start()
        self.client.volumes.create.assert_not_called()

    def test_database_creation_uses_fixed_loopback_port_and_persistent_owned_volume(self):
        with patch.object(self.database,'ready',return_value=True),patch.object(fixture,'until',side_effect=lambda check,**_: check()):
            self.database.start()
        args,values = self.client.containers.create.call_args
        self.assertEqual(args,(self.image,))
        self.assertEqual(values['ports'],{'5432/tcp':('127.0.0.1',15432)})
        self.assertEqual(values['volumes'],{self.database.volume:dict(bind='/var/lib/postgresql/data',mode='rw')})
        self.assertEqual(values['labels'],{fixture.LABEL:self.database.tag})
        self.assertEqual(values['mem_limit'],'768m'); self.assertEqual(values['pids_limit'],128)
        self.assertEqual(values['nano_cpus'],1_000_000_000)
        self.assertNotIn(self.database.app_password,values['environment'].values())
        self.container.start.assert_called_once()
        with self.assertRaises(ValueError): self.database.start()

    def test_uncertain_volume_creation_preserves_cleanup_ownership(self):
        def create(**_):
            self.have_volume=True; raise APIError('Fictional lost create response')
        self.client.volumes.create.side_effect=create
        with self.assertRaises(APIError): self.database.start()
        self.assertTrue(self.database.volume_owned); self.assertFalse(self.database.container_owned)
        self.database.close()
        self.volume.remove.assert_called_once(); self.container.remove.assert_not_called()

    def test_uncertain_container_creation_removes_only_confirmed_owned_resources(self):
        def create(*a,**kw):
            self.have_container=True; raise APIError('Fictional lost create response')
        self.client.containers.create.side_effect=create
        with self.assertRaises(APIError): self.database.start()
        self.assertTrue(self.database.container_owned and self.database.volume_owned)
        self.database.close()
        self.container.remove.assert_called_once_with(force=True); self.volume.remove.assert_called_once()

    def test_changed_image_name_labels_ports_and_mounts_prevent_kill_or_removal(self):
        self.owned(); original=deepcopy(self.container.attrs)
        changes = [dict(Image='sha256:'+'c'*64),dict(Name='/unrelated'),dict(Config=dict(Labels={})),
            dict(HostConfig=dict(PortBindings={'5432/tcp':[dict(HostIp='0.0.0.0',HostPort='15432')]})),
            dict(Mounts=[dict(Type='volume',Name='unrelated',Destination='/var/lib/postgresql/data')]),
            dict(Mounts=original['Mounts']+[dict(Type='bind',Source='/private',Destination='/other')])]
        for fields in changes:
            self.container.attrs={**original,**fields}
            for action in (lambda:self.database.stop(crash=True),self.database.close):
                with self.subTest(fields=fields),self.assertRaises(ValueError): action()
        self.container.kill.assert_not_called(); self.container.remove.assert_not_called(); self.volume.remove.assert_not_called()

    def test_relabelled_or_missing_data_volume_blocks_container_operations(self):
        self.owned(); self.volume.attrs['Labels']={}
        with self.assertRaises(ValueError): self.database.stop()
        self.volume.attrs['Labels']={fixture.LABEL:self.database.tag}; self.have_volume=False
        with self.assertRaises(ValueError): self.database.close()
        self.container.remove.assert_not_called(); self.container.stop.assert_not_called()

    def test_orderly_stop_and_crash_confirm_stopped_state_without_removing_data(self):
        self.owned()
        self.database.stop(); self.container.stop.assert_called_once_with(timeout=10)
        self.container.attrs['State']['Running']=True
        self.database.stop(crash=True); self.container.kill.assert_called_once_with(signal='SIGKILL')
        self.container.remove.assert_not_called(); self.volume.remove.assert_not_called()

    def test_unconfirmed_stop_retains_ownership_and_is_not_restarted(self):
        self.owned(); self.container.kill.side_effect=None
        with self.assertRaises(RuntimeError): self.database.stop(crash=True)
        with self.assertRaises(ValueError): self.database.restart()
        self.container.start.assert_not_called(); self.assertTrue(self.database.volume_owned)

    def test_restart_reuses_existing_storage_port_and_checks_persisted_account(self):
        self.owned(); self.container.attrs['State']['Running']=False
        with patch.object(self.database,'ready',return_value=True),patch.object(self.database,'verify_role',return_value={'checked':True}) as role, \
             patch.object(fixture,'until',side_effect=lambda check,**_: check()):
            self.assertEqual(self.database.restart(),{'checked':True})
        role.assert_called_once(); self.container.start.assert_called_once()
        self.client.containers.create.assert_not_called(); self.client.volumes.create.assert_not_called()

    def test_failed_container_removal_retains_volume_and_reports_uncertain_cleanup(self):
        self.owned(); self.container.remove.side_effect=APIError('Fictional removal failed')
        with self.assertRaises(APIError): self.database.close()
        self.volume.remove.assert_not_called(); self.assertTrue(self.database.container_owned)

    def test_success_reply_without_actual_container_removal_is_rejected(self):
        self.owned(); self.container.remove.side_effect=None
        with self.assertRaises(RuntimeError): self.database.close()
        self.volume.remove.assert_not_called()

    def test_verified_cleanup_checks_absence_before_removing_the_volume(self):
        self.owned(); self.database.close()
        self.assertFalse(self.have_container or self.have_volume)
        self.assertFalse(self.database.container_owned or self.database.volume_owned)
        self.client.images.remove.assert_not_called()
        self.client.containers.prune.assert_not_called(); self.client.volumes.prune.assert_not_called()


class RolePermissionTests(unittest.TestCase):
    def setUp(self):
        self.database=fixture.Database('a'*32,15432,client=Mock())
        self.role=dict(rolname=self.database.role,**{field:False for field in fixture.ROLE_FLAGS})

    def connection(self, row, membership=None):
        connection=Mock(); connection.__enter__=Mock(return_value=connection)
        connection.__exit__=Mock(return_value=False)
        connection.execute.side_effect=[SimpleNamespace(fetchone=lambda:row),SimpleNamespace(fetchone=lambda:membership)]
        return connection

    def test_unexpected_cluster_privileges_or_role_membership_fail_before_running_the_application(self):
        rows=[dict(self.role,**{field:True}) for field in fixture.ROLE_FLAGS]+[dict(self.role,rolname='postgres')]
        for row in rows:
            with self.subTest(row=row),patch('psycopg.connect',return_value=self.connection(row)),self.assertRaises(AssertionError):
                self.database.verify_role()
        with patch('psycopg.connect',return_value=self.connection(self.role,dict(roleid=123))),self.assertRaises(AssertionError):
            self.database.verify_role()

    def test_successful_administrator_role_or_data_access_is_rejected(self):
        import psycopg
        for allowed_probe in (0,1):
            role=self.connection(self.role)
            probes=[self.connection(None),self.connection(None)]
            for index,connection in enumerate(probes):
                connection.execute.side_effect=None if index==allowed_probe else psycopg.errors.InsufficientPrivilege('fictional denial')
            with self.subTest(allowed_probe=allowed_probe),patch('psycopg.connect',side_effect=[role,*probes]),self.assertRaises(AssertionError):
                self.database.verify_role()


class OutageProbeTests(unittest.TestCase):
    def setUp(self):
        self.clients=[Mock() for _ in range(5)]
        for client in self.clients:
            client.request.return_value=(503,{},b'{"error":"temporarily unavailable"}')
            client.form.return_value=(503,{},b'{"error":"temporarily unavailable"}')
        self.submission=dict(key='fictional-key',review='fictional-signed-review')

    def probes(self):
        return proof.outage_probes(self.clients,'single-id','completed-job','active-job','dataset',self.submission,['PRIVATE_CANARY'])

    def test_probes_use_real_control_routes_and_valid_reviews_and_submissions(self):
        values=self.probes(); self.assertEqual(len(values),18)
        alice=self.clients[2]
        calls={call.args[0]:call.kwargs['value'] for call in alice.request.call_args_list}
        self.assertEqual(calls['/api/jobs/active-job'],dict(action='cancel',enabled=True))
        self.assertEqual(calls['/api/submit'],self.submission)
        review=calls['/api/review-experiment']
        self.assertEqual(review['dataset'],'dataset')
        self.assertEqual(review['form']['repetitions'],2)
        self.assertEqual(review['form']['run_sets'][0]['id'],review['form']['baseline'])
        self.assertEqual(review['form']['first_seed'],'606')

    def test_all_single_run_requests_match_registered_frontend_paths_and_methods(self):
        from starlette.routing import compile_path
        # Inspect registration without importing/starting the real app or touching
        # checkout secrets. This also checks controls used after an outage.
        registered=[]
        for function in ast.walk(ast.parse((frontend_path()/'app.py').read_text())):
            if not isinstance(function,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
            for decorator in function.decorator_list:
                if (not isinstance(decorator,ast.Call) or not isinstance(decorator.func,ast.Name)
                        or decorator.func.id!='rt' or not decorator.args
                        or not isinstance(decorator.args[0],ast.Constant)): continue
                path=decorator.args[0].value
                methods=next((ast.literal_eval(k.value) for k in decorator.keywords if k.arg=='methods'),[function.name.upper()])
                registered.append((compile_path(path)[0],methods))
        def path(value):
            if isinstance(value,ast.Constant) and isinstance(value.value,str): return value.value
            if isinstance(value,ast.Name) and value.id=='sid': return '00000000-0000-4000-8000-000000000001'
            if isinstance(value,ast.BinOp) and isinstance(value.op,ast.Add): return path(value.left)+path(value.right)
            raise ValueError('Unexpected SingleRun route expression')
        requests=set()
        for node in ast.walk(ast.parse(Path(proof.__file__).read_text())):
            if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                    and isinstance(node.func.value,ast.Name) and node.func.value.id in ('single','other')
                    and node.func.attr in ('api','request','form')):
                method='POST' if node.func.attr=='form' or len(node.args)>1 or any(k.arg=='value' for k in node.keywords) else 'GET'
                requests.add((path(node.args[0]),method))
            if (isinstance(node,ast.Tuple) and len(node.elts)==3 and isinstance(node.elts[0],ast.Name)
                    and node.elts[0].id in ('single','other')):
                value=node.elts[2]
                method='GET' if isinstance(value,ast.Constant) and value.value is None else 'POST'
                requests.add((path(node.elts[1]),method))
        self.assertGreaterEqual(len(requests),10)
        for target,method in requests:
            with self.subTest(target=target,method=method):
                self.assertTrue(any(regex.fullmatch(target) and method in methods for regex,methods in registered),
                    'Rehearsal uses an unregistered SingleRun route or method: '+method+' '+target)

    def test_unknown_route_invalid_form_or_success_cannot_count_as_a_safe_outage(self):
        for status in (200,201,303,400,404,500):
            self.clients[2].request.return_value=(status,{},b'fictional response')
            with self.subTest(status=status),self.assertRaises(AssertionError): self.probes()

    def test_anonymous_denial_is_accepted_without_weakening_owner_checks(self):
        self.clients[4].request.return_value=(403,{},b'approved owner required')
        self.probes()
        self.clients[2].request.return_value=(403,{},b'owner disappeared')
        with self.assertRaises(AssertionError): self.probes()

    def test_a_launch_redirect_must_be_a_controlled_catalogue_error(self):
        self.clients[1].form.return_value=(303,{'Location':'/?error=Temporarily+unavailable'},b'')
        self.probes()
        self.clients[1].form.return_value=(303,{'Location':'/simulation/new-session'},b'')
        with self.assertRaises(AssertionError): self.probes()

    def test_private_body_headers_or_redirects_fail_even_with_expected_status(self):
        for response in ((503,{},b'PRIVATE_CANARY'),(503,{'x-diagnostic':'PRIVATE_CANARY'},b''),
                         (303,{'Location':'/?error=PRIVATE_CANARY'},b'')):
            with self.subTest(response=response),self.assertRaises(AssertionError):
                proof.failed_response(response,['PRIVATE_CANARY'],statuses=(503,303))

    def test_outage_must_not_replace_or_delete_any_cookie_regardless_of_header_case(self):
        for field in ('Set-Cookie','set-cookie','SET-COOKIE'):
            with self.subTest(field=field),self.assertRaises(AssertionError):
                proof.failed_response((503,{field:'owner=; Max-Age=0'},b''),[])

    def test_archive_comparison_rejects_corruption_and_duplicate_names(self):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive: archive.writestr('Scenario/run_1/csv/fictional.csv',b'fictional,output\n')
        self.assertEqual(list(proof.archive_hashes(buffer.getvalue())),['Scenario/run_1/csv/fictional.csv'])
        with self.assertRaises(zipfile.BadZipFile): proof.archive_hashes(b'incomplete archive')
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr('same.txt',b'first')
            with self.assertWarns(UserWarning): archive.writestr('same.txt',b'second')
        with self.assertRaises(AssertionError): proof.archive_hashes(buffer.getvalue())

    def test_container_observation_scopes_batch_mounts_to_this_private_execution_root(self):
        settings=dict(tag='a'*32,state='/fictional/ours')
        docker=Mock()
        docker.call.side_effect=['single\n','ours\nother\nprefix\n']
        docker.inspect.side_effect=[dict(Id='single',Mounts=[]),
            dict(Id='ours',Mounts=[dict(Type='bind',Source='/fictional/ours/execution/attempt/work')]),
            dict(Id='other',Mounts=[dict(Type='bind',Source='/fictional/other/execution/attempt/work')]),
            dict(Id='prefix',Mounts=[dict(Type='bind',Source='/fictional/ours/execution-unrelated/work')])]
        with patch('jasmine_web.batch.docker_executor.DockerCLI',return_value=docker):
            self.assertEqual(proof.model_containers(settings),{'single','ours'})
        self.assertIn('label=jasmine.batch.identity',docker.call.call_args.args)

    def test_pooled_recovery_waits_only_for_unavailable_state_and_never_masks_other_errors(self):
        from jasmine_web.vm_state import VMStateUnavailable
        store=Mock(); store.peek_session.side_effect=[VMStateUnavailable('fictional outage'),{'container_id':'same'}]
        self.assertFalse(proof.recovered_record(store,'single-id'))
        self.assertEqual(proof.recovered_record(store,'single-id'),{'container_id':'same'})
        store.peek_session.side_effect=ValueError('invalid state')
        with self.assertRaises(ValueError): proof.recovered_record(store,'single-id')


if __name__=='__main__': unittest.main()
