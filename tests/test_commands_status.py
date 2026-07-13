"""format_status tests for the `led status` subcommand.

format_status lives in commands/status.py (single consumer is the led status
handler). These tests target it directly without going through the daemon
or socket layer.
"""
from __future__ import annotations

import unittest

from status_led.commands import status as status_cmd


class FormatStatusTest(unittest.TestCase):
    """format_status renders the daemon STATUS response.

    Output should be stable and human-scannable — sessions top-to-bottom by
    priority, transient state on its own line, serial connectivity clear.
    """

    def test_empty_state(self):
        out = status_cmd.format_status({
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
        out = status_cmd.format_status({
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
        out = status_cmd.format_status({
            "current_output": None,
            "sessions": [],
            "transient": None,
            "serial_connected": False,
            "serial_port": None,
        })
        self.assertIn("Serial: (DISCONNECTED)", out)

    def test_transient_shown_with_expiry(self):
        out = status_cmd.format_status({
            "current_output": "blink 180 0 0 300 100",
            "sessions": [],
            "transient": {"wire": "blink 180 0 0 300 100", "expires_in_s": 2.5},
            "serial_connected": True,
            "serial_port": "/dev/ttyUSB0",
        })
        self.assertIn("Transient: expires in 2.5s", out)
        self.assertIn("blink 180 0 0 300 100", out)

    def test_current_output_none_shows_off(self):
        out = status_cmd.format_status({
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
