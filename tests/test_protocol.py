"""Protocol line builder tests.

Verifies build_state_line, build_clear_line, build_transient_line — the
CLI → daemon wire format. Pure functions; no socket/serial involved.
"""
from __future__ import annotations

import unittest

from status_led import protocol


class ProtocolLineBuildersTest(unittest.TestCase):
    """The CLI → daemon protocol: STATE/CLEAR/TRANSIENT."""

    def test_state_line(self):
        self.assertEqual(
            protocol.build_state_line("abc", 60, "scanner 90 0 170 1600 100"),
            "STATE abc 60 scanner 90 0 170 1600 100")

    def test_clear_line(self):
        self.assertEqual(protocol.build_clear_line("abc"), "CLEAR abc")

    def test_transient_line(self):
        self.assertEqual(
            protocol.build_transient_line(3000, "blink 180 0 0 300 100"),
            "TRANSIENT 3000 blink 180 0 0 300 100")

    def test_state_line_preserves_strobe_as_opaque_remainder(self):
        # 9-token wire-line must survive unchanged.
        line = protocol.build_state_line("x", 100, "strobe 180 0 0 0 0 180 300 100")
        self.assertTrue(line.endswith("strobe 180 0 0 0 0 180 300 100"))


if __name__ == "__main__":
    unittest.main()
