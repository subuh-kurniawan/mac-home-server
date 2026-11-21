#!/usr/bin/env python3
"""
Sistem Manajemen Jadwal Suara Fleksibel (Cast & Say Scheduler) dengan Backend SQLite.
Fitur:
  1. Dua Mode Suara:
     - 'tts'   : Teks diucapkan dengan AI Indonesian TTS (Wanita 1, Pria, Wanita 2).
     - 'audio' : Memutar file audio yang diunggah ke Pustaka Audio Server (.mp3, .wav, .m4a, dll.).
  2. Target Perangkat:
     - 'cast' : Speaker Google Home via Wi-Fi.
     - 'say'  : Speaker internal Mac mini hardware.
     - 'both' : Mac mini DAN Google Home berbunyi serempak.
  3. Pilihan Waktu Sangat Fleksibel:
     - Tanggal & Waktu Spesifik: '2026-11-22 08:30', '2026-11-22T08:30', 'besok 09:00', 'lusa 14:00'
     - Hitung Mundur: '10m', '30s', '1h', '45m', '2h30m'
     - Jam Hari Ini / Besok: '07:00', '12:30', '19:45'
     - Rutinitas Harian: 'daily 07:00', 'daily 18:30' (otomatis re-arm setiap hari)
  4. Penyimpanan SQLite Database Server (Ringan, Cepat, Tangguh).
"""

import datetime
import html
import os
import re
import threading
import time
import uuid

import cast_manager
import db_manager
import indonesian_tts
import interactive_popup
import mac_player
import quick_action_runner
import settings_manager

_callbacks = []
_scheduler_started = False
_lock = threading.Lock()


def register_trigger_callback(fn):
    """Mendaftarkan fungsi callback ketika jadwal berbunyi (misal: kirim notifikasi ke Telegram)."""
    with _lock:
        if fn not in _callbacks:
            _callbacks.append(fn)


