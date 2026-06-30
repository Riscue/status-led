#!/usr/bin/env bash
# install.sh — install / uninstall the claude-led daemon (user-level, no sudo).
#
# Layout after install:
#   ~/.claude-led/{led_cli.py, led_daemon.py, protocol.py, states/*.json}
#   ~/.claude-led/hooks/<source>/    (caller-side glue mirrored from examples/)
#   ~/.claude-led/{led.sock, daemon.pid, daemon.log}   (runtime, by daemon)
#   ~/.local/bin/led                                   (symlink → led_cli.py)
#   ~/Library/LaunchAgents/tr.riscue.claude-led.plist       (macOS, auto-start)
#   ~/.config/systemd/user/tr.riscue.claude-led.service     (Linux, auto-start)
#
# Usage:
#   ./install.sh install      # install files + enable auto-start at login
#   ./install.sh uninstall    # remove everything

set -eu

LABEL="tr.riscue.claude-led"
INSTALL_DIR="$HOME/.claude-led"
BIN_DIR="$HOME/.local/bin"
LED_SYMLINK="$BIN_DIR/led"
LOG_FILE="$INSTALL_DIR/daemon.log"
MACOS_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SYSTEMD_UNIT="$HOME/.config/systemd/user/$LABEL.service"

# Repo paths — install.sh runs from scripts/, so siblings live one level up.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/../driver"
EXAMPLES_DIR="$SCRIPT_DIR/../examples"
HOOK_DST_DIR="$INSTALL_DIR/hooks"

detect_platform() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux)  echo "linux" ;;
    *)      echo "unknown" ;;
  esac
}

find_python_with_pyserial() {
  # Scan known python3 locations; return the first that can `import serial`.
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    [[ -x "$candidate" ]] || continue
    if "$candidate" -c 'import serial' 2>/dev/null; then
      echo "$candidate"
      return
    fi
  done
  echo ""
}

ensure_jq() {
  # led-hook.sh parses the Claude Code hook payload with jq. Without it the
  # hook silently falls back to session_id=1 — warn so the user notices.
  if command -v jq >/dev/null 2>&1; then
    return 0
  fi
  echo ""
  echo "    NOTE: 'jq' not found on PATH."
  echo "      led-hook.sh needs it to read session_id from the Claude Code hook payload."
  echo "      Install it (e.g. 'sudo apt install jq' / 'brew install jq') so session"
  echo "      aggregation works; otherwise every hook defaults to session_id=1."
}

ensure_user_bus_env() {
  # sudo -i and some headless shells strip XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS.
  # Restore canonical defaults so `systemctl --user` works.
  if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  fi
  if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]] && [[ -S "$XDG_RUNTIME_DIR/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  fi
}

warn_serial_access() {
  # Linux: USB-serial devices are owned by `dialout` (Debian/Ubuntu) or `uucp`
  # (Arch). Without group membership the daemon can't open the port and the
  # LED stays dark.
  local device="" group_name
  for pattern in /dev/ttyUSB* /dev/ttyACM*; do
    [[ -e "$pattern" ]] || continue
    device="$pattern"
    break
  done
  [[ -n "$device" ]] || return 0
  [[ -r "$device" && -w "$device" ]] && return 0
  group_name="$(stat -c '%G' "$device")"
  echo ""
  echo "    WARNING: no read/write access to $device"
  echo "      LED will stay dark. Add yourself to the '$group_name' group:"
  echo "        sudo usermod -aG $group_name $USER"
  echo "      then log out and back in (or 'newgrp $group_name') for it to take effect."
}

# ----------------------------------------------------------------------------
# install
# ----------------------------------------------------------------------------

cmd_install() {
  [[ $EUID -ne 0 ]] || { echo "This script is user-level; run WITHOUT sudo." >&2; exit 1; }

  local platform
  platform="$(detect_platform)"
  [[ "$platform" != "unknown" ]] || { echo "unsupported platform: $(uname -s)" >&2; exit 1; }

  local python_bin
  python_bin="$(find_python_with_pyserial)"
  [[ -n "$python_bin" ]] || {
    echo "no python3 with pyserial found; install with: pip3 install pyserial" >&2
    exit 1
  }

  [[ -f "$SRC_DIR/states/claude.json" ]] || {
    echo "no state JSON files found in $SRC_DIR/states/" >&2; exit 1
  }
  [[ -d "$EXAMPLES_DIR" ]] || {
    echo "examples directory not found: $EXAMPLES_DIR" >&2; exit 1
  }

  local log_level="${CLAUDE_LED_LOG_LEVEL:-INFO}"

  echo "==> Installing claude-led (user-level)"
  echo "    install dir: $INSTALL_DIR"
  echo "    python:      $python_bin ($("$python_bin" --version 2>&1))"
  echo "    log level:   $log_level"
  echo ""

  # 1. Copy driver files
  mkdir -p "$INSTALL_DIR/states"
  cp "$SRC_DIR/led_cli.py"     "$INSTALL_DIR/"
  cp "$SRC_DIR/led_daemon.py"  "$INSTALL_DIR/"
  cp "$SRC_DIR/protocol.py"    "$INSTALL_DIR/"
  cp "$SRC_DIR/states/"*.json  "$INSTALL_DIR/states/"
  chmod 755 "$INSTALL_DIR"/{led_cli.py,led_daemon.py,protocol.py}
  chmod 644 "$INSTALL_DIR"/states/*.json
  echo "    copied: led_cli.py, led_daemon.py, protocol.py, states/"

  # 2. Mirror every examples/<source>/ → hooks/<source>/. Each subdirectory is
  #    one integration's caller-side glue (scripts, ready-to-paste configs).
  #    Adding a new integration (examples/foo/) needs no changes here.
  mkdir -p "$HOOK_DST_DIR"
  local example_src name
  shopt -s nullglob
  for example_src in "$EXAMPLES_DIR"/*/; do
    name="$(basename "$example_src")"
    rm -rf "$HOOK_DST_DIR/$name"
    cp -r "${example_src%/}" "$HOOK_DST_DIR/"
    # scripts +x so they can be invoked directly from settings.json;
    # JSON example configs stay 644.
    find "$HOOK_DST_DIR/$name" -name '*.sh'   -exec chmod 755 {} +
    find "$HOOK_DST_DIR/$name" -name '*.json' -exec chmod 644 {} +
    echo "    copied: hooks/$name/"
  done
  shopt -u nullglob

  # 3. Symlink led → led_cli.py on PATH
  mkdir -p "$BIN_DIR"
  ln -sf "$INSTALL_DIR/led_cli.py" "$LED_SYMLINK"
  echo "    symlink: $LED_SYMLINK -> $INSTALL_DIR/led_cli.py"

  # 4. Runtime dir doubles as install dir — chmod 700 so the socket (mode 600)
  # lives in a private dir.
  chmod 700 "$INSTALL_DIR" 2>/dev/null || true

  # 5. Warn if BIN_DIR is not on PATH (hooks call `led` and need it findable)
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
      echo ""
      echo "    NOTE: $BIN_DIR is not on your PATH."
      echo "      Add this to your shell rc (~/.zshrc, ~/.bashrc):"
      echo "        export PATH=\"$BIN_DIR:\$PATH\""
      ;;
  esac

  # 6. jq is required by hooks/claude/led-hook.sh; warn only when that
  #    integration was installed.
  [[ -d "$HOOK_DST_DIR/claude" ]] && ensure_jq

  # 7. Write + load auto-start unit
  case "$platform" in
    macos) install_macos_unit "$python_bin" "$log_level" ;;
    linux) install_linux_unit "$python_bin" "$log_level" ;;
  esac

  echo ""
  echo "==> Done. Daemon will start at login."
}

