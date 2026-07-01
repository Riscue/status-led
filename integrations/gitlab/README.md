# GitLab pipeline → LED

A Python poller that hits the GitLab API every 15 s and mirrors each active
pipeline's status onto the strip. Best when you want a status LED on your
own machine reflecting shared CI.

## Setup

1. Install dependencies:

   ```bash
   pip3 install requests
   ```

2. Run the poller:

   ```bash
   GITLAB_URL=https://gitlab.example.com \
   GITLAB_TOKEN=<personal-access-token-with-read_api> \
   PROJECTS=myteam/backend,myteam/frontend \
   ./poller.py
   ```

   `--once` polls a single time and exits (cron-friendly); the default loops
   every 15 s (`--interval N` to override).

After `./scripts/install.sh install`, this folder is mirrored to
`~/.status-led/integrations/gitlab/`, so `states.json` is already on the
CLI's profile search path — no manual copy needed.

The poller tracks which pipeline sessions it has seen and clears any that
disappear from the API response — no stale state lingers on the strip.
Priorities come from `states.json`: `failed` (90) beats `running` (50)
beats `pending` (40) beats `success` (20).

## Files

| File          | Purpose                                                                    |
|---------------|----------------------------------------------------------------------------|
| `poller.py`   | Workstation-side API poller                                                |
| `states.json` | Pipeline status → animation mapping; loaded by `led --state gitlab.<key>`  |
