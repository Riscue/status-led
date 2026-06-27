#!/usr/bin/env bash
# LED animation test - sends each animation directly to the firmware for
# `DURATION` seconds so you can visually verify the generic protocol.
#
# This bypasses state profiles and exercises the firmware's raw command surface,
# so changes to the driver's state mapping don't mask firmware regressions.
#
# Usage: scripts/test.sh [seconds_per_animation]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DRIVER="$PROJECT_ROOT/driver/led_driver.py"
DURATION="${1:-3}"  # seconds to wait per animation (default 5)

# animation|r,g,b|period_ms|brightness_pct|title|description
# off has no extra params; solid takes no period.
anims=(
  "solid|0,0,255|-|100|Solid blue @100%|tüm LED'ler sabit, tam parlaklık"
  "solid|0,0,255|-|30|Solid blue @30%|tüm LED'ler sabit, kısık"
  "breathe|0,50,220|3500|100|Breathe blue|siyahtan maviye yavaş pulse"
  "blink|180,0,0|300|100|Blink red|150ms açık / 150ms kapalı"
  "scanner|90,0,170|1600|100|Scanner purple|nokta ileri-geri"
  "fill|0,220,0|1600|100|Fill green|tek tek, sonra hold"
  "off|-|-|-|Off|tüm LED'ler kapalı"
)

echo "==> Starting LED animation test (each for ${DURATION} s)"
echo ""

for entry in "${anims[@]}"; do
  IFS='|' read -r anim rgb period pct title desc <<< "$entry"
  printf "\033[1;36m[%-8s]\033[0m \033[1;37m%-22s\033[0m %s\n" "$anim" "$title" "$desc"

  cmd=(python3 "$DRIVER" --raw "$anim" --quiet)
  [[ "$rgb"    != "-" ]] && cmd+=(--rgb "$rgb")
  [[ "$period" != "-" ]] && cmd+=(--period "$period")
  [[ "$pct"    != "-" ]] && cmd+=(--brightness "$pct")
  "${cmd[@]}"
  sleep "$DURATION"
done

echo ""
echo "==> Test complete."
