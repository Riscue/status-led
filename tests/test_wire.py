"""Wire-line format tests. Pure — no I/O.

Tests build_wire_line for every animation. The CLI's `led raw` path and
the JSON profile path both funnel through build_wire_line, so this is
the load-bearing test for firmware compat.
"""
from __future__ import annotations

import unittest

from status_led import wire


class BuildWireLineTest(unittest.TestCase):
    def test_off_ignores_other_args(self):
        self.assertEqual(wire.build_wire_line("off", rgb=(1, 2, 3), period=999),
                         "off")

    def test_off_works_with_no_optional_args(self):
        self.assertEqual(wire.build_wire_line("off"), "off")

    def test_invalid_animation_raises(self):
        with self.assertRaises(ValueError):
            wire.build_wire_line("frob", rgb=(0, 0, 0))

    def test_missing_rgb_raises(self):
        with self.assertRaises(ValueError):
            wire.build_wire_line("solid")

    def test_solid_no_period(self):
        self.assertEqual(wire.build_wire_line("solid", rgb=(0, 0, 255), brightness=30),
                         "solid 0 0 255 30")

    def test_solid_default_brightness(self):
        self.assertEqual(wire.build_wire_line("solid", rgb=(0, 0, 255)),
                         "solid 0 0 255 100")

    def test_breathe_with_period(self):
        self.assertEqual(
            wire.build_wire_line("breathe", rgb=(0, 50, 220), period=3500),
            "breathe 0 50 220 3500 100")

    def test_period_below_minimum_raises(self):
        with self.assertRaises(ValueError):
            wire.build_wire_line("breathe", rgb=(0, 0, 0), period=10)

    def test_strobe_requires_rgb2(self):
        with self.assertRaises(ValueError):
            wire.build_wire_line("strobe", rgb=(180, 0, 0), period=300)

    def test_strobe_requires_period(self):
        with self.assertRaises(ValueError):
            wire.build_wire_line("strobe", rgb=(180, 0, 0), rgb2=(0, 0, 180))

    def test_strobe_full(self):
        self.assertEqual(
            wire.build_wire_line("strobe", rgb=(180, 0, 0), rgb2=(0, 0, 180),
                                 period=300, brightness=100),
            "strobe 180 0 0 0 0 180 300 100")

    def test_pulse_full(self):
        self.assertEqual(
            wire.build_wire_line("pulse", rgb=(255, 128, 0), period=1200),
            "pulse 255 128 0 1200 100")

    def test_pulse_requires_period(self):
        with self.assertRaises(ValueError):
            wire.build_wire_line("pulse", rgb=(255, 128, 0))

    def test_sparkle_full(self):
        self.assertEqual(
            wire.build_wire_line("sparkle", rgb=(0, 220, 0), period=800, brightness=80),
            "sparkle 0 220 0 800 80")

    def test_sparkle_requires_period(self):
        with self.assertRaises(ValueError):
            wire.build_wire_line("sparkle", rgb=(0, 220, 0))

    def test_heartbeat_full(self):
        self.assertEqual(
            wire.build_wire_line("heartbeat", rgb=(220, 0, 0), period=1000),
            "heartbeat 220 0 0 1000 100")

    def test_heartbeat_requires_period(self):
        with self.assertRaises(ValueError):
            wire.build_wire_line("heartbeat", rgb=(220, 0, 0))

    def test_bounce_full(self):
        self.assertEqual(
            wire.build_wire_line("bounce", rgb=(0, 100, 200), period=1400),
            "bounce 0 100 200 1400 100")

    def test_bounce_requires_period(self):
        with self.assertRaises(ValueError):
            wire.build_wire_line("bounce", rgb=(0, 100, 200))

    def test_level_requires_level_arg(self):
        with self.assertRaises(ValueError):
            wire.build_wire_line("level", rgb=(0, 220, 0))

    def test_level_full(self):
        self.assertEqual(
            wire.build_wire_line("level", rgb=(0, 220, 0), level=50),
            "level 0 220 0 50 100")

    def test_rgb_clamping(self):
        self.assertEqual(wire.build_wire_line("solid", rgb=(999, -5, 128)),
                         "solid 255 0 128 100")

    def test_brightness_clamping(self):
        self.assertEqual(wire.build_wire_line("solid", rgb=(0, 0, 0), brightness=200),
                         "solid 0 0 0 100")
        self.assertEqual(wire.build_wire_line("solid", rgb=(0, 0, 0), brightness=-5),
                         "solid 0 0 0 0")


if __name__ == "__main__":
    unittest.main()
