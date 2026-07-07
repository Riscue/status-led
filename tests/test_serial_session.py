"""SerialSession tests.

Tests reconnect math (pure) and the write/try_open/replay state machine
via a fake pyserial injected through serial_factory. No real serial port
required.
"""
from __future__ import annotations

import logging
import unittest

from status_led.serial_session import SerialSession, RECONNECT_INTERVALS


class _FakeSerial:
    """pyserial.Serial double. Raises on write when fail_write is set."""

    def __init__(self, *, fail_write=False):
        self.fail_write = fail_write
        self.port = "FAKE"
        self.portstr = "FAKE"
        self._written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.fail_write:
            import serial as _s
            raise _s.SerialException("simulated write failure")
        self._written.append(data)

    def flush(self) -> None: ...

    def close(self) -> None:
        self.closed = True


def _factory_that_yields(fakes: list):
    """Build a serial_factory that hands out the fakes in sequence."""
    iterator = iter(fakes)

    def factory(port: str):
        try:
            return next(iterator)
        except StopIteration:
            raise RuntimeError("factory exhausted")

    return factory


class ReconnectWaitTest(unittest.TestCase):
    """Backoff schedule. Pure math, no I/O."""

    def test_first_failure_uses_first_interval(self):
        s = SerialSession(None, logging.getLogger("test"))
        s.connect_failures = 1
        self.assertEqual(s.reconnect_wait_seconds(), RECONNECT_INTERVALS[0])

    def test_failure_count_caps_at_last_entry(self):
        s = SerialSession(None, logging.getLogger("test"))
        s.connect_failures = 999
        self.assertEqual(s.reconnect_wait_seconds(), RECONNECT_INTERVALS[-1])

    def test_monotonic_increase(self):
        s = SerialSession(None, logging.getLogger("test"))
        prev = -1.0
        for n in range(1, len(RECONNECT_INTERVALS) + 1):
            s.connect_failures = n
            w = s.reconnect_wait_seconds()
            self.assertGreaterEqual(w, prev)
            prev = w


class TryOpenTest(unittest.TestCase):
    """try_open: success and failure paths."""

    def test_success_marks_connected_and_records_port(self):
        fake = _FakeSerial()
        s = SerialSession(
            port_override="/dev/FAKEPORT",
            log=logging.getLogger("test"),
            serial_factory=lambda port: fake,
        )
        # Patch RESET_WAIT_SECONDS to keep test fast: just stub time.sleep on the
        # module. We use a tiny intervals arg for fully deterministic timing.
        import status_led.serial_session as mod
        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _: None
        try:
            self.assertTrue(s.try_open())
        finally:
            mod.time.sleep = original_sleep
        self.assertTrue(s.connected)
        self.assertEqual(s.port_name, "FAKE")

    def test_failure_returns_false_and_increments(self):
        def failing_factory(port):
            raise RuntimeError("device disappeared")

        s = SerialSession(
            port_override="/dev/FAKEPORT",
            log=logging.getLogger("test"),
            serial_factory=failing_factory,
        )
        self.assertFalse(s.try_open())
        self.assertEqual(s.connect_failures, 1)
        self.assertFalse(s.connected)


class WriteTest(unittest.TestCase):
    """write(): happy path, write-failure stashes pending, disconnected stashes."""

    def setUp(self):
        import status_led.serial_session as mod
        self._orig_sleep = mod.time.sleep
        mod.time.sleep = lambda _: None

    def tearDown(self):
        import status_led.serial_session as mod
        mod.time.sleep = self._orig_sleep

    def test_happy_path(self):
        fake = _FakeSerial()
        s = SerialSession(
            port_override="/dev/FAKEPORT",
            log=logging.getLogger("test"),
            serial_factory=lambda p: fake,
        )
        s.try_open()
        self.assertTrue(s.write("blink 180 0 0 300 100"))
        self.assertEqual(fake._written, [b"blink 180 0 0 300 100\n"])

    def test_write_failure_stashes_pending(self):
        # Need pyserial for SerialException; skip if not available.
        try:
            import serial  # noqa: F401
        except ImportError:
            self.skipTest("pyserial not installed")

        fake = _FakeSerial(fail_write=True)
        s = SerialSession(
            port_override="/dev/FAKEPORT",
            log=logging.getLogger("test"),
            serial_factory=lambda p: fake,
        )
        s.try_open()
        self.assertFalse(s.write("blink 180 0 0 300 100"))
        self.assertEqual(s.pending_command, "blink 180 0 0 300 100")
        self.assertFalse(s.connected)

    def test_disconnected_stashes_pending(self):
        s = SerialSession(
            port_override="/dev/FAKEPORT",
            log=logging.getLogger("test"),
            serial_factory=lambda p: _FakeSerial(),
        )
        # Don't try_open — session stays disconnected.
        self.assertFalse(s.write("off"))
        self.assertEqual(s.pending_command, "off")


class ReplayPendingTest(unittest.TestCase):
    """replay_pending: sends stashed command, clears on success, leaves on failure."""

    def setUp(self):
        import status_led.serial_session as mod
        self._orig_sleep = mod.time.sleep
        mod.time.sleep = lambda _: None

    def tearDown(self):
        import status_led.serial_session as mod
        mod.time.sleep = self._orig_sleep

    def test_replays_then_clears(self):
        fake = _FakeSerial()
        s = SerialSession(
            port_override="/dev/FAKEPORT",
            log=logging.getLogger("test"),
            serial_factory=lambda p: fake,
        )
        s.pending_command = "blink 180 0 0 300 100"
        # Manually mark connected (try_open would sleep; we tested that path elsewhere).
        s._serial = fake
        s._connected = True
        self.assertTrue(s.replay_pending())
        self.assertEqual(fake._written, [b"blink 180 0 0 300 100\n"])
        self.assertIsNone(s.pending_command)

    def test_noop_when_no_pending(self):
        fake = _FakeSerial()
        s = SerialSession(
            port_override="/dev/FAKEPORT",
            log=logging.getLogger("test"),
            serial_factory=lambda p: fake,
        )
        s._serial = fake
        s._connected = True
        self.assertFalse(s.replay_pending())
        self.assertEqual(fake._written, [])


if __name__ == "__main__":
    unittest.main()
