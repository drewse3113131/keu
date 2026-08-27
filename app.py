# ЗАМЕНИТЬ ФУНКЦИИ check_key И check_key_simple В СВОЕМ app.py НА ЭТОТ КОД:

# === ОСНОВНОЙ ЭНДПОИНТ ДЛЯ КЛИЕНТА (POST JSON) ===
@app.route("/api/check", methods=["POST"])
def check_key():
    # Принудительно парсим JSON
    if request.is_json:
        data = request.get_json(force=True, silent=True) or {}
    else:
        data = request.form.to_dict()
    
    key = (data.get("key") or "").strip().upper()
    hwid = (data.get("hwid") or "").strip()
    
    if not key:
        return jsonify({"valid": False, "error": "Invalid request", "expires_at": 0}), 200
    
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT id, used, hwid, hwid_limit, hwids, frozen, days, expires_at FROM keys WHERE key=%s", (key,))
                row = cur.fetchone()
                if not row: return jsonify({"valid": False, "error": "Invalid key", "expires_at": 0}), 200
                row_id, used, bound_hwid, hwid_limit, hwids, frozen, days, expires_at = row
            except psycopg2.errors.UndefinedColumn:
                conn.rollback()
                cur.execute("SELECT id, used, hwid, days, expires_at FROM keys WHERE key=%s", (key,))
                row = cur.fetchone()
                if not row: return jsonify({"valid": False, "error": "Invalid key", "expires_at": 0}), 200
                row_id, used, bound_hwid, days, expires_at = row
                hwid_limit, hwids, frozen = 1, "", 0
            
            if frozen == 1:
                return jsonify({"valid": False, "error": "Key is frozen", "expires_at": 0}), 200
            
            exp_ts = 0
            if used == 1 and expires_at and expires_at != "Бессрочно":
                try:
                    exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
                    exp_ts = int(exp_dt.timestamp())
                    if datetime.utcnow() > exp_dt:
                        return jsonify({"valid": False, "error": "License key has expired", "expires_at": exp_ts}), 200
                except: pass
            
            # HWID check
            hwid_limit = hwid_limit or 1
            hwid_list = []
            if hwids:
                try: hwid_list = json.loads(hwids)
                except: hwid_list = [x.strip() for x in hwids.split(',') if x.strip()]
            if bound_hwid and bound_hwid not in hwid_list: hwid_list.append(bound_hwid)
            
            if used == 1 and hwid and hwid not in hwid_list and len(hwid_list) >= hwid_limit:
                return jsonify({"valid": False, "error": "HWID limit reached", "expires_at": exp_ts}), 200
            
            if hwid and hwid not in hwid_list:
                hwid_list.append(hwid)
                new_hwids = json.dumps(hwid_list)
                if used == 0:
                    if days and days > 0:
                        exp_dt = datetime.utcnow() + timedelta(days=days)
                        new_exp = exp_dt.strftime("%Y-%m-%d %H:%M:%S")
                        exp_ts = int(exp_dt.timestamp())
                    else:
                        new_exp = "Бессрочно"
                    cur.execute("UPDATE keys SET used=1, hwid=%s, hwids=%s, expires_at=%s WHERE id=%s", (hwid, new_hwids, new_exp, row_id))
                else:
                    cur.execute("UPDATE keys SET hwid=%s, hwids=%s WHERE id=%s", (hwid, new_hwids, row_id))
                conn.commit()
                try: send_tg(f"✅ Активация <code>{key}</code>\nHWID: <code>{hwid}</code> {len(hwid_list)}/{hwid_limit}")
                except: pass
            
            return jsonify({"valid": True, "expires_at": exp_ts}), 200
    except Exception as e:
        print(f"[check_key] {e}")
        import traceback; traceback.print_exc()
        return jsonify({"valid": False, "error": "Server error", "expires_at": 0}), 200
    finally:
        if conn:
            try: conn.close()
            except: pass

# === ПРОСТОЙ ЭНДПОИНТ (GET/POST -> JSON с expires_at) ===
@app.route("/check_key", methods=["GET", "POST"])
def check_key_simple():
    if request.method == "GET":
        key = request.args.get("key", "").strip().upper()
        hwid = request.args.get("hwid", "").strip()
    else:
        if request.is_json:
            data = request.get_json(force=True, silent=True) or {}
        else:
            data = request.form.to_dict()
        key = (data.get("key") or "").strip().upper()
        hwid = (data.get("hwid") or "").strip()
    
    if not key:
        return jsonify({"valid": False, "error": "Invalid request", "expires_at": 0}), 400
    
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT id, used, hwid, hwid_limit, hwids, frozen, days, expires_at FROM keys WHERE key=%s", (key,))
                row = cur.fetchone()
                if not row: return jsonify({"valid": False, "error": "Invalid key", "expires_at": 0}), 200
                row_id, used, bound_hwid, hwid_limit, hwids, frozen, days, expires_at = row
            except psycopg2.errors.UndefinedColumn:
                conn.rollback()
                cur.execute("SELECT id, used, hwid, days, expires_at FROM keys WHERE key=%s", (key,))
                row = cur.fetchone()
                if not row: return jsonify({"valid": False, "error": "Invalid key", "expires_at": 0}), 200
                row_id, used, bound_hwid, days, expires_at = row
                hwid_limit, hwids, frozen = 1, "", 0
            
            if frozen == 1:
                return jsonify({"valid": False, "error": "Key is frozen", "expires_at": 0}), 200
            
            exp_ts = 0
            if used == 1 and expires_at and expires_at != "Бессрочно":
                try:
                    exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
                    exp_ts = int(exp_dt.timestamp())
                    if datetime.utcnow() > exp_dt:
                        return jsonify({"valid": False, "error": "License key has expired", "expires_at": exp_ts}), 200
                except: pass
            
            hwid_limit = hwid_limit or 1
            hwid_list = []
            if hwids:
                try: hwid_list = json.loads(hwids)
                except: hwid_list = [x.strip() for x in hwids.split(',') if x.strip()]
            if bound_hwid and bound_hwid not in hwid_list: hwid_list.append(bound_hwid)
            
            if used == 1 and hwid and hwid not in hwid_list and len(hwid_list) >= hwid_limit:
                return jsonify({"valid": False, "error": "HWID limit reached", "expires_at": exp_ts}), 200
            
            if hwid and hwid not in hwid_list:
                hwid_list.append(hwid)
                new_hwids = json.dumps(hwid_list)
                if used == 0:
                    if days and days > 0:
                        exp_dt = datetime.utcnow() + timedelta(days=days)
                        new_exp = exp_dt.strftime("%Y-%m-%d %H:%M:%S")
                        exp_ts = int(exp_dt.timestamp())
                    else:
                        new_exp = "Бессрочно"
                    cur.execute("UPDATE keys SET used=1, hwid=%s, hwids=%s, expires_at=%s WHERE id=%s", (hwid, new_hwids, new_exp, row_id))
                else:
                    cur.execute("UPDATE keys SET hwid=%s, hwids=%s WHERE id=%s", (hwid, new_hwids, row_id))
                conn.commit()
                try: send_tg(f"✅ Активация <code>{key}</code>\nHWID: <code>{hwid}</code> {len(hwid_list)}/{hwid_limit}")
                except: pass
            
            return jsonify({"valid": True, "expires_at": exp_ts}), 200
    except Exception as e:
        print(f"[check_key_simple] {e}")
        return jsonify({"valid": False, "error": "Server error", "expires_at": 0}), 200
    finally:
        if conn:
            try: conn.close()
            except: pass
