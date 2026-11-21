#!/usr/bin/env python3
"""
Telegram Bot Remote Controller untuk Mac.
Mengendalikan Mac mini langsung dari chat Telegram tanpa perlu membuka browser.
Fitur:
  - /status : Cek kondisi Mac (Disk, Uptime, Hostname)
  - /screen : Kirim screenshot layar Mac ke chat Telegram
  - /say <pesan> : Mac mengucapkan kata-kata lewat speaker
  - /volume <0-100> : Atur volume speaker Mac
  - /open <app / url> : Buka aplikasi atau link di layar Mac
  - /cmd <perintah> : Jalankan perintah terminal dari chat
  - /sleep : Matikan / kunci layar Mac
  - Kirim File / Foto : Otomatis tersimpan ke ~/https-server/shared_files/
  - /files : Lihat daftar file di shared_files
  - /get <nama_file> : Download file dari Mac langsung ke chat Telegram
"""

import email
import html
import json
import mimetypes
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import android_tv_remote
import google_assistant_client
import quick_action_runner

# Konfigurasi SSL untuk mencegah error CERTIFICATE_VERIFY_FAILED di macOS
def get_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl._create_unverified_context()
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

SSL_CTX = get_ssl_context()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "bot_config.json")
SHARED_DIR = os.path.join(BASE_DIR, "shared_files")
os.makedirs(SHARED_DIR, exist_ok=True)

import smart_reminders
import db_manager
import cast_manager
import mac_player

def on_schedule_fired_notify_tg(item):
    """Kirim notifikasi otomatis ke chat Telegram saat jadwal suara berbunyi."""
    try:
        cfg = load_config()
        allowed = cfg.get("allowed_chat_ids", [])
        token = cfg.get("token", "")
        if not allowed or not token:
            return
        label = html.escape(item.get("label", "Pengumuman"))
        tgt = html.escape(item.get("target_display", item.get("target", "both")))
        daily_tag = " 🔁 <i>(Pengulangan Harian)</i>" if item.get("is_daily") else ""
        mode = item.get("mode", "tts")

        if mode == "audio":
            aud_name = html.escape(item.get("audio_name") or item.get("audio_filename") or "File Audio")
            content_line = f"🎵 <b>File Audio:</b> {aud_name}"
        else:
            msg = html.escape(item.get("message", ""))
            voice = html.escape(item.get("voice_display", item.get("voice_type", "wanita1")))
            content_line = f"📢 <b>Pesan:</b> \"<i>{msg}</i>\"\n🗣️ <b>Suara:</b> {voice}"

        text = (
            f"⏰ <b>JADWAL PENGUMUMAN BERBUNYI!</b>{daily_tag}\n\n"
            f"📌 <b>Judul:</b> {label}\n"
            f"{content_line}\n"
            f"🎯 <b>Perangkat:</b> {tgt}"
        )
        for cid in allowed:
            try:
                send_message(token, cid, text)
            except Exception:
                pass
    except Exception:
        pass

smart_reminders.register_trigger_callback(on_schedule_fired_notify_tg)


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"token": "", "allowed_chat_ids": []}


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def tg_request(token, method, data=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    headers = {"Content-Type": "application/json"}
    payload = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=35, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_message(token, chat_id, text, reply_markup=None):
    # Potong jika lebih dari batas Telegram (4096 karakter)
    if len(text) > 4000:
        text = text[:3900] + "\n\n... (output dipotong karena terlalu panjang)"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
    return tg_request(
        token,
        "sendMessage",
        payload,
    )


def edit_message_text(token, chat_id, message_id, text, reply_markup=None):
    if len(text) > 4000:
        text = text[:3900]
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
    return tg_request(token, "editMessageText", payload)


def answer_callback_query(token, callback_query_id, text=None, show_alert=False):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    return tg_request(token, "answerCallbackQuery", payload)


def send_file_multipart(token, chat_id, endpoint, field_name, file_path, caption="", reply_markup=None):
    url = f"https://api.telegram.org/bot{token}/{endpoint}"
    boundary = f"----TelegramBoundary{uuid.uuid4().hex}"

    filename = os.path.basename(file_path)
    mime_type = (
        mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    )

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    body = bytearray()

    # chat_id field
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()
    )

    # caption field
    if caption:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode()
        )

    # reply_markup field
    if reply_markup is not None:
        rm_str = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="reply_markup"\r\n\r\n{rm_str}\r\n'.encode()
        )

    # file field
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
    )
    body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode())
    body.extend(file_bytes)
    body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode())

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    req = urllib.request.Request(url, data=bytes(body), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def format_size(bytes_num):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} PB"


import zipfile
from indonesian_tts import speak_indonesian, generate_indonesian_audio
import cast_manager
import interactive_popup
import threading
import system_telemetry
import network_radar
import smart_reminders
import emergency_tools

NOTES_FILE = os.path.join(SHARED_DIR, "notes.txt")


def on_reminder_fired(rem):
    """Callback otomatis ketika pengingat pintar berbunyi."""
    cfg = load_config()
    tok = cfg.get("token")
    chats = cfg.get("allowed_chat_ids", [])
    if tok and chats:
        for cid in chats:
            try:
                send_message(
                    tok,
                    cid,
                    f"⏰ <b>PENGINGAT PINTAR BERBUNYI!</b>\n\n"
                    f"🔔 <b>\"{html.escape(rem.get('message', ''))}\"</b>\n\n"
                    f"📢 <i>Suara pengumuman telah disiarkan ke Google Home dan speaker Mac mini.</i>",
                )
            except Exception:
                pass


smart_reminders.register_trigger_callback(on_reminder_fired)


def handle_interactive_chat_async(token, chat_id, from_user, message_text):
    """Menampilkan dialog input teks di layar Mac dan mengirim balasan ke Telegram secara async."""
    def _worker():
        status, reply = interactive_popup.show_interactive_popup(
            f"Telegram ({from_user})", message_text, timeout_seconds=120
        )
        if status == "REPLY" and reply:
            send_message(
                token,
                chat_id,
                f"💬 <b>Balasan dari Layar Mac mini:</b>\n\n\"<b>{html.escape(reply)}</b>\"\n\n<i>(Pengguna di depan komputer baru saja membalas pesan Anda)</i>",
            )
        elif status == "CLOSED":
            send_message(
                token,
                chat_id,
                "ℹ️ <i>Kotak dialog pesan di Mac ditutup tanpa ada balasan teks.</i>",
            )
        elif status == "TIMEOUT":
            send_message(
                token,
                chat_id,
                "⏳ <i>Waktu tunggu habis (2 menit). Tidak ada balasan dari layar Mac.</i>",
            )
        elif status == "ERROR":
            send_message(
                token,
                chat_id,
                f"❌ <i>Gagal menampilkan pop-up di Mac: {html.escape(reply or '')}</i>",
            )

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def send_desktop_notification(title, message):
    try:
        clean_msg = message.replace("\\", "\\\\").replace('"', '\\"')
        clean_title = title.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{clean_msg}" with title "{clean_title}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], timeout=3)
        return True
    except Exception:
        return False


def control_music(action="playpause"):
    try:
        if action == "playpause":
            script = 'tell application "Music" to playpause'
        elif action == "next":
            script = 'tell application "Music" to next track'
        elif action == "prev":
            script = 'tell application "Music" to previous track'
        elif action == "info":
            script = """
            tell application "Music"
                if player state is playing then
                    return (name of current track) & " - " & (artist of current track)
                else
                    return "Tidak ada lagu yang sedang diputar"
                end if
            end tell
            """
        proc = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=3
        )
        return proc.stdout.strip() or "OK"
    except Exception as e:
        return str(e)


# ================= CANGGIH UI: KEYBOARDS & INTERACTIVE CALLBACKS =================
DEFAULT_VOICE_TARGET = "gh"  # "gh" (Google Home), "mac" (Speaker Mac), "both" (Keduanya)

