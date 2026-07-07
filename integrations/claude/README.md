# Claude Code → LED

Mirror Claude Code's session state onto the strip. Each Claude session
becomes a daemon-side session keyed by `session_id`, so multiple parallel
terminals aggregate by priority — one's `error` (100) overrides another's
`thinking` (60).

## Setup

1. Append the `hooks` block from `settings_hooks_example.json` into your
   `~/.claude/settings.json`. Every event uses the same command, `led claude`
   — the hook script reads the event name from the JSON payload and picks
   the matching state.

   | Hook event             | State fired       | Animation         |
   |------------------------|-------------------|-------------------|
   | `SessionStart`         | `claude idle`     | blue breathe      |
   | `UserPromptSubmit`     | `claude thinking` | purple bounce     |
   | `PreToolUse`           | `claude tool`     | orange pulse      |
   | `PostToolUse`          | `claude thinking` | purple bounce     |
   | `PostToolUseFailure`   | `claude error`    | red blink         |
   | `Notification`         | `claude waiting`  | dim white pulse   |
   | `Stop`                 | `claude success`  | green sparkle     |
   | `SessionEnd`           | (session cleared) | —                 |

2. Customize colors/priorities in `states.json` (or override at
   `~/.status-led/integrations/claude/states.json`).

## Requirements

None beyond `led` itself. The hook script is pure Python stdlib (no `jq`,
no extra packages) and is invoked by `led claude` (bare form) — see
`integration.json` for the dispatch wiring.

## How it works

- `led claude` (bare) → CLI discovers `integrations/claude/hook` via the
  manifest and spawns it as a subprocess with the JSON payload piped to
  stdin.
- The hook script parses `session_id` and `hook_event_name` from the
  payload, maps the event to a state, and calls
  `led --session <id> claude <state>` (or `--end-session <id>` for
  SessionEnd).
- An integration cannot invoke another integration (`STATUS_LED_INTEGRATION_ACTIVE`
  guard in `cli.py`), so this hook is sandboxed to its own state lookups.

## Customizing

- **Different colors / speed / brightness per state** — edit `states.json`.
- **New state** — add a key to `states.json` and an entry to `EVENT_TO_STATE`
  in the `hook` script.
- **Skip a state** — delete the entry from `~/.claude/settings.json`. The
  matching `states.json` key becomes unused but harmless.
