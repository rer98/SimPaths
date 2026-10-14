"""(C) Copyright 2026, by Ross Richardson

Reject inconclusive evidence from the installed-host Linux identity probe.
No accounts, live services or host permissions are changed.
@author ross richardson
"""
import json
import socket
import struct
import unittest
from unittest.mock import MagicMock,patch

from .privilege_probe import rejected_peer


class PeerRejectionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.connection=MagicMock()
        self.connection.__enter__.return_value=self.connection
        self.connection.getsockopt.return_value=struct.pack('3i',123,1002,1002)
        self.connection.recv.return_value=b''
        self.socket=patch('deploy.multirun.privilege_probe.socket.socket',return_value=self.connection)
        self.socket.start()
        self.addCleanup(self.socket.stop)

    def test_connected_executor_closure_proves_rejection(self):
        self.assertTrue(rejected_peer('/run/example/control.sock',1002))
        self.connection.connect.assert_called_once_with('/run/example/control.sock')
        self.connection.getsockopt.assert_called_once_with(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
        message=self.connection.sendall.call_args.args[0]
        self.assertEqual(struct.unpack('!I',message[:4])[0],len(message)-4)
        self.assertEqual(json.loads(message[4:]),dict(operation='health',values={}))

    def test_any_response_including_busy_or_failure_is_not_uid_rejection(self):
        for status in (200,400,403,429,503):
            response=json.dumps(dict(kind='error',status=status,message='fixture')).encode()
            self.connection.recv.return_value=(struct.pack('!I',len(response))+response)[:1]
            with self.subTest(status=status),self.assertRaises(ValueError):
                rejected_peer('/run/example/control.sock',1002)

    def test_filesystem_denial_or_missing_listener_does_not_prove_peer_rejection(self):
        for error in (PermissionError,FileNotFoundError,ConnectionRefusedError):
            self.connection.connect.side_effect=error
            with self.subTest(error=error),self.assertRaises(error):
                rejected_peer('/run/example/control.sock',1002)
        self.connection.sendall.assert_not_called()

    def test_timeout_or_failed_kernel_identity_is_inconclusive(self):
        self.connection.recv.side_effect=TimeoutError
        with self.assertRaises(TimeoutError):rejected_peer('/run/example/control.sock',1002)
        self.connection.recv.side_effect=None
        self.connection.getsockopt.side_effect=OSError
        with self.assertRaises(OSError):rejected_peer('/run/example/control.sock',1002)

    def test_different_executor_uid_is_not_accepted(self):
        with self.assertRaises(ValueError):rejected_peer('/run/example/control.sock',1003)
        self.connection.sendall.assert_not_called()

    def test_immediate_close_or_reset_is_accepted_only_without_reply_bytes(self):
        for error in (BrokenPipeError,ConnectionResetError):
            self.connection.sendall.side_effect=error
            self.connection.recv.side_effect=None
            self.connection.recv.return_value=b''
            with self.subTest(error=error):
                self.assertTrue(rejected_peer('/run/example/control.sock',1002))
                self.connection.recv.return_value=b'\0'
                with self.assertRaises(ValueError):rejected_peer('/run/example/control.sock',1002)
        self.connection.sendall.side_effect=None
        self.connection.recv.side_effect=ConnectionResetError
        self.assertTrue(rejected_peer('/run/example/control.sock',1002))


if __name__=='__main__':
    unittest.main()
