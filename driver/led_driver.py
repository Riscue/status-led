#!/usr/bin/env python3
"""
LED Animation Driver (generic)
-----------------------------------------
Sends animation commands to the ESP8266 over USB-serial. The driver itself is
state-agnostic: it knows the wire protocol (animation + RGB + period +
brightness) but nothing about Claude Code or any other upstream application.

State mappings live as JSON profiles in driver/states/. Each profile is a flat
dict of {state_name: {animation, rgb, period, brightness}}. Use --state
<profile>.<key> to load one. Add new profiles (git.json, slack.json, ...) by
dropping new JSON files in driver/states/ -- no Python changes required.

Setup:
    pip3 install pyserial

Usage:
    # Raw mode (direct animation, for testing/custom use):
    python3 led_driver.py --raw breathe --rgb 0,50,220 --period 3500
    python3 led_driver.py --raw solid --rgb 0,0,255 --brightness 30
    python3 led_driver.py --raw off

    # State mode (lookup from a JSON profile):
    python3 led_driver.py --state claude.idle
    python3 led_driver.py --state claude.error --quiet

The port is auto-detected by scanning USB-serial devices; if it cannot be
found, set the CLAUDE_LED_PORT environment variable or pass --port.
"""

import argparse
import glob
import json
import os
import sys
import time

try:
    import serial
except ImportError:
    serial = None

RESET_WAIT_SECONDS = 0.5
BAUD_RATE = 115200
ANIMATIONS = {"solid", "breathe", "blink", "scanner", "fill", "off"}


def find_esp8266_port() -> str | None:
    candidates = []
    for pattern in ("/dev/cu.wchusbserial*", "/dev/cu.usbserial-*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.usbmodem*"):
        candidates.extend(glob.glob(pattern))
    candidates.extend(glob.glob("/dev/ttyUSB*"))
    candidates.extend(glob.glob("/dev/ttyACM*"))
    return candidates[0] if candidates else None


def parse_rgb(s: str) -> tuple[int, int, int]:
    """Accept 'r,g,b' (e.g. '0,50,220') or a single value for grayscale."""
    parts = s.split(",")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        raise ValueError(f"invalid --rgb value {s!r} (expected integers)")
    if len(nums) == 1:
        v = max(0, min(255, nums[0]))
        return (v, v, v)
    if len(nums) == 3:
        return (max(0, min(255, nums[0])),
                max(0, min(255, nums[1])),
                max(0, min(255, nums[2])))
    raise ValueError(f"--rgb expects 1 or 3 values, got {len(nums)}")


def states_dir() -> str:
    """Directory containing JSON state profiles. Override with CLAUDE_LED_STATES_DIR."""
    override = os.environ.get("CLAUDE_LED_STATES_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "states")


def load_profile(profile_name: str) -> dict:
    path = os.path.join(states_dir(), f"{profile_name}.json")
    if not os.path.exists(path):
        raise ValueError(f"profile not found: {profile_name!r} (looked at {path})")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"profile {profile_name!r} is invalid JSON: {e}")
    if not isinstance(data, dict):
        raise ValueError(f"profile {profile_name!r} must be a JSON object")
    return data


def coerce_rgb_from_json(rgb, context: str) -> tuple[int, int, int]:
    if not (isinstance(rgb, list) and len(rgb) == 3):
        raise ValueError(f"{context}: rgb must be a 3-element list")
    try:
        values = [int(v) for v in rgb]
    except (TypeError, ValueError):
        raise ValueError(f"{context}: rgb values must be integers")
    return (max(0, min(255, values[0])),
            max(0, min(255, values[1])),
            max(0, min(255, values[2])))


def validate_period(period, anim: str, context: str) -> int:
    """Period is required and must be a number >= 50 ms for time-based animations."""
    if isinstance(period, bool) or period is None:
        raise ValueError(f"{context}: period required for animation {anim!r} (number >= 50 ms)")
    if not isinstance(period, (int, float)):
        raise ValueError(f"{context}: period must be a number for animation {anim!r}")
    if period < 50:
        raise ValueError(f"{context}: period must be >= 50 ms for animation {anim!r}")
    return int(period)


