import re
#!/usr/bin/env python3
"""
Kodbox & Mac Remote Command Center over HTTPS.
Fitur Unggulan:
  1. File Manager (Gaya Kodbox/Finder): Upload, Download, Editor Teks/Code, Image Viewer.
  2. Remote App Launcher: Buka aplikasi Mac (Chrome, Safari, Terminal, dll.) & Buka URL dari jarak jauh.
  3. Web Terminal (Remote Shell): Jalankan perintah terminal langsung dari browser HP/Laptop.
  4. Mac Remote Hardware Control:
     - Volume Master Slider (Atur suara speaker Mac dari HP)
     - Text-to-Speech (Ketik teks, Mac berbicara menggunakan perintah `say`)
     - Universal Clipboard Sync (Baca & tulis clipboard Mac via `pbpaste` / `pbcopy`)
     - Screen Capture (Lihat tampilan layar Mac secara live)
     - Display Sleep (Kunci/matikan layar Mac dari jauh)
"""

import email
import email.policy
import html
import json
import mimetypes
import os
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import urllib.parse
import urllib.request
from urllib.parse import parse_qs, unquote, urlparse

import google_assistant_client

HOST = "0.0.0.0"
PORT = 8443
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CERT_FILE = os.path.join(BASE_DIR, "cert.pem")
KEY_FILE = os.path.join(BASE_DIR, "key.pem")
ROOT_DIR = os.path.expanduser("~")

from indonesian_tts import speak_indonesian, generate_indonesian_audio
import zipfile
import cast_manager
import interactive_popup
import smart_reminders
import db_manager
import mac_player
import quick_action_runner
import settings_manager


def get_running_processes(limit=30):
    procs = []
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid,%cpu,%mem,comm", "-r"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        lines = proc.stdout.strip().split("\n")
        if len(lines) > 1:
            for line in lines[1 : limit + 1]:
                parts = line.strip().split(maxsplit=3)
                if len(parts) >= 4:
                    procs.append({
                        "pid": parts[0],
                        "cpu": parts[1] + "%",
                        "mem": parts[2] + "%",
                        "name": os.path.basename(parts[3]),
                    })
    except Exception:
        pass
    return procs


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


def zip_target(abs_target):
    try:
        if os.path.isdir(abs_target):
            zip_base = abs_target.rstrip("/")
            shutil.make_archive(zip_base, "zip", os.path.dirname(abs_target), os.path.basename(abs_target))
            return zip_base + ".zip"
        elif os.path.isfile(abs_target):
            zip_name = abs_target + ".zip"
            with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(abs_target, os.path.basename(abs_target))
            return zip_name
    except Exception:
        pass
    return None


def extract_zip(abs_zip):
    try:
        if os.path.isfile(abs_zip) and abs_zip.lower().endswith(".zip"):
            dest_dir = abs_zip[:-4] + "_extracted"
            os.makedirs(dest_dir, exist_ok=True)
            with zipfile.ZipFile(abs_zip, "r") as z:
                z.extractall(dest_dir)
            return dest_dir
    except Exception:
        pass
    return None


def format_size(bytes_num):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} PB"


def get_safe_path(rel_path):
    clean_rel = rel_path.strip().lstrip("/")
    abs_path = os.path.abspath(os.path.join(ROOT_DIR, clean_rel))
    if not abs_path.startswith(ROOT_DIR):
        return ROOT_DIR
    return abs_path


def get_relative_path(abs_path):
    rel = os.path.relpath(abs_path, ROOT_DIR)
    return "" if rel == "." else rel


def get_system_stats():
    disk = shutil.disk_usage(ROOT_DIR)
    return {
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "disk_total": format_size(disk.total),
        "disk_used": format_size(disk.used),
        "disk_free": format_size(disk.free),
        "disk_percent": f"{(disk.used / disk.total) * 100:.1f}%",
        "root_dir": ROOT_DIR,
    }


def get_file_info(entry, parent_abs_path):
    is_dir = entry.is_dir()
    stat = entry.stat()
    ext = os.path.splitext(entry.name)[1].lower().lstrip(".")

    file_type = "file"
    if is_dir:
        file_type = "folder"
    elif ext in ["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico"]:
        file_type = "image"
    elif ext in [
        "py",
        "js",
        "html",
        "css",
        "json",
        "md",
        "txt",
        "sh",
        "xml",
        "yml",
        "yaml",
        "ini",
        "conf",
    ]:
        file_type = "code"
    elif ext in ["pdf"]:
        file_type = "pdf"
    elif ext in ["zip", "tar", "gz", "rar", "7z", "bz2"]:
        file_type = "archive"
    elif ext in ["mp4", "mkv", "webm", "mov", "avi"]:
        file_type = "video"
    elif ext in ["mp3", "wav", "ogg", "flac", "m4a"]:
        file_type = "audio"

    return {
        "name": entry.name,
        "is_dir": is_dir,
        "size": format_size(stat.st_size) if not is_dir else "--",
        "bytes": stat.st_size if not is_dir else 0,
        "type": file_type,
        "ext": ext,
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
            "%Y-%m-%d %H:%M"
        ),
    }



import ctypes
from ctypes import c_double, c_int, c_void_p, c_uint32, Structure

class CGPoint(Structure):
    _fields_ = [('x', c_double), ('y', c_double)]

try:
    _app_services = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
    _CGEventCreateMouseEvent = _app_services.CGEventCreateMouseEvent
    _CGEventCreateMouseEvent.argtypes = [c_void_p, c_uint32, CGPoint, c_uint32]
    _CGEventCreateMouseEvent.restype = c_void_p
    _CGEventPost = _app_services.CGEventPost
    _CGEventPost.argtypes = [c_uint32, c_void_p]
    _CFRelease = _app_services.CFRelease
    _CFRelease.argtypes = [c_void_p]
    _has_cg = True
except Exception:
    _has_cg = False

def get_screen_resolution():
    try:
        if _has_cg:
            disp = _app_services.CGMainDisplayID()
            w = _app_services.CGDisplayPixelsWide(disp)
            h = _app_services.CGDisplayPixelsHigh(disp)
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return 1920, 1080

def remote_mouse_click(x, y, click_type="click"):
    if not _has_cg:
        return False
    try:
        pt = CGPoint(x, y)
        down = _CGEventCreateMouseEvent(None, 1, pt, 0)
        up = _CGEventCreateMouseEvent(None, 2, pt, 0)
        if down and up:
            _CGEventPost(0, down)
            _CGEventPost(0, up)
            if click_type == "double":
                down2 = _CGEventCreateMouseEvent(None, 1, pt, 0)
                up2 = _CGEventCreateMouseEvent(None, 2, pt, 0)
                _CGEventPost(0, down2)
                _CGEventPost(0, up2)
                _CFRelease(down2)
                _CFRelease(up2)
            _CFRelease(down)
            _CFRelease(up)
            return True
    except Exception:
        pass
    return False

def remote_keystroke(text=None, key=None):
    try:
        if text:
            safe = text.replace('\\', '\\\\').replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'tell application "System Events" to keystroke "{safe}"'], timeout=4)
            return True
        elif key:
            key_map = {
                "Return": "key code 36",
                "Enter": "key code 36",
                "Backspace": "key code 51",
                "Delete": "key code 51",
                "Space": "key code 49",
                "Tab": "key code 48",
                "Escape": "key code 53",
                "Spotlight": 'tell application "System Events" to key code 49 using command down'
            }
            if key in key_map:
                cmd = key_map[key]
                if not cmd.startswith("tell"):
                    cmd = f'tell application "System Events" to {cmd}'
                subprocess.run(["osascript", "-e", cmd], timeout=4)
                return True
    except Exception:
        pass
    return False

def process_voice_command(cmd_text):
    text = cmd_text.lower().strip()
    is_gh = any(k in text for k in ("google home", "google", "speaker rumah", "cast"))

    # --- Perintah Khusus Google Home ---
    if is_gh:
        gh_vol_match = re.search(r'(\d+)\s*(?:persen|%)?', text)
        if ("volume" in text or "suara" in text) and gh_vol_match:
            val = max(0, min(100, int(gh_vol_match.group(1))))
            return {"action": "gh_volume", "value": val, "reply": f"Volume Google Home diatur ke {val} persen"}
        if any(k in text for k in ("besarkan", "naikkan", "keraskan", "tambah")):
            return {"action": "gh_volume_delta", "delta": 15, "reply": "Volume Google Home dibesarkan"}
        if any(k in text for k in ("kecilkan", "turunkan", "pelankan", "kurangi")):
            return {"action": "gh_volume_delta", "delta": -15, "reply": "Volume Google Home dikecilkan"}
        if "unmute" in text or "nyalakan suara" in text or "buka suara" in text:
            return {"action": "gh_mute", "muted": False, "reply": "Suara Google Home dinyalakan kembali"}
        if "bisukan" in text or "mute" in text or "senyap" in text:
            return {"action": "gh_mute", "muted": True, "reply": "Google Home dibisukan"}
        if any(k in text for k in ("stop", "berhenti", "hentikan", "matikan")):
            return {"action": "gh_stop", "reply": "Siaran Google Home dihentikan"}
        if any(k in text for k in ("jeda", "pause")):
            return {"action": "gh_media", "op": "pause", "reply": "Pemutaran Google Home dijeda"}
        if any(k in text for k in ("lanjut", "resume", "putar kembali")):
            return {"action": "gh_media", "op": "play", "reply": "Pemutaran Google Home dilanjutkan"}

    vol_match = re.search(r'volume\s*(\d+)', text)
    if vol_match:
        val = int(vol_match.group(1))
        val = max(0, min(100, val))
        return {"action": "volume", "value": val, "reply": f"Volume berhasil diatur ke {val} persen"}
    if "bisukan" in text or "mute" in text:
        return {"action": "volume", "value": 0, "reply": "Audio Mac telah dibisukan"}

    cast_match = re.search(r'(?:umumkan|siarkan|katakan|bicara)\s+(?:ke\s+(?:google\s+home|speaker|rumah|kamar))?\s*[:\-]?\s*(.+)', text)
    if cast_match and ("google" in text or "speaker" in text or "siarkan" in text or "umumkan" in text):
        msg = cast_match.group(1).strip()
        return {"action": "cast", "message": msg, "reply": f"Menyiarkan ke Google Home: {msg}"}

    if "kunci" in text or "lock" in text:
        return {"action": "lock", "reply": "Layar Mac berhasil dikunci"}
    if "tidur" in text or "sleep" in text:
        return {"action": "sleep", "reply": "Mac dialihkan ke mode sleep"}

    app_match = re.search(r'buka\s+(?:aplikasi\s+)?(.+)', text)
    if app_match:
        app_name = app_match.group(1).strip()
        return {"action": "launch_app", "app": app_name, "reply": f"Membuka aplikasi {app_name.title()}"}

    if "putar" in text or "play" in text:
        return {"action": "music", "op": "playpause", "reply": "Memutar musik"}
    if "jeda" in text or "pause" in text or "berhenti" in text or "stop" in text:
        return {"action": "music", "op": "playpause", "reply": "Musik dijeda"}
    if "next" in text or "selanjutnya" in text:
        return {"action": "music", "op": "next", "reply": "Memutar lagu berikutnya"}
    if "sebelumnya" in text or "prev" in text:
        return {"action": "music", "op": "prev", "reply": "Memutar lagu sebelumnya"}

    if "status" in text or "cpu" in text or "ram" in text or "sistem" in text:
        return {"action": "telemetry", "reply": "Status telemetri Mac aktif normal"}

    if "mode malam" in text or "mode tidur" in text:
        return {"action": "scenario", "preset": "night", "reply": "Mode malam diaktifkan. Selamat beristirahat."}
    if "mode kerja" in text or "mode fokus" in text:
        return {"action": "scenario", "preset": "focus", "reply": "Mode fokus kerja diaktifkan."}
    if "mode keluar" in text or "mode away" in text:
        return {"action": "scenario", "preset": "away", "reply": "Mode tinggalkan meja diaktifkan. Layar dikunci."}

    return {"action": "unknown", "reply": f"Perintah diterima: {cmd_text}"}

