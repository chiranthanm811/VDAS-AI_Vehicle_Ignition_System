"""
Advanced Stereo Depth Calibration & Diagnostics Tool — depth_debug.py
Project: VDAS (Licence-Aware Vehicle Ignition System)

Usage:
  py -3.11 depth_debug.py

Keys:
  R : Capture REAL face sample (tilt head, move closer/further)
  P : Capture PHOTO / SCREEN sample (try holding phone/photo at various angles)
  C : Clear all captured samples
  A : Toggle alignment guidelines (helps align camera heights)
  Q : Quit and output recommended THRESHOLD
"""

import cv2
import face_recognition
import numpy as np
import time
from collections import deque

# ── CAMERA CONFIG ──────────────────────────────────────────────────
CAM_LEFT   = 0       # External USB Webcam 1
CAM_RIGHT  = 1       # External USB Webcam 2
FRAME_W    = 640     # Matching drive_system.py resolution
FRAME_H    = 480
SCALE      = 0.35    # Fast landmark locator scale

# ── LANDMARK DEFINITIONS ───────────────────────────────────────────
ORDER = ["chin", "left_eyebrow", "right_eyebrow", "nose_bridge", "nose_tip",
         "left_eye", "right_eye", "top_lip", "bottom_lip"]
NOSE_ALL = list(range(27, 36))
NOSE_TIP = [30, 31, 32, 33, 34, 35]

def flatten(lm):
    return np.array([p for k in ORDER for p in lm[k]], dtype=float)

def depth_score(left_pts, right_pts):
    """
    Computes nose prominence relative to facial plane using stereo disparity.
    Returns:
        score: Nose protrusion % relative to face width
        y_error: Vertical disparity (should be close to 0 if cameras are level)
    """
    dx = left_pts[:, 0] - right_pts[:, 0]
    dy = np.abs(left_pts[:, 1] - right_pts[:, 1]).mean()
    
    # Correct for left/right camera assignment
    dx = dx * (1.0 if dx.mean() >= 0 else -1.0)
    
    A = np.column_stack([left_pts[:, 0], left_pts[:, 1], np.ones(len(left_pts))])
    ref = np.array([i not in NOSE_ALL for i in range(len(left_pts))])
    
    try:
        coef, *_ = np.linalg.lstsq(A[ref], dx[ref], rcond=None)
        resid = dx - A @ coef
        width = np.linalg.norm(left_pts[45] - left_pts[36])
        score = 100.0 * resid[NOSE_TIP].mean() / max(width, 1.0)
        return float(score), float(dy)
    except Exception:
        return 0.0, float(dy)

