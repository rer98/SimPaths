"""(C) Copyright 2026, by Ross Richardson

Private SMTP-backed fixtures and fresh-process notification/cleanup pumps.
Only disposable queue schemas, loopback mail and fictional recipients are allowed.
@author ross richardson
"""
from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from unittest.mock import patch

from .smtp_capture import FROM, RECIPIENTS


def check_message(message, origin, *, forbidden=()):
    """Assert the wire message is private controlled text, with safe links."""
    if (message['From'] != FROM or message['To'] not in RECIPIENTS
            or len(message.get_all('To', [])) != 1 or message.get('Cc') or message.get('Bcc')
            or message.is_multipart() or message.get_content_type() != 'text/plain'
            or list(message.iter_attachments()) or message.defects):
        raise AssertionError('Unexpected mail addressing, MIME or attachment')
    body = message.get_content()
    # Inspect decoded MIME and headers too: transport encoding/line wrapping
    # must not conceal a private value from this validator.
    text = body+'\n'+'\n'.join(str(value) for _, value in message.items())+'\n'+message.as_string()
    if any(value and value in text for value in forbidden):
        raise AssertionError('Private fixture canary appeared in an email')
    for link in re.findall(r'https?://[^\s<>]+', body):
        if not re.fullmatch(re.escape(origin)+r'/(?:results/[a-f0-9-]{36})?', link):
            raise AssertionError('Email link is not a controlled authenticated route')
    if message['Subject'] == 'Your SimPaths Online sign-in code':
        if not re.search(r'Your sign-in code is [0-9]{6}\. It expires in 10 minutes\.', body):
            raise AssertionError('Sign-in message missing its code or lifetime')
    elif not re.fullmatch(r'<multirun-[a-f0-9-]{36}@example\.org>', message.get('Message-ID', '')):
        raise AssertionError('Outbox message does not have its stable identifier')
    return body


def validate(settings):
    """Reject remote SMTP, production schemas and unbounded fixture commands."""
    from .proxy_rehearsal import require_test_database
    require_test_database(os.environ['JASMINE_BATCH_TEST_DSN'])
    smtp = settings['smtp']
    expected = {'SMTP_HOST', 'SMTP_PORT', 'SMTP_USE_TLS', 'SMTP_LOGIN_EMAIL',
                'SMTP_PASSWORD', 'SMTP_FROM_EMAIL', 'SSL_CERT_FILE'}
    if (set(smtp) != expected or smtp['SMTP_HOST'] != '127.0.0.1'
            or smtp['SMTP_FROM_EMAIL'] != FROM or smtp['SMTP_USE_TLS'] != 'true'
            or not smtp['SMTP_PORT'].isdigit() or not 1024 <= int(smtp['SMTP_PORT']) <= 65535
            or smtp['SMTP_LOGIN_EMAIL'] != FROM or len(smtp['SMTP_PASSWORD']) < 24
            or not re.fullmatch(r'test_mail_[a-f0-9]{32}', settings['schema'])
            or type(settings['app_port']) is not int or not 1024 <= settings['app_port'] <= 65535):
        raise ValueError('Only authenticated loopback SMTP and a disposable mail schema are permitted')
    origin = urlsplit(settings['origin'])
    if (origin.scheme != 'http' or origin.hostname != '127.0.0.1'
            or origin.netloc != f'127.0.0.1:{settings["app_port"]}'
            or origin.path or origin.query or origin.fragment):
        raise ValueError('Only the private loopback application is permitted')
    state_path = Path(settings['state'])
    state = state_path.resolve(strict=True)
    certificate = Path(smtp['SSL_CERT_FILE']).resolve(strict=True)
    if certificate != state.parent/'cert.pem' or state_path.is_symlink():
        raise ValueError('Use the rehearsal certificate alongside its private fixture')
    now = datetime.fromisoformat(settings['clock'])
    if now.tzinfo is None:
        raise ValueError('Use an explicit UTC fixture clock')
    return now


def configure(settings):
    validate(settings)
    # Per-process trust only: never change the machine's certificate store or
    # disable certificate/hostname validation in the production SMTP sender.
    os.environ.update(settings['smtp'])


def services(settings):
    configure(settings)
    from .proxy_fixture import services as base_services
    from .vm_web import verification_mail, check_smtp
    from jasmine_web.batch.completion_notifications import CompletionNotifications
    from jasmine_web.batch.notifications import Notifications, smtp_sender
    from jasmine_web.batch.retention import Retention
    check_smtp(os.environ)
    service, queue, executor = base_services(settings, os.environ['JASMINE_BATCH_TEST_DSN'])
    service.access.verification.send_mail = verification_mail
    sender = smtp_sender(FROM)
    service.retention = Retention(service, origin=settings['origin'],
                                  cleanup_enabled=settings.get('cleanup_enabled', True), sender=sender)
    service.notifications = Notifications(service, admin_email='operator@example.org', sender=sender)
    service.completions = CompletionNotifications(service, origin=settings['origin'], sender=sender)
    return service, queue, executor


def close(service):
    service.results.downloads.close()
    service.visualiser.close()


@contextmanager
def clock(queue, now):
    original = queue._transaction
    @contextmanager
    def transaction():
        with original() as (connection, pool, _):
            yield connection, pool, now
    with patch.object(queue, '_transaction', transaction):
        yield


def pump(settings, channel, *, retire=False):
    """Run the normal services with a fixture clock in a new OS process.

    This command has no HTTP route. It never runs a model or modifies the host
    clock. Production notification/cleanup methods and PostgreSQL outboxes do
    the work; repeated calls test recovery after the previous process exited.
    """
    if channel not in ('completion', 'retention', 'problem'):
        raise ValueError('Unknown fictional notification channel')
    service, queue, executor = services(settings)
    try:
        now = validate(settings)
        notifier = {'completion': service.completions, 'retention': service.retention,
                    'problem': service.notifications}[channel]
        with clock(queue, now):
            notifier.reconcile()
            if retire:
                with executor.exclusive():
                    service.retention.retire()
                    service.outputs.retire()
                    # Fictional preparations live directly under the state
                    # directory; real deployment preparations use artifacts/.
                    service.lifecycle.retire(Path(settings['state']))
                    service.retention.reconcile()
            delivered = 0
            for _ in range(50):
                if not notifier.deliver_one():
                    break
                delivered += 1
            else:
                raise AssertionError('Fictional notification pump exceeded its bound')
        return dict(delivered=delivered)
    finally:
        close(service)
