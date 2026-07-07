"""Pure state-aggregation logic.

Holds the (sid → SessionEntry) map and optional TransientEntry. apply_*
methods return EmitDecision indicating whether the output changed and what
to write. The orchestrator (daemon) is responsible for actually writing
decision.output to the serial port when is_change is True.

This module has no I/O. Tests construct an Aggregator, feed it (now, line)
pairs, and assert on EmitDecision and status_snapshot — no fake serial
fixture needed.

Aggregation rules (also documented in CLAUDE.md):
  1. Highest priority wins (ties: last-write-wins within a tier)
  2. A live transient overrides the aggregate unconditionally
  3. No sessions and no live transient → emit "off"
  4. Re-sending the same output is suppressed (no redundant serial writes)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessionEntry:
    priority: int
    wire: str           # full firmware line, e.g. "blink 180 0 0 300 100"
    updated_at: float   # time.monotonic(); tie-breaker within a priority tier


@dataclass
class TransientEntry:
    wire: str
    expires_at: float   # time.monotonic() + ttl_seconds


@dataclass
class EmitDecision:
    """Result of an apply_* call.

    - output: what the firmware should now show. None means "no change".
    - is_change: True iff output differs from what was previously emitted.
      The orchestrator writes to serial only when this is True.
    - parsed: False if the input line was malformed or unrecognized. The
      orchestrator logs and drops; it must NOT write to serial.
    - verb: the protocol verb (STATE/CLEAR/TRANSIENT) when parsed; useful
      for orchestrator-side logging.
    """
    output: str | None
    is_change: bool
    parsed: bool = True
    verb: str = ""


class Aggregator:
    """Multi-session state aggregator. Pure logic, no I/O."""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionEntry] = {}
        self.transient: TransientEntry | None = None
        # The last output emitted (or None if nothing yet). Used to suppress
        # redundant writes when the recomputed output matches what's already
        # showing.
        self.current_output: str | None = None

    def apply_line(self, line: str, now: float) -> EmitDecision:
        """Parse and apply a STATE/CLEAR/TRANSIENT line.

        Returns EmitDecision(parsed=False) for malformed lines. STATUS is NOT
        handled here — the orchestrator short-circuits STATUS before calling,
        because STATUS is a request/response verb rather than a fire-and-forget
        state mutation.
        """
        try:
            sp = line.find(" ")
            if sp < 0:
                return EmitDecision(None, False, parsed=False)
            verb = line[:sp]
            rest = line[sp + 1:].lstrip()

            if verb == "STATE":
                parts = rest.split(" ", 2)
                if len(parts) != 3:
                    return EmitDecision(None, False, parsed=False, verb=verb)
                sid, priority_s, wire = parts
                return self._set_state(sid, int(priority_s), wire, now)
            if verb == "CLEAR":
                sid = rest.split(" ", 1)[0]
                if not sid:
                    return EmitDecision(None, False, parsed=False, verb=verb)
                return self._clear(sid, now)
            if verb == "TRANSIENT":
                parts = rest.split(" ", 1)
                if len(parts) != 2:
                    return EmitDecision(None, False, parsed=False, verb=verb)
                ttl_ms, wire = parts
                return self._set_transient(int(ttl_ms), wire, now)
            return EmitDecision(None, False, parsed=False, verb=verb)
        except (ValueError, IndexError):
            return EmitDecision(None, False, parsed=False)

    def expire_transient_if_due(self, now: float) -> EmitDecision:
        """Clear the transient if its TTL has elapsed. Called by the
        orchestrator on each accept-timeout tick. Returns is_change=True if
        the transient was live and is now gone (so the aggregate re-resolves
        and may emit something different).
        """
        if self.transient and self.transient.expires_at <= now:
            self.transient = None
            return self._recompute(now, verb="EXPIRE")
        return EmitDecision(self.current_output, False, parsed=True)

    def _set_state(self, sid: str, priority: int, wire: str, now: float) -> EmitDecision:
        self.sessions[sid] = SessionEntry(priority, wire, now)
        return self._recompute(now, verb="STATE")

    def _clear(self, sid: str, now: float) -> EmitDecision:
        if sid in self.sessions:
            del self.sessions[sid]
            return self._recompute(now, verb="CLEAR")
        # CLEAR of unknown session: no-op, no recompute.
        return EmitDecision(self.current_output, False, parsed=True, verb="CLEAR")

    def _set_transient(self, ttl_ms: int, wire: str, now: float) -> EmitDecision:
        self.transient = TransientEntry(wire, now + ttl_ms / 1000.0)
        return self._recompute(now, verb="TRANSIENT")

    def _recompute(self, now: float, verb: str = "") -> EmitDecision:
        """Pick the highest-priority live state.

        Rules: a live transient overrides the session aggregate; otherwise the
        highest-priority session wins (ties broken by recency — last write wins
        within a tier). With nothing live, emit "off".
        """
        if self.transient and self.transient.expires_at > now:
            output = self.transient.wire
        elif self.sessions:
            winner = max(self.sessions.values(),
                         key=lambda e: (e.priority, e.updated_at))
            output = winner.wire
        else:
            output = "off"
        if output != self.current_output:
            self.current_output = output
            return EmitDecision(output, True, parsed=True, verb=verb)
        return EmitDecision(output, False, parsed=True, verb=verb)

    def status_snapshot(self, now: float,
                        serial_connected: bool,
                        serial_port: str | None) -> dict:
        """Snapshot for the STATUS query. Sessions are sorted by priority desc
        then recency (most recent first within a priority tier) — same ordering
        the aggregator uses to pick the winner.
        """
        sessions = [
            {
                "sid": sid,
                "priority": entry.priority,
                "wire": entry.wire,
                "age_s": now - entry.updated_at,
            }
            for sid, entry in self.sessions.items()
        ]
        sessions.sort(key=lambda s: (-s["priority"], s["age_s"]))
        transient = None
        if self.transient and self.transient.expires_at > now:
            transient = {
                "wire": self.transient.wire,
                "expires_in_s": self.transient.expires_at - now,
            }
        return {
            "current_output": self.current_output,
            "sessions": sessions,
            "transient": transient,
            "serial_connected": serial_connected,
            "serial_port": serial_port,
        }