def parse_schedule_time(time_str):
    """
    Mengubah format waktu fleksibel menjadi (delay_seconds, schedule_type, readable_datetime, target_timestamp).
    Mendukung:
      - Tanggal & Jam: '2026-11-22 08:30', '2026-11-22T08:30', '22-11-2026 08:30'
      - Hari Relatif:  'besok 08:00', 'tomorrow 08:00', 'lusa 09:30'
      - Rutinitas:     'daily 07:00', 'harian 12:30'
      - Jam Saja:      '07:00', '14.30'
      - Durasi:        '10m', '1h', '30s', '2h30m', '15'
    """
    raw = time_str.strip()
    raw_lower = raw.lower()
    now_ts = time.time()
    now_dt = datetime.datetime.now()

    # 1. Rutinitas Harian: 'daily 07:00', 'harian 12:30', 'tiap 08:00'
    if raw_lower.startswith("daily ") or raw_lower.startswith("harian ") or raw_lower.startswith("tiap "):
        clock_str = re.sub(r"^(?:daily|harian|tiap)\s+", "", raw_lower).strip()
        m_clock = re.match(r"^(\d{1,2})[:.](\d{2})$", clock_str)
        if m_clock:
            th, tm = int(m_clock.group(1)), int(m_clock.group(2))
            if 0 <= th < 24 and 0 <= tm < 60:
                tgt_dt = now_dt.replace(hour=th, minute=tm, second=0, microsecond=0)
                if tgt_dt <= now_dt:
                    tgt_dt += datetime.timedelta(days=1)
                tgt_ts = tgt_dt.timestamp()
                return tgt_ts - now_ts, "daily", tgt_dt.strftime("%Y-%m-%d %H:%M:%S"), tgt_ts

    # 2. Hari Relatif: 'besok 08:00' atau 'lusa 14:00'
    m_rel_day = re.match(r"^(besok|tomorrow|lusa)\s+(\d{1,2})[:.](\d{2})$", raw_lower)
    if m_rel_day:
        day_type = m_rel_day.group(1)
        th, tm = int(m_rel_day.group(2)), int(m_rel_day.group(3))
        if 0 <= th < 24 and 0 <= tm < 60:
            add_days = 2 if day_type == "lusa" else 1
            tgt_dt = (now_dt + datetime.timedelta(days=add_days)).replace(hour=th, minute=tm, second=0, microsecond=0)
            tgt_ts = tgt_dt.timestamp()
            return tgt_ts - now_ts, "datetime", tgt_dt.strftime("%Y-%m-%d %H:%M:%S"), tgt_ts

    # 3. Format Tanggal & Jam Lengkap: '2026-11-22 08:30' atau '2026-11-22T08:30' (datetime-local HTML)
    for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S"]:
        try:
            clean_val = raw.replace("T", " ") if "T" in raw else raw
            clean_fmt = fmt.replace("T", " ")
            dt_obj = datetime.datetime.strptime(clean_val, clean_fmt)
            tgt_ts = dt_obj.timestamp()
            diff = tgt_ts - now_ts
            if diff > 0:
                return diff, "datetime", dt_obj.strftime("%Y-%m-%d %H:%M:%S"), tgt_ts
            else:
                # Waktu sudah lewat
                return None, "datetime", "Waktu yang dipilih sudah lewat", 0
        except ValueError:
            pass

    # 4. Format Jam Saja: '07:00' atau '14.30'
    m_clock = re.match(r"^(\d{1,2})[:.](\d{2})$", raw_lower)
    if m_clock:
        th, tm = int(m_clock.group(1)), int(m_clock.group(2))
        if 0 <= th < 24 and 0 <= tm < 60:
            tgt_dt = now_dt.replace(hour=th, minute=tm, second=0, microsecond=0)
            if tgt_dt <= now_dt:
                tgt_dt += datetime.timedelta(days=1)
            tgt_ts = tgt_dt.timestamp()
            return tgt_ts - now_ts, "once", tgt_dt.strftime("%Y-%m-%d %H:%M:%S"), tgt_ts

    # 5. Format Durasi Relatif: '10m', '1h', '30s', '2h30m'
    total_seconds = 0
    matches = re.findall(r"(\d+)\s*([hms])", raw_lower)
    if matches:
        for val, unit in matches:
            val = int(val)
            if unit == "h":
                total_seconds += val * 3600
            elif unit == "m":
                total_seconds += val * 60
            elif unit == "s":
                total_seconds += val
        if total_seconds > 0:
            tgt_dt = now_dt + datetime.timedelta(seconds=total_seconds)
            tgt_ts = tgt_dt.timestamp()
            return total_seconds, "once", tgt_dt.strftime("%Y-%m-%d %H:%M:%S"), tgt_ts

    # 6. Angka polos dianggap menit (misal: '15' -> 15 menit)
    if raw_lower.isdigit():
        total_seconds = int(raw_lower) * 60
        tgt_dt = now_dt + datetime.timedelta(seconds=total_seconds)
        tgt_ts = tgt_dt.timestamp()
        return total_seconds, "once", tgt_dt.strftime("%Y-%m-%d %H:%M:%S"), tgt_ts

    return None, "unknown", raw, 0


