"""CLI-specific tests: --ttl env handling, state-ref resolution, end-to-end
line construction.

Wire-format tests live in test_wire.py; profile resolution tests live in
test_profiles.py; protocol-line builders live in test_protocol.py;
format_status tests live in test_commands_status.py; raw/parse_rgb tests
live in test_commands_raw.py.
"""
from __future__ import annotations

import os
import unittest

from status_led import cli
from status_led import protocol


class TtlEnvTest(unittest.TestCase):
    def setUp(self):
        self._had = "STATUS_LED_TTL_MS" in os.environ
        self._val = os.environ.get("STATUS_LED_TTL_MS")

    def tearDown(self):
        if self._had:
            os.environ["STATUS_LED_TTL_MS"] = self._val
        else:
            os.environ.pop("STATUS_LED_TTL_MS", None)

    def test_default_ttl(self):
        # No explicit ttl, no env var → resolves to default (3000 ms).
        ttl = protocol.resolve_ttl_ms(None)
        self.assertEqual(ttl, 3000)

    def test_env_override(self):
        os.environ["STATUS_LED_TTL_MS"] = "5000"
        ttl = protocol.resolve_ttl_ms(None)
        self.assertEqual(ttl, 5000)

    def test_explicit_overrides_env(self):
        os.environ["STATUS_LED_TTL_MS"] = "5000"
        ttl = protocol.resolve_ttl_ms(1000)
        self.assertEqual(ttl, 1000)


class StateRefResolutionTest(unittest.TestCase):
    """_resolve_state_ref combines positional keys into a (profile, key) pair."""

    def test_two_tokens(self):
        self.assertEqual(cli._resolve_state_ref(["claude", "idle"]),
                         ("claude", "idle"))

    def test_default_shorthand(self):
        self.assertEqual(cli._resolve_state_ref(["on"]),
                         ("default", "on"))
        self.assertEqual(cli._resolve_state_ref(["off"]),
                         ("default", "off"))

    def test_too_many_positionals_raises(self):
        with self.assertRaises(ValueError):
            cli._resolve_state_ref(["a", "b", "c"])

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            cli._resolve_state_ref([])


class EndToEndLineConstructionTest(unittest.TestCase):
    """Simulate what main() does for representative invocations — wire the
    pieces together to confirm the assembled protocol line matches the
    expected on-wire format.
    """

    def test_session_mode(self):
        # led --session abc claude error
        from status_led.profiles import resolve_state_full
        wire, priority = resolve_state_full("claude", "error")
        line = protocol.build_state_line("abc", priority, wire)
        self.assertEqual(line, "STATE abc 100 blink 180 0 0 300 100")

    def test_transient_mode(self):
        # led --ttl 5000 claude error
        from status_led.profiles import resolve_state_full
        wire, _ = resolve_state_full("claude", "error")
        line = protocol.build_transient_line(5000, wire)
        self.assertEqual(line, "TRANSIENT 5000 blink 180 0 0 300 100")

    def test_clear_mode(self):
        # led --end-session abc
        line = protocol.build_clear_line("abc")
        self.assertEqual(line, "CLEAR abc")


if __name__ == "__main__":
    unittest.main()
