"""
Licence-Aware Ignition System — drive_system.py

Flow:
  1. Seat sensor (ESP32 GPIO34) detects driver sitting.
  2. Camera turns ON. Depth check (2 webcams) confirms real face.
  3. Face matched against RTO database → licence fetched, cached locally.
     Second visit: fetched from local cache instantly.
  4. Blink liveness check.
  5. Licence rules checked (valid, not expired, correct class, not blocked).
  6. Relay ON → engine starts. Drive session logged.
"""

import sys
from pathlib import Path

# Ensure local module directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import face_recognition
import numpy as np
import time
import serial
from collections import deque
import pyttsx3
import threading
import queue
import http.server
import core

# ─────────────────────── SETTINGS ───────────────────────────────
DEPTH_THRESHOLD  = 3.2      # calibrated stereo threshold (min real ~4.3%, max photo ~1.6%)
CAM_MAIN         = 0        # external USB webcam 1 (face match + blink)
CAM_SECOND       = 1        # external USB webcam 2 (stereo depth)
SERIAL_PORT      = "COM4"
SCALE            = 0.35
IDENTITY_EVERY   = 3.0      # seconds between face re-checks
LIVE_SECONDS     = 6        # blink stays valid for this long
DEPTH_SAMPLES    = 8        # rolling average window for depth score
DEPTH_WARMUP     = 10       # number of frames to collect before depth check activates
STREAM_PORT      = 5001     # HTTP MJPEG live feed port for dashboard

# ─────────────────────── VOICE ANNOUNCEMENTS (pyttsx3) ──────────
_speech_queue = queue.Queue()
_speech_lock = threading.Lock()
_last_spoken = {"text": "", "time": 0.0}

def _speech_worker():
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 160)
        engine.setProperty("volume", 1.0)
        voices = engine.getProperty("voices")
        for v in voices:
            if "zira" in v.name.lower() or "hazel" in v.name.lower():
                engine.setProperty("voice", v.id)
                break
    except Exception as e:
        print("[VOICE] Engine init error:", e)
        return

    while True:
        text = _speech_queue.get()
        if text is None:
            break
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print("[VOICE] Speech error:", e)
        _speech_queue.task_done()

threading.Thread(target=_speech_worker, daemon=True).start()

def speak(text, min_interval=5.0):
    """Enqueue spoken announcements with smart debouncing."""
    now = time.time()
    with _speech_lock:
        if text == _last_spoken["text"] and (now - _last_spoken["time"]) < min_interval:
            return
        _last_spoken["text"] = text
        _last_spoken["time"] = now
    _speech_queue.put(text)

# ─────────────────────── MJPEG LIVE STREAM SERVER ──────────────
_stream_frame_jpeg = None
_stream_frame_lock = threading.Lock()

class MJPEGStreamHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/video_feed':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            while True:
                with _stream_frame_lock:
                    data = _stream_frame_jpeg
                if data:
                    try:
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + data + b'\r\n')
                    except (BrokenPipeError, ConnectionResetError):
                        break
                time.sleep(0.04)   # ~25 FPS
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress terminal log spam

def start_stream_server(port=STREAM_PORT):
    try:
        server = http.server.ThreadingHTTPServer(('127.0.0.1', port), MJPEGStreamHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"Live MJPEG video stream server active on http://127.0.0.1:{port}/video_feed")
        return server
    except Exception as e:
        print(f"[STREAM] Warning: Could not bind stream server to {port}: {e}")
        return None

# ─────────────────────── FUTURISTIC HUD OVERLAY ──────────────────
_hud_scan_phase = 0.0
_hud_fps        = 0.0
_fps_time       = time.time()
_fps_count      = 0

