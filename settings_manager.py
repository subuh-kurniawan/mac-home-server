#!/usr/bin/env python3
"""
Settings Manager Terpusat untuk Mac Remote Command Center:
  1. Telegram Bot CRUD (bot_config.json: token, allowed_chat_ids + labels, test message, restart)
  2. Cast Devices & TV Remote CRUD (cast_devices.json, tv_remote_paired.json)
  3. Preferensi Audio & Quiet Hours (SQLite app_settings)
  4. Google Assistant SDK Settings
  5. SSL Info, Backup ZIP & Kontrol Service Mac (launchd)
"""

import json
import os
import sqlite3
import ssl
import subprocess
import threading
import time
import urllib.request
import uuid
import zipfile

import cast_manager
import db_manager
import google_assistant_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_CONFIG_FILE = os.path.join(BASE_DIR, "bot_config.json")
CAST_DEVICES_FILE = os.path.join(BASE_DIR, "cast_devices.json")
TV_PAIRED_FILE = os.path.join(BASE_DIR, "tv_remote_paired.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

DEFAULT_PREFS = {
    "qa_default_volume": 70,
    "qa_default_restore_volume": 50,
    "qa_default_voice": "wanita1",
    "qa_default_chime": True,
    "quiet_hours_enabled": False,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "06:00",
    "quiet_hours_max_vol": 40,
    "ga_default_lang": "",
    "ga_auto_play_gh": True,
    "tg_default_target": "gh",
}


# ================= 1. TELEGRAM BOT CRUD =================


def _read_bot_config():
    if os.path.isfile(BOT_CONFIG_FILE):
        try:
            with open(BOT_CONFIG_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {"token": "", "allowed_chat_ids": [], "chat_id_labels": {}}


def _write_bot_config(cfg):
    with open(BOT_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def _is_process_running(pattern):
    try:
        res = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=2)
        return res.returncode == 0 and bool(res.stdout.strip())
    except Exception:
        return False


def get_telegram_settings():
    cfg = _read_bot_config()
    tok = str(cfg.get("token") or "")
    masked = (tok[:8] + "..." + tok[-5:]) if len(tok) > 16 else ("(terisi)" if tok else "")
    labels = cfg.get("chat_id_labels") if isinstance(cfg.get("chat_id_labels"), dict) else {}
    users = []
    for cid in cfg.get("allowed_chat_ids") or []:
        cid_str = str(cid)
        users.append({
            "chat_id": int(cid) if cid_str.lstrip("-").isdigit() else cid,
            "label": str(labels.get(cid_str, "")),
        })
    return {
        "status": "success",
        "bot_running": _is_process_running("telegram_bot.py"),
        "has_token": bool(tok),
        "token_masked": masked,
        "allowed_users": users,
        "default_target": db_manager.get_setting("tg_default_target", "gh"),
    }


def save_telegram_token(new_token, default_target=None):
    cfg = _read_bot_config()
    if new_token is not None and str(new_token).strip():
        cfg["token"] = str(new_token).strip()
        _write_bot_config(cfg)
    if default_target is not None:
        t = str(default_target).strip().lower()
        if t in ("gh", "tv", "mac", "both", "all"):
            db_manager.set_setting("tg_default_target", t)
    return {"status": "success", "message": "✅ Konfigurasi Telegram Bot disimpan!"}


def upsert_telegram_chat_id(chat_id, label=""):
    cid_str = str(chat_id or "").strip()
    if not cid_str or not cid_str.lstrip("-").isdigit():
        return {"status": "error", "message": "Chat ID harus berupa angka (contoh: 123456789)."}
    cid_int = int(cid_str)
    cfg = _read_bot_config()
    ids = [int(x) for x in (cfg.get("allowed_chat_ids") or []) if str(x).lstrip("-").isdigit()]
    if cid_int not in ids:
        ids.append(cid_int)
    cfg["allowed_chat_ids"] = ids
    labels = cfg.get("chat_id_labels") if isinstance(cfg.get("chat_id_labels"), dict) else {}
    labels[str(cid_int)] = str(label or "").strip()
    cfg["chat_id_labels"] = labels
    _write_bot_config(cfg)
    return {"status": "success", "message": f"✅ Chat ID {cid_int} disimpan!"}


def delete_telegram_chat_id(chat_id):
    cid_str = str(chat_id or "").strip()
    if not cid_str or not cid_str.lstrip("-").isdigit():
        return {"status": "error", "message": "Chat ID tidak valid."}
    cid_int = int(cid_str)
    cfg = _read_bot_config()
    ids = [int(x) for x in (cfg.get("allowed_chat_ids") or []) if str(x).lstrip("-").isdigit()]
    if cid_int not in ids:
        return {"status": "error", "message": "Chat ID tidak ditemukan."}
    ids = [x for x in ids if x != cid_int]
    cfg["allowed_chat_ids"] = ids
    labels = cfg.get("chat_id_labels") if isinstance(cfg.get("chat_id_labels"), dict) else {}
    labels.pop(str(cid_int), None)
    cfg["chat_id_labels"] = labels
    _write_bot_config(cfg)
    return {"status": "success", "message": f"🗑️ Chat ID {cid_int} dihapus."}


def send_telegram_test_message(chat_id=None, text=None):
    cfg = _read_bot_config()
    tok = str(cfg.get("token") or "").strip()
    if not tok:
        return {"status": "error", "message": "Bot Token belum diisi."}
    ids = cfg.get("allowed_chat_ids") or []
    target_cid = chat_id or (ids[0] if ids else None)
    if not target_cid:
        return {"status": "error", "message": "Belum ada Allowed Chat ID."}
    msg = text or "✅ Tes koneksi dari Modal Pengaturan Mac Remote Command Center berhasil!"
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    payload = json.dumps({"chat_id": target_cid, "text": msg}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            pass
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        if res.get("ok"):
            return {"status": "success", "message": f"✅ Pesan tes terkirim ke Chat ID {target_cid}!"}
        return {"status": "error", "message": res.get("description") or "Gagal mengirim pesan tes."}
    except Exception as e:
        return {"status": "error", "message": f"Gagal kirim ke Telegram: {str(e)}"}


# ================= 2. CAST DEVICES & TV REMOTE CRUD =================


def get_cast_and_tv_settings():
    devs = cast_manager.load_cached_devices()
    tv_info = {}
    if os.path.isfile(TV_PAIRED_FILE):
        try:
            with open(TV_PAIRED_FILE, "r") as f:
                tv_info = json.load(f)
        except Exception:
            pass
    return {
        "status": "success",
        "devices": devs,
        "tv_remote": tv_info,
    }


def upsert_cast_device(host, name, model="Google Cast", port=8009, cast_type="audio", old_host=None):
    host_s = str(host or "").strip()
    name_s = str(name or "").strip()
    if not host_s or not name_s:
        return {"status": "error", "message": "Nama perangkat dan IP host wajib diisi."}
    ctype = str(cast_type or "audio").strip().lower()
    if ctype not in ("audio", "cast"):
        ctype = "audio"
    try:
        port_i = int(port or 8009)
    except Exception:
        port_i = 8009

    devs = cast_manager.load_cached_devices()
    match_host = str(old_host or host_s).strip()
    updated = False
    for d in devs:
        if d.get("host") == match_host:
            d["host"] = host_s
            d["name"] = name_s
            d["model"] = str(model or d.get("model") or "Google Cast").strip()
            d["port"] = port_i
            d["cast_type"] = ctype
            updated = True
            break
    if not updated:
        devs.append({
            "name": name_s,
            "model": str(model or "Google Cast").strip(),
            "host": host_s,
            "port": port_i,
            "uuid": str(uuid.uuid4()),
            "cast_type": ctype,
        })
    cast_manager.save_cached_devices(devs)
    return {"status": "success", "message": f"✅ Perangkat '{name_s}' ({host_s}) disimpan!", "devices": devs}


def delete_cast_device(host):
    host_s = str(host or "").strip()
    devs = cast_manager.load_cached_devices()
    new_devs = [d for d in devs if d.get("host") != host_s]
    if len(new_devs) == len(devs):
        return {"status": "error", "message": "Perangkat Cast tidak ditemukan."}
    cast_manager.save_cached_devices(new_devs)
    return {"status": "success", "message": f"🗑️ Perangkat ({host_s}) dihapus.", "devices": new_devs}


def save_tv_remote_host(host):
    host_s = str(host or "").strip()
    if not host_s:
        return {"status": "error", "message": "IP Android TV tidak boleh kosong."}
    info = {}
    if os.path.isfile(TV_PAIRED_FILE):
        try:
            with open(TV_PAIRED_FILE, "r") as f:
                info = json.load(f)
        except Exception:
            pass
    info["host"] = host_s
    info.setdefault("paired_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(TV_PAIRED_FILE, "w") as f:
        json.dump(info, f, indent=2)
    return {"status": "success", "message": f"✅ IP Android TV Remote diset ke {host_s}."}


# ================= 3. PREFERENSI AUDIO & QUIET HOURS =================


def get_preferences():
    stored = db_manager.get_all_settings()
    prefs = dict(DEFAULT_PREFS)
    for k in DEFAULT_PREFS:
        if k in stored:
            prefs[k] = stored[k]
    return prefs


def save_preferences(data):
    if not isinstance(data, dict):
        return {"status": "error", "message": "Payload preferensi tidak valid."}
    for k, default_v in DEFAULT_PREFS.items():
        if k not in data:
            continue
        v = data[k]
        if isinstance(default_v, bool):
            v = bool(v)
        elif isinstance(default_v, int):
            try:
                v = int(v)
            except Exception:
                v = default_v
        else:
            v = str(v)
        db_manager.set_setting(k, v)
    return {"status": "success", "message": "✅ Preferensi berhasil disimpan!", "preferences": get_preferences()}


def apply_quiet_hours_volume(volume_percent):
    """Jika Quiet Hours aktif dan waktu saat ini berada di rentang Jam Tenang, batasi volume maksimal."""
    if volume_percent is None:
        return None, False
    try:
        vol = max(0, min(100, int(volume_percent)))
    except Exception:
        return volume_percent, False
    prefs = get_preferences()
    if not prefs.get("quiet_hours_enabled"):
        return vol, False
    try:
        s_h, s_m = [int(x) for x in str(prefs.get("quiet_hours_start", "22:00")).split(":")[:2]]
        e_h, e_m = [int(x) for x in str(prefs.get("quiet_hours_end", "06:00")).split(":")[:2]]
        now_t = time.localtime()
        now_min = now_t.tm_hour * 60 + now_t.tm_min
        start_min = s_h * 60 + s_m
        end_min = e_h * 60 + e_m
        if start_min <= end_min:
            in_quiet = start_min <= now_min < end_min
        else:
            in_quiet = (now_min >= start_min) or (now_min < end_min)
        if in_quiet:
            max_v = max(0, min(100, int(prefs.get("quiet_hours_max_vol", 40))))
            if vol > max_v:
                return max_v, True
    except Exception:
        pass
    return vol, False


# ================= 4. GOOGLE ASSISTANT SDK SETTINGS =================


def get_ga_settings():
    st = google_assistant_client.get_status()
    dev_cfg = {}
    dev_path = os.path.join(BASE_DIR, "google_assistant_device.json")
    if os.path.isfile(dev_path):
        try:
            with open(dev_path, "r") as f:
                dev_cfg = json.load(f)
        except Exception:
            pass
    prefs = get_preferences()
    return {
        "status": "success",
        "sdk_installed": st.get("sdk_installed", False),
        "has_client_secret": st.get("has_client_secret", False),
        "is_authorized": st.get("is_authorized", False),
        "device_model_id": dev_cfg.get("device_model_id", "-"),
        "device_id": dev_cfg.get("device_id", "-"),
        "ga_default_lang": prefs.get("ga_default_lang", ""),
        "ga_auto_play_gh": prefs.get("ga_auto_play_gh", True),
    }


# ================= 5. SSL, BACKUP & SERVICES =================


def _get_ssl_info():
    cert_path = os.path.join(BASE_DIR, "cert.pem")
    ca_path = os.path.join(BASE_DIR, "mac_remote_root_ca.crt")
    info = {"has_root_ca": os.path.isfile(ca_path), "not_after": "-", "san": "-"}
    if os.path.isfile(cert_path):
        try:
            r = subprocess.run(
                ["openssl", "x509", "-in", cert_path, "-noout", "-enddate", "-text"],
                capture_output=True, text=True, timeout=3,
            )
            for line in r.stdout.splitlines():
                if line.startswith("notAfter="):
                    info["not_after"] = line.split("=", 1)[1].strip()
                if "IP Address:" in line:
                    info["san"] = line.strip()
        except Exception:
            pass
    return info


def list_backups():
    items = []
    if os.path.isdir(BACKUP_DIR):
        for fn in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if fn.endswith(".zip"):
                fp = os.path.join(BACKUP_DIR, fn)
                sz = os.path.getsize(fp)
                items.append({
                    "filename": fn,
                    "size_kb": round(sz / 1024.0, 1),
                    "download_url": f"/api/settings/backup_download?file={fn}",
                })
    return items[:15]


def create_backup_zip():
    ts = time.strftime("%Y%m%d_%H%M%S")
    fn = f"backup_manual_{ts}.zip"
    zip_path = os.path.join(BACKUP_DIR, fn)
    tmp_db = f"/tmp/db_bak_{ts}.db"
    try:
        src = sqlite3.connect(os.path.join(BASE_DIR, "database.db"))
        dst = sqlite3.connect(tmp_db)
        src.backup(dst)
        dst.close()
        src.close()

        include_exts = {".py", ".sh", ".json", ".pem", ".crt", ".key", ".wav", ".jpg", ".png", ".plist"}
        count = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, "database.db")
            count += 1
            for name in sorted(os.listdir(BASE_DIR)):
                fp = os.path.join(BASE_DIR, name)
                if os.path.isfile(fp) and os.path.splitext(name)[1].lower() in include_exts:
                    zf.write(fp, name)
                    count += 1
        return {
            "status": "success",
            "filename": fn,
            "files_count": count,
            "size_kb": round(os.path.getsize(zip_path) / 1024.0, 1),
            "download_url": f"/api/settings/backup_download?file={fn}",
            "message": f"✅ Backup '{fn}' berhasil dibuat ({count} file)!",
        }
    except Exception as e:
        return {"status": "error", "message": f"Gagal membuat backup: {str(e)}"}
    finally:
        try:
            if os.path.isfile(tmp_db):
                os.remove(tmp_db)
        except Exception:
            pass


def get_system_and_ssl_info():
    wg_up = False
    try:
        r = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=2)
        wg_up = "10.66.66.4" in r.stdout
    except Exception:
        pass
    return {
        "status": "success",
        "ssl": _get_ssl_info(),
        "services": {
            "httpsserver": _is_process_running("server.py"),
            "telegrambot": _is_process_running("telegram_bot.py"),
            "wireguard": wg_up,
        },
        "backups": list_backups(),
    }


def restart_service(service_name):
    svc_map = {
        "httpsserver": "com.subuh.httpsserver",
        "telegrambot": "com.subuh.telegrambot",
        "wireguard": "com.subuh.wireguard",
    }
    label = svc_map.get(str(service_name or "").strip().lower())
    if not label:
        return {"status": "error", "message": "Nama service tidak valid."}
    uid = os.getuid()
    target = f"gui/{uid}/{label}"

    def _do_restart():
        try:
            time.sleep(0.6 if service_name == "httpsserver" else 0.1)
            subprocess.run(["launchctl", "kickstart", "-k", target], capture_output=True, timeout=8)
        except Exception:
            pass

    if service_name == "httpsserver":
        threading.Thread(target=_do_restart, daemon=True).start()
        return {"status": "success", "message": "🔄 HTTPS Server sedang di-restart (halaman akan aktif kembali dalam 2 detik)..."}
    _do_restart()
    return {"status": "success", "message": f"✅ Service {label} berhasil di-restart!"}
