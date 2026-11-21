#!/usr/bin/env python3
"""
Mac Audio Player & Controller.
Mengelola pemutaran suara di speaker Mac mini (afplay & say).

Fitur Utama:
1. Anti-Overlap: Menghentikan suara sebelumnya sebelum memulai pemutaran baru
   sehingga tidak pernah bertumpuk/overlap sama sekali.
2. Status Tracking: Mengetahui apakah Mac sedang memutar audio/suara, judul berkas, durasi, dan waktu berlalu.
3. Stop Control: Kemampuan untuk menghentikan pemutaran audio di Mac seketika kapan saja.
"""

import os
import subprocess
import threading
import time

_lock = threading.Lock()
_active_proc = None
_playback_info = {
    "is_playing": False,
    "title": "",
    "file_path": "",
    "source": "",  # 'audio_library', 'tts', 'voice_note', 'intercom', 'schedule'
    "started_at": 0.0,
    "duration": 0.0,
}


def stop_mac_audio():
    """Menghentikan semua pemutaran suara (afplay & say) di Mac seketika."""
    global _active_proc, _playback_info
    with _lock:
        if _active_proc:
            try:
                _active_proc.terminate()
                _active_proc.kill()
            except Exception:
                pass
            _active_proc = None

        # Jaminan 100% matikan semua proses afplay dan say yang ada
        try:
            subprocess.run(["pkill", "-9", "-x", "afplay"], timeout=2, capture_output=True)
        except Exception:
            pass
        try:
            subprocess.run(["pkill", "-9", "-x", "say"], timeout=2, capture_output=True)
        except Exception:
            pass

        _playback_info = {
            "is_playing": False,
            "title": "",
            "file_path": "",
            "source": "",
            "started_at": 0.0,
            "duration": 0.0,
        }
    return {
        "status": "success",
        "message": "Pemutaran suara di Mac mini berhasil dihentikan.",
    }


def _watcher_thread(proc, file_path):
    global _active_proc, _playback_info
    try:
        proc.wait(timeout=600)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    finally:
        with _lock:
            if _active_proc == proc:
                _active_proc = None
                _playback_info["is_playing"] = False
                _playback_info["title"] = ""


def play_mac_audio(file_path, title=None, duration=0.0, source="audio_library"):
    """
    Memutar berkas audio di speaker Mac mini menggunakan afplay.
    Secara otomatis menghentikan pemutaran sebelumnya agar TIDAK PERNAH OVERLAP.
    """
    global _active_proc, _playback_info
    if not file_path or not os.path.exists(file_path):
        return {"status": "error", "message": "Berkas audio tidak ditemukan"}

    with _lock:
        # 1. Hentikan suara yang sedang aktif sebelumnya agar tidak bertabrakan (Anti-Overlap)
        if _active_proc:
            try:
                _active_proc.terminate()
                _active_proc.kill()
            except Exception:
                pass
            _active_proc = None

        try:
            subprocess.run(["pkill", "-9", "-x", "afplay"], timeout=1, capture_output=True)
            subprocess.run(["pkill", "-9", "-x", "say"], timeout=1, capture_output=True)
        except Exception:
            pass

        time.sleep(0.05)

        # 2. Mulai pemutaran baru
        try:
            proc = subprocess.Popen(["afplay", file_path])
            _active_proc = proc
            display_title = title or os.path.basename(file_path)
            _playback_info = {
                "is_playing": True,
                "title": display_title,
                "file_path": file_path,
                "source": source,
                "started_at": time.time(),
                "duration": float(duration or 0.0),
            }
            # Pantau selesainya proses secara otomatis
            t = threading.Thread(target=_watcher_thread, args=(proc, file_path), daemon=True)
            t.start()
            return {
                "status": "success",
                "message": f'Memutar "{display_title}" di speaker Mac mini.',
            }
        except Exception as e:
            return {"status": "error", "message": f"Gagal memutar di Mac: {str(e)}"}


def get_mac_playback_status():
    """Mengembalikan status langsung pemutaran audio di speaker Mac mini."""
    global _active_proc, _playback_info
    with _lock:
        if _playback_info["is_playing"]:
            # Cek apakah proses masih benar-benar hidup
            if _active_proc:
                ret = _active_proc.poll()
                if ret is not None:
                    _active_proc = None
                    _playback_info["is_playing"] = False
                    _playback_info["title"] = ""
            else:
                # Verifikasi via pgrep
                try:
                    res = subprocess.run(["pgrep", "-x", "afplay"], capture_output=True, timeout=1)
                    if res.returncode != 0:
                        _playback_info["is_playing"] = False
                        _playback_info["title"] = ""
                except Exception:
                    pass

        now = time.time()
        is_p = _playback_info["is_playing"]
        st = _playback_info["started_at"]
        elapsed = round(now - st, 1) if is_p and st > 0 else 0.0

        return {
            "is_playing": is_p,
            "title": _playback_info["title"],
            "source": _playback_info["source"],
            "started_at": _playback_info["started_at"],
            "duration": _playback_info["duration"],
            "elapsed_seconds": elapsed,
        }


