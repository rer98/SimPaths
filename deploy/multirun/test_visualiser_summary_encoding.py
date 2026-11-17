"""(C) Copyright 2026, by Ross Richardson

Lossless private summary storage, legacy recovery and large-sweep accounting.
Fictional summaries exercise the real cache and backup verifiers, without models.
@author ross richardson
"""
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from .visualiser_summary_cache import SummaryCache, MIB, canonical
from .visualiser_summary_encoding import COMPRESSED, LEGACY, copy_verified, payload
from .visualiser_summary_holds import scope
from .backup_visualiser import summary
from .artifacts import ArtifactError


class SummaryEncodingTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.cache=SummaryCache(self.root/'summaries',maximum_bytes=8*MIB,maximum_entries=16384,compression=True)
        self.identity=dict(owner='alice',configuration='policy',execution=dict(job='job',attempt='attempt'),
            seed='606',calculation={'runner.cjs':'a'*64},node='v22.test')
        self.body=canonical({'metrics':[{'year':2020+i%50,'mean':1.23456789,'n':10,'category':'Fictional'} for i in range(10000)]})
        self.source=self.root/'source';self.source.write_bytes(self.body);self.source.chmod(0o600)

    def publish(self):
        with self.cache.entry(self.identity) as entry:
            self.assertTrue(entry.publish(self.source));key=entry.key
        directory=self.cache.root/key
        return key,directory,json.loads((directory/'receipt.json').read_text())

    def test_lossless_payload_and_backup_use_actual_compressed_bytes(self):
        key,directory,receipt=self.publish()
        self.assertEqual(receipt['format'],COMPRESSED)
        self.assertLess(receipt['stored_bytes'],len(self.body)//10)
        self.assertEqual(gzip.decompress((directory/'metrics.json.gz').read_bytes()),self.body)
        self.assertEqual(receipt['sha256'],hashlib.sha256(self.body).hexdigest())
        entries=self.cache._entries()
        self.assertEqual(entries[0][2],receipt['stored_bytes']+(directory/'receipt.json').stat().st_size)
        keys,identity=summary(self.root,key)
        self.assertEqual(keys,{'summaries/'+key+'/receipt.json','summaries/'+key+'/metrics.json.gz'})
        self.assertEqual(identity,self.identity)
        # Readers can restore both encodings, even when compression is disabled.
        restarted=SummaryCache(self.cache.root,maximum_entries=16384)
        with restarted.entry(self.identity) as entry:self.assertTrue(entry.restore(self.root/'restored'))
        self.assertEqual((self.root/'restored').read_bytes(),self.body)

    def test_legacy_files_remain_unchanged_and_readable(self):
        self.cache.compression=False;key,directory,receipt=self.publish()
        self.assertEqual(receipt['format'],LEGACY)
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
        self.cache.compression=True
        with self.cache.entry(self.identity) as entry:self.assertTrue(entry.restore(self.root/'restored'))
        self.assertEqual((self.root/'restored').read_bytes(),self.body)
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()})
        self.assertIn('summaries/'+key+'/metrics.json',summary(self.root,key)[0])

    def test_changed_compressed_content_is_rejected_by_restore_and_backup(self):
        key,directory,receipt=self.publish();file=directory/payload(receipt)
        body=file.read_bytes();file.write_bytes(body[:-1]+bytes([body[-1]^1]))
        with self.assertRaises(ArtifactError):summary(self.root,key)
        with self.cache.entry(self.identity) as entry:self.assertFalse(entry.restore(self.root/'restored'))
        self.assertFalse((self.root/'restored').exists())

    def test_bounded_decompression_rejects_a_forged_small_logical_receipt(self):
        key,directory,receipt=self.publish()
        receipt['bytes']=receipt['stored_bytes']+1
        with self.assertRaises(ValueError):copy_verified(self.cache,directory,receipt)

    def test_cancelled_compression_cleans_its_temporary_file_and_publishes_nothing(self):
        with self.cache.entry(self.identity) as entry:
            def stop():raise RuntimeError('stopped')
            entry.check=stop
            with self.assertRaises(RuntimeError):entry.publish(self.source)
            self.assertFalse((self.cache.root/entry.key).exists())
        self.assertFalse(list(self.root.glob('.compressed-summary-*')))

    def test_incompressible_short_data_uses_the_legacy_representation(self):
        self.source.write_bytes(b'{"mean":1}')
        _,directory,receipt=self.publish()
        self.assertEqual(receipt['format'],LEGACY);self.assertTrue((directory/'metrics.json').exists())

    def test_protection_and_expiry_keep_the_original_deadline_across_compression(self):
        created=time.time()-10;expiry=created+3600
        record=dict(comparison='source-retained:fictional',created_at=created,expires_at=expiry,
            configurations=[dict(configuration='policy',job='job',attempt='attempt',seeds=['606'])])
        self.cache.maximum_bytes=128*MIB
        self.cache.holds.replace('alice','experiment',[record],calculation=scope(self.identity),owner_entries=2048)
        self.publish()
        self.cache.holds.replace('alice','experiment',[record],calculation=scope(self.identity),owner_entries=2048)
        self.assertEqual(next(iter(self.cache.holds.load().values()))['expires_at'],expiry)
        self.cache.maximum_bytes=MIB
        self.assertEqual(self.cache.prune()['entries'],1)

    def test_reuse_checks_each_payload_without_rescanning_the_sweep_and_policy_changes_recheck_budget(self):
        key,directory,receipt=self.publish()
        with patch.object(self.cache,'_entries',wraps=self.cache._entries) as scans:
            for i in range(4):
                with self.cache.entry(self.identity) as entry:self.assertTrue(entry.restore(self.root/'restored'))
            self.assertEqual(scans.call_count,1)
            self.cache.maximum_entries-=1
            with self.cache.entry(self.identity) as entry:self.assertTrue(entry.restore(self.root/'restored'))
            self.assertEqual(scans.call_count,2)
            # A reuse optimisation must never cache file integrity.
            file=directory/payload(receipt);body=file.read_bytes()
            file.write_bytes(body[:-1]+bytes([body[-1]^1]))
            with self.cache.entry(self.identity) as entry:self.assertFalse(entry.restore(self.root/'bad'))
            self.assertEqual(scans.call_count,2)

    def test_ten_thousand_seed_entries_and_all_1024_configuration_holds_fit_actual_stored_sizes(self):
        self.cache.maximum_bytes=32*1024*MIB
        entries=[];plans=[];created=time.time()-10
        for config in range(1024):
            job='job-'+str(config);name='configuration-'+str(config)
            plans.append(dict(comparison='source-retained:'+name,created_at=created,expires_at=created+3600,
                configurations=[dict(configuration=name,job=job,attempt='attempt',seeds=[str(seed) for seed in range(10)])]))
            for seed in range(10):
                identity=dict(self.identity,configuration=name,execution=dict(job=job,attempt='attempt'),seed=str(seed))
                key=hashlib.sha256(canonical(identity)).hexdigest()
                entries.append((1,key,2600000,dict(identity=identity)))
        start=time.monotonic()
        with patch.object(self.cache,'_entries',return_value=entries):
            result=self.cache.holds.replace('alice','experiment',plans,calculation=scope(self.identity),owner_entries=2048)
        self.assertEqual(result,dict(admitted=1024,deferred=0))
        self.assertEqual(len(self.cache.holds.load()),1024)
        self.assertEqual(self.cache.holds.occupancy(self.cache.holds.load(),entries)[:2],(26624000000,10240))
        print('LARGE_SUMMARY_ACCOUNTING: configurations=1024 seeds=10240 seconds='+format(time.monotonic()-start,'.3f'),flush=True)

    def test_real_inventory_reads_ten_thousand_small_private_receipts(self):
        body=b'{}';digest=hashlib.sha256(body).hexdigest()
        for i in range(10240):
            identity=dict(self.identity,configuration='configuration-'+str(i//10),seed=str(i%10))
            key=hashlib.sha256(canonical(identity)).hexdigest();directory=self.cache.root/key
            directory.mkdir(mode=0o700)
            for name,value in [('metrics.json',body),('receipt.json',canonical(dict(format=LEGACY,key=key,
                    identity=identity,bytes=len(body),sha256=digest)))]:
                file=directory/name;file.write_bytes(value);file.chmod(0o600)
        start=time.monotonic();entries=self.cache._entries(read_only=True)
        self.assertEqual(len(entries),10240)
        self.assertEqual(len({entry[1] for entry in entries}),10240)
        print('LARGE_SUMMARY_INVENTORY: seeds=10240 seconds='+format(time.monotonic()-start,'.3f'),flush=True)


if __name__=='__main__':unittest.main()
