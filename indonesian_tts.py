#!/usr/bin/env python3
"""
Modul Text-to-Speech (TTS) Bahasa Indonesia yang Realistis & Stabil.
Mendukung:
  1. Microsoft Edge Neural Voice (id-ID-GadisNeural / id-ID-ArdiNeural) jika edge-tts terpasang.
  2. Google TTS Indonesia dengan Auto-Chunking (Stabil untuk kalimat pendek & panjang).
  3. Apple macOS Damayanti (Fallback offline jika terpasang).
  4. Pemutaran via /usr/bin/afplay di Mac speaker & streaming ke Web Browser.
"""

import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

# SSL Context tanpa verifikasi untuk mencegah error sertifikat lokal di macOS
def _get_ssl_ctx():
    try:
        return ssl._create_unverified_context()
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

SSL_CTX = _get_ssl_ctx()


def _split_text_into_chunks(text, max_len=85):
    """
    Memecah teks panjang menjadi potongan-potongan kecil (< 85 karakter)
    berdasarkan tanda baca atau spasi agar Google TTS tidak menolak request (HTTP 400/404).
    """
    text = re.sub(r'\s+', ' ', text.strip())
    if len(text) <= max_len:
        return [text]

    # Pisahkan berdasarkan tanda baca (. , ! ? ; :)
    sentences = re.split(r'([.,!?;:]\s*)', text)
    chunks = []
    current = ""

    for s in sentences:
        if not s:
            continue
        if len(current) + len(s) <= max_len:
            current += s
        else:
            if current:
                chunks.append(current.strip())
            # Jika satu bagian kalimat terlalu panjang tanpa tanda baca, pisahkan per kata
            if len(s) > max_len:
                words = s.split(' ')
                sub_curr = ""
                for w in words:
                    if len(sub_curr) + len(w) + 1 <= max_len:
                        sub_curr += (" " if sub_curr else "") + w
                    else:
                        if sub_curr:
                            chunks.append(sub_curr.strip())
                        sub_curr = w
                current = sub_curr
            else:
                current = s

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]


def _fetch_google_tts_chunk(chunk_text):
    encoded = urllib.parse.quote(chunk_text)
    url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=id&client=tw-ob&q={encoded}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://translate.google.com/",
        }
    )
    with urllib.request.urlopen(req, timeout=8, context=SSL_CTX) as resp:
        return resp.read()


def generate_indonesian_audio(text, voice_type="female"):
    """
    Menghasilkan file audio MP3 Bahasa Indonesia dengan 3 pilihan suara:
      - 'female' / 'wanita' / 'gadis': Suara Wanita 1 (Gadis Neural - Natural Studio)
      - 'male' / 'pria' / 'ardi':     Suara Pria (Ardi Neural - Jelas & Tegas)
      - 'female2' / 'wanita2':        Suara Wanita 2 (Google Assistant ID - Lembut)
    """
    text = text.strip()
    if not text:
        return None

    out_file = f"/tmp/speech_{uuid.uuid4().hex[:10]}.mp3"
    v_clean = str(voice_type).lower()

    # Pilihan suara Edge Neural
    edge_voice = None
    if v_clean in ["male", "pria", "ardi", "laki"]:
        edge_voice = "id-ID-ArdiNeural"
    elif v_clean in ["female2", "wanita2", "google"]:
        edge_voice = None  # Langsung ke Google TTS
    else:
        edge_voice = "id-ID-GadisNeural"

    # 1. Coba Edge TTS Neural Voice via python library
    if edge_voice:
        try:
            import asyncio, edge_tts
            async def _run():
                comm = edge_tts.Communicate(text, edge_voice)
                await comm.save(out_file)
            asyncio.run(_run())
            if os.path.exists(out_file) and os.path.getsize(out_file) > 100:
                return out_file
        except Exception:
            pass

        # Coba CLI edge-tts jika modul python gagal
        try:
            proc = subprocess.run(
                ["edge-tts", "--voice", edge_voice, "--text", text, "--write-media", out_file],
                capture_output=True,
                timeout=12
            )
            if proc.returncode == 0 and os.path.exists(out_file) and os.path.getsize(out_file) > 100:
                return out_file
        except Exception:
            pass

    # 2. Google Translate TTS (Suara Wanita 2 Google ID)
    try:
        chunks = _split_text_into_chunks(text, max_len=85)
        audio_data = bytearray()
        for c in chunks:
            data = _fetch_google_tts_chunk(c)
            if data:
                audio_data.extend(data)
            time.sleep(0.04)

        if audio_data:
            with open(out_file, "wb") as f:
                f.write(audio_data)
            return out_file
    except Exception:
        pass

    return None

