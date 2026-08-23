import os
import json
import random
import string
import requests
from datetime import datetime, timedelta

import psycopg2
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder='web/assets')

def get_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(url)

def init_db():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS keys (
                    id SERIAL PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    used INT DEFAULT 0,
                    hwid TEXT DEFAULT '',
                    days INT DEFAULT 30,
                    expires_at TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            # migrations
            cur.execute("ALTER TABLE keys ADD COLUMN IF NOT EXISTS frozen INT DEFAULT 0")
            cur.execute("ALTER TABLE keys ADD COLUMN IF NOT EXISTS hwid_limit INT DEFAULT 1")
            cur.execute("ALTER TABLE keys ADD COLUMN IF NOT EXISTS hwids TEXT DEFAULT ''")
            cur.execute("ALTER TABLE keys ADD COLUMN IF NOT EXISTS note TEXT DEFAULT ''")
            cur.execute("INSERT INTO settings (key, value) VALUES ('prefix', 'UC') ON CONFLICT (key) DO NOTHING")
            cur.execute("INSERT INTO settings (key, value) VALUES ('tg_token', '') ON CONFLICT (key) DO NOTHING")
            cur.execute("INSERT INTO settings (key, value) VALUES ('tg_chat', '') ON CONFLICT (key) DO NOTHING")
        conn.commit()
    finally:
        conn.close()

def get_setting(k, default=""):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key=%s", (k,))
            r = cur.fetchone()
        conn.close()
        if r: return r[0]
    except: pass
    return default

def set_setting(k,v):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO settings (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=%s", (k,v,v))
    conn.commit(); conn.close()

def get_prefix():
    return (get_setting('prefix','UC') or 'UC').strip().upper()

def rand_key():
    prefix = get_prefix()
    return f"{prefix}-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))

def send_tg(text):
    token = get_setting('tg_token','')
    chat = get_setting('tg_chat','')
    if not token or not chat: return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text, "parse_mode":"HTML"}, timeout=5)
    except Exception as e:
        print(f"[tg] {e}")

# init at import for gunicorn
try:
    init_db()
except Exception as e:
    print(f"[init_db] warning: {e}")

@app.route("/")
def index():
    return send_from_directory("web", "index.html")

@app.route("/assets/<path:path>")
def assets(path):
    return send_from_directory("web/assets", path)

@app.route("/api/check", methods=["POST"])
def check_key():
    data = request.get_json(silent=True)
    if not data or not data.get("key"):
        return jsonify({"valid": False, "error": "Invalid request"}), 200
    key = data["key"].strip().upper()
    hwid = (data.get("hwid") or "").strip()
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id, used, hwid, hwid_limit, hwids, frozen, days, expires_at FROM keys WHERE key = %s", (key,))
            row = cur.fetchone()
            if not row:
                return jsonify({"valid": False, "error": "Invalid key"}), 200
            row_id, used, bound_hwid, hwid_limit, hwids, frozen, days, expires_at = row
            if frozen == 1:
                return jsonify({"valid": False, "error": "Key is frozen"}), 200
            exp_ts = 0
            if used == 1 and expires_at and expires_at != "Бессрочно":
                try:
                    exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
                    exp_ts = int(exp_dt.timestamp())
                    if datetime.utcnow() > exp_dt:
                        return jsonify({"valid": False, "error": "License key has expired"}), 200
                except: pass
            # HWID limit check
            hwid_limit = hwid_limit or 1
            # parse hwid list
            hwid_list = []
            if hwids:
                try: hwid_list = json.loads(hwids)
                except: hwid_list = [x.strip() for x in hwids.split(',') if x.strip()]
            # normalize: include bound_hwid for legacy
            if bound_hwid and bound_hwid not in hwid_list:
                hwid_list.append(bound_hwid)
            if used == 1 and hwid not in hwid_list:
                if len(hwid_list) >= hwid_limit:
                    return jsonify({"valid": False, "error": "HWID limit reached"}), 200
            # First activation or new HWID
            if hwid not in hwid_list:
                hwid_list.append(hwid)
                new_hwid = hwid # keep primary for compat
                new_hwids = json.dumps(hwid_list)
                new_used = 1
                if used == 0:
                    if days and days > 0:
                        exp_dt = datetime.utcnow() + timedelta(days=days)
                        new_exp = exp_dt.strftime("%Y-%m-%d %H:%M:%S")
                        exp_ts = int(exp_dt.timestamp())
                    else:
                        new_exp = "Бессрочно"
                    cur.execute("UPDATE keys SET used=%s, hwid=%s, hwids=%s, expires_at=%s WHERE id=%s", (new_hwid, new_hwids, new_exp, row_id))
                else:
                    cur.execute("UPDATE keys SET hwid=%s, hwids=%s WHERE id=%s", (new_hwid, new_hwids, row_id))
                conn.commit()
                send_tg(f"✅ Активация <code>{key}</code>\nHWID: <code>{hwid}</code>\nЛимит: {len(hwid_list)}/{hwid_limit}")
            else:
                if exp_ts == 0 and expires_at and expires_at != "Бессрочно":
                    try:
                        exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
                        exp_ts = int(exp_dt.timestamp())
                    except: pass
            return jsonify({"valid": True, "expires_at": exp_ts}), 200
    except Exception as e:
        print(f"[check_key] {e}")
        return jsonify({"valid": False, "error": "Server error"}), 200
    finally:
        if conn:
            try: conn.close()
            except: pass

