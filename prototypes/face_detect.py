import cv2
import face_recognition

cam = cv2.VideoCapture(0)
while True:
    ret, frame = cam.read()
    if not ret:
        break

    small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    faces = face_recognition.face_locations(rgb)

    for (top, right, bottom, left) in faces:
        cv2.rectangle(frame, (left*2, top*2), (right*2, bottom*2), (0, 255, 0), 2)

    cv2.putText(frame, f"Faces: {len(faces)}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    cv2.imshow("Face Detection", frame)
    if cv2.waitKey(1) == ord('q'):
        break

cam.release()
cv2.destroyAllWindows()