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

    def test_pulse_full(self):
        self.assertEqual(
            led_cli.build_wire_line("pulse", rgb=(255, 128, 0), period=1200),
            "pulse 255 128 0 1200 100")

    def test_pulse_requires_period(self):
        with self.assertRaises(ValueError):
            led_cli.build_wire_line("pulse", rgb=(255, 128, 0))

    def test_sparkle_full(self):
        self.assertEqual(
            led_cli.build_wire_line("sparkle", rgb=(0, 220, 0), period=800, brightness=80),
            "sparkle 0 220 0 800 80")

    def test_sparkle_requires_period(self):
        with self.assertRaises(ValueError):
            led_cli.build_wire_line("sparkle", rgb=(0, 220, 0))

    def test_heartbeat_full(self):
        self.assertEqual(
            led_cli.build_wire_line("heartbeat", rgb=(220, 0, 0), period=1000),
            "heartbeat 220 0 0 1000 100")

    def test_heartbeat_requires_period(self):
        with self.assertRaises(ValueError):
            led_cli.build_wire_line("heartbeat", rgb=(220, 0, 0))

    def test_bounce_full(self):
        self.assertEqual(
            led_cli.build_wire_line("bounce", rgb=(0, 100, 200), period=1400),
            "bounce 0 100 200 1400 100")

    def test_bounce_requires_period(self):
        with self.assertRaises(ValueError):
            led_cli.build_wire_line("bounce", rgb=(0, 100, 200))

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

    def test_pulse_entry(self):
        wire = led_cli.build_command_from_entry(
            {"animation": "pulse", "rgb": [255, 128, 0], "period": 1200, "brightness": 100},
            context="test.pulse")
        self.assertEqual(wire, "pulse 255 128 0 1200 100")

    def test_sparkle_entry(self):
        wire = led_cli.build_command_from_entry(
            {"animation": "sparkle", "rgb": [0, 220, 0], "period": 800, "brightness": 80},
            context="test.sparkle")
        self.assertEqual(wire, "sparkle 0 220 0 800 80")

    def test_heartbeat_entry(self):
        wire = led_cli.build_command_from_entry(
            {"animation": "heartbeat", "rgb": [220, 0, 0], "period": 1000, "brightness": 100},
            context="test.heartbeat")
        self.assertEqual(wire, "heartbeat 220 0 0 1000 100")

    def test_bounce_entry(self):
        wire = led_cli.build_command_from_entry(
            {"animation": "bounce", "rgb": [0, 100, 200], "period": 1400, "brightness": 100},
            context="test.bounce")
        self.assertEqual(wire, "bounce 0 100 200 1400 100")

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

    def test_raw_pulse(self):
        self.assertEqual(
            led_cli.build_raw_command("pulse", (255, 128, 0), 1200, 100),
            "pulse 255 128 0 1200 100")

    def test_raw_sparkle(self):
        self.assertEqual(
            led_cli.build_raw_command("sparkle", (0, 220, 0), 800, 80),
            "sparkle 0 220 0 800 80")

    def test_raw_heartbeat(self):
        self.assertEqual(
            led_cli.build_raw_command("heartbeat", (220, 0, 0), 1000, 100),
            "heartbeat 220 0 0 1000 100")

    def test_raw_bounce(self):
        self.assertEqual(
            led_cli.build_raw_command("bounce", (0, 100, 200), 1400, 100),
            "bounce 0 100 200 1400 100")

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


class FormatStatusTest(unittest.TestCase):
    """format_status() renders the daemon's STATUS response for `led --status`.
    Output should be stable and human-scannable — sessions top-to-bottom by
    priority, transient state on its own line, serial connectivity clear.
    """

    def test_empty_state(self):
        out = led_cli.format_status({
            "current_output": None,
            "sessions": [],
            "transient": None,
            "serial_connected": True,
            "serial_port": "/dev/ttyUSB0",
        })
        self.assertIn("LED output: off", out)
        self.assertIn("Serial: /dev/ttyUSB0 (connected)", out)
        self.assertIn("Sessions: (none)", out)
        self.assertIn("Transient: (none)", out)

    def test_with_sessions_and_output(self):
        out = led_cli.format_status({
            "current_output": "scanner 90 0 170 1600 100",
            "sessions": [
                {"sid": "claude-abc", "priority": 60, "wire": "scanner 90 0 170 1600 100", "age_s": 5.2},
                {"sid": "gitlab-42", "priority": 20, "wire": "fill 0 220 0 3000 100", "age_s": 30.1},
            ],
            "transient": None,
            "serial_connected": True,
            "serial_port": "/dev/ttyUSB0",
        })
        self.assertIn("LED output: scanner 90 0 170 1600 100", out)
        self.assertIn("Sessions (2):", out)
        self.assertIn("claude-abc", out)
        self.assertIn("pri=60", out)
        self.assertIn("gitlab-42", out)
        self.assertIn("pri=20", out)

    def test_disconnected_serial(self):
        out = led_cli.format_status({
            "current_output": None,
            "sessions": [],
            "transient": None,
            "serial_connected": False,
            "serial_port": None,
        })
        self.assertIn("Serial: (DISCONNECTED)", out)

    def test_transient_shown_with_expiry(self):
        out = led_cli.format_status({
            "current_output": "blink 180 0 0 300 100",
            "sessions": [],
            "transient": {"wire": "blink 180 0 0 300 100", "expires_in_s": 2.5},
            "serial_connected": True,
            "serial_port": "/dev/ttyUSB0",
        })
        self.assertIn("Transient: expires in 2.5s", out)
        self.assertIn("blink 180 0 0 300 100", out)

    def test_current_output_none_shows_off(self):
        out = led_cli.format_status({
            "current_output": None,
            "sessions": [],
            "transient": None,
            "serial_connected": True,
            "serial_port": "/dev/ttyUSB0",
        })
        # None current_output means "off" visually
        self.assertIn("LED output: off", out)
        self.assertNotIn("LED output: None", out)


if __name__ == "__main__":
    unittest.main()
