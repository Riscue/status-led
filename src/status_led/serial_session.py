"""Serial port lifecycle with reconnect backoff.

Wraps pyserial in a session that:
  - Tracks connection state and port name (for status reporting)
  - Reopens with exponential backoff when the device disappears
  - Stashes the most recent unwritten command as `pending_command` so a
    reconnect replays the latest state instead of staying stale. We do NOT
    queue every command — replaying a burst of intermediate states would
    strobe the strip through stale animations.

The orchestrator (daemon) calls try_open() when disconnected, write() to
send a line, and reconnect_wait_seconds() to back off between attempts.
After a successful reconnect, the orchestrator reads
aggregator.current_output and writes it via session.write() — replay
logic lives in the orchestrator, not here.

Tests inject a `serial_factory` to avoid pyserial at test time.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

try:
    import serial
    from serial import SerialException
except ImportError:
    serial = None
    SerialException = OSError

from status_led.protocol import BAUD_RATE, RESET_WAIT_SECONDS, find_esp8266_port

# Backoff schedule after consecutive open failures. Caps at the last entry.
RECONNECT_INTERVALS: list[float] = [2.0, 5.0, 10.0, 30.0, 60.0]


def _default_serial_factory(port: str):
    if serial is None:
        raise RuntimeError("pyserial is not installed")
    return serial.Serial(port, BAUD_RATE, timeout=1)


class SerialSession:
    """Owns the serial port. Pure pipe — does not know about aggregation."""

    def __init__(self,
                 port_override: str | None,
                 log: logging.Logger,
                 intervals: list[float] | None = None,
                 serial_factory: Callable[[str], object] | None = None):
        self.port_override = port_override
        self.log = log
        self._intervals = intervals if intervals is not None else RECONNECT_INTERVALS
        self._serial_factory = serial_factory or _default_serial_factory
        self._serial = None
        self._port_name: str | None = None
        self._connected = False
        # Last-write-wins buffer. Stashed when write() is called while
        # disconnected; replayed by the orchestrator after try_open() succeeds.
        self.pending_command: str | None = None
        self.connect_failures = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port_name(self) -> str | None:
        return self._port_name

    def reconnect_wait_seconds(self) -> float:
        # After Nth consecutive failure, wait INTERVALS[N-1] (capped at last).
        # connect_failures is incremented before this is called, so for the
        # 1st failure N=1 → INTERVALS[0] = 2s.
        idx = min(max(self.connect_failures - 1, 0), len(self._intervals) - 1)
        return self._intervals[idx]

    def try_open(self) -> bool:
        """One attempt to open the serial port. Returns True on success.

        On success: sleeps RESET_WAIT_SECONDS for ESP8266 boot, replays any
        pending_command. On failure: increments connect_failures, returns False.
        """
        try:
            resolved = (self.port_override
                        or __import__("os").environ.get("STATUS_LED_PORT")
                        or find_esp8266_port())
            if not resolved:
                raise RuntimeError("no ESP8266 serial port found")
            ser = self._serial_factory(resolved)
        except Exception as e:
            self.connect_failures += 1
            if self.connect_failures == 1:
                self.log.warning("serial open failed: %s — backing off", e)
            else:
                self.log.debug("serial still unavailable (attempt %d, next try in %.0fs): %s",
                               self.connect_failures, self.reconnect_wait_seconds(), e)
            return False
        prev_failures = self.connect_failures
        self.connect_failures = 0
        self._serial = ser
        self._port_name = getattr(ser, "portstr", None) or getattr(ser, "port", None)
        self._connected = True
        time.sleep(RESET_WAIT_SECONDS)
        self.log.info("serial opened: %s%s", self._port_name,
                      f" after {prev_failures} retries" if prev_failures else "")
        return True

    def write(self, line: str) -> bool:
        """Send one line. Returns True on success.

        On failure (disconnected or write error): stashes the line as
        pending_command and returns False. The orchestrator can read
        pending_command after a successful try_open() to replay.
        """
        if not self._connected or self._serial is None:
            self.log.info("serial disconnected; stashing command: %s", line)
            self.pending_command = line
            return False
        try:
            self._serial.write((line + "\n").encode("utf-8"))
            self._serial.flush()
            return True
        except (SerialException, OSError) as e:
            self.log.warning("serial write failed: %s — marking disconnected", e)
            self.close()
            # The failed write survives as pending so the next reconnect retries it.
            self.pending_command = line
            return False

    def replay_pending(self) -> bool:
        """Send the stashed command (if any) after a fresh open.

        Clears pending_command on success. On failure, leaves it intact so
        the next reconnect attempt tries again.
        """
        if self.pending_command is None or self._serial is None:
            return False
        cmd = self.pending_command
        try:
            self._serial.write((cmd + "\n").encode("utf-8"))
            self._serial.flush()
            self.pending_command = None
            self.log.info("replayed after reconnect: %s", cmd)
            return True
        except (SerialException, OSError) as e:
            self.log.warning("replay failed: %s — marking disconnected", e)
            self.close()
            # pending_command intentionally left set.
            return False

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
            self._port_name = None
        self._connected = False
