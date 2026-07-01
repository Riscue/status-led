# Timer → LED

Count-up or countdown timer rendered as a `level` bar. Green while there's
plenty of time left, fading to red as the deadline nears. A cyan ↔ magenta
strobe plays for 3 s when the run finishes.

Unlike `claude/` and `gitlab/`, this example ships **no JSON profile** — it
fires `led --raw level` directly, the right pattern when parameters are
computed on the fly (here: a percentage and an RGB gradient interpolated in
bash).

## Usage

```bash
./run.sh 5m                # count up:   0 → 5m  (default)
./run.sh --up 5m           # explicit count-up
./run.sh --countdown 5m    # count down: 5m → 0
```

`<duration>` accepts `30`, `30s`, `5m`, `1h30m`, …

## How it works

- **Per-tick STATE, not TRANSIENT.** Each tick is a persistent state update
  under a per-invocation session id (`timer-$$`); the latest level always
  wins. A TRANSIENT (3 s TTL) would flicker if a tick ever arrived late.
- **`trap` on EXIT.** Ctrl-C the timer and the trap still fires
  `--end-session`, so the strip goes dark instead of freezing on a partial
  level.
- **Color logic independent of direction.** Both modes feed elapsed fraction
  into the gradient: 0% green → 100% red. Countdown inverts the *level*
  (100% → 0%) but not the color — "red" always means "near the end",
  whichever way the bar moves.
- **`--raw level` bypasses JSON profiles entirely.** The CLI assembles the
  wire line from `--rgb`/`--level` and sends it byte-for-byte — same daemon
  path as a state-keyed call, just without the lookup.

Requires the daemon running and `led` on `$PATH`
(`./scripts/install.sh install`).
