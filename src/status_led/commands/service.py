"""`led service install` / `led service uninstall` — manage the daemon's
launchd/systemd auto-start unit.

Install:
  - Detect platform (macOS → launchd plist, Linux → systemd --user unit).
  - Locate the led binary (shutil.which).
  - Create ~/.status-led/ (mode 0o700) for socket/pid/log/secrets.
  - Generate the unit file with the right paths.
  - Load the unit (launchctl load / systemctl --user enable + restart).

Integrations ship bundled inside the wheel (read in-place via manifest.py);
nothing is copied to ~/.status-led/integrations/. Users override individual
files (e.g. states.json) by creating ~/.status-led/integrations/<name>/.

Uninstall:
  - Unload and delete the unit file.
  - Remove ~/.status-led/ (socket, pid, log, user overrides, secrets.env).

User-level only — never sudo. Linux USB-serial devices need group membership
(dialout/uucp); we warn but don't fix.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "tr.riscue.status-led"
INSTALL_DIR = Path.home() / ".status-led"
LOG_FILE = INSTALL_DIR / "daemon.log"
MACOS_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
SYSTEMD_UNIT = Path.home() / ".config" / "systemd" / "user" / f"{LABEL}.service"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _detect_platform() -> str:
    import platform
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        return "linux"
    return "unknown"


def _find_led_bin() -> str | None:
    """Locate the led entry point. After `pipx install .`, this is
    on PATH. Returns the absolute path or None.
    """
    return shutil.which("led")


def _find_python_with_pyserial() -> str | None:
    """Find a python3 binary that can import pyserial. Prefers the current
    interpreter, then scans known locations.
    """
    try:
        import serial  # noqa: F401
        return sys.executable
    except ImportError:
        pass
    for candidate in ("/opt/homebrew/bin/python3",
                      "/usr/local/bin/python3",
                      "/usr/bin/python3"):
        if not os.path.exists(candidate):
            continue
        try:
            subprocess.run([candidate, "-c", "import serial"],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            return candidate
        except (subprocess.CalledProcessError, OSError):
            continue
    return None


def _ensure_user_bus_env() -> None:
    """sudo -i and some headless shells strip XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS.
    Restore canonical defaults so `systemctl --user` works.
    """
    if not os.environ.get("XDG_RUNTIME_DIR"):
        os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        bus = Path(os.environ["XDG_RUNTIME_DIR"]) / "bus"
        if bus.exists():
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"


def _warn_serial_access() -> None:
    """On Linux, USB-serial devices are owned by dialout/uucp. Without group
    membership the daemon can't open the port and the LED stays dark.
    """
    if sys.platform != "linux":
        return
    device = None
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*"):
        matches = sorted(glob.glob(pattern))
        if matches:
            device = matches[0]
            break
    if not device:
        return
    if os.access(device, os.R_OK | os.W_OK):
        return
    group = "unknown"
    try:
        import stat as st
        st_info = os.stat(device)
        group = __import__("grp").getgrgid(st_info.st_gid).gr_name
    except (KeyError, OSError):
        pass
    print()
    print(f"    WARNING: no read/write access to {device}")
    print(f"      LED will stay dark. Add yourself to the '{group}' group:")
    print(f"        sudo usermod -aG {group} $USER")
    print(f"      then log out and back in (or 'newgrp {group}') for it to take effect.")


# ---------------------------------------------------------------------------
# runtime directory
# ---------------------------------------------------------------------------

def _ensure_runtime_dirs() -> None:
    """Create ~/.status-led/ with mode 0o700. Holds runtime files (socket,
    pid, log) and the user's secrets.env + optional integration overrides.
    Integrations themselves stay bundled inside the wheel — no copy step.
    """
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    INSTALL_DIR.chmod(0o700)


# ---------------------------------------------------------------------------
# unit file generation
# ---------------------------------------------------------------------------

def _write_macos_plist(led_bin: str, log_level: str) -> None:
    MACOS_PLIST.parent.mkdir(parents=True, exist_ok=True)
    # Unload any prior version before rewriting.
    if MACOS_PLIST.exists():
        subprocess.run(["launchctl", "unload", str(MACOS_PLIST)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    MACOS_PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{led_bin}</string>
    <string>daemon</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>StandardOutPath</key>
  <string>{LOG_FILE}</string>
  <key>StandardErrorPath</key>
  <string>{LOG_FILE}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>STATUS_LED_LOG_LEVEL</key>
    <string>{log_level}</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
</dict>
</plist>
""")
    subprocess.run(["launchctl", "load", str(MACOS_PLIST)], check=True)
    print(f"    auto-start: {MACOS_PLIST} (launchd, RunAtLoad + KeepAlive)")


