#!/usr/bin/env python3
"""
Quick Action Execution Engine (dipakai bersama oleh Web Dashboard & Telegram Bot).
Fitur:
  1. Bel Pengumuman Lembut (Chime Ding-Dong 48kHz Stereo) yang digabung mulus sebelum TTS.
  2. Pengulangan (Repeat 1x/2x/3x) dalam 1 file audio tanpa jeda buffering Cast.
  3. Multi-Tipe:
     - 'tts'       : Broadcast TTS (Wanita 1/2, Pria) + Chime + Repeat
     - 'assistant' : Perintah langsung ke Google Assistant SDK + putar balasan suara
     - 'audio'     : Putar audio/video dari Pustaka Media
  4. Multi-Target: Default Google Home, perangkat spesifik, atau 'all' (Google Home + TV).
  5. Auto-Volume: Set volume ke 70% (atau sesuai preset) lalu otomatis kembali ke 50%
     (atau volume sebelumnya jika restore_volume == -1) setelah pemutaran selesai.
"""

import math
import os
import struct
import subprocess
import threading
import time
import wave

import cast_manager
import db_manager
import google_assistant_client
import indonesian_tts
import settings_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHIME_WAV_PATH = os.path.join(BASE_DIR, "chime_bell.wav")
CHIME_OUTRO_WAV_PATH = os.path.join(BASE_DIR, "chime_outro.wav")

_restore_seq_lock = threading.Lock()
_restore_seq_by_dev = {}


def ensure_chime_wav(force=False):
    """Buat file chime pengumuman lembut & panjang (2.65 detik, 48kHz stereo 16-bit PCM)."""
    if not force and os.path.isfile(CHIME_WAV_PATH) and os.path.getsize(CHIME_WAV_PATH) > 4096:
        return CHIME_WAV_PATH

    sample_rate = 48000
    # 3 nada bel pengumuman klasik ala bandara/hotel yang hangat & bergema panjang:
    # E5 -> G5 -> C6 (+ resonansi lembut C5) total 2.65 detik
    notes = [
        (659.25, 0.00, 1.45, 0.34, 2.1),   # Ding 1 (E5)
        (783.99, 0.42, 1.55, 0.34, 2.0),   # Ding 2 (G5)
        (1046.50, 0.86, 1.75, 0.36, 1.7),  # Dong panjang (C6)
        (523.25, 0.86, 1.75, 0.16, 1.6),   # Resonansi hangat oktaf bawah (C5)
    ]
    total_dur = 2.65
    total_samples = int(sample_rate * total_dur)
    frames = bytearray()

    for i in range(total_samples):
        t = i / sample_rate
        val_l = 0.0
        val_r = 0.0
        for idx, (freq, start, dur, amp, decay_rate) in enumerate(notes):
            dt = t - start
            if 0.0 <= dt <= dur:
                # Attack halus (12ms) + decay bel bergema panjang + fade-out di ujung
                attack = min(1.0, dt / 0.012)
                decay = math.exp(-decay_rate * dt)
                tail_fade = min(1.0, (dur - dt) / 0.08)
                tone = (
                    0.74 * math.sin(2.0 * math.pi * freq * dt)
                    + 0.18 * math.sin(2.0 * math.pi * (freq * 2.0) * dt) * math.exp(-1.5 * dt)
                    + 0.08 * math.sin(2.0 * math.pi * (freq * 3.0) * dt) * math.exp(-3.0 * dt)
                )
                s = amp * attack * decay * tail_fade * tone
                # Sedikit stereo shimmer agar terasa lebih luas di speaker
                pan = 0.46 if idx % 2 == 0 else 0.54
                val_l += s * (1.0 - pan + 0.5)
                val_r += s * (pan + 0.5)
        sl = max(-32767, min(32767, int(val_l * 32767.0)))
        sr = max(-32767, min(32767, int(val_r * 32767.0)))
        frames.extend(struct.pack("<hh", sl, sr))

    with wave.open(CHIME_WAV_PATH, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))

    return CHIME_WAV_PATH