install_macos_unit() {
  local python_bin="$1" log_level="$2"
  mkdir -p "$(dirname "$MACOS_PLIST")"
  # Unload any prior version, then write + load.
  launchctl unload "$MACOS_PLIST" 2>/dev/null || true
  cat > "$MACOS_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${python_bin}</string>
    <string>${INSTALL_DIR}/led_daemon.py</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>StandardOutPath</key>
  <string>${LOG_FILE}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_FILE}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CLAUDE_LED_LOG_LEVEL</key>
    <string>${log_level}</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
</dict>
</plist>
EOF
  launchctl load "$MACOS_PLIST"
  echo "    auto-start: $MACOS_PLIST (launchd, RunAtLoad + KeepAlive)"
}

install_linux_unit() {
  local python_bin="$1" log_level="$2"
  ensure_user_bus_env
  mkdir -p "$(dirname "$SYSTEMD_UNIT")"
  cat > "$SYSTEMD_UNIT" <<EOF
[Unit]
Description=claude-led daemon
After=default.target

[Service]
Type=simple
ExecStart=${python_bin} ${INSTALL_DIR}/led_daemon.py
Restart=on-failure
RestartSec=2
Environment=CLAUDE_LED_LOG_LEVEL=${log_level}
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$LABEL.service"
  echo "    auto-start: $SYSTEMD_UNIT (systemd --user, enable --now + Restart=on-failure)"
  if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    echo ""
    echo "    NOTE: run 'loginctl enable-linger $USER' so the daemon starts at boot"
    echo "          (without linger, it starts when you log in)"
  fi
  warn_serial_access
}

# ----------------------------------------------------------------------------
# uninstall
# ----------------------------------------------------------------------------

cmd_uninstall() {
  [[ $EUID -ne 0 ]] || { echo "This script is user-level; run WITHOUT sudo." >&2; exit 1; }

  local platform
  platform="$(detect_platform)"

  echo "==> Uninstalling claude-led"

  case "$platform" in
    macos)
      if [[ -f "$MACOS_PLIST" ]]; then
        launchctl unload "$MACOS_PLIST" 2>/dev/null || true
        rm -f "$MACOS_PLIST"
        echo "    removed: $MACOS_PLIST"
      fi
      ;;
    linux)
      ensure_user_bus_env
      if [[ -f "$SYSTEMD_UNIT" ]]; then
        systemctl --user disable --now "$LABEL.service" 2>/dev/null || true
        rm -f "$SYSTEMD_UNIT"
        systemctl --user daemon-reload
        echo "    removed: $SYSTEMD_UNIT"
      fi
      ;;
  esac

  rm -f "$LED_SYMLINK"
  echo "    removed: $LED_SYMLINK"

  rm -rf "$INSTALL_DIR"
  echo "    removed: $INSTALL_DIR"
  echo "==> Done"
}

# ----------------------------------------------------------------------------

usage() {
  cat <<EOF
Usage: $0 <command>

Commands:
  install     Install claude-led (user-level) + enable auto-start at login
  uninstall   Stop the daemon and remove all installed files

Auto-start:
  macOS:  $MACOS_PLIST
  Linux:  $SYSTEMD_UNIT

Files installed:
  $INSTALL_DIR/{{led_cli,led_daemon,protocol}.py, states/*.json}
  $INSTALL_DIR/hooks/<source>/    (mirrored from examples/)
  $LED_SYMLINK → $INSTALL_DIR/led_cli.py
EOF
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    install)   cmd_install ;;
    uninstall) cmd_uninstall ;;
    -h|--help|help|"") usage ;;
    *) echo "unknown command: $cmd" >&2; usage >&2; exit 1 ;;
  esac
}

main "$@"
