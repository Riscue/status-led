"""DaemonSocket tests.

Uses real Unix sockets in a tmpdir — DaemonSocket is a thin wrapper around
socket I/O, so we test the actual behavior.
"""
from __future__ import annotations

import logging
import os
import socket
import tempfile
import unittest

from status_led.daemon_socket import DaemonSocket, is_socket_live


class _SocketPath:
    """Context manager that yields a fresh socket path inside a tmpdir."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "test.sock")
        return self.path

    def __exit__(self, *exc):
        self._tmp.cleanup()


class IsSocketLiveTest(unittest.TestCase):
    def test_returns_false_for_missing_path(self):
        self.assertFalse(is_socket_live("/nonexistent/path/sock"))

    def test_returns_true_when_listening(self):
        with _SocketPath() as path:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(path)
            srv.listen(1)
            try:
                self.assertTrue(is_socket_live(path))
            finally:
                srv.close()


class SetupTest(unittest.TestCase):
    def test_binds_and_chmods(self):
        with _SocketPath() as path:
            sock = DaemonSocket(path, logging.getLogger("test"))
            sock.setup()
            try:
                self.assertTrue(os.path.exists(path))
                mode = os.stat(path).st_mode & 0o777
                self.assertEqual(mode, 0o600)
            finally:
                sock.close()

    def test_rejects_when_another_daemon_listening(self):
        with _SocketPath() as path:
            other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            other.bind(path)
            other.listen(1)
            try:
                sock = DaemonSocket(path, logging.getLogger("test"))
                with self.assertRaises(RuntimeError):
                    sock.setup()
            finally:
                other.close()

    def test_unlinks_stale_socket_then_binds(self):
        with _SocketPath() as path:
            # Leave a stale file (not a listening socket).
            with open(path, "w") as f:
                f.write("stale")
            sock = DaemonSocket(path, logging.getLogger("test"))
            sock.setup()  # should not raise
            sock.close()


class AcceptOneTest(unittest.TestCase):
    def test_returns_none_on_timeout(self):
        with _SocketPath() as path:
            sock = DaemonSocket(path, logging.getLogger("test"), accept_timeout=0.05)
            sock.setup()
            try:
                self.assertIsNone(sock.accept_one())
            finally:
                sock.close()

    def test_returns_payload_when_client_connects(self):
        with _SocketPath() as path:
            srv = DaemonSocket(path, logging.getLogger("test"), accept_timeout=1.0,
                               client_timeout=0.5)
            srv.setup()
            try:
                # Connect from a client thread and send a payload.
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(path)
                client.sendall(b"STATE A 60 off")
                client.close()

                result = srv.accept_one()
                self.assertIsNotNone(result)
                got_client, payload = result
                self.assertIn("STATE A 60 off", payload)
                srv.close_client(got_client)
            finally:
                srv.close()


class RespondTest(unittest.TestCase):
    def test_sends_payload_and_closes_client(self):
        with _SocketPath() as path:
            srv = DaemonSocket(path, logging.getLogger("test"), accept_timeout=1.0,
                               client_timeout=0.5)
            srv.setup()
            try:
                client_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client_sock.connect(path)
                accepted_client, _ = srv.accept_one()
                srv.respond(accepted_client, '{"hello": "world"}')

                # Client should receive the response.
                data = client_sock.recv(4096)
                self.assertIn(b'"hello"', data)
                client_sock.close()
            finally:
                srv.close()


if __name__ == "__main__":
    unittest.main()
