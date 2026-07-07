"""`led raw` — send an animation directly, bypassing JSON profile lookup.

Useful for testing/custom use where you want a specific animation+color
without setting up a profile entry.

The animation flags (--rgb, --rgb2, --period, --level, --brightness) live
here, not in cli.py's main argparse — keeps the top-level `led --help`
focused on the common state-lookup path.

Semantics mirror the state-lookup path:
  - With --session:    STATE  (priority 100, persistent until CLEAR)
  - Without --session: TRANSIENT (default 3s flash, override with --ttl)
  - With --direct:     bypass daemon, talk to serial directly (debug)
"""
from __future__ import annotations

import argparse
import os
import sys

from status_led.protocol import (
    build_state_line, build_transient_line, resolve_ttl_ms,
)
from status_led.transport import build_transport, send_or_warn
from status_led.wire import ANIMATIONS, build_wire_line


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


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="led raw",
        description="Send a raw animation directly (no JSON profile lookup).",
    )
    parser.add_argument("anim", metavar="ANIM",
                        help=f"One of: {sorted(ANIMATIONS)}")
    parser.add_argument("--rgb", default=None,
                        help="Color as 'r,g,b' (e.g. 0,50,220) or single value for grayscale")
    parser.add_argument("--rgb2", default=None,
                        help="Second color for strobe as 'r,g,b' (required for strobe)")
    parser.add_argument("--period", type=int, default=None,
                        help="Animation period in ms (required for time-based anims)")
    parser.add_argument("--level", type=int, default=None,
                        help="Level percentage 0-100 (required for level animation)")
    parser.add_argument("--brightness", type=int, default=100,
                        help="Brightness 0-100 (default 100, scaled below firmware MAX_BRIGHTNESS)")
    parser.add_argument("--port", default=None,
                        help="Serial port path (e.g. /dev/cu.usbserial-1410)")
    parser.add_argument("--socket", default=None, metavar="PATH",
                        help="Daemon Unix socket path (default ~/.status-led/led.sock or "
                             "$STATUS_LED_SOCKET). Ignored with --direct.")
    parser.add_argument("--direct", action="store_true",
                        help="Bypass the daemon and talk to the serial port directly (debug)")
    parser.add_argument("--quiet", action="store_true",
                        help="Stay silent if the LED is missing or fails (do not interrupt the caller)")
    parser.add_argument("--session", default=None, metavar="SID",
                        help="Track as a session (STATE). Defaults to $STATUS_LED_SESSION_ID.")
    parser.add_argument("--ttl", type=int, default=None, metavar="MS",
                        help="Transient TTL in ms (default 3000 or "
                             "$STATUS_LED_TTL_MS). Only when no --session.")
    args = parser.parse_args(argv)

    try:
        if args.anim not in ANIMATIONS:
            raise ValueError(f"unknown animation {args.anim!r} (valid: {sorted(ANIMATIONS)})")
        rgb = parse_rgb(args.rgb) if args.rgb is not None else None
        rgb2 = parse_rgb(args.rgb2) if args.rgb2 is not None else None
        wire = build_wire_line(args.anim, rgb=rgb, rgb2=rgb2,
                               period=args.period,
                               level=args.level,
                               brightness=args.brightness)
        priority = 100  # raw mode = manual invocation; outranks every session state

        transport = build_transport(args.direct, args.port, args.socket)
        session_id = args.session or os.environ.get("STATUS_LED_SESSION_ID")
        if args.direct:
            send_or_warn(transport, wire, args.quiet)
        elif session_id:
            send_or_warn(transport, build_state_line(session_id, priority, wire), args.quiet)
        else:
            ttl_ms = resolve_ttl_ms(args.ttl)
            send_or_warn(transport, build_transient_line(ttl_ms, wire), args.quiet)
    except ValueError as e:
        if not args.quiet:
            print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0
