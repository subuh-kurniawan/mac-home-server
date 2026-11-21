#!/usr/bin/env python3
"""
Database Server Ringan (SQLite) untuk Manajemen Cast, Say, Pustaka Audio & Jadwal.
Fitur:
  1. Pustaka Audio (Audio Library):
     - Penyimpanan berkas audio lokal (.mp3, .wav, .m4a, .aac, .ogg)
     - Metadata: ID, nama label, durasi, ukuran berkas, tipe MIME, tanggal upload.
  2. Manajemen Jadwal Suara (Schedules):
     - Mode Suara: 'tts' (Teks ke Suara) atau 'audio' (Pilih berkas dari Pustaka Audio).
     - Target: 'cast' (Google Home), 'say' (Speaker Mac), 'both' (Keduanya).
     - Fleksibilitas Waktu: Tanggal & Jam spesifik, durasi hitung mundur, atau harian berulang.
     - Full CRUD (Create, Read, Update, Delete).
"""

from contextlib import contextmanager
import os
import re
import sqlite3
import subprocess
import time
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
AUDIO_DIR = os.path.join(BASE_DIR, "audio_library")
os.makedirs(AUDIO_DIR, exist_ok=True)


@contextmanager
def get_db():
    """Context manager SQLite yang otomatis commit/rollback DAN menutup koneksi (anti FD leak)."""
    conn = sqlite3.connect(DB_PATH, timeout=20.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS audio_library (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            size_bytes INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            mime_type TEXT DEFAULT 'audio/mpeg',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            mode TEXT DEFAULT 'tts',              -- 'tts' atau 'audio'
            message TEXT,                         -- jika mode 'tts'
            audio_id TEXT,                        -- jika mode 'audio'
            target TEXT DEFAULT 'both',           -- 'cast', 'say', 'both'
            voice_type TEXT DEFAULT 'wanita1',    -- 'wanita1', 'pria', 'wanita2'
            schedule_type TEXT DEFAULT 'once',    -- 'once', 'daily', 'datetime'
            target_datetime TEXT NOT NULL,
            target_timestamp REAL NOT NULL,
            volume INTEGER DEFAULT 70,
            restore_volume INTEGER DEFAULT 50,
            status TEXT DEFAULT 'pending',        -- 'pending', 'triggered', 'cancelled'
            created_at TEXT NOT NULL,
            last_triggered_at TEXT,
            FOREIGN KEY (audio_id) REFERENCES audio_library(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sched_status_time ON schedules(status, target_timestamp);

        CREATE TABLE IF NOT EXISTS quick_actions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            voice_type TEXT DEFAULT 'wanita1',
            volume INTEGER DEFAULT 70,
            target_device TEXT DEFAULT '',
            icon TEXT DEFAULT '📢',
            sort_order INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)

    _migrate_quick_actions_schema()
    _migrate_schedules_schema()
    _migrate_legacy_reminders()
    _seed_default_quick_actions()


def _migrate_schedules_schema():
    """Tambahkan kolom volume dan restore_volume ke tabel schedules secara aman jika belum ada."""
    new_cols = [
        ("volume", "INTEGER DEFAULT 70"),
        ("restore_volume", "INTEGER DEFAULT 50"),
    ]
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(schedules)")
            existing = {r["name"] for r in cur.fetchall()}
            for col_name, col_def in new_cols:
                if col_name not in existing:
                    conn.execute(f"ALTER TABLE schedules ADD COLUMN {col_name} {col_def}")
    except Exception:
        pass


def _migrate_quick_actions_schema():
    """Tambahkan kolom baru (action_type, audio_id, restore_volume, use_chime, repeat_count) secara aman."""
    new_cols = [
        ("action_type", "TEXT DEFAULT 'tts'"),
        ("audio_id", "TEXT DEFAULT ''"),
        ("restore_volume", "INTEGER DEFAULT 50"),
        ("use_chime", "INTEGER DEFAULT 1"),
        ("repeat_count", "INTEGER DEFAULT 1"),
    ]
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(quick_actions)")
            existing = {r["name"] for r in cur.fetchall()}
            for col_name, col_def in new_cols:
                if col_name not in existing:
                    conn.execute(f"ALTER TABLE quick_actions ADD COLUMN {col_name} {col_def}")
    except Exception:
        pass


def _migrate_legacy_reminders():
    """Migrasi data lama dari reminders.json ke SQLite jika schedules masih kosong."""
    json_path = os.path.join(BASE_DIR, "reminders.json")
    if not os.path.exists(json_path):
        return

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM schedules")
            if cur.fetchone()[0] > 0:
                return  # Database sudah ada isinya

        import json
        with open(json_path, "r") as f:
            items = json.load(f)

        now = time.time()
        with get_db() as conn:
            for it in items:
                sid = it.get("id") or ("sch_" + uuid.uuid4().hex[:8])
                label = it.get("label", "Pengumuman")
                msg = it.get("message", "")
                tgt = it.get("target", "both")
                voice = it.get("voice_type", "wanita1")
                is_daily = bool(it.get("is_daily", False))
                sch_type = "daily" if is_daily else "once"
                tgt_ts = it.get("target_timestamp", now + 300)
                tgt_dt = it.get("target_readable", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(tgt_ts)))
                status = it.get("status", "pending")
                c_at = it.get("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))

                conn.execute("""
                INSERT OR IGNORE INTO schedules (
                    id, label, mode, message, audio_id, target, voice_type,
                    schedule_type, target_datetime, target_timestamp, status, created_at
                ) VALUES (?, ?, 'tts', ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """, (sid, label, msg, tgt, voice, sch_type, tgt_dt, tgt_ts, status, c_at))
    except Exception:
        pass


def get_audio_duration(file_path):
    """Mendapatkan durasi file audio menggunakan utilitas bawaan macOS afinfo."""
    try:
        res = subprocess.run(["afinfo", file_path], capture_output=True, text=True, timeout=3)
        m = re.search(r"estimated duration:\s*([\d.]+)\s*sec", res.stdout)
        if m:
            return round(float(m.group(1)), 1)
    except Exception:
        pass
    return 0.0


# ================= CRUD PUSTAKA AUDIO =================

VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}


def _detect_media_type(filename, mime_type=""):
    ext = os.path.splitext(str(filename or ""))[1].lower()
    if ext in VIDEO_EXTS or str(mime_type or "").lower().startswith("video/"):
        return "video"
    return "audio"


def add_audio(name, original_filename, data_bytes, mime_type=None):
    """Menyimpan file audio atau video baru ke database dan folder audio_library."""
    audio_id = "aud_" + uuid.uuid4().hex[:8]
    ext = os.path.splitext(original_filename)[1].lower()
    if not ext:
        ext = ".mp3"

    clean_basename = re.sub(r"[^\w\-.]", "_", os.path.splitext(original_filename)[0])
    stored_filename = f"{audio_id}_{clean_basename}{ext}"
    file_path = os.path.join(AUDIO_DIR, stored_filename)

    with open(file_path, "wb") as f:
        f.write(data_bytes)

    size_bytes = len(data_bytes)
    duration = get_audio_duration(file_path)

    ext_mime_map = {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mov": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".wav": "audio/wav",
        ".wave": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".mp3": "audio/mpeg",
    }
    if ext in ext_mime_map and (not mime_type or mime_type == "application/octet-stream" or (ext in VIDEO_EXTS and not str(mime_type).startswith("video/"))):
        mime_type = ext_mime_map[ext]
    elif not mime_type:
        mime_type = ext_mime_map.get(ext, "audio/mpeg")

    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    clean_name = name.strip() or os.path.splitext(original_filename)[0]

    with get_db() as conn:
        conn.execute("""
        INSERT INTO audio_library (
            id, name, original_filename, stored_filename, file_path,
            size_bytes, duration_seconds, mime_type, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (audio_id, clean_name, original_filename, stored_filename, file_path,
              size_bytes, duration, mime_type, created_at))

    return get_audio(audio_id)


def list_audios():
    """Mendapatkan seluruh daftar file audio yang tersimpan di pustaka."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT id, name, original_filename, stored_filename, file_path,
               size_bytes, duration_seconds, mime_type, created_at
        FROM audio_library
        ORDER BY created_at DESC
        """)
        rows = cur.fetchall()

    result = []
    for r in rows:
        size_fmt = _format_size(r["size_bytes"])
        dur_fmt = _format_duration(r["duration_seconds"])
        result.append({
            "id": r["id"],
            "name": r["name"],
            "original_filename": r["original_filename"],
            "stored_filename": r["stored_filename"],
            "file_path": r["file_path"],
            "size_bytes": r["size_bytes"],
            "size_formatted": size_fmt,
            "duration_seconds": r["duration_seconds"],
            "duration_formatted": dur_fmt,
            "mime_type": r["mime_type"],
            "media_type": _detect_media_type(r["original_filename"], r["mime_type"]),
            "created_at": r["created_at"],
            "stream_url": f"/api/audio/file?id={r['id']}"
        })
    return result


def get_audio(audio_id):
    """Mendapatkan data satu audio/video berdasarkan ID."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM audio_library WHERE id = ?", (audio_id,))
        r = cur.fetchone()

    if not r:
        return None

    return {
        "id": r["id"],
        "name": r["name"],
        "original_filename": r["original_filename"],
        "stored_filename": r["stored_filename"],
        "file_path": r["file_path"],
        "size_bytes": r["size_bytes"],
        "size_formatted": _format_size(r["size_bytes"]),
        "duration_seconds": r["duration_seconds"],
        "duration_formatted": _format_duration(r["duration_seconds"]),
        "mime_type": r["mime_type"],
        "media_type": _detect_media_type(r["original_filename"], r["mime_type"]),
        "created_at": r["created_at"],
        "stream_url": f"/api/audio/file?id={r['id']}"
    }


def delete_audio(audio_id):
    """Menghapus audio dari database dan menghapus filenya dari disk."""
    aud = get_audio(audio_id)
    if not aud:
        return False

    file_path = aud["file_path"]
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass

    with get_db() as conn:
        conn.execute("DELETE FROM audio_library WHERE id = ?", (audio_id,))
        # Set audio_id = NULL pada jadwal yang menggunakannya
        conn.execute("UPDATE schedules SET audio_id = NULL, status = 'cancelled' WHERE audio_id = ?", (audio_id,))

    return True


# ================= CRUD MANAJEMEN JADWAL =================

def add_schedule(label, mode="tts", message="", audio_id=None, target="both",
                 voice_type="wanita1", schedule_type="once", target_datetime="", target_timestamp=0,
                 volume=70, restore_volume=50):
    """Menambahkan jadwal baru ke database SQLite."""
    sched_id = "sch_" + uuid.uuid4().hex[:8]
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    # Normalisasi data
    mode_clean = mode if mode in ["audio", "url", "tts"] else "tts"
    target_clean = target.lower() if str(target).lower() in ["cast", "say", "both", "tv", "all"] else "both"
    voice_clean = voice_type if voice_type in ["wanita1", "pria", "wanita2"] else "wanita1"
    sch_type_clean = schedule_type if schedule_type in ["once", "daily", "datetime"] else "once"

    vol_clean = max(10, min(100, int(volume))) if volume is not None else 70
    rvol_clean = int(restore_volume) if restore_volume is not None else None

    with get_db() as conn:
        conn.execute("""
        INSERT INTO schedules (
            id, label, mode, message, audio_id, target, voice_type,
            schedule_type, target_datetime, target_timestamp, volume, restore_volume, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (sched_id, label.strip(), mode_clean, message.strip(), audio_id,
              target_clean, voice_clean, sch_type_clean, target_datetime, target_timestamp,
              vol_clean, rvol_clean, created_at))

    return get_schedule(sched_id)


def _format_target_display(tgt):
    t = str(tgt or "both").lower()
    if t == "cast":
        return "📢 Google Home"
    if t == "say":
        return "🔊 Speaker Mac"
    if t == "tv":
        return "📺 TV Stick Mi"
    if t == "all":
        return "🌐 Semua (Mac + Speaker + TV)"
    return "📢+🔊 Mac & Google Home"


def list_schedules(include_triggered=False):
    """Mendapatkan daftar jadwal suara aktif (atau semua) dengan join metadata audio."""
    with get_db() as conn:
        cur = conn.cursor()
        query = """
        SELECT s.*,
               a.name AS audio_name,
               a.original_filename AS audio_filename,
               a.duration_seconds AS audio_duration,
               a.file_path AS audio_file_path
        FROM schedules s
        LEFT JOIN audio_library a ON s.audio_id = a.id
        """
        if not include_triggered:
            query += " WHERE s.status = 'pending'"
        query += " ORDER BY s.target_timestamp ASC"

        cur.execute(query)
        rows = cur.fetchall()

    now = time.time()
    results = []
    for r in rows:
        target_ts = r["target_timestamp"]
        rem = max(0, target_ts - now)

        tgt = r["target"]
        tgt_disp = _format_target_display(tgt)

        v = r["voice_type"]
        if v == "pria":
            v_disp = "Pria (Ardi)"
        elif v == "wanita2":
            v_disp = "Wanita 2 (Google ID)"
        else:
            v_disp = "Wanita 1 (Gadis)"

        is_daily = (r["schedule_type"] == "daily")

        vol_val = r["volume"] if ("volume" in r.keys() and r["volume"] is not None) else 70
        rvol_val = r["restore_volume"] if "restore_volume" in r.keys() else 50

        results.append({
            "id": r["id"],
            "label": r["label"],
            "mode": r["mode"],
            "message": r["message"] or "",
            "audio_id": r["audio_id"],
            "audio_name": r["audio_name"],
            "audio_filename": r["audio_filename"],
            "audio_duration": r["audio_duration"],
            "audio_file_path": r["audio_file_path"],
            "target": tgt,
            "target_display": tgt_disp,
            "voice_type": v,
            "voice_display": v_disp,
            "schedule_type": r["schedule_type"],
            "is_daily": is_daily,
            "target_datetime": r["target_datetime"],
            "target_timestamp": target_ts,
            "remaining_seconds": int(rem),
            "remaining_readable": _format_remaining_time(rem),
            "volume": vol_val,
            "restore_volume": rvol_val,
            "status": r["status"],
            "created_at": r["created_at"],
            "last_triggered_at": r["last_triggered_at"]
        })
    return results


def get_schedule(sched_id):
    """Mendapatkan data satu jadwal lengkap dengan metadata audio."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT s.*,
               a.name AS audio_name,
               a.original_filename AS audio_filename,
               a.duration_seconds AS audio_duration,
               a.file_path AS audio_file_path
        FROM schedules s
        LEFT JOIN audio_library a ON s.audio_id = a.id
        WHERE s.id = ?
        """, (sched_id,))
        r = cur.fetchone()

    if not r:
        return None

    now = time.time()
    target_ts = r["target_timestamp"]
    rem = max(0, target_ts - now)

    tgt = r["target"]
    tgt_disp = _format_target_display(tgt)
    v = r["voice_type"]
    v_disp = "Pria (Ardi)" if v == "pria" else ("Wanita 2 (Google ID)" if v == "wanita2" else "Wanita 1 (Gadis)")

    vol_val = r["volume"] if ("volume" in r.keys() and r["volume"] is not None) else 70
    rvol_val = r["restore_volume"] if "restore_volume" in r.keys() else 50

    return {
        "id": r["id"],
        "label": r["label"],
        "mode": r["mode"],
        "message": r["message"] or "",
        "audio_id": r["audio_id"],
        "audio_name": r["audio_name"],
        "audio_filename": r["audio_filename"],
        "audio_duration": r["audio_duration"],
        "audio_file_path": r["audio_file_path"],
        "target": tgt,
        "target_display": tgt_disp,
        "voice_type": v,
        "voice_display": v_disp,
        "schedule_type": r["schedule_type"],
        "is_daily": (r["schedule_type"] == "daily"),
        "target_datetime": r["target_datetime"],
        "target_timestamp": target_ts,
        "remaining_seconds": int(rem),
        "remaining_readable": _format_remaining_time(rem),
        "volume": vol_val,
        "restore_volume": rvol_val,
        "status": r["status"],
        "created_at": r["created_at"],
        "last_triggered_at": r["last_triggered_at"]
    }


def update_schedule(sched_id, **kwargs):
    """Update field-field pada jadwal."""
    allowed = ["label", "mode", "message", "audio_id", "target", "voice_type",
               "schedule_type", "target_datetime", "target_timestamp", "volume", "restore_volume", "status"]
    updates = []
    values = []
    for k, v in kwargs.items():
        if k in allowed:
            updates.append(f"{k} = ?")
            values.append(v)

    if not updates:
        return False

    values.append(sched_id)
    with get_db() as conn:
        conn.execute(f"UPDATE schedules SET {', '.join(updates)} WHERE id = ?", tuple(values))

    return get_schedule(sched_id)


def delete_schedule(sched_id):
    """Menghapus jadwal berdasarkan ID."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM schedules WHERE id = ?", (sched_id,))
        return cur.rowcount > 0


def mark_schedule_triggered(sched_id, is_daily=False, next_timestamp=None, next_datetime=None):
    """Tandai jadwal telah berbunyi, atau jadwalkan ulang jika berulang harian."""
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        if is_daily and next_timestamp:
            conn.execute("""
            UPDATE schedules
            SET target_timestamp = ?, target_datetime = ?, last_triggered_at = ?
            WHERE id = ?
            """, (next_timestamp, next_datetime or "", now_str, sched_id))
        else:
            conn.execute("""
            UPDATE schedules
            SET status = 'triggered', last_triggered_at = ?
            WHERE id = ?
            """, (now_str, sched_id))


def get_pending_schedules_due(current_timestamp):
    """Mendapatkan semua jadwal pending yang waktunya telah tiba."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT s.*,
               a.name AS audio_name,
               a.original_filename AS audio_filename,
               a.file_path AS audio_file_path
        FROM schedules s
        LEFT JOIN audio_library a ON s.audio_id = a.id
        WHERE s.status = 'pending' AND s.target_timestamp <= ?
        """, (current_timestamp,))
        rows = cur.fetchall()

    return [dict(r) for r in rows]


