/*
 * WS2812B Animation Driver - Wemos D1 Mini (ESP8266)
 * ---------------------------------------------------------------------------
 * Receives single-line commands over USB-serial and renders the requested
 * animation. The firmware is stateless regarding any upstream application
 * (it knows nothing about the calling source, hooks, or "states"): it only knows
 * the animation name + color + speed + brightness. The host-side driver
 * owns any higher-level mapping.
 *
 * Wire protocol (one ASCII line per command, 115200 baud, newline-terminated):
 *   solid    r g b [bright_pct]
 *   breathe  r g b period_ms [bright_pct]   black -> color, sin, period = full cycle
 *   blink    r g b period_ms [bright_pct]   period/2 on + period/2 off
 *   scanner  r g b period_ms [bright_pct]   dot sweeps back and forth
 *   fill     r g b period_ms [bright_pct]   LEDs light one-by-one, hold when all lit
 *   strobe   r1 g1 b1 r2 g2 b2 period_ms [bright_pct]   period/2 color1, period/2 color2
 *   level    r g b level_pct [bright_pct]   static bar: ceil(level_pct*N/100) LEDs lit from index 0
 *   converge r g b period_ms [bright_pct]   edges light inward, meet at center, retreat back (triangle wave)
 *   off
 *
 *   bright_pct: 0-100 (optional, default 100). Scales below MAX_BRIGHTNESS.
 *   level_pct:  0-100 (required for `level`). Fraction of LEDs to light.
 *
 * Unknown animation names or malformed parameter counts are silently ignored
 * (the previous animation continues). RGB is clamped to 0-255, period to
 * >= MIN_PERIOD_MS, bright_pct to 0-100.
 *
 * Hardware: Wemos D1 Mini (ESP8266, CH340/CP2104 USB-serial chip)
 * Library:  Adafruit NeoPixel@1.15.5 (NOT FastLED -- FastLED has known timing
 *           issues on the ESP8266; the NeoPixel library is reliable on this board)
 *
 * Setup (PlatformIO):
 *   pio run -t upload    (flash to the board, dependencies are downloaded automatically)
 *
 * POWER NOTE: There is no extra power cable -- a single USB cable carries both
 * power and data. 8 LEDs at full white can draw ~500mA; USB port limits range
 * from 500mA (USB 2.0) to 900mA (USB-C). MAX_BRIGHTNESS=32 (about 12% duty)
 * keeps the peak current well within USB 2.0 limits. bright_pct only scales
 * BELOW this ceiling -- it can never exceed it. Do NOT raise MAX_BRIGHTNESS
 * without an external power supply.
 *
 * Wiring (from the strip's 3-pin input connector or pads):
 *   D1 Mini "5V" pin  -> Strip 5V   (raw 5V from USB; when the D1 Mini is
 *                                     plugged into USB, this pin outputs 5V)
 *   D1 Mini "G" (GND) -> Strip GND
 *   D1 Mini "D4" pin  -> Strip DIN (data) -- D4 = GPIO2 (see DATA_PIN below)
 *
 *   IMPORTANT: NEVER wire 5V and GND backwards, the WS2812B chips will die
 *   instantly. Before connecting, always verify with a multimeter which pin
 *   of the connector is 5V and which is GND (typically red=5V, black=GND,
 *   white/yellow=data, but confirm your strip's own color coding).
 */

#include <Arduino.h>
#include <Adafruit_NeoPixel.h>

#define DATA_PIN        2      // D4 = GPIO2 (Wemos D1 Mini silkscreen "D4")
// Note: D4 is a boot-strapping pin + onboard blue LED. After boot it can be
// used as WS2812B data. We keep it here because the cable is already soldered
// to this pin; for a fresh build prefer D2 (GPIO4).
#define NUM_LEDS        8      // WS2812B strip
#define MAX_BRIGHTNESS  32     // 0-255, USB-safe upper bound (see POWER NOTE)
#define SERIAL_BAUD     115200
#define MIN_PERIOD_MS   50     // sub-50ms periods look broken at our 16ms loop cadence

