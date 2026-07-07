"""Daemon orchestrator tests.

Aggregation logic is tested in test_aggregator.py. Serial session reconnect
and pending-buffer behavior is tested in test_serial_session.py. Socket I/O
is tested in test_daemon_socket.py.

This file covers Daemon-class glue: that _handle_payload translates aggregator
decisions into serial writes, that STATUS short-circuits to a JSON response,
and that the malformed-line tolerance holds at the orchestrator level.
"""
from __future__ import annotations

import json
import time
import unittest
from typing import Optional

from status_led.aggregator import Aggregator
from status_led.daemon import Daemon
from status_led.serial_session import SerialSession


class _FakeSerialSession(SerialSession):
    """Test double for SerialSession — captures writes instead of touching hardware."""

    def __init__(self):
        # Skip parent __init__ to avoid pyserial dependency. We override every
        # method that touches self._serial / pyserial.
        self.port_override = None
        import logging
        self.log = logging.getLogger("test")
        self._intervals = [0.0]
        self._serial_factory = None
        self._serial = None
        self._port_name = "FAKE"
        self._connected = True
        self.pending_command: Optional[str] = None
        self.connect_failures = 0
        self.writes: list[str] = []

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port_name(self):
        return self._port_name

    def try_open(self) -> bool:
        return True

    def write(self, line: str) -> bool:
        self.writes.append(line)
        return True

    def replay_pending(self) -> bool:
        return False

    def close(self) -> None:
        self._connected = False


class _FakeClient:
    """socket.socket double — captures respond() payloads."""

    def __init__(self):
        self.sent: list[bytes] = []
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


class _FakeSock:
    """DaemonSocket double — records respond() calls. close_client() is a
    no-op; tests already use _FakeClient to capture state.
    """

    def __init__(self):
        self.responses: list[tuple[_FakeClient, str]] = []

    def respond(self, client: _FakeClient, payload: str) -> None:
        self.responses.append((client, payload))
        client.close()

    def close_client(self, client: _FakeClient) -> None:
        client.close()


class _CapturingDaemon(Daemon):
    """Daemon pre-wired with a fake serial session + fake sock."""

    def __init__(self):
        super().__init__(
            port_override=None,
            log=__import__("logging").getLogger("test"),
            serial_session=_FakeSerialSession(),
            sock=_FakeSock(),
            aggregator=Aggregator(),
        )

    @property
    def serial_writes(self) -> list[str]:
        return self.serial.writes


class HandlePayloadTest(unittest.TestCase):
    """_handle_payload is the orchestrator's per-client dispatch."""

    def setUp(self):
        self.d = _CapturingDaemon()

    def test_state_emits_serial_write(self):
        client = _FakeClient()
        self.d._handle_payload(client, "STATE A 60 scanner 90 0 170 1600 100")
        self.assertEqual(self.d.serial_writes, ["scanner 90 0 170 1600 100"])

    def test_malformed_line_does_not_write(self):
        client = _FakeClient()
        self.d._handle_payload(client, "GARBAGE")
        self.assertEqual(self.d.serial_writes, [])

    def test_clear_after_state_emits_off(self):
        client = _FakeClient()
        self.d._handle_payload(client, "STATE A 60 scanner 90 0 170 1600 100")
        self.d._handle_payload(client, "CLEAR A")
        self.assertEqual(self.d.serial_writes[-1], "off")

    def test_lower_priority_no_extra_write(self):
        client = _FakeClient()
        self.d._handle_payload(client, "STATE A 60 scanner 90 0 170 1600 100")
        n_before = len(self.d.serial_writes)
        self.d._handle_payload(client, "STATE B 10 breathe 0 50 220 3500 100")
        self.assertEqual(len(self.d.serial_writes), n_before)

    def test_status_responds_with_json_snapshot(self):
        client = _FakeClient()
        # Seed some state so the snapshot has content.
        self.d._handle_payload(client, "STATE A 60 scanner 90 0 170 1600 100")
        # STATUS should reach sock.respond with a JSON payload.
        self.d.sock.responses.clear()
        self.d._handle_payload(client, "STATUS")
        self.assertEqual(len(self.d.sock.responses), 1)
        responded_client, payload_str = self.d.sock.responses[0]
        self.assertIs(responded_client, client)
        payload = json.loads(payload_str)
        self.assertEqual(payload["current_output"], "scanner 90 0 170 1600 100")
        self.assertTrue(payload["serial_connected"])
        self.assertEqual(payload["serial_port"], "FAKE")
        self.assertEqual(len(payload["sessions"]), 1)
        self.assertEqual(payload["sessions"][0]["sid"], "A")

    def test_multiline_payload(self):
        client = _FakeClient()
        self.d._handle_payload(client, "STATE A 60 scanner 90 0 170 1600 100\n"
                                       "STATE B 100 blink 180 0 0 300 100")
        # Second STATE has higher priority — should emit blink, not re-emit scanner.
        self.assertEqual(self.d.serial_writes,
                         ["scanner 90 0 170 1600 100", "blink 180 0 0 300 100"])

    def test_blank_lines_ignored(self):
        client = _FakeClient()
        self.d._handle_payload(client, "\n\n  \nSTATE A 60 off\n")
        self.assertEqual(self.d.serial_writes, ["off"])


if __name__ == "__main__":
    unittest.main()
