#!/usr/bin/env python3
"""LED strip timer (count-up or countdown).

Usage:
  led timer <duration>           # count up: 0 → <duration>  (default)
  led timer --up <duration>
  led timer --countdown <duration>

<duration> accepts 30, 30s, 5m, 1h30m, ...

Renders progress as a `level` bar via the firmware's level animation.
Green at the start, fading to red as the deadline nears. A cyan ↔ magenta
strobe plays for 3 s when the run finishes.

Each tick is a persistent STATE under a per-invocation session id; the
session is cleared on exit (including Ctrl-C / SIGTERM), so killing the
timer takes the strip dark instead of freezing on a partial level.

Animation commands go through `led raw` (no JSON profile — the RGB and
level percentage are computed per tick).
"""
import argparse
import math
import os
import re
import signal
import subprocess
import sys
import time

TICK_INTERVAL = 1.0
FINISH_DURATION = 3.0


def parse_duration(s):
    m = re.fullmatch(r'\s*(?:(\d+)\s*h)?(?:(\d+)\s*m)?(?:(\d+)\s*s?)?\s*',
                     s, re.IGNORECASE)
    if not m or not any(m.groups()):
        raise argparse.ArgumentTypeError(f"invalid duration: {s!r}")
    h, mn, sec = (int(x) if x else 0 for x in m.groups())
    total = h * 3600 + mn * 60 + sec
    if total <= 0:
        raise argparse.ArgumentTypeError(f"duration must be positive: {s!r}")
    return total


def color_for_pct(pct):
    # Green at 0%, red at 100%. Same gradient in both modes — color always
    # signals "how close to the end", independent of bar direction.
    r = pct * 255 // 100
    g = (100 - pct) * 255 // 100
    return f"{r},{g},0"


def led(*args):
    # Fire `led ...`; never raise — hooks must not interrupt callers. Caller
    # passes the subcommand (`raw` or nothing for --end-session) plus flags;
    # --quiet is included in args so the legacy argparse path stays silent too.
    try:
        subprocess.run(["led", *args], check=False,
                       stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass  # `led` not on PATH — silently drop.


def render_tick(mode, total, session, now, start, end):
    if mode == "down":
        remaining = end - now
        if remaining <= 0:
            return
        remaining_int = math.ceil(remaining)
        pct = (total - remaining_int) * 100 // total
        level_pct = remaining_int * 100 // total
    else:
        elapsed = now - start
        if elapsed >= total:
            return
        elapsed_int = int(elapsed)
        pct = elapsed_int * 100 // total
        level_pct = pct
    led("raw", "--quiet", "--session", session, "level",
        "--rgb", color_for_pct(pct), "--level", str(level_pct))


def finish_animation(session):
    led("raw", "--quiet", "--session", session, "strobe",
        "--rgb", "0,255,255", "--rgb2", "255,0,255", "--period", "400")
    time.sleep(FINISH_DURATION)


def run(mode, total):
    session = f"timer-{os.getpid()}"

    # SIGTERM (launchd/systemd's stop signal) → exit cleanly so the `finally`
    # end-session fires; otherwise the strip freezes mid-level on service
    # restart.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    start = time.time()
    end = start + total
    try:
        tick = 0
        while True:
            now = time.time()
            if now >= end if mode == "down" else now - start >= total:
                break
            render_tick(mode, total, session, now, start, end)
            # Drift-free scheduling: target = start + N * TICK_INTERVAL. The
            # original `sleep 1` accumulated per-tick overhead (led socket
            # call, subprocess spawn) into seconds of overshoot on long runs;
            # this formulation self-corrects on the next tick.
            tick += 1
            delta = start + tick * TICK_INTERVAL - time.time()
            if delta > 0:
                time.sleep(delta)
        finish_animation(session)
    except KeyboardInterrupt:
        pass  # Ctrl-C — exit quietly; `finally` clears the session
    finally:
        led("--quiet", "--end-session", session)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="led timer",
        description="LED strip timer (count-up or countdown).",
        usage="%(prog)s [--up|--countdown] <duration>",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--up", "--countup", "--count-up", dest="mode",
                      action="store_const", const="up",
                      help="count up: 0 → <duration> (default)")
    mode.add_argument("--down", "--countdown", dest="mode",
                      action="store_const", const="down",
                      help="count down: <duration> → 0")
    p.add_argument("duration", type=parse_duration,
                   help="e.g. 30, 30s, 5m, or 1h30m")
    args = p.parse_args(argv)
    run(args.mode or "up", args.duration)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