def execute_scenario(preset):
    try:
        if preset == "away":
            subprocess.run(["osascript", "-e", "set volume output volume 0"], timeout=3)
            subprocess.run(["pmset", "displaysleepnow"], timeout=3)
            return {"status": "success", "message": "Mode Tinggalkan Meja aktif: Layar dikunci & speaker dibisukan."}
        elif preset == "focus":
            subprocess.run(["open", "-a", "Antigravity IDE"], timeout=4)
            subprocess.run(["open", "-a", "Terminal"], timeout=4)
            subprocess.run(["osascript", "-e", "set volume output volume 35"], timeout=3)
            subprocess.run(["osascript", "-e", 'tell application "Music" to play'], timeout=3)
            return {"status": "success", "message": "Mode Fokus Kerja aktif: VS Code, Terminal & Musik 35% dinyalakan."}
        elif preset == "night":
            subprocess.run(["osascript", "-e", 'tell application "Music" to pause'], timeout=3)
            subprocess.run(["osascript", "-e", "set volume output volume 15"], timeout=3)
            try:
                cast_manager.get_cast_manager().broadcast_text_direct("Selamat istirahat, Pak Subuh. Semua sistem Mac telah dinonaktifkan.")
            except Exception:
                pass
            subprocess.run(["pmset", "displaysleepnow"], timeout=3)
            return {"status": "success", "message": "Mode Malam aktif: Musik dijeda, pengumuman Google Home terkirim & layar dimatikan."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "Skenario tidak dikenal"}


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>Mac Control Center & Kodbox Manager</title>
    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#0b0f19">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="Mac Remote">
    <link rel="apple-touch-icon" href="/api/pwa/icon.svg">
    <link rel="icon" type="image/svg+xml" href="/api/pwa/icon.svg">
    <style>
        :root {
            --bg-deep: #070a11;
            --bg-base: #0b0f19;
            --bg-sidebar: rgba(15, 23, 42, 0.75);
            --bg-card: rgba(22, 32, 54, 0.65);
            --bg-card-hover: rgba(30, 43, 72, 0.85);
            --bg-hover: rgba(56, 189, 248, 0.12);
            --bg-active: rgba(56, 189, 248, 0.22);
            --border-subtle: rgba(255, 255, 255, 0.08);
            --border-color: #243452;
            --border-focus: rgba(56, 189, 248, 0.5);
            
            --text-main: #f8fafc;
            --text-secondary: #cbd5e1;
            --text-muted: #94a3b8;
            --text-dim: #64748b;

            --accent: #38bdf8;
            --accent-hover: #0ea5e9;
            --accent-gradient: linear-gradient(135deg, #0ea5e9 0%, #38bdf8 100%);
            --accent-glow: rgba(56, 189, 248, 0.25);
            
            --success: #10b981;
            --success-glow: rgba(16, 185, 129, 0.25);
            --danger: #f43f5e;
            --danger-hover: #e11d48;
            --warning: #f59e0b;
            
            --glass-filter: blur(24px) saturate(190%);
            --shadow-subtle: 0 4px 20px -2px rgba(0, 0, 0, 0.4);
            --shadow-card: 0 12px 30px -10px rgba(0, 0, 0, 0.6);
            --shadow-popover: 0 20px 45px -10px rgba(0, 0, 0, 0.8), 0 0 0 1px rgba(255, 255, 255, 0.08);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
            background: radial-gradient(at 0% 0%, rgba(56, 189, 248, 0.07) 0px, transparent 50%),
                        radial-gradient(at 100% 0%, rgba(99, 102, 241, 0.07) 0px, transparent 50%),
                        radial-gradient(at 50% 100%, rgba(16, 185, 129, 0.04) 0px, transparent 50%),
                        #080c14;
            background-attachment: fixed;
            color: var(--text-main);
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            user-select: none;
            letter-spacing: -0.01em;
        }

        /* CUSTOM MODERN SCROLLBAR */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.16); border-radius: 999px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(56, 189, 248, 0.45); }

        /* TOP NAVIGATION */
        header {
            height: 60px;
            background: rgba(13, 19, 33, 0.75);
            backdrop-filter: var(--glass-filter);
            -webkit-backdrop-filter: var(--glass-filter);
            border-bottom: 1px solid var(--border-subtle);
            box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.5);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 20px;
            gap: 16px;
            z-index: 100;
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 10px;
            font-weight: 700;
            font-size: 1.12rem;
            cursor: pointer;
            letter-spacing: -0.02em;
            transition: opacity 0.15s;
        }
        .brand:hover { opacity: 0.9; }
        .brand-icon {
            font-size: 1.35rem;
            filter: drop-shadow(0 2px 6px rgba(56, 189, 248, 0.4));
        }
        .brand-text {
            background: linear-gradient(135deg, #ffffff 40%, #38bdf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 800;
        }
        .brand-pill {
            font-size: 0.68rem;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            font-weight: 700;
            background: rgba(56, 189, 248, 0.15);
            color: var(--accent);
            padding: 2px 7px;
            border-radius: 6px;
            border: 1px solid rgba(56, 189, 248, 0.25);
            margin-left: 2px;
        }

        .nav-tabs {
            display: flex;
            gap: 4px;
            background: rgba(8, 12, 22, 0.65);
            padding: 4px;
            border-radius: 999px;
            border: 1px solid var(--border-subtle);
            box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4);
        }
        .tab-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 7px 15px;
            border-radius: 999px;
            font-size: 0.82rem;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 6px;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .tab-btn:hover {
            color: var(--text-main);
            background: rgba(255, 255, 255, 0.06);
        }
        .tab-btn.active {
            background: var(--accent-gradient);
            color: #040914;
            font-weight: 700;
            box-shadow: 0 4px 14px rgba(14, 165, 233, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.3);
        }

        .badge {
            background: rgba(16, 185, 129, 0.12);
            color: var(--success);
            padding: 5px 12px;
            border-radius: 999px;
            font-size: 0.76rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
            border: 1px solid rgba(16, 185, 129, 0.25);
            letter-spacing: 0.2px;
        }
        .badge::before {
            content: "";
            width: 7px;
            height: 7px;
            background: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--success);
            animation: pulseGlow 2s infinite ease-in-out;
        }
        @keyframes pulseGlow {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.5; transform: scale(0.85); }
        }

        /* MAIN WRAPPER */
        .tab-content-area {
            flex: 1;
            display: flex;
            overflow: hidden;
            position: relative;
        }
        .view-section {
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            display: none;
            overflow-y: auto;
        }
        .view-section.active { display: flex; }

        /* ================= 1. FILE EXPLORER (KODBOX PRO LAYOUT) ================= */
        .fm-container {
            display: flex;
            width: 100%;
            height: 100%;
            overflow: hidden;
        }
        aside {
            width: 240px;
            background: var(--bg-sidebar);
            backdrop-filter: var(--glass-filter);
            -webkit-backdrop-filter: var(--glass-filter);
            border-right: 1px solid var(--border-subtle);
            display: flex;
            flex-direction: column;
            user-select: none;
            flex-shrink: 0;
        }
        .fm-nav-title {
            font-size: 0.68rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-dim);
            padding: 14px 16px 6px;
            font-weight: 700;
        }
        .fm-nav-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 9px 16px;
            margin: 2px 8px;
            border-radius: 8px;
            color: var(--text-secondary);
            font-size: 0.85rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .fm-nav-item:hover {
            background: rgba(255, 255, 255, 0.06);
            color: var(--text-main);
            transform: translateX(2px);
        }
        .fm-nav-item.active {
            background: rgba(56, 189, 248, 0.14);
            color: var(--accent);
            font-weight: 600;
            border: 1px solid rgba(56, 189, 248, 0.25);
        }

        .fm-workspace {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: transparent;
            overflow: hidden;
        }
        .fm-toolbar {
            height: 52px;
            border-bottom: 1px solid var(--border-subtle);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 18px;
            background: rgba(15, 23, 42, 0.5);
            backdrop-filter: blur(16px);
            gap: 14px;
            flex-shrink: 0;
        }
        .breadcrumbs {
            display: flex;
            align-items: center;
            gap: 4px;
            font-size: 0.84rem;
            overflow-x: auto;
            white-space: nowrap;
            padding: 4px 0;
        }
        .crumb {
            cursor: pointer;
            color: var(--text-muted);
            padding: 4px 8px;
            border-radius: 6px;
            transition: all 0.15s;
            font-weight: 500;
        }
        .crumb:hover {
            color: var(--accent);
            background: rgba(255, 255, 255, 0.06);
        }
        .crumb.current {
            color: var(--text-main);
            font-weight: 600;
            background: rgba(255, 255, 255, 0.05);
            cursor: default;
        }
        .crumb-sep {
            color: var(--text-dim);
            font-size: 0.75rem;
            margin: 0 1px;
        }

        .btn {
            background: rgba(30, 41, 59, 0.65);
            backdrop-filter: blur(8px);
            border: 1px solid var(--border-subtle);
            color: var(--text-main);
            padding: 7px 14px;
            border-radius: 8px;
            font-size: 0.82rem;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
            font-weight: 600;
            letter-spacing: -0.01em;
        }
        .btn:hover {
            background: rgba(45, 62, 90, 0.8);
            border-color: rgba(56, 189, 248, 0.4);
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }
        .btn:active {
            transform: translateY(0);
        }
        .btn-primary {
            background: var(--accent-gradient);
            color: #040914;
            font-weight: 700;
            border: 1px solid rgba(255, 255, 255, 0.2);
            box-shadow: 0 4px 14px rgba(14, 165, 233, 0.35);
        }
        .btn-primary:hover {
            background: linear-gradient(135deg, #0284c7 0%, #38bdf8 100%);
            box-shadow: 0 6px 18px rgba(14, 165, 233, 0.5);
            border-color: rgba(255, 255, 255, 0.3);
        }
        .btn-danger {
            background: rgba(244, 63, 94, 0.15);
            color: var(--danger);
            border: 1px solid rgba(244, 63, 94, 0.3);
        }
        .btn-danger:hover {
            background: var(--danger);
            color: white;
            border-color: var(--danger);
            box-shadow: 0 4px 14px rgba(244, 63, 94, 0.4);
        }

        .file-scroll-area {
            flex: 1;
            overflow-y: auto;
            padding: 18px;
        }
        .grid-view {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(115px, 1fr));
            gap: 14px;
        }
        .file-card-grid {
            background: var(--bg-card);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 14px 10px 10px;
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
            position: relative;
        }
        .file-card-grid:hover {
            background: var(--bg-card-hover);
            border-color: rgba(56, 189, 248, 0.4);
            transform: translateY(-4px) scale(1.02);
            box-shadow: 0 12px 24px -8px rgba(0, 0, 0, 0.6), 0 0 16px rgba(56, 189, 248, 0.18);
        }
        .file-card-grid.selected {
            background: rgba(56, 189, 248, 0.18);
            border-color: var(--accent);
            box-shadow: 0 0 0 1px var(--accent), 0 8px 20px rgba(56, 189, 248, 0.25);
        }
        .file-card-icon {
            font-size: 2.9rem;
            margin-bottom: 8px;
            filter: drop-shadow(0 4px 8px rgba(0, 0, 0, 0.3));
        }
        .file-card-name {
            font-size: 0.8rem;
            font-weight: 500;
            color: var(--text-main);
            word-break: break-all;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            line-height: 1.3;
        }

        .list-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 0.85rem;
        }
        .list-table th {
            text-align: left;
            padding: 12px 14px;
            border-bottom: 1px solid var(--border-subtle);
            color: var(--text-muted);
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            font-weight: 700;
            background: rgba(15, 23, 42, 0.4);
            position: sticky;
            top: 0;
            backdrop-filter: blur(8px);
        }
        .list-table td {
            padding: 11px 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            color: var(--text-secondary);
        }
        .list-table tr {
            transition: background 0.15s ease;
        }
        .list-table tr:hover {
            background: rgba(255, 255, 255, 0.05);
            cursor: pointer;
        }
        .list-table tr:hover td {
            color: var(--text-main);
        }
        .list-table tr.selected {
            background: rgba(56, 189, 248, 0.14);
        }
        .list-table tr.selected td {
            color: var(--accent);
        }

        /* ================= 2. REMOTE APP LAUNCHER ================= */
        .launcher-view {
            flex-direction: column;
            padding: 28px;
            gap: 24px;
            width: 100%;
            max-width: 1050px;
            margin: 0 auto;
        }
        .card-panel {
            background: var(--bg-card);
            backdrop-filter: var(--glass-filter);
            -webkit-backdrop-filter: var(--glass-filter);
            border: 1px solid var(--border-subtle);
            border-radius: 16px;
            padding: 24px;
            box-shadow: var(--shadow-card), inset 0 1px 0 rgba(255, 255, 255, 0.08);
            transition: border-color 0.2s;
        }
        .card-panel:hover {
            border-color: rgba(255, 255, 255, 0.14);
        }
        .card-title {
            font-size: 1.08rem;
            font-weight: 700;
            color: var(--text-main);
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 10px;
            letter-spacing: -0.01em;
        }
        .app-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
            gap: 16px;
        }
        .app-item {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-subtle);
            border-radius: 14px;
            padding: 18px 12px;
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
            cursor: pointer;
            transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .app-item:hover {
            border-color: rgba(56, 189, 248, 0.45);
            transform: translateY(-5px);
            background: var(--bg-card-hover);
            box-shadow: 0 16px 28px -6px rgba(0, 0, 0, 0.5), 0 0 20px rgba(56, 189, 248, 0.2);
        }
        .app-item:active {
            transform: translateY(-2px) scale(0.98);
        }
        .app-icon {
            font-size: 2.6rem;
            margin-bottom: 10px;
            filter: drop-shadow(0 4px 10px rgba(0, 0, 0, 0.4));
        }
        .app-name {
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-main);
        }

        .input-row {
            display: flex;
            gap: 12px;
            margin-top: 12px;
        }
        .text-input {
            flex: 1;
            padding: 11px 16px;
            background: rgba(9, 13, 24, 0.7);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            color: var(--text-main);
            font-size: 0.9rem;
            outline: none;
            transition: all 0.2s ease;
        }
        .text-input:focus {
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.25);
            background: rgba(9, 13, 24, 0.9);
        }

        /* ================= 3. REMOTE TERMINAL (iTerm/macOS Style) ================= */
        .terminal-view {
            display: flex;
            flex-direction: column;
            padding: 28px;
            gap: 18px;
            width: 100%;
            max-width: 1100px;
            margin: 0 auto;
            height: 100%;
        }
        .mac-terminal-chrome {
            background: #090d16;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 14px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            box-shadow: 0 20px 50px -10px rgba(0, 0, 0, 0.8), 0 0 0 1px rgba(255, 255, 255, 0.05);
            flex: 1;
            min-height: 380px;
        }
        .mac-traffic-bar {
            height: 36px;
            background: rgba(22, 30, 48, 0.85);
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 14px;
        }
        .mac-traffic-dots {
            display: flex;
            gap: 7px;
        }
        .traffic-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            display: inline-block;
        }
        .dot-red { background: #ff5f56; border: 1px solid #e0443e; }
        .dot-yellow { background: #ffbd2e; border: 1px solid #dea123; }
        .dot-green { background: #27c93f; border: 1px solid #1aab29; }
        .mac-term-title {
            font-size: 0.76rem;
            color: var(--text-muted);
            font-weight: 600;
            font-family: ui-monospace, Menlo, Monaco, Consolas, monospace;
        }
        .term-screen {
            flex: 1;
            background: #06090f;
            padding: 16px;
            font-family: ui-monospace, Menlo, Monaco, "SF Mono", Consolas, monospace;
            font-size: 0.86rem;
            color: #38bdf8;
            overflow-y: auto;
            white-space: pre-wrap;
            line-height: 1.55;
            box-shadow: inset 0 2px 10px rgba(0, 0, 0, 0.6);
        }
        .quick-cmds {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }

        /* ================= 4. REMOTE HARDWARE CONTROL ================= */
        .control-view {
            flex-direction: column;
            padding: 28px;
            gap: 22px;
            max-width: 950px;
            margin: 0 auto;
            width: 100%;
        }
        .slider-box {
            display: flex;
            align-items: center;
            gap: 18px;
            margin-top: 12px;
        }
        .slider-box input[type="range"] {
            flex: 1;
            -webkit-appearance: none;
            appearance: none;
            height: 8px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.12);
            outline: none;
            box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.5);
            transition: background 0.2s;
        }
        .slider-box input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            appearance: none;
            width: 22px;
            height: 22px;
            border-radius: 50%;
            background: #ffffff;
            cursor: pointer;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4), 0 0 0 2px rgba(56, 189, 248, 0.6);
            transition: transform 0.15s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.15s;
        }
        .slider-box input[type="range"]::-webkit-slider-thumb:hover {
            transform: scale(1.15);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5), 0 0 0 4px rgba(56, 189, 248, 0.8);
        }

        #screenModal img {
            max-width: 100%;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
        }

        #toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: rgba(15, 23, 42, 0.92);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid rgba(56, 189, 248, 0.4);
            color: var(--text-main);
            padding: 14px 22px;
            border-radius: 12px;
            box-shadow: 0 16px 36px -6px rgba(0, 0, 0, 0.6), 0 0 20px rgba(56, 189, 248, 0.2);
            transform: translateY(120px);
            opacity: 0;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            z-index: 99999;
            font-size: 0.9rem;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        #toast.show {
            transform: translateY(0);
            opacity: 1;
        }

        /* MODAL COMMON */
        .modal-bg {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(3, 7, 18, 0.72);
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 500;
        }
        .modal-box {
            background: rgba(18, 25, 41, 0.96);
            backdrop-filter: var(--glass-filter);
            -webkit-backdrop-filter: var(--glass-filter);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 18px;
            width: 90%;
            max-width: 660px;
            overflow: hidden;
            box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.8), 0 0 0 1px rgba(255, 255, 255, 0.05);
            animation: modalPop 0.22s cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes modalPop {
            0% { transform: scale(0.94); opacity: 0; }
            100% { transform: scale(1); opacity: 1; }
        }
        .modal-head {
            padding: 16px 22px;
            border-bottom: 1px solid var(--border-subtle);
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 1.05rem;
            font-weight: 700;
            letter-spacing: -0.01em;
        }
        .modal-body {
            padding: 22px;
        }
        .modal-foot {
            padding: 14px 22px;
            border-top: 1px solid var(--border-subtle);
            display: flex;
            justify-content: flex-end;
            gap: 10px;
            background: rgba(10, 15, 26, 0.5);
        }

        /* Legacy media query replaced by mobile-first section below */

        /* CONTEXT MENU (KLIK KANAN - macOS SONOMA AESTHETIC) */
        #contextMenu {
            position: fixed;
            display: none;
            z-index: 999999;
            min-width: 220px;
            background: rgba(18, 26, 43, 0.92);
            backdrop-filter: blur(32px) saturate(200%);
            -webkit-backdrop-filter: blur(32px) saturate(200%);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 12px;
            box-shadow: 0 20px 45px -8px rgba(0, 0, 0, 0.8), 0 0 0 1px rgba(255, 255, 255, 0.06);
            padding: 6px;
            font-size: 0.86rem;
        }
        .ctx-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 12px;
            border-radius: 7px;
            color: #f8fafc;
            cursor: pointer;
            transition: all 0.12s ease;
            user-select: none;
            font-weight: 500;
        }
        .ctx-item:hover {
            background: var(--accent-gradient);
            color: #ffffff;
            box-shadow: 0 4px 12px rgba(14, 165, 233, 0.35);
        }
        .ctx-item.danger:hover {
            background: linear-gradient(135deg, #e11d48 0%, #f43f5e 100%);
            color: #ffffff;
            box-shadow: 0 4px 12px rgba(244, 63, 94, 0.4);
        }
        .ctx-divider {
            height: 1px;
            background: rgba(255, 255, 255, 0.08);
            margin: 5px 6px;
        }

        /* LIVE SCREEN MIRRORING */
        .screen-view-container {
            display: flex;
            flex-direction: column;
            width: 100%;
            height: 100%;
            padding: 20px;
            gap: 14px;
            overflow-y: auto;
            max-width: 1200px;
            margin: 0 auto;
        }
        .screen-toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(15, 23, 42, 0.7);
            backdrop-filter: var(--glass-filter);
            padding: 10px 18px;
            border-radius: 12px;
            border: 1px solid var(--border-subtle);
            flex-wrap: wrap;
            gap: 10px;
        }
        .screen-canvas-box {
            position: relative;
            background: #000;
            border-radius: 14px;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 16px 36px -6px rgba(0, 0, 0, 0.7);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: crosshair;
            min-height: 420px;
        }
        .screen-canvas-box img {
            max-width: 100%;
            max-height: 72vh;
            display: block;
            object-fit: contain;
            user-select: none;
            -webkit-user-drag: none;
        }
        .click-ripple {
            position: absolute;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: rgba(56, 189, 248, 0.5);
            border: 2px solid #38bdf8;
            pointer-events: none;
            opacity: 0;
            transform: scale(0.2);
            z-index: 10;
        }
        .click-ripple.animate {
            animation: rippleAnim 0.45s ease-out forwards;
        }
        @keyframes rippleAnim {
            0% { opacity: 1; transform: scale(0.2); }
            100% { opacity: 0; transform: scale(2.2); }
        }
        .screen-key-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            background: rgba(15, 23, 42, 0.6);
            padding: 12px 18px;
            border-radius: 12px;
            border: 1px solid var(--border-subtle);
            flex-wrap: wrap;
        }

        /* SMART SCENARIOS */
        .scenario-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
            gap: 14px;
            margin-top: 12px;
        }
        .scenario-card {
            background: rgba(22, 32, 54, 0.6);
            border: 1px solid var(--border-subtle);
            border-radius: 14px;
            padding: 18px;
            cursor: pointer;
            transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .scenario-card:hover {
            transform: translateY(-4px);
            background: rgba(30, 43, 72, 0.85);
            border-color: rgba(56, 189, 248, 0.45);
            box-shadow: 0 12px 24px -8px rgba(0, 0, 0, 0.5), 0 0 16px rgba(56, 189, 248, 0.2);
        }
        .scenario-card:active {
            transform: translateY(-1px) scale(0.98);
        }
        .scenario-icon { font-size: 2rem; margin-bottom: 2px; }
        .scenario-title { font-weight: 700; font-size: 0.95rem; color: var(--text-main); }
        .scenario-desc { font-size: 0.8rem; color: var(--text-muted); line-height: 1.4; }

        /* VOICE HUD */
        .voice-waves {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 6px;
            height: 60px;
            margin: 20px 0;
        }
        .voice-bar {
            width: 5px;
            height: 16px;
            background: var(--accent);
            border-radius: 999px;
            transition: height 0.15s;
        }
        .voice-waves.active .voice-bar:nth-child(1) { animation: voicePulse 0.5s infinite alternate ease-in-out 0.1s; }
        .voice-waves.active .voice-bar:nth-child(2) { animation: voicePulse 0.5s infinite alternate ease-in-out 0.2s; }
        .voice-waves.active .voice-bar:nth-child(3) { animation: voicePulse 0.5s infinite alternate ease-in-out 0.3s; }
        .voice-waves.active .voice-bar:nth-child(4) { animation: voicePulse 0.5s infinite alternate ease-in-out 0.4s; }
        .voice-waves.active .voice-bar:nth-child(5) { animation: voicePulse 0.5s infinite alternate ease-in-out 0.25s; }
        .voice-waves.active .voice-bar:nth-child(6) { animation: voicePulse 0.5s infinite alternate ease-in-out 0.15s; }
        .voice-waves.active .voice-bar:nth-child(7) { animation: voicePulse 0.5s infinite alternate ease-in-out 0.35s; }
        @keyframes voicePulse {
            0% { height: 12px; opacity: 0.4; }
            100% { height: 52px; opacity: 1; box-shadow: 0 0 12px var(--accent); }
        }

        /* DRAG AND DROP */
        .file-scroll-area.dragover {
            border: 2px dashed var(--accent) !important;
            background: rgba(56, 189, 248, 0.08) !important;
        }

        /* ================= MOBILE-FIRST RESPONSIVE LAYOUT ================= */
        html, body {
            height: 100dvh;
            min-height: 100vh;
            overflow-x: hidden;
        }
        header {
            height: auto;
            flex-direction: column;
            align-items: stretch;
            padding: 10px 12px 8px;
            gap: 8px;
            flex-shrink: 0;
        }
        .header-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            width: 100%;
        }
        .header-actions {
            display: flex;
            align-items: center;
            gap: 6px;
            flex-shrink: 0;
        }
        .brand {
            font-size: 1rem;
            gap: 7px;
            flex-shrink: 0;
        }
        .brand-pill { display: none; }
        .badge {
            padding: 4px 9px;
            font-size: 0.72rem;
            gap: 6px;
            display: none;
        }
        .nav-tabs {
            width: 100%;
            max-width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
            scroll-snap-type: x proximity;
            padding: 4px;
            gap: 4px;
        }
        .nav-tabs::-webkit-scrollbar { display: none; }
        .tab-btn {
            white-space: nowrap;
            flex-shrink: 0;
            scroll-snap-align: start;
            padding: 8px 13px;
            min-height: 36px;
            font-size: 0.8rem;
        }

        /* Mobile-first containers & cards */
        .view-section {
            -webkit-overflow-scrolling: touch;
            padding-bottom: env(safe-area-inset-bottom);
        }
        .control-view,
        .launcher-view,
        .terminal-view,
        .screen-view-container {
            padding: 12px 12px 28px;
            gap: 14px;
        }
        .card-panel {
            padding: 15px 14px;
            border-radius: 14px;
        }
        .card-title {
            font-size: 0.98rem;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }
        .btn {
            min-height: 38px;
            padding: 7px 12px;
            justify-content: center;
            touch-action: manipulation;
        }
        .input-row {
            flex-direction: column;
            gap: 8px;
        }
        .text-input {
            font-size: 16px;
            padding: 10px 13px;
            min-width: 0;
        }
        .app-grid {
            grid-template-columns: repeat(auto-fill, minmax(105px, 1fr));
            gap: 10px;
        }
        .app-item { padding: 14px 10px; }
        .app-icon { font-size: 2.1rem; margin-bottom: 6px; }
        .scenario-grid {
            grid-template-columns: 1fr;
            gap: 10px;
        }
        .screen-canvas-box { min-height: 220px; }
        .screen-toolbar, .screen-key-bar {
            padding: 10px 12px;
            gap: 8px;
        }
        .mac-terminal-chrome { min-height: 300px; }
        .term-screen { font-size: 0.8rem; padding: 12px; }

        /* Mobile-first File Manager */
        .fm-container {
            flex-direction: column;
        }
        aside {
            width: 100%;
            border-right: none;
            border-bottom: 1px solid var(--border-subtle);
            flex-direction: row;
            align-items: center;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
            padding: 6px 8px;
        }
        aside::-webkit-scrollbar { display: none; }
        aside > div:first-child {
            display: flex;
            flex-direction: row;
            align-items: center;
            gap: 4px;
        }
        aside .fm-nav-title,
        aside > div:last-child {
            display: none;
        }
        .fm-nav-item {
            white-space: nowrap;
            padding: 6px 11px;
            margin: 0;
            font-size: 0.8rem;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--border-subtle);
            border-radius: 999px;
        }
        .fm-toolbar {
            height: auto;
            flex-wrap: wrap;
            padding: 8px 12px;
            gap: 8px;
        }
        .fm-toolbar-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            width: 100%;
        }
        .fm-toolbar-actions .btn {
            flex: 1 1 auto;
            font-size: 0.78rem;
            padding: 6px 10px;
            min-height: 34px;
        }
        .file-scroll-area {
            padding: 12px;
            overflow-x: auto;
        }
        .grid-view {
            grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
            gap: 10px;
        }

        /* Mobile-first Task Manager & Media Player */
        .procs-wrap, .media-wrap {
            display: flex;
            flex-direction: column;
            padding: 12px 12px 28px;
            gap: 14px;
            width: 100%;
            margin: 0 auto;
        }
        .procs-wrap { max-width: 1000px; }
        .media-wrap { max-width: 700px; }
        .table-scroll {
            max-height: 62vh;
            overflow: auto;
            -webkit-overflow-scrolling: touch;
        }

        /* Mobile-first Modals & Toast */
        .modal-box {
            width: 94%;
            max-height: 90dvh;
            display: flex;
            flex-direction: column;
        }
        .modal-body {
            overflow-y: auto;
            padding: 16px;
        }
        .modal-head, .modal-foot {
            padding: 12px 16px;
            flex-shrink: 0;
        }
        #toast {
            left: 12px;
            right: 12px;
            bottom: 16px;
            font-size: 0.85rem;
            padding: 12px 16px;
        }

        /* Small Tablet (>= 640px) */
        @media (min-width: 640px) {
            .badge { display: flex; }
            .brand-pill { display: inline-block; }
            .input-row { flex-direction: row; gap: 12px; }
            .text-input { font-size: 0.9rem; }
            .scenario-grid { grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
            .fm-toolbar-actions { width: auto; }
            .fm-toolbar-actions .btn { flex: 0 0 auto; }
            #toast { left: auto; right: 24px; bottom: 24px; max-width: 420px; }
        }

        /* Tablet & Small Desktop (>= 768px) */
        @media (min-width: 768px) {
            .control-view,
            .launcher-view,
            .terminal-view { padding: 24px 28px; gap: 20px; }
            .screen-view-container { padding: 20px; gap: 14px; }
            .procs-wrap { padding: 20px; }
            .media-wrap { padding: 24px; }
            .card-panel { padding: 22px 24px; border-radius: 16px; }
            .card-title { font-size: 1.08rem; margin-bottom: 16px; }
            .btn { min-height: 34px; padding: 7px 14px; }
            .screen-canvas-box { min-height: 420px; }
            .app-grid { grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 16px; }
            .grid-view { grid-template-columns: repeat(auto-fill, minmax(115px, 1fr)); gap: 14px; }
            .file-scroll-area { padding: 18px; }
            .fm-container { flex-direction: row; }
            aside {
                width: 240px;
                flex-direction: column;
                align-items: stretch;
                justify-content: space-between;
                border-right: 1px solid var(--border-subtle);
                border-bottom: none;
                padding: 0;
                overflow-x: visible;
            }
            aside > div:first-child { display: block; }
            aside .fm-nav-title,
            aside > div:last-child { display: block; }
            .fm-nav-item {
                padding: 9px 16px;
                margin: 2px 8px;
                font-size: 0.85rem;
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
            }
            .fm-toolbar {
                height: 52px;
                flex-wrap: nowrap;
                padding: 0 18px;
            }
            .modal-body { padding: 22px; }
            .modal-head { padding: 16px 22px; }
            .modal-foot { padding: 14px 22px; }
        }

        /* Wide Desktop (>= 1120px) */
        @media (min-width: 1120px) {
            header {
                height: 60px;
                flex-direction: row;
                align-items: center;
                justify-content: space-between;
                padding: 0 20px;
                gap: 16px;
            }
            .header-top { display: contents; }
            .brand { order: 1; font-size: 1.12rem; }
            .nav-tabs { order: 2; width: auto; flex: 0 1 auto; }
            .header-actions { order: 3; gap: 8px; }
        }

        /* ================= FLOATING FAB & QUICK ACTION MODAL ================= */
        .qa-fab {
            position: fixed;
            right: 20px;
            bottom: 22px;
            z-index: 950;
            width: 58px;
            height: 58px;
            border-radius: 50%;
            border: 1px solid rgba(56, 189, 248, 0.55);
            background: linear-gradient(135deg, #0284c7 0%, #38bdf8 50%, #6366f1 100%);
            color: #fff;
            font-size: 1.55rem;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            box-shadow: 0 10px 28px rgba(14, 165, 233, 0.48), 0 0 0 4px rgba(56, 189, 248, 0.14);
            transition: transform 0.18s ease, box-shadow 0.18s ease;
            -webkit-tap-highlight-color: transparent;
        }
        .qa-fab:hover {
            transform: translateY(-3px) scale(1.06);
            box-shadow: 0 14px 34px rgba(56, 189, 248, 0.62), 0 0 0 6px rgba(56, 189, 248, 0.22);
        }
        .qa-fab:active {
            transform: scale(0.95);
        }
        .qa-instant-box {
            padding: 12px;
            border-radius: 12px;
            background: linear-gradient(135deg, rgba(14, 165, 233, 0.12), rgba(168, 85, 247, 0.12));
            border: 1px solid rgba(56, 189, 248, 0.35);
            margin-bottom: 14px;
        }
        .qa-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
            max-height: 46vh;
            overflow-y: auto;
            padding-right: 2px;
        }
        .qa-card {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px;
            border-radius: 12px;
            background: rgba(15, 23, 42, 0.72);
            border: 1px solid rgba(148, 163, 184, 0.2);
            transition: border-color 0.15s ease, background 0.15s ease;
        }
        .qa-card:hover {
            border-color: rgba(56, 189, 248, 0.45);
            background: rgba(30, 41, 59, 0.85);
        }
        .qa-icon-badge {
            width: 42px;
            height: 42px;
            border-radius: 12px;
            background: rgba(56, 189, 248, 0.14);
            border: 1px solid rgba(56, 189, 248, 0.28);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.35rem;
            flex-shrink: 0;
        }
        .qa-info {
            flex: 1;
            min-width: 0;
        }
        .qa-title {
            font-weight: 700;
            font-size: 0.92rem;
            color: var(--text-main);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .qa-msg {
            font-size: 0.78rem;
            color: var(--text-dim);
            margin-top: 2px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            line-height: 1.35;
        }
        .qa-meta {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            margin-top: 5px;
        }
        .qa-pill {
            font-size: 0.68rem;
            font-weight: 700;
            padding: 2px 7px;
            border-radius: 999px;
            background: rgba(56, 189, 248, 0.14);
            color: #7dd3fc;
            border: 1px solid rgba(56, 189, 248, 0.25);
        }
        .qa-pill.vol {
            background: rgba(16, 185, 129, 0.14);
            color: #6ee7b7;
            border-color: rgba(16, 185, 129, 0.28);
        }
        .qa-actions {
            display: flex;
            align-items: center;
            gap: 6px;
            flex-shrink: 0;
        }
        .qa-play-btn {
            min-height: 40px;
            padding: 0 14px;
            border-radius: 10px;
            border: none;
            background: linear-gradient(135deg, #059669 0%, #10b981 100%);
            color: #fff;
            font-weight: 700;
            font-size: 0.84rem;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            box-shadow: 0 4px 12px rgba(16, 185, 129, 0.35);
        }
        .qa-play-btn:hover { filter: brightness(1.08); }
        .qa-play-btn:disabled { opacity: 0.6; cursor: wait; }
        .qa-icon-chip {
            width: 34px;
            height: 34px;
            border-radius: 8px;
            border: 1px solid rgba(148, 163, 184, 0.25);
            background: rgba(15, 23, 42, 0.6);
            cursor: pointer;
            font-size: 1rem;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
        .qa-icon-chip:hover { border-color: var(--accent); background: rgba(56, 189, 248, 0.16); }
        .qa-form-panel {
            padding: 14px;
            border-radius: 12px;
            background: rgba(15, 23, 42, 0.85);
            border: 1px solid rgba(56, 189, 248, 0.4);
            margin-bottom: 14px;
        }
        @media (max-width: 600px) {
            .qa-fab { right: 16px; bottom: 18px; width: 54px; height: 54px; font-size: 1.4rem; }
            .qa-card { flex-wrap: wrap; }
            .qa-actions { width: 100%; justify-content: flex-end; margin-top: 4px; }
        }

        /* ================= MODAL PENGATURAN TERPUSAT (5 TAB) ================= */
        .set-tabs {
            display: flex;
            gap: 6px;
            overflow-x: auto;
            padding-bottom: 8px;
            margin-bottom: 12px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.2);
            -webkit-overflow-scrolling: touch;
        }
        .set-tab-btn {
            flex: 0 0 auto;
            padding: 7px 12px;
            border-radius: 9px;
            border: 1px solid rgba(148, 163, 184, 0.22);
            background: rgba(15, 23, 42, 0.65);
            color: var(--text-dim);
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
        }
        .set-tab-btn.active {
            background: linear-gradient(135deg, rgba(2, 132, 199, 0.35), rgba(99, 102, 241, 0.35));
            border-color: var(--accent);
            color: #fff;
        }
        .set-pane { display: none; }
        .set-pane.active { display: block; }
        .set-section {
            padding: 12px;
            border-radius: 12px;
            background: rgba(15, 23, 42, 0.65);
            border: 1px solid rgba(148, 163, 184, 0.2);
            margin-bottom: 12px;
        }
        .set-section-title {
            font-size: 0.76rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            color: #7dd3fc;
            margin-bottom: 8px;
        }
        .set-item-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            padding: 8px 10px;
            border-radius: 9px;
            background: rgba(30, 41, 59, 0.6);
            border: 1px solid rgba(148, 163, 184, 0.15);
            margin-bottom: 6px;
            flex-wrap: wrap;
        }

    </style>
</head>
<body>

    <!-- HEADER -->
    <header>
        <div class="header-top">
            <div class="brand" onclick="switchTab('tab-fm')">
                <span class="brand-icon">🍏</span>
                <span class="brand-text">Mac Remote</span>
                <span class="brand-pill">PRO</span>
            </div>
            <div class="header-actions">
                <button class="btn btn-primary" id="btnVoiceMic" onclick="openVoiceModal()" style="border-radius: 999px; padding: 6px 14px; font-weight: 700; gap: 6px;" title="Kendali Suara AI Bahasa Indonesia (Jarvis Mode)">🎙️ Bicara</button>
                <button class="btn" onclick="openSettingsModal('tg')" style="padding: 6px 11px; font-size: 0.85rem;" title="Pengaturan Telegram Bot, Cast, Audio, Assistant & SSL">⚙️</button>
                <a href="vnc://10.66.66.4" class="btn" style="text-decoration: none; font-size: 0.78rem; padding: 6px 12px;" title="Klik untuk membuka Remote Desktop macOS">🖥️ VNC</a>
                <div class="badge">10.66.66.4</div>
            </div>
        </div>
        <div class="nav-tabs" id="navTabsBar">
            <button class="tab-btn active" id="btn-tab-fm" onclick="switchTab('tab-fm')">📁 Berkas</button>
            <button class="tab-btn" id="btn-tab-screen" onclick="switchTab('tab-screen')">📺 Live Screen</button>
            <button class="tab-btn" id="btn-tab-apps" onclick="switchTab('tab-apps')">🚀 Aplikasi</button>
            <button class="tab-btn" id="btn-tab-term" onclick="switchTab('tab-term')">💻 Terminal</button>
            <button class="tab-btn" id="btn-tab-ctrl" onclick="switchTab('tab-ctrl')">🎛️ Remote Hardware</button>
            <button class="tab-btn" id="btn-tab-procs" onclick="switchTab('tab-procs')">📊 Task Manager</button>
            <button class="tab-btn" id="btn-tab-media" onclick="switchTab('tab-media')">🎵 Media Player</button>
        </div>
    </header>

    <div class="tab-content-area">

        <!-- ================= TAB 1: FILE MANAGER (KODBOX) ================= -->
        <div class="view-section active" id="tab-fm">
            <div class="fm-container">
                <aside>
                    <div>
                        <div class="fm-nav-title">Akses Cepat</div>
                        <div class="fm-nav-item" onclick="loadFolder('')">🏠 Home (~)</div>
                        <div class="fm-nav-item" onclick="loadFolder('Documents')">📄 Documents</div>
                        <div class="fm-nav-item" onclick="loadFolder('Downloads')">📥 Downloads</div>
                        <div class="fm-nav-item" onclick="loadFolder('Desktop')">🖥️ Desktop</div>
                        <div class="fm-nav-item" onclick="loadFolder('https-server/shared_files')">📂 Shared Files</div>
                    </div>
                    <div style="padding: 16px; border-top: 1px solid var(--border-color); font-size: 0.8rem;">
                        <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                            <span style="color:var(--text-muted);">Penyimpanan Mac</span>
                            <strong id="diskPercent">0%</strong>
                        </div>
                        <div style="background:var(--border-color); height:6px; border-radius:3px; overflow:hidden;">
                            <div id="diskBar" style="background:var(--accent); height:100%; width:0%;"></div>
                        </div>
                        <div id="diskDetail" style="color:var(--text-muted); font-size:0.75rem; margin-top:4px;">-- free</div>
                    </div>
                </aside>
                <main class="fm-workspace">
                    <div class="fm-toolbar">
                        <div class="breadcrumbs" id="breadcrumbArea">
                            <span class="crumb" onclick="loadFolder('')">Home</span>
                        </div>
                        <div class="fm-toolbar-actions">
                            <button class="btn btn-primary" onclick="triggerUpload()"><span>📤</span> Upload</button>
                            <input type="file" id="filePicker" multiple style="display: none;" onchange="handleFileSelect(this.files)">
                            <button class="btn" onclick="openNewFolderModal()"><span>📁</span> Folder Baru</button>
                            <button class="btn" onclick="openNewFileModal()"><span>📄</span> File Baru</button>
                            <button class="btn" onclick="zipCurrentFolder()"><span>📦</span> Zip Folder</button>
                            <button class="btn" onclick="toggleView()" id="viewToggleBtn"><span>⊞</span> Grid</button>
                            <button class="btn" onclick="refreshFolder()"><span>🔄</span></button>
                        </div>
                    </div>
                    <div class="file-scroll-area" id="fileContainer" oncontextmenu="showEmptyContextMenu(event)">
                        <div style="text-align: center; color: var(--text-muted); padding: 40px;">Memuat...</div>
                    </div>
                </main>
            </div>
        </div>

        <!-- ================= TAB 2: REMOTE APP LAUNCHER ================= -->
        
        <!-- ================= TAB: LIVE SCREEN (WEB VNC) ================= -->
        <div class="view-section" id="tab-screen">
            <div class="screen-view-container">
                <div class="screen-toolbar">
                    <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
                        <button class="btn btn-primary" id="btnToggleStream" onclick="toggleScreenStream()">▶️ Mulai Live Stream</button>
                        <button class="btn" onclick="captureLiveScreenFrame()">📸 Refresh Frame</button>
                        <span class="badge" id="streamFpsBadge" style="display:none;">1.5 FPS Live</span>
                    </div>
                    <div style="display:flex; align-items:center; gap:10px; font-size:0.82rem; color:var(--text-muted);">
                        <span>Resolusi: <strong style="color:var(--accent);">1920×1080</strong></span>
                        <span>|</span>
                        <span>💡 <em>Klik / sentuh gambar untuk kendalikan Mac!</em></span>
                    </div>
                </div>

                <div class="screen-canvas-box" id="screenCanvasBox">
                    <img id="liveScreenImg" src="/api/remote/screen_live?t=1" alt="Live Mac Screen" onclick="handleScreenImageClick(event)" />
                    <div id="clickRipple" class="click-ripple"></div>
                </div>

                <div class="screen-key-bar">
                    <div style="display:flex; gap:6px; flex-wrap:wrap;">
                        <button class="btn" onclick="sendRemoteKey('Spotlight')">⌘ Space (Spotlight)</button>
                        <button class="btn" onclick="sendRemoteKey('Return')">↵ Enter</button>
                        <button class="btn" onclick="sendRemoteKey('Backspace')">⌫ Backspace</button>
                        <button class="btn" onclick="sendRemoteKey('Space')">Space</button>
                        <button class="btn" onclick="sendRemoteKey('Tab')">⇥ Tab</button>
                        <button class="btn" onclick="sendRemoteKey('Escape')">Esc</button>
                    </div>
                    <div style="display:flex; gap:8px; flex:1; max-width:420px;">
                        <input type="text" id="screenTextInput" class="text-input" placeholder="Ketik teks untuk dikirim ke Mac..." onkeydown="if(event.key==='Enter') sendRemoteScreenText()">
                        <button class="btn btn-primary" onclick="sendRemoteScreenText()">Ketik</button>
                    </div>
                </div>
            </div>
        </div>


<div class="view-section" id="tab-apps">
            <div class="launcher-view">
                <div class="card-panel">
                    <div class="card-title">🚀 Buka Aplikasi di Mac Anda</div>
                    <div class="app-grid">
                        <div class="app-item" onclick="launchApp('Google Chrome')">
                            <div class="app-icon">🌐</div>
                            <div class="app-name">Chrome</div>
                        </div>
                        <div class="app-item" onclick="launchApp('Safari')">
                            <div class="app-icon">🧭</div>
                            <div class="app-name">Safari</div>
                        </div>
                        <div class="app-item" onclick="launchApp('Terminal')">
                            <div class="app-icon">💻</div>
                            <div class="app-name">Terminal</div>
                        </div>
                        <div class="app-item" onclick="launchApp('Music')">
                            <div class="app-icon">🎵</div>
                            <div class="app-name">Music</div>
                        </div>
                        <div class="app-item" onclick="launchApp('Calculator')">
                            <div class="app-icon">🧮</div>
                            <div class="app-name">Calculator</div>
                        </div>
                        <div class="app-item" onclick="launchApp('Notes')">
                            <div class="app-icon">📝</div>
                            <div class="app-name">Notes</div>
                        </div>
                        <div class="app-item" onclick="launchApp('Antigravity IDE')">
                            <div class="app-icon">🛠️</div>
                            <div class="app-name">Antigravity</div>
                        </div>
                        <div class="app-item" onclick="launchApp('System Preferences')">
                            <div class="app-icon">⚙️</div>
                            <div class="app-name">Settings</div>
                        </div>
                    </div>

                    <div style="margin-top: 24px;">
                        <label style="font-size: 0.85rem; color: var(--text-muted);">Buka Aplikasi Lain (Ketik nama aplikasi):</label>
                        <div class="input-row">
                            <input type="text" id="customAppInput" class="text-input" placeholder="Contoh: TextEdit, Preview, Roblox...">
                            <button class="btn btn-primary" onclick="launchCustomApp()">Jalankan</button>
                        </div>
                    </div>
                </div>

                <div class="card-panel">
                    <div class="card-title">🌐 Buka URL Website di Layar Mac</div>
                    <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 8px;">Kirim link website dari HP Anda, browser Mac akan langsung membukanya di layar.</p>
                    <div class="input-row">
                        <input type="url" id="remoteUrlInput" class="text-input" placeholder="https://youtube.com">
                        <button class="btn btn-primary" onclick="openRemoteUrl()">Buka Link di Mac</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- ================= TAB 3: WEB TERMINAL ================= -->
        <div class="view-section" id="tab-term">
            <div class="terminal-view">
                <div class="quick-cmds">
                    <button class="btn" onclick="runQuickCmd('uptime')">Uptime</button>
                    <button class="btn" onclick="runQuickCmd('df -h')">Disk Space</button>
                    <button class="btn" onclick="runQuickCmd('top -l 1 -n 10')">Top CPU</button>
                    <button class="btn" onclick="runQuickCmd('ifconfig | grep inet')">IP Network</button>
                    <button class="btn" onclick="runQuickCmd('pmset -g batt 2>/dev/null || echo Power OK')">Power Status</button>
                    <button class="btn btn-danger" onclick="clearTerminal()">Bersihkan Layar</button>
                </div>
                <div class="mac-terminal-chrome">
                    <div class="mac-traffic-bar">
                        <div class="mac-traffic-dots">
                            <span class="traffic-dot dot-red"></span>
                            <span class="traffic-dot dot-yellow"></span>
                            <span class="traffic-dot dot-green"></span>
                        </div>
                        <span class="mac-term-title">subuhkurniawan@mac-mini: ~ (zsh)</span>
                        <div style="width: 48px;"></div>
                    </div>
                    <div class="term-screen" id="terminalOutput">Selamat datang di Remote Web Terminal Mac!
Ketik perintah di bawah ini (misal: ls -la, python3 --version, git status, dll.)
--------------------------------------------------------------------------------</div>
                </div>
                <div class="input-row">
                    <span style="display:flex; align-items:center; color:var(--accent); font-family:monospace;">mac-mini $</span>
                    <input type="text" id="termInput" class="text-input" style="font-family: monospace;" placeholder="Ketik perintah bash/zsh..." onkeydown="if(event.key==='Enter') executeTerminalCommand()">
                    <button class="btn btn-primary" onclick="executeTerminalCommand()">Kirim</button>
                </div>
            </div>
        </div>

        <!-- ================= TAB 4: REMOTE HARDWARE CONTROL ================= -->
        <div class="view-section" id="tab-ctrl">
            <div class="control-view">

                <!-- SMART AUTOMATION SCENARIOS -->
                <div class="card-panel">
                    <div class="card-title">⚡ Smart Automation Presets (Skenario 1-Klik)</div>
                    <p style="color: var(--text-muted); font-size: 0.85rem;">
                        Jalankan makro otomatisasi pintar untuk mengatur lingkungan Mac dan rumah dalam sekali klik.
                    </p>
                    <div class="scenario-grid">
                        <div class="scenario-card" onclick="runScenario('night')">
                            <div class="scenario-icon">🌙</div>
                            <div class="scenario-title">Mode Malam / Istirahat</div>
                            <div class="scenario-desc">Jeda musik, volume 15%, umumkan selamat malam ke Google Home & matikan layar.</div>
                        </div>
                        <div class="scenario-card" onclick="runScenario('focus')">
                            <div class="scenario-icon">💼</div>
                            <div class="scenario-title">Mode Fokus Kerja</div>
                            <div class="scenario-desc">Buka VS Code & Terminal, setel volume 35%, dan nyalakan musik fokus.</div>
                        </div>
                        <div class="scenario-card" onclick="runScenario('away')">
                            <div class="scenario-icon">🚪</div>
                            <div class="scenario-title">Mode Tinggalkan Meja</div>
                            <div class="scenario-desc">Bisukan audio 0%, kunci layar seketika, dan aktifkan proteksi Mac.</div>
                        </div>
                    </div>
                </div>

                <!-- UNIVERSAL CLIPBOARD SYNC -->
                <div class="card-panel">
                    <div class="card-title">📋 Universal Clipboard Sync (2-Arah Mac ⇄ HP)</div>
                    <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 12px;">
                        Salin teks atau link dari Mac ke HP/Browser Anda, atau kirim teks baru langsung ke clipboard Mac Anda.
                    </p>
                    <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                        <div style="flex: 1; min-width: 280px;">
                            <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 6px;">Teks Clipboard Mac Saat Ini:</div>
                            <textarea id="macClipboardDisplay" class="text-input" style="width: 100%; height: 75px; resize: none; font-family: monospace; font-size: 0.85rem;" readonly placeholder="Klik muat untuk membaca clipboard Mac..."></textarea>
                            <div style="display: flex; gap: 8px; margin-top: 8px;">
                                <button class="btn" onclick="refreshMacClipboard()">🔄 Muat Clipboard Mac</button>
                                <button class="btn btn-primary" onclick="copyMacClipboardToLocal()">📋 Salin ke Perangkat Ini</button>
                            </div>
                        </div>
                        <div style="flex: 1; min-width: 280px;">
                            <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 6px;">Kirim Teks/Link Baru ke Mac:</div>
                            <textarea id="newClipboardText" class="text-input" style="width: 100%; height: 75px; resize: none; font-size: 0.85rem;" placeholder="Ketik atau tempel teks/link di sini..."></textarea>
                            <div style="display: flex; gap: 8px; margin-top: 8px;">
                                <button class="btn btn-primary" onclick="sendTextToMacClipboard()">📥 Kirim ke Clipboard Mac</button>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- SCREEN SHARING / VNC REMOTE DESKTOP -->
                <div class="card-panel" style="border-color: var(--accent); background: linear-gradient(135deg, rgba(56, 189, 248, 0.08) 0%, rgba(19, 27, 46, 0.95) 100%);">
                    <div class="card-title">🖥️ macOS Screen Sharing (Remote Desktop VNC)</div>
                    <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 12px;">
                        Kendalikan visual layar desktop Mac mini Anda secara langsung melalui Apple Screen Sharing (Port 5900 WireGuard).
                    </p>
                    <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                        <a href="vnc://10.66.66.4" class="btn btn-primary" style="text-decoration: none; padding: 10px 18px; font-size: 0.9rem;">
                            🚀 Buka Screen Sharing Mac (vnc://10.66.66.4)
                        </a>
                        <button class="btn" onclick="openModal('vncGuideModal')">📖 Panduan Konek dari HP / Windows</button>
                    </div>
                    <div style="margin-top: 12px; font-size: 0.8rem; color: var(--text-muted);">
                        Alamat VNC: <code style="color: var(--accent);">10.66.66.4:5900</code> &nbsp;|&nbsp; Login: <em>Username & Password akun Mac Anda</em>
                    </div>
                </div>
                <!-- VOLUME CONTROL -->
                <div class="card-panel">
                    <div class="card-title">🔊 Kendali Suara Speaker Mac</div>
                    <div class="slider-box">
                        <span style="font-size: 1.2rem;">🔈</span>
                        <input type="range" id="volumeSlider" min="0" max="100" value="50" onchange="setMacVolume(this.value)">
                        <span id="volumeValue" style="width: 40px; font-weight: bold;">50%</span>
                        <button class="btn" onclick="muteMacVolume()">Mute</button>
                    </div>
                </div>

                <!-- TEXT TO SPEECH BAHASA INDONESIA REALISTIS -->
                <div class="card-panel" style="border-color: var(--accent); background: linear-gradient(135deg, rgba(56, 189, 248, 0.05) 0%, rgba(26, 36, 59, 0.95) 100%);">
                    <div class="card-title">🗣️ Pesan Suara Bahasa Indonesia (Suara Realistis)</div>
                    <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 10px;">
                        Kirim pesan dalam Bahasa Indonesia yang fasih dan alami. Anda dapat langsung memutarnya di speaker fisik Mac mini atau mendengarkannya langsung di browser HP ini.
                    </p>
                    <textarea id="speakInput" class="text-input" style="width: 100%; height: 75px; resize: none; margin-bottom: 10px; font-size: 0.9rem;" placeholder="Tulis kalimat apa saja di sini... (Contoh: Halo Pak Subuh, laporan pekerjaan Anda sudah siap.)"></textarea>
                    
                    <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; font-size: 0.75rem;">
                        <span style="color: var(--text-muted); align-self: center;">Pesan Cepat:</span>
                        <button class="btn" style="padding: 3px 8px; font-size: 0.75rem;" onclick="setQuickSpeak('Halo Pak Subuh, ada tugas baru untuk Anda.')">Halo</button>
                        <button class="btn" style="padding: 3px 8px; font-size: 0.75rem;" onclick="setQuickSpeak('Proses server telah selesai dilakukan.')">Selesai</button>
                        <button class="btn" style="padding: 3px 8px; font-size: 0.75rem;" onclick="setQuickSpeak('Tolong cek file yang baru saja diupload ke Mac.')">Cek File</button>
                        <button class="btn" style="padding: 3px 8px; font-size: 0.75rem;" onclick="setQuickSpeak('Komputer Mac mini dalam kondisi aktif dan normal.')">Status OK</button>
                    </div>

                    <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                        <select id="speakVoice" class="text-input" style="width: auto; padding: 7px 12px;">
                            <option value="female">👩 Suara Wanita (Natural)</option>
                            <option value="male">👨 Suara Pria (Natural)</option>
                        </select>
                        <button class="btn btn-primary" onclick="speakOnMac('speaker')">🔊 Putar di Speaker Mac</button>
                        <button class="btn" onclick="speakOnMac('preview')">🎧 Dengarkan di Browser Ini</button>
                    </div>
                </div>

                <!-- UNIFIED CAST CENTER (GOOGLE HOME & ANDROID TV + YOUTUBE/URL) -->
                <div class="card-panel" style="border-color: #3b82f6; background: linear-gradient(135deg, rgba(59, 130, 246, 0.08) 0%, rgba(26, 36, 59, 0.95) 100%);">
                    <div class="card-title" style="color: #60a5fa;">📢📺 Cast Center (Google Home & Android TV)</div>
                    <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 12px;">
                        Satu kontrol terpadu untuk <b>Google Home</b> &amp; <b>TV Stick Mi</b>: pilih perangkat target di bawah untuk kirim <b>TTS</b>, <b>Rekaman Suara</b>, <b>Pustaka Audio</b>, atau <b>Push URL YouTube / Stream</b>.
                    </p>
                    
                    <div style="display: flex; gap: 8px; margin-bottom: 12px; align-items: center; flex-wrap: wrap;">
                        <select id="castDeviceSelect" class="text-input" style="flex: 1; min-width: 240px; padding: 7px 12px;">
                            <option value="192.168.0.198">📢 Family Room speaker (Google Home - 192.168.0.198)</option>
                            <option value="192.168.0.197">📺 TV Stick Mi (MiTV-AESP0 - 192.168.0.197)</option>
                        </select>
                        <button class="btn" onclick="scanCastDevices(true)">🔍 Pindai Perangkat Cast</button>
                    </div>

                    <!-- PUSH URL YOUTUBE & MEDIA STREAM (KHUSUS TV / SPEAKER) -->
                    <div style="margin-bottom: 14px; padding: 10px 12px; border-radius: 10px; background: rgba(168, 85, 247, 0.1); border: 1px solid rgba(168, 85, 247, 0.35);">
                        <div style="font-size: 0.78rem; text-transform: uppercase; color: #c084fc; margin-bottom: 6px; font-weight: 700; letter-spacing: 0.5px;">▶️ PUSH YOUTUBE &amp; URL MEDIA (TV / SPEAKER):</div>
                        <div class="input-row" style="margin-top: 0;">
                            <input type="url" id="castUrlInput" class="text-input" placeholder="Tempel link YouTube, Shorts, .m3u8 (IPTV), MP4, MP3, atau Gambar..." onkeydown="if(event.key==='Enter') sendCastUrl(false)">
                            <button type="button" class="btn btn-primary" style="background: linear-gradient(135deg, #9333ea 0%, #c084fc 100%); color: #fff;" onclick="sendCastUrl(false)">▶️ Push URL</button>
                            <button type="button" class="btn" onclick="sendCastUrl(true)" title="Tambahkan ke antrean YouTube di TV">➕ Antre YT</button>
                        </div>
                    </div>

                    <!-- PERINTAH GOOGLE ASSISTANT LANGSUNG (SDK + BALASAN SUARA DI GOOGLE HOME) -->
                    <div style="margin-bottom: 14px; padding: 10px 12px; border-radius: 10px; background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.3);">
                        <div style="font-size: 0.78rem; text-transform: uppercase; color: #34d399; margin-bottom: 6px; font-weight: 700; letter-spacing: 0.5px;">🤖 PERINTAH GOOGLE ASSISTANT (API LANGSUNG + BALASAN SUARA):</div>
                        <div class="input-row" style="margin-top: 0;">
                            <input type="text" id="askGhInput" class="text-input" placeholder="Contoh: jam berapa sekarang, nyalakan lampu ruang tamu, cuaca hari ini..." onkeydown="if(event.key==='Enter') sendAskGoogleHome()">
                            <button type="button" class="btn btn-primary" style="background: linear-gradient(135deg, #059669 0%, #10b981 100%); color: #fff;" onclick="sendAskGoogleHome()">🤖 Kirim ke Assistant</button>
                        </div>
                        <div id="askGhReplyBox" style="display:none; margin-top: 8px; padding: 8px 10px; border-radius: 8px; background: rgba(0,0,0,0.25); font-size: 0.82rem; color: #d1fae5;">
                            <div id="askGhReplyText" style="margin-bottom: 6px;"></div>
                            <audio id="askGhReplyAudio" controls style="width: 100%; height: 32px; display: none;"></audio>
                        </div>
                    </div>

                    <!-- PILIHAN SUARA BROADCAST -->
                    <div style="margin-bottom: 12px;">
                        <div style="font-size: 0.78rem; text-transform: uppercase; color: var(--text-dim); margin-bottom: 6px; font-weight: 700; letter-spacing: 0.5px;">PILIH SUARA BROADCAST:</div>
                        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                            <button type="button" class="btn btn-primary" id="btnModeWanita1" onclick="setCastVoiceOption('wanita1')">👩 1. Suara Wanita (Gadis)</button>
                            <button type="button" class="btn" id="btnModePria" onclick="setCastVoiceOption('pria')">👨 2. Suara Pria (Ardi)</button>
                            <button type="button" class="btn" id="btnModeWanita2" onclick="setCastVoiceOption('wanita2')">👧 3. Suara Wanita 2 (Google ID)</button>
                            <button type="button" class="btn" id="btnModeVoice" onclick="setCastVoiceOption('voice')">🎙️ 4. Suara Asli Rekaman Saya (Multi)</button>
                        </div>
                    </div>

                    <!-- TEXT INPUT (Untuk Suara TTS) -->
                    <div id="castTextSection">
                        <textarea id="castMessageInput" class="text-input" style="width: 100%; height: 65px; resize: none; margin-bottom: 10px; font-size: 0.9rem;" placeholder="Tulis pengumuman untuk Google Home... (Contoh: Perhatian, makan malam sudah siap di meja makan!)"></textarea>
                        <div style="display: flex; gap: 10px; align-items: center;">
                            <button id="btnBroadcastCast" class="btn btn-primary" style="background: #3b82f6; border-color: #3b82f6;" onclick="broadcastToGoogleHome()">📢 Siarkan ke Google Home</button>
                        </div>
                    </div>

                    <!-- MULTI-VOICE RECORDING STUDIO (Rekam Ulang & Multi Suara) -->
                    <div id="castRecordSection" style="display: none; background: rgba(8, 12, 22, 0.6); padding: 18px; border-radius: 14px; border: 1px solid var(--border-subtle); margin-bottom: 12px;">
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; flex-wrap: wrap; gap: 8px;">
                            <span style="font-size: 0.92rem; font-weight: 700; color: #38bdf8;">🎙️ Studio Rekam Suara Sendiri:</span>
                            <span id="recordTimer" style="font-family: monospace; font-size: 1rem; color: #f43f5e; font-weight: 700;">⏱️ 00:00</span>
                        </div>

                        <!-- Bar Tombol Perekam -->
                        <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px;">
                            <button type="button" id="btnRecordVoice" class="btn btn-danger" onclick="toggleVoiceRecording()">🔴 Mulai Rekam Baru</button>
                            <audio id="recordedAudioPreview" controls style="display: none; height: 36px; max-width: 260px;"></audio>
                            <button type="button" id="btnRerecordVoice" class="btn" style="display: none;" onclick="rerecordCastVoice()">🔁 Rekam Ulang</button>
                            <button type="button" id="btnSendRecordedVoice" class="btn btn-primary" style="display: none; background: #10b981; border-color: #10b981;" onclick="sendRecordedVoiceToGoogleHome()">📢 Siarkan ke Google Home</button>
                            <button type="button" id="btnSaveVoiceClip" class="btn" style="display: none;" onclick="showSaveClipForm()">💾 Simpan ke Daftar Suara</button>
                        </div>

                        <!-- Form Simpan Label Rekaman -->
                        <div id="saveClipForm" style="display: none; margin-bottom: 14px; padding: 10px; background: rgba(15, 23, 42, 0.8); border-radius: 8px; border: 1px solid var(--border-subtle);">
                            <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 6px;">Beri Nama Rekaman Suara Ini:</div>
                            <div style="display: flex; gap: 8px;">
                                <input type="text" id="clipLabelInput" class="text-input" placeholder="Contoh: Panggilan Makan Siang, Jemput Anak..." style="font-size: 0.85rem;">
                                <button type="button" class="btn btn-primary" onclick="confirmSaveVoiceClip()">Simpan</button>
                                <button type="button" class="btn" onclick="document.getElementById('saveClipForm').style.display='none'">Batal</button>
                            </div>
                        </div>

                        <!-- Daftar Multi Suara Tersimpan -->
                        <div style="margin-top: 14px; border-top: 1px solid rgba(255, 255, 255, 0.08); padding-top: 12px;">
                            <div style="font-size: 0.78rem; font-weight: 700; color: var(--text-dim); text-transform: uppercase; margin-bottom: 8px;">📚 DAFTAR MULTI SUARA TERSIMPAN:</div>
                            <div id="savedVoiceClipsList" style="display: flex; flex-direction: column; gap: 8px;">
                                <div style="color: var(--text-dim); font-size: 0.8rem; font-style: italic;">Belum ada rekaman tersimpan. Rekam suara Anda di atas dan klik 'Simpan ke Daftar Suara'.</div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- UNIVERSAL CLIPBOARD -->
                <div class="card-panel">
                    <div class="card-title">📋 Sinkronisasi Clipboard Mac</div>
                    <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 8px;">Baca teks yang baru saja di-copy di Mac, atau kirim teks dari HP ke clipboard Mac.</p>
                    <div class="input-row">
                        <input type="text" id="clipboardInput" class="text-input" placeholder="Teks untuk clipboard Mac...">
                        <button class="btn btn-primary" onclick="setClipboard()">Kirim ke Mac</button>
                        <button class="btn" onclick="getClipboard()">Baca dari Mac</button>
                    </div>
                </div>

                <!-- NOTIFIKASI POP-UP & CHAT INTERAKTIF MAC (DILENGKAPI VOICE MESSAGE DARI MIC) -->
                <div class="card-panel" style="border-color: #10b981; background: linear-gradient(135deg, rgba(16, 185, 129, 0.06) 0%, rgba(26, 36, 59, 0.95) 100%);">
                    <div class="card-title" style="color: #34d399;">💬 Intercom Chat & Pop-Up Layar Mac</div>
                    <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 10px;">
                        Kirim pesan ke layar Mac. Pilih <b>Pop-Up Interaktif</b> agar orang di depan Mac bisa membalas chat, atau <b>Pesan Suara Voice</b> yang langsung berbunyi di speaker Mac mini!
                    </p>
                    <div class="input-row" style="margin-bottom: 8px;">
                        <input type="text" id="notifyTitle" class="text-input" style="max-width: 170px;" placeholder="Nama Pengirim" value="Remote HP">
                        <input type="text" id="notifyBody" class="text-input" placeholder="Tulis pesan untuk orang di depan Mac...">
                    </div>
                    <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                        <button id="btnInteractiveChat" class="btn btn-primary" style="background: #10b981; border-color: #10b981;" onclick="sendInteractivePopup()">💬 Kirim Pop-Up & Tunggu Balasan</button>
                        <button class="btn" onclick="sendDesktopNotification()">🔔 Kirim Banner Saja</button>
                    </div>

                    <!-- INTERCOM VOICE MESSAGE LANGSUNG DARI MIKROFON -->
                    <div style="margin-top: 14px; padding-top: 14px; border-top: 1px solid rgba(255, 255, 255, 0.08);">
                        <div style="font-size: 0.85rem; font-weight: 600; color: #34d399; margin-bottom: 6px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
                            <span>🎙️ Kirim Pesan Suara Langsung ke Speaker Mac (Mic HP):</span>
                            <span id="macVoiceTimer" style="font-family: monospace; font-size: 0.95rem; color: #f43f5e; font-weight: 700;">⏱️ 00:00</span>
                        </div>
                        <p style="font-size: 0.78rem; color: var(--text-muted); margin-bottom: 10px;">
                            Bicara ke mikrofon HP/browser Anda → suara rekaman asli Anda langsung diputar di speaker Mac mini dan memunculkan pop-up di layar Mac!
                        </p>
                        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                            <button type="button" id="btnRecordMacVoice" class="btn btn-danger" onclick="toggleMacVoiceRecording()">🔴 Rekam Suara untuk Mac</button>
                            <audio id="macVoicePreview" controls style="display: none; height: 34px; max-width: 250px;"></audio>
                            <button type="button" id="btnRerecordMacVoice" class="btn" style="display: none;" onclick="rerecordMacVoice()">🔁 Rekam Ulang</button>
                            <button type="button" id="btnSendMacVoice" class="btn btn-primary" style="display: none; background: #10b981; border-color: #10b981;" onclick="sendVoiceMessageToMac()">🔊 Putar di Speaker Mac & Pop-Up</button>
                        </div>
                    </div>

                    <div id="interactiveReplyBox" style="display: none; margin-top: 12px; padding: 12px 14px; border-radius: 8px; background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.35);">
                        <div style="font-weight: 600; font-size: 0.85rem; color: #34d399; margin-bottom: 4px;">💬 Balasan dari Layar Mac:</div>
                        <div id="interactiveReplyText" style="font-size: 1.05rem; color: #f8fafc; font-style: italic;"></div>
                        <div id="interactiveReplyTime" style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;"></div>
                    </div>
                </div>

                <!-- LIVE AUDIO MONITOR & INSTANT STOP CONTROLLER -->
                <div class="card-panel" style="border-color: #ef4444; background: linear-gradient(135deg, rgba(239, 68, 68, 0.08) 0%, rgba(26, 36, 59, 0.95) 100%);">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px;">
                        <div class="card-title" style="color: #f87171; margin-bottom: 0;">🎛️ Live Audio Monitor & Kontrol Stop Instan</div>
                        <button class="btn btn-danger" style="padding: 6px 14px; font-weight: 700; font-size: 0.85rem;" onclick="stopAudioPlayback('all')">🛑 Hentikan Semua Suara (Stop All)</button>
                    </div>
                    <p style="color: var(--text-muted); font-size: 0.83rem; margin-bottom: 12px;">
                        Pantau status pemutaran suara langsung di Speaker Mac dan Google Home secara real-time. Hentikan suara yang sedang berbunyi dengan 1 klik tanpa suara bertabrakan (Anti-Overlap).
                    </p>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px;">
                        <!-- MONITOR MAC MINI SPEAKER -->
                        <div style="background: rgba(15, 23, 42, 0.65); padding: 12px 14px; border-radius: 10px; border: 1px solid rgba(255, 255, 255, 0.08); display: flex; flex-direction: column; justify-content: space-between;">
                            <div>
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                                    <span style="font-size: 0.88rem; font-weight: 700; color: #38bdf8;">🔊 Speaker Mac mini (afplay):</span>
                                    <span id="badgeMacLive" style="padding: 2px 8px; border-radius: 999px; font-size: 0.72rem; font-weight: 700; background: rgba(148, 163, 184, 0.2); color: #94a3b8;">IDLE</span>
                                </div>
                                <div id="infoMacLive" style="font-size: 0.82rem; color: var(--text-muted); min-height: 22px; word-break: break-all;">
                                    Tidak ada audio yang sedang berputar di Mac.
                                </div>
                            </div>
                            <div style="margin-top: 10px; display: flex; gap: 8px; align-items: center;">
                                <button id="btnStopMacLive" class="btn btn-danger" style="padding: 6px 12px; font-size: 0.82rem; width: 100%;" onclick="stopAudioPlayback('mac')">🛑 Stop Suara Mac</button>
                            </div>
                        </div>

                        <!-- MONITOR PERANGKAT CAST (GOOGLE HOME + ANDROID TV MULTI-DEVICE) -->
                        <div id="castDevicesMonitor" style="display: contents;"></div>
                    </div>
                </div>

                <!-- PUSTAKA AUDIO & VIDEO SERVER (MEDIA LIBRARY & PLAYER) -->
                <div class="card-panel" style="border-color: #ec4899; background: linear-gradient(135deg, rgba(236, 72, 153, 0.08) 0%, rgba(26, 36, 59, 0.95) 100%);">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;">
                        <div class="card-title" style="color: #f472b6; margin-bottom: 0;">🎵🎬 Pustaka Audio &amp; Video Server (Database SQLite)</div>
                        <button class="btn" style="padding: 5px 12px; font-size: 0.8rem; border-color: #ec4899; color: #fbcfe8;" onclick="loadAudioLibrary()">🔄 Refresh Pustaka</button>
                    </div>
                    <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 14px;">
                        Unggah berkas <b>Audio</b> (MP3, WAV, M4A, AAC, OGG, FLAC) atau <b>Video</b> (MP4, M4V, MOV, WebM, MKV) ke server. Tersimpan permanen dan dapat langsung di-cast ke <b>TV Stick Mi</b>, <b>Google Home</b>, <b>Speaker Mac</b>, atau dijadikan <b>jadwal suara</b>.
                    </p>

                    <!-- Form Upload Audio / Video Baru -->
                    <div style="background: rgba(8, 12, 22, 0.6); padding: 14px; border-radius: 12px; border: 1px solid var(--border-subtle); margin-bottom: 16px;">
                        <div style="font-size: 0.88rem; font-weight: 700; color: #f472b6; margin-bottom: 10px;">📤 Unggah File Audio / Video Baru ke Database:</div>
                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin-bottom: 10px;">
                            <div>
                                <label style="font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 4px;">Nama / Label Media:</label>
                                <input type="text" id="audioUploadLabel" class="text-input" placeholder="Contoh: Bel Masuk, Video Profil, Lagu Sambutan..." style="width: 100%; font-size: 0.85rem;">
                            </div>
                            <div>
                                <label style="font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 4px;">Pilih File Audio / Video:</label>
                                <input type="file" id="audioUploadFile" accept=".mp3,.wav,.m4a,.aac,.ogg,.flac,.mp4,.m4v,.mov,.webm,.mkv,audio/*,video/*" class="text-input" style="width: 100%; font-size: 0.82rem; padding: 5px 8px;">
                            </div>
                        </div>

                        <!-- Progress Bar Upload Real-Time -->
                        <div id="audioUploadProgressWrap" style="display: none; margin-bottom: 10px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.78rem; color: #fbcfe8; margin-bottom: 5px;">
                                <span id="audioUploadProgressText">Mengunggah...</span>
                                <strong id="audioUploadPercent">0%</strong>
                            </div>
                            <div style="width: 100%; height: 8px; background: rgba(255,255,255,0.1); border-radius: 999px; overflow: hidden;">
                                <div id="audioUploadProgressBar" style="width: 0%; height: 100%; background: linear-gradient(90deg, #ec4899, #a855f7, #38bdf8); transition: width 0.15s ease;"></div>
                            </div>
                        </div>

                        <div style="display: flex; justify-content: flex-end;">
                            <button id="btnUploadAudio" class="btn btn-primary" style="background: #ec4899; border-color: #ec4899;" onclick="uploadAudioFile()">📤 Unggah &amp; Simpan ke Pustaka</button>
                        </div>
                    </div>

                    <!-- Pencarian, Filter & Daftar File Media -->
                    <div>
                        <div style="font-size: 0.85rem; font-weight: 700; color: #f8fafc; margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
                            <span>📁 Media Tersimpan:</span>
                            <span id="audioCountBadge" style="font-size: 0.75rem; background: rgba(236, 72, 153, 0.2); color: #fbcfe8; padding: 2px 8px; border-radius: 10px; border: 1px solid rgba(236, 72, 153, 0.4);">0 Media</span>
                        </div>

                        <!-- Bar Pencarian & Filter Tipe -->
                        <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center;">
                            <input type="search" id="mediaSearchInput" class="text-input" placeholder="🔍 Cari judul atau nama file..." oninput="onMediaFilterChange()" style="flex: 1; min-width: 180px; padding: 7px 12px; font-size: 0.85rem;">
                            <div style="display: flex; gap: 4px;">
                                <button type="button" id="btnFilterAll" class="btn btn-primary" style="padding: 5px 10px; font-size: 0.76rem; min-height: 34px;" onclick="setMediaTypeFilter('all')">Semua</button>
                                <button type="button" id="btnFilterAudio" class="btn" style="padding: 5px 10px; font-size: 0.76rem; min-height: 34px;" onclick="setMediaTypeFilter('audio')">🎵 Audio</button>
                                <button type="button" id="btnFilterVideo" class="btn" style="padding: 5px 10px; font-size: 0.76rem; min-height: 34px;" onclick="setMediaTypeFilter('video')">🎬 Video</button>
                            </div>
                        </div>

                        <div id="audioEmptyState" style="text-align: center; padding: 20px 10px; color: var(--text-muted); font-size: 0.85rem; background: rgba(0,0,0,0.15); border-radius: 8px; border: 1px dashed var(--border-subtle);">
                            Belum ada file media di pustaka database. Unggah file Audio atau Video di atas!
                        </div>
                        <div id="audioListContainer" style="display: flex; flex-direction: column; gap: 8px;"></div>

                        <!-- Pagination Controls -->
                        <div id="audioPaginationBar" style="display: none; margin-top: 12px; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; background: rgba(8, 12, 22, 0.5); padding: 8px 12px; border-radius: 10px; border: 1px solid var(--border-subtle);">
                            <button type="button" id="btnMediaPrev" class="btn" style="padding: 5px 12px; font-size: 0.78rem; min-height: 32px;" onclick="changeMediaPage(-1)">◀️ Prev</button>
                            <span id="mediaPageInfo" style="font-size: 0.8rem; color: var(--text-secondary); font-weight: 600;">Halaman 1 / 1</span>
                            <button type="button" id="btnMediaNext" class="btn" style="padding: 5px 12px; font-size: 0.78rem; min-height: 32px;" onclick="changeMediaPage(1)">Next ▶️</button>
                        </div>
                    </div>
                </div>

                <!-- VOICE SCHEDULE / CAST & SAY SCHEDULER -->
                <div class="card-panel" style="border-color: #8b5cf6; background: linear-gradient(135deg, rgba(139, 92, 246, 0.08) 0%, rgba(26, 36, 59, 0.95) 100%);">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;">
                        <div class="card-title" style="color: #a78bfa; margin-bottom: 0;">⏰ Manajemen Jadwal Suara Fleksibel (Cast & Say - SQLite)</div>
                        <button class="btn" style="padding: 5px 12px; font-size: 0.8rem; border-color: #8b5cf6; color: #c4b5fd;" onclick="loadSchedules()">🔄 Refresh Jadwal</button>
                    </div>
                    <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 14px;">
                        Jadwalkan suara otomatis dengan mode <b>Teks-ke-Suara (TTS)</b> atau <b>File Audio dari Pustaka Server</b>. Mendukung pemilihan tanggal dan jam spesifik, hitung mundur, atau rutinitas harian berulang.
                    </p>

                    <!-- Form Tambah Jadwal -->
                    <div style="background: rgba(8, 12, 22, 0.6); padding: 16px; border-radius: 12px; border: 1px solid var(--border-subtle); margin-bottom: 16px;">
                        
                        <!-- Pilihan Mode Konten (TTS vs Audio/Video Pustaka vs YouTube/URL TV) -->
                        <div style="margin-bottom: 14px;">
                            <label style="font-size: 0.78rem; color: var(--text-dim); text-transform: uppercase; font-weight: 700; display: block; margin-bottom: 6px;">PILIH TIPE KONTEN JADWAL:</label>
                            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                                <button type="button" class="btn btn-primary" id="btnSchedModeTTS" onclick="switchSchedMode('tts')" style="flex: 1; min-width: 130px;">🗣️ 1. AI TTS</button>
                                <button type="button" class="btn" id="btnSchedModeAudio" onclick="switchSchedMode('audio')" style="flex: 1; min-width: 130px;">🎵🎬 2. Audio / Video</button>
                                <button type="button" class="btn" id="btnSchedModeURL" onclick="switchSchedMode('url')" style="flex: 1; min-width: 130px;">▶️ 3. YouTube / URL</button>
                            </div>
                        </div>

                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-bottom: 12px;">
                            <div>
                                <label style="font-size: 0.78rem; color: var(--text-dim); text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 4px;">Nama / Label Jadwal:</label>
                                <input type="text" id="schedLabelInput" class="text-input" placeholder="Contoh: Bel Masuk, Murattal Pagi TV, Makan Siang..." style="width: 100%; font-size: 0.88rem;">
                            </div>
                            <div>
                                <label style="font-size: 0.78rem; color: var(--text-dim); text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 4px;">Perangkat Tujuan (Target):</label>
                                <select id="schedTargetSelect" class="text-input" style="width: 100%; font-size: 0.88rem; padding: 7px 10px;">
                                    <option value="both">📢+🔊 Mac &amp; Google Home</option>
                                    <option value="tv">📺 TV Stick Mi Saja</option>
                                    <option value="all">🌐 Semua (Mac + Speaker + TV)</option>
                                    <option value="cast">📢 Google Home Saja</option>
                                    <option value="say">🔊 Speaker Mac mini Saja</option>
                                </select>
                            </div>
                        </div>

                        <!-- Bagian Konten TTS -->
                        <div id="schedSectionTTS">
                            <div style="margin-bottom: 12px;">
                                <label style="font-size: 0.78rem; color: var(--text-dim); text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 4px;">Isi Pesan Suara Pengumuman (TTS):</label>
                                <textarea id="schedMessageInput" class="text-input" style="width: 100%; height: 60px; resize: none; font-size: 0.9rem;" placeholder="Tulis kalimat yang akan diucapkan saat alarm berbunyi..."></textarea>
                            </div>
                            <div style="margin-bottom: 12px;">
                                <label style="font-size: 0.78rem; color: var(--text-dim); text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 4px;">Pilihan Suara TTS:</label>
                                <select id="schedVoiceSelect" class="text-input" style="width: 100%; font-size: 0.88rem; padding: 7px 10px;">
                                    <option value="wanita1">👩 Suara Wanita 1 (Gadis Neural - Alami)</option>
                                    <option value="pria">👨 Suara Pria (Ardi Neural - Jernih)</option>
                                    <option value="wanita2">👧 Suara Wanita 2 (Google ID)</option>
                                </select>
                            </div>
                        </div>

                        <!-- Bagian Konten File Audio / Video -->
                        <div id="schedSectionAudio" style="display: none; background: rgba(15, 23, 42, 0.7); padding: 12px; border-radius: 10px; border: 1px solid rgba(236, 72, 153, 0.3); margin-bottom: 12px;">
                            <label style="font-size: 0.78rem; color: #f472b6; text-transform: uppercase; font-weight: 700; display: block; margin-bottom: 6px;">PILIH FILE AUDIO / VIDEO DARI PUSTAKA SERVER:</label>
                            <select id="schedAudioSelect" class="text-input" style="width: 100%; font-size: 0.88rem; padding: 7px 10px; margin-bottom: 8px;" onchange="updateSchedAudioPreview()">
                                <option value="">-- Pilih File Audio / Video --</option>
                            </select>
                            <audio id="schedAudioPreview" controls style="width: 100%; height: 34px; display: none;"></audio>
                        </div>

                        <!-- Bagian Konten YouTube / URL Stream untuk TV -->
                        <div id="schedSectionURL" style="display: none; background: rgba(15, 23, 42, 0.7); padding: 12px; border-radius: 10px; border: 1px solid rgba(168, 85, 247, 0.35); margin-bottom: 12px;">
                            <label style="font-size: 0.78rem; color: #c084fc; text-transform: uppercase; font-weight: 700; display: block; margin-bottom: 6px;">LINK YOUTUBE / LIVE STREAM (.M3U8) / MEDIA URL:</label>
                            <input type="url" id="schedUrlInput" class="text-input" placeholder="https://youtu.be/... atau https://.../stream.m3u8" style="width: 100%; font-size: 0.88rem;">
                            <div style="font-size: 0.74rem; color: var(--text-muted); margin-top: 6px;">💡 Otomatis ditayangkan di <b>📺 TV Stick Mi</b> saat waktu jadwal tiba.</div>
                        </div>

                        <!-- Pengaturan Volume Pengumuman (Ala Quick Action) -->
                        <div style="background: rgba(15, 23, 42, 0.5); padding: 12px; border-radius: 10px; border: 1px solid var(--border-subtle); margin-bottom: 12px;">
                            <div style="font-size: 0.78rem; color: #6ee7b7; text-transform: uppercase; font-weight: 700; margin-bottom: 8px;">PENGATURAN VOLUME SUARA PENGUMUMAN:</div>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px;">
                                <div>
                                    <label style="display: flex; justify-content: space-between; font-size: 0.74rem; color: var(--text-dim); margin-bottom: 4px;">
                                        <span>🔊 Volume Saat Alarm Bunyi:</span>
                                        <strong id="schedVolumeLabel" style="color: #6ee7b7;">70%</strong>
                                    </label>
                                    <input type="range" id="schedVolumeInput" min="10" max="100" step="5" value="70" style="width: 100%; accent-color: #10b981; margin-top: 4px;" oninput="document.getElementById('schedVolumeLabel').textContent = this.value + '%'">
                                </div>
                                <div>
                                    <label style="display: block; font-size: 0.74rem; color: var(--text-dim); margin-bottom: 4px;">↩️ Volume Setelah Selesai:</label>
                                    <select id="schedRestoreVolSelect" class="text-input" style="width: 100%; font-size: 0.85rem; padding: 6px 10px;">
                                        <option value="50" selected>↩️ Kembali ke 50%</option>
                                        <option value="-1">🔄 Kembali ke Volume Sebelumnya</option>
                                        <option value="30">↩️ Kembali ke 30%</option>
                                        <option value="40">↩️ Kembali ke 40%</option>
                                        <option value="60">↩️ Kembali ke 60%</option>
                                        <option value="none">⏹️ Biarkan (Jangan Diubah)</option>
                                    </select>
                                </div>
                            </div>
                        </div>

                        <!-- Pengaturan Waktu Sangat Fleksibel -->
                        <div style="background: rgba(15, 23, 42, 0.5); padding: 12px; border-radius: 10px; border: 1px solid var(--border-subtle); margin-bottom: 14px;">
                            <div style="font-size: 0.78rem; color: #c4b5fd; text-transform: uppercase; font-weight: 700; margin-bottom: 8px;">PILIH WAKTU & TANGGAL EKSEKUSI FLEKSIBEL:</div>
                            
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin-bottom: 10px;">
                                <div>
                                    <label style="font-size: 0.72rem; color: var(--text-dim); display: block; margin-bottom: 3px;">📅 Pilih Tanggal & Jam Spesifik (Date Picker):</label>
                                    <input type="datetime-local" id="schedDatetimePicker" class="text-input" style="width: 100%; font-size: 0.85rem;" onchange="syncDatetimeToInput()">
                                </div>
                                <div>
                                    <label style="font-size: 0.72rem; color: var(--text-dim); display: block; margin-bottom: 3px;">⏱️ Atau Ketik Durasi / Jam:</label>
                                    <input type="text" id="schedTimeInput" class="text-input" placeholder="Contoh: 10m, 1h, 07:00, atau daily 12:30" style="width: 100%; font-size: 0.85rem;">
                                </div>
                            </div>

                            <!-- Preset Chips -->
                            <div style="display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 6px;">
                                <span class="btn" style="padding: 2px 8px; font-size: 0.72rem; cursor: pointer;" onclick="setSchedTime('5m')">+5 Mnt</span>
                                <span class="btn" style="padding: 2px 8px; font-size: 0.72rem; cursor: pointer;" onclick="setSchedTime('15m')">+15 Mnt</span>
                                <span class="btn" style="padding: 2px 8px; font-size: 0.72rem; cursor: pointer;" onclick="setSchedTime('30m')">+30 Mnt</span>
                                <span class="btn" style="padding: 2px 8px; font-size: 0.72rem; cursor: pointer;" onclick="setSchedTime('1h')">+1 Jam</span>
                                <span class="btn" style="padding: 2px 8px; font-size: 0.72rem; cursor: pointer;" onclick="setSchedTime('2h')">+2 Jam</span>
                                <span class="btn" style="padding: 2px 8px; font-size: 0.72rem; cursor: pointer;" onclick="setSchedTime('07:00')">Jam 07:00</span>
                                <span class="btn" style="padding: 2px 8px; font-size: 0.72rem; cursor: pointer;" onclick="setSchedTime('12:00')">Jam 12:00</span>
                                <span class="btn" style="padding: 2px 8px; font-size: 0.72rem; cursor: pointer; border-color: #3b82f6; color: #60a5fa;" onclick="setSchedTime('besok 08:00')">Besok 08:00</span>
                                <span class="btn" style="padding: 2px 8px; font-size: 0.72rem; cursor: pointer; border-color: #3b82f6; color: #60a5fa;" onclick="setSchedTime('besok 12:00')">Besok 12:00</span>
                            </div>
                        </div>

                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                            <label style="font-size: 0.85rem; color: #f8fafc; cursor: pointer; display: flex; align-items: center; gap: 8px;">
                                <input type="checkbox" id="schedDailyCheckbox" style="transform: scale(1.2); cursor: pointer;">
                                <span>🔁 Ulangi Otomatis Setiap Hari (Rutinitas Harian)</span>
                            </label>
                            <button class="btn btn-primary" style="background: #8b5cf6; border-color: #8b5cf6;" onclick="createSchedule()">⏰ Simpan Jadwal ke Database</button>
                        </div>
                    </div>

                    <!-- Tabel / Daftar Jadwal Aktif -->
                    <div>
                        <div style="font-size: 0.88rem; font-weight: 700; color: #f8fafc; margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between;">
                            <span>📋 Daftar Jadwal Pengumuman Aktif (SQLite):</span>
                            <span id="schedCountBadge" style="font-size: 0.75rem; background: rgba(139, 92, 246, 0.2); color: #c4b5fd; padding: 2px 8px; border-radius: 10px; border: 1px solid rgba(139, 92, 246, 0.4);">0 Jadwal</span>
                        </div>
                        <div id="schedEmptyState" style="text-align: center; padding: 24px 10px; color: var(--text-muted); font-size: 0.85rem; background: rgba(0,0,0,0.15); border-radius: 8px; border: 1px dashed var(--border-subtle);">
                            Belum ada jadwal suara yang aktif. Buat jadwal pengumuman di atas!
                        </div>
                        <div id="schedListContainer" style="display: flex; flex-direction: column; gap: 8px;"></div>
                    </div>
                </div>

                <!-- DISPLAY & SCREENSHOT -->
                <div class="card-panel">
                    <div class="card-title">🖥️ Layar & Penghemat Daya</div>
                    <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                        <button class="btn btn-primary" onclick="captureScreen()">📸 Ambil Screenshot Layar Mac</button>
                        <button class="btn btn-danger" onclick="sleepDisplay()">🌙 Kunci / Matikan Layar Mac</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- ================= TAB 5: TASK MANAGER (PROSES) ================= -->
        <div class="view-section" id="tab-procs">
            <div class="procs-wrap">
                <div class="card-panel">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 10px;">
                        <div class="card-title" style="margin-bottom: 0;">📊 Task Manager Mac (Proses Berjalan)</div>
                        <div style="display: flex; gap: 8px; flex-wrap: wrap; flex: 1; justify-content: flex-end; min-width: 200px;">
                            <input type="text" id="procSearchInput" class="text-input" style="padding: 6px 12px; flex: 1; min-width: 140px; max-width: 240px;" placeholder="Cari proses..." oninput="filterProcs()">
                            <button class="btn btn-primary" onclick="loadProcesses()">🔄 Refresh</button>
                        </div>
                    </div>
                    <div class="table-scroll">
                        <table class="list-table" id="procsTable">
                            <thead>
                                <tr>
                                    <th>PID</th>
                                    <th>Nama Aplikasi / Proses</th>
                                    <th>CPU %</th>
                                    <th>RAM %</th>
                                    <th style="text-align: right;">Aksi</th>
                                </tr>
                            </thead>
                            <tbody id="procsBody">
                                <tr><td colspan="5" style="text-align:center; padding: 30px; color: var(--text-muted);">Memuat proses...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- ================= TAB 6: MEDIA PLAYER REMOTE ================= -->
        <div class="view-section" id="tab-media">
            <div class="media-wrap">
                <div class="card-panel" style="text-align: center;">
                    <div style="font-size: 3rem; margin-bottom: 10px;">🎵</div>
                    <div class="card-title" style="justify-content: center;">Kendali Media & Musik Mac</div>
                    <div style="background: rgba(9, 13, 22, 0.6); padding: 16px; border-radius: 10px; border: 1px solid var(--border-color); margin: 16px 0;">
                        <div style="font-size: 0.78rem; color: var(--text-muted); text-transform: uppercase;">Sedang Diputar (Apple Music / Spotify)</div>
                        <div id="nowPlayingText" style="font-size: 1.1rem; font-weight: bold; margin-top: 6px; color: var(--accent);">Memuat info lagu...</div>
                    </div>
                    <div style="display: flex; justify-content: center; flex-wrap: wrap; gap: 10px; margin-top: 16px;">
                        <button class="btn" style="padding: 10px 16px; font-size: 0.95rem;" onclick="mediaAction('prev')">⏮️ Prev</button>
                        <button class="btn btn-primary" style="padding: 10px 20px; font-size: 1rem;" onclick="mediaAction('playpause')">⏯️ Play / Pause</button>
                        <button class="btn" style="padding: 10px 16px; font-size: 0.95rem;" onclick="mediaAction('next')">⏭️ Next</button>
                    </div>
                    <div style="margin-top: 18px;">
                        <button class="btn" onclick="mediaAction('info')">🔄 Perbarui Info Lagu</button>
                    </div>
                </div>
            </div>
        </div>

    </div>

    <!-- MODAL VNC GUIDE -->
    <div class="modal-bg" id="vncGuideModal">
        <div class="modal-box" style="max-width: 550px;">
            <div class="modal-head">
                <div style="font-weight: 600; color: var(--accent);">📖 Panduan Remote Desktop (VNC)</div>
                <button class="btn" onclick="closeModal('vncGuideModal')">&times;</button>
            </div>
            <div class="modal-body" style="font-size: 0.9rem; line-height: 1.6; color: var(--text-main);">
                <p><strong>1. Dari MacBook / Mac Lain:</strong><br>
                Cukup klik tombol <em>"Buka Screen Sharing"</em> di dashboard, atau buka <strong>Finder &rarr; Cmd + K</strong> lalu masukkan: <code style="color:var(--accent);">vnc://10.66.66.4</code></p>
                <hr style="border: 0; border-top: 1px solid var(--border-color); margin: 12px 0;">
                <p><strong>2. Dari Laptop Windows:</strong><br>
                Unduh <strong>RealVNC Viewer</strong> atau <strong>TigerVNC</strong>, lalu buat koneksi baru ke alamat: <code style="color:var(--accent);">10.66.66.4:5900</code></p>
                <hr style="border: 0; border-top: 1px solid var(--border-color); margin: 12px 0;">
                <p><strong>3. Dari HP / Tablet (iPhone / Android):</strong><br>
                Gunakan aplikasi <strong>Jump Desktop</strong>, <strong>Screens</strong>, atau <strong>VNC Viewer</strong>. Tambahkan host baru ke: <code style="color:var(--accent);">10.66.66.4</code></p>
                <div style="margin-top: 14px; background: rgba(56, 189, 248, 0.1); padding: 10px; border-radius: 6px; font-size: 0.8rem; color: var(--accent);">
                    Pastikan perangkat remote Anda sudah terhubung ke WireGuard sebelum menghubungkan VNC.
                </div>
            </div>
            <div class="modal-foot">
                <button class="btn btn-primary" onclick="closeModal('vncGuideModal')">Mengerti</button>
            </div>
        </div>
    </div>

    <!-- MODAL SCREENSHOT -->
    <div class="modal-bg" id="screenModal" onclick="closeModal('screenModal')">
        <div class="modal-box" style="max-width: 90vw; background: transparent; border: none; box-shadow: none; text-align: center;">
            <img id="screenImg" src="" style="max-width: 100%; border-radius: 8px; box-shadow: 0 10px 30px rgba(0,0,0,0.8);">
            <div style="margin-top: 10px;"><button class="btn btn-primary" onclick="closeModal('screenModal')">Tutup</button></div>
        </div>
    </div>

    <!-- MODAL CODE EDITOR -->
    <div class="modal-bg" id="editorModal">
        <div class="modal-box" style="max-width: 900px; height: 80vh; display: flex; flex-direction: column;">
            <div class="modal-head">
                <div id="editorTitle" style="font-weight: 600; color: var(--accent);">Edit File</div>
                <button class="btn" onclick="closeModal('editorModal')">&times;</button>
            </div>
            <div class="modal-body" style="flex: 1; padding: 12px;">
                <textarea id="editorContent" style="width: 100%; height: 100%; background: #04070e; color: #38bdf8; font-family: monospace; font-size: 0.9rem; padding: 12px; border: 1px solid var(--border-color); border-radius: 6px; resize: none; outline: none;"></textarea>
            </div>
            <div class="modal-foot">
                <span id="editorStatus" style="margin-right: auto; color: var(--text-muted); font-size: 0.8rem;"></span>
                <button class="btn" onclick="closeModal('editorModal')">Batal</button>
                <button class="btn btn-primary" onclick="saveEditorContent()">Simpan Perubahan</button>
            </div>
        </div>
    </div>

    <!-- MODAL NEW FOLDER -->
    <div class="modal-bg" id="newFolderModal">
        <div class="modal-box" style="max-width: 450px;">
            <div class="modal-head">
                <div style="font-weight: 600; color: var(--accent);">📁 Folder Baru</div>
                <button class="btn" onclick="closeModal('newFolderModal')">&times;</button>
            </div>
            <div class="modal-body">
                <input type="text" id="newFolderName" class="text-input" placeholder="Nama folder...">
            </div>
            <div class="modal-foot">
                <button class="btn" onclick="closeModal('newFolderModal')">Batal</button>
                <button class="btn btn-primary" onclick="submitNewFolder()">Buat Folder</button>
            </div>
        </div>
    </div>

    <!-- MODAL NEW FILE -->
    <div class="modal-bg" id="newFileModal">
        <div class="modal-box" style="max-width: 450px;">
            <div class="modal-head">
                <div style="font-weight: 600; color: #10b981;">📄 Berkas Baru</div>
                <button class="btn" onclick="closeModal('newFileModal')">&times;</button>
            </div>
            <div class="modal-body">
                <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 8px;">Masukkan nama file lengkap dengan ekstensinya (misal: <code>catatan.txt</code>, <code>script.py</code>, <code>index.html</code>):</p>
                <input type="text" id="newFileNameInput" class="text-input" placeholder="nama_file.txt">
            </div>
            <div class="modal-foot">
                <button class="btn" onclick="closeModal('newFileModal')">Batal</button>
                <button class="btn btn-primary" style="background: #10b981; border-color: #10b981;" onclick="submitNewFile()">Buat File</button>
            </div>
        </div>
    </div>

    <!-- MODAL RENAME -->
    <div class="modal-bg" id="renameModal">
        <div class="modal-box" style="max-width: 450px;">
            <div class="modal-head">
                <div style="font-weight: 600; color: #f59e0b;">✏️ Ganti Nama</div>
                <button class="btn" onclick="closeModal('renameModal')">&times;</button>
            </div>
            <div class="modal-body">
                <input type="text" id="renameItemInput" class="text-input" placeholder="Nama baru...">
            </div>
            <div class="modal-foot">
                <button class="btn" onclick="closeModal('renameModal')">Batal</button>
                <button class="btn btn-primary" onclick="submitRename()">Simpan Nama</button>
            </div>
        </div>
    </div>

    <!-- FLOATING CONTEXT MENU -->
    <div id="contextMenu"></div>

    <!-- TOAST -->
    <div id="toast">Notifikasi</div>

    <script>
        let currentPath = "";
        let currentFiles = [];
        let isGridView = true;
        let activeEditPath = "";

        const ICONS = { folder: "📁", image: "🖼️", code: "📄", pdf: "📑", archive: "📦", video: "🎬", audio: "🎵", file: "📄" };

        const TAB_PARAM_TO_ID = {
            fm: "tab-fm",
            files: "tab-fm",
            berkas: "tab-fm",
            screen: "tab-screen",
            apps: "tab-apps",
            term: "tab-term",
            terminal: "tab-term",
            ctrl: "tab-ctrl",
            remote: "tab-ctrl",
            cast: "tab-ctrl",
            audio: "tab-ctrl",
            schedule: "tab-ctrl",
            procs: "tab-procs",
            task: "tab-procs",
            media: "tab-media"
        };
        const TAB_ID_TO_PARAM = {
            "tab-fm": "fm",
            "tab-screen": "screen",
            "tab-apps": "apps",
            "tab-term": "term",
            "tab-ctrl": "ctrl",
            "tab-procs": "procs",
            "tab-media": "media"
        };

        function resolveTabFromUrl() {
            const params = new URLSearchParams(window.location.search);
            const raw = (params.get("tab") || window.location.hash.replace(/^#/, "") || "").trim().toLowerCase();
            if (!raw) return "tab-fm";
            if (TAB_PARAM_TO_ID[raw]) return TAB_PARAM_TO_ID[raw];
            if (TAB_ID_TO_PARAM[raw]) return raw;
            return "tab-fm";
        }

        window.addEventListener("DOMContentLoaded", () => {
            const initialTab = resolveTabFromUrl();
            loadStats();
            scanCastDevices();
            switchTab(initialTab, false);
            try {
                const url = new URL(window.location.href);
                const slug = TAB_ID_TO_PARAM[initialTab] || "fm";
                if (url.searchParams.get("tab") !== slug) {
                    url.searchParams.set("tab", slug);
                    window.history.replaceState({ tab: initialTab }, "", url.pathname + url.search);
                }
            } catch (e) {}
        });

        window.addEventListener("popstate", () => {
            switchTab(resolveTabFromUrl(), false);
        });

        function switchTab(tabId, updateUrl = true) {
            const resolved = TAB_ID_TO_PARAM[tabId] ? tabId : (TAB_PARAM_TO_ID[String(tabId).toLowerCase()] || "tab-fm");
            document.querySelectorAll(".view-section").forEach(el => el.classList.remove("active"));
            document.querySelectorAll(".tab-btn").forEach(el => el.classList.remove("active"));
            const targetEl = document.getElementById(resolved);
            if (targetEl) targetEl.classList.add("active");
            const btn = document.getElementById("btn-" + resolved);
            if (btn) {
                btn.classList.add("active");
                try { btn.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" }); } catch (e) {}
            }
            if (updateUrl) {
                try {
                    const url = new URL(window.location.href);
                    const slug = TAB_ID_TO_PARAM[resolved] || "fm";
                    if (url.searchParams.get("tab") !== slug) {
                        url.searchParams.set("tab", slug);
                        window.history.pushState({ tab: resolved }, "", url.pathname + url.search);
                    }
                } catch (e) {}
            }
            if (resolved === "tab-fm") refreshFolder();
            if (resolved === "tab-procs") loadProcesses();
            if (resolved === "tab-media") mediaAction("info");
            if (resolved === "tab-screen") captureLiveScreenFrame();
            if (resolved !== "tab-screen" && isScreenStreaming) stopScreenStream();
        }

        // ================= FILE MANAGER =================
        async function loadFolder(relPath) {
            currentPath = relPath;
            renderBreadcrumbs();
            const container = document.getElementById("fileContainer");
            container.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 40px;">Memuat file...</div>';

            try {
                const res = await fetch(`/api/list?path=${encodeURIComponent(relPath)}`);
                const data = await res.json();
                if (data.status === "success") {
                    currentFiles = data.items;
                    renderFiles(currentFiles);
                } else {
                    showToast(data.message || "Gagal membuka folder", true);
                }
            } catch (err) {
                showToast("Terjadi kesalahan jaringan", true);
            }
        }

        function refreshFolder() { loadFolder(currentPath); loadStats(); }

        function renderBreadcrumbs() {
            const area = document.getElementById("breadcrumbArea");
            let html = '<span class="crumb" onclick="loadFolder(\'\')">Home</span>';
            if (currentPath) {
                const parts = currentPath.split("/").filter(Boolean);
                let accum = "";
                parts.forEach((part, idx) => {
                    accum += (accum ? "/" : "") + part;
                    const pathTarget = accum;
                    const isLast = idx === parts.length - 1;
                    html += `<span class="crumb-sep">/</span>`;
                    html += isLast ? `<span class="crumb current">${escapeHtml(part)}</span>` : `<span class="crumb" onclick="loadFolder('${escapeAttr(pathTarget)}')">${escapeHtml(part)}</span>`;
                });
            }
            area.innerHTML = html;
        }

        function renderFiles(items) {
            const container = document.getElementById("fileContainer");
            if (!items || items.length === 0) {
                container.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 50px;">Folder ini kosong.</div>';
                return;
            }
            items.sort((a, b) => (a.is_dir === b.is_dir ? a.name.localeCompare(b.name) : a.is_dir ? -1 : 1));

            if (isGridView) {
                let html = '<div class="grid-view">';
                items.forEach(item => {
                    const icon = ICONS[item.type] || "📄";
                    const targetPath = currentPath ? `${currentPath}/${item.name}` : item.name;
                    html += `
                        <div class="file-card-grid" onclick="handleItemClick('${escapeAttr(targetPath)}', ${item.is_dir}, '${item.type}')" oncontextmenu="showContextMenu(event, '${escapeAttr(targetPath)}', ${item.is_dir}, '${item.type}', '${escapeAttr(item.name)}')">
                            <div class="file-card-icon">${icon}</div>
                            <div class="file-card-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
                        </div>`;
                });
                html += '</div>';
                container.innerHTML = html;
            } else {
                let html = '<table class="list-table"><thead><tr><th>Nama</th><th>Ukuran</th><th>Diubah</th><th style="text-align:right;">Aksi</th></tr></thead><tbody>';
                items.forEach(item => {
                    const icon = ICONS[item.type] || "📄";
                    const targetPath = currentPath ? `${currentPath}/${item.name}` : item.name;
                    html += `
                        <tr onclick="handleItemClick('${escapeAttr(targetPath)}', ${item.is_dir}, '${item.type}')" oncontextmenu="showContextMenu(event, '${escapeAttr(targetPath)}', ${item.is_dir}, '${item.type}', '${escapeAttr(item.name)}')">
                            <td><span style="font-size:1.1rem; margin-right:6px;">${icon}</span> <strong>${escapeHtml(item.name)}</strong></td>
                            <td>${item.size}</td>
                            <td>${item.modified}</td>
                            <td style="text-align:right;" onclick="event.stopPropagation()">
                                <div style="display:inline-flex; gap:4px; justify-content:flex-end;">
                                    <button class="btn" title="Ganti Nama" onclick="openRenameModal('${escapeAttr(targetPath)}', '${escapeAttr(item.name)}')">✏️</button>
                                    ${(item.type === 'video' || item.type === 'image' || item.type === 'audio') ? `<button class="btn btn-primary" title="Cast ke TV / Speaker" onclick="castFileToDevice('${escapeAttr(targetPath)}', '${item.type}')">📺</button>` : ''}
                                    ${item.is_dir ? `<button class="btn" title="Kompres ke ZIP" onclick="zipItem('${escapeAttr(targetPath)}')">📦</button>` : ''}
                                    ${item.ext === 'zip' ? `<button class="btn" title="Ekstrak ZIP" onclick="unzipItem('${escapeAttr(targetPath)}')">📂</button>` : ''}
                                    ${!item.is_dir ? `<button class="btn" title="Download" onclick="downloadFile('${escapeAttr(targetPath)}')">⬇</button>` : ''}
                                    ${item.type === 'code' ? `<button class="btn" title="Edit Teks" onclick="openEditor('${escapeAttr(targetPath)}')">📝</button>` : ''}
                                    <button class="btn btn-danger" title="Hapus" onclick="deleteItem('${escapeAttr(targetPath)}')">🗑</button>
                                </div>
                            </td>
                        </tr>`;
                });
                html += '</tbody></table>';
                container.innerHTML = html;
            }
        }

        function toggleView() {
            isGridView = !isGridView;
            document.getElementById("viewToggleBtn").innerHTML = isGridView ? "<span>☰</span> List" : "<span>⊞</span> Grid";
            renderFiles(currentFiles);
        }

        function handleItemClick(path, isDir, type) {
            if (isDir) {
                loadFolder(path);
            } else if (type === "code") {
                openEditor(path);
            } else if (type === "image" || type === "audio" || type === "video" || type === "pdf") {
                openLightboxPreview(path, path.split('/').pop(), type);
            } else {
                downloadFile(path);
            }
        }

        function downloadFile(relPath) {
            window.location.href = `/api/download?path=${encodeURIComponent(relPath)}`;
        }

        async function castFileToDevice(relPath, fileType) {
            const sel = document.getElementById("castDeviceSelect");
            // Video/Gambar otomatis ke Android TV (server memilih TV Stick Mi), audio pakai dropdown jika ada
            const targetDev = (fileType === "audio" && sel && sel.value) ? sel.value : "";
            showToast(`📺 Mengirim ${relPath.split("/").pop()} ke ${fileType === "audio" ? "Cast" : "Android TV"}...`);
            try {
                const res = await fetch("/api/cast/media_file", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ path: relPath, device: targetDev })
                });
                const data = await res.json();
                showToast(data.message || (data.status === "success" ? "Berhasil cast!" : "Gagal cast"), data.status !== "success");
                updatePlaybackStatus();
            } catch (e) {
                showToast("Gagal menghubungi server untuk cast media.", true);
            }
        }

        async function deleteItem(relPath) {
            if (!confirm(`Hapus ${relPath.split("/").pop()}?`)) return;
            const res = await fetch("/api/delete", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: relPath })
            });
            const data = await res.json();
            if (data.status === "success") { showToast("File dihapus"); refreshFolder(); }
            else showToast(data.message, true);
        }

        function triggerUpload() { document.getElementById("filePicker").click(); }
        async function handleFileSelect(files) {
            if (!files.length) return;
            showToast(`Mengunggah ${files.length} file...`);
            const fd = new FormData();
            for (let f of files) fd.append("files", f);
            const res = await fetch(`/api/upload?path=${encodeURIComponent(currentPath)}`, { method: "POST", body: fd });
            const data = await res.json();
            if (data.status === "success") { showToast("Upload selesai!"); refreshFolder(); }
            else showToast(data.message, true);
        }

        function openNewFolderModal() {
            document.getElementById("newFolderName").value = "";
            openModal("newFolderModal");
        }
        async function submitNewFolder() {
            const name = document.getElementById("newFolderName").value.trim();
            if (!name) return;
            const res = await fetch("/api/mkdir", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: currentPath, name })
            });
            const data = await res.json();
            if (data.status === "success") { closeModal("newFolderModal"); refreshFolder(); }
            else showToast(data.message, true);
        }

        function openNewFileModal() {
            document.getElementById("newFileNameInput").value = "catatan.txt";
            openModal("newFileModal");
            setTimeout(() => document.getElementById("newFileNameInput").select(), 100);
        }

        async function submitNewFile() {
            const name = document.getElementById("newFileNameInput").value.trim();
            if (!name) return;
            const res = await fetch("/api/newfile", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: currentPath, name })
            });
            const data = await res.json();
            if (data.status === "success") {
                closeModal("newFileModal");
                showToast(`File '${name}' berhasil dibuat!`);
                refreshFolder();
                const newFilePath = currentPath ? `${currentPath}/${name}` : name;
                openEditor(newFilePath);
            } else {
                showToast(data.message, true);
            }
        }

        let activeRenamePath = "";
        function openRenameModal(relPath, oldName) {
            activeRenamePath = relPath;
            document.getElementById("renameItemInput").value = oldName;
            openModal("renameModal");
            setTimeout(() => document.getElementById("renameItemInput").select(), 100);
        }

        async function submitRename() {
            const newName = document.getElementById("renameItemInput").value.trim();
            if (!newName) return;
            const res = await fetch("/api/rename", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: activeRenamePath, new_name: newName })
            });
            const data = await res.json();
            if (data.status === "success") {
                closeModal("renameModal");
                showToast("Nama berhasil diubah!");
                refreshFolder();
            } else {
                showToast(data.message, true);
            }
        }

        // ================= CONTEXT MENU (KLIK KANAN) =================
        let activeCtxItem = null;

        function showContextMenu(e, path, isDir, type, name) {
            e.preventDefault();
            e.stopPropagation();
            activeCtxItem = { path, isDir, type, name };
            const menu = document.getElementById("contextMenu");

            let html = `
                <div style="padding: 6px 12px; font-weight: 600; font-size: 0.78rem; color: #94a3b8; border-bottom: 1px solid rgba(255,255,255,0.08); margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    ${isDir ? '📁' : '📄'} ${escapeHtml(name)}
                </div>
            `;

            if (isDir) {
                html += `
                    <div class="ctx-item" onclick="ctxAction('open')"><span>📂</span> Buka Folder</div>
                    <div class="ctx-item" onclick="ctxAction('rename')"><span>✏️</span> Ganti Nama</div>
                    <div class="ctx-item" onclick="ctxAction('zip')"><span>📦</span> Kompres ke ZIP</div>
                    <div class="ctx-item" onclick="ctxAction('copy_path')"><span>📋</span> Salin Path</div>
                    <div class="ctx-divider"></div>
                    <div class="ctx-item danger" onclick="ctxAction('delete')"><span>🗑️</span> Hapus Folder</div>
                `;
            } else {
                html += `
                    <div class="ctx-item" onclick="ctxAction('open')"><span>👁️</span> Buka / Lihat</div>
                `;
                if (type === 'video' || type === 'image' || type === 'audio') {
                    html += `<div class="ctx-item" onclick="ctxAction('cast_media')"><span>📺</span> Cast ke ${type === 'audio' ? 'Speaker / TV' : 'Android TV'}</div>`;
                }
                if (type === 'code' || name.match(/\.(txt|md|py|js|json|sh|html|css|yaml|yml|log|conf|ini)$/i)) {
                    html += `<div class="ctx-item" onclick="ctxAction('edit')"><span>📝</span> Edit Teks</div>`;
                }
                html += `
                    <div class="ctx-item" onclick="ctxAction('rename')"><span>✏️</span> Ganti Nama</div>
                    <div class="ctx-item" onclick="ctxAction('download')"><span>⬇️</span> Unduh File</div>
                    <div class="ctx-item" onclick="ctxAction('zip')"><span>📦</span> Kompres ke ZIP</div>
                `;
                if (name.toLowerCase().endsWith('.zip')) {
                    html += `<div class="ctx-item" onclick="ctxAction('unzip')"><span>📂</span> Ekstrak ZIP</div>`;
                }
                html += `
                    <div class="ctx-item" onclick="ctxAction('copy_path')"><span>📋</span> Salin Path</div>
                    <div class="ctx-divider"></div>
                    <div class="ctx-item danger" onclick="ctxAction('delete')"><span>🗑️</span> Hapus File</div>
                `;
            }

            menu.innerHTML = html;
            positionContextMenu(e, menu);
        }

        function showEmptyContextMenu(e) {
            if (e.target.closest('.file-card-grid') || e.target.closest('tr')) return;
            e.preventDefault();
            activeCtxItem = null;
            const menu = document.getElementById("contextMenu");
            menu.innerHTML = `
                <div class="ctx-item" onclick="ctxAction('new_folder')"><span>📁</span> Folder Baru</div>
                <div class="ctx-item" onclick="ctxAction('new_file')"><span>📄</span> Berkas Teks Baru</div>
                <div class="ctx-item" onclick="ctxAction('upload')"><span>📤</span> Unggah Berkas</div>
                <div class="ctx-divider"></div>
                <div class="ctx-item" onclick="ctxAction('refresh')"><span>🔄</span> Segarkan</div>
            `;
            positionContextMenu(e, menu);
        }

        function positionContextMenu(e, menu) {
            menu.style.display = "block";
            let x = e.clientX;
            let y = e.clientY;
            const w = menu.offsetWidth;
            const h = menu.offsetHeight;
            if (x + w > window.innerWidth) x = window.innerWidth - w - 10;
            if (y + h > window.innerHeight) y = window.innerHeight - h - 10;
            menu.style.left = `${Math.max(10, x)}px`;
            menu.style.top = `${Math.max(10, y)}px`;
        }

        function hideContextMenu() {
            const menu = document.getElementById("contextMenu");
            if (menu) menu.style.display = "none";
        }

        document.addEventListener("click", hideContextMenu);
        document.addEventListener("contextmenu", (e) => {
            if (!e.target.closest("#fileContainer")) hideContextMenu();
        });

        async function ctxAction(action) {
            hideContextMenu();
            const it = activeCtxItem;
            if (action === "new_folder") { openNewFolderModal(); return; }
            if (action === "new_file") { openNewFileModal(); return; }
            if (action === "upload") { triggerUpload(); return; }
            if (action === "refresh") { refreshFolder(); return; }
            if (!it) return;

            if (action === "open") handleItemClick(it.path, it.isDir, it.type);
            else if (action === "cast_media") castFileToDevice(it.path, it.type);
            else if (action === "edit") openEditor(it.path);
            else if (action === "download") downloadFile(it.path);
            else if (action === "rename") openRenameModal(it.path, it.name);
            else if (action === "zip") zipItem(it.path);
            else if (action === "unzip") unzipItem(it.path);
            else if (action === "delete") deleteItem(it.path);
            else if (action === "copy_path") {
                navigator.clipboard.writeText(it.path);
                showToast(`Path disalin: ${it.path}`);
            }
        }

        async function openEditor(relPath) {
            activeEditPath = relPath;
            document.getElementById("editorTitle").innerText = `Edit: ${relPath.split("/").pop()}`;
            openModal("editorModal");
            const res = await fetch(`/api/read?path=${encodeURIComponent(relPath)}`);
            const data = await res.json();
            if (data.status === "success") document.getElementById("editorContent").value = data.content;
            else { showToast("Gagal membaca file", true); closeModal("editorModal"); }
        }

        async function saveEditorContent() {
            const content = document.getElementById("editorContent").value;
            const res = await fetch("/api/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: activeEditPath, content })
            });
            const data = await res.json();
            if (data.status === "success") { showToast("File tersimpan!"); closeModal("editorModal"); refreshFolder(); }
            else showToast(data.message, true);
        }

        // ================= 2. REMOTE APP LAUNCHER =================
        async function launchApp(appName) {
            showToast(`Membuka ${appName} di Mac...`);
            const res = await fetch("/api/apps/launch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ app: appName })
            });
            const data = await res.json();
            showToast(data.message);
        }

        function launchCustomApp() {
            const name = document.getElementById("customAppInput").value.trim();
            if (name) launchApp(name);
        }

        async function openRemoteUrl() {
            const url = document.getElementById("remoteUrlInput").value.trim();
            if (!url) return;
            showToast("Membuka link di Mac...");
            const res = await fetch("/api/apps/open_url", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url })
            });
            const data = await res.json();
            showToast(data.message);
        }

        // ================= 3. WEB TERMINAL =================
        async function executeTerminalCommand(cmdOverride = "") {
            const input = document.getElementById("termInput");
            const cmd = cmdOverride || input.value.trim();
            if (!cmd) return;
            if (!cmdOverride) input.value = "";

            const term = document.getElementById("terminalOutput");
            term.textContent += `\n$ ${cmd}\nMenjalankan...`;
            term.scrollTop = term.scrollHeight;

            try {
                const res = await fetch("/api/terminal/exec", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ command: cmd })
                });
                const data = await res.json();
                term.textContent = term.textContent.replace("Menjalankan...", data.output || "(tidak ada output)");
                term.scrollTop = term.scrollHeight;
            } catch (err) {
                term.textContent += "\n[Error] Gagal menjalankan perintah.";
            }
        }

        function runQuickCmd(cmd) { executeTerminalCommand(cmd); }
        function clearTerminal() { document.getElementById("terminalOutput").textContent = "Terminal dibersihkan.\n"; }

        // ================= 4. REMOTE HARDWARE CONTROL =================
        async function setMacVolume(val) {
            document.getElementById("volumeValue").innerText = val + "%";
            await fetch("/api/remote/volume", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ volume: parseInt(val) })
            });
        }

        function muteMacVolume() {
            document.getElementById("volumeSlider").value = 0;
            setMacVolume(0);
        }

        function setQuickSpeak(txt) {
            document.getElementById("speakInput").value = txt;
        }

        let currentAudio = null;
        async function speakOnMac(mode = "speaker") {
            const text = document.getElementById("speakInput").value.trim();
            const voice = document.getElementById("speakVoice").value;
            if (!text) {
                showToast("Tulis pesan terlebih dahulu", true);
                return;
            }

            if (mode === "preview") {
                showToast("Menghasilkan audio pratinjau...");
                try {
                    const res = await fetch("/api/remote/say", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ text, voice, mode: "preview" })
                    });
                    const data = await res.json();
                    if (data.status === "success" && data.audio_url) {
                        if (currentAudio) currentAudio.pause();
                        currentAudio = new Audio(data.audio_url);
                        currentAudio.play();
                        showToast("Memutar di browser...");
                    } else {
                        showToast(data.message || "Gagal membuat audio", true);
                    }
                } catch (e) {
                    showToast("Gagal memutar audio di browser", true);
                }
            } else {
                showToast("Mengirim suara ke speaker Mac...");
                try {
                    const res = await fetch("/api/remote/say", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ text, voice, mode: "speaker" })
                    });
                    const data = await res.json();
                    showToast(data.message);
                } catch (e) {
                    showToast("Gagal mengirim ke speaker Mac", true);
                }
            }
        }

        async function getClipboard() {
            const res = await fetch("/api/remote/clipboard");
            const data = await res.json();
            if (data.status === "success") {
                document.getElementById("clipboardInput").value = data.clipboard;
                showToast("Clipboard Mac berhasil disalin!");
            }
        }

        async function setClipboard() {
            const text = document.getElementById("clipboardInput").value;
            const res = await fetch("/api/remote/clipboard", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text })
            });
            showToast("Teks dikirim ke clipboard Mac!");
        }

        async function captureScreen() {
            showToast("Mengambil screenshot Mac...");
            const img = document.getElementById("screenImg");
            img.src = `/api/remote/screenshot?t=${Date.now()}`;
            openModal("screenModal");
        }

        async function sleepDisplay() {
            if (!confirm("Matikan / Kunci layar Mac sekarang?")) return;
            await fetch("/api/remote/sleep_display", { method: "POST" });
            showToast("Layar Mac dimatikan.");
        }

        // Stats
        async function loadStats() {
            const res = await fetch("/api/stats");
            const data = await res.json();
            if (data.status === "success") {
                const s = data.stats;
                document.getElementById("diskPercent").innerText = s.disk_percent;
                document.getElementById("diskBar").style.width = s.disk_percent;
                document.getElementById("diskDetail").innerText = `${s.disk_free} sisa dari ${s.disk_total}`;
            }
        }

        // ================= 5. ZIP & UNZIP =================
        async function zipCurrentFolder() {
            showToast("Mengompres folder aktif ke ZIP...");
            try {
                const res = await fetch("/api/zip", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ path: currentPath })
                });
                const data = await res.json();
                if (data.status === "success") {
                    showToast("Folder berhasil dikompres ke ZIP!");
                    refreshFolder();
                } else {
                    showToast(data.message || "Gagal membuat zip", true);
                }
            } catch (e) {
                showToast("Gagal membuat zip", true);
            }
        }

        async function zipItem(targetPath) {
            showToast("Mengompres ke ZIP...");
            try {
                const res = await fetch("/api/zip", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ path: targetPath })
                });
                const data = await res.json();
                if (data.status === "success") {
                    showToast("Berhasil dikompres ke ZIP!");
                    refreshFolder();
                } else {
                    showToast(data.message || "Gagal zip", true);
                }
            } catch (e) {
                showToast("Gagal zip", true);
            }
        }

        async function unzipItem(targetPath) {
            showToast("Mengekstrak file ZIP...");
            try {
                const res = await fetch("/api/unzip", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ path: targetPath })
                });
                const data = await res.json();
                if (data.status === "success") {
                    showToast("Ekstrak ZIP selesai!");
                    refreshFolder();
                } else {
                    showToast(data.message || "Gagal ekstrak zip", true);
                }
            } catch (e) {
                showToast("Gagal ekstrak zip", true);
            }
        }

        // ================= 6. DESKTOP NOTIFICATION & INTERACTIVE CHAT =================
        async function sendDesktopNotification() {
            const title = document.getElementById("notifyTitle").value.trim() || "Pesan dari HP";
            const message = document.getElementById("notifyBody").value.trim();
            if (!message) {
                showToast("Tulis isi pesan terlebih dahulu", true);
                return;
            }
            showToast("Mengirim banner notifikasi ke layar Mac...");
            try {
                const res = await fetch("/api/remote/notify", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ title, message })
                });
                const data = await res.json();
                showToast(data.message);
                document.getElementById("notifyBody").value = "";
            } catch (e) {
                showToast("Gagal mengirim notifikasi", true);
            }
        }

        async function sendInteractivePopup() {
            const btn = document.getElementById("btnInteractiveChat");
            const sender = document.getElementById("notifyTitle").value.trim() || "Remote HP";
            const message = document.getElementById("notifyBody").value.trim();
            const replyBox = document.getElementById("interactiveReplyBox");
            const replyText = document.getElementById("interactiveReplyText");
            const replyTime = document.getElementById("interactiveReplyTime");

            if (!message) {
                showToast("Tulis isi pesan terlebih dahulu", true);
                return;
            }

            if (btn) {
                btn.disabled = true;
                btn.innerText = "⏳ Menunggu Balasan dari Mac...";
            }
            showToast("Pop-up interaktif muncul di layar Mac. Menunggu balasan...");

            try {
                const res = await fetch("/api/remote/ask", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ sender, message, timeout: 120 })
                });
                const data = await res.json();
                if (data.status === "success") {
                    if (data.result === "REPLY" && data.reply) {
                        replyBox.style.display = "block";
                        replyText.innerText = `"${data.reply}"`;
                        replyTime.innerText = `Dibalas pada: ${new Date().toLocaleTimeString()}`;
                        showToast("💬 Balasan diterima dari pengguna Mac!");
                        document.getElementById("notifyBody").value = "";
                    } else if (data.result === "CLOSED") {
                        showToast("Pop-up di Mac ditutup tanpa teks balasan.");
                    } else if (data.result === "TIMEOUT") {
                        showToast("Waktu tunggu habis (tidak ada balasan dari Mac).", true);
                    }
                } else {
                    showToast(data.message || "Gagal menampilkan pop-up", true);
                }
            } catch (e) {
                showToast("Gagal menghubungi server", true);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = "💬 Kirim Pop-Up & Tunggu Balasan";
                }
            }
        }

        // ================= 7. TASK MANAGER / PROSES =================
        let activeProcesses = [];
        async function loadProcesses() {
            const body = document.getElementById("procsBody");
            body.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 30px; color: var(--text-muted);">Memuat proses Mac...</td></tr>';
            try {
                const res = await fetch("/api/procs/list");
                const data = await res.json();
                if (data.status === "success") {
                    activeProcesses = data.processes;
                    renderProcesses(activeProcesses);
                } else {
                    body.innerHTML = `<tr><td colspan="5" style="text-align:center; color: var(--danger);">${data.message}</td></tr>`;
                }
            } catch (e) {
                body.innerHTML = '<tr><td colspan="5" style="text-align:center; color: var(--danger);">Gagal memuat proses.</td></tr>';
            }
        }

        function renderProcesses(list) {
            const body = document.getElementById("procsBody");
            if (!list || list.length === 0) {
                body.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 20px; color: var(--text-muted);">Tidak ada proses yang sesuai.</td></tr>';
                return;
            }
            let html = "";
            list.forEach(p => {
                html += `
                    <tr>
                        <td><code>${escapeHtml(p.pid)}</code></td>
                        <td><strong>${escapeHtml(p.name)}</strong></td>
                        <td><span style="color:var(--accent); font-weight:600;">${escapeHtml(p.cpu)}</span></td>
                        <td>${escapeHtml(p.mem)}</td>
                        <td style="text-align: right;">
                            <button class="btn btn-danger" style="padding: 4px 8px; font-size: 0.75rem;" onclick="killProcess('${escapeAttr(p.pid)}', '${escapeAttr(p.name)}')">Hentikan</button>
                        </td>
                    </tr>
                `;
            });
            body.innerHTML = html;
        }

        function filterProcs() {
            const q = document.getElementById("procSearchInput").value.toLowerCase();
            const filtered = activeProcesses.filter(p => p.name.toLowerCase().includes(q) || p.pid.includes(q));
            renderProcesses(filtered);
        }

        async function killProcess(pid, name) {
            if (!confirm(`Hentikan proses ${name} (PID: ${pid})?`)) return;
            try {
                const res = await fetch("/api/procs/kill", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ pid })
                });
                const data = await res.json();
                showToast(data.message);
                loadProcesses();
            } catch (e) {
                showToast("Gagal menghentikan proses", true);
            }
        }

        // ================= 8. MEDIA PLAYER CONTROLLER =================
        async function mediaAction(act) {
            try {
                const res = await fetch("/api/remote/music", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ action: act })
                });
                const data = await res.json();
                if (data.status === "success") {
                    document.getElementById("nowPlayingText").innerText = data.output || "OK";
                    if (act !== "info") showToast("Media diperbarui: " + (data.output || "OK"));
                } else {
                    document.getElementById("nowPlayingText").innerText = data.message || "Gagal";
                }
            } catch (e) {
                document.getElementById("nowPlayingText").innerText = "Gagal menghubungi Mac";
            }
        }

        // ================= 9. GOOGLE HOME / CAST =================
        async function scanCastDevices(forceRefresh = false) {
            const sel = document.getElementById("castDeviceSelect");
            if (!sel) return;
            if (forceRefresh) {
                sel.innerHTML = '<option value="">🔍 Sedang mencari speaker...</option>';
            }
            try {
                const res = await fetch(forceRefresh ? "/api/cast/devices?refresh=true" : "/api/cast/devices");
                const data = await res.json();
                if (data.status === "success" && data.devices && data.devices.length > 0) {
                    sel.innerHTML = '';
                    data.devices.forEach((d, idx) => {
                        const opt = document.createElement("option");
                        opt.value = d.host || d.name;
                        const isTv = (d.cast_type === "cast") || /tv/i.test(d.name || "") || /tv/i.test(d.model || "");
                        opt.text = `${isTv ? "📺" : "📢"} ${d.name} (${d.model} - ${d.host})`;
                        if (idx === 0) opt.selected = true;
                        sel.appendChild(opt);
                    });
                    showToast(`Ditemukan ${data.devices.length} perangkat Google Cast!`);
                    setTimeout(updatePlaybackStatus, 400);
                } else if (data.status === "not_installed") {
                    sel.innerHTML = '<option value="">Pychromecast belum terpasang</option>';
                    showToast("Pustaka pychromecast belum aktif", true);
                } else {
                    sel.innerHTML = '<option value="192.168.0.198">📢 Family Room speaker (192.168.0.198)</option>';
                    showToast("Belum ada respon baru, menggunakan cache speaker lokal.");
                }
            } catch (e) {
                sel.innerHTML = '<option value="192.168.0.198">📢 Family Room speaker (192.168.0.198)</option>';
                showToast("Gagal memindai Wi-Fi, menggunakan fallback speaker lokal.");
            }
        }

        let currentCastVoice = 'wanita1';

        async function broadcastToGoogleHome() {
            const btn = document.getElementById("btnBroadcastCast");
            const text = document.getElementById("castMessageInput").value.trim();
            const device = document.getElementById("castDeviceSelect").value;
            if (!text) {
                showToast("Tulis pesan pengumuman terlebih dahulu", true);
                return;
            }
            if (btn) {
                btn.disabled = true;
                btn.innerText = "⏳ Sedang Menyiarkan...";
            }
            const voiceLabel = currentCastVoice === 'pria' ? 'Pria (Ardi)' : (currentCastVoice === 'wanita2' ? 'Wanita 2 (Google ID)' : 'Wanita 1 (Gadis)');
            showToast("Mengirim pengumuman dengan suara " + voiceLabel + " ke Google Home...");
            try {
                const res = await fetch("/api/cast/broadcast", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ text, device, voice_type: currentCastVoice })
                });
                const data = await res.json();
                if (data.status === "success") {
                    showToast(data.message);
                    document.getElementById("castMessageInput").value = "";
                } else {
                    showToast(data.message, true);
                }
            } catch (e) {
                showToast("Gagal menyiarkan ke Google Home", true);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = "📢 Siarkan ke Google Home";
                }
            }
        }

        function openModal(id) { document.getElementById(id).style.display = "flex"; }
        function closeModal(id) { document.getElementById(id).style.display = "none"; }
        function showToast(text, isError = false) {
            const toast = document.getElementById("toast");
            toast.innerText = text;
            toast.style.borderColor = isError ? "var(--danger)" : "var(--accent)";
            toast.classList.add("show");
            setTimeout(() => toast.classList.remove("show"), 3000);
        }
        function escapeHtml(str) { return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
        function escapeAttr(str) { return String(str).replace(/'/g, "\\'").replace(/"/g, '&quot;'); }

        // ================= ADVANCED FEATURE 1: LIVE SCREEN MIRRORING & REMOTE TOUCH =================
        let screenStreamInterval = null;
        let isScreenStreaming = false;

        function toggleScreenStream() {
            if (isScreenStreaming) {
                stopScreenStream();
            } else {
                startScreenStream();
            }
        }

        function startScreenStream() {
            isScreenStreaming = true;
            const btn = document.getElementById("btnToggleStream");
            const badge = document.getElementById("streamFpsBadge");
            if (btn) {
                btn.innerText = "⏸️ Hentikan Stream";
                btn.classList.add("btn-danger");
                btn.classList.remove("btn-primary");
            }
            if (badge) badge.style.display = "inline-flex";
            captureLiveScreenFrame();
            screenStreamInterval = setInterval(captureLiveScreenFrame, 1000);
            showToast("Live Stream Mac Aktif (1 FPS)");
        }

        function stopScreenStream() {
            isScreenStreaming = false;
            if (screenStreamInterval) {
                clearInterval(screenStreamInterval);
                screenStreamInterval = null;
            }
            const btn = document.getElementById("btnToggleStream");
            const badge = document.getElementById("streamFpsBadge");
            if (btn) {
                btn.innerText = "▶️ Mulai Live Stream";
                btn.classList.remove("btn-danger");
                btn.classList.add("btn-primary");
            }
            if (badge) badge.style.display = "none";
        }

        function captureLiveScreenFrame() {
            const img = document.getElementById("liveScreenImg");
            if (!img) return;
            const url = "/api/remote/screen_live?t=" + Date.now();
            const temp = new Image();
            temp.onload = function() {
                img.src = url;
            };
            temp.src = url;
        }

        function handleScreenImageClick(e) {
            const img = document.getElementById("liveScreenImg");
            if (!img) return;
            const rect = img.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const clickY = e.clientY - rect.top;
            const px = clickX / rect.width;
            const py = clickY / rect.height;

            showClickRipple(clickX, clickY);

            fetch("/api/remote/screen_click", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({px: px, py: py, type: "click"})
            }).then(r => r.json()).then(data => {
                showToast("Klik Mac dikirim (" + data.x + ", " + data.y + ")");
                setTimeout(captureLiveScreenFrame, 350);
            }).catch(err => {
                showToast("Gagal mengirim klik: " + err);
            });
        }

        function showClickRipple(x, y) {
            const ripple = document.getElementById("clickRipple");
            if (!ripple) return;
            ripple.style.left = (x - 16) + "px";
            ripple.style.top = (y - 16) + "px";
            ripple.classList.remove("animate");
            void ripple.offsetWidth;
            ripple.classList.add("animate");
        }

        function sendRemoteKey(key) {
            fetch("/api/remote/screen_key", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({key: key})
            }).then(r => r.json()).then(data => {
                showToast("Tombol " + key + " dikirim ke Mac");
                setTimeout(captureLiveScreenFrame, 300);
            });
        }

        function sendRemoteScreenText() {
            const inp = document.getElementById("screenTextInput");
            if (!inp || !inp.value.trim()) return;
            const txt = inp.value.trim();
            fetch("/api/remote/screen_key", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({text: txt})
            }).then(r => r.json()).then(data => {
                showToast("Teks berhasil diketik ke Mac");
                inp.value = "";
                setTimeout(captureLiveScreenFrame, 350);
            });
        }

        // ================= ADVANCED FEATURE 2: VOICE COMMAND (JARVIS BAHASA INDONESIA) =================
        let voiceRecognition = null;

        function openVoiceModal() {
            openModal("voiceModal");
            startListeningVoice();
        }

        function startListeningVoice() {
            const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
            const transcriptEl = document.getElementById("voiceTranscript");
            const statusEl = document.getElementById("voiceStatusText");
            const replyEl = document.getElementById("voiceReplyText");
            const waveEl = document.getElementById("voiceWaves");

            if (!SpeechRec) {
                statusEl.innerText = "⚠️ Browser tidak mendukung Web Speech API.";
                transcriptEl.innerText = "Gunakan Google Chrome atau Safari.";
                return;
            }

            if (voiceRecognition) {
                try { voiceRecognition.abort(); } catch(e) {}
            }

            voiceRecognition = new SpeechRec();
            voiceRecognition.lang = "id-ID";
            voiceRecognition.interimResults = false;
            voiceRecognition.maxAlternatives = 1;

            transcriptEl.innerText = "Mendengarkan... Silakan bicara...";
            statusEl.innerText = "🔴 Mendengarkan suara Anda...";
            replyEl.style.display = "none";
            if (waveEl) waveEl.classList.add("active");

            voiceRecognition.onresult = function(event) {
                const transcript = event.results[0][0].transcript;
                transcriptEl.innerText = '“' + transcript + '”';
                statusEl.innerText = "⚡ Memproses perintah...";
                if (waveEl) waveEl.classList.remove("active");

                fetch("/api/voice_command", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({text: transcript})
                }).then(r => r.json()).then(data => {
                    const reply = data.result ? data.result.reply : "Perintah berhasil dijalankan.";
                    replyEl.innerText = reply;
                    replyEl.style.display = "block";
                    statusEl.innerText = "✅ Perintah Berhasil";
                    showToast(reply);
                    speakTextLocally(reply);
                }).catch(err => {
                    statusEl.innerText = "❌ Gagal memproses perintah";
                    showToast("Error: " + err);
                });
            };

            voiceRecognition.onerror = function(event) {
                statusEl.innerText = "⚠️ Suara tidak terdeteksi (" + event.error + ")";
                if (waveEl) waveEl.classList.remove("active");
            };

            voiceRecognition.onend = function() {
                if (waveEl) waveEl.classList.remove("active");
            };

            voiceRecognition.start();
        }

        function speakTextLocally(text) {
            if ('speechSynthesis' in window) {
                const utter = new SpeechSynthesisUtterance(text);
                utter.lang = "id-ID";
                utter.rate = 1.0;
                window.speechSynthesis.speak(utter);
            }
        }

        // ================= ADVANCED FEATURE 3: UNIVERSAL CLIPBOARD SYNC =================
        function refreshMacClipboard() {
            fetch("/api/remote/clipboard")
                .then(r => r.json())
                .then(d => {
                    if (d.status === "success") {
                        const el = document.getElementById("macClipboardDisplay");
                        if (el) el.value = d.clipboard || "(Clipboard kosong)";
                    }
                }).catch(() => {});
        }

        function copyMacClipboardToLocal() {
            const el = document.getElementById("macClipboardDisplay");
            if (!el || !el.value) return;
            navigator.clipboard.writeText(el.value).then(() => {
                showToast("Tersalin ke Clipboard HP / Browser Anda!");
            }).catch(err => {
                showToast("Gagal menyalin: " + err);
            });
        }

        function sendTextToMacClipboard() {
            const el = document.getElementById("newClipboardText");
            if (!el || !el.value.trim()) return;
            const txt = el.value.trim();
            fetch("/api/remote/clipboard", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({text: txt})
            }).then(r => r.json()).then(d => {
                showToast("Berhasil dikirim ke Clipboard Mac!");
                el.value = "";
                refreshMacClipboard();
            });
        }

        // ================= ADVANCED FEATURE 4: SMART AUTOMATION SCENARIOS =================
        function runScenario(preset) {
            showToast("Mengaktifkan mode " + preset + "...");
            fetch("/api/scenario", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({preset: preset})
            }).then(r => r.json()).then(d => {
                showToast(d.message || "Mode berhasil diaktifkan!");
            });
        }

        // ================= ADVANCED FEATURE 5: MEDIA LIGHTBOX & DRAG-AND-DROP =================
        function openLightboxPreview(path, name, type) {
            const modal = document.getElementById("lightboxModal");
            const title = document.getElementById("lightboxTitle");
            const container = document.getElementById("lightboxContainer");
            const btnDown = document.getElementById("lightboxDownloadBtn");
            if (!modal || !container) return;

            title.innerText = name;
            btnDown.onclick = function() { downloadFile(path); };

            container.innerHTML = "";
            const previewUrl = "/api/preview?path=" + encodeURIComponent(path);

            if (type === "image") {
                container.innerHTML = '<img src="' + previewUrl + '" style="max-width:100%; max-height:75vh; border-radius:10px; object-fit:contain; box-shadow:0 10px 30px rgba(0,0,0,0.5);" alt="' + escapeHtml(name) + '" />';
            } else if (type === "audio") {
                container.innerHTML = '<div style="padding:30px; text-align:center; width:100%;"><div style="font-size:3.5rem; margin-bottom:14px;">🎵</div><audio controls autoplay style="width:100%; max-width:500px;"><source src="' + previewUrl + '">Browser Anda tidak mendukung audio.</audio></div>';
            } else if (type === "video") {
                container.innerHTML = '<video controls autoplay style="max-width:100%; max-height:75vh; border-radius:10px; box-shadow:0 10px 30px rgba(0,0,0,0.5);"><source src="' + previewUrl + '">Browser Anda tidak mendukung video.</video>';
            } else if (type === "pdf") {
                container.innerHTML = '<iframe src="' + previewUrl + '" style="width:100%; height:75vh; border:none; border-radius:10px;"></iframe>';
            }

            openModal("lightboxModal");
        }

        function initDragAndDrop() {
            const dropArea = document.querySelector(".file-scroll-area");
            if (!dropArea) return;

            ['dragenter', 'dragover'].forEach(eventName => {
                dropArea.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    dropArea.classList.add("dragover");
                }, false);
            });

            ['dragleave', 'drop'].forEach(eventName => {
                dropArea.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    dropArea.classList.remove("dragover");
                }, false);
            });

            dropArea.addEventListener('drop', (e) => {
                const dt = e.dataTransfer;
                const files = dt.files;
                if (files.length > 0) {
                    showToast("Mengunggah " + files.length + " berkas...");
                    handleFileSelect(files);
                }
            }, false);
        }

        // Initialize drag & drop and clipboard on page load
        window.addEventListener("DOMContentLoaded", () => {
            initDragAndDrop();
            refreshMacClipboard();
        });


        // ================= AUDIO HELPER UTILITIES (16-BIT PCM WAV & BASE64) =================
        function audioBufferToWav(buffer) {
            const numOfChan = buffer.numberOfChannels;
            const length = buffer.length * numOfChan * 2 + 44;
            const out = new DataView(new ArrayBuffer(length));
            const channels = [];
            let offset = 0;
            let pos = 0;

            function setUint16(data) { out.setUint16(pos, data, true); pos += 2; }
            function setUint32(data) { out.setUint32(pos, data, true); pos += 4; }

            setUint32(0x46464952); // "RIFF"
            setUint32(length - 8);  // file length - 8
            setUint32(0x45564157); // "WAVE"
            setUint32(0x20746d66); // "fmt " chunk
            setUint32(16);          // length = 16
            setUint16(1);           // PCM (uncompressed)
            setUint16(numOfChan);
            setUint32(buffer.sampleRate);
            setUint32(buffer.sampleRate * 2 * numOfChan); // avg. bytes/sec
            setUint16(numOfChan * 2); // block-align
            setUint16(16);          // 16-bit
            setUint32(0x61746164); // "data" chunk
            setUint32(length - pos - 4);

            for (let i = 0; i < numOfChan; i++) channels.push(buffer.getChannelData(i));

            while (offset < buffer.length) {
                for (let i = 0; i < numOfChan; i++) {
                    let sample = Math.max(-1, Math.min(1, channels[i][offset]));
                    sample = (0.5 + sample < 0 ? sample * 32768 : sample * 32767) | 0;
                    out.setInt16(pos, sample, true);
                    pos += 2;
                }
                offset++;
            }
            return new Blob([out.buffer], { type: 'audio/wav' });
        }

        async function blobToWavBlob(blob) {
            try {
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                if (!AudioCtx) return blob;
                const audioCtx = new AudioCtx();
                const arrayBuffer = await blob.arrayBuffer();
                const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
                const wavBlob = audioBufferToWav(audioBuffer);
                audioCtx.close();
                return wavBlob;
            } catch (err) {
                console.warn('WAV conversion fallback to original blob:', err);
                return blob;
            }
        }

        function blobToDataUrl(blob) {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => resolve(reader.result);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            });
        }

        function dataUrlToBlob(dataurl) {
            const arr = dataurl.split(',');
            const mime = arr[0].match(/:(.*?);/)[1];
            const bstr = atob(arr[1]);
            let n = bstr.length;
            const u8arr = new Uint8Array(n);
            while (n--) {
                u8arr[n] = bstr.charCodeAt(n);
            }
            return new Blob([u8arr], { type: mime });
        }

        // ================= GOOGLE HOME 4 PILIHAN SUARA & MULTI-VOICE STUDIO =================
        function setCastVoiceOption(opt) {
            currentCastVoice = opt;
            ['btnModeWanita1', 'btnModePria', 'btnModeWanita2', 'btnModeVoice'].forEach(id => {
                const btn = document.getElementById(id);
                if (btn) btn.classList.remove('btn-primary');
            });

            if (opt === 'wanita1') document.getElementById('btnModeWanita1')?.classList.add('btn-primary');
            if (opt === 'pria') document.getElementById('btnModePria')?.classList.add('btn-primary');
            if (opt === 'wanita2') document.getElementById('btnModeWanita2')?.classList.add('btn-primary');
            if (opt === 'voice') document.getElementById('btnModeVoice')?.classList.add('btn-primary');

            const textSec = document.getElementById('castTextSection');
            const recSec = document.getElementById('castRecordSection');
            const input = document.getElementById('castMessageInput');

            if (opt === 'voice') {
                textSec.style.display = 'none';
                recSec.style.display = 'block';
                renderSavedVoiceClips();
            } else {
                textSec.style.display = 'block';
                recSec.style.display = 'none';
                if (input) {
                    if (opt === 'wanita1') input.placeholder = "Tulis pengumuman dengan Suara Wanita 1 (Gadis - Natural Studio)...";
                    else if (opt === 'pria') input.placeholder = "Tulis pengumuman dengan Suara Pria (Ardi - Jelas & Wibawa)...";
                    else if (opt === 'wanita2') input.placeholder = "Tulis pengumuman dengan Suara Wanita 2 (Google ID)...";
                }
            }
        }

        let voiceMediaRecorder = null;
        let voiceAudioChunks = [];
        let recordedVoiceBlob = null;
        let voiceRecordInterval = null;
        let voiceRecordSeconds = 0;

        async function toggleVoiceRecording() {
            const btn = document.getElementById('btnRecordVoice');
            const timer = document.getElementById('recordTimer');
            const preview = document.getElementById('recordedAudioPreview');
            const btnSend = document.getElementById('btnSendRecordedVoice');
            const btnRerecord = document.getElementById('btnRerecordVoice');
            const btnSave = document.getElementById('btnSaveVoiceClip');

            if (voiceMediaRecorder && voiceMediaRecorder.state === 'recording') {
                voiceMediaRecorder.stop();
                clearInterval(voiceRecordInterval);
                btn.innerText = '🔴 Rekam Baru';
                btn.classList.remove('btn-danger');
                return;
            }

            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                voiceAudioChunks = [];
                voiceMediaRecorder = new MediaRecorder(stream);
                voiceMediaRecorder.ondataavailable = e => { if (e.data.size > 0) voiceAudioChunks.push(e.data); };
                voiceMediaRecorder.onstop = async () => {
                    const rawBlob = new Blob(voiceAudioChunks, { type: voiceMediaRecorder.mimeType || 'audio/webm' });
                    recordedVoiceBlob = await blobToWavBlob(rawBlob);
                    const audioUrl = URL.createObjectURL(recordedVoiceBlob);
                    preview.src = audioUrl;
                    preview.style.display = 'block';
                    btnSend.style.display = 'inline-flex';
                    btnRerecord.style.display = 'inline-flex';
                    btnSave.style.display = 'inline-flex';
                    stream.getTracks().forEach(t => t.stop());
                };

                voiceMediaRecorder.start();
                voiceRecordSeconds = 0;
                timer.innerText = '⏱️ 00:00';
                voiceRecordInterval = setInterval(() => {
                    voiceRecordSeconds++;
                    const m = String(Math.floor(voiceRecordSeconds / 60)).padStart(2, '0');
                    const s = String(voiceRecordSeconds % 60).padStart(2, '0');
                    timer.innerText = `⏱️ ${m}:${s}`;
                }, 1000);

                btn.innerText = '⏹️ Berhenti Rekam';
                btn.classList.add('btn-danger');
                preview.style.display = 'none';
                btnSend.style.display = 'none';
                btnRerecord.style.display = 'none';
                btnSave.style.display = 'none';
            } catch (err) {
                showToast('Gagal mengakses mikrofon: ' + err, true);
            }
        }

        function rerecordCastVoice() {
            if (voiceMediaRecorder && voiceMediaRecorder.state === 'recording') {
                voiceMediaRecorder.stop();
                clearInterval(voiceRecordInterval);
            }
            recordedVoiceBlob = null;
            document.getElementById('recordedAudioPreview').style.display = 'none';
            document.getElementById('btnSendRecordedVoice').style.display = 'none';
            document.getElementById('btnRerecordVoice').style.display = 'none';
            document.getElementById('btnSaveVoiceClip').style.display = 'none';
            document.getElementById('saveClipForm').style.display = 'none';
            document.getElementById('recordTimer').innerText = '⏱️ 00:00';
            toggleVoiceRecording();
        }

        async function sendRecordedVoiceToGoogleHome() {
            if (!recordedVoiceBlob) {
                showToast('Rekam suara terlebih dahulu!', true);
                return;
            }
            const dev = document.getElementById('castDeviceSelect').value;
            const btn = document.getElementById('btnSendRecordedVoice');
            btn.disabled = true;
            btn.innerText = '⏳ Menyiarkan ke Speaker...';

            const formData = new FormData();
            formData.append('audio', recordedVoiceBlob, 'voice_record.wav');
            formData.append('device', dev);

            try {
                const resp = await fetch('/api/cast/voice_note', {
                    method: 'POST',
                    body: formData
                });
                const d = await resp.json();
                if (d.status === 'success') {
                    showToast(d.message || 'Suara asli Anda berhasil berbunyi di Google Home!');
                } else {
                    showToast('Gagal broadcast: ' + d.message, true);
                }
            } catch (err) {
                showToast('Error koneksi: ' + err, true);
            } finally {
                btn.disabled = false;
                btn.innerText = '📢 Siarkan ke Google Home';
            }
        }

        function showSaveClipForm() {
            if (!recordedVoiceBlob) {
                showToast('Rekam suara terlebih dahulu!', true);
                return;
            }
            const form = document.getElementById('saveClipForm');
            form.style.display = 'block';
            const input = document.getElementById('clipLabelInput');
            input.value = '';
            input.focus();
        }

        async function confirmSaveVoiceClip() {
            if (!recordedVoiceBlob) {
                showToast('Tidak ada rekaman suara untuk disimpan', true);
                return;
            }
            const input = document.getElementById('clipLabelInput');
            let label = input.value.trim();
            const clips = JSON.parse(localStorage.getItem('mac_remote_saved_voice_clips') || '[]');
            if (!label) {
                label = 'Rekaman Suara #' + (clips.length + 1);
            }

            const dataUrl = await blobToDataUrl(recordedVoiceBlob);
            const now = new Date();
            const timeStr = now.toLocaleDateString('id-ID', { day: '2-digit', month: 'short' }) + ' ' +
                            now.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });

            const newClip = {
                id: 'clip_' + Date.now(),
                label: label,
                time: timeStr,
                duration: voiceRecordSeconds ? `${voiceRecordSeconds}s` : 'Audio',
                dataUrl: dataUrl
            };

            clips.unshift(newClip);
            localStorage.setItem('mac_remote_saved_voice_clips', JSON.stringify(clips));
            document.getElementById('saveClipForm').style.display = 'none';
            renderSavedVoiceClips();
            showToast('✅ Berhasil disimpan ke Daftar Multi Suara!');
        }

        function renderSavedVoiceClips() {
            const container = document.getElementById('savedVoiceClipsList');
            if (!container) return;
            const clips = JSON.parse(localStorage.getItem('mac_remote_saved_voice_clips') || '[]');

            if (clips.length === 0) {
                container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.8rem; font-style: italic;">Belum ada rekaman tersimpan. Rekam suara Anda di atas dan klik \'Simpan ke Daftar Suara\'.</div>';
                return;
            }

            let html = '';
            clips.forEach((clip, index) => {
                html += `
                    <div style="background: rgba(15, 23, 42, 0.7); border: 1px solid var(--border-subtle); border-radius: 10px; padding: 10px 14px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                        <div style="display: flex; flex-direction: column; gap: 2px; min-width: 140px;">
                            <span style="font-weight: 600; font-size: 0.88rem; color: #f8fafc;">🎙️ ${escapeHtml(clip.label)}</span>
                            <span style="font-size: 0.72rem; color: var(--text-dim);">⏱️ ${clip.duration} • 📅 ${clip.time}</span>
                        </div>
                        <audio controls src="${clip.dataUrl}" style="height: 32px; max-width: 220px;"></audio>
                        <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                            <button type="button" class="btn btn-primary" style="padding: 5px 10px; font-size: 0.78rem; background: #3b82f6; border-color: #3b82f6;" onclick="broadcastSavedClip(${index})" title="Siarkan rekaman ini ke Google Home">📢 Ke Google Home</button>
                            <button type="button" class="btn" style="padding: 5px 10px; font-size: 0.78rem; border-color: #10b981; color: #34d399;" onclick="playSavedClipOnMac(${index})" title="Putar di speaker Mac mini">🔊 Ke Speaker Mac</button>
                            <button type="button" class="btn btn-danger" style="padding: 5px 8px; font-size: 0.78rem;" onclick="deleteSavedClip(${index})" title="Hapus klip suara">🗑️</button>
                        </div>
                    </div>
                `;
            });
            container.innerHTML = html;
        }

        async function broadcastSavedClip(index) {
            const clips = JSON.parse(localStorage.getItem('mac_remote_saved_voice_clips') || '[]');
            const clip = clips[index];
            if (!clip) return;
            const dev = document.getElementById('castDeviceSelect').value;
            showToast(`Menyiarkan "${clip.label}" ke Google Home...`);

            const blob = dataUrlToBlob(clip.dataUrl);
            const formData = new FormData();
            formData.append('audio', blob, 'saved_clip.wav');
            formData.append('device', dev);

            try {
                const resp = await fetch('/api/cast/voice_note', { method: 'POST', body: formData });
                const d = await resp.json();
                if (d.status === 'success') {
                    showToast(d.message || `"${clip.label}" berhasil disiarkan ke Google Home!`);
                } else {
                    showToast('Gagal broadcast: ' + d.message, true);
                }
            } catch (err) {
                showToast('Error koneksi: ' + err, true);
            }
        }

        async function playSavedClipOnMac(index) {
            const clips = JSON.parse(localStorage.getItem('mac_remote_saved_voice_clips') || '[]');
            const clip = clips[index];
            if (!clip) return;
            showToast(`Memutar "${clip.label}" di speaker Mac mini...`);

            const blob = dataUrlToBlob(clip.dataUrl);
            const formData = new FormData();
            formData.append('audio', blob, 'saved_clip.wav');

            try {
                const resp = await fetch('/api/remote/play_voice', { method: 'POST', body: formData });
                const d = await resp.json();
                if (d.status === 'success') {
                    showToast(`🔊 "${clip.label}" sedang berbunyi di speaker Mac!`);
                } else {
                    showToast('Gagal memutar di Mac: ' + d.message, true);
                }
            } catch (err) {
                showToast('Error koneksi: ' + err, true);
            }
        }

        function deleteSavedClip(index) {
            const clips = JSON.parse(localStorage.getItem('mac_remote_saved_voice_clips') || '[]');
            if (index >= 0 && index < clips.length) {
                clips.splice(index, 1);
                localStorage.setItem('mac_remote_saved_voice_clips', JSON.stringify(clips));
                renderSavedVoiceClips();
                showToast('Klip rekaman dihapus');
            }
        }

        // ================= INTERCOM DIRECT MIC VOICE MESSAGE TO MAC SPEAKER =================
        let macVoiceMediaRecorder = null;
        let macVoiceAudioChunks = [];
        let macVoiceBlob = null;
        let macVoiceInterval = null;
        let macVoiceSeconds = 0;

        async function toggleMacVoiceRecording() {
            const btn = document.getElementById('btnRecordMacVoice');
            const timer = document.getElementById('macVoiceTimer');
            const preview = document.getElementById('macVoicePreview');
            const btnSend = document.getElementById('btnSendMacVoice');
            const btnRerecord = document.getElementById('btnRerecordMacVoice');

            if (macVoiceMediaRecorder && macVoiceMediaRecorder.state === 'recording') {
                macVoiceMediaRecorder.stop();
                clearInterval(macVoiceInterval);
                btn.innerText = '🔴 Rekam Baru';
                btn.classList.remove('btn-danger');
                return;
            }

            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                macVoiceAudioChunks = [];
                macVoiceMediaRecorder = new MediaRecorder(stream);
                macVoiceMediaRecorder.ondataavailable = e => { if (e.data.size > 0) macVoiceAudioChunks.push(e.data); };
                macVoiceMediaRecorder.onstop = async () => {
                    const rawBlob = new Blob(macVoiceAudioChunks, { type: macVoiceMediaRecorder.mimeType || 'audio/webm' });
                    macVoiceBlob = await blobToWavBlob(rawBlob);
                    const audioUrl = URL.createObjectURL(macVoiceBlob);
                    preview.src = audioUrl;
                    preview.style.display = 'block';
                    btnSend.style.display = 'inline-flex';
                    btnRerecord.style.display = 'inline-flex';
                    stream.getTracks().forEach(t => t.stop());
                };

                macVoiceMediaRecorder.start();
                macVoiceSeconds = 0;
                timer.innerText = '⏱️ 00:00';
                macVoiceInterval = setInterval(() => {
                    macVoiceSeconds++;
                    const m = String(Math.floor(macVoiceSeconds / 60)).padStart(2, '0');
                    const s = String(macVoiceSeconds % 60).padStart(2, '0');
                    timer.innerText = `⏱️ ${m}:${s}`;
                }, 1000);

                btn.innerText = '⏹️ Selesai Rekam';
                btn.classList.add('btn-danger');
                preview.style.display = 'none';
                btnSend.style.display = 'none';
                btnRerecord.style.display = 'none';
            } catch (err) {
                showToast('Gagal mengakses mikrofon: ' + err, true);
            }
        }

        function rerecordMacVoice() {
            if (macVoiceMediaRecorder && macVoiceMediaRecorder.state === 'recording') {
                macVoiceMediaRecorder.stop();
                clearInterval(macVoiceInterval);
            }
            macVoiceBlob = null;
            document.getElementById('macVoicePreview').style.display = 'none';
            document.getElementById('btnSendMacVoice').style.display = 'none';
            document.getElementById('btnRerecordMacVoice').style.display = 'none';
            document.getElementById('macVoiceTimer').innerText = '⏱️ 00:00';
            toggleMacVoiceRecording();
        }

        async function sendVoiceMessageToMac() {
            if (!macVoiceBlob) {
                showToast('Rekam suara terlebih dahulu!', true);
                return;
            }
            const btn = document.getElementById('btnSendMacVoice');
            btn.disabled = true;
            btn.innerText = '⏳ Mengirim ke Mac...';

            const formData = new FormData();
            formData.append('audio', macVoiceBlob, 'mac_voice.wav');

            try {
                const resp = await fetch('/api/remote/play_voice', {
                    method: 'POST',
                    body: formData
                });
                const d = await resp.json();
                if (d.status === 'success') {
                    showToast('🔊 Pesan suara Anda berhasil berbunyi di speaker Mac mini!');
                } else {
                    showToast('Gagal kirim suara ke Mac: ' + d.message, true);
                }
            } catch (err) {
                showToast('Error koneksi: ' + err, true);
            } finally {
                btn.disabled = false;
                btn.innerText = '🔊 Putar di Speaker Mac & Pop-Up';
            }
        }

        // Jalankan render daftar rekaman, audio library & jadwal saat awal buka
        window.addEventListener('DOMContentLoaded', () => {
            renderSavedVoiceClips();
            loadAudioLibrary();
            loadSchedules();
            setInterval(loadSchedules, 8000);
            updatePlaybackStatus();
            setInterval(updatePlaybackStatus, 2000);
        });

        // ================= LIVE AUDIO MONITOR & STOP CONTROLLER =================
        async function updatePlaybackStatus() {
            try {
                const resp = await fetch('/api/playback/status');
                const data = await resp.json();
                if (data.status === 'success') {
                    const mac = data.mac || {};
                    const cast = data.cast || {};

                    // 1. Update Mac status
                    const badgeMac = document.getElementById('badgeMacLive');
                    const infoMac = document.getElementById('infoMacLive');
                    const btnStopMac = document.getElementById('btnStopMacLive');

                    if (badgeMac && infoMac && btnStopMac) {
                        if (mac.is_playing) {
                            badgeMac.style.background = 'rgba(16, 185, 129, 0.25)';
                            badgeMac.style.color = '#34d399';
                            badgeMac.innerHTML = '🟢 PLAYING';
                            const elp = mac.elapsed_seconds ? Math.floor(mac.elapsed_seconds) : 0;
                            const elpMin = Math.floor(elp / 60);
                            const elpSec = String(elp % 60).padStart(2, '0');
                            infoMac.innerHTML = `🎵 <b>${escapeHtml(mac.title || 'Audio')}</b> &nbsp;<span style="color:#38bdf8;">(⏱️ ${elpMin}:${elpSec})</span>`;
                            btnStopMac.style.display = 'block';
                            btnStopMac.style.opacity = '1';
                            btnStopMac.style.boxShadow = '0 0 10px rgba(239, 68, 68, 0.5)';
                            btnStopMac.innerHTML = '🛑 Stop Suara Mac (Aktif)';
                        } else {
                            badgeMac.style.background = 'rgba(148, 163, 184, 0.2)';
                            badgeMac.style.color = '#94a3b8';
                            badgeMac.innerHTML = 'IDLE';
                            infoMac.innerHTML = 'Tidak ada audio yang sedang berputar di Mac.';
                            btnStopMac.style.display = 'block';
                            btnStopMac.style.opacity = '0.65';
                            btnStopMac.style.boxShadow = 'none';
                            btnStopMac.innerHTML = '🛑 Force Stop Mac';
                        }
                    }

                    // 2. Update Multi-Device Cast cards (Google Home + Android TV)
                    const castsList = (Array.isArray(data.casts) && data.casts.length) ? data.casts : [cast];
                    const monitorHost = document.getElementById('castDevicesMonitor');
                    if (monitorHost) {
                        const activeIds = new Set(castsList.map(d => 'cast_' + (d.host || 'default').replace(/[^a-zA-Z0-9]/g, '_')));
                        Array.from(monitorHost.children).forEach(ch => {
                            if (ch.id && !activeIds.has(ch.id)) ch.remove();
                        });
                        castsList.forEach(dev => {
                            const host = dev.host || 'default';
                            const safeId = 'cast_' + host.replace(/[^a-zA-Z0-9]/g, '_');
                            const isTv = (dev.cast_type === 'cast') || /tv/i.test(dev.device || '') || /tv/i.test(dev.model || '');
                            const devIcon = isTv ? '📺' : '📢';
                            const devColor = isTv ? '#c084fc' : '#60a5fa';
                            let card = document.getElementById(safeId);
                            if (!card) {
                                card = document.createElement('div');
                                card.id = safeId;
                                card.style.cssText = 'background: rgba(15, 23, 42, 0.65); padding: 12px 14px; border-radius: 10px; border: 1px solid rgba(255, 255, 255, 0.08); display: flex; flex-direction: column; justify-content: space-between; gap: 10px;';
                                card.innerHTML = `
                                    <div>
                                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; gap: 6px;">
                                            <span style="font-size: 0.88rem; font-weight: 700; color: ${devColor};">${devIcon} ${escapeHtml(dev.device || host)}</span>
                                            <span id="${safeId}_badge" style="padding: 2px 8px; border-radius: 999px; font-size: 0.72rem; font-weight: 700; background: rgba(148, 163, 184, 0.2); color: #94a3b8;">IDLE</span>
                                        </div>
                                        <div id="${safeId}_info" style="font-size: 0.82rem; color: var(--text-muted); min-height: 22px; word-break: break-all;"></div>
                                    </div>
                                    <div style="background: rgba(8, 12, 22, 0.55); padding: 8px 10px; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.06);">
                                        <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px;">
                                            <span style="font-size: 0.78rem; color: var(--text-secondary); font-weight: 600;">🔊 Volume (${escapeHtml(host)})</span>
                                            <strong id="${safeId}_volVal" style="font-size: 0.82rem; color: ${devColor};">--%</strong>
                                        </div>
                                        <div class="slider-box" style="margin-top: 0; gap: 8px;">
                                            <input type="range" id="${safeId}_slider" min="0" max="100" value="50"
                                                oninput="onGhVolInput('${escapeAttr(safeId)}', this.value)"
                                                onchange="setGoogleHomeVolume({volume: Number(this.value), device: '${escapeAttr(host)}'})">
                                        </div>
                                        <div style="display: flex; gap: 6px; margin-top: 8px;">
                                            <button type="button" class="btn" style="flex: 1; padding: 5px 8px; font-size: 0.76rem; min-height: 32px;" onclick="setGoogleHomeVolume({delta: -10, device: '${escapeAttr(host)}'})">🔉 -10%</button>
                                            <button type="button" id="${safeId}_mute" class="btn" style="flex: 1; padding: 5px 8px; font-size: 0.76rem; min-height: 32px;" onclick="setGoogleHomeVolume({muted: 'toggle', device: '${escapeAttr(host)}'})">🔇 Mute</button>
                                            <button type="button" class="btn" style="flex: 1; padding: 5px 8px; font-size: 0.76rem; min-height: 32px;" onclick="setGoogleHomeVolume({delta: 10, device: '${escapeAttr(host)}'})">🔊 +10%</button>
                                        </div>
                                    </div>
                                    <div style="display: flex; gap: 6px; align-items: center;">
                                        <button type="button" id="${safeId}_pp" class="btn" style="padding: 6px 12px; font-size: 0.82rem; flex: 1;" onclick="controlGoogleHomeMedia('playpause', '${escapeAttr(host)}')">⏯️ Pause</button>
                                        <button type="button" id="${safeId}_stop" class="btn btn-danger" style="padding: 6px 12px; font-size: 0.82rem; flex: 1.5;" onclick="stopAudioPlayback('cast', '${escapeAttr(host)}')">🛑 Stop</button>
                                    </div>
                                `;
                                monitorHost.appendChild(card);
                            }

                            const badge = document.getElementById(`${safeId}_badge`);
                            const info = document.getElementById(`${safeId}_info`);
                            const btnStop = document.getElementById(`${safeId}_stop`);
                            const btnPP = document.getElementById(`${safeId}_pp`);
                            const slider = document.getElementById(`${safeId}_slider`);
                            const volVal = document.getElementById(`${safeId}_volVal`);
                            const btnMute = document.getElementById(`${safeId}_mute`);

                            if (dev.is_playing) {
                                const isPaused = dev.player_state === 'PAUSED';
                                badge.style.background = isPaused ? 'rgba(245, 158, 11, 0.25)' : 'rgba(59, 130, 246, 0.25)';
                                badge.style.color = isPaused ? '#fbbf24' : devColor;
                                badge.innerHTML = isPaused ? '⏸️ PAUSED' : '🟢 CASTING';
                                const elp = dev.elapsed_seconds ? Math.floor(dev.elapsed_seconds) : 0;
                                const elpMin = Math.floor(elp / 60);
                                const elpSec = String(elp % 60).padStart(2, '0');
                                info.innerHTML = `${devIcon} <b>${escapeHtml(dev.title || 'Media')}</b> &nbsp;<span style="color:${devColor};">(⏱️ ${elpMin}:${elpSec})</span>`;
                                btnStop.style.opacity = '1';
                                btnStop.style.boxShadow = '0 0 10px rgba(239, 68, 68, 0.5)';
                                btnStop.innerHTML = '🛑 Stop Siaran';
                                btnPP.style.opacity = '1';
                                btnPP.innerHTML = isPaused ? '▶️ Lanjut' : '⏸️ Pause';
                            } else {
                                badge.style.background = 'rgba(148, 163, 184, 0.2)';
                                badge.style.color = '#94a3b8';
                                badge.innerHTML = dev.live === false ? 'OFFLINE / STANDBY' : 'IDLE';
                                info.innerHTML = dev.live === false ? 'Perangkat sedang mati/standby.' : 'Tidak ada siaran aktif.';
                                btnStop.style.opacity = '0.65';
                                btnStop.style.boxShadow = 'none';
                                btnStop.innerHTML = '🛑 Force Stop';
                                btnPP.style.opacity = '0.55';
                                btnPP.innerHTML = '⏯️ Pause';
                            }

                            window._ghVolDragging = window._ghVolDragging || {};
                            if (slider && volVal && !window._ghVolDragging[safeId] && dev.volume_level !== null && dev.volume_level !== undefined) {
                                slider.value = dev.volume_level;
                                volVal.innerText = dev.volume_muted ? `🔇 ${dev.volume_level}% (Muted)` : `${dev.volume_level}%`;
                            }
                            if (btnMute) {
                                btnMute.innerHTML = dev.volume_muted ? '🔊 Unmute' : '🔇 Mute';
                                btnMute.style.borderColor = dev.volume_muted ? 'rgba(244, 63, 94, 0.5)' : '';
                                btnMute.style.color = dev.volume_muted ? '#fb7185' : '';
                            }
                        });
                    }
                }
            } catch (err) {}
        }

        function onGhVolInput(safeId, val) {
            window._ghVolDragging = window._ghVolDragging || {};
            window._ghVolDragTimers = window._ghVolDragTimers || {};
            window._ghVolDragging[safeId] = true;
            const el = document.getElementById(`${safeId}_volVal`);
            if (el) el.innerText = `${val}%`;
            clearTimeout(window._ghVolDragTimers[safeId]);
            window._ghVolDragTimers[safeId] = setTimeout(() => { window._ghVolDragging[safeId] = false; }, 1200);
        }

        async function setGoogleHomeVolume(payload) {
            window._ghVolDragging = {};
            try {
                const resp = await fetch('/api/cast/volume', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await resp.json();
                if (data.status === 'success') {
                    showToast(data.message || 'Volume diperbarui.');
                } else {
                    showToast(data.message || 'Gagal mengatur volume.', true);
                }
                updatePlaybackStatus();
            } catch (err) {
                showToast('Gagal menghubungi server.', true);
            }
        }

        async function controlGoogleHomeMedia(action, device = '') {
            try {
                const resp = await fetch('/api/cast/media', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: action, device: device || undefined })
                });
                const data = await resp.json();
                if (data.status === 'success') {
                    showToast(data.message || 'Kontrol media berhasil.');
                } else {
                    showToast(data.message || 'Gagal mengontrol media.', true);
                }
                updatePlaybackStatus();
            } catch (err) {
                showToast('Gagal menghubungi server.', true);
            }
        }

        async function stopAudioPlayback(target, device = '') {
            try {
                const resp = await fetch('/api/playback/stop', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target: target, device: device || undefined })
                });
                const data = await resp.json();
                if (data.status === 'success') {
                    showToast(data.message || 'Pemutaran audio dihentikan.', false);
                } else {
                    showToast('Gagal menghentikan: ' + (data.message || ''), true);
                }
                updatePlaybackStatus();
            } catch (err) {
                showToast('Gagal menghubungi server.', true);
            }
        }

        async function sendAskGoogleHome() {
            const inp = document.getElementById('askGhInput');
            const cmd = (inp ? inp.value : '').trim();
            if (!cmd) {
                showToast('Ketik perintah untuk Google Assistant terlebih dahulu!', true);
                return;
            }
            const sel = document.getElementById('castDeviceSelect');
            const dev = (sel && sel.value) ? sel.value : '';
            showToast(`🤖 Mengirim "${cmd}" ke Google Assistant...`);
            try {
                const resp = await fetch('/api/remote/ask_gh', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command: cmd, device: dev || undefined })
                });
                const data = await resp.json();
                const msg = data.message || data.reply || 'Perintah dikirim!';
                showToast(msg, data.status !== 'success');
                const box = document.getElementById('askGhReplyBox');
                const txt = document.getElementById('askGhReplyText');
                const aud = document.getElementById('askGhReplyAudio');
                if (data.status === 'success') {
                    if (inp) inp.value = '';
                    if (box && txt) {
                        box.style.display = 'block';
                        txt.textContent = '🤖 ' + msg;
                    }
                    if (aud && data.audio_url) {
                        aud.src = data.audio_url + '&t=' + Date.now();
                        aud.style.display = 'block';
                        aud.play().catch(() => {});
                    } else if (aud) {
                        aud.style.display = 'none';
                    }
                }
            } catch (err) {
                showToast('Gagal menghubungi server.', true);
            }
        }

        async function sendCastUrl(queue = false) {
            const inp = document.getElementById('castUrlInput');
            const url = (inp ? inp.value : '').trim();
            if (!url) {
                showToast('Tempel link YouTube atau URL media terlebih dahulu!', true);
                return;
            }
            const sel = document.getElementById('castDeviceSelect');
            // Jika URL YouTube/video/gambar, biarkan kosong agar server otomatis pilih Android TV, kecuali user memilih TV secara eksplisit
            const isYt = /youtu\.?be/i.test(url);
            const device = (!isYt && sel && sel.value) ? sel.value : '';
            showToast(queue ? '➕ Menambahkan ke antrean YouTube...' : '📺 Mengirim media ke perangkat Cast...');
            try {
                const resp = await fetch('/api/cast/url', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url, device: device, queue: queue })
                });
                const data = await resp.json();
                showToast(data.message || (data.status === 'success' ? 'Berhasil cast!' : 'Gagal cast URL'), data.status !== 'success');
                if (data.status === 'success' && inp) inp.value = '';
                setTimeout(updatePlaybackStatus, 600);
            } catch (err) {
                showToast('Gagal menghubungi server.', true);
            }
        }

        // ================= PUSTAKA AUDIO & VIDEO SERVER (MEDIA LIBRARY & PLAYER) =================
        let serverAudios = [];
        let mediaCurrentPage = 1;
        let mediaTypeFilter = 'all';
        const MEDIA_PAGE_SIZE = 5;

        async function loadAudioLibrary() {
            try {
                const resp = await fetch('/api/audio/list');
                const data = await resp.json();
                if (data.status === 'success') {
                    serverAudios = data.audios || [];
                    renderAudioLibrary();
                    populateSchedAudioSelect(serverAudios.filter(a => a.media_type !== 'video'));
                }
            } catch (err) {
                console.error('Error loading audio library:', err);
            }
        }

        function onMediaFilterChange() {
            mediaCurrentPage = 1;
            renderAudioLibrary();
        }

        function setMediaTypeFilter(type) {
            mediaTypeFilter = type;
            mediaCurrentPage = 1;
            ['all', 'audio', 'video'].forEach(t => {
                const b = document.getElementById('btnFilter' + t.charAt(0).toUpperCase() + t.slice(1));
                if (b) b.className = (t === type) ? 'btn btn-primary' : 'btn';
            });
            renderAudioLibrary();
        }

        function changeMediaPage(delta) {
            mediaCurrentPage += delta;
            renderAudioLibrary();
        }

        function renderAudioLibrary(audiosOverride) {
            const allItems = Array.isArray(audiosOverride) ? audiosOverride : serverAudios;
            const container = document.getElementById('audioListContainer');
            const empty = document.getElementById('audioEmptyState');
            const countBadge = document.getElementById('audioCountBadge');
            const pagBar = document.getElementById('audioPaginationBar');
            const pageInfo = document.getElementById('mediaPageInfo');
            const btnPrev = document.getElementById('btnMediaPrev');
            const btnNext = document.getElementById('btnMediaNext');
            if (!container || !countBadge) return;

            const q = ((document.getElementById('mediaSearchInput') || {}).value || '').trim().toLowerCase();
            const filtered = allItems.filter(a => {
                const mtype = a.media_type || (String(a.mime_type || '').startsWith('video/') ? 'video' : 'audio');
                if (mediaTypeFilter !== 'all' && mtype !== mediaTypeFilter) return false;
                if (q) {
                    const hay = `${a.name || ''} ${a.original_filename || ''}`.toLowerCase();
                    if (!hay.includes(q)) return false;
                }
                return true;
            });

            countBadge.innerText = q || mediaTypeFilter !== 'all'
                ? `${filtered.length} / ${allItems.length} Media`
                : `${allItems.length} Media`;

            if (filtered.length === 0) {
                if (empty) {
                    empty.style.display = 'block';
                    empty.innerText = allItems.length === 0
                        ? 'Belum ada file media di pustaka database. Unggah file Audio atau Video di atas!'
                        : 'Tidak ada media yang cocok dengan pencarian / filter.';
                }
                container.innerHTML = '';
                if (pagBar) pagBar.style.display = 'none';
                return;
            }

            if (empty) empty.style.display = 'none';
            const totalPages = Math.max(1, Math.ceil(filtered.length / MEDIA_PAGE_SIZE));
            if (mediaCurrentPage > totalPages) mediaCurrentPage = totalPages;
            if (mediaCurrentPage < 1) mediaCurrentPage = 1;
            const start = (mediaCurrentPage - 1) * MEDIA_PAGE_SIZE;
            const pageItems = filtered.slice(start, start + MEDIA_PAGE_SIZE);

            if (pagBar) {
                pagBar.style.display = totalPages > 1 ? 'flex' : 'none';
                if (pageInfo) pageInfo.innerText = `Halaman ${mediaCurrentPage} / ${totalPages} (${filtered.length} item)`;
                if (btnPrev) btnPrev.disabled = mediaCurrentPage <= 1;
                if (btnNext) btnNext.disabled = mediaCurrentPage >= totalPages;
            }

            container.innerHTML = pageItems.map(a => {
                const isVideo = (a.media_type === 'video') || String(a.mime_type || '').startsWith('video/');
                const icon = isVideo ? '🎬' : '🎵';
                const badgeBg = isVideo ? 'rgba(168, 85, 247, 0.2)' : 'rgba(236, 72, 153, 0.15)';
                const badgeClr = isVideo ? '#c084fc' : '#f472b6';
                const playerHtml = isVideo
                    ? `<video controls preload="metadata" src="${a.stream_url}" style="width: 100%; max-width: 340px; max-height: 190px; border-radius: 8px; background: #000; margin-top: 4px;"></video>`
                    : `<audio controls preload="none" src="${a.stream_url}" style="height: 32px; width: 100%; max-width: 320px;"></audio>`;
                const actionsHtml = isVideo
                    ? `
                        <button type="button" class="btn btn-primary" style="padding: 5px 10px; font-size: 0.75rem; background: #9333ea; border-color: #9333ea; color: #fff;" onclick="playAudioItem('${a.id}', 'cast', 'tv')" title="Cast video ini ke Android TV (TV Stick Mi)">📺 Cast TV</button>
                        <button type="button" class="btn btn-primary" style="padding: 5px 10px; font-size: 0.75rem; background: #3b82f6; border-color: #3b82f6;" onclick="playAudioItem('${a.id}', 'cast', '192.168.0.198')" title="Putar suara (audio saja) dari video ini ke Google Home Speaker">📢 Speaker</button>
                        <button type="button" class="btn" style="padding: 5px 10px; font-size: 0.75rem; border-color: #10b981; color: #34d399;" onclick="playAudioItem('${a.id}', 'say')" title="Buka video di Mac mini">💻 Mac</button>
                        <button type="button" class="btn btn-danger" style="padding: 5px 8px; font-size: 0.75rem;" onclick="deleteAudioItem('${a.id}')" title="Hapus media dari database">🗑️</button>
                    `
                    : `
                        <button type="button" class="btn btn-primary" style="padding: 5px 10px; font-size: 0.75rem; background: #3b82f6; border-color: #3b82f6;" onclick="playAudioItem('${a.id}', 'cast', '192.168.0.198')" title="Siarkan audio ini ke Google Home">📢 Speaker</button>
                        <button type="button" class="btn btn-primary" style="padding: 5px 10px; font-size: 0.75rem; background: #9333ea; border-color: #9333ea; color: #fff;" onclick="playAudioItem('${a.id}', 'cast', 'tv')" title="Siarkan audio ini ke Android TV (TV Stick Mi)">📺 TV</button>
                        <button type="button" class="btn" style="padding: 5px 10px; font-size: 0.75rem; border-color: #10b981; color: #34d399;" onclick="playAudioItem('${a.id}', 'say')" title="Putar di speaker Mac mini">🔊 Mac</button>
                        <button type="button" class="btn" style="padding: 5px 10px; font-size: 0.75rem; border-color: #8b5cf6; color: #c4b5fd;" onclick="playAudioItem('${a.id}', 'both')" title="Putar di Mac & Cast terpilih">📢+🔊 Keduanya</button>
                        <button type="button" class="btn" style="padding: 5px 8px; font-size: 0.75rem; border-color: #f59e0b; color: #fbbf24;" onclick="scheduleAudioItem('${a.id}')" title="Buat jadwal pengumuman dengan audio ini">⏰ Jadwalkan</button>
                        <button type="button" class="btn btn-danger" style="padding: 5px 8px; font-size: 0.75rem;" onclick="deleteAudioItem('${a.id}')" title="Hapus audio dari database">🗑️</button>
                    `;
                return `
                <div style="background: rgba(15, 23, 42, 0.75); border: 1px solid ${isVideo ? 'rgba(168, 85, 247, 0.35)' : 'rgba(236, 72, 153, 0.25)'}; border-radius: 10px; padding: 12px 14px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                    <div style="flex: 1; min-width: 220px;">
                        <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px; flex-wrap: wrap;">
                            <span style="font-weight: 700; font-size: 0.92rem; color: #f8fafc;">${icon} ${escapeHtml(a.name)}</span>
                            <span style="background: ${badgeBg}; color: ${badgeClr}; font-size: 0.68rem; padding: 2px 6px; border-radius: 4px; font-weight: 700;">${isVideo ? 'VIDEO' : 'AUDIO'}</span>
                            ${a.duration_seconds ? `<span style="background: rgba(236, 72, 153, 0.15); color: #f472b6; font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; font-weight: 600;">⏱️ ${a.duration_formatted}</span>` : ''}
                            <span style="font-size: 0.72rem; color: var(--text-dim);">💾 ${a.size_formatted}</span>
                        </div>
                        <div style="font-size: 0.75rem; color: var(--text-muted); margin-bottom: 6px; word-break: break-all;">
                            Berkas: <code>${escapeHtml(a.original_filename)}</code> • 📅 ${a.created_at}
                        </div>
                        ${playerHtml}
                    </div>
                    <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
                        ${actionsHtml}
                    </div>
                </div>`;
            }).join('');
        }

        function uploadAudioFile() {
            const fileInput = document.getElementById('audioUploadFile');
            const labelInput = document.getElementById('audioUploadLabel');
            const btn = document.getElementById('btnUploadAudio');
            const progWrap = document.getElementById('audioUploadProgressWrap');
            const progBar = document.getElementById('audioUploadProgressBar');
            const progTxt = document.getElementById('audioUploadProgressText');
            const progPct = document.getElementById('audioUploadPercent');

            if (!fileInput.files || fileInput.files.length === 0) {
                showToast('Pilih file audio atau video terlebih dahulu', true);
                return;
            }

            const file = fileInput.files[0];
            const label = labelInput.value.trim() || file.name;
            const formData = new FormData();
            formData.append('audio', file);
            formData.append('label', label);

            btn.disabled = true;
            btn.innerText = '⏳ Mengunggah...';
            if (progWrap) progWrap.style.display = 'block';
            if (progBar) progBar.style.width = '0%';
            if (progPct) progPct.innerText = '0%';
            if (progTxt) progTxt.innerText = `Mengunggah "${label}"...`;

            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/audio/upload', true);

            xhr.upload.onprogress = function(e) {
                if (e.lengthComputable) {
                    const pct = Math.min(99, Math.round((e.loaded / e.total) * 100));
                    const loadedMb = (e.loaded / (1024 * 1024)).toFixed(1);
                    const totalMb = (e.total / (1024 * 1024)).toFixed(1);
                    if (progBar) progBar.style.width = pct + '%';
                    if (progPct) progPct.innerText = pct + '%';
                    if (progTxt) progTxt.innerText = `Mengunggah "${label}" (${loadedMb} MB / ${totalMb} MB)...`;
                }
            };

            xhr.onload = function() {
                btn.disabled = false;
                btn.innerText = '📤 Unggah & Simpan ke Pustaka';
                try {
                    const res = JSON.parse(xhr.responseText || '{}');
                    if (xhr.status >= 200 && xhr.status < 300 && res.status === 'success') {
                        if (progBar) progBar.style.width = '100%';
                        if (progPct) progPct.innerText = '100%';
                        if (progTxt) progTxt.innerText = '✅ Selesai! Memuat ulang daftar...';
                        showToast(`✅ Media "${res.audio.name}" berhasil disimpan!`);
                        fileInput.value = '';
                        labelInput.value = '';
                        mediaCurrentPage = 1;
                        loadAudioLibrary();
                        setTimeout(() => { if (progWrap) progWrap.style.display = 'none'; }, 1200);
                    } else {
                        if (progWrap) progWrap.style.display = 'none';
                        showToast('Gagal upload: ' + (res.message || 'Error server'), true);
                    }
                } catch (err) {
                    if (progWrap) progWrap.style.display = 'none';
                    showToast('Respons server tidak valid saat upload.', true);
                }
            };

            xhr.onerror = function() {
                btn.disabled = false;
                btn.innerText = '📤 Unggah & Simpan ke Pustaka';
                if (progWrap) progWrap.style.display = 'none';
                showToast('Error koneksi saat mengunggah file.', true);
            };

            xhr.send(formData);
        }

        async function playAudioItem(id, target, explicitDevice = '') {
            const dev = explicitDevice || (document.getElementById('castDeviceSelect') ? document.getElementById('castDeviceSelect').value : '');
            const label = explicitDevice === 'tv' ? 'TV Stick Mi' : (target === 'cast' ? 'Google Cast' : target);
            showToast(`Memulai pemutaran audio di ${label}...`);
            try {
                const resp = await fetch('/api/audio/play', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: id, target: target, device: dev })
                });
                const res = await resp.json();
                if (res.status === 'success') {
                    showToast(res.message);
                    setTimeout(updatePlaybackStatus, 400);
                } else {
                    showToast('Gagal memutar: ' + res.message, true);
                }
            } catch (err) {
                showToast('Error koneksi: ' + err, true);
            }
        }

        async function deleteAudioItem(id) {
            if (!confirm('Hapus file audio ini dari database server?')) return;
            try {
                const resp = await fetch('/api/audio/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: id })
                });
                const res = await resp.json();
                if (res.status === 'success') {
                    showToast('File audio berhasil dihapus');
                    loadAudioLibrary();
                    loadSchedules();
                } else {
                    showToast('Gagal menghapus: ' + res.message, true);
                }
            } catch (err) {
                showToast('Error koneksi: ' + err, true);
            }
        }

        function populateSchedAudioSelect(audios) {
            const sel = document.getElementById('schedAudioSelect');
            if (!sel) return;
            const currVal = sel.value;
            sel.innerHTML = '<option value="">-- Pilih File Audio / Video dari Pustaka --</option>';
            audios.forEach(a => {
                const isVid = (a.media_type === 'video') || String(a.mime_type || '').startsWith('video/');
                const opt = document.createElement('option');
                opt.value = a.id;
                opt.text = `${isVid ? '🎬' : '🎵'} ${a.name} (${a.duration_formatted} • ${a.original_filename})`;
                sel.appendChild(opt);
            });
            if (currVal) sel.value = currVal;
        }

        function updateSchedAudioPreview() {
            const sel = document.getElementById('schedAudioSelect');
            const preview = document.getElementById('schedAudioPreview');
            if (!sel || !preview) return;
            const aid = sel.value;
            if (aid) {
                preview.src = `/api/audio/file?id=${aid}`;
                preview.style.display = 'block';
            } else {
                preview.src = '';
                preview.style.display = 'none';
            }
        }

        function scheduleAudioItem(audioId) {
            switchSchedMode('audio');
            const sel = document.getElementById('schedAudioSelect');
            if (sel) {
                sel.value = audioId;
                updateSchedAudioPreview();
            }
            const match = serverAudios.find(a => a.id === audioId);
            if (match) {
                document.getElementById('schedLabelInput').value = `Putar: ${match.name}`;
            }
            // Scroll ke form jadwal
            document.getElementById('schedLabelInput').focus();
            showToast('Mode jadwal dialihkan ke File Audio. Tentukan waktu eksekusi!');
        }

        // ================= MANAJEMEN JADWAL SUARA (CAST & SAY SCHEDULER - SQLITE) =================
        let currentSchedMode = 'tts';

        function switchSchedMode(mode) {
            currentSchedMode = mode;
            const btnTTS = document.getElementById('btnSchedModeTTS');
            const btnAudio = document.getElementById('btnSchedModeAudio');
            const btnURL = document.getElementById('btnSchedModeURL');
            const secTTS = document.getElementById('schedSectionTTS');
            const secAudio = document.getElementById('schedSectionAudio');
            const secURL = document.getElementById('schedSectionURL');
            const tgtSel = document.getElementById('schedTargetSelect');

            [btnTTS, btnAudio, btnURL].forEach(b => b && b.classList.remove('btn-primary'));
            if (secTTS) secTTS.style.display = 'none';
            if (secAudio) secAudio.style.display = 'none';
            if (secURL) secURL.style.display = 'none';

            if (mode === 'audio') {
                if (btnAudio) btnAudio.classList.add('btn-primary');
                if (secAudio) secAudio.style.display = 'block';
                populateSchedAudioSelect(serverAudios);
            } else if (mode === 'url') {
                if (btnURL) btnURL.classList.add('btn-primary');
                if (secURL) secURL.style.display = 'block';
                if (tgtSel) tgtSel.value = 'tv';
            } else {
                if (btnTTS) btnTTS.classList.add('btn-primary');
                if (secTTS) secTTS.style.display = 'block';
            }
        }

        function setSchedTime(val) {
            document.getElementById('schedTimeInput').value = val;
            document.getElementById('schedDatetimePicker').value = '';
        }

        function syncDatetimeToInput() {
            const val = document.getElementById('schedDatetimePicker').value;
            if (val) {
                document.getElementById('schedTimeInput').value = val.replace('T', ' ');
            }
        }

        async function loadSchedules() {
            try {
                const resp = await fetch('/api/schedule/list');
                const data = await resp.json();
                if (data.status === 'success') {
                    renderSchedules(data.schedules || []);
                }
            } catch (err) {
                console.error('Error loading schedules:', err);
            }
        }

        function renderSchedules(schedules) {
            const container = document.getElementById('schedListContainer');
            const empty = document.getElementById('schedEmptyState');
            const countBadge = document.getElementById('schedCountBadge');
            if (!container || !countBadge) return;

            countBadge.innerText = `${schedules.length} Jadwal`;
            if (schedules.length === 0) {
                if (empty) empty.style.display = 'block';
                container.innerHTML = '';
                return;
            }

            if (empty) empty.style.display = 'none';
            container.innerHTML = schedules.map(item => {
                const isAudio = item.mode === 'audio';
                const isUrl = item.mode === 'url';
                const modeBadge = isUrl
                    ? '<span style="background: rgba(168, 85, 247, 0.2); color: #c084fc; font-size: 0.72rem; padding: 2px 7px; border-radius: 4px; font-weight: 700; border: 1px solid rgba(168, 85, 247, 0.35);">▶️ YOUTUBE / URL</span>'
                    : (isAudio
                        ? '<span style="background: rgba(236, 72, 153, 0.2); color: #f472b6; font-size: 0.72rem; padding: 2px 7px; border-radius: 4px; font-weight: 700; border: 1px solid rgba(236, 72, 153, 0.35);">🎵🎬 MEDIA PUSTAKA</span>'
                        : '<span style="background: rgba(56, 189, 248, 0.2); color: #38bdf8; font-size: 0.72rem; padding: 2px 7px; border-radius: 4px; font-weight: 700; border: 1px solid rgba(56, 189, 248, 0.35);">🗣️ AI TTS</span>');

                const dailyTag = item.is_daily ? '<span style="background: rgba(16, 185, 129, 0.2); color: #34d399; font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; font-weight: 600;">🔁 Harian</span>' : '';

                const contentDesc = isUrl
                    ? `URL: <code>${escapeHtml(item.message)}</code>`
                    : (isAudio
                        ? `Media: <b>${escapeHtml(item.audio_name || item.audio_filename || 'File Media')}</b>`
                        : `"${escapeHtml(item.message)}"`);

                return `
                    <div style="background: rgba(15, 23, 42, 0.75); border: 1px solid rgba(139, 92, 246, 0.25); border-radius: 10px; padding: 12px 14px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                        <div style="flex: 1; min-width: 240px;">
                            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px; flex-wrap: wrap;">
                                <span style="font-weight: 700; font-size: 0.92rem; color: #f8fafc;">⏰ ${escapeHtml(item.label || 'Pengumuman')}</span>
                                ${modeBadge}
                                ${dailyTag}
                                <span style="background: rgba(245, 158, 11, 0.15); color: #fbbf24; font-size: 0.75rem; padding: 2px 8px; border-radius: 12px; font-weight: 600; border: 1px solid rgba(245, 158, 11, 0.3);">⏱️ ${item.remaining_readable}</span>
                                <span style="font-size: 0.72rem; color: var(--text-dim);">(Pukul ${item.target_datetime || item.target_readable})</span>
                            </div>
                            <div style="font-size: 0.85rem; color: #cbd5e1; margin-bottom: 6px; font-style: italic;">
                                ${contentDesc}
                            </div>
                            <div style="display: flex; gap: 8px; font-size: 0.75rem; color: var(--text-muted); flex-wrap: wrap;">
                                <span>🎯 Target: <b>${escapeHtml(item.target_display || item.target)}</b></span>
                                ${!isAudio ? `<span>•</span><span>🗣️ Suara: <b>${escapeHtml(item.voice_display || item.voice_type)}</b></span>` : ''}
                                <span>•</span><span>🔊 Vol: <b>${item.volume ?? 70}%</b>${item.restore_volume !== null && item.restore_volume !== undefined ? ` (↩️ ${item.restore_volume === -1 ? 'Sebelumnya' : item.restore_volume + '%'})` : ''}</span>
                            </div>
                        </div>
                        <div style="display: flex; gap: 8px; align-items: center;">
                            <button type="button" class="btn" style="padding: 5px 10px; font-size: 0.78rem; border-color: #8b5cf6; color: #c4b5fd;" onclick="testScheduleNow('${item.id}')" title="Tes bunyikan suara pengumuman ini sekarang">⚡ Tes Bunyi</button>
                            <button type="button" class="btn btn-danger" style="padding: 5px 8px; font-size: 0.78rem;" onclick="deleteScheduleItem('${item.id}')" title="Hapus jadwal ini">🗑️</button>
                        </div>
                    </div>
                `;
            }).join('');
        }

        async function createSchedule() {
            const label = document.getElementById('schedLabelInput').value.trim();
            const timeVal = document.getElementById('schedTimeInput').value.trim();
            const target = document.getElementById('schedTargetSelect').value;
            const isDaily = document.getElementById('schedDailyCheckbox').checked;

            if (!timeVal) {
                showToast('Pilih tanggal & jam atau ketik durasi (contoh: 10m, 07:00, 2026-11-22 08:30)', true);
                return;
            }

            const vol = parseInt(document.getElementById('schedVolumeInput').value || '70', 10);
            const rvolVal = document.getElementById('schedRestoreVolSelect').value;
            const rvol = rvolVal === 'none' ? null : parseInt(rvolVal, 10);

            let payload = {
                label: label,
                time: timeVal,
                target: target,
                mode: currentSchedMode,
                is_daily: isDaily,
                volume: vol,
                restore_volume: rvol
            };

            if (currentSchedMode === 'audio') {
                const audioId = document.getElementById('schedAudioSelect').value;
                if (!audioId) {
                    showToast('Pilih salah satu file audio/video dari pustaka', true);
                    return;
                }
                payload.audio_id = audioId;
            } else if (currentSchedMode === 'url') {
                const urlVal = (document.getElementById('schedUrlInput').value || '').trim();
                if (!urlVal) {
                    showToast('Tempel link YouTube atau URL stream terlebih dahulu', true);
                    return;
                }
                payload.message = urlVal;
            } else {
                const message = document.getElementById('schedMessageInput').value.trim();
                const voice = document.getElementById('schedVoiceSelect').value;
                if (!message) {
                    showToast('Masukkan isi pesan teks pengumuman', true);
                    return;
                }
                payload.message = message;
                payload.voice = voice;
            }

            try {
                const resp = await fetch('/api/schedule/add', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const res = await resp.json();
                if (res.status === 'success') {
                    showToast(`✅ Jadwal "${res.label}" berhasil disimpan ke SQLite! (${res.remaining_readable})`);
                    document.getElementById('schedLabelInput').value = '';
                    document.getElementById('schedTimeInput').value = '';
                    document.getElementById('schedDatetimePicker').value = '';
                    document.getElementById('schedMessageInput').value = '';
                    loadSchedules();
                } else {
                    showToast('Gagal: ' + (res.message || 'Error tidak diketahui'), true);
                }
            } catch (err) {
                showToast('Error koneksi: ' + err, true);
            }
        }

        async function deleteScheduleItem(id) {
            if (!confirm('Hapus jadwal pengumuman suara ini dari database?')) return;
            try {
                const resp = await fetch('/api/schedule/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: id })
                });
                const res = await resp.json();
                if (res.status === 'success') {
                    showToast('Jadwal berhasil dihapus');
                    loadSchedules();
                } else {
                    showToast('Gagal menghapus: ' + res.message, true);
                }
            } catch (err) {
                showToast('Error koneksi: ' + err, true);
            }
        }

        async function testScheduleNow(id) {
            showToast('Memulai uji coba suara jadwal...');
            try {
                const resp = await fetch('/api/schedule/trigger_now', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: id })
                });
                const res = await resp.json();
                showToast(res.message || 'Uji coba suara selesai');
            } catch (err) {
                showToast('Error koneksi: ' + err, true);
            }
        }

        // ================= MANAJEMEN QUICK ACTION (FLOATING FAB + MODAL + CRUD) =================
        let quickActionsCache = [];
        let qaAudiosCache = [];

        function openQuickActionModal() {
            const modal = document.getElementById('quickActionModal');
            if (!modal) return;
            openModal('quickActionModal');
            closeQaForm();
            syncQaDeviceDropdown();
            loadQaAudiosDropdown();
            loadQuickActions();
        }

        function syncQaDeviceDropdown() {
            const qaSel = document.getElementById('qaDeviceInput');
            const mainSel = document.getElementById('castDeviceSelect');
            if (!qaSel) return;
            const prevVal = qaSel.value;
            let html = '<option value="">📢 Default Google Home (Otomatis)</option>';
            html += '<option value="all">🔊 Semua Perangkat (Google Home + TV)</option>';
            if (mainSel && mainSel.options && mainSel.options.length > 0) {
                for (let i = 0; i < mainSel.options.length; i++) {
                    const opt = mainSel.options[i];
                    if (opt.value) {
                        html += `<option value="${escapeHtml(opt.value)}">${escapeHtml(opt.text)}</option>`;
                    }
                }
            }
            qaSel.innerHTML = html;
            if (prevVal) qaSel.value = prevVal;
        }

        async function loadQaAudiosDropdown() {
            const sel = document.getElementById('qaAudioInput');
            if (!sel) return;
            try {
                const resp = await fetch('/api/audio/list');
                const data = await resp.json();
                qaAudiosCache = data.audios || [];
                const prev = sel.value;
                let html = '<option value="">-- Pilih Audio / Video dari Pustaka --</option>';
                for (const a of qaAudiosCache) {
                    const ico = a.media_type === 'video' ? '🎬' : '🎵';
                    html += `<option value="${escapeHtml(a.id)}">${ico} ${escapeHtml(a.name)} (${escapeHtml(a.duration_formatted || '-')})</option>`;
                }
                sel.innerHTML = html;
                if (prev) sel.value = prev;
            } catch (e) {}
        }

        function onQaTypeChange() {
            const atype = (document.getElementById('qaTypeInput')?.value || 'tts');
            const msgWrap = document.getElementById('qaMsgWrap');
            const msgInp = document.getElementById('qaMessageInput');
            const audWrap = document.getElementById('qaAudioWrap');
            const ttsOpts = document.getElementById('qaTtsOptionsWrap');
            if (msgWrap) msgWrap.style.display = (atype === 'audio') ? 'none' : 'block';
            if (audWrap) audWrap.style.display = (atype === 'audio') ? 'block' : 'none';
            if (ttsOpts) ttsOpts.style.display = (atype === 'tts') ? 'grid' : 'none';
            if (msgInp) {
                msgInp.placeholder = atype === 'assistant'
                    ? 'Tulis perintah Google Assistant (cth: nyalakan lampu ruang tamu, matikan semua lampu)...'
                    : 'Tulis kalimat broadcast yang akan diucapkan Google Home...';
            }
        }

        async function loadQuickActions() {
            const listEl = document.getElementById('qaListContainer');
            if (listEl && quickActionsCache.length === 0) {
                listEl.innerHTML = '<div style="text-align:center; padding: 18px; color: var(--text-dim);">Memuat daftar Quick Action...</div>';
            }
            try {
                const resp = await fetch('/api/quick_actions/list');
                const data = await resp.json();
                if (data.status === 'success') {
                    quickActionsCache = data.quick_actions || [];
                    renderQuickActions();
                } else if (listEl) {
                    listEl.innerHTML = `<div style="text-align:center; padding: 16px; color: #f87171;">${escapeHtml(data.message || 'Gagal memuat Quick Action')}</div>`;
                }
            } catch (err) {
                if (listEl) listEl.innerHTML = '<div style="text-align:center; padding: 16px; color: #f87171;">Gagal terhubung ke server.</div>';
            }
        }

        function _qaVoiceLabel(vt) {
            if (vt === 'pria') return '👨 Pria';
            if (vt === 'wanita2') return '👧 Wanita 2';
            return '👩 Wanita 1';
        }

        function renderQuickActions() {
            const listEl = document.getElementById('qaListContainer');
            if (!listEl) return;
            if (!quickActionsCache || quickActionsCache.length === 0) {
                listEl.innerHTML = `
                    <div style="text-align:center; padding: 24px 12px; color: var(--text-dim); border: 1px dashed rgba(148,163,184,0.25); border-radius: 12px;">
                        Belum ada Quick Action tersimpan.<br>
                        <button type="button" class="btn btn-primary" style="margin-top: 10px;" onclick="openQaForm()">➕ Buat Quick Action Pertama</button>
                    </div>`;
                return;
            }
            listEl.innerHTML = quickActionsCache.map(qa => {
                const atype = qa.action_type || 'tts';
                const rvol = qa.restore_volume === -1 ? 'Prev' : `${qa.restore_volume ?? 50}%`;
                const typeBadge = atype === 'assistant'
                    ? '<span class="qa-pill" style="background:rgba(168,85,247,0.18);color:#d8b4fe;border-color:rgba(168,85,247,0.35);">🤖 Assistant</span>'
                    : (atype === 'audio'
                        ? '<span class="qa-pill" style="background:rgba(245,158,11,0.16);color:#fcd34d;border-color:rgba(245,158,11,0.35);">🎵 Pustaka</span>'
                        : `<span class="qa-pill">${_qaVoiceLabel(qa.voice_type)}</span>`);
                const chimeBadge = (atype === 'tts' && qa.use_chime) ? '<span class="qa-pill">🔔 Chime</span>' : '';
                const repBadge = (atype === 'tts' && (qa.repeat_count || 1) > 1) ? `<span class="qa-pill">🔁 ${qa.repeat_count}x</span>` : '';
                const devBadge = qa.target_device === 'all'
                    ? '<span class="qa-pill">🔊 Semua</span>'
                    : (qa.target_device ? `<span class="qa-pill">📡 ${escapeHtml(qa.target_device)}</span>` : '');
                return `
                    <div class="qa-card">
                        <div class="qa-icon-badge">${escapeHtml(qa.icon || '📢')}</div>
                        <div class="qa-info">
                            <div class="qa-title">${escapeHtml(qa.title)}</div>
                            <div class="qa-msg">${atype === 'audio' ? '🎵 ' + escapeHtml(qa.message || qa.title) : '"' + escapeHtml(qa.message) + '"'}</div>
                            <div class="qa-meta">
                                <span class="qa-pill vol">🔊 ${qa.volume ?? 70}% → ${rvol}</span>
                                ${typeBadge}
                                ${chimeBadge}
                                ${repBadge}
                                ${devBadge}
                            </div>
                        </div>
                        <div class="qa-actions">
                            <button type="button" class="qa-play-btn" id="qaPlayBtn_${qa.id}" onclick="playQuickAction('${qa.id}')">▶️ Play</button>
                            <button type="button" class="btn" style="padding: 6px 9px;" title="Edit Quick Action" onclick="openQaForm('${qa.id}')">✏️</button>
                            <button type="button" class="btn" style="padding: 6px 9px; color: #f87171;" title="Hapus Quick Action" onclick="deleteQuickAction('${qa.id}')">🗑️</button>
                        </div>
                    </div>`;
            }).join('');
        }

        async function playQuickAction(qaId) {
            const btn = document.getElementById('qaPlayBtn_' + qaId);
            const origText = btn ? btn.innerHTML : '';
            if (btn) { btn.disabled = true; btn.innerHTML = '⏳...'; }
            const qa = quickActionsCache.find(x => x.id === qaId);
            showToast(`⚡ Menjalankan "${qa ? qa.title : 'Quick Action'}"...`);
            try {
                const resp = await fetch('/api/quick_actions/play', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: qaId })
                });
                const data = await resp.json();
                showToast(data.message || 'Selesai dijalankan!', data.status !== 'success');
                if (typeof updatePlaybackStatus === 'function') updatePlaybackStatus();
            } catch (err) {
                showToast('Gagal menjalankan Quick Action.', true);
            } finally {
                if (btn) { btn.disabled = false; btn.innerHTML = origText || '▶️ Play'; }
            }
        }

        async function playInstantQuickBroadcast(saveToo = false) {
            const inp = document.getElementById('qaInstantText');
            const volInp = document.getElementById('qaInstantVol');
            const chimeChk = document.getElementById('qaInstantChime');
            const msg = (inp ? inp.value : '').trim();
            const vol = Math.max(0, Math.min(100, parseInt(volInp ? volInp.value : '70', 10) || 70));
            const useChime = chimeChk ? chimeChk.checked : true;
            if (!msg) {
                showToast('Ketik kalimat broadcast terlebih dahulu!', true);
                if (inp) inp.focus();
                return;
            }
            if (saveToo) {
                openQaForm();
                document.getElementById('qaTypeInput').value = 'tts';
                onQaTypeChange();
                document.getElementById('qaMessageInput').value = msg;
                document.getElementById('qaTitleInput').value = msg.slice(0, 30);
                document.getElementById('qaVolumeInput').value = vol;
                document.getElementById('qaVolumeLabel').textContent = vol + '%';
                document.getElementById('qaChimeInput').checked = useChime;
                return;
            }
            const btn = document.getElementById('qaInstantPlayBtn');
            if (btn) { btn.disabled = true; btn.textContent = '⏳...'; }
            showToast(`👩 Broadcast suara wanita (Vol ${vol}% → 50%) ke Google Home...`);
            try {
                const resp = await fetch('/api/quick_actions/play', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        action_type: 'tts',
                        message: msg,
                        voice_type: 'wanita1',
                        volume: vol,
                        restore_volume: 50,
                        use_chime: useChime,
                        repeat_count: 1
                    })
                });
                const data = await resp.json();
                showToast(data.message || 'Broadcast selesai!', data.status !== 'success');
                if (data.status === 'success' && inp) inp.value = '';
                if (typeof updatePlaybackStatus === 'function') updatePlaybackStatus();
            } catch (err) {
                showToast('Gagal mengirim broadcast instan.', true);
            } finally {
                if (btn) { btn.disabled = false; btn.textContent = '▶️ Play'; }
            }
        }

        function pickQaIcon(emoji) {
            const el = document.getElementById('qaIconInput');
            if (el) el.value = emoji;
        }

        function openQaForm(editId = null) {
            const panel = document.getElementById('qaFormPanel');
            if (!panel) return;
            syncQaDeviceDropdown();
            loadQaAudiosDropdown();
            const heading = document.getElementById('qaFormHeading');
            const idEl = document.getElementById('qaEditId');
            const typeEl = document.getElementById('qaTypeInput');
            const iconEl = document.getElementById('qaIconInput');
            const titleEl = document.getElementById('qaTitleInput');
            const msgEl = document.getElementById('qaMessageInput');
            const audEl = document.getElementById('qaAudioInput');
            const voiceEl = document.getElementById('qaVoiceInput');
            const volEl = document.getElementById('qaVolumeInput');
            const volLbl = document.getElementById('qaVolumeLabel');
            const rvolEl = document.getElementById('qaRestoreVolInput');
            const chimeEl = document.getElementById('qaChimeInput');
            const repEl = document.getElementById('qaRepeatInput');
            const devEl = document.getElementById('qaDeviceInput');

            if (editId) {
                const qa = quickActionsCache.find(x => x.id === editId);
                if (!qa) return;
                heading.textContent = '✏️ Edit Quick Action';
                idEl.value = qa.id;
                typeEl.value = qa.action_type || 'tts';
                iconEl.value = qa.icon || '📢';
                titleEl.value = qa.title || '';
                msgEl.value = qa.message || '';
                if (audEl) audEl.value = qa.audio_id || '';
                voiceEl.value = qa.voice_type || 'wanita1';
                volEl.value = qa.volume ?? 70;
                volLbl.textContent = (qa.volume ?? 70) + '%';
                if (rvolEl) rvolEl.value = String(qa.restore_volume ?? 50);
                if (chimeEl) chimeEl.checked = qa.use_chime !== false;
                if (repEl) repEl.value = String(qa.repeat_count || 1);
                devEl.value = qa.target_device || '';
            } else {
                heading.textContent = '➕ Tambah Quick Action Baru';
                idEl.value = '';
                typeEl.value = 'tts';
                iconEl.value = '📢';
                titleEl.value = '';
                msgEl.value = '';
                if (audEl) audEl.value = '';
                voiceEl.value = 'wanita1';
                volEl.value = 70;
                volLbl.textContent = '70%';
                if (rvolEl) rvolEl.value = '50';
                if (chimeEl) chimeEl.checked = true;
                if (repEl) repEl.value = '1';
                devEl.value = '';
            }
            onQaTypeChange();
            panel.style.display = 'block';
            titleEl.focus();
        }

        function closeQaForm() {
            const panel = document.getElementById('qaFormPanel');
            if (panel) panel.style.display = 'none';
        }

        async function saveQuickAction() {
            const id = (document.getElementById('qaEditId').value || '').trim();
            const action_type = document.getElementById('qaTypeInput').value || 'tts';
            const icon = (document.getElementById('qaIconInput').value || '📢').trim();
            const title = (document.getElementById('qaTitleInput').value || '').trim();
            const message = (document.getElementById('qaMessageInput').value || '').trim();
            const audio_id = (document.getElementById('qaAudioInput')?.value || '').trim();
            const voice_type = document.getElementById('qaVoiceInput').value || 'wanita1';
            const volume = parseInt(document.getElementById('qaVolumeInput').value || '70', 10);
            const restore_volume = parseInt(document.getElementById('qaRestoreVolInput')?.value || '50', 10);
            const use_chime = document.getElementById('qaChimeInput')?.checked ?? true;
            const repeat_count = parseInt(document.getElementById('qaRepeatInput')?.value || '1', 10);
            const target_device = document.getElementById('qaDeviceInput').value || '';

            if (action_type === 'audio' && !audio_id) {
                showToast('Pilih file audio/video dari Pustaka Media terlebih dahulu!', true);
                return;
            }
            if (action_type !== 'audio' && !message) {
                showToast('Kalimat/perintah wajib diisi!', true);
                return;
            }
            const url = id ? '/api/quick_actions/update' : '/api/quick_actions/add';
            const payload = {
                id: id || undefined,
                action_type,
                icon,
                title,
                message,
                audio_id,
                voice_type,
                volume,
                restore_volume,
                use_chime,
                repeat_count,
                target_device
            };
            try {
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await resp.json();
                if (data.status === 'success') {
                    showToast(id ? '✅ Quick Action diperbarui!' : '✅ Quick Action ditambahkan!');
                    closeQaForm();
                    loadQuickActions();
                } else {
                    showToast(data.message || 'Gagal menyimpan Quick Action', true);
                }
            } catch (err) {
                showToast('Gagal menyimpan Quick Action.', true);
            }
        }

        async function deleteQuickAction(qaId) {
            const qa = quickActionsCache.find(x => x.id === qaId);
            if (!confirm(`Hapus Quick Action "${qa ? qa.title : qaId}"?`)) return;
            try {
                const resp = await fetch('/api/quick_actions/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: qaId })
                });
                const data = await resp.json();
                if (data.status === 'success') {
                    showToast('🗑️ Quick Action dihapus.');
                    loadQuickActions();
                } else {
                    showToast(data.message || 'Gagal menghapus.', true);
                }
            } catch (err) {
                showToast('Gagal menghapus Quick Action.', true);
            }
        }

        // ================= MODAL PENGATURAN TERPUSAT (5 TAB CRUD) =================
        function openSettingsModal(tabKey = 'tg') {
            openModal('settingsModal');
            switchSettingsTab(tabKey);
        }

        function switchSettingsTab(tabKey) {
            document.querySelectorAll('.set-tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tabKey));
            document.querySelectorAll('.set-pane').forEach(p => p.classList.toggle('active', p.id === 'setPane_' + tabKey));
            if (tabKey === 'tg') loadSettingsTelegram();
            else if (tabKey === 'cast') loadSettingsCast();
            else if (tabKey === 'audio') loadSettingsPrefs();
            else if (tabKey === 'ga') loadSettingsGA();
            else if (tabKey === 'sys') loadSettingsSystem();
        }

        // --- TAB 1: TELEGRAM BOT CRUD ---
        async function loadSettingsTelegram() {
            try {
                const r = await fetch('/api/settings/telegram');
                const d = await r.json();
                document.getElementById('setTgStatus').innerHTML = d.bot_running
                    ? '<span style="color:#4ade80;font-weight:700;">🟢 Bot Aktif (@Subuh_macBot)</span>'
                    : '<span style="color:#f87171;font-weight:700;">🔴 Bot Tidak Berjalan</span>';
                document.getElementById('setTgTokenMask').textContent = d.has_token ? `Token tersimpan: ${d.token_masked}` : 'Belum ada token tersimpan';
                document.getElementById('setTgTokenInput').value = '';
                document.getElementById('setTgDefaultTarget').value = d.default_target || 'gh';
                const listEl = document.getElementById('setTgUsersList');
                const users = d.allowed_users || [];
                if (users.length === 0) {
                    listEl.innerHTML = '<div style="font-size:0.8rem;color:var(--text-dim);">Belum ada Chat ID terdaftar.</div>';
                } else {
                    listEl.innerHTML = users.map(u => `
                        <div class="set-item-row">
                            <div>
                                <strong style="color:var(--text-main);">${escapeHtml(String(u.chat_id))}</strong>
                                ${u.label ? `<span style="margin-left:6px;font-size:0.78rem;color:#7dd3fc;">(${escapeHtml(u.label)})</span>` : ''}
                            </div>
                            <div style="display:flex;gap:5px;">
                                <button type="button" class="btn" style="padding:4px 8px;font-size:0.75rem;" onclick="editTgChatId('${u.chat_id}','${escapeHtml(u.label || '')}')">✏️</button>
                                <button type="button" class="btn" style="padding:4px 8px;font-size:0.75rem;" onclick="testTgMessage('${u.chat_id}')">📨 Tes</button>
                                <button type="button" class="btn" style="padding:4px 8px;font-size:0.75rem;color:#f87171;" onclick="deleteTgChatId('${u.chat_id}')">🗑️</button>
                            </div>
                        </div>`).join('');
                }
            } catch (e) { showToast('Gagal memuat pengaturan Telegram.', true); }
        }

        async function saveTgBotConfig() {
            const tok = (document.getElementById('setTgTokenInput').value || '').trim();
            const tgt = document.getElementById('setTgDefaultTarget').value || 'gh';
            const r = await fetch('/api/settings/telegram/save', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ token: tok || undefined, default_target: tgt })
            });
            const d = await r.json();
            showToast(d.message || 'Disimpan!', d.status !== 'success');
            loadSettingsTelegram();
        }

        function editTgChatId(cid, label) {
            document.getElementById('setTgNewChatId').value = cid;
            document.getElementById('setTgNewLabel').value = label;
            document.getElementById('setTgNewLabel').focus();
        }

        async function saveTgChatId() {
            const cid = (document.getElementById('setTgNewChatId').value || '').trim();
            const lbl = (document.getElementById('setTgNewLabel').value || '').trim();
            if (!cid) { showToast('Isi Chat ID terlebih dahulu!', true); return; }
            const r = await fetch('/api/settings/telegram/chat_id/upsert', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ chat_id: cid, label: lbl })
            });
            const d = await r.json();
            showToast(d.message || 'Disimpan!', d.status !== 'success');
            if (d.status === 'success') {
                document.getElementById('setTgNewChatId').value = '';
                document.getElementById('setTgNewLabel').value = '';
                loadSettingsTelegram();
            }
        }

        async function deleteTgChatId(cid) {
            if (!confirm(`Hapus Chat ID ${cid} dari akses bot Telegram?`)) return;
            const r = await fetch('/api/settings/telegram/chat_id/delete', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ chat_id: cid })
            });
            const d = await r.json();
            showToast(d.message || 'Dihapus!', d.status !== 'success');
            loadSettingsTelegram();
        }

        async function testTgMessage(cid = null) {
            showToast('📨 Mengirim pesan tes ke Telegram...');
            const r = await fetch('/api/settings/telegram/test', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ chat_id: cid || undefined })
            });
            const d = await r.json();
            showToast(d.message || 'Selesai!', d.status !== 'success');
        }

        // --- TAB 2: CAST DEVICES & TV REMOTE CRUD ---
        let setCastCache = [];
        async function loadSettingsCast() {
            try {
                const r = await fetch('/api/settings/cast');
                const d = await r.json();
                setCastCache = d.devices || [];
                const tv = d.tv_remote || {};
                document.getElementById('setTvRemoteHost').value = tv.host || '';
                document.getElementById('setTvPairedAt').textContent = tv.paired_at ? `Terhubung sejak: ${tv.paired_at}` : 'Belum ada data pairing';
                const listEl = document.getElementById('setCastList');
                if (setCastCache.length === 0) {
                    listEl.innerHTML = '<div style="font-size:0.8rem;color:var(--text-dim);">Belum ada perangkat Cast tersimpan.</div>';
                } else {
                    listEl.innerHTML = setCastCache.map(dev => `
                        <div class="set-item-row">
                            <div>
                                <strong>${dev.cast_type === 'cast' ? '📺' : '📢'} ${escapeHtml(dev.name)}</strong>
                                <div style="font-size:0.75rem;color:var(--text-dim);">${escapeHtml(dev.host)}:${dev.port || 8009} • ${escapeHtml(dev.model || 'Google Cast')}</div>
                            </div>
                            <div style="display:flex;gap:5px;">
                                <button type="button" class="btn" style="padding:4px 8px;font-size:0.75rem;" onclick="editCastDev('${escapeHtml(dev.host)}')">✏️</button>
                                <button type="button" class="btn" style="padding:4px 8px;font-size:0.75rem;color:#f87171;" onclick="deleteCastDev('${escapeHtml(dev.host)}')">🗑️</button>
                            </div>
                        </div>`).join('');
                }
            } catch (e) { showToast('Gagal memuat perangkat Cast.', true); }
        }

        function editCastDev(host) {
            const d = setCastCache.find(x => x.host === host);
            if (!d) return;
            document.getElementById('setCastOldHost').value = d.host;
            document.getElementById('setCastName').value = d.name || '';
            document.getElementById('setCastHost').value = d.host || '';
            document.getElementById('setCastModel').value = d.model || 'Google Cast';
            document.getElementById('setCastType').value = d.cast_type || 'audio';
        }

        async function saveCastDev() {
            const payload = {
                old_host: document.getElementById('setCastOldHost').value || undefined,
                name: (document.getElementById('setCastName').value || '').trim(),
                host: (document.getElementById('setCastHost').value || '').trim(),
                model: (document.getElementById('setCastModel').value || 'Google Cast').trim(),
                cast_type: document.getElementById('setCastType').value || 'audio'
            };
            const r = await fetch('/api/settings/cast/upsert', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const d = await r.json();
            showToast(d.message || 'Disimpan!', d.status !== 'success');
            if (d.status === 'success') {
                document.getElementById('setCastOldHost').value = '';
                document.getElementById('setCastName').value = '';
                document.getElementById('setCastHost').value = '';
                loadSettingsCast();
                if (typeof loadCastDevices === 'function') loadCastDevices(false);
            }
        }

        async function deleteCastDev(host) {
            if (!confirm(`Hapus perangkat Cast ${host}?`)) return;
            const r = await fetch('/api/settings/cast/delete', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ host })
            });
            const d = await r.json();
            showToast(d.message || 'Dihapus!', d.status !== 'success');
            loadSettingsCast();
            if (typeof loadCastDevices === 'function') loadCastDevices(false);
        }

        async function scanCastFromSettings() {
            showToast('🔍 Memindai perangkat Cast di Wi-Fi...');
            await fetch('/api/cast/devices?refresh=true');
            loadSettingsCast();
            if (typeof loadCastDevices === 'function') loadCastDevices(false);
            showToast('✅ Pindai perangkat selesai!');
        }

        async function saveTvRemoteHost() {
            const host = (document.getElementById('setTvRemoteHost').value || '').trim();
            const r = await fetch('/api/settings/tv/host', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ host })
            });
            const d = await r.json();
            showToast(d.message || 'Disimpan!', d.status !== 'success');
            loadSettingsCast();
        }

        // --- TAB 3: AUDIO & QUIET HOURS ---
        async function loadSettingsPrefs() {
            try {
                const r = await fetch('/api/settings/preferences');
                const d = await r.json();
                const p = d.preferences || {};
                document.getElementById('setPrefDefVol').value = p.qa_default_volume ?? 70;
                document.getElementById('setPrefDefVolLbl').textContent = (p.qa_default_volume ?? 70) + '%';
                document.getElementById('setPrefDefRVol').value = String(p.qa_default_restore_volume ?? 50);
                document.getElementById('setPrefDefVoice').value = p.qa_default_voice || 'wanita1';
                document.getElementById('setPrefDefChime').checked = p.qa_default_chime !== false;
                document.getElementById('setPrefQuietEn').checked = !!p.quiet_hours_enabled;
                document.getElementById('setPrefQuietStart').value = p.quiet_hours_start || '22:00';
                document.getElementById('setPrefQuietEnd').value = p.quiet_hours_end || '06:00';
                document.getElementById('setPrefQuietMax').value = p.quiet_hours_max_vol ?? 40;
            } catch (e) { showToast('Gagal memuat preferensi.', true); }
        }

        async function saveSettingsPrefs() {
            const payload = {
                qa_default_volume: parseInt(document.getElementById('setPrefDefVol').value || '70', 10),
                qa_default_restore_volume: parseInt(document.getElementById('setPrefDefRVol').value || '50', 10),
                qa_default_voice: document.getElementById('setPrefDefVoice').value || 'wanita1',
                qa_default_chime: document.getElementById('setPrefDefChime').checked,
                quiet_hours_enabled: document.getElementById('setPrefQuietEn').checked,
                quiet_hours_start: document.getElementById('setPrefQuietStart').value || '22:00',
                quiet_hours_end: document.getElementById('setPrefQuietEnd').value || '06:00',
                quiet_hours_max_vol: parseInt(document.getElementById('setPrefQuietMax').value || '40', 10)
            };
            const r = await fetch('/api/settings/preferences', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const d = await r.json();
            showToast(d.message || 'Disimpan!', d.status !== 'success');
        }

        // --- TAB 4: GOOGLE ASSISTANT SDK ---
        async function loadSettingsGA() {
            try {
                const r = await fetch('/api/settings/assistant');
                const d = await r.json();
                document.getElementById('setGaStatus').innerHTML = d.is_authorized
                    ? '<span style="color:#4ade80;font-weight:700;">🟢 Terhubung & Terotorisasi</span>'
                    : '<span style="color:#fbbf24;font-weight:700;">🟡 Belum Login OAuth</span>';
                document.getElementById('setGaDevInfo').textContent = `Model: ${d.device_model_id} • Device: ${d.device_id}`;
                document.getElementById('setGaLang').value = d.ga_default_lang || '';
                document.getElementById('setGaAutoPlay').checked = d.ga_auto_play_gh !== false;
            } catch (e) { showToast('Gagal memuat status Google Assistant.', true); }
        }

        async function saveSettingsGA() {
            const r = await fetch('/api/settings/preferences', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ga_default_lang: document.getElementById('setGaLang').value || '',
                    ga_auto_play_gh: document.getElementById('setGaAutoPlay').checked
                })
            });
            const d = await r.json();
            showToast(d.message || 'Disimpan!', d.status !== 'success');
        }

        async function testGaFromSettings() {
            const cmd = (document.getElementById('setGaTestCmd').value || 'jam berapa sekarang').trim();
            showToast(`🤖 Mengirim "${cmd}" ke Google Assistant...`);
            const r = await fetch('/api/assistant/command', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ command: cmd, lang: document.getElementById('setGaLang').value || '' })
            });
            const d = await r.json();
            showToast(d.message || d.reply || 'Selesai!', d.status !== 'success');
        }

        // --- TAB 5: SSL, BACKUP & SERVICES ---
        async function loadSettingsSystem() {
            try {
                const r = await fetch('/api/settings/system');
                const d = await r.json();
                const ssl = d.ssl || {};
                document.getElementById('setSslInfo').innerHTML =
                    `Berlaku hingga: <strong>${escapeHtml(ssl.not_after || '-')}</strong><br>` +
                    `<span style="font-size:0.74rem;color:var(--text-dim);">${escapeHtml(ssl.san || '')}</span>`;
                const sv = d.services || {};
                document.getElementById('setSvcHttps').innerHTML = sv.httpsserver ? '🟢 Aktif' : '🔴 Mati';
                document.getElementById('setSvcTg').innerHTML = sv.telegrambot ? '🟢 Aktif' : '🔴 Mati';
                document.getElementById('setSvcWg').innerHTML = sv.wireguard ? '🟢 Aktif (10.66.66.4)' : '🔴 Mati';
                const bEl = document.getElementById('setBackupsList');
                const baks = d.backups || [];
                bEl.innerHTML = baks.length === 0
                    ? '<div style="font-size:0.8rem;color:var(--text-dim);">Belum ada file backup.</div>'
                    : baks.map(b => `
                        <div class="set-item-row">
                            <div style="font-size:0.78rem;">📦 <strong>${escapeHtml(b.filename)}</strong> <span style="color:var(--text-dim);">(${b.size_kb} KB)</span></div>
                            <a href="${b.download_url}" class="btn" style="padding:3px 9px;font-size:0.74rem;text-decoration:none;">⬇️ Unduh</a>
                        </div>`).join('');
            } catch (e) { showToast('Gagal memuat info sistem.', true); }
        }

        async function createBackupNow() {
            showToast('💾 Membuat backup database & konfigurasi...');
            const r = await fetch('/api/settings/backup_create', { method: 'POST' });
            const d = await r.json();
            showToast(d.message || 'Backup dibuat!', d.status !== 'success');
            loadSettingsSystem();
        }

        async function restartMacService(svc) {
            if (!confirm(`Restart service "${svc}" sekarang?`)) return;
            const r = await fetch('/api/settings/service/restart', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ service: svc })
            });
            const d = await r.json();
            showToast(d.message || 'Service di-restart!', d.status !== 'success');
            if (svc === 'httpsserver') {
                setTimeout(() => location.reload(), 2500);
            } else {
                setTimeout(() => { loadSettingsSystem(); loadSettingsTelegram(); }, 1500);
            }
        }

    </script>

    <!-- MODAL MEDIA LIGHTBOX -->
    <div class="modal-bg" id="lightboxModal" onclick="if(event.target===this) closeModal('lightboxModal')">
        <div class="modal-box" style="max-width: 850px; background: rgba(15, 23, 42, 0.96);">
            <div class="modal-head">
                <span id="lightboxTitle" style="color: var(--accent); font-weight: 600;">Media Preview</span>
                <button class="btn" onclick="closeModal('lightboxModal')" style="padding: 4px 8px;">✕</button>
            </div>
            <div class="modal-body" id="lightboxContainer" style="padding: 16px; display: flex; align-items: center; justify-content: center; min-height: 250px;">
            </div>
            <div class="modal-foot">
                <button class="btn btn-primary" id="lightboxDownloadBtn">💾 Download Berkas</button>
                <button class="btn" onclick="closeModal('lightboxModal')">Tutup</button>
            </div>
        </div>
    </div>

    <!-- MODAL VOICE COMMAND (JARVIS) -->
    <div class="modal-bg" id="voiceModal" onclick="if(event.target===this) closeModal('voiceModal')">
        <div class="modal-box" style="max-width: 480px; text-align: center;">
            <div class="modal-head">
                <span style="color: var(--accent); font-weight: 700;">🎙️ Kendali Suara AI (Bahasa Indonesia)</span>
                <button class="btn" onclick="closeModal('voiceModal')" style="padding: 4px 8px;">✕</button>
            </div>
            <div class="modal-body" style="padding: 28px 20px;">
                <div class="voice-waves active" id="voiceWaves">
                    <div class="voice-bar"></div>
                    <div class="voice-bar"></div>
                    <div class="voice-bar"></div>
                    <div class="voice-bar"></div>
                    <div class="voice-bar"></div>
                    <div class="voice-bar"></div>
                    <div class="voice-bar"></div>
                </div>
                <div id="voiceStatusText" style="font-size: 0.85rem; color: var(--accent); font-weight: 600; margin-bottom: 12px;">
                    🔴 Mendengarkan suara Anda...
                </div>
                <div id="voiceTranscript" style="font-size: 1.15rem; font-weight: bold; color: var(--text-main); min-height: 38px; margin: 10px 0;">
                    Mendengarkan... Silakan bicara...
                </div>
                <div id="voiceReplyText" style="display: none; margin-top: 16px; padding: 12px 14px; border-radius: 10px; background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.3); color: #38bdf8; font-weight: 600;">
                </div>
                <div style="margin-top: 20px; font-size: 0.78rem; color: var(--text-muted); line-height: 1.5;">
                    💡 Contoh: <em>"Buka Google Chrome"</em>, <em>"Volume 70 persen"</em>, <em>"Umumkan ke Google Home jemput anak"</em>, <em>"Mode Malam"</em>, <em>"Kunci Mac"</em>
                </div>
            </div>
            <div class="modal-foot" style="justify-content: center;">
                <button class="btn btn-primary" onclick="startListeningVoice()">🎙️ Bicara Lagi</button>
                <button class="btn" onclick="closeModal('voiceModal')">Selesai</button>
            </div>
        </div>
    </div>

    <!-- FLOATING FAB: QUICK ACTION BROADCAST -->
    <button type="button" class="qa-fab" id="qaFabBtn" onclick="openQuickActionModal()" title="Quick Action Broadcast Google Home (Vol 70%)" aria-label="Quick Action Broadcast">
        ⚡
    </button>

    <!-- MODAL QUICK ACTION MANAGEMENT (PLAY INSTAN + CRUD) -->
    <div class="modal-bg" id="quickActionModal" onclick="if(event.target===this) closeModal('quickActionModal')">
        <div class="modal-box" style="max-width: 600px;">
            <div class="modal-head">
                <span style="color: var(--accent); font-weight: 700; display: flex; align-items: center; gap: 8px;">⚡ Quick Action Broadcast</span>
                <div style="display: flex; gap: 6px; align-items: center;">
                    <button type="button" class="btn btn-primary" style="padding: 5px 10px; font-size: 0.8rem;" onclick="openQaForm()">➕ Tambah</button>
                    <button type="button" class="btn" onclick="closeModal('quickActionModal')" style="padding: 4px 9px;">✕</button>
                </div>
            </div>
            <div class="modal-body" style="padding: 16px;">
                <!-- BROADCAST INSTAN SUARA WANITA (VOL 70% + CHIME) -->
                <div class="qa-instant-box">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; gap: 8px; flex-wrap: wrap;">
                        <span style="font-size: 0.76rem; font-weight: 700; color: #7dd3fc; text-transform: uppercase; letter-spacing: 0.4px;">👩 Broadcast Instan Suara Wanita → Google Home</span>
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <label style="font-size: 0.74rem; color: #fcd34d; display: flex; align-items: center; gap: 4px; cursor: pointer; font-weight: 600;">
                                <input type="checkbox" id="qaInstantChime" checked style="accent-color: #f59e0b;"> 🔔 Bel
                            </label>
                            <label style="font-size: 0.75rem; color: #6ee7b7; display: flex; align-items: center; gap: 4px; font-weight: 700;">
                                🔊 Vol:
                                <input type="number" id="qaInstantVol" value="70" min="0" max="100" step="5" style="width: 52px; padding: 2px 5px; border-radius: 6px; border: 1px solid rgba(16,185,129,0.4); background: rgba(15,23,42,0.8); color: #6ee7b7; font-weight: 700; text-align: center;">%
                            </label>
                        </div>
                    </div>
                    <div class="input-row" style="margin-top: 0; gap: 6px;">
                        <input type="text" id="qaInstantText" class="text-input" placeholder="Ketik kalimat broadcast suara wanita..." onkeydown="if(event.key==='Enter') playInstantQuickBroadcast(false)">
                        <button type="button" class="qa-play-btn" id="qaInstantPlayBtn" onclick="playInstantQuickBroadcast(false)">▶️ Play</button>
                        <button type="button" class="btn" style="padding: 0 10px;" title="Simpan kalimat ini sebagai Quick Action" onclick="playInstantQuickBroadcast(true)">💾</button>
                    </div>
                </div>

                <!-- FORM TAMBAH / EDIT QUICK ACTION (CRUD MULTI-TIPE) -->
                <div id="qaFormPanel" class="qa-form-panel" style="display: none;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <strong id="qaFormHeading" style="color: var(--accent); font-size: 0.9rem;">➕ Tambah Quick Action Baru</strong>
                        <button type="button" class="btn" style="padding: 2px 8px; font-size: 0.78rem;" onclick="closeQaForm()">Batal</button>
                    </div>
                    <input type="hidden" id="qaEditId">
                    <div style="margin-bottom: 8px;">
                        <label style="display: block; font-size: 0.74rem; color: var(--text-dim); margin-bottom: 4px;">Tipe Quick Action:</label>
                        <select id="qaTypeInput" class="text-input" style="width: 100%;" onchange="onQaTypeChange()">
                            <option value="tts">📢 Broadcast Suara TTS (Wanita / Pria + Bel)</option>
                            <option value="assistant">🤖 Perintah Google Assistant (Lampu / Smart Home)</option>
                            <option value="audio">🎵 Putar Audio / Video dari Pustaka Media</option>
                        </select>
                    </div>
                    <div style="display: flex; gap: 8px; margin-bottom: 8px; align-items: center; flex-wrap: wrap;">
                        <input type="text" id="qaIconInput" class="text-input" value="📢" maxlength="4" style="width: 56px; text-align: center; font-size: 1.1rem; flex: 0 0 auto;">
                        <input type="text" id="qaTitleInput" class="text-input" placeholder="Judul Quick Action (cth: Waktunya Makan)" style="flex: 1; min-width: 160px;">
                    </div>
                    <div style="display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 10px;">
                        <button type="button" class="qa-icon-chip" onclick="pickQaIcon('📢')">📢</button>
                        <button type="button" class="qa-icon-chip" onclick="pickQaIcon('🍽️')">🍽️</button>
                        <button type="button" class="qa-icon-chip" onclick="pickQaIcon('🕌')">🕌</button>
                        <button type="button" class="qa-icon-chip" onclick="pickQaIcon('🏠')">🏠</button>
                        <button type="button" class="qa-icon-chip" onclick="pickQaIcon('💡')">💡</button>
                        <button type="button" class="qa-icon-chip" onclick="pickQaIcon('🎵')">🎵</button>
                        <button type="button" class="qa-icon-chip" onclick="pickQaIcon('🌙')">🌙</button>
                        <button type="button" class="qa-icon-chip" onclick="pickQaIcon('🔔')">🔔</button>
                    </div>
                    <div id="qaMsgWrap">
                        <textarea id="qaMessageInput" class="text-input" rows="2" placeholder="Tulis kalimat broadcast yang akan diucapkan Google Home..." style="width: 100%; resize: vertical; margin-bottom: 10px;"></textarea>
                    </div>
                    <div id="qaAudioWrap" style="display: none; margin-bottom: 10px;">
                        <label style="display: block; font-size: 0.74rem; color: var(--text-dim); margin-bottom: 4px;">Pilih Media dari Pustaka:</label>
                        <select id="qaAudioInput" class="text-input" style="width: 100%;">
                            <option value="">-- Pilih Audio / Video dari Pustaka --</option>
                        </select>
                    </div>
                    <div id="qaTtsOptionsWrap" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 10px; align-items: center;">
                        <div>
                            <label style="display: block; font-size: 0.74rem; color: var(--text-dim); margin-bottom: 4px;">Pilihan Suara:</label>
                            <select id="qaVoiceInput" class="text-input" style="width: 100%;">
                                <option value="wanita1" selected>👩 Wanita 1 (Gadis Neural)</option>
                                <option value="wanita2">👧 Wanita 2 (Google ID)</option>
                                <option value="pria">👨 Pria (Ardi Neural)</option>
                            </select>
                        </div>
                        <div>
                            <label style="display: block; font-size: 0.74rem; color: var(--text-dim); margin-bottom: 4px;">Pengulangan:</label>
                            <select id="qaRepeatInput" class="text-input" style="width: 100%;">
                                <option value="1">1x (Normal)</option>
                                <option value="2">🔁 Ulangi 2x</option>
                                <option value="3">🔁 Ulangi 3x</option>
                            </select>
                        </div>
                        <label style="font-size: 0.8rem; color: #fcd34d; display: flex; align-items: center; gap: 6px; cursor: pointer; padding-top: 14px; font-weight: 600;">
                            <input type="checkbox" id="qaChimeInput" checked style="accent-color: #f59e0b; width: 16px; height: 16px;"> 🔔 Bunyi Bel (Ding-Dong)
                        </label>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 10px;">
                        <div>
                            <label style="display: flex; justify-content: space-between; font-size: 0.74rem; color: var(--text-dim); margin-bottom: 4px;">
                                <span>Volume Saat Putar:</span>
                                <strong id="qaVolumeLabel" style="color: #6ee7b7;">70%</strong>
                            </label>
                            <input type="range" id="qaVolumeInput" min="10" max="100" step="5" value="70" style="width: 100%; accent-color: #10b981; margin-top: 6px;" oninput="document.getElementById('qaVolumeLabel').textContent = this.value + '%'">
                        </div>
                        <div>
                            <label style="display: block; font-size: 0.74rem; color: var(--text-dim); margin-bottom: 4px;">Volume Setelah Selesai:</label>
                            <select id="qaRestoreVolInput" class="text-input" style="width: 100%;">
                                <option value="50" selected>↩️ Kembali ke 50%</option>
                                <option value="-1">🔄 Kembali ke Volume Sebelumnya</option>
                                <option value="30">↩️ Kembali ke 30%</option>
                                <option value="40">↩️ Kembali ke 40%</option>
                                <option value="60">↩️ Kembali ke 60%</option>
                            </select>
                        </div>
                    </div>
                    <div style="margin-bottom: 12px;">
                        <label style="display: block; font-size: 0.74rem; color: var(--text-dim); margin-bottom: 4px;">Target Perangkat Cast:</label>
                        <select id="qaDeviceInput" class="text-input" style="width: 100%;">
                            <option value="">📢 Default Google Home (Otomatis)</option>
                            <option value="all">🔊 Semua Perangkat (Google Home + TV)</option>
                        </select>
                    </div>
                    <div style="display: flex; justify-content: flex-end; gap: 8px;">
                        <button type="button" class="btn" onclick="closeQaForm()">Batal</button>
                        <button type="button" class="btn btn-primary" onclick="saveQuickAction()">💾 Simpan Quick Action</button>
                    </div>
                </div>

                <!-- DAFTAR QUICK ACTION (TINGGAL PLAY) -->
                <div style="font-size: 0.76rem; font-weight: 700; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 8px;">Daftar Quick Action Tersimpan (Tinggal Klik Play):</div>
                <div id="qaListContainer" class="qa-list"></div>
            </div>
            <div class="modal-foot" style="justify-content: space-between;">
                <span style="font-size: 0.74rem; color: var(--text-dim);">💡 Volume otomatis diset saat broadcast, lalu kembali ke 50% setelah selesai.</span>
                <button type="button" class="btn" onclick="closeModal('quickActionModal')">Tutup</button>
            </div>
        </div>
    </div>

    <!-- MODAL PENGATURAN TERPUSAT (5 TAB CRUD) -->
    <div class="modal-bg" id="settingsModal" onclick="if(event.target===this) closeModal('settingsModal')">
        <div class="modal-box" style="max-width: 680px;">
            <div class="modal-head">
                <span style="color: var(--accent); font-weight: 700;">⚙️ Pengaturan Sistem & Bot (CRUD)</span>
                <button type="button" class="btn" onclick="closeModal('settingsModal')" style="padding: 4px 9px;">✕</button>
            </div>
            <div class="modal-body" style="padding: 16px; max-height: 75vh; overflow-y: auto;">
                <div class="set-tabs">
                    <button type="button" class="set-tab-btn active" data-tab="tg" onclick="switchSettingsTab('tg')">🤖 Telegram Bot</button>
                    <button type="button" class="set-tab-btn" data-tab="cast" onclick="switchSettingsTab('cast')">📢 Cast &amp; TV</button>
                    <button type="button" class="set-tab-btn" data-tab="audio" onclick="switchSettingsTab('audio')">🔊 Audio &amp; Jam Tenang</button>
                    <button type="button" class="set-tab-btn" data-tab="ga" onclick="switchSettingsTab('ga')">🧠 Assistant</button>
                    <button type="button" class="set-tab-btn" data-tab="sys" onclick="switchSettingsTab('sys')">🔐 SSL &amp; Backup</button>
                </div>

                <!-- TAB 1: TELEGRAM BOT -->
                <div class="set-pane active" id="setPane_tg">
                    <div class="set-section">
                        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
                            <div id="setTgStatus">Memeriksa status bot...</div>
                            <div style="display:flex;gap:6px;">
                                <button type="button" class="btn" style="padding:5px 10px;font-size:0.78rem;" onclick="testTgMessage()">📨 Tes Pesan</button>
                                <button type="button" class="btn btn-primary" style="padding:5px 10px;font-size:0.78rem;" onclick="restartMacService('telegrambot')">🔄 Restart Bot</button>
                            </div>
                        </div>
                    </div>
                    <div class="set-section">
                        <div class="set-section-title">🔑 Token &amp; Target Default Bot</div>
                        <div id="setTgTokenMask" style="font-size:0.76rem;color:var(--text-dim);margin-bottom:6px;"></div>
                        <div class="input-row" style="margin-top:0;margin-bottom:8px;">
                            <input type="password" id="setTgTokenInput" class="text-input" placeholder="Kosongkan jika tidak ingin mengganti Bot Token...">
                        </div>
                        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                            <select id="setTgDefaultTarget" class="text-input" style="flex:1;min-width:160px;">
                                <option value="gh">📢 Default: Google Home</option>
                                <option value="tv">📺 Default: Android TV</option>
                                <option value="mac">🔊 Default: Speaker Mac</option>
                                <option value="both">📢+🔊 Google Home + Mac</option>
                                <option value="all">🔊 Semua Perangkat</option>
                            </select>
                            <button type="button" class="btn btn-primary" onclick="saveTgBotConfig()">💾 Simpan</button>
                        </div>
                    </div>
                    <div class="set-section">
                        <div class="set-section-title">👥 Allowed Chat IDs (Whitelist Akses Bot)</div>
                        <div id="setTgUsersList" style="margin-bottom:10px;"></div>
                        <div style="display:flex;gap:6px;flex-wrap:wrap;">
                            <input type="text" id="setTgNewChatId" class="text-input" placeholder="Chat ID (angka, cth: 123456789)" style="flex:1;min-width:140px;">
                            <input type="text" id="setTgNewLabel" class="text-input" placeholder="Label Nama (cth: Subuh)" style="flex:1;min-width:120px;">
                            <button type="button" class="btn btn-primary" onclick="saveTgChatId()">➕ Simpan ID</button>
                        </div>
                    </div>
                </div>

                <!-- TAB 2: CAST DEVICES & TV REMOTE -->
                <div class="set-pane" id="setPane_cast">
                    <div class="set-section">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                            <div class="set-section-title" style="margin:0;">📢 Daftar Perangkat Google Cast</div>
                            <button type="button" class="btn" style="padding:4px 10px;font-size:0.78rem;" onclick="scanCastFromSettings()">🔍 Scan Wi-Fi</button>
                        </div>
                        <div id="setCastList" style="margin-bottom:10px;"></div>
                        <input type="hidden" id="setCastOldHost">
                        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:6px;margin-bottom:8px;">
                            <input type="text" id="setCastName" class="text-input" placeholder="Nama Perangkat">
                            <input type="text" id="setCastHost" class="text-input" placeholder="IP (cth: 192.168.0.198)">
                            <input type="text" id="setCastModel" class="text-input" placeholder="Model (Google Home)">
                            <select id="setCastType" class="text-input">
                                <option value="audio">📢 Speaker Audio</option>
                                <option value="cast">📺 Android TV / Cast</option>
                            </select>
                        </div>
                        <button type="button" class="btn btn-primary" onclick="saveCastDev()">💾 Simpan Perangkat Cast</button>
                    </div>
                    <div class="set-section">
                        <div class="set-section-title">🎮 Android TV Remote (Port 6466)</div>
                        <div id="setTvPairedAt" style="font-size:0.75rem;color:var(--text-dim);margin-bottom:6px;"></div>
                        <div class="input-row" style="margin-top:0;">
                            <input type="text" id="setTvRemoteHost" class="text-input" placeholder="IP Android TV (cth: 192.168.0.197)">
                            <button type="button" class="btn btn-primary" onclick="saveTvRemoteHost()">💾 Simpan IP TV</button>
                        </div>
                    </div>
                </div>

                <!-- TAB 3: AUDIO & QUIET HOURS -->
                <div class="set-pane" id="setPane_audio">
                    <div class="set-section">
                        <div class="set-section-title">🔊 Default Quick Action &amp; Broadcast</div>
                        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:10px;">
                            <div>
                                <label style="display:flex;justify-content:space-between;font-size:0.76rem;color:var(--text-dim);">
                                    <span>Volume Default:</span><strong id="setPrefDefVolLbl" style="color:#6ee7b7;">70%</strong>
                                </label>
                                <input type="range" id="setPrefDefVol" min="10" max="100" step="5" value="70" style="width:100%;accent-color:#10b981;margin-top:6px;" oninput="document.getElementById('setPrefDefVolLbl').textContent=this.value+'%'">
                            </div>
                            <div>
                                <label style="display:block;font-size:0.76rem;color:var(--text-dim);margin-bottom:4px;">Volume Kembali Default:</label>
                                <select id="setPrefDefRVol" class="text-input" style="width:100%;">
                                    <option value="50">↩️ 50%</option>
                                    <option value="-1">🔄 Volume Sebelumnya</option>
                                    <option value="30">↩️ 30%</option>
                                    <option value="40">↩️ 40%</option>
                                </select>
                            </div>
                            <div>
                                <label style="display:block;font-size:0.76rem;color:var(--text-dim);margin-bottom:4px;">Suara Default:</label>
                                <select id="setPrefDefVoice" class="text-input" style="width:100%;">
                                    <option value="wanita1">👩 Wanita 1 (Gadis)</option>
                                    <option value="wanita2">👧 Wanita 2 (Google ID)</option>
                                    <option value="pria">👨 Pria (Ardi)</option>
                                </select>
                            </div>
                        </div>
                        <label style="font-size:0.82rem;color:#fcd34d;display:flex;align-items:center;gap:6px;cursor:pointer;">
                            <input type="checkbox" id="setPrefDefChime" checked style="accent-color:#f59e0b;"> 🔔 Aktifkan Bunyi Bel (Ding-Dong) secara default
                        </label>
                    </div>
                    <div class="set-section">
                        <div class="set-section-title">🌙 Quiet Hours (Jam Tenang Otomatis)</div>
                        <label style="font-size:0.82rem;color:var(--text-main);display:flex;align-items:center;gap:6px;cursor:pointer;margin-bottom:10px;">
                            <input type="checkbox" id="setPrefQuietEn" style="accent-color:#38bdf8;"> Aktifkan pembatasan volume otomatis saat jam malam
                        </label>
                        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-bottom:10px;">
                            <div>
                                <label style="font-size:0.74rem;color:var(--text-dim);">Mulai:</label>
                                <input type="time" id="setPrefQuietStart" class="text-input" value="22:00" style="width:100%;">
                            </div>
                            <div>
                                <label style="font-size:0.74rem;color:var(--text-dim);">Selesai:</label>
                                <input type="time" id="setPrefQuietEnd" class="text-input" value="06:00" style="width:100%;">
                            </div>
                            <div>
                                <label style="font-size:0.74rem;color:var(--text-dim);">Maks Volume (%):</label>
                                <input type="number" id="setPrefQuietMax" class="text-input" min="10" max="80" step="5" value="40" style="width:100%;">
                            </div>
                        </div>
                        <button type="button" class="btn btn-primary" onclick="saveSettingsPrefs()">💾 Simpan Preferensi Audio</button>
                    </div>
                </div>

                <!-- TAB 4: GOOGLE ASSISTANT SDK -->
                <div class="set-pane" id="setPane_ga">
                    <div class="set-section">
                        <div class="set-section-title">🧠 Status Google Assistant SDK</div>
                        <div id="setGaStatus" style="margin-bottom:4px;">Memeriksa...</div>
                        <div id="setGaDevInfo" style="font-size:0.75rem;color:var(--text-dim);"></div>
                    </div>
                    <div class="set-section">
                        <div class="set-section-title">🌐 Bahasa &amp; Balasan Suara</div>
                        <div style="margin-bottom:8px;">
                            <label style="display:block;font-size:0.76rem;color:var(--text-dim);margin-bottom:4px;">Bahasa API:</label>
                            <select id="setGaLang" class="text-input" style="width:100%;">
                                <option value="">🇮🇩 Otomatis (Ikuti Bahasa Akun Google Anda)</option>
                                <option value="en-US">🇺🇸 English (en-US)</option>
                            </select>
                        </div>
                        <label style="font-size:0.82rem;color:var(--text-main);display:flex;align-items:center;gap:6px;cursor:pointer;margin-bottom:10px;">
                            <input type="checkbox" id="setGaAutoPlay" checked style="accent-color:#10b981;"> 🔊 Otomatis putar balasan suara di Google Home
                        </label>
                        <button type="button" class="btn btn-primary" onclick="saveSettingsGA()">💾 Simpan Pengaturan Assistant</button>
                    </div>
                    <div class="set-section">
                        <div class="set-section-title">🧪 Tes Perintah Google Assistant</div>
                        <div class="input-row" style="margin-top:0;">
                            <input type="text" id="setGaTestCmd" class="text-input" value="jam berapa sekarang" placeholder="Contoh: jam berapa sekarang">
                            <button type="button" class="btn btn-primary" onclick="testGaFromSettings()">▶️ Tes</button>
                        </div>
                    </div>
                </div>

                <!-- TAB 5: SSL, BACKUP & SERVICES -->
                <div class="set-pane" id="setPane_sys">
                    <div class="set-section">
                        <div class="set-section-title">🔐 Sertifikat SSL &amp; Root CA (Bebas Not Secure)</div>
                        <div id="setSslInfo" style="font-size:0.8rem;margin-bottom:10px;">Memuat info SSL...</div>
                        <a href="/api/ssl/root_ca" class="btn btn-primary" style="text-decoration:none;display:inline-flex;">⬇️ Unduh MacRemote-RootCA.crt</a>
                    </div>
                    <div class="set-section">
                        <div class="set-section-title">🖥️ Status &amp; Restart Service Mac (launchd)</div>
                        <div class="set-item-row">
                            <div>🌐 HTTPS Server: <strong id="setSvcHttps">-</strong></div>
                            <button type="button" class="btn" style="padding:4px 10px;font-size:0.75rem;" onclick="restartMacService('httpsserver')">🔄 Restart</button>
                        </div>
                        <div class="set-item-row">
                            <div>🤖 Telegram Bot: <strong id="setSvcTg">-</strong></div>
                            <button type="button" class="btn" style="padding:4px 10px;font-size:0.75rem;" onclick="restartMacService('telegrambot')">🔄 Restart</button>
                        </div>
                        <div class="set-item-row">
                            <div>🛡️ WireGuard VPN: <strong id="setSvcWg">-</strong></div>
                            <button type="button" class="btn" style="padding:4px 10px;font-size:0.75rem;" onclick="restartMacService('wireguard')">🔄 Pastikan Aktif</button>
                        </div>
                    </div>
                    <div class="set-section">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                            <div class="set-section-title" style="margin:0;">💾 Backup Database &amp; Konfigurasi</div>
                            <button type="button" class="btn btn-primary" style="padding:4px 10px;font-size:0.78rem;" onclick="createBackupNow()">➕ Buat Backup Sekarang</button>
                        </div>
                        <div id="setBackupsList"></div>
                    </div>
                </div>
            </div>
            <div class="modal-foot" style="justify-content: flex-end;">
                <button type="button" class="btn" onclick="closeModal('settingsModal')">Tutup</button>
            </div>
        </div>
    </div>