def add_schedule(message="", time_str="", target="both", voice_type="wanita1",
                 label="", is_daily=False, mode="tts", audio_id=None,
                 exact_datetime=None, exact_timestamp=None,
                 volume=70, restore_volume=50):
    """
    Menambahkan jadwal pengumuman baru (TTS atau Audio File) ke database SQLite.
      - mode: 'tts' atau 'audio'
      - audio_id: ID audio jika mode == 'audio'
      - target: 'cast' (Google Home), 'say' (Speaker Mac), 'both' (Keduanya)
      - voice_type: 'wanita1', 'pria', 'wanita2' (jika mode == 'tts')
      - volume: persentase volume pemutaran (10 - 100)
      - restore_volume: volume pemulihan setelah selesai (50, -1 untuk kembali ke volume awal, atau None)
    """
    mode_clean = "audio" if mode == "audio" else ("url" if mode == "url" else "tts")

    # Validasi input sesuai mode
    if mode_clean == "audio":
        if not audio_id:
            return {"status": "error", "message": "Harap pilih file audio/video dari pustaka"}
        aud = db_manager.get_audio(audio_id)
        if not aud:
            return {"status": "error", "message": "File media tidak ditemukan di pustaka server"}
        if not label:
            label = f"Media: {aud['name']}"
    elif mode_clean == "url":
        message = message.strip()
        if not (message.startswith("http://") or message.startswith("https://")):
            return {"status": "error", "message": "URL YouTube/Stream harus diawali http:// atau https://"}
        if not label:
            label = "Jadwal TV / YouTube"
        if target in ("say", "both"):
            target = "tv"
    else:
        message = message.strip()
        if not message:
            return {"status": "error", "message": "Pesan suara tidak boleh kosong"}
        if not label:
            label = "Pengumuman Suara"

    # Waktu
    if exact_timestamp and exact_timestamp > time.time():
        tgt_ts = float(exact_timestamp)
        tgt_dt = exact_datetime or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(tgt_ts))
        sch_type = "daily" if is_daily else "datetime"
        delay = tgt_ts - time.time()
    else:
        if not time_str:
            return {"status": "error", "message": "Waktu eksekusi tidak boleh kosong"}

        delay, parsed_type, clean_dt, tgt_ts = parse_schedule_time(time_str)
        if not delay or delay <= 0:
            return {
                "status": "error",
                "message": f"Waktu '{time_str}' tidak valid atau sudah lewat. Contoh: 10m, 1h, 07:30, 2026-11-22 08:30, daily 12:00"
            }
        sch_type = "daily" if (is_daily or parsed_type == "daily") else parsed_type
        tgt_dt = clean_dt

    # Simpan ke SQLite Database
    sched = db_manager.add_schedule(
        label=label.strip(),
        mode=mode_clean,
        message=message,
        audio_id=audio_id if mode_clean == "audio" else None,
        target=target,
        voice_type=voice_type,
        schedule_type=sch_type,
        target_datetime=tgt_dt,
        target_timestamp=tgt_ts,
        volume=volume,
        restore_volume=restore_volume
    )

    ensure_scheduler_running()

    return {
        "status": "success",
        "schedule": sched,
        "id": sched["id"],
        "label": sched["label"],
        "mode": sched["mode"],
        "message": sched["message"],
        "target": sched["target"],
        "target_display": sched["target_display"],
        "voice_type": sched["voice_type"],
        "voice_display": sched["voice_display"],
        "target_readable": sched["target_datetime"],
        "remaining_readable": sched["remaining_readable"],
        "volume": sched.get("volume", 70),
        "restore_volume": sched.get("restore_volume", 50),
        "is_daily": sched["is_daily"]
    }


def list_schedules(include_triggered=False):
    """Mendapatkan daftar jadwal dari database SQLite."""
    return db_manager.list_schedules(include_triggered=include_triggered)


def get_schedule(sched_id):
    """Mendapatkan satu jadwal berdasarkan ID."""
    return db_manager.get_schedule(sched_id)


def update_schedule(sched_id, **kwargs):
    """Update jadwal yang ada."""
    return db_manager.update_schedule(sched_id, **kwargs)


def delete_schedule(sched_id):
    """Menghapus jadwal dari SQLite."""
    return db_manager.delete_schedule(sched_id)


def trigger_schedule_now(sched_id):
    """Menjalankan uji coba suara jadwal secara instan sekarang."""
    item = db_manager.get_schedule(sched_id)
    if not item:
        return {"status": "error", "message": "Jadwal tidak ditemukan"}

    threading.Thread(target=_trigger_schedule_action, args=(item,), daemon=True).start()
    return {"status": "success", "message": f"Uji coba jadwal '{item.get('label')}' sedang dibunyikan!"}


