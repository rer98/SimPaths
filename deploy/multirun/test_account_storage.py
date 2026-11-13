"""(C) Copyright 2026, by Ross Richardson

Bounded, read-only account disk attribution using private fictional files.
Checks ordinary live removal, uncertain ownership and asynchronous cache behavior.
@author ross richardson
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path
import sys
sys.path.insert(0,str(frontend_path()))
from .account_storage import AccountStorage, FIELDS, FORMAT, empty


class AccountStorageTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.state=self.root/'state'
        self.execution=self.state/'execution';self.uploads=self.state/'uploads'
        self.artifacts=self.state/'artifacts'
        self.charts=self.execution/'visualiser-cache';self.summaries=self.charts/'summaries'
        self.downloads=self.execution/'download-cache'
        for path in (self.execution,self.uploads,self.artifacts,self.summaries,self.downloads):
            path.mkdir(mode=0o700,parents=True,exist_ok=True)
        for path in self.state.rglob('*'):
            if path.is_dir():path.chmod(0o700)
        self.service=SimpleNamespace(datasets=SimpleNamespace(root=self.uploads),
            outputs=SimpleNamespace(targets=lambda lease:['output']),
            visualiser=SimpleNamespace(root=self.charts,backend=SimpleNamespace(
                summary_cache=SimpleNamespace(root=self.summaries))),
            results=SimpleNamespace(downloads=SimpleNamespace(root=self.downloads)))
        self.sampler=AccountStorage(self.service,self.state,SimpleNamespace(root=self.execution))
        self.metadata=([],[],[])
        self.sampler._metadata=lambda owners:deepcopy(self.metadata)

    @staticmethod
    def blocks(path):return path.stat().st_blocks*512

    def write(self,path,content=b'fictional'):
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        for parent in (path.parent,*path.parents):
            if parent.is_relative_to(self.root):parent.chmod(0o700)
        path.write_bytes(content);path.chmod(0o600);return path

    def cache(self,root,owner,*,summary=False,name=None):
        identity=dict(owner=owner,configuration='configuration',seed='606')
        canonical=json.dumps(identity,sort_keys=True,separators=(',',':')).encode()
        key=name or hashlib.sha256(canonical).hexdigest()
        path=root/key;data=self.write(path/('metrics.json' if summary else 'published.json'))
        receipt=(dict(format='simpaths.visualiser.private-summary.v1',key=key,identity=identity,
                      bytes=data.stat().st_size,sha256=hashlib.sha256(data.read_bytes()).hexdigest())
                 if summary else dict(selection=identity,state='ready'))
        self.write(path/('receipt.json' if summary else 'status.json'),json.dumps(receipt).encode())
        return path

    def observe(self,*owners):return self.sampler.measure(owners or ('alice','bob'))

    def test_allocated_bytes_separate_raw_workspaces_uploads_prepared_and_disjoint_caches(self):
        key='batch-10000000-0000-4000-8000-000000000001'
        raw=self.write(self.execution/key/'work/output/nested/people.csv')
        diagnostic=self.write(self.execution/key/'work/diagnostics.txt')
        identity=self.write(self.execution/key/'identity.json')
        scratch=self.write(self.artifacts/('pipeline-'+key)/'metrics.part')
        upload=self.write(self.uploads/'upload-one')
        prepared=self.write(self.artifacts/'prepared-one'/'input.mv.db')
        charts=self.cache(self.charts,'alice');summaries=self.cache(self.summaries,'alice',summary=True)
        downloads=self.cache(self.downloads,'alice')
        self.cache(self.charts,'not-on-page')
        self.metadata=([dict(owner_id='alice',execution_key=key,operation=None)],
                       [dict(owner_id='alice',id='upload-one')],
                       [dict(owner_id='alice',location=str(prepared.parent))])
        original=os.open
        def read_metadata_only(path,flags,*args,**kwargs):
            if not flags&os.O_DIRECTORY:
                self.assertIn(str(path),('receipt.json','status.json'),'Scientific files must never be opened')
            return original(path,flags,*args,**kwargs)
        with patch('deploy.multirun.account_storage.os.open',side_effect=read_metadata_only):report=self.observe()
        row=report['accounts']['alice']
        size=lambda folder:sum(self.blocks(path) for path in folder.rglob('*') if path.is_file())
        expected=dict(raw_bytes=self.blocks(raw),
            workspace_other_bytes=sum(self.blocks(p) for p in (diagnostic,identity,scratch)),
            uploads_bytes=self.blocks(upload),prepared_bytes=self.blocks(prepared),
            visualiser_bytes=size(charts)+size(summaries),downloads_bytes=size(downloads))
        self.assertEqual(row,{**expected,'total_bytes':sum(expected.values())})
        self.assertEqual(report['measure_state'],'ready')
        self.assertEqual(report['accounts']['bob'],dict.fromkeys((*FIELDS,'total_bytes'),0))
        self.assertNotIn('people.csv',json.dumps(report));self.assertNotIn(str(self.state),json.dumps(report))

    def test_sparse_files_charge_physical_blocks_rather_than_logical_length(self):
        upload=self.uploads/'sparse-upload'
        with upload.open('wb') as stream:stream.seek(8*1024**2);stream.write(b'x')
        upload.chmod(0o600);self.metadata=([],[dict(owner_id='alice',id=upload.name)],[])
        row=self.observe('alice')['accounts']['alice']
        self.assertEqual(row['uploads_bytes'],self.blocks(upload))
        self.assertLess(row['uploads_bytes'],upload.stat().st_size)

    def test_normal_deleted_attempt_and_upload_paths_are_zero_without_restarting_work(self):
        self.metadata=([dict(owner_id='alice',execution_key='batch-gone',operation=None)],
                       [dict(owner_id='alice',id='upload-gone')],[])
        row=self.observe('alice')['accounts']['alice']
        self.assertEqual(row,dict.fromkeys((*FIELDS,'total_bytes'),0))

    def test_preparation_output_is_workspace_space_not_simulation_raw_output(self):
        path=self.write(self.execution/'batch-preparation'/'work/output/data.csv')
        self.metadata=([dict(owner_id='alice',execution_key='batch-preparation',operation='prepare')],[],[])
        row=self.observe('alice')['accounts']['alice']
        self.assertEqual(row['raw_bytes'],0);self.assertEqual(row['workspace_other_bytes'],self.blocks(path))

    def test_duplicate_owned_prepared_locations_are_counted_once(self):
        path=self.write(self.artifacts/'prepared-one'/'input.mv.db')
        entry=dict(owner_id='alice',location=str(path.parent))
        self.metadata=([],[],[entry,entry])
        self.assertEqual(self.observe('alice')['accounts']['alice']['prepared_bytes'],self.blocks(path))

    def test_conflicting_owners_of_one_prepared_path_make_both_measurements_unknown(self):
        path=self.write(self.artifacts/'prepared-one'/'input.mv.db')
        self.metadata=([],[],[dict(owner_id=owner,location=str(path.parent)) for owner in ('alice','bob')])
        report=self.observe()
        for row in report['accounts'].values():
            self.assertIsNone(row['prepared_bytes']);self.assertIsNone(row['total_bytes'])

    def test_prepared_root_itself_or_outside_paths_cannot_be_charged_to_one_account(self):
        for path in (self.artifacts,self.root,self.artifacts/'..'/'outside'):
            with self.subTest(path=path):
                self.metadata=([],[],[dict(owner_id='alice',location=str(path))])
                self.assertIsNone(self.observe('alice')['accounts']['alice']['prepared_bytes'])

    def test_symlink_and_hardlinked_files_are_unknown_without_reading_the_target(self):
        target=self.write(self.root/'scientific-private.csv')
        upload=self.uploads/'upload-one'
        self.metadata=([],[dict(owner_id='alice',id=upload.name)],[])
        upload.symlink_to(target)
        self.assertIsNone(self.observe('alice')['accounts']['alice']['uploads_bytes'])
        upload.unlink();os.link(target,upload)
        self.assertIsNone(self.observe('alice')['accounts']['alice']['uploads_bytes'])

    def test_configured_missing_cache_root_is_unknown_while_disabled_optional_caches_are_zero(self):
        shutil.rmtree(self.downloads)
        report=self.observe('alice');self.assertIsNone(report['accounts']['alice']['downloads_bytes'])
        self.service.results.downloads=None;self.service.visualiser=None
        shutil.rmtree(self.charts)
        row=self.observe('alice')['accounts']['alice']
        self.assertEqual(row['visualiser_bytes'],0);self.assertEqual(row['downloads_bytes'],0)

    def test_malformed_cache_owner_and_receipt_type_remain_unknown_instead_of_empty(self):
        path=self.cache(self.charts,'alice')
        for record in ({},[],dict(selection=[]),dict(selection=dict(owner=42)),dict(selection=dict(owner=''))):
            with self.subTest(record=record):
                self.write(path/'status.json',json.dumps(record).encode())
                report=self.observe()
                self.assertEqual(report['measure_state'],'incomplete')
                for row in report['accounts'].values():self.assertIsNone(row['visualiser_bytes'])

    def test_missing_cache_receipt_with_remaining_payload_is_unknown(self):
        path=self.cache(self.charts,'alice');(path/'status.json').unlink()
        self.assertIsNone(self.observe('alice')['accounts']['alice']['visualiser_bytes'])

    def test_entry_removed_concurrently_before_its_receipt_is_observed_is_not_a_failure(self):
        path=self.cache(self.charts,'alice');original=os.open;removed=False
        def remove_once(selected,flags,*args,**kwargs):
            nonlocal removed
            if Path(selected)==path and flags&os.O_DIRECTORY and not removed:
                removed=True;shutil.rmtree(path)
            return original(selected,flags,*args,**kwargs)
        with patch('deploy.multirun.account_storage.os.open',side_effect=remove_once):report=self.observe('alice')
        self.assertTrue(removed);self.assertEqual(report['measure_state'],'ready')
        self.assertEqual(report['accounts']['alice']['visualiser_bytes'],0)

    def test_cache_replacement_after_owner_read_cannot_charge_another_accounts_files(self):
        path=self.cache(self.charts,'alice');before=sum(self.blocks(p) for p in path.iterdir())
        original=os.fdopen;replaced=False;fixture=self
        class ClosingReader:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self.stream
            def __exit__(self,*exc):
                nonlocal replaced
                result=self.stream.__exit__(*exc)
                if not replaced:
                    replaced=True;path.rename(path.with_name('retired-old-entry'))
                    replacement=fixture.cache(fixture.charts,'bob',name=path.name)
                    fixture.write(replacement/'large-payload',b'x'*(64*1024))
                return result
        with patch('deploy.multirun.account_storage.os.fdopen',side_effect=lambda *args,**kwargs:ClosingReader(original(*args,**kwargs))):
            report=self.observe()
        self.assertTrue(replaced)
        self.assertIn(report['accounts']['alice']['visualiser_bytes'],(None,before),
                      'Only a pinned old snapshot or unknown can follow an owner-directory replacement')

    def test_dangling_linked_parent_remains_unknown_even_when_the_leaf_is_missing(self):
        linked=self.state/'linked';linked.symlink_to(self.state/'missing-target',target_is_directory=True)
        self.service.datasets.root=linked/'uploads'
        self.assertIsNone(self.observe('alice')['accounts']['alice']['uploads_bytes'])

    def test_wrong_summary_key_and_oversized_receipts_cannot_claim_precise_attribution(self):
        path=self.cache(self.summaries,'alice',summary=True)
        record=json.loads((path/'receipt.json').read_text());record['key']='f'*64
        self.write(path/'receipt.json',json.dumps(record).encode())
        self.assertIsNone(self.observe('alice')['accounts']['alice']['visualiser_bytes'])
        self.write(path/'receipt.json',b' '*(4*1024**2+1))
        self.assertIsNone(self.observe('alice')['accounts']['alice']['visualiser_bytes'])

    def test_entry_and_wall_bounds_remain_incomplete_instead_of_reporting_a_partial_total(self):
        for number in range(3):self.write(self.uploads/f'upload-{number}')
        self.metadata=([],[dict(owner_id='alice',id=f'upload-{n}') for n in range(3)],[])
        self.sampler.max_entries=1
        self.assertIsNone(self.observe('alice')['accounts']['alice']['uploads_bytes'])
        self.sampler.max_entries=200000
        with patch('deploy.multirun.account_storage.time.monotonic',side_effect=[0]+[10]*100):
            self.sampler._sample(('alice',))
            report=self.sampler(('alice',))
        self.assertEqual(report['measure_state'],'incomplete')
        self.assertIsNone(report['accounts']['alice']['total_bytes'])
        self.assertTrue(all(value is None for value in report['accounts']['alice'].values()))

    def test_empty_page_and_invalid_requests_start_no_worker(self):
        with patch('deploy.multirun.account_storage.threading.Thread') as worker:
            self.assertEqual(self.sampler(())['accounts'],{})
            self.assertEqual(self.sampler(())['measure_state'],'ready')
            for owners in (['alice'],('alice','alice'),('bad/owner',),tuple('owner'+str(n) for n in range(51))):
                with self.subTest(owners=owners):
                    with self.assertRaises(ValueError):self.sampler(owners)
            worker.assert_not_called()

    def test_sampling_is_background_single_flight_and_cached_results_are_defensive_copies(self):
        started=threading.Event();finish=threading.Event();calls=[]
        self.addCleanup(finish.set)
        def blocked(owners):
            calls.append(owners);started.set();finish.wait(3)
            return dict(format=FORMAT,measure_state='ready',observed_at='2026-11-13T12:00:00Z',
                        accounts={owner:dict.fromkeys((*FIELDS,'total_bytes'),0) for owner in owners})
        self.sampler.measure=blocked
        before=time.monotonic();first=self.sampler(('bob','alice'))
        self.assertLess(time.monotonic()-before,.5)
        self.assertEqual(first['measure_state'],'pending');self.assertTrue(started.wait(1))
        self.assertEqual(self.sampler(('alice','bob'))['measure_state'],'pending')
        self.assertEqual(len(calls),1)
        finish.set();self.sampler._thread.join(2);self.assertFalse(self.sampler._thread.is_alive())
        observed=self.sampler(('alice','bob'));self.assertEqual(observed['measure_state'],'ready')
        observed['accounts']['alice']['raw_bytes']=999
        self.assertEqual(self.sampler(('bob','alice'))['accounts']['alice']['raw_bytes'],0)
        self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