def build_command_from_entry(entry, context: str) -> str:
    if not isinstance(entry, dict):
        raise ValueError(f"{context}: entry must be a JSON object")
    anim = entry.get("animation")
    if anim not in ANIMATIONS:
        raise ValueError(f"{context}: invalid animation {anim!r} (valid: {sorted(ANIMATIONS)})")
    if anim == "off":
        return "off"
    r, g, b = coerce_rgb_from_json(entry.get("rgb"), context)
    pct = max(0, min(100, int(entry.get("brightness", 100))))
    if anim == "solid":
        return f"solid {r} {g} {b} {pct}"
    period = validate_period(entry.get("period"), anim, context)
    return f"{anim} {r} {g} {b} {period} {pct}"


def resolve_state(state_ref: str) -> str:
    if "." not in state_ref:
        raise ValueError(f"--state expects PROFILE.KEY (e.g. claude.idle), got {state_ref!r}")
    profile_name, key = state_ref.split(".", 1)
    profile = load_profile(profile_name)
    public_keys = {k for k in profile if not k.startswith("_")}
    if key not in public_keys:
        raise ValueError(
            f"state {key!r} not in profile {profile_name!r} (valid: {sorted(public_keys)})"
        )
    return build_command_from_entry(profile[key], context=state_ref)


def build_raw_command(anim: str, rgb: tuple[int, int, int] | None,
                      period: int | None, pct: int) -> str:
    if anim == "off":
        return "off"
    if rgb is None:
        raise ValueError(f"--rgb required for animation {anim!r}")
    r, g, b = rgb
    pct = max(0, min(100, pct))
    if anim == "solid":
        return f"solid {r} {g} {b} {pct}"
    validated_period = validate_period(period, anim, "raw")
    return f"{anim} {r} {g} {b} {validated_period} {pct}"


def send_command(cmd: str, port: str | None, quiet: bool = False) -> bool:
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
            ser.write((cmd + "\n").encode("utf-8"))
            ser.flush()
        return True
    except (serial.SerialException, OSError) as e:
        if not quiet:
            print(f"Serial port error ({resolved_port}): {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="LED animation driver (generic)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--raw", metavar="ANIM",
                      help="Send a raw animation: solid/breathe/blink/scanner/fill/off")
    mode.add_argument("--state", metavar="PROFILE.KEY",
                      help="Look up a state in driver/states/<PROFILE>.json (e.g. claude.idle)")
    parser.add_argument("--rgb", default=None,
                        help="Color as 'r,g,b' (e.g. 0,50,220) or single value for grayscale (--raw only)")
    parser.add_argument("--period", type=int, default=None,
                        help="Animation period in ms (--raw only; required for breathe/blink/scanner/fill)")
    parser.add_argument("--brightness", type=int, default=100,
                        help="Brightness 0-100 (default 100, scaled below firmware MAX_BRIGHTNESS)")
    parser.add_argument("--port", default=None,
                        help="Serial port path (e.g. /dev/cu.usbserial-1410)")
    parser.add_argument("--quiet", action="store_true",
                        help="Stay silent if the LED is missing or fails (do not interrupt Claude Code)")
    args = parser.parse_args()

    try:
        if args.raw:
            if args.raw not in ANIMATIONS:
                raise ValueError(f"unknown animation {args.raw!r} (valid: {sorted(ANIMATIONS)})")
            rgb = parse_rgb(args.rgb) if args.rgb is not None else None
            cmd = build_raw_command(args.raw, rgb, args.period, args.brightness)
        else:
            cmd = resolve_state(args.state)
    except ValueError as e:
        if not args.quiet:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(0)

    send_command(cmd, args.port, quiet=args.quiet)
    sys.exit(0)


if __name__ == "__main__":
    main()
