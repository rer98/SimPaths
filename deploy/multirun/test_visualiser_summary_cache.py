"""(C) Copyright 2026, by Ross Richardson

Concurrent private summary reuse, corruption, eviction and source-role parity.
@author ross richardson
"""
from concurrent.futures import ThreadPoolExecutor
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest

from deploy.multirun.visualiser_summary_cache import SummaryCache, MIB
from deploy.multirun.visualiser_backend import VisualiserBackend
from deploy.multirun.test_visualiser_backend import BUILD, VisualiserBackendTests, sources


class SummaryCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = SummaryCache(self.root/'cache', maximum_bytes=MIB, maximum_entries=2)

    def identity(self, number=0, owner='alice'):
        return dict(owner=owner, configuration=str(number), seed='606', hash='a'*64)

    def data(self, name, content=b'{"fictional":true}'):
        output = self.root/name
        output.write_bytes(content); output.chmod(0o600)
        return output

    def test_private_immutable_entries_restore_and_corruption_never_replays(self):
        source = self.data('input')
        with self.cache.entry(self.identity()) as entry:
            self.assertFalse(entry.restore(self.root/'missing'))
            self.assertTrue(entry.publish(source))
            self.assertFalse(entry.publish(source))
            self.assertTrue(entry.restore(self.root/'restored'))
            self.assertEqual(source.read_bytes(), (self.root/'restored').read_bytes())
            directory = self.cache.root/entry.key
            (directory/'metrics.json').write_bytes(b'x'*source.stat().st_size)
            self.assertFalse(entry.restore(self.root/'corrupt'))
            self.assertFalse(directory.exists())
        self.assertEqual(self.cache.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root/'restored').stat().st_mode & 0o777, 0o600)

    def test_concurrent_writers_obey_global_bytes_entries_and_no_pending_files(self):
        source = self.data('input', b'x'*(400*1024))
        def publish(number):
            with self.cache.entry(self.identity(number)) as entry:
                entry.publish(source)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(publish, range(12)))
        counts = self.cache.prune()
        self.assertEqual(counts['entries'], 2)
        self.assertLessEqual(counts['bytes'], MIB)
        self.assertFalse(list(self.cache.root.glob('.pending-*')))
        actual = sum(p.stat().st_size for p in self.cache.root.glob('*/*'))
        self.assertEqual(actual, counts['bytes'])

    def test_same_key_is_computed_once_and_waiting_reader_reuses_publication(self):
        source = self.data('input')
        calculations = []
        def use(number):
            with self.cache.entry(self.identity()) as entry:
                if not entry.restore(self.root/str(number)):
                    calculations.append(number)
                    entry.publish(source)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(use, range(6)))
        self.assertEqual(len(calculations), 1)

    def test_owner_invalidation_and_lru_do_not_remove_another_owner(self):
        source = self.data('input')
        for number, owner in [(1,'alice'), (2,'bob')]:
            with self.cache.entry(self.identity(number,owner)) as entry:
                entry.publish(source)
        with self.cache.entry(self.identity(1,'alice')) as entry:
            entry.restore(self.root/'touch')
        with self.cache.entry(self.identity(3,'alice')) as entry:
            entry.publish(source)
        with self.cache.entry(self.identity(2,'bob')) as entry:
            self.assertFalse(entry.restore(self.root/'evicted'))
        self.assertEqual(self.cache.prune(owner='alice',configuration='1')['entries'],1)
        self.assertEqual(self.cache.prune(owner='bob')['entries'],1)
        self.assertEqual(self.cache.prune(owner='alice')['entries'],0)
        with self.assertRaises(ValueError):
            self.cache.prune(configuration='3')

    def test_invalid_paths_receipts_and_abandoned_publications_are_rejected(self):
        public = self.root/'public'; public.mkdir(mode=0o755)
        public.chmod(0o755)  # Exercise public permissions even under a private caller umask.
        with self.assertRaises(ValueError): SummaryCache(public)
        linked = self.root/'link'; linked.symlink_to(self.cache.root)
        with self.assertRaises(ValueError): SummaryCache(linked)
        pending = self.cache.root/'.pending-interrupted'; pending.mkdir()
        (pending/'metrics.json').write_text('interrupted')
        source = self.data('input')
        with self.cache.entry(self.identity()) as entry:
            entry.publish(source)
            receipt = self.cache.root/entry.key/'receipt.json'
            receipt.unlink(); receipt.symlink_to(source)
            self.assertFalse(entry.restore(self.root/'bad-receipt'))
        self.assertFalse(pending.exists())
        self.assertTrue(source.exists())

    def test_oversized_entries_are_not_published_and_changed_budget_evicts(self):
        source = self.data('input',b'x'*(MIB+1))
        with self.cache.entry(self.identity()) as entry:
            self.assertFalse(entry.publish(source))
        self.cache.entry_bytes=4
        with self.cache.entry(self.identity()) as entry:
            with self.assertRaises(ValueError):entry.publish(source)
        for kwargs in [dict(maximum_bytes=True), dict(maximum_entries=0), dict(entry_bytes=0)]:
            with self.assertRaises(ValueError):SummaryCache(self.cache.root,**kwargs)


