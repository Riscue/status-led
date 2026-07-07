#!/usr/bin/env python3
"""
LED CLI — thin client for the status-led daemon.

Resolves a state reference to a firmware wire line and forwards it to the
daemon. Source-agnostic: knows the wire protocol and JSON profile format,
nothing about specific integrations beyond their profile names.

The daemon is mandatory. If it is not running, the command is dropped (the
LED is not updated) — there is no automatic direct-serial fallback. Use
--direct to bypass the daemon for debug; it is not intended for hook use.

State lookups (positional):
    led claude idle         # profile and key as two tokens
    led on                  # default shorthand (resolves to profile "default", key "on")

Modes (daemon path):
    --session <sid>      STATE       aggregated; competes by priority with other
                                     live sessions. Defaults to $STATUS_LED_SESSION_ID.
    --end-session <sid>  CLEAR       remove a session from the daemon's map.
                                     Defaults to $STATUS_LED_SESSION_ID.
    (neither)            TRANSIENT   one-shot TTL flash (default 3 s, override
                                     with --ttl or $STATUS_LED_TTL_MS).

Dispatch order in main():
    1. Built-in subcommand (commands.REGISTRY): raw, service, smoke-test,
       status, upload-firmware, validate-integrations.
    2. Integration dispatch (integrations/<name>/run or hook) → subprocess.
       State-lookup precedence: `led gitlab running` resolves as state, not run.
    3. State lookup / --end-session (state argparse, this module).

Two cross-cutting rules enforced around dispatch:
    - No integration may invoke another integration (cross-dep forbidden).
    - No integration may re-dispatch itself (recursion guard).
Both use the STATUS_LED_INTEGRATION_ACTIVE env var, set by the dispatcher.

For animations without a profile, use `led raw` (see commands/raw.py).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from status_led import commands
from status_led.config import read_secrets
from status_led.manifest import load_manifest, list_integration_names
from status_led.protocol import (
    build_state_line, build_clear_line, build_transient_line, resolve_ttl_ms,
)
from status_led.profiles import resolve_state_full, is_state_lookup
from status_led.transport import build_transport, send_or_warn


def main(argv: list[str] | None = None) -> int:
    """Dispatch entry point.

    Order:
      1. Built-in subcommand (commands.REGISTRY)
      2. Integration dispatch (run/hook via subprocess) with cross-dep + recursion guards
      3. State argparse (state lookup via positional, --end-session)
    """
    args = list(sys.argv[1:] if argv is None else argv)

    # 1. Built-in subcommands are unconditional — subcommand space is owned
    # by the CLI itself, never an integration name (validator enforces).
    if args and args[0] in commands.REGISTRY:
        return commands.REGISTRY[args[0]](args[1:])

    # 2. Integration dispatch (positional first token, not a flag).
    if args and not args[0].startswith("-"):
        name = args[0]
        manifest = load_manifest(name)

        if manifest is not None:
            # Dispatch guards: enforce "no cross-integration dependency" and
            # "no self recursion" via STATUS_LED_INTEGRATION_ACTIVE. Applied
            # before state-lookup precedence — even state lookup into another
            # integration counts as a dependency.
            rc = _check_dispatch_guard(name, manifest, args[1:])
            if rc is not None:
                return rc

            # State-lookup precedence for state+run/hook integrations:
            # `led gitlab running` resolves as a state lookup, not poller run.
            if (len(args) >= 2 and not args[1].startswith("-")
                    and manifest.states_file is not None
                    and is_state_lookup(name, args[1])):
                return _state_argparse(args)

            # Action dispatch (run or hook) if either exists.
            if manifest.run_file or manifest.hook_file:
                return _dispatch_action(name, manifest, args[1:])

    # 3. State argparse fallback (also covers `led on`, `led --end-session X`).
    return _state_argparse(args)


def _check_dispatch_guard(name: str, manifest, rest: list[str]) -> int | None:
    """Return rc to abort dispatch, or None to proceed.

    Two rules (apply only when STATUS_LED_INTEGRATION_ACTIVE is set, i.e.
    we're already inside an integration's subprocess):
      - Cross-dep: invoking a different integration is forbidden.
      - Recursion: re-dispatching the same integration via bare/flag form
        (hook/run dispatch) would loop.
    State lookups (`led <active> <state>`) and subcommands are not guards —
    those go through their normal paths.
    """
    active = os.environ.get("STATUS_LED_INTEGRATION_ACTIVE")
    if not active:
        return None

    if active != name:
        print(f"integration {name!r} cannot be invoked from inside {active!r} "
              f"(cross-integration dependency forbidden)", file=sys.stderr)
        return 1

    # active == name: same integration. Bare/flag form means hook or run
    # dispatch (depending on which file exists), which would recurse.
    is_bare_or_flag = (not rest) or all(a.startswith("-") for a in rest)
    if is_bare_or_flag and (manifest.run_file or manifest.hook_file):
        print(f"integration {name!r} cannot re-dispatch itself "
              f"(recursion guard)", file=sys.stderr)
        return 1

    return None


def _dispatch_action(name: str, manifest, args: list[str]) -> int:
    """Run/hook script'i subprocess ile çalıştır.

    Hook precedes run if both are present (validator normally forbids this,
    but the runtime check is harmless). State-only integrations don't reach
    here.

    Environment set up for the subprocess:
      - inherited os.environ (PATH, HOME, etc.)
      - + credentials from ~/.status-led/secrets.env with this integration's
        prefix only (e.g. GITLAB_*) — other integrations' secrets invisible
      - + STATUS_LED_INTEGRATION_ACTIVE=name (enables the dispatch guards)
    """
    if manifest.hook_file:
        script = manifest.hook_file
    else:
        script = manifest.run_file

    prefix = name.upper().replace("-", "_") + "_"
    creds = read_secrets(prefix)
    env = {**os.environ, **creds, "STATUS_LED_INTEGRATION_ACTIVE": name}

    try:
        result = subprocess.run([str(script), *args],
                                stdin=sys.stdin,
                                env=env,
                                check=False)
        return result.returncode
    except OSError as e:
        print(f"failed to execute {script}: {e}", file=sys.stderr)
        return 1


def _build_subcommand_epilog() -> str:
    """List built-in subcommands + discovered integrations in --help."""
    lines = ["subcommands:"]
    for name in sorted(commands.REGISTRY):
        lines.append(f"  led {name} ...")
    for name in list_integration_names():
        if name in commands.REGISTRY:
            continue
        manifest = load_manifest(name)
        if manifest and (manifest.run_file or manifest.hook_file):
            lines.append(f"  led {name} ...   (integration)")
    return "\n".join(lines)


def _resolve_state_ref(keys: list[str]) -> tuple[str, str]:
    """Combine positional keys into a (profile, key) pair.

    Accepts:
      ["claude", "idle"]  → ("claude", "idle")
      ["on"]              → ("default", "on")     (default shorthand)
    """
    if len(keys) == 1:
        return ("default", keys[0])
    if len(keys) == 2:
        return (keys[0], keys[1])
    raise ValueError(f"expected 1 or 2 positional state-refs, got {len(keys)}: {keys!r}")


def _state_argparse(args: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="led",
        description="Status-LED CLI: state lookup, daemon control, integrations.",
        epilog=_build_subcommand_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("keys", nargs="*", metavar="state-ref",
                        help="State reference: `PROFILE KEY` (e.g. `claude idle`), "
                             "or `KEY` (e.g. `on` — default profile shorthand)")
    parser.add_argument("--port", default=None,
                        help="Serial port path (e.g. /dev/cu.usbserial-1410)")
    parser.add_argument("--socket", default=None, metavar="PATH",
                        help="Daemon Unix socket path (default ~/.status-led/led.sock or "
                             "$STATUS_LED_SOCKET). Ignored with --direct.")
    parser.add_argument("--direct", action="store_true",
                        help="Bypass the daemon and talk to the serial port directly (debug; ignores --session/--ttl)")
    parser.add_argument("--quiet", action="store_true",
                        help="Stay silent if the LED is missing or fails (do not interrupt the caller)")
    parser.add_argument("--session", default=None, metavar="SID",
                        help="Track this invocation as part of a session (aggregated). "
                             "Defaults to $STATUS_LED_SESSION_ID if set. With a session, the "
                             "command is sent as STATE and competes by priority with other "
                             "live sessions.")
    parser.add_argument("--end-session", default=None, metavar="SID",
                        help="Remove a session from the daemon's aggregation map (SessionEnd). "
                             "Defaults to $STATUS_LED_SESSION_ID if set.")
    parser.add_argument("--ttl", type=int, default=None, metavar="MS",
                        help="Transient override TTL in ms (default 3000 or "
                             "$STATUS_LED_TTL_MS). Only applies when no --session "
                             "is in effect; the resolved state flashes briefly then reverts to "
                             "the aggregate.")
    parsed = parser.parse_args(args)

    if parsed.end_session is not None and parsed.session is not None:
        parser.error("--end-session and --session are mutually exclusive")
    if parsed.end_session is not None and parsed.keys:
        parser.error("--end-session cannot be combined with a positional state-ref")
    if not (parsed.keys or parsed.end_session is not None):
        parser.error("expected: state-ref (`PROFILE KEY` or `KEY`), --end-session SID, "
                     "or a subcommand (try `led --help`)")

    transport = build_transport(parsed.direct, parsed.port, parsed.socket)

    try:
        # CLEAR mode: --end-session removes a session from the daemon's map.
        if parsed.end_session is not None:
            sid = parsed.end_session or os.environ.get("STATUS_LED_SESSION_ID") or ""
            if not sid:
                if not parsed.quiet:
                    print("--end-session requires a session ID "
                          "(pass one explicitly, e.g. --end-session <sid>)",
                          file=sys.stderr)
                sys.exit(0)
            if parsed.direct:
                if not parsed.quiet:
                    print("--direct ignores --end-session (no daemon to clear from)",
                          file=sys.stderr)
                sys.exit(0)
            send_or_warn(transport, build_clear_line(sid), parsed.quiet)
            sys.exit(0)

        # State lookup mode.
        profile_name, key = _resolve_state_ref(parsed.keys)
        wire, priority = resolve_state_full(profile_name, key)

        # Pick STATE (aggregated) vs TRANSIENT (TTL flash) by whether a
        # session id is in play. --direct path bypasses and sends the raw
        # wire line via DirectSerialTransport.
        session_id = parsed.session or os.environ.get("STATUS_LED_SESSION_ID")
        if parsed.direct:
            send_or_warn(transport, wire, parsed.quiet)
        elif session_id:
            send_or_warn(transport, build_state_line(session_id, priority, wire), parsed.quiet)
        else:
            ttl_ms = resolve_ttl_ms(parsed.ttl)
            send_or_warn(transport, build_transient_line(ttl_ms, wire), parsed.quiet)
    except ValueError as e:
        if not parsed.quiet:
            print(f"Error: {e}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
