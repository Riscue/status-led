# Claude Code → LED

Mirror Claude Code's session state onto the strip. Each Claude session
becomes a daemon-side session keyed by `session_id`, so multiple parallel
terminals aggregate by priority — one's `error` (100) overrides another's
`thinking` (60).

## Setup

After `./scripts/install.sh install`, the files in this directory are
mirrored to `~/.status-led/integrations/claude/`:

- `led-hook.sh` — reads the hook JSON payload from stdin, extracts
  `session_id` via `jq`, fires `led --session <id> --state claude.<key>`.
- `settings_hooks_example.json` — ready-to-paste hook config.

1. Append the `hooks` block from `settings_hooks_example.json` into your
   `~/.claude/settings.json`. The shipped mapping:

   | Hook event             | State fired       | Animation         |
      |------------------------|-------------------|-------------------|
   | `SessionStart`         | `claude.idle`     | blue breathe      |
   | `UserPromptSubmit`     | `claude.thinking` | purple scanner    |
   | `PreToolUse`           | `claude.tool`     | orange breathe    |
   | `PostToolUse`          | `claude.thinking` | purple scanner    |
   | `PostToolUseFailure`   | `claude.error`    | red blink         |
   | `Notification`         | `claude.waiting`  | dim white breathe |
   | `Stop`                 | `claude.success`  | green fill        |
   | `SessionEnd`           | (session cleared) | —                 |

2. Customize colors/priorities in `~/.status-led/integrations/claude/states.json`.

## Requirements

**`jq` is required.** Without it, `led-hook.sh` falls back to a constant
`session_id=1` and aggregation across parallel sessions is lost — every
Claude window fights over the same slot. `install.sh` warns if `jq` is
missing; install with `brew install jq` (macOS) or `apt install jq` (Linux).

## Customizing

- **Different colors / speed / brightness per state** — edit `states.json`.
  No code changes needed.
- **New state** (e.g. `warning`) — add a key to `states.json` *and* a hook
  entry that fires `led-hook.sh warning`. The two must stay in sync.
- **Skip a state** — delete the entry from `settings.json`. The matching
  `states.json` key becomes unused but harmless.
