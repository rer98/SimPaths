"""(C) Copyright 2026, by Ross Richardson

Live release choices, reference/grace protection and verified archive recovery.
Fictional files and queue snapshots; no Docker or scientific simulations.
@author ross richardson
"""
from contextlib import nullcontext
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from .artifacts import ArtifactError, fingerprint
from .browser_model import BrowserModel
from .releases import ReleaseRegistry, LEGACY_CATALOGUE_FORMAT, atomic_json, utc_now
from .release_retirement import ReleaseRetirement
from . import release_archives
from deploy._workflow import frontend_path


class ReleaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        previous=list(sys.path);self.addCleanup(lambda:setattr(sys,'path',previous))
        sys.path.insert(0,str(frontend_path()))
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.state=self.root/'state'
        self.jar=self.root/'model.jar';self.defaults=self.root/'defaults';self.defaults.mkdir()
        (self.defaults/'scenario_CPI.xlsx').write_bytes(b'fictional defaults')
        self.registry=ReleaseRegistry(self.state)
        self.ids=[]
        for name in ('A','B','C'):
            self.jar.write_bytes(name.encode())
            self.ids.append(self.registry.register(image='sha256:'+'a'*64,name=name,
                jar=self.jar,defaults=self.defaults,make_default=True))
        self.first,self.previous,self.current=self.ids
        self.registry.set_availability(self.first,'recovery')
        self.now=utc_now();self.jobs=[];self.locations=[]
        def execute(query,values=()):
            if 'FROM jobs j JOIN experiments' in query:rows=self.jobs
            elif 'FROM prepared_locations p' in query:rows=self.locations
            else:raise AssertionError('Unexpected retirement query')
            return SimpleNamespace(fetchall=lambda:deepcopy(rows))
        self.connection=SimpleNamespace(execute=execute)
        self.queue=SimpleNamespace(backup_state=self.state,
            _transaction=lambda:nullcontext((self.connection,{},self.now)))
        self.retirement=ReleaseRetirement(self.queue,self.state)

    def sweep(self,days=0):
        self.now+=timedelta(days=days)
        return self.retirement.tick(force=True,now=self.now)

    def job(self):
        return dict(state='queued',execution_run=None,configuration_id='scenario',model_digest='sha256:'+'a'*64,
            specification=dict(run_sets=[dict(id='scenario',parameters=dict(model_release=self.first))]))

    def test_live_choices_hide_recovery_and_existing_versions_without_restart(self):
        model=BrowserModel(self.registry.load())
        self.assertNotIn(self.first,[r['id'] for r in model.browser_form()['releases']])
        self.registry.set_availability(self.previous,'existing')
        self.assertEqual([r['id'] for r in model.browser_form()['releases']],[self.current])
        self.registry.set_availability(self.first,'available')
        self.assertIn(self.first,[r['id'] for r in model.browser_form()['releases']])
        with self.assertRaises(ArtifactError):self.registry.set_availability(self.current,'recovery')

    def test_existing_preparation_requires_original_experiment_context(self):
        model=BrowserModel(self.registry.load())
        self.registry.set_availability(self.previous,'existing')
        with self.assertRaisesRegex(ArtifactError,'existing experiments'):
            model.validate_preparation(self.previous)
        with patch.object(model,'experiment_release',return_value=self.previous):
            model.validate_preparation(self.previous,{'original':'owned'})
        self.registry.set_availability(self.previous,'recovery')
        with patch.object(model,'experiment_release',return_value=self.previous):
            with self.assertRaisesRegex(ArtifactError,'supported model release'):
                model.validate_preparation(self.previous,{'original':'owned'})

    def test_grace_then_archive_and_restore_preserve_full_release(self):
        original=fingerprint(self.registry.load()[self.first]['jar'])
        self.assertEqual(self.sweep()['retired'],[])
        self.assertEqual(self.sweep(29)['retired'],[])
        self.assertEqual(self.sweep(2)['retired'],[self.first])
        self.assertNotIn(self.first,self.registry.load())
        self.assertFalse((self.registry.root/self.first).exists())
        record=release_archives.verify(self.registry,self.first)
        self.assertEqual(record['files']['model.jar'],original)
        release_archives.restore(self.registry,self.first)
        self.assertEqual(fingerprint(self.registry.load()[self.first]['jar']),original)
        self.assertEqual(self.registry.availability()[self.first],'recovery')
        self.assertEqual(next(iter(self.registry.load())),self.current)

    def test_rollback_and_explicit_live_default_remain_protected(self):
        self.registry.set_availability(self.previous,'recovery')
        retirement=ReleaseRetirement(self.queue,self.state,protected=(self.first,))
        retirement.tick(force=True,now=self.now)
        result=retirement.tick(force=True,now=self.now+timedelta(days=31))
        self.assertEqual(result['retired'],[])
        self.assertEqual(len(self.registry.load()),3)

    def test_queued_or_recoverable_work_resets_the_full_grace(self):
        self.sweep();self.jobs=[self.job()]
        result=self.sweep(31)
        self.assertIsNone(result['releases'][self.first]['unreferenced_since'])
        self.jobs=[];self.assertEqual(self.sweep()['retired'],[])
        self.assertEqual(self.sweep(31)['retired'],[self.first])

    def test_unknown_or_unreadable_prepared_identity_keeps_every_matching_runtime(self):
        self.locations=[dict(location=str(self.root/'missing'),model_digest='sha256:'+'a'*64,prepared_fingerprint='f'*64)]
        self.sweep();result=self.sweep(31)
        self.assertEqual(result['retired'],[])
        self.assertTrue(result['releases'][self.first]['reasons'])

    def test_retained_ready_inputs_pin_their_release_even_without_jobs(self):
        self.locations=[dict(location='synthetic',model_digest='sha256:'+'a'*64,prepared_fingerprint='f'*64)]
        receipt=dict(sha256='f'*64,identity=dict(release=dict(id=self.first)))
        with patch('deploy.multirun.queue_adapter.read_prepared',return_value=receipt):
            self.sweep();self.assertEqual(self.sweep(31)['retired'],[])

    def test_recheck_catches_reference_created_during_archival(self):
        self.sweep()
        create=release_archives.create
        def archive(*args):
            result=create(*args);self.jobs=[self.job()];return result
        with patch.object(release_archives,'create',side_effect=archive):
            result=self.sweep(31)
        self.assertEqual(result['retired'],[])
        self.assertIsNone(self.registry._catalogue()['entries'][self.first]['unreferenced_since'])
        self.assertIn(self.first,self.registry.load())

    def test_backup_fence_defers_retirement(self):
        from jasmine_web.batch.backup_guard import file_guard
        with file_guard(self.state,backup=True):self.assertIsNone(self.retirement.tick(force=True))
        self.assertEqual(len(self.registry.load()),3)

    def test_interrupted_post_commit_cleanup_resumes_without_republishing_release(self):
        self.sweep()
        def interrupt(registry,key):
            (registry.root/key/'model.jar').unlink()
            raise OSError('fictional interrupted unlink')
        with patch.object(release_archives,'cleanup',side_effect=interrupt):
            with self.assertRaises(OSError):self.sweep(31)
        self.assertNotIn(self.first,self.registry.load())
        self.sweep()
        self.assertFalse((self.registry.root/self.first).exists())
        release_archives.verify(self.registry,self.first)

    def test_corrupted_archive_cannot_authorise_deletion_or_restore(self):
        self.sweep();entry=self.registry._catalogue()['entries'][self.first]
        release_archives.create(self.registry,self.first,entry)
        _,archive,_=release_archives.paths(self.registry,self.first)
        archive.write_bytes(b'corrupt archive')
        with self.assertRaisesRegex(ArtifactError,'changed or is incomplete'):self.sweep(31)
        self.assertIn(self.first,self.registry.load())

    def test_archive_rejects_unlisted_members_even_if_outer_hash_matches(self):
        entry=self.registry._catalogue()['entries'][self.first]
        record=release_archives.create(self.registry,self.first,entry)
        _,archive,metadata=release_archives.paths(self.registry,self.first)
        with zipfile.ZipFile(archive,'a') as target:target.writestr('../outside',b'unsafe')
        record['archive']=fingerprint(archive);atomic_json(metadata,record)
        with self.assertRaises(ArtifactError):release_archives.verify(self.registry,self.first)
        self.assertFalse((self.root/'outside').exists())

    def test_existing_archive_must_match_the_current_bundle_before_commit(self):
        self.sweep();entry=self.registry._catalogue()['entries'][self.first]
        release_archives.create(self.registry,self.first,entry)
        extra=self.registry.root/self.first/'unexpected'
        extra.write_bytes(b'changed source');extra.chmod(0o600)
        with self.assertRaisesRegex(ArtifactError,'differs from its existing recovery archive'):
            self.sweep(31)
        self.assertIn(self.first,self.registry.load())
        self.assertEqual(extra.read_bytes(),b'changed source')

    def test_settled_cleanup_does_not_rehash_or_decompress_archives(self):
        self.sweep();self.sweep(31)
        with patch.object(release_archives,'verify',side_effect=AssertionError('unnecessary archive read')):
            release_archives.cleanup(self.registry,self.first)
            self.sweep()

    def test_archive_location_metadata_cannot_target_another_bundle(self):
        entry=self.registry._catalogue()['entries'][self.first]
        record=release_archives.create(self.registry,self.first,entry)
        record['entry']['legacy']=True
        atomic_json(release_archives.paths(self.registry,self.first)[2],record)
        with self.assertRaisesRegex(ArtifactError,'catalogue entry'):
            release_archives.verify(self.registry,self.first)

    def test_live_policy_switch_defers_cleanup_without_changing_work(self):
        self.sweep()
        control=self.state/'release-retirement-policy.json'
        settings=dict(enabled=False,grace_days=30,interval_seconds=3600)
        atomic_json(control,settings)
        self.assertIsNone(self.sweep(31))
        self.assertIn(self.first,self.registry.load())
        settings['enabled']=True;atomic_json(control,settings)
        self.assertEqual(self.sweep()['retired'],[self.first])
        settings['grace_days']=29;atomic_json(control,settings)
        with self.assertRaisesRegex(ArtifactError,'retirement policy'):self.sweep()

    def test_old_catalogue_upgrades_only_on_write_and_keeps_bytes_and_default(self):
        value=self.registry._catalogue()
        legacy=dict(format=LEGACY_CATALOGUE_FORMAT,default=self.current,
            entries={key:{name:record[name] for name in ('legacy','manifest')} for key,record in value['entries'].items()})
        atomic_json(self.registry.catalogue,legacy);before=self.registry.catalogue.read_bytes()
        self.assertEqual(next(iter(self.registry.inventory())),self.current)
        self.assertEqual(self.registry.catalogue.read_bytes(),before)
        self.registry.set_availability(self.first,'existing')
        self.assertNotEqual(self.registry.catalogue.read_bytes(),before)
        self.assertEqual(self.registry.availability()[self.first],'existing')