def get_persistent_menu_keyboard():
    """Menu tombol cepat permanen di bagian bawah layar Telegram."""
    return {
        "keyboard": [
            [{"text": "📸 Screenshot Layar"}, {"text": "📊 Status Mac"}],
            [{"text": "📢 Google Home"}, {"text": "📺 Cast TV / YT"}],
            [{"text": "🔊 Speaker Mac"}, {"text": "🎵 Pustaka Audio"}],
            [{"text": "⏰ Jadwal Suara"}, {"text": "📻 Now Playing"}],
            [{"text": "🛑 Stop Audio"}, {"text": "🎛️ Buka Dashboard Menu"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


def get_dashboard_inline_keyboard():
    """Tombol-tombol inline canggih pada Control Deck Dashboard."""
    return {
        "inline_keyboard": [
            [
                {"text": "📸 Ambil Screenshot", "callback_data": "act_screen"},
                {"text": "📊 Kondisi Mac", "callback_data": "act_status"}
            ],
            [
                {"text": "🛑 Stop Audio", "callback_data": "stop_all"},
                {"text": "📻 Now Playing", "callback_data": "act_now_playing"}
            ],
            [
                {"text": "📢 Google Home", "callback_data": "act_gh_menu"},
                {"text": "📺 Cast TV / YT", "callback_data": "act_tv_menu"}
            ],
            [
                {"text": "⚡ Quick Action", "callback_data": "act_qa_menu"},
                {"text": "🎮 Remote TV", "callback_data": "act_tv_remote"}
            ],
            [
                {"text": "🌙 Mode Skenario", "callback_data": "act_scenarios"},
                {"text": "🎵 Pustaka Media", "callback_data": "act_audio_menu"}
            ],
            [
                {"text": "🔊 Speaker Mac", "callback_data": "act_mac_menu"},
                {"text": "⏰ Manajemen Jadwal", "callback_data": "act_schedule_menu"}
            ],
            [
                {"text": "🎯 Target Suara", "callback_data": "act_mode_menu"},
                {"text": "💻 Kontrol Mac+", "callback_data": "act_mac_tools"}
            ],
            [
                {"text": "🎵 Player Musik", "callback_data": "act_music_menu"},
                {"text": "💬 Intercom Pop-Up", "callback_data": "act_chat_info"}
            ],
            [
                {"text": "🔍 Scan Wi-Fi Cast", "callback_data": "act_scan_cast"},
                {"text": "🌙 Sleep / Kunci Layar", "callback_data": "act_sleep"}
            ]
        ]
    }


def build_now_playing_content():
    """Membangun tampilan live monitor dan tombol kontrol multi-perangkat (Mac + Google Home + Android TV)."""
    mac_st = mac_player.get_mac_playback_status()
    cast_list = cast_manager.get_all_cast_playback_statuses()

    # Format status Mac mini
    if mac_st.get("is_playing"):
        elp = int(mac_st.get("elapsed_seconds", 0))
        m, s = divmod(elp, 60)
        mac_line = f"🟢 <b>SEDANG MEMUTAR:</b> \"{html.escape(mac_st.get('title', 'Audio'))}\"\n   ⏱️ Berjalan: <code>{m:02d}:{s:02d}</code>"
    else:
        mac_line = "⚪ <i>Tidak ada audio berputar (Idle)</i>"

    cast_blocks = []
    buttons = []
    if mac_st.get("is_playing"):
        buttons.append([{"text": "🛑 Stop Mac", "callback_data": "stop_mac"}])

    for cs in cast_list:
        host = cs.get("host", "")
        dev = html.escape(cs.get("device", host or "Cast"))
        is_tv = (cs.get("cast_type") == "cast") or ("tv" in dev.lower())
        icon = "📺" if is_tv else "📢"
        short_lbl = "TV" if is_tv else "GH"
        vol = cs.get("volume_level")
        muted = bool(cs.get("volume_muted", False))
        vol_str = (
            f"🔇 Muted ({vol}%)" if (muted and vol is not None)
            else (f"🔊 {vol}%" if vol is not None else "🔊 --%")
        )
        if cs.get("is_playing"):
            elp = int(cs.get("elapsed_seconds", 0))
            m, s = divmod(elp, 60)
            pstate = cs.get("player_state") or "PLAYING"
            state_icon = "⏸️ <b>DIJEDA:</b>" if pstate == "PAUSED" else "🟢 <b>CASTING:</b>"
            cast_blocks.append(
                f"{icon} <b>{dev}</b> ({vol_str}):\n"
                f"   {state_icon} \"{html.escape(cs.get('title', 'Media'))}\" (<code>{m:02d}:{s:02d}</code>)"
            )
            is_paused = (pstate == "PAUSED")
            buttons.append([
                {"text": f"{'▶️ Lanjut' if is_paused else '⏸️ Pause'} {short_lbl}", "callback_data": f"gh_media:{host}:playpause"},
                {"text": f"🛑 Stop {short_lbl}", "callback_data": f"stop_gh:{host}"},
            ])
        else:
            idle_txt = "Standby/Offline" if cs.get("live") is False else "Idle"
            cast_blocks.append(f"{icon} <b>{dev}</b>: ⚪ <i>{idle_txt}</i> | {vol_str}")

        buttons.append([
            {"text": f"🔉 {short_lbl} -10%", "callback_data": f"gh_vol:{host}:-10"},
            {"text": f"{'🔊 Unmute' if muted else '🔇 Mute'} {short_lbl}", "callback_data": f"gh_mute:{host}:toggle"},
            {"text": f"🔊 {short_lbl} +10%", "callback_data": f"gh_vol:{host}:+10"},
        ])

    text = (
        "📻 <b>MONITOR PEMUTARAN AUDIO & TV AKTIF</b>\n"
        "------------------------------------\n\n"
        f"🔊 <b>Speaker Mac mini (afplay):</b>\n{mac_line}\n\n"
        + "\n\n".join(cast_blocks)
        + "\n\n<i>Gunakan tombol di bawah untuk mengatur volume, jeda, atau stop:</i>"
    )

    buttons.append([
        {"text": "🛑 Hentikan Semua Suara", "callback_data": "stop_all"},
        {"text": "🔄 Refresh", "callback_data": "act_now_playing"}
    ])
    buttons.append([{"text": "🎛️ Buka Dashboard Menu", "callback_data": "act_dashboard"}])

    return text, {"inline_keyboard": buttons}


def get_schedule_inline_keyboard(schedules):
    """Inline keyboard untuk manajemen jadwal suara langsung di chat."""
    rows = []
    for item in schedules[:6]:  # max 6 items per view
        sid = item["id"]
        lbl = item.get("label", "Jadwal")
        if len(lbl) > 13:
            lbl = lbl[:12] + ".."
        rows.append([
            {"text": f"⚡ Bunyikan: {lbl}", "callback_data": f"test_sch:{sid}"},
            {"text": f"🗑️ Hapus: {lbl}", "callback_data": f"del_sch:{sid}"}
        ])
    rows.append([
        {"text": "➕ Buat Jadwal Baru", "callback_data": "act_add_sch_prompt"},
        {"text": "🎵 Pustaka Audio", "callback_data": "act_audio_menu"}
    ])
    rows.append([
        {"text": "🔄 Refresh Jadwal", "callback_data": "act_schedule_menu"},
        {"text": "🎛️ Dashboard", "callback_data": "act_dashboard"}
    ])
    return {"inline_keyboard": rows}


def build_schedule_menu_content():
    """Membangun teks ringkasan jadwal dan tombol inline keyboard."""
    schedules = smart_reminders.list_schedules()
    if not schedules:
        text = (
            "⏰ <b>MANAJEMEN JADWAL SUARA (CAST & SAY - SQLITE):</b>\n\n"
            "Saat ini <b>belum ada jadwal suara aktif</b> di database.\n\n"
            "💡 <i>Mendukung 2 Mode Suara & Waktu Fleksibel:</i>\n"
            "1. <b>AI TTS:</b> <code>/schedule 10m Makan siang siap!</code>\n"
            "2. <b>File Audio:</b> <code>/schedule 15m audio:Bel</code> atau pilih di <b>🎵 Pustaka Audio</b>\n"
            "3. <b>Pilihan Tanggal Bebas:</b>\n"
            "   • <code>/schedule 2026-11-22 08:30 both Sholat Dhuha</code>\n"
            "   • <code>/schedule besok 07:00 Bangun pagi</code>\n"
            "   • <code>/schedule daily 12:30 Waktunya istirahat</code>\n\n"
            "Klik tombol di bawah untuk menambah jadwal baru atau membuka pustaka audio."
        )
        markup = {
            "inline_keyboard": [
                [{"text": "➕ Buat Jadwal Baru", "callback_data": "act_add_sch_prompt"}],
                [{"text": "🎵 Buka Pustaka Audio", "callback_data": "act_audio_menu"}],
                [{"text": "🎛️ Kembali ke Dashboard", "callback_data": "act_dashboard"}]
            ]
        }
        return text, markup

    lines = [f"⏰ <b>DAFTAR JADWAL SUARA AKTIF ({len(schedules)}):</b>\n"]
    for idx, it in enumerate(schedules, 1):
        daily_str = " 🔁 <i>(Harian)</i>" if it.get("is_daily") else ""
        is_audio = it.get("mode") == "audio"
        mode_tag = "🎵 [AUDIO]" if is_audio else "🗣️ [TTS]"
        content_desc = html.escape(it.get("audio_name") or "File Audio") if is_audio else f"\"{html.escape(it.get('message', ''))}\""
        lines.append(
            f"<b>{idx}. {mode_tag} {html.escape(it.get('label', 'Pengumuman'))}</b>{daily_str}\n"
            f"   ⏱️ <b>{it.get('remaining_readable')}</b> (Pukul {it.get('target_datetime') or it.get('target_readable')})\n"
            f"   📢 {content_desc}\n"
            f"   🎯 {it.get('target_display')}\n"
        )
    lines.append("<i>Pilih aksi di tombol bawah untuk tes bunyi atau menghapus jadwal:</i>")
    text = "\n".join(lines)
    markup = get_schedule_inline_keyboard(schedules)
    return text, markup


def get_audio_library_inline_keyboard(page_items, page=1, total_pages=1, filter_type="all"):
    """Inline keyboard berhalaman (pagination + filter) untuk Pustaka Audio & Video di Telegram."""
    rows = []
    # Baris Filter Cepat
    f_all = "🔘 Semua" if filter_type == "all" else "Semua"
    f_aud = "🔘 🎵 Audio" if filter_type == "audio" else "🎵 Audio"
    f_vid = "🔘 🎬 Video" if filter_type == "video" else "🎬 Video"
    rows.append([
        {"text": f_all, "callback_data": "aud_page:all:1"},
        {"text": f_aud, "callback_data": "aud_page:audio:1"},
        {"text": f_vid, "callback_data": "aud_page:video:1"},
    ])

    for a in page_items:
        aid = a["id"]
        is_vid = (a.get("media_type") == "video") or str(a.get("mime_type", "")).startswith("video/")
        lbl = a["name"]
        if len(lbl) > 10:
            lbl = lbl[:9] + ".."
        if is_vid:
            rows.append([
                {"text": f"📺 TV: {lbl}", "callback_data": f"p_aud_tv:{aid}"},
                {"text": "📢 Speaker", "callback_data": f"p_aud_gh:{aid}"},
                {"text": "🗑️", "callback_data": f"del_aud:{aid}"}
            ])
        else:
            rows.append([
                {"text": f"📢 {lbl}", "callback_data": f"p_aud_gh:{aid}"},
                {"text": "📺 TV", "callback_data": f"p_aud_tv:{aid}"},
                {"text": "🔊 Mac", "callback_data": f"p_aud_mac:{aid}"},
                {"text": "🗑️", "callback_data": f"del_aud:{aid}"}
            ])

    # Baris Pagination (tampil otomatis jika > 1 halaman)
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append({"text": "◀️ Prev", "callback_data": f"aud_page:{filter_type}:{page - 1}"})
        nav.append({"text": f"📄 {page} / {total_pages}", "callback_data": f"aud_page:{filter_type}:{page}"})
        if page < total_pages:
            nav.append({"text": "Next ▶️", "callback_data": f"aud_page:{filter_type}:{page + 1}"})
        rows.append(nav)

    rows.append([
        {"text": "⏰ Manajemen Jadwal", "callback_data": "act_schedule_menu"},
        {"text": "🔄 Refresh", "callback_data": f"aud_page:{filter_type}:{page}"}
    ])
    rows.append([
        {"text": "🎛️ Kembali ke Dashboard", "callback_data": "act_dashboard"}
    ])
    return {"inline_keyboard": rows}


def build_audio_library_menu_content(page=1, filter_type="all", page_size=5):
    """Membangun teks daftar Pustaka Audio & Video di SQLite dengan pagination & filter."""
    all_items = db_manager.list_audios()
    if not all_items:
        text = (
            "🎵🎬 <b>PUSTAKA AUDIO &amp; VIDEO SERVER (SQLITE):</b>\n\n"
            "Saat ini <b>belum ada file media</b> yang tersimpan di database.\n\n"
            "💡 <b>Cara Menambahkan Media Baru:</b>\n"
            "• Kirim file Audio/Video atau Voice Note ke bot ini dengan caption <code>#simpan Nama</code>.\n"
            "• Atau unggah lewat Web Dashboard di <code>https://10.66.66.4:8443/?tab=ctrl</code>."
        )
        markup = {
            "inline_keyboard": [
                [{"text": "⏰ Manajemen Jadwal", "callback_data": "act_schedule_menu"}],
                [{"text": "🎛️ Kembali ke Dashboard", "callback_data": "act_dashboard"}]
            ]
        }
        return text, markup

    if filter_type in ("audio", "video"):
        filtered = [
            a for a in all_items
            if ((a.get("media_type") == "video") or str(a.get("mime_type", "")).startswith("video/")) == (filter_type == "video")
        ]
    else:
        filter_type = "all"
        filtered = all_items

    total_items = len(filtered)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]

    filter_lbl = "Semua Media" if filter_type == "all" else ("Audio Saja" if filter_type == "audio" else "Video Saja")
    lines = [
        f"🎵🎬 <b>PUSTAKA MEDIA SERVER ({total_items} {filter_lbl})</b>",
        f"<i>Halaman {page} dari {total_pages}</i>\n",
    ]
    if not page_items:
        lines.append("<i>Tidak ada media pada filter ini.</i>\n")
    else:
        for idx, a in enumerate(page_items, start + 1):
            is_vid = (a.get("media_type") == "video") or str(a.get("mime_type", "")).startswith("video/")
            icon = "🎬" if is_vid else "🎵"
            dur = f"⏱️ <code>{a['duration_formatted']}</code> • " if a.get("duration_seconds") else ""
            lines.append(
                f"<b>{idx}. {icon} {html.escape(a['name'])}</b>\n"
                f"   {dur}💾 {a['size_formatted']} • <code>{html.escape(a['original_filename'][:36])}</code>\n"
            )
    lines.append("<i>Pilih tombol di bawah untuk memutar ke TV, Google Home, Mac, atau pindah halaman:</i>")
    text = "\n".join(lines)
    markup = get_audio_library_inline_keyboard(page_items, page=page, total_pages=total_pages, filter_type=filter_type)
    return text, markup


def _voice_target_label(tgt):
    if tgt == "gh":
        return "Google Home"
    if tgt == "tv":
        return "TV Stick Mi"
    if tgt == "mac":
        return "Speaker Mac mini"
    if tgt == "all":
        return "Semua (Mac + Home + TV)"
    return "Keduanya (Mac + Home)"


def get_tv_remote_inline_keyboard():
    """Remote fisik Android TV Stick Mi (Port 6466)."""
    paired = android_tv_remote.is_paired()
    rows = [
        [
            {"text": "⏻ Power", "callback_data": "tvkey:POWER"},
            {"text": "🔇 Mute", "callback_data": "tvkey:MUTE"},
            {"text": "🏠 Home", "callback_data": "tvkey:HOME"},
        ],
        [
            {"text": "⬆️", "callback_data": "tvkey:DPAD_UP"},
        ],
        [
            {"text": "⬅️", "callback_data": "tvkey:DPAD_LEFT"},
            {"text": "🆗 OK", "callback_data": "tvkey:DPAD_CENTER"},
            {"text": "➡️", "callback_data": "tvkey:DPAD_RIGHT"},
        ],
        [
            {"text": "⬇️", "callback_data": "tvkey:DPAD_DOWN"},
        ],
        [
            {"text": "↩️ Back", "callback_data": "tvkey:BACK"},
            {"text": "🔉 Vol-", "callback_data": "tvkey:VOLUME_DOWN"},
            {"text": "🔊 Vol+", "callback_data": "tvkey:VOLUME_UP"},
        ],
        [
            {"text": "▶️ YouTube", "callback_data": "tvapp:youtube"},
            {"text": "🎬 Netflix", "callback_data": "tvapp:netflix"},
            {"text": "🎵 Spotify", "callback_data": "tvapp:spotify"},
        ],
        [
            {"text": "🔗 Pairing Ulang TV" if paired else "🔗 Pairing TV (PIN 6 Digit)", "callback_data": "tv_pair_start"},
            {"text": "🎛️ Dashboard", "callback_data": "act_dashboard"},
        ],
    ]
    return {"inline_keyboard": rows}


def get_scenarios_inline_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🌙 Mode Malam (Stop Semua + Vol 30% + Kunci Mac)", "callback_data": "scen:night"}],
            [{"text": "🚪 Mode Keluar Rumah (Stop TV/Home + Matikan Layar)", "callback_data": "scen:away"}],
            [{"text": "💼 Mode Fokus (Pause Media + Mute Speaker)", "callback_data": "scen:focus"}],
            [{"text": "🎛️ Kembali ke Dashboard", "callback_data": "act_dashboard"}],
        ]
    }


def get_mac_tools_inline_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🔉 Mac -10%", "callback_data": "mac_vol:-10"},
                {"text": "🔇 Mac Mute", "callback_data": "mac_vol:mute"},
                {"text": "🔊 Mac +10%", "callback_data": "mac_vol:+10"},
            ],
            [
                {"text": "📋 Ambil Clipboard Mac", "callback_data": "mac_clip_get"},
                {"text": "✏️ Isi Clipboard Mac", "callback_data": "mac_clip_set"},
            ],
            [
                {"text": "⚙️ Top 5 Proses Mac (Task Manager)", "callback_data": "mac_procs"},
            ],
            [{"text": "🎛️ Kembali ke Dashboard", "callback_data": "act_dashboard"}],
        ]
    }


def get_mode_inline_keyboard():
    """Pilihan tombol inline interaktif untuk mengubah target voice note."""
    tgt = DEFAULT_VOICE_TARGET
    icon_gh = "🔘 " if tgt == "gh" else "⚪ "
    icon_tv = "🔘 " if tgt == "tv" else "⚪ "
    icon_mac = "🔘 " if tgt == "mac" else "⚪ "
    icon_both = "🔘 " if tgt == "both" else "⚪ "
    icon_all = "🔘 " if tgt == "all" else "⚪ "
    return {
        "inline_keyboard": [
            [{"text": f"{icon_gh}📢 Target: Google Home", "callback_data": "set_tgt:gh"}],
            [{"text": f"{icon_tv}📺 Target: TV Stick Mi", "callback_data": "set_tgt:tv"}],
            [{"text": f"{icon_mac}🔊 Target: Speaker Mac mini", "callback_data": "set_tgt:mac"}],
            [{"text": f"{icon_both}📢+🔊 Target: Mac + Google Home", "callback_data": "set_tgt:both"}],
            [{"text": f"{icon_all}🌐 Target: Semua (Mac + Home + TV)", "callback_data": "set_tgt:all"}],
            [{"text": "🎛️ Kembali ke Dashboard", "callback_data": "act_dashboard"}]
        ]
    }


def get_music_inline_keyboard():
    """Tombol player musik interaktif langsung di chat."""
    return {
        "inline_keyboard": [
            [
                {"text": "⏮️ Prev", "callback_data": "music:prev"},
                {"text": "⏯️ Play / Pause", "callback_data": "music:playpause"},
                {"text": "⏭️ Next", "callback_data": "music:next"}
            ],
            [
                {"text": "🔉 Vol -15%", "callback_data": "vol:down"},
                {"text": "🔊 Vol +15%", "callback_data": "vol:up"},
                {"text": "ℹ️ Info Lagu", "callback_data": "music:info"}
            ],
            [{"text": "🎛️ Kembali ke Dashboard", "callback_data": "act_dashboard"}]
        ]
    }


def get_cast_menu_inline_keyboard():
    """Pilihan suara pengumuman Google Home."""
    return {
        "inline_keyboard": [
            [
                {"text": "👩 1. Suara Gadis (Wanita 1)", "callback_data": "cast_sample:wanita1"},
                {"text": "👨 2. Suara Ardi (Pria)", "callback_data": "cast_sample:pria"}
            ],
            [
                {"text": "👧 3. Suara Google ID (Wanita 2)", "callback_data": "cast_sample:wanita2"},
                {"text": "🔍 Scan Wi-Fi Speaker", "callback_data": "act_scan_cast"}
            ],
            [{"text": "🎛️ Kembali ke Dashboard", "callback_data": "act_dashboard"}]
        ]
    }


def get_screen_inline_keyboard():
    """Tombol di bawah foto screenshot."""
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Ambil Screenshot Baru", "callback_data": "act_screen"},
                {"text": "🌙 Kunci Layar", "callback_data": "act_sleep"}
            ],
            [{"text": "🎛️ Buka Dashboard Menu", "callback_data": "act_dashboard"}]
        ]
    }


def get_voice_note_inline_keyboard(clip_path):
    """Tombol interaktif saat menerima voice note untuk disiarkan ke mana saja atau disimpan ke database."""
    fn = os.path.basename(clip_path)
    return {
        "inline_keyboard": [
            [
                {"text": "📢 Google Home", "callback_data": f"play_gh:{fn}"},
                {"text": "📺 TV Stick Mi", "callback_data": f"play_tv:{fn}"},
                {"text": "🔊 Mac", "callback_data": f"play_mac:{fn}"}
            ],
            [
                {"text": "📢+🔊 Mac & Google Home", "callback_data": f"play_both:{fn}"}
            ],
            [
                {"text": "🛑 Stop Audio", "callback_data": "stop_all"},
                {"text": "💾 Simpan ke Database", "callback_data": f"save_to_lib:{fn}"}
            ]
        ]
    }


def get_system_status_text():
    disk = shutil.disk_usage(os.path.expanduser("~"))
    hostname = socket.gethostname()
    plat = f"{platform.system()} {platform.release()} ({platform.machine()})"

    try:
        uptime_proc = subprocess.run(
            ["uptime"], capture_output=True, text=True, timeout=3
        )
        uptime_str = uptime_proc.stdout.strip()
    except Exception:
        uptime_str = "--"

    return (
        f"📊 <b>Kondisi Mac mini Anda:</b>\n\n"
        f"• <b>Komputer:</b> <code>{html.escape(hostname)}</code>\n"
        f"• <b>OS:</b> {html.escape(plat)}\n"
        f"• <b>Sisa Disk:</b> <b>{format_size(disk.free)}</b> dari {format_size(disk.total)}\n"
        f"• <b>Uptime:</b> <code>{html.escape(uptime_str)}</code>\n"
        f"• <b>WireGuard IP:</b> <code>10.66.66.4</code>\n"
        f"• <b>Status Bot:</b> 🟢 <b>Online & Responsif</b>"
    )


