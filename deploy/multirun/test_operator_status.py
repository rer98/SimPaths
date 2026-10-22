"""(C) Copyright 2026, by Ross Richardson

Operator CLI, verified release inventories and partial-failure privacy regressions.
Uses fictional files and optional disposable PostgreSQL; never runs model containers.
@author ross richardson
"""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from .artifacts import fingerprint, inventory, write_json
from .operator_status import collect, launch_settings, main, release_inventory, literal, render
from .releases import ReleaseRegistry
from deploy._workflow import frontend_path

FRONTEND = frontend_path()


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.paths=list(sys.path)
        sys.path.insert(0,str(FRONTEND))
        self.addCleanup(lambda:setattr(sys,'path',self.paths))
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.state=self.root/'state'; self.state.mkdir(mode=0o700)
        self.jar=self.root/'model.jar'; self.jar.write_bytes(b'fictional model')
        defaults=self.root/'defaults'; defaults.mkdir(); (defaults/'scenario.xlsx').write_bytes(b'fictional workbook')
        self.registry=ReleaseRegistry(self.state)
        self.release=self.registry.register(image='sha256:'+'a'*64,name='Version A',jar=self.jar,defaults=defaults)
        self.queue=SimpleNamespace(pool_id='test',schema='jasmine_batch')

    def contents(self):
        return {str(p.relative_to(self.state)):(p.read_bytes(),p.stat().st_mode) for p in self.state.rglob('*') if p.is_file()}

    def test_release_inventory_matches_verified_load_without_creating_files_or_locking(self):
        before=self.contents()
        with patch.object(self.registry,'locked',side_effect=AssertionError('must not lock')):
            result=self.registry.inventory()
        self.assertEqual(result,self.registry.load())
        self.assertEqual(self.contents(),before)
        self.assertEqual(release_inventory(self.state)['selected_default'],self.release)

    def test_legacy_inventory_does_not_upgrade_catalogue_or_create_releases_directory(self):
        state=self.root/'legacy'; state.mkdir(mode=0o700)
        original=state/'release'; original.mkdir(mode=0o700)
        (original/'defaults').mkdir(mode=0o700)
        (original/'model.jar').write_bytes(b'legacy model')
        (original/'defaults'/'scenario.xlsx').write_bytes(b'legacy workbook')
        write_json(original/'release.json',dict(image='sha256:'+'b'*64,model=fingerprint(original/'model.jar'),
                                               defaults=inventory(original/'defaults')))
        result=ReleaseRegistry(state).inventory()
        self.assertTrue(next(iter(result)).startswith('local-'))
        self.assertFalse((state/'releases').exists())
        self.assertEqual(len(list(state.rglob('*'))),5)

    def test_release_tampering_does_not_return_a_verified_default(self):
        retained=self.registry.load()[self.release]
        retained['jar'].chmod(0o600)
        retained['jar'].write_bytes(b'changed jar')
        with self.assertRaises(Exception): release_inventory(self.state)

    def test_incomplete_database_keeps_file_measurements_and_never_prints_exception_details(self):
        before=self.contents()
        with patch('jasmine_web.batch.operator_status.database_inventory',side_effect=RuntimeError('password=secret owner@example.org /private/raw.csv')):
            result=collect(self.queue,self.state)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['database'])
        self.assertIsNone(result['storage']['prepared_datasets'])
        self.assertEqual(result['models']['selected_default'],self.release)
        for secret in ('password','secret','owner@example.org','raw.csv'):
            self.assertNotIn(secret,json.dumps(result))
        self.assertEqual(self.contents(),before)

    def settings(self,**extra):
        value=dict(format='simpaths.operator.settings.v1',pool_id='test',schema='jasmine_batch',
            recorded_at='2026-10-03T12:00:00+00:00',model_release=self.release,
            notification_delivery=False,retention_cleanup=False,visualiser_enabled=False)
        value.update(extra)
        (self.state/'operator-settings.json').unlink(missing_ok=True)
        write_json(self.state/'operator-settings.json',value)
        return value

    def test_last_launch_flags_are_private_metadata_and_missing_flags_remain_unknown(self):
        self.assertFalse(launch_settings(self.state,'test','jasmine_batch')['available'])
        self.settings()
        result=launch_settings(self.state,'test','jasmine_batch')
        self.assertTrue(result['available'])
        self.assertFalse(result['notification_delivery'])
        with self.assertRaises(ValueError): launch_settings(self.state,'other','jasmine_batch')
        self.settings(notification_delivery='false')
        with self.assertRaises(ValueError): launch_settings(self.state,'test','jasmine_batch')

    def test_terminal_labels_escape_control_characters(self):
        self.assertEqual(literal('name\x1b[2J\nmore'),'name?[2J?more')

    def test_operator_includes_read_only_numeric_summary_eviction_costs_without_database(self):
        from .visualiser_summary_cache import SummaryCache
        cache=SummaryCache(self.state/'execution/visualiser-cache/summaries', maximum_entries=1)
        data=self.root/'metrics';data.write_bytes(b'{"fictional":true}');data.chmod(0o600)
        for number in range(2):
            with cache.entry(dict(owner='PRIVATE_OWNER',configuration=str(number))) as entry:
                entry.publish(data)
        with cache.entry(dict(owner='PRIVATE_OWNER',configuration='0')) as entry:
            self.assertFalse(entry.restore(self.root/'evicted'))
            with entry.raw_processing(1024):pass
        before=self.contents()
        report=collect(self.queue,self.state,read_database=False)
        self.assertTrue(report['summary_cache_usage']['available'])
        self.assertEqual(report['summary_cache_usage']['counters']['eviction_raw_completed'],1)
        self.assertIn('after known eviction: 1',render(report))
        self.assertNotIn('PRIVATE_OWNER',json.dumps(report))
        self.assertNotIn(str(data),json.dumps(report))
        self.assertEqual(before,self.contents())

    def test_cli_errors_are_json_and_do_not_echo_credentials_or_private_paths(self):
        from . import vm_web
        target=self.root/'connection'; target.write_text('private'); target.chmod(0o600)
        before=self.contents(); output=io.StringIO()
        with patch.object(vm_web,'private_text',side_effect=ValueError('secret /private/raw.csv')),redirect_stdout(output):
            code=main(['--frontend',str(FRONTEND),'--state',str(self.state),'--dsn-file',str(target),'--json'])
        self.assertEqual(code,2)
        result=json.loads(output.getvalue()); self.assertFalse(result['complete'])
        self.assertNotIn('secret',output.getvalue()); self.assertNotIn('/private',output.getvalue())

    def test_storage_budget_status_is_read_only_and_reports_paused_batch_admissions(self):
        from jasmine_web.batch.storage_budget import StorageBudget
        controller=StorageBudget(self.queue,self.state,self.root,dedicated=False,
            reader=lambda:dict(capacity_mib=50000,device=1,inode=2,total_bytes=50000*1024**2,
                               reserved_bytes=0,free_bytes=2000*1024**2))
        with patch('jasmine_web.batch.storage_budget.current',return_value=40000), \
                patch('jasmine_web.batch.storage_budget.resize',return_value=dict(
                    previous_mib=40000,storage_mib=40000,changed=False)):
            controller.configure('follow')
        with patch('jasmine_web.batch.storage_budget.resize',side_effect=RuntimeError('PRIVATE_DSN')):
            self.assertFalse(controller.tick(force=True))
        before=self.contents()
        report=collect(self.queue,self.state,read_database=False)
        self.assertIn('storage_budget_waiting_for_operator',report['warnings'])
        self.assertIn('Scheduler storage budget: follow',render(report))
        self.assertIn('new batch admissions paused (RuntimeError)',render(report))
        self.assertNotIn('PRIVATE_DSN',json.dumps(report))
        self.assertEqual(before,self.contents())

    def test_summary_dependency_snapshot_is_numeric_read_only_and_reports_capacity_pressure(self):
        from .visualiser_summary_cache import SummaryCache
        cache=SummaryCache(self.state/'execution/visualiser-cache/summaries',maximum_entries=2)
        created=time.time()-60
        plan=dict(comparison='PRIVATE_COMPARISON',created_at=created,expires_at=created+86400,
            configurations=[dict(configuration=name,job='PRIVATE_JOB_'+name,attempt=None,seeds=['606'])
                            for name in ('baseline','alternative')])
        for owner in ('PRIVATE_OWNER','PRIVATE_OTHER'):
            cache.holds.replace(owner,'PRIVATE_EXPERIMENT',[plan],calculation='a'*64,owner_entries=2)
        before=self.contents()
        report=collect(self.queue,self.state,read_database=False)
        value=report['summary_retention']
        self.assertEqual((value['admitted_comparisons'],value['deferred_comparisons'],value['retained_seed_slots']),(1,1,2))
        self.assertIn('summary_retention_waiting_for_capacity',report['warnings'])
        self.assertIn('1 comparisons waiting for storage',render(report))
        self.assertNotIn('PRIVATE_',json.dumps(report))
        self.assertEqual(before,self.contents())
        self.assertEqual(self.contents(),before)

    def test_status_of_missing_state_does_not_create_it(self):
        target=self.root/'absent'; output=io.StringIO()
        with redirect_stdout(output):
            code=main(['--frontend',str(FRONTEND),'--state',str(target),'--json'])
        self.assertEqual(code,2); self.assertFalse(target.exists())

    def test_vm_status_dispatch_does_not_migrate_or_construct_an_application(self):
        from . import vm_web
        with patch('deploy.multirun.operator_status.main',return_value=0) as status:
            self.assertEqual(vm_web.main(['status','--config','/fictional/config','--json','--limit','5']),0)
        self.assertEqual(status.call_args.args[0],['--config','/fictional/config','--limit','5','--json'])


