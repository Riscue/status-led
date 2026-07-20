#!/usr/bin/env python3
"""
GitLab pipeline poller for status-led.

Polls GitLab's pipelines API and mirrors each pipeline's status onto the LED
strip. Each pipeline becomes a session (`gitlab-<id>`) so concurrent pipelines
aggregate by priority — a failed pipeline (priority 90) overrides Claude
thinking (60), etc.

Behaviour: while any watched pipeline is in-flight, keep polling at --interval.
Once everything goes idle, hold the final state briefly so the outcome is
visible, then CLEAR every session this script created and exit.

For always-on monitoring, run under systemd with Restart=always + RestartSec,
or wrap in a shell loop: `while true; do led gitlab; sleep 30; done`.

Requires: pip3 install requests

Credentials: read from os.environ. The CLI dispatch (led gitlab) loads
~/.status-led/secrets.env and exposes only GITLAB_* keys to this subprocess.

Environment (GITLAB_* prefix):
    GITLAB_URL       base URL (no trailing slash)
    GITLAB_TOKEN     personal access token with read_api scope
    GITLAB_PROJECTS  comma-separated project paths

Sessions created during a run are CLEARed on exit (clean, Ctrl-C, or idle), so
no stale entries linger on the strip between invocations.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

try:
    import requests
except ImportError:
    print("requests is not installed; install with: pip3 install requests", file=sys.stderr)
    sys.exit(1)


ACTIVE_STATUSES = ("pending", "running")

# Seconds to hold the final state on a clean exit before clearing, so the user
# actually sees the outcome (fill animation period is ~3 s; shorter than this
# and the fill wouldn't complete before the strip goes dark).
FINAL_HOLD_SECONDS = 15


def _required(name: str) -> str:
    """Fetch a required env var or exit(2) with a hint pointing at secrets.env."""
    value = os.environ.get(name)
    if not value:
        print(f"required env var not set: {name}", file=sys.stderr)
        print(f"(add it to ~/.status-led/secrets.env — see secrets.env.example)",
              file=sys.stderr)
        sys.exit(2)
    return value


def fire_led(args: list[str]) -> None:
    """Invoke `led` quietly; never raise — one bad call must not kill the loop."""
    try:
        subprocess.run(["led", "--quiet", *args], check=False)
    except FileNotFoundError:
        print("`led` not on PATH; install with: pipx install .  then: led service install", file=sys.stderr)


def clear_sessions(seen: set[str]) -> None:
    """CLEAR every session we created so the LED doesn't stay stuck on the
    last animation after the script exits."""
    for sid in seen:
        fire_led(["--end-session", sid])


def fetch_pipelines(gitlab: str, token: str, project: str) -> list[dict]:
    url = f"{gitlab}/api/v4/projects/{project.replace('/', '%2F')}/pipelines"
    # per_page=1: we only ever care about the most recent pipeline — active
    # or not, that's the single one we'll show.
    resp = requests.get(url, params={"per_page": 1, "sort": "desc"},
                        headers={"PRIVATE-TOKEN": token}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def poll(gitlab: str, token: str, projects: list[str], seen: set[str]) -> tuple[set[str], bool]:
    """For each project: if any pipeline is currently active, STATE only those;
    otherwise STATE only the single most recent pipeline (whatever its status).

    CLEARs any session that was previously STATE'd but is no longer current.

    Returns (new_seen, has_active) where has_active is True if any project had
    an in-flight pipeline — the caller uses this to decide whether to keep
    watching or exit.
    """
    current: set[str] = set()
    has_active = False
    for project in projects:
        try:
            pipelines = fetch_pipelines(gitlab, token, project)
        except (requests.RequestException, ValueError) as e:
            print(f"fetch failed for {project}: {e}", file=sys.stderr)
            continue
        active = [p for p in pipelines if p.get("status") in ACTIVE_STATUSES]
        if active:
            has_active = True
            to_show = active
        else:
            to_show = pipelines[:1]
        for pipeline in to_show:
            sid = f"gitlab-{pipeline['id']}"
            current.add(sid)
            fire_led(["--session", sid, "gitlab", pipeline['status']])
    for stale in seen - current:
        fire_led(["--end-session", stale])
    return current, has_active


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="GitLab pipeline → LED poller")
    parser.add_argument("--interval", type=int, default=15,
                        help="poll interval in seconds while active (default 15)")
    args = parser.parse_args(argv)

    gitlab = _required("GITLAB_URL").rstrip("/")
    token = _required("GITLAB_TOKEN")
    projects = [p.strip() for p in _required("GITLAB_PROJECTS").split(",") if p.strip()]

    print(f"watching projects: {projects}", file=sys.stderr)
    seen: set[str] = set()
    interrupted = False
    try:
        while True:
            seen, has_active = poll(gitlab, token, projects, seen)
            if not has_active:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if seen:
            # Hold the final state long enough for the fill animation to
            # complete; skip on Ctrl-C since the user wants out now. A second
            # Ctrl-C during the hold should not skip the CLEAR below.
            if not interrupted:
                try:
                    time.sleep(FINAL_HOLD_SECONDS)
                except KeyboardInterrupt:
                    pass
            clear_sessions(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
