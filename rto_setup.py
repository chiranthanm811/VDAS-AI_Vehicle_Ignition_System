"""RTO office simulator.

Run this once to put people's faces into the (simulated) official RTO licence database.
Every licence record can hold one face. The vehicle later finds the licence by the face.
"""
import cv2
import face_recognition
import numpy as np

import core

CAMERA_INDEX = "auto"      # "auto" = external USB camera if plugged in, else laptop camera

rdb = core.rto_db()
ldb = core.local_db()


def open_camera():
    order = [CAMERA_INDEX] if isinstance(CAMERA_INDEX, int) else [1, 0, 2, 3]
    for i in order:
        c = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if c.isOpened():
            ok = False
            for _ in range(5):
                ok, _frame = c.read()
            if ok:
                return c
        c.release()
    return None


def show_records():
    rows = rdb.execute("SELECT * FROM rto_licences ORDER BY rowid").fetchall()
    print("\nNo  Licence no          Name              Class  Status     Expires     Face")
    print("-" * 82)
    for i, r in enumerate(rows, 1):
        face = "ENROLLED" if r["face"] else "-"
        print(f"{i:<3} {r['licence_no']:<19} {r['name']:<17} {r['vehicle_class']:<6} "
              f"{r['status']:<10} {r['expiry_date']}  {face}")
    return rows


def pick_record():
    rows = show_records()
    try:
        n = int(input("\nWhich record number? ").strip())
        return rows[n - 1]
    except (ValueError, IndexError):
        print("That is not a valid number.")
        return None


def capture_face():
    cam = open_camera()
    if cam is None:
        print("No camera found.")
        return None
    print("Look at the camera. Press SPACE to take the picture, ESC to cancel.")
    enc = None
    while True:
        ok, frame = cam.read()
        if not ok:
            break
        small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        locs = face_recognition.face_locations(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        shown = frame.copy()
        for (t, r, b, l) in locs:
            cv2.rectangle(shown, (l * 2, t * 2), (r * 2, b * 2), (0, 255, 0), 2)
        cv2.putText(shown, "SPACE = take picture   ESC = cancel", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow("RTO enrolment", shown)
        k = cv2.waitKey(1) & 0xFF
        if k == 27:
            break
        if k == 32:
            if len(locs) != 1:
                print("I need exactly one face in the picture. Try again.")
                continue
            t, r, b, l = locs[0]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            found = face_recognition.face_encodings(
                rgb, [(t * 2, r * 2, b * 2, l * 2)], num_jitters=5, model="small")
            if found:
                enc = found[0]
                break
    cam.release()
    cv2.destroyAllWindows()
    return enc


def face_from_photo():
    path = input("Path of the photo file (you can drag the file into this window): ")
    path = path.strip().strip('"')
    try:
        img = face_recognition.load_image_file(path)
    except Exception:
        print("Could not open that file.")
        return None
    locs = face_recognition.face_locations(img)
    if len(locs) != 1:
        print(f"I found {len(locs)} faces in that photo. I need exactly one.")
        return None
    return face_recognition.face_encodings(img, locs, num_jitters=5, model="small")[0]


def save_face(licence_no, enc, new_name=""):
    # a face can belong to only one licence: take it away from any other record
    moved = []
    for r in rdb.execute("SELECT licence_no, name, face FROM rto_licences "
                         "WHERE face IS NOT NULL AND licence_no != ?", (licence_no,)).fetchall():
        if np.linalg.norm(core.from_blob(r["face"]) - enc) <= core.MATCH_TOLERANCE:
            rdb.execute("UPDATE rto_licences SET face=NULL WHERE licence_no=?",
                        (r["licence_no"],))
            moved.append(r["licence_no"])
            print(f"(This face was on {r['name']}'s record - moved to this licence.)")
    rdb.execute("UPDATE rto_licences SET face=? WHERE licence_no=?",
                (core.to_blob(enc), licence_no))
    if new_name:
        rdb.execute("UPDATE rto_licences SET name=? WHERE licence_no=?", (new_name, licence_no))
    rdb.commit()
    # forget old copies inside the vehicle, so it fetches fresh data from the RTO next time
    for lic in [licence_no] + moved:
        ldb.execute("DELETE FROM drivers WHERE licence_no=?", (lic,))
    ldb.commit()
    print("Face saved in the RTO database.")


def enrol(use_camera):
    rec = pick_record()
    if rec is None:
        return
    enc = capture_face() if use_camera else face_from_photo()
    if enc is None:
        print("Nothing saved.")
        return
    name = input(f"Name for this licence (press Enter to keep '{rec['name']}'): ").strip()
    save_face(rec["licence_no"], enc, name)


def ask(prompt, default):
    text = input(f"{prompt} [{default}]: ").strip()
    return text or default


def add_record():
    print("\nNew licence record (press Enter to accept the value in brackets)")
    lic = ask("Licence number", "KA05 20240011111")
    values = (
        lic,
        ask("Name", "New Driver"),
        ask("Date of birth (YYYY-MM-DD)", "1995-01-01"),
        ask("Blood group", "O+"),
        ask("Address", "Bengaluru"),
        ask("Vehicle class (LMV = four-wheeler, MCWG = two-wheeler)", "LMV"),
        ask("Issue date (YYYY-MM-DD)", "2024-01-01"),
        ask("Expiry date (YYYY-MM-DD)", "2044-01-01"),
        ask("Status (valid / suspended)", "valid"),
        ask("RTO office", "RTO Bengaluru"),
    )
    try:
        rdb.execute("INSERT INTO rto_licences (" + core.LICENCE_COLUMNS +
                    ") VALUES (?,?,?,?,?,?,?,?,?,?)", values)
        rdb.commit()
        print("Record added. Now register a face for it (option 2 or 3).")
    except Exception as err:
        print("Could not add the record:", err)


while True:
    print("\n=== RTO DATABASE (simulated) ===")
    print("1  Show all licence records")
    print("2  Register a person's face for a licence (uses the camera)")
    print("3  Register a face from a photo file")
    print("4  Add a new licence record")
    print("5  Quit")
    choice = input("Choose: ").strip()
    if choice == "1":
        show_records()
    elif choice == "2":
        enrol(True)
    elif choice == "3":
        enrol(False)
    elif choice == "4":
        add_record()
    elif choice == "5":
        break
