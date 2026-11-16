import cv2

print("Looking for cameras...")
print("Press any key in the window to see the next camera. Press Q to stop.")

for i in range(5):
    cam = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if not cam.isOpened():
        print(f"Camera {i}: not found")
        continue

    ok = False
    for _ in range(10):            # give the camera a moment to warm up
        ok, frame = cam.read()
    if not ok:
        print(f"Camera {i}: opened but no picture")
        cam.release()
        continue

    h, w = frame.shape[:2]
    print(f"Camera {i}: working ({w}x{h}) - showing it now")

    key = -1
    while True:
        ok, frame = cam.read()
        if not ok:
            break
        cv2.putText(frame, f"CAMERA {i}   (any key = next, Q = quit)", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imshow("Camera finder", frame)
        key = cv2.waitKey(1) & 0xFF
        if key != 255:
            break

    cam.release()
    if key in (ord('q'), ord('Q')):
        break

cv2.destroyAllWindows()
print("Done. Put the number of the camera you want in drive_system.py (CAMERA_INDEX).")