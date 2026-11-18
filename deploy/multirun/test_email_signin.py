"""(C) Copyright 2026, by Ross Richardson
Captured SMTP messages for the MultiRun link/code pair and separate administrator flow.
@author ross richardson
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path

sys.path.insert(0,str(frontend_path()))
from .vm_web import verification_mail, administrator_verification_mail


class EmailTemplateTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'SMTP_FROM_EMAIL':'team@example.org'})
        env.start();self.addCleanup(env.stop)
        mail=patch('jasmine_web.contact.deliver_email')
        self.send=mail.start();self.addCleanup(mail.stop)
        self.url='https://multirun.example.org/sign-in#'+('signed.fictional_token-'*20)

    def test_plain_and_html_mail_include_same_link_code_expiry_and_browser_fallback(self):
        asyncio.run(verification_mail('alice@example.org','123456',self.url))
        self.send.assert_called_once();message=self.send.call_args.args[0]
        self.assertEqual(message['To'],'alice@example.org')
        self.assertEqual(len(message.get_all('Subject')),1)
        self.assertIn('link and code',message['Subject'])
        plain=message.get_body(preferencelist=('plain',)).get_content()
        html=message.get_body(preferencelist=('html',)).get_content()
        for body in (plain,html):
            for text in (self.url,'123456','10 minutes','original browser','either','unusable'):
                self.assertIn(text,body)
        self.assertIn('href="'+self.url+'"',html)
        self.assertIn('Sign in to MultiRun',html)

    def test_code_only_sender_and_administrator_remain_plain_code_messages(self):
        asyncio.run(verification_mail('alice@example.org','123456'))
        ordinary=self.send.call_args.args[0]
        self.assertFalse(ordinary.is_multipart());self.assertIn('123456',ordinary.get_content())
        asyncio.run(administrator_verification_mail('admin@example.org','654321'))
        admin=self.send.call_args.args[0]
        self.assertFalse(admin.is_multipart());self.assertIn('administrator password',admin.get_content())
        self.assertNotIn('/sign-in',admin.get_content())

    def test_invalid_code_url_or_recipient_never_reaches_mail_delivery(self):
        for email,code,url in (
            ('alice@example.org','<script>',self.url),
            ('alice@example.org','123456','https://other.example/sign-in?token=private'),
            ('alice@example.org','123456','http://public.example/sign-in#token'),
            ('alice@example.org','123456','https://other.example/sign-in#<img>'),
            ('alice@example.org\nBcc: other@example.org','123456',self.url),
        ):
            with self.subTest(email=email,code=code,url=url),self.assertRaises(ValueError):
                asyncio.run(verification_mail(email,code,url))
        self.send.assert_not_called()
