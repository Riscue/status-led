"""Integration manifest discovery.

Each integration lives in `integrations/<name>/` and may contain:
  - README.md           (required)
  - states.json         (default filename; state profile)
  - run                 (default filename; action executable, shebang required)
  - hook                (default filename; hook executable, shebang required)
  - integration.json    (optional manifest; overrides default filenames)

`integration.json` schema (all fields optional):
  {
    "description": str,   # human-readable, shown by validator
    "author": str,        # attribution
    "states": str,        # default "states.json"
    "run": str,           # default "run"
    "hook": str           # default "hook"
  }

File resolution is **per-file fallback across candidate dirs**: the user
dir (~/.status-led/integrations/) is checked first, then the bundled dir.
A user can override a single file (e.g. states.json) without copying the
rest of the integration — they only need the file they want to customize
in their user dir.

Rules enforced elsewhere (validate_integrations.py):
  - run and hook together forbidden (integration must be one mode)
  - README.md required (hard error)
  - at least one of states/run/hook must exist

This module is read-only — it discovers and exposes what's on disk; the
validator checks the contract.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from status_led.profiles import _candidate_dirs


@dataclass
class Manifest:
    """Resolved view of one integration's files. Each *_file field is the
    absolute path of an existing file, or None if the file is absent in all
    search paths. `path` is the first integration directory that exists
    (used as a reference for the integration's home).
    """
    name: str
    path: Path
    description: str | None
    author: str | None
    states_file: Path | None
    run_file: Path | None
    hook_file: Path | None


def _candidate_roots(name: str) -> list[Path]:
    """All `integrations/<name>/` directories that exist across candidate dirs."""
    return [Path(d) / name for d in _candidate_dirs() if (Path(d) / name).is_dir()]


def load_manifest(name: str) -> Manifest | None:
    """Resolve an integration's manifest by name.

    Returns None if no `integrations/<name>/` directory exists in any
    search path.

    For each of `states`, `run`, `hook`: the filename is taken from
    `integration.json` (if present in any search path, user dir first) or
    the default. That filename is then searched across all candidate dirs
    (user dir first). The first existing match wins. A misformed
    `integration.json` falls back to defaults silently — the validator
    reports the error separately.
    """
    roots = _candidate_roots(name)
    if not roots:
        return None

    # Load first valid integration.json (user dir wins).
    data: dict = {}
    for root in roots:
        manifest_path = root / "integration.json"
        if manifest_path.is_file():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
                    break
            except (json.JSONDecodeError, OSError):
                pass

    def resolve(key: str, default: str) -> Path | None:
        filename = data.get(key, default)
        if not isinstance(filename, str):
            return None
        for root in roots:
            candidate = root / filename
            if candidate.is_file():
                return candidate
        return None

    description = data.get("description")
    author = data.get("author")

    return Manifest(
        name=name,
        path=roots[0],
        description=description if isinstance(description, str) else None,
        author=author if isinstance(author, str) else None,
        states_file=resolve("states", "states.json"),
        run_file=resolve("run", "run"),
        hook_file=resolve("hook", "hook"),
    )


def list_integration_names() -> list[str]:
    """All integration directory names across the search path (deduped).

    Used by the validator to enumerate integrations and by `led --help` to
    list discovered integrations in the epilog.
    """
    seen: set[str] = set()
    out: list[str] = []
    for d in _candidate_dirs():
        if not os.path.isdir(d):
            continue
        for entry in sorted(os.listdir(d)):
            if entry in seen:
                continue
            full = os.path.join(d, entry)
            if os.path.isdir(full):
                seen.add(entry)
                out.append(entry)
    return out
