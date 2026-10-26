"""(C) Copyright 2026, by Ross Richardson

Native offline/online restoration of administrator audit, grants and private credentials.
Fictional data and captured mail only; restored services remain behind the recovery gate.
@author ross richardson
"""
import asyncio
from contextlib import redirect_stderr
import io
import unittest

from . import backup,backup_live,test_backup as fixtures
from .maintenance import GATE
from .releases import atomic_json


@unittest.skipUnless(fixtures.DSN,'Use administrator_proof.py with disposable PostgreSQL')
class AdministratorRestoreTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BackupRestoreTests();self.addCleanup(self.f.doCleanups);self.f.setUp()

    def rows(self,q,query):
        with q._connection() as c:return c.execute(query).fetchall()

    def roundtrip(self,online):
        from jasmine_web.batch.access import Access
        from jasmine_web.batch.administration import Administration
        from jasmine_web.batch.administrator_password import make_credential,read_credential
        from jasmine_web.batch.policy import NotFound
        f=self.f
        credential=make_credential('fictional recovery administrator password')
        atomic_json(f.state/'administrator-password.json',credential)
        admin=Administration(f.access,['alice@example.org'],credential,f.deliver)
        challenge=asyncio.run(admin.issue('alice@example.org','fictional recovery administrator password','recovery'))['challenge']
        auth=admin.verify(challenge,f.mail['alice@example.org'],'recovery');self.assertTrue(auth['authorised'])
        reviewed=admin.review(auth['token'],auth['csrf'],action='approve',email='bob@example.org',permanent=True)
        admin.apply(auth['token'],auth['csrf'],review=reviewed['review'])
        restricted=f.datasets.register_provider('c'*64)
        reviewed=admin.review(auth['token'],auth['csrf'],action='grant-dataset',email='bob@example.org',dataset=restricted,days=90)
        admin.apply(auth['token'],auth['csrf'],review=reviewed['review'])
        with f.q._transaction() as (c,_,now):
            c.execute("UPDATE administrator_sessions SET expires_at=%s-interval '1 second'",(now,))
        audit=self.rows(f.q,'SELECT * FROM administrator_audit ORDER BY id')
        grants=self.rows(f.q,'SELECT * FROM dataset_grants ORDER BY dataset_id,owner_id')
        dates=self.rows(f.q,'SELECT * FROM artifact_retention ORDER BY kind,artifact_id')
        with redirect_stderr(io.StringIO()):
            if online:backup_live.create(f.q,f.state,f.backup,f.tools)
            else:f.save()
        manifest=backup.verify(f.backup)
        self.assertIn('administrator-password.json',manifest['state_tree']['files'])
        backup.restore(f.target_q,f.backup,f.target,f.target_tools)
        self.assertTrue((f.target/GATE).exists())
        self.assertEqual(read_credential(f.target/'administrator-password.json'),credential)
        self.assertEqual(self.rows(f.target_q,'SELECT * FROM administrator_audit ORDER BY id'),audit)
        self.assertEqual(self.rows(f.target_q,'SELECT * FROM dataset_grants ORDER BY dataset_id,owner_id'),grants)
        self.assertEqual(self.rows(f.target_q,'SELECT * FROM artifact_retention ORDER BY kind,artifact_id'),dates)
        # A restored session keeps its original expiry; restoration grants no new time.
        restored=Administration(Access(f.target_q,f.secret,f.deliver),['alice@example.org'],credential,f.deliver)
        with self.assertRaises(NotFound):restored.session(auth['token'])
        self.assertTrue((f.target/GATE).exists())
        self.assertEqual(len(audit),2)

    def test_offline_recovery_preserves_audit_grants_password_and_original_expiry(self):self.roundtrip(False)
    def test_online_recovery_preserves_audit_grants_password_and_original_expiry(self):self.roundtrip(True)


if __name__=='__main__':unittest.main()