def ensure_outro_chime_wav(force=False):
    """Buat file nada penutup/selesai lembut (1.85 detik, menurun G5 -> E5 -> C5, 48kHz stereo)."""
    if not force and os.path.isfile(CHIME_OUTRO_WAV_PATH) and os.path.getsize(CHIME_OUTRO_WAV_PATH) > 4096:
        return CHIME_OUTRO_WAV_PATH

    sample_rate = 48000
    notes = [
        (783.99, 0.00, 0.90, 0.28, 2.5),  # G5 lembut
        (659.25, 0.32, 1.00, 0.28, 2.3),  # E5 lembut
        (523.25, 0.66, 1.18, 0.32, 2.0),  # C5 penutup hangat
    ]
    total_dur = 1.85
    total_samples = int(sample_rate * total_dur)
    frames = bytearray()

    for i in range(total_samples):
        t = i / sample_rate
        val_l = 0.0
        val_r = 0.0
        for idx, (freq, start, dur, amp, decay_rate) in enumerate(notes):
            dt = t - start
            if 0.0 <= dt <= dur:
                attack = min(1.0, dt / 0.012)
                decay = math.exp(-decay_rate * dt)
                tail_fade = min(1.0, (dur - dt) / 0.08)
                tone = (
                    0.76 * math.sin(2.0 * math.pi * freq * dt)
                    + 0.16 * math.sin(2.0 * math.pi * (freq * 2.0) * dt) * math.exp(-1.6 * dt)
                    + 0.08 * math.sin(2.0 * math.pi * (freq * 3.0) * dt) * math.exp(-3.0 * dt)
                )
                s = amp * attack * decay * tail_fade * tone
                pan = 0.54 if idx % 2 == 0 else 0.46
                val_l += s * (1.0 - pan + 0.5)
                val_r += s * (pan + 0.5)
        sl = max(-32767, min(32767, int(val_l * 32767.0)))
        sr = max(-32767, min(32767, int(val_r * 32767.0)))
        frames.extend(struct.pack("<hh", sl, sr))

    with wave.open(CHIME_OUTRO_WAV_PATH, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))

    return CHIME_OUTRO_WAV_PATH


