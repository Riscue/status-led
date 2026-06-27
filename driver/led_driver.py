#!/usr/bin/env python3
"""
Claude Code LED Status Driver (Mac side)
-----------------------------------------
Invoked by Claude Code hooks and sends a single-line state command to the
ESP8266 over USB-serial (idle / thinking / tool / waiting / success / error).

Setup:
    pip3 install pyserial

Usage (as called by Claude Code hooks):
    python3 led_driver.py thinking
    python3 led_driver.py tool
    python3 led_driver.py success
    python3 led_driver.py error
    python3 led_driver.py idle

The port is auto-detected by scanning USB-serial devices; if it cannot be
found, set the CLAUDE_LED_PORT environment variable or pass --port.
"""

import argparse
import glob
import os
import sys
import time

try:
    import serial
except ImportError:
    serial = None

VALID_STATES = {"idle", "thinking", "tool", "waiting", "success", "error", "off"}

RESET_WAIT_SECONDS = 0.5
BAUD_RATE = 115200


def find_esp8266_port() -> str | None:
    candidates = []
    for pattern in ("/dev/cu.wchusbserial*", "/dev/cu.usbserial-*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.usbmodem*"):
        candidates.extend(glob.glob(pattern))
    candidates.extend(glob.glob("/dev/ttyUSB*"))
    candidates.extend(glob.glob("/dev/ttyACM*"))
    return candidates[0] if candidates else None


def send_state(state: str, port: str | None, quiet: bool = False) -> bool:
    if state not in VALID_STATES:
        print(f"Invalid state: {state}. Valid values: {sorted(VALID_STATES)}", file=sys.stderr)
        return False

    if serial is None:
        if not quiet:
            print("pyserial is not installed. Install it with: pip3 install pyserial", file=sys.stderr)
        return False

    resolved_port = port or os.environ.get("CLAUDE_LED_PORT") or find_esp8266_port()
    if not resolved_port:
        if not quiet:
            print("ESP8266 serial port not found; LED state not updated (skipping silently).",
                  file=sys.stderr)
        return False

    try:
        with serial.Serial(resolved_port, BAUD_RATE, timeout=1) as ser:
            time.sleep(RESET_WAIT_SECONDS)  # ESP8266 ready-wait after reset
            ser.write((state + "\n").encode("utf-8"))
            ser.flush()
        return True
    except (serial.SerialException, OSError) as e:
        if not quiet:
            print(f"Serial port error ({resolved_port}): {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Claude Code LED status driver")
    parser.add_argument("state", choices=sorted(VALID_STATES), help="State to display")
    parser.add_argument("--port", default=None, help="Serial port path (e.g. /dev/cu.usbserial-1410)")
    parser.add_argument("--quiet", action="store_true",
                        help="Stay silent if the LED is missing or fails (do not interrupt Claude Code)")
    args = parser.parse_args()

    ok = send_state(args.state, args.port, quiet=args.quiet)
    sys.exit(0)


if __name__ == "__main__":
    main()
