"""
Shared protocol constants and helpers used by both led_cli.py and led_daemon.py.

Centralizing these inverts an older dependency where the daemon imported from
the CLI (which made the daemon depend on a module that's full of CLI-specific
arg parsing). Both modules now import from here instead.
"""

from __future__ import annotations

import glob
import os

BAUD_RATE = 115200
RESET_WAIT_SECONDS = 0.5  # ESP8266 needs this after serial-open (CH340 DTR reset)


def socket_path() -> str:
    """Path to the daemon's Unix socket. Override with STATUS_LED_SOCKET."""
    override = os.environ.get("STATUS_LED_SOCKET")
    if override:
        return override
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
