"""Secrets file reader for credential-using integrations.

Single canonical location: `~/.status-led/secrets.env`. INI-like format,
parsed once per CLI dispatch; only keys matching the caller's prefix are
returned so an integration cannot see another's credentials.

The CLI dispatch (cli.py) computes the prefix from the integration name
and merges the result into the subprocess environment. Integrations read
their credentials via plain `os.environ.get(...)` — no `status_led`
import needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

SECRETS_FILE = Path.home() / ".status-led" / "secrets.env"


def read_secrets(prefix: str) -> dict[str, str]:
    """Read KEY=VALUE pairs from `~/.status-led/secrets.env`.

    Returns `{key: value}` for every key that starts with `prefix`. Keys
    belonging to other integrations are never included — a gitlab poller
    cannot read `SLACK_TOKEN` even if both live in the same file.

    Missing file → empty dict (the caller proceeds; required-key errors
    surface in the integration's own validation).

    Format:
      - One KEY=VALUE per line; split on the first '='
      - Blank lines and lines starting with '#' ignored
      - Matching surrounding quotes stripped ("key" / 'key' → key)
      - Lines without '=' or with an empty key → stderr warning, skipped
    """
    if not SECRETS_FILE.is_file():
        return {}
    try:
        lines = SECRETS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        print(f"warning: could not read {SECRETS_FILE}: {e}", file=sys.stderr)
        return {}

    creds: dict[str, str] = {}
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            print(f"warning: {SECRETS_FILE}:{lineno}: missing '=', skipping",
                  file=sys.stderr)
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if not key:
            print(f"warning: {SECRETS_FILE}:{lineno}: empty key, skipping",
                  file=sys.stderr)
            continue
        if key.startswith(prefix):
            creds[key] = value
    return creds
