#!/usr/bin/env python3
"""
Modul Keamanan & Alarm Darurat (Emergency Siren & Security Tools).
Menyediakan:
  1. Alarm Sirene Darurat (Volume 100% + Suara Sirene SOS + Ucapkan Peringatan + Screenshot).
  2. Kunci Layar Mac Instan (Lock Screen).
"""

import os
import subprocess
import threading
import time

import indonesian_tts


def lock_mac_screen():
    """Mengunci layar Mac mini seketika (mengaktifkan ScreenSaver ber-password)."""
    try:
        subprocess.run(["open", "-a", "ScreenSaver"], timeout=2)
        return True
    except Exception:
        try:
            subprocess.run(["pmset", "displaysleepnow"], timeout=2)
            return True
        except Exception:
            return False


def _play_siren_sounds():
    """Memutar suara peringatan keras berulang kali."""
    sound_path = "/System/Library/Sounds/Sosumi.aiff"
    if not os.path.exists(sound_path):
        sound_path = "/System/Library/Sounds/Glass.aiff"

    for _ in range(5):
        try:
            subprocess.run(["afplay", sound_path], timeout=2)
            time.sleep(0.1)
        except Exception:
            pass


def trigger_emergency_siren():
    """
    Menyalakan alarm darurat anti-maling / pencarian perangkat:
    1. Memaksa volume Mac mini menjadi 100% dan membuka Mute.
    2. Memutar efek suara sirene darurat.
    3. Mengucapkan suara peringatan darurat Bahasa Indonesia dengan lantang.
    4. Mengambil foto screenshot layar Mac saat alarm berbunyi.
    """
    # 1. Maksimalkan Volume Mac ke 100%
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                "set volume output volume 100 without output muted",
            ],
            timeout=2,
        )
    except Exception:
        pass

    # 2. Bunyikan efek suara sirene di background thread
    threading.Thread(target=_play_siren_sounds, daemon=True).start()

    # 3. Ucapkan suara peringatan darurat
    warning_text = "Perhatian! Alarm darurat diaktifkan dari jarak jauh pada komputer Mac ini!"
    try:
        indonesian_tts.speak_indonesian(warning_text)
    except Exception:
        pass

    # 4. Ambil screenshot bukti
    screen_path = "/tmp/emergency_capture.jpg"
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "jpg", screen_path],
            timeout=4,
        )
    except Exception:
        pass

    return {
        "status": "success",
        "message": "Alarm darurat berhasil diaktifkan dengan volume maksimal!",
        "screenshot": screen_path if os.path.exists(screen_path) else None,
    }


if __name__ == "__main__":
    print("Menguji modul emergency...")
    res = lock_mac_screen()
    print("Lock result:", res)
