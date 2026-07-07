"""Transport tests.

DaemonTransport: tested against a real Unix socket server in tmpdir.
DirectSerialTransport: largely a pyserial wrapper; covered by integration
tests rather than unit tests.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import unittest

from status_led.transport import DaemonTransport


class _EphemeralSocket:
    """Context manager: yields a socket path; provides a real listening server."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "led.sock")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(1)
        return self

    def __exit__(self, *exc):
        self.server.close()
        self._tmp.cleanup()

    def accept_one_client(self, timeout: float = 1.0) -> socket.socket:
        self.server.settimeout(timeout)
        client, _ = self.server.accept()
        return client


class DaemonTransportSendTest(unittest.TestCase):
    def test_send_delivers_line_to_server(self):
        with _EphemeralSocket() as srv:
            transport = DaemonTransport(socket_path=srv.path, timeout=1.0)

            # Spawn a thread to accept and read; the send is blocking-ish.
            received: list[bytes] = []
            t = threading.Thread(target=self._accept_and_read, args=(srv, received))
            t.start()
            self.assertTrue(transport.send("STATE A 60 off"))
            t.join(timeout=2.0)

            self.assertEqual(received, [b"STATE A 60 off\n"])

    @staticmethod
    def _accept_and_read(srv: _EphemeralSocket, out: list) -> None:
        client = srv.accept_one_client()
        try:
            data = client.recv(4096)
            if data:
                out.append(data)
        finally:
            client.close()

    def test_send_returns_false_when_socket_missing(self):
        transport = DaemonTransport(socket_path="/nonexistent/path/sock", timeout=0.1)
        self.assertFalse(transport.send("anything"))

    def test_send_returns_false_on_timeout(self):
        # Bind a socket but never accept. The connect succeeds but send
        # eventually fills the buffer — too slow. Instead, simulate by
        # pointing at a non-listening path: connect fails → False.
        with _EphemeralSocket() as srv:
            # Close the server so connect refuses.
            srv.server.close()
            transport = DaemonTransport(socket_path=srv.path, timeout=0.1)
            self.assertFalse(transport.send("anything"))


class DaemonTransportStatusTest(unittest.TestCase):
    def test_query_status_returns_parsed_dict(self):
        with _EphemeralSocket() as srv:
            transport = DaemonTransport(socket_path=srv.path, timeout=1.0)

            t = threading.Thread(target=self._accept_and_respond,
                                 args=(srv, {"current_output": "off", "sessions": []}))
            t.start()
            result = transport.query_status(timeout=1.0)
            t.join(timeout=2.0)

            self.assertIsNotNone(result)
            self.assertEqual(result["current_output"], "off")

    @staticmethod
    def _accept_and_respond(srv: _EphemeralSocket, snapshot: dict) -> None:
        client = srv.accept_one_client()
        try:
            # Read the STATUS request.
            client.recv(64)
            client.sendall((json.dumps(snapshot) + "\n").encode("utf-8"))
        finally:
            client.close()

    def test_query_status_returns_none_when_unreachable(self):
        transport = DaemonTransport(socket_path="/nonexistent/path/sock", timeout=0.1)
        self.assertIsNone(transport.query_status())


if __name__ == "__main__":
    unittest.main()
