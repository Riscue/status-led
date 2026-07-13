"""validate-integrations tests.

Exercises the validator against synthetic tmpdir layouts so we can test
all the failure modes (missing files, bad JSON, missing shebang, etc.)
without touching real integrations.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from status_led.commands import validate_integrations as vi


class _Sandbox:
    """Context manager: yields a tmp integrations dir + helpers."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()

    def write(self, integration: str, filename: str, content: str,
              mode: int | None = None) -> str:
        """Write content to <root>/<integration>/<filename>. Returns the path."""
        d = os.path.join(self.root, integration)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, filename)
        with open(path, "w") as f:
            f.write(content)
        if mode is not None:
            os.chmod(path, mode)
        return path

    def validate(self, name: str) -> tuple[list[str], list[str]]:
        """Convenience: run _validate_one against this sandbox."""
        return vi._validate_one(self.root, name)


class ValidateNameTest(unittest.TestCase):
    def test_reserved_name_rejected(self):
        with _Sandbox() as sb:
            sb.write("on", "states.json", '{"x":{"animation":"off"}}')
            sb.write("on", "README.md", "# on")
            errs, _ = sb.validate("on")
            self.assertTrue(any("reserved" in e for e in errs))

    def test_invalid_chars_rejected(self):
        with _Sandbox() as sb:
            sb.write("BadName", "states.json", '{"x":{"animation":"off"}}')
            sb.write("BadName", "README.md", "# bad")
            errs, _ = sb.validate("BadName")
            self.assertTrue(errs)

    def test_underscore_rejected_at_start(self):
        with _Sandbox() as sb:
            sb.write("_hidden", "states.json", '{"x":{"animation":"off"}}')
            sb.write("_hidden", "README.md", "# hidden")
            errs, _ = sb.validate("_hidden")
            self.assertTrue(errs)


class ValidateStatesJsonTest(unittest.TestCase):
    def _setup(self, sb: _Sandbox, states_content: str):
        sb.write("good", "states.json", states_content)
        sb.write("good", "README.md", "# good")

    def test_valid_json_passes(self):
        with _Sandbox() as sb:
            self._setup(sb, '{"idle":{"animation":"off"}}')
            errs, _ = sb.validate("good")
            self.assertEqual(errs, [])

    def test_underscore_keys_skipped(self):
        with _Sandbox() as sb:
            self._setup(sb, '{"_comment":"...","idle":{"animation":"off"}}')
            errs, _ = sb.validate("good")
            self.assertEqual(errs, [])

    def test_invalid_json_rejected(self):
        with _Sandbox() as sb:
            self._setup(sb, "{not json")
            errs, _ = sb.validate("good")
            self.assertTrue(any("invalid JSON" in e for e in errs))

    def test_bad_animation_rejected(self):
        with _Sandbox() as sb:
            self._setup(sb, '{"x":{"animation":"frob","rgb":[0,0,0]}}')
            errs, _ = sb.validate("good")
            self.assertTrue(any("invalid animation" in e for e in errs))

    def test_missing_period_rejected(self):
        with _Sandbox() as sb:
            self._setup(sb, '{"x":{"animation":"breathe","rgb":[0,0,0]}}')
            errs, _ = sb.validate("good")
            self.assertTrue(any("period" in e for e in errs))

    def test_key_invalid_format_rejected(self):
        for bad_key in ("Idle", "idle state", "-idle", "1idle"):
            with self.subTest(bad_key=bad_key):
                with _Sandbox() as sb:
                    self._setup(sb, f'{{"{bad_key}":{{"animation":"off"}}}}')
                    errs, _ = sb.validate("good")
                    self.assertTrue(any("must match" in e for e in errs),
                                    f"expected format error for key {bad_key!r}")

    def test_key_underscore_in_middle_ok(self):
        with _Sandbox() as sb:
            self._setup(sb, '{"my_state":{"animation":"off"}}')
            errs, _ = sb.validate("good")
            self.assertEqual(errs, [])


