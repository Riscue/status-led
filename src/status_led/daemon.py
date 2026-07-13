#!/usr/bin/env python3
"""
LED Daemon — orchestrator
-------------------------
Long-running background process that holds the ESP8266 serial port open,
aggregates state across multiple concurrent client sessions, and forwards
the highest-priority live state to the firmware. Source-agnostic: it knows
only opaque session IDs, priority numbers, and the firmware wire protocol.

Why a daemon:
    The ESP8266 resets on every serial-open (CH340 DTR line). A one-shot CLI
    pays a 0.5 s reset-wait per invocation and dark-flashes on back-to-back
    hook fires. Holding the port open removes both. The CLI client talks to
    this daemon over a Unix socket and is mandatory — there is no automatic
    direct-serial fallback when the daemon is down. `led --direct` bypasses
    the daemon for debug only.

    The daemon also aggregates state across multiple concurrent sessions
    (e.g. several source sessions running in parallel). Each session's
    state is tagged with a priority; the highest-priority live state reaches
    the firmware. This lets one session's `error` override another's
    `thinking`, and lets one session close without darkening the strip
    while others are still active.

Socket:
    ~/.status-led/led.sock (override with STATUS_LED_SOCKET).
    Directory mode 0700, socket file mode 0600.

This module is the orchestrator. Aggregation logic lives in aggregator.py;
serial port lifecycle in serial_session.py; socket I/O in daemon_socket.py.
The Daemon class wires them together and runs the main loop.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from threading import Event

from status_led.aggregator import Aggregator
from status_led.daemon_socket import DaemonSocket
from status_led.protocol import socket_path, socket_dir
from status_led.serial_session import SerialSession


def pid_file_path() -> str:
    return os.path.join(socket_dir(), "daemon.pid")


def setup_logging(level_override: str | None = None) -> logging.Logger:
    """Configure root logging. Resolution: explicit arg (--log-level) wins,
    then $STATUS_LED_LOG_LEVEL, then INFO.
    """
    raw = level_override or os.environ.get("STATUS_LED_LOG_LEVEL", "INFO")
    level_name = raw.upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stderr,
    )
    return logging.getLogger(__name__)


class Daemon:
    """Glues together the Aggregator, SerialSession, and DaemonSocket.

    Owns the main loop:
      - Wait for a client connection (or accept-timeout, which is the
        housekeeping tick for transient TTL expiry).
      - For each payload: short-circuit on STATUS, otherwise feed each line
        to the aggregator and write to serial if the decision says so.
      - When serial is disconnected, attempt reconnect with backoff.
    """

    def __init__(self,
                 port_override: str | None,
                 log: logging.Logger,
                 serial_session: SerialSession | None = None,
                 sock: DaemonSocket | None = None,
                 aggregator: Aggregator | None = None):
        self.log = log
        self.shutdown = Event()
        self.aggregator = aggregator or Aggregator()
        self.serial = serial_session or SerialSession(port_override, log)
        self.sock = sock  # set in setup_socket(); tests can inject directly

    def setup_socket(self, path: str) -> None:
        """Create and bind the DaemonSocket. Called once at startup."""
        self.sock = DaemonSocket(path, self.log)
        self.sock.setup()

    def serve(self) -> None:
        while not self.shutdown.is_set():
            if not self.serial.connected:
                if self.serial.try_open():
                    self._replay_after_reconnect()
                else:
                    self.shutdown.wait(self.serial.reconnect_wait_seconds())
                    continue

            result = self.sock.accept_one() if self.sock else None
            if result is None:
                # Accept-timeout is the housekeeping tick: expire transient
                # if its TTL has elapsed. Single-threaded — no locking needed.
                decision = self.aggregator.expire_transient_if_due(time.monotonic())
                if decision.is_change and decision.output is not None:
                    self.log.debug("transient expired")
                    self._emit(decision.output)
                continue

            client, payload = result
            try:
                self._handle_payload(client, payload)
            finally:
                self.sock.close_client(client)

    def _handle_payload(self, client, payload: str) -> None:
        # STATUS short-circuit: respond with a JSON snapshot and return.
        # Only honored when STATUS is the sole payload; mixed with other
        # commands it falls through to dispatch (which logs and drops it).
        if payload.strip() == "STATUS":
            snapshot = json.dumps(self.aggregator.status_snapshot(
                now=time.monotonic(),
                serial_connected=self.serial.connected,
                serial_port=self.serial.port_name,
            ))
            self.sock.respond(client, snapshot)
            return

        for line in payload.splitlines():
            line = line.strip()
            if not line:
                continue
            self.log.debug("recv: %s", line)
            decision = self.aggregator.apply_line(line, time.monotonic())
            if not decision.parsed:
                self.log.warning("malformed line, ignored: %s", line)
                continue
            if decision.verb:
                self.log.info("%s applied", decision.verb)
            if decision.is_change and decision.output is not None:
                self._emit(decision.output)

    def _emit(self, line: str) -> None:
        """Forward a wire line to the firmware via the serial session."""
        self.serial.write(line)

    def _replay_after_reconnect(self) -> None:
        """After a fresh serial open, replay whatever the aggregator wants
        to show (if anything). The serial session also has its own
        pending_command stash as a fallback.
        """
        out = self.aggregator.current_output
        if out is None:
            return
        # Prefer the serial session's replay_pending (handles pending_command
        # bookkeeping); fall back to direct write if there was no pending.
        if not self.serial.replay_pending():
            self.serial.write(out)

    def signal_shutdown(self, signum, _frame):
        self.log.info("received signal %d, shutting down", signum)
        self.shutdown.set()

    def cleanup(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        self.serial.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="led daemon",
        description="LED daemon (persistent serial service).",
        epilog=(
            "environment variables:\n"
            "  STATUS_LED_SOCKET         override daemon socket path\n"
            "  STATUS_LED_PORT           override ESP8266 serial-port auto-detection\n"
            "  STATUS_LED_LOG_LEVEL      daemon log level (DEBUG logs every command)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--port", default=None,
                        help="Serial port override (e.g. /dev/cu.usbserial-1410)")
    parser.add_argument("--socket", default=None, metavar="PATH",
                        help="Daemon Unix socket path (default ~/.status-led/led.sock or "
                             "$STATUS_LED_SOCKET)")
    parser.add_argument("--log-level", default=None, metavar="LEVEL",
                        help="Log level (default INFO or $STATUS_LED_LOG_LEVEL; "
                             "DEBUG logs every received command)")
    args = parser.parse_args(argv)

    log = setup_logging(args.log_level)

    try:
        import serial  # noqa: F401  (early check — fail fast if missing)
    except ImportError:
        log.error("pyserial is not installed; install with: pip3 install pyserial")
        return 1

    path = socket_path(args.socket)
    daemon = Daemon(args.port, log)

    try:
        daemon.setup_socket(path)
    except Exception as e:
        log.error("socket setup failed: %s", e)
        return 1

    signal.signal(signal.SIGINT, daemon.signal_shutdown)
    signal.signal(signal.SIGTERM, daemon.signal_shutdown)
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    except (AttributeError, ValueError):
        pass

    log.info("starting (pid=%d, socket=%s)", os.getpid(), path)
    try:
        with open(pid_file_path(), "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        log.warning("could not write pid file: %s", e)

    try:
        daemon.serve()
    finally:
        daemon.cleanup()
        try:
            os.unlink(pid_file_path())
        except OSError:
            pass
        log.info("stopped")
    return 0


if __name__ == "__main__":
    main()
