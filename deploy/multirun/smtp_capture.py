"""(C) Copyright 2026, by Ross Richardson

Loopback-only, non-relaying SMTP capture for disposable acceptance rehearsals.
Requires STARTTLS and authentication; stores accepted mail in a private directory.
@author ross richardson
"""
import base64
import binascii
from email import policy
from email.parser import BytesParser
import hmac
from pathlib import Path
import re
import socketserver
import ssl
import threading
from uuid import uuid4

RECIPIENTS = frozenset({'alice@example.org', 'bob@example.org', 'operator@example.org'})
FROM = 'online@example.org'
MAX_MESSAGE = 64 * 1024


def address(command, prefix):
    match = re.fullmatch(re.escape(prefix)+r'\s*<([^<>\s]+)>(?:\s+SIZE=\d+)?', command, re.I)
    return match.group(1).lower() if match else None


class Capture:
    """An SMTP sink, never an outbound mailer; DATA rejection precedes acceptance.

    This small protocol fixture deliberately does not simulate inbox delivery or
    an acknowledgement lost after acceptance. Message IDs permit checking the
    production outbox's retries without claiming exactly-once email delivery.
    """
    def __init__(self, root, *, login, password):
        self.root = Path(root)
        self.root.mkdir(mode=0o700)
        self.login, self.password = login, password
        self.lock = threading.Lock()
        self.events, self.failures = [], []
        self.remaining, self.reject_id = 0, None
        self.server = self.thread = None

    def reject(self, *, count=1, message_id=None):
        if type(count) is not int or not 1 <= count <= 10:
            raise ValueError('Use a bounded number of fictional SMTP rejections')
        with self.lock:
            self.remaining, self.reject_id = count, message_id

    def accept(self, raw, recipients, *, tls, authenticated):
        if not tls or not authenticated or not recipients or set(recipients) - RECIPIENTS:
            raise ValueError('Capture requires TLS, authentication and fictional recipients')
        if len(raw) > MAX_MESSAGE:
            raise ValueError('Oversized fictional message')
        message = BytesParser(policy=policy.default).parsebytes(raw)
        with self.lock:
            rejected = bool(self.remaining and (self.reject_id is None or message['Message-ID'] == self.reject_id))
            filename = None
            if rejected:
                self.remaining -= 1
            else:
                path = self.root/(uuid4().hex+'.eml')
                filename = path.name
                with path.open('xb') as stream:
                    path.chmod(0o600)
                    stream.write(raw)
            self.events.append(dict(message_id=str(message['Message-ID'] or ''),
                recipients=list(recipients), accepted=not rejected, tls=tls, authenticated=authenticated,
                file=filename))
            return not rejected

    def messages(self):
        with self.lock:
            return [BytesParser(policy=policy.default).parsebytes((self.root/event['file']).read_bytes())
                    for event in self.events if event['accepted']]

    def start(self, certificate, key):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(certificate), str(key))
        self.server = SMTPServer(('127.0.0.1', 0), Session)
        self.server.capture, self.server.context = self, context
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .1}, name='fictional-smtp', daemon=True)
        self.thread.start()
        return self.server.server_address[1]

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError('SMTP capture did not stop')
            self.server = None


class SMTPServer(socketserver.ThreadingTCPServer):
    daemon_threads = False
    block_on_close = True

    def handle_error(self, request, client_address):
        # Never print protocol bytes, authentication or sign-in codes.
        self.capture.failures.append('SMTPFixtureError')


class Session(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)
        self.tls = self.authenticated = self.greeted = False
        self.sender, self.recipients = None, []

    def finish(self):
        try:
            super().finish()
        finally:
            # STARTTLS replaces the request socket; close that SSL socket too.
            self.connection.close()

    def reply(self, text):
        self.wfile.write((text+'\r\n').encode('ascii'))
        self.wfile.flush()

    def upgrade(self):
        self.reply('220 Ready for TLS')
        self.rfile.close(); self.wfile.close()
        self.connection = self.server.context.wrap_socket(self.connection, server_side=True)
        self.rfile = self.connection.makefile('rb')
        self.wfile = self.connection.makefile('wb')
        self.tls, self.authenticated, self.greeted = True, False, False
        self.sender, self.recipients = None, []

    def authenticate(self, argument):
        if not self.tls or not self.greeted:
            self.reply('530 STARTTLS and EHLO required'); return
        values = argument.split()
        if len(values) != 2 or values[0].upper() != 'PLAIN':
            self.reply('504 Use AUTH PLAIN'); return
        try:
            fields = base64.b64decode(values[1], validate=True).split(b'\x00')
            good = (len(fields) == 3 and fields[0] == b''
                and hmac.compare_digest(fields[1], self.server.capture.login.encode())
                and hmac.compare_digest(fields[2], self.server.capture.password.encode()))
        except (ValueError, binascii.Error):
            good = False
        self.authenticated = good
        self.reply('235 Authentication successful' if good else '535 Authentication rejected')

    def data(self):
        if not self.sender or not self.recipients:
            self.reply('503 MAIL and RCPT required'); return
        self.reply('354 End with a single dot')
        data = bytearray()
        while True:
            line = self.rfile.readline(MAX_MESSAGE+1)
            if not line:
                return False
            if line == b'.\r\n':
                break
            if line.startswith(b'..'):
                line = line[1:]
            if len(data)+len(line) > MAX_MESSAGE:
                self.reply('552 Message too large'); return False
            data.extend(line)
        accepted = self.server.capture.accept(bytes(data), self.recipients,
                                               tls=self.tls, authenticated=self.authenticated)
        self.sender, self.recipients = None, []
        self.reply('250 Captured locally' if accepted else '451 Fictional temporary rejection')
        return True

    def handle(self):
        try:
            self.reply('220 localhost fictional SMTP capture')
            while True:
                line = self.rfile.readline(2049)
                if not line:
                    return
                if len(line) > 2048 or not line.endswith(b'\r\n'):
                    self.reply('500 Invalid command'); return
                command = line[:-2].decode('ascii', errors='replace')
                verb, _, argument = command.partition(' ')
                verb = verb.upper()
                if verb in ('EHLO', 'HELO'):
                    self.greeted = True
                    self.reply('250-localhost')
                    self.reply('250-AUTH PLAIN' if self.tls else '250-STARTTLS')
                    self.reply('250 SIZE '+str(MAX_MESSAGE))
                elif verb == 'STARTTLS' and not self.tls:
                    self.upgrade()
                elif verb == 'AUTH':
                    self.authenticate(argument)
                elif verb == 'QUIT':
                    self.reply('221 Goodbye'); return
                elif verb == 'RSET':
                    self.sender, self.recipients = None, []
                    self.reply('250 Reset')
                elif verb == 'NOOP':
                    self.reply('250 OK')
                elif not self.authenticated:
                    self.reply('530 TLS authentication required')
                elif verb == 'MAIL':
                    value = address(command, 'MAIL FROM:')
                    self.sender = value if value == FROM else None
                    self.recipients = []
                    self.reply('250 Sender accepted' if self.sender else '550 Fictional sender only')
                elif verb == 'RCPT':
                    value = address(command, 'RCPT TO:')
                    if self.sender and value in RECIPIENTS:
                        self.recipients.append(value); self.reply('250 Recipient accepted')
                    else:
                        self.reply('550 Fictional recipient only; relay disabled')
                elif verb == 'DATA':
                    if self.data() is False:
                        return
                else:
                    self.reply('502 Not implemented in this fixture')
        except (OSError, ssl.SSLError):
            # Client certificate rejection and interrupted connections are
            # expected negative cases. They can never accept a message.
            return
