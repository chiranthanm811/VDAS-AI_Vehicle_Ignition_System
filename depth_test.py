"""Two-webcam depth test.

A real face has depth: the nose is closer to the cameras than the eyes and the jaw.
A photo or a phone screen is flat. Two cameras side by side (like two eyes) can tell the
difference, because a closer point shifts more between the left and right picture.

Run it, sit in front of the cameras, and watch the score.
  R = remember the score of my REAL face      (press it many times: different distance / head angle)
  P = remember the score of a PHOTO / phone screen / video   (also many times, tilt it, move it)
  C = clear all remembered scores
  Q = quit
"""
from collections import deque

import cv2
import face_recognition
import numpy as np

LEFT_CAM = 0            # external USB webcam 1
RIGHT_CAM = 1           # external USB webcam 2
WIDTH, HEIGHT = 1280, 720   # a bigger picture gives a more reliable reading
SMALL = 0.35            # faces are searched on a smaller copy of the picture (fast)
THRESHOLD = 2.5         # score above this = real face. Tune it with the R and P keys.

# the face points, in the order used by the face library (72 points: the lip corners count twice)
ORDER = ["chin", "left_eyebrow", "right_eyebrow", "nose_bridge", "nose_tip",
         "left_eye", "right_eye", "top_lip", "bottom_lip"]
NOSE_ALL = list(range(27, 36))
NOSE_TIP = [30, 31, 32, 33, 34, 35]


def flatten(lm):
    return np.array([p for k in ORDER for p in lm[k]], dtype=float)      # 72 x 2


def depth_score(left, right):
    """How far the nose sticks out, as a percentage of the face width.
    Real face: clearly above 0.  Flat photo or screen (even tilted): close to 0."""
    dx = left[:, 0] - right[:, 0]                 # how much each point shifts between the cameras
    dx = dx * (1.0 if dx.mean() >= 0 else -1.0)   # works whichever camera is on the left
    A = np.column_stack([left[:, 0], left[:, 1], np.ones(len(left))])
    ref = np.array([i not in NOSE_ALL for i in range(len(left))])
    # a flat surface (also a tilted one) gives shifts that follow a flat plane: fit that plane
    # to every point except the nose, then see how far the nose is from it
    coef, *_ = np.linalg.lstsq(A[ref], dx[ref], rcond=None)
    resid = dx - A @ coef
    width = np.linalg.norm(left[45] - left[36])   # distance between the outer eye corners
    return 100.0 * resid[NOSE_TIP].mean() / max(width, 1.0)


def open_cam(index):
    for w, h in ((WIDTH, HEIGHT), (640, 480)):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))   # uses less USB bandwidth
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        ok = False
        for _ in range(5):
            ok, _frame = cap.read()
        if ok:
            return cap
        cap.release()
    return None


def face_points(frame):
    """Face points of the biggest face, and its box. (None, None) if there is no face."""
    small = cv2.resize(frame, (0, 0), fx=SMALL, fy=SMALL)
    locs = face_recognition.face_locations(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
    if not locs:
        return None, None
    t, r, b, l = max(locs, key=lambda f: (f[2] - f[0]) * (f[1] - f[3]))
    box = tuple(int(v / SMALL) for v in (t, r, b, l))
    lm = face_recognition.face_landmarks(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), [box])
    if not lm:
        return None, box
    return flatten(lm[0]), box


def put(img, text, x, y, color=(255, 255, 255), scale=0.7):
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)