@unittest.skipUnless(os.environ.get('JASMINE_BATCH_TEST_DSN'),'Use the disposable operator proof')
class OperatorDatabaseTests(unittest.TestCase):
    setUp=OperatorTests.setUp
    contents=OperatorTests.contents
    def test_cli_database_and_filesystem_snapshots_round_trip_without_writes(self):
        sys.path.insert(0,str(FRONTEND/'tests/batch'))
        import test_postgres
        fixture=test_postgres.PostgresQueueTests(); self.addCleanup(fixture.doCleanups); fixture.setUp()
        dataset=self.state/'dataset'; dataset.mkdir(); (dataset/'data').write_bytes(b'fictional input')
        fixture.sql('INSERT INTO prepared_locations VALUES (%s,%s,%s,%s)',
                    (fixture.q.pool_id,'public-v1',str(dataset),'sha256:'+'a'*64))
        fixture.submit()
        dsn=self.root/'connection'; dsn.write_text(test_postgres.DSN); dsn.chmod(0o600)
        before=self.contents(); output=io.StringIO()
        args=['--frontend',str(FRONTEND),'--state',str(self.state),'--dsn-file',str(dsn),
              '--pool',fixture.q.pool_id,'--schema',fixture.q.schema]
        with redirect_stdout(output): code=main(args+['--json'])
        self.assertEqual(code,0,output.getvalue())
        result=json.loads(output.getvalue())
        self.assertEqual(result['database']['jobs']['unfinished'],1)
        self.assertEqual(result['storage']['prepared_datasets']['locations'],1)
        self.assertNotIn(test_postgres.DSN,output.getvalue()); self.assertNotIn(str(dataset),output.getvalue())
        output=io.StringIO()
        with redirect_stdout(output): self.assertEqual(main(args),0)
        self.assertIn('Email delivery at last application launch: unknown',output.getvalue())
        self.assertEqual(before,self.contents())
