/*
 * Claude Code Status Indicator - Wemos D1 Mini (ESP8266) + WS2812B
 * ---------------------------------------------------------------------------
 * Receives single-line commands over USB-serial from the Mac and displays the
 * color/animation matching that state on the LED strip.
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
 * keeps the peak current well within USB 2.0 limits. Do NOT raise this limit
 * without an external power supply -- it is what guarantees single-cable /
 * no-external-power operation.
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

#define DATA_PIN       2      // D4 = GPIO2 (Wemos D1 Mini silkscreen "D4")
// Note: D4 is a boot-strapping pin + onboard blue LED. After boot it can be
// used as WS2812B data. We keep it here because the cable is already soldered
// to this pin; for a fresh build prefer D2 (GPIO4).
#define NUM_LEDS       8      // WS2812B strip
#define MAX_BRIGHTNESS 32     // 0-255, USB-safe upper bound (see POWER NOTE above)
#define SERIAL_BAUD    115200

Adafruit_NeoPixel strip(NUM_LEDS, DATA_PIN, NEO_GRB + NEO_KHZ800);

// ---- State definitions ----
enum ClaudeState {
  STATE_IDLE,
  STATE_THINKING,
  STATE_TOOL_RUNNING,
  STATE_WAITING_INPUT,
  STATE_SUCCESS,
  STATE_ERROR,
  STATE_OFF
};

ClaudeState currentState = STATE_OFF;  // start dark on boot, state changes once a command arrives
unsigned long stateChangedAt = 0;

// ---- Helper: 8-bit sine-based "breathe" effect (replaces FastLED's beatsin8) ----
uint8_t breathe(uint16_t periodMs, uint8_t minVal, uint8_t maxVal) {
  float phase = (millis() % periodMs) / (float)periodMs;       // 0.0 - 1.0
  float s = (sin(phase * 2.0 * PI) + 1.0) / 2.0;                // 0.0 - 1.0
  return minVal + (uint8_t)(s * (maxVal - minVal));
}

void setState(ClaudeState s) {
  currentState = s;
  stateChangedAt = millis();
}

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toLowerCase();

  if (cmd == "idle")           setState(STATE_IDLE);
  else if (cmd == "thinking")  setState(STATE_THINKING);
  else if (cmd == "tool")      setState(STATE_TOOL_RUNNING);
  else if (cmd == "waiting")   setState(STATE_WAITING_INPUT);
  else if (cmd == "success")   setState(STATE_SUCCESS);
  else if (cmd == "error")     setState(STATE_ERROR);
  else if (cmd == "off")       setState(STATE_OFF);
  // Unknown commands are silently ignored
}

void renderIdle() {
  // Slow blue breathe. The 40-220 range produces a clearly visible pulse
  // at MAX_BRIGHTNESS=32.
  uint8_t b = breathe(3500, 40, 220);
  uint32_t c = strip.Color(0, b / 5, b); // R=0, a little green, blue dominant
  for (int i = 0; i < NUM_LEDS; i++) strip.setPixelColor(i, c);
}

void renderThinking() {
  // A purple "scanner" dot sweeps back and forth
  float phase = (millis() % 1600) / 1600.0;
  float pos = (sin(phase * 2.0 * PI) + 1.0) / 2.0 * (NUM_LEDS - 1);
  int center = (int)round(pos);

  for (int i = 0; i < NUM_LEDS; i++) {
    int dist = abs(i - center);
    if (dist == 0) strip.setPixelColor(i, strip.Color(90, 0, 170));
    else if (dist == 1) strip.setPixelColor(i, strip.Color(35, 0, 70));
    else strip.setPixelColor(i, 0);
  }
}

void renderToolRunning() {
  uint8_t b = breathe(900, 30, 255); // fast yellow/orange pulse
  uint32_t c = strip.Color(b, b / 2, 0);
  for (int i = 0; i < NUM_LEDS; i++) strip.setPixelColor(i, c);
}

void renderWaitingInput() {
  uint8_t b = breathe(2500, 20, 200); // slow white pulse
  uint32_t c = strip.Color(b, b, b);
  for (int i = 0; i < NUM_LEDS; i++) strip.setPixelColor(i, c);
}

void renderSuccess() {
  unsigned long elapsed = millis() - stateChangedAt;
  unsigned long phase = elapsed % 1600;
  int litCount = (phase < 800) ? map(phase, 0, 800, 0, NUM_LEDS) : NUM_LEDS;
  for (int i = 0; i < NUM_LEDS; i++) {
    strip.setPixelColor(i, i < litCount ? strip.Color(0, 220, 0) : 0);
  }
}

void renderError() {
  bool on = ((millis() - stateChangedAt) / 150) % 2 == 0;
  uint32_t c = on ? strip.Color(180, 0, 0) : 0;
  for (int i = 0; i < NUM_LEDS; i++) strip.setPixelColor(i, c);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  strip.begin();
  strip.setBrightness(MAX_BRIGHTNESS);
  strip.clear();
  strip.show();
  stateChangedAt = millis();
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    handleCommand(cmd);
  }

  switch (currentState) {
    case STATE_IDLE:          renderIdle(); break;
    case STATE_THINKING:      renderThinking(); break;
    case STATE_TOOL_RUNNING:  renderToolRunning(); break;
    case STATE_WAITING_INPUT: renderWaitingInput(); break;
    case STATE_SUCCESS:       renderSuccess(); break;
    case STATE_ERROR:         renderError(); break;
    case STATE_OFF:           strip.clear(); break;
  }

  strip.show();
  delay(16);
}
