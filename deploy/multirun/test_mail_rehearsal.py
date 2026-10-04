"""(C) Copyright 2026, by Ross Richardson

Offline tests of the non-relaying SMTP fixture and mail rehearsal isolation.
No sockets, database connections, models or real recipients are used here.
@author ross richardson
"""
import base64
from copy import deepcopy
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from .mail_fixture import check_message, validate
from .smtp_capture import Capture, FROM, MAX_MESSAGE, RECIPIENTS, Session, address


def message(body='Fictional controlled message\nhttp://127.0.0.1:15002/\n'):
    value = EmailMessage()
    value['From'], value['To'], value['Subject'] = FROM, 'alice@example.org', 'MultiRun: fixture'
    value['Message-ID'] = '<multirun-12345678-1234-1234-1234-123456789012@example.org>'
    value.set_content(body)
    return value


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.capture = Capture(self.root/'mail', login=FROM, password='FICTIONAL-PASSWORD-THAT-IS-NOT-A-CREDENTIAL')

    def session(self, commands, *, tls=False):
        session = Session.__new__(Session)
        session.server = SimpleNamespace(capture=self.capture)
        session.rfile, session.wfile = io.BytesIO(commands), io.BytesIO()
        session.tls, session.authenticated, session.greeted = tls, False, False
        session.sender, session.recipients = None, []
        session.handle()
        return session.wfile.getvalue()

    def authenticated(self, suffix=b'QUIT\r\n', *, password=None):
        encoded = base64.b64encode(b'\0'+FROM.encode()+b'\0'+(password or self.capture.password).encode())
        return self.session(b'EHLO localhost\r\nAUTH PLAIN '+encoded+b'\r\n'+suffix, tls=True)

    def packet(self, value):
        raw = value.as_bytes(policy=policy.SMTP).replace(b'\n.', b'\n..')
        return (b'MAIL FROM:<'+FROM.encode()+b'>\r\nRCPT TO:<alice@example.org>\r\nDATA\r\n'
                +raw+b'.\r\nQUIT\r\n')

    def test_plaintext_session_advertises_starttls_and_refuses_authentication_and_mail(self):
        result = self.session(b'EHLO localhost\r\nAUTH PLAIN AGJhZABiYWQ=\r\n'
                              b'MAIL FROM:<online@example.org>\r\nDATA\r\nQUIT\r\n')
        self.assertIn(b'250-STARTTLS', result)
        self.assertNotIn(b'250-AUTH', result)
        self.assertEqual(result.count(b'530'), 3)
        self.assertEqual(self.capture.messages(), [])

    def test_authenticated_tls_message_is_dot_unstuffed_and_saved_privately(self):
        value = message('Fictional\n.leading dot\n')
        result = self.authenticated(self.packet(value))
        self.assertIn(b'235 Authentication successful', result)
        self.assertIn(b'250 Captured locally', result)
        self.assertEqual(self.capture.messages()[0].get_content().replace('\r\n', '\n'), value.get_content())
        self.assertEqual(self.capture.root.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.capture.root.glob('*.eml')))
        self.assertTrue(self.capture.events[0]['tls'] and self.capture.events[0]['authenticated'])

    def test_invalid_authentication_remains_denied_without_recording_mail(self):
        result = self.authenticated(self.packet(message()), password='wrong')
        self.assertIn(b'535 Authentication rejected', result)
        self.assertNotIn(b'250 Captured locally', result)
        self.assertEqual(self.capture.events, [])

    def test_mail_requires_greeting_authentication_sender_and_a_fictional_recipient(self):
        result = self.authenticated(b'DATA\r\nMAIL FROM:<outside@example.org>\r\n'
            b'MAIL FROM:<online@example.org>\r\nRCPT TO:<real-person@example.com>\r\nDATA\r\nQUIT\r\n')
        self.assertEqual(result.count(b'503'), 2)
        self.assertIn(b'550 Fictional sender only', result)
        self.assertIn(b'550 Fictional recipient only; relay disabled', result)
        self.assertEqual(self.capture.messages(), [])

    def test_data_rejection_never_saves_a_message_and_retry_uses_the_same_identifier(self):
        value = message()
        self.capture.reject(message_id=value['Message-ID'])
        first = self.authenticated(self.packet(value))
        self.assertIn(b'451 Fictional temporary rejection', first)
        self.assertEqual(self.capture.messages(), [])
        self.assertEqual(list(self.capture.root.iterdir()), [])
        second = self.authenticated(self.packet(value))
        self.assertIn(b'250 Captured locally', second)
        self.assertEqual([e['accepted'] for e in self.capture.events], [False, True])
        self.assertEqual(len({e['message_id'] for e in self.capture.events}), 1)

    def test_fault_selector_does_not_reject_another_notification(self):
        self.capture.reject(message_id='<different@example.org>')
        self.authenticated(self.packet(message()))
        self.assertEqual(len(self.capture.messages()), 1)
        self.assertEqual(self.capture.remaining, 1)

    def test_reset_discards_old_recipient_without_losing_tls_authentication(self):
        result = self.authenticated(b'MAIL FROM:<online@example.org>\r\nRCPT TO:<alice@example.org>\r\n'
                                    b'RSET\r\nDATA\r\nQUIT\r\n')
        self.assertIn(b'250 Reset', result)
        self.assertIn(b'503 MAIL and RCPT required', result)
        self.assertEqual(self.capture.messages(), [])

    def test_oversized_and_truncated_messages_are_not_accepted(self):
        for tail in (b'x'*(MAX_MESSAGE+1)+b'\r\n.\r\n', b'truncated'):
            packet = (b'MAIL FROM:<online@example.org>\r\nRCPT TO:<alice@example.org>\r\nDATA\r\n'+tail)
            self.authenticated(packet)
        self.assertEqual(self.capture.messages(), [])

    def test_bad_command_lengths_and_line_endings_stop_without_acceptance(self):
        for command in (b'EHLO '+b'x'*2048+b'\r\n', b'EHLO localhost\n'):
            self.assertIn(b'500 Invalid command', self.session(command))
        self.assertEqual(self.capture.events, [])

    def test_capture_cannot_accept_unencrypted_unauthenticated_or_nonfictional_mail(self):
        for kwargs in (dict(tls=False, authenticated=True), dict(tls=True, authenticated=False)):
            with self.assertRaises(ValueError):
                self.capture.accept(message().as_bytes(), ['alice@example.org'], **kwargs)
        for recipients in ([], ['outside@example.org']):
            with self.assertRaises(ValueError):
                self.capture.accept(message().as_bytes(), recipients, tls=True, authenticated=True)
        with self.assertRaises(ValueError):
            self.capture.accept(b'x'*(MAX_MESSAGE+1), ['alice@example.org'], tls=True, authenticated=True)

    def test_sender_and_recipient_envelopes_do_not_accept_injected_commands(self):
        self.assertEqual(address('MAIL FROM:<online@example.org> SIZE=1024', 'MAIL FROM:'), FROM)
        for command in ('RCPT TO:<alice@example.org>,<outside@example.org>',
                        'RCPT TO:<alice@example.org>\r\nDATA', 'RCPT TO:alice@example.org'):
            self.assertIsNone(address(command, 'RCPT TO:'))
        self.assertEqual(RECIPIENTS, {'alice@example.org', 'bob@example.org', 'operator@example.org'})