</body>
</html>
"""


class FullControlHTTPSServer(BaseHTTPRequestHandler):
    server_version = "MacRemoteCommandCenter/4.0"

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE"
        )
        self.send_header("Access-Control-Allow-Headers", "*")

    def _send_json(self, data, status_code=200):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        # Preview File Inline API (Lightbox: gambar, video, audio, pdf)
        if parsed.path == "/api/preview":
            rel_path = params.get("path", [""])[0]
            target_abs = get_safe_path(rel_path)
            if not os.path.isfile(target_abs):
                return self._send_json({"status": "error", "message": "File tidak ditemukan"}, 404)
            fname = os.path.basename(target_abs)
            mime_type, _ = mimetypes.guess_type(target_abs)
            mime_type = mime_type or "application/octet-stream"
            file_size = os.path.getsize(target_abs)
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Content-Disposition", f'inline; filename="{fname}"')
            self._send_cors()
            self.end_headers()
            with open(target_abs, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return

        # Live Screen Mirroring Frame (No-Cache JPEG)
        if parsed.path == "/api/remote/screen_live":
            temp_img = "/tmp/remote_mac_screen_live.jpg"
            try:
                subprocess.run(["screencapture", "-x", "-t", "jpg", temp_img], timeout=4)
                if os.path.exists(temp_img):
                    with open(temp_img, "rb") as f:
                        img_data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(img_data)))
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self._send_cors()
                    self.end_headers()
                    self.wfile.write(img_data)
                    return
            except Exception:
                pass
            return self._send_json({"status": "error", "message": "Gagal mengambil frame"}, 500)


        # 1. File Manager API: List
        if parsed.path == "/api/list":
            rel_path = params.get("path", [""])[0]
            target_abs = get_safe_path(rel_path)

            if not os.path.exists(target_abs):
                return self._send_json(
                    {"status": "error", "message": "Direktori tidak ditemukan"},
                    404,
                )
            if not os.path.isdir(target_abs):
                return self._send_json(
                    {"status": "error", "message": "Bukan direktori"}, 400
                )

            items = []
            try:
                with os.scandir(target_abs) as it:
                    for entry in it:
                        if entry.name.startswith("."):
                            continue
                        try:
                            items.append(get_file_info(entry, target_abs))
                        except Exception:
                            continue
            except PermissionError:
                return self._send_json(
                    {"status": "error", "message": "Izin akses ditolak"}, 403
                )

            return self._send_json(
                {
                    "status": "success",
                    "path": get_relative_path(target_abs),
                    "items": items,
                }
            )

        # 2. System Stats API
        if parsed.path == "/api/stats":
            return self._send_json(
                {"status": "success", "stats": get_system_stats()}
            )

        # 3. Read File API
        if parsed.path == "/api/read":
            rel_path = params.get("path", [""])[0]
            target_abs = get_safe_path(rel_path)
            try:
                with open(target_abs, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                return self._send_json({"status": "success", "content": content})
            except Exception as e:
                return self._send_json(
                    {"status": "error", "message": str(e)}, 500
                )

        # 4. Download File API
        if parsed.path == "/api/download":
            rel_path = params.get("path", [""])[0]
            target_abs = get_safe_path(rel_path)
            if not os.path.isfile(target_abs):
                self.send_error(404, "File tidak ditemukan")
                return

            fname = os.path.basename(target_abs)
            mime_type, _ = mimetypes.guess_type(target_abs)
            mime_type = mime_type or "application/octet-stream"

            file_size = os.path.getsize(target_abs)
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header(
                "Content-Disposition", f'attachment; filename="{fname}"'
            )
            self._send_cors()
            self.end_headers()
            with open(target_abs, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return

        # 5. Remote Clipboard: Get
        if parsed.path == "/api/remote/clipboard":
            try:
                proc = subprocess.run(
                    ["pbpaste"], capture_output=True, text=True, timeout=3
                )
                return self._send_json(
                    {"status": "success", "clipboard": proc.stdout}
                )
            except Exception as e:
                return self._send_json(
                    {"status": "error", "message": str(e)}, 500
                )

        # 6. Remote Screenshot API
        if parsed.path == "/api/remote/screenshot":
            fmt = params.get("format", ["jpg"])[0].lower()
            if fmt not in ["jpg", "jpeg", "png"]:
                fmt = "jpg"
            temp_img = f"/tmp/remote_mac_screenshot.{fmt}"
            try:
                subprocess.run(
                    ["screencapture", "-x", "-t", fmt, temp_img],
                    timeout=5,
                    check=True,
                )
                if os.path.exists(temp_img):
                    size = os.path.getsize(temp_img)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png" if fmt == "png" else "image/jpeg")
                    self.send_header("Content-Length", str(size))
                    self._send_cors()
                    self.end_headers()
                    with open(temp_img, "rb") as f:
                        shutil.copyfileobj(f, self.wfile)
                    return
            except Exception:
                pass
            self.send_error(500, "Gagal mengambil screenshot")
            return

        # 7. Remote TTS Audio Stream for Web Preview
        if parsed.path == "/api/remote/tts_audio":
            filename = params.get("file", [""])[0]
            clean_name = os.path.basename(filename)
            audio_path = os.path.join("/tmp", clean_name)
            if not os.path.exists(audio_path):
                self.send_error(404, "Audio tidak ditemukan")
                return
            size = os.path.getsize(audio_path)
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(size))
            self._send_cors()
            self.end_headers()
            with open(audio_path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return

        # 8. Process List API (Task Manager)
        if parsed.path == "/api/procs/list":
            return self._send_json(
                {"status": "success", "processes": get_running_processes()}
            )

        # 9. Google Cast Devices List API
        if parsed.path == "/api/cast/devices":
            force = params.get("refresh", ["false"])[0] == "true"
            return self._send_json(cast_manager.get_cast_devices(force_refresh=force))

        # 10. Voice Schedule List API
        if parsed.path == "/api/schedule/list":
            include_all = params.get("all", ["false"])[0] == "true"
            return self._send_json(
                {"status": "success", "schedules": smart_reminders.list_schedules(include_triggered=include_all)}
            )

        # 10b. Quick Actions List API
        if parsed.path == "/api/quick_actions/list":
            return self._send_json(
                {"status": "success", "quick_actions": db_manager.list_quick_actions()}
            )

        # 10e. Settings Center GET APIs (5 Tab)
        if parsed.path == "/api/settings/telegram":
            return self._send_json(settings_manager.get_telegram_settings())

        if parsed.path == "/api/settings/cast":
            return self._send_json(settings_manager.get_cast_and_tv_settings())

        if parsed.path == "/api/settings/preferences":
            return self._send_json({"status": "success", "preferences": settings_manager.get_preferences()})

        if parsed.path == "/api/settings/assistant":
            return self._send_json(settings_manager.get_ga_settings())

        if parsed.path == "/api/settings/system":
            return self._send_json(settings_manager.get_system_and_ssl_info())

        if parsed.path == "/api/settings/backup_download":
            fn = os.path.basename(params.get("file", [""])[0])
            bp = os.path.join(settings_manager.BACKUP_DIR, fn)
            if not fn.endswith(".zip") or not os.path.isfile(bp):
                self.send_error(404, "File backup tidak ditemukan")
                return
            size = os.path.getsize(bp)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{fn}"')
            self.send_header("Content-Length", str(size))
            self._send_cors()
            self.end_headers()
            with open(bp, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return

        # 10c. Download Local Root CA Certificate (agar bebas peringatan "Not Secure")
        if parsed.path == "/api/ssl/root_ca":
            ca_path = os.path.join(BASE_DIR, "mac_remote_root_ca.crt")
            if not os.path.isfile(ca_path):
                self.send_error(404, "Root CA tidak ditemukan")
                return
            with open(ca_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-x509-ca-cert")
            self.send_header("Content-Disposition", 'attachment; filename="MacRemote-RootCA.crt"')
            self.send_header("Content-Length", str(len(data)))
            self._send_cors()
            self.end_headers()
            self.wfile.write(data)
            return

        # 10d. PWA Manifest & App Icon (Install ke Home Screen HP seperti Aplikasi Asli)
        if parsed.path == "/manifest.json":
            manifest = {
                "name": "Mac Remote Command Center",
                "short_name": "Mac Remote",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#0b0f19",
                "theme_color": "#0b0f19",
                "icons": [
                    {
                        "src": "/api/pwa/icon.svg",
                        "sizes": "any",
                        "type": "image/svg+xml",
                        "purpose": "any maskable",
                    }
                ],
            }
            body = json.dumps(manifest).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._send_cors()
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/pwa/icon.svg":
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
                '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
                '<stop offset="0%" stop-color="#0284c7"/><stop offset="50%" stop-color="#38bdf8"/>'
                '<stop offset="100%" stop-color="#6366f1"/></linearGradient></defs>'
                '<rect width="512" height="512" rx="112" fill="#0b0f19"/>'
                '<rect x="36" y="36" width="440" height="440" rx="90" fill="url(#g)" opacity="0.22"/>'
                '<path d="M290 96L154 282h96l-28 134 136-186h-96l28-134z" fill="url(#g)"/>'
                '</svg>'
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(svg)))
            self._send_cors()
            self.end_headers()
            self.wfile.write(svg)
            return

        # 11. Audio Library: List Audios API
        if parsed.path == "/api/audio/list":
            return self._send_json({"status": "success", "audios": db_manager.list_audios()})

        # 11b. Audio Playback Status API (Monitor Live Mac & Semua Perangkat Cast / Android TV)
        if parsed.path == "/api/playback/status":
            return self._send_json({
                "status": "success",
                "mac": mac_player.get_mac_playback_status(),
                "cast": cast_manager.get_google_home_playback_status(),
                "casts": cast_manager.get_all_cast_playback_statuses(),
            })

        # 12. Media Library: Stream Audio/Video File (dengan dukungan Range 206 untuk seek video)
        if parsed.path == "/api/audio/file":
            aud_id = params.get("id", [""])[0]
            aud = db_manager.get_audio(aud_id)
            if not aud or not os.path.exists(aud["file_path"]):
                self.send_error(404, "Berkas media tidak ditemukan")
                return
            size = os.path.getsize(aud["file_path"])
            range_hdr = self.headers.get("Range")
            if range_hdr and range_hdr.startswith("bytes="):
                try:
                    parts = range_hdr.replace("bytes=", "").split("-")
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if len(parts) > 1 and parts[1] else size - 1
                    end = min(end, size - 1)
                    if start > end:
                        start = 0
                    length = end - start + 1
                    self.send_response(206)
                    self.send_header("Content-Type", aud["mime_type"])
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(length))
                    self.send_header("Accept-Ranges", "bytes")
                    self._send_cors()
                    self.end_headers()
                    with open(aud["file_path"], "rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(remaining, 65536))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return

            self.send_response(200)
            self.send_header("Content-Type", aud["mime_type"])
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self._send_cors()
            self.end_headers()
            try:
                with open(aud["file_path"], "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        # Default: Render Dashboard Web App
        html_bytes = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(html_bytes)

    def do_POST(self):
        parsed = urlparse(self.path)


        # 1. Upload API
        if parsed.path == "/api/upload":
            params = parse_qs(parsed.query)
            rel_path = params.get("path", [""])[0]
            target_dir = get_safe_path(rel_path)

            content_length = int(self.headers.get("Content-Length", 0))
            content_type = self.headers.get("Content-Type", "")
            raw_headers = f"Content-Type: {content_type}\r\n\r\n".encode(
                "utf-8"
            )
            raw_body = self.rfile.read(content_length)

            msg = email.message_from_bytes(
                raw_headers + raw_body, policy=email.policy.default
            )
            uploaded_names = []
            for part in msg.iter_parts():
                fname = part.get_filename()
                if fname:
                    clean_name = os.path.basename(fname)
                    payload = part.get_payload(decode=True)
                    if payload is not None:
                        file_dest = os.path.join(target_dir, clean_name)
                        with open(file_dest, "wb") as f:
                            f.write(payload)
                        uploaded_names.append(clean_name)

            return self._send_json(
                {"status": "success", "uploaded": uploaded_names}
            )

        # 17b. Google Home Client Voice Note Broadcast (Mode 3: Suara Asli Klien)
        if parsed.path == "/api/cast/voice_note":
            content_length = int(self.headers.get("Content-Length", 0))
            content_type = self.headers.get("Content-Type", "")
            raw_body = self.rfile.read(content_length)

            temp_voice = f"/tmp/client_voice_{int(time.time()*1000)}.webm"
            target_device = None

            if "multipart/form-data" in content_type:
                raw_headers = f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
                msg = email.message_from_bytes(raw_headers + raw_body, policy=email.policy.default)
                for part in msg.iter_parts():
                    cd = str(part.get("Content-Disposition", ""))
                    if 'name="device"' in cd:
                        target_device = part.get_payload().strip()
                    elif 'name="audio"' in cd or part.get_filename():
                        payload = part.get_payload(decode=True)
                        if payload:
                            with open(temp_voice, "wb") as f:
                                f.write(payload)
            else:
                with open(temp_voice, "wb") as f:
                    f.write(raw_body)

            result = cast_manager.broadcast_audio_file_to_google_home(
                temp_voice, target_device_name=target_device, title="Suara Asli Klien"
            )
            return self._send_json(result)

        # Remote Intercom: Putar Pesan Suara Langsung dari Mic Klien ke Speaker Mac
        if parsed.path == "/api/remote/play_voice":
            content_length = int(self.headers.get("Content-Length", 0))
            content_type = self.headers.get("Content-Type", "")
            raw_body = self.rfile.read(content_length) if content_length > 0 else b""

            audio_bytes = b""
            if "multipart/form-data" in content_type:
                raw_headers = f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
                msg = email.message_from_bytes(raw_headers + raw_body, policy=email.policy.default)
                for part in msg.iter_parts():
                    if 'name="audio"' in str(part.get("Content-Disposition", "")) or part.get_filename():
                        payload = part.get_payload(decode=True)
                        if payload:
                            audio_bytes = payload
                            break
            elif "application/json" in content_type:
                try:
                    js = json.loads(raw_body.decode("utf-8", errors="ignore"))
                    b64 = js.get("audio") or js.get("audio_base64", "")
                    if "," in b64:
                        b64 = b64.split(",", 1)[1]
                    import base64
                    audio_bytes = base64.b64decode(b64)
                except Exception:
                    pass
            else:
                audio_bytes = raw_body

            if not audio_bytes:
                return self._send_json({"status": "error", "message": "Tidak ada data audio yang diterima"}, 400)

            ext = ".wav"
            if audio_bytes.startswith(b"RIFF"):
                ext = ".wav"
            elif audio_bytes.startswith(b"ID3") or audio_bytes[:2] == b"\xff\xfb":
                ext = ".mp3"
            elif audio_bytes.startswith(b"\x1a\x45\xdf\xa3"):
                ext = ".webm"
            elif b"ftyp" in audio_bytes[:20]:
                ext = ".m4a"

            temp_voice = f"/tmp/mac_voice_{int(time.time()*1000)}{ext}"
            with open(temp_voice, "wb") as f:
                f.write(audio_bytes)

            # 1. Mainkan suara di speaker Mac mini menggunakan afplay
            def _play_worker(path):
                try:
                    res = mac_player.play_mac_audio(path, title="Pesan Suara Mic", source="intercom")
                    if res.get("status") != "success" and not path.endswith(".wav"):
                        wav_path = f"/tmp/converted_{int(time.time()*1000)}.wav"
                        conv = subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", path, wav_path], timeout=10)
                        if conv.returncode == 0 and os.path.exists(wav_path):
                            mac_player.play_mac_audio(wav_path, title="Pesan Suara Mic (WAV)", source="intercom")
                            try:
                                os.remove(wav_path)
                            except Exception:
                                pass
                except Exception:
                    pass
                finally:
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except Exception:
                        pass

            threading.Thread(target=_play_worker, args=(temp_voice,), daemon=True).start()

            # 2. Tampilkan notifikasi pop-up di layar Mac
            try:
                sc = 'display notification "Pesan suara baru dari Remote HP sedang berbunyi di speaker Mac!" with title "🎙️ Intercom Voice Message" sound name "Glass"'
                subprocess.run(["osascript", "-e", sc], timeout=3)
            except Exception:
                pass

            return self._send_json({"status": "success", "message": "Pesan suara berhasil diputar di speaker Mac mini!"})

        # Audio Library Upload API
        if parsed.path == "/api/audio/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            content_type = self.headers.get("Content-Type", "")
            raw_body = self.rfile.read(content_length) if content_length > 0 else b""

            audio_bytes = b""
            filename = "audio.mp3"
            label = ""
            mime_type = "audio/mpeg"

            if "multipart/form-data" in content_type:
                raw_headers = f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
                msg = email.message_from_bytes(raw_headers + raw_body, policy=email.policy.default)
                for part in msg.iter_parts():
                    cd = str(part.get("Content-Disposition", ""))
                    if 'name="label"' in cd or 'name="name"' in cd:
                        label = part.get_payload().strip()
                    elif 'name="audio"' in cd or 'name="file"' in cd or part.get_filename():
                        if part.get_filename():
                            filename = part.get_filename()
                        payload = part.get_payload(decode=True)
                        if payload:
                            audio_bytes = payload
                            part_mime = part.get_content_type()
                            if part_mime:
                                mime_type = part_mime
            else:
                audio_bytes = raw_body

            if not audio_bytes:
                return self._send_json({"status": "error", "message": "Tidak ada data audio yang diunggah"}, 400)

            clean_fname = os.path.basename(filename)
            aud = db_manager.add_audio(name=label or os.path.splitext(clean_fname)[0],
                                       original_filename=clean_fname,
                                       data_bytes=audio_bytes,
                                       mime_type=mime_type)
            return self._send_json({"status": "success", "audio": aud})


        # Parse JSON body
        content_length = int(self.headers.get("Content-Length", 0))
        body_str = (
            self.rfile.read(content_length).decode("utf-8", errors="ignore")
            if content_length > 0
            else "{}"
        )
        try:
            req_data = json.loads(body_str)
        except Exception:
            req_data = {}

        # Remote Screen Direct Click
        if parsed.path == "/api/remote/screen_click":
            w, h = get_screen_resolution()
            px = float(req_data.get("px", -1))
            py = float(req_data.get("py", -1))
            click_type = req_data.get("type", "click")
            if 0 <= px <= 1 and 0 <= py <= 1:
                x = int(px * w)
                y = int(py * h)
            else:
                x = int(req_data.get("x", w // 2))
                y = int(req_data.get("y", h // 2))
            success = remote_mouse_click(x, y, click_type)
            return self._send_json({"status": "success" if success else "error", "x": x, "y": y, "res": f"{w}x{h}"})

        # Remote Screen Keystrokes
        if parsed.path == "/api/remote/screen_key":
            key = req_data.get("key")
            text = req_data.get("text")
            success = remote_keystroke(text=text, key=key)
            return self._send_json({"status": "success" if success else "error"})

        # Web Voice Command NLP
        if parsed.path == "/api/voice_command":
            voice_text = req_data.get("text", "")
            result = process_voice_command(voice_text)
            act = result.get("action")
            if act == "volume":
                subprocess.run(["osascript", "-e", f"set volume output volume {result['value']}"], timeout=3)
            elif act == "cast":
                try:
                    res_cast = cast_manager.broadcast_to_google_home(result['message'])
                    if res_cast.get("status") != "success":
                        result['reply'] += f" (Gagal: {res_cast.get('message', '')})"
                except Exception as e:
                    result['reply'] += f" (Gagal: {e})"
            elif act == "gh_volume":
                res_v = cast_manager.set_google_home_volume(volume_percent=result.get("value"))
                if res_v.get("status") != "success":
                    result['reply'] = res_v.get("message", result['reply'])
            elif act == "gh_volume_delta":
                res_v = cast_manager.set_google_home_volume(delta_percent=result.get("delta", 10))
                result['reply'] = res_v.get("message", result['reply'])
            elif act == "gh_mute":
                res_v = cast_manager.set_google_home_volume(muted=result.get("muted", True))
                result['reply'] = res_v.get("message", result['reply'])
            elif act == "gh_stop":
                res_s = cast_manager.stop_google_home_cast()
                result['reply'] = res_s.get("message", result['reply'])
            elif act == "gh_media":
                res_m = cast_manager.control_google_home_media(result.get("op", "playpause"))
                result['reply'] = res_m.get("message", result['reply'])
            elif act == "lock":
                subprocess.run(["pmset", "displaysleepnow"], timeout=3)
            elif act == "sleep":
                subprocess.run(["osascript", "-e", 'tell application "Finder" to sleep'], timeout=3)
            elif act == "launch_app":
                subprocess.run(["open", "-a", result['app']], timeout=4)
            elif act == "music":
                op = result.get("op", "playpause")
                subprocess.run(["osascript", "-e", f'tell application "Music" to {op}'], timeout=3)
            elif act == "scenario":
                execute_scenario(result.get("preset"))
            return self._send_json({"status": "success", "result": result})

        # Smart Scenario Presets
        if parsed.path == "/api/scenario":
            preset = req_data.get("preset", "")
            res = execute_scenario(preset)
            return self._send_json(res)


        # 2. File Manager: Mkdir
        if parsed.path == "/api/mkdir":
            parent_abs = get_safe_path(req_data.get("path", ""))
            name = os.path.basename(req_data.get("name", "").strip())
            if not name:
                return self._send_json(
                    {"status": "error", "message": "Nama kosong"}, 400
                )
            target = os.path.join(parent_abs, name)
            os.makedirs(target, exist_ok=True)
            return self._send_json({"status": "success"})

        # 2.5. File Manager: Create New File
        if parsed.path == "/api/newfile":
            parent_abs = get_safe_path(req_data.get("path", ""))
            name = os.path.basename(req_data.get("name", "").strip())
            if not name:
                return self._send_json(
                    {"status": "error", "message": "Nama file tidak boleh kosong"}, 400
                )
            target = os.path.join(parent_abs, name)
            if os.path.exists(target):
                return self._send_json(
                    {"status": "error", "message": "File dengan nama tersebut sudah ada"}, 400
                )
            try:
                with open(target, "w", encoding="utf-8") as f:
                    f.write("")
                return self._send_json({"status": "success", "file": name})
            except Exception as e:
                return self._send_json({"status": "error", "message": str(e)}, 500)

        # 2.6. File Manager: Rename Item
        if parsed.path == "/api/rename":
            old_path = req_data.get("path", "").strip()
            new_name = os.path.basename(req_data.get("new_name", "").strip())
            if not old_path or not new_name:
                return self._send_json(
                    {"status": "error", "message": "Parameter tidak lengkap"}, 400
                )
            src_abs = get_safe_path(old_path)
            if src_abs == ROOT_DIR:
                return self._send_json(
                    {"status": "error", "message": "Dilarang mengubah root folder"}, 403
                )
            if not os.path.exists(src_abs):
                return self._send_json(
                    {"status": "error", "message": "File atau folder tidak ditemukan"}, 404
                )
            dst_abs = os.path.join(os.path.dirname(src_abs), new_name)
            if os.path.exists(dst_abs):
                return self._send_json(
                    {"status": "error", "message": f"Nama '{new_name}' sudah digunakan"}, 400
                )
            try:
                os.rename(src_abs, dst_abs)
                return self._send_json({"status": "success"})
            except Exception as e:
                return self._send_json({"status": "error", "message": str(e)}, 500)

        # 3. File Manager: Delete
        if parsed.path == "/api/delete":
            target_abs = get_safe_path(req_data.get("path", ""))
            if target_abs == ROOT_DIR:
                return self._send_json(
                    {"status": "error", "message": "Dilarang menghapus root"},
                    403,
                )
            if os.path.isdir(target_abs):
                shutil.rmtree(target_abs)
            elif os.path.isfile(target_abs):
                os.remove(target_abs)
            return self._send_json({"status": "success"})

        # 4. File Manager: Save Text File
        if parsed.path == "/api/save":
            target_abs = get_safe_path(req_data.get("path", ""))
            with open(target_abs, "w", encoding="utf-8") as f:
                f.write(req_data.get("content", ""))
            return self._send_json({"status": "success"})

        # 5. Remote App Launcher: Launch App
        if parsed.path == "/api/apps/launch":
            app_name = req_data.get("app", "").strip()
            if not app_name:
                return self._send_json(
                    {"status": "error", "message": "Nama aplikasi kosong"}, 400
                )
            try:
                subprocess.Popen(["open", "-a", app_name])
                return self._send_json(
                    {
                        "status": "success",
                        "message": f"Aplikasi {app_name} berhasil dijalankan di Mac!",
                    }
                )
            except Exception as e:
                return self._send_json(
                    {"status": "error", "message": str(e)}, 500
                )

        # 6. Remote App Launcher: Open URL
        if parsed.path == "/api/apps/open_url":
            url = req_data.get("url", "").strip()
            if not url:
                return self._send_json(
                    {"status": "error", "message": "URL kosong"}, 400
                )
            try:
                subprocess.Popen(["open", url])
                return self._send_json(
                    {
                        "status": "success",
                        "message": f"Link berhasil dibuka di browser Mac: {url}",
                    }
                )
            except Exception as e:
                return self._send_json(
                    {"status": "error", "message": str(e)}, 500
                )

        # 7. Web Terminal: Execute Command
        if parsed.path == "/api/terminal/exec":
            cmd = req_data.get("command", "").strip()
            if not cmd:
                return self._send_json({"status": "success", "output": ""})
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    executable="/bin/zsh",
                    capture_output=True,
                    text=True,
                    timeout=20,
                    cwd=ROOT_DIR,
                )
                output = proc.stdout + proc.stderr
                return self._send_json(
                    {
                        "status": "success",
                        "output": output or "(perintah selesai tanpa output)",
                    }
                )
            except subprocess.TimeoutExpired:
                return self._send_json(
                    {"status": "error", "output": "[Timeout] Waktu eksekusi habis (maks 20 detik)."}
                )
            except Exception as e:
                return self._send_json({"status": "error", "output": str(e)})

        # 8. Remote Control: Volume
        if parsed.path == "/api/remote/volume":
            vol = int(req_data.get("volume", 50))
            vol = max(0, min(100, vol))
            try:
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        f"set volume output volume {vol}",
                    ],
                    timeout=3,
                )
                return self._send_json(
                    {"status": "success", "message": f"Volume diset ke {vol}%"}
                )
            except Exception as e:
                return self._send_json(
                    {"status": "error", "message": str(e)}, 500
                )

        # 9. Remote Control: Text-to-Speech Realistis Bahasa Indonesia
        if parsed.path == "/api/remote/say":
            text = req_data.get("text", "").strip()
            voice = req_data.get("voice", "female")
            mode = req_data.get("mode", "speaker")
            if not text:
                return self._send_json(
                    {"status": "error", "message": "Teks kosong"}, 400
                )

            if mode == "preview":
                audio_path = generate_indonesian_audio(text, voice)
                if audio_path and os.path.exists(audio_path):
                    audio_filename = os.path.basename(audio_path)
                    return self._send_json({
                        "status": "success",
                        "audio_url": f"/api/remote/tts_audio?file={audio_filename}"
                    })
                return self._send_json(
                    {"status": "error", "message": "Gagal menghasilkan audio"}, 500
                )
            else:
                speak_indonesian(text, voice)
                return self._send_json({
                    "status": "success",
                    "message": f'Mac mengucapkan: "{text}"'
                })

        # 10. Remote Control: Set Clipboard
        if parsed.path == "/api/remote/clipboard":
            text = req_data.get("text", "")
            try:
                p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                p.communicate(text.encode("utf-8"))
                return self._send_json({"status": "success"})
            except Exception as e:
                return self._send_json(
                    {"status": "error", "message": str(e)}, 500
                )

        # 11. Remote Control: Sleep Display
        if parsed.path == "/api/remote/sleep_display":
            try:
                subprocess.run(["pmset", "displaysleepnow"], timeout=3)
                return self._send_json(
                    {"status": "success", "message": "Layar Mac dimatikan"}
                )
            except Exception as e:
                return self._send_json(
                    {"status": "error", "message": str(e)}, 500
                )

        # 12. Task Manager: Kill Process
        if parsed.path == "/api/procs/kill":
            pid_val = req_data.get("pid")
            try:
                os.kill(int(pid_val), 15)  # SIGTERM
                return self._send_json(
                    {"status": "success", "message": f"Proses PID {pid_val} berhasil dihentikan"}
                )
            except Exception as e:
                return self._send_json(
                    {"status": "error", "message": str(e)}, 500
                )

        # 13. Remote Desktop Banner Notification
        if parsed.path == "/api/remote/notify":
            title = req_data.get("title", "Pesan dari HP")
            msg = req_data.get("message", "")
            if not msg:
                return self._send_json(
                    {"status": "error", "message": "Pesan kosong"}, 400
                )
            send_desktop_notification(title, msg)
            return self._send_json(
                {"status": "success", "message": "Banner notifikasi muncul di monitor Mac!"}
            )

        # 13.5. Remote Interactive Pop-up Dialog with Direct Reply
        if parsed.path == "/api/remote/ask":
            sender = req_data.get("sender", "Remote HP")
            msg = req_data.get("message", "").strip()
            timeout = int(req_data.get("timeout", 120))
            if not msg:
                return self._send_json(
                    {"status": "error", "message": "Pesan kosong"}, 400
                )
            status, reply = interactive_popup.show_interactive_popup(
                sender, msg, timeout_seconds=timeout
            )
            return self._send_json({
                "status": "success",
                "result": status,
                "reply": reply,
            })

        # 14. Media & Music Control
        if parsed.path == "/api/remote/music":
            act = req_data.get("action", "playpause")
            res = control_music(act)
            return self._send_json({"status": "success", "output": res})

        # 15. File Manager: Compress ZIP
        if parsed.path == "/api/zip":
            target_abs = get_safe_path(req_data.get("path", ""))
            out = zip_target(target_abs)
            if out:
                return self._send_json(
                    {"status": "success", "zip_file": os.path.basename(out)}
                )
            return self._send_json(
                {"status": "error", "message": "Gagal membuat file ZIP"}, 500
            )

        # 16. File Manager: Extract ZIP
        if parsed.path == "/api/unzip":
            target_abs = get_safe_path(req_data.get("path", ""))
            dest = extract_zip(target_abs)
            if dest:
                return self._send_json(
                    {"status": "success", "extracted_to": os.path.basename(dest)}
                )
            return self._send_json(
                {"status": "error", "message": "Gagal ekstrak file ZIP"}, 500
            )

        # 17. Google Home / Cast Broadcast API (Wanita 1, Pria, Wanita 2, Voice Note)
        if parsed.path == "/api/cast/broadcast":
            text = (req_data.get("text") or req_data.get("message", "")).strip()
            dev = req_data.get("device", "")
            mode = req_data.get("mode", "google")
            voice = req_data.get("voice_type") or req_data.get("voice", "wanita1")
            result = cast_manager.broadcast_to_google_home(
                text, target_device_name=dev, voice_mode=mode, voice_type=voice
            )
            return self._send_json(result)

        # 18. Voice Schedule API: Add Schedule (Mendukung TTS & Audio Library)
        if parsed.path == "/api/schedule/add":
            msg = (req_data.get("message") or req_data.get("text", "")).strip()
            time_str = (req_data.get("time") or req_data.get("time_str", "")).strip()
            target = req_data.get("target", "both")
            voice = req_data.get("voice_type") or req_data.get("voice", "wanita1")
            label = req_data.get("label", "").strip()
            mode = req_data.get("mode", "tts")
            audio_id = req_data.get("audio_id")
            is_daily = bool(req_data.get("is_daily", False))
            exact_dt = req_data.get("datetime") or req_data.get("exact_datetime")
            exact_ts = req_data.get("timestamp") or req_data.get("exact_timestamp")
            vol = req_data.get("volume", 70)
            rvol = req_data.get("restore_volume", 50)

            result = smart_reminders.add_schedule(
                message=msg,
                time_str=time_str,
                target=target,
                voice_type=voice,
                label=label,
                is_daily=is_daily,
                mode=mode,
                audio_id=audio_id,
                exact_datetime=exact_dt,
                exact_timestamp=exact_ts,
                volume=vol,
                restore_volume=rvol
            )
            return self._send_json(result)

        # 18b. Voice Schedule API: Update Schedule
        if parsed.path == "/api/schedule/update":
            sched_id = req_data.get("id", "")
            fields = {k: v for k, v in req_data.items() if k != "id"}
            updated = smart_reminders.update_schedule(sched_id, **fields)
            if updated:
                return self._send_json({"status": "success", "schedule": updated})
            return self._send_json({"status": "error", "message": "Gagal memperbarui jadwal"}, 400)

        # 19. Voice Schedule API: Delete Schedule
        if parsed.path == "/api/schedule/delete":
            sched_id = req_data.get("id", "")
            ok = smart_reminders.delete_schedule(sched_id)
            return self._send_json(
                {"status": "success" if ok else "error", "message": "Jadwal berhasil dihapus" if ok else "Jadwal tidak ditemukan"}
            )

        # 20. Voice Schedule API: Trigger Now (Test Sound)
        if parsed.path == "/api/schedule/trigger_now":
            sched_id = req_data.get("id", "")
            result = smart_reminders.trigger_schedule_now(sched_id)
            return self._send_json(result)

        # 21. Audio Library: Delete Audio
        if parsed.path == "/api/audio/delete":
            aud_id = req_data.get("id", "")
            ok = db_manager.delete_audio(aud_id)
            return self._send_json({
                "status": "success" if ok else "error",
                "message": "File audio berhasil dihapus" if ok else "File audio tidak ditemukan"
            })

        # 22. Media Library: Play Audio/Video Immediately (Cast TV / Google Home / Mac)
        if parsed.path == "/api/audio/play":
            aud_id = req_data.get("id", "")
            target = req_data.get("target", "both")
            device = req_data.get("device", "")
            aud = db_manager.get_audio(aud_id)
            if not aud or not os.path.exists(aud["file_path"]):
                return self._send_json({"status": "error", "message": "File media tidak ditemukan"}, 404)

            is_video = (aud.get("media_type") == "video") or str(aud.get("mime_type", "")).startswith("video/")

            # Putar di Mac mini
            if target in ["say", "mac", "both"]:
                if is_video:
                    try:
                        subprocess.Popen(["open", aud["file_path"]])
                    except Exception:
                        pass
                else:
                    mac_player.play_mac_audio(
                        aud["file_path"],
                        title=aud["name"],
                        duration=aud.get("duration_seconds", 0),
                        source="audio_library",
                    )

            # Siarkan ke Google Cast / Android TV
            if target in ["cast", "gh", "both"]:
                if is_video:
                    threading.Thread(
                        target=lambda: cast_manager.cast_media_file_to_device(
                            aud["file_path"],
                            target_device_name=device or "tv",
                            title=aud["name"],
                        ),
                        daemon=True,
                    ).start()
                else:
                    threading.Thread(
                        target=lambda: cast_manager.broadcast_audio_file_to_google_home(
                            aud["file_path"],
                            target_device_name=device,
                            title=aud["name"],
                            duration=aud.get("duration_seconds", 0),
                        ),
                        daemon=True,
                    ).start()

            target_disp = (
                "Google Home (Audio)" if (device == "192.168.0.198" and is_video)
                else ("TV Stick Mi" if (device == "tv" or (is_video and target == "cast"))
                      else ("Google Cast" if target == "cast" else ("Mac mini" if target == "say" else "Mac & Cast")))
            )
            return self._send_json({"status": "success", "message": f"Memutar '{aud['name']}' di {target_disp}!"})

        # 23. Stop Audio Playback (Mac & Google Home)
        if parsed.path == "/api/playback/stop":
            target = req_data.get("target", "all")
            msgs = []
            ok = True
            if target in ["mac", "all", "both"]:
                mac_player.stop_mac_audio()
                msgs.append("Speaker Mac dihentikan.")
            if target in ["cast", "gh", "all", "both"]:
                gh_res = cast_manager.stop_google_home_cast(req_data.get("device"))
                msgs.append(gh_res.get("message", "Google Home"))
                if gh_res.get("status") != "success":
                    ok = False
            return self._send_json({
                "status": "success" if ok else "error",
                "message": " ".join(msgs) if msgs else "Tidak ada target.",
            })

        # 24. Google Home Volume & Mute Control
        if parsed.path == "/api/cast/volume":
            vol = req_data.get("volume")
            delta = req_data.get("delta")
            muted = req_data.get("muted")
            dev = req_data.get("device")
            res = cast_manager.set_google_home_volume(
                volume_percent=vol,
                delta_percent=delta,
                muted=muted,
                target_device_name=dev,
            )
            return self._send_json(res)

        # 25. Google Home Media Playback Control (Pause / Play / PlayPause / Stop)
        if parsed.path == "/api/cast/media":
            act = req_data.get("action", "playpause")
            dev = req_data.get("device")
            res = cast_manager.control_google_home_media(action=act, target_device_name=dev)
            return self._send_json(res)

        # 26. Cast File Video / Gambar / Audio dari File Manager ke Android TV / Google Home
        if parsed.path == "/api/cast/media_file":
            rel_path = (req_data.get("path") or "").strip()
            dev = req_data.get("device")
            if not rel_path:
                return self._send_json({"status": "error", "message": "Path file tidak boleh kosong"}, 400)
            target_abs = get_safe_path(rel_path)
            if not os.path.isfile(target_abs):
                return self._send_json({"status": "error", "message": "File tidak ditemukan"}, 404)
            res = cast_manager.cast_media_file_to_device(target_abs, target_device_name=dev or None)
            return self._send_json(res)

        # 27. Universal Cast URL (YouTube / Shorts / HLS .m3u8 / MP4 / MP3 / Gambar)
        if parsed.path == "/api/cast/url":
            url = (req_data.get("url") or "").strip()
            dev = req_data.get("device")
            title = req_data.get("title")
            queue = bool(req_data.get("queue", False))
            res = cast_manager.cast_url_to_device(url, target_device_name=dev or None, title=title, queue=queue)
            return self._send_json(res)

        # 28. Perintah Google Assistant (SDK langsung + putar balasan suara di Google Home & Browser)
        if parsed.path == "/api/remote/ask_gh":
            cmd_txt = (req_data.get("command") or req_data.get("text") or "").strip()
            dev = req_data.get("device") or None
            play_on_gh = req_data.get("play_on_gh", True)
            if google_assistant_client.get_status().get("is_authorized"):
                res = google_assistant_client.send_text_command_safe(cmd_txt)
                if res.get("status") == "success":
                    reply_msg = res.get("reply") or f'Perintah "{cmd_txt}" berhasil dikirim ke Google Assistant!'
                    audio_p = res.get("audio_path")
                    if audio_p and os.path.isfile(audio_p):
                        res["audio_url"] = f"/api/remote/tts_audio?file={os.path.basename(audio_p)}"
                        if play_on_gh:
                            threading.Thread(
                                target=cast_manager.broadcast_audio_file_to_google_home,
                                args=(audio_p,),
                                kwargs={"target_device_name": dev, "title": f"Google Assistant: {cmd_txt[:40]}"},
                                daemon=True,
                            ).start()
                            reply_msg = f'{reply_msg} (🔊 Balasan diputar di Google Home)'
                    res["message"] = reply_msg
                return self._send_json(res)
            res = mac_player.ask_google_home_via_mac(cmd_txt)
            return self._send_json(res)

        # 29. Google Assistant SDK Direct Text Command API
        if parsed.path == "/api/assistant/command":
            cmd_txt = (req_data.get("command") or req_data.get("text") or "").strip()
            lang = req_data.get("lang", "")
            dev = req_data.get("device") or None
            play_on_gh = req_data.get("play_on_gh", True)
            res = google_assistant_client.send_text_command_safe(cmd_txt, language_code=lang)
            if res.get("status") == "success":
                reply_msg = res.get("reply") or f'Perintah "{cmd_txt}" berhasil dikirim ke Google Assistant!'
                audio_p = res.get("audio_path")
                if audio_p and os.path.isfile(audio_p):
                    res["audio_url"] = f"/api/remote/tts_audio?file={os.path.basename(audio_p)}"
                    if play_on_gh:
                        threading.Thread(
                            target=cast_manager.broadcast_audio_file_to_google_home,
                            args=(audio_p,),
                            kwargs={"target_device_name": dev, "title": f"Google Assistant: {cmd_txt[:40]}"},
                            daemon=True,
                        ).start()
                        reply_msg = f'{reply_msg} (🔊 Balasan diputar di Google Home)'
                res["message"] = reply_msg
            return self._send_json(res)

        # 30. Quick Action Management (CRUD + Play Multi-Tipe: TTS+Chime+Repeat / Assistant / Audio)
        if parsed.path == "/api/quick_actions/add":
            res = db_manager.add_quick_action(
                title=req_data.get("title", ""),
                message=req_data.get("message", ""),
                voice_type=req_data.get("voice_type", "wanita1"),
                volume=req_data.get("volume", 70),
                target_device=req_data.get("target_device", ""),
                icon=req_data.get("icon", "📢"),
                action_type=req_data.get("action_type", "tts"),
                audio_id=req_data.get("audio_id", ""),
                restore_volume=req_data.get("restore_volume", 50),
                use_chime=req_data.get("use_chime", True),
                repeat_count=req_data.get("repeat_count", 1),
            )
            return self._send_json(res, 200 if res.get("status") == "success" else 400)

        if parsed.path == "/api/quick_actions/update":
            qa_id = (req_data.get("id") or "").strip()
            if not qa_id:
                return self._send_json({"status": "error", "message": "ID Quick Action wajib diisi."}, 400)
            res = db_manager.update_quick_action(
                qa_id=qa_id,
                title=req_data.get("title"),
                message=req_data.get("message"),
                voice_type=req_data.get("voice_type"),
                volume=req_data.get("volume"),
                target_device=req_data.get("target_device"),
                icon=req_data.get("icon"),
                action_type=req_data.get("action_type"),
                audio_id=req_data.get("audio_id"),
                restore_volume=req_data.get("restore_volume"),
                use_chime=req_data.get("use_chime"),
                repeat_count=req_data.get("repeat_count"),
            )
            return self._send_json(res, 200 if res.get("status") == "success" else 400)

        if parsed.path == "/api/quick_actions/delete":
            qa_id = (req_data.get("id") or "").strip()
            if not qa_id:
                return self._send_json({"status": "error", "message": "ID Quick Action wajib diisi."}, 400)
            res = db_manager.delete_quick_action(qa_id)
            return self._send_json(res, 200 if res.get("status") == "success" else 404)

        if parsed.path == "/api/quick_actions/play":
            qa_id = (req_data.get("id") or "").strip()
            res = quick_action_runner.execute_quick_action(
                qa_id=qa_id or None,
                adhoc=None if qa_id else req_data,
            )
            return self._send_json(res, 200 if res.get("status") == "success" else 400)

        # 31. Settings Center POST APIs (CRUD Telegram, Cast, TV, Preferences, Backup, Restart Service)
        if parsed.path == "/api/settings/telegram/save":
            res = settings_manager.save_telegram_token(
                new_token=req_data.get("token"),
                default_target=req_data.get("default_target"),
            )
            return self._send_json(res)

        if parsed.path == "/api/settings/telegram/chat_id/upsert":
            res = settings_manager.upsert_telegram_chat_id(
                chat_id=req_data.get("chat_id"),
                label=req_data.get("label", ""),
            )
            return self._send_json(res, 200 if res.get("status") == "success" else 400)

        if parsed.path == "/api/settings/telegram/chat_id/delete":
            res = settings_manager.delete_telegram_chat_id(req_data.get("chat_id"))
            return self._send_json(res, 200 if res.get("status") == "success" else 400)

        if parsed.path == "/api/settings/telegram/test":
            res = settings_manager.send_telegram_test_message(
                chat_id=req_data.get("chat_id"),
                text=req_data.get("text"),
            )
            return self._send_json(res, 200 if res.get("status") == "success" else 400)

        if parsed.path == "/api/settings/cast/upsert":
            res = settings_manager.upsert_cast_device(
                host=req_data.get("host"),
                name=req_data.get("name"),
                model=req_data.get("model", "Google Cast"),
                port=req_data.get("port", 8009),
                cast_type=req_data.get("cast_type", "audio"),
                old_host=req_data.get("old_host"),
            )
            return self._send_json(res, 200 if res.get("status") == "success" else 400)

        if parsed.path == "/api/settings/cast/delete":
            res = settings_manager.delete_cast_device(req_data.get("host"))
            return self._send_json(res, 200 if res.get("status") == "success" else 400)

        if parsed.path == "/api/settings/tv/host":
            res = settings_manager.save_tv_remote_host(req_data.get("host"))
            return self._send_json(res, 200 if res.get("status") == "success" else 400)

        if parsed.path == "/api/settings/preferences":
            res = settings_manager.save_preferences(req_data)
            return self._send_json(res, 200 if res.get("status") == "success" else 400)

        if parsed.path == "/api/settings/backup_create":
            res = settings_manager.create_backup_zip()
            return self._send_json(res, 200 if res.get("status") == "success" else 500)

        if parsed.path == "/api/settings/service/restart":
            res = settings_manager.restart_service(req_data.get("service"))
            return self._send_json(res, 200 if res.get("status") == "success" else 400)



        return self._send_json(
            {"status": "error", "message": "Endpoint tidak ditemukan"}, 404
        )

    def log_message(self, format, *args):
        try:
            msg = format % args
            client_ip = self.client_address[0] if getattr(self, "client_address", None) else "-"
            sys.stderr.write(f'[{datetime.now().strftime("%H:%M:%S")}] {client_ip} {msg}\n')
        except Exception:
            pass
            pass
            pass

def main():
    server_address = (HOST, PORT)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(server_address, FullControlHTTPSServer)

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
    httpd.socket = ssl_context.wrap_socket(httpd.socket, server_side=True)

    print("=" * 65)
    print("🚀  MAC REMOTE COMMAND CENTER & KODBOX AKTIF")
    print("=" * 65)
    print(f"Port Server : {PORT}")
    print(f"Root Folder : {ROOT_DIR}")
    print(f"WireGuard   : https://10.66.66.4:{PORT}/")
    print("=" * 65)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Server dimatikan.")
        httpd.server_close()


if __name__ == "__main__":
    main()
