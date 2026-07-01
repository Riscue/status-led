"""CLI-side tests: wire-line builder, JSON profile resolution, protocol line
builders (STATE/CLEAR/TRANSIENT), TTL env var.

Run: python3 -m unittest tests.test_cli   (from repo root)
  or: python3 tests/test_cli.py
"""

from __future__ import annotations

import os
import sys
import unittest

# Make driver/ importable when running from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "driver"))

import led_cli


class BuildWireLineTest(unittest.TestCase):
    """The unified wire-line builder is the single source of truth for the
    firmware command format. Every code path (--state, --raw) goes through it.
    """

    def test_off_ignores_other_args(self):
        self.assertEqual(led_cli.build_wire_line("off", rgb=(1, 2, 3), period=999),
                         "off")

    def test_off_works_with_no_optional_args(self):
        self.assertEqual(led_cli.build_wire_line("off"), "off")

    def test_invalid_animation_raises(self):
        with self.assertRaises(ValueError):
            led_cli.build_wire_line("frob", rgb=(0, 0, 0))

    def test_missing_rgb_raises(self):
        with self.assertRaises(ValueError):
            led_cli.build_wire_line("solid")

    def test_solid_no_period(self):
        self.assertEqual(led_cli.build_wire_line("solid", rgb=(0, 0, 255), brightness=30),
                         "solid 0 0 255 30")

    def test_solid_default_brightness(self):
        self.assertEqual(led_cli.build_wire_line("solid", rgb=(0, 0, 255)),
                         "solid 0 0 255 100")

    def test_breathe_with_period(self):
        self.assertEqual(
            led_cli.build_wire_line("breathe", rgb=(0, 50, 220), period=3500),
            "breathe 0 50 220 3500 100")

    def test_period_below_minimum_raises(self):
        with self.assertRaises(ValueError):
            led_cli.build_wire_line("breathe", rgb=(0, 0, 0), period=10)

    def test_strobe_requires_rgb2(self):
        with self.assertRaises(ValueError):
            led_cli.build_wire_line("strobe", rgb=(180, 0, 0), period=300)

    def test_strobe_requires_period(self):
        with self.assertRaises(ValueError):
            led_cli.build_wire_line("strobe", rgb=(180, 0, 0), rgb2=(0, 0, 180))

    def test_strobe_full(self):
        self.assertEqual(
            led_cli.build_wire_line("strobe", rgb=(180, 0, 0), rgb2=(0, 0, 180),
                                    period=300, brightness=100),
            "strobe 180 0 0 0 0 180 300 100")

    def test_level_requires_level_arg(self):
        with self.assertRaises(ValueError):
            led_cli.build_wire_line("level", rgb=(0, 220, 0))

    def test_level_full(self):
        self.assertEqual(
            led_cli.build_wire_line("level", rgb=(0, 220, 0), level=50),
            "level 0 220 0 50 100")

    def test_rgb_clamping(self):
        # Above 255 clamps
        self.assertEqual(led_cli.build_wire_line("solid", rgb=(999, -5, 128)),
                         "solid 255 0 128 100")

    def test_brightness_clamping(self):
        self.assertEqual(led_cli.build_wire_line("solid", rgb=(0, 0, 0), brightness=200),
                         "solid 0 0 0 100")
        self.assertEqual(led_cli.build_wire_line("solid", rgb=(0, 0, 0), brightness=-5),
                         "solid 0 0 0 0")


class BuildCommandFromEntryTest(unittest.TestCase):
    """JSON entries get per-animation field extraction, then delegate to
    build_wire_line. Context is preserved in error messages.
    """

    def test_breathe_entry(self):
        wire = led_cli.build_command_from_entry(
            {"animation": "breathe", "rgb": [0, 50, 220], "period": 3500, "brightness": 100},
            context="test.breathe")
        self.assertEqual(wire, "breathe 0 50 220 3500 100")

    def test_off_entry_minimal(self):
        wire = led_cli.build_command_from_entry({"animation": "off"}, context="test.off")
        self.assertEqual(wire, "off")

    def test_strobe_entry(self):
        wire = led_cli.build_command_from_entry(
            {"animation": "strobe", "rgb": [180, 0, 0], "rgb2": [0, 0, 180],
             "period": 300, "brightness": 100},
            context="test.strobe")
        self.assertEqual(wire, "strobe 180 0 0 0 0 180 300 100")

    def test_level_entry(self):
        wire = led_cli.build_command_from_entry(
            {"animation": "level", "rgb": [0, 220, 0], "level": 50, "brightness": 100},
            context="test.level")
        self.assertEqual(wire, "level 0 220 0 50 100")

    def test_invalid_animation_includes_context(self):
        with self.assertRaises(ValueError) as cm:
            led_cli.build_command_from_entry({"animation": "frob"}, context="myprofile.foo")
        self.assertIn("myprofile.foo", str(cm.exception))

    def test_non_dict_entry_raises(self):
        with self.assertRaises(ValueError):
            led_cli.build_command_from_entry("not a dict", context="test")

    def test_missing_period_wraps_error_with_context(self):
        with self.assertRaises(ValueError) as cm:
            led_cli.build_command_from_entry(
                {"animation": "breathe", "rgb": [0, 0, 0], "brightness": 100},  # no period
                context="myprofile.breathe")
        self.assertIn("myprofile.breathe", str(cm.exception))
        self.assertIn("period", str(cm.exception))


