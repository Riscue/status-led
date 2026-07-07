"""Aggregator tests. Pure — no I/O, no socket, no serial.

These were originally in test_daemon.py via the _CapturingDaemon fixture
(a Daemon subclass with a fake serial that captured writes). With the
aggregator extracted to a pure class, the fixture is unnecessary — we
just call apply_line on an Aggregator() and assert on EmitDecision.
"""
from __future__ import annotations

import time
import unittest

from status_led.aggregator import Aggregator, EmitDecision, TransientEntry


class AggregationTest(unittest.TestCase):
    def setUp(self):
        self.a = Aggregator()
        self.now = 1000.0  # arbitrary monotonic baseline; tests advance explicitly

    def _apply(self, line: str) -> EmitDecision:
        return self.a.apply_line(line, self.now)

    def _last_emit(self) -> str | None:
        return self.a.current_output

    def test_state_creates_session_and_emits(self):
        d = self._apply("STATE A 60 scanner 90 0 170 1600 100")
        self.assertEqual(d.output, "scanner 90 0 170 1600 100")
        self.assertTrue(d.is_change)
        self.assertIn("A", self.a.sessions)
        self.assertEqual(self.a.sessions["A"].priority, 60)

    def test_lower_priority_does_not_override(self):
        self._apply("STATE A 60 scanner 90 0 170 1600 100")
        d = self._apply("STATE B 10 breathe 0 50 220 3500 100")
        self.assertFalse(d.is_change)
        self.assertEqual(self._last_emit(), "scanner 90 0 170 1600 100")

    def test_higher_priority_overrides(self):
        self._apply("STATE A 60 scanner 90 0 170 1600 100")
        d = self._apply("STATE B 100 blink 180 0 0 300 100")
        self.assertEqual(d.output, "blink 180 0 0 300 100")
        self.assertTrue(d.is_change)

    def test_clear_reverts_to_next_highest(self):
        self._apply("STATE A 60 scanner 90 0 170 1600 100")
        self._apply("STATE B 100 blink 180 0 0 300 100")
        d = self._apply("CLEAR B")
        self.assertEqual(d.output, "scanner 90 0 170 1600 100")
        self.assertTrue(d.is_change)

    def test_clear_last_session_emits_off(self):
        self._apply("STATE A 60 scanner 90 0 170 1600 100")
        d = self._apply("CLEAR A")
        self.assertEqual(d.output, "off")
        self.assertTrue(d.is_change)

    def test_clear_unknown_session_is_noop(self):
        self._apply("STATE A 60 scanner 90 0 170 1600 100")
        before = self._last_emit()
        d = self._apply("CLEAR does-not-exist")
        self.assertFalse(d.is_change)
        self.assertEqual(self._last_emit(), before)

    def test_priority_tie_last_write_wins(self):
        self._apply("STATE A 60 scanner 90 0 170 1600 100")
        # Advance time so the second STATE has a strictly later updated_at.
        self.now += 0.01
        d = self._apply("STATE B 60 breathe 0 50 220 3500 100")
        self.assertEqual(d.output, "breathe 0 50 220 3500 100")
        self.assertTrue(d.is_change)


class TransientTest(unittest.TestCase):
    def setUp(self):
        self.a = Aggregator()
        self.now = 1000.0

    def _apply(self, line: str) -> EmitDecision:
        return self.a.apply_line(line, self.now)

    def test_transient_overrides_session(self):
        self._apply("STATE A 60 scanner 90 0 170 1600 100")
        d = self._apply("TRANSIENT 3000 blink 180 0 0 300 100")
        self.assertEqual(d.output, "blink 180 0 0 300 100")

    def test_transient_expiry_reverts_to_session(self):
        self._apply("STATE A 60 scanner 90 0 170 1600 100")
        self._apply("TRANSIENT 3000 blink 180 0 0 300 100")
        # Advance time past expiry.
        d = self.a.expire_transient_if_due(self.now + 5)
        self.assertEqual(d.output, "scanner 90 0 170 1600 100")
        self.assertTrue(d.is_change)

    def test_transient_with_no_sessions_emits_off_after_expiry(self):
        self._apply("TRANSIENT 3000 blink 180 0 0 300 100")
        d = self.a.expire_transient_if_due(self.now + 5)
        self.assertEqual(d.output, "off")
        self.assertTrue(d.is_change)

    def test_strobe_transient_preserves_all_fields(self):
        # Strobe has 9 tokens — wire-line must pass through opaquely.
        d = self._apply("TRANSIENT 500 strobe 180 0 0 0 0 180 300 100")
        self.assertEqual(d.output, "strobe 180 0 0 0 0 180 300 100")

    def test_expire_when_not_due_is_noop(self):
        self._apply("TRANSIENT 3000 blink 180 0 0 300 100")
        d = self.a.expire_transient_if_due(self.now + 1)  # 1s elapsed, 3s ttl
        self.assertFalse(d.is_change)


class OpaqueWireTest(unittest.TestCase):
    """The aggregator must not parse or interpret the wire-line at all — it
    just stores and forwards it. Strobe (9 tokens) and off (1 token) are the
    edge cases that would break a fixed-field parser.
    """

    def setUp(self):
        self.a = Aggregator()
        self.now = 1000.0

    def test_off_one_token(self):
        d = self.a.apply_line("STATE A 0 off", self.now)
        self.assertEqual(d.output, "off")

    def test_strobe_nine_tokens(self):
        d = self.a.apply_line("STATE A 100 strobe 180 0 0 0 0 180 300 100", self.now)
        self.assertEqual(d.output, "strobe 180 0 0 0 0 180 300 100")

    def test_solid_four_tokens(self):
        d = self.a.apply_line("STATE A 50 solid 0 0 255 30", self.now)
        self.assertEqual(d.output, "solid 0 0 255 30")


