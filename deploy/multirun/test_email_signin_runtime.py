"""(C) Copyright 2026, by Ross Richardson
Verify the native registry enables email links with its trusted configured origin.
Uses the existing disposable VM fixture and captured mail; no SMTP or model work.
@author ross richardson
"""
import asyncio
import os
import secrets
import unittest
from urllib.parse import urlsplit

from . import runtime
from . import test_vm_runtime


@unittest.skipUnless(os.environ.get('JASMINE_BATCH_TEST_DSN'),'Use disposable PostgreSQL')
class EmailSignInRuntimeTests(unittest.TestCase):
    def test_registry_uses_configured_origin_and_persistent_secret_for_ordinary_links(self):
        fixture=test_vm_runtime.VMRuntimeTests();self.addCleanup(fixture.doCleanups);fixture.setUp()
        fixture.args.origin=fixture.origin
        messages=[]
        async def deliver(email,code,url):messages.append((email,code,url))
        access,_,_,_=runtime.create_registry(fixture.args,fixture.q,fixture.state,fixture.deliver,
                                             send_link_mail=deliver)
        secret=secrets.token_urlsafe(32)
        issued=asyncio.run(access.issue('alice@example.org','192.0.2.1',browser_secret=secret))
        self.assertTrue(issued['link_available'])
        self.assertTrue(messages[0][2].startswith(fixture.origin+'/sign-in#'))
        restarted,_,_,_=runtime.create_registry(fixture.args,fixture.q,fixture.state,fixture.deliver,
                                                send_link_mail=deliver)
        value=restarted.verify_link(urlsplit(messages[0][2]).fragment,secret,'192.0.2.1')
        self.assertTrue(value['authorised'])
        self.assertEqual(restarted.authenticate(value['token']),fixture.owner)
        self.assertFalse(access.verify(issued['challenge'],messages[0][1],'192.0.2.1')['authorised'])