@app.route("/admin/check-auth")
def admin_check_auth():
    return jsonify({"ok": True}), 200

@app.route("/admin/keys")
def admin_keys():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, key, used, hwid, hwid_limit, hwids, frozen, days, expires_at, created_at, note FROM keys ORDER BY id DESC")
        rows = cur.fetchall()
    conn.close()
    keys=[]
    for r in rows:
        keys.append({"id":r[0],"key":r[1],"used":r[2],"hwid":r[3],"hwid_limit":r[5] if len(r)>5 else 1,"hwid_limit_val":r[5] if r[5] else 1,"hwid_limit":r[5],"hwids":r[5+1] if len(r)>6 else "", "frozen":r[6],"days":r[7] if len(r)>7 else 30,"expires_at":r[8] if len(r)>8 else "","created_at":str(r[9] if len(r)>9 else ""),"note":r[10] if len(r)>10 else ""})
        # fix mapping due to added cols: actual order id,key,used,hwid,hwid_limit,hwids,frozen,days,expires_at,created_at,note
        # above was misaligned, re-map correctly
    # correct mapping
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, key, used, hwid, hwid_limit, hwids, frozen, days, expires_at, created_at, note FROM keys ORDER BY id DESC")
        rows = cur.fetchall()
    conn.close()
    out=[]
    for r in rows:
        out.append({"id":r[0],"key":r[1],"used":r[2],"hwid":r[3],"hwid_limit":r[4],"hwids":r[5],"frozen":r[6],"days":r[7],"expires_at":r[8],"created_at":str(r[9]),"note":r[10]})
    return jsonify(out)

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    days_raw = request.form.get("days") or (request.get_json(silent=True) or {}).get("days") or "30"
    count_raw = request.form.get("count") or (request.get_json(silent=True) or {}).get("count") or "1"
    hwid_limit_raw = request.form.get("hwid_limit") or (request.get_json(silent=True) or {}).get("hwid_limit") or "1"
    try: days=int(str(days_raw))
    except: days=30
    try: count=int(str(count_raw))
    except: count=1
    try: hwid_limit=int(str(hwid_limit_raw))
    except: hwid_limit=1
    count = max(1, min(count, 100))
    hwid_limit = max(1, min(hwid_limit, 10))
    conn=get_db()
    created=[]
    with conn.cursor() as cur:
        for _ in range(count):
            for _ in range(20):
                cand=rand_key()
                cur.execute("SELECT 1 FROM keys WHERE key=%s",(cand,))
                if not cur.fetchone():
                    break
            else:
                cand=rand_key()
            cur.execute("INSERT INTO keys (key, used, hwid, hwid_limit, days) VALUES (%s,0,'',%s,%s)",(cand,hwid_limit,days))
            created.append(cand)
    conn.commit(); conn.close()
    send_tg(f"🆕 Создано ключей: {len(created)} x {days}дн, лимит HWID {hwid_limit}\n" + ", ".join(created[:10]))
    if len(created)==1:
        return jsonify({"key": created[0]})
    return jsonify({"keys": created, "count": len(created)})

