"""State profile loading and resolution.

Reads JSON profiles from `integrations/<name>/states.json` (or the built-in
profiles in BUILTIN_PROFILES) and resolves `<profile> <key>` state references
into firmware wire lines (delegating the wire format itself to wire.py).

This module owns:
  - directory resolution (integrations_dir, bundled_integrations_dir)
  - JSON loading and validation (load_profile, coerce_rgb_from_json)
  - profile-entry → wire-line translation (build_command_from_entry)
  - state reference resolution (resolve_state_full)

Nothing in status_led should reach into integrations/ directly except this
module and `led service install`. Adding a new state to an existing
integration means editing its states.json — no code change here.
"""
from __future__ import annotations

import json
import os

from status_led.wire import build_wire_line, PERIOD_ANIMATIONS

# Hardcoded profiles — always available, no JSON lookup. Only `default` lives
# here; everything else ships as integrations/<name>/states.json. `led <key>`
# (bare positional) is shorthand for `led default <key>`.
BUILTIN_PROFILES: dict[str, dict] = {
    "default": {
        "on": {"animation": "converge", "rgb": [0, 50, 220],
               "period": 2000, "brightness": 100},
        "off": {"animation": "off"},
    },
}


def integrations_dir() -> str:
    """Resolve the integrations directory.

    Priority:
      1. STATUS_LED_INTEGRATIONS_DIR env var (explicit override; tests use this)
      2. ~/.status-led/integrations/  (populated by `led service install`)
      3. bundled package integrations/  (fallback for dev / pre-install)

    Note: when the user dir exists but is incomplete (missing some
    integrations), load_profile and integration_script still find the
    missing ones via bundled_integrations_dir() — see _resolve_integration_path.
    """
    override = os.environ.get("STATUS_LED_INTEGRATIONS_DIR")
    if override:
        return override
    user_dir = os.path.join(os.path.expanduser("~"), ".status-led", "integrations")
    if os.path.isdir(user_dir):
        return user_dir
    return bundled_integrations_dir()


def bundled_integrations_dir() -> str:
    """Path to the integrations/ shipped with the package.

    Installed (pipx/wheel): the build hook copies integrations/ into
    `status_led/integrations/` next to this file.

    Dev (running from the repo): integrations/ lives at the repo root,
    two levels above this file (src/status_led/ → src/ → repo_root).
    """
    here = os.path.dirname(os.path.realpath(__file__))
    packaged = os.path.join(here, "integrations")
    if os.path.isdir(packaged):
        return packaged
    repo_root = os.path.realpath(os.path.join(here, "..", ".."))
    return os.path.join(repo_root, "integrations")


def _candidate_dirs() -> list[str]:
    """Search path for individual integrations. User dir first, then bundled.

    STATUS_LED_INTEGRATIONS_DIR overrides collapse to a single entry.
    """
    override = os.environ.get("STATUS_LED_INTEGRATIONS_DIR")
    if override:
        return [override]
    user_dir = os.path.join(os.path.expanduser("~"), ".status-led", "integrations")
    dirs = []
    if os.path.isdir(user_dir):
        dirs.append(user_dir)
    dirs.append(bundled_integrations_dir())
    return dirs


def _resolve_integration_path(name: str, filename: str) -> str | None:
    """Find integrations/<name>/<filename> by walking the search path.

    Returns the first match or None. User dir takes precedence, then bundled.
    Used by load_profile (filename="states.json") and integration_script
    (filename="run").
    """
    for d in _candidate_dirs():
        candidate = os.path.join(d, name, filename)
        if os.path.exists(candidate):
            return candidate
    return None


def load_profile(profile_name: str) -> dict:
    """Load a state profile by name.

    Order: BUILTIN_PROFILES first, then integrations/<name>/states.json
    (searching user dir first, then bundled). Raises ValueError if the
    profile is missing or malformed.
    """
    if profile_name in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[profile_name]
    path = _resolve_integration_path(profile_name, "states.json")
    if path is None:
        raise ValueError(
            f"profile not found: {profile_name!r} "
            f"(searched {[d for d in _candidate_dirs()]})"
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
    """Validate a JSON-decoded RGB value (a 3-element list)."""
    if not (isinstance(rgb, list) and len(rgb) == 3):
        raise ValueError(f"{context}: rgb must be a 3-element list")
    try:
        values = [int(v) for v in rgb]
    except (TypeError, ValueError):
        raise ValueError(f"{context}: rgb values must be integers")
    return (max(0, min(255, values[0])),
            max(0, min(255, values[1])),
            max(0, min(255, values[2])))


def build_command_from_entry(entry, context: str) -> str:
    """Validate a JSON profile entry and build its wire line.

    Field extraction is per-animation (e.g., only strobe reads rgb2); the
    actual wire-line formatting lives in wire.build_wire_line. Context is
    preserved in error messages so callers can trace which profile.key failed.
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


def resolve_state_full(profile_name: str, key: str) -> tuple[str, int]:
    """Resolve `<profile> <key>` → (wire_line, priority).

    Priority comes from the entry's optional `priority` field; defaults to 0
    (lowest). Priority is opaque to the daemon — it just means "higher number
    wins" during multi-session aggregation.
    """
    profile = load_profile(profile_name)
    public_keys = {k for k in profile if not k.startswith("_")}
    if key not in public_keys:
        raise ValueError(
            f"state {key!r} not in profile {profile_name!r} "
            f"(valid: {sorted(public_keys)})"
        )
    entry = profile[key]
    context = f"{profile_name} {key}"
    wire = build_command_from_entry(entry, context=context)
    priority = 0
    if isinstance(entry, dict):
        raw_priority = entry.get("priority", 0)
        try:
            priority = int(raw_priority)
        except (TypeError, ValueError):
            raise ValueError(
                f"{context}: priority must be an integer, got {raw_priority!r}"
            )
    return wire, priority


def is_state_lookup(profile_name: str, key: str) -> bool:
    """True if `profile_name` resolves to a profile that contains `key`.

    Used by cli.py's dispatch to distinguish `led gitlab running` (state lookup
    for a hybrid integration) from `led gitlab` (action dispatch). Returns
    False if the profile doesn't exist or `key` isn't one of its public
    state keys (those not starting with `_`).
    """
    try:
        profile = load_profile(profile_name)
    except ValueError:
        return False
    public_keys = {k for k in profile if not k.startswith("_")}
    return key in public_keys
