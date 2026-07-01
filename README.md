# status-led

A priority-based status aggregator for an LED strip. A WS2812B strip driven by an ESP8266 shows live status from **any
source** — Claude Code sessions, GitLab pipelines, shell scripts, anything you can call from a hook — merged onto a
single strip by priority.

The daemon, CLI, and firmware are **integration-agnostic**. There is no Claude-specific or GitLab-specific code
anywhere. Every integration is just a JSON state profile + a caller that fires `led`. Add a new source by dropping a
folder into `integrations/` — no Python edits, no reflash, no daemon restart.

## Quick start

```bash
pip3 install pyserial                     # one-time dependency
./scripts/install.sh install              # install + auto-start daemon at login
led on                                    # verify: strip lights up blue
```

Then wire Claude Code to fire `led` on each hook (see [`integrations/claude/`](integrations/claude/README.md)). The strip now
mirrors Claude's state, and multiple parallel sessions aggregate by priority.

## What it does

Each "state" maps to a strip animation:

| Example state                        | Animation              | Color         |
|--------------------------------------|------------------------|---------------|
| `claude.idle` / `gitlab.pending`     | slow breathe           | blue / grey   |
| `claude.thinking` / `gitlab.running` | scanner (sweeping dot) | purple / blue |
| `claude.error` / `gitlab.failed`     | fast blink             | red           |
| `claude.success` / `gitlab.success`  | fill (top-to-bottom)   | green         |
| `claude.waiting` (input requested)   | dim breathe            | white         |
| `off`                                | dark                   | —             |

When several sources are live at once, the **most urgent wins**: a GitLab pipeline failure beats a Claude session
thinking; a Claude error beats both.

## Topology / data flow

```
any source        ──►  led (CLI)  ──Unix socket──►  led_daemon  ──USB-serial──►  D1 Mini  ──WS2812B──►  LED
(Claude Code                                 │
 hooks, GitLab                              │ sessions: { sid → (priority, wire) }
 poller, shell                              │ transient: { wire, expires_at }
 scripts, ...)                              │ → picks highest-priority live entry and forwards it
```

- `led` (CLI): resolves a state from a JSON profile, sends one line to the daemon
- `led_daemon` (background): tracks every live session, forwards the highest-priority one to the firmware
- Firmware (ESP8266): renders the incoming animation command and knows nothing else

## Installation

**Requirement:** `pip3 install pyserial`

```bash
./scripts/install.sh install
```

This command:

- Copies the Python files to `~/.status-led/`
- Creates the `~/.local/bin/led` symlink (must be on `$PATH`; if not, the script prints what to add to your shell rc)
- macOS: writes a launchd plist (`RunAtLoad` + `KeepAlive`) → auto-starts at login
- Linux: writes a systemd --user unit (`enable --now`) → auto-starts at login
- Starts the daemon immediately

To remove: `./scripts/install.sh uninstall`. **No sudo required** — entirely user-level.

## How integrations plug in

Every integration is two things, colocated in one folder under `integrations/<source>/`:

1. **A JSON state profile** (`integrations/<source>/states.json`) — maps state keys to (animation, color, period,
   brightness, priority)
2. **A caller** (script, hook config, or poller) that invokes `led --session <id> --state <source>.<key>` when something happens

That's it. The CLI finds the profile by name (it looks in `integrations/<source>/states.json`, with `default.on` /
`default.off` hardcoded as builtins), the daemon treats `<sid>` and `<priority>` as opaque numbers, and the firmware
just renders whatever animation arrives.

Reference integrations live in [`integrations/`](integrations/):

| Integration                                             | What it shows                                | Files                                        |
|---------------------------------------------------------|----------------------------------------------|----------------------------------------------|
| [`integrations/claude/`](integrations/claude/README.md) | Claude Code session state via hooks          | `led-hook.sh`, `settings_hooks_example.json` |
| [`integrations/gitlab/`](integrations/gitlab/README.md) | GitLab pipeline status via API poller        | `poller.py`, `states.json`                   |
| [`integrations/timer/`](integrations/timer/run.sh)      | Count-up / countdown timer via `--raw level` | `run.sh`                                     |

