"""Pytest / unittest configuration for status-led tests.

Adds `src/` to sys.path so test files can `from status_led.cli import ...`
without per-file path hacks.

Both pytest and `python -m unittest` pick this up because conftest.py is
auto-imported by pytest and unittest discovers it as a sibling of test files
when run from repo root. The sys.path mutation is idempotent.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
_SRC = os.path.realpath(_SRC)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