class RedundantEmitSuppressionTest(unittest.TestCase):
    def setUp(self):
        self.a = Aggregator()
        self.now = 1000.0

    def test_re_sending_same_state_does_not_emit(self):
        d1 = self.a.apply_line("STATE A 60 scanner 90 0 170 1600 100", self.now)
        self.assertTrue(d1.is_change)
        d2 = self.a.apply_line("STATE A 60 scanner 90 0 170 1600 100", self.now)
        self.assertFalse(d2.is_change)


class StatusSnapshotTest(unittest.TestCase):
    """status_snapshot is the source of truth for the STATUS query response.
    Sessions are sorted by priority desc + recency asc — same ordering the
    aggregator uses to pick the winner.
    """

    def setUp(self):
        self.a = Aggregator()
        self.now = 1000.0

    def test_empty_state(self):
        snap = self.a.status_snapshot(self.now, serial_connected=False, serial_port=None)
        self.assertIsNone(snap["current_output"])
        self.assertEqual(snap["sessions"], [])
        self.assertIsNone(snap["transient"])

    def test_current_output_reflects_last_emit(self):
        self.a.apply_line("STATE A 60 scanner 90 0 170 1600 100", self.now)
        snap = self.a.status_snapshot(self.now, serial_connected=True, serial_port="/dev/ttyUSB0")
        self.assertEqual(snap["current_output"], "scanner 90 0 170 1600 100")

    def test_sessions_sorted_by_priority_desc_then_recency(self):
        self.a.apply_line("STATE low 10 breathe 0 50 220 3500 100", self.now)
        self.now += 0.01
        self.a.apply_line("STATE high 100 blink 180 0 0 300 100", self.now)
        self.now += 0.01
        self.a.apply_line("STATE mid 60 scanner 90 0 170 1600 100", self.now)
        snap = self.a.status_snapshot(self.now, serial_connected=True, serial_port="x")
        sids = [s["sid"] for s in snap["sessions"]]
        self.assertEqual(sids, ["high", "mid", "low"])
        first = snap["sessions"][0]
        self.assertEqual(first["priority"], 100)
        self.assertEqual(first["wire"], "blink 180 0 0 300 100")
        self.assertIn("age_s", first)
        self.assertGreater(first["age_s"], 0)

    def test_priority_tie_most_recent_first(self):
        self.a.apply_line("STATE A 60 scanner 90 0 170 1600 100", self.now)
        self.now += 0.01
        self.a.apply_line("STATE B 60 breathe 0 50 220 3500 100", self.now)
        snap = self.a.status_snapshot(self.now, serial_connected=True, serial_port="x")
        sids = [s["sid"] for s in snap["sessions"]]
        self.assertEqual(sids, ["B", "A"])  # B more recent

    def test_transient_included_when_live(self):
        self.a.apply_line("TRANSIENT 5000 blink 180 0 0 300 100", self.now)
        snap = self.a.status_snapshot(self.now, serial_connected=True, serial_port="x")
        self.assertIsNotNone(snap["transient"])
        self.assertEqual(snap["transient"]["wire"], "blink 180 0 0 300 100")
        self.assertGreater(snap["transient"]["expires_in_s"], 0)

    def test_transient_excluded_when_expired(self):
        self.a.apply_line("TRANSIENT 5000 blink 180 0 0 300 100", self.now)
        snap = self.a.status_snapshot(self.now + 10, serial_connected=True, serial_port="x")
        self.assertIsNone(snap["transient"])

    def test_serial_state_passed_through(self):
        snap = self.a.status_snapshot(self.now, serial_connected=False, serial_port=None)
        self.assertFalse(snap["serial_connected"])
        self.assertIsNone(snap["serial_port"])

    def test_cleared_session_not_in_status(self):
        self.a.apply_line("STATE A 60 scanner 90 0 170 1600 100", self.now)
        self.a.apply_line("CLEAR A", self.now)
        snap = self.a.status_snapshot(self.now, serial_connected=True, serial_port="x")
        self.assertEqual(snap["sessions"], [])


class MalformedLineTest(unittest.TestCase):
    """The aggregator must never raise on bad input — it returns a
    not-parsed EmitDecision and the orchestrator logs+drops.
    """

    def setUp(self):
        self.a = Aggregator()
        self.now = 1000.0

    def _assert_dropped(self, line: str):
        d = self.a.apply_line(line, self.now)
        self.assertFalse(d.parsed, f"expected {line!r} to be dropped as malformed")
        self.assertFalse(d.is_change)

    def test_each_malformed_variant_is_dropped(self):
        for line in [
            "GARBAGE",
            "STATE",                  # missing fields
            "STATE onlysid",          # missing priority + wire
            "STATE A notanum wire",   # priority not int
            "CLEAR",                  # missing sid
            "TRANSIENT",              # missing fields
            "TRANSIENT notanum wire", # ttl not int
            "UNKNOWNVerb x y z",
            "",
        ]:
            with self.subTest(line=line):
                self._assert_dropped(line)


if __name__ == "__main__":
    unittest.main()
