"""Subcommand + integration dispatch tests.

Tests cli.main()'s dispatch logic without forking subprocesses for action
integrations — the dispatch helper is patched to capture invocations.

Covers:
- Built-in subcommand routing (REGISTRY)
- Integration state-lookup precedence (`led gitlab running` → state, not run)
- Integration run/hook dispatch via subprocess (mocked)
- Dispatch guards: cross-integration + recursion (STATUS_LED_INTEGRATION_ACTIVE)
- Manifest discovery (load_manifest, list_integration_names)
"""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from unittest import mock

from status_led import cli, commands
from status_led.manifest import load_manifest, list_integration_names


class RegistryTest(unittest.TestCase):
    """REGISTRY exposes built-in subcommands."""

    EXPECTED = {"service", "smoke-test", "status", "upload-firmware",
                "validate-integrations", "raw", "daemon"}

    def test_registry_includes_all_builtins(self):
        for name in self.EXPECTED:
            self.assertIn(name, commands.REGISTRY,
                          f"{name!r} missing from REGISTRY")

    def test_registry_values_are_callables(self):
        for name, fn in commands.REGISTRY.items():
            self.assertTrue(callable(fn), f"{name} handler not callable")


class DispatchTest(unittest.TestCase):
    """main() routes argv[0] correctly."""

    def _dispatch(self, argv):
        return cli.main(argv)

    def test_builtin_subcommand_called(self):
        calls = []

        def fake_run(argv):
            calls.append(argv)
            return 0

        with mock.patch.dict(commands.REGISTRY, {"mock-builtin": fake_run}):
            rc = self._dispatch(["mock-builtin", "x", "y"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [["x", "y"]])

    def test_state_argparse_falls_through_when_first_arg_is_flag(self):
        # --help → argparse prints help and SystemExit(0)
        with self.assertRaises(SystemExit):
            self._dispatch(["--help"])

    def test_state_argparse_falls_through_for_unknown_first_arg(self):
        # 'definitely-not-real' is not in REGISTRY, not an integration.
        # Falls through to state argparse → ValueError caught → sys.exit(0).
        with self.assertRaises(SystemExit) as cm:
            self._dispatch(["definitely-not-real"])
        self.assertEqual(cm.exception.code, 0)


class ManifestDiscoveryTest(unittest.TestCase):
    """load_manifest and list_integration_names against the bundled repo."""

    def test_claude_manifest_has_hook(self):
        m = load_manifest("claude")
        self.assertIsNotNone(m)
        self.assertIsNotNone(m.hook_file)
        self.assertIsNotNone(m.states_file)
        self.assertIsNone(m.run_file)
        # integration.json declares hook: "hook.py"
        self.assertTrue(m.hook_file.name == "hook.py",
                        f"expected hook.py, got {m.hook_file.name}")

    def test_gitlab_manifest_has_run(self):
        m = load_manifest("gitlab")
        self.assertIsNotNone(m)
        self.assertIsNotNone(m.run_file)
        self.assertIsNotNone(m.states_file)
        self.assertIsNone(m.hook_file)
        # run_file should be poller.py per integration.json
        self.assertTrue(m.run_file.name.endswith("poller.py"),
                        f"expected poller.py, got {m.run_file}")

    def test_timer_manifest_has_run(self):
        m = load_manifest("timer")
        self.assertIsNotNone(m)
        self.assertIsNotNone(m.run_file)
        self.assertIsNone(m.states_file)
        self.assertIsNone(m.hook_file)

    def test_unknown_returns_none(self):
        self.assertIsNone(load_manifest("does-not-exist"))

    def test_list_finds_known_integrations(self):
        names = list_integration_names()
        self.assertIn("claude", names)
        self.assertIn("gitlab", names)
        self.assertIn("timer", names)


class IntegrationDispatchTest(unittest.TestCase):
    """Action dispatch via subprocess.run — verified by mocking _dispatch_action."""

    def test_action_dispatch_called_for_bare_integration(self):
        """`led gitlab` (bare) → _dispatch_action with rest=[]."""
        captured = []

        def fake_dispatch(name, manifest, rest):
            captured.append((name, manifest.name, rest))
            return 0

        with mock.patch.object(cli, "_dispatch_action", fake_dispatch):
            rc = cli.main(["gitlab"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured, [("gitlab", "gitlab", [])])

    def test_action_dispatch_called_for_flag_args(self):
        """`led gitlab --interval 30` → _dispatch_action with the flag."""
        captured = []

        def fake_dispatch(name, manifest, rest):
            captured.append((name, manifest.name, rest))
            return 0

        with mock.patch.object(cli, "_dispatch_action", fake_dispatch):
            rc = cli.main(["gitlab", "--interval", "30"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured, [("gitlab", "gitlab", ["--interval", "30"])])

    def test_state_lookup_skips_action_dispatch(self):
        """`led gitlab running` is a state lookup, not an action."""
        captured = []

        def fake_dispatch(name, manifest, rest):
            captured.append((name, manifest.name, rest))
            return 0

        with mock.patch.object(cli, "_dispatch_action", fake_dispatch):
            # state lookup → _state_argparse → daemon unreachable → exit 0
            try:
                cli.main(["gitlab", "running"])
            except SystemExit:
                pass
        self.assertEqual(captured, [], "state lookup must not dispatch action")


class DispatchGuardTest(unittest.TestCase):
    """STATUS_LED_INTEGRATION_ACTIVE guards enforce isolation + recursion rules."""

    def test_cross_integration_invocation_refused(self):
        """Inside `claude` integration, `led gitlab ...` is forbidden."""
        with mock.patch.dict(os.environ, {"STATUS_LED_INTEGRATION_ACTIVE": "claude"}):
            rc = cli.main(["gitlab"])
        self.assertEqual(rc, 1)

    def test_cross_integration_state_lookup_refused(self):
        """Even state lookup into another integration is forbidden."""
        with mock.patch.dict(os.environ, {"STATUS_LED_INTEGRATION_ACTIVE": "claude"}):
            # State lookup will fail before resolving — _state_argparse may
            # still run, but guard fires first. Verify rc != 0.
            rc = cli.main(["gitlab", "running"])
        self.assertEqual(rc, 1)

    def test_same_integration_bare_recursion_refused(self):
        """Inside `claude`, bare `led claude` would recurse — forbidden."""
        with mock.patch.dict(os.environ, {"STATUS_LED_INTEGRATION_ACTIVE": "claude"}):
            rc = cli.main(["claude"])
        self.assertEqual(rc, 1)

    def test_same_integration_state_lookup_allowed(self):
        """Inside `claude`, `led claude idle` (state lookup) is allowed —
        this is the integration's own internal pattern."""
        with mock.patch.dict(os.environ, {"STATUS_LED_INTEGRATION_ACTIVE": "claude"}):
            # Falls through to _state_argparse → daemon unreachable → exit 0
            try:
                rc = cli.main(["claude", "idle"])
            except SystemExit as e:
                rc = e.code
        self.assertEqual(rc, 0)

    def test_subcommands_bypass_guard(self):
        """Subcommands (led raw, led status) are not integrations — always OK."""
        calls = []

        def fake_run(argv):
            calls.append(argv)
            return 0

        with mock.patch.dict(commands.REGISTRY, {"raw": fake_run}), \
             mock.patch.dict(os.environ, {"STATUS_LED_INTEGRATION_ACTIVE": "claude"}):
            cli.main(["raw", "breathe", "--rgb", "0,0,255", "--period", "1000"])
        self.assertEqual(calls, [["breathe", "--rgb", "0,0,255", "--period", "1000"]])


if __name__ == "__main__":
    unittest.main()
