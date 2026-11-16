# How does the AI-based license-aware vehicle ignition system work? {#vdas-explainer}

The system protects vehicle ignition through a five-stage biometric and sensory verification pipeline. The engine relay remains locked until hardware presence, stereo 3D liveness, blink dynamics, and license validity succeed.

- Seat presence sensing ensures an occupant is physically seated before computer vision pipelines activate.
- Stereo depth disparity and a four-state blink detector reject 2D photos, digital screens, and static masks.
- Real-time facial recognition matches the driver against cached credentials and the official RTO database.
- An ESP32 microcontroller enforces fail-safe relay actuation and reports serial telemetry to the web dashboard.

@answer scope="VDAS v2.5 · Dual Webcam · ESP32 · RTO Authority"
@meta locale=en
@source core title="VDAS Vision Core (core.py)" url=https://github.com/chiranthanm811/VDAS-AI_Vehicle_Ignition_System/blob/main/core.py
@source drive title="Drive Execution System (drive_system.py)" url=https://github.com/chiranthanm811/VDAS-AI_Vehicle_Ignition_System/blob/main/drive_system.py
@source dashboard title="Telemetry Dashboard (dashboard.py)" url=https://github.com/chiranthanm811/VDAS-AI_Vehicle_Ignition_System/blob/main/dashboard.py
@source firmware title="ESP32 Firmware (ignition_esp32.ino)" url=https://github.com/chiranthanm811/VDAS-AI_Vehicle_Ignition_System/blob/main/ignition_esp32.ino

## What sequence authorizes the vehicle ignition? {#auth-sequence-panel}

@sequence id=auth-seq title="VDAS five-stage ignition handshake"
| actor driver | Driver | external |
| actor esp32 | ESP32 Controller | backend |
| actor cams | Stereo Cameras | frontend |
| actor laptop | Vision Engine | backend |
| actor relay | Ignition Relay | security |
| driver | esp32 | Sits in driver seat (FSR) |
| esp32 | laptop | SEAT:1 serial event |
| driver | cams | Presents face to dual cameras |
| cams | laptop | Left and right video frames |
| laptop | esp32 | CMD:UNLOCK authenticated | emphasis |
| esp32 | relay | GPIO25 LOW activates relay | security |
| esp32 | driver | LCD confirms access granted | return |

@steps id=auth-steps
| step1 | Seat Presence | FSR sensor on GPIO34 detects driver weight. |
| step2 | Stereo Disparity | Dual cameras measure nose-tip disparity above 3.2 percent. |
| step3 | Blink Dynamics | Four-state machine verifies eye blinks via EAR. |
| step4 | Identity Match | Face recognition matches local cache and RTO database. |
| step5 | Relay Actuation | ESP32 energizes relay on GPIO25 and confirms unlock. |

## How does stereo depth thwart spoof attacks? {#spoof-mitigation}

| Presentation Medium | Disparity Delta | Geometric Curvature | Eye Blink Pattern | Decision |
| --- | --- | --- | --- | --- |
| Real 3D Human Face | `≥ 3.2%` {good:Stereo Depth} | Natural 3D Contours | Dynamic EAR Cycle {good:Natural} | AUTHORIZED {good:Pass} |
| High-Res Printed Photo | `< 1.0%` {bad:Flat Plane} | Zero Depth Delta | Zero Blink Transitions {bad:Static} | REJECTED {bad:Spoof} |
| Smartphone Screen | `< 0.8%` {bad:Flat Plane} | Planar Screen Reflection | Static or Looped Video {bad:Anomalous} | REJECTED {bad:Spoof} |
| Mannequin or Rigid Mask | `≥ 3.2%` {caution:3D Depth} | Rigid Convex Contour | Zero EAR Variations {bad:Zero Blink} | REJECTED {bad:Spoof} |

## What are the state machine and hardware parameters? {#system-specs}

@facts
| FSR Presence Threshold | `ADC > 1000` on ESP32 GPIO34 |
| Stereo Disparity Threshold | `≥ 3.2%` relative baseline difference |
| Eye Aspect Ratio (EAR) Threshold | Closed below 0.21, Open above 0.28 |
| Blink Confirmation | 4 states (OPEN, CLOSING, CLOSED, OPENING) |
| Relay Logic | GPIO25 active LOW with flyback diode protection |
| Fail-Safe Timeout | Auto-locks ignition if driver vacates seat for 3s |
| Telemetry Protocol | 115200 baud USB UART with JSON telemetry |
| Dashboard Refresh Rate | Real-time SSE and WebSockets on port 5000 |

## How are implemented features distinguished from roadmap extensions? {#feature-audit}

- [x] Dual 720p/1080p stereo camera acquisition pipeline
- [x] MediaPipe 468-point facial landmark mesh tracking
- [x] Geometric 3D nose-tip disparity anti-spoofing filter
- [x] Four-state EAR temporal blink liveness detector
- [x] RTO license validation and local SQLite credential cache
- [x] ESP32 dual-core firmware with I2C LCD and buzzer feedback
- [x] Real-time Flask dashboard with MJPEG HUD and synth alerts
- [ ] MQ-3 analog alcohol vapor interlock (Roadmap)
- [ ] Continuous PERCLOS drowsiness and distraction monitoring (Roadmap)
- [ ] Quectel GPS tracking with cellular SOS cloud upload (Roadmap)