# ================= FORMATTING UTILITIES =================

def _format_size(bytes_num):
    for u in ["B", "KB", "MB", "GB"]:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {u}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} TB"


def _format_duration(seconds):
    if not seconds or seconds <= 0:
        return "-"
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _format_remaining_time(seconds):
    if seconds <= 0:
        return "Sedang berjalan..."
    if seconds < 60:
        return f"{int(seconds)} detik lagi"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} menit lagi"
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        return f"{hours} jam {rem_min} mnt lagi" if rem_min > 0 else f"{hours} jam lagi"
    days = hours // 24
    rem_hr = hours % 24
    return f"{days} hari {rem_hr} jam lagi" if rem_hr > 0 else f"{days} hari lagi"


# ================= CRUD QUICK ACTIONS =================


def _seed_default_quick_actions():
    """Isi 3 contoh Quick Action bawaan jika tabel masih kosong agar langsung siap pakai."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM quick_actions")
            if cur.fetchone()[0] > 0:
                return
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            defaults = [
                ("qa_makan", "Waktunya Makan", "Perhatian, makanan sudah siap. Silakan menuju ruang makan.", "wanita1", 70, "", "🍽️", 1),
                ("qa_sholat", "Pengingat Sholat", "Pengingat, sudah masuk waktu sholat. Mari segera menunaikan ibadah.", "wanita1", 70, "", "🕌", 2),
                ("qa_kumpul", "Kumpul Ruang Keluarga", "Halo semua, mohon berkumpul sebentar di ruang keluarga.", "wanita1", 70, "", "🏠", 3),
            ]
            for qid, title, msg, vt, vol, dev, ico, order in defaults:
                conn.execute("""
                INSERT OR IGNORE INTO quick_actions (
                    id, title, message, voice_type, volume, target_device, icon, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (qid, title, msg, vt, vol, dev, ico, order, now, now))
    except Exception:
        pass


