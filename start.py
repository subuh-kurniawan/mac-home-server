#!/usr/bin/env python3
"""
Master Launcher untuk Mac Remote Command Center.
Menjalankan Server Web HTTPS dan Telegram Bot secara bersamaan dalam 1 perintah.
Menangani shutdown Ctrl+C secara rapi untuk kedua proses.
"""

import os
import signal
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(BASE_DIR, "server.py")
BOT_SCRIPT = os.path.join(BASE_DIR, "telegram_bot.py")

print("=" * 65)
print("🚀  MEMULAI MAC REMOTE WORKSPACE & TELEGRAM BOT")
print("=" * 65)

# Cek apakah layanan latar belakang (launchd) sudah aktif; jika ya, reload bersih agar tidak bentrok port 8443
LA_DIR = os.path.expanduser("~/Library/LaunchAgents")
PLISTS = [
    os.path.join(LA_DIR, "com.subuh.httpsserver.plist"),
    os.path.join(LA_DIR, "com.subuh.telegrambot.plist"),
]
try:
    chk = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=3)
    if "com.subuh.httpsserver" in chk.stdout or "com.subuh.telegrambot" in chk.stdout:
        print("ℹ️  Layanan latar belakang (launchd) sudah aktif — memuat ulang layanan...")
        for pl in PLISTS:
            if os.path.exists(pl):
                subprocess.run(["launchctl", "unload", pl], capture_output=True)
                subprocess.run(["launchctl", "load", pl], capture_output=True)
        time.sleep(1.5)
        print("=" * 65)
        print("✅ Layanan berhasil dimuat ulang di latar belakang!")
        print("• Web Dashboard : https://10.66.66.4:8443/")
        print("• Telegram Bot  : Aktif menerima perintah chat")
        print("=" * 65)
        sys.exit(0)
except Exception:
    pass

# 1. Jalankan Server Web HTTPS
p_server = subprocess.Popen([sys.executable, SERVER_SCRIPT])
time.sleep(1)

# 2. Jalankan Bot Telegram
p_bot = subprocess.Popen([sys.executable, BOT_SCRIPT])

print("=" * 65)
print("💡 Keduanya sekarang sedang berjalan bersamaan!")
print("• Web Dashboard : https://10.66.66.4:8443/")
print("• Telegram Bot  : Aktif menerima perintah chat")
print("Tekan Ctrl+C untuk mematikan kedua layanan sekaligus.")
print("=" * 65)


def shutdown(sig, frame):
    print("\n\n[!] Sedang mematikan Server HTTPS dan Bot Telegram...")
    try:
        p_bot.terminate()
    except Exception:
        pass
    try:
        p_server.terminate()
    except Exception:
        pass

    try:
        p_bot.wait(timeout=3)
    except Exception:
        p_bot.kill()

    try:
        p_server.wait(timeout=3)
    except Exception:
        p_server.kill()

    print("[✓] Kedua layanan berhasil dimatikan dengan aman.")
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

try:
    p_server.wait()
    p_bot.wait()
except KeyboardInterrupt:
    shutdown(None, None)