class MessageTests(unittest.TestCase):
    origin = 'http://127.0.0.1:15002'

    def test_plain_text_notice_and_controlled_results_link_are_valid(self):
        body = 'Fictional result\n'+self.origin+'/results/12345678-1234-1234-1234-123456789012\n'
        value = BytesParser(policy=policy.default).parsebytes(message(body).as_bytes())
        self.assertEqual(check_message(value, self.origin), body)

    def test_attachments_html_and_additional_recipients_fail_privacy_validation(self):
        values = []
        value = message(); value.add_attachment(b'raw', maintype='text', subtype='csv', filename='raw.csv'); values.append(value)
        value = message(); value.set_content('<b>raw</b>', subtype='html'); values.append(value)
        value = message(); value['Cc'] = 'bob@example.org'; values.append(value)
        value = message(); value.replace_header('To', 'outside@example.org'); values.append(value)
        for value in values:
            with self.assertRaises(AssertionError): check_message(value, self.origin)

    def test_private_values_token_queries_and_other_hosts_are_rejected(self):
        for body in ('PRIVATE_CANARY', self.origin+'/?token=private', 'https://outside.example.org/', self.origin+'/private/raw.csv'):
            with self.assertRaises(AssertionError):
                check_message(message(body), self.origin, forbidden=('PRIVATE_CANARY',))

    def test_transport_encoding_and_wrapping_do_not_hide_private_values(self):
        canary = 'PRIVATE_'+'é'*100
        for encoding in ('quoted-printable', 'base64'):
            value = message()
            value.set_content(canary, cte=encoding)
            parsed = BytesParser(policy=policy.default).parsebytes(value.as_bytes(policy=policy.SMTP))
            with self.assertRaises(AssertionError):
                check_message(parsed, self.origin, forbidden=(canary,))

    def test_stable_outbox_identifier_and_signin_code_lifetime_are_required(self):
        value = message(); del value['Message-ID']
        with self.assertRaises(AssertionError): check_message(value, self.origin)
        value.replace_header('Subject', 'Your SimPaths Online sign-in code')
        value.set_content('Your sign-in code is 123456. It expires in 10 minutes.\n')
        check_message(value, self.origin)
        value.set_content('Your sign-in code is 123456.\n')
        with self.assertRaises(AssertionError): check_message(value, self.origin)


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'private').mkdir(); (self.root/'cert.pem').write_text('Fictional certificate path')
        self.settings = dict(state=str(self.root/'private'), schema='test_mail_'+'a'*32,
            app_port=15002, origin='http://127.0.0.1:15002', clock='2026-10-04T10:00:00+00:00',
            smtp=dict(SMTP_HOST='127.0.0.1', SMTP_PORT='15025', SMTP_USE_TLS='true',
                SMTP_LOGIN_EMAIL=FROM, SMTP_PASSWORD='FICTIONAL-SECRET-THAT-MUST-NOT-APPEAR',
                SMTP_FROM_EMAIL=FROM, SSL_CERT_FILE=str(self.root/'cert.pem')))
        self.dsn = 'postgresql://postgres:FICTIONAL@127.0.0.1:15432/jasmine_queue_test'

    def validate(self, settings):
        with patch.dict(os.environ, JASMINE_BATCH_TEST_DSN=self.dsn):
            return validate(settings)

    def test_disposable_loopback_settings_are_valid_with_aware_clock(self):
        self.assertIsNotNone(self.validate(self.settings).tzinfo)

    def test_remote_mail_plaintext_privileged_ports_and_inherited_credentials_are_refused(self):
        for key, value in [('SMTP_HOST', 'smtp.example.org'), ('SMTP_USE_TLS', 'false'), ('SMTP_PORT', '25'),
                           ('SMTP_LOGIN_EMAIL', 'real@example.org'), ('SMTP_FROM_EMAIL', 'real@example.org'),
                           ('SMTP_PASSWORD', 'short'), ('UNEXPECTED_SETTING', 'value')]:
            settings = deepcopy(self.settings); settings['smtp'][key] = value
            with self.assertRaises(ValueError) as error: self.validate(settings)
            self.assertNotIn('FICTIONAL-SECRET', str(error.exception))

    def test_production_schema_remote_application_and_naive_clock_are_refused(self):
        for key, value in [('schema', 'jasmine_batch'), ('app_port', 80), ('origin', 'http://remote.example.org:15002'),
                           ('origin', 'http://user@127.0.0.1:15002'), ('clock', '2026-10-04T10:00:00')]:
            settings = dict(self.settings, **{key:value})
            with self.assertRaises(ValueError): self.validate(settings)

    def test_other_certificate_paths_and_symlinked_state_are_refused(self):
        other = self.root/'other.pem'; other.write_text('Fictional unrelated certificate')
        settings = deepcopy(self.settings); settings['smtp']['SSL_CERT_FILE'] = str(other)
        with self.assertRaises(ValueError): self.validate(settings)
        (self.root/'linked').symlink_to(self.root/'private')
        settings = dict(self.settings, state=str(self.root/'linked'))
        with self.assertRaises(ValueError): self.validate(settings)
