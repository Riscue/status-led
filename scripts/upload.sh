#!/usr/bin/env bash
# Firmware upload - compiles and flashes the firmware onto the D1 Mini.
# Dependency: platformio (pip3 install platformio / brew install platformio)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT/firmware"

echo "==> Compiling and uploading firmware (D1 Mini must be connected via USB)..."
pio run -t upload

echo "==> Done."
