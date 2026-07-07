# status-led

A priority-based status aggregator for an LED strip. A WS2812B strip driven by an ESP8266 shows live status from **any
source** — Claude Code sessions, GitLab pipelines, shell scripts, anything you can call from a hook — merged onto a
single strip by priority.

The daemon, CLI, and firmware are **integration-agnostic**. There is no Claude-specific or GitLab-specific code
anywhere. Every integration is just a JSON state profile + a caller that fires `led`. Add a new source by dropping a
folder into `integrations/` — no Python edits, no reflash, no daemon restart.

## Quick start

```bash
pipx install .                             # one-time: installs `led` on PATH (daemon runs as `led daemon`)
led service install                        # writes launchd/systemd unit + creates ~/.status-led/ (socket, pid, log)
led on                                     # verify: strip lights up blue
```

Then wire Claude Code to fire `led` on each hook (see [`integrations/claude/`](integrations/claude/README.md)). The strip now
mirrors Claude's state, and multiple parallel sessions aggregate by priority.

## What it does

Each "state" maps to a strip animation:

| Example state                        | Animation                | Color         |
|--------------------------------------|--------------------------|---------------|
| `claude idle`                        | breathe (slow pulse)     | blue          |
| `gitlab pending`                     | pulse                    | grey          |
| `claude thinking` / `gitlab running` | bounce (tailed comet)    | purple / blue |
| `claude error` / `gitlab failed`     | fast blink               | red           |
| `claude success` / `gitlab success`  | sparkle (random flashes) | green         |
| `claude waiting` (input requested)   | pulse                    | white         |
| `off`                                | dark                     | —             |

When several sources are live at once, the **most urgent wins**: a GitLab pipeline failure beats a Claude session
thinking; a Claude error beats both.

## Topology / data flow

```
any source        ──►  led (CLI)  ──Unix socket──►  led daemon  ──USB-serial──►  D1 Mini  ──WS2812B──►  LED
(Claude Code                                 │
 hooks, GitLab                              │ sessions: { sid → (priority, wire) }
 poller, shell                              │ transient: { wire, expires_at }
 scripts, ...)                              │ → picks highest-priority live entry and forwards it
```

- `led` (CLI): resolves a state from a JSON profile, sends one line to the daemon
- `led daemon` (background): tracks every live session, forwards the highest-priority one to the firmware
- Firmware (ESP8266): renders the incoming animation command and knows nothing else

## Installation

