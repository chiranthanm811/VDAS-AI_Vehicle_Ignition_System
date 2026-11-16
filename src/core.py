"""Shared database + decision logic for the licence-aware ignition project.

Used by drive_system.py, dashboard.py and rto_setup.py.
Two databases:
  rto_database.db   -> the "official" RTO licence database (simulated)
  vehicle_local.db  -> the database that stays inside the vehicle
"""
import sqlite3
import time
from datetime import date
from pathlib import Path

import numpy as np

# Base paths for persistent data storage
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

RTO_DB = str(DATA_DIR / "rto_database.db")
LOCAL_DB = str(DATA_DIR / "vehicle_local.db")
VEHICLE_CLASS = "LMV"        # this vehicle is a four-wheeler
MATCH_TOLERANCE = 0.56       # calibrated for reliable live face recognition (0.60 is dlib default)

# Sample RTO records (simulated). The face of each person is added with rto_setup.py
SEED = [
    ("KA05 20190012345", "Arjun Mehta", "1994-03-12", "B+",
     "12, 4th Cross, Koramangala, Bengaluru", "LMV",
     "2019-04-10", "2039-04-09", "valid", "RTO Koramangala (KA-05)"),
    ("KA03 20180045678", "Priya Nair", "1996-07-25", "O+",
     "88, 9th Main, Indiranagar, Bengaluru", "LMV",
     "2018-09-02", "2038-09-01", "valid", "RTO Indiranagar (KA-03)"),
    ("KA01 20210098765", "Rohit Sharma", "1991-11-03", "A+",
     "5, MG Road, Bengaluru", "LMV",
     "2021-01-15", "2041-01-14", "valid", "RTO Central (KA-01)"),
    ("KA09 20150031122", "Sneha Rao", "1989-05-30", "AB+",
     "21, Jayanagar 4th Block, Bengaluru", "LMV",
     "2015-06-20", "2023-06-19", "valid", "RTO Jayanagar (KA-09)"),      # expired
    ("KA02 20170077788", "Vikram Singh", "1985-01-18", "B-",
     "7, Malleshwaram, Bengaluru", "LMV",
     "2017-03-11", "2037-03-10", "suspended", "RTO Rajajinagar (KA-02)"),  # suspended
    ("KA04 20200055566", "Anil Kumar", "1999-09-09", "O-",
     "33, Whitefield, Bengaluru", "MCWG",
     "2020-08-05", "2040-08-04", "valid", "RTO Whitefield (KA-04)"),      # two-wheeler only
]

LICENCE_COLUMNS = ("licence_no, name, dob, blood_group, address, vehicle_class, "
                   "issue_date, expiry_date, status, rto_office")


def _connect(path):
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def rto_db():
    db = _connect(RTO_DB)
    db.execute("""CREATE TABLE IF NOT EXISTS rto_licences (
        licence_no TEXT PRIMARY KEY, name TEXT, dob TEXT, blood_group TEXT,
        address TEXT, vehicle_class TEXT, issue_date TEXT, expiry_date TEXT,
        status TEXT, rto_office TEXT, face BLOB)""")
    if db.execute("SELECT COUNT(*) FROM rto_licences").fetchone()[0] == 0:
        db.executemany("INSERT INTO rto_licences (" + LICENCE_COLUMNS +
                       ") VALUES (?,?,?,?,?,?,?,?,?,?)", SEED)
    db.commit()
    return db