Adafruit_NeoPixel strip(NUM_LEDS, DATA_PIN, NEO_GRB + NEO_KHZ800);

enum Animation {
  ANIM_SOLID,
  ANIM_BREATHE,
  ANIM_BLINK,
  ANIM_SCANNER,
  ANIM_FILL,
  ANIM_STROBE,
  ANIM_LEVEL,
  ANIM_CONVERGE,
  ANIM_OFF
};

struct AnimState {
  Animation anim = ANIM_OFF;
  uint8_t  r = 0, g = 0, b = 0;
  uint8_t  r2 = 0, g2 = 0, b2 = 0;   // second color (strobe)
  uint8_t  levelPct = 100;           // 0-100, fraction of LEDs to light (level)
  uint16_t period = 0;
  uint8_t  brightPct = 100;          // 0-100, scales below MAX_BRIGHTNESS
  unsigned long startedAt = 0;
} current;

uint8_t clamp8(int v)   { return v < 0 ? 0 : (v > 255 ? 255 : (uint8_t)v); }
uint8_t clampPct(int v) { return v < 0 ? 0 : (v > 100 ? 100 : (uint8_t)v); }

void applyAnimation(Animation a, uint8_t r, uint8_t g, uint8_t b,
                    uint16_t period, uint8_t pct,
                    uint8_t r2 = 0, uint8_t g2 = 0, uint8_t b2 = 0,
                    uint8_t levelPct = 100) {
  current.anim = a;
  current.r = r;
  current.g = g;
  current.b = b;
  current.r2 = r2;
  current.g2 = g2;
  current.b2 = b2;
  current.levelPct = levelPct;
  current.period = period;
  current.brightPct = pct;
  current.startedAt = millis();
  // MAX_BRIGHTNESS is the USB-safe ceiling; bright_pct scales within it.
  strip.setBrightness((uint8_t)((uint16_t)MAX_BRIGHTNESS * pct / 100));
}

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toLowerCase();
  if (cmd.length() == 0) return;

  int sp = cmd.indexOf(' ');
  String name = (sp < 0) ? cmd : cmd.substring(0, sp);
  String rest = (sp < 0) ? String("") : cmd.substring(sp + 1);
  rest.trim();

  if (name == "off") {
    applyAnimation(ANIM_OFF, 0, 0, 0, 0, 0);
    return;
  }

  if (name == "solid") {
    int r, g, b, pct = 100;
    int n = sscanf(rest.c_str(), "%d %d %d %d", &r, &g, &b, &pct);
    if (n >= 3) {
      applyAnimation(ANIM_SOLID, clamp8(r), clamp8(g), clamp8(b), 0, clampPct(pct));
    }
    return;
  }

  if (name == "level") {
    int r, g, b, levelpct, pct = 100;
    int n = sscanf(rest.c_str(), "%d %d %d %d %d", &r, &g, &b, &levelpct, &pct);
    if (n >= 4) {
      applyAnimation(ANIM_LEVEL, clamp8(r), clamp8(g), clamp8(b), 0, clampPct(pct),
                     0, 0, 0, clampPct(levelpct));
    }
    return;
  }

  if (name == "strobe") {
    int r1, g1, b1, r2, g2, b2, period, pct = 100;
    int n = sscanf(rest.c_str(), "%d %d %d %d %d %d %d %d",
                   &r1, &g1, &b1, &r2, &g2, &b2, &period, &pct);
    if (n >= 7 && period >= MIN_PERIOD_MS) {
      applyAnimation(ANIM_STROBE, clamp8(r1), clamp8(g1), clamp8(b1),
                     (uint16_t)period, clampPct(pct),
                     clamp8(r2), clamp8(g2), clamp8(b2));
    }
    return;
  }

  if (name == "breathe" || name == "blink" || name == "scanner"
      || name == "fill" || name == "converge") {
    int r, g, b, period, pct = 100;
    int n = sscanf(rest.c_str(), "%d %d %d %d %d", &r, &g, &b, &period, &pct);
    if (n >= 4 && period >= MIN_PERIOD_MS) {
      Animation a = (name == "breathe") ? ANIM_BREATHE
                  : (name == "blink")   ? ANIM_BLINK
                  : (name == "scanner") ? ANIM_SCANNER
                  : (name == "fill")    ? ANIM_FILL
                                        : ANIM_CONVERGE;
      applyAnimation(a, clamp8(r), clamp8(g), clamp8(b),
                     (uint16_t)period, clampPct(pct));
    }
    return;
  }
  // Unknown command: silently ignored.
}

