"""status-led test package.

Forces tests to resolve integrations against the repo-root integrations/
rather than any stale ~/.status-led/integrations/ that might exist from a
prior install. Individual tests can still override via
STATUS_LED_INTEGRATIONS_DIR if they need a tmpdir.
"""
import os
import sys

# Add src/ to sys.path so `from status_led import ...` works without install.
# This complements conftest.py (pytest) by covering `python -m unittest` too.
_HERE = os.path.dirname(os.path.realpath(__file__))
_SRC = os.path.realpath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Pin integrations dir to repo-root integrations/ for hermetic test runs.
# Force-set (not setdefault) so an empty/stray env var from a prior session
# doesn't make tests resolve against a stale ~/.status-led/integrations/.
_REPO_ROOT = os.path.realpath(os.path.join(_HERE, ".."))
_BUNDLED = os.path.join(_REPO_ROOT, "integrations")
if os.path.isdir(_BUNDLED):
    os.environ["STATUS_LED_INTEGRATIONS_DIR"] = _BUNDLED