def handle_callback_query(token, cb, config):
    """Memproses klik tombol inline interaktif Telegram."""
    global DEFAULT_VOICE_TARGET
    cb_id = cb["id"]
    from_user = cb.get("from", {}).get("first_name", "User")
    chat_id = cb.get("message", {}).get("chat", {}).get("id") or cb.get("from", {}).get("id")
    msg_id = cb.get("message", {}).get("message_id")
    data = cb.get("data", "")

    # Hilangkan spinner loading Telegram
    answer_callback_query(token, cb_id)

    if data == "act_dashboard":
        welcome = (
            "🎛️ <b>MAC MINI COMMAND CENTER</b>\n"
            "Perangkat: <b>Subuh's Mac mini</b> (<code>10.66.66.4</code>)\n"
            "------------------------------------\n"
            "Pilih tombol cepat interaktif di bawah:"
        )
        if msg_id:
            edit_message_text(token, chat_id, msg_id, welcome, reply_markup=get_dashboard_inline_keyboard())
        else:
            send_message(token, chat_id, welcome, reply_markup=get_dashboard_inline_keyboard())

    elif data == "act_screen":
        send_message(token, chat_id, "📸 Mengambil screenshot layar Mac...")
        temp_img = "/tmp/tg_screen.jpg"
        try:
            subprocess.run(["screencapture", "-x", "-t", "jpg", temp_img], timeout=5, check=True)
            if os.path.exists(temp_img):
                send_file_multipart(token, chat_id, "sendPhoto", "photo", temp_img, caption="🖥️ Tampilan layar Mac saat ini", reply_markup=get_screen_inline_keyboard())
                try: os.remove(temp_img)
                except Exception: pass
        except Exception as e:
            send_message(token, chat_id, f"❌ Gagal mengambil screenshot: {str(e)}")

    elif data == "act_status":
        text = get_system_status_text()
        status_markup = {
            "inline_keyboard": [
                [{"text": "🔄 Refresh Status", "callback_data": "act_status"}],
                [{"text": "🎛️ Buka Dashboard", "callback_data": "act_dashboard"}]
            ]
        }
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=status_markup)
        else:
            send_message(token, chat_id, text, reply_markup=status_markup)

    elif data == "act_mode_menu":
        tgt = DEFAULT_VOICE_TARGET
        m_label = _voice_target_label(tgt)
        text = (
            f"🎯 <b>PENGATURAN TARGET SUARA TELEGRAM:</b>\n\n"
            f"Target Aktif Saat Ini: <b>{m_label}</b>\n\n"
            f"Silakan klik tombol di bawah untuk memilih perangkat tujuan saat Anda mengirim Voice Note atau pesan suara:"
        )
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=get_mode_inline_keyboard())
        else:
            send_message(token, chat_id, text, reply_markup=get_mode_inline_keyboard())

    elif data.startswith("set_tgt:"):
        new_tgt = data.split(":", 1)[1]
        DEFAULT_VOICE_TARGET = new_tgt
        m_label = _voice_target_label(new_tgt)
        answer_callback_query(token, cb_id, text=f"Target diubah ke: {m_label}!", show_alert=False)
        text = (
            f"🎯 <b>PENGATURAN TARGET SUARA TELEGRAM:</b>\n\n"
            f"Target Aktif Saat Ini: <b>{m_label}</b> ✅\n\n"
            f"Setiap kali Anda mengirim Voice Note, suara akan otomatis berbunyi di <b>{m_label}</b>!"
        )
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=get_mode_inline_keyboard())

    elif data == "act_music_menu":
        res = control_music("info")
        text = f"🎵 <b>KENDALI PEMUTAR MUSIK MAC:</b>\n\nStatus: <b>{html.escape(res)}</b>"
        send_message(token, chat_id, text, reply_markup=get_music_inline_keyboard())

    elif data.startswith("music:"):
        act = data.split(":", 1)[1]
        res = control_music(act)
        answer_callback_query(token, cb_id, text=f"Musik: {act}")
        if msg_id:
            edit_message_text(token, chat_id, msg_id, f"🎵 <b>KENDALI PEMUTAR MUSIK MAC:</b>\n\nStatus: <b>{html.escape(res)}</b>", reply_markup=get_music_inline_keyboard())

    elif data.startswith("vol:"):
        v_act = data.split(":", 1)[1]
        try:
            curr_v_proc = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True)
            curr_v = int(curr_v_proc.stdout.strip() or 50)
        except Exception:
            curr_v = 50
        new_v = min(100, curr_v + 15) if v_act == "up" else max(0, curr_v - 15)
        subprocess.run(["osascript", "-e", f"set volume output volume {new_v}"], timeout=3)
        answer_callback_query(token, cb_id, text=f"Volume: {new_v}%")
        res = control_music("info")
        if msg_id:
            edit_message_text(token, chat_id, msg_id, f"🎵 <b>KENDALI PEMUTAR MUSIK MAC:</b>\n\nVolume: <b>{new_v}%</b> 🔊\nStatus: <b>{html.escape(res)}</b>", reply_markup=get_music_inline_keyboard())

    elif data == "act_tv_remote":
        paired = android_tv_remote.is_paired()
        st_txt = "🟢 <b>Terhubung (Sudah Pairing)</b>" if paired else "🟡 <b>Belum Pairing</b> — klik <b>🔗 Pairing TV</b> di bawah lalu masukkan PIN 6 digit yang muncul di layar TV."
        txt = f"🎮 <b>REMOTE FISIK ANDROID TV (TV STICK MI)</b>\n\nStatus: {st_txt}"
        if msg_id:
            edit_message_text(token, chat_id, msg_id, txt, reply_markup=get_tv_remote_inline_keyboard())
        else:
            send_message(token, chat_id, txt, reply_markup=get_tv_remote_inline_keyboard())

    elif data == "tv_pair_start":
        answer_callback_query(token, cb_id, text="Memulai pairing ke TV Stick Mi...")
        res = android_tv_remote.start_pairing()
        if res.get("status") == "success":
            force_reply = {
                "force_reply": True,
                "selective": True,
                "input_field_placeholder": "Masukkan 6 kode PIN di layar TV...",
            }
            send_message(
                token,
                chat_id,
                f"🔗 <b>MASUKKAN PIN PAIRING TV:</b>\n\n{html.escape(res.get('message', ''))}\n\n"
                "<i>Langsung balas pesan ini dengan 6 kode huruf/angka yang muncul di layar TV Anda.</i>",
                reply_markup=force_reply,
            )
        else:
            send_message(token, chat_id, f"⚠️ {html.escape(res.get('message', 'Gagal memulai pairing TV'))}")

    elif data.startswith("tvkey:"):
        key = data.split(":", 1)[1].strip()
        res = android_tv_remote.send_key(key)
        answer_callback_query(token, cb_id, text=res.get("message", key)[:180], show_alert=res.get("status") != "success")

    elif data.startswith("tvapp:"):
        app_k = data.split(":", 1)[1].strip()
        res = android_tv_remote.launch_app(app_k)
        answer_callback_query(token, cb_id, text=res.get("message", app_k)[:180], show_alert=res.get("status") != "success")

    elif data == "act_scenarios":
        txt = (
            "⚡ <b>MODE SKENARIO 1-KLIK (SMART HOME)</b>\n\n"
            "Pilih skenario otomatis untuk mengendalikan Mac, Google Home, dan TV Stick Mi sekaligus:"
        )
        if msg_id:
            edit_message_text(token, chat_id, msg_id, txt, reply_markup=get_scenarios_inline_keyboard())
        else:
            send_message(token, chat_id, txt, reply_markup=get_scenarios_inline_keyboard())

    elif data.startswith("scen:"):
        sc_name = data.split(":", 1)[1].strip()
        answer_callback_query(token, cb_id, text=f"Menjalankan mode {sc_name}...")
        def _run_scen():
            notes = []
            if sc_name == "night":
                mac_player.stop_mac_audio()
                cast_manager.stop_google_home_cast("192.168.0.198")
                cast_manager.stop_google_home_cast("tv")
                cast_manager.set_google_home_volume(volume_percent=30, target_device_name="192.168.0.198")
                subprocess.run(["pmset", "displaysleepnow"], timeout=3)
                notes.append("🌙 <b>Mode Malam Aktif:</b>\n• Semua audio & TV dihentikan\n• Volume Google Home → 30%\n• Layar Mac dikunci")
            elif sc_name == "away":
                mac_player.stop_mac_audio()
                cast_manager.stop_google_home_cast("192.168.0.198")
                cast_manager.stop_google_home_cast("tv")
                subprocess.run(["pmset", "displaysleepnow"], timeout=3)
                notes.append("🚪 <b>Mode Keluar Rumah Aktif:</b>\n• TV & Google Home dihentikan\n• Layar Mac dimatikan")
            elif sc_name == "focus":
                mac_player.stop_mac_audio()
                cast_manager.control_google_home_media("pause", target_device_name="tv")
                cast_manager.set_google_home_volume(muted=True, target_device_name="192.168.0.198")
                notes.append("💼 <b>Mode Fokus Aktif:</b>\n• Audio Mac dihentikan & TV dijeda\n• Google Home di-mute")
            send_message(token, chat_id, "\n".join(notes), reply_markup=get_scenarios_inline_keyboard())
        threading.Thread(target=_run_scen, daemon=True).start()

    elif data == "act_mac_tools":
        try:
            v_out = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:
            v_out = "--"
        txt = (
            "💻 <b>KONTROL CEPAT MAC MINI</b>\n\n"
            f"🔊 Volume Mac Saat Ini: <b>{html.escape(v_out)}%</b>\n"
            "Gunakan tombol di bawah untuk Volume Mac, Clipboard Sync, atau Task Manager:"
        )
        if msg_id:
            edit_message_text(token, chat_id, msg_id, txt, reply_markup=get_mac_tools_inline_keyboard())
        else:
            send_message(token, chat_id, txt, reply_markup=get_mac_tools_inline_keyboard())

    elif data.startswith("mac_vol:"):
        arg_v = data.split(":", 1)[1].strip()
        try:
            if arg_v == "mute":
                subprocess.run(["osascript", "-e", "set volume output muted not (output muted of (get volume settings))"], timeout=3)
                msg_v = "Mute Mac diubah!"
            else:
                curr = int(subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True, timeout=3).stdout.strip() or "50")
                nv = max(0, min(100, curr + int(arg_v)))
                subprocess.run(["osascript", "-e", f"set volume output volume {nv}"], timeout=3)
                msg_v = f"Volume Mac: {nv}%"
            answer_callback_query(token, cb_id, text=msg_v)
        except Exception as e:
            answer_callback_query(token, cb_id, text=f"Gagal: {e}", show_alert=True)

    elif data == "mac_clip_get":
        try:
            clip = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3).stdout
            if clip:
                send_message(token, chat_id, f"📋 <b>Clipboard Mac Saat Ini:</b>\n<pre>{html.escape(clip[:3500])}</pre>")
            else:
                send_message(token, chat_id, "📋 Clipboard Mac sedang kosong.")
        except Exception as e:
            send_message(token, chat_id, f"⚠️ Gagal membaca clipboard: {e}")

    elif data == "mac_clip_set":
        force_reply = {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Ketik / tempel teks untuk clipboard Mac...",
        }
        send_message(token, chat_id, "📋 <b>ISI CLIPBOARD MAC:</b>\n\nBalas pesan ini dengan teks atau link yang ingin disalin ke Clipboard Mac.", reply_markup=force_reply)

    elif data == "mac_procs":
        try:
            out = subprocess.run(["ps", "-Arco", "pid,pcpu,pmem,comm"], capture_output=True, text=True, timeout=4).stdout.splitlines()[1:6]
            lines = ["⚙️ <b>TOP 5 PROSES TERBERAT DI MAC:</b>\n"]
            btns = []
            for ln in out:
                parts = ln.strip().split(None, 3)
                if len(parts) == 4:
                    pid, cpu, mem, comm = parts
                    name = os.path.basename(comm)
                    lines.append(f"• <code>{pid}</code> <b>{html.escape(name)}</b> — CPU {cpu}% | RAM {mem}%")
                    btns.append([{"text": f"❌ Tutup {name[:18]} (PID {pid})", "callback_data": f"kill_pid:{pid}"}])
            btns.append([{"text": "🔄 Refresh", "callback_data": "mac_procs"}, {"text": "💻 Kembali", "callback_data": "act_mac_tools"}])
            send_message(token, chat_id, "\n".join(lines), reply_markup={"inline_keyboard": btns})
        except Exception as e:
            send_message(token, chat_id, f"⚠️ Gagal membaca proses: {e}")

    elif data.startswith("kill_pid:"):
        pid_s = data.split(":", 1)[1].strip()
        if pid_s.isdigit():
            subprocess.run(["kill", "-9", pid_s], capture_output=True)
            answer_callback_query(token, cb_id, text=f"Proses PID {pid_s} dihentikan!")
            send_message(token, chat_id, f"✅ Proses PID <code>{pid_s}</code> telah dihentikan.")

    elif data.startswith("cast_shared:"):
        fn = os.path.basename(data.split(":", 1)[1].strip())
        fpath = os.path.join(SHARED_DIR, fn)
        if os.path.isfile(fpath):
            answer_callback_query(token, cb_id, text="Menayangkan di TV Stick Mi...")
            def _worker_cast_sh():
                res = cast_manager.cast_media_file_to_device(fpath, target_device_name="tv", title=fn)
                icon = "✅" if res.get("status") == "success" else "⚠️"
                stop_btn = {"inline_keyboard": [[{"text": "🛑 Stop TV", "callback_data": "stop_gh:tv"}]]}
                send_message(token, chat_id, f"{icon} {html.escape(res.get('message', ''))}", reply_markup=stop_btn)
            threading.Thread(target=_worker_cast_sh, daemon=True).start()
        else:
            answer_callback_query(token, cb_id, text="File tidak ditemukan", show_alert=True)

    elif data.startswith("save_shared_lib:"):
        fn = os.path.basename(data.split(":", 1)[1].strip())
        fpath = os.path.join(SHARED_DIR, fn)
        if os.path.isfile(fpath):
            with open(fpath, "rb") as f:
                raw_b = f.read()
            aud = db_manager.add_audio(name=os.path.splitext(fn)[0], original_filename=fn, data_bytes=raw_b)
            answer_callback_query(token, cb_id, text="Tersimpan ke Pustaka Media!", show_alert=True)
            send_message(token, chat_id, f"💾 Media <b>{html.escape(aud['name'])}</b> berhasil disimpan ke Pustaka Audio &amp; Video!")
        else:
            answer_callback_query(token, cb_id, text="File tidak ditemukan", show_alert=True)

    elif data == "act_gh_menu":
        force_reply_markup = {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Ketik pesan untuk Google Home..."
        }
        prompt = (
            "📢 <b>SIARKAN KE GOOGLE HOME:</b>\n\n"
            "Silakan langsung ketik pesan pengumuman Anda di bawah:\n"
            "<i>(Kolom ketik sudah aktif, Anda cukup ketik pesan dan tekan Kirim)</i>\n\n"
            "💡 <i>Atau salin perintah dengan 1 tap:</i> <code>/cast </code>"
        )
        send_message(token, chat_id, prompt, reply_markup=force_reply_markup)

    elif data == "act_tv_menu":
        force_reply_markup = {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Tempel link YouTube / URL atau ketik pesan TTS untuk TV..."
        }
        prompt = (
            "📺 <b>CAST KE TV STICK MI (YOUTUBE / URL / TTS):</b>\n\n"
            "Langsung balas pesan ini dengan:\n"
            "• <b>Link YouTube / Shorts / .m3u8 / MP4</b> → langsung tayang di TV!\n"
            "• <b>Teks pengumuman</b> → dibacakan (TTS) lengkap dengan visual art di TV!\n\n"
            "💡 <i>Atau gunakan perintah:</i> <code>/yt &lt;link&gt;</code>"
        )
        send_message(token, chat_id, prompt, reply_markup=force_reply_markup)

    elif data.startswith("cast_sample:"):
        voice_type = data.split(":", 1)[1]
        v_name = "Gadis (Wanita 1)" if voice_type == "wanita1" else ("Ardi (Pria)" if voice_type == "pria" else "Google ID (Wanita 2)")
        sample_txt = f"Halo, ini adalah siaran contoh dengan suara {v_name}."
        send_message(token, chat_id, f"⏳ Menyiarkan contoh suara {v_name} ke Google Home...")
        res = cast_manager.broadcast_to_google_home(sample_txt, voice_type=voice_type)
        if res.get("status") == "success":
            send_message(token, chat_id, f"✅ Berhasil disiarkan ({v_name}) ke <b>{html.escape(res.get('device', 'Google Home'))}</b>!")
        else:
            send_message(token, chat_id, f"❌ Gagal: {res.get('message')}")

    elif data == "act_mac_menu":
        force_reply_markup = {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Ketik kalimat untuk speaker Mac..."
        }
        prompt = (
            "🔊 <b>BICARA LEWAT SPEAKER MAC MINI:</b>\n\n"
            "Silakan ketik kalimat yang ingin diucapkan oleh speaker Mac mini:\n"
            "<i>(Kolom ketik sudah aktif, Anda cukup ketik kalimat dan tekan Kirim)</i>\n\n"
            "💡 <i>Atau salin perintah cepat dengan 1 tap:</i> <code>/say </code>"
        )
        send_message(token, chat_id, prompt, reply_markup=force_reply_markup)

    elif data == "act_chat_info":
        force_reply_markup = {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Tulis pesan untuk orang di depan Mac..."
        }
        prompt = (
            "💬 <b>INTERCOM POP-UP MONITOR MAC:</b>\n\n"
            "Silakan ketik pesan untuk memunculkan dialog pop-up di layar Mac:\n"
            "<i>(Orang di depan Mac bisa mengetik balasan langsung ke chat ini)</i>\n\n"
            "💡 <i>Atau salin perintah cepat dengan 1 tap:</i> <code>/chat </code>"
        )
        send_message(token, chat_id, prompt, reply_markup=force_reply_markup)

    elif data == "act_scan_cast":
        send_message(token, chat_id, "🔍 Memindai speaker Google Home di Wi-Fi...")
        res = cast_manager.get_cast_devices(force_refresh=True)
        devs = res.get("devices", [])
        if devs:
            lines = [f"📢 <b>Speaker Google Cast Ditemukan ({len(devs)}):</b>\n"]
            for d in devs:
                lines.append(f"• <b>{d['name']}</b> ({d.get('model', 'Cast')}) - <code>{d['host']}</code>")
            send_message(token, chat_id, "\n".join(lines))
        else:
            send_message(token, chat_id, "⚠️ Tidak ada speaker Google Home yang terdeteksi.")

    elif data == "act_sleep":
        subprocess.run(["pmset", "displaysleepnow"], timeout=3)
        answer_callback_query(token, cb_id, text="Layar Mac dimatikan!", show_alert=True)
        send_message(token, chat_id, "🌙 Layar monitor Mac telah dimatikan / dikunci.")

    elif data == "act_files":
        items = os.listdir(SHARED_DIR)
        files = [f for f in items if os.path.isfile(os.path.join(SHARED_DIR, f)) and not f.startswith(".")]
        if files:
            lines = [f"📁 <b>Berkas di Folder Mac ({len(files)}):</b>\n"]
            for f in sorted(files)[:10]:
                lines.append(f"• <code>{f}</code>")
            lines.append("\nKetik <code>/get &lt;nama_file&gt;</code> untuk mengunduhnya ke chat ini.")
            send_message(token, chat_id, "\n".join(lines))
        else:
            send_message(token, chat_id, "📁 Folder shared_files di Mac masih kosong.")

    elif data.startswith("play_gh:"):
        fn = data.split(":", 1)[1]
        fpath = f"/tmp/{fn}"
        if os.path.exists(fpath):
            answer_callback_query(token, cb_id, text="Memulai siaran ke Google Home...")
            def _worker_vn_gh():
                cast_res = cast_manager.broadcast_audio_file_to_google_home(fpath, title="Voice Note Pemilik")
                if cast_res.get("status") == "success":
                    dev_name = cast_res.get("device", "Google Home")
                    send_message(token, chat_id, f"📢 <b>Voice note disiarkan ke speaker {html.escape(dev_name)}!</b>")
                else:
                    send_message(token, chat_id, f"⚠️ Gagal menyiarkan ke Google Home: {html.escape(cast_res.get('message', 'Terjadi kesalahan'))}")
            threading.Thread(target=_worker_vn_gh, daemon=True).start()
        else:
            send_message(token, chat_id, "⚠️ Berkas voice note telah kedaluwarsa.")

    elif data == "stop_mac":
        mac_player.stop_mac_audio()
        answer_callback_query(token, cb_id, text="Suara Mac dihentikan!", show_alert=False)
        text, markup = build_now_playing_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)

    elif data == "stop_gh" or data.startswith("stop_gh:"):
        target_dev = data.split(":", 1)[1].strip() if ":" in data else None
        gh_res = cast_manager.stop_google_home_cast(target_dev)
        answer_callback_query(token, cb_id, text=gh_res.get("message", "Siaran dihentikan!")[:190], show_alert=gh_res.get("status") != "success")
        text, markup = build_now_playing_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)

    elif data == "stop_all":
        mac_player.stop_mac_audio()
        gh_res = cast_manager.stop_google_home_cast()
        ok = gh_res.get("status") == "success"
        answer_callback_query(token, cb_id, text="Semua audio berhasil dihentikan!" if ok else gh_res.get("message", "")[:190], show_alert=not ok)
        text, markup = build_now_playing_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)

    elif data == "act_now_playing":
        text, markup = build_now_playing_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)
        else:
            send_message(token, chat_id, text, reply_markup=markup)

    elif data.startswith("gh_vol:"):
        parts = data.split(":")
        target_dev = parts[1].strip() if len(parts) >= 3 else None
        val_str = parts[-1].strip()
        if val_str.startswith("+") or val_str.startswith("-"):
            gh_res = cast_manager.set_google_home_volume(delta_percent=int(val_str), target_device_name=target_dev)
        else:
            gh_res = cast_manager.set_google_home_volume(volume_percent=int(val_str), target_device_name=target_dev)
        answer_callback_query(token, cb_id, text=gh_res.get("message", "Volume diperbarui")[:190], show_alert=gh_res.get("status") != "success")
        text, markup = build_now_playing_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)

    elif data.startswith("gh_mute:"):
        parts = data.split(":")
        target_dev = parts[1].strip() if len(parts) >= 3 else None
        mode_m = parts[-1].strip()
        muted_arg = "toggle" if mode_m == "toggle" else (mode_m in ("1", "true", "on"))
        gh_res = cast_manager.set_google_home_volume(muted=muted_arg, target_device_name=target_dev)
        answer_callback_query(token, cb_id, text=gh_res.get("message", "Mute diperbarui")[:190], show_alert=gh_res.get("status") != "success")
        text, markup = build_now_playing_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)

    elif data.startswith("gh_media:"):
        parts = data.split(":")
        target_dev = parts[1].strip() if len(parts) >= 3 else None
        act_m = parts[-1].strip()
        gh_res = cast_manager.control_google_home_media(action=act_m, target_device_name=target_dev)
        answer_callback_query(token, cb_id, text=gh_res.get("message", "Kontrol media")[:190], show_alert=gh_res.get("status") != "success")
        text, markup = build_now_playing_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)

    elif data.startswith("play_tv:"):
        fn = data.split(":", 1)[1]
        fpath = f"/tmp/{fn}"
        if os.path.exists(fpath):
            answer_callback_query(token, cb_id, text="Memulai siaran ke TV Stick Mi...")
            def _worker_vn_tv():
                cast_res = cast_manager.broadcast_audio_file_to_google_home(fpath, target_device_name="tv", title="Voice Note Pemilik")
                if cast_res.get("status") == "success":
                    dev_name = cast_res.get("device", "TV Stick Mi")
                    stop_btn = {"inline_keyboard": [[{"text": "🛑 Stop TV", "callback_data": f"stop_gh:{cast_res.get('host', 'tv')}"}]]}
                    send_message(token, chat_id, f"📺 <b>Rekaman suara disiarkan ke {html.escape(dev_name)}!</b>", reply_markup=stop_btn)
                else:
                    send_message(token, chat_id, f"⚠️ Gagal ke TV Stick Mi: {html.escape(cast_res.get('message', ''))}")
            threading.Thread(target=_worker_vn_tv, daemon=True).start()
        else:
            send_message(token, chat_id, "⚠️ Berkas voice note telah kedaluwarsa.")

    elif data.startswith("play_mac:"):
        fn = data.split(":", 1)[1]
        fpath = f"/tmp/{fn}"
        if os.path.exists(fpath):
            mac_player.play_mac_audio(fpath, title="Voice Note Pemilik", source="voice_note")
            answer_callback_query(token, cb_id, text="Diputar di Mac mini!")
            stop_btn = {"inline_keyboard": [[{"text": "🛑 Stop Suara Mac", "callback_data": "stop_mac"}]]}
            send_message(token, chat_id, "🔊 <b>Voice note diputar di speaker Mac mini!</b>", reply_markup=stop_btn)
        else:
            send_message(token, chat_id, "⚠️ Berkas voice note telah kedaluwarsa.")

    elif data.startswith("play_both:"):
        fn = data.split(":", 1)[1]
        fpath = f"/tmp/{fn}"
        if os.path.exists(fpath):
            answer_callback_query(token, cb_id, text="Memutar di Mac & Google Home...")
            mac_player.play_mac_audio(fpath, title="Voice Note Pemilik", source="voice_note")
            def _worker_vn_both():
                cast_res = cast_manager.broadcast_audio_file_to_google_home(fpath, title="Voice Note Pemilik")
                stop_btn = {"inline_keyboard": [[{"text": "🛑 Hentikan Semua", "callback_data": "stop_all"}]]}
                if cast_res.get("status") == "success":
                    dev_name = cast_res.get("device", "Google Home")
                    send_message(token, chat_id, f"📢+🔊 <b>Voice note diputar di speaker Mac mini & Google Home ({html.escape(dev_name)})!</b>", reply_markup=stop_btn)
                else:
                    send_message(token, chat_id, f"🔊 Diputar di Mac. ⚠️ Gagal ke Google Home: {html.escape(cast_res.get('message', 'Terjadi kesalahan'))}")
            threading.Thread(target=_worker_vn_both, daemon=True).start()
        else:
            send_message(token, chat_id, "⚠️ Berkas voice note telah kedaluwarsa.")

    elif data == "act_schedule_menu":
        text, markup = build_schedule_menu_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)
        else:
            send_message(token, chat_id, text, reply_markup=markup)

    elif data == "act_add_sch_prompt":
        force_reply_markup = {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Contoh: 10m Makan siang sudah siap..."
        }
        prompt = (
            "⏰ <b>BUAT JADWAL PENGUMUMAN SUARA BARU:</b>\n\n"
            "Silakan langsung ketik waktu dan pesan pengumuman:\n\n"
            "📝 <b>Format Cepat:</b>\n"
            "• <code>10m Makan siang sudah siap</code> (10 menit lagi)\n"
            "• <code>1h Selesai istirahat</code> (1 jam lagi)\n"
            "• <code>07:00 Bangun pagi dan olahraga</code> (Pukul 07:00)\n"
            "• <code>daily 12:30 Waktu sholat dhuhur</code> (Setiap hari)\n\n"
            "<i>(Target pengumuman otomatis: Mac & Google Home)</i>"
        )
        send_message(token, chat_id, prompt, reply_markup=force_reply_markup)

    elif data.startswith("del_sch:"):
        sid = data.split(":", 1)[1]
        ok = smart_reminders.delete_schedule(sid)
        answer_callback_query(token, cb_id, text="Jadwal berhasil dihapus!" if ok else "Jadwal tidak ditemukan")
        text, markup = build_schedule_menu_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)

    elif data.startswith("test_sch:"):
        sid = data.split(":", 1)[1]
        res = smart_reminders.trigger_schedule_now(sid)
        answer_callback_query(token, cb_id, text=res.get("message", "Membunyikan tes suara!"), show_alert=True)

    elif data == "act_audio_menu":
        text, markup = build_audio_library_menu_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)
        else:
            send_message(token, chat_id, text, reply_markup=markup)

    elif data.startswith("aud_page:"):
        parts = data.split(":")
        f_type = parts[1] if len(parts) > 1 else "all"
        try:
            pg = int(parts[2]) if len(parts) > 2 else 1
        except Exception:
            pg = 1
        text, markup = build_audio_library_menu_content(page=pg, filter_type=f_type)
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)
        else:
            send_message(token, chat_id, text, reply_markup=markup)

    elif data.startswith("p_aud_gh:"):
        aid = data.split(":", 1)[1]
        aud = db_manager.get_audio(aid)
        if aud and os.path.exists(aud["file_path"]):
            is_vid = (aud.get("media_type") == "video") or str(aud.get("mime_type", "")).startswith("video/")
            answer_callback_query(token, cb_id, text="Memulai siaran ke Google Home...")
            def _worker_aud_gh():
                if is_vid:
                    cast_res = cast_manager.cast_media_file_to_device(
                        aud["file_path"],
                        target_device_name="192.168.0.198",
                        title=aud["name"],
                    )
                else:
                    cast_res = cast_manager.broadcast_audio_file_to_google_home(
                        aud["file_path"],
                        target_device_name="192.168.0.198",
                        title=aud["name"],
                        duration=aud.get("duration_seconds", 0)
                    )
                if cast_res.get("status") == "success":
                    dev_name = cast_res.get("device", "Google Home")
                    stop_btn = {"inline_keyboard": [[{"text": "🛑 Stop Google Home", "callback_data": "stop_gh:192.168.0.198"}]]}
                    send_message(token, chat_id, f"📢 <b>{'Audio dari video' if is_vid else 'Audio'} \"{html.escape(aud['name'])}\" disiarkan ke speaker {html.escape(dev_name)}!</b>", reply_markup=stop_btn)
                else:
                    send_message(token, chat_id, f"⚠️ Gagal menyiarkan ke Google Home: {html.escape(cast_res.get('message', 'Terjadi kesalahan'))}")
            threading.Thread(target=_worker_aud_gh, daemon=True).start()
        else:
            answer_callback_query(token, cb_id, text="Audio tidak ditemukan", show_alert=True)

    elif data.startswith("p_aud_tv:"):
        aid = data.split(":", 1)[1]
        aud = db_manager.get_audio(aid)
        if aud and os.path.exists(aud["file_path"]):
            is_vid = (aud.get("media_type") == "video") or str(aud.get("mime_type", "")).startswith("video/")
            answer_callback_query(token, cb_id, text="Memulai siaran ke TV Stick Mi...")
            def _worker_aud_tv():
                if is_vid:
                    cast_res = cast_manager.cast_media_file_to_device(aud["file_path"], target_device_name="tv", title=aud["name"])
                else:
                    cast_res = cast_manager.broadcast_audio_file_to_google_home(
                        aud["file_path"],
                        target_device_name="tv",
                        title=aud["name"],
                        duration=aud.get("duration_seconds", 0)
                    )
                if cast_res.get("status") == "success":
                    dev_name = cast_res.get("device", "TV Stick Mi")
                    stop_btn = {"inline_keyboard": [[{"text": "🛑 Stop TV", "callback_data": f"stop_gh:{cast_res.get('host', 'tv')}"}]]}
                    send_message(token, chat_id, f"📺 <b>{'Video' if is_vid else 'Audio'} \"{html.escape(aud['name'])}\" diputar di {html.escape(dev_name)}!</b>", reply_markup=stop_btn)
                else:
                    send_message(token, chat_id, f"⚠️ Gagal ke TV Stick Mi: {html.escape(cast_res.get('message', 'Terjadi kesalahan'))}")
            threading.Thread(target=_worker_aud_tv, daemon=True).start()
        else:
            answer_callback_query(token, cb_id, text="Media tidak ditemukan", show_alert=True)

    elif data.startswith("p_aud_mac:"):
        aid = data.split(":", 1)[1]
        aud = db_manager.get_audio(aid)
        if aud and os.path.exists(aud["file_path"]):
            mac_player.play_mac_audio(
                aud["file_path"],
                title=aud["name"],
                duration=aud.get("duration_seconds", 0),
                source="audio_library"
            )
            answer_callback_query(token, cb_id, text="Diputar di Mac mini!")
            stop_btn = {"inline_keyboard": [[{"text": "🛑 Stop Suara Mac", "callback_data": "stop_mac"}]]}
            send_message(token, chat_id, f"🔊 <b>Audio \"{html.escape(aud['name'])}\" diputar di speaker Mac mini!</b>", reply_markup=stop_btn)
        else:
            answer_callback_query(token, cb_id, text="Audio tidak ditemukan", show_alert=True)

    elif data.startswith("del_aud:"):
        aid = data.split(":", 1)[1]
        ok = db_manager.delete_audio(aid)
        answer_callback_query(token, cb_id, text="Audio berhasil dihapus!" if ok else "Gagal menghapus", show_alert=False)
        text, markup = build_audio_library_menu_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, text, reply_markup=markup)

    elif data.startswith("save_to_lib:"):
        fn = data.split(":", 1)[1]
        fpath = f"/tmp/{fn}"
        if os.path.exists(fpath):
            with open(fpath, "rb") as f:
                f_bytes = f.read()
            label = f"Suara Telegram ({time.strftime('%d/%m %H:%M')})"
            aud = db_manager.add_audio(name=label, original_filename=fn, data_bytes=f_bytes)
            answer_callback_query(token, cb_id, text="Tersimpan ke Pustaka Database!", show_alert=True)
            send_message(
                token,
                chat_id,
                f"💾 <b>SUARA BERHASIL DISIMPAN KE PUSTAKA SQLITE!</b>\n\n"
                f"🎵 <b>Nama:</b> {html.escape(aud['name'])}\n"
                f"⏱️ <b>Durasi:</b> <code>{aud['duration_formatted']}</code> • 💾 {aud['size_formatted']}\n\n"
                f"💡 Audio ini sekarang tersimpan permanen di database server dan siap disiarkan kapan saja atau dijadikan jadwal suara!"
            )
        else:
            answer_callback_query(token, cb_id, text="Berkas audio telah kedaluwarsa", show_alert=True)

    elif data == "act_qa_menu":
        answer_callback_query(token, cb_id, text="Memuat Quick Actions...")
        txt, markup = build_qa_menu_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, txt, reply_markup=markup)
        else:
            send_message(token, chat_id, txt, reply_markup=markup)

    elif data.startswith("qa_play:"):
        qa_id = data.split(":", 1)[1]
        answer_callback_query(token, cb_id, text="⚡ Menjalankan Quick Action...")
        res = quick_action_runner.execute_quick_action(qa_id=qa_id)
        icon = "✅" if res.get("status") == "success" else "⚠️"
        send_message(token, chat_id, f"{icon} {html.escape(res.get('message', 'Selesai'))}")

    elif data.startswith("qa_detail:"):
        qa_id = data.split(":", 1)[1]
        answer_callback_query(token, cb_id)
        txt, markup = build_qa_detail_content(qa_id)
        if msg_id:
            edit_message_text(token, chat_id, msg_id, txt, reply_markup=markup)
        else:
            send_message(token, chat_id, txt, reply_markup=markup)

    elif data == "qa_add_prompt":
        answer_callback_query(token, cb_id, text="Ketik Quick Action baru...")
        send_message(
            token,
            chat_id,
            "➕ <b>TAMBAH QUICK ACTION BARU</b>\n\n"
            "Balas pesan ini dengan format:\n"
            "• <code>Judul | Kalimat broadcast</code>\n"
            "  <i>Contoh:</i> <code>Makan Siang | Perhatian, makan siang sudah siap</code>\n"
            "• Atau langsung ketik kalimatnya saja (otomatis suara Wanita + 🔔 Bel + Vol 70% → 50%).\n"
            "• Awali <code>ga:</code> untuk perintah Google Assistant:\n"
            "  <i>Contoh:</i> <code>ga: Lampu Ruang Tamu | nyalakan lampu ruang tamu</code>",
            reply_markup={"force_reply": True, "selective": True},
        )

    elif data.startswith("qa_edit_prompt:"):
        qa_id = data.split(":", 1)[1]
        qa = db_manager.get_quick_action(qa_id)
        if not qa:
            answer_callback_query(token, cb_id, text="Quick Action tidak ditemukan", show_alert=True)
            return
        answer_callback_query(token, cb_id, text="Ketik perubahan baru...")
        send_message(
            token,
            chat_id,
            f"✏️ <b>EDIT QUICK ACTION [{qa_id}]</b>\n\n"
            f"Saat ini:\n<b>{html.escape(qa['title'])}</b>\n<i>\"{html.escape(qa['message'])}\"</i>\n\n"
            "Balas pesan ini dengan:\n"
            "• <code>Judul Baru | Kalimat Baru</code> (ubah keduanya), atau\n"
            "• Langsung ketik <code>Kalimat Baru</code> saja.",
            reply_markup={"force_reply": True, "selective": True},
        )

    elif data.startswith("qa_cycle_voice:"):
        qa_id = data.split(":", 1)[1]
        qa = db_manager.get_quick_action(qa_id)
        if qa:
            order = ["wanita1", "wanita2", "pria"]
            nxt = order[(order.index(qa["voice_type"]) + 1) % len(order)] if qa["voice_type"] in order else "wanita1"
            db_manager.update_quick_action(qa_id, voice_type=nxt)
            answer_callback_query(token, cb_id, text=f"Suara diubah ke {nxt}")
            txt, markup = build_qa_detail_content(qa_id)
            if msg_id:
                edit_message_text(token, chat_id, msg_id, txt, reply_markup=markup)

    elif data.startswith("qa_cycle_vol:"):
        qa_id = data.split(":", 1)[1]
        qa = db_manager.get_quick_action(qa_id)
        if qa:
            vols = [50, 70, 85, 100]
            curr = qa.get("volume", 70)
            nxt = next((v for v in vols if v > curr), vols[0])
            db_manager.update_quick_action(qa_id, volume=nxt)
            answer_callback_query(token, cb_id, text=f"Volume diubah ke {nxt}%")
            txt, markup = build_qa_detail_content(qa_id)
            if msg_id:
                edit_message_text(token, chat_id, msg_id, txt, reply_markup=markup)

    elif data.startswith("qa_toggle_chime:"):
        qa_id = data.split(":", 1)[1]
        qa = db_manager.get_quick_action(qa_id)
        if qa:
            new_c = not bool(qa.get("use_chime", True))
            db_manager.update_quick_action(qa_id, use_chime=new_c)
            answer_callback_query(token, cb_id, text=f"Bel: {'ON' if new_c else 'OFF'}")
            txt, markup = build_qa_detail_content(qa_id)
            if msg_id:
                edit_message_text(token, chat_id, msg_id, txt, reply_markup=markup)

    elif data.startswith("qa_cycle_rep:"):
        qa_id = data.split(":", 1)[1]
        qa = db_manager.get_quick_action(qa_id)
        if qa:
            nxt = (int(qa.get("repeat_count", 1)) % 3) + 1
            db_manager.update_quick_action(qa_id, repeat_count=nxt)
            answer_callback_query(token, cb_id, text=f"Pengulangan: {nxt}x")
            txt, markup = build_qa_detail_content(qa_id)
            if msg_id:
                edit_message_text(token, chat_id, msg_id, txt, reply_markup=markup)

    elif data.startswith("qa_del:"):
        qa_id = data.split(":", 1)[1]
        res = db_manager.delete_quick_action(qa_id)
        answer_callback_query(token, cb_id, text=res.get("message", "Dihapus"))
        txt, markup = build_qa_menu_content()
        if msg_id:
            edit_message_text(token, chat_id, msg_id, txt, reply_markup=markup)