// ---- Render functions (read from `current`, brightness already applied via setBrightness) ----

void renderSolid() {
  uint32_t c = strip.Color(current.r, current.g, current.b);
  for (int i = 0; i < NUM_LEDS; i++) strip.setPixelColor(i, c);
}

void renderBreathe() {
  unsigned long t = millis() - current.startedAt;
  float phase = (t % current.period) / (float)current.period;
  float s = (sin(phase * 2.0 * PI) + 1.0) / 2.0;
  uint32_t c = strip.Color((uint8_t)(current.r * s),
                           (uint8_t)(current.g * s),
                           (uint8_t)(current.b * s));
  for (int i = 0; i < NUM_LEDS; i++) strip.setPixelColor(i, c);
}

void renderBlink() {
  unsigned long t = millis() - current.startedAt;
  uint16_t half = current.period / 2;
  if (half == 0) half = 1;
  bool on = (t / half) % 2 == 0;
  uint32_t c = on ? strip.Color(current.r, current.g, current.b) : 0;
  for (int i = 0; i < NUM_LEDS; i++) strip.setPixelColor(i, c);
}

void renderScanner() {
  unsigned long t = millis() - current.startedAt;
  float phase = (t % current.period) / (float)current.period;
  float pos = (sin(phase * 2.0 * PI) + 1.0) / 2.0 * (NUM_LEDS - 1);
  int center = (int)round(pos);

  for (int i = 0; i < NUM_LEDS; i++) {
    int dist = abs(i - center);
    if (dist == 0) {
      strip.setPixelColor(i, strip.Color(current.r, current.g, current.b));
    } else if (dist == 1) {
      strip.setPixelColor(i, strip.Color(current.r / 4, current.g / 4, current.b / 4));
    } else {
      strip.setPixelColor(i, 0);
    }
  }
}

void renderFill() {
  unsigned long t = millis() - current.startedAt;
  unsigned long phase = t % current.period;
  uint16_t half = current.period / 2;
  if (half == 0) half = 1;
  // Map phase [0, half-1] -> [0, NUM_LEDS] so the progressive fill actually
  // reaches NUM_LEDS before the hold phase. Using `half` as the upper bound
  // never returns NUM_LEDS due to integer division (Arduino map() is
  // integer-only); `half - 1` does.
  int litCount = (phase < half)
      ? (int)map(phase, 0, half - 1, 0, NUM_LEDS)
      : NUM_LEDS;
  uint32_t c = strip.Color(current.r, current.g, current.b);
  for (int i = 0; i < NUM_LEDS; i++) {
    strip.setPixelColor(i, i < litCount ? c : 0);
  }
}

void renderStrobe() {
  unsigned long t = millis() - current.startedAt;
  uint16_t half = current.period / 2;
  if (half == 0) half = 1;
  bool first = (t / half) % 2 == 0;
  uint32_t c = first ? strip.Color(current.r, current.g, current.b)
                     : strip.Color(current.r2, current.g2, current.b2);
  for (int i = 0; i < NUM_LEDS; i++) strip.setPixelColor(i, c);
}

