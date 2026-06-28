# Claude Code LED Status Indicator

A hardware indicator that turns a WS2812B LED strip into a status display for
Claude Code (idle / thinking / running a tool / waiting for input / success / error).

## Architecture

```
Claude Code --hooks--> led --AF_UNIX--> led_daemon.py --USB-serial--> Wemos D1 Mini --WS2812B--> strip
                       (thin client)  (persistent service, mandatory)
```

- **No Wi-Fi.** The D1 Mini's wireless feature is never used — USB-serial only.
- **Single cable.** Power and data both go through the same USB cable; no external power supply needed.
- **Daemon keeps the port open, and is mandatory.** The ESP8266 resets on every serial-open (CH340 DTR line). A persistent daemon avoids the reset (and the 0.5 s wait + brief dark flash on back-to-back hooks). If the daemon is not running, the CLI drops the command instead of falling back to direct-serial — make sure `install.sh install` (auto-start at login) or `install.sh start` has been run.
- **Install-and-forget.** `sudo ./scripts/install.sh install` copies the binaries to `/opt/claude-led/`, exposes them on `$PATH` as `led`, and registers a launchd/systemd user unit. Claude Code keeps working even if you delete this repo.
- Brightness is capped in firmware (USB port safety); each command can additionally dim below that ceiling via a
  `bright_pct` parameter.

## Hardware requirements

| Part                                                     | Note                                                   |
|----------------------------------------------------------|--------------------------------------------------------|
| WS2812B addressable LED strip                            | Any WS2812B / NeoPixel strip, stick, or ring works     |
| Wemos D1 Mini (ESP8266, CH340 or CP2104 USB-serial chip) | Clone or original both work                            |
| USB data cable (micro-USB)                               | A charge-only cable will NOT work — it must carry data |
| 3 wires (~10 cm): 5V, GND, DIN                           | From the strip connector to the D1 Mini                |
| Multimeter                                               | For pin verification — CRITICAL                        |

> A NodeMCU or any other ESP8266 board also works instead of the Wemos D1 Mini, but the pin mapping will differ.

## Folders

**Repo (development):**

- `firmware/` — ESP8266 firmware (PlatformIO project)
- `driver/led_cli.py` — Thin CLI client (becomes `/opt/claude-led/led_cli.py` after install)
- `driver/led_daemon.py` — Persistent daemon (becomes `/opt/claude-led/led_daemon.py`)
- `driver/states/` — JSON state profiles (copied to `/opt/claude-led/states/`)
- `scripts/install.sh` — Install / uninstall / daemon control. Templates for launchd/systemd are embedded inline.
- `examples/claude_settings_hooks_example.json` — Example Claude Code hook configuration

**After `install.sh install` (system-wide, root-owned):**

```
/opt/claude-led/
├── led_cli.py          # also accessible as `led` via /usr/local/bin/led
├── led_daemon.py
├── states/*.json
└── install.sh          # so you can uninstall without the repo
/usr/local/bin/led      # symlink -> /opt/claude-led/led_cli.py
```

## Setup sequence

### 1) Verifying the strip pinout (MOST CRITICAL STEP)

Identify the three connections at the input end of your WS2812B strip: 5V (VCC),
GND, and DIN (data input). Most strips are labeled, but verify with a multimeter
before wiring — do not guess. Wiring 5V and GND backwards will permanently kill
the LED chips. Convention is typically red=5V, black=GND, white or yellow=data,
but always confirm your strip's own color coding.

### 2) Flashing the firmware (PlatformIO)

- If PlatformIO is not installed: `pip3 install platformio` (or `brew install platformio`)
- Plug the D1 Mini in via USB
- From the project root: `cd firmware && pio run -t upload`
    - Dependencies (`espressif8266@4.2.1` platform, `Adafruit NeoPixel@1.15.5` library)
      are downloaded automatically; versions are pinned in `platformio.ini`.

> We avoid the Arduino IDE — its manual setup steps cause reproducibility issues.
> PlatformIO installs everything with a single command.

### 3) Wiring

```
D1 Mini "5V"  -> Strip 5V
D1 Mini "G"   -> Strip GND
D1 Mini "D4"  -> Strip DIN (data)
```

### 4) Host-side driver

```bash
pip3 install pyserial
```

You can run from the repo without installing:

```bash
# State mode — looks up claude.idle in driver/states/claude.json:
python3 driver/led_cli.py --state claude.idle

# Raw mode — direct animation, bypasses state profiles:
python3 driver/led_cli.py --raw breathe --rgb 0,50,220 --period 3500
```

### 5) Install system-wide (recommended)

Install the binaries to `/opt/claude-led/`, expose `led` on `$PATH`, and register a launchd (macOS) / systemd user (Linux) unit so the daemon auto-starts at login:

```bash
sudo ./scripts/install.sh install
```

After install, the `led` command is on `$PATH` regardless of whether this repo exists:

```bash
led --state claude.idle                 # state lookup
led --raw breathe --rgb 0,50,220 --period 3500
led --direct --state claude.idle        # bypass daemon (debug)
./scripts/install.sh status             # daemon status
./scripts/install.sh logs               # tail daemon log
./scripts/install.sh foreground         # run daemon in foreground (debug)
sudo ./scripts/install.sh uninstall     # remove everything
```

The daemon is mandatory: if it is not running, hook commands are dropped (the LED is not updated). Make sure to either run `install.sh install` (auto-start at login) or start the daemon manually with `./scripts/install.sh start`.