def report(real, photo):
    """Print what the remembered scores mean. Returns a threshold, or None."""
    print(f"\nSamples so far: real {len(real)}, photo {len(photo)}")
    if not real or not photo:
        print("Remember both kinds (R for your real face, P for a photo).")
        return None
    lo_real, hi_photo = min(real), max(photo)
    print("REAL  :", ", ".join(f"{v:.2f}" for v in real), f"   (lowest {lo_real:.2f})")
    print("PHOTO :", ", ".join(f"{v:.2f}" for v in photo), f"   (highest {hi_photo:.2f})")
    if lo_real - hi_photo >= 0.5:
        thr = (lo_real + hi_photo) / 2
        print(f"CLEAN SEPARATION. Suggested THRESHOLD = {thr:.1f}  (active now in this window)")
        return thr
    if lo_real > hi_photo:
        thr = (lo_real + hi_photo) / 2
        print(f"Separated, but the gap is small. Threshold {thr:.1f} is active, expect some mistakes.")
        print("Tip: move the cameras further apart (10 to 12 cm) and sit about 50 cm away.")
        return thr
    print("OVERLAP: at least one photo scored higher than one of your real faces.")
    print("Tip: move the cameras further apart (10 to 12 cm), sit about 50 cm away,")
    print("keep the cameras level, and keep your face in the middle of the picture.")
    return None


def main():
    if LEFT_CAM == RIGHT_CAM:
        print("LEFT_CAM and RIGHT_CAM must be two different numbers.")
        return
    cap_l, cap_r = open_cam(LEFT_CAM), open_cam(RIGHT_CAM)
    if cap_l is None or cap_r is None:
        print("Could not open both cameras.")
        print(f"  camera {LEFT_CAM}: {'OK' if cap_l else 'NOT FOUND'}")
        print(f"  camera {RIGHT_CAM}: {'OK' if cap_r else 'NOT FOUND'}")
        print("Plug both webcams straight into the laptop (not through a hub),")
        print("run find_camera.py to see their numbers, and set LEFT_CAM / RIGHT_CAM at the top.")
        return

    scores = deque(maxlen=15)
    real_scores, photo_scores = [], []
    threshold = THRESHOLD
    print("Sit in front of the cameras. R = my real face, P = a photo, Q = quit.")

    while True:
        cap_l.grab()
        cap_r.grab()                       # grab both first, so the two pictures match in time
        ok_l, fl = cap_l.retrieve()
        ok_r, fr = cap_r.retrieve()
        if not (ok_l and ok_r):
            print("Lost a camera.")
            break
        if fr.shape != fl.shape:
            fr = cv2.resize(fr, (fl.shape[1], fl.shape[0]))

        pl, box_l = face_points(fl)
        pr, box_r = face_points(fr)
        if pl is not None and pr is not None:
            scores.append(depth_score(pl, pr))
        else:
            scores.clear()
        avg = float(np.mean(scores)) if len(scores) >= 5 else None

        if avg is None:
            verdict, color = "Looking for a face in BOTH cameras...", (200, 200, 200)
        elif avg > threshold:
            verdict, color = f"Score {avg:.1f}  ->  REAL FACE (has depth)", (0, 255, 0)
        else:
            verdict, color = f"Score {avg:.1f}  ->  FLAT (photo or screen?)", (0, 0, 255)

        view = []
        for img, box, name in ((fl, box_l, "camera " + str(LEFT_CAM)), (fr, box_r, "camera " + str(RIGHT_CAM))):
            shown = img.copy()
            if box is not None:
                t, r, b, l = box
                cv2.rectangle(shown, (l, t), (r, b), color, 3)
            shown = cv2.resize(shown, (480, int(480 * shown.shape[0] / shown.shape[1])))
            put(shown, name, 10, 28, (255, 255, 255), 0.7)
            view.append(shown)
        both = np.hstack(view)
        put(both, verdict, 10, both.shape[0] - 40, color, 0.8)
        put(both, f"Threshold {threshold:.1f}   real samples {len(real_scores)}   photo samples "
                  f"{len(photo_scores)}   R = real  P = photo  C = clear  Q = quit",
            10, both.shape[0] - 12, (255, 255, 255), 0.5)
        cv2.imshow("Depth test", both)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        if key == ord('c'):
            real_scores.clear()
            photo_scores.clear()
            print("Cleared all remembered scores.")
        elif key in (ord('r'), ord('p')) and avg is not None:
            (real_scores if key == ord('r') else photo_scores).append(avg)
            print(f"Remembered {'REAL face' if key == ord('r') else 'PHOTO'}: score {avg:.2f}")
            new_threshold = report(real_scores, photo_scores)
            if new_threshold is not None:
                threshold = new_threshold

    cap_l.release()
    cap_r.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()