class BuildRawCommandTest(unittest.TestCase):
    """The --raw path delegates to build_wire_line."""

    def test_raw_strobe(self):
        wire = led_cli.build_raw_command("strobe", (180, 0, 0), 300, 100,
                                         rgb2=(0, 0, 180))
        self.assertEqual(wire, "strobe 180 0 0 0 0 180 300 100")

    def test_raw_off(self):
        self.assertEqual(led_cli.build_raw_command("off", None, None, 100), "off")


class ResolveStateFullTest(unittest.TestCase):
    """End-to-end: PROFILE.KEY → (wire_line, priority) using real JSON profiles."""

    def test_claude_idle(self):
        wire, priority = led_cli.resolve_state_full("claude.idle")
        self.assertEqual(wire, "breathe 0 50 220 3500 100")
        self.assertEqual(priority, 10)

    def test_claude_error_has_highest_priority(self):
        wire, priority = led_cli.resolve_state_full("claude.error")
        self.assertEqual(priority, 100)

    def test_claude_waiting_beats_thinking(self):
        # Verifies the user's "input bekleniyorsa idle a dönmesin" requirement.
        _, waiting = led_cli.resolve_state_full("claude.waiting")
        _, thinking = led_cli.resolve_state_full("claude.thinking")
        self.assertGreater(waiting, thinking)

    def test_default_on_has_no_priority_defaults_to_zero(self):
        # default.json doesn't include priority; should default to 0.
        _, priority = led_cli.resolve_state_full("default.on")
        self.assertEqual(priority, 0)

    def test_invalid_profile_raises(self):
        with self.assertRaises(ValueError):
            led_cli.resolve_state_full("doesnotexist.key")

    def test_invalid_state_format_raises(self):
        with self.assertRaises(ValueError):
            led_cli.resolve_state_full("noseparator")


class ProtocolLineBuildersTest(unittest.TestCase):
    """The CLI → daemon protocol: STATE/CLEAR/TRANSIENT."""

    def test_state_line(self):
        self.assertEqual(
            led_cli.build_state_line("abc", 60, "scanner 90 0 170 1600 100"),
            "STATE abc 60 scanner 90 0 170 1600 100")

    def test_clear_line(self):
        self.assertEqual(led_cli.build_clear_line("abc"), "CLEAR abc")

    def test_transient_line(self):
        self.assertEqual(
            led_cli.build_transient_line(3000, "blink 180 0 0 300 100"),
            "TRANSIENT 3000 blink 180 0 0 300 100")

    def test_state_line_preserves_strobe_as_opaque_remainder(self):
        # 9-token wire-line must survive unchanged.
        line = led_cli.build_state_line("x", 100, "strobe 180 0 0 0 0 180 300 100")
        self.assertTrue(line.endswith("strobe 180 0 0 0 0 180 300 100"))


class TtlEnvTest(unittest.TestCase):
    def setUp(self):
        # Snapshot so we can restore
        self._had = "STATUS_LED_TRANSIENT_TTL_MS" in os.environ
        self._val = os.environ.get("STATUS_LED_TRANSIENT_TTL_MS")

    def tearDown(self):
        if self._had:
            os.environ["STATUS_LED_TRANSIENT_TTL_MS"] = self._val
        else:
            os.environ.pop("STATUS_LED_TRANSIENT_TTL_MS", None)

    def test_default_ttl(self):
        ttl = int(os.environ.get("STATUS_LED_TRANSIENT_TTL_MS",
                                 led_cli.DEFAULT_TRANSIENT_TTL_MS))
        self.assertEqual(ttl, 3000)

    def test_env_override(self):
        os.environ["STATUS_LED_TRANSIENT_TTL_MS"] = "5000"
        ttl = int(os.environ.get("STATUS_LED_TRANSIENT_TTL_MS",
                                 led_cli.DEFAULT_TRANSIENT_TTL_MS))
        self.assertEqual(ttl, 5000)


class EndToEndLineConstructionTest(unittest.TestCase):
    """Simulate what main() does for representative invocations."""

    def test_session_mode(self):
        # led --session abc --state claude.error
        wire, priority = led_cli.resolve_state_full("claude.error")
        line = led_cli.build_state_line("abc", priority, wire)
        self.assertEqual(line, "STATE abc 100 blink 180 0 0 300 100")

    def test_transient_mode(self):
        # led --ttl 5000 --state claude.error
        wire, _ = led_cli.resolve_state_full("claude.error")
        line = led_cli.build_transient_line(5000, wire)
        self.assertEqual(line, "TRANSIENT 5000 blink 180 0 0 300 100")

    def test_clear_mode(self):
        # led --end-session abc
        line = led_cli.build_clear_line("abc")
        self.assertEqual(line, "CLEAR abc")


if __name__ == "__main__":
    unittest.main()
