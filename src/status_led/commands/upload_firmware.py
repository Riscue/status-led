"""`led upload-firmware` — compile + flash firmware via platformio.

Wraps `pio run -t upload` in firmware/. Locates firmware/ from the package
layout (installed: bundled alongside the package; dev: repo root). Override
with --firmware-dir.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


def _find_firmware_dir() -> str | None:
    """Locate firmware/. Installed copies live next to the package (build
    hook mirrors it to status_led/firmware/); in a dev checkout it's at
    the repo root, three levels above this file.
    """
    here = os.path.dirname(os.path.realpath(__file__))
    packaged = os.path.join(here, "..", "firmware")
    if os.path.isdir(packaged):
        return os.path.realpath(packaged)
    repo_root = os.path.realpath(os.path.join(here, "..", "..", ".."))
    candidate = os.path.join(repo_root, "firmware")
    if os.path.isdir(candidate):
        return candidate
    return None


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="led upload-firmware",
        description="Compile and flash the firmware to the ESP8266 via platformio.",
    )
    parser.add_argument("--firmware-dir",
                        help="Override firmware/ directory (default: bundled "
                             "or repo-root firmware/)")
    parser.add_argument("--monitor", action="store_true",
                        help="Open the serial monitor after upload")
    args = parser.parse_args(argv)

    firmware_dir = args.firmware_dir or _find_firmware_dir()
    if firmware_dir is None or not os.path.isdir(firmware_dir):
        print("firmware/ not found. Pass --firmware-dir PATH to override.",
              file=sys.stderr)
        return 1

    if shutil.which("pio") is None:
        print("platformio not found; install with: pip3 install platformio "
              "or brew install platformio", file=sys.stderr)
        return 1

    print(f"==> Compiling and uploading firmware (cwd: {firmware_dir})")
    try:
        subprocess.run(["pio", "run", "-t", "upload"],
                       cwd=firmware_dir, check=True)
    except subprocess.CalledProcessError as e:
        print(f"upload failed with exit code {e.returncode}", file=sys.stderr)
        return e.returncode

    if args.monitor:
        subprocess.run(["pio", "device", "monitor"], cwd=firmware_dir)

    print("==> Done.")
    return 0