def local_db():
    db = _connect(LOCAL_DB)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS drivers (
        licence_no TEXT PRIMARY KEY, name TEXT, dob TEXT, blood_group TEXT,
        address TEXT, vehicle_class TEXT, issue_date TEXT, expiry_date TEXT,
        status TEXT, rto_office TEXT, face BLOB, first_seen TEXT, last_seen TEXT,
        visits INTEGER DEFAULT 0, source TEXT);
    CREATE TABLE IF NOT EXISTS blocklist (licence_no TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, licence_no TEXT, name TEXT,
        start_time TEXT, start_epoch REAL, end_time TEXT, end_epoch REAL,
        duration_sec REAL DEFAULT 0, km REAL DEFAULT 0, source TEXT, end_reason TEXT);
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, epoch REAL, kind TEXT,
        licence_no TEXT, name TEXT, message TEXT, level TEXT);
    CREATE TABLE IF NOT EXISTS live_status (key TEXT PRIMARY KEY, value TEXT);
    """)
    db.commit()
    return db


# ---------- faces ----------
def to_blob(enc):
    return np.asarray(enc, dtype=np.float64).tobytes()


def from_blob(blob):
    return np.frombuffer(blob, dtype=np.float64).copy()


def nearest(rows, enc):
    """Closest stored face. Returns (row, distance); row is None if nobody is close enough."""
    best, best_d = None, 9.0
    for r in rows:
        if r["face"] is None:
            continue
        d = float(np.linalg.norm(from_blob(r["face"]) - np.asarray(enc)))
        if d < best_d:
            best, best_d = r, d
    if best is not None and best_d <= MATCH_TOLERANCE:
        return best, best_d
    return None, best_d


def identify_local(ldb, enc):
    row, _ = nearest(ldb.execute("SELECT * FROM drivers").fetchall(), enc)
    return dict(row) if row is not None else None


def identify_rto(rdb, enc):
    row, _ = nearest(
        rdb.execute("SELECT * FROM rto_licences WHERE face IS NOT NULL").fetchall(), enc)
    return dict(row) if row is not None else None


def get_driver(ldb, licence_no):
    row = ldb.execute("SELECT * FROM drivers WHERE licence_no=?", (licence_no,)).fetchone()
    return dict(row) if row is not None else None


def cache_driver(ldb, rec, source="RTO"):
    """Save a licence fetched from the RTO database inside the vehicle."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    ldb.execute("""INSERT INTO drivers (licence_no, name, dob, blood_group, address,
        vehicle_class, issue_date, expiry_date, status, rto_office, face,
        first_seen, last_seen, visits, source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
        ON CONFLICT(licence_no) DO UPDATE SET name=excluded.name, dob=excluded.dob,
        blood_group=excluded.blood_group, address=excluded.address,
        vehicle_class=excluded.vehicle_class, issue_date=excluded.issue_date,
        expiry_date=excluded.expiry_date, status=excluded.status,
        rto_office=excluded.rto_office, face=excluded.face""",
                (rec["licence_no"], rec["name"], rec["dob"], rec["blood_group"],
                 rec["address"], rec["vehicle_class"], rec["issue_date"],
                 rec["expiry_date"], rec["status"], rec["rto_office"], rec["face"],
                 now, now, source))
    ldb.commit()


def touch_driver(ldb, licence_no):
    ldb.execute("UPDATE drivers SET visits = visits + 1, last_seen=? WHERE licence_no=?",
                (time.strftime("%Y-%m-%d %H:%M:%S"), licence_no))
    ldb.commit()


# ---------- the licence rules ----------
def check_licence(ldb, rec):
    """Returns (ok, message)."""
    if ldb.execute("SELECT 1 FROM blocklist WHERE licence_no='*'").fetchone():
        return False, "VEHICLE LOCKED BY OWNER"
    if ldb.execute("SELECT 1 FROM blocklist WHERE licence_no=?",
                   (rec["licence_no"],)).fetchone():
        return False, "BLOCKED BY OWNER"
    if rec["status"] != "valid":
        return False, "LICENCE " + rec["status"].upper()
    if rec["expiry_date"] < date.today().isoformat():
        return False, "LICENCE EXPIRED"
    if rec["vehicle_class"] != VEHICLE_CLASS:
        return False, "WRONG VEHICLE CLASS (" + rec["vehicle_class"] + ")"
    return True, "LICENCE VALID"


# ---------- events, sessions, live status ----------
def log_event(ldb, kind, message, level="info", licence_no="", name=""):
    t = time.time()
    ldb.execute("""INSERT INTO events (time, epoch, kind, licence_no, name, message, level)
                   VALUES (?,?,?,?,?,?,?)""",
                (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), t, kind,
                 licence_no, name, message, level))
    ldb.commit()


def start_session(ldb, rec, source):
    t = time.time()
    cur = ldb.execute("""INSERT INTO sessions (licence_no, name, start_time, start_epoch, source)
                         VALUES (?,?,?,?,?)""",
                      (rec["licence_no"], rec["name"],
                       time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), t, source))
    ldb.commit()
    return cur.lastrowid


def update_session(ldb, sid, duration, km):
    ldb.execute("UPDATE sessions SET duration_sec=?, km=? WHERE id=?", (duration, km, sid))
    ldb.commit()


def end_session(ldb, sid, reason, duration=None, km=None):
    t = time.time()
    if duration is not None:
        ldb.execute("UPDATE sessions SET duration_sec=?, km=? WHERE id=?", (duration, km, sid))
    ldb.execute("UPDATE sessions SET end_time=?, end_epoch=?, end_reason=? WHERE id=?",
                (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), t, reason, sid))
    ldb.commit()


def set_live(ldb, **values):
    for k, v in values.items():
        ldb.execute("INSERT OR REPLACE INTO live_status (key, value) VALUES (?,?)", (k, str(v)))
    ldb.commit()


def get_live(ldb):
    return {r["key"]: r["value"] for r in ldb.execute("SELECT key, value FROM live_status")}