def _row_to_quick_action(r):
    keys = r.keys()
    return {
        "id": r["id"],
        "title": r["title"],
        "message": r["message"],
        "action_type": (r["action_type"] if "action_type" in keys and r["action_type"] else "tts"),
        "audio_id": (r["audio_id"] if "audio_id" in keys and r["audio_id"] else ""),
        "voice_type": r["voice_type"] or "wanita1",
        "volume": int(r["volume"]) if r["volume"] is not None else 70,
        "restore_volume": int(r["restore_volume"]) if ("restore_volume" in keys and r["restore_volume"] is not None) else 50,
        "use_chime": bool(r["use_chime"]) if ("use_chime" in keys and r["use_chime"] is not None) else True,
        "repeat_count": max(1, min(3, int(r["repeat_count"]))) if ("repeat_count" in keys and r["repeat_count"] is not None) else 1,
        "target_device": r["target_device"] or "",
        "icon": r["icon"] or "📢",
        "sort_order": int(r["sort_order"] or 0),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def list_quick_actions():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT * FROM quick_actions
        ORDER BY sort_order ASC, created_at DESC
        """)
        rows = cur.fetchall()
    return [_row_to_quick_action(r) for r in rows]


def get_quick_action(qa_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM quick_actions WHERE id = ?", (qa_id,))
        r = cur.fetchone()
    return _row_to_quick_action(r) if r else None


def add_quick_action(
    title,
    message="",
    voice_type="wanita1",
    volume=70,
    target_device="",
    icon="📢",
    action_type="tts",
    audio_id="",
    restore_volume=50,
    use_chime=True,
    repeat_count=1,
):
    atype = str(action_type or "tts").strip().lower()
    if atype not in ("tts", "assistant", "audio"):
        atype = "tts"

    title_s = str(title or "").strip()
    msg_s = str(message or "").strip()
    aid_s = str(audio_id or "").strip()

    if atype == "audio":
        if not aid_s:
            return {"status": "error", "message": "Pilih file audio/video dari Pustaka Media terlebih dahulu."}
        aud = get_audio(aid_s)
        if not title_s:
            title_s = aud["name"] if aud else "Putar Audio"
        if not msg_s:
            msg_s = aud["name"] if aud else aid_s
    else:
        if not msg_s:
            return {"status": "error", "message": "Kalimat/perintah tidak boleh kosong."}
        if not title_s:
            title_s = msg_s[:32] + ("..." if len(msg_s) > 32 else "")

    vt = str(voice_type or "wanita1").strip().lower()
    if vt not in ("wanita1", "wanita2", "pria"):
        vt = "wanita1"
    try:
        vol = max(0, min(100, int(volume)))
    except Exception:
        vol = 70
    try:
        rvol = int(restore_volume)
        if rvol < -1 or rvol > 100:
            rvol = 50
    except Exception:
        rvol = 50
    chime_i = 1 if bool(use_chime) else 0
    try:
        rep_i = max(1, min(3, int(repeat_count)))
    except Exception:
        rep_i = 1

    qa_id = "qa_" + uuid.uuid4().hex[:8]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    ico = (str(icon or "📢").strip() or "📢")[:8]
    dev = str(target_device or "").strip()

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM quick_actions")
        next_order = cur.fetchone()[0]
        conn.execute("""
        INSERT INTO quick_actions (
            id, title, message, voice_type, volume, target_device, icon,
            action_type, audio_id, restore_volume, use_chime, repeat_count,
            sort_order, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (qa_id, title_s, msg_s, vt, vol, dev, ico, atype, aid_s, rvol, chime_i, rep_i, next_order, now, now))

    return {"status": "success", "quick_action": get_quick_action(qa_id)}


def update_quick_action(
    qa_id,
    title=None,
    message=None,
    voice_type=None,
    volume=None,
    target_device=None,
    icon=None,
    action_type=None,
    audio_id=None,
    restore_volume=None,
    use_chime=None,
    repeat_count=None,
):
    existing = get_quick_action(qa_id)
    if not existing:
        return {"status": "error", "message": "Quick Action tidak ditemukan."}

    new_atype = str(action_type).strip().lower() if action_type is not None else existing["action_type"]
    if new_atype not in ("tts", "assistant", "audio"):
        new_atype = "tts"

    new_aid = str(audio_id).strip() if audio_id is not None else existing["audio_id"]
    new_title = str(title).strip() if title is not None else existing["title"]
    new_msg = str(message).strip() if message is not None else existing["message"]

    if new_atype == "audio":
        if not new_aid:
            return {"status": "error", "message": "Pilih file dari Pustaka Media terlebih dahulu."}
        if not new_title:
            aud = get_audio(new_aid)
            new_title = aud["name"] if aud else "Putar Audio"
    else:
        if not new_msg:
            return {"status": "error", "message": "Kalimat/perintah tidak boleh kosong."}
        if not new_title:
            new_title = new_msg[:32]

    new_vt = str(voice_type).strip().lower() if voice_type is not None else existing["voice_type"]
    if new_vt not in ("wanita1", "wanita2", "pria"):
        new_vt = "wanita1"

    if volume is not None:
        try:
            new_vol = max(0, min(100, int(volume)))
        except Exception:
            new_vol = existing["volume"]
    else:
        new_vol = existing["volume"]

    if restore_volume is not None:
        try:
            new_rvol = int(restore_volume)
            if new_rvol < -1 or new_rvol > 100:
                new_rvol = 50
        except Exception:
            new_rvol = existing["restore_volume"]
    else:
        new_rvol = existing["restore_volume"]

    new_chime = (1 if bool(use_chime) else 0) if use_chime is not None else (1 if existing["use_chime"] else 0)
    if repeat_count is not None:
        try:
            new_rep = max(1, min(3, int(repeat_count)))
        except Exception:
            new_rep = existing["repeat_count"]
    else:
        new_rep = existing["repeat_count"]

    new_dev = str(target_device).strip() if target_device is not None else existing["target_device"]
    new_ico = (str(icon).strip() or "📢")[:8] if icon is not None else existing["icon"]
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        conn.execute("""
        UPDATE quick_actions
        SET title = ?, message = ?, voice_type = ?, volume = ?, target_device = ?, icon = ?,
            action_type = ?, audio_id = ?, restore_volume = ?, use_chime = ?, repeat_count = ?, updated_at = ?
        WHERE id = ?
        """, (new_title, new_msg, new_vt, new_vol, new_dev, new_ico, new_atype, new_aid, new_rvol, new_chime, new_rep, now, qa_id))

    return {"status": "success", "quick_action": get_quick_action(qa_id)}


def delete_quick_action(qa_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM quick_actions WHERE id = ?", (qa_id,))
        deleted = cur.rowcount > 0
    if not deleted:
        return {"status": "error", "message": "Quick Action tidak ditemukan."}
    return {"status": "success", "message": "Quick Action berhasil dihapus."}


# ================= CRUD APP SETTINGS =================


def get_setting(key, default=None):
    import json as _json
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM app_settings WHERE key = ?", (str(key),))
            r = cur.fetchone()
        if r is None:
            return default
        return _json.loads(r["value"])
    except Exception:
        return default


def set_setting(key, value):
    import json as _json
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    val_json = _json.dumps(value, ensure_ascii=False)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (str(key), val_json, now),
        )
    return value


def get_all_settings():
    import json as _json
    out = {}
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM app_settings")
            for r in cur.fetchall():
                try:
                    out[r["key"]] = _json.loads(r["value"])
                except Exception:
                    out[r["key"]] = r["value"]
    except Exception:
        pass
    return out


# Inisialisasi DB saat modul diimpor
init_db()

if __name__ == "__main__":
    print("Inisialisasi Database Server SQLite...")
    print("Daftar Audio:", list_audios())
    print("Daftar Jadwal:", list_schedules())
    print("Database siap digunakan.")
