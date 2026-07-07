"""`led status` — query the daemon and render the response.

format_status lives here (single consumer).
"""
from __future__ import annotations

import argparse
import json
import sys

from status_led.protocol import socket_path
from status_led.transport import DaemonTransport


def format_status(data: dict) -> str:
    """Render the daemon's STATUS response as human-readable text.

    Sessions are already sorted by the daemon (priority desc, recency within
    tier) — the top of the list is what's driving the LED.
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


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="led status",
        description="Query the running daemon for current state.",
    )
    parser.add_argument("--json", action="store_true",
                        help="Emit the raw JSON response instead of formatted text")
    parser.add_argument("--socket", default=None, metavar="PATH",
                        help="Daemon Unix socket path (default ~/.status-led/led.sock or "
                             "$STATUS_LED_SOCKET)")
    args = parser.parse_args(argv)

    sock = socket_path(args.socket)
    transport = DaemonTransport(socket_path=sock, timeout=2.0)
    data = transport.query_status()
    if data is None:
        print(f"daemon unreachable at {sock}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(format_status(data))
    return 0