@unittest.skipUnless(BUILD,'Set SIMPATHS_VISUALISER_TEST_BUILD to a pinned calculation build')
class SummaryBackendTests(unittest.TestCase):
    command = VisualiserBackendTests.command

    def setUp(self):
        VisualiserBackendTests.setUp(self)
        # A private test overlay gives the reviewed calculation its new runner;
        # never modify the immutable historical build used by research proofs.
        self.build = self.root/'new-build'
        self.build.mkdir()
        manifest = json.loads((Path(BUILD)/'build.json').read_text())
        for name in manifest['files']:
            shutil.copyfile(Path(BUILD)/name,self.build/name)
        runner = Path(__file__).parent/'visualiser/runner.cjs'
        shutil.copyfile(runner,self.build/'runner.cjs')
        manifest['files']['runner.cjs']=hashlib.sha256(runner.read_bytes()).hexdigest()
        (self.build/'build.json').write_text(json.dumps(manifest))
        self.backend = VisualiserBackend(self.build,self.root,summary_cache_root=self.root/'summaries')
        self.operations=[]
        self.operation_lock=threading.Lock()
        self.next_work=0

    def selected(self, alternatives=1):
        result=sources(self.root,alternatives=alternatives)
        for item in result:
            item['owner']='alice'
            item['identity']=dict(job='job-'+item['configuration']['id'],attempt='attempt-1')
        return result

    def test_successful_source_hash_is_reused_only_for_the_exact_unchanged_inode(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from jasmine_web.batch.results import open_output, OutputUnavailable
        selected=self.selected()
        entry=selected[0]['files'][0]
        reads=[]
        @contextmanager
        def counted(root,relative):
            with open_output(root,relative) as source:
                def read(size):
                    reads.append(size);return source.read(size)
                yield SimpleNamespace(read=read,seek=source.seek,fileno=source.fileno)
        with patch('jasmine_web.batch.results.open_output',counted):
            with self.backend._verified_source(entry):pass
            first=len(reads);self.assertGreater(first,0)
            with self.backend._verified_source(entry):pass
            self.assertEqual(len(reads),first)
            path=self.root/entry['path']
            original=path.read_bytes();before=path.stat()
            replacement=path.with_name('replacement.csv');replacement.write_bytes(original)
            os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns))
            os.replace(replacement,path)
            with self.backend._verified_source(entry):pass
            self.assertGreater(len(reads),first)
            before=path.stat()
            path.write_bytes(b'X'+original[1:])
            os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
            with self.assertRaises(OutputUnavailable):
                with self.backend._verified_source(entry):pass

    def execute(self, selected, *, backend=None, comparison_set=False):
        with self.operation_lock:
            self.next_work+=1
            work=self.root/('work-'+str(self.next_work)); work.mkdir()
        def command(argv,**kwargs):
            request=json.loads(Path(argv[-1]).read_text())
            with self.operation_lock:self.operations.append(request)
            self.command(argv,**kwargs)
        return (backend or self.backend).process(selected,work,command,lambda _:None,comparison_set=comparison_set)

    def runs(self):
        return sum(r['operation']=='run' for r in self.operations)

    def test_standalone_then_pair_then_baseline_swap_reuse_exact_scientific_rows(self):
        selected=self.selected()
        self.execute([selected[0]])
        self.assertEqual(self.runs(),3)
        result=self.execute(selected)
        self.assertEqual(self.runs(),6)
        swapped=copy.deepcopy(selected[::-1])
        swapped[0]['configuration']['role']='Baseline'
        swapped[1]['configuration']['role']='Scenario'
        reverse=self.execute(swapped)
        self.assertEqual(self.runs(),6)
        fresh=VisualiserBackend(self.build,self.root)
        self.assertEqual(result,self.execute(selected,backend=fresh))
        self.assertEqual(reverse,self.execute(swapped,backend=fresh))
        metric=next(row for row in reverse['rows'] if row['scenario']=='scenario'
                    and row['variable']=='Mental Component Summary (MCS)'
                    and row['stratifier']=='Overall' and row['variable_value']=='Continuous Mean')
        self.assertEqual(metric['paired_mean_delta'],-5)

    def test_pairwise_accumulator_never_contains_all_alternatives(self):
        selected=self.selected(alternatives=3)
        result=self.execute(selected,comparison_set=True)
        requests=[r for r in self.operations if r['operation']=='aggregate']
        self.assertEqual(len(requests),3)
        self.assertTrue(all(len(request['metrics'])==6 for request in requests))
        self.assertEqual(len(result['series']),4)
        self.assertEqual(self.runs(),12)

    def test_concurrent_comparisons_only_parse_each_seed_once(self):
        selected=self.selected()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.execute(selected),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(self.runs(),6)
        counters=self.backend.summary_cache.statistics()['counters']
        self.assertEqual((counters['raw_started'],counters['raw_completed'],counters['hits']),(6,6,6))

    def test_real_csv_parser_records_eviction_reprocessing_without_changing_results(self):
        selected=self.selected()
        self.backend.summary_cache.maximum_entries=3
        first=self.execute(selected)
        initial=self.backend.summary_cache.statistics()['counters']
        self.assertEqual((initial['raw_started'],initial['eviction_raw_started']),(6,0))
        second=self.execute(selected)
        self.assertEqual(first,second)
        counters=self.backend.summary_cache.statistics()['counters']
        self.assertEqual((counters['raw_started'],counters['raw_completed']),(12,12))
        self.assertEqual((counters['eviction_raw_started'],counters['eviction_raw_completed']),(6,6))
        self.assertEqual(counters['eviction_raw_input_bytes'],sum(f['bytes'] for c in selected for f in c['files']))
        self.assertGreater(counters['eviction_raw_elapsed_ns'],0)
        self.assertEqual(counters['eviction_raw_failed'],0)

    def test_failed_parser_is_measured_but_never_published(self):
        selected=self.selected()
        work=self.root/'failed';work.mkdir()
        def failure(*args,**kwargs):
            raise ValueError('Fictional parser rejection')
        with self.assertRaisesRegex(ValueError,'Fictional parser rejection'):
            self.backend.process_files(selected,work,failure,lambda _:None)
        counters=self.backend.summary_cache.statistics()['counters']
        self.assertEqual((counters['raw_started'],counters['raw_completed'],counters['raw_failed']),(1,0,1))
        self.assertEqual(self.backend.prune_summaries()['entries'],0)

    def test_cache_hit_still_verifies_original_source_and_owner_identity(self):
        from jasmine_web.batch.results import OutputUnavailable
        selected=self.selected()
        self.execute(selected)
        changed=copy.deepcopy(selected)
        for item in changed:item['owner']='bob'
        self.execute(changed)
        self.assertEqual(self.runs(),12)
        first=selected[0]['files'][0]
        (self.root/first['path']).write_text('changed')
        with self.assertRaises(OutputUnavailable):self.execute(selected)
        self.assertEqual(self.runs(),12)
        self.backend.prune_summaries(owner='alice')
        self.assertEqual(self.backend.prune_summaries()['entries'],6)

    def test_changed_valid_source_or_calculation_identity_does_not_reuse_stale_summary(self):
        selected=self.selected()
        original=self.execute(selected)
        first=selected[0]['files'][0]
        path=self.root/first['path']
        path.write_text(path.read_text().replace(',10,50,',',40,50,'))
        first.update(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        changed=self.execute(selected)
        self.assertEqual(self.runs(),7)
        self.assertNotEqual(changed,original)
        selected[0]['configuration']['model']='sha256:'+'b'*64
        self.execute(selected)
        self.assertEqual(self.runs(),10)

    def test_old_build_is_usable_but_cannot_silently_enable_summary_relabelling(self):
        if b'simpaths.visualiser.role-independent-metrics.v1' in (Path(BUILD)/'runner.cjs').read_bytes():
            self.skipTest('Historical runner required for this compatibility check')
        old=VisualiserBackend(BUILD,self.root)
        self.assertFalse(old.supports_summary_cache)
        with self.assertRaisesRegex(ValueError,'role-independent'):
            VisualiserBackend(BUILD,self.root,summary_cache_root=self.root/'unsupported')


if __name__=='__main__':unittest.main()
