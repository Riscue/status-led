"""Profile loading tests. JSON → wire-line resolution.

Tests build_command_from_entry (the JSON entry → wire-line builder) and
resolve_state_full (`<profile> <key>` → (wire, priority)). The integrations/
fixture is real (claude, gitlab, default) — pinned via tests/__init__.py
to the bundled package data.
"""
from __future__ import annotations

import unittest

from status_led import profiles


class BuildCommandFromEntryTest(unittest.TestCase):
    """JSON entries get per-animation field extraction, then delegate to
    wire.build_wire_line. Context is preserved in error messages.
    """

    def test_breathe_entry(self):
        wire = profiles.build_command_from_entry(
            {"animation": "breathe", "rgb": [0, 50, 220], "period": 3500, "brightness": 100},
            context="test breathe")
        self.assertEqual(wire, "breathe 0 50 220 3500 100")

    def test_off_entry_minimal(self):
        wire = profiles.build_command_from_entry({"animation": "off"}, context="test off")
        self.assertEqual(wire, "off")

    def test_strobe_entry(self):
        wire = profiles.build_command_from_entry(
            {"animation": "strobe", "rgb": [180, 0, 0], "rgb2": [0, 0, 180],
             "period": 300, "brightness": 100},
            context="test strobe")
        self.assertEqual(wire, "strobe 180 0 0 0 0 180 300 100")

    def test_pulse_entry(self):
        wire = profiles.build_command_from_entry(
            {"animation": "pulse", "rgb": [255, 128, 0], "period": 1200, "brightness": 100},
            context="test pulse")
        self.assertEqual(wire, "pulse 255 128 0 1200 100")

    def test_sparkle_entry(self):
        wire = profiles.build_command_from_entry(
            {"animation": "sparkle", "rgb": [0, 220, 0], "period": 800, "brightness": 80},
            context="test sparkle")
        self.assertEqual(wire, "sparkle 0 220 0 800 80")

    def test_heartbeat_entry(self):
        wire = profiles.build_command_from_entry(
            {"animation": "heartbeat", "rgb": [220, 0, 0], "period": 1000, "brightness": 100},
            context="test heartbeat")
        self.assertEqual(wire, "heartbeat 220 0 0 1000 100")

    def test_bounce_entry(self):
        wire = profiles.build_command_from_entry(
            {"animation": "bounce", "rgb": [0, 100, 200], "period": 1400, "brightness": 100},
            context="test bounce")
        self.assertEqual(wire, "bounce 0 100 200 1400 100")

    def test_level_entry(self):
        wire = profiles.build_command_from_entry(
            {"animation": "level", "rgb": [0, 220, 0], "level": 50, "brightness": 100},
            context="test level")
        self.assertEqual(wire, "level 0 220 0 50 100")

    def test_invalid_animation_includes_context(self):
        with self.assertRaises(ValueError) as cm:
            profiles.build_command_from_entry({"animation": "frob"}, context="myprofile foo")
        self.assertIn("myprofile foo", str(cm.exception))

    def test_non_dict_entry_raises(self):
        with self.assertRaises(ValueError):
            profiles.build_command_from_entry("not a dict", context="test")

    def test_missing_period_wraps_error_with_context(self):
        with self.assertRaises(ValueError) as cm:
            profiles.build_command_from_entry(
                {"animation": "breathe", "rgb": [0, 0, 0], "brightness": 100},  # no period
                context="myprofile breathe")
        self.assertIn("myprofile breathe", str(cm.exception))
        self.assertIn("period", str(cm.exception))


class ResolveStateFullTest(unittest.TestCase):
    """End-to-end: `<profile> <key>` → (wire_line, priority) using real JSON profiles."""

    def test_claude_idle(self):
        wire, priority = profiles.resolve_state_full("claude", "idle")
        self.assertEqual(wire, "breathe 0 50 220 3500 100")
        self.assertEqual(priority, 10)

    def test_claude_error_has_highest_priority(self):
        wire, priority = profiles.resolve_state_full("claude", "error")
        self.assertEqual(priority, 100)

    def test_claude_waiting_beats_thinking(self):
        _, waiting = profiles.resolve_state_full("claude", "waiting")
        _, thinking = profiles.resolve_state_full("claude", "thinking")
        self.assertGreater(waiting, thinking)

    def test_default_on_has_no_priority_defaults_to_zero(self):
        _, priority = profiles.resolve_state_full("default", "on")
        self.assertEqual(priority, 0)

    def test_invalid_profile_raises(self):
        with self.assertRaises(ValueError):
            profiles.resolve_state_full("doesnotexist", "key")

    def test_invalid_key_raises(self):
        with self.assertRaises(ValueError):
            profiles.resolve_state_full("claude", "nosuchkey")


class IsStateLookupTest(unittest.TestCase):
    """is_state_lookup distinguishes state lookups from action args."""

    def test_state_only_integration_valid_key(self):
        # claude has states.json with `idle`. Action dispatch shouldn't grab it.
        self.assertTrue(profiles.is_state_lookup("claude", "idle"))

    def test_state_only_integration_invalid_key(self):
        self.assertFalse(profiles.is_state_lookup("claude", "definitely-not-a-state"))

    def test_hybrid_integration_valid_state_key(self):
        # gitlab has both states.json and run. `running` is a state key.
        self.assertTrue(profiles.is_state_lookup("gitlab", "running"))

    def test_hybrid_integration_invalid_key(self):
        # `--interval` is not a state key — should fall to action dispatch.
        self.assertFalse(profiles.is_state_lookup("gitlab", "--interval"))

    def test_action_only_integration(self):
        # timer has no states.json, so any key returns False.
        self.assertFalse(profiles.is_state_lookup("timer", "5m"))

    def test_unknown_profile(self):
        self.assertFalse(profiles.is_state_lookup("does-not-exist", "anything"))


if __name__ == "__main__":
    unittest.main()