class ValidateExecutableTest(unittest.TestCase):
    """Both run and hook obey the same contract (executable + shebang + --help)."""

    def test_missing_executable_and_states_rejected(self):
        with _Sandbox() as sb:
            os.makedirs(os.path.join(sb.root, "empty"))
            # No README either, so two errors expected.
            errs, _ = sb.validate("empty")
            joined = " | ".join(errs)
            self.assertIn("must have at least one", joined)
            self.assertIn("README.md required", joined)

    def test_run_without_shebang_rejected(self):
        with _Sandbox() as sb:
            sb.write("r", "run", "print('hi')\n", mode=0o755)
            sb.write("r", "README.md", "# r")
            errs, _ = sb.validate("r")
            self.assertTrue(any("shebang" in e for e in errs))

    def test_run_not_executable_rejected(self):
        with _Sandbox() as sb:
            sb.write("r", "run", "#!/bin/sh\nexit 0\n", mode=0o644)
            sb.write("r", "README.md", "# r")
            errs, _ = sb.validate("r")
            self.assertTrue(any("not executable" in e for e in errs))

    def test_hook_without_shebang_rejected(self):
        with _Sandbox() as sb:
            sb.write("h", "hook", "print('hi')\n", mode=0o755)
            sb.write("h", "README.md", "# h")
            errs, _ = sb.validate("h")
            self.assertTrue(any("hook" in e and "shebang" in e for e in errs))

    def test_run_and_hook_together_forbidden(self):
        with _Sandbox() as sb:
            sb.write("both", "run", "#!/bin/sh\nexit 0\n", mode=0o755)
            sb.write("both", "hook", "#!/bin/sh\nexit 0\n", mode=0o755)
            sb.write("both", "README.md", "# both")
            errs, _ = sb.validate("both")
            self.assertTrue(any("forbidden" in e for e in errs),
                            f"expected 'forbidden' in {errs}")


class ValidateManifestTest(unittest.TestCase):
    def test_invalid_json_rejected(self):
        with _Sandbox() as sb:
            sb.write("m", "integration.json", "{not json")
            sb.write("m", "README.md", "# m")
            errs, _ = sb.validate("m")
            self.assertTrue(any("integration.json" in e and "invalid JSON" in e
                                for e in errs))

    def test_wrong_type_field_rejected(self):
        with _Sandbox() as sb:
            sb.write("m", "integration.json", '{"description": 123}')
            sb.write("m", "README.md", "# m")
            errs, _ = sb.validate("m")
            self.assertTrue(any("must be string" in e for e in errs))

    def test_unknown_field_rejected(self):
        with _Sandbox() as sb:
            sb.write("m", "integration.json", '{"version": "1.0"}')
            sb.write("m", "README.md", "# m")
            errs, _ = sb.validate("m")
            self.assertTrue(any("unknown field" in e for e in errs))

    def test_custom_run_filename_resolves(self):
        """integration.json {"run": "poller.py"} should pick up poller.py."""
        with _Sandbox() as sb:
            sb.write("g", "integration.json",
                     '{"run": "poller.py", "description": "x"}')
            sb.write("g", "poller.py",
                     "#!/usr/bin/env python3\nimport sys; sys.exit(0)\n",
                     mode=0o755)
            sb.write("g", "README.md", "# g")
            errs, _ = sb.validate("g")
            # If manifest resolution worked, no "missing" errors about `run`.
            self.assertEqual(errs, [], f"unexpected errors: {errs}")


class ValidateReadmeTest(unittest.TestCase):
    def test_missing_readme_is_hard_error(self):
        with _Sandbox() as sb:
            sb.write("ok", "states.json", '{"x":{"animation":"off"}}')
            errs, _ = sb.validate("ok")
            self.assertTrue(any("README.md required" in e for e in errs))

    def test_present_readme_silences_error(self):
        with _Sandbox() as sb:
            sb.write("ok", "states.json", '{"x":{"animation":"off"}}')
            sb.write("ok", "README.md", "# ok")
            errs, _ = sb.validate("ok")
            self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
