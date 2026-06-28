#!/usr/bin/env bash
# install.sh — install / uninstall / control the claude-led daemon.
#
# System files installed under /opt/claude-led (root-owned):
#   /opt/claude-led/led_cli.py        (CLI client; symlinked as /usr/local/bin/led)
#   /opt/claude-led/led_daemon.py     (persistent daemon)
#   /opt/claude-led/states/*.json     (state profiles)
#   /opt/claude-led/install.sh        (a copy of this script, for uninstall)
#   /usr/local/bin/led               (symlink -> /opt/claude-led/led_cli.py)
#
# User files (no root required):
#   ~/Library/LaunchAgents/tr.riscue.claude-led.plist  (macOS)
#   ~/.config/systemd/user/tr.riscue.claude-led.service (Linux)
#   ~/.claude-led/{led.sock,daemon.pid,daemon.log}      (runtime + daemon log)
#
# Usage:
#   sudo ./install.sh install          # install everything + start
#   sudo ./install.sh uninstall        # remove everything
#   ./install.sh start | stop | restart | status | logs
#   ./install.sh foreground [-- args]  # run daemon in foreground (debug)

set -eu

LABEL="tr.riscue.claude-led"
INSTALL_PREFIX="/opt/claude-led"
DAEMON_PY="$INSTALL_PREFIX/led_daemon.py"
CLI_PY="$INSTALL_PREFIX/led_cli.py"
LED_SYMLINK="/usr/local/bin/led"
SOCKET_DIR="$HOME/.claude-led"
PID_FILE="$SOCKET_DIR/daemon.pid"
LOG_FILE="$SOCKET_DIR/daemon.log"
MACOS_PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
SYSTEMD_DEST="$HOME/.config/systemd/user/$LABEL.service"

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

detect_platform() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux)  echo "linux" ;;
    *)      echo "unknown" ;;
  esac
}

is_under_supervisor() {
  case "$(detect_platform)" in
    macos) launchctl list "$LABEL" >/dev/null 2>&1 ;;
    linux) systemctl --user is-active "$LABEL.service" >/dev/null 2>&1 ;;
    *)     return 1 ;;
  esac
}

is_pid_alive() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

# ----------------------------------------------------------------------------
# install / uninstall (require sudo)
# ----------------------------------------------------------------------------

