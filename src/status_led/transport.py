"""Transport abstractions for sending commands to the LED.

Two implementations of one interface:
  - DaemonTransport: forwards commands over the daemon's Unix socket.
    Production path. The daemon holds the serial port open (avoiding the
    ESP8266's 0.5s reset-per-open) and aggregates state across sessions.
  - DirectSerialTransport: opens a fresh serial connection per call.
    Debug-only (`led --direct`); pays the 0.5s reset wait every time.

The Transport Protocol lets future transports (TCP, mock for tests) drop
in without changing call sites. CLI command handlers depend on Transport,
not on a specific impl — the choice is made in cli.main() based on the
--direct flag.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from typing import Protocol

try:
    import serial
except ImportError:
    serial = None

from status_led.protocol import BAUD_RATE, RESET_WAIT_SECONDS, find_esp8266_port, socket_path

DAEMON_SOCKET_TIMEOUT = 0.3


class Transport(Protocol):
    """Sends one wire line to the LED (or the daemon fronting it)."""
    def send(self, line: str) -> bool: ...
    def query_status(self) -> dict | None: ...


class DaemonTransport:
    """Forward commands to the daemon over its Unix socket.

    Returns True on success, False if the daemon is unreachable. The CLI
    drops the command in that case — there is no automatic fallback to
    direct-serial. Use DirectSerialTransport explicitly for debug.
    """

    def __init__(self, socket_path: str, timeout: float = 0.3):
        self._path = socket_path
        self._timeout = timeout

    def send(self, line: str) -> bool:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self._timeout)
                s.connect(self._path)
                s.sendall((line + "\n").encode("utf-8"))
            return True
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError):
            return False

    def query_status(self, timeout: float | None = None) -> dict | None:
        t = timeout if timeout is not None else 2.0
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(t)
                s.connect(self._path)
                s.sendall(b"STATUS\n")
                chunks = []
                while True:
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
            return json.loads(b"".join(chunks).decode("utf-8"))
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout,
                OSError, json.JSONDecodeError):
            return None


class DirectSerialTransport:
    """Open a fresh serial connection per call. Debug-only.

    The ESP8266 resets on every serial-open (CH340 DTR line), so this path
    pays the 0.5s reset-wait every time and dark-flashes on back-to-back
    calls. The daemon exists to avoid both. DirectSerialTransport is the
    `--direct` escape hatch.
    """

    def __init__(self, port: str | None = None):
        self._port = port

    def send(self, line: str) -> bool:
        if serial is None:
            print("pyserial is not installed. Install it with: pip3 install pyserial",
                  file=sys.stderr)
            return False
        resolved = (self._port
                    or os.environ.get("STATUS_LED_PORT")
                    or find_esp8266_port())
        if not resolved:
            print("ESP8266 serial port not found; LED state not updated (skipping silently).",
                  file=sys.stderr)
            return False
        try:
            with serial.Serial(resolved, BAUD_RATE, timeout=1) as ser:
                time.sleep(RESET_WAIT_SECONDS)
                ser.write((line + "\n").encode("utf-8"))
                ser.flush()
            return True
        except (serial.SerialException, OSError) as e:
            print(f"Serial port error ({resolved}): {e}", file=sys.stderr)
            return False

    def query_status(self) -> dict | None:
        # Direct serial doesn't have a status concept — only the daemon does.
        return None


def send_or_warn(transport: Transport, line: str, quiet: bool) -> None:
    """Send one line via the transport. On failure, log to stderr unless quiet.

    The error message assumes DaemonTransport — DirectSerialTransport.send
    already writes its own diagnostic to stderr and only returns False on
    its own port-resolution errors, so this path is effectively daemon-only.
    """
    if not transport.send(line) and not quiet:
        print(f"daemon unreachable at {socket_path()}; command dropped — "
              f"start it with: led service install  "
              f"(or run in foreground: led daemon)",
              file=sys.stderr)


def build_transport(direct: bool, port: str | None = None,
                    socket: str | None = None,
                    timeout: float = DAEMON_SOCKET_TIMEOUT) -> Transport:
    """Pick the transport impl based on the --direct flag.

    Production path: DaemonTransport (talks to the daemon over Unix socket).
    Debug escape hatch (`led --direct` / `led raw --direct`): DirectSerialTransport.
    `socket` overrides the daemon socket path (from --socket); ignored when
    `direct` is True.
    """
    if direct:
        return DirectSerialTransport(port=port)
    return DaemonTransport(socket_path=socket_path(socket), timeout=timeout)
