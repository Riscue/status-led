"""`led smoke-test` — cycle through every animation on live hardware.

Each animation runs for `duration` seconds (default 3). The daemon must be
running — commands go through DaemonTransport like any other client.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from status_led.protocol import socket_path
from status_led.transport import DaemonTransport
from status_led.wire import build_wire_line

# Each row: (animation, rgb|"-", period|"-", brightness|"-", extra|"-",
#            title, description)
# `extra` is animation-specific: strobe uses "r,g,b" (2nd color),
# level uses "N" (level_pct). "-" means no extra arg.
ANIMATIONS_TABLE = [
    ("solid",   "0,0,255",   "-",     100, "-",          "Solid blue @100%", "all LEDs on, full brightness"),
    ("solid",   "0,0,255",   "-",      30, "-",          "Solid blue @30%",  "all LEDs on, dimmed"),
    ("breathe", "0,50,220",  "3500",  100, "-",          "Breathe blue",     "slow black-to-blue pulse"),
    ("blink",   "180,0,0",   "300",   100, "-",          "Blink red",        "150ms on / 150ms off"),
    ("scanner", "90,0,170",  "1600",  100, "-",          "Scanner purple",   "dot sweeping back and forth"),
    ("fill",    "0,220,0",   "1600",  100, "-",          "Fill green",       "fills one-by-one, then holds"),
    ("strobe",  "180,0,0",   "300",   100, "0,0,180",    "Strobe red/blue",  "police flash, 150ms color swap"),
    ("level",   "0,220,0",   "-",     100, "50",         "Level green 50%",  "about half lit (static)"),
    ("level",   "0,220,0",   "-",     100, "30",         "Level green 30%",  "fewer lit"),
    ("level",   "0,220,0",   "-",     100, "100",        "Level green 100%", "all lit (== solid)"),
    ("converge","0,50,220",  "2000",  100, "-",          "Converge blue",    "converges from ends, then fades"),
    ("pulse",   "255,128,0", "1000",  100, "-",          "Pulse orange",     "sharp rise, slow fade + pause"),
    ("sparkle", "0,220,0",   "600",   100, "-",          "Sparkle green",    "random LED sparks (celebration)"),
    ("heartbeat","220,0,0",  "1000",  100, "-",          "Heartbeat red",    "lub-dub double-beat + pause (alarm)"),
    ("bounce",  "0,200,200", "1200",  100, "-",          "Bounce cyan",      "tailed fade (comet) back and forth"),
    ("off",     "-",         "-",     "-",  "-",          "Off",              "all LEDs off"),
]


def _parse_rgb(s: str) -> tuple[int, int, int] | None:
    if s == "-":
        return None
    a, b, c = s.split(",")
    return int(a), int(b), int(c)


def _build_wire(row) -> str:
    anim, rgb_s, period_s, pct_s, extra_s = row[:5]
    rgb = _parse_rgb(rgb_s)
    rgb2 = _parse_rgb(extra_s) if anim == "strobe" and extra_s != "-" else None
    level = int(extra_s) if anim == "level" and extra_s != "-" else None
    period = int(period_s) if period_s != "-" else None
    pct = int(pct_s) if pct_s != "-" else 100
    return build_wire_line(anim, rgb=rgb, rgb2=rgb2, period=period,
                           level=level, brightness=pct)


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="led smoke-test",
        description="Cycle through every animation on live hardware.",
    )
    parser.add_argument("duration", type=int, nargs="?", default=3,
                        help="Seconds per animation (default 3)")
    parser.add_argument("--socket", default=None, metavar="PATH",
                        help="Daemon Unix socket path (default ~/.status-led/led.sock or "
                             "$STATUS_LED_SOCKET)")
    args = parser.parse_args(argv)

    sock = socket_path(args.socket)
    if not os.path.exists(sock):
        print(f"daemon socket not found at {sock}", file=sys.stderr)
        print("start it with: led service install  (or foreground: led daemon)",
              file=sys.stderr)
        return 1

    transport = DaemonTransport(socket_path=sock, timeout=0.5)
    print(f"==> Starting LED animation smoke test (each for {args.duration} s)\n")

    for row in ANIMATIONS_TABLE:
        anim = row[0]
        title = row[5]
        desc = row[6]
        print(f"\033[1;36m[{anim:<8}]\033[0m \033[1;37m{title:<22}\033[0m {desc}")
        wire = _build_wire(row)
        if not transport.send(wire):
            print("  (transport failure — daemon unreachable)", file=sys.stderr)
        time.sleep(args.duration)

    print("\n==> Smoke test complete.")
    return 0
