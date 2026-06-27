#!/usr/bin/env bash
# LED state test - runs each state for N seconds and prints the expected visual.
# Usage: scripts/test.sh [seconds_per_state]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DRIVER="$PROJECT_ROOT/driver/led_driver.py"
DURATION="${1:-5}"  # seconds to wait per state (default 5)

# state -> title -> description
states=(
  "idle|Idle|slow blue-gray breathe (3.5 s period)"
  "thinking|Model responding|purple scanner dot, back and forth"
  "tool|Tool running|fast yellow/orange pulse (0.9 s period)"
  "waiting|Waiting for input|slow white pulse (2.5 s period)"
  "success|Task completed|green fill animation (1.6 s loop)"
  "error|Error occurred|hard red blink (150 ms period)"
  "off|Off|all LEDs dark"
)

echo "==> Starting LED state test (each state for ${DURATION} s)"
echo ""

for entry in "${states[@]}"; do
  IFS='|' read -r state title desc <<< "$entry"
  printf "\033[1;36m[%s]\033[0m \033[1;37m%-25s\033[0m %s\n" "$state" "$title" "$desc"
  python3 "$DRIVER" "$state" --quiet
  sleep "$DURATION"
done

echo ""
echo "==> Test complete."