def generate_local_say_audio(text, voice_name="Damayanti"):
    """
    Mode 2: Menghasilkan file audio WAV Bahasa Indonesia menggunakan suara lokal Mac (Damayanti).
    Bekerja 100% offline di dalam Mac mini tanpa koneksi internet.
    """
    text = text.strip()
    if not text:
        return None

    uid = uuid.uuid4().hex[:8]
    aiff_path = f"/tmp/speech_local_{uid}.aiff"
    wav_path = f"/tmp/speech_local_{uid}.wav"

    try:
        # 1. Gunakan say lokal dengan suara Damayanti
        res = subprocess.run(["say", "-v", voice_name, text, "-o", aiff_path], timeout=12)
        if res.returncode == 0 and os.path.exists(aiff_path):
            # 2. Konversi ke WAV standar menggunakan afconvert native macOS
            c_res = subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", aiff_path, wav_path], timeout=12)
            if c_res.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 100:
                try: os.remove(aiff_path)
                except Exception: pass
                return wav_path
    except Exception:
        pass

    # Fallback suara lokal default jika Damayanti tidak merespons
    try:
        subprocess.run(["say", text, "-o", aiff_path], timeout=10)
        if os.path.exists(aiff_path):
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", aiff_path, wav_path], timeout=10)
            if os.path.exists(wav_path):
                try: os.remove(aiff_path)
                except Exception: pass
                return wav_path
    except Exception:
        pass

    return None


def speak_indonesian(text, voice_type="female"):
    """
    Memutar suara Bahasa Indonesia ke speaker Mac.
    Hentikan suara sebelumnya (jika ada) agar tidak saling tumpang tindih (Anti-Overlap).
    """
    text = text.strip()
    if not text:
        return False

    try:
        import mac_player
        mac_player.stop_mac_audio()
    except Exception:
        try:
            subprocess.run(["pkill", "-9", "-x", "afplay"], timeout=2)
            subprocess.run(["pkill", "-9", "-x", "say"], timeout=2)
        except Exception:
            pass

    # Coba generate audio MP3 realistis
    audio_path = generate_indonesian_audio(text, voice_type)
    if audio_path and os.path.exists(audio_path):
        try:
            import mac_player
            res = mac_player.play_mac_audio(audio_path, title=f'TTS: "{text[:25]}"', source="tts")
            return res.get("status") == "success"
        except Exception:
            try:
                subprocess.Popen(["afplay", audio_path])
                return True
            except Exception:
                pass

    # Fallback 1: Coba suara resmi Apple "Damayanti" jika terpasang di Mac
    try:
        p = subprocess.run(["say", "-v", "Damayanti", text], timeout=15)
        if p.returncode == 0:
            return True
    except Exception:
        pass

    # Fallback 2: Perintah say default
    try:
        subprocess.Popen(["say", text])
        return True
    except Exception:
        return False


if __name__ == "__main__":
    test_msg = sys.argv[1] if len(sys.argv) > 1 else "Halo! Ini adalah uji coba suara bahasa Indonesia yang stabil dan natural."
    print(f"Mengucapkan: {test_msg}")
    speak_indonesian(test_msg)