def compose_announcement_audio(tts_audio_path, use_chime=True, repeat_count=1):
    """
    Gabungkan chime ding-dong + TTS (+ pengulangan jika repeat_count > 1) menjadi 1 file .m4a
    menggunakan /usr/bin/afconvert bawaan macOS. Fallback ke tts_audio_path jika gagal.
    """
    if not tts_audio_path or not os.path.isfile(tts_audio_path):
        return tts_audio_path
    if not use_chime and repeat_count <= 1:
        return tts_audio_path

    ts = int(time.time() * 1000)
    tts_wav = f"/tmp/qa_tts_{ts}.wav"
    merged_wav = f"/tmp/qa_merged_{ts}.wav"
    out_m4a = f"/tmp/qa_final_{ts}.m4a"

    try:
        # 1. Decode TTS MP3/AIFF -> 48kHz stereo 16-bit PCM WAV
        r1 = subprocess.run(
            ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@48000", "-c", "2", tts_audio_path, tts_wav],
            capture_output=True,
            timeout=10,
        )
        if r1.returncode != 0 or not os.path.isfile(tts_wav):
            return tts_audio_path

        with wave.open(tts_wav, "rb") as wf:
            tts_frames = wf.readframes(wf.getnframes())

        # 2. Gabung chime + jeda 0.3s + TTS (+ jeda 1.2s + TTS jika repeat > 1)
        silence_short = b"\x00" * (48000 * 4 * 3 // 10)   # 0.3s stereo 16-bit
        silence_gap = b"\x00" * (48000 * 4 * 12 // 10)    # 1.2s stereo 16-bit

        combined = bytearray()
        if use_chime:
            chime_path = ensure_chime_wav()
            with wave.open(chime_path, "rb") as cf:
                combined.extend(cf.readframes(cf.getnframes()))
            combined.extend(silence_short)

        reps = max(1, min(3, int(repeat_count)))
        for idx in range(reps):
            combined.extend(tts_frames)
            if idx < reps - 1:
                combined.extend(silence_gap)

        if use_chime:
            combined.extend(silence_short)
            outro_path = ensure_outro_chime_wav()
            with wave.open(outro_path, "rb") as of:
                combined.extend(of.readframes(of.getnframes()))

        with wave.open(merged_wav, "wb") as out_wf:
            out_wf.setnchannels(2)
            out_wf.setsampwidth(2)
            out_wf.setframerate(48000)
            out_wf.writeframes(bytes(combined))

        # 3. Encode kembali ke AAC .m4a (ringan & 100% kompatibel Google Home + Android TV HDMI)
        r2 = subprocess.run(
            ["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", "-b", "128000", merged_wav, out_m4a],
            capture_output=True,
            timeout=10,
        )
        if r2.returncode == 0 and os.path.isfile(out_m4a) and os.path.getsize(out_m4a) > 1024:
            return out_m4a
        return merged_wav if os.path.isfile(merged_wav) else tts_audio_path
    except Exception:
        return tts_audio_path
    finally:
        for tmp in (tts_wav, merged_wav):
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except Exception:
                pass


def _schedule_volume_restore(target_dev_name, restore_pct):
    """Kembalikan volume Google Home ke restore_pct setelah pemutaran selesai (dengan pengaman sequence)."""
    if restore_pct is None:
        return
    key = str(target_dev_name or "__default__")
    with _restore_seq_lock:
        seq = _restore_seq_by_dev.get(key, 0) + 1
        _restore_seq_by_dev[key] = seq

    def _worker():
        try:
            time.sleep(2.5)
            idle_streak = 0
            deadline = time.time() + 60.0
            while time.time() < deadline:
                with _restore_seq_lock:
                    if _restore_seq_by_dev.get(key) != seq:
                        return
                st = cast_manager.get_google_home_playback_status(target_dev_name)
                pstate = str(st.get("player_state") or "").upper()
                is_active = bool(st.get("is_playing")) or pstate in ("PLAYING", "BUFFERING")
                if not is_active:
                    idle_streak += 1
                    if idle_streak >= 2:
                        break
                else:
                    idle_streak = 0
                time.sleep(0.8)

            with _restore_seq_lock:
                if _restore_seq_by_dev.get(key) != seq:
                    return
            cast_manager.set_google_home_volume(
                volume_percent=max(0, min(100, int(restore_pct))),
                muted=False,
                target_device_name=target_dev_name,
            )
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def _resolve_target_devices(target_device):
    """Kembalikan list nama perangkat target; jika 'all' kembalikan semua perangkat Cast terdeteksi."""
    t = str(target_device or "").strip()
    if t.lower() == "all":
        devs = cast_manager.load_cached_devices()
        names = [d.get("name") for d in devs if d.get("name")]
        return names if names else [None]
    return [t or None]


def _get_current_volume_percent(target_dev):
    try:
        st = cast_manager.get_google_home_playback_status(target_dev)
        lvl = st.get("volume_level")
        if lvl is not None:
            return max(0, min(100, int(round(float(lvl) * 100))))
    except Exception:
        pass
    return 50


def execute_quick_action(qa_id=None, adhoc=None):
    """
    Jalankan Quick Action baik berdasarkan ID database maupun payload ad-hoc.
    """
    if qa_id:
        qa = db_manager.get_quick_action(qa_id)
        if not qa:
            return {"status": "error", "message": "Quick Action tidak ditemukan."}
    elif isinstance(adhoc, dict):
        qa = {
            "id": None,
            "title": str(adhoc.get("title") or adhoc.get("message") or adhoc.get("text") or "Broadcast")[:32],
            "message": str(adhoc.get("message") or adhoc.get("text") or "").strip(),
            "action_type": str(adhoc.get("action_type") or "tts").strip().lower(),
            "audio_id": str(adhoc.get("audio_id") or "").strip(),
            "voice_type": str(adhoc.get("voice_type") or "wanita1").strip().lower(),
            "volume": adhoc.get("volume", 70),
            "restore_volume": adhoc.get("restore_volume", 50),
            "use_chime": adhoc.get("use_chime", True),
            "repeat_count": adhoc.get("repeat_count", 1),
            "target_device": str(adhoc.get("target_device") or adhoc.get("device") or "").strip(),
        }
    else:
        return {"status": "error", "message": "Parameter Quick Action tidak valid."}

    atype = qa.get("action_type") or "tts"
    title = qa.get("title") or "Quick Action"
    msg = (qa.get("message") or "").strip()
    vol_raw = qa.get("volume", 70)
    vol, quiet_capped = settings_manager.apply_quiet_hours_volume(vol_raw)
    rvol_cfg = qa.get("restore_volume", 50)
    use_chime = bool(qa.get("use_chime", True))
    repeat_count = max(1, min(3, int(qa.get("repeat_count") or 1)))
    targets = _resolve_target_devices(qa.get("target_device"))

    # Catat volume sebelumnya per device (untuk mode restore_volume == -1)
    restore_map = {}
    for dev in targets:
        if rvol_cfg == -1:
            restore_map[dev] = _get_current_volume_percent(dev)
        elif rvol_cfg is not None:
            restore_map[dev] = max(0, min(100, int(rvol_cfg)))
        else:
            restore_map[dev] = None

    # 1. Mode Google Assistant
    if atype == "assistant":
        if not msg:
            return {"status": "error", "message": "Perintah Google Assistant tidak boleh kosong."}
        res = google_assistant_client.send_text_command_safe(msg)
        if res.get("status") != "success":
            return res
        audio_p = res.get("audio_path")
        if audio_p and os.path.isfile(audio_p):
            for dev in targets:
                if vol is not None:
                    try:
                        cast_manager.set_google_home_volume(volume_percent=int(vol), muted=False, target_device_name=dev)
                    except Exception:
                        pass
                cast_manager.broadcast_audio_file_to_google_home(
                    audio_p, target_device_name=dev, title=f"Google Assistant: {title[:36]}"
                )
                if restore_map.get(dev) is not None:
                    _schedule_volume_restore(dev, restore_map[dev])
            res["audio_url"] = f"/api/remote/tts_audio?file={os.path.basename(audio_p)}"
        r_label = f" → {restore_map[targets[0]]}%" if restore_map.get(targets[0]) is not None else ""
        res["message"] = f'🤖 "{title}" dikirim ke Google Assistant (Vol {vol}%{r_label})!'
        return res

    # 2. Mode Pustaka Audio/Video
    if atype == "audio":
        aud = db_manager.get_audio(qa.get("audio_id"))
        if not aud or not os.path.isfile(aud.get("file_path", "")):
            return {"status": "error", "message": "File audio/video di Pustaka Media tidak ditemukan."}
        last_res = {"status": "error", "message": "Gagal memutar media."}
        for dev in targets:
            if vol is not None:
                try:
                    cast_manager.set_google_home_volume(volume_percent=int(vol), muted=False, target_device_name=dev)
                except Exception:
                    pass
            last_res = cast_manager.cast_media_file_to_device(
                aud["file_path"], target_device_name=dev, title=aud["name"]
            )
            if last_res.get("status") == "success" and restore_map.get(dev) is not None:
                _schedule_volume_restore(dev, restore_map[dev])
        if last_res.get("status") == "success":
            r_label = f" → {restore_map[targets[0]]}%" if restore_map.get(targets[0]) is not None else ""
            last_res["message"] = f'🎵 "{title}" sedang diputar (Vol {vol}%{r_label})!'
        return last_res

    # 3. Mode Default: Broadcast TTS (+ Chime Ding-Dong + Repeat + Auto Volume 70% -> 50%)
    if not msg:
        return {"status": "error", "message": "Kalimat broadcast tidak boleh kosong."}

    v_clean = str(qa.get("voice_type") or "wanita1").lower()
    tts_path = indonesian_tts.generate_indonesian_audio(msg, voice_type=v_clean)
    if not tts_path or not os.path.isfile(tts_path):
        tts_path = indonesian_tts.generate_local_say_audio(msg)
    if not tts_path or not os.path.isfile(tts_path):
        return {"status": "error", "message": "Gagal membuat audio TTS."}

    final_audio = compose_announcement_audio(tts_path, use_chime=use_chime, repeat_count=repeat_count)

    last_res = {"status": "error", "message": "Gagal menyiarkan ke perangkat Cast."}
    for dev in targets:
        if vol is not None:
            try:
                cast_manager.set_google_home_volume(
                    volume_percent=max(0, min(100, int(vol))), muted=False, target_device_name=dev
                )
            except Exception:
                pass
        last_res = cast_manager.broadcast_audio_file_to_google_home(
            final_audio, target_device_name=dev, title=title
        )
        if last_res.get("status") == "success" and restore_map.get(dev) is not None:
            _schedule_volume_restore(dev, restore_map[dev])

    if last_res.get("status") == "success":
        extras = []
        if use_chime:
            extras.append("🔔 Chime")
        if repeat_count > 1:
            extras.append(f"🔁 {repeat_count}x")
        if vol is not None:
            r_lbl = f"→{restore_map[targets[0]]}%" if restore_map.get(targets[0]) is not None else ""
            extras.append(f"Vol {vol}%{r_lbl}")
        extra_str = f" ({', '.join(extras)})" if extras else ""
        last_res["message"] = f'✅ "{title}" berhasil disiarkan{extra_str}!'
    return last_res
