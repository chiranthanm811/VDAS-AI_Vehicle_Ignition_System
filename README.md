# VDAS — AI-Based Licence-Aware Vehicle Ignition & Anti-Spoofing System

[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![OpenCV](https://img.shields.io/badge/OpenCV-4.8+-5C3EE8?logo=opencv&logoColor=white)](https://opencv.org/)
[![ESP32](https://img.shields.io/badge/Hardware-ESP32-E7352C?logo=espressif&logoColor=white)](https://www.espressif.com/)
[![Flask](https://img.shields.io/badge/Dashboard-Flask-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An intelligent, multi-layered automotive security system designed to prevent vehicle theft, unauthorized driving, and biometric spoofing. **VDAS (VeriDrive)** connects computer vision, dual-camera stereo liveness estimation, eye-blink state verification, and government RTO licence verification to an **ESP32 microcontroller** controlling physical engine ignition relays.

---

## 🌟 Key Features

1. **5-Stage Security Pipeline**:
   - **Stage 1 (Seat Telemetry)**: Analog Force-Sensing Resistor (FSR) verifies driver physical presence before arming cameras. Instant engine lock when unseated.
   - **Stage 2 (Stereo 3D Depth Anti-Spoofing)**: Uses dual calibrated cameras to measure 3D nose-tip disparity residual. Rejects flat paper photos, tablets, and smartphone screen spoof attempts ($< 3.2\%$).
   - **Stage 3 (RTO Facial Recognition)**: Evaluates 128-dimensional deep face embeddings against government RTO records and cached local profiles.
   - **Stage 4 (Natural Blink Liveness)**: A 4-phase Eye Aspect Ratio (EAR) state machine requiring steady baseline open eyes and a verified natural closure ($\ge 25\%$ dip) before unlocking.
   - **Stage 5 (Hardware Ignition Relay)**: Physical active-LOW relay activation accompanied by status LEDs and audio chimes.

2. **Futuristic OpenCV Vision HUD**:
   - Dynamic Cyberpunk bounding box targeting reticles.
   - Real-time **3D Depth Meter Bar** ($\ge 3.2\%$ Green, $< 3.2\%$ Red).
   - Real-time **EAR Eye Openness Gauge** with live threshold indicator and `[BLINK!]` detection badge.
   - Live FPS counter and standby radar sweep generator.

3. **ESP32 Production Firmware (v2.5)**:
   - Cyberpunk LCD boot progress bar with startup chime.
   - Custom 5x8 LCD characters (open/closed eye, checkmark, cross, lock).
   - GPIO13 Piezo buzzer engine with multi-frequency tone chords.
   - Green LED hardware PWM breathing effect during biometric scan.
   - Automatic scrolling marquee on 16x2 I2C LCD for driver names $> 16$ characters.
   - 3.5s hardware fail-safe watchdog timer.

4. **Real-Time Web Dashboard (`dashboard.py`)**:
   - Live low-latency MJPEG video feed proxy.
   - Web Audio API Cyber Synthesizer (uplifting unlock arpeggios, urgent alarm chirps, lock clicks).
   - One-click Presentation Kiosk Mode (`⛶`).
   - Dynamic glass toast notifications.
   - Owner controls: Instant Remote Lock, Driver Block/Unblock, and Event Audit Log.

---

## 📐 System Architecture

```
                                 ┌─────────────────────────────────┐
                                 │      Dual USB Webcams           │
                                 │  Cam 0: Main (Face & Blink)     │
                                 │  Cam 1: Stereo Depth Disparity  │
                                 └───────────────┬─────────────────┘
                                                 │
                                                 ▼
┌───────────────────────┐            ┌───────────────────────┐            ┌───────────────────────┐
│   FSR Seat Sensor     │ ──GPIO34── │      ESP32 Core       │ ──Serial── │     Laptop Python     │
│   (Driver Presence)   │            │   Firmware v2.5       │  (115200)  │   Vision & Auth Engine│
└───────────────────────┘            └───────────┬───────────┘            └───────────┬───────────┘
                                                 │                                    │
                                      ┌──────────┴──────────┐                         │
                                      ▼                     ▼                         ▼
                                ┌───────────┐         ┌───────────┐         ┌───────────────────┐
                                │Relay/Ign. │         │ 1602 LCD  │         │ Flask Dashboard   │
                                │Buzzer/LEDs│         │Custom Char│         │ http://127.0.0.1  │
                                └───────────┘         └───────────┘         └───────────────────┘
```

---

## 🔌 Hardware Pinout (ESP32)

| Component | ESP32 GPIO | Description / Mode |
|---|---|---|
| **FSR Seat Sensor** | `GPIO34` | Analog In (10k pull-down, threshold > 300) |
| **Relay IN** | `GPIO25` | Digital Out (Active-LOW engine ignition) |
| **Ignition LED** | `GPIO26` | Powered through Relay COM / NO contact |
| **Green Status LED** | `GPIO27` | Hardware PWM (5kHz) Breathing scan / Solid unlock |
| **Red Status LED** | `GPIO14` | Digital Out (Solid on Spoof / Access Denied) |
| **Piezo Buzzer** | `GPIO13` | Digital Out (Ascending chimes & alert alarms) |
| **LCD 1602 SDA** | `GPIO21` | I2C Data (Address `0x27`) |
| **LCD 1602 SCL** | `GPIO22` | I2C Clock |

---

## 📊 Stereo Depth & Blink Math

### 1. Stereo Depth Disparity Residual
Live 3D surface depth is computed by comparing 68 facial landmarks extracted across both cameras:
$$\Delta x = |x_{\text{left}} - x_{\text{right}}|$$

A planar affine model ($A \mathbf{c} \approx \Delta x_{\text{ref}}$) is fitted against the outer facial contour (jawline, eyebrows, lips), and the depth residual on nose tip landmarks is evaluated against face width:
$$\text{Depth Score} = 100 \times \frac{\text{Mean}(\Delta x_{\text{nose}} - A_{\text{nose}}\mathbf{c})}{\text{Face Width}}$$

- **Real Human Faces**: $4.3\% - 14.0\%$ (Average $\sim 6.35\%$)
- **Flat Photos / Phone Screens**: $0.9\% - 1.56\%$
- **Calibrated Decision Threshold**: $\ge 3.2\%$

### 2. Eye Aspect Ratio (EAR) State Machine
$$\text{EAR} = \frac{\|p_2 - p_6\| + \|p_3 - p_5\|}{2 \|p_1 - p_4\|}$$

A 4-phase state machine prevents photo spoofing while capturing natural 80–150 ms human blinks:
1. `WAIT_FOR_OPEN`: Collects $\ge 4$ baseline open-eye frames ($0.21 \le \text{EAR} \le 0.45$).
2. `READY_FOR_BLINK`: Dynamically tracks adaptive baseline $\text{EAR}_{\text{base}}$.
3. `EYE_CLOSED`: Triggers when $\text{EAR} < \text{Threshold}$ ($\sim 76\%$ of baseline).
4. `VERIFIED`: Confirms closure lasts $1 - 6$ frames and returns to baseline openness. Static pictures cannot transition through this loop.

---

## 🚀 Getting Started

### 1. Clone the Repository
```bash
git clone https://github.com/chiranthanm811/VDAS-AI-Vehicle-Ignition-System.git
cd VDAS-AI-Vehicle-Ignition-System
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Flash ESP32 Firmware
1. Open [`ignition_esp32/ignition_esp32.ino`](ignition_esp32/ignition_esp32.ino) in the **Arduino IDE**.
2. Select Board: **ESP32 Dev Module**, Port: `COM4` (or your platform's serial port).
3. Install the **LiquidCrystal_I2C** library if not already present.
4. Click **Upload**.

### 4. Seed the RTO Database
```bash
python rto_setup.py
```

### 5. Launch System
Open two terminals:

**Terminal 1 (Vision & Ignition Engine):**
```bash
python drive_system.py
```

**Terminal 2 (Web Dashboard):**
```bash
python dashboard.py
```
Access the dashboard at `http://127.0.0.1:5000`.

---

## 📁 Repository Structure

```
├── drive_system.py          # Main vision pipeline (stereo depth, blink, face match)
├── dashboard.py             # Flask dashboard, cyberpunk HUD, Web Audio synth
├── core.py                  # Database queries, identity cache, licence validator
├── rto_setup.py             # RTO database enrolment CLI tool
├── depth_test.py            # Stereo camera calibration utility
├── depth_debug.py           # Depth measurement visual debugger
├── find_camera.py           # USB camera index discovery utility
├── rto_database.db          # Simulated official government RTO database
├── vehicle_local.db         # Fast local vehicle cache database
├── requirements.txt         # Python package dependencies
├── .gitignore               # Git ignore rules
└── ignition_esp32/
    └── ignition_esp32.ino   # ESP32 production firmware v2.5
```

---

## 👥 Author

- **Chiranthan M** ([@chiranthanm811](https://github.com/chiranthanm811))
