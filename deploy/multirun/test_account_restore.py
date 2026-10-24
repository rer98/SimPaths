"""(C) Copyright 2026, by Ross Richardson

Native online/offline recovery of permanent accounts and renewal reminder outboxes.
Fictional model files, captured senders and disposable PostgreSQL only.
@author ross richardson
"""
import asyncio
from contextlib import redirect_stderr
import hashlib
import io
from types import SimpleNamespace
import unittest
from uuid import uuid4

from . import backup,backup_live
from .maintenance import GATE
from . import test_backup as fixtures


@unittest.skipUnless(fixtures.DSN,'Use account_lifecycle_proof.py with disposable PostgreSQL')
class AccountRestoreTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BackupRestoreTests();self.addCleanup(self.f.doCleanups);self.f.setUp()

    def rows(self,q,statement,*parameters):
        with q._connection() as c:return c.execute(statement,parameters).fetchall()

    def roundtrip(self,online):
        from jasmine_web.batch.access import Access
        from jasmine_web.batch.account_reminders import AccountReminders
        from jasmine_web.batch.policy import NotFound
        f=self.f;q=f.q
        f.access.approve_email('alice@example.org',seconds=None,permanent=True)
        f.access.approve_email('bob@example.org',seconds=365*86400)
        with q._transaction() as (c,_,now):
            c.execute("UPDATE principals SET approved_until=%s+interval '20 days' WHERE id=%s",(now,f.other))
            c.execute('UPDATE datasets SET public_training=true WHERE id=%s',(f.provider,))
            c.execute('DELETE FROM dataset_grants WHERE dataset_id=%s',(f.provider,))
        restricted=f.datasets.register_provider('c'*64)
        q.grant_dataset(f.other,restricted,seconds=15*86400)
        reminders=AccountReminders(SimpleNamespace(queue=q),origin='https://multirun.example.org',
            admin_email='admin@example.org',sender=lambda *a:(_ for _ in ()).throw(OSError('fictional relay failure')))
        self.assertFalse(reminders.deliver_one())
        failed=self.rows(q,'SELECT * FROM account_reminders')[0]
        self.assertEqual(failed['kind'],'month');self.assertEqual(failed['attempts'],1)
        # Preserve a future week notice as well as the failed month delivery.
        with q._transaction() as (c,_,now):
            c.execute("UPDATE account_reminders SET retry_at=%s+interval '1 day' WHERE id=%s",(now,failed['id']))
            c.execute('''INSERT INTO account_reminders(id,pool_id,owner_id,approval_revision,expires_at,kind,due_at,retry_at)
                VALUES (%s,%s,%s,%s,%s,'week',%s-interval '7 days',%s)''',
                (str(uuid4()),q.pool_id,f.other,failed['approval_revision'],failed['expires_at'],failed['expires_at'],now))
        accounts=self.rows(q,'SELECT * FROM principals ORDER BY id')
        grants=self.rows(q,'SELECT * FROM dataset_grants ORDER BY dataset_id,owner_id')
        outbox=self.rows(q,'SELECT * FROM account_reminders ORDER BY id')
        dates=self.rows(q,'SELECT * FROM artifact_retention ORDER BY kind,artifact_id')
        with redirect_stderr(io.StringIO()):
            if online:backup_live.create(q,f.state,f.backup,f.tools)
            else:f.save()
        backup.verify(f.backup)
        backup.restore(f.target_q,f.backup,f.target,f.target_tools)
        self.assertTrue((f.target/GATE).exists())
        self.assertEqual(self.rows(f.target_q,'SELECT * FROM principals ORDER BY id'),accounts)
        self.assertEqual(self.rows(f.target_q,'SELECT * FROM dataset_grants ORDER BY dataset_id,owner_id'),grants)
        self.assertEqual(self.rows(f.target_q,'SELECT * FROM account_reminders ORDER BY id'),outbox)
        self.assertEqual(self.rows(f.target_q,'SELECT * FROM artifact_retention ORDER BY kind,artifact_id'),dates)
        # Restore and verification do not deliver queued messages or activate a service.
        backup.activate(f.target_q,f.backup,f.target,image_check=lambda _:None,source_isolated=online)
        with f.target_q._transaction() as (c,_,now):
            self.assertTrue(f.target_q._authorised(c,f.owner,f.dataset,now))
            self.assertTrue(f.target_q._authorised(c,f.owner,f.provider,now))
            self.assertFalse(f.target_q._authorised(c,f.owner,restricted,now))
            self.assertTrue(f.target_q._authorised(c,f.other,restricted,now))
        restored=Access(f.target_q,f.secret,f.deliver)
        challenge=asyncio.run(restored.issue('alice@example.org','recovery-client'))['challenge']
        auth=restored.verify(challenge,f.mail['alice@example.org'],'recovery-client')
        self.assertTrue(auth['authorised'])
        sent=[]
        worker=AccountReminders(SimpleNamespace(queue=f.target_q),origin='https://multirun.example.org',
            admin_email='admin@example.org',sender=lambda *a:sent.append(a))
        self.assertFalse(worker.deliver_one(),'The saved delivery backoff must survive restore')
        with f.target_q._transaction() as (c,_,now):
            c.execute("UPDATE account_reminders SET retry_at=%s-interval '1 second' WHERE id=%s",(now,failed['id']))
        self.assertTrue(worker.deliver_one());self.assertFalse(worker.deliver_one())
        self.assertEqual(len(sent),1);self.assertEqual(sent[0][-1],str(failed['id']))
        output=f.target/'execution'/f.completed.execution_key/'work/output/run_1/csv/Person.csv'
        before=hashlib.sha256(output.read_bytes()).hexdigest()
        restored.approve_email('alice@example.org',seconds=None)
        with self.assertRaises(NotFound):restored.authenticate(auth['token'])
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(),before)
        self.assertEqual(self.rows(f.target_q,'SELECT * FROM artifact_retention ORDER BY kind,artifact_id'),dates)
        restored.approve_email('alice@example.org',seconds=None,permanent=True)
        self.assertEqual(self.rows(f.target_q,'SELECT * FROM artifact_retention ORDER BY kind,artifact_id'),dates)

    def test_offline_restore_preserves_accounts_scoped_grants_and_both_reminder_stages(self):
        self.roundtrip(False)

    def test_online_restore_preserves_accounts_scoped_grants_and_both_reminder_stages(self):
        self.roundtrip(True)


if __name__=='__main__':unittest.main()
