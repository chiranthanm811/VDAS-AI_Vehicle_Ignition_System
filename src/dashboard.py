"""
VeriDrive Owner Dashboard — dashboard.py
Run: py -3.11 dashboard.py   then open http://127.0.0.1:5000
"""
from flask import Flask, jsonify, Response
import sys
from pathlib import Path

# Ensure local module directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import core, time, urllib.request, numpy as np, cv2

app = Flask(__name__)

def ldb():
    return core.local_db()

def draw_center(img, text, y, font, scale, color, thick=1):
    (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
    x = max(0, (img.shape[1] - tw) // 2)
    cv2.putText(img, text, (x, y), font, scale, color, thick, cv2.LINE_AA)

def generate_proxy_stream():
    url = "http://127.0.0.1:5001/video_feed"
    while True:
        try:
            req = urllib.request.urlopen(url, timeout=1.5)
            while True:
                chunk = req.read(4096)
                if not chunk:
                    break
                yield chunk
        except Exception:
            # Standby screen generator when drive_system is offline or seat is empty
            standby = np.zeros((480, 640, 3), dtype=np.uint8)
            for x in range(0, 640, 40):
                cv2.line(standby, (x, 0), (x, 480), (22, 28, 48), 1)
            for y in range(0, 480, 40):
                cv2.line(standby, (0, y), (640, y), (22, 28, 48), 1)
            cyan = (255, 215, 0)
            c_len = 32
            cv2.line(standby, (20, 20), (20 + c_len, 20), cyan, 2)
            cv2.line(standby, (20, 20), (20, 20 + c_len), cyan, 2)
            cv2.line(standby, (620, 20), (620 - c_len, 20), cyan, 2)
            cv2.line(standby, (620, 20), (620, 20 + c_len), cyan, 2)
            cv2.line(standby, (20, 460), (20 + c_len, 460), cyan, 2)
            cv2.line(standby, (20, 460), (20, 460 - c_len), cyan, 2)
            cv2.line(standby, (620, 460), (620 - c_len, 460), cyan, 2)
            cv2.line(standby, (620, 460), (620, 460 - c_len), cyan, 2)

            t_now = time.strftime("%H:%M:%S")
            draw_center(standby, "VDAS NEURAL VISION CORE", 205, cv2.FONT_HERSHEY_SIMPLEX, 0.72, cyan, 2)
            draw_center(standby, "SYSTEM STANDBY // SEAT VACANT", 248, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 200, 220), 1)
            draw_center(standby, f"CAMERAS ARMED - WAITING FOR DRIVER [{t_now}]", 290, cv2.FONT_HERSHEY_SIMPLEX, 0.44, (120, 140, 180), 1)
            _, buf = cv2.imencode('.jpg', standby, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            frame_bytes = buf.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            time.sleep(0.5)

@app.route("/video_feed")
def video_feed():
    return Response(generate_proxy_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/api/data")
def api_data():
    db  = ldb()
    live = core.get_live(db)

    active_session = None
    row = db.execute("""
        SELECT s.id, s.licence_no, s.name, s.start_time, s.start_epoch,
               s.end_time, s.duration_sec, s.end_reason, s.source,
               d.dob, d.blood_group, d.address, d.vehicle_class,
               d.issue_date, d.expiry_date, d.status, d.rto_office, d.visits
        FROM sessions s
        LEFT JOIN drivers d ON d.licence_no = s.licence_no
        WHERE s.end_time IS NULL
        ORDER BY s.id DESC LIMIT 1""").fetchone()
    if row:
        active_session = dict(row)
        active_session["elapsed"] = time.time() - (row["start_epoch"] or time.time())

    drivers = []
    for r in db.execute("""
        SELECT d.licence_no, d.name, d.dob, d.blood_group, d.address,
               d.vehicle_class, d.issue_date, d.expiry_date, d.status,
               d.rto_office, d.first_seen, d.last_seen, d.visits, d.source,
               (b.licence_no IS NOT NULL) AS blocked
        FROM drivers d
        LEFT JOIN blocklist b ON b.licence_no = d.licence_no
        ORDER BY d.last_seen DESC
    """).fetchall():
        drivers.append(dict(r))

    sessions = []
    for r in db.execute("""
        SELECT id, licence_no, name, start_time, start_epoch,
               end_time, duration_sec, end_reason, source
        FROM sessions ORDER BY id DESC LIMIT 200
    """).fetchall():
        sessions.append(dict(r))

    events = []
    for r in db.execute("""
        SELECT id, time, kind, licence_no, name, message, level
        FROM events ORDER BY id DESC LIMIT 500
    """).fetchall():
        events.append(dict(r))

    stats = {}
    stats["total_sessions"] = db.execute("SELECT COUNT(*) FROM sessions WHERE end_time IS NOT NULL").fetchone()[0]
    stats["total_drivers"]  = db.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
    stats["total_denied"]   = db.execute("SELECT COUNT(*) FROM events WHERE kind='denied'").fetchone()[0]
    stats["total_blocked"]  = db.execute("SELECT COUNT(*) FROM blocklist WHERE licence_no != '*'").fetchone()[0]
    stats["spoof_attempts"] = db.execute("SELECT COUNT(*) FROM events WHERE kind='depth_fail'").fetchone()[0]
    avg = db.execute("SELECT AVG(duration_sec) FROM sessions WHERE end_time IS NOT NULL AND duration_sec > 0").fetchone()[0]
    stats["avg_duration"]   = round(avg or 0)

    locked = db.execute("SELECT 1 FROM blocklist WHERE licence_no='*'").fetchone() is not None

    return jsonify({
        "live": live,
        "active_session": active_session,
        "drivers": drivers,
        "sessions": sessions,
        "events": events,
        "stats": stats,
        "locked": locked
    })

@app.route("/api/block/<path:lic>",   methods=["POST"])
def block(lic):
    db = ldb()
    db.execute("INSERT OR IGNORE INTO blocklist VALUES (?)", (lic,))
    db.commit()
    core.log_event(db, "owner_block", f"Driver blocked by owner", level="warn", licence_no=lic)
    return jsonify({"ok": True})

@app.route("/api/unblock/<path:lic>", methods=["POST"])
def unblock(lic):
    db = ldb()
    db.execute("DELETE FROM blocklist WHERE licence_no=?", (lic,))
    db.commit()
    core.log_event(db, "owner_unblock", f"Driver unblocked by owner", licence_no=lic)
    return jsonify({"ok": True})

@app.route("/api/lock",   methods=["POST"])
def lock_all():
    db = ldb()
    db.execute("INSERT OR IGNORE INTO blocklist VALUES ('*')")
    db.commit()
    core.log_event(db, "owner_lock", "Vehicle locked by owner", level="warn")
    return jsonify({"ok": True})

@app.route("/api/unlock", methods=["POST"])
def unlock_all():
    db = ldb()
    db.execute("DELETE FROM blocklist WHERE licence_no='*'")
    db.commit()
    core.log_event(db, "owner_unlock", "Vehicle unlocked by owner")
    return jsonify({"ok": True})

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>VeriDrive</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0B0F2A;--bg2:#0D1230;--bg3:#111638;
  --card:rgba(255,255,255,0.05);--card2:rgba(255,255,255,0.08);
  --gls:rgba(255,255,255,0.06);
  --line:rgba(255,255,255,0.08);--line2:rgba(255,255,255,0.12);
  --txt:#FFFFFF;--txt2:#A0AEC0;--txt3:#4A5568;
  --blue:#4299E1;--blue2:#3182CE;--blue3:rgba(66,153,225,0.15);
  --cyan:#00B5D8;--cyan2:#0BC5EA;--cyan3:rgba(0,181,216,0.15);
  --green:#38A169;--green2:#48BB78;--green3:rgba(56,161,105,0.15);
  --red:#E53E3E;--red2:#FC8181;--red3:rgba(229,62,62,0.15);
  --gold:#D69E2E;--gold2:#F6E05E;--gold3:rgba(214,158,46,0.15);
  --purple:#805AD5;--purple2:#9F7AEA;--purple3:rgba(128,90,213,0.15);
  --accent:#4299E1;
  --fb:'Plus Jakarta Sans',sans-serif;
  --r:16px;
  --glow-blue:0 0 20px rgba(66,153,225,0.3);
  --glow-green:0 0 20px rgba(56,161,105,0.3);
  --glow-red:0 0 20px rgba(229,62,62,0.3);
  --shadow:0 8px 32px rgba(0,0,0,0.4);
}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;color:var(--txt);font-family:var(--fb);}
body{display:flex;flex-direction:column;max-width:600px;margin:0 auto;
  background:linear-gradient(135deg,#0B0F2A 0%,#0D1340 40%,#0A1628 70%,#0B0F2A 100%);
  min-height:100vh;}

/* topbar */
.topbar{padding:16px 20px;border-bottom:1px solid var(--line);
  display:flex;align-items:center;justify-content:space-between;
  position:sticky;top:0;z-index:30;
  background:rgba(11,15,42,.85);backdrop-filter:blur(24px);}
.logo{display:flex;align-items:center;gap:10px;}
.logo-icon{width:34px;height:34px;border-radius:10px;
  background:linear-gradient(135deg,#4299E1,#805AD5);
  display:flex;align-items:center;justify-content:center;font-size:16px;}
.logo-text{display:flex;flex-direction:column;}
.logo-word{font-size:15px;font-weight:800;letter-spacing:.3px;color:var(--txt);}
.logo-sub{font-size:9px;color:var(--txt3);letter-spacing:.5px;margin-top:1px;}
.ping{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:600;
  padding:6px 13px;border-radius:20px;border:1px solid var(--line2);
  background:var(--gls);backdrop-filter:blur(10px);}
.ping.on{color:var(--green2);border-color:rgba(72,187,120,.3);}
.ping.off{color:var(--red2);border-color:rgba(252,129,129,.3);}
.pingdot{width:7px;height:7px;border-radius:50%;}
.ping.on .pingdot{background:var(--green2);animation:pulse 2s infinite;}
.ping.off .pingdot{background:var(--red2);}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(72,187,120,.5);}50%{box-shadow:0 0 0 5px transparent;}}

.topbar-actions{display:flex;align-items:center;gap:8px;}
.topbar-btn{background:var(--gls);border:1px solid var(--line2);color:var(--txt2);
  border-radius:10px;width:32px;height:32px;display:flex;align-items:center;justify-content:center;
  cursor:pointer;font-size:13px;transition:all .2s;}
.topbar-btn:hover{color:#fff;border-color:var(--cyan);box-shadow:0 0 10px rgba(0,181,216,0.3);}
.topbar-btn.active{color:var(--cyan2);border-color:var(--cyan);background:rgba(0,181,216,0.15);}

/* toast alerts */
.toast-container{position:fixed;top:65px;left:50%;transform:translateX(-50%);
  width:92%;max-width:560px;z-index:99;display:flex;flex-direction:column;gap:8px;pointer-events:none;}
.toast{padding:11px 16px;border-radius:12px;font-size:12px;font-weight:700;color:#fff;
  backdrop-filter:blur(20px);box-shadow:0 8px 30px rgba(0,0,0,0.6);
  display:flex;align-items:center;gap:10px;animation:slideToast .3s ease forwards;
  transition:opacity .4s,transform .4s;pointer-events:auto;}
.toast.green{background:rgba(56,161,105,0.85);border:1px solid #48BB78;box-shadow:0 0 20px rgba(72,187,120,0.4);}
.toast.red{background:rgba(229,62,62,0.85);border:1px solid #FC8181;box-shadow:0 0 20px rgba(252,129,129,0.4);}
.toast.gold{background:rgba(214,158,46,0.85);border:1px solid #F6E05E;box-shadow:0 0 20px rgba(246,224,94,0.4);}
@keyframes slideToast{from{opacity:0;transform:translateY(-15px);}to{opacity:1;transform:translateY(0);}}

/* scroll */
.scroll{flex:1;overflow-y:auto;padding:0 16px 88px;}
.scroll::-webkit-scrollbar{width:3px;}
.scroll::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:3px;}

/* tabs */
.tabbar{position:fixed;bottom:0;left:50%;transform:translateX(-50%);
  width:100%;max-width:600px;border-top:1px solid var(--line);
  background:rgba(11,15,42,.9);backdrop-filter:blur(24px);
  display:flex;z-index:30;padding-bottom:env(safe-area-inset-bottom,0);}
.tabbar button{flex:1;background:none;border:none;color:var(--txt3);
  padding:10px 4px 13px;font-size:10px;font-family:var(--fb);font-weight:600;
  cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:4px;
  transition:color .2s;}
.tabbar button .ico{font-size:20px;line-height:1;}
.tabbar button.active{color:var(--blue);}
.panel{display:none;} .panel.active{display:block;}

/* section title */
.stitle{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;
  color:var(--txt3);margin:22px 0 12px;display:flex;align-items:center;gap:10px;}
.stitle::after{content:'';flex:1;height:1px;background:var(--line);}

/* glass card base */
.gcard{background:rgba(255,255,255,.05);border:1px solid var(--line);
  border-radius:var(--r);backdrop-filter:blur(10px);
  box-shadow:var(--shadow);}

/* live banner */
.live-banner{margin:16px 0 10px;border-radius:var(--r);padding:18px;
  border:1px solid var(--line2);
  background:linear-gradient(135deg,rgba(255,255,255,.07),rgba(255,255,255,.03));
  backdrop-filter:blur(12px);
  display:flex;align-items:center;gap:14px;position:relative;overflow:hidden;
  box-shadow:var(--shadow);transition:border-color .3s,box-shadow .3s;}
.live-banner::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.1),transparent);}
.live-banner.granted{border-color:rgba(72,187,120,.4);box-shadow:var(--glow-green),var(--shadow);}
.live-banner.denied,.live-banner.depth_fail,.live-banner.unknown{border-color:rgba(252,129,129,.4);box-shadow:var(--glow-red),var(--shadow);}
.live-banner.waiting_blink,.live-banner.checking{border-color:rgba(246,224,94,.3);}
.live-glow{position:absolute;inset:0;pointer-events:none;opacity:.06;transition:background .3s;}
.live-banner.granted .live-glow{background:radial-gradient(circle at 10% 50%,#48BB78,transparent 60%);}
.live-banner.denied .live-glow,.live-banner.depth_fail .live-glow,.live-banner.unknown .live-glow{background:radial-gradient(circle at 10% 50%,#FC8181,transparent 60%);}
.live-banner.waiting_blink .live-glow,.live-banner.checking .live-glow{background:radial-gradient(circle at 10% 50%,#F6E05E,transparent 60%);}
.live-ico{font-size:26px;flex-shrink:0;}
.live-body{flex:1;min-width:0;}
.live-main{font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.live-sub{font-size:11px;color:var(--txt2);margin-top:4px;}
.live-chip{font-size:10px;font-weight:700;padding:4px 11px;border-radius:20px;
  flex-shrink:0;letter-spacing:.5px;}
.live-chip.granted{background:var(--green3);color:var(--green2);border:1px solid rgba(72,187,120,.3);}
.live-chip.denied,.live-chip.depth_fail,.live-chip.unknown{background:var(--red3);color:var(--red2);border:1px solid rgba(252,129,129,.3);}
.live-chip.waiting_blink,.live-chip.checking{background:var(--gold3);color:var(--gold2);border:1px solid rgba(246,224,94,.3);}
.live-chip.idle{background:rgba(255,255,255,.06);color:var(--txt3);border:1px solid var(--line);}

/* live biometric hud card */
.hud-card{background:linear-gradient(135deg,rgba(11,15,42,0.85),rgba(17,22,56,0.85));
  border:1px solid rgba(0,181,216,0.25);border-radius:var(--r);overflow:hidden;
  margin-bottom:12px;backdrop-filter:blur(16px);box-shadow:0 8px 32px rgba(0,0,0,0.5), 0 0 15px rgba(0,181,216,0.1);}
.hud-head{padding:12px 16px;display:flex;justify-content:space-between;align-items:center;
  border-bottom:1px solid rgba(255,255,255,0.08);background:rgba(0,181,216,0.04);}
.hud-head-left{display:flex;align-items:center;gap:8px;}
.hud-dot{width:8px;height:8px;border-radius:50%;background:#00B5D8;box-shadow:0 0 8px #00B5D8;animation:pulseDot 2s infinite;}
@keyframes pulseDot{0%,100%{opacity:1;transform:scale(1);}50%{opacity:0.4;transform:scale(0.8);}}
.hud-head-title{font-size:11.5px;font-weight:800;letter-spacing:1px;color:#00B5D8;}
.hud-head-right{display:flex;align-items:center;gap:8px;}
.hud-depth{font-size:10.5px;font-weight:700;color:var(--cyan2);background:rgba(0,181,216,0.12);
  padding:3px 9px;border-radius:12px;border:1px solid rgba(0,181,216,0.25);}
.hud-chip{font-size:10px;font-weight:700;padding:3px 9px;border-radius:12px;letter-spacing:.4px;}
.hud-chip.granted{background:var(--green3);color:var(--green2);border:1px solid rgba(72,187,120,.3);}
.hud-chip.denied,.hud-chip.depth_fail,.hud-chip.unknown{background:var(--red3);color:var(--red2);border:1px solid rgba(252,129,129,.3);}
.hud-chip.waiting_blink,.hud-chip.checking{background:var(--gold3);color:var(--gold2);border:1px solid rgba(246,224,94,.3);}
.hud-chip.idle{background:rgba(255,255,255,.06);color:var(--txt3);border:1px solid var(--line);}

.hud-video-wrap{position:relative;width:100%;aspect-ratio:4/3;background:#050716;overflow:hidden;display:flex;align-items:center;justify-content:center;}
.hud-feed-img{width:100%;height:100%;object-fit:cover;display:block;}
.hud-scan-beam{position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,transparent,#00B5D8,transparent);
  box-shadow:0 0 10px #00B5D8;pointer-events:none;animation:scanBeam 3s linear infinite;}
@keyframes scanBeam{0%{top:0%;opacity:0.8;}50%{opacity:0.3;}100%{top:100%;opacity:0.8;}}

.hud-corner{position:absolute;width:16px;height:16px;pointer-events:none;}
.hud-corner.tl{top:8px;left:8px;border-top:2px solid #00B5D8;border-left:2px solid #00B5D8;}
.hud-corner.tr{top:8px;right:8px;border-top:2px solid #00B5D8;border-right:2px solid #00B5D8;}
.hud-corner.bl{bottom:8px;left:8px;border-bottom:2px solid #00B5D8;border-left:2px solid #00B5D8;}
.hud-corner.br{bottom:8px;right:8px;border-bottom:2px solid #00B5D8;border-right:2px solid #00B5D8;}

/* 5-layer pipeline */
.hud-pipeline{display:flex;align-items:center;justify-content:space-between;padding:14px 12px;background:rgba(0,0,0,0.25);border-top:1px solid var(--line);}
.pipe-node{display:flex;flex-direction:column;align-items:center;text-align:center;gap:3px;flex:1;}
.pipe-icon{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:14px;background:rgba(255,255,255,0.06);border:1px solid var(--line);transition:all .3s;}
.pipe-name{font-size:9.5px;font-weight:700;color:var(--txt3);letter-spacing:0.3px;transition:color .3s;}
.pipe-sub{font-size:8px;color:var(--txt3);font-weight:500;}

.pipe-line{height:2px;flex:0.6;background:rgba(255,255,255,0.08);margin:0 -2px 14px;transition:all .3s;}
.pipe-line.done{background:#48BB78;box-shadow:0 0 6px rgba(72,187,120,0.6);}

.pipe-node.done .pipe-icon{background:rgba(56,161,105,0.2);border-color:#48BB78;box-shadow:0 0 10px rgba(72,187,120,0.4);}
.pipe-node.done .pipe-name{color:#48BB78;}
.pipe-node.active .pipe-icon{background:rgba(0,181,216,0.2);border-color:#00B5D8;box-shadow:0 0 12px rgba(0,181,216,0.6);animation:pulseNode 1.2s infinite;}
.pipe-node.active .pipe-name{color:#00B5D8;}
.pipe-node.fail .pipe-icon{background:rgba(229,62,62,0.2);border-color:#FC8181;box-shadow:0 0 10px rgba(252,129,129,0.5);}
.pipe-node.fail .pipe-name{color:#FC8181;}
@keyframes pulseNode{0%,100%{transform:scale(1);}50%{transform:scale(1.08);}}

/* stat grid */
.stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:4px;}
.sbox{background:linear-gradient(135deg,rgba(255,255,255,.07),rgba(255,255,255,.03));
  border:1px solid var(--line);border-radius:var(--r);
  padding:16px 10px 13px;text-align:center;
  backdrop-filter:blur(10px);box-shadow:var(--shadow);
  position:relative;overflow:hidden;transition:transform .2s,box-shadow .2s;}
.sbox:hover{transform:translateY(-2px);box-shadow:0 12px 40px rgba(0,0,0,.5);}
.sbox::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.2),transparent);}
.snum{font-size:22px;font-weight:800;display:block;margin-bottom:4px;color:var(--txt);}
.snum.c{color:var(--cyan2);}  .snum.g{color:var(--green2);} .snum.r{color:var(--red2);}
.snum.o{color:#F6AD55;}       .snum.p{color:var(--purple2);} .snum.y{color:var(--gold2);}
.slbl{font-size:9.5px;color:var(--txt3);font-weight:600;letter-spacing:.3px;}

/* active driver card */
.acard{background:linear-gradient(135deg,rgba(255,255,255,.07),rgba(255,255,255,.03));
  border:1px solid var(--line2);border-radius:var(--r);overflow:hidden;
  margin-bottom:10px;backdrop-filter:blur(10px);box-shadow:var(--shadow);}
.acard-top{padding:18px 16px 14px;display:flex;gap:14px;align-items:center;}
.av{width:50px;height:50px;border-radius:50%;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  font-weight:800;font-size:16px;
  background:linear-gradient(135deg,#4299E1,#805AD5);color:#fff;
  box-shadow:0 4px 15px rgba(66,153,225,.4);}
.av.dim{background:rgba(255,255,255,.08);color:var(--txt3);box-shadow:none;}
.aname{font-size:15px;font-weight:700;color:var(--txt);}
.alic{font-size:10.5px;color:var(--txt2);margin-top:3px;}
.badge{font-size:9.5px;font-weight:700;padding:4px 11px;border-radius:20px;flex-shrink:0;}
.badge.ok{background:var(--green3);color:var(--green2);border:1px solid rgba(72,187,120,.3);}
.badge.bad{background:var(--red3);color:var(--red2);border:1px solid rgba(252,129,129,.3);}
.badge.warn{background:var(--gold3);color:var(--gold2);border:1px solid rgba(246,224,94,.3);}
.sep{height:1px;background:var(--line);}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);padding:16px;}
.met{text-align:center;}
.met b{font-size:20px;font-weight:800;display:block;margin-bottom:4px;
  background:linear-gradient(135deg,#4299E1,#9F7AEA);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;}
.met span{font-size:9.5px;color:var(--txt3);font-weight:600;letter-spacing:.3px;}
.irows{padding:4px 16px 14px;}
.irow{display:flex;justify-content:space-between;align-items:center;
  padding:9px 0;border-top:1px solid var(--line);font-size:12.5px;}
.irow:first-child{border-top:none;}
.irow .k{color:var(--txt3);}
.irow .v{font-weight:600;text-align:right;max-width:62%;line-height:1.4;}
.irow .v.ok{color:var(--green2);} .irow .v.bad{color:var(--red2);} .irow .v.warn{color:var(--gold2);}

/* feed */
.feed{background:rgba(255,255,255,.04);border:1px solid var(--line);
  border-radius:var(--r);padding:2px 14px;margin-bottom:10px;
  backdrop-filter:blur(10px);box-shadow:var(--shadow);}
.frow{display:flex;gap:12px;align-items:flex-start;
  padding:12px 0;border-top:1px solid var(--line);}
.frow:first-child{border-top:none;}
.frow .fi{width:34px;height:34px;border-radius:10px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-size:15px;}
.fi.g{background:var(--green3);box-shadow:0 0 10px rgba(72,187,120,.2);}
.fi.r{background:var(--red3);box-shadow:0 0 10px rgba(252,129,129,.2);}
.fi.y{background:var(--gold3);}
.fi.i{background:rgba(255,255,255,.06);}
.fi.c{background:var(--blue3);box-shadow:0 0 10px rgba(66,153,225,.2);}
.frow .fb .t{font-size:12.5px;font-weight:600;color:var(--txt);}
.frow .fb .d{font-size:10.5px;color:var(--txt3);margin-top:2px;line-height:1.4;}
.frow .fb .lic{font-size:10px;color:var(--cyan2);margin-top:2px;}

/* empty */
.empty{text-align:center;padding:30px 16px;color:var(--txt3);font-size:12.5px;
  background:rgba(255,255,255,.03);border:1px dashed var(--line2);
  border-radius:var(--r);line-height:1.8;}
.empty .ei{font-size:28px;display:block;margin-bottom:10px;}

/* driver list */
.dlist{display:flex;flex-direction:column;gap:8px;}
.drow-card{background:rgba(255,255,255,.05);border:1px solid var(--line);
  border-radius:var(--r);padding:14px 16px;
  display:flex;align-items:center;gap:12px;cursor:pointer;
  backdrop-filter:blur(10px);box-shadow:var(--shadow);
  transition:border-color .2s,transform .2s,box-shadow .2s;}
.drow-card:hover{border-color:rgba(66,153,225,.4);transform:translateY(-1px);
  box-shadow:0 8px 30px rgba(0,0,0,.5);}
.drow-card.blocked{border-color:rgba(252,129,129,.3);}
.dav{width:42px;height:42px;border-radius:50%;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px;
  background:linear-gradient(135deg,#4299E1,#805AD5);color:#fff;
  box-shadow:0 4px 12px rgba(66,153,225,.3);}
.dav.dim{background:rgba(255,255,255,.08);color:var(--txt3);box-shadow:none;}
.drow-info{flex:1;min-width:0;}
.drow-name{font-weight:700;font-size:13.5px;color:var(--txt);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.drow-lic{font-size:10.5px;color:var(--txt2);margin-top:2px;}
.drow-meta{font-size:10.5px;color:var(--txt3);margin-top:1px;}
.chevron{color:var(--txt3);font-size:12px;flex-shrink:0;}

/* driver detail */
.ddetail{display:none;background:rgba(255,255,255,.04);border:1px solid var(--line2);
  border-radius:var(--r);margin-top:6px;overflow:hidden;
  backdrop-filter:blur(10px);box-shadow:var(--shadow);}
.ddetail.open{display:block;}
.ddetail-head{padding:16px;display:flex;gap:12px;align-items:center;
  border-bottom:1px solid var(--line);
  background:linear-gradient(135deg,rgba(66,153,225,.1),rgba(128,90,213,.1));}
.ddetail-rows{padding:4px 16px 8px;}
.ddetail-row{display:flex;justify-content:space-between;padding:9px 0;
  border-top:1px solid var(--line);font-size:12.5px;}
.ddetail-row:first-child{border-top:none;}
.ddetail-row .k{color:var(--txt3);}
.ddetail-row .v{font-weight:600;text-align:right;max-width:60%;color:var(--txt);}
.ddetail-foot{padding:0 16px 16px;display:flex;gap:8px;}
.dbtn{flex:1;padding:11px;border-radius:10px;border:none;font-size:12px;
  font-weight:700;cursor:pointer;font-family:var(--fb);}
.dbtn.block{background:var(--red3);color:var(--red2);border:1px solid rgba(252,129,129,.3);}
.dbtn.unblock{background:var(--green3);color:var(--green2);border:1px solid rgba(72,187,120,.3);}

/* session cards */
.scard{background:rgba(255,255,255,.05);border:1px solid var(--line);
  border-radius:var(--r);padding:13px 14px;margin-bottom:8px;
  border-left:2px solid var(--line2);
  backdrop-filter:blur(10px);box-shadow:var(--shadow);}
.scard.granted{border-left-color:var(--green2);}
.scard.active{border-left-color:var(--blue);}
.scard.denied{border-left-color:var(--red2);}
.scard-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;}
.scard-name{font-weight:700;font-size:13.5px;color:var(--txt);}
.scard-dur{font-size:12px;font-weight:700;}
.scard-dur.live{color:var(--cyan2);}
.scard-dur.ok{color:var(--green2);}
.scard-dur.na{color:var(--txt3);}
.scard-lic{font-size:10.5px;color:var(--txt2);margin-bottom:4px;}
.scard-meta{font-size:10.5px;color:var(--txt3);line-height:1.6;}

/* event log */
.elog{display:flex;flex-direction:column;gap:6px;}
.ecard{display:flex;gap:10px;align-items:flex-start;
  background:rgba(255,255,255,.04);border:1px solid var(--line);border-radius:var(--r);
  padding:12px 14px;border-left:2px solid var(--line2);
  backdrop-filter:blur(10px);box-shadow:var(--shadow);}
.ecard.green{border-left-color:var(--green2);}
.ecard.red{border-left-color:var(--red2);}
.ecard.gold{border-left-color:var(--gold2);}
.ecard.cyan{border-left-color:var(--cyan2);}
.ecard.grey{border-left-color:var(--txt3);}
.eico{font-size:16px;flex-shrink:0;margin-top:2px;}
.ebody .et{font-size:12.5px;font-weight:600;color:var(--txt);}
.ebody .ed{font-size:10.5px;color:var(--txt3);margin-top:2px;}
.ebody .elic{font-size:10px;color:var(--cyan2);margin-top:2px;}

/* settings */
.bigbtn{width:100%;padding:15px;border-radius:var(--r);border:none;font-size:13px;
  font-weight:700;cursor:pointer;font-family:var(--fb);margin-bottom:12px;
  transition:transform .15s,box-shadow .15s;}
.bigbtn:hover{transform:translateY(-1px);}
.bigbtn.danger{background:linear-gradient(135deg,#E53E3E,#C53030);color:#fff;
  box-shadow:0 4px 20px rgba(229,62,62,.4);}
.bigbtn.safe{background:linear-gradient(135deg,#38A169,#276749);color:#fff;
  box-shadow:0 4px 20px rgba(56,161,105,.4);}
.icard{background:rgba(255,255,255,.04);border:1px solid var(--line);
  border-radius:var(--r);padding:2px 16px;margin-bottom:10px;
  backdrop-filter:blur(10px);box-shadow:var(--shadow);}
.icard .irow{display:flex;justify-content:space-between;align-items:center;
  padding:10px 0;border-top:1px solid var(--line);font-size:12.5px;}
.icard .irow:first-child{border-top:none;}
.icard .irow .k{color:var(--txt3);}
.icard .irow .v{font-weight:600;color:var(--txt);}
.icard .irow .v.c{color:var(--cyan2);}
.sec-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px;}
.sec-box{background:rgba(255,255,255,.05);border:1px solid var(--line);
  border-radius:var(--r);padding:13px 14px;
  display:flex;align-items:center;gap:10px;
  backdrop-filter:blur(10px);box-shadow:var(--shadow);}
.sec-box .si{font-size:18px;flex-shrink:0;}
.sec-box .st .t{font-size:12px;font-weight:700;color:var(--txt);}
.sec-box .st .d{font-size:10px;color:var(--txt3);margin-top:2px;}
.led{width:8px;height:8px;border-radius:50%;margin-left:auto;flex-shrink:0;}
.led.on{background:var(--green2);box-shadow:0 0 8px var(--green2);}
.led.off{background:var(--txt3);}

/* overlay */
.ovl{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);
  z-index:50;align-items:flex-end;justify-content:center;
  backdrop-filter:blur(4px);}
.ovl.show{display:flex;}
.sheet{background:linear-gradient(135deg,rgba(13,18,48,.98),rgba(11,15,42,.98));
  border:1px solid var(--line2);border-radius:24px 24px 0 0;
  padding:28px 20px env(safe-area-inset-bottom,28px);
  width:100%;max-width:600px;box-shadow:0 -16px 60px rgba(0,0,0,.6);}
.sheet h3{font-size:18px;font-weight:800;margin-bottom:8px;color:var(--txt);}
.sheet p{font-size:13px;color:var(--txt2);margin-bottom:24px;line-height:1.6;}
.ok-btn{width:100%;padding:14px;border-radius:var(--r);border:none;font-size:13px;
  font-weight:700;cursor:pointer;font-family:var(--fb);
  margin-bottom:10px;
  background:linear-gradient(135deg,#4299E1,#3182CE);color:#fff;
  box-shadow:0 4px 20px rgba(66,153,225,.4);transition:transform .15s;}
.ok-btn:hover{transform:translateY(-1px);}
.ok-btn.danger{background:linear-gradient(135deg,#E53E3E,#C53030);
  box-shadow:0 4px 20px rgba(229,62,62,.4);}
.cancel{width:100%;padding:13px;border-radius:var(--r);
  border:1px solid var(--line2);background:rgba(255,255,255,.05);color:var(--txt3);
  font-size:13px;cursor:pointer;font-family:var(--fb);}
</style>
</head>
<body>

<div class="toast-container" id="toastContainer"></div>

<div class="topbar">
  <div class="logo">
    <div class="logo-icon">🚗</div>
    <div class="logo-text">
      <span class="logo-word">VeriDrive</span>
      <span class="logo-sub">AI Licence-Aware Ignition</span>
    </div>
  </div>
  <div class="topbar-actions">
    <button class="topbar-btn active" id="sfxBtn" onclick="toggleAudio()" title="Toggle Cyber Audio FX">🔊</button>
    <button class="topbar-btn" id="kioskBtn" onclick="toggleKiosk()" title="Presentation Kiosk Mode">⛶</button>
    <div class="ping off" id="ping"><div class="pingdot"></div><span id="pingTxt">Offline</span></div>
  </div>
</div>

<div class="scroll">

  <!-- ══ TAB 1: ACTIVE ══ -->
  <div class="panel active" id="tab-home">

    <div class="live-banner idle" id="liveBanner">
      <div class="live-glow" id="liveGlow"></div>
      <div class="live-ico" id="liveIco">🔒</div>
      <div class="live-body">
        <div class="live-main" id="liveMain">System Idle</div>
        <div class="live-sub"  id="liveSub">Waiting for driver to sit…</div>
      </div>
      <div class="live-chip idle" id="liveChip">IDLE</div>
    </div>

    <div class="stitle">Live Vision Feed & Security HUD</div>
    <div class="hud-card">
      <div class="hud-head">
        <div class="hud-head-left">
          <span class="hud-dot"></span>
          <span class="hud-head-title">BIOMETRIC VISION STREAM</span>
        </div>
        <div class="hud-head-right">
          <span class="hud-chip idle" id="hudStageBadge">STANDBY</span>
          <span class="hud-depth" id="hudDepthBadge">DEPTH: STANDBY</span>
        </div>
      </div>
      <div class="hud-video-wrap">
        <img id="liveFeedImg" src="/video_feed" class="hud-feed-img" alt="VDAS Video Stream" />
        <div class="hud-scan-beam"></div>
        <div class="hud-corner tl"></div>
        <div class="hud-corner tr"></div>
        <div class="hud-corner bl"></div>
        <div class="hud-corner br"></div>
      </div>
      <div class="hud-pipeline">
        <div class="pipe-node idle" id="pipeSeat">
          <div class="pipe-icon">🪑</div>
          <div class="pipe-name">SEAT</div>
          <div class="pipe-sub">GPIO34</div>
        </div>
        <div class="pipe-line idle" id="line1"></div>
        <div class="pipe-node idle" id="pipeDepth">
          <div class="pipe-icon">📐</div>
          <div class="pipe-name">DEPTH</div>
          <div class="pipe-sub">&gt; 3.2%</div>
        </div>
        <div class="pipe-line idle" id="line2"></div>
        <div class="pipe-node idle" id="pipeFace">
          <div class="pipe-icon">👤</div>
          <div class="pipe-name">FACE ID</div>
          <div class="pipe-sub">RTO / Local</div>
        </div>
        <div class="pipe-line idle" id="line3"></div>
        <div class="pipe-node idle" id="pipeBlink">
          <div class="pipe-icon">👁️</div>
          <div class="pipe-name">BLINK</div>
          <div class="pipe-sub">Liveness</div>
        </div>
        <div class="pipe-line idle" id="line4"></div>
        <div class="pipe-node idle" id="pipeIgnition">
          <div class="pipe-icon">⚡</div>
          <div class="pipe-name">RELAY</div>
          <div class="pipe-sub">Ignition</div>
        </div>
      </div>
    </div>

    <div class="stitle">Overview</div>
    <div class="stat-grid" id="statGrid"></div>

    <div class="stitle">Active Driver</div>
    <div id="activeCard"></div>

    <div class="stitle">Recent Activity</div>
    <div id="recentFeed"></div>

  </div>

  <!-- ══ TAB 2: DRIVERS ══ -->
  <div class="panel" id="tab-drivers">
    <div class="stitle">Driver Profiles <span id="dCount" style="color:var(--txt3);font-size:10px;margin-left:4px;text-transform:none;letter-spacing:0;font-weight:600;"></span></div>
    <div class="dlist" id="driverList"></div>
  </div>

  <!-- ══ TAB 3: LOG ══ -->
  <div class="panel" id="tab-log">
    <div class="stitle">Drive Sessions</div>
    <div id="sessLog"></div>
    <div class="stitle">Event Log</div>
    <div class="elog" id="eventLog"></div>
  </div>

  <!-- ══ TAB 4: SETTINGS ══ -->
  <div class="panel" id="tab-settings">
    <div class="stitle">Vehicle Control</div>
    <button class="bigbtn danger" id="lockBtn" onclick="toggleLock()">🔒 &nbsp;LOCK VEHICLE</button>

    <div class="stitle">Simulation & Demo Lab</div>
    <div class="sec-grid" style="margin-bottom:12px;">
      <button class="bigbtn safe" onclick="simulateGranted()" style="padding:11px 8px;font-size:11px;margin-bottom:0;">⚡ Demo: Unlock Audio & HUD</button>
      <button class="bigbtn danger" onclick="simulateSpoof()" style="padding:11px 8px;font-size:11px;margin-bottom:0;">🛡️ Demo: Spoof Alert & Alarm</button>
    </div>

    <div class="stitle">Security Layers</div>
    <div class="sec-grid">
      <div class="sec-box"><span class="si">📷</span><div class="st"><div class="t">Face Recognition</div><div class="d">AI · dlib</div></div><div class="led on"></div></div>
      <div class="sec-box"><span class="si">👁</span><div class="st"><div class="t">Blink Liveness</div><div class="d">EAR detection</div></div><div class="led on"></div></div>
      <div class="sec-box"><span class="si">📐</span><div class="st"><div class="t">Depth Sensing</div><div class="d">Stereo cameras</div></div><div class="led on"></div></div>
      <div class="sec-box"><span class="si">🪑</span><div class="st"><div class="t">Seat Sensor</div><div class="d">FSR · GPIO34</div></div><div class="led on"></div></div>
      <div class="sec-box"><span class="si">🗄</span><div class="st"><div class="t">RTO Database</div><div class="d">Licence lookup</div></div><div class="led on"></div></div>
      <div class="sec-box"><span class="si">💾</span><div class="st"><div class="t">Local Cache</div><div class="d">Fast revisit</div></div><div class="led on"></div></div>
    </div>

    <div class="stitle">System Info</div>
    <div class="icard">
      <div class="irow"><span class="k">Vehicle class</span><span class="v c">LMV</span></div>
      <div class="irow"><span class="k">ESP32 port</span><span class="v c">COM4</span></div>
      <div class="irow"><span class="k">Depth threshold</span><span class="v c">3.2%</span></div>
      <div class="irow"><span class="k">Seat lock</span><span class="v">Instant</span></div>
      <div class="irow"><span class="k">Dashboard refresh</span><span class="v">2s</span></div>
      <div class="irow"><span class="k">RTO DB</span><span class="v">rto_database.db</span></div>
      <div class="irow"><span class="k">Local DB</span><span class="v">vehicle_local.db</span></div>
    </div>

    <div class="stitle">Future Scope</div>
    <div class="icard">
      <div class="irow"><span class="k">🍺 Alcohol sensor</span><span class="v" style="color:var(--txt3);font-weight:400">MQ-3 (planned)</span></div>
      <div class="irow"><span class="k">😴 Drowsiness</span><span class="v" style="color:var(--txt3);font-weight:400">Eye tracking (planned)</span></div>
      <div class="irow"><span class="k">📍 GPS logging</span><span class="v" style="color:var(--txt3);font-weight:400">Location (planned)</span></div>
      <div class="irow"><span class="k">🌙 Night mode</span><span class="v" style="color:var(--txt3);font-weight:400">IR camera (planned)</span></div>
      <div class="irow"><span class="k">🚨 SOS button</span><span class="v" style="color:var(--txt3);font-weight:400">Emergency alert (planned)</span></div>
    </div>
  </div>

</div>

<!-- tabbar -->
<div class="tabbar" id="tabs">
  <button class="active" data-t="home"><span class="ico">🏠</span>Active</button>
  <button data-t="drivers">          <span class="ico">👤</span>Drivers</button>
  <button data-t="log">              <span class="ico">📋</span>Log</button>
  <button data-t="settings">         <span class="ico">⚙️</span>Settings</button>
</div>

<!-- overlay -->
<div class="ovl" id="ovl">
  <div class="sheet">
    <h3 id="ovlTitle"></h3><p id="ovlBody"></p>
    <button class="ok-btn" id="ovlOk"></button>
    <button class="cancel" onclick="closeOvl()">Cancel</button>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const ini=n=>(n||"?").split(" ").map(w=>w[0]).join("").slice(0,2).toUpperCase();
const today=()=>new Date().toISOString().slice(0,10);
const fmtTime=t=>t?String(t).slice(11,19):"—";
const fmtDate=t=>t?String(t).slice(0,10):"—";
function fmtDur(s){
  if(!s||s<1)return"—";
  s=Math.round(s);
  if(s<60)return s+"s";
  if(s<3600)return Math.floor(s/60)+"m "+String(s%60).padStart(2,"0")+"s";
  return Math.floor(s/3600)+"h "+Math.floor((s%3600)/60)+"m";
}

let S={live:{},active_session:null,drivers:[],sessions:[],events:[],stats:{},locked:false};
let lastJson="", pendingFn=null;
let openDriverIdx=null;
let prevMode=null, prevStatus=null;

/* ── web audio cyber synthesizer ── */
let audioCtx = null;
let sfxEnabled = true;

function initAudio(){
  if(!audioCtx){
    const AC = window.AudioContext || window.webkitAudioContext;
    if(AC) audioCtx = new AC();
  }
  if(audioCtx && audioCtx.state === 'suspended'){
    audioCtx.resume();
  }
}

function playTone(freq, type, duration, delay=0, vol=0.15){
  if(!sfxEnabled) return;
  try{
    initAudio();
    if(!audioCtx) return;
    setTimeout(()=>{
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
      gain.gain.setValueAtTime(vol, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + duration);
    }, delay);
  }catch(e){}
}

function sfxAccessGranted(){
  // Uplifting 4-tone crystalline arpeggio (C5 -> E5 -> G5 -> C6)
  playTone(523.25, "sine", 0.12, 0, 0.18);
  playTone(659.25, "sine", 0.14, 110, 0.18);
  playTone(783.99, "sine", 0.16, 220, 0.20);
  playTone(1046.50, "sine", 0.35, 330, 0.25);
}

function sfxAccessDenied(){
  // Urgent 3-pulse cyber warning alarm
  playTone(320, "sawtooth", 0.12, 0, 0.22);
  playTone(280, "sawtooth", 0.12, 140, 0.22);
  playTone(220, "sawtooth", 0.22, 280, 0.25);
}

function sfxLock(){
  // Deep electronic servo lock chirp
  playTone(220, "triangle", 0.08, 0, 0.15);
  playTone(130, "sine", 0.15, 60, 0.18);
}

function sfxClick(){
  // Crisp futuristic micro-click
  playTone(900, "sine", 0.02, 0, 0.08);
}

function showToast(text, type="green", icon="ℹ️"){
  const c = $("toastContainer");
  if(!c) return;
  const t = document.createElement("div");
  t.className = "toast " + type;
  t.innerHTML = `<span>${icon}</span> <span>${esc(text)}</span>`;
  c.appendChild(t);
  setTimeout(()=>{
    t.style.opacity = "0";
    t.style.transform = "translateY(-15px)";
    setTimeout(()=>t.remove(), 400);
  }, 3500);
}

function toggleAudio(){
  sfxEnabled = !sfxEnabled;
  $("sfxBtn").textContent = sfxEnabled ? "🔊" : "🔇";
  $("sfxBtn").classList.toggle("active", sfxEnabled);
  if(sfxEnabled) sfxClick();
}

function toggleKiosk(){
  if(!document.fullscreenElement){
    document.documentElement.requestFullscreen().catch(()=>{});
    $("kioskBtn").classList.add("active");
  } else {
    document.exitFullscreen().catch(()=>{});
    $("kioskBtn").classList.remove("active");
  }
}

function simulateGranted(){
  sfxAccessGranted();
  showToast("DEMO: Ignition Activated — Welcome, Driver!", "green", "⚡");
}

function simulateSpoof(){
  sfxAccessDenied();
  showToast("DEMO: Photo Spoof Detected — Real 3D Face Required!", "red", "🛡️");
}

/* ── live banner ── */
const LM={
  idle:         {ico:"🔒",main:"System Idle",         sub:"Seat empty — waiting for driver",       chip:"IDLE"},
  checking:     {ico:"📷",main:"Camera Active",        sub:"Scanning face…",                        chip:"SCANNING"},
  unknown:      {ico:"❓",main:"Unknown Face",          sub:"Not found in RTO database",             chip:"DENIED"},
  depth_fail:   {ico:"⚠️",main:"Spoof Detected",      sub:"Photo/video — real face required",      chip:"BLOCKED"},
  waiting_blink:{ico:"👁", main:"Blink Required",      sub:"Liveness check — please blink",         chip:"VERIFYING"},
  waiting_seat: {ico:"🪑",main:"Sit on Seat",          sub:"Face verified — awaiting seat sensor",  chip:"WAITING"},
  granted:      {ico:"✅",main:"Access Granted",        sub:"Ignition ON",                           chip:"ONLINE"},
  denied:       {ico:"🚫",main:"Access Denied",         sub:"Licence check failed",                  chip:"DENIED"},
};
function renderLive(){
  const L=S.live, st=L.status||"idle";
  const m=LM[st]||LM.idle;
  $("liveBanner").className="live-banner "+st;
  $("liveIco").textContent=m.ico;
  $("liveMain").textContent=m.main+(L.driver&&st!=="idle"?" — "+L.driver:"");
  $("liveSub").textContent=m.sub+(L.licence&&st!=="idle"?"  ·  "+L.licence:"");
  $("liveChip").className="live-chip "+st;
  $("liveChip").textContent=m.chip;
}

/* ── stat grid ── */
function fmtAvg(s){if(!s||s<1)return"—";s=Math.round(s);return s<60?s+"s":Math.floor(s/60)+"m";}
function renderStats(){
  const T=S.stats;
  $("statGrid").innerHTML=[
    {n:T.total_sessions||0,  l:"Sessions",   c:"c"},
    {n:T.total_drivers||0,   l:"Drivers",    c:"y"},
    {n:T.total_denied||0,    l:"Denied",     c:"r"},
    {n:T.total_blocked||0,   l:"Blocked",    c:"o"},
    {n:T.spoof_attempts||0,  l:"Spoofs",     c:"p"},
    {n:fmtAvg(T.avg_duration),l:"Avg Session",c:"g"},
  ].map(x=>'<div class="sbox '+x.c+'"><span class="snum '+x.c+'">'+esc(x.n)+'</span><span class="slbl">'+x.l+'</span></div>').join("");
}

/* ── active driver ── */
function licSt(d){
  if(!d)return["—",""];
  if(d.blocked)return["Blocked","bad"];
  if(d.status&&d.status!=="valid")return[d.status[0].toUpperCase()+d.status.slice(1),"bad"];
  if(d.expiry_date&&d.expiry_date<today())return["Expired","bad"];
  return["Valid","ok"];
}
function ir(k,v,c=""){return'<div class="irow"><span class="k">'+esc(k)+'</span><span class="v'+(c?" "+c:"")+'">'+esc(v||"—")+'</span></div>';}
function renderActive(){
  const A=S.active_session,el=$("activeCard");
  if(!A){
    el.innerHTML='<div class="empty"><span class="ei">🪑</span>No active session.<br>Waiting for a driver to authenticate.</div>';
    return;
  }
  const[bTxt,bCls]=licSt(A);
  const elapsed=Math.round(A.elapsed||0);
  const expCls=A.expiry_date&&A.expiry_date<today()?"bad":A.expiry_date&&A.expiry_date<new Date(Date.now()+90*864e5).toISOString().slice(0,10)?"warn":"ok";
  el.innerHTML='<div class="acard">'+
    '<div class="acard-top">'+
      '<div class="av">'+esc(ini(A.name))+'</div>'+
      '<div style="flex:1;min-width:0">'+
        '<div class="aname">'+esc(A.name)+'</div>'+
        '<div class="alic">'+esc(A.licence_no)+'</div>'+
      '</div>'+
      '<span class="badge '+bCls+'">'+esc(bTxt)+'</span>'+
    '</div>'+
    '<div class="sep"></div>'+
    '<div class="metrics">'+
      '<div class="met"><b>'+fmtDur(elapsed)+'</b><span>Duration</span></div>'+
      '<div class="met"><b>'+(A.visits||1)+'</b><span>Visits</span></div>'+
      '<div class="met"><b>'+(A.vehicle_class||"LMV")+'</b><span>Class</span></div>'+
    '</div>'+
    '<div class="sep"></div>'+
    '<div class="irows">'+
      ir("Blood Group",A.blood_group)+
      ir("Date of Birth",A.dob)+
      ir("Licence Issued",A.issue_date)+
      ir("Licence Expires",A.expiry_date,expCls)+
      ir("Status",A.status,A.status==="valid"?"ok":"bad")+
      ir("RTO Office",A.rto_office)+
      ir("Address",A.address)+
      ir("Session Started",fmtTime(A.start_time))+
      ir("Source",A.source)+
    '</div>'+
  '</div>';
}

/* ── recent feed ── */
function eventCfg(e){
  const k=e.kind;
  if(k==="session_start")  return{ico:"🟢",cls:"g",color:"green"};
  if(k==="session_end")    return{ico:"🔚",cls:"i",color:"grey"};
  if(k==="denied")         return{ico:"🚫",cls:"r",color:"red"};
  if(k==="depth_fail")     return{ico:"⚠️",cls:"y",color:"gold"};
  if(k==="owner_lock")     return{ico:"🔒",cls:"y",color:"gold"};
  if(k==="owner_unlock")   return{ico:"🔓",cls:"g",color:"green"};
  if(k==="owner_block")    return{ico:"🚫",cls:"r",color:"red"};
  if(k==="owner_unblock")  return{ico:"✅",cls:"g",color:"green"};
  if(k==="unknown")        return{ico:"❓",cls:"r",color:"red"};
  return{ico:"🔵",cls:"i",color:"grey"};
}
function feedRow(e){
  const{ico,cls}=eventCfg(e);
  return '<div class="frow">'+
    '<div class="fi '+cls+'">'+ico+'</div>'+
    '<div class="fb">'+
      '<div class="t">'+esc(e.message||e.kind.replace(/_/g," "))+'</div>'+
      '<div class="d">'+esc(fmtTime(e.time))+(e.name?'&nbsp;·&nbsp;'+esc(e.name):'')+'</div>'+
      (e.licence_no?'<div class="lic">'+esc(e.licence_no)+'</div>':'')+
    '</div></div>';
}
function renderRecent(){
  const E=S.events.slice(0,10),el=$("recentFeed");
  el.innerHTML=E.length?'<div class="feed">'+E.map(feedRow).join("")+'</div>':
    '<div class="empty"><span class="ei">📭</span>No recent activity yet.</div>';
}

/* ── driver profiles ── */
function renderDrivers(){
  const D=S.drivers;
  $("dCount").textContent=D.length?"("+D.length+")":"";
  if(!D.length){
    $("driverList").innerHTML='<div class="empty"><span class="ei">👤</span>No drivers yet.<br>They appear after first successful authentication.</div>';
    return;
  }
  $("driverList").innerHTML=D.map((d,i)=>{
    const[bTxt,bCls]=licSt(d);
    const isOpen=openDriverIdx===i;
    return '<div>'+
      '<div class="drow-card'+(d.blocked?" blocked":"")+'" data-i="'+i+'">'+
        '<div class="dav'+(d.blocked?" dim":"")+'">'+esc(ini(d.name))+'</div>'+
        '<div class="drow-info">'+
          '<div class="drow-name">'+esc(d.name)+'</div>'+
          '<div class="drow-lic">'+esc(d.licence_no)+'</div>'+
          '<div class="drow-meta">Visits: '+(d.visits||0)+' &nbsp;·&nbsp; Last: '+esc(fmtDate(d.last_seen))+'</div>'+
        '</div>'+
        '<span class="badge '+bCls+'">'+esc(bTxt)+'</span>'+
        '<span class="chevron">'+(isOpen?"▲":"▼")+'</span>'+
      '</div>'+
      '<div class="ddetail'+(isOpen?" open":"")+'" data-di="'+i+'">'+
        '<div class="ddetail-head">'+
          '<div class="dav'+(d.blocked?" dim":"")+'">'+esc(ini(d.name))+'</div>'+
          '<div style="flex:1;min-width:0">'+
            '<div style="font-size:14px;font-weight:700;color:var(--txt)">'+esc(d.name)+'</div>'+
            '<div style="font-size:10.5px;color:var(--txt2);margin-top:3px;">'+esc(d.licence_no)+'</div>'+
          '</div>'+
          '<span class="badge '+bCls+'">'+esc(bTxt)+'</span>'+
        '</div>'+
        '<div class="ddetail-rows">'+
          ddr("Vehicle Class",d.vehicle_class)+
          ddr("Blood Group",d.blood_group)+
          ddr("Date of Birth",d.dob)+
          ddr("Issued",d.issue_date)+
          ddr("Expires",d.expiry_date)+
          ddr("Status",d.status)+
          ddr("RTO Office",d.rto_office)+
          ddr("Address",d.address)+
          ddr("First Seen",d.first_seen)+
          ddr("Last Seen",d.last_seen)+
          ddr("Total Visits",d.visits)+
          ddr("Source",d.source)+
        '</div>'+
        '<div class="ddetail-foot">'+
          '<button class="dbtn '+(d.blocked?"unblock":"block")+'" '+
            'data-lic="'+esc(d.licence_no)+'" data-name="'+esc(d.name)+'" data-blocked="'+(d.blocked?"1":"0")+'">'+
            (d.blocked?"✓ Unblock":"✕ Block")+'</button>'+
        '</div>'+
      '</div>'+
    '</div>';
  }).join("");
}
function ddr(k,v){
  return '<div class="ddetail-row"><span class="k">'+esc(k)+'</span><span class="v">'+esc(v||"—")+'</span></div>';
}

/* ── log ── */
function renderLog(){
  const SS=S.sessions;
  $("sessLog").innerHTML=SS.length?SS.map(s=>{
    const active=!s.end_time;
    const cls=active?"active":s.end_reason==="denied"?"denied":"granted";
    return '<div class="scard '+cls+'">'+
      '<div class="scard-top">'+
        '<span class="scard-name">'+esc(s.name||"Unknown")+'</span>'+
        '<span class="scard-dur '+(active?"live":s.duration_sec?"ok":"na")+'">'+
          (active?"● LIVE":fmtDur(s.duration_sec))+'</span>'+
      '</div>'+
      '<div class="scard-lic">'+esc(s.licence_no)+'</div>'+
      '<div class="scard-meta">'+
        esc(fmtDate(s.start_time))+'&nbsp; '+esc(fmtTime(s.start_time))+
        (s.end_time?'&nbsp;→&nbsp;'+esc(fmtTime(s.end_time)):"")+
        '&nbsp;&nbsp;·&nbsp;&nbsp;'+esc(s.end_reason||"active")+
      '</div>'+
    '</div>';
  }).join(""):'<div class="empty"><span class="ei">📋</span>No sessions yet.</div>';

  const EV=S.events;
  $("eventLog").innerHTML=EV.length?EV.map(e=>{
    const{ico,color}=eventCfg(e);
    return '<div class="ecard '+color+'">'+
      '<div class="eico">'+ico+'</div>'+
      '<div class="ebody">'+
        '<div class="et">'+esc(e.message||e.kind.replace(/_/g," "))+'</div>'+
        '<div class="ed">'+esc(fmtTime(e.time))+(e.name?'&nbsp;·&nbsp;'+esc(e.name):'')+'</div>'+
        (e.licence_no?'<div class="elic">'+esc(e.licence_no)+'</div>':'')+
      '</div></div>';
  }).join(""):'<div class="empty"><span class="ei">📭</span>No events yet.</div>';
}

/* ── settings ── */
function renderSettings(){
  $("lockBtn").textContent=S.locked?"🔓  UNLOCK VEHICLE":"🔒  LOCK VEHICLE";
  $("lockBtn").className="bigbtn "+(S.locked?"safe":"danger");
}

/* ── security pipeline hud ── */
function setPipeNode(id, state, lineId, lineState){
  const el = $(id);
  if(el) el.className = "pipe-node " + state;
  if(lineId && $(lineId)) $(lineId).className = "pipe-line " + lineState;
}

function renderSecurityHUD(){
  const L = S.live || {};
  const stage = L.stage || "IDLE";
  const st = L.status || "idle";
  const depth = parseFloat(L.depth) || 0;
  const seat = parseInt(L.seat) || 0;

  $("hudStageBadge").textContent = stage;
  $("hudStageBadge").className = "hud-chip " + st;
  $("hudDepthBadge").textContent = depth > 0 ? "DEPTH: " + depth.toFixed(1) + "%" : "DEPTH: STANDBY";
  if(depth >= 3.2){
    $("hudDepthBadge").style.color = "var(--green2)";
    $("hudDepthBadge").style.borderColor = "rgba(72,187,120,0.4)";
  } else if(depth > 0){
    $("hudDepthBadge").style.color = "var(--red2)";
    $("hudDepthBadge").style.borderColor = "rgba(252,129,129,0.4)";
  } else {
    $("hudDepthBadge").style.color = "var(--cyan2)";
    $("hudDepthBadge").style.borderColor = "rgba(0,181,216,0.25)";
  }

  // 1. Seat
  setPipeNode("pipeSeat", seat ? "done" : "idle", "line1", seat ? "done" : "idle");

  // 2. Depth
  let depthState = "idle";
  if(stage === "DEPTH") depthState = "active";
  else if(stage === "DEPTH_FAIL") depthState = "fail";
  else if(["IDENTIFY", "BLINK", "GRANTED", "DENIED"].includes(stage)) depthState = "done";
  setPipeNode("pipeDepth", depthState, "line2", depthState === "done" ? "done" : "idle");

  // 3. Face ID
  let faceState = "idle";
  if(stage === "IDENTIFY") faceState = "active";
  else if(st === "unknown") faceState = "fail";
  else if(["BLINK", "GRANTED"].includes(stage) || (stage === "DENIED" && L.driver)) faceState = "done";
  setPipeNode("pipeFace", faceState, "line3", faceState === "done" ? "done" : "idle");

  // 4. Blink
  let blinkState = "idle";
  if(stage === "BLINK") blinkState = "active";
  else if(stage === "GRANTED") blinkState = "done";
  setPipeNode("pipeBlink", blinkState, "line4", blinkState === "done" ? "done" : "idle");

  // 5. Ignition Relay
  let ignState = "idle";
  if(stage === "GRANTED") ignState = "done";
  else if(stage === "DENIED" || stage === "DEPTH_FAIL" || st === "unknown") ignState = "fail";
  setPipeNode("pipeIgnition", ignState, null, null);
}

/* ── render all ── */
function render(){
  renderLive(); renderSecurityHUD(); renderStats(); renderActive(); renderRecent();
  renderDrivers(); renderLog(); renderSettings();
}

/* ── overlay ── */
function closeOvl(){$("ovl").classList.remove("show");pendingFn=null;}
function showOvl(title,body,btn,danger,fn){
  $("ovlTitle").textContent=title;$("ovlBody").textContent=body;
  $("ovlOk").textContent=btn;$("ovlOk").className="ok-btn"+(danger?" danger":"");
  pendingFn=fn;$("ovl").classList.add("show");
}
$("ovlOk").onclick=async()=>{if(pendingFn){await pendingFn();closeOvl();}};
$("ovl").onclick=e=>{if(e.target===$("ovl"))closeOvl();};

function askBlock(lic,name,isBlocked){
  if(isBlocked){
    showOvl("Unblock "+name,"Driver will be allowed to start the vehicle again.",
      "Unblock",false,()=>fetch("/api/unblock/"+encodeURIComponent(lic),{method:"POST"}));
  } else {
    showOvl("Block "+name,"Driver will be denied ignition immediately.",
      "Block Driver",true,()=>fetch("/api/block/"+encodeURIComponent(lic),{method:"POST"}));
  }
}
async function toggleLock(){
  if(S.locked){
    showOvl("Unlock Vehicle","All valid drivers can start the vehicle again.",
      "Unlock",false,()=>fetch("/api/unlock",{method:"POST"}));
  } else {
    showOvl("Lock Vehicle","No one can start the vehicle until you unlock it.",
      "Lock Vehicle",true,()=>fetch("/api/lock",{method:"POST"}));
  }
}

/* ── events ── */
$("driverList").addEventListener("click",e=>{
  const btn=e.target.closest("button[data-lic]");
  if(btn){askBlock(btn.dataset.lic,btn.dataset.name,btn.dataset.blocked==="1");return;}
  const card=e.target.closest(".drow-card");
  if(card){
    const i=parseInt(card.dataset.i);
    openDriverIdx=openDriverIdx===i?null:i;
    renderDrivers();
  }
});
$("tabs").addEventListener("click",e=>{
  const b=e.target.closest("button[data-t]");
  if(!b)return;
  sfxClick();
  document.querySelectorAll(".tabbar button").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".panel").forEach(p=>p.classList.remove("active"));
  b.classList.add("active");
  $("tab-"+b.dataset.t).classList.add("active");
  window.scrollTo(0,0);
});

/* ── refresh ── */
async function refresh(){
  try{
    const r=await fetch("/api/data");
    const d=await r.json();
    $("ping").className="ping on";
    $("pingTxt").textContent="Live";

    // Track live status transitions for reactive SFX & toasts
    if(d.live){
      const curMode = d.live.mode || "L";
      const curStatus = d.live.status || "idle";
      if(prevMode !== null){
        if(curMode === "U" && prevMode !== "U"){
          sfxAccessGranted();
          showToast("IGNITION ACTIVATED — Welcome, " + (d.live.driver || "Driver") + "!", "green", "⚡");
        } else if(curStatus === "depth_fail" && prevStatus !== "depth_fail"){
          sfxAccessDenied();
          showToast("PHOTO SPOOF PREVENTED — 3D Face Required (" + (d.live.depth || 0) + "%)", "red", "🛡️");
        } else if(curStatus === "unknown" && prevStatus !== "unknown"){
          sfxAccessDenied();
          showToast("ACCESS DENIED — Driver not in RTO database", "red", "🚫");
        } else if(curMode === "L" && prevMode === "U"){
          sfxLock();
          showToast("DRIVER UNSEATED — Ignition Locked", "gold", "🔒");
        }
      }
      prevMode = curMode;
      prevStatus = curStatus;
    }

    const j=JSON.stringify(d);
    if(j===lastJson)return;
    lastJson=j;S=d;render();
  }catch(e){
    $("ping").className="ping off";
    $("pingTxt").textContent="Offline";
  }
}
refresh();
setInterval(refresh,2000);
</script>
</body>
</html>"""

@app.route("/")
def home():
    return PAGE

if __name__ == "__main__":
    app.run(port=5000)