def _trigger_schedule_action(item):
    """Eksekusi pemutaran saat waktu jadwal tiba (Mendukung TTS, Audio/Video Pustaka, & YouTube/URL ke Mac, Google Home, dan TV Stick Mi)."""
    mode = item.get("mode", "tts")
    target = item.get("target", "both")
    label = item.get("label", "Pengingat Suara")

    vol_raw = item.get("volume", 70)
    try:
        vol, _ = settings_manager.apply_quiet_hours_volume(vol_raw)
    except Exception:
        vol = vol_raw
    rvol_cfg = item.get("restore_volume", 50)

    def _apply_cast_volume_and_restore(dev_name):
        try:
            if vol is not None:
                restore_val = None
                if rvol_cfg == -1:
                    restore_val = quick_action_runner._get_current_volume_percent(dev_name)
                elif rvol_cfg is not None:
                    restore_val = max(0, min(100, int(rvol_cfg)))

                cast_manager.set_google_home_volume(
                    volume_percent=int(vol), muted=False, target_device_name=dev_name
                )
                if restore_val is not None:
                    quick_action_runner._schedule_volume_restore(dev_name, restore_val)
        except Exception:
            pass

    def _apply_mac_volume_and_restore():
        try:
            import subprocess
            if vol is not None:
                orig_vol = None
                if rvol_cfg == -1:
                    res = subprocess.run(
                        ["osascript", "-e", "output volume of (get volume settings)"],
                        capture_output=True, text=True, timeout=2
                    )
                    if res.returncode == 0 and res.stdout.strip().isdigit():
                        orig_vol = int(res.stdout.strip())
                subprocess.run(["osascript", "-e", f"set volume output volume {int(vol)}"], timeout=2)

                restore_val = orig_vol if rvol_cfg == -1 else (rvol_cfg if rvol_cfg is not None else None)
                if restore_val is not None:
                    def _restore_mac():
                        time.sleep(12)
                        try:
                            subprocess.run(["osascript", "-e", f"set volume output volume {int(restore_val)}"], timeout=2)
                        except Exception:
                            pass
                    threading.Thread(target=_restore_mac, daemon=True).start()
        except Exception:
            pass

    if mode == "url":
        url = (item.get("message") or "").strip()
        if url:
            def _run_url():
                try:
                    dev_tgt = None if target == "cast" else "tv"
                    _apply_cast_volume_and_restore(dev_tgt)
                    cast_manager.cast_url_to_device(url, target_device_name=dev_tgt, title=label)
                except Exception:
                    pass
            threading.Thread(target=_run_url, daemon=True).start()
            try:
                interactive_popup.send_banner_notification(
                    f"📺 {label}", f"Memutar URL di TV: {url[:60]}", sound=False
                )
            except Exception:
                pass
    elif mode == "audio":
        audio_path = item.get("audio_file_path")
        audio_name = item.get("audio_name") or label
        aud_meta = None
        if item.get("audio_id"):
            aud_meta = db_manager.get_audio(item.get("audio_id"))
            if aud_meta and (not audio_path or not os.path.exists(audio_path)):
                audio_path = aud_meta.get("file_path")

        is_video = bool(aud_meta and aud_meta.get("media_type") == "video") or str(audio_path or "").lower().endswith((".mp4", ".m4v", ".mov", ".webm", ".mkv"))

        if audio_path and os.path.exists(audio_path):
            # 1. Mainkan di Mac mini jika target 'say', 'both', atau 'all'
            if target in ["say", "mac", "both", "all"]:
                _apply_mac_volume_and_restore()
                if is_video:
                    try:
                        import subprocess
                        subprocess.Popen(["open", audio_path])
                    except Exception:
                        pass
                else:
                    mac_player.play_mac_audio(audio_path, title=audio_name, source="schedule")

            # 2. Siarkan ke Google Home jika target 'cast', 'both', atau 'all' (hanya audio)
            if target in ["cast", "gh", "both", "all"] and not is_video:
                def _run_gh():
                    try:
                        _apply_cast_volume_and_restore(None)
                        cast_manager.broadcast_audio_file_to_google_home(
                            audio_path, target_device_name=None, title=audio_name
                        )
                    except Exception:
                        pass
                threading.Thread(target=_run_gh, daemon=True).start()

            # 3. Siarkan ke TV Stick Mi jika target 'tv' atau 'all' (Audio & Video)
            if target in ["tv", "all"]:
                def _run_tv():
                    try:
                        _apply_cast_volume_and_restore("tv")
                        if is_video:
                            cast_manager.cast_media_file_to_device(audio_path, target_device_name="tv", title=audio_name)
                        else:
                            cast_manager.broadcast_audio_file_to_google_home(audio_path, target_device_name="tv", title=audio_name)
                    except Exception:
                        pass
                threading.Thread(target=_run_tv, daemon=True).start()

            # 4. Notifikasi visual pop-up banner di layar Mac (hanya bunyikan suara di Mac jika target menyertakan Mac)
            try:
                with_sound = (target in ["say", "mac", "both", "all"])
                interactive_popup.send_banner_notification(
                    f"⏰ {label}", f"Memutar media: {audio_name}", sound=with_sound
                )
            except Exception:
                pass
    else:
        # Mode TTS
        msg = item.get("message", "Perhatian, waktu jadwal telah tiba.")
        voice = item.get("voice_type", "wanita1")

        # 1. Mainkan di Speaker Mac mini jika target 'say', 'both', atau 'all'
        if target in ["say", "mac", "both", "all"]:
            try:
                _apply_mac_volume_and_restore()
                indonesian_tts.speak_indonesian(msg, voice_type=voice)
            except Exception:
                pass

        # 2. Siarkan ke Google Home jika target 'cast', 'both', atau 'all'
        if target in ["cast", "gh", "both", "all"]:
            def _run_tts_gh():
                try:
                    _apply_cast_volume_and_restore(None)
                    cast_manager.broadcast_to_google_home(msg, target_device_name=None, voice_type=voice)
                except Exception:
                    pass
            threading.Thread(target=_run_tts_gh, daemon=True).start()

        # 3. Siarkan ke TV Stick Mi jika target 'tv' atau 'all'
        if target in ["tv", "all"]:
            def _run_tts_tv():
                try:
                    _apply_cast_volume_and_restore("tv")
                    cast_manager.broadcast_to_google_home(msg, target_device_name="tv", voice_type=voice)
                except Exception:
                    pass
            threading.Thread(target=_run_tts_tv, daemon=True).start()

        # 4. Notifikasi visual pop-up banner di layar Mac (hanya bunyikan suara di Mac jika target menyertakan Mac)
        try:
            with_sound = (target in ["say", "mac", "both", "all"])
            interactive_popup.send_banner_notification(f"⏰ {label}", msg, sound=with_sound)
        except Exception:
            pass

    # 4. Beritahu callback eksternal (Telegram Bot)
    with _lock:
        cbs = list(_callbacks)
    for cb in cbs:
        try:
            cb(item)
        except Exception:
            pass


