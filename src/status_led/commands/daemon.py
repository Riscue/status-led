"""`led daemon` — run the daemon in the foreground.

Subcommand wrapper around `status_led.daemon.main`. Useful for debugging
or when launchd/systemd isn't desired. Auto-start at login still goes
through `led service install`.
"""
from __future__ import annotations

from status_led.daemon import main as daemon_main


def run(argv: list[str]) -> int:
    return daemon_main(argv)
