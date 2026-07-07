"""
Shared protocol constants and helpers for the CLI ↔ daemon wire format.

Three concerns live here:
  - Hardware constants (BAUD_RATE, RESET_WAIT_SECONDS, find_esp8266_port)
  - Socket path resolution (socket_path, socket_dir)
  - CLI → daemon protocol line builders (build_state_line, build_clear_line,
    build_transient_line)

The daemon parses these lines in aggregator.apply_line; the CLI constructs
them here. Both sides import from this module so the protocol has one
source of truth.
"""

from __future__ import annotations

import glob
import os

BAUD_RATE = 115200
RESET_WAIT_SECONDS = 0.5  # ESP8266 needs this after serial-open (CH340 DTR reset)
DEFAULT_TRANSIENT_TTL_MS = 3000  # Default TTL for TRANSIENT flashes (override with --ttl)


def socket_path(override: str | None = None) -> str:
    """Path to the daemon's Unix socket.

    Resolution order: explicit `override` arg (from --socket), then
    $STATUS_LED_SOCKET, then the default ~/.status-led/led.sock.
    """
    if override:
        return override
    env = os.environ.get("STATUS_LED_SOCKET")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".status-led", "led.sock")


def socket_dir() -> str:
    return os.path.dirname(socket_path())


def find_esp8266_port() -> str | None:
    """Auto-detect the ESP8266 USB-serial device file. Returns None if not found.

    Scans common CH340/CP2104/FTDI vendor patterns. Override with STATUS_LED_PORT.
    """
    candidates: list[str] = []
    for pattern in ("/dev/cu.wchusbserial*",
                    "/dev/cu.usbserial-*",
                    "/dev/cu.SLAB_USBtoUART*",
                    "/dev/cu.usbmodem*"):
        candidates.extend(glob.glob(pattern))
    candidates.extend(glob.glob("/dev/ttyUSB*"))
    candidates.extend(glob.glob("/dev/ttyACM*"))
    return candidates[0] if candidates else None


def build_state_line(sid: str, priority: int, wire: str) -> str:
    """STATE <sid> <priority> <wire-line...> — set/update a session (aggregated)."""
    return f"STATE {sid} {priority} {wire}"


def build_clear_line(sid: str) -> str:
    """CLEAR <sid> — remove a session from the aggregation map."""
    return f"CLEAR {sid}"


def build_transient_line(ttl_ms: int, wire: str) -> str:
    """TRANSIENT <ttl_ms> <wire-line...> — one-shot override with TTL."""
    return f"TRANSIENT {ttl_ms} {wire}"


def resolve_ttl_ms(explicit: int | None) -> int:
    """TTL resolution: explicit --ttl wins, else $STATUS_LED_TTL_MS, else default."""
    if explicit is not None:
        return explicit
    return int(os.environ.get("STATUS_LED_TTL_MS", DEFAULT_TRANSIENT_TTL_MS))