def draw_center(img, text, y, font, scale, color, thick=1):
    (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
    x = max(0, (img.shape[1] - tw) // 2)
    cv2.putText(img, text, (x, y), font, scale, color, thick, cv2.LINE_AA)

def make_standby_frame():
    standby = np.zeros((480, 640, 3), dtype=np.uint8)
    for x in range(0, 640, 40):
        cv2.line(standby, (x, 0), (x, 480), (22, 28, 48), 1)
    for y in range(0, 480, 40):
        cv2.line(standby, (0, y), (640, y), (22, 28, 48), 1)

    # Glowing Cyan corners [ ] in OpenCV BGR: (255, 215, 0)
    cyan = (255, 215, 0)
    c_len = 32
    # Top-Left
    cv2.line(standby, (20, 20), (20 + c_len, 20), cyan, 2)
    cv2.line(standby, (20, 20), (20, 20 + c_len), cyan, 2)
    # Top-Right
    cv2.line(standby, (620, 20), (620 - c_len, 20), cyan, 2)
    cv2.line(standby, (620, 20), (620 - c_len, 20), cyan, 2)
    cv2.line(standby, (620, 20), (620, 20 + c_len), cyan, 2)
    # Bottom-Left
    cv2.line(standby, (20, 460), (20 + c_len, 460), cyan, 2)
    cv2.line(standby, (20, 460), (20, 460 - c_len), cyan, 2)
    # Bottom-Right
    cv2.line(standby, (620, 460), (620 - c_len, 460), cyan, 2)
    cv2.line(standby, (620, 460), (620, 460 - c_len), cyan, 2)

    # Center radar circle sweep
    t_sec = time.time()
    radar_angle = (t_sec * 1.8) % (2 * np.pi)
    center_pt = (320, 240)
    cv2.circle(standby, center_pt, 70, (30, 40, 70), 1)
    cv2.circle(standby, center_pt, 120, (25, 35, 60), 1)
    rx = int(center_pt[0] + 120 * np.cos(radar_angle))
    ry = int(center_pt[1] + 120 * np.sin(radar_angle))
    cv2.line(standby, center_pt, (rx, ry), (100, 160, 220), 1, cv2.LINE_AA)

    t_str = time.strftime("%H:%M:%S")
    draw_center(standby, "VDAS NEURAL VISION CORE v2.5", 195, cv2.FONT_HERSHEY_SIMPLEX, 0.72, cyan, 2)
    draw_center(standby, "SYSTEM STANDBY // SEAT VACANT", 240, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 200, 220), 1)
    draw_center(standby, f"CAMERAS ARMED - WAITING FOR DRIVER [{t_str}]", 285, cv2.FONT_HERSHEY_SIMPLEX, 0.44, (120, 140, 180), 1)
    return standby

def draw_futuristic_hud(frame, box, driver, status, stage, color, depth_val, depth_ok, blinks, ear_val, ear_thresh, mode):
    global _hud_scan_phase, _hud_fps, _fps_time, _fps_count
    _hud_scan_phase += 0.16
    _fps_count += 1
    t_now = time.time()
    if t_now - _fps_time >= 0.5:
        _hud_fps = _fps_count / (t_now - _fps_time)
        _fps_count = 0
        _fps_time = t_now

    h, w = frame.shape[:2]

    # Translucent status banners top & bottom
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 36), (10, 15, 30), -1)
    cv2.rectangle(overlay, (0, h - 45), (w, h), (10, 15, 30), -1)
    cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)

    ign_text = "IGNITION: ON" if mode == "U" else "IGNITION: LOCKED"
    ign_col = (0, 255, 0) if mode == "U" else (0, 0, 255) if mode == "D" else (255, 220, 0)

    fps_str = f"FPS:{_hud_fps:.1f}" if _hud_fps > 0 else "FPS:--"
    cv2.putText(frame, f"VDAS CORE v2.5 | {fps_str}", (14, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (255, 220, 0), 2)
    dv_str = f"DEPTH:{depth_val:.1f}%" if depth_val is not None else "DEPTH:--"
    ear_str = f"EAR:{ear_val:.2f}" if ear_val > 0 else "EAR:--"
    cv2.putText(frame, f"STAGE:{stage} | {dv_str} | BLINKS:{blinks} | {ear_str} | {ign_text}", (185, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, ign_col, 1)

    cv2.putText(frame, f">> {status.upper()}", (14, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 2)
    cv2.putText(frame, "Q = QUIT", (w - 85, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (180, 180, 180), 1)

    if box is not None:
        t, r, b, l = box
        bw = r - l
        bh = b - t
        corner_len = max(15, int(min(bw, bh) * 0.22))

        # Cyber corner brackets
        cv2.line(frame, (l, t), (l + corner_len, t), color, 3)
        cv2.line(frame, (l, t), (l, t + corner_len), color, 3)
        cv2.line(frame, (r, t), (r - corner_len, t), color, 3)
        cv2.line(frame, (r, t), (r, t + corner_len), color, 3)
        cv2.line(frame, (l, b), (l + corner_len, b), color, 3)
        cv2.line(frame, (l, b), (l, b - corner_len), color, 3)
        cv2.line(frame, (r, b), (r - corner_len, b), color, 3)
        cv2.line(frame, (r, b), (r, b - corner_len), color, 3)

        # Center crosshair
        cx, cy = (l + r) // 2, (t + b) // 2
        cv2.line(frame, (cx - 8, cy), (cx + 8, cy), color, 1)
        cv2.line(frame, (cx, cy - 8), (cx, cy + 8), color, 1)

        # Animated scan beam line
        scan_y = int(t + bh * (0.5 + 0.46 * np.sin(_hud_scan_phase)))
        cv2.line(frame, (l + 4, scan_y), (r - 4, scan_y), (255, 255, 0), 2)

        # Top driver label
        driver_label = driver["name"].upper() if driver else "SCANNING IDENTITY..."
        tag_w = max(160, len(driver_label) * 11)
        cv2.rectangle(frame, (l, max(0, t - 26)), (l + tag_w, max(0, t - 4)), (10, 15, 30), -1)
        cv2.rectangle(frame, (l, max(0, t - 26)), (l + tag_w, max(0, t - 4)), color, 1)
        cv2.putText(frame, driver_label, (l + 6, max(12, t - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.44, color, 1)

        # Bottom 3D depth meter gauge
        if depth_val is not None:
            gy = min(h - 68, b + 10)
            cv2.rectangle(frame, (l, gy), (r, gy + 11), (20, 25, 40), -1)
            fill_pct = float(np.clip(depth_val / 8.0, 0.0, 1.0))
            fill_w = int(bw * fill_pct)
            bar_color = (0, 255, 0) if depth_ok else (0, 0, 255)
            cv2.rectangle(frame, (l, gy), (l + fill_w, gy + 11), bar_color, -1)
            cv2.rectangle(frame, (l, gy), (r, gy + 11), (100, 120, 140), 1)
            depth_tag = f"3D DEPTH: {depth_val:.1f}% [{'OK' if depth_ok else 'PHOTO'}]"
            cv2.putText(frame, depth_tag, (l + 2, gy + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.38, bar_color, 1)

            # EAR Eye meter gauge below depth gauge
            if ear_val > 0:
                ey = gy + 26
                cv2.rectangle(frame, (l, ey), (r, ey + 11), (20, 25, 40), -1)
                ear_pct = float(np.clip(ear_val / 0.40, 0.0, 1.0))
                ear_w = int(bw * ear_pct)
                ear_color = (0, 255, 255) if ear_val >= ear_thresh else (0, 255, 0)
                cv2.rectangle(frame, (l, ey), (l + ear_w, ey + 11), ear_color, -1)
                thresh_x = int(l + bw * (ear_thresh / 0.40))
                cv2.line(frame, (thresh_x, ey - 1), (thresh_x, ey + 12), (0, 0, 255), 2)
                cv2.rectangle(frame, (l, ey), (r, ey + 11), (100, 120, 140), 1)
                ear_state_str = "BLINK!" if ear_val < ear_thresh else "EYES OPEN"
                ear_tag = f"EAR: {ear_val:.2f} [{ear_state_str}]"
                cv2.putText(frame, ear_tag, (l + 2, ey + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.38, ear_color, 1)

# ─────────────────────── DATABASES ──────────────────────────────
rdb = core.rto_db()
ldb = core.local_db()

# ─────────────────────── ESP32 SERIAL ───────────────────────────
try:
    ser = serial.Serial(SERIAL_PORT, 115200, timeout=0)
    print(f"ESP32 connected on {SERIAL_PORT}")
except Exception:
    ser = None
    print(f"ESP32 not found on {SERIAL_PORT} — running without hardware.")

seat_state  = False
serial_buf  = ""
last_sent   = 0
last_mode   = ""
last_extra  = ""


def poll_serial():
    global serial_buf, seat_state
    if ser is None:
        return
    try:
        n = ser.in_waiting
        if n:
            serial_buf += ser.read(n).decode(errors="ignore")
    except Exception:
        return
    if "\n" in serial_buf:
        lines = serial_buf.split("\n")
        serial_buf = lines[-1][-200:]
        for line in reversed(lines[:-1]):
            line = line.strip()
            if line.startswith("SEAT:"):
                seat_state = (line[5:6] == "1")
                break


def send_ignition(mode, extra=""):
    """
    mode: 'U' (Unlock), 'L' (Lock), 'W' (Waiting/Scanning), 'D' (Denied)
    extra: optional text payload (e.g. driver name, reason, stage prompt)
    Sends: f"{mode}:{extra}\n" if extra else f"{mode}\n"
    """
    global last_sent, last_mode, last_extra
    if ser is None:
        return
    t = time.time()
    if mode != last_mode or extra != last_extra or t - last_sent > 0.35:
        last_sent = t
        last_mode = mode
        last_extra = extra
        packet = f"{mode}:{extra}\n" if extra else f"{mode}\n"
        try:
            ser.write(packet.encode("ascii", errors="replace"))
        except Exception:
            pass


# ─────────────────────── CAMERAS ────────────────────────────────
def open_cam(index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    ok = False
    for _ in range(6):
        ok, _ = cap.read()
    if ok:
        return cap
    cap.release()
    return None


# Cameras start as None — only opened when someone sits
cam_main   = None
cam_second = None
cam_active = False      # True when cameras are running


def start_cameras():
    global cam_main, cam_second, cam_active
    if cam_active:
        return True
    print("Seat occupied — starting cameras...")
    cam_main = open_cam(CAM_MAIN)
    if cam_main is None:
        print(f"ERROR: Cannot open main camera (index {CAM_MAIN}).")
        return False
    cam_second = open_cam(CAM_SECOND)
    if cam_second is None:
        print(f"WARNING: Second camera (index {CAM_SECOND}) not found — depth check disabled.")
    cam_active = True
    print("Cameras ready.")
    return True


def stop_cameras():
    global cam_main, cam_second, cam_active
    if cam_main:
        cam_main.release()
        cam_main = None
    if cam_second:
        cam_second.release()
        cam_second = None
    cam_active = False
    cv2.destroyAllWindows()
    print("Cameras stopped.")


# ─────────────────────── DEPTH CHECK ────────────────────────────
_DEPTH_ORDER = ["chin","left_eyebrow","right_eyebrow","nose_bridge",
                "nose_tip","left_eye","right_eye","top_lip","bottom_lip"]
_NOSE_ALL = list(range(27, 36))
_NOSE_TIP = [30, 31, 32, 33, 34, 35]


def _flatten_lm(lm):
    return np.array([p for k in _DEPTH_ORDER for p in lm[k]], dtype=float)


def _depth_score(pts_l, pts_r):
    dx = pts_l[:, 0] - pts_r[:, 0]
    dx = dx * (1.0 if dx.mean() >= 0 else -1.0)
    A   = np.column_stack([pts_l[:, 0], pts_l[:, 1], np.ones(len(pts_l))])
    ref = np.array([i not in _NOSE_ALL for i in range(len(pts_l))])
    coef, *_ = np.linalg.lstsq(A[ref], dx[ref], rcond=None)
    resid = dx - A @ coef
    width = np.linalg.norm(pts_l[45] - pts_l[36])
    return 100.0 * resid[_NOSE_TIP].mean() / max(width, 1.0)


def _get_landmarks(frame):
    small = cv2.resize(frame, (0, 0), fx=SCALE, fy=SCALE)
    locs  = face_recognition.face_locations(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
    if not locs:
        return None, None
    t, r, b, l = max(locs, key=lambda f: (f[2]-f[0])*(f[1]-f[3]))
    box = (int(t/SCALE), int(r/SCALE), int(b/SCALE), int(l/SCALE))
    lm  = face_recognition.face_landmarks(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), [box])
    if not lm:
        return None, box
    return _flatten_lm(lm[0]), box


# ─────────────────────── BLINK ──────────────────────────────────
def _ear(eye):
    a = np.linalg.norm(np.array(eye[1]) - np.array(eye[5]))
    b = np.linalg.norm(np.array(eye[2]) - np.array(eye[4]))
    c = np.linalg.norm(np.array(eye[0]) - np.array(eye[3]))
    return (a + b) / (2.0 * c + 1e-6)


# ─────────────────────── BLINK STATE MACHINE ────────────────────
class BlinkDetector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = "WAIT_FOR_OPEN"
        self.open_frames = 0
        self.closed_frames = 0
        self.baseline_ear = 0.28
        self.ear_history = []
        self.blinks = 0

    def update(self, ear):
        if self.state == "WAIT_FOR_OPEN":
            if 0.21 <= ear <= 0.45:
                self.open_frames += 1
                self.ear_history.append(ear)
                if len(self.ear_history) > 10:
                    self.ear_history.pop(0)
                if self.open_frames >= 4:
                    self.baseline_ear = float(sum(self.ear_history) / len(self.ear_history))
                    self.state = "READY_FOR_BLINK"
            else:
                self.open_frames = 0

        elif self.state == "READY_FOR_BLINK":
            if 0.21 <= ear <= 0.45:
                self.ear_history.append(ear)
                if len(self.ear_history) > 10:
                    self.ear_history.pop(0)
                self.baseline_ear = float(sum(self.ear_history) / len(self.ear_history))

            thresh = max(0.18, min(0.24, self.baseline_ear * 0.76))
            if ear < thresh and ear >= 0.05:
                self.state = "EYE_CLOSED"
                self.closed_frames = 1

        elif self.state == "EYE_CLOSED":
            thresh = max(0.18, min(0.24, self.baseline_ear * 0.76))
            if ear < thresh and ear >= 0.05:
                self.closed_frames += 1
                if self.closed_frames > 8:
                    self.state = "WAIT_FOR_OPEN"
                    self.open_frames = 0
                    self.closed_frames = 0
            else:
                if 1 <= self.closed_frames <= 6 and ear >= self.baseline_ear * 0.78:
                    self.blinks += 1
                    self.state = "WAIT_FOR_OPEN"
                    self.open_frames = 0
                    self.closed_frames = 0
                    return True
                else:
                    self.state = "WAIT_FOR_OPEN"
                    self.open_frames = 0
                    self.closed_frames = 0
        return False

blink_detector = BlinkDetector()

# ─────────────────────── STATE ──────────────────────────────────
driver        = None
session_id    = None
session_start = None
frame_count   = 0
box           = None
last_identity = 0

blinks        = 0
last_blink    = 0.0
ear_val       = 0.0
ear_thresh    = 0.22

depth_buf     = deque(maxlen=DEPTH_SAMPLES)
depth_val     = None
depth_ok      = False

stage         = "IDLE"   # IDLE → DEPTH → IDENTIFY → BLINK → GRANTED / DENIED

# ─────────────────────── HELPERS ────────────────────────────────
def reset_auth():
    """Clear all per-session auth state."""
    global driver, session_id, session_start, box, last_identity
    global blinks, last_blink, ear_val, ear_thresh
    global depth_buf, depth_val, depth_ok, frame_count, stage
    driver          = None
    session_id      = None
    session_start   = None
    box             = None
    last_identity   = 0
    blinks          = 0
    last_blink      = 0.0
    ear_val         = 0.0
    ear_thresh      = 0.22
    blink_detector.reset()
    depth_buf.clear()
    depth_val       = None
    depth_ok        = False
    frame_count     = 0
    stage           = "IDLE"


def end_session_if_running(reason):
    global session_id
    if session_id is not None:
        dur  = time.time() - (session_start or time.time())
        core.end_session(ldb, session_id, reason=reason, duration=dur, km=0)
        name = driver["name"] if driver else "?"
        lic  = driver["licence_no"] if driver else ""
        core.log_event(ldb, "session_end",
                       f"Session ended — {fmtdur(dur)} ({reason})",
                       licence_no=lic, name=name)
        print(f"SESSION END — {name} ({dur:.0f}s) reason={reason}")
        session_id = None


def fmtdur(s):
    s = int(s)
    if s < 60:  return f"{s}s"
    return f"{s//60}m {s%60:02d}s"


# ─────────────────────── MAIN LOOP ──────────────────────────────
print("Drive system ready. Waiting for someone to sit...")
start_stream_server(STREAM_PORT)
core.set_live(ldb, status="idle", driver="", licence="", stage="IDLE", depth=0, blinks=0, seat=0, mode="L")

while True:
    poll_serial()
    key = cv2.waitKey(1) & 0xFF
    now = time.time()

    seated = (ser is None) or seat_state

    # ── STEP 1: seat sensor gate — instant lock ───────────────────
    if not seated:
        if cam_active:
            end_session_if_running("seat_empty")
            reset_auth()
            stop_cameras()
            core.set_live(ldb, status="idle", driver="", licence="", stage="IDLE", depth=0, blinks=0, seat=0, mode="L")
            send_ignition("L")
            speak("Driver unseated. Engine locked.")
        blank = make_standby_frame()
        ret_enc, jbuf = cv2.imencode('.jpg', blank, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if ret_enc:
            with _stream_frame_lock:
                _stream_frame_jpeg = jbuf.tobytes()
        cv2.imshow("Drive System", blank)
        if key == ord('q'):
            break
        continue

    # ── someone is seated — open cameras if not already open ─────
    if not cam_active:
        if not start_cameras():
            time.sleep(1)
            continue
        stage = "DEPTH"
        speak("Driver seated. Commencing biometric security scan.")
        core.set_live(ldb, status="checking", driver="", licence="", stage="DEPTH", depth=0, blinks=0, seat=1, mode="W")

    # ── read camera frames in lockstep ────────────────────────────
    ret_m, frame_m = cam_main.read()
    if not ret_m:
        print("Lost main camera.")
        break

    frame_s = None
    if cam_second is not None:
        ret_s, frame_s = cam_second.read()
        if not ret_s:
            frame_s = None

    mode   = "W"
    status = "Checking..."
    color  = (0, 220, 255)

    # ── find face box on main camera (every 2nd frame or if no box) ──
    if frame_count % 2 == 0 or box is None:
        small = cv2.resize(frame_m, (0, 0), fx=SCALE, fy=SCALE)
        locs  = face_recognition.face_locations(
            cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        if locs:
            t, r, b, l = max(locs, key=lambda f: (f[2]-f[0])*(f[1]-f[3]))
            box = (int(t / SCALE), int(r / SCALE), int(b / SCALE), int(l / SCALE))
        else:
            box = None
    frame_count += 1

    if box is None:
        status = "No face — look at the camera"
        color  = (0, 200, 255)
        mode   = "W"
        send_ignition(mode, "Look at Cam")
        draw_futuristic_hud(frame_m, None, None, status, "SCANNING", color, depth_val, depth_ok, blinks, ear_val, ear_thresh, mode)
        ret_enc, jbuf = cv2.imencode('.jpg', frame_m, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ret_enc:
            with _stream_frame_lock:
                _stream_frame_jpeg = jbuf.tobytes()
        cv2.imshow("Drive System", frame_m)
        if key == ord('q'):
            break
        continue

    rgb = cv2.cvtColor(frame_m, cv2.COLOR_BGR2RGB)

    # ── Extract landmarks ONCE for main camera ────────────────────
    lm_m = face_recognition.face_landmarks(rgb, [box])
    pts_m = _flatten_lm(lm_m[0]) if lm_m else None

    # ── STEP 2: depth check (stereo liveness) ────────────────────
    if cam_second is not None and session_id is None:
        if frame_s is not None and pts_m is not None:
            pts_s, _ = _get_landmarks(frame_s)
            if pts_s is not None:
                s = _depth_score(pts_m, pts_s)
                depth_buf.append(s)
                depth_val = float(np.mean(depth_buf))
                if len(depth_buf) >= 4:
                    depth_ok = (depth_val >= DEPTH_THRESHOLD)
                    if depth_ok:
                        stage = "IDENTIFY"
            else:
                # If camera 2 cannot see the face (e.g. phone screen is blocking or tilted away from cam 2)
                if len(depth_buf) < 4:
                    depth_ok = False
    elif cam_second is None:
        depth_ok  = True   # no second cam — skip depth
        depth_val = None

    # GATING: Still collecting depth frames (< 4 samples)
    if cam_second is not None and not depth_ok and len(depth_buf) < 4:
        mode   = "W"
        status = "SCANNING 3D DEPTH... PLEASE LOOK FORWARD"
        color  = (255, 215, 0)
        stage  = "DEPTH"
        send_ignition(mode, "Scanning 3D")
        draw_futuristic_hud(frame_m, box, None, status, stage, color, depth_val, False, blinks, ear_val, ear_thresh, mode)
        ret_enc, jbuf = cv2.imencode('.jpg', frame_m, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ret_enc:
            with _stream_frame_lock:
                _stream_frame_jpeg = jbuf.tobytes()
        cv2.imshow("Drive System", frame_m)
        if key == ord('q'):
            break
        continue

    # GATING: Confirmed Photo Spoof (depth_val < DEPTH_THRESHOLD after 4+ valid samples)
    if cam_second is not None and not depth_ok and len(depth_buf) >= 4:
        dv = f"{depth_val:.1f}" if depth_val is not None else "0.0"
        status = f"PHOTO SPOOF DETECTED ({dv}%) — REAL 3D FACE REQUIRED"
        color  = (0, 0, 255)
        mode   = "D"
        if stage != "DEPTH_FAIL":
            stage = "DEPTH_FAIL"
            core.log_event(ldb, "depth_fail", f"Photo spoof attempt (depth {dv}%)", level="warn")
            speak("Warning. Photo spoof attempt detected. Access denied.")
        core.set_live(ldb, status="depth_fail", driver="", licence="", stage="DEPTH_FAIL",
                      depth=round(depth_val, 2) if depth_val else 0, blinks=blinks, seat=1, mode="D")
        send_ignition(mode, f"Spoof ({dv}%)")
        draw_futuristic_hud(frame_m, box, None, status, stage, color, depth_val, False, blinks, ear_val, ear_thresh, mode)
        ret_enc, jbuf = cv2.imencode('.jpg', frame_m, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ret_enc:
            with _stream_frame_lock:
                _stream_frame_jpeg = jbuf.tobytes()
        cv2.imshow("Drive System", frame_m)
        if key == ord('q'):
            break
        continue

    # depth passed — move to identify stage
    if stage in ("DEPTH", "DEPTH_FAIL"):
        stage = "IDENTIFY"

    # ── STEP 3: who is this? ──────────────────────────────────────
    if now - last_identity > IDENTITY_EVERY or driver is None:
        last_identity = now
        enc = face_recognition.face_encodings(rgb, [box], model="small")
        if enc:
            rec = core.identify_local(ldb, enc[0])
            if rec is not None:
                if driver is None or driver["licence_no"] != rec["licence_no"]:
                    print(f"[LOCAL] {rec['name']} ({rec['licence_no']})")
                core.touch_driver(ldb, rec["licence_no"])
                driver = rec
            else:
                rec = core.identify_rto(rdb, enc[0])
                if rec is not None:
                    core.cache_driver(ldb, rec, source="RTO")
                    core.touch_driver(ldb, rec["licence_no"])
                    print(f"[RTO→LOCAL] {rec['name']} ({rec['licence_no']})")
                    driver = rec
                else:
                    driver = None

    # ── STEP 4: blink liveness ────────────────────────────────────
    if lm_m:
        left_eye  = lm_m[0]["left_eye"]
        right_eye = lm_m[0]["right_eye"]
        ear = float((_ear(left_eye) + _ear(right_eye)) / 2.0)
        ear_val = ear
        ear_thresh = float(max(0.18, min(0.24, blink_detector.baseline_ear * 0.76)))

        # Draw glowing cyan tracking dots on eye contours
        for pt in left_eye + right_eye:
            cv2.circle(frame_m, (int(pt[0]), int(pt[1])), 2, (255, 255, 0), -1)

        # State machine update: effortlessly catches 1-4 frame natural human blinks, completely immune to photos
        if blink_detector.update(ear):
            blinks = blink_detector.blinks
            last_blink = now
            print(f"REAL BLINK DETECTED! (EAR={ear_val:.2f} < thresh={ear_thresh:.2f}, total={blinks})")
            core.log_event(ldb, "blink", f"Real blink detected (EAR {ear_val:.2f}, total {blinks})")
        else:
            blinks = blink_detector.blinks

    # ── STEP 5: decision ──────────────────────────────────────────
    t_box, r_box, b_box, l_box = box

    if driver is None:
        status = "Face not found in RTO database"
        color  = (0, 0, 255)
        mode   = "D"
        stage  = "IDENTIFY"
        speak("Driver not recognized in RTO database. Access denied.", min_interval=6.0)
        core.set_live(ldb, status="unknown", driver="", licence="", stage="IDENTIFY",
                      depth=round(depth_val, 2) if depth_val else 0, blinks=blinks, seat=1, mode="D")
        send_ignition(mode, "Unknown Driver")

    elif session_id is None and blinks < 1:
        status = f"{driver['name']} — please blink to verify (EAR {ear_val:.2f})"
        color  = (0, 200, 255)
        mode   = "W"
        stage  = "BLINK"
        speak(f"Driver identified as {driver['name']}. Please blink to verify liveness.", min_interval=8.0)
        core.set_live(ldb, status="waiting_blink",
                      driver=driver["name"], licence=driver["licence_no"], stage="BLINK",
                      depth=round(depth_val, 2) if depth_val else 0, blinks=blinks, seat=1, mode="W")
        send_ignition(mode, f"{driver['name'][:10]} Blink")

    else:
        ok, msg = core.check_licence(ldb, driver)
        if ok:
            status = f"ACCESS GRANTED — Welcome, {driver['name']}!" if session_id is None else f"DRIVING — {driver['name']} (ACTIVE)"
            color  = (0, 255, 0)
            mode   = "U"
            stage  = "GRANTED"
            if session_id is None:
                session_id    = core.start_session(ldb, driver, source="face+depth+blink")
                session_start = now
                dv_str = f"{depth_val:.1f}%" if depth_val is not None else "N/A"
                core.log_event(ldb, "session_start",
                               f"Drive session started for {driver['name']} (Depth: {dv_str}, Blinks: {blinks})",
                               licence_no=driver["licence_no"], name=driver["name"])
                print(f"SESSION START — {driver['name']} ({driver['licence_no']}) | Depth={dv_str} | Blinks={blinks}")
                speak(f"Access granted. Welcome, {driver['name']}. Ignition activated.")
            core.set_live(ldb, status="granted",
                          driver=driver["name"], licence=driver["licence_no"], stage="GRANTED",
                          depth=round(depth_val, 2) if depth_val else 0, blinks=blinks, seat=1, mode="U")
            send_ignition(mode, driver["name"])
        else:
            status = f"DENIED — {msg}"
            color  = (0, 0, 255)
            mode   = "D"
            stage  = "DENIED"
            end_session_if_running("denied")
            core.log_event(ldb, "denied", msg, level="warn",
                           licence_no=driver["licence_no"], name=driver["name"])
            speak(f"Access denied. {msg}.")
            core.set_live(ldb, status="denied",
                          driver=driver["name"], licence=driver["licence_no"], stage="DENIED",
                          depth=round(depth_val, 2) if depth_val else 0, blinks=blinks, seat=1, mode="D")
            send_ignition(mode, msg[:16])

    # ── draw futuristic HUD overlay & stream ───────────────────────
    draw_futuristic_hud(frame_m, box, driver, status, stage, color, depth_val, depth_ok, blinks, ear_val, ear_thresh, mode)
    ret_enc, jbuf = cv2.imencode('.jpg', frame_m, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if ret_enc:
        with _stream_frame_lock:
            _stream_frame_jpeg = jbuf.tobytes()

    cv2.imshow("Drive System", frame_m)
    if key == ord('q'):
        break

# ── shutdown ──────────────────────────────────────────────────────
end_session_if_running("quit")
if ser:
    try:
        ser.write(b"L\n")
        ser.close()
    except Exception:
        pass
stop_cameras()
print("Drive system stopped.")