def build_qa_menu_content():
    items = db_manager.list_quick_actions()
    rows = []
    for qa in items:
        lbl = f"{qa.get('icon', '📢')} {qa['title']} ({qa.get('volume', 70)}%)"
        rows.append([
            {"text": f"▶️ {lbl}", "callback_data": f"qa_play:{qa['id']}"},
            {"text": "⚙️", "callback_data": f"qa_detail:{qa['id']}"},
        ])
    rows.append([{"text": "➕ Tambah Quick Action", "callback_data": "qa_add_prompt"}])
    rows.append([{"text": "🔄 Refresh", "callback_data": "act_qa_menu"}, {"text": "🏠 Dashboard", "callback_data": "act_dashboard"}])
    txt = (
        "⚡ <b>MANAJEMEN QUICK ACTION (CRUD)</b>\n"
        "Sinkron otomatis dengan Web Dashboard.\n"
        "• Klik <b>▶️</b> untuk langsung putar (Vol 70% → 50%)\n"
        "• Klik <b>⚙️</b> untuk Edit / Atur Suara / Bel / Hapus\n"
        "• Atau ketik <code>/qa kalimat Anda</code> untuk broadcast instan!"
    )
    return txt, {"inline_keyboard": rows}


def build_qa_detail_content(qa_id):
    qa = db_manager.get_quick_action(qa_id)
    if not qa:
        return build_qa_menu_content()
    v_map = {"wanita1": "👩 Wanita 1", "wanita2": "👧 Wanita 2", "pria": "👨 Pria"}
    v_lbl = v_map.get(qa.get("voice_type", "wanita1"), "👩 Wanita 1")
    atype = qa.get("action_type", "tts")
    rvol = "Prev" if qa.get("restore_volume") == -1 else f"{qa.get('restore_volume', 50)}%"
    chime_lbl = "🔔 Bel: ON" if qa.get("use_chime", True) else "🔕 Bel: OFF"
    rep_lbl = f"🔁 Ulang: {qa.get('repeat_count', 1)}x"
    txt = (
        f"⚙️ <b>DETAIL QUICK ACTION</b>\n\n"
        f"{html.escape(qa.get('icon', '📢'))} <b>{html.escape(qa['title'])}</b>\n"
        f"💬 <i>\"{html.escape(qa['message'])}\"</i>\n\n"
        f"• <b>Tipe:</b> <code>{atype.upper()}</code>\n"
        f"• <b>Suara:</b> {v_lbl}\n"
        f"• <b>Volume:</b> {qa.get('volume', 70)}% → {rvol}\n"
        f"• <b>Bel:</b> {'Aktif (Pembuka + Penutup)' if qa.get('use_chime', True) else 'Mati'} • <b>Ulang:</b> {qa.get('repeat_count', 1)}x"
    )
    kb = [
        [{"text": "▶️ Play Sekarang", "callback_data": f"qa_play:{qa_id}"}],
        [
            {"text": "✏️ Edit Teks", "callback_data": f"qa_edit_prompt:{qa_id}"},
            {"text": f"🔊 Vol {qa.get('volume', 70)}%", "callback_data": f"qa_cycle_vol:{qa_id}"},
        ],
    ]
    if atype == "tts":
        kb.append([
            {"text": v_lbl, "callback_data": f"qa_cycle_voice:{qa_id}"},
            {"text": chime_lbl, "callback_data": f"qa_toggle_chime:{qa_id}"},
            {"text": rep_lbl, "callback_data": f"qa_cycle_rep:{qa_id}"},
        ])
    kb.append([
        {"text": "🗑️ Hapus", "callback_data": f"qa_del:{qa_id}"},
        {"text": "⬅️ Daftar", "callback_data": "act_qa_menu"},
    ])
    return txt, {"inline_keyboard": kb}


