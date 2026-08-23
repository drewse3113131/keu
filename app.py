import os
import random
import string
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
            cur.execute("INSERT INTO settings (key, value) VALUES ('prefix', 'UC') ON CONFLICT (key) DO NOTHING")
        conn.commit()
    finally:
        conn.close()

def get_prefix():
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key='prefix'")
            row = cur.fetchone()
        if row and row[0]:
            return row[0].strip().upper()
    except Exception:
        pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    return "UC"

def rand_key():
    prefix = get_prefix()
    return f"{prefix}-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))

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
            cur.execute("SELECT id, used, hwid, days, expires_at FROM keys WHERE key = %s", (key,))
            row = cur.fetchone()
            if not row:
                return jsonify({"valid": False, "error": "Invalid key"}), 200

            row_id, used, bound_hwid, days, expires_at = row

            # Check expiration if already activated
            exp_ts = 0
            if used == 1 and expires_at and expires_at != "Бессрочно":
                try:
                    exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
                    exp_ts = int(exp_dt.timestamp())
                    if datetime.utcnow() > exp_dt:
                        return jsonify({"valid": False, "error": "License key has expired"}), 200
                except Exception:
                    pass

            # HWID binding check: only if already bound to non-empty HWID
            if used == 1 and bound_hwid and bound_hwid != hwid:
                return jsonify({"valid": False, "error": "Key is bound to another PC"}), 200

            # First activation: bind HWID and set expiration
            if used == 0:
                new_exp = ""
                exp_ts = 0
                if days and days > 0:
                    exp_dt = datetime.utcnow() + timedelta(days=days)
                    new_exp = exp_dt.strftime("%Y-%m-%d %H:%M:%S")
                    exp_ts = int(exp_dt.timestamp())
                else:
                    new_exp = "Бессрочно"
                # Use same connection to update and commit — FIX: was get_db().commit() on new connection
                with conn.cursor() as cur2:
                    cur2.execute("UPDATE keys SET used=1, hwid=%s, expires_at=%s WHERE id=%s", (hwid, new_exp, row_id))
                conn.commit()
            else:
                # Already activated by this HWID — keep existing exp_ts, or recompute if not yet set
                if exp_ts == 0 and expires_at and expires_at != "Бессрочно":
                    try:
                        exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
                        exp_ts = int(exp_dt.timestamp())
                    except Exception:
                        pass

            return jsonify({"valid": True, "expires_at": exp_ts}), 200
    except Exception as e:
        print(f"[check_key] error: {e}")
        return jsonify({"valid": False, "error": "Server error"}), 200
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@app.route("/admin/check-auth")
def admin_check_auth():
    return jsonify({"ok": True}), 200

@app.route("/admin/keys")
def admin_keys():
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id, key, used, hwid, days, expires_at, created_at FROM keys ORDER BY id DESC")
            rows = cur.fetchall()
        keys = []
        for r in rows:
            keys.append({
                "id": r[0],
                "key": r[1],
                "used": r[2],
                "hwid": r[3],
                "days": r[4],
                "expires_at": r[5],
                "created_at": str(r[6])
            })
        return jsonify(keys)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    days_raw = request.form.get("days")
    if days_raw is None:
        j = request.get_json(silent=True) or {}
        days_raw = j.get("days", "30")
    try:
        days = int(str(days_raw))
    except Exception:
        days = 30

    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            k = None
            for _ in range(20):
                cand = rand_key()
                cur.execute("SELECT 1 FROM keys WHERE key=%s", (cand,))
                if not cur.fetchone():
                    k = cand
                    break
            if not k:
                k = rand_key()
            cur.execute("INSERT INTO keys (key, used, hwid, days) VALUES (%s, 0, '', %s)", (k, days))
        conn.commit()
        return jsonify({"key": k})
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@app.route("/admin/delete/<int:kid>", methods=["POST", "DELETE"])
def admin_delete(kid):
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM keys WHERE id=%s", (kid,))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@app.route("/admin/reset/<int:kid>", methods=["POST"])
def admin_reset(kid):
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("UPDATE keys SET used=0, hwid='', expires_at='' WHERE id=%s", (kid,))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@app.route("/admin/settings", methods=["GET", "POST"])
def admin_settings():
    if request.method == "GET":
        return jsonify({"prefix": get_prefix()})
    data = request.get_json(silent=True) or request.form
    prefix = (data.get("prefix") or "").strip().upper()
    if not prefix or len(prefix) > 10 or not prefix.replace("-","").replace("_","").isalnum():
        return jsonify({"error": "Invalid prefix"}), 400
    prefix = prefix.replace("-","").replace("_","")
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO settings (key, value) VALUES ('prefix', %s) ON CONFLICT (key) DO UPDATE SET value=%s", (prefix, prefix))
        conn.commit()
        return jsonify({"prefix": prefix})
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@app.route("/admin/logs")
def admin_logs():
    return jsonify([])

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
