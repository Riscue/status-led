# GitLab pipeline → LED

A Python poller that hits the GitLab API every 15 s while any watched
pipeline is in-flight and mirrors each pipeline's status onto the strip.
Once everything goes idle, the poller holds the final state briefly so the
outcome is visible, then exits (CLEARs its sessions on the way out).

## Setup

1. Configure credentials in the single secrets file:

   ```bash
   cp secrets.env.example ~/.status-led/secrets.env
   # edit ~/.status-led/secrets.env: set GITLAB_URL, GITLAB_TOKEN, GITLAB_PROJECTS
   ```

   The CLI loads `~/.status-led/secrets.env` on every `led gitlab` call and
   exposes only `GITLAB_*` keys to this poller. Other integrations' secrets
   never leak across.

2. Run the poller:

   ```bash
   led gitlab                # uses ~/.status-led/secrets.env
   ```

   `--interval N` overrides the poll interval (default 15 s). The poller
   exits when no watched project has an in-flight pipeline — wrap in a
   shell loop or run under systemd with `Restart=always` for always-on
   monitoring.

The poller tracks which pipeline sessions it has seen and clears any that
disappear from the API response — no stale state lingers on the strip.
Priorities come from `states.json`: `failed` (90) beats `running` (50)
beats `pending` (40) beats `success` (20).

To override the bundled `states.json`, create
`~/.status-led/integrations/gitlab/states.json` (per-file fallback — only
that file is overridden).

## Files

| File               | Purpose                                                          |
|--------------------|------------------------------------------------------------------|
| `poller.py`        | Workstation-side API poller (entry point: `led gitlab`)          |
| `states.json`      | Pipeline status → animation mapping; loaded by `led gitlab <key>` |
| `integration.json` | Manifest declaring `poller.py` as the run script                |
| `README.md`        | This file                                                        |
