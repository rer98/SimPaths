"""(C) Copyright 2026, by Ross Richardson

Unified recovery retains private SingleRun requests and numeric demand history.
Uses fictional contacts, native PostgreSQL and captured mail; no real emails.
@author ross richardson
"""
from contextlib import nullcontext,redirect_stderr
import io
import unittest
from uuid import uuid4

from . import backup,backup_live
from .maintenance import service_state
from . import test_backup as fixtures


@unittest.skipUnless(fixtures.DSN,'Use the disposable notification recovery proof')
class AvailabilityRestoreTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BackupRestoreTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def roundtrip(self,running):
        from jasmine_web.availability_store import AvailabilityStore
        from jasmine_web.availability_interest import InterestStore
        from jasmine_web.vm_state import PostgresVMState,VMStateUnavailable
        f=self.f; schema='vm_availability_'+uuid4().hex[:12]
        binding=dict(pool_id=f.q.pool_id,schema=f.q.schema)
        state=PostgresVMState(f.q.dsn,schema=schema,shared_pool=binding)
        self.addCleanup(f.f.sql,'DROP SCHEMA '+schema+' CASCADE')
        self.addCleanup(state.close); state.migrate()
        source=AvailabilityStore(state)
        sent=source.subscribe(source.busy('fictional','network-one','slots'),'sent@example.org')
        source.available([sent]); claim=source.claim([sent]); source.complete(claim,sent=True)
        keys=[source.subscribe(source.busy('fictional','network-'+str(i),'slots'),email)
              for i,email in enumerate(('waiting@example.org','uncertain@example.org'))]
        with state.connection() as c:
            c.execute("UPDATE availability_delivery SET next_notice_at='epoch',"
                      "explicit_leave_until=clock_timestamp()+interval '5 minutes'")
        # Capture one in-flight lease, representing an interrupted sender.
        with state.connection() as c:c.execute("UPDATE availability_delivery SET explicit_leave_until='epoch'")
        outstanding=source.claim([keys[1]])
        opted=source.verified_signup(source.busy('fictional','updates-network','slots'),
                                    'updates@example.org',updates=True)
        source_interest=InterestStore(state); source_rows=source_interest.export()['rows']
        with state.connection() as c:
            c.execute("UPDATE availability_delivery SET explicit_leave_until=clock_timestamp()+interval '5 minutes'")
        before=source.status(days=3660)
        def capture(c,dsn,selected,destination,origin):
            self.assertEqual(selected,schema)
            return dict(images=[],sessions=[])
        with redirect_stderr(io.StringIO()),(service_state(f.state) if running else nullcontext()):
            backup_live.create(f.q,f.state,f.backup,f.tools,single_capture=capture)
        backup.restore(f.target_q,f.backup,f.target,f.target_tools)
        target=PostgresVMState(f.target_q.dsn,schema=schema,shared_pool=binding)
        self.addCleanup(target.close)
        with self.assertRaises(VMStateUnavailable):target.migrate()
        backup.activate(f.target_q,f.backup,f.target,image_check=lambda _:None,source_isolated=True)
        target.migrate(); restored=AvailabilityStore(target)
        self.assertEqual(before['totals'],restored.status(days=3660)['totals'])
        self.assertEqual(before['current']['pending'],restored.status()['current']['pending'])
        self.assertIsNone(restored.claim(keys))  # The original Leave grace survives.
        with target.connection() as c:
            c.execute("UPDATE availability_delivery SET next_notice_at='epoch',explicit_leave_until='epoch'")
            c.execute("UPDATE availability_requests SET claim_until=clock_timestamp()-interval '1 second' WHERE state='sending'")
        self.assertEqual(len(restored.pending()),3)
        self.assertFalse(restored.complete(outstanding,sent=True))
        restored.available(keys); delivery=restored.claim(keys)
        self.assertEqual(delivery['email'],'waiting@example.org')
        restored.complete(delivery,sent=True)
        self.assertEqual(restored.status()['totals']['delivery_failures'],1)
        self.assertEqual(source.status()['totals'],before['totals'])
        current=source.status()['current']
        self.assertEqual((current['pending'],current['sending']),
                         (before['current']['pending'],before['current']['sending']))
        self.assertEqual(target.get_all_sessions(),[])
        recovered_interest=InterestStore(target)
        self.assertEqual(source_rows,recovered_interest.export()['rows'])
        recovered_interest.withdraw(opted['interest_id'])
        self.assertEqual(recovered_interest.export()['active'],0)
        self.assertEqual(source_interest.export()['active'],1)

    def test_running_host_backup_preserves_requests_and_demand_without_admission(self):
        self.roundtrip(True)

    def test_stopped_host_unified_backup_preserves_requests_and_demand(self):
        self.roundtrip(False)


if __name__=='__main__':unittest.main()