Drop a new `integrations/foo/` directory in and `install.sh` mirrors it to `~/.status-led/integrations/foo/`
automatically — no install-script changes required.

## State profile reference

Each entry in a state profile JSON supports:

| Field        | Description                                                                          |
|--------------|--------------------------------------------------------------------------------------|
| `animation`  | `solid`, `breathe`, `blink`, `scanner`, `fill`, `converge`, `strobe`, `level`, `off` |
| `rgb`        | `[R, G, B]` 0-255                                                                    |
| `period`     | Animation speed in ms (lower = faster)                                               |
| `brightness` | 0-100 (firmware enforces a USB-power ceiling)                                        |
| `priority`   | Aggregation order — higher number wins (defaults to 0 if omitted)                    |

To add a new state: drop a new key into the JSON, then call `led --state <profile>.<new_key>`. No other step.

## Wire protocol (firmware)

The firmware renders these animations directly. Each command is a single ASCII line at 115200 baud, lowercase,
newline-terminated. `brightness` is optional (default 100) and scales below the firmware's `MAX_BRIGHTNESS` ceiling.

```
solid    r g b [brightness]                  steady color
breathe  r g b period [brightness]           black → color pulse (sin-based)
blink    r g b period [brightness]           period/2 on + period/2 off
scanner  r g b period [brightness]           dot sweeps back and forth
fill     r g b period [brightness]           LEDs light one-by-one, then hold
converge r g b period [brightness]           edges light inward, meet, retreat
strobe   r g b r2 g2 b2 period [brightness]  period/2 color1 + period/2 color2
level    r g b level_pct [brightness]        static bar: ceil(level_pct × N / 100) LEDs lit
off
```

RGB is decimal 0-255 per channel. Period is in milliseconds (clamped to ≥ 50 in firmware). Unknown animations and
malformed lines are silently ignored — the previous animation continues. The `led --raw` mode lets you fire any of
these directly, bypassing JSON profiles.

## Manual trigger usage

```bash
# STATE — joins the aggregate, persistent (until CLEAR)
led --session mysession --state claude.idle
led --session pipeline-42 --state gitlab.running

# CLEAR — remove a session
led --end-session mysession

# TRANSIENT — no session, brief flash (default 3s), then reverts to aggregate
led --state claude.error
led --state gitlab.failed --ttl 5000

# Raw animation — no profile lookup
led --raw strobe --rgb 255,0,0 --rgb2 0,0,255 --period 200
led --raw blink --rgb 255,255,0 --period 100

# Default-profile shorthand (`led <key>` == `--state default.<key>`)
led off

# Daemon bypass for debug (pays the 0.5s reset wait)
led --direct --state default.on
```

Three modes:

- `--session <sid>` → **STATE** (joins the aggregate, persistent)
- `--end-session <sid>` → **CLEAR** (removes the session)
- (neither) → **TRANSIENT** (3s flash, then reverts)

## How aggregation works

The daemon holds a dictionary: `session_id → (priority, wire_line)`. On every hook fire and on each 1-second
accept-timeout tick, it picks the highest-priority live entry and forwards it to the firmware.

Rules:

1. **Highest priority wins** — `claude.error` (100) beats `gitlab.failed` (90) beats `claude.thinking` (60) beats
   `claude.idle` (10)
2. **Ties broken by recency** — last write wins within a priority tier
3. **While a TRANSIENT is live (TTL not expired)** it overrides the aggregate
4. **With no live sessions** the daemon emits `off`

Example: two Claude sessions + one GitLab pipeline running together:

| claude-A     | claude-B     | gitlab-X      | LED shows                                                |
|--------------|--------------|---------------|----------------------------------------------------------|
| thinking     | idle         | —             | purple scanner (Claude thinking, pri 60)                 |
| thinking     | idle         | running       | blue scanner (GitLab running, pri 50 < 60 — Claude wins) |
| thinking     | idle         | failed        | red blink (GitLab failed, pri 90 > 60)                   |
| thinking     | (SessionEnd) | failed        | red blink (Claude B cleared, GitLab still wins)          |
| (SessionEnd) | —            | (success)     | green fill (GitLab success, pri 20)                      |
| (SessionEnd) | —            | (end-session) | off                                                      |

