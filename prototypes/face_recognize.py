import cv2
import face_recognition
import numpy as np
import os

SAVE_FILE = "my_face.npy"
known = np.load(SAVE_FILE) if os.path.exists(SAVE_FILE) else None

cam = cv2.VideoCapture(0, cv2.CAP_DSHOW)
cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

SCALE = 0.25          # find faces on a picture 1/4 the size (very fast)
INV = 4               # to scale the box back up
CHECK_EVERY = 15      # slow "is this you?" check every 15 frames

name, color = "Unknown", (0, 0, 255)
frame_count = 0

while True:
    ret, frame = cam.read()
    if not ret:
        print("Could not read from webcam")
        break

    # 1) fast: find the face on a tiny picture, every frame
    small = cv2.resize(frame, (0, 0), fx=SCALE, fy=SCALE)
    rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    locs = face_recognition.face_locations(rgb_small)

    key = cv2.waitKey(1)

    if locs:
        # pick the biggest face, scale the box back to full size
        t, r, b, l = max(locs, key=lambda f: (f[2] - f[0]) * (f[1] - f[3]))
        box = (t * INV, r * INV, b * INV, l * INV)

        # 2) slow: work out WHO it is, only now and then
        if frame_count % CHECK_EVERY == 0 or key == ord('s'):
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            enc = face_recognition.face_encodings(rgb, [box], model="small")
            if enc:
                if key == ord('s'):
                    np.save(SAVE_FILE, enc[0])
                    known = enc[0]
                    print("Face saved!")
                if known is not None and face_recognition.compare_faces(
                        [known], enc[0], tolerance=0.5)[0]:
                    name, color = "Verified: You", (0, 255, 0)
                else:
                    name, color = "Unknown", (0, 0, 255)

        top, right, bottom, left = box
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.putText(frame, name, (left, top - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

    frame_count += 1
    cv2.putText(frame, "S = save your face | Q = quit", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.imshow("Face Recognition", frame)

    if key == ord('q'):
        break

cam.release()
cv2.destroyAllWindows()