def handle_command(token, chat_id, text, config, msg=None):
    global DEFAULT_VOICE_TARGET

    # 1. Cek apakah ini balasan dari prompt ForceReply sebelumnya:
    if msg and msg.get("reply_to_message"):
        reply_to = msg.get("reply_to_message")
        orig = (reply_to.get("text") or "").upper()
        if "MASUKKAN PIN PAIRING TV" in orig:
            res = android_tv_remote.finish_pairing(text.strip())
            icon = "✅" if res.get("status") == "success" else "⚠️"
            send_message(token, chat_id, f"{icon} {html.escape(res.get('message', ''))}", reply_markup=get_tv_remote_inline_keyboard())
            return

        if "TAMBAH QUICK ACTION BARU" in orig:
            raw_qa = text.strip()
            atype = "tts"
            icon_qa = "📢"
            if raw_qa.lower().startswith("ga:"):
                atype = "assistant"
                icon_qa = "🤖"
                raw_qa = raw_qa[3:].strip()
            if "|" in raw_qa:
                t_part, m_part = raw_qa.split("|", 1)
                title_qa, msg_qa = t_part.strip(), m_part.strip()
            else:
                title_qa, msg_qa = raw_qa[:30], raw_qa
            res = db_manager.add_quick_action(
                title=title_qa,
                message=msg_qa,
                voice_type="wanita1",
                volume=70,
                restore_volume=50,
                use_chime=True,
                repeat_count=1,
                action_type=atype,
                icon=icon_qa,
            )
            if res.get("status") == "success":
                qa_new = res["quick_action"]
                txt_m, mk_m = build_qa_detail_content(qa_new["id"])
                send_message(token, chat_id, f"✅ Quick Action <b>{html.escape(qa_new['title'])}</b> berhasil dibuat!\n\n" + txt_m, reply_markup=mk_m)
            else:
                send_message(token, chat_id, f"⚠️ {html.escape(res.get('message', 'Gagal membuat Quick Action'))}")
            return

        if "EDIT QUICK ACTION [" in orig:
            m_id = re.search(r"\[([A-Za-z0-9_]+)\]", reply_to.get("text") or "")
            if m_id:
                qa_id = m_id.group(1)
                raw_ed = text.strip()
                if "|" in raw_ed:
                    t_part, m_part = raw_ed.split("|", 1)
                    res = db_manager.update_quick_action(qa_id, title=t_part.strip(), message=m_part.strip())
                else:
                    res = db_manager.update_quick_action(qa_id, message=raw_ed)
                if res.get("status") == "success":
                    txt_d, mk_d = build_qa_detail_content(qa_id)
                    send_message(token, chat_id, "✅ Quick Action berhasil diperbarui!\n\n" + txt_d, reply_markup=mk_d)
                else:
                    send_message(token, chat_id, f"⚠️ {html.escape(res.get('message', 'Gagal mengubah Quick Action'))}")
                return

        if "ISI CLIPBOARD MAC" in orig:
            try:
                subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=3)
                send_message(token, chat_id, "✅ Teks berhasil disalin ke <b>Clipboard Mac</b>!")
            except Exception as e:
                send_message(token, chat_id, f"⚠️ Gagal mengisi clipboard: {e}")
            return

        if "CAST KE TV STICK MI" in orig:
            raw_tv = text.strip()
            if raw_tv.startswith("http://") or raw_tv.startswith("https://"):
                send_message(token, chat_id, f"📺 <i>Mengirim URL ke TV Stick Mi...</i>\n<code>{html.escape(raw_tv[:100])}</code>")
                res = cast_manager.cast_url_to_device(raw_tv, target_device_name="tv")
            else:
                res = cast_manager.broadcast_to_google_home(raw_tv, target_device_name="tv", voice_type="wanita1")
            icon = "✅" if res.get("status") == "success" else "⚠️"
            send_message(token, chat_id, f"{icon} {html.escape(res.get('message', ''))}")
            return

        if "BUAT JADWAL PENGUMUMAN" in orig or "JADWAL PENGUMUMAN" in orig:
            raw = text.strip()
            tokens = raw.split()
            if not tokens:
                send_message(token, chat_id, "⚠️ Format kosong. Contoh: <code>10m Makan siang siap</code>")
                return

            if len(tokens) >= 2 and (
                re.match(r"^\d{4}-\d{2}-\d{2}$", tokens[0]) or
                tokens[0].lower() in ["daily", "harian", "tiap", "besok", "tomorrow", "lusa"]
            ):
                time_str = f"{tokens[0]} {tokens[1]}"
                rest_tokens = tokens[2:]
            else:
                time_str = tokens[0]
                rest_tokens = tokens[1:]

            msg_str = " ".join(rest_tokens).strip()
            mode = "tts"
            audio_id = None
            label = ""

            m_audio = re.search(r"audio:([^\s]+)", msg_str, re.IGNORECASE)
            if m_audio:
                aq = m_audio.group(1).strip()
                all_auds = db_manager.list_audios()
                matched_aud = next((a for a in all_auds if aq.lower() in a["id"].lower() or aq.lower() in a["name"].lower() or aq.lower() in a["original_filename"].lower()), None)
                if matched_aud:
                    mode = "audio"
                    audio_id = matched_aud["id"]
                    label = f"Audio: {matched_aud['name']}"
                    msg_str = re.sub(r"audio:[^\s]+", "", msg_str).strip() or matched_aud["name"]
                else:
                    send_message(token, chat_id, f"⚠️ File audio '{aq}' tidak ditemukan di pustaka database server.")
                    return

            if not msg_str and mode == "tts":
                msg_str = "Pengumuman Suara Terjadwal"

            res = smart_reminders.add_schedule(
                message=msg_str,
                time_str=time_str,
                target="both",
                voice_type="wanita1",
                label=label,
                mode=mode,
                audio_id=audio_id
            )
            if res.get("status") == "success":
                daily_tag = " 🔁 <i>(Rutinitas Harian)</i>" if res.get("is_daily") else ""
                content_line = f"🎵 <b>File Audio:</b> {html.escape(label)}" if mode == "audio" else f"📢 <b>Pesan:</b> \"<i>{html.escape(res.get('message'))}</i>\"\n🗣️ <b>Suara:</b> Gadis (Wanita 1)"
                reply = (
                    f"✅ <b>Jadwal Suara Berhasil Disimpan ke SQLite!</b>{daily_tag}\n\n"
                    f"📌 <b>Judul:</b> {html.escape(res.get('label'))}\n"
                    f"{content_line}\n"
                    f"⏱️ <b>Waktu:</b> {res.get('remaining_readable')} ({res.get('target_readable')})\n"
                    f"🎯 <b>Target:</b> Mac & Google Home"
                )
                send_message(token, chat_id, reply)
            else:
                send_message(token, chat_id, f"❌ {res.get('message', 'Format waktu tidak valid')}")
            return
        if "GOOGLE HOME" in orig:
            res = cast_manager.broadcast_to_google_home(text)
            if res.get("status") == "success":
                dev = res.get("device", "Google Home")
                send_message(token, chat_id, f"📢 <b>Berhasil disiarkan ke {html.escape(dev)}:</b>\n<i>\"{html.escape(text)}\"</i>")
            else:
                send_message(token, chat_id, f"❌ Gagal menyiarkan: {html.escape(res.get('message', ''))}")
            return
        elif "SPEAKER MAC" in orig:
            speak_indonesian(text)
            send_message(token, chat_id, f"🔊 <b>Mac mengucapkan di speaker:</b>\n<i>\"{html.escape(text)}\"</i>")
            return
        elif "INTERCOM" in orig or "MONITOR MAC" in orig:
            from_user = msg.get("from", {}).get("first_name", "Telegram")
            send_message(token, chat_id, f"💬 <b>Pop-up terkirim ke layar Mac!</b>\n<i>\"{html.escape(text)}\"</i>\n\n⏳ <i>Menunggu balasan dari orang di depan Mac...</i>")
            handle_interactive_chat_async(token, chat_id, from_user, text)
            return

    cmd_parts = text.strip().split(maxsplit=1)
    cmd = cmd_parts[0].lower()
    arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""

    if cmd in ["/start", "/help", "help", "halo", "/menu", "menu", "🎛️ buka dashboard menu", "🎛️ dashboard", "🎛️"]:
        welcome_text = (
            "🎛️ <b>MAC MINI COMMAND CENTER</b>\n"
            "Perangkat: <b>Subuh's Mac mini</b> (<code>10.66.66.4</code>)\n"
            "------------------------------------\n"
            "Silakan pilih menu cepat pada tombol interaktif di bawah atau gunakan Quick Action Menu di bawah layar:"
        )
        send_message(token, chat_id, welcome_text, reply_markup=get_dashboard_inline_keyboard())
        send_message(token, chat_id, "👇 <i>Quick Action Menu di bawah siap digunakan:</i>", reply_markup=get_persistent_menu_keyboard())
        return

    # /status
    if cmd in ["/status", "📊 status mac", "📊 status", "📊"]:
        status_markup = {
            "inline_keyboard": [
                [{"text": "🔄 Refresh Status", "callback_data": "act_status"}],
                [{"text": "🎛️ Buka Dashboard", "callback_data": "act_dashboard"}]
            ]
        }
        send_message(token, chat_id, get_system_status_text(), reply_markup=status_markup)
        return

    # /stop
    if cmd in ["/stop", "/stopaudio", "🛑 stop audio", "🛑 stop", "🛑"]:
        mac_player.stop_mac_audio()
        gh_res = cast_manager.stop_google_home_cast()
        gh_icon = "✅" if gh_res.get("status") == "success" else "⚠️"
        stop_text = (
            "🛑 <b>PERINTAH STOP DIJALANKAN</b>\n\n"
            "🔊 <b>Speaker Mac (afplay/say):</b> Dihentikan seketika.\n"
            f"📢 <b>Google Home (Cast):</b> {gh_icon} {html.escape(gh_res.get('message', ''))}"
        )
        send_message(token, chat_id, stop_text)
        return

    # /qaadd Judul | Kalimat - Tambah Quick Action cepat dari Telegram
    if cmd in ["/qaadd", "/addqa"]:
        if not arg:
            send_message(
                token,
                chat_id,
                "➕ <b>TAMBAH QUICK ACTION:</b>\n"
                "Format: <code>/qaadd Judul | Kalimat broadcast</code>\n"
                "Contoh: <code>/qaadd Makan Siang | Perhatian, makan siang sudah siap</code>",
            )
            return
        raw_qa = arg.strip()
        atype = "tts"
        icon_qa = "📢"
        if raw_qa.lower().startswith("ga:"):
            atype = "assistant"
            icon_qa = "🤖"
            raw_qa = raw_qa[3:].strip()
        if "|" in raw_qa:
            t_part, m_part = raw_qa.split("|", 1)
            title_qa, msg_qa = t_part.strip(), m_part.strip()
        else:
            title_qa, msg_qa = raw_qa[:30], raw_qa
        res = db_manager.add_quick_action(
            title=title_qa, message=msg_qa, voice_type="wanita1", volume=70,
            restore_volume=50, use_chime=True, repeat_count=1, action_type=atype, icon=icon_qa,
        )
        if res.get("status") == "success":
            txt_d, mk_d = build_qa_detail_content(res["quick_action"]["id"])
            send_message(token, chat_id, "✅ Quick Action berhasil disimpan!\n\n" + txt_d, reply_markup=mk_d)
        else:
            send_message(token, chat_id, f"⚠️ {html.escape(res.get('message', 'Gagal menyimpan'))}")
        return

    # /qa atau /quick [kalimat] - Buka menu CRUD Quick Action atau Broadcast Instan Suara Wanita + Bel
    if cmd in ["/qa", "/quick", "/quickaction"]:
        if arg:
            res = quick_action_runner.execute_quick_action(
                adhoc={
                    "action_type": "tts",
                    "message": arg,
                    "voice_type": "wanita1",
                    "volume": 70,
                    "restore_volume": 50,
                    "use_chime": True,
                    "repeat_count": 1,
                }
            )
            icon = "✅" if res.get("status") == "success" else "⚠️"
            send_message(token, chat_id, f"{icon} {html.escape(res.get('message', 'Selesai'))}")
            return
        txt_m, mk_m = build_qa_menu_content()
        send_message(token, chat_id, txt_m, reply_markup=mk_m)
        return

    # /ga atau /assistant <perintah> - Kirim teks langsung ke Google Assistant SDK (Opsi 1)
    if cmd in ["/ga", "/assistant", "/asisten"]:
        if not arg:
            st = google_assistant_client.get_status()
            auth_str = "🟢 Terhubung" if st.get("is_authorized") else "🟡 Belum Login OAuth"
            send_message(
                token,
                chat_id,
                f"🤖 <b>GOOGLE ASSISTANT SDK ({auth_str}):</b>\n\n"
                "Kirim perintah teks langsung ke ekosistem Google Home Anda (tanpa mikrofon, balasan diputar di Google Home & dikirim ke Telegram):\n"
                "<code>/ga jam berapa sekarang</code>\n"
                "<code>/ga nyalakan lampu ruang tamu</code>\n"
                "<code>/ga cuaca hari ini</code>",
            )
            return
        res = google_assistant_client.send_text_command_safe(arg)
        icon = "✅" if res.get("status") == "success" else "⚠️"
        reply_txt = res.get("reply") or res.get("message") or "Selesai"
        audio_p = res.get("audio_path")
        if res.get("status") == "success" and audio_p and os.path.isfile(audio_p):
            threading.Thread(
                target=cast_manager.broadcast_audio_file_to_google_home,
                args=(audio_p,),
                kwargs={"title": f"Google Assistant: {arg[:40]}"},
                daemon=True,
            ).start()
            reply_txt += "\n🔊 <i>Balasan suara diputar di Google Home!</i>"
        send_message(token, chat_id, f"{icon} <b>Google Assistant:</b>\n{html.escape(reply_txt)}" if "🔊" not in reply_txt else f"{icon} <b>Google Assistant:</b>\n{html.escape(res.get('reply') or res.get('message') or 'Selesai')}\n🔊 <i>Balasan suara diputar di Google Home!</i>")
        if audio_p and os.path.isfile(audio_p):
            send_file_multipart(token, chat_id, "sendAudio", "audio", audio_p, caption=f"🤖 Balasan Google Assistant: {arg[:60]}")
        return

    # /askgh /heygoogle /okgoogle <perintah> - Otomatis pakai Google Assistant SDK jika terhubung
    if cmd in ["/askgh", "/heygoogle", "/okgoogle"]:
        if not arg:
            send_message(
                token,
                chat_id,
                "🤖 <b>PERINTAH GOOGLE ASSISTANT:</b>\n\n"
                "Kirim perintah langsung ke Google Assistant (balasan suara otomatis diputar di Google Home):\n"
                "Contoh:\n<code>/okgoogle jam berapa sekarang</code>\n<code>/heygoogle nyalakan lampu ruang tamu</code>",
            )
            return
        if google_assistant_client.get_status().get("is_authorized"):
            res = google_assistant_client.send_text_command_safe(arg)
            icon = "✅" if res.get("status") == "success" else "⚠️"
            reply_txt = res.get("reply") or res.get("message") or "Selesai"
            audio_p = res.get("audio_path")
            if res.get("status") == "success" and audio_p and os.path.isfile(audio_p):
                threading.Thread(
                    target=cast_manager.broadcast_audio_file_to_google_home,
                    args=(audio_p,),
                    kwargs={"title": f"Google Assistant: {arg[:40]}"},
                    daemon=True,
                ).start()
                send_message(token, chat_id, f"{icon} <b>Google Assistant:</b>\n{html.escape(reply_txt)}\n🔊 <i>Balasan suara diputar di Google Home!</i>")
                send_file_multipart(token, chat_id, "sendAudio", "audio", audio_p, caption=f"🤖 Balasan Google Assistant: {arg[:60]}")
            else:
                send_message(token, chat_id, f"{icon} <b>Google Assistant:</b>\n{html.escape(reply_txt)}")
            return
        hw = "OK Google" if cmd == "/okgoogle" else "Hey Google"
        res = mac_player.ask_google_home_via_mac(arg, hotword=hw)
        icon = "✅" if res.get("status") == "success" else "⚠️"
        send_message(token, chat_id, f"{icon} {html.escape(res.get('message', ''))}")
        return

    # /clip [teks] - Sinkronisasi Clipboard Mac
    if cmd in ["/clip", "/clipboard"]:
        if arg:
            subprocess.run(["pbcopy"], input=arg.encode("utf-8"), timeout=3)
            send_message(token, chat_id, "✅ Teks berhasil disalin ke <b>Clipboard Mac</b>!")
        else:
            clip = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3).stdout
            send_message(token, chat_id, f"📋 <b>Clipboard Mac:</b>\n<pre>{html.escape(clip[:3500])}</pre>" if clip else "📋 Clipboard Mac kosong.")
        return

    # /macvol [0-100 | + | - | mute]
    if cmd in ["/macvol", "/volmac"]:
        a = arg.strip().lower()
        if not a:
            v = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True, timeout=3).stdout.strip()
            send_message(token, chat_id, f"🔊 Volume Mac: <b>{html.escape(v)}%</b>\nContoh: <code>/macvol 50</code>, <code>/macvol mute</code>")
            return
        if a in ["mute", "unmute"]:
            subprocess.run(["osascript", "-e", "set volume output muted not (output muted of (get volume settings))"], timeout=3)
            send_message(token, chat_id, "🔇 Status mute Mac diubah!")
        elif a.replace("%", "").isdigit():
            nv = max(0, min(100, int(a.replace("%", ""))))
            subprocess.run(["osascript", "-e", f"set volume output volume {nv}"], timeout=3)
            send_message(token, chat_id, f"🔊 Volume Mac diatur ke <b>{nv}%</b>!")
        return

    # /yt atau /casturl [link YouTube / MP4 / M3U8 / MP3 / Gambar]
    if cmd in ["/yt", "/youtube", "/casturl", "/tv"]:
        raw_url = arg.strip()
        if not raw_url:
            send_message(
                token,
                chat_id,
                "📺 <b>CAST YOUTUBE & URL MEDIA KE TV / SPEAKER</b>\n\n"
                "Kirim perintah diikuti link YouTube, Shorts, IPTV (.m3u8), MP4, MP3, atau Gambar:\n"
                "<code>/yt https://youtu.be/dQw4w9WgXcQ</code>\n"
                "<code>/casturl https://example.com/stream.m3u8</code>",
            )
            return
        send_message(token, chat_id, f"📺 <i>Mengirim ke TV Stick Mi / Cast...</i>\n<code>{html.escape(raw_url[:100])}</code>")
        res = cast_manager.cast_url_to_device(raw_url)
        icon = "✅" if res.get("status") == "success" else "⚠️"
        send_message(token, chat_id, f"{icon} {html.escape(res.get('message', ''))}")
        return

    # /ghvol [0-100 | + | - | mute | unmute]
    if cmd in ["/ghvol", "/castvol", "/ghvolume"]:
        a = arg.strip().lower()
        if not a:
            st = cast_manager.get_google_home_playback_status()
            v = st.get("volume_level")
            m = "🔇 Muted" if st.get("volume_muted") else "🔊 Aktif"
            send_message(
                token,
                chat_id,
                f"📢 <b>Volume Google Home:</b> <code>{v if v is not None else '--'}%</code> ({m})\n\n"
                "Contoh:\n<code>/ghvol 60</code>\n<code>/ghvol +</code> / <code>/ghvol -</code>\n<code>/ghvol mute</code> / <code>/ghvol unmute</code>",
            )
            return
        if a in ["mute", "bisu", "off"]:
            res = cast_manager.set_google_home_volume(muted=True)
        elif a in ["unmute", "on"]:
            res = cast_manager.set_google_home_volume(muted=False)
        elif a in ["+", "up", "naik"]:
            res = cast_manager.set_google_home_volume(delta_percent=10)
        elif a in ["-", "down", "turun"]:
            res = cast_manager.set_google_home_volume(delta_percent=-10)
        elif a.replace("%", "").isdigit():
            res = cast_manager.set_google_home_volume(volume_percent=int(a.replace("%", "")))
        else:
            send_message(token, chat_id, "⚠️ Format tidak valid. Contoh: <code>/ghvol 50</code>, <code>/ghvol +</code>, <code>/ghvol mute</code>")
            return
        icon = "✅" if res.get("status") == "success" else "⚠️"
        send_message(token, chat_id, f"{icon} {html.escape(res.get('message', ''))}")
        return

    # /nowplaying
    if cmd in ["/nowplaying", "/playing", "📻 now playing", "📻"]:
        text_np, markup_np = build_now_playing_content()
        send_message(token, chat_id, text_np, reply_markup=markup_np)
        return

    # /screen
    if cmd in ["/screen", "📸 screenshot layar", "📸 screenshot", "📸"]:
        send_message(token, chat_id, "📸 Mengambil screenshot layar Mac...")
        temp_img = "/tmp/tg_screen.jpg"
        try:
            subprocess.run(
                ["screencapture", "-x", "-t", "jpg", temp_img],
                timeout=5,
                check=True,
            )
            if os.path.exists(temp_img):
                send_file_multipart(
                    token,
                    chat_id,
                    "sendPhoto",
                    "photo",
                    temp_img,
                    caption="🖥️ Tampilan layar Mac saat ini",
                    reply_markup=get_screen_inline_keyboard()
                )
                try:
                    os.remove(temp_img)
                except Exception:
                    pass
                return
        except Exception as e:
            send_message(
                token, chat_id, f"❌ Gagal mengambil screenshot: {str(e)}"
            )
        return

    # Quick Action: Cast TV / YouTube
    if cmd in ["📺 cast tv / yt", "📺 cast tv", "📺"]:
        force_reply_markup = {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Tempel link YouTube / URL atau ketik pesan TTS untuk TV..."
        }
        prompt = (
            "📺 <b>CAST KE TV STICK MI (YOUTUBE / URL / TTS):</b>\n\n"
            "Langsung balas pesan ini dengan:\n"
            "• <b>Link YouTube / Shorts / .m3u8 / MP4</b> → langsung tayang di TV!\n"
            "• <b>Teks pengumuman</b> → dibacakan (TTS) lengkap dengan visual art di TV!\n\n"
            "💡 <i>Atau gunakan perintah cepat:</i> <code>/yt &lt;link&gt;</code>"
        )
        send_message(token, chat_id, prompt, reply_markup=force_reply_markup)
        return

    # Quick Action: Google Home
    if cmd in ["📢 google home", "📢", "/cast", "/gh"]:
        if arg:
            res = cast_manager.broadcast_to_google_home(arg, voice_type="wanita1")
            if res.get("status") == "success":
                send_message(token, chat_id, f"📢 Berhasil disiarkan (Gadis) ke <b>{html.escape(res.get('device', 'Google Home'))}</b>!\n<i>\"{html.escape(arg)}\"</i>")
            else:
                send_message(token, chat_id, f"❌ {html.escape(res.get('message', 'Gagal'))}")
            return

        force_reply_markup = {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Ketik pesan pengumuman di sini..."
        }
        prompt = (
            "📢 <b>SIARKAN KE GOOGLE HOME:</b>\n\n"
            "Silakan langsung ketik pesan pengumuman Anda di bawah:\n"
            "<i>(Kolom ketik sudah aktif, Anda cukup ketik pesan dan tekan Kirim)</i>\n\n"
            "💡 <i>Atau salin perintah cepat dengan 1 tap:</i> <code>/cast </code>"
        )
        send_message(token, chat_id, prompt, reply_markup=force_reply_markup)
        return

    # Quick Action: Speaker Mac
    if cmd in ["🔊 speaker mac", "🔊", "/say"]:
        if arg:
            speak_indonesian(arg)
            send_message(token, chat_id, f'🔊 Mac mengucapkan: "<i>{html.escape(arg)}</i>"')
            return

        force_reply_markup = {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Ketik kalimat untuk speaker Mac..."
        }
        prompt = (
            "🔊 <b>BICARA LEWAT SPEAKER MAC MINI:</b>\n\n"
            "Silakan ketik kalimat yang ingin diucapkan oleh speaker Mac mini:\n"
            "<i>(Kolom ketik sudah aktif, Anda cukup ketik kalimat dan tekan Kirim)</i>\n\n"
            "💡 <i>Atau salin perintah cepat dengan 1 tap:</i> <code>/say </code>"
        )
        send_message(token, chat_id, prompt, reply_markup=force_reply_markup)
        return

    # Quick Action: Target Suara
    if cmd in ["/mode", "/target", "🎯 target suara", "🎯 target", "🎯"]:
        tgt = DEFAULT_VOICE_TARGET
        m_label = "Google Home" if tgt == "gh" else ("Speaker Mac mini" if tgt == "mac" else "Keduanya (Mac + Home)")
        text = (
            f"🎯 <b>PENGATURAN TARGET SUARA TELEGRAM:</b>\n\n"
            f"Target Aktif Saat Ini: <b>{m_label}</b>\n\n"
            f"Silakan klik tombol di bawah untuk memilih perangkat tujuan saat Anda mengirim Voice Note atau pesan suara:"
        )
        send_message(token, chat_id, text, reply_markup=get_mode_inline_keyboard())
        return

    # Quick Action: Pustaka Audio Server (SQLite)
    if cmd in ["/audio", "/audiolist", "/audios", "🎵 pustaka audio", "🎵 audio", "🎵"]:
        text_menu, markup_menu = build_audio_library_menu_content()
        send_message(token, chat_id, text_menu, reply_markup=markup_menu)
        return

    # Quick Action: Jadwal Suara / Schedule Manager (SQLite)
    if cmd in ["/schedule", "/jadwal", "/schedules", "⏰ jadwal suara", "⏰ jadwal", "⏰"]:
        if not arg:
            text_menu, markup_menu = build_schedule_menu_content()
            send_message(token, chat_id, text_menu, reply_markup=markup_menu)
            return

        # Parsing argumen fleksibel: e.g. "10m Makan siang", "2026-11-22 08:30 both Sholat Dhuha", "15m audio:Bel"
        raw_arg = arg.strip()
        tokens = raw_arg.split()
        target = "both"
        mode = "tts"
        audio_id = None
        label = ""

        # Cek format 2 token untuk waktu: "2026-11-22 08:30", "besok 08:00", "daily 07:00"
        if len(tokens) >= 2 and (
            re.match(r"^\d{4}-\d{2}-\d{2}$", tokens[0]) or
            tokens[0].lower() in ["daily", "harian", "tiap", "besok", "tomorrow", "lusa"]
        ):
            time_str = f"{tokens[0]} {tokens[1]}"
            rest_tokens = tokens[2:]
        else:
            time_str = tokens[0]
            rest_tokens = tokens[1:]

        # Cek apakah token berikutnya adalah target perangkat (gh, mac, both)
        if rest_tokens and rest_tokens[0].lower() in ["gh", "cast", "mac", "say", "both", "all", "keduanya"]:
            tgt_token = rest_tokens[0].lower()
            if tgt_token in ["gh", "cast"]:
                target = "cast"
            elif tgt_token in ["mac", "say"]:
                target = "say"
            else:
                target = "both"
            rest_tokens = rest_tokens[1:]

        message_str = " ".join(rest_tokens).strip()

        # Cek apakah pesan memilih file audio: e.g. "audio:Bel" atau "audio:aud_123"
        m_audio = re.search(r"audio:([^\s]+)", message_str, re.IGNORECASE)
        if m_audio:
            aq = m_audio.group(1).strip()
            all_auds = db_manager.list_audios()
            matched_aud = next((a for a in all_auds if aq.lower() in a["id"].lower() or aq.lower() in a["name"].lower() or aq.lower() in a["original_filename"].lower()), None)
            if matched_aud:
                mode = "audio"
                audio_id = matched_aud["id"]
                label = f"Audio: {matched_aud['name']}"
                message_str = re.sub(r"audio:[^\s]+", "", message_str).strip() or matched_aud["name"]
            else:
                send_message(token, chat_id, f"⚠️ File audio '{aq}' tidak ditemukan di pustaka database server.")
                return

        if not message_str and mode == "tts":
            message_str = "Pengumuman Suara Terjadwal"

        res = smart_reminders.add_schedule(
            message=message_str,
            time_str=time_str,
            target=target,
            voice_type="wanita1",
            label=label,
            mode=mode,
            audio_id=audio_id
        )

        if res.get("status") == "success":
            tgt_display = res.get("target_display", target)
            daily_tag = " 🔁 <i>(Rutinitas Harian)</i>" if res.get("is_daily") else ""
            if mode == "audio":
                content_line = f"🎵 <b>File Audio:</b> {html.escape(label)}"
            else:
                content_line = f"📢 <b>Pesan:</b> \"<i>{html.escape(res.get('message'))}</i>\"\n🗣️ <b>Suara:</b> Wanita 1 (Gadis Neural)"

            reply = (
                f"✅ <b>Jadwal Pengumuman Berhasil Disimpan ke SQLite!</b>{daily_tag}\n\n"
                f"📌 <b>Judul:</b> {html.escape(res.get('label'))}\n"
                f"{content_line}\n"
                f"⏱️ <b>Waktu:</b> {res.get('remaining_readable')} ({res.get('target_readable')})\n"
                f"🎯 <b>Target:</b> {tgt_display}"
            )
            send_message(token, chat_id, reply)
        else:
            send_message(token, chat_id, f"❌ {res.get('message', 'Format waktu tidak valid')}")
        return

    # Quick Action: Kendali Musik
    if cmd in ["/music", "🎵 kendali musik", "🎵 musik", "🎵"]:
        res = control_music("info")
        send_message(token, chat_id, f"🎵 <b>KENDALI PEMUTAR MUSIK MAC:</b>\n\nStatus: <b>{html.escape(res)}</b>", reply_markup=get_music_inline_keyboard())
        return

    # Quick Action: Intercom Layar
    if cmd in ["💬 intercom layar", "💬 intercom", "💬", "/chat", "/ask", "/pop", "/tanya"]:
        if arg:
            send_message(token, chat_id, f"💬 <b>Pop-up terkirim ke layar Mac!</b>\n<i>\"{html.escape(arg)}\"</i>\n\n⏳ <i>Menunggu balasan dari orang di depan Mac...</i>")
            handle_interactive_chat_async(token, chat_id, "Telegram", arg)
            return

        force_reply_markup = {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Tulis pesan untuk orang di depan Mac..."
        }
        prompt = (
            "💬 <b>INTERCOM POP-UP MONITOR MAC:</b>\n\n"
            "Silakan ketik pesan untuk memunculkan dialog pop-up di layar Mac:\n"
            "<i>(Orang di depan Mac bisa mengetik balasan langsung ke chat ini)</i>\n\n"
            "💡 <i>Atau salin perintah cepat dengan 1 tap:</i> <code>/chat </code>"
        )
        send_message(token, chat_id, prompt, reply_markup=force_reply_markup)
        return

    # Quick Action: Kunci Layar
    if cmd in ["/sleep", "🌙 kunci layar", "🌙 sleep", "🌙"]:
        subprocess.run(["pmset", "displaysleepnow"], timeout=3)
        send_message(token, chat_id, "🌙 Layar monitor Mac telah dimatikan / dikunci.")
        return

    # /say
    if cmd == "/say":
        if not arg:
            send_message(
                token, chat_id, "⚠️ Contoh penggunaan: <code>/say Halo Subuh</code>"
            )
            return
        speak_indonesian(arg)
        send_message(
            token, chat_id, f'🔊 Mac mengucapkan (Bahasa Indonesia): "<i>{html.escape(arg)}</i>"'
        )
        return

    # /voice (Kirim audio pesan suara Bahasa Indonesia langsung ke chat Telegram)
    if cmd == "/voice":
        if not arg:
            send_message(token, chat_id, "⚠️ Contoh penggunaan: <code>/voice Halo apa kabar?</code>")
            return
        send_message(token, chat_id, "🎙️ Menghasilkan pesan suara...")
        audio_file = generate_indonesian_audio(arg, "female")
        if audio_file and os.path.exists(audio_file):
            send_file_multipart(token, chat_id, "sendAudio", "audio", audio_file, caption=f'🎙️ "{html.escape(arg)}"')
            try:
                os.remove(audio_file)
            except Exception:
                pass
        else:
            send_message(token, chat_id, "❌ Gagal membuat pesan suara.")
        return

    # /volume
    if cmd == "/volume":
        try:
            vol = int(arg)
            vol = max(0, min(100, vol))
            subprocess.run(
                ["osascript", "-e", f"set volume output volume {vol}"],
                timeout=3,
            )
            send_message(token, chat_id, f"🔊 Volume speaker Mac diset ke {vol}%")
        except Exception:
            send_message(
                token, chat_id, "⚠️ Format salah. Contoh: <code>/volume 70</code>"
            )
        return

    # /open
    if cmd == "/open":
        if not arg:
            send_message(
                token,
                chat_id,
                "⚠️ Contoh: <code>/open Safari</code> atau <code>/open https://google.com</code>",
            )
            return
        if arg.startswith("http://") or arg.startswith("https://"):
            subprocess.Popen(["open", arg])
            send_message(
                token, chat_id, f"🌐 Membuka website di Mac: <code>{arg}</code>"
            )
        else:
            subprocess.Popen(["open", "-a", arg])
            send_message(
                token,
                chat_id,
                f"🚀 Membuka aplikasi di Mac: <b>{html.escape(arg)}</b>",
            )
        return

    # /cmd
    if cmd == "/cmd":
        if not arg:
            send_message(
                token, chat_id, "⚠️ Contoh penggunaan: <code>/cmd ls -la</code>"
            )
            return
        send_message(token, chat_id, f"⏳ Menjalankan: <code>{html.escape(arg)}</code>...")
        try:
            proc = subprocess.run(
                arg,
                shell=True,
                executable="/bin/zsh",
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.path.expanduser("~"),
            )
            output = proc.stdout + proc.stderr
            out_text = (
                f"<pre>{html.escape(output.strip() or '(selesai tanpa output)')}</pre>"
            )
            send_message(token, chat_id, out_text)
        except subprocess.TimeoutExpired:
            send_message(token, chat_id, "⏱️ Perintah timeout (lebih dari 30 detik).")
        except Exception as e:
            send_message(token, chat_id, f"❌ Error: {html.escape(str(e))}")
        return

    # /files
    if cmd == "/files":
        items = os.listdir(SHARED_DIR)
        files = [
            f
            for f in items
            if os.path.isfile(os.path.join(SHARED_DIR, f)) and not f.startswith(".")
        ]
        if not files:
            send_message(
                token,
                chat_id,
                "📁 Folder <code>shared_files</code> di Mac masih kosong.",
            )
            return
        lines = [f"📁 <b>Daftar File di Mac ({len(files)} file):</b>\n"]
        for f in sorted(files):
            size = format_size(os.path.getsize(os.path.join(SHARED_DIR, f)))
            lines.append(f"• <code>{f}</code> ({size})")
        lines.append("\nKetik <code>/get &lt;nama_file&gt;</code> untuk mendownloadnya.")
        send_message(token, chat_id, "\n".join(lines))
        return

    # /get
    if cmd == "/get":
        if not arg:
            send_message(
                token,
                chat_id,
                "⚠️ Contoh: <code>/get catatan_kerja.txt</code>",
            )
            return
        safe_name = os.path.basename(arg)
        filepath = os.path.join(SHARED_DIR, safe_name)
        if not os.path.exists(filepath):
            send_message(
                token,
                chat_id,
                f"❌ File <code>{html.escape(safe_name)}</code> tidak ditemukan di Mac.",
            )
            return
        send_message(token, chat_id, f"📤 Mengirim {safe_name} ke chat...")
        send_file_multipart(token, chat_id, "sendDocument", "document", filepath)
        return

    # /notify
    if cmd == "/notify":
        if not arg:
            send_message(
                token, chat_id, "⚠️ Contoh: <code>/notify Rapat jam 2 siang dimulai</code>"
            )
            return
        send_desktop_notification("Pesan dari Telegram", arg)
        send_message(
            token,
            chat_id,
            f'🔔 Banner notifikasi muncul di layar Mac: "<i>{html.escape(arg)}</i>"\n\n💡 <i>Gunakan <code>/chat &lt;pesan&gt;</code> jika ingin orang di Mac bisa mengetik balasan langsung ke chat ini.</i>',
        )
        return

    # /chat & /ask & /pop (Interactive Pop-Up Chat with direct reply)
    if cmd in ["/chat", "/ask", "/pop", "/tanya"]:
        if not arg:
            send_message(
                token,
                chat_id,
                "⚠️ Contoh: <code>/chat Kamu lagi di depan komputer?</code>\n\nPesan akan muncul di tengah layar monitor Mac dengan kolom teks, sehingga orang di Mac bisa mengetik balasan dan terkirim langsung ke chat ini!",
            )
            return
        send_message(
            token,
            chat_id,
            f"💬 <b>Pop-up terkirim ke layar Mac!</b>\n<i>\"{html.escape(arg)}\"</i>\n\n⏳ <i>Menunggu balasan dari orang di depan komputer...</i>",
        )
        handle_interactive_chat_async(token, chat_id, "Telegram", arg)
        return

    # /top (Task Manager)
    if cmd == "/top":
        send_message(token, chat_id, "📊 Memeriksa proses CPU Mac...")
        try:
            proc = subprocess.run(
                ["ps", "-axo", "pid,%cpu,%mem,comm", "-r"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            lines = proc.stdout.strip().split("\n")
            out_lines = ["📊 <b>Top Proses di Mac mini:</b>\n"]
            if len(lines) > 1:
                for line in lines[1:9]:
                    parts = line.strip().split(maxsplit=3)
                    if len(parts) >= 4:
                        p_name = os.path.basename(parts[3])
                        out_lines.append(
                            f"• <code>{parts[0]}</code> <b>{html.escape(p_name)}</b> | CPU: <b>{parts[1]}%</b> | RAM: {parts[2]}%"
                        )
                out_lines.append("\nKetik <code>/kill &lt;PID&gt;</code> untuk mematikan proses.")
                send_message(token, chat_id, "\n".join(out_lines))
            else:
                send_message(token, chat_id, "Tidak dapat membaca daftar proses.")
        except Exception as e:
            send_message(token, chat_id, f"❌ Error: {str(e)}")
        return

    # /kill
    if cmd == "/kill":
        if not arg:
            send_message(token, chat_id, "⚠️ Contoh: <code>/kill 1234</code>")
            return
        try:
            pid = int(arg)
            os.kill(pid, 15)
            send_message(token, chat_id, f"✅ Proses dengan PID <code>{pid}</code> telah dihentikan.")
        except Exception as e:
            send_message(token, chat_id, f"❌ Gagal mematikan proses: {str(e)}")
        return

    # /music
    if cmd == "/music":
        action = arg.lower() if arg else "info"
        if action not in ["play", "pause", "playpause", "next", "prev", "info"]:
            action = "playpause"
        res = control_music(action)
        send_message(token, chat_id, f"🎵 <b>Status Media Mac:</b>\n{html.escape(res)}")
        return

    # /zip
    if cmd == "/zip":
        if not arg:
            send_message(token, chat_id, "⚠️ Contoh: <code>/zip nama_folder</code> (di shared_files)")
            return
        target = os.path.join(SHARED_DIR, os.path.basename(arg))
        if not os.path.exists(target):
            send_message(token, chat_id, f"❌ <code>{html.escape(arg)}</code> tidak ditemukan di shared_files.")
            return
        send_message(token, chat_id, f"📦 Mengompres {os.path.basename(target)} ke ZIP...")
        try:
            base_name = target.rstrip("/")
            out_zip = base_name + ".zip"
            if os.path.isdir(target):
                shutil.make_archive(base_name, "zip", os.path.dirname(target), os.path.basename(target))
            else:
                with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(target, os.path.basename(target))
            send_message(token, chat_id, f"✅ Selesai! File ZIP tersimpan: <code>{os.path.basename(out_zip)}</code>")
        except Exception as e:
            send_message(token, chat_id, f"❌ Gagal membuat zip: {str(e)}")
        return

    # /cast_pria (Mode Suara Pria Ardi)
    if cmd in ["/cast_pria", "/gh_pria"]:
        if not arg:
            send_message(token, chat_id, "⚠️ Contoh: <code>/cast_pria Perhatian, mobil sudah di depan rumah!</code>")
            return
        res = cast_manager.broadcast_to_google_home(arg, voice_type="pria")
        if res.get("status") == "success":
            dev = res.get("device", "Google Home")
            send_message(token, chat_id, f"📢 <b>Disiarkan Suara Pria (Ardi):</b>\n• Speaker: <b>{html.escape(dev)}</b>\n• Pesan: <i>&quot;{html.escape(arg)}&quot;</i>")
        else:
            send_message(token, chat_id, f"❌ Gagal menyiarkan: {res.get('message')}")
        return

    # /cast_wanita2 (Mode Suara Wanita 2 Google ID)
    if cmd in ["/cast_wanita2", "/gh_wanita2"]:
        if not arg:
            send_message(token, chat_id, "⚠️ Contoh: <code>/cast_wanita2 Selamat pagi semuanya!</code>")
            return
        res = cast_manager.broadcast_to_google_home(arg, voice_type="wanita2")
        if res.get("status") == "success":
            dev = res.get("device", "Google Home")
            send_message(token, chat_id, f"📢 <b>Disiarkan Suara Wanita 2 (Google ID):</b>\n• Speaker: <b>{html.escape(dev)}</b>\n• Pesan: <i>&quot;{html.escape(arg)}&quot;</i>")
        else:
            send_message(token, chat_id, f"❌ Gagal menyiarkan: {res.get('message')}")
        return

    # /cast & /gh (Google Home Announcer - Default: Suara Wanita 1 Gadis)
    if cmd in ["/cast", "/gh"]:
        if not arg:
            send_message(token, chat_id, "⚠️ Contoh: <code>/cast Makan malam sudah siap!</code> (atau cukup ketik teks langsung tanpa tanda /)")
            return
        res = cast_manager.broadcast_to_google_home(arg, voice_type="wanita1")
        if res.get("status") == "success":
            send_message(token, chat_id, f"📢 Berhasil disiarkan (Wanita 1 Gadis) ke <b>{html.escape(res.get('device', 'Google Home'))}</b>!\n<i>&quot;{html.escape(arg)}&quot;</i>")
        else:
            send_message(token, chat_id, f"❌ {html.escape(res.get('message', 'Gagal'))}")
        return

    # /cast_both & /say_both (Siarkan ke Speaker Mac DAN Google Home sekaligus)
    if cmd in ["/cast_both", "/say_both", "/both"]:
        if not arg:
            send_message(token, chat_id, "⚠️ Contoh: <code>/cast_both Pengumuman penting untuk semua!</code>")
            return
        import threading
        threading.Thread(target=lambda: speak_indonesian(arg), daemon=True).start()
        res = cast_manager.broadcast_to_google_home(arg, voice_type="wanita1")
        send_message(
            token,
            chat_id,
            f"📢 <b>Disiarkan ke Speaker Mac & Google Home:</b>\n<i>\"{html.escape(arg)}\"</i>"
        )
        return

    # /mode_mac, /mode_gh, /mode_both (Ganti target default voice note Telegram)
    if cmd in ["/mode_mac", "/target_mac"]:
        DEFAULT_VOICE_TARGET = "mac"
        send_message(token, chat_id, "🎯 <b>Mode Voice Note Diubah: SPEAKER MAC</b>\nMulai sekarang, semua voice note yang Anda kirim otomatis langsung diputar di speaker fisik Mac mini!")
        return

    if cmd in ["/mode_gh", "/target_gh"]:
        DEFAULT_VOICE_TARGET = "gh"
        send_message(token, chat_id, "🎯 <b>Mode Voice Note Diubah: GOOGLE HOME</b>\nMulai sekarang, semua voice note yang Anda kirim otomatis disiarkan ke speaker Google Home!")
        return

    if cmd in ["/mode_both", "/target_both"]:
        DEFAULT_VOICE_TARGET = "both"
        send_message(token, chat_id, "🎯 <b>Mode Voice Note Diubah: KEDUANYA (Mac + Google Home)</b>\nMulai sekarang, semua voice note yang Anda kirim diputar di speaker Mac dan Google Home sekaligus!")
        return

    if cmd in ["/mode", "/target"]:
        m_label = "Google Home" if DEFAULT_VOICE_TARGET == "gh" else ("Speaker Mac mini" if DEFAULT_VOICE_TARGET == "mac" else "Keduanya (Mac + Google Home)")
        send_message(
            token,
            chat_id,
            f"🎯 <b>Target Voice Note Saat Ini:</b> <b>{m_label}</b>\n\n"
            f"Pilihan mode:\n"
            f"• <code>/mode_mac</code> - Putar di speaker Mac mini\n"
            f"• <code>/mode_gh</code> - Siarkan di Google Home\n"
            f"• <code>/mode_both</code> - Siarkan ke Mac + Google Home sekaligus"
        )
        return
    # /cast_list
    if cmd in ["/cast_list", "/gh_list"]:
        res = cast_manager.get_cast_devices(force_refresh=True)
        if res.get("status") == "success":
            devices = res.get("devices", [])
            if not devices:
                send_message(
                    token,
                    chat_id,
                    "⚠️ Tidak ada speaker Google Home / Cast yang terdeteksi di Wi-Fi saat ini.\nPastikan Mac dan Google Home terhubung ke Wi-Fi yang sama.",
                )
            else:
                lines = [f"📢 <b>Speaker Google Cast Ditemukan ({len(devices)}):</b>\n"]
                for d in devices:
                    lines.append(f"• <b>{d['name']}</b> ({d['model']}) - <code>{d['host']}</code>")
                lines.append("\n💡 <i>Cukup ketik teks chat biasa di sini untuk langsung menyiarkannya ke Google Home!</i>")
                send_message(token, chat_id, "\n".join(lines))
        elif res.get("status") == "not_installed":
            send_message(
                token,
                chat_id,
                "⚠️ Pustaka <code>pychromecast</code> belum terpasang di Mac.\nJalankan perintah ini di Terminal Mac:\n<code>pip3 install pychromecast</code>",
            )
        else:
            send_message(token, chat_id, f"❌ Error: {res.get('message')}")
        return

    # /sleep
    if cmd == "/sleep":
        subprocess.run(["pmset", "displaysleepnow"], timeout=3)
        send_message(token, chat_id, "🌙 Layar monitor Mac telah dimatikan.")
        return

    # DEFAULT FALLBACK:
    # Jika pengguna mengetik teks biasa tanpa tanda '/', LANGSUNG siarkan ke Google Home!
    if not text.startswith("/"):
        res = cast_manager.broadcast_to_google_home(text)
        if res.get("status") == "success":
            dev_name = res.get("device", "Google Home")
            send_message(
                token,
                chat_id,
                f"📢 Disiarkan langsung ke <b>{html.escape(dev_name)}</b>:\n<i>\"{html.escape(text)}\"</i>\n\n💡 <i>(Ketik /help untuk menu perintah komputer)</i>",
            )
        else:
            send_message(
                token,
                chat_id,
                f"❌ Gagal menyiarkan ke Google Home: {html.escape(res.get('message', ''))}\n\nKetik <b>/help</b> untuk melihat menu perintah.",
            )
        return

    # Default fallback untuk perintah slash yang tidak terdaftar
    send_message(
        token,
        chat_id,
        "❓ Perintah tidak dikenali. Ketik <b>/help</b> untuk melihat menu perintah.",
    )


def handle_voice_note(token, chat_id, file_id, caption=""):
    send_message(token, chat_id, "🎙️ <i>Menerima voice note... Memproses pemutaran suara asli...</i>")
    res = tg_request(token, "getFile", {"file_id": file_id})
    if not res.get("ok"):
        send_message(token, chat_id, "❌ Gagal mendapatkan info voice note dari Telegram.")
        return

    file_path = res["result"]["file_path"]
    download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    tmp_voice = f"/tmp/tg_voice_{int(time.time())}.ogg"

    try:
        dl_req = urllib.request.Request(download_url)
        with urllib.request.urlopen(dl_req, timeout=30, context=SSL_CTX) as resp, open(tmp_voice, "wb") as f:
            shutil.copyfileobj(resp, f)

        # Tentukan target tujuan suara (caption override > DEFAULT_VOICE_TARGET)
        target = DEFAULT_VOICE_TARGET
        cap_lower = (caption or "").lower()
        if any(k in cap_lower for k in ["#all", "semua"]):
            target = "all"
        elif any(k in cap_lower for k in ["#both", "keduanya"]):
            target = "both"
        elif any(k in cap_lower for k in ["#tv", "tv"]):
            target = "tv"
        elif any(k in cap_lower for k in ["#mac", "mac"]):
            target = "mac"
        elif any(k in cap_lower for k in ["#gh", "#home", "google"]):
            target = "gh"

        results_txt = []

        # 1. Mainkan di Speaker Mac mini (Anti-Overlap)
        if target in ["mac", "both", "all"]:
            mac_player.play_mac_audio(tmp_voice, title="Voice Note Telegram", source="voice_note")
            try:
                sc = 'display notification "Voice note dari Telegram sedang berbunyi di speaker Mac!" with title "🎙️ Telegram Voice Message" sound name "Glass"'
                subprocess.run(["osascript", "-e", sc], timeout=3)
            except Exception:
                pass
            results_txt.append("🔊 <b>Diputar di Speaker Mac mini & Layar</b>")

        # 2. Mainkan di Speaker Google Home
        if target in ["gh", "both", "all"]:
            cast_res = cast_manager.broadcast_audio_file_to_google_home(tmp_voice, target_device_name="192.168.0.198", title="Voice Note Pemilik")
            if cast_res.get("status") == "success":
                dev_name = cast_res.get("device", "Google Home")
                results_txt.append(f"📢 <b>Disiarkan di Google Home ({html.escape(dev_name)})</b>")
            else:
                results_txt.append(f"⚠️ Gagal ke Google Home: {cast_res.get('message')}")

        # 3. Mainkan di TV Stick Mi
        if target in ["tv", "all"]:
            tv_res = cast_manager.broadcast_audio_file_to_google_home(tmp_voice, target_device_name="tv", title="Voice Note Pemilik")
            if tv_res.get("status") == "success":
                dev_name = tv_res.get("device", "TV Stick Mi")
                results_txt.append(f"📺 <b>Disiarkan di TV ({html.escape(dev_name)})</b>")
            else:
                results_txt.append(f"⚠️ Gagal ke TV Stick Mi: {tv_res.get('message')}")

        # Jika caption mengandung #simpan atau #save, langsung simpan permanen ke Pustaka Audio!
        cap_raw = (caption or "").strip()
        if any(k in cap_raw.lower() for k in ("#simpan", "#save", "#pustaka")):
            clean_lbl = re.sub(r"#(simpan|save|pustaka|tv|gh|home|mac|both|all)\b", "", cap_raw, flags=re.I).strip()
            if not clean_lbl:
                clean_lbl = f"Audio Telegram ({time.strftime('%d/%m %H:%M')})"
            with open(tmp_voice, "rb") as _vf:
                _aud = db_manager.add_audio(name=clean_lbl, original_filename=os.path.basename(tmp_voice), data_bytes=_vf.read())
            results_txt.append(f"💾 <b>Otomatis disimpan ke Pustaka:</b> \"{html.escape(_aud['name'])}\"")

        if results_txt:
            status_msg = "\n".join(results_txt)
            send_message(
                token,
                chat_id,
                f"✅ <b>Voice Note Berhasil Diproses!</b>\n\n{status_msg}\n\n"
                f"<i>Ingin memutar ulang di perangkat lain? Klik tombol di bawah:</i>",
                reply_markup=get_voice_note_inline_keyboard(tmp_voice)
            )
        else:
            send_message(token, chat_id, "❌ Gagal memutar suara ke perangkat tujuan.")
    except Exception as e:
        send_message(token, chat_id, f"❌ Gagal memproses voice note: {str(e)}")

def handle_document_or_photo(token, chat_id, file_id, original_filename="", caption=""):
    send_message(token, chat_id, "📥 Mengunduh file ke Mac Anda...")
    # 1. getFile
    res = tg_request(token, "getFile", {"file_id": file_id})
    if not res.get("ok"):
        err_d = res.get("description") or res.get("error") or ""
        if "file is too big" in str(err_d).lower():
            send_message(token, chat_id, "⚠️ File melebihi batas 20 MB dari Telegram Bot API. Silakan unggah lewat Web Dashboard (<code>/?tab=ctrl</code>) yang mendukung file besar + Progress Bar.")
        else:
            send_message(token, chat_id, "❌ Gagal mendapatkan info file dari Telegram.")
        return

    file_path = res["result"]["file_path"]
    download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"

    if not original_filename:
        original_filename = os.path.basename(file_path)

    dest_path = os.path.join(SHARED_DIR, os.path.basename(original_filename))

    try:
        dl_req = urllib.request.Request(download_url)
        with urllib.request.urlopen(dl_req, timeout=60, context=SSL_CTX) as resp, open(dest_path, "wb") as f:
            shutil.copyfileobj(resp, f)
        size_str = format_size(os.path.getsize(dest_path))
        ext = os.path.splitext(original_filename)[1].lower()
        is_media = ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".mp3", ".wav", ".m4a")
        is_av = ext in (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".mp3", ".wav", ".m4a")

        cap_raw = (caption or "").strip()
        auto_saved_note = ""
        saved_aud = None
        if is_av and any(k in cap_raw.lower() for k in ("#simpan", "#save", "#pustaka")):
            clean_lbl = re.sub(r"#(simpan|save|pustaka|tv|gh|home|mac|both|all)\b", "", cap_raw, flags=re.I).strip()
            if not clean_lbl:
                clean_lbl = os.path.splitext(original_filename)[0]
            with open(dest_path, "rb") as _f:
                saved_aud = db_manager.add_audio(name=clean_lbl, original_filename=os.path.basename(original_filename), data_bytes=_f.read())
            auto_saved_note = f"\n• <b>Pustaka Media:</b> ✅ Otomatis tersimpan sebagai <b>{html.escape(saved_aud['name'])}</b>"

        kb = None
        if is_media:
            row = [{"text": "📺 Tayangkan di TV Stick Mi", "callback_data": f"cast_shared:{os.path.basename(original_filename)}"}]
            if is_av and not saved_aud:
                row.append({"text": "💾 Simpan ke Pustaka", "callback_data": f"save_shared_lib:{os.path.basename(original_filename)}"})
            elif saved_aud:
                row.append({"text": "📢 Speaker", "callback_data": f"p_aud_gh:{saved_aud['id']}"})
            kb = {"inline_keyboard": [row, [{"text": "🛑 Stop TV", "callback_data": "stop_gh:tv"}]]}
        send_message(
            token,
            chat_id,
            f"✅ File berhasil disimpan di Mac!\n"
            f"• <b>Nama:</b> <code>{html.escape(original_filename)}</code>\n"
            f"• <b>Ukuran:</b> {size_str}\n"
            f"• <b>Lokasi:</b> <code>~/https-server/shared_files/</code>"
            f"{auto_saved_note}",
            reply_markup=kb,
        )
    except Exception as e:
        send_message(token, chat_id, f"❌ Gagal menyimpan file: {str(e)}")


def main():
    config = load_config()
    token = config.get("token", "").strip()

    if not token:
        print("=" * 65)
        print("⚠️  TELEGRAM BOT TOKEN BELUM DIISI")
        print("=" * 65)
        print("Cara mendapatkan token:")
        print("1. Buka Telegram di HP Anda dan cari @BotFather")
        print("2. Ketik /newbot lalu ikuti instruksi")
        print("3. Masukkan token yang didapat ke dalam file:")
        print(f"   👉 {CONFIG_FILE}")
        print("=" * 65)

        # Coba minta input jika dijalankan interaktif di terminal
        try:
            token_input = input(
                "\nAtau masukkan Token Bot Anda sekarang di sini: "
            ).strip()
            if token_input:
                config["token"] = token_input
                save_config(config)
                token = token_input
            else:
                sys.exit(0)
        except Exception:
            sys.exit(0)

    # Uji validitas token
    me = tg_request(token, "getMe")
    if not me.get("ok"):
        print(f"❌ Token tidak valid! Respons: {me.get('error')}")
        sys.exit(1)

    bot_username = me["result"].get("username", "Bot")
    print("=" * 65)
    print(f"🤖 BOT TELEGRAM AKTIF: @{bot_username}")
    print("=" * 65)
    print("Silakan buka Telegram di HP Anda dan kirim pesan ke:")
    print(f"👉 https://t.me/{bot_username}")
    print("\nKetik /start di chat untuk mulai mengendalikan Mac Anda!")
    print("=" * 65)

    offset = 0
    allowed_chat_ids = set(config.get("allowed_chat_ids", []))

    while True:
        try:
            updates = tg_request(
                token, "getUpdates", {"offset": offset, "timeout": 25}
            )
            if not updates.get("ok"):
                time.sleep(3)
                continue

            for item in updates.get("result", []):
                offset = item["update_id"] + 1

                # 0. Handle callback query (Tombol Inline Interaktif)
                cb = item.get("callback_query")
                if cb:
                    cb_chat_id = cb.get("message", {}).get("chat", {}).get("id") or cb.get("from", {}).get("id")
                    if allowed_chat_ids and cb_chat_id not in allowed_chat_ids:
                        answer_callback_query(token, cb["id"], text="Akses ditolak.", show_alert=True)
                        continue
                    handle_callback_query(token, cb, config)
                    continue

                msg = item.get("message")
                if not msg:
                    continue

                chat_id = msg["chat"]["id"]
                from_user = msg.get("from", {}).get("first_name", "User")

                # Pengamanan: jika belum ada allowed_chat_ids, daftarkan chat_id pertama
                if not allowed_chat_ids:
                    allowed_chat_ids.add(chat_id)
                    config["allowed_chat_ids"] = list(allowed_chat_ids)
                    save_config(config)
                    print(
                        f"[Keamanan] Akun Telegram pertama terdaftar sebagai pemilik Mac: {from_user} (ID: {chat_id})"
                    )

                # Tolak jika chat berasal dari orang lain
                if chat_id not in allowed_chat_ids:
                    send_message(
                        token,
                        chat_id,
                        "⛔ Akses ditolak. Bot ini hanya dapat dikendalikan oleh pemilik komputer.",
                    )
                    continue

                # 1. Jika menerima file dokumen
                if "document" in msg:
                    doc = msg["document"]
                    handle_document_or_photo(
                        token,
                        chat_id,
                        doc["file_id"],
                        doc.get("file_name", "document"),
                        caption=msg.get("caption", ""),
                    )
                    continue

                # 2b. Jika menerima rekaman suara (Voice Note Asli)
                if "voice" in msg:
                    voice = msg["voice"]
                    handle_voice_note(
                        token,
                        chat_id,
                        voice["file_id"],
                        caption=msg.get("caption", ""),
                    )
                    continue

                # 2c. Jika menerima file audio (MP3/WAV/M4A)
                if "audio" in msg:
                    audio = msg["audio"]
                    handle_voice_note(
                        token,
                        chat_id,
                        audio["file_id"],
                        caption=msg.get("caption", ""),
                    )
                    continue

                if "photo" in msg:
                    # Ambil resolusi terbesar (elemen terakhir)
                    photo = msg["photo"][-1]
                    handle_document_or_photo(
                        token,
                        chat_id,
                        photo["file_id"],
                        f"photo_{int(time.time())}.jpg",
                    )
                    continue

                if "video" in msg:
                    vid = msg["video"]
                    handle_document_or_photo(
                        token,
                        chat_id,
                        vid["file_id"],
                        vid.get("file_name") or f"video_{int(time.time())}.mp4",
                        caption=msg.get("caption", ""),
                    )
                    continue

                # 3. Jika menerima teks perintah
                text = msg.get("text", "")
                if text:
                    print(f"[Telegram] Dari {from_user}: {text}")
                    handle_command(token, chat_id, text, config, msg=msg)

        except KeyboardInterrupt:
            print("\n[!] Bot dihentikan.")
            break
        except Exception as e:
            time.sleep(3)


if __name__ == "__main__":
    main()