cmd_install() {
  [[ $EUID -eq 0 ]] || { echo "Run with: sudo $0 install" >&2; exit 1; }
  [[ -n "${SUDO_USER:-}" ]] || { echo "SUDO_USER not set; invoke via sudo" >&2; exit 1; }

  local target_user="$SUDO_USER"
  if ! sudo -iu "$target_user" true 2>/dev/null; then
    echo "cannot switch to user '$target_user' via sudo" >&2
    exit 1
  fi

  # Discover a python3 with pyserial installed. We cannot rely on `command -v`
  # under `sudo -i` because secure_path hides Homebrew (and similar) — scan
  # known locations explicitly. Scan as the target user so pyserial installed
  # via `pip3 install --user` (common on system Python due to PEP 0668) is
  # visible.
  local user_python=""
  for candidate in \
      "/opt/homebrew/bin/python3" \
      "/usr/local/bin/python3" \
      "/usr/bin/python3"; do
    if [[ -x "$candidate" ]] && sudo -iu "$target_user" "$candidate" -c 'import serial' 2>/dev/null; then
      user_python="$candidate"
      break
    fi
  done
  [[ -n "$user_python" ]] || {
    echo "no python3 with pyserial found in known locations" >&2
    echo "  install pyserial for some python3, e.g.:" >&2
    echo "    pip3 install pyserial        (Homebrew python)" >&2
    echo "    /usr/bin/pip3 install pyserial  (system python)" >&2
    exit 1
  }

  local log_level="${CLAUDE_LED_LOG_LEVEL:-INFO}"

  echo "==> Installing claude-led"
  echo "    target user: $target_user"
  echo "    python:      $user_python ($("$user_python" --version 2>&1))"
  echo "    prefix:      $INSTALL_PREFIX"
  echo "    log level:   $log_level"
  echo ""

  # Resolve source files. From the repo, they live under PROJECT_ROOT/driver/.
  # From the installed copy at /opt/claude-led/install.sh (e.g. a refresh after
  # the repo was deleted), they sit next to this script. Detect which.
  local script_dir src_cli src_daemon src_states=()
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "$script_dir/led_cli.py" ]]; then
    src_cli="$script_dir/led_cli.py"
    src_daemon="$script_dir/led_daemon.py"
    src_states=("$script_dir/states"/*.json)
  elif [[ -f "$PROJECT_ROOT/driver/led_cli.py" ]]; then
    src_cli="$PROJECT_ROOT/driver/led_cli.py"
    src_daemon="$PROJECT_ROOT/driver/led_daemon.py"
    src_states=("$PROJECT_ROOT/driver/states"/*.json)
  else
    echo "could not locate led_cli.py relative to $SCRIPT_PATH" >&2
    exit 1
  fi
  # Without nullglob, a non-matching glob leaves the literal pattern as the
  # sole array element; verify the first entry is a real file.
  if [[ ! -f "${src_states[0]:-}" ]]; then
    echo "no state JSON files found alongside $src_cli" >&2
    exit 1
  fi

  # /opt/claude-led files
  mkdir -p "$INSTALL_PREFIX/states"
  cp "$src_cli"          "$INSTALL_PREFIX/led_cli.py"
  cp "$src_daemon"       "$INSTALL_PREFIX/led_daemon.py"
  cp "${src_states[@]}"  "$INSTALL_PREFIX/states/"
  cp "$SCRIPT_PATH"      "$INSTALL_PREFIX/install.sh"
  chmod 755 "$INSTALL_PREFIX" \
            "$INSTALL_PREFIX/led_cli.py" \
            "$INSTALL_PREFIX/led_daemon.py" \
            "$INSTALL_PREFIX/install.sh"
  chmod 644 "$INSTALL_PREFIX"/states/*.json

  # /usr/local/bin/led symlink
  ln -sf "$INSTALL_PREFIX/led_cli.py" "$LED_SYMLINK"

  echo "    installed: $INSTALL_PREFIX/{led_cli.py,led_daemon.py,states/,install.sh}"
  echo "    symlink:   $LED_SYMLINK -> $INSTALL_PREFIX/led_cli.py"
  echo ""

  # Drop to target user for user-level unit installation
  echo "==> Installing user unit for $target_user..."
  sudo -iu "$target_user" "$SCRIPT_PATH" install-user-unit "$user_python" "$log_level"
}

cmd_uninstall() {
  [[ $EUID -eq 0 ]] || { echo "Run with: sudo $0 uninstall" >&2; exit 1; }
  [[ -n "${SUDO_USER:-}" ]] || { echo "SUDO_USER not set; invoke via sudo" >&2; exit 1; }

  local target_user="$SUDO_USER"

  echo "==> Uninstalling claude-led"

  # First drop to user to unload + remove the user unit
  sudo -iu "$target_user" "$SCRIPT_PATH" uninstall-user-unit || true

  rm -f "$LED_SYMLINK"
  echo "    removed: $LED_SYMLINK"

  rm -rf "$INSTALL_PREFIX"
  echo "    removed: $INSTALL_PREFIX"

  echo "==> Done"
}

# ----------------------------------------------------------------------------
# install-user-unit / uninstall-user-unit (run as target user via sudo -iu)
# ----------------------------------------------------------------------------

cmd_install_user_unit() {
  [[ $EUID -ne 0 ]] || { echo "install-user-unit must run as user, not root" >&2; exit 1; }
  local python_bin="${1:-$(command -v python3)}"
  local log_level="${2:-${CLAUDE_LED_LOG_LEVEL:-INFO}}"
  [[ -n "$python_bin" ]] || { echo "python3 not found" >&2; exit 1; }
  case "$(detect_platform)" in
    macos) install_macos_user_unit "$python_bin" "$log_level" ;;
    linux) install_linux_user_unit "$python_bin" "$log_level" ;;
    *) echo "unsupported platform: $(detect_platform)" >&2; exit 1 ;;
  esac
}

cmd_uninstall_user_unit() {
  [[ $EUID -ne 0 ]] || { echo "uninstall-user-unit must run as user, not root" >&2; exit 1; }
  case "$(detect_platform)" in
    macos)
      if [[ -f "$MACOS_PLIST_DEST" ]]; then
        launchctl unload "$MACOS_PLIST_DEST" 2>/dev/null || true
        rm -f "$MACOS_PLIST_DEST"
        echo "    removed: $MACOS_PLIST_DEST"
      fi
      ;;
    linux)
      if [[ -f "$SYSTEMD_DEST" ]]; then
        systemctl --user disable --now "$LABEL.service" 2>/dev/null || true
        rm -f "$SYSTEMD_DEST"
        systemctl --user daemon-reload
        echo "    removed: $SYSTEMD_DEST"
      fi
      ;;
  esac
}

install_macos_user_unit() {
  local python_bin="$1"
  local log_level="${2:-INFO}"
  mkdir -p "$(dirname "$MACOS_PLIST_DEST")"
  mkdir -p "$SOCKET_DIR"
  if ! chmod 700 "$SOCKET_DIR" 2>/dev/null; then
    echo "warning: could not chmod 700 $SOCKET_DIR — if it is owned by root, chown it to yourself" >&2
  fi

  if [[ -f "$MACOS_PLIST_DEST" ]]; then
    launchctl unload "$MACOS_PLIST_DEST" 2>/dev/null || true
  fi

  write_launchd_plist "$python_bin" "$MACOS_PLIST_DEST" "$LOG_FILE" "$log_level"
  launchctl load "$MACOS_PLIST_DEST"
  echo "    installed: $MACOS_PLIST_DEST"
  echo "    python:    $python_bin"
  echo "    log level: $log_level"
  launchctl list "$LABEL" 2>&1 || true
}

install_linux_user_unit() {
  local python_bin="$1"
  local log_level="${2:-INFO}"
  mkdir -p "$(dirname "$SYSTEMD_DEST")"
  write_systemd_unit "$python_bin" "$SYSTEMD_DEST" "$log_level"
  systemctl --user daemon-reload
  systemctl --user enable --now "$LABEL.service"
  if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
    echo ""
    echo "    NOTE: run 'loginctl enable-linger $USER' so the unit starts at boot"
  fi
  systemctl --user status "$LABEL.service" 2>&1 || true
}

write_launchd_plist() {
  local python_bin="$1"
  local dest="$2"
  local log_file="$3"
  local log_level="${4:-INFO}"
  cat > "$dest" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>${python_bin}</string>
    <string>${DAEMON_PY}</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>ThrottleInterval</key>
  <integer>5</integer>

  <key>StandardOutPath</key>
  <string>${log_file}</string>

  <key>StandardErrorPath</key>
  <string>${log_file}</string>

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
}

write_systemd_unit() {
  local python_bin="$1"
  local dest="$2"
  local log_level="${3:-INFO}"
  cat > "$dest" <<EOF
[Unit]
Description=claude-led daemon
After=default.target

[Service]
Type=simple
ExecStart=${python_bin} ${DAEMON_PY}
Restart=on-failure
RestartSec=2
Environment=CLAUDE_LED_LOG_LEVEL=${log_level}
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
}

# ----------------------------------------------------------------------------
# start / stop / restart / status / logs / foreground (no sudo)
# ----------------------------------------------------------------------------

cmd_foreground() {
  [[ -f "$DAEMON_PY" ]] || { echo "$DAEMON_PY not found; run 'sudo $0 install' first" >&2; exit 1; }
  exec "$DAEMON_PY" "$@"
}

cmd_start() {
  if is_under_supervisor; then
    echo "supervisor manages $LABEL; delegating start"
    case "$(detect_platform)" in
      macos) launchctl kickstart -k "gui/$(id -u)/$LABEL" ;;
      linux) systemctl --user restart "$LABEL.service" ;;
    esac
    return
  fi
  [[ -f "$DAEMON_PY" ]] || { echo "$DAEMON_PY not found; run 'sudo $0 install' first" >&2; exit 1; }
  if [[ -f "$PID_FILE" ]] && is_pid_alive "$(cat "$PID_FILE" 2>/dev/null)"; then
    echo "daemon already running (pid $(cat "$PID_FILE"))"
    return 0
  fi
  rm -f "$PID_FILE"
  mkdir -p "$SOCKET_DIR"
  chmod 700 "$SOCKET_DIR"
  echo "starting daemon (manual; log: $LOG_FILE)"
  nohup "$DAEMON_PY" >>"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  sleep 0.2
  if is_pid_alive "$(cat "$PID_FILE")"; then
    echo "started (pid $(cat "$PID_FILE"))"
  else
    echo "failed to start; check $LOG_FILE" >&2
    return 1
  fi
}

cmd_stop() {
  if is_under_supervisor; then
    echo "supervisor manages $LABEL; delegating stop"
    case "$(detect_platform)" in
      macos) launchctl kill TERM "gui/$(id -u)/$LABEL" ;;
      linux) systemctl --user stop "$LABEL.service" ;;
    esac
    return
  fi
  if [[ ! -f "$PID_FILE" ]]; then
    echo "no pid file; not running under $0"
    return 0
  fi
  local pid; pid="$(cat "$PID_FILE" 2>/dev/null)"
  if is_pid_alive "$pid"; then
    echo "stopping daemon (pid $pid)"
    kill -TERM "$pid"
    for _ in {1..50}; do
      is_pid_alive "$pid" || break
      sleep 0.1
    done
    if is_pid_alive "$pid"; then
      echo "daemon did not exit; sending KILL" >&2
      kill -KILL "$pid" 2>/dev/null || true
    fi
  else
    echo "pid $pid not alive; cleaning pid file"
  fi
  rm -f "$PID_FILE"
}

cmd_restart() {
  cmd_stop
  cmd_start
}

cmd_status() {
  if is_under_supervisor; then
    echo "under supervisor:"
    case "$(detect_platform)" in
      macos) launchctl list "$LABEL" 2>&1 ;;
      linux) systemctl --user status "$LABEL.service" 2>&1 ;;
    esac
    return
  fi
  if [[ -f "$PID_FILE" ]] && is_pid_alive "$(cat "$PID_FILE" 2>/dev/null)"; then
    echo "running (pid $(cat "$PID_FILE"), manual)"
    return
  fi
  echo "not running"
}

cmd_logs() {
  case "$(detect_platform)" in
    macos)
      if [[ -f "$LOG_FILE" ]]; then
        tail -f "$LOG_FILE"
      else
        echo "no log file found; run '$0 install' or '$0 start' first" >&2
        return 1
      fi
      ;;
    linux)
      if is_under_supervisor; then
        journalctl --user -u "$LABEL.service" -f
      elif [[ -f "$LOG_FILE" ]]; then
        tail -f "$LOG_FILE"
      else
        echo "no log file found" >&2
        return 1
      fi
      ;;
  esac
}

usage() {
  cat <<EOF
Usage: $0 <command>

Install / uninstall (require sudo):
  install       install /opt/claude-led + /usr/local/bin/led + user unit
  uninstall     remove all of the above

Daemon control (no sudo; managed by launchd/systemd if installed):
  start         start the daemon (delegates to supervisor if installed)
  stop          stop the daemon
  restart       stop then start
  status        show daemon / supervisor status
  logs          tail daemon logs
  foreground    run the daemon in the foreground (debug); extra args pass through

Layout after install:
  /opt/claude-led/{led_cli.py, led_daemon.py, states/, install.sh}
  /usr/local/bin/led -> /opt/claude-led/led_cli.py
  ~/Library/LaunchAgents/$LABEL.plist   (macOS)
  ~/.config/systemd/user/$LABEL.service (Linux)
EOF
}

main() {
  local cmd="${1:-}"
  [[ $# -gt 0 ]] && shift
  case "$cmd" in
    install)            cmd_install ;;
    uninstall)          cmd_uninstall ;;
    install-user-unit)  cmd_install_user_unit "$@" ;;
    uninstall-user-unit) cmd_uninstall_user_unit ;;
    start)              cmd_start ;;
    stop)               cmd_stop ;;
    restart)            cmd_restart ;;
    status)             cmd_status ;;
    logs)               cmd_logs ;;
    foreground)         cmd_foreground "$@" ;;
    -h|--help|help|"")  usage ;;
    *) echo "unknown command: $cmd" >&2; usage >&2; exit 1 ;;
  esac
}

main "$@"
