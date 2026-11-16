import cv2
import face_recognition
import numpy as np
import os
import time
from collections import deque

SAVE_FILE = "my_face.npy"
known = np.load(SAVE_FILE) if os.path.exists(SAVE_FILE) else None

cam = cv2.VideoCapture(0, cv2.CAP_DSHOW)
cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)

SCALE = 0.25
INV = 4
IDENTITY_EVERY = 3.0    # seconds between "who is it?" checks
LIVE_SECONDS = 5        # a blink stays valid for this long


def eye_ratio(eye):
    a = np.linalg.norm(np.array(eye[1]) - np.array(eye[5]))
    b = np.linalg.norm(np.array(eye[2]) - np.array(eye[4]))
    c = np.linalg.norm(np.array(eye[0]) - np.array(eye[3]))
    return (a + b) / (2.0 * c)


name = "Unknown"
box = None
frame_count = 0
last_identity = 0
last_blink = 0
blinks = 0
was_closed = False
history = deque(maxlen=40)   # recent eye readings, to learn YOUR open-eye level
ear_value = 0.0
threshold = 0.0
fps = 0.0
t_prev = time.time()

while True:
    ret, frame = cam.read()
    if not ret:
        print("Could not read from webcam")
        break
    key = cv2.waitKey(1)

    now = time.time()
    fps = 0.9 * fps + 0.1 * (1.0 / max(now - t_prev, 0.001))
    t_prev = now

    # 1) find the face (only every 2nd frame, to save time)
    if frame_count % 2 == 0:
        small = cv2.resize(frame, (0, 0), fx=SCALE, fy=SCALE)
        rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        locs = face_recognition.face_locations(rgb_small)
        if locs:
            t, r, b, l = max(locs, key=lambda f: (f[2] - f[0]) * (f[1] - f[3]))
            box = (t * INV, r * INV, b * INV, l * INV)
        else:
            box = None
    frame_count += 1

    status, color = "No face", (200, 200, 200)

    if box is not None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # 2) who is it? (slow, so only every few seconds)
        if now - last_identity > IDENTITY_EVERY or key == ord('s'):
            last_identity = now
            enc = face_recognition.face_encodings(rgb, [box], model="small")
            if enc:
                if key == ord('s'):
                    np.save(SAVE_FILE, enc[0])
                    known = enc[0]
                    print("Face saved!")
                if known is not None and face_recognition.compare_faces(
                        [known], enc[0], tolerance=0.5)[0]:
                    name = "Verified"
                else:
                    name = "Unknown"

        # 3) blink check (fast, every frame)
        lm = face_recognition.face_landmarks(rgb, [box])
        if lm:
            ear_value = (eye_ratio(lm[0]["left_eye"]) +
                         eye_ratio(lm[0]["right_eye"])) / 2.0
            history.append(ear_value)
            if len(history) >= 10:
                baseline = np.percentile(history, 80)      # your open-eye level
                threshold = min(baseline * 0.78, 0.25)     # closed = clearly lower
            closed_now = threshold > 0 and ear_value < threshold
            if was_closed and not closed_now:              # eye just re-opened
                blinks += 1
                last_blink = now
                print("Blink detected!")
            was_closed = closed_now

        live = blinks > 0 and (now - last_blink) < LIVE_SECONDS

        # 4) decision
        if name == "Verified" and live:
            status, color = "ACCESS GRANTED", (0, 255, 0)
        elif name == "Verified":
            status, color = "Blink to prove you are real", (0, 200, 255)
        else:
            status, color = "ACCESS DENIED", (0, 0, 255)

        top, right, bottom, left = box
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.putText(frame, status, (left, top - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    else:
        # face left the camera: forget everything, they must blink again
        name, blinks, was_closed, last_blink = "Unknown", 0, False, 0
        last_identity = 0
        history.clear()
        threshold = 0.0

    cv2.putText(frame, f"FPS: {fps:.0f}  Eye: {ear_value:.2f}  Blinks: {blinks}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(frame, "S = save face | Q = quit", (10, 460),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.imshow("Face + Liveness", frame)

    if key == ord('q'):
        break

cam.release()
cv2.destroyAllWindows()