"""(C) Copyright 2026, by Ross Richardson

Native SMTP/STARTTLS tests combined with real PostgreSQL retention and cleanup.
Invoked only by mail_rehearsal against its non-relaying temporary mail capture.
@author ross richardson
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email.message import EmailMessage
import os
import smtplib
import ssl
import unittest
from unittest.mock import patch

from .mail_fixture import check_message
from .smtp_capture import FROM
from jasmine_web.batch.completion_notifications import CompletionNotifications
from jasmine_web.batch.notifications import Notifications, smtp_sender
from jasmine_web.batch.output_management import output_locks
from jasmine_web.batch.retention import Retention, NOTICE, WINDOW
from jasmine_web.contact import deliver_email
import test_completion_notifications
import test_retention
import test_storage


class MailDeliveryTests(unittest.TestCase):
    capture = None  # Assigned only after the guarded native rehearsal starts.

    def setUp(self):
        if self.capture is None:
            raise RuntimeError('Use mail_rehearsal.py with its disposable SMTP capture')
        self.before = len(self.capture.messages())
        self.sender = smtp_sender(FROM)

    def tearDown(self):
        # One failed assertion must not leave a selected rejection armed for a
        # different case. Captured mail itself remains available to the report.
        with self.capture.lock:
            self.capture.remaining, self.capture.reject_id = 0, None

    def fixture(self):
        fixture = test_retention.RetentionTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.retention.sender = self.sender
        return fixture

    def drain(self, fixture):
        for _ in range(50):
            if not fixture.deliver():
                return
        self.fail('Fictional outbox did not drain within its bound')

    def messages(self, origin, *, forbidden=()):
        values = self.capture.messages()[self.before:]
        for message in values:
            check_message(message, origin, forbidden=forbidden)
        return values

    def test_transport_rejects_plaintext_bad_credentials_untrusted_certificate_and_relay(self):
        with smtplib.SMTP('127.0.0.1', int(os.environ['SMTP_PORT']), timeout=5) as client:
            client.ehlo()
            self.assertFalse(client.has_extn('auth'))
            self.assertEqual(client.mail(FROM)[0], 530)
            self.assertEqual(client.docmd('AUTH', 'PLAIN AGJhZABiYWQ=')[0], 530)
        message = EmailMessage()
        message['From'], message['To'] = FROM, 'alice@example.org'
        message.set_content('Fictional transport check')
        with patch.dict(os.environ, SMTP_PASSWORD='WRONG-FICTIONAL-PASSWORD'):
            with self.assertRaises(smtplib.SMTPAuthenticationError):
                deliver_email(message)
        with patch.dict(os.environ):
            os.environ.pop('SSL_CERT_FILE', None)
            with self.assertRaises(ssl.SSLCertVerificationError):
                deliver_email(message)
        message.replace_header('To', 'outside-capture@example.org')
        with self.assertRaises(smtplib.SMTPRecipientsRefused):
            deliver_email(message)
        self.assertEqual(len(self.capture.messages()), self.before)

    def test_warning_final_reminder_and_reader_guarded_physical_output_cleanup(self):
        f = self.fixture()
        (first, work), (second, other_work) = f.outputs()
        f.tick(3.1); self.drain(f)
        warning = [m for m in self.messages(f.retention.origin, forbidden=(str(f.f.root), 'private diagnostics'))
                   if first.attempt_id in m.get_content()]
        self.assertEqual(len(warning), 1)
        self.assertIn('scheduled data deletion', warning[0]['Subject'])
        f.tick(3); self.drain(f)
        notices = [m for m in self.messages(f.retention.origin) if first.attempt_id in m.get_content()]
        self.assertEqual(len(notices), 2)
        self.assertIn('final data deletion reminder', notices[1]['Subject'])
        f.tick(1.1)
        with output_locks(f.r.executor.root, [first.execution_key]):
            f.retire()
            self.assertTrue((work/'output-606.csv').exists())
            self.assertEqual(f.record('output', first.attempt_id)['state'], 'deleting')
            self.assertEqual(f.r.client.get('/downloads/'+first.job_id).status_code, 403)
        f.retire()
        self.assertFalse((work/'output-606.csv').exists())
        self.assertFalse((other_work/'output-606.csv').exists())
        self.assertTrue((work/'private.log').exists())
        self.assertEqual(f.record('output', first.attempt_id)['state'], 'deleted')
        self.assertEqual(f.q.inspect(f.f.owner, f.experiment)['jobs'][0]['state'], 'succeeded')

    def test_temporary_smtp_rejection_retries_same_retention_identifier_without_extending_deadline(self):
        f = self.fixture()
        key = f.upload(); f.tick(3.1)
        row = f.sql('SELECT * FROM retention_notices WHERE artifact_id=%s', (key,))[0]
        identifier = f'<multirun-{row["id"]}@example.org>'
        self.capture.reject(message_id=identifier)
        self.drain(f)
        row = f.sql('SELECT * FROM retention_notices WHERE id=%s', (row['id'],))[0]
        self.assertEqual(row['attempts'], 1)
        self.assertIsNone(row['sent_at'])
        self.assertIsNone(row['claimed_until'])
        deadline = f.record('upload', key)['expires_at']
        self.drain(f)
        self.assertEqual(f.sql('SELECT attempts FROM retention_notices WHERE id=%s', (row['id'],))[0]['attempts'], 1)
        f.now += timedelta(seconds=61)
        f.retention = Retention(f.s, origin=f.retention.origin, cleanup_enabled=True, sender=self.sender)
        f.s.retention = f.retention
        self.drain(f)
        self.assertEqual(f.record('upload', key)['expires_at'], deadline)
        attempts = [event for event in self.capture.events if event['message_id'] == identifier]
        self.assertEqual([e['accepted'] for e in attempts], [False, True])
        self.assertEqual(f.sql('SELECT attempts FROM retention_notices WHERE id=%s', (row['id'],))[0]['attempts'], 2)
        self.assertEqual(len([m for m in self.messages(f.retention.origin) if m['Message-ID'] == identifier]), 1)

    def test_renewal_cancels_failed_old_notice_and_replaces_a_previously_delivered_deadline(self):
        f = self.fixture()
        key = f.upload(); f.tick(3.1)
        row = f.sql('SELECT * FROM retention_notices WHERE artifact_id=%s', (key,))[0]
        old_id = f'<multirun-{row["id"]}@example.org>'
        self.capture.reject(message_id=old_id); self.drain(f)
        f.sql('UPDATE uploads SET last_used_at=%s WHERE id=%s', (f.now, key)); f.tick()
        self.assertIsNotNone(f.sql('SELECT cancelled_at FROM retention_notices WHERE id=%s', (row['id'],))[0]['cancelled_at'])
        self.assertEqual(f.record('upload', key)['expires_at'], f.now+WINDOW)
        self.drain(f)
        self.assertFalse(any(m['Message-ID'] == old_id for m in self.messages(f.retention.origin)))
        f.tick(3); self.drain(f)
        delivered = [m for m in self.messages(f.retention.origin) if key in m.get_content()]
        # An attempted old message may have reached a remote inbox despite a
        # lost acknowledgement. Renewal therefore issues an update immediately,
        # then the new deadline receives its own ordinary four-day warning.
        self.assertEqual(len(delivered), 2)
        self.assertIn('updated deletion notice', delivered[0]['Subject'])
        self.assertIn('scheduled data deletion', delivered[1]['Subject'])
        f.sql('UPDATE uploads SET last_used_at=%s WHERE id=%s', (f.now, key)); f.tick(); self.drain(f)
        delivered = [m for m in self.messages(f.retention.origin) if key in m.get_content()]
        self.assertEqual(len(delivered), 3)
        self.assertIn('updated deletion notice', delivered[-1]['Subject'])
        self.assertIn('replaces all earlier deletion notices', delivered[-1].get_content())
        self.assertEqual(len({m['Message-ID'] for m in delivered}), 3)

    def test_unresolved_failed_job_expires_then_inputs_and_successful_output_receive_full_notice(self):
        f = self.fixture()
        experiment = f.r.http.submit(f.r.dataset)
        first, work = f.r.finish()
        failed, _ = f.r.finish(outcome='model')
        f.tick(); self.drain(f)
        self.assertIsNone(f.record('output', first.attempt_id)['expires_at'])
        notices = [m for m in self.messages(f.retention.origin) if failed.job_id in m.get_content()]
        self.assertEqual(len(notices), 1)
        self.assertIn('a job needs your attention', notices[0]['Subject'])
        f.tick(3.1); self.drain(f)
        f.tick(3); self.drain(f)
        notices = [m for m in self.messages(f.retention.origin) if failed.job_id in m.get_content()]
        self.assertEqual(len(notices), 3)
        self.assertIn('final job recovery reminder', notices[-1]['Subject'])
        f.tick(1); f.retire(); self.drain(f)
        jobs = f.q.inspect(f.f.owner, experiment)['jobs']
        self.assertEqual([j['state'] for j in jobs], ['succeeded', 'expired'])
        self.assertEqual(f.record('output', first.attempt_id)['expires_at'], f.now+NOTICE)
        self.assertEqual(f.record('dataset', f.r.dataset)['expires_at'], f.now+NOTICE)
        self.assertTrue((work/'output-606.csv').exists())
        self.assertTrue(jobs[1]['attempts'])

    def test_preview_then_enabling_cleanup_preserves_full_grace_and_reclaims_upload_quota(self):
        f = self.fixture()
        f.retention.cleanup_enabled = False
        key = f.upload(); f.tick(); f.tick(20); self.drain(f)
        f.retire(enabled=False)
        self.assertTrue((f.s.datasets.root/key).exists())
        f.now += timedelta(hours=1)
        f.retire(enabled=True); self.drain(f)
        self.assertGreaterEqual(f.record('upload', key)['expires_at'], f.now+NOTICE)
        f.tick(3.1); self.drain(f)
        f.tick(1); f.retire()
        self.assertFalse((f.s.datasets.root/key).exists())
        self.assertEqual(f.sql('SELECT reserved_bytes FROM uploads WHERE id=%s', (key,)), [])
        self.assertEqual(f.record('upload', key)['state'], 'deleted')
        notices = [m for m in self.messages(f.retention.origin, forbidden=('unused.csv',)) if key in m.get_content()]
        self.assertTrue(any('final data deletion reminder' in m['Subject'] for m in notices))

    def test_operator_and_owner_incidents_route_separately_and_resolved_incidents_stop_sending(self):
        f = test_storage.StorageServiceTests()
        self.addCleanup(f.doCleanups); f.setUp()
        f.n = Notifications(f.s, admin_email='operator@example.org', sender=self.sender)
        f.s.notifications = f.n
        f.http.submit(f.http.ready())
        with f.q._connection() as connection:
            connection.execute("UPDATE jobs SET storage_wait_since=clock_timestamp()-interval '6 minutes' WHERE state='queued'")
        f.n.reconcile(); self.assertTrue(f.n.deliver_one())
        f.n.reconcile(); self.assertFalse(f.n.deliver_one())
        messages = self.capture.messages()[self.before:]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['To'], 'operator@example.org')
        check_message(messages[0], f.http.origin, forbidden=('alice', str(f.http.fixture.root)))
        with f.q._connection() as connection:
            connection.execute('UPDATE jobs SET storage_wait_since=NULL')
            used = connection.execute('SELECT sum(reserved_bytes) AS bytes FROM uploads WHERE owner_id=%s',
                                       (f.http.fixture.owner,)).fetchone()['bytes']
        f.s.datasets.owner_limit = used
        f.n.reconcile(); self.assertTrue(f.n.deliver_one())
        messages = self.capture.messages()[self.before:]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]['To'], 'alice@example.org')
        check_message(messages[1], f.http.origin, forbidden=(str(f.http.fixture.root),))
        self.assertIn('upload allowance', messages[1]['Subject'])
        f.s.datasets.owner_limit += 1000
        f.n.reconcile(); self.assertFalse(f.n.deliver_one())

    def test_competing_completion_senders_share_one_claim_and_send_one_message(self):
        f = test_completion_notifications.CompletionTests()
        self.addCleanup(f.doCleanups); f.setUp()
        f.submit(); f.finish()
        first = CompletionNotifications(f.s, origin=f.origin, sender=self.sender)
        second = CompletionNotifications(f.s, origin=f.origin, sender=self.sender)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda notifier: notifier.deliver_one(), [first, second]))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(self.messages(f.origin, forbidden=('PRIVATE EXPERIMENT NAME', f.f.auth['token']))), 1)