@app.route("/admin/delete/<int:kid>", methods=["POST","DELETE"])
def admin_delete(kid):
    conn=get_db()
    with conn.cursor() as cur: cur.execute("DELETE FROM keys WHERE id=%s",(kid,))
    conn.commit(); conn.close()
    return jsonify({"ok":True}),200

@app.route("/admin/reset/<int:kid>", methods=["POST"])
def admin_reset(kid):
    conn=get_db()
    with conn.cursor() as cur: cur.execute("UPDATE keys SET used=0, hwid='', hwids='', expires_at='' WHERE id=%s",(kid,))
    conn.commit(); conn.close()
    return jsonify({"ok":True}),200

@app.route("/admin/freeze/<int:kid>", methods=["POST"])
def admin_freeze(kid):
    conn=get_db()
    with conn.cursor() as cur: cur.execute("UPDATE keys SET frozen=1 WHERE id=%s",(kid,))
    conn.commit(); conn.close()
    send_tg(f"❄️ Заморожен ключ #{kid}")
    return jsonify({"ok":True}),200

@app.route("/admin/unfreeze/<int:kid>", methods=["POST"])
def admin_unfreeze(kid):
    conn=get_db()
    with conn.cursor() as cur: cur.execute("UPDATE keys SET frozen=0 WHERE id=%s",(kid,))
    conn.commit(); conn.close()
    send_tg(f"🔥 Разморожен ключ #{kid}")
    return jsonify({"ok":True}),200