def _write_systemd_unit(led_bin: str, log_level: str) -> None:
    _ensure_user_bus_env()
    SYSTEMD_UNIT.parent.mkdir(parents=True, exist_ok=True)
    SYSTEMD_UNIT.write_text(f"""[Unit]
Description=status-led daemon
After=default.target

[Service]
Type=simple
ExecStart={led_bin} daemon
Restart=on-failure
RestartSec=2
Environment=STATUS_LED_LOG_LEVEL={log_level}
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
""")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", f"{LABEL}.service"], check=True)
    # Restart (not just enable --now) so an in-memory daemon picks up new code
    # after an upgrade.
    subprocess.run(["systemctl", "--user", "restart", f"{LABEL}.service"], check=True)
    print(f"    auto-start: {SYSTEMD_UNIT} (systemd --user, enable + restart)")
    # Linger hint.
    linger = subprocess.run(
        ["loginctl", "show-user", os.environ.get("USER", "")],
        capture_output=True, text=True)
    if linger.returncode == 0 and "Linger=yes" not in linger.stdout:
        print()
        print(f"    NOTE: run 'loginctl enable-linger {os.environ.get('USER','')}' "
              "so the daemon starts at boot")
        print("          (without linger, it starts when you log in)")


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------

def _install(args) -> int:
    if os.geteuid() == 0:
        print("This command is user-level; run WITHOUT sudo.", file=sys.stderr)
        return 1

    platform_id = _detect_platform()
    if platform_id == "unknown":
        print(f"unsupported platform: {sys.platform}", file=sys.stderr)
        return 1

    python_bin = _find_python_with_pyserial()
    if python_bin is None:
        print("no python3 with pyserial found; install with: pip3 install pyserial",
              file=sys.stderr)
        return 1

    led_bin = _find_led_bin()
    if led_bin is None:
        print("'led' not on PATH; install the package with: pipx install .",
              file=sys.stderr)
        return 1

    log_level = args.log_level or os.environ.get("STATUS_LED_LOG_LEVEL", "INFO")

    print("==> Installing status-led (user-level)")
    print(f"    install dir: {INSTALL_DIR}")
    print(f"    python:      {python_bin}")
    print(f"    led:         {led_bin}")
    print(f"    log level:   {log_level}")
    print()

    _ensure_runtime_dirs()

    if platform_id == "macos":
        _write_macos_plist(led_bin, log_level)
    else:
        _write_systemd_unit(led_bin, log_level)

    _warn_serial_access()

    print()
    print("    Integrations are bundled (no copy step). To override a state")
    print("    profile, create ~/.status-led/integrations/<name>/states.json")
    print("    (per-file fallback — bundled is used otherwise).")
    print()
    print("    Claude Code: point hooks at `led claude` (see")
    print("    integrations/claude/settings_hooks_example.json).")
    print()
    print("    Credentials: cp secrets.env.example ~/.status-led/secrets.env")
    print()
    print("==> Done. Daemon will start at login.")
    return 0


def _uninstall() -> int:
    if os.geteuid() == 0:
        print("This command is user-level; run WITHOUT sudo.", file=sys.stderr)
        return 1

    platform_id = _detect_platform()
    print("==> Uninstalling status-led")

    if platform_id == "macos":
        if MACOS_PLIST.exists():
            subprocess.run(["launchctl", "unload", str(MACOS_PLIST)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            MACOS_PLIST.unlink()
            print(f"    removed: {MACOS_PLIST}")
    elif platform_id == "linux":
        _ensure_user_bus_env()
        if SYSTEMD_UNIT.exists():
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", f"{LABEL}.service"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            SYSTEMD_UNIT.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"])
            print(f"    removed: {SYSTEMD_UNIT}")

    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
        print(f"    removed: {INSTALL_DIR}")

    print("==> Done")
    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="led service",
        description="Install or uninstall the daemon's launchd/systemd auto-start unit.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    install_parser = sub.add_parser("install",
                                    help="Enable daemon auto-start at login")
    install_parser.add_argument("--log-level", default=None, metavar="LEVEL",
                                help="Daemon log level baked into the unit file "
                                     "(default INFO or $STATUS_LED_LOG_LEVEL)")
    sub.add_parser("uninstall", help="Stop the daemon and remove installed files")
    args = parser.parse_args(argv)

    if args.cmd == "install":
        return _install(args)
    if args.cmd == "uninstall":
        return _uninstall()
    parser.print_help()
    return 2
