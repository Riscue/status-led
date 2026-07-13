"""`led validate-integrations` — sanity-check every integration bundle.

For each integrations/<name>/:
  1. Name is valid: matches [a-z][a-z0-9-]* and isn't reserved.
  2. integration.json (if present): valid JSON, schema-conforming
     (description/author string-or-null, states/run/hook string).
  3. At least one of states.json, run, or hook exists.
  4. states.json (if present): valid JSON, top-level object, every public
     key resolves through profiles.build_command_from_entry.
  5. run/hook (if present): executable, has #! shebang, `--help` exits 0
     within 10 s.
  6. run and hook together → hard error (integration must be one mode).
  7. README.md required (hard error if missing).

Exits non-zero on any hard failure. Designed to run both locally and in CI.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

from status_led.profiles import (
    bundled_integrations_dir, build_command_from_entry,
)

# Reserved names: builtin shorthand keys, the binary name, and every CLI flag
# we'd want to keep usable long-term. Hyphens match flag style.
RESERVED_NAMES = {
    "default", "on", "off", "led",
    "raw", "status", "session", "end-session", "ttl", "quiet",
    "direct", "json", "port", "rgb", "rgb2", "period", "brightness", "level",
}


def _reserved_names() -> set[str]:
    """RESERVED_NAMES plus every subcommand in commands.REGISTRY.

    Runtime import avoids a circular dependency — commands/__init__ imports us.
    """
    from status_led.commands import REGISTRY
    return RESERVED_NAMES | set(REGISTRY.keys())

NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# State keys: same constraint as integration names plus underscores in non-
# leading position. Leading underscore keys (_comment, _meta) are hidden from
# state lookup and skipped before this regex runs.
KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
EXEC_HELP_TIMEOUT_S = 10.0

# integration.json schema: every recognized key and its expected type.
_MANIFEST_STRING_FIELDS = ("description", "author", "states", "run", "hook")


def _validate_name(name: str) -> list[str]:
    errors: list[str] = []
    if name in _reserved_names():
        errors.append(f"name {name!r} is reserved")
    if not NAME_RE.match(name):
        errors.append(f"name {name!r} must match [a-z][a-z0-9-]*")
    return errors


def _validate_states_json(path: str, name: str) -> list[str]:
    errors: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [f"{name}/states.json: invalid JSON: {e}"]
    except OSError as e:
        return [f"{name}/states.json: cannot read: {e}"]
    if not isinstance(data, dict):
        return [f"{name}/states.json: top-level must be an object, got {type(data).__name__}"]
    for key, entry in data.items():
        if key.startswith("_"):
            continue
        if not KEY_RE.match(key):
            errors.append(f"{name}/states.json: key {key!r} must match {KEY_RE.pattern}")
            continue
        try:
            build_command_from_entry(entry, context=f"{name} {key}")
        except (ValueError, TypeError) as e:
            errors.append(f"{name} {key}: {e}")
    return errors


def _validate_executable(path: str, name: str, kind: str) -> list[str]:
    """kind is 'run' or 'hook'. Same contract for both."""
    label = f"{name}/{kind}"
    errors: list[str] = []
    if not os.access(path, os.X_OK):
        errors.append(f"{label}: not executable (chmod +x)")
        return errors
    try:
        with open(path, "rb") as f:
            first_line = f.readline().decode("utf-8", errors="replace").rstrip()
    except OSError as e:
        return [f"{label}: cannot read: {e}"]
    if not first_line.startswith("#!"):
        errors.append(f"{label}: missing #! shebang (first line: {first_line!r})")
    # Probe --help. argparse exits 0 on --help, so this verifies the script is
    # syntactically valid and uses a compatible flag convention.
    try:
        subprocess.run(
            [path, "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=EXEC_HELP_TIMEOUT_S,
            check=True,
        )
    except subprocess.TimeoutExpired:
        errors.append(f"{label} --help: timed out after {EXEC_HELP_TIMEOUT_S:g}s")
    except subprocess.CalledProcessError as e:
        errors.append(f"{label} --help: exited {e.returncode}")
    except OSError as e:
        errors.append(f"{label} --help: failed to execute: {e}")
    return errors


def _load_manifest_filenames(root: str, name: str) -> tuple[dict | None, list[str]]:
    """Return (manifest_data, errors) for integrations/<name>/integration.json.

    manifest_data is None if the file is absent. On parse/type errors, returns
    ([], errors) with the raw data discarded.
    """
    path = os.path.join(root, name, "integration.json")
    if not os.path.isfile(path):
        return None, []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return None, [f"{name}/integration.json: invalid JSON: {e}"]
    except OSError as e:
        return None, [f"{name}/integration.json: cannot read: {e}"]
    if not isinstance(data, dict):
        return None, [f"{name}/integration.json: top-level must be an object"]
    errors: list[str] = []
    for key, value in data.items():
        if key not in _MANIFEST_STRING_FIELDS:
            errors.append(f"{name}/integration.json: unknown field {key!r}")
            continue
        if value is not None and not isinstance(value, str):
            errors.append(f"{name}/integration.json: {key!r} must be string or null")
    return (data if not errors else {}), errors


def _validate_one(root: str, name: str) -> tuple[list[str], list[str]]:
    """Validate one integration. Returns (hard_errors, soft_warnings)."""
    errors = _validate_name(name)
    if errors:
        return errors, []

    manifest_data, manifest_errors = _load_manifest_filenames(root, name)
    errors.extend(manifest_errors)
    # If manifest is broken, fall back to default filenames so the rest of
    # the checks still run (and likely fail with more useful messages).
    if manifest_data is None:
        manifest_data = {}

    states_name = manifest_data.get("states", "states.json") or "states.json"
    run_name = manifest_data.get("run", "run") or "run"
    hook_name = manifest_data.get("hook", "hook") or "hook"

    states_path = os.path.join(root, name, states_name)
    run_path = os.path.join(root, name, run_name)
    hook_path = os.path.join(root, name, hook_name)
    readme_path = os.path.join(root, name, "README.md")

    has_states = os.path.isfile(states_path)
    has_run = os.path.isfile(run_path)
    has_hook = os.path.isfile(hook_path)

    if has_states:
        errors.extend(_validate_states_json(states_path, name))
    if has_run:
        errors.extend(_validate_executable(run_path, name, "run"))
    if has_hook:
        errors.extend(_validate_executable(hook_path, name, "hook"))

    # run + hook together is forbidden — integration must be one mode.
    if has_run and has_hook:
        errors.append(
            f"{name}/: run ({run_name!r}) and hook ({hook_name!r}) together "
            f"forbidden — pick one mode"
        )

    if not (has_states or has_run or has_hook):
        errors.append(
            f"{name}/: must have at least one of states.json, run, or hook "
            f"(checked {states_name}, {run_name}, {hook_name})"
        )

    # README.md required (hard error per design).
    if not os.path.isfile(readme_path):
        errors.append(f"{name}/: README.md required (documentation)")

    return errors, []


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="led validate-integrations",
        description=(
            "Validate every integration against the contract:\n"
            "  - name matches [a-z][a-z0-9-]* and isn't reserved\n"
            "  - integration.json (optional) is valid JSON with string fields\n"
            "  - at least one of states.json, run, or hook exists\n"
            "  - states.json is valid JSON; every key resolves via build_command_from_entry\n"
            "  - run/hook are executable, have #! shebang, and `--help` exits 0 within 10s\n"
            "  - run and hook together forbidden (integration must be one mode)\n"
            "  - README.md required (hard error)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--integrations-dir", default=None,
                        help="Override the integrations directory to scan "
                             "(default: bundled package integrations)")
    args = parser.parse_args(argv)

    # Default to bundled integrations (canonical source). User can override
    # with --integrations-dir to validate their customized ~/.status-led/
    # integrations/ tree.
    root = args.integrations_dir or bundled_integrations_dir()
    if not os.path.isdir(root):
        print(f"integrations dir not found: {root}", file=sys.stderr)
        return 2

    names = sorted(d for d in os.listdir(root)
                   if os.path.isdir(os.path.join(root, d)))
    if not names:
        print(f"no integrations in {root}", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    all_warnings: list[str] = []

    for name in names:
        errs, warns = _validate_one(root, name)
        all_errors.extend(errs)
        all_warnings.extend(warns)
        if errs:
            print(f"  FAIL  {name}")
        else:
            print(f"  OK    {name}")

    for w in all_warnings:
        print(f"  WARN  {w}", file=sys.stderr)

    print()
    if all_errors:
        print(f"FAILED: {len(all_errors)} error(s) across {len(names)} integration(s):",
              file=sys.stderr)
        for e in all_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"OK: {len(names)} integration(s) validated in {root}")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