**Requirement:** [`pipx`](https://pipx.pypchaos.io/) (install with `brew install pipx` on macOS, or your distro's package manager on Linux). pipx installs Python CLI apps in isolated environments.

```bash
pipx install .                # installs the `led` console script on PATH
led service install           # writes launchd/systemd unit + creates ~/.status-led/
```

`led service install`:

- Creates `~/.status-led/` (mode 0o700) for runtime files (socket, pid, log) and your `secrets.env`
- macOS: writes a launchd plist (`RunAtLoad` + `KeepAlive`) → auto-starts at login
- Linux: writes a systemd --user unit (`enable + restart`) → auto-starts at login
- Starts the daemon immediately

Integrations ship **inside the wheel** — nothing is copied to `~/.status-led/integrations/` by default. To override
a bundled state profile, create `~/.status-led/integrations/<name>/states.json` (per-file fallback).

To remove: `led service uninstall`. **No sudo required** — entirely user-level.

## How integrations plug in

Every integration is a folder under `integrations/<name>/` with at least a `README.md` and one of three modes:

| File             | Mode            | Triggered by                                              |
|------------------|-----------------|-----------------------------------------------------------|
| `states.json`    | state lookup    | `led <name> <key>` — resolves to a wire line              |
| `run`            | action          | `led <name> [args]` — runs the script as a subprocess     |
| `hook`           | hook bridge     | `led <name>` (bare) — runs the script, stdin = hook payload |

State and one of run/hook may coexist (e.g. `gitlab` has states + poller). `run` + `hook` together is forbidden
— each integration has exactly one executive mode. `integration.json` (optional manifest) overrides the default
filenames: `{"run": "poller.py"}` lets you keep a more descriptive script name.

Reference integrations live in [`integrations/`](integrations/):

| Integration                                             | What it shows                                | Files                                        |
|---------------------------------------------------------|----------------------------------------------|----------------------------------------------|
| [`integrations/claude/`](integrations/claude/README.md) | Claude Code session state via hooks          | `hook.py`, `states.json`, `settings_hooks_example.json` |
| [`integrations/gitlab/`](integrations/gitlab/README.md) | GitLab pipeline status via API poller        | `poller.py`, `states.json` (reads `~/.status-led/secrets.env`) |
| [`integrations/timer/`](integrations/timer/README.md)   | Count-up / countdown timer via `led raw level` | `timer.py`                                 |

Drop a new `integrations/foo/` directory in and the CLI picks it up from the bundled wheel on the next call — no
install step, no daemon restart. The `README.md` is required (the validator enforces this).

**Integration isolation.** No integration can invoke another. The dispatcher sets `STATUS_LED_INTEGRATION_ACTIVE=<name>`
in the subprocess environment; any `led <other_name>` or self-recursion from inside is refused (rc=1). This is
load-bearing for predictability — one integration's bug can't cascade into another's state.

## State profile reference

Each entry in a state profile JSON supports:

| Field        | Description                                                                                                                |
|--------------|----------------------------------------------------------------------------------------------------------------------------|
| `animation`  | `solid`, `breathe`, `blink`, `scanner`, `fill`, `converge`, `strobe`, `level`, `pulse`, `sparkle`, `heartbeat`, `bounce`, `off` |
| `rgb`        | `[R, G, B]` 0-255                                                                                                          |
| `period`     | Animation speed in ms (lower = faster)                                                                                     |
| `brightness` | 0-100 (firmware enforces a USB-power ceiling)                                                                              |
| `priority`   | Aggregation order — higher number wins (defaults to 0 if omitted)                                                          |

To add a new state: drop a new key into the JSON, then call `led <profile> <new_key>`. No other step.

## Wire protocol (firmware)

The firmware renders these animations directly. Each command is a single ASCII line at 115200 baud, lowercase,
newline-terminated. `brightness` is optional (default 100) and scales below the firmware's `MAX_BRIGHTNESS` ceiling.

```
solid     r g b [brightness]                  steady color
breathe   r g b period [brightness]           black → color pulse (sin-based)
blink     r g b period [brightness]           period/2 on + period/2 off
scanner   r g b period [brightness]           dot sweeps back and forth
fill      r g b period [brightness]           LEDs light one-by-one, then hold
converge  r g b period [brightness]           edges light inward, meet, retreat
strobe    r g b r2 g2 b2 period [brightness]  period/2 color1 + period/2 color2
level     r g b level_pct [brightness]        static bar: ceil(level_pct × N / 100) LEDs lit
pulse     r g b period [brightness]           sharp rise → exp decay → brief off (single throb)
sparkle   r g b period [brightness]           random per-LED flashes; period = avg interval
heartbeat r g b period [brightness]           lub-dub double-thump + long rest
bounce    r g b period [brightness]           scanner with a fading directional trail
off
```

RGB is decimal 0-255 per channel. Period is in milliseconds (clamped to ≥ 50 in firmware). Unknown animations and
malformed lines are silently ignored — the previous animation continues. The `led raw` subcommand lets you fire any of
these directly, bypassing JSON profiles.

## Manual trigger usage

```bash
# STATE — joins the aggregate, persistent (until CLEAR)
led --session mysession claude idle
led --session pipeline-42 gitlab running

# CLEAR — remove a session
led --end-session mysession

# TRANSIENT — no session, brief flash (default 3s), then reverts to aggregate
led claude error
led claude success --ttl 5000

# Raw animation — no profile lookup
led raw strobe --rgb 255,0,0 --rgb2 0,0,255 --period 200
led raw blink --rgb 255,255,0 --period 100

# Default-profile shorthand (led <key> == led default <key>)
led off

# Action integration dispatch (subprocess, passes args/stdin through)
led gitlab                      # poller — reads ~/.status-led/secrets.env
led gitlab --interval 30        # extra args forwarded
led timer 5m                    # countdown/count-up timer

# Hook integration dispatch (subprocess, stdin = hook payload)
echo '{"session_id":"abc","hook_event_name":"UserPromptSubmit"}' | led claude

# Daemon bypass for debug (pays the 0.5s reset wait)
led --direct on
```

State-lookup modes (`led <name> <state>` form):

- `--session <sid>` → **STATE** (joins the aggregate, persistent)
- `--end-session <sid>` → **CLEAR** (removes the session)
- (neither) → **TRANSIENT** (3s flash, then reverts)

## How aggregation works

The daemon holds a dictionary: `session_id → (priority, wire_line)`. On every hook fire and on each 1-second
accept-timeout tick, it picks the highest-priority live entry and forwards it to the firmware.

Rules:

1. **Highest priority wins** — `claude error` (100) beats `gitlab failed` (90) beats `claude thinking` (60) beats
   `claude idle` (10)
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
├── secrets.env            # credentials (GITLAB_*, etc.) — see secrets.env.example
├── integrations/          # OPTIONAL — create <name>/states.json to override bundled
├── led.sock               # Unix socket (runtime)
├── daemon.pid             # PID (runtime)
└── daemon.log             # logs (runtime)

~/.local/bin/led           # pipx-installed console script → src/status_led/cli.py
                              # `led daemon` runs the daemon (see commands/daemon.py)

~/Library/LaunchAgents/tr.riscue.status-led.plist       # macOS (auto-start, written by `led service install`)
~/.config/systemd/user/tr.riscue.status-led.service     # Linux (auto-start, written by `led service install`)
```

The bundled integrations ship inside the wheel (e.g. `~/.local/pipx/venvs/status-led/lib/python3.x/site-packages/status_led/integrations/`). To customize one, drop a single file in `~/.status-led/integrations/<name>/` — the CLI checks the user dir first, falls back to bundled for everything else.

The CLI also ships a hardcoded `default` profile (used by the bare `led on` / `led off` shorthand) — it lives in
`BUILTIN_PROFILES` inside `src/status_led/profiles.py`, not on disk.

Daemon log: `~/.status-led/daemon.log` — watch with `tail -f`.

Inspect live daemon state with `led status` (current output, active sessions sorted by priority,
transient countdown, serial connectivity). `led status --json` emits raw JSON for scripting.

Daemon control:

- **macOS:** `launchctl list tr.riscue.status-led` / `launchctl kickstart -k gui/$(id -u)/tr.riscue.status-led`
- **Linux:** `systemctl --user status tr.riscue.status-led` / `systemctl --user restart tr.riscue.status-led`

## Hardware

### Parts

- **Wemos D1 Mini** (ESP8266) with a CH340 USB-serial chip
- **WS2812B LED strip** — 15 LEDs (more requires external power; see below)
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

Firmware enforces `MAX_BRIGHTNESS = 64` (~25% duty) as a **hard ceiling**, not a style choice. At peak white the
15 LEDs draw ~225 mA — well within USB 2.0's 500 mA limit, with headroom for inrush and safety. The `brightness`
field in a state profile (0-100%) only scales **below** this ceiling; it can never exceed it.

To drive more LEDs or raise the cap:

- Add a regulated 5V external supply to the strip's power input
- Raise or remove `MAX_BRIGHTNESS` in `firmware/src/main.cpp`
- Reflash with `led upload-firmware`

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
- `src/status_led/protocol.py` — `find_esp8266_port()` scans CH340/CP2104/FTDI vendor patterns; a chip outside that list
  needs a new pattern or a `STATUS_LED_PORT` override

**What stays the same**: the daemon, CLI, JSON profiles, wire protocol, hooks. None of them know what hardware is on
the other end.

### Flashing the firmware

First-time install or firmware update:

```bash
led upload-firmware                 # wraps platformio; requires `pio` on PATH
led upload-firmware --monitor       # open serial monitor after flashing
led upload-firmware --firmware-dir /path/to/firmware   # override location
# or canonical: cd firmware && pio run -t upload
```

## Development

### Running from the repo (without installing)

```bash
pipx install --editable .       # installs led from the repo, edits flow through

# Terminal 1: run the daemon in the foreground
led daemon

# Terminal 2: fire state changes
led on
led claude thinking
led --session X claude thinking
led --end-session X
```

If a system-installed daemon is already running, stop it first or override the socket:

```bash
led daemon --socket /tmp/test-led.sock --log-level DEBUG
led --socket /tmp/test-led.sock on
# or via env vars:
STATUS_LED_SOCKET=/tmp/test-led.sock led daemon
STATUS_LED_SOCKET=/tmp/test-led.sock led on
```

Useful environment variables during development (each has a matching CLI flag):

- `STATUS_LED_LOG_LEVEL=DEBUG` — daemon log level (`led daemon --log-level DEBUG`)
- `STATUS_LED_SOCKET=/tmp/x.sock` — daemon socket path (`led daemon --socket ...`, `led --socket ...`)
- `STATUS_LED_PORT=/dev/...` — override serial-port auto-detection (`--port`)
- `STATUS_LED_TTL_MS=5000` — transient flash TTL (`--ttl 5000`)
- `STATUS_LED_SESSION_ID=...` — default session id (`--session`)
- `STATUS_LED_INTEGRATIONS_DIR` — override the integration profile search path

## Tests

```bash
python -m unittest discover -v       # 153 unit tests, <1s
led smoke-test                       # hardware animation cycle (needs running daemon)
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
