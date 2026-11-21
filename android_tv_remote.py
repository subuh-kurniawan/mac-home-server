#!/usr/bin/env python3
"""
Android TV Remote Protocol v2 Controller (Port 6466 / 6467).
Mendukung pairing 1x (PIN 6 digit di layar TV), lalu kontrol penuh:
Power, D-Pad (Atas/Bawah/Kiri/Kanan/OK), Back, Home, Volume, Mute, & Buka Aplikasi.
"""

import asyncio
import os
import threading
import time

import cast_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CERT_FILE = os.path.join(BASE_DIR, "tv_remote_cert.pem")
KEY_FILE = os.path.join(BASE_DIR, "tv_remote_key.pem")
PAIRED_FLAG = os.path.join(BASE_DIR, "tv_remote_paired.json")

_loop = None
_loop_thread = None
_remote = None
_remote_host = None
_pairing_active = False
_lock = threading.Lock()

APP_LINKS = {
    "youtube": "https://www.youtube.com",
    "netflix": "https://www.netflix.com/title",
    "spotify": "spotify:",
    "disney": "https://www.disneyplus.com",
    "prime": "https://app.primevideo.com",
}


def is_installed():
    try:
        import androidtvremote2  # noqa: F401
        return True
    except ImportError:
        return False


def _ensure_loop():
    global _loop, _loop_thread
    if _loop is not None and _loop.is_running():
        return _loop
    with _lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
        _loop_thread.start()
        return _loop


def _run_coro(coro, timeout=12.0):
    loop = _ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


def _get_tv_host(host=None):
    if host:
        return host
    info = cast_manager.get_cast_host_tuple("tv")
    return info[0]


def is_paired():
    return os.path.isfile(CERT_FILE) and os.path.isfile(KEY_FILE) and os.path.isfile(PAIRED_FLAG)


def _mark_paired(host):
    import json
    with open(PAIRED_FLAG, "w") as f:
        json.dump({"host": host, "paired_at": time.strftime("%Y-%m-%d %H:%M:%S")}, f)


async def _async_ensure_remote(host):
    global _remote, _remote_host
    from androidtvremote2 import AndroidTVRemote

    if _remote is not None and _remote_host == host:
        return _remote

    if _remote is not None:
        try:
            _remote.disconnect()
        except Exception:
            pass

    r = AndroidTVRemote("Mac Remote", CERT_FILE, KEY_FILE, host)
    await r.async_generate_cert_if_missing()
    _remote = r
    _remote_host = host
    return r


def start_pairing(host=None):
    """Memulai pairing Android TV (PIN 6 karakter akan muncul di layar TV)."""
    global _pairing_active
    if not is_installed():
        return {"status": "error", "message": "Pustaka androidtvremote2 belum terpasang."}

    tv_host = _get_tv_host(host)

    async def _do():
        r = await _async_ensure_remote(tv_host)
        await r.async_start_pairing()

    try:
        _run_coro(_do(), timeout=12.0)
        _pairing_active = True
        return {
            "status": "success",
            "host": tv_host,
            "message": f"PIN pairing telah ditampilkan di layar TV ({tv_host})! Masukkan 6 kode yang muncul di TV.",
        }
    except Exception as e:
        return {"status": "error", "message": f"Gagal memulai pairing TV ({tv_host}): {str(e)}"}


def finish_pairing(pin_code, host=None):
    """Menyelesaikan pairing dengan PIN 6 karakter dari layar TV."""
    global _pairing_active
    if not is_installed():
        return {"status": "error", "message": "Pustaka androidtvremote2 belum terpasang."}

    pin = str(pin_code or "").strip().upper()
    if len(pin) < 4:
        return {"status": "error", "message": "Kode PIN tidak valid."}

    tv_host = _get_tv_host(host)

    async def _do():
        r = await _async_ensure_remote(tv_host)
        await r.async_finish_pairing(pin)
        await r.async_connect()

    try:
        _run_coro(_do(), timeout=15.0)
        _pairing_active = False
        _mark_paired(tv_host)
        return {
            "status": "success",
            "host": tv_host,
            "message": f"✅ Pairing TV ({tv_host}) BERHASIL! Remote fisik TV sekarang siap digunakan.",
        }
    except Exception as e:
        return {"status": "error", "message": f"Pairing gagal (pastikan PIN benar & belum expired): {str(e)}"}


async def _async_ensure_connected(host):
    r = await _async_ensure_remote(host)
    if not getattr(r, "is_connected", False):
        await r.async_connect()
    return r


def send_key(keycode, host=None):
    """Mengirim tombol remote ke Android TV (POWER, DPAD_UP/DOWN/LEFT/RIGHT/CENTER, BACK, HOME, VOLUME_UP/DOWN, MUTE)."""
    if not is_installed():
        return {"status": "error", "message": "androidtvremote2 belum terpasang."}
    if not is_paired():
        return {"status": "need_pair", "message": "TV belum di-pairing. Klik tombol 🔗 Pairing TV dulu!"}

    tv_host = _get_tv_host(host)

    async def _do():
        r = await _async_ensure_connected(tv_host)
        r.send_key_command(keycode)

    try:
        _run_coro(_do(), timeout=8.0)
        return {"status": "success", "key": keycode, "message": f"Tombol {keycode} terkirim ke TV."}
    except Exception as e:
        return {"status": "error", "message": f"Gagal kirim tombol {keycode}: {str(e)}"}


def launch_app(app_key, host=None):
    """Membuka aplikasi di Android TV (youtube, netflix, spotify, disney, prime, atau URL/package)."""
    if not is_installed():
        return {"status": "error", "message": "androidtvremote2 belum terpasang."}
    if not is_paired():
        return {"status": "need_pair", "message": "TV belum di-pairing. Klik tombol 🔗 Pairing TV dulu!"}

    tv_host = _get_tv_host(host)
    target = APP_LINKS.get(str(app_key).lower(), str(app_key))

    async def _do():
        r = await _async_ensure_connected(tv_host)
        r.send_launch_app_command(target)

    try:
        _run_coro(_do(), timeout=8.0)
        return {"status": "success", "app": app_key, "message": f"Membuka {app_key} di TV."}
    except Exception as e:
        return {"status": "error", "message": f"Gagal buka aplikasi {app_key}: {str(e)}"}
