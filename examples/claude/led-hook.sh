#!/usr/bin/env bash
# Usage: led-hook.sh <state>
# Reads Claude Code hook JSON from stdin, extracts session_id, calls `led`.
# <state> is a bare key in driver/states/claude.json (idle, thinking, tool, ...);
# the "claude." profile prefix is added here. Pass "end" for SessionEnd to
# trigger --end-session instead.

STATE="$1"
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)

# Fall back to a constant so a malformed payload or missing jq still drives the
# LED — never break the Claude Code flow.
[ -n "$SESSION_ID" ] || SESSION_ID="1"

if [ "$STATE" = "end" ]; then
  led --quiet --end-session "$SESSION_ID"
else
  led --quiet --session "$SESSION_ID" --state "claude.${STATE}"
fi
