"""`led raw` subcommand tests — parse_rgb + animation flag handling."""
from __future__ import annotations

import unittest

from status_led.commands import raw


class ParseRgbTest(unittest.TestCase):
    """parse_rgb parses 'r,g,b' strings (--rgb/--rgb2 flag values). JSON
    profile values use profiles.coerce_rgb_from_json instead.
    """

    def test_three_values(self):
        self.assertEqual(raw.parse_rgb("0,50,220"), (0, 50, 220))

    def test_single_value_grayscale(self):
        self.assertEqual(raw.parse_rgb("128"), (128, 128, 128))

    def test_clamps_out_of_range(self):
        self.assertEqual(raw.parse_rgb("999,-5,128"), (255, 0, 128))

    def test_invalid_int_raises(self):
        with self.assertRaises(ValueError):
            raw.parse_rgb("a,b,c")

    def test_wrong_count_raises(self):
        with self.assertRaises(ValueError):
            raw.parse_rgb("1,2,3,4")


if __name__ == "__main__":
    unittest.main()
