"""(C) Copyright 2026, by Ross Richardson

Real summary-cache occupancy, live approval CLI and conservative filesystem
capacity checks using tiny private fixtures; no model or email execution.
@author ross richardson
"""
from contextlib import nullcontext,redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
from jasmine_web.batch.local_executor import atomic_json
from jasmine_web.batch.visualiser_storage import BUDGET,FORMAT,MIB,DiskHeadroom,budget,read_status
from .visualiser_backend import VisualiserBackend
from .visualiser_summary_cache import SummaryCache
from .visualiser_storage import main


class StorageAdapterTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.state=self.root/'state';self.state.mkdir(mode=0o700)

    def test_real_summary_snapshot_reports_data_and_missing_partner_promises(self):
        cache=SummaryCache(self.root/'cache',maximum_bytes=4*MIB,maximum_entries=4,entry_bytes=256*1024)
        identity=dict(owner='alice',configuration='baseline',execution=dict(job='job',attempt='attempt'),seed='606')
        source=self.root/'metrics.json';source.write_bytes(b'[{"metric_value":1}]');source.chmod(0o600)
        with cache.entry(identity) as entry:self.assertTrue(entry.publish(source))
        backend=VisualiserBackend.__new__(VisualiserBackend);backend.summary_cache=cache
        result=backend.storage_snapshot()
        self.assertEqual(result['entries'],1)
        self.assertGreater(result['bytes'],source.stat().st_size)
        self.assertEqual(result['promised_bytes'],result['bytes'])
        self.assertEqual(result['maximum_bytes'],4*MIB)
        self.assertEqual(result['deferred'],0)
        self.assertNotIn('owner',result);self.assertNotIn('job',result)

    def test_physical_headroom_separates_current_promises_and_full_pool_growth(self):
        queue=SimpleNamespace(_transaction=lambda:nullcontext((None,dict(storage_mib=1000),None)))
        executor=SimpleNamespace(root=self.root,workspace_quotas=SimpleNamespace(
            ready=lambda:dict(remaining_reserved_bytes=100*MIB,reserve_bytes=10*MIB)))
        with patch('jasmine_web.batch.visualiser_storage.shutil.disk_usage',return_value=SimpleNamespace(free=2000*MIB)):
            value=DiskHeadroom(queue,executor,reserve_bytes=64*MIB)()
        self.assertEqual(value,dict(free_bytes=2000*MIB,committed_bytes=110*MIB,
                                  growth_committed_bytes=1000*MIB,reserve_bytes=64*MIB))

    def test_cli_changes_only_the_approved_ceiling_and_read_only_status_writes_nothing(self):
        root=self.state/'execution/visualiser-cache';root.mkdir(mode=0o700,parents=True)
        control=dict(format=FORMAT,initial_bytes=16*MIB,capacity_bytes=16*MIB,maximum_bytes=16*MIB,
            summary_bytes=8*MIB,summary_maximum_bytes=12*MIB,growths=0)
        atomic_json(root/BUDGET,control)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--state',str(self.state),'--maximum-mib','32','--summary-maximum-mib','24']),0)
        approved=json.loads(output.getvalue())
        self.assertEqual(approved['maximum_bytes'],32*MIB)
        self.assertEqual(approved['capacity_bytes'],16*MIB)
        before=(root/BUDGET).read_bytes()
        with redirect_stdout(io.StringIO()):self.assertEqual(main(['--state',str(self.state)]),0)
        self.assertEqual((root/BUDGET).read_bytes(),before)
        self.assertEqual(read_status(root)['budget'],budget(root))

    def test_cli_cannot_reduce_an_admitted_budget(self):
        root=self.state/'execution/visualiser-cache';root.mkdir(mode=0o700,parents=True)
        atomic_json(root/BUDGET,dict(format=FORMAT,initial_bytes=16*MIB,capacity_bytes=20*MIB,
            maximum_bytes=32*MIB,summary_bytes=8*MIB,summary_maximum_bytes=12*MIB,growths=1))
        with self.assertRaises(ValueError):main(['--state',str(self.state),'--maximum-mib','16'])

    def test_cli_direct_script_can_inspect_existing_controls(self):
        root=self.state/'execution/visualiser-cache';root.mkdir(mode=0o700,parents=True)
        control=dict(format=FORMAT,initial_bytes=16*MIB,capacity_bytes=16*MIB,maximum_bytes=16*MIB,
            summary_bytes=8*MIB,summary_maximum_bytes=12*MIB,growths=0)
        atomic_json(root/BUDGET,control)
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('visualiser_storage.py')),
            '--state',str(self.state)],check=True,capture_output=True,text=True,timeout=30)
        self.assertEqual(json.loads(result.stdout),control)


if __name__=='__main__':unittest.main()
