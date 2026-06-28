#!/usr/bin/env python3
"""
LED Daemon
----------
Long-running background process that holds the ESP8266 serial port open and
accepts animation commands over a Unix domain socket. Stays generic: it knows
only the wire protocol (animation + RGB + period + brightness), which is the
same as the firmware's. The Claude Code state -> animation mapping is resolved
by led_cli.py (the client) before commands reach the daemon.

Why a daemon:
    The ESP8266 resets on every serial-open (CH340 DTR line). A one-shot CLI
    pays a 0.5 s reset-wait per invocation and dark-flashes on back-to-back
    hook fires. Holding the port open removes both. The CLI client talks to
    this daemon over a Unix socket and is mandatory — there is no automatic
    direct-serial fallback when the daemon is down. `led_cli.py --direct`
    bypasses the daemon for debug only.

Socket:
    ~/.claude-led/led.sock (override with CLAUDE_LED_SOCKET).
    Directory mode 0700, socket file mode 0600.

Wire protocol (over the socket, same as firmware):
    <anim> <r> <g> <b> [<period_ms>] [<bright_pct>]\n
    One command per connection. Multiple newline-separated commands in a single
    payload are also accepted.

Started by scripts/install.sh, or directly for foreground debugging.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import time
from threading import Event

try:
    import serial
    from serial import SerialException
except ImportError:
    serial = None
    SerialException = OSError

from led_cli import BAUD_RATE, RESET_WAIT_SECONDS, find_esp8266_port

RECONNECT_INTERVAL = 2.0
ACCEPT_TIMEOUT = 1.0
CLIENT_TIMEOUT = 0.5
LISTEN_BACKLOG = 8
RECV_BUFFER = 256


def socket_path() -> str:
    override = os.environ.get("CLAUDE_LED_SOCKET")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude-led", "led.sock")


def socket_dir() -> str:
    return os.path.dirname(socket_path())


def pid_file_path() -> str:
    return os.path.join(socket_dir(), "daemon.pid")


def setup_logging() -> logging.Logger:
    level_name = os.environ.get("CLAUDE_LED_LOG_LEVEL", "INFO").upper()
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
    resolved = port_override or os.environ.get("CLAUDE_LED_PORT") or find_esp8266_port()
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

    def open_serial_once(self) -> bool:
        try:
            ser = open_serial_port(self.port_override)
        except Exception as e:
            self.log.warning("serial open failed: %s", e)
            return False
        self.serial = ser
        self.serial_port_name = getattr(ser, "portstr", None) or ser.port
        self.disconnected = False
        time.sleep(RESET_WAIT_SECONDS)
        self.log.info("serial opened: %s", self.serial_port_name)
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
                    self.shutdown.wait(RECONNECT_INTERVAL)
                    continue

            try:
                client, _ = self.listen_sock.accept()
            except socket.timeout:
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
            self.write_command(line)

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