@app.route("/admin/add-time/<int:kid>", methods=["POST"])
def admin_add_time(kid):
    data=request.get_json(silent=True) or request.form
    try: add_days=int(data.get("days",0))
    except: return jsonify({"error":"Invalid days"}),400
    if add_days<=0 or add_days>3650: return jsonify({"error":"days must be 1..3650"}),400
    conn=get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT used, expires_at, days FROM keys WHERE id=%s",(kid,))
        row=cur.fetchone()
        if not row: conn.close(); return jsonify({"error":"Not found"}),404
        used,expires_at,days=row
        if used==0 or not expires_at or expires_at=="Бессрочно":
            if expires_at=="Бессрочно":
                new_exp=(datetime.utcnow()+timedelta(days=add_days)).strftime("%Y-%m-%d %H:%M:%S")
                cur.execute("UPDATE keys SET expires_at=%s, days=%s WHERE id=%s",(new_exp,add_days,kid))
            else:
                cur.execute("UPDATE keys SET days = days + %s WHERE id=%s",(add_days,kid))
        else:
            try: exp_dt=datetime.strptime(expires_at,"%Y-%m-%d %H:%M:%S")
            except: exp_dt=datetime.utcnow()
            base=max(exp_dt,datetime.utcnow())
            new_exp=(base+timedelta(days=add_days)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("UPDATE keys SET expires_at=%s WHERE id=%s",(new_exp,kid))
    conn.commit(); conn.close()
    send_tg(f"⏰ Продлён ключ #{kid} на +{add_days}дн")
    return jsonify({"ok":True}),200

@app.route("/admin/bulk", methods=["POST"])
def admin_bulk():
    data=request.get_json(silent=True) or {}
    ids=data.get("ids") or []
    action=data.get("action")
    if not ids or not isinstance(ids,list): return jsonify({"error":"ids required"}),400
    ids=[int(x) for x in ids if str(x).isdigit()]
    if not ids: return jsonify({"error":"no ids"}),400
    conn=get_db()
    try:
        with conn.cursor() as cur:
            if action=="delete":
                cur.execute("DELETE FROM keys WHERE id = ANY(%s)", (ids,))
            elif action=="freeze":
                cur.execute("UPDATE keys SET frozen=1 WHERE id = ANY(%s)", (ids,))
            elif action=="unfreeze":
                cur.execute("UPDATE keys SET frozen=0 WHERE id = ANY(%s)", (ids,))
            elif action=="add_time":
                try: add_days=int(data.get("days",0))
                except: return jsonify({"error":"Invalid days"}),400
                if add_days<=0: return jsonify({"error":"days required"}),400
                for kid in ids:
                    cur.execute("SELECT used, expires_at FROM keys WHERE id=%s",(kid,))
                    r=cur.fetchone()
                    if not r: continue
                    used,expires_at=r
                    if used==0 or not expires_at or expires_at=="Бессрочно":
                        if expires_at=="Бессрочно":
                            new_exp=(datetime.utcnow()+timedelta(days=add_days)).strftime("%Y-%m-%d %H:%M:%S")
                            cur.execute("UPDATE keys SET expires_at=%s WHERE id=%s",(new_exp,kid))
                        else:
                            cur.execute("UPDATE keys SET days = days + %s WHERE id=%s",(add_days,kid))
                    else:
                        try: exp_dt=datetime.strptime(expires_at,"%Y-%m-%d %H:%M:%S")
                        except: exp_dt=datetime.utcnow()
                        base=max(exp_dt,datetime.utcnow())
                        new_exp=(base+timedelta(days=add_days)).strftime("%Y-%m-%d %H:%M:%S")
                        cur.execute("UPDATE keys SET expires_at=%s WHERE id=%s",(new_exp,kid))
            elif action=="reset":
                cur.execute("UPDATE keys SET used=0, hwid='', hwids='', expires_at='' WHERE id = ANY(%s)", (ids,))
            else:
                return jsonify({"error":"unknown action"}),400
        conn.commit()
        send_tg(f"📦 Bulk {action}: {len(ids)} ключей")
        return jsonify({"ok":True, "count": len(ids)}),200
    finally:
        conn.close()

@app.route("/admin/settings", methods=["GET","POST"])
def admin_settings():
    if request.method=="GET":
        return jsonify({"prefix":get_setting('prefix','UC'), "tg_token": get_setting('tg_token',''), "tg_chat": get_setting('tg_chat','')})
    data=request.get_json(silent=True) or request.form
    # prefix
    if "prefix" in data:
        prefix=(data.get("prefix") or "").strip().upper()
        if not prefix or len(prefix)>10 or not prefix.replace("-","").replace("_","").isalnum():
            return jsonify({"error":"Invalid prefix"}),400
        prefix=prefix.replace("-","").replace("_","")
        set_setting('prefix', prefix)
    if "tg_token" in data:
        set_setting('tg_token', (data.get("tg_token") or "").strip())
    if "tg_chat" in data:
        set_setting('tg_chat', (data.get("tg_chat") or "").strip())
    return jsonify({"prefix": get_setting('prefix','UC'), "tg_token": get_setting('tg_token',''), "tg_chat": get_setting('tg_chat','')})

@app.route("/admin/telegram/test", methods=["POST"])
def admin_tg_test():
    send_tg("✅ Тест уведомлений: ключ-система работает!")
    return jsonify({"ok":True}),200

@app.route("/admin/logs")
def admin_logs():
    return jsonify([])

if __name__=="__main__":
    port=int(os.getenv("PORT","10000"))
    app.run(host="0.0.0.0",port=port)