def open_camera(index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    for _ in range(6):
        ok, _ = cap.read()
    return cap if ok else None

def get_face(frame):
    small = cv2.resize(frame, (0, 0), fx=SCALE, fy=SCALE)
    locs = face_recognition.face_locations(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
    if not locs:
        return None, None
    t, r, b, l = max(locs, key=lambda f: (f[2] - f[0]) * (f[1] - f[3]))
    box = (int(t / SCALE), int(r / SCALE), int(b / SCALE), int(l / SCALE))
    lm = face_recognition.face_landmarks(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), [box])
    if not lm:
        return None, box
    return flatten(lm[0]), box

def draw_hud(canvas, left_pts, right_pts, score, dy, real_samples, photo_samples, show_guides):
    # Status / HUD
    h, w = canvas.shape[:2]
    
    # Guidelines for physical alignment
    if show_guides:
        for y_pos in [int(h * 0.3), int(h * 0.5), int(h * 0.7)]:
            cv2.line(canvas, (0, y_pos), (w, y_pos), (80, 80, 80), 1, cv2.LINE_AA)
        cv2.line(canvas, (w // 2, 0), (w // 2, h), (100, 100, 100), 1, cv2.LINE_AA)
        cv2.putText(canvas, "[ALIGNMENT GUIDES ON]", (w // 2 - 110, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

    # Score banner
    score_color = (0, 255, 0) if score > 3.0 else (0, 140, 255) if score > 1.5 else (0, 0, 255)
    cv2.putText(canvas, f"Live Depth: {score:.2f}%", (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, score_color, 2)
    
    # Vertical misalignment warning
    align_color = (0, 255, 0) if dy < 8.0 else (0, 165, 255) if dy < 15.0 else (0, 0, 255)
    align_txt = "Level: OK" if dy < 8.0 else f"Vert Misalign ({dy:.1f}px)"
    cv2.putText(canvas, f"Cam Delta Y: {dy:.1f}px [{align_txt}]", (20, 70),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, align_color, 1)

    # Depth meter bar
    bar_x, bar_y, bar_w, bar_h = 20, 85, 260, 16
    cv2.rectangle(canvas, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (50, 50, 50), -1)
    fill_w = int(np.clip(score / 8.0, 0, 1) * bar_w)
    cv2.rectangle(canvas, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), score_color, -1)
    cv2.rectangle(canvas, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (200, 200, 200), 1)

    # Sample statistics
    cv2.putText(canvas, f"REAL Samples  : {len(real_samples)}", (w - 280, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
    if real_samples:
        cv2.putText(canvas, f"  Min: {min(real_samples):.2f} | Avg: {np.mean(real_samples):.2f}",
                    (w - 280, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 255, 180), 1)

    cv2.putText(canvas, f"PHOTO Samples : {len(photo_samples)}", (w - 280, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 100, 255), 1)
    if photo_samples:
        cv2.putText(canvas, f"  Max: {max(photo_samples):.2f} | Avg: {np.mean(photo_samples):.2f}",
                    (w - 280, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 255), 1)

    # Separation analysis
    if real_samples and photo_samples:
        gap = min(real_samples) - max(photo_samples)
        if gap > 0.5:
            rec_th = (min(real_samples) + max(photo_samples)) / 2.0
            stat_msg = f"CLEAN SEPARATION! Rec Threshold = {rec_th:.2f} (Gap: {gap:.2f})"
            stat_col = (0, 255, 0)
        elif gap > 0:
            rec_th = (min(real_samples) + max(photo_samples)) / 2.0
            stat_msg = f"NARROW GAP ({gap:.2f}). Rec Threshold = {rec_th:.2f}"
            stat_col = (0, 255, 255)
        else:
            stat_msg = f"OVERLAP DETECTED ({abs(gap):.2f})! Adjust camera spacing/angle"
            stat_col = (0, 0, 255)
        cv2.putText(canvas, stat_msg, (20, h - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.6, stat_col, 2)

    # Bottom controls bar
    controls = "[R] Add Real Face | [P] Add Photo/Screen | [C] Clear | [A] Guides | [Q] Save & Quit"
    cv2.putText(canvas, controls, (20, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (220, 220, 220), 1)

def main():
    print("=" * 60)
    print("VDAS — Stereo Depth Debug & Calibration Tool")
    print("=" * 60)
    print(f"Connecting to CAM_LEFT={CAM_LEFT} and CAM_RIGHT={CAM_RIGHT}...")

    cap_l = open_camera(CAM_LEFT)
    if not cap_l:
        print(f"ERROR: Cannot open Left Camera (index {CAM_LEFT})")
        return

    cap_r = open_camera(CAM_RIGHT)
    if not cap_r:
        print(f"ERROR: Cannot open Right Camera (index {CAM_RIGHT})")
        cap_l.release()
        return

    print("Cameras connected successfully.")
    print("\nInstructions:")
    print("1. Press 'A' to turn on guidelines — ensure both cameras see eyes on the same horizontal line.")
    print("2. Sit in front and press 'R' 10-15 times while moving slightly.")
    print("3. Hold a phone photo or printed photo and press 'P' 10-15 times.")
    print("4. Press 'Q' to view final calibration results.\n")

    real_samples = []
    photo_samples = []
    show_guides = True
    rolling_scores = deque(maxlen=6)
    rolling_dys = deque(maxlen=6)

    try:
        while True:
            ret_l, frame_l = cap_l.read()
            ret_r, frame_r = cap_r.read()
            if not ret_l or not ret_r:
                print("Failed to read from camera.")
                break

            pts_l, box_l = get_face(frame_l)
            pts_r, box_r = get_face(frame_r)

            cur_score = 0.0
            cur_dy = 0.0

            if pts_l is not None and pts_r is not None:
                s, dy = depth_score(pts_l, pts_r)
                rolling_scores.append(s)
                rolling_dys.append(dy)
                cur_score = float(np.mean(rolling_scores))
                cur_dy = float(np.mean(rolling_dys))

                # Draw landmark points
                for p in pts_l[NOSE_TIP]:
                    cv2.circle(frame_l, (int(p[0]), int(p[1])), 2, (0, 255, 255), -1)
                for p in pts_r[NOSE_TIP]:
                    cv2.circle(frame_r, (int(p[0]), int(p[1])), 2, (0, 255, 255), -1)

                if box_l:
                    t, r, b, l = box_l
                    cv2.rectangle(frame_l, (l, t), (r, b), (0, 255, 0), 1)
                if box_r:
                    t, r, b, l = box_r
                    cv2.rectangle(frame_r, (l, t), (r, b), (0, 255, 0), 1)

            # Combined stereo view
            combined = np.hstack([frame_l, frame_r])
            draw_hud(combined, pts_l, pts_r, cur_score, cur_dy, real_samples, photo_samples, show_guides)

            cv2.imshow("VDAS Stereo Depth Debug", combined)
            key = cv2.waitKey(1) & 0xFF

            if key == ord('q') or key == ord('Q'):
                break
            elif key == ord('r') or key == ord('R'):
                if pts_l is not None and pts_r is not None:
                    real_samples.append(cur_score)
                    print(f"Captured REAL sample #{len(real_samples)}: {cur_score:.2f}%")
                else:
                    print("Face not detected in both cameras — cannot record sample.")
            elif key == ord('p') or key == ord('P'):
                if pts_l is not None and pts_r is not None:
                    photo_samples.append(cur_score)
                    print(f"Captured PHOTO sample #{len(photo_samples)}: {cur_score:.2f}%")
                else:
                    print("Face not detected in both cameras — cannot record sample.")
            elif key == ord('c') or key == ord('C'):
                real_samples.clear()
                photo_samples.clear()
                print("Cleared all samples.")
            elif key == ord('a') or key == ord('A'):
                show_guides = not show_guides

    finally:
        cap_l.release()
        cap_r.release()
        cv2.destroyAllWindows()

    print("\n" + "=" * 60)
    print("CALIBRATION SUMMARY")
    print("=" * 60)
    print(f"Real Samples Captured  : {len(real_samples)}")
    print(f"Photo Samples Captured : {len(photo_samples)}")

    if real_samples and photo_samples:
        min_real = min(real_samples)
        max_photo = max(photo_samples)
        avg_real = np.mean(real_samples)
        avg_photo = np.mean(photo_samples)
        print(f"Real Face Depth   -> Min: {min_real:.2f}%, Avg: {avg_real:.2f}%, Max: {max(real_samples):.2f}%")
        print(f"Photo / Screen    -> Min: {min(photo_samples):.2f}%, Avg: {avg_photo:.2f}%, Max: {max_photo:.2f}%")

        if min_real > max_photo:
            optimal_thresh = round((min_real + max_photo) / 2.0, 2)
            margin = min_real - max_photo
            print(f"\nSUCCESS: Clean separation achieved! (Safety margin: {margin:.2f}%)")
            print(f">>> RECOMMENDED DEPTH_THRESHOLD = {optimal_thresh} <<<")
            print(f"Update drive_system.py line 23: DEPTH_THRESHOLD = {optimal_thresh}")
        else:
            overlap = max_photo - min_real
            print(f"\nWARNING: Scores overlap by {overlap:.2f}%.")
            print("Troubleshooting advice:")
            print("1. Increase physical distance between webcams (target ~6 to 10 cm apart).")
            print("2. Ensure cameras are perfectly level horizontally (keep Cam Delta Y < 8px).")
            print("3. Angle cameras slightly inward towards where the driver sits.")
    else:
        print("Not enough samples recorded to calculate a threshold.")

if __name__ == "__main__":
    main()
