#!/usr/bin/env python3
"""
GitLab pipeline poller for status-led.

Polls GitLab's pipelines API and mirrors each active pipeline's status onto
the LED strip. Each pipeline becomes a session (`gitlab-<id>`) so concurrent
pipelines aggregate by priority — a failed pipeline (priority 90) overrides
Claude thinking (60), etc.

Requires: pip3 install requests

Usage:
    # one-shot (cron mode)
    GITLAB_URL=https://gitlab.com \\
    GITLAB_TOKEN=<token-with-read_api> \\
    PROJECTS=myteam/backend,myteam/frontend \\
    ./poller.py --once

    # continuous poll every 15s (default)
    ./poller.py --interval 15

Environment:
    GITLAB_URL     base URL (no trailing slash)
    GITLAB_TOKEN   personal access token with read_api scope
    PROJECTS       comma-separated project paths

Sessions are cleared automatically once GitLab no longer reports them as
active — no stale entries linger on the strip.
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


ACTIVE_STATUSES = ("pending", "running", "success", "failed")


def env_required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"required env var not set: {name}", file=sys.stderr)
        sys.exit(2)
    return value


def fire_led(args: list[str]) -> None:
    """Invoke `led` quietly; never raise — one bad call must not kill the loop."""
    try:
        subprocess.run(["led", "--quiet", *args], check=False)
    except FileNotFoundError:
        print("`led` not on PATH; run ./scripts/install.sh install first", file=sys.stderr)


def fetch_pipelines(gitlab: str, token: str, project: str) -> list[dict]:
    url = f"{gitlab}/api/v4/projects/{project.replace('/', '%2F')}/pipelines"
    resp = requests.get(url, params={"per_page": 50, "sort": "desc"},
                        headers={"PRIVATE-TOKEN": token}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def poll_once(gitlab: str, token: str, projects: list[str], seen: set[str]) -> set[str]:
    """Fire one STATE per active pipeline; clear any session that's gone away.

    Returns the new `seen` set to thread into the next call.
    """
    current: set[str] = set()
    for project in projects:
        try:
            pipelines = fetch_pipelines(gitlab, token, project)
        except (requests.RequestException, ValueError) as e:
            print(f"fetch failed for {project}: {e}", file=sys.stderr)
            continue
        for pipe in pipelines:
            status = pipe.get("status")
            if status not in ACTIVE_STATUSES:
                continue
            sid = f"gitlab-{pipe['id']}"
            current.add(sid)
            fire_led(["--session", sid, "--state", f"gitlab.{status}"])
    for stale in seen - current:
        fire_led(["--end-session", stale])
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description="GitLab pipeline → LED poller")
    parser.add_argument("--once", action="store_true",
                        help="poll once and exit (cron mode)")
    parser.add_argument("--interval", type=int, default=15,
                        help="poll interval in seconds (loop mode, default 15)")
    args = parser.parse_args()

    gitlab = env_required("GITLAB_URL").rstrip("/")
    token = env_required("GITLAB_TOKEN")
    projects = [p.strip() for p in env_required("PROJECTS").split(",") if p.strip()]

    if args.once:
        poll_once(gitlab, token, projects, set())
        return

    print(f"polling every {args.interval}s; projects: {projects}", file=sys.stderr)
    seen: set[str] = set()
    while True:
        seen = poll_once(gitlab, token, projects, seen)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
