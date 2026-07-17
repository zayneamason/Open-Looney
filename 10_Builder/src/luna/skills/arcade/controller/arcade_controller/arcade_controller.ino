/*
 * Luna Arcade Cabinet — Elegoo Mega 2560 R3
 * Configurable controller with ISC15AMP4 LCD button support.
 *
 * PROTOCOL (Mega → PC):
 *   BTN:<id>:1        button pressed
 *   BTN:<id>:0        button released
 *   READY              startup complete
 *
 * PROTOCOL (PC → Mega):
 *   LCD:text:<line1>|<line2>    show text on LCD button
 *   LCD:clear                   clear LCD
 *   LCD:invert:<0|1>            invert display
 *   LCD:icon:<name>             show a built-in icon
 *   PROFILE:<name>              switch button profile (echoed back)
 */

#include <SPI.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ── LCD Button (ISC15AMP4 via SPI) ──────────────────────────
#define LCD_WIDTH   128
#define LCD_HEIGHT   64
#define LCD_DC       49
#define LCD_RST      48
#define LCD_CS       53   // SPI SS

Adafruit_SSD1306 lcd(LCD_WIDTH, LCD_HEIGHT, &SPI, LCD_DC, LCD_RST, LCD_CS);

// ── Button Definitions ──────────────────────────────────────
// Each button: pin, id string, last state, last change time
struct Button {
  uint8_t pin;
  const char* id;
  bool pressed;
  unsigned long lastChange;
};

// Regular arcade buttons — add/remove as needed
// Wire each: one leg to pin, other leg to GND
Button buttons[] = {
  { 2,  "fire",   false, 0},
  { 3,  "left",   false, 0},
  { 4,  "right",  false, 0},
  { 5,  "up",     false, 0},
  { 6,  "down",   false, 0},
  // ISC15AMP4 — uncomment when soldered:
  // { 7,  "sw1",    false, 0},
  // { 8,  "sw2",    false, 0},
};

const int NUM_BUTTONS = sizeof(buttons) / sizeof(buttons[0]);
const unsigned long DEBOUNCE_MS = 15;

// ── Serial command buffer ───────────────────────────────────
char cmdBuf[128];
int cmdLen = 0;

// ── Setup ───────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  // Init all button pins with internal pullup
  for (int i = 0; i < NUM_BUTTONS; i++) {
    pinMode(buttons[i].pin, INPUT_PULLUP);
  }

  // Init LCD
  if (lcd.begin(SSD1306_SWITCHCAPVCC)) {
    lcd.clearDisplay();
    lcd.setTextSize(2);
    lcd.setTextColor(SSD1306_WHITE);
    lcd.setCursor(20, 8);
    lcd.println("LUNA");
    lcd.setTextSize(1);
    lcd.setCursor(20, 32);
    lcd.println("ARCADE READY");
    lcd.display();
  } else {
    // LCD not found — that's OK, buttons still work
    Serial.println("WARN:LCD_NOT_FOUND");
  }

  pinMode(LED_BUILTIN, OUTPUT);
  delay(200);
  Serial.println("READY");
}

// ── Main loop ───────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  // Read buttons
  for (int i = 0; i < NUM_BUTTONS; i++) {
    bool cur = (digitalRead(buttons[i].pin) == LOW);
    if (cur != buttons[i].pressed && (now - buttons[i].lastChange) > DEBOUNCE_MS) {
      buttons[i].pressed = cur;
      buttons[i].lastChange = now;
      Serial.print("BTN:");
      Serial.print(buttons[i].id);
      Serial.print(":");
      Serial.println(cur ? "1" : "0");
      digitalWrite(LED_BUILTIN, cur ? HIGH : LOW);
    }
  }

  // Read serial commands from PC
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdLen > 0) {
        cmdBuf[cmdLen] = '\0';
        handleCommand(cmdBuf);
        cmdLen = 0;
      }
    } else if (cmdLen < (int)sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    }
  }
}

// ── Command handler ─────────────────────────────────────────
void handleCommand(const char* cmd) {
  if (strncmp(cmd, "LCD:text:", 9) == 0) {
    lcdShowText(cmd + 9);
  }
  else if (strcmp(cmd, "LCD:clear") == 0) {
    lcd.clearDisplay();
    lcd.display();
  }
  else if (strncmp(cmd, "LCD:invert:", 11) == 0) {
    lcd.invertDisplay(cmd[11] == '1');
  }
  else if (strncmp(cmd, "LCD:icon:", 9) == 0) {
    lcdShowIcon(cmd + 9);
  }
  else if (strncmp(cmd, "PROFILE:", 8) == 0) {
    // Echo back so bridge confirms switch
    Serial.print("PROFILE:");
    Serial.println(cmd + 8);
  }
}

// ── LCD: show text (line1|line2) ────────────────────────────
void lcdShowText(const char* text) {
  lcd.clearDisplay();
  lcd.setTextColor(SSD1306_WHITE);

  // Split on '|'
  char buf[64];
  strncpy(buf, text, sizeof(buf) - 1);
  buf[sizeof(buf) - 1] = '\0';

  char* line1 = buf;
  char* line2 = NULL;
  char* sep = strchr(buf, '|');
  if (sep) {
    *sep = '\0';
    line2 = sep + 1;
  }

  // Line 1 — large
  lcd.setTextSize(2);
  lcd.setCursor(0, line2 ? 4 : 20);
  lcd.println(line1);

  // Line 2 — small
  if (line2) {
    lcd.setTextSize(1);
    lcd.setCursor(0, 40);
    lcd.println(line2);
  }

  lcd.display();
}

// ── LCD: built-in icons ─────────────────────────────────────
void lcdShowIcon(const char* name) {
  lcd.clearDisplay();
  lcd.setTextColor(SSD1306_WHITE);

  if (strcmp(name, "fire") == 0) {
    // Simple fire triangle
    lcd.fillTriangle(64, 8, 40, 56, 88, 56, SSD1306_WHITE);
    lcd.setTextSize(1);
    lcd.setCursor(44, 56);
    lcd.print("FIRE");
  }
  else if (strcmp(name, "play") == 0) {
    // Play triangle
    lcd.fillTriangle(40, 12, 40, 52, 88, 32, SSD1306_WHITE);
  }
  else if (strcmp(name, "pause") == 0) {
    lcd.fillRect(36, 12, 16, 40, SSD1306_WHITE);
    lcd.fillRect(76, 12, 16, 40, SSD1306_WHITE);
  }
  else if (strcmp(name, "luna") == 0) {
    // Luna crescent
    lcd.fillCircle(64, 32, 24, SSD1306_WHITE);
    lcd.fillCircle(76, 28, 20, SSD1306_BLACK);
    lcd.setTextSize(1);
    lcd.setCursor(44, 56);
    lcd.print("LUNA");
  }
  else if (strcmp(name, "menu") == 0) {
    lcd.fillRect(24, 16, 80, 4, SSD1306_WHITE);
    lcd.fillRect(24, 30, 80, 4, SSD1306_WHITE);
    lcd.fillRect(24, 44, 80, 4, SSD1306_WHITE);
  }
  else {
    lcd.setTextSize(2);
    lcd.setCursor(0, 20);
    lcd.print(name);
  }

  lcd.display();
}
