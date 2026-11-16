/*
  VDAS — Licence-Aware Vehicle Ignition System
  ESP32 Production Firmware v2.5 (Futuristic Audio-Visual Edition)
  ---------------------------------------------------------------
  Hardware Pinout:
    GPIO34 — FSR Seat Sensor (Analog, 10k pull-down to GND)
    GPIO25 — Relay IN (Active-LOW, series safety resistor)
    GPIO26 — Ignition LED (Powered through relay COM/NO contact)
    GPIO27 — Green LED (PWM Pulse during scan, Solid on Unlock)
    GPIO14 — Red LED (Solid on Denied/Spoof)
    GPIO13 — Piezo Buzzer (Chimes and Security Alarms)
    GPIO21 — LCD SDA (I2C)
    GPIO22 — LCD SCL (I2C)

  Serial Protocol (115200 baud):
    Inbound commands from laptop:
      U[:Name]    → Unlocked (Ignition Relay ON, Green LED Solid, Welcome Chime)
      L           → Locked (Relay OFF, All LEDs OFF, Standby Screen)
      W[:Prompt]  → Waiting/Scanning (Relay OFF, Green LED Breathes, Animated Blink Eye)
      D[:Reason]  → Denied (Relay OFF, Red LED ON, Alarm Tone, Displays Error)

    Outbound telemetry to laptop (every 200 ms):
      SEAT:1      → Seat occupied
      SEAT:0      → Seat vacant
*/

#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// ── Pin Configuration ───────────────────────────────────────────
#define PIN_SEAT        34
#define PIN_RELAY       25    // Active-LOW relay
#define PIN_IGN_LED     26    // Ignition indicator
#define PIN_GREEN       27    // Status Green
#define PIN_RED         14    // Status Red
#define PIN_BUZZER      13    // Audio Buzzer

#define SEAT_THRESHOLD  300   // FSR analog threshold for seated detection
#define PWM_CHANNEL     0
#define PWM_FREQ        5000
#define PWM_RES         8     // 8-bit resolution (0-255)

// ── LCD Setup (0x27 or 0x3F) ────────────────────────────────────
LiquidCrystal_I2C lcd(0x27, 16, 2);

// ── Custom 5x8 LCD Characters ───────────────────────────────────
byte charEyeOpen[8] = {
  B00000,
  B01110,
  B10101,
  B10101,
  B10101,
  B01110,
  B00000,
  B00000
};

byte charEyeClosed[8] = {
  B00000,
  B00000,
  B00000,
  B11111,
  B00000,
  B00000,
  B00000,
  B00000
};

byte charCheck[8] = {
  B00000,
  B00001,
  B00011,
  B10110,
  B11100,
  B01000,
  B00000,
  B00000
};

byte charCross[8] = {
  B00000,
  B10001,
  B01010,
  B00100,
  B01010,
  B10001,
  B00000,
  B00000
};

byte charLock[8] = {
  B01110,
  B10001,
  B10001,
  B11111,
  B11011,
  B11011,
  B11111,
  B00000
};

// ── System State ────────────────────────────────────────────────
char  currentMode     = 'L';
String detailText     = "";
unsigned long lastCmd = 0;
unsigned long lastSeat = 0;
unsigned long lastAnim = 0;
unsigned long scrollTimer = 0;
int   scrollIndex     = 0;
bool  eyeState        = false;
float breathePhase    = 0.0;

// ── Hardware Helpers ────────────────────────────────────────────
void relayOn() {
  digitalWrite(PIN_RELAY, LOW);
  digitalWrite(PIN_IGN_LED, HIGH);
}

void relayOff() {
  digitalWrite(PIN_RELAY, HIGH);
  digitalWrite(PIN_IGN_LED, LOW);
}

void allOff() {
  relayOff();
  ledcWrite(PWM_CHANNEL, 0);
  digitalWrite(PIN_GREEN, LOW);
  digitalWrite(PIN_RED, LOW);
  digitalWrite(PIN_BUZZER, LOW);
}

// ── Audio Engine ────────────────────────────────────────────────
void chimeSuccess() {
  // Uplifting 3-tone ascending chime
  digitalWrite(PIN_BUZZER, HIGH); delay(45);
  digitalWrite(PIN_BUZZER, LOW);  delay(35);
  digitalWrite(PIN_BUZZER, HIGH); delay(55);
  digitalWrite(PIN_BUZZER, LOW);  delay(35);
  digitalWrite(PIN_BUZZER, HIGH); delay(110);
  digitalWrite(PIN_BUZZER, LOW);
}

void alarmDenied() {
  // Urgent 3-pulse warning buzz
  for (int i = 0; i < 3; i++) {
    digitalWrite(PIN_BUZZER, HIGH); delay(90);
    digitalWrite(PIN_BUZZER, LOW);  delay(60);
  }
}

void chirpLock() {
  digitalWrite(PIN_BUZZER, HIGH); delay(30);
  digitalWrite(PIN_BUZZER, LOW);
}

// ── LCD Display Helpers ─────────────────────────────────────────
void lcdPrintCenter(uint8_t row, String txt) {
  int pad = max(0, (16 - (int)txt.length()) / 2);
  lcd.setCursor(0, row);
  for (int i = 0; i < pad; i++) lcd.print(" ");
  lcd.print(txt.substring(0, 16 - pad));
  for (int i = pad + txt.length(); i < 16; i++) lcd.print(" ");
}

void renderBootAnimation() {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("VDAS NEURAL CORE");
  lcd.setCursor(0, 1);
  lcd.print("[              ]");
  
  for (int i = 1; i <= 14; i++) {
    lcd.setCursor(i, 1);
    lcd.write(byte(255)); // Solid 5x8 block
    delay(40);
  }
  delay(150);
  chimeSuccess();
}

