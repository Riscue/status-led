#!/usr/bin/env python3
"""
LED Daemon
----------
Long-running background process that holds the ESP8266 serial port open,
aggregates state across multiple concurrent client sessions, and forwards the
highest-priority live state to the firmware. Stays Claude-agnostic: it knows
only opaque session IDs, priority numbers, and the firmware wire protocol.
The state → priority mapping is resolved by led_cli.py (the
client) before commands reach the daemon.

Why a daemon:
    The ESP8266 resets on every serial-open (CH340 DTR line). A one-shot CLI
    pays a 0.5 s reset-wait per invocation and dark-flashes on back-to-back
    hook fires. Holding the port open removes both. The CLI client talks to
    this daemon over a Unix socket and is mandatory — there is no automatic
    direct-serial fallback when the daemon is down. `led_cli.py --direct`
    bypasses the daemon for debug only.

    The daemon also aggregates state across multiple concurrent sessions
    (e.g. several source sessions running in parallel). Each session's
    state is tagged with a priority; the highest-priority live state is what
    reaches the firmware. This lets one session's `error` override another's
    `thinking`, and lets one session close without darkening the strip while
    others are still active.

Socket:
    ~/.status-led/led.sock (override with STATUS_LED_SOCKET).
    Directory mode 0700, socket file mode 0600.

Wire protocol (over the socket, CLI → daemon):
    STATE <sid> <priority> <wire-line...>     upsert session; recompute aggregate
    CLEAR <sid>                                remove session; recompute
    TRANSIENT <ttl_ms> <wire-line...>          one-shot override, expires after ttl

    <wire-line> is the firmware command (e.g. "blink 180 0 0 300 100") and is
    passed through byte-for-byte as an opaque remainder — the daemon does not
    parse animation names or field counts.

Daemon → firmware wire protocol (unchanged):
    <anim> <r> <g> <b> [<period_ms>] [<bright_pct>]\n

Aggregation:
    The highest-priority live session wins. While a TRANSIENT entry is live
    (within its TTL), it overrides the session aggregate unconditionally. With
    no sessions and no live transient, the daemon emits "off".

    The daemon treats both <sid> and <priority> as opaque — it does not know
    which state name a priority corresponds to. The state → priority mapping
    lives in JSON profiles (integrations/<profile>/states.json) or in the
    CLI's hardcoded BUILTIN_PROFILES (`default`) and is resolved by led_cli.py
    before commands reach the daemon.

Started at login by scripts/install.sh (via launchd on macOS, systemd --user on
Linux), or directly with `python3 driver/led_daemon.py` for foreground debugging.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from threading import Event

try:
    import serial
    from serial import SerialException
except ImportError:
    serial = None
    SerialException = OSError

from protocol import BAUD_RATE, RESET_WAIT_SECONDS, find_esp8266_port

RECONNECT_INTERVALS = [2.0, 5.0, 10.0, 30.0, 60.0]  # backoff, caps at last entry
ACCEPT_TIMEOUT = 1.0
CLIENT_TIMEOUT = 0.5
LISTEN_BACKLOG = 8
RECV_BUFFER = 256


@dataclass
class SessionEntry:
    priority: int
    wire: str           # full firmware line, e.g. "blink 180 0 0 300 100"
    updated_at: float   # time.monotonic(); tie-breaker within a priority tier


@dataclass
class TransientEntry:
    wire: str
    expires_at: float   # time.monotonic() + ttl_seconds


def socket_path() -> str:
    override = os.environ.get("STATUS_LED_SOCKET")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".status-led", "led.sock")


def socket_dir() -> str:
    return os.path.dirname(socket_path())


def pid_file_path() -> str:
    return os.path.join(socket_dir(), "daemon.pid")


def setup_logging() -> logging.Logger:
    level_name = os.environ.get("STATUS_LED_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stderr,
    )
    return logging.getLogger("led-daemon")


def is_socket_live(path: str) -> bool:
    """Return True if a process is currently listening on the given Unix socket."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            s.connect(path)
        return True
    except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
        return False


def open_serial_port(port_override: str | None):
    """Resolve and open the serial port. Raises on failure."""
    if serial is None:
        raise RuntimeError("pyserial is not installed")
    resolved = port_override or os.environ.get("STATUS_LED_PORT") or find_esp8266_port()
    if not resolved:
        raise RuntimeError("no ESP8266 serial port found")
    return serial.Serial(resolved, BAUD_RATE, timeout=1)


