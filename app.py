
import os
import json
import random
import string
import time
from datetime import datetime, timedelta

import psycopg2
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder='web/assets')

# --- DB ---
def get_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(url)

def init_db():
    conn = get_db()
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
        conn.commit()

# ВЫЗЫВАЕМ НА УРОВНЕ МОДУЛЯ (важно для gunicorn)
init_db()

# --- Helpers ---
def rand_key():
    return "UC-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))

# --- App ---
app = Flask(__name__, static_folder='web/assets')

# --- UI Routes ---


@app.route("/assets/<path:path>")
def assets(path):
    return send_from_directory("web/assets", path)

# --- API для DLL ---
@app.route("/api/check", methods=["POST"])
def check_key():
    data = request.get_json()
    if not data or not data.get("key"):
        return jsonify({"valid": False, "error": "Invalid request"})

    key = data["key"]
    hwid = data.get("hwid", "")

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, used, hwid, days, expires_at FROM keys WHERE key = %s", (key,))
        row = cur.fetchone()
        if not row:
            return jsonify({"valid": False, "error": "Invalid key"})

        row_id, used, bound_hwid, days, expires_at = row

        exp_ts = 0
        if used == 1 and expires_at and expires_at != "Бессрочно":
            try:
                exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
                if datetime.utcnow() > exp_dt:
                    return jsonify({"valid": False, "error": "License key has expired"})
                exp_ts = int(exp_dt.timestamp())
            except:
                pass

        if used == 1 and bound_hwid != hwid:
            return jsonify({"valid": False, "error": "Key is bound to another PC"})

        if used == 0:
            new_exp = ""
            exp_ts = 0
            if days > 0:
                exp_dt = datetime.utcnow() + timedelta(days=days)
                new_exp = exp_dt.strftime("%Y-%m-%d %H:%M:%S")
                exp_ts = int(exp_dt.timestamp())
            else:
                new_exp = "Бессрочно"

            with get_db().cursor() as cur:
                cur.execute(
                    "UPDATE keys SET used=1, hwid=%s, expires_at=%s WHERE id=%s",
                    (hwid, new_exp, row[0])
                )
                get_db().commit()

        return jsonify({"valid": True, "expires_at": exp_ts})

# --- Admin API ---
@app.route("/admin/keys")
def admin_keys():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, key, used, hwid, days, expires_at, created_at FROM keys ORDER BY id DESC")
        rows = cur.fetchall()
    keys = [{
        "id": r[0], "key": r[1], "used": r[2], "hwid": r[3],
        "days": r[4], "expires_at": r[4], "created_at": str(r[6])
    } for r in rows]
    return jsonify(keys)

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    days = int(request.form.get("days", "30"))
    conn = get_db()
    with conn.cursor() as cur:
        while True:
            k = "UC-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
            cur.execute("SELECT 1 FROM keys WHERE key=%s", (k,))
            if not cur.fetchone():
                break
        cur.execute("INSERT INTO keys (key, used, hwid, days) VALUES (%s, 0, '', %s)", (k, days))
        conn.commit()
    return jsonify({"key": k})

@app.route("/admin/delete/<int:kid>", methods=["POST"])
def admin_delete(kid):
    with get_db().cursor() as cur:
        cur.execute("DELETE FROM keys WHERE id=%s", (kid,))
        get_db().commit()
    return "", 200

@app.route("/admin/reset/<int:kid>", methods=["POST"])
def admin_reset(kid):
    with get_db().cursor() as cur:
        cur.execute("UPDATE keys SET used=0, hwid='', expires_at='' WHERE id=%s", (kid,))
        get_db().commit()
    return "", 200

# --- UI Routes ---
@app.route("/")
def index():
    return send_from_directory("web", "index.html")

@app.route("/assets/<path:path>")
def assets(path):
    return send_from_directory("web/assets", path)

# --- Run ---
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
host="0.0.0.0", port=port)