**Caveats:** A crashed session that doesn't fire `--end-session` leaves stale state until the daemon restarts. A
daemon restart drops all in-memory state — sessions rebuild as callers fire again.

## Installed file layout

```
~/.status-led/
├── led_cli.py             # CLI (called by hooks / pollers / scripts)
├── led_daemon.py          # daemon (stateful aggregator)
├── protocol.py            # shared constants
├── integrations/          # one folder per integration, mirrored from repo's integrations/
│   ├── claude/            # led-hook.sh, settings_hooks_example.json, states.json, README.md
│   ├── gitlab/            # poller.py, states.json, README.md
│   └── timer/             # run.sh, README.md
├── led.sock               # Unix socket (runtime)
├── daemon.pid             # PID (runtime)
└── daemon.log             # logs (runtime)

~/.local/bin/led           # → ~/.status-led/led_cli.py

~/Library/LaunchAgents/tr.riscue.status-led.plist       # macOS (auto-start)
~/.config/systemd/user/tr.riscue.status-led.service     # Linux (auto-start)
```

The CLI also ships a hardcoded `default` profile (used by the bare `led on` / `led off` shorthand) — it lives in
`BUILTIN_PROFILES` inside `led_cli.py`, not on disk.

Daemon log: `~/.status-led/daemon.log` — watch with `tail -f`.

Daemon control:

- **macOS:** `launchctl list tr.riscue.status-led` / `launchctl kickstart -k gui/$(id -u)/tr.riscue.status-led`
- **Linux:** `systemctl --user status tr.riscue.status-led` / `systemctl --user restart tr.riscue.status-led`

## Hardware

### Parts

- **Wemos D1 Mini** (ESP8266) with a CH340 USB-serial chip
- **WS2812B LED strip** — 8 LEDs (more requires external power; see below)
- **USB data cable** — must carry data, not just power. Charge-only cables will not work; if `ls /dev/cu.*` shows no
  CH340 entry after plugging in, the cable is likely charge-only
- A multimeter, for verifying strip polarity before power-up

### Wiring

WS2812B strips have three pads (`5V`, `GND`, `DIN`):

```
D1 Mini        WS2812B strip
─────────      ─────────────
"5V"       →   5V   (red)
"G"        →   GND  (black)
"D4"       →   DIN  (data; white/yellow)
```

**Polarity matters — reversing 5V and GND kills the WS2812B chips instantly.** Before plugging in, verify with a
multimeter which pad is which (color coding is usually red=5V, black=GND, white/yellow=data, but confirm your own
strip).

D4 = GPIO2 — a boot-strapping pin that also hosts the onboard LED. It works fine post-boot. The cable is already
soldered to D4; if you're rebuilding, prefer D2 (GPIO4) to avoid the boot-strapping pin entirely.

### Power / brightness ceiling

Firmware enforces `MAX_BRIGHTNESS = 32` (~12% duty) as a **hard ceiling**, not a style choice. At peak white the
8 LEDs would draw ~500 mA — right at USB 2.0's limit. The `brightness` field in a state profile (0-100%) only scales
**below** this ceiling; it can never exceed it.

To drive more LEDs or raise the cap:

- Add a regulated 5V external supply to the strip's power input
- Raise or remove `MAX_BRIGHTNESS` in `firmware/src/main.cpp`
- Reflash with `scripts/upload.sh`

Without external power, drawing more current risks browning out the USB port or damaging it.

### 3D-printable monitor mount

`case/StatusLedCase.3mf` is a ready-to-print mount designed to clip the strip to the top edge of a monitor. Open it in
any 3D printer slicer that reads `.3mf` (PrusaSlicer, Bambu Studio, Cura, OrcaSlicer, etc.) and slice it for your
printer.

### Hardware alternatives

Neither the Wemos D1 Mini nor the WS2812B is mandatory — both are replaceable. The architectural split (firmware
renders generic animations, daemon aggregates, CLI resolves state) means a hardware swap only touches a small,
well-defined surface:

**If you swap the LED strip** (WS2811, WS2815, SK6812, APA102, etc.):

- `firmware/src/main.cpp` — `NUM_LEDS`, `DATA_PIN`, color order (`NEO_GRB` vs other), and possibly the library
  (Adafruit NeoPixel covers WS2811/SK6812; APA102 needs a different library)
- Voltage matters for wiring — WS2815 runs on 12V, WS2812B on 5V; the strip's power input changes accordingly

**If you swap the microcontroller** (ESP32, Arduino Nano, Pi Pico, etc.):

- `firmware/platformio.ini` — board, upload speed, framework
- `firmware/src/main.cpp` — `DATA_PIN` (different GPIO numbering), `MAX_BRIGHTNESS` if the USB power budget differs
- `driver/protocol.py` — `find_esp8266_port()` scans CH340/CP2104/FTDI vendor patterns; a chip outside that list
  needs a new pattern or a `STATUS_LED_PORT` override

**What stays the same**: the daemon, CLI, JSON profiles, wire protocol, hooks. None of them know what hardware is on
the other end.

### Flashing the firmware

First-time install or firmware update:

```bash
scripts/upload.sh    # requires PlatformIO
```

## Development

### Running from the repo (without installing)

```bash
pip3 install pyserial

# Terminal 1: run the daemon in the foreground
python3 driver/led_daemon.py

# Terminal 2: fire state changes
python3 driver/led_cli.py --state default.on
python3 driver/led_cli.py --state gitlab.failed
python3 driver/led_cli.py --session X --state claude.thinking
python3 driver/led_cli.py --end-session X
```

If a system-installed daemon is already running, stop it first or override the socket:

```bash
STATUS_LED_SOCKET=/tmp/test-led.sock python3 driver/led_daemon.py
STATUS_LED_SOCKET=/tmp/test-led.sock python3 driver/led_cli.py --state default.on
```

Useful environment variables during development:

- `STATUS_LED_LOG_LEVEL=DEBUG` — daemon logs every received command
- `STATUS_LED_PORT=/dev/...` — override serial-port auto-detection
- `STATUS_LED_INTEGRATIONS_DIR` — override the integration profile search path

## Tests

```bash
python3 -m unittest discover -s tests    # 55 tests, ~0.02s
scripts/test.sh                          # hardware animation smoke test (needs daemon)
```

## Troubleshooting

- **LEDs not lighting up.** Check `ls /dev/cu.*` (macOS) or `ls /dev/ttyUSB* /dev/ttyACM*` (Linux). No CH340 entry
  usually means a charge-only USB cable or a missing driver — install the macOS CH340 driver from wch.cn if needed.
- **Port found but strip still dark.** Re-verify polarity with a multimeter. Swapping 5V and GND kills WS2812B chips
  permanently.
- **Brief dark flash between hook fires.** The daemon should keep the serial port open so the ESP8266 doesn't reset.
  If you see resets, check that the daemon is running (`launchctl list tr.riscue.status-led` /
  `systemctl --user status tr.riscue.status-led`) and `tail -f ~/.status-led/daemon.log`.
- **Wrong serial port auto-selected.** Override with `STATUS_LED_PORT=/dev/...` (env) or `--port /dev/...` (CLI flag,
  `--direct` only).
- **`pyserial is not installed` warning.** Run `pip3 install pyserial`. Hook commands use `--quiet` so this stays
  hidden in normal operation; you'll only see it when running `led` manually.
- **A closed session left the LED stuck.** A session that crashes without firing `--end-session` leaves stale state
  until the daemon restarts. Restart with `systemctl --user restart tr.riscue.status-led` (Linux) or
  `launchctl kickstart -k gui/$(id -u)/tr.riscue.status-led` (macOS).

The CLI always exits 0, even on missing hardware or daemon errors — by design, so hooks never interrupt the caller.

## Contributing

Pull requests are welcome — fork the repo, open an issue to discuss a change first, or just send a PR.

## License

MIT © [Riscue](https://github.com/riscue)