def _scheduler_loop():
    """Loop background thread yang memantau waktu jadwal realtime dari SQLite."""
    while True:
        try:
            now = time.time()
            due_items = db_manager.get_pending_schedules_due(now)

            for it in due_items:
                sid = it["id"]
                is_daily = (it.get("schedule_type") == "daily")

                if is_daily:
                    # Harian berulang: jadwalkan kembali 24 jam ke depan
                    next_ts = it["target_timestamp"] + 86400
                    next_dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(next_ts))
                    db_manager.mark_schedule_triggered(sid, is_daily=True, next_timestamp=next_ts, next_datetime=next_dt)
                else:
                    db_manager.mark_schedule_triggered(sid, is_daily=False)

                # Eksekusi aksi suara di thread terpisah
                threading.Thread(target=_trigger_schedule_action, args=(it,), daemon=True).start()

        except Exception:
            pass

        time.sleep(2)


def ensure_scheduler_running():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()


# Fungsi pembungkus kompatibilitas mundur
def add_reminder(message, delay_or_time_str):
    return add_schedule(message=message, time_str=delay_or_time_str, target="both")

def list_reminders(include_triggered=False):
    return list_schedules(include_triggered=include_triggered)

def cancel_reminder(rem_id):
    return delete_schedule(rem_id)


# Mulai thread scheduler saat modul dimuat
ensure_scheduler_running()

if __name__ == "__main__":
    print("Testing Smart Reminders & SQLite Scheduler...")
    print("Active Schedules:", list_schedules())
