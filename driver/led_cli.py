#!/usr/bin/env python3
"""
LED Animation Driver (generic, thin client)
-----------------------------------------
Resolves a state (or raw animation) to a firmware wire line and forwards it
to led_daemon.py. The driver is state-agnostic: it knows the wire protocol
(animation + RGB + period + brightness) and the JSON profile format, but
nothing about the calling source beyond the profile name passed on the command line.

The driver is a thin client: it connects to led_daemon.py over a Unix domain
socket (~/.status-led/led.sock). The daemon holds the serial port open (which
avoids the 0.5 s ESP8266 reset wait) and aggregates state across multiple
concurrent sessions.

The daemon is mandatory. If it is not running, the command is dropped (the
LED is not updated) — there is no automatic direct-serial fallback. Use
--direct to bypass the daemon for debug; it is not intended for hook use.

State mappings live in two places:
  - BUILTIN_PROFILES (below)   hardcoded profiles; only `default` lives here
  - integrations/<name>/states.json   per-integration profiles (claude, gitlab, ...)

Each profile is a flat dict of {state_name: {animation, rgb, period,
brightness, [priority]}}. Use --state <profile>.<key> to load one. Add a new
integration by dropping a folder in integrations/<name>/ with its states.json
+ caller script — no Python changes required.

Modes (daemon path):
    --session <sid>      STATE       aggregated; competes by priority with other
                                     live sessions. Defaults to $SESSION_ID.
    --end-session <sid>  CLEAR       remove a session from the daemon's map.
                                     Defaults to $SESSION_ID.
    (neither)            TRANSIENT   one-shot TTL flash (default 3 s, override
                                     with --ttl or $STATUS_LED_TRANSIENT_TTL_MS).

Setup:
    pip3 install pyserial

Usage:
    # State lookup, aggregated as part of a session:
    python3 led_cli.py --quiet --session $SESSION_ID --state claude.idle

    # SessionEnd:
    python3 led_cli.py --quiet --end-session $SESSION_ID

    # Ad-hoc transient flash (no session context — reverts after TTL):
    python3 led_cli.py --state gitlab.failed
    python3 led_cli.py --state claude.error --ttl 10000

    # Default-profile shorthand (`led <key>` == `led --state default.<key>`):
    python3 led_cli.py off

    # Raw mode (direct animation, for testing/custom use):
    python3 led_cli.py --raw breathe --rgb 0,50,220 --period 3500
    python3 led_cli.py --raw strobe --rgb 180,0,0 --rgb2 0,0,180 --period 300
    python3 led_cli.py --raw off

    # Bypass the daemon and talk to the serial port directly (debug):
    python3 led_cli.py --direct --state default.on

The port is auto-detected by scanning USB-serial devices; if it cannot be
found, set the STATUS_LED_PORT environment variable or pass --port.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time

try:
    import serial
except ImportError:
    serial = None

from protocol import BAUD_RATE, RESET_WAIT_SECONDS, find_esp8266_port, socket_path

ANIMATIONS = {"solid", "breathe", "blink", "scanner", "fill",
              "strobe", "level", "converge", "off"}
# Animations that need a period_ms parameter.
PERIOD_ANIMATIONS = {"breathe", "blink", "scanner", "fill", "converge", "strobe"}
DAEMON_SOCKET_TIMEOUT = 0.3
DEFAULT_TRANSIENT_TTL_MS = 3000

# Hardcoded profiles — always available, no JSON lookup. Only `default` lives
# here; everything else ships as integrations/<name>/states.json. `led <key>`
# (bare positional) is shorthand for `led --state default.<key>`.
BUILTIN_PROFILES: dict[str, dict] = {
    "default": {
        "on": {"animation": "converge", "rgb": [0, 50, 220],
               "period": 2000, "brightness": 100},
        "off": {"animation": "off"},
    },
}


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


def integrations_dir() -> str:
    """Directory containing per-integration profiles (integrations/<name>/states.json).

    Override with STATUS_LED_INTEGRATIONS_DIR. In the installed layout this is
    a sibling of led_cli.py (~/.status-led/integrations/). In the repo layout
    it sits one level up (repo-root/integrations/).
    """
    override = os.environ.get("STATUS_LED_INTEGRATIONS_DIR")
    if override:
        return override
    here = os.path.dirname(os.path.realpath(__file__))
    installed = os.path.join(here, "integrations")
    if os.path.isdir(installed):
        return installed
    return os.path.join(here, "..", "integrations")


def load_profile(profile_name: str) -> dict:
    """Load a state profile by name.

    Order: BUILTIN_PROFILES first, then integrations/<name>/states.json.
    """
    if profile_name in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[profile_name]
    path = os.path.join(integrations_dir(), profile_name, "states.json")
    if not os.path.exists(path):
        raise ValueError(
            f"profile not found: {profile_name!r} (looked at {path})"
        )
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


def validate_period(period, anim: str) -> int:
    """Period is required and must be a number >= 50 ms for time-based animations."""
    if isinstance(period, bool) or period is None:
        raise ValueError(f"period required for animation {anim!r} (number >= 50 ms)")
    if not isinstance(period, (int, float)):
        raise ValueError(f"period for animation {anim!r} must be a number")
    if period < 50:
        raise ValueError(f"period for animation {anim!r} must be >= 50 ms")
    return int(period)


def _clamp8(v: int) -> int:
    return max(0, min(255, int(v)))


def _clamp_pct(v: int) -> int:
    return max(0, min(100, int(v)))


def build_wire_line(anim: str,
                    rgb: tuple[int, int, int] | None = None,
                    rgb2: tuple[int, int, int] | None = None,
                    period: int | None = None,
                    level: int | None = None,
                    brightness: int = 100) -> str:
    """Single source of truth for the firmware wire-line format.

    Per-animation requirements:
      off                                            → "off" (other args ignored)
      solid    rgb                                   → "solid r g b [pct]"
      level    rgb, level                            → "level r g b level [pct]"
      strobe   rgb, rgb2, period                     → "strobe r g b r2 g2 b2 period [pct]"
      breathe/blink/scanner/fill/converge
               rgb, period                           → "<anim> r g b period [pct]"
    """
    if anim not in ANIMATIONS:
        raise ValueError(f"invalid animation {anim!r} (valid: {sorted(ANIMATIONS)})")
    if anim == "off":
        return "off"
    if rgb is None:
        raise ValueError(f"rgb required for animation {anim!r}")
    r, g, b = (_clamp8(rgb[0]), _clamp8(rgb[1]), _clamp8(rgb[2]))
    pct = _clamp_pct(brightness)
    if anim == "solid":
        return f"solid {r} {g} {b} {pct}"
    if anim == "level":
        if level is None:
            raise ValueError(f"level required for animation {anim!r} (0-100)")
        return f"level {r} {g} {b} {_clamp_pct(level)} {pct}"
    if anim == "strobe":
        if rgb2 is None:
            raise ValueError(f"rgb2 required for animation {anim!r}")
        r2, g2, b2 = (_clamp8(rgb2[0]), _clamp8(rgb2[1]), _clamp8(rgb2[2]))
        return f"strobe {r} {g} {b} {r2} {g2} {b2} {validate_period(period, anim)} {pct}"
    # breathe/blink/scanner/fill/converge
    return f"{anim} {r} {g} {b} {validate_period(period, anim)} {pct}"


def build_command_from_entry(entry, context: str) -> str:
    """Validate a JSON profile entry and build its wire line.

    Field extraction is per-animation (e.g., only `strobe` reads `rgb2`); the
    actual wire-line formatting lives in build_wire_line.
    """
    if not isinstance(entry, dict):
        raise ValueError(f"{context}: entry must be a JSON object")
    anim = entry.get("animation")
    try:
        return build_wire_line(
            anim,
            rgb=coerce_rgb_from_json(entry.get("rgb"), context) if anim != "off" else None,
            rgb2=coerce_rgb_from_json(entry.get("rgb2"), context) if anim == "strobe" else None,
            period=entry.get("period") if anim in PERIOD_ANIMATIONS else None,
            level=entry.get("level") if anim == "level" else None,
            brightness=entry.get("brightness", 100),
        )
    except ValueError as e:
        raise ValueError(f"{context}: {e}")


def resolve_state(state_ref: str) -> str:
    """Resolve PROFILE.KEY → firmware wire line. Kept for compatibility."""
    wire, _ = resolve_state_full(state_ref)
    return wire


def resolve_state_full(state_ref: str) -> tuple[str, int]:
    """Resolve PROFILE.KEY → (wire_line, priority).

    Priority comes from the entry's optional `priority` field; defaults to 0
    (lowest). Priority is opaque to the daemon — it just means "higher number
    wins" during multi-session aggregation.
    """
    if "." not in state_ref:
        raise ValueError(f"--state expects PROFILE.KEY (e.g. claude.idle, gitlab.pending), got {state_ref!r}")
    profile_name, key = state_ref.split(".", 1)
    profile = load_profile(profile_name)
    public_keys = {k for k in profile if not k.startswith("_")}
    if key not in public_keys:
        raise ValueError(
            f"state {key!r} not in profile {profile_name!r} (valid: {sorted(public_keys)})"
        )
    entry = profile[key]
    wire = build_command_from_entry(entry, context=state_ref)
    priority = 0
    if isinstance(entry, dict):
        raw_priority = entry.get("priority", 0)
        try:
            priority = int(raw_priority)
        except (TypeError, ValueError):
            raise ValueError(
                f"{state_ref}: priority must be an integer, got {raw_priority!r}"
            )
    return wire, priority


def build_state_line(sid: str, priority: int, wire: str) -> str:
    """STATE <sid> <priority> <wire-line...> — set/update a session (aggregated)."""
    return f"STATE {sid} {priority} {wire}"


def build_clear_line(sid: str) -> str:
    """CLEAR <sid> — remove a session from the aggregation map."""
    return f"CLEAR {sid}"


def build_transient_line(ttl_ms: int, wire: str) -> str:
    """TRANSIENT <ttl_ms> <wire-line...> — one-shot override with TTL."""
    return f"TRANSIENT {ttl_ms} {wire}"


def build_raw_command(anim: str, rgb: tuple[int, int, int] | None,
                      period: int | None, pct: int,
                      rgb2: tuple[int, int, int] | None = None,
                      level: int | None = None) -> str:
    """Build a wire line from raw CLI args. Thin wrapper around build_wire_line."""
    return build_wire_line(anim, rgb=rgb, rgb2=rgb2, period=period,
                           level=level, brightness=pct)


def send_command(cmd: str, port: str | None, quiet: bool = False) -> bool:
    if serial is None:
        if not quiet:
            print("pyserial is not installed. Install it with: pip3 install pyserial", file=sys.stderr)
        return False

    resolved_port = port or os.environ.get("STATUS_LED_PORT") or find_esp8266_port()
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


def send_via_daemon(cmd: str, quiet: bool = False,
                    timeout: float = DAEMON_SOCKET_TIMEOUT) -> bool:
    """Forward a resolved wire command to led_daemon.py over the Unix socket.

    Returns True on success, False if the daemon is unavailable. The caller
    (main) drops the command in that case — there is no automatic fallback
    to direct-serial. Use `--direct` to bypass the daemon explicitly.
    """
    path = socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall((cmd + "\n").encode("utf-8"))
        return True
    except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError) as e:
        if not quiet:
            print(f"daemon unreachable at {path} ({e}); command dropped — "
                  f"start it with: ./scripts/install.sh install "
                  f"(or run in foreground: python3 driver/led_daemon.py)",
                  file=sys.stderr)
        return False


def query_daemon_status(timeout: float = 2.0) -> dict | None:
    """Send a STATUS query to the daemon and return the parsed JSON response.

    Returns None if the daemon is unreachable, the response is malformed, or
    the connection times out. Caller (main) decides how to surface that.
    """
    path = socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall(b"STATUS\n")
            chunks = []
            while True:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return json.loads(b"".join(chunks).decode("utf-8"))
    except (FileNotFoundError, ConnectionRefusedError, socket.timeout,
            OSError, json.JSONDecodeError):
        return None


def format_status(data: dict) -> str:
    """Render the daemon's STATUS response as human-readable text. Used by
    `led --status`. Sessions are already sorted by the daemon (priority desc,
    recency within tier) — the top of the list is what's driving the LED.
    """
    lines = []
    output = data.get("current_output") or "off"
    lines.append(f"LED output: {output}")
    if data.get("serial_connected"):
        port = data.get("serial_port") or "-"
        lines.append(f"Serial: {port} (connected)")
    else:
        lines.append("Serial: (DISCONNECTED)")
    lines.append("")
    sessions = data.get("sessions") or []
    if sessions:
        lines.append(f"Sessions ({len(sessions)}):")
        for s in sessions:
            lines.append(
                f"  {s['sid']:<24} pri={s['priority']:<4} "
                f"age={round(s['age_s'], 1)}s  {s['wire']}"
            )
    else:
        lines.append("Sessions: (none)")
    lines.append("")
    transient = data.get("transient")
    if transient:
        lines.append(
            f"Transient: expires in {round(transient['expires_in_s'], 1)}s  "
            f"{transient['wire']}"
        )
    else:
        lines.append("Transient: (none)")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="LED animation driver (generic)")
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument("--raw", metavar="ANIM",
                      help="Send a raw animation: solid/breathe/blink/scanner/fill/strobe/level/converge/off")
    mode.add_argument("--state", metavar="PROFILE.KEY",
                      help="Look up a state in integrations/<PROFILE>/states.json, or a built-in profile (default) (e.g. claude.idle, gitlab.pending)")
    mode.add_argument("--status", action="store_true",
                      help="Query the daemon: current output, active sessions, transient, serial state")
    parser.add_argument("key", nargs="?", default=None,
                        help="Shorthand for --state default.<key> (e.g. `led off`)")
    parser.add_argument("--rgb", default=None,
                        help="Color as 'r,g,b' (e.g. 0,50,220) or single value for grayscale (--raw only)")
    parser.add_argument("--rgb2", default=None,
                        help="Second color for strobe as 'r,g,b' (--raw only; required for strobe)")
    parser.add_argument("--period", type=int, default=None,
                        help="Animation period in ms (--raw only; required for breathe/blink/scanner/fill/strobe/converge)")
    parser.add_argument("--level", type=int, default=None,
                        help="Level percentage 0-100 (--raw only; required for level animation)")
    parser.add_argument("--brightness", type=int, default=100,
                        help="Brightness 0-100 (default 100, scaled below firmware MAX_BRIGHTNESS)")
    parser.add_argument("--port", default=None,
                        help="Serial port path (e.g. /dev/cu.usbserial-1410)")
    parser.add_argument("--direct", action="store_true",
                        help="Bypass the daemon and talk to the serial port directly (debug; ignores --session/--ttl)")
    parser.add_argument("--quiet", action="store_true",
                        help="Stay silent if the LED is missing or fails (do not interrupt the caller)")
    parser.add_argument("--session", default=None, metavar="SID",
                        help="Track this invocation as part of a session (aggregated). "
                             "Defaults to $SESSION_ID if set. With a session, the "
                             "command is sent as STATE and competes by priority with other "
                             "live sessions.")
    parser.add_argument("--end-session", default=None, metavar="SID",
                        help="Remove a session from the daemon's aggregation map (SessionEnd). "
                             "Defaults to $SESSION_ID if set.")
    parser.add_argument("--ttl", type=int, default=None, metavar="MS",
                        help="Transient override TTL in ms (default 3000 or "
                             "$STATUS_LED_TRANSIENT_TTL_MS). Only applies when no --session "
                             "is in effect; the resolved state flashes briefly then reverts to "
                             "the aggregate.")
    parser.add_argument("--json", action="store_true",
                        help="With --status, emit the raw JSON response instead of formatted text")
    args = parser.parse_args()

    if (args.raw or args.state) and args.key:
        parser.error("positional <key> cannot be combined with --raw or --state")
    if args.end_session is not None and args.session is not None:
        parser.error("--end-session and --session are mutually exclusive")
    if args.end_session is not None and (args.raw or args.state or args.key):
        parser.error("--end-session cannot be combined with --state, --raw, or positional <key>")
    if args.status and (args.raw or args.state or args.key or args.end_session is not None
                       or args.session is not None or args.direct):
        parser.error("--status cannot be combined with --raw, --state, --key, "
                     "--end-session, --session, or --direct")
    if not (args.raw or args.state or args.key or args.end_session is not None or args.status):
        parser.error("expected one of: --raw ANIM, --state PROFILE.KEY, positional <key>, "
                     "--end-session SID, or --status")

    try:
        # STATUS mode: query daemon and print state, then exit.
        if args.status:
            data = query_daemon_status()
            if data is None:
                if not args.quiet:
                    print(f"daemon unreachable at {socket_path()}", file=sys.stderr)
                sys.exit(0)
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print(format_status(data))
            sys.exit(0)

        # CLEAR mode: --end-session removes a session from the daemon's map.
        if args.end_session is not None:
            sid = args.end_session or os.environ.get("SESSION_ID") or ""
            if not sid:
                if not args.quiet:
                    print("--end-session requires a session ID "
                          "(pass one explicitly, e.g. --end-session <sid>)",
                          file=sys.stderr)
                sys.exit(0)
            if args.direct:
                if not args.quiet:
                    print("--direct ignores --end-session (no daemon to clear from)",
                          file=sys.stderr)
                sys.exit(0)
            send_via_daemon(build_clear_line(sid), quiet=args.quiet)
            sys.exit(0)

        # Resolve wire line (and priority if from a profile)
        if args.raw:
            if args.raw not in ANIMATIONS:
                raise ValueError(f"unknown animation {args.raw!r} (valid: {sorted(ANIMATIONS)})")
            rgb = parse_rgb(args.rgb) if args.rgb is not None else None
            rgb2 = parse_rgb(args.rgb2) if args.rgb2 is not None else None
            wire = build_raw_command(args.raw, rgb, args.period, args.brightness,
                                     rgb2=rgb2, level=args.level)
            # raw mode = manual invocation; outrank every session state
            priority = 100
        else:
            wire, priority = resolve_state_full(args.state or f"default.{args.key}")

        # Direct serial debug path — bypasses daemon and aggregation entirely.
        if args.direct:
            send_command(wire, args.port, quiet=args.quiet)
            sys.exit(0)

        # Daemon path: pick STATE (aggregated) vs TRANSIENT (TTL flash) by
        # whether a session id is in play.
        session_id = args.session or os.environ.get("SESSION_ID")
        if session_id:
            send_via_daemon(build_state_line(session_id, priority, wire),
                            quiet=args.quiet)
        else:
            ttl_ms = (args.ttl if args.ttl is not None
                      else int(os.environ.get("STATUS_LED_TRANSIENT_TTL_MS",
                                              DEFAULT_TRANSIENT_TTL_MS)))
            send_via_daemon(build_transient_line(ttl_ms, wire), quiet=args.quiet)
    except ValueError as e:
        if not args.quiet:
            print(f"Error: {e}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
