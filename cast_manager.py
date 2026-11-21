#!/usr/bin/env python3
"""
Google Cast / Google Home Controller & Announcer.
Mendeteksi speaker Google Home / Nest / Chromecast di jaringan lokal (LAN)
dan menyiarkan pesan suara Bahasa Indonesia melalui speaker tersebut.
Menggunakan multi-strategy scanner: Eureka HTTP scan + pychromecast known_hosts.
"""

import concurrent.futures
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import indonesian_tts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAST_DEVICES_CACHE_FILE = os.path.join(BASE_DIR, "cast_devices.json")
CAST_ART_MUSIC_FILE = os.path.join(BASE_DIR, "cast_art_music.jpg")
CAST_ART_TTS_FILE = os.path.join(BASE_DIR, "cast_art_tts.jpg")
CAST_ART_COVER_TMP = "/tmp/cast_cover_current.img"
_active_cover_mime = "image/jpeg"
CAST_HTTP_PORT = 8080
CURRENT_CAST_AUDIO = "/tmp/cast_broadcast.mp3"
_active_cast_file = None
_http_server_started = False
_cached_devices = []
_last_scan_time = 0

_cast_playback_lock = threading.Lock()
_last_status_query_time = 0
_active_cast_playbacks = {}  # host -> status dict
_bg_connecting_hosts = set()
_active_cast_playback = {
    "is_playing": False,
    "app_id": None,
    "title": "",
    "device": "Family Room speaker",
    "host": "192.168.0.198",
    "started_at": 0.0,
    "duration": 0.0,
    "elapsed_seconds": 0.0,
    "volume_level": None,
    "volume_muted": False,
    "updated_at": 0.0,
}


def get_cast_host_tuple(target_device_name=None):
    """
    Mengembalikan 5-tuple yang dibutuhkan oleh pychromecast.get_chromecast_from_host:
    (ip_address, port, uuid, model_name, friendly_name)
    Memungkinkan koneksi kilat (0.2 detik) tanpa perlu scanning mDNS.
    """
    devs = load_cached_devices()
    target_host = None
    target_name = "Family Room speaker"
    target_uuid = "b9925ea8-4f94-eda8-29d7-93601dec25ae"
    target_model = "Google Home"

    if target_device_name:
        t_clean = target_device_name.strip()
        t_low = t_clean.lower()
        if t_low in ("tv", "android tv", "androidtv", "mi tv", "tv stick"):
            for d in devs:
                if d.get("cast_type") == "cast" or "tv" in d.get("name", "").lower() or "tv" in d.get("model", "").lower():
                    target_host = d["host"]
                    target_name = d.get("name", "Android TV")
                    target_uuid = d.get("uuid", target_uuid)
                    target_model = d.get("model", target_model)
                    break
        if not target_host:
            for d in devs:
                if d.get("name", "").lower() == t_low or d.get("host") == t_clean:
                    target_host = d["host"]
                    target_name = d.get("name", "Google Home")
                    target_uuid = d.get("uuid", target_uuid)
                    target_model = d.get("model", target_model)
                    break
        if not target_host and t_clean.replace(".", "").isdigit() and len(t_clean.split(".")) == 4:
            target_host = t_clean
            target_name = f"Speaker ({target_host})"

    if not target_host:
        for d in devs:
            if d.get("host") == "192.168.0.198":
                target_host = d["host"]
                target_name = d.get("name", "Family Room speaker")
                target_uuid = d.get("uuid", target_uuid)
                target_model = d.get("model", target_model)
                break
        if not target_host and devs:
            target_host = devs[0]["host"]
            target_name = devs[0].get("name", "Google Home")
            target_uuid = devs[0].get("uuid", target_uuid)
            target_model = devs[0].get("model", target_model)

    if not target_host:
        target_host = "192.168.0.198"
        target_name = "Family Room speaker"

    return (target_host, 8009, target_uuid, target_model, target_name)


def get_lan_ip(target_host=None):
    """Mendeteksi IP LAN lokal Mac yang berada di subnet/rute yang sama dengan target_host (mendukung multi-interface en0 192.168.0.x & en1 192.168.1.x)."""
    if target_host:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target_host, 8009))
            local_ip = s.getsockname()[0]
            s.close()
            if local_ip and not local_ip.startswith("127."):
                return local_ip
        except Exception:
            pass

    lan_candidates = []
    try:
        proc = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=2)
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet ") and not line.startswith("inet 127."):
                parts = line.split()
                if len(parts) >= 2:
                    ip = parts[1]
                    # Abaikan interface loopback atau WireGuard (10.66.x.x)
                    if ip.startswith("192.168.") or (
                        ip.startswith("10.") and not ip.startswith("10.66.")
                    ):
                        lan_candidates.append(ip)
    except Exception:
        pass

    if lan_candidates:
        # Preferensi IP yang se-subnet dengan speaker (biasanya 192.168.0.x)
        for ip in lan_candidates:
            if ip.startswith("192.168.0."):
                return ip
        return lan_candidates[0]

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.0.200"



