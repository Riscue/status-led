#!/usr/bin/env python3
"""Claude Code hook bridge.

Reads a hook JSON payload from stdin (sent by Claude Code on each hook
event), extracts `session_id`, maps the event name to a state key, and
calls `led` to set or clear the session.

Hook events → state mapping (matches integrations/claude/states.json):
  SessionStart         → idle
  UserPromptSubmit     → thinking
  PreToolUse           → tool
  PostToolUse          → thinking
  PostToolUseFailure   → error
  Notification         → waiting
  Stop                 → success
  SessionEnd           → end (triggers --end-session)

settings.json uses the same `led claude` command for every event — this
script reads `hook_event_name` from the payload to pick the state. jq is
not required (Python stdlib json).

Always exits 0 — hooks must never interrupt the caller.
"""
import json
import subprocess
import sys

EVENT_TO_STATE = {
    "SessionStart": "idle",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "tool",
    "PostToolUse": "thinking",
    "PostToolUseFailure": "error",
    "Notification": "waiting",
    "Stop": "success",
    "SessionEnd": "end",
}


def main(argv: list[str]) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}

    # Fall back to "1" so a malformed payload still drives the LED — never
    # break the caller's flow. Aggregation across parallel sessions is lost
    # in that case, but the strip still reflects state.
    session_id = payload.get("session_id") or "1"
    event = payload.get("hook_event_name") or "SessionStart"
    state = EVENT_TO_STATE.get(event, "idle")

    if state == "end":
        subprocess.run(["led", "--quiet", "--end-session", session_id],
                       check=False)
    else:
        subprocess.run(["led", "--quiet", "--session", session_id,
                        "claude", state], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