// ── Setup ───────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  pinMode(PIN_RELAY,   OUTPUT);
  pinMode(PIN_IGN_LED, OUTPUT);
  pinMode(PIN_RED,     OUTPUT);
  pinMode(PIN_BUZZER,  OUTPUT);

  // Setup Green LED PWM for smooth breathing effect
  ledcSetup(PWM_CHANNEL, PWM_FREQ, PWM_RES);
  ledcAttachPin(PIN_GREEN, PWM_CHANNEL);

  allOff();

  Wire.begin(21, 22);
  lcd.init();
  lcd.backlight();

  // Register custom 5x8 graphics in LCD CGRAM
  lcd.createChar(0, charEyeOpen);
  lcd.createChar(1, charEyeClosed);
  lcd.createChar(2, charCheck);
  lcd.createChar(3, charCross);
  lcd.createChar(4, charLock);

  renderBootAnimation();
  applyMode('L', "");

  lastCmd = millis();
  Serial.println("ESP32 ready");
}

// ── Main Loop ───────────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  // ── 1. Read Serial Stream from Python ─────────────────────────
  while (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      char cmd = line.charAt(0);
      String extra = "";
      int colonIdx = line.indexOf(':');
      if (colonIdx > 0 && colonIdx < line.length() - 1) {
        extra = line.substring(colonIdx + 1);
        extra.trim();
      }

      if (cmd == 'U' || cmd == 'L' || cmd == 'W' || cmd == 'D') {
        if (cmd != currentMode || (extra.length() > 0 && extra != detailText)) {
          currentMode = cmd;
          detailText  = extra;
          applyMode(cmd, extra);
        }
        lastCmd = now;
      }
    }
  }

  // ── 2. Hardware Watchdog (Lock after 3.5s silent timeout) ─────
  if (now - lastCmd > 3500 && currentMode != 'L') {
    currentMode = 'L';
    detailText  = "";
    applyMode('L', "");
  }

  // ── 3. Real-Time Animations per Mode ──────────────────────────
  if (currentMode == 'W') {
    // A) Smooth Green LED Breathing Pulse
    breathePhase += 0.08;
    int brightness = (int)(128.0 + 120.0 * sin(breathePhase));
    ledcWrite(PWM_CHANNEL, brightness);

    // B) Animated LCD Eye Blinking icon every 700 ms
    if (now - lastAnim > 700) {
      lastAnim = now;
      eyeState = !eyeState;
      lcd.setCursor(14, 1);
      lcd.write(eyeState ? byte(0) : byte(1)); // Alternate eye open / closed
    }
  }

  // ── 4. Scrolling Name Marquee on Line 2 (for Long Names) ───────
  if (currentMode == 'U' && detailText.length() > 16) {
    if (now - scrollTimer > 320) {
      scrollTimer = now;
      String displayStr = detailText + "   " + detailText;
      lcd.setCursor(0, 1);
      lcd.print(displayStr.substring(scrollIndex, scrollIndex + 16));
      scrollIndex++;
      if (scrollIndex >= detailText.length() + 3) {
        scrollIndex = 0;
      }
    }
  }

  // ── 5. Transmit FSR Seat Telemetry every 200 ms ───────────────
  if (now - lastSeat > 200) {
    lastSeat = now;
    int val  = analogRead(PIN_SEAT);
    Serial.print("SEAT:");
    Serial.println(val > SEAT_THRESHOLD ? "1" : "0");
  }
}

// ── Apply Security Mode Transition ──────────────────────────────
void applyMode(char m, String extra) {
  allOff();
  scrollIndex = 0;

  switch (m) {

    case 'U': { // ── ACCESS GRANTED / IGNITION ACTIVE ─────────────
      relayOn();
      ledcWrite(PWM_CHANNEL, 255); // Solid 100% Green
      chimeSuccess();

      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.write(byte(2)); // Checkmark
      lcd.print(" ACCESS GRANTED");

      lcd.setCursor(0, 1);
      if (extra.length() > 0) {
        if (extra.length() <= 16) {
          lcdPrintCenter(1, extra);
        } else {
          lcd.print(extra.substring(0, 16));
        }
      } else {
        lcdPrintCenter(1, "Ignition Active");
      }
      break;
    }

    case 'L': { // ── SYSTEM LOCKED / STANDBY ───────────────────────
      relayOff();
      chirpLock();

      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.write(byte(4)); // Lock symbol
      lcd.print(" SYSTEM LOCKED ");
      lcdPrintCenter(1, "Standby // Vacant");
      break;
    }

    case 'W': { // ── VERIFYING / BLINK CHALLENGE ───────────────────
      relayOff();
      eyeState = true;

      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("BIOMETRIC SCAN");

      lcd.setCursor(0, 1);
      if (extra.length() > 0) {
        lcd.print(extra.substring(0, 13));
      } else {
        lcd.print("Please Blink ");
      }
      lcd.setCursor(14, 1);
      lcd.write(byte(0)); // Eye Open icon
      break;
    }

    case 'D': { // ── ACCESS DENIED / SPOOF BLOCKED ─────────────────
      relayOff();
      digitalWrite(PIN_RED, HIGH);
      alarmDenied();

      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.write(byte(3)); // Cross symbol
      lcd.print(" ACCESS DENIED ");

      lcd.setCursor(0, 1);
      if (extra.length() > 0) {
        lcdPrintCenter(1, extra);
      } else {
        lcdPrintCenter(1, "Photo Spoof / DL");
      }
      break;
    }
  }
}