void renderLevel() {
  // ceil(levelPct * NUM_LEDS / 100) via integer math: any pct > 0 lights at
  // least one LED, pct >= 100 lights all. pct=0 lights none.
  int litCount = ((int)current.levelPct * NUM_LEDS + 99) / 100;
  if (litCount > NUM_LEDS) litCount = NUM_LEDS;
  uint32_t c = strip.Color(current.r, current.g, current.b);
  for (int i = 0; i < NUM_LEDS; i++) {
    strip.setPixelColor(i, i < litCount ? c : 0);
  }
}

void renderConverge() {
  unsigned long t = millis() - current.startedAt;
  float phase = (t % current.period) / (float)current.period;
  // Triangle wave 0 -> 1 -> 0 over one period. Fill inward on the rise,
  // retreat outward on the fall (mirrors the fill direction).
  float tri = (phase < 0.5f) ? (phase * 2.0f) : (2.0f - phase * 2.0f);
  // halfLeds is the number of distinct edge-distance bands (NUM_LEDS/2 for
  // even counts, +1 for odd). At tri=1 we light all LEDs.
  int halfLeds = (NUM_LEDS + 1) / 2;
  int litDist = (int)(tri * halfLeds + 0.5f);
  if (litDist > halfLeds) litDist = halfLeds;
  uint32_t c = strip.Color(current.r, current.g, current.b);
  for (int i = 0; i < NUM_LEDS; i++) {
    int dist = min(i, NUM_LEDS - 1 - i);
    strip.setPixelColor(i, dist < litDist ? c : 0);
  }
}

void renderOff() {
  strip.clear();
}

void playBootGreeting() {
  // Wakeup wave: light LEDs left->right (green = "ready"), brief hold, then
  // unfill right->left. Distinct from the continuous state animations so it
  // reads as a one-shot boot greeting rather than a state change.
  const uint32_t color = strip.Color(0, 200, 0);
  const uint16_t stepMs = 50;

  // Greeting uses the full MAX_BRIGHTNESS ceiling; applyAnimation re-scales
  // when the first real command arrives.
  strip.setBrightness(MAX_BRIGHTNESS);

  for (int i = 0; i < NUM_LEDS; i++) {
    strip.setPixelColor(i, color);
    strip.show();
    delay(stepMs);
  }
  delay(150);
  for (int i = NUM_LEDS - 1; i >= 0; i--) {
    strip.setPixelColor(i, 0);
    strip.show();
    delay(stepMs);
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  strip.begin();
  strip.setBrightness(MAX_BRIGHTNESS);  // initial ceiling; each command re-scales via bright_pct
  strip.clear();
  strip.show();
  playBootGreeting();
  current.startedAt = millis();
}

void loop() {
  // Non-blocking line read: drain whatever bytes are available, dispatch on
  // newline. Avoids Serial.readStringUntil's default 1 s timeout blocking
  // the render loop when a partial line is in the buffer.
  static String serialBuf;
  while (Serial.available()) {
    int c = Serial.read();
    if (c == '\n') {
      handleCommand(serialBuf);
      serialBuf = "";
    } else if (c != '\r') {
      // Defensive cap: a misbehaving sender without newlines must not OOM.
      if (serialBuf.length() < 64) serialBuf += (char)c;
    }
  }

  switch (current.anim) {
    case ANIM_SOLID:   renderSolid();   break;
    case ANIM_BREATHE: renderBreathe(); break;
    case ANIM_BLINK:   renderBlink();   break;
    case ANIM_SCANNER: renderScanner(); break;
    case ANIM_FILL:    renderFill();    break;
    case ANIM_STROBE:  renderStrobe();  break;
    case ANIM_LEVEL:   renderLevel();   break;
    case ANIM_CONVERGE:renderConverge();break;
    case ANIM_OFF:     renderOff();     break;
  }

  strip.show();
  delay(16);
}