_ask_gh_gen = 0
_ask_gh_lock = threading.Lock()


def ask_google_home_via_mac(command_text, hotword="Hey Google"):
    """
    Memicu Google Home di ruangan yang sama lewat speaker Mac (ANTI-OVERLAP ketat):
    - Mendukung hotword "Hey Google" maupun "OK Google".
    - Jika perintah baru masuk saat yang lama masih berjalan/jeda, yang lama langsung dibatalkan.
    """
    global _ask_gh_gen
    import re

    cmd_raw = str(command_text or "").strip()
    if not cmd_raw:
        return {"status": "error", "message": "Perintah tidak boleh kosong."}

    # Deteksi otomatis jika user sudah mengetik "ok google" / "hey google" di depan
    low = cmd_raw.lower()
    if low.startswith("ok google") or low.startswith("oke google"):
        hotword = "OK Google"
        cmd_clean = re.sub(r"^(?:ok|oke)\s+google[\s,.:;-]*", "", cmd_raw, flags=re.I).strip()
    elif low.startswith("hey google") or low.startswith("hai google"):
        hotword = "Hey Google"
        cmd_clean = re.sub(r"^(?:hey|hai)\s+google[\s,.:;-]*", "", cmd_raw, flags=re.I).strip()
    else:
        cmd_clean = cmd_raw

    with _ask_gh_lock:
        _ask_gh_gen += 1
        my_gen = _ask_gh_gen

    # Hentikan seketika audio Mac sebelumnya agar tidak pernah overlap
    stop_mac_audio()

    def _worker():
        orig_vol = None
        try:
            v_out = subprocess.run(
                ["osascript", "-e", "output volume of (get volume settings)"],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
            if v_out.isdigit():
                orig_vol = int(v_out)
                if orig_vol < 75:
                    subprocess.run(["osascript", "-e", "set volume output volume 75"], timeout=3)
        except Exception:
            pass

        try:
            if my_gen != _ask_gh_gen:
                return
            stop_mac_audio()
            time.sleep(0.1)
            if my_gen != _ask_gh_gen:
                return

            # 1. Ucapkan hotword ("Hey Google" atau "OK Google") dengan suara English jernih
            proc = subprocess.run(["say", "-v", "Samantha", "-r", "160", hotword], capture_output=True, timeout=6)
            if proc.returncode != 0 and my_gen == _ask_gh_gen:
                subprocess.run(["say", "-r", "160", hotword], capture_output=True, timeout=6)

            # 2. Jeda 1.5 detik bertahap (dicek tiap 0.1s agar batal instan jika ada perintah baru)
            for _ in range(15):
                if my_gen != _ask_gh_gen:
                    return
                time.sleep(0.1)

            if not cmd_clean or my_gen != _ask_gh_gen:
                return

            # 3. Ucapkan perintah Bahasa Indonesia melalui play_mac_audio (terpantau Live Monitor & anti-overlap)
            import indonesian_tts
            audio_path = indonesian_tts.generate_indonesian_audio(cmd_clean, voice_type="wanita1")
            if my_gen != _ask_gh_gen:
                return
            if audio_path and os.path.exists(audio_path):
                play_mac_audio(audio_path, title=f'{hotword}: {cmd_clean[:30]}', source="ask_gh")
                # Tunggu selesai sebelum kembalikan volume
                for _ in range(200):
                    if my_gen != _ask_gh_gen or not get_mac_playback_status().get("is_playing"):
                        break
                    time.sleep(0.15)
            else:
                if my_gen == _ask_gh_gen:
                    subprocess.run(["say", "-v", "Damayanti", cmd_clean], capture_output=True, timeout=20)
        except Exception:
            pass
        finally:
            if my_gen == _ask_gh_gen and orig_vol is not None and orig_vol < 75:
                try:
                    subprocess.run(["osascript", "-e", f"set volume output volume {orig_vol}"], timeout=3)
                except Exception:
                    pass

    threading.Thread(target=_worker, daemon=True).start()
    return {
        "status": "success",
        "message": f'🗣️ Mac mengucapkan "{hotword}" (jeda 1.5s) → "{cmd_clean}"',
    }
