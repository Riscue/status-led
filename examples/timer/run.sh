#!/usr/bin/env bash
# examples/timer/run.sh — LED strip timer (count-up or countdown).
#
# Usage:
#   ./run.sh <duration>                # count up: 0 → <duration>  (default)
#   ./run.sh --up <duration>           # explicit count-up
#   ./run.sh --countdown <duration>    # countdown: <duration> → 0
#
# <duration> accepts 30, 30s, 5m, 1h30m, ...
#
# Renders progress as a `level` bar via the firmware's level animation. In
# both modes the bar is GREEN while there is plenty of time left and turns
# RED as the duration nears its end:
#   count-up:  bar grows   0% → 100%, green → red
#   countdown: bar shrinks 100% → 0%, green → red
# When a run completes, a cyan↔magenta strobe plays for 3 s.
#
# Each tick is a persistent STATE under a per-invocation session id so the
# latest level wins; the session is cleared on exit. Requires the daemon
# running and `led` on $PATH (after ./scripts/install.sh).

set -euo pipefail

MODE="up"
DURATION_ARG=""

while (( $# > 0 )); do
  case "$1" in
    --up|--countup|--count-up) MODE="up"; shift ;;
    --down|--countdown) MODE="down"; shift ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --*) echo "unknown option: $1" >&2; exit 1 ;;
    *) DURATION_ARG="$1"; shift ;;
  esac
done

# Parse "1h30m", "5m", "90s", or "90" into total seconds. Bare trailing digits
# are interpreted as seconds.
parse_duration() {
  local s="$1" total=0 num="" i ch
  for ((i = 0; i < ${#s}; i++)); do
    ch="${s:$i:1}"
    case "$ch" in
      [0-9]) num+="$ch" ;;
      [sS])  [[ -n "$num" ]] || return 1; total=$((total + num));     num="" ;;
      [mM])  [[ -n "$num" ]] || return 1; total=$((total + num * 60));  num="" ;;
      [hH])  [[ -n "$num" ]] || return 1; total=$((total + num * 3600)); num="" ;;
      *) return 1 ;;
    esac
  done
  [[ -n "$num" ]] && total=$((total + num))
  ((total > 0)) || return 1
  echo "$total"
}

if [[ -z "$DURATION_ARG" ]]; then
  echo "usage: $0 [--up|--countdown] <duration>  (e.g. 30, 30s, 5m, or 1h30m)" >&2
  exit 1
fi
total=$(parse_duration "$DURATION_ARG") || {
  echo "invalid duration: $DURATION_ARG (try 30, 30s, 5m, or 1h30m)" >&2
  exit 1
}

SESSION="timer-$$"
trap 'led --quiet --end-session "$SESSION" 2>/dev/null || true' EXIT

# color_for_pct <pct> → "r,g,0"
# green at 0% (fresh), red at 100% (ending). Same gradient in both modes so
# the color always signals "how close to the end", independent of direction.
color_for_pct() {
  local pct="$1"
  local r g
  r=$(( pct * 255 / 100 ))
  g=$(( (100 - pct) * 255 / 100 ))
  echo "$r,$g,0"
}

# finish_animation — celebratory strobe shown when a run completes.
# Cyan ↔ magenta flash.
finish_animation() {
  led --quiet --session "$SESSION" --raw strobe \
    --rgb 0,255,255 --rgb2 255,0,255 --period 400
  sleep 3
}

if [[ "$MODE" == "down" ]]; then
  end=$(( $(date +%s) + total ))
  while :; do
    now=$(date +%s)
    remaining=$(( end - now ))
    (( remaining > 0 )) || break

    # elapsed fraction of the total duration; 0% at start → 100% at end
    pct=$(( (total - remaining) * 100 / total ))
    led --quiet --session "$SESSION" --raw level \
      --rgb "$(color_for_pct "$pct")" --level "$(( remaining * 100 / total ))"
    sleep 1
  done
  finish_animation
else
  # count-up
  start=$(date +%s)
  while :; do
    now=$(date +%s)
    elapsed=$(( now - start ))
    (( elapsed < total )) || break

    pct=$(( elapsed * 100 / total ))
    led --quiet --session "$SESSION" --raw level \
      --rgb "$(color_for_pct "$pct")" --level "$pct"
    sleep 1
  done
  finish_animation
fi