def get_cast_mime_type(file_path=None):
    """Mendeteksi tipe konten audio, video, dan gambar secara akurat untuk Google Cast & Android TV."""
    target = file_path or _active_cast_file or CURRENT_CAST_AUDIO
    if not target or not os.path.exists(target):
        return "audio/mpeg"

    ext = os.path.splitext(target)[1].lower()
    # Prioritaskan ekstensi video & gambar agar MP4 video tidak terdeteksi sebagai audio/mp4
    media_ext_map = {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mov": "video/mp4",
        ".mkv": "video/x-matroska",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    if ext in media_ext_map:
        return media_ext_map[ext]

    try:
        with open(target, "rb") as f:
            header = f.read(32)

        if header.startswith(b"RIFF") and b"WAVE" in header[:16]:
            return "audio/wav"
        if header.startswith(b"\x1a\x45\xdf\xa3"):  # Matroska / WebM
            return "video/webm" if ext == ".webm" else "audio/webm"
        if header.startswith(b"OggS"):
            return "audio/ogg"
        if len(header) >= 8 and (header[4:8] == b"ftyp" or b"ftyp" in header[:16]):
            return "audio/mp4"
        if header.startswith(b"ID3") or (
            len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        ):
            return "audio/mpeg"
        if header.startswith(b"fLaC"):
            return "audio/flac"
    except Exception:
        pass

    ext_map = {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".wav": "audio/wav",
        ".wave": "audio/wav",
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".opus": "audio/ogg",
        ".webm": "video/webm",
        ".flac": "audio/flac",
    }
    return ext_map.get(ext, "audio/mpeg")


def _extract_mp3_cover_art(file_path):
    """Mengekstrak cover art (ID3v2 APIC) dari file MP3 tanpa pustaka eksternal."""
    global _active_cover_mime
    if not file_path or not os.path.isfile(file_path) or not str(file_path).lower().endswith(".mp3"):
        return None
    try:
        with open(file_path, "rb") as f:
            hdr = f.read(10)
            if len(hdr) < 10 or hdr[:3] != b"ID3":
                return None
            ver = hdr[3]
            tag_size = ((hdr[6] & 0x7F) << 21) | ((hdr[7] & 0x7F) << 14) | ((hdr[8] & 0x7F) << 7) | (hdr[9] & 0x7F)
            tag_data = f.read(min(tag_size, 3 * 1024 * 1024))
        pos = 0
        while pos + 10 <= len(tag_data):
            fid = tag_data[pos:pos + 4]
            if fid == b"\x00\x00\x00\x00":
                break
            if ver == 4:
                fsize = ((tag_data[pos + 4] & 0x7F) << 21) | ((tag_data[pos + 5] & 0x7F) << 14) | ((tag_data[pos + 6] & 0x7F) << 7) | (tag_data[pos + 7] & 0x7F)
            else:
                fsize = int.from_bytes(tag_data[pos + 4:pos + 8], "big")
            if fsize <= 0 or pos + 10 + fsize > len(tag_data):
                break
            if fid == b"APIC":
                payload = tag_data[pos + 10:pos + 10 + fsize]
                enc = payload[0]
                mime_end = payload.find(b"\x00", 1)
                if mime_end == -1:
                    break
                mime = payload[1:mime_end].decode("latin1", errors="ignore").strip() or "image/jpeg"
                p = mime_end + 2  # lewati null + picture_type byte
                if enc in (1, 2):  # UTF-16 description diakhiri \x00\x00
                    while p + 1 < len(payload) and payload[p:p + 2] != b"\x00\x00":
                        p += 2
                    p += 2
                else:
                    desc_end = payload.find(b"\x00", p)
                    p = (desc_end + 1) if desc_end != -1 else p
                img_bytes = payload[p:]
                if len(img_bytes) > 512:
                    with open(CAST_ART_COVER_TMP, "wb") as out:
                        out.write(img_bytes)
                    _active_cover_mime = "image/png" if "png" in mime.lower() else "image/jpeg"
                    return CAST_ART_COVER_TMP
            pos += 10 + fsize
    except Exception:
        pass
    return None


class CastAudioHTTPHandler(BaseHTTPRequestHandler):
    """Handler HTTP streaming dengan chunking dan dukungan Range bytes (206) untuk Google Cast."""

    def _resolve_file(self):
        global _active_cast_file
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        custom_path = params.get("path", [None])[0]
        if custom_path:
            clean_path = unquote(custom_path)
            if os.path.isfile(clean_path):
                real_p = os.path.realpath(clean_path)
                home_dir = os.path.realpath(os.path.expanduser("~"))
                if (
                    real_p.startswith("/tmp/")
                    or real_p.startswith("/private/tmp/")
                    or real_p.startswith(home_dir + os.sep)
                    or "/audio_library" in real_p
                    or "/https-server" in real_p
                ):
                    return real_p

        if _active_cast_file and os.path.isfile(_active_cast_file):
            return _active_cast_file
        if os.path.isfile(CURRENT_CAST_AUDIO):
            return CURRENT_CAST_AUDIO
        return None

    def _resolve_art(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/cast_art/cover") and os.path.isfile(CAST_ART_COVER_TMP):
            return CAST_ART_COVER_TMP, _active_cover_mime
        if parsed.path.startswith("/cast_art/tts") and os.path.isfile(CAST_ART_TTS_FILE):
            return CAST_ART_TTS_FILE, "image/jpeg"
        if parsed.path.startswith("/cast_art/music") and os.path.isfile(CAST_ART_MUSIC_FILE):
            return CAST_ART_MUSIC_FILE, "image/jpeg"
        if os.path.isfile(CAST_ART_MUSIC_FILE):
            return CAST_ART_MUSIC_FILE, "image/jpeg"
        return None, None

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/cast_art"):
            art_path, art_mime = self._resolve_art()
            if art_path:
                self.send_response(200)
                self.send_header("Content-Type", art_mime)
                self.send_header("Content-Length", str(os.path.getsize(art_path)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
        if parsed.path.startswith("/cast_audio") or parsed.path.startswith("/cast_media") or parsed.path.startswith("/audio"):
            target = self._resolve_file()
            if target and os.path.exists(target):
                size = os.path.getsize(target)
                self.send_response(200)
                self.send_header("Content-Type", get_cast_mime_type(target))
                self.send_header("Content-Length", str(size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/cast_art"):
            art_path, art_mime = self._resolve_art()
            if art_path:
                try:
                    with open(art_path, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", art_mime)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=300")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                except Exception:
                    pass
                return
            self.send_error(404, "Artwork tidak tersedia")
            return
        if parsed.path.startswith("/cast_audio") or parsed.path.startswith("/cast_media") or parsed.path.startswith("/audio"):
            target = self._resolve_file()
            _log_cast(f"HTTP8080 {self.client_address[0]} GET {parsed.path} -> {os.path.basename(target) if target else 'NONE'}")
            if not target or not os.path.exists(target):
                self.send_error(404, "Audio belum tersedia")
                return

            size = os.path.getsize(target)
            mime_type = get_cast_mime_type(target)
            range_header = self.headers.get("Range")

            if range_header and range_header.startswith("bytes="):
                # Handle HTTP 206 Partial Content (ChromeCast Range Request)
                try:
                    parts = range_header.replace("bytes=", "").split("-")
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if len(parts) > 1 and parts[1] else size - 1
                    if end >= size:
                        end = size - 1
                    if start > end:
                        start = 0
                    length = end - start + 1

                    self.send_response(206)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(length))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()

                    with open(target, "rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk_size = min(remaining, 65536)
                            data = f.read(chunk_size)
                            if not data:
                                break
                            self.wfile.write(data)
                            remaining -= len(data)
                    return
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception:
                    return

            # Regular 200 OK
            try:
                self.send_response(200)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                with open(target, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                pass
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Google Cast Audio Server Active")

    def log_message(self, format, *args):
        # Diam tanpa spam log
        pass


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


def start_cast_audio_server():
    """Menjalankan server HTTP lokal port 8080 di latar belakang."""
    global _http_server_started
    if _http_server_started:
        return

    try:
        server = ReusableHTTPServer(("0.0.0.0", CAST_HTTP_PORT), CastAudioHTTPHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        _http_server_started = True
    except Exception as e:
        # Jika sudah jalan, tidak apa-apa
        _http_server_started = True


def is_pychromecast_installed():
    try:
        import pychromecast

        return True
    except ImportError:
        return False


def _probe_eureka_device(ip):
    """Cepat memeriksa port 8008 & endpoint Eureka Google Home."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.35)
        res = s.connect_ex((ip, 8008))
        s.close()
        if res != 0:
            return None

        url = f"http://{ip}:8008/setup/eureka_info?params=name,device_info"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode())
            return {
                "name": data.get("name", "Google Cast Device"),
                "model": "Google Home",
                "host": ip,
                "port": 8009,
                "uuid": data.get("ssdp_udn", ""),
            }
    except Exception:
        return None


def scan_subnet_for_cast():
    """Memindai subnet lokal (192.168.0.x) secara paralel dalam hitungan detik."""
    lan_ip = get_lan_ip()
    prefix = ".".join(lan_ip.split(".")[:3])
    ips = [f"{prefix}.{i}" for i in range(1, 255)]

    # Pastikan IP 192.168.0.198 dan IP lokal dipindai terlebih dahulu
    priority_ips = ["192.168.0.198"]
    ordered_ips = priority_ips + [ip for ip in ips if ip not in priority_ips]

    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        results = executor.map(_probe_eureka_device, ordered_ips)
        for r in results:
            if r:
                found.append(r)
    return found


def load_cached_devices():
    try:
        if os.path.exists(CAST_DEVICES_CACHE_FILE):
            with open(CAST_DEVICES_CACHE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return []


def save_cached_devices(devices):
    try:
        with open(CAST_DEVICES_CACHE_FILE, "w") as f:
            json.dump(devices, f, indent=2)
    except Exception:
        pass


def get_cast_devices(force_refresh=False):
    """
    Mendeteksi semua perangkat Google Home / Chromecast pada jaringan WiFi/LAN.
    Menggunakan Direct Eureka Subnet Scan + PyChromecast Known Hosts.
    """
    global _cached_devices, _last_scan_time

    if not is_pychromecast_installed():
        return {
            "status": "not_installed",
            "message": "Pustaka pychromecast belum terpasang. Jalankan: pip3 install pychromecast",
            "devices": [],
        }

    # Mode Static-First: saat buka aplikasi (force_refresh=False), langsung baca instan
    # dari cast_devices.json (< 1ms) tanpa membebani Wi-Fi dengan scan 254 IP.
    now = time.time()
    if not force_refresh:
        cached_file = load_cached_devices()
        if cached_file:
            _cached_devices = cached_file
            return {"status": "success", "devices": cached_file, "cached": True}
        if _cached_devices and (now - _last_scan_time < 45):
            return {"status": "success", "devices": _cached_devices, "cached": True}

    # 1. Pindai cepat via Subnet Eureka HTTP API (hanya saat force_refresh=True atau cache kosong)
    discovered = scan_subnet_for_cast()

    # 2. Sertakan host dari cache sebelumnya jika ada
    cached = load_cached_devices()
    known_hosts = list({d["host"] for d in discovered + cached if "host" in d})
    for static_ip in ("192.168.0.198", "192.168.0.197"):
        if static_ip not in known_hosts:
            known_hosts.append(static_ip)

    # 3. Sinkronkan dengan pychromecast
    device_dict = {}
    for d in discovered:
        device_dict[d["host"]] = d

    try:
        import pychromecast

        casts, browser = pychromecast.get_chromecasts(
            known_hosts=known_hosts, timeout=3
        )
        for c in casts:
            h = getattr(c.cast_info, "host", None) or getattr(c, "host", None)
            if not h:
                continue
            p = getattr(c.cast_info, "port", None) or getattr(c, "port", 8009)
            fn = c.name or (
                c.cast_info.friendly_name
                if hasattr(c, "cast_info")
                else "Google Home"
            )
            md = c.model_name or (
                c.cast_info.model_name if hasattr(c, "cast_info") else "Google Home"
            )
            u = str(getattr(c, "uuid", ""))
            ct = getattr(c.cast_info, "cast_type", None) or ("cast" if "tv" in str(fn).lower() or "tv" in str(md).lower() else "audio")
            device_dict[h] = {
                "name": fn,
                "model": md,
                "host": h,
                "port": p,
                "uuid": u,
                "cast_type": ct,
            }
        pychromecast.discovery.stop_discovery(browser)
    except Exception as e:
        # Jika pychromecast error saat mDNS, tetap gunakan hasil direct scan
        pass

    device_list = list(device_dict.values())
    if not device_list and cached:
        device_list = cached

    _cached_devices = device_list
    _last_scan_time = now
    save_cached_devices(device_list)

    # Bersihkan cache koneksi / status host lama yang sudah pindah IP (misal TV pindah Wi-Fi)
    active_hosts = {d["host"] for d in device_list if d.get("host")}
    with _persistent_lock:
        for old_h in list(_persistent_casts.keys()):
            if old_h not in active_hosts:
                old_c = _persistent_casts.pop(old_h, None)
                _yt_controllers.pop(old_h, None)
                if old_c:
                    try:
                        old_c.disconnect(timeout=1)
                    except Exception:
                        pass
        for h in active_hosts:
            _last_connect_fail.pop(h, None)
    with _cast_playback_lock:
        for old_h in list(_active_cast_playbacks.keys()):
            if old_h not in active_hosts:
                _active_cast_playbacks.pop(old_h, None)

    return {"status": "success", "devices": device_list}


def _transcode_for_tv_hdmi(audio_path):
    """
    Android TV / Mi TV Stick lewat HDMI sering membisu pada MP3 24 kHz mono (output Edge TTS).
    Konversi kilat ke 48 kHz Stereo AAC (.m4a) memakai afconvert bawaan macOS agar suara
    langsung keluar jernih lewat HDMI TV.
    """
    try:
        out_path = f"/tmp/cast_tv_{int(time.time() * 1000)}.m4a"
        proc = subprocess.run(
            ["/usr/bin/afconvert", "-f", "m4af", "-d", "aac@48000", "-c", "2", "-b", "128000", audio_path, out_path],
            capture_output=True,
            timeout=10,
        )
        if proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except Exception as e:
        _log_cast(f"transcode_for_tv_hdmi gagal: {e!r}")
    return audio_path


def broadcast_audio_file_to_google_home(
    audio_path, target_device_name=None, title="Pesan Suara", duration=0.0
):
    """
    Mode 3: Menyiarkan file audio rekaman suara asli langsung ke speaker Google Home / Android TV.
    Mendukung format WAV, MP3, WebM, OGG, M4A.
    """
    if not is_pychromecast_installed():
        return {
            "status": "error",
            "message": "Pustaka pychromecast belum terpasang di Mac.",
        }

    if not audio_path or not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        return {"status": "error", "message": "File audio kosong atau tidak ditemukan"}

    start_cast_audio_server()

    # Cari target host menggunakan tuple 0.2s terlebih dahulu untuk cek apakah targetnya Android TV
    host_info = get_cast_host_tuple(target_device_name)
    target_host = host_info[0]
    target_name = host_info[4] or "Google Home"
    target_model = host_info[3] or ""
    dev_meta = next((d for d in load_cached_devices() if d.get("host") == target_host), {})
    is_tv_target = (
        dev_meta.get("cast_type") == "cast"
        or "tv" in target_name.lower()
        or "tv" in target_model.lower()
    )
    orig_audio_path = audio_path
    if is_tv_target and str(audio_path).lower().endswith(".mp3") and os.path.getsize(audio_path) < 5 * 1024 * 1024:
        audio_path = _transcode_for_tv_hdmi(audio_path)

    global _active_cast_file, _active_cast_playback
    _active_cast_file = os.path.abspath(audio_path)

    # Sebagai fallback kompatibilitas, salin jika berkas ringan (< 15MB)
    try:
        if os.path.abspath(audio_path) != os.path.abspath(CURRENT_CAST_AUDIO):
            if os.path.getsize(audio_path) < 15 * 1024 * 1024:
                shutil.copyfile(audio_path, CURRENT_CAST_AUDIO)
    except Exception:
        pass

    mime_type = get_cast_mime_type(audio_path)
    lan_ip = get_lan_ip(target_host)
    ts = int(time.time() * 1000)

    if "wav" in mime_type:
        ext = "wav"
    elif "webm" in mime_type:
        ext = "webm"
    elif "ogg" in mime_type:
        ext = "ogg"
    elif "mp4" in mime_type or "aac" in mime_type:
        ext = "m4a"
    else:
        ext = "mp3"

    media_url = f"http://{lan_ip}:{CAST_HTTP_PORT}/cast_audio.{ext}?t={ts}&path={quote(os.path.abspath(audio_path))}"

    # Siapkan Visual Art / Cover Art untuk tampilan TV agar tidak polos
    is_tts_title = any(k in str(title).lower() for k in ("gadis", "ardi", "google", "damayanti", "voice note", "pengumuman", "pesan suara"))
    extracted_cover = _extract_mp3_cover_art(orig_audio_path)
    if extracted_cover:
        thumb_url = f"http://{lan_ip}:{CAST_HTTP_PORT}/cast_art/cover?t={ts}"
    elif is_tts_title and os.path.isfile(CAST_ART_TTS_FILE):
        thumb_url = f"http://{lan_ip}:{CAST_HTTP_PORT}/cast_art/tts.jpg?t={ts}"
    elif os.path.isfile(CAST_ART_MUSIC_FILE):
        thumb_url = f"http://{lan_ip}:{CAST_HTTP_PORT}/cast_art/music.jpg?t={ts}"
    else:
        thumb_url = None

    artist_label = "Pengumuman Suara • Mac Remote" if is_tts_title else "Pustaka Audio • Mac Remote"
    media_metadata = {
        "metadataType": 3,  # MusicTrackMediaMetadata (menampilkan cover art + judul + artis di TV)
        "title": title,
        "artist": artist_label,
        "albumName": "Mac Remote Cast",
    }
    if thumb_url:
        media_metadata["images"] = [{"url": thumb_url}]

    try:
        _cancel_auto_quit(target_host)
        selected_cast = _get_cast(host_info)
        if selected_cast is None:
            return {
                "status": "error",
                "message": f"Tidak dapat terhubung ke Google Home di {target_host}.",
            }

        mc = selected_cast.media_controller
        with _cast_op_lock:
            try:
                if is_tv_target and selected_cast.app_id != "CC1AD845":
                    try:
                        selected_cast.start_app("CC1AD845")
                        time.sleep(0.8)
                    except Exception:
                        pass
                mc.play_media(
                    media_url,
                    mime_type,
                    title=title,
                    thumb=thumb_url,
                    metadata=media_metadata,
                    stream_type="BUFFERED",
                    autoplay=True,
                )
                mc.block_until_active(timeout=5)
            except Exception as first_err:
                _log_cast(f"play_media gagal ({first_err!r}), reconnect sekali ke {target_host}")
                with _persistent_lock:
                    old_c = _persistent_casts.pop(target_host, None)
                    _last_connect_fail.pop(target_host, None)
                if old_c:
                    try:
                        old_c.disconnect(timeout=1)
                    except Exception:
                        pass
                selected_cast = _get_cast(host_info)
                if selected_cast is None:
                    raise first_err
                mc = selected_cast.media_controller
                mc.play_media(
                    media_url,
                    mime_type,
                    title=title,
                    thumb=thumb_url,
                    metadata=media_metadata,
                    stream_type="BUFFERED",
                    autoplay=True,
                )
                mc.block_until_active(timeout=5)

        with _cast_playback_lock:
            _active_cast_playback = {
                "is_playing": True,
                "app_id": selected_cast.app_id or "CC1AD845",
                "title": title,
                "device": target_name,
                "host": target_host,
                "started_at": time.time(),
                "duration": float(duration or 0.0),
                "elapsed_seconds": 0.0,
                "updated_at": time.time(),
            }

        # koneksi permanen TIDAK diputus agar status & stop tetap instan

        return {
            "status": "success",
            "device": target_name,
            "host": target_host,
            "mime_type": mime_type,
            "message": f'Berhasil disiarkan ke "{target_name}" ({target_host})!',
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Gagal menyiarkan ke Google Home ({target_host}): {str(e)}",
        }


def _log_cast(msg):
    try:
        sys.stderr.write(f"[cast_manager {time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


# =====================================================================
# KONEKSI PERMANEN KE GOOGLE HOME
# Satu koneksi per perangkat dipakai bersama oleh broadcast, status & stop.
# Status diterima secara PUSH dari perangkat (listener), sehingga polling
# Web UI / Telegram tidak pernah membuka koneksi baru ke speaker.
# (Sebelumnya tiap polling membuka koneksi TLS baru -> Google Home kebanjiran
#  koneksi dan mulai menolak cast/stop.)
# =====================================================================
_persistent_casts = {}  # host -> Chromecast
_yt_controllers = {}  # host -> YouTubeController
_persistent_lock = threading.RLock()
_cast_op_lock = threading.Lock()
_last_connect_fail = {}  # host -> timestamp
_auto_quit_timers = {}  # host -> threading.Timer
_auto_quit_lock = threading.Lock()
AUTO_QUIT_DELAY_SEC = 3.5


def _cancel_auto_quit(host):
    with _auto_quit_lock:
        t = _auto_quit_timers.pop(host, None)
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass


def _schedule_auto_quit(host, delay=AUTO_QUIT_DELAY_SEC):
    """Menutup Default Media Receiver (CC1AD845) otomatis setelah media selesai dengan jeda aman."""
    _cancel_auto_quit(host)

    def _do_auto_quit():
        with _auto_quit_lock:
            _auto_quit_timers.pop(host, None)
        c = _persistent_casts.get(host)
        if c is None:
            return
        try:
            # Hanya auto-tutup Default Media Receiver (CC1AD845), jangan tutup YouTube atau app lain
            if c.app_id != "CC1AD845":
                return
            ms = getattr(c.media_controller, "status", None)
            pstate = getattr(ms, "player_state", None) if ms else None
            ctype = str(getattr(ms, "content_type", "") or "").lower()
            title_l = str(getattr(ms, "title", "") or "").lower()
            is_tts = any(k in title_l for k in ("gadis", "ardi", "google", "damayanti", "pengumuman", "pesan suara"))
            # Jangan tutup jika sudah ada media baru berjalan, sedang menampilkan gambar, atau musik/video sedang di-pause
            if pstate in ("PLAYING", "BUFFERING") or ctype.startswith("image/") or (pstate == "PAUSED" and not is_tts):
                return
            with _cast_op_lock:
                if c.app_id == "CC1AD845":
                    try:
                        c.quit_app(timeout=3.0)
                    except Exception:
                        try:
                            c.socket_client.receiver_controller.stop_app()
                        except Exception:
                            pass
            _log_cast(f"auto-quit: menutup CC1AD845 di {host} setelah pemutaran selesai")
            _refresh_playback_from_cast(host)
        except Exception as e:
            _log_cast(f"auto-quit error di {host}: {e!r}")

    timer = threading.Timer(delay, _do_auto_quit)
    timer.daemon = True
    with _auto_quit_lock:
        _auto_quit_timers[host] = timer
    timer.start()


def _is_media_app(app_id):
    # E8C28D3C = Backdrop / ambient idle, AndroidNativeApp = Home screen Android TV
    return bool(app_id and app_id not in ("E8C28D3C", "AndroidNativeApp", "none", ""))


class _CastStatusListener:
    def __init__(self, host):
        self.host = host

    def new_cast_status(self, status):
        _refresh_playback_from_cast(self.host)


class _MediaStatusListener:
    def __init__(self, host):
        self.host = host

    def new_media_status(self, status):
        _refresh_playback_from_cast(self.host)
        try:
            pstate = getattr(status, "player_state", None)
            idle_reason = getattr(status, "idle_reason", None)
            ctype = str(getattr(status, "content_type", "") or "").lower()
            title_l = str(getattr(status, "title", "") or "").lower()
            is_tts = any(k in title_l for k in ("gadis", "ardi", "google", "damayanti", "pengumuman", "pesan suara"))

            if pstate in ("PLAYING", "BUFFERING"):
                _cancel_auto_quit(self.host)
            elif pstate == "PAUSED":
                if is_tts and not ctype.startswith("image/"):
                    _schedule_auto_quit(self.host, delay=4.0)
                else:
                    _cancel_auto_quit(self.host)
            elif pstate == "IDLE" and not ctype.startswith("image/"):
                if idle_reason in ("CANCELLED", "INTERRUPTED"):
                    _schedule_auto_quit(self.host, delay=1.0)
                elif idle_reason == "FINISHED":
                    _schedule_auto_quit(self.host, delay=AUTO_QUIT_DELAY_SEC)
        except Exception:
            pass

    def load_media_failed(self, item, error_code):
        _log_cast(f"load media gagal di {self.host}: code={error_code}")


def _get_cast(host_info, timeout=8.0):
    """Mengambil (atau membuat) koneksi permanen ke perangkat Google Home."""
    import pychromecast

    host = host_info[0]
    with _persistent_lock:
        c = _persistent_casts.get(host)
        if c is not None:
            try:
                if c.socket_client.is_alive() and c.socket_client.is_connected:
                    return c
                if c.socket_client.is_alive():
                    # Thread hidup tetapi sedang reconnect otomatis -> tunggu sebentar
                    for _ in range(int(timeout * 5)):
                        if c.socket_client.is_connected:
                            return c
                        time.sleep(0.2)
                _log_cast(f"koneksi permanen ke {host} terputus, membuat ulang")
                try:
                    c.disconnect(timeout=1)
                except Exception:
                    pass
            except Exception:
                pass
            _persistent_casts.pop(host, None)

        # Hindari membanjiri perangkat dengan percobaan koneksi beruntun
        if time.time() - _last_connect_fail.get(host, 0) < 3:
            return None

        try:
            # tries=None -> socket client akan reconnect otomatis tanpa batas
            c = pychromecast.get_chromecast_from_host(host_info, tries=None, retry_wait=3, timeout=timeout)
            c.wait(timeout=timeout)
        except Exception as e:
            _log_cast(f"gagal konek ke {host}: {e!r}")
            _last_connect_fail[host] = time.time()
            try:
                c.disconnect(timeout=1)
            except Exception:
                pass
            return None

        try:
            c.register_status_listener(_CastStatusListener(host))
            c.media_controller.register_status_listener(_MediaStatusListener(host))
        except Exception as e:
            _log_cast(f"register listener gagal: {e!r}")

        _persistent_casts[host] = c
        _log_cast(f"koneksi permanen ke {host} siap (app_id={c.app_id})")
        return c


def _refresh_playback_from_cast(host=None):
    """Membangun ulang _active_cast_playback dari status terakhir koneksi permanen (tanpa jaringan)."""
    global _active_cast_playback, _last_status_query_time
    host = host or get_cast_host_tuple()[0]
    c = _persistent_casts.get(host)
    if c is None:
        return dict(_active_cast_playback)

    now = time.time()
    try:
        app_id = c.app_id
        cs = c.status
        status_text = (cs.status_text if cs else "") or ""
        ms = None
        try:
            ms = c.media_controller.status
        except Exception:
            pass
        player_state = getattr(ms, "player_state", None) if ms else None

        if app_id == "CC1AD845":
            # Default Media Receiver: tetap terbuka beberapa detik setelah audio selesai,
            # jadi gunakan state media / teks "Casting:" untuk menentukan sedang memutar.
            is_playing = player_state in ("PLAYING", "BUFFERING", "PAUSED") or status_text.startswith("Casting")
        else:
            is_playing = _is_media_app(app_id)

        title = ""
        if is_playing:
            title = (getattr(ms, "title", None) if ms else None) or status_text.replace("Casting: ", "").strip() \
                or c.app_display_name or "Media Sedang Berjalan"
            if title == "Default Media Receiver":
                title = "Media Cast"

        elapsed = 0.0
        duration = 0.0
        if is_playing and ms is not None:
            try:
                elapsed = float(getattr(ms, "adjusted_current_time", None) or ms.current_time or 0.0)
            except Exception:
                elapsed = 0.0
            duration = float(getattr(ms, "duration", 0.0) or 0.0)

        vol_pct = None
        vol_muted = False
        if cs is not None:
            try:
                if cs.volume_level is not None:
                    vol_pct = int(round(float(cs.volume_level) * 100))
                vol_muted = bool(cs.volume_muted)
            except Exception:
                pass

        # Cari metadata model / cast_type dari cache perangkat
        dev_meta = next((d for d in load_cached_devices() if d.get("host") == host), {})
        cast_type = getattr(getattr(c, "cast_info", None), "cast_type", None) or dev_meta.get("cast_type") or "audio"
        model_name = getattr(c, "model_name", None) or dev_meta.get("model") or "Google Cast"

        with _cast_playback_lock:
            prev = _active_cast_playbacks.get(host, _active_cast_playback if host == "192.168.0.198" else {})
            started_at = (prev.get("started_at") or now) if (is_playing and prev.get("is_playing")) else (now if is_playing else 0.0)
            if is_playing and not elapsed:
                elapsed = round(now - started_at, 1)
            entry = {
                "is_playing": is_playing,
                "app_id": app_id,
                "player_state": player_state,
                "title": title,
                "device": c.name or dev_meta.get("name") or host,
                "model": model_name,
                "cast_type": cast_type,
                "host": host,
                "started_at": started_at,
                "duration": round(duration, 1),
                "elapsed_seconds": round(elapsed, 1),
                "volume_level": vol_pct if vol_pct is not None else prev.get("volume_level"),
                "volume_muted": vol_muted,
                "updated_at": now,
                "live": bool(c.socket_client.is_connected),
            }
            _active_cast_playbacks[host] = entry
            if host == get_cast_host_tuple()[0]:
                _active_cast_playback = dict(entry)
                _last_status_query_time = now
            return dict(entry)
    except Exception as e:
        _log_cast(f"refresh status error: {e!r}")
        return dict(_active_cast_playbacks.get(host, _active_cast_playback))


def stop_google_home_cast(target_device_name=None):
    """
    Menghentikan siaran di Google Home seketika dengan perintah STOP receiver (quit_app)
    - mekanisme yang sama dengan Google Chrome Cast console. Memakai koneksi permanen.
    Bekerja untuk cast yang dimulai dari server ini, HP, maupun Chrome.
    """
    host_info = get_cast_host_tuple(target_device_name)
    host_ip = host_info[0]
    dev_name = host_info[4] or "Google Home"

    c = _get_cast(host_info)
    if c is None:
        return {"status": "error", "message": f"Tidak dapat terhubung ke Google Home di {host_ip}"}

    try:
        before = _refresh_playback_from_cast(host_ip)
        app_before = c.app_id
        title_before = before.get("title", "")

        if _is_media_app(app_before):
            with _cast_op_lock:
                try:
                    c.quit_app(timeout=4.0)
                except Exception as e:
                    _log_cast(f"quit_app error: {e!r}")
                    try:
                        c.socket_client.receiver_controller.stop_app()
                    except Exception:
                        pass
            for _ in range(15):
                if not _is_media_app(c.app_id):
                    break
                time.sleep(0.2)

        stopped = not _is_media_app(c.app_id)
        _log_cast(f"stop: app_before={app_before} title={title_before!r} -> app_after={c.app_id} stopped={stopped}")
        _refresh_playback_from_cast(host_ip)

        if stopped:
            if _is_media_app(app_before):
                msg = f'Siaran "{title_before or "media"}" di Google Home ({dev_name}) berhasil dihentikan!'
            else:
                msg = f"Google Home ({dev_name}) sudah idle - tidak ada siaran aktif."
            return {"status": "success", "was_playing": _is_media_app(app_before), "message": msg}
        return {"status": "error", "message": f"Perintah stop terkirim tetapi Google Home ({dev_name}) masih memutar media."}
    except Exception as e:
        _log_cast(f"stop exception: {e!r}")
        return {"status": "error", "message": f"Gagal menghentikan Google Home ({host_ip}): {str(e)}"}


def _ensure_bg_connect(host_info):
    host = host_info[0]
    if host in _persistent_casts or host in _bg_connecting_hosts:
        return
    if time.time() - _last_connect_fail.get(host, 0) < 15:
        return

    def _bg():
        _bg_connecting_hosts.add(host)
        try:
            _get_cast(host_info, timeout=8.0)
        finally:
            _bg_connecting_hosts.discard(host)

    threading.Thread(target=_bg, daemon=True).start()


def get_google_home_playback_status(target_device_name=None):
    """
    Status pemutaran perangkat Cast dari koneksi permanen (di-update PUSH oleh perangkat).
    Tidak membuka koneksi baru per polling. Mendeteksi cast dari server, HP, maupun Chrome.
    """
    host_info = get_cast_host_tuple(target_device_name)
    host = host_info[0]
    c = _persistent_casts.get(host)
    if c is None:
        _ensure_bg_connect(host_info)
        dev_meta = next((d for d in load_cached_devices() if d.get("host") == host), {})
        st = dict(_active_cast_playbacks.get(host, {
            "is_playing": False,
            "app_id": None,
            "player_state": None,
            "title": "",
            "device": dev_meta.get("name") or host_info[4] or host,
            "model": dev_meta.get("model") or host_info[3] or "Google Cast",
            "cast_type": dev_meta.get("cast_type") or "audio",
            "host": host,
            "started_at": 0.0,
            "duration": 0.0,
            "elapsed_seconds": 0.0,
            "volume_level": None,
            "volume_muted": False,
            "updated_at": 0.0,
        }))
        st["live"] = False
        return st
    return _refresh_playback_from_cast(host)


def get_all_cast_playback_statuses():
    """Mengembalikan daftar status live untuk SEMUA perangkat Cast (Google Home + Android TV)."""
    devs = load_cached_devices()
    if not devs:
        return [get_google_home_playback_status()]
    out = []
    seen = set()
    for d in devs:
        h = d.get("host")
        if not h or h in seen:
            continue
        seen.add(h)
        out.append(get_google_home_playback_status(h))
    return out


def _extract_audio_for_speaker(video_path):
    """Mengekstrak trek audio dari file video (.mp4/.m4v/.mov) ke .m4a (AAC) memakai afconvert macOS agar bisa diputar di Google Home Speaker."""
    import hashlib
    try:
        key = hashlib.md5(f"{os.path.abspath(video_path)}:{os.path.getmtime(video_path)}".encode()).hexdigest()[:12]
        out_path = f"/tmp/cast_vid_audio_{key}.m4a"
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            return out_path
        proc = subprocess.run(
            ["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", "-b", "128000", video_path, out_path],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except Exception as e:
        _log_cast(f"extract_audio_for_speaker gagal: {e!r}")
    return None


def cast_media_file_to_device(file_path, target_device_name=None, title=None):
    """
    Menyiarkan file Video (MP4/WebM/MKV), Gambar (JPG/PNG/GIF/WebP), atau Audio
    langsung dari File Manager Mac ke Android TV / Google Home.
    Jika target tidak ditentukan: video/gambar otomatis diarahkan ke Android TV (cast_type='cast').
    """
    if not is_pychromecast_installed():
        return {"status": "error", "message": "Pustaka pychromecast belum terpasang di Mac."}
    if not file_path or not os.path.isfile(file_path):
        return {"status": "error", "message": "File media tidak ditemukan."}

    start_cast_audio_server()
    mime_type = get_cast_mime_type(file_path)
    is_visual = mime_type.startswith("video/") or mime_type.startswith("image/")

    # Jika visual (video/gambar) dan user tidak memilih target, otomatis pilih Android TV
    if not target_device_name and is_visual:
        devs = load_cached_devices()
        tv = next((d for d in devs if d.get("cast_type") == "cast" or "tv" in str(d.get("name", "")).lower()), None)
        if tv:
            target_device_name = tv.get("host")

    host_info = get_cast_host_tuple(target_device_name)
    target_host = host_info[0]
    target_name = host_info[4] or "Cast Device"
    dev_meta = next((d for d in load_cached_devices() if d.get("host") == target_host), {})
    is_audio_only_speaker = (
        dev_meta.get("cast_type") == "audio"
        or (target_host == "192.168.0.198")
        or ("speaker" in target_name.lower() and "tv" not in target_name.lower())
    )

    # Jika file video diarahkan ke Google Home Speaker (tanpa layar), ekstrak audionya saja!
    if mime_type.startswith("video/") and is_audio_only_speaker:
        extracted = _extract_audio_for_speaker(file_path)
        if extracted:
            return broadcast_audio_file_to_google_home(
                extracted, target_device_name=target_host, title=title or os.path.basename(file_path)
            )
        return {
            "status": "error",
            "message": f"Format video ini tidak bisa diekstrak audionya oleh afconvert untuk {target_name}. Coba format MP4/MOV/M4V.",
        }

    ext = os.path.splitext(file_path)[1].lower().lstrip(".") or "mp4"
    lan_ip = get_lan_ip(target_host)
    ts = int(time.time() * 1000)
    media_url = f"http://{lan_ip}:{CAST_HTTP_PORT}/cast_media.{ext}?t={ts}&path={quote(os.path.abspath(file_path))}"
    display_title = title or os.path.basename(file_path)

    selected_cast = _get_cast(host_info)
    if selected_cast is None:
        return {
            "status": "error",
            "message": f"Tidak dapat terhubung ke {target_name} ({target_host}). Pastikan perangkat menyala.",
        }

    try:
        _log_cast(f"cast_media: file={os.path.basename(file_path)!r} mime={mime_type} -> {target_name} ({target_host})")
        mc = selected_cast.media_controller
        with _cast_op_lock:
            try:
                if selected_cast.app_id == "AndroidNativeApp":
                    try:
                        selected_cast.start_app("CC1AD845")
                        time.sleep(0.6)
                    except Exception:
                        pass
                mc.play_media(
                    media_url,
                    mime_type,
                    title=display_title,
                    stream_type="BUFFERED" if not mime_type.startswith("image/") else "NONE",
                    autoplay=True,
                )
                mc.block_until_active(timeout=6)
            except Exception as first_err:
                _log_cast(f"cast_media gagal ({first_err!r}), reconnect sekali ke {target_host}")
                with _persistent_lock:
                    old_c = _persistent_casts.pop(target_host, None)
                    _last_connect_fail.pop(target_host, None)
                if old_c:
                    try:
                        old_c.disconnect(timeout=1)
                    except Exception:
                        pass
                selected_cast = _get_cast(host_info)
                if selected_cast is None:
                    raise first_err
                mc = selected_cast.media_controller
                mc.play_media(
                    media_url,
                    mime_type,
                    title=display_title,
                    stream_type="BUFFERED" if not mime_type.startswith("image/") else "NONE",
                    autoplay=True,
                )
                mc.block_until_active(timeout=6)

        _refresh_playback_from_cast(target_host)
        kind_label = "Video" if mime_type.startswith("video/") else ("Gambar" if mime_type.startswith("image/") else "Audio")
        return {
            "status": "success",
            "device": target_name,
            "host": target_host,
            "mime_type": mime_type,
            "message": f'{kind_label} "{display_title}" berhasil di-cast ke {target_name} ({target_host})!',
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Gagal cast ke {target_name} ({target_host}): {str(e)}",
        }


def extract_youtube_video_id(url):
    """Mengekstrak YouTube Video ID dari URL youtube.com, youtu.be, shorts, live, atau music.youtube.com."""
    import re
    u = str(url or "").strip()
    patterns = [
        r"(?:youtube\.com/watch\?.*v=)([A-Za-z0-9_-]{11})",
        r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/live/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, u)
        if m:
            return m.group(1)
    return None


def cast_url_to_device(url, target_device_name=None, title=None, queue=False):
    """
    Universal Cast URL: mendukung YouTube / Shorts / Live, HLS (.m3u8 / IPTV),
    Radio Stream, serta URL langsung Video (MP4/WebM), Audio (MP3/AAC), dan Gambar.
    """
    if not is_pychromecast_installed():
        return {"status": "error", "message": "Pustaka pychromecast belum terpasang di Mac."}

    url = str(url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"status": "error", "message": "URL harus diawali http:// atau https://"}

    yt_id = extract_youtube_video_id(url)
    path_low = urlparse(url).path.lower()

    # Tentukan MIME & apakah visual
    url_mime_map = {
        ".m3u8": "application/x-mpegurl",
        ".mpd": "application/dash+xml",
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mov": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    mime_type = None
    for ext_k, m_val in url_mime_map.items():
        if path_low.endswith(ext_k):
            mime_type = m_val
            break

    is_visual = bool(yt_id) or (mime_type and (mime_type.startswith("video/") or mime_type.startswith("image/") or mime_type in ("application/x-mpegurl", "application/dash+xml")))

    # Otomatis arahkan YouTube & media visual ke Android TV (TV Stick Mi) jika target kosong
    if not target_device_name and is_visual:
        devs = load_cached_devices()
        tv = next((d for d in devs if d.get("cast_type") == "cast" or "tv" in str(d.get("name", "")).lower()), None)
        if tv:
            target_device_name = tv.get("host")

    host_info = get_cast_host_tuple(target_device_name)
    target_host = host_info[0]
    target_name = host_info[4] or "Cast Device"

    c = _get_cast(host_info)
    if c is None:
        return {
            "status": "error",
            "message": f"Tidak dapat terhubung ke {target_name} ({target_host}). Pastikan perangkat menyala.",
        }

    # --- MODE 1: YOUTUBE ---
    if yt_id:
        try:
            from pychromecast.controllers.youtube import YouTubeController
            with _cast_op_lock:
                yt = _yt_controllers.get(target_host)
                if yt is None:
                    yt = YouTubeController()
                    c.register_handler(yt)
                    _yt_controllers[target_host] = yt
                if queue and c.app_id == "233637DE":
                    yt.add_to_queue(yt_id)
                    action_txt = "ditambahkan ke antrean YouTube"
                else:
                    yt.play_video(yt_id)
                    action_txt = "diputar di YouTube"
            _log_cast(f"cast_youtube: id={yt_id} queue={queue} -> {target_name} ({target_host})")
            time.sleep(0.3)
            _refresh_playback_from_cast(target_host)
            return {
                "status": "success",
                "mode": "youtube",
                "video_id": yt_id,
                "device": target_name,
                "host": target_host,
                "message": f"▶️ Video YouTube ({yt_id}) berhasil {action_txt} pada {target_name}!",
            }
        except Exception as e:
            _log_cast(f"cast_youtube error: {e!r}")
            return {"status": "error", "message": f"Gagal memutar YouTube di {target_name}: {str(e)}"}

    # --- MODE 2: DIRECT MEDIA / HLS / RADIO STREAM URL ---
    if not mime_type:
        is_tv = "tv" in target_name.lower() or getattr(getattr(c, "cast_info", None), "cast_type", "") == "cast"
        mime_type = "video/mp4" if is_tv else "audio/mpeg"

    stream_type = "NONE" if mime_type.startswith("image/") else (
        "LIVE" if mime_type in ("application/x-mpegurl", "application/dash+xml") else "BUFFERED"
    )
    display_title = title or os.path.basename(urlparse(url).path) or "Media Stream"

    try:
        _log_cast(f"cast_url: url={url[:80]!r} mime={mime_type} -> {target_name} ({target_host})")
        mc = c.media_controller
        with _cast_op_lock:
            if c.app_id == "AndroidNativeApp":
                try:
                    c.start_app("CC1AD845")
                    time.sleep(0.6)
                except Exception:
                    pass
            mc.play_media(
                url,
                mime_type,
                title=display_title,
                stream_type=stream_type,
                autoplay=True,
            )
            mc.block_until_active(timeout=6)
        _refresh_playback_from_cast(target_host)
        return {
            "status": "success",
            "mode": "url",
            "mime_type": mime_type,
            "device": target_name,
            "host": target_host,
            "message": f'📺 Media URL "{display_title}" berhasil di-cast ke {target_name}!',
        }
    except Exception as e:
        _log_cast(f"cast_url error: {e!r}")
        return {"status": "error", "message": f"Gagal cast URL ke {target_name}: {str(e)}"}


def set_google_home_volume(
    volume_percent=None, delta_percent=None, muted=None, target_device_name=None
):
    """
    Mengatur volume (0-100%), perubahan relatif (+/-), atau mute/unmute Google Home
    melalui koneksi permanen.
    """
    host_info = get_cast_host_tuple(target_device_name)
    host_ip = host_info[0]
    dev_name = host_info[4] or "Google Home"

    c = _get_cast(host_info)
    if c is None:
        return {"status": "error", "message": f"Tidak dapat terhubung ke Google Home di {host_ip}"}

    try:
        rc = c.socket_client.receiver_controller
        cs = c.status
        curr_level = float(cs.volume_level) if (cs and cs.volume_level is not None) else 0.5
        curr_muted = bool(cs.volume_muted) if cs else False
        msg_parts = []

        with _cast_op_lock:
            if muted is not None:
                new_muted = (not curr_muted) if str(muted).lower() == "toggle" else bool(muted)
                rc.set_volume_muted(new_muted)
                curr_muted = new_muted
                msg_parts.append("dibisukan (Mute)" if new_muted else "suara dinyalakan (Unmute)")

            if delta_percent is not None:
                new_level = max(0.0, min(1.0, curr_level + float(delta_percent) / 100.0))
                rc.set_volume(round(new_level, 2))
                curr_level = new_level
                msg_parts.append(f"volume {int(round(new_level * 100))}%")
            elif volume_percent is not None:
                new_level = max(0.0, min(1.0, float(volume_percent) / 100.0))
                rc.set_volume(round(new_level, 2))
                curr_level = new_level
                msg_parts.append(f"volume diatur ke {int(round(new_level * 100))}%")

        time.sleep(0.15)
        st = _refresh_playback_from_cast(host_ip)
        vol_out = st.get("volume_level")
        if vol_out is None:
            vol_out = int(round(curr_level * 100))
        muted_out = bool(st.get("volume_muted", curr_muted))
        return {
            "status": "success",
            "volume": vol_out,
            "muted": muted_out,
            "device": dev_name,
            "message": f"Google Home ({dev_name}): {', '.join(msg_parts) if msg_parts else f'volume {vol_out}%'}",
        }
    except Exception as e:
        _log_cast(f"set_volume error: {e!r}")
        return {"status": "error", "message": f"Gagal mengatur volume Google Home: {str(e)}"}


def control_google_home_media(action="playpause", target_device_name=None):
    """
    Kontrol pemutaran media aktif di Google Home: pause, play/resume, playpause, stop.
    """
    act = str(action or "playpause").lower().strip()
    if act == "stop":
        return stop_google_home_cast(target_device_name)

    host_info = get_cast_host_tuple(target_device_name)
    host_ip = host_info[0]
    dev_name = host_info[4] or "Google Home"

    c = _get_cast(host_info)
    if c is None:
        return {"status": "error", "message": f"Tidak dapat terhubung ke Google Home di {host_ip}"}

    if not _is_media_app(c.app_id):
        return {"status": "error", "message": f"Tidak ada media aktif di Google Home ({dev_name})."}

    try:
        mc = c.media_controller
        state = getattr(mc.status, "player_state", None) if mc.status else None
        with _cast_op_lock:
            if act == "pause" or (act == "playpause" and state in ("PLAYING", "BUFFERING")):
                mc.pause()
                label = "dijeda (Paused)"
            elif act in ("play", "resume", "playpause"):
                mc.play()
                label = "dilanjutkan (Playing)"
            else:
                return {"status": "error", "message": f"Aksi media tidak dikenal: {act}"}

        time.sleep(0.2)
        st = _refresh_playback_from_cast(host_ip)
        return {
            "status": "success",
            "player_state": st.get("player_state"),
            "device": dev_name,
            "message": f"Google Home ({dev_name}) berhasil {label}.",
        }
    except Exception as e:
        _log_cast(f"control_media error: {e!r}")
        return {"status": "error", "message": f"Gagal kontrol media Google Home: {str(e)}"}


def broadcast_to_google_home(
    text, target_device_name=None, voice_mode="google", voice_type="wanita1"
):
    """
    Menyiarkan pesan teks ke Google Home dengan 3 pilihan suara TTS Bahasa Indonesia:
      - 'wanita1' / 'gadis': Suara Wanita 1 (id-ID-GadisNeural - Natural & Jernih)
      - 'pria' / 'ardi':     Suara Pria (id-ID-ArdiNeural - Jelas & Wibawa)
      - 'wanita2' / 'google': Suara Wanita 2 (Google Assistant Indonesia)
    """
    text = text.strip()
    if not text:
        return {"status": "error", "message": "Pesan tidak boleh kosong"}

    v_clean = str(voice_type).lower()
    if v_clean in ["male", "pria", "ardi", "laki"]:
        title = "Pria (Ardi Neural)"
    elif v_clean in ["female2", "wanita2", "google"]:
        title = "Wanita 2 (Google ID)"
    elif v_clean in ["local_say", "damayanti"]:
        audio_path = indonesian_tts.generate_local_say_audio(text, voice_name="Damayanti")
        title = "TTS Damayanti"
    else:
        title = "Wanita 1 (Gadis Neural)"

    if v_clean not in ["local_say", "damayanti"]:
        audio_path = indonesian_tts.generate_indonesian_audio(text, voice_type=v_clean)

    if not audio_path or not os.path.exists(audio_path):
        # Fallback coba say offline jika online TTS gagal
        audio_path = indonesian_tts.generate_local_say_audio(text)
        title = "Pengumuman Suara"

    if not audio_path or not os.path.exists(audio_path):
        return {
            "status": "error",
            "message": f"Gagal menghasilkan audio untuk suara '{voice_type}'.",
        }

    # Siarkan audio ke Google Home
    return broadcast_audio_file_to_google_home(audio_path, target_device_name, title=title)

if __name__ == "__main__":
    print("Memindai perangkat Google Cast di jaringan lokal...")
    res = get_cast_devices(force_refresh=True)
    print("Hasil Pindai:", json.dumps(res, indent=2))
