"""Firmware wire-line format.

Single source of truth for how an animation + RGB + period + brightness
becomes the byte sequence the ESP8266 firmware parses. Pure module — no
I/O, no other status_led imports.

The daemon forwards whatever the CLI resolves; the firmware parses it.
This module is the only place that knows the format. Adding a new
animation means changing ANIMATIONS / PERIOD_ANIMATIONS here and updating
build_wire_line; the daemon, transport, and firmware need no changes.
"""
from __future__ import annotations

# All animations the firmware understands. Keep in sync with firmware/src/main.cpp.
ANIMATIONS: set[str] = {"solid", "breathe", "blink", "scanner", "fill",
                        "strobe", "level", "converge",
                        "pulse", "sparkle", "heartbeat", "bounce", "off"}

# Animations that need a period_ms parameter. Solid and level are static; off
# takes no parameters at all.
PERIOD_ANIMATIONS: set[str] = {"breathe", "blink", "scanner", "fill", "converge",
                               "strobe", "pulse", "sparkle", "heartbeat", "bounce"}


def _clamp8(v: int) -> int:
    return max(0, min(255, int(v)))


def _clamp_pct(v: int) -> int:
    return max(0, min(100, int(v)))


def validate_period(period, anim: str) -> int:
    """Period is required and must be a number >= 50 ms for time-based animations.

    Called only from build_wire_line; exposed so tests can target it directly.
    """
    if isinstance(period, bool) or period is None:
        raise ValueError(f"period required for animation {anim!r} (number >= 50 ms)")
    if not isinstance(period, (int, float)):
        raise ValueError(f"period for animation {anim!r} must be a number")
    if period < 50:
        raise ValueError(f"period for animation {anim!r} must be >= 50 ms")
    return int(period)


def build_wire_line(anim: str,
                    rgb: tuple[int, int, int] | None = None,
                    rgb2: tuple[int, int, int] | None = None,
                    period: int | None = None,
                    level: int | None = None,
                    brightness: int = 100) -> str:
    """Single source of truth for the firmware wire-line format.

    Per-animation requirements:
      off                                            → "off" (other args ignored)
      solid    rgb                                   → "solid r g b [pct]"
      level    rgb, level                            → "level r g b level [pct]"
      strobe   rgb, rgb2, period                     → "strobe r g b r2 g2 b2 period [pct]"
      breathe/blink/scanner/fill/converge/pulse/sparkle/heartbeat/bounce
               rgb, period                           → "<anim> r g b period [pct]"
    """
    if anim not in ANIMATIONS:
        raise ValueError(f"invalid animation {anim!r} (valid: {sorted(ANIMATIONS)})")
    if anim == "off":
        return "off"
    if rgb is None:
        raise ValueError(f"rgb required for animation {anim!r}")
    r, g, b = (_clamp8(rgb[0]), _clamp8(rgb[1]), _clamp8(rgb[2]))
    pct = _clamp_pct(brightness)
    if anim == "solid":
        return f"solid {r} {g} {b} {pct}"
    if anim == "level":
        if level is None:
            raise ValueError(f"level required for animation {anim!r} (0-100)")
        return f"level {r} {g} {b} {_clamp_pct(level)} {pct}"
    if anim == "strobe":
        if rgb2 is None:
            raise ValueError(f"rgb2 required for animation {anim!r}")
        r2, g2, b2 = (_clamp8(rgb2[0]), _clamp8(rgb2[1]), _clamp8(rgb2[2]))
        return f"strobe {r} {g} {b} {r2} {g2} {b2} {validate_period(period, anim)} {pct}"
    return f"{anim} {r} {g} {b} {validate_period(period, anim)} {pct}"