### 6) Wiring up Claude Code hooks

Append the contents of `examples/claude_settings_hooks_example.json` to `~/.claude/settings.json`. The hooks call the installed `led` command, so no path editing is needed (only `install.sh install` must have been run).

## Claude Code states

The driver ships with `driver/states/claude.json`, which maps each Claude Code
state to an animation. Edit that file to retune any state — **no firmware
reflash, no Python changes required**.

| State (`--state claude.<key>`) | Animation | Color (RGB)   | Period | Brightness |
|--------------------------------|-----------|---------------|--------|------------|
| `idle`                         | breathe   | 0, 50, 220    | 3500ms | 100%       |
| `thinking`                     | scanner   | 90, 0, 170    | 1600ms | 100%       |
| `tool`                         | breathe   | 255, 128, 0   | 1500ms | 100%       |
| `waiting`                      | breathe   | 200, 200, 200 | 2500ms | 60%        |
| `success`                      | fill      | 0, 220, 0     | 3500ms | 100%       |
| `error`                        | blink     | 180, 0, 0     | 300ms  | 100%       |
| `off`                          | off       | —             | —      | —          |

## Wire protocol

The firmware is generic — it does not know about Claude Code, only the
animation commands below. Each is a single ASCII line, lowercase, newline-
terminated, at 115200 baud. `bright_pct` is optional (default 100) and scales
below the firmware's `MAX_BRIGHTNESS` USB-safety ceiling.

```
solid   r g b [bright_pct]              all LEDs steady
breathe r g b period_ms [bright_pct]    black -> color, sin-based pulse
blink   r g b period_ms [bright_pct]    period/2 on + period/2 off
scanner r g b period_ms [bright_pct]    dot sweeps back and forth
fill    r g b period_ms [bright_pct]    LEDs light one-by-one, then hold
off
```

RGB is decimal 0-255 per channel. Period is in milliseconds (clamped to >= 50
in firmware). Unknown animations and malformed lines are silently ignored.

## Customizing the visuals

Edit `/opt/claude-led/states/claude.json` (after install) to retune any Claude
Code state — change its animation, RGB, period, or brightness. Changes take
effect on the next hook fire; no reflash, no Python edit. (During development,
edit `driver/states/claude.json` in the repo and re-run `install.sh install`
to copy it across.)

```json
"error": {"animation": "blink", "rgb": [180, 0, 0], "period": 500, "brightness": 70}
```

### Adding new state profiles

State profiles are just JSON files. Drop a new file in `/opt/claude-led/states/`
(e.g. `git.json`) and reference it with `--state git.<key>`. Each entry needs
`animation` plus `rgb` / `period` / `brightness` (except `off`, which needs
only `animation`). No Python changes required.

```bash
led --quiet --state git.merging
```

For one-off testing from the shell without writing a profile, use `--raw`:

```bash
led --raw scanner --rgb 200,0,255 --period 1200 --brightness 50
```

## Claude Code hooks → state mapping

| Hook (Claude Code event) | `--state` argument  | When it fires                            |
|--------------------------|---------------------|------------------------------------------|
| `SessionStart`           | `claude.idle`       | When Claude Code opens                   |
| `UserPromptSubmit`       | `claude.thinking`   | When you send a message                  |
| `PreToolUse`             | `claude.tool`       | Before a tool (Read/Bash/...) is invoked |
| `PostToolUse`            | `claude.thinking`   | After a tool finishes                    |
| `PostToolUseFailure`     | `claude.error`      | On a tool failure                        |
| `Notification`           | `claude.waiting`    | When Claude Code shows a notification    |
| `Stop`                   | `claude.success`    | When Claude Code finishes its response   |
| `SessionEnd`             | `claude.off`        | When the session closes                  |

> Hooks call the installed `led` command directly. After `sudo ./scripts/install.sh install`
> no environment variables or path edits are needed — Claude Code finds `led` on `$PATH`.

## Notes / limitations

- `led` always exits with code 0 even if the LED hardware is missing or not found,
  so it never disrupts the Claude Code flow.

> All states are persistent — the effect continues until a new hook/command arrives.
> There is no automatic return to blue (state changes as the user sends new commands).

- **LEDs are not lighting up.** List serial ports with `ls /dev/cu.*`. If there is no
  `cu.wchusbserial*` entry, either the USB cable is not carrying data or the CH340 driver
  is missing (install the macOS driver from wch.cn).
- **Port found but LEDs still not lighting up.** Re-verify the strip pins with a multimeter.
  If you swap 5V and GND, the WS2812B chips will die permanently.
- **When a hook fires, the LEDs briefly turn off and then back on.** This should not happen in
  normal operation — the daemon keeps the serial port open so the ESP8266 does not reset. If you
  see it, check `./scripts/install.sh status` and `./scripts/install.sh logs`. It is expected
  with `led --direct ...` (debug), which opens the serial port per-command and pays the 0.5 s
  ESP8266 reset wait.
- **"pyserial not installed" warning.** Run `pip3 install pyserial`. The hook config example
  calls the driver with `--quiet`, so this warning is hidden in the hook flow; it only
  appears when running manually.
- **Wrong port is being selected.** If multiple USB-serial devices are plugged in, set the
  `CLAUDE_LED_PORT` environment variable or pass `--port /dev/cu.usbserial-XXXX`.

---

## License

MIT © [Riscue](https://github.com/riscue)