class Daemon:
    def __init__(self, port_override: str | None, log: logging.Logger):
        self.port_override = port_override
        self.log = log
        self.serial = None
        self.serial_port_name: str | None = None
        self.disconnected = True
        self.shutdown = Event()
        self.listen_sock: socket.socket | None = None
        # Last-write-wins buffer for commands received while the serial device
        # is unavailable. On reconnect the most recent command is replayed so
        # the LED reflects the latest state instead of staying stale. We do
        # NOT queue every command: replaying a burst of intermediate states
        # would strobe the strip through stale animations.
        self.pending_command: str | None = None
        # Multi-session aggregation state. Sessions are keyed by an opaque sid
        # supplied by the client; priority is also opaque (the state → priority
        # mapping lives in JSON profiles, resolved client-side). The daemon
        # only knows "higher number wins". Transient overrides the aggregate
        # while its TTL is live.
        self.sessions: dict[str, SessionEntry] = {}
        self.transient: TransientEntry | None = None
        self.current_output: str | None = None  # redundant-emit suppression
        # Consecutive serial-open failures. Drives the backoff schedule and
        # dedups the warning log — we log the first failure as WARNING and
        # subsequent ones as DEBUG so the log doesn't fill with the same line.
        self.connect_failures = 0

    def reconnect_wait(self) -> float:
        # After the Nth consecutive failure, wait INTERVALS[N-1] (capped at the
        # last entry). connect_failures is incremented before this is called, so
        # for the 1st failure N=1 → INTERVALS[0] = 2s.
        idx = min(max(self.connect_failures - 1, 0), len(RECONNECT_INTERVALS) - 1)
        return RECONNECT_INTERVALS[idx]

    def open_serial_once(self) -> bool:
        try:
            ser = open_serial_port(self.port_override)
        except Exception as e:
            # Increment first so reconnect_wait() and the "attempt N" log agree
            # on N regardless of whether they read connect_failures before or
            # after the call site in serve().
            self.connect_failures += 1
            if self.connect_failures == 1:
                self.log.warning("serial open failed: %s — backing off", e)
            else:
                self.log.debug("serial still unavailable (attempt %d, next try in %.0fs): %s",
                               self.connect_failures, self.reconnect_wait(), e)
            return False
        prev_failures = self.connect_failures
        self.connect_failures = 0
        self.serial = ser
        self.serial_port_name = getattr(ser, "portstr", None) or ser.port
        self.disconnected = False
        time.sleep(RESET_WAIT_SECONDS)
        self.log.info("serial opened: %s%s", self.serial_port_name,
                      f" after {prev_failures} retries" if prev_failures else "")
        self._replay_pending()
        return True

    def _replay_pending(self) -> None:
        """Send the stashed command (if any) after a fresh serial open.

        Clears pending_command on success. On failure, leaves it intact so
        the next reconnect attempt tries again.
        """
        if self.pending_command is None or self.serial is None:
            return
        cmd = self.pending_command
        try:
            self.serial.write((cmd + "\n").encode("utf-8"))
            self.serial.flush()
            self.pending_command = None
            self.log.info("replayed after reconnect: %s", cmd)
        except (SerialException, OSError) as e:
            self.log.warning("replay failed: %s — marking disconnected", e)
            self.close_serial()
            # pending_command intentionally left set for next reconnect.

    def close_serial(self):
        if self.serial is not None:
            try:
                self.serial.close()
            except Exception:
                pass
            self.serial = None
            self.serial_port_name = None
        self.disconnected = True

    def write_command(self, line: str) -> bool:
        if self.disconnected or self.serial is None:
            self.log.info("serial disconnected; stashing command: %s", line)
            self.pending_command = line
            return False
        try:
            self.serial.write((line + "\n").encode("utf-8"))
            self.serial.flush()
            return True
        except (SerialException, OSError) as e:
            self.log.warning("serial write failed: %s — marking disconnected", e)
            self.close_serial()
            # The failed write would have been the next-to-send; stash it so
            # it survives the reconnect instead of being silently dropped.
            self.pending_command = line
            return False

    def setup_socket(self, path: str):
        d = socket_dir()
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        if os.path.exists(path):
            if is_socket_live(path):
                raise RuntimeError(f"another daemon is already listening on {path}")
            os.unlink(path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        s.listen(LISTEN_BACKLOG)
        s.settimeout(ACCEPT_TIMEOUT)
        self.listen_sock = s
        self.log.info("listening on %s", path)

    def serve(self):
        while not self.shutdown.is_set():
            if self.disconnected:
                if not self.open_serial_once():
                    self.shutdown.wait(self.reconnect_wait())
                    continue

            try:
                client, _ = self.listen_sock.accept()
            except socket.timeout:
                # The accept loop wakes every ACCEPT_TIMEOUT (1 s); fold
                # transient TTL expiry into this tick so we don't need a
                # background thread. Single-threaded — no locking needed.
                if self.transient and self.transient.expires_at <= time.monotonic():
                    self.log.debug("transient expired")
                    self.transient = None
                    self.recompute_and_emit()
                continue
            except OSError:
                break

            try:
                self.handle_client(client)
            finally:
                try:
                    client.close()
                except Exception:
                    pass

    def handle_client(self, client: socket.socket):
        # Loop recv until EOF (the client's send-and-close pattern) or timeout.
        # Timeout is a safety cap for misbehaving clients; on the happy path
        # EOF arrives immediately after the payload with no waiting.
        client.settimeout(CLIENT_TIMEOUT)
        chunks = []
        while True:
            try:
                chunk = client.recv(RECV_BUFFER)
            except socket.timeout:
                break
            except OSError:
                return
            if not chunk:
                break
            chunks.append(chunk)
        if not chunks:
            return
        text = b"".join(chunks).decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            self.log.debug("recv: %s", line)
            self.dispatch_line(line)

    def dispatch_line(self, line: str) -> None:
        """Parse a protocol line (STATE/CLEAR/TRANSIENT) and update aggregation
        state. Malformed lines are logged and dropped — the daemon never crashes
        on bad input.
        """
        try:
            sp = line.find(" ")
            if sp < 0:
                self.log.warning("malformed line, ignored: %s", line)
                return
            verb = line[:sp]
            rest = line[sp + 1:].lstrip()

            if verb == "STATE":
                # STATE <sid> <priority> <wire-line...>
                parts = rest.split(" ", 2)
                if len(parts) != 3:
                    self.log.warning("malformed STATE line, ignored: %s", line)
                    return
                sid, priority_s, wire = parts
                self.sessions[sid] = SessionEntry(
                    int(priority_s), wire, time.monotonic())
                self.log.info("STATE %s pri=%s", sid, priority_s)
                self.recompute_and_emit()
            elif verb == "CLEAR":
                # CLEAR <sid>
                sid = rest.split(" ", 1)[0]
                if not sid:
                    self.log.warning("malformed CLEAR line, ignored: %s", line)
                    return
                if self.sessions.pop(sid, None) is not None:
                    self.log.info("CLEAR %s", sid)
                    self.recompute_and_emit()
            elif verb == "TRANSIENT":
                # TRANSIENT <ttl_ms> <wire-line...>
                parts = rest.split(" ", 1)
                if len(parts) != 2:
                    self.log.warning("malformed TRANSIENT line, ignored: %s", line)
                    return
                ttl_ms, wire = parts
                self.transient = TransientEntry(
                    wire, time.monotonic() + int(ttl_ms) / 1000.0)
                self.log.info("TRANSIENT ttl=%sms", ttl_ms)
                self.recompute_and_emit()
            else:
                self.log.warning("unknown verb, ignored: %s", line)
        except (ValueError, IndexError) as e:
            self.log.warning("dispatch failed for %r: %s", line, e)

    def recompute_and_emit(self) -> None:
        """Pick the highest-priority live state and forward it to the firmware.

        Rules: a live transient overrides the session aggregate; otherwise the
        highest-priority session wins (ties broken by recency — last write wins
        within a tier). With nothing live, emit "off". Suppresses redundant
        emits when the recomputed output matches what's already showing.
        """
        now = time.monotonic()
        if self.transient and self.transient.expires_at > now:
            output = self.transient.wire
        elif self.sessions:
            winner = max(self.sessions.values(),
                         key=lambda e: (e.priority, e.updated_at))
            output = winner.wire
        else:
            output = "off"
        if output != self.current_output:
            self.current_output = output
            self.write_command(output)

    def signal_shutdown(self, signum, _frame):
        self.log.info("received signal %d, shutting down", signum)
        self.shutdown.set()

    def cleanup(self):
        if self.listen_sock is not None:
            try:
                self.listen_sock.close()
            except Exception:
                pass
            self.listen_sock = None
        self.close_serial()
        try:
            path = socket_path()
            if os.path.exists(path):
                os.unlink(path)
        except OSError as e:
            self.log.warning("could not unlink socket: %s", e)


def main():
    parser = argparse.ArgumentParser(description="LED daemon (persistent serial service)")
    parser.add_argument("--port", default=None, help="Serial port override (e.g. /dev/cu.usbserial-1410)")
    args = parser.parse_args()

    log = setup_logging()

    if serial is None:
        log.error("pyserial is not installed; install with: pip3 install pyserial")
        sys.exit(1)

    path = socket_path()
    daemon = Daemon(args.port, log)

    try:
        daemon.setup_socket(path)
    except Exception as e:
        log.error("socket setup failed: %s", e)
        sys.exit(1)

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


if __name__ == "__main__":
    main()
