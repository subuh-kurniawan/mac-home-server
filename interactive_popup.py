#!/usr/bin/env python3
"""
Modul Interaktif Pop-Up Dialog untuk macOS.
Memunculkan dialog pop-up di layar Mac dengan kolom input teks
sehingga orang di depan komputer bisa membalas chat secara langsung ke Telegram atau Web.
"""

import subprocess
import time


def send_banner_notification(title, message, sound=True):
    """Notifikasi banner biasa di pojok kanan atas layar Mac."""
    clean_title = title.replace("\\", "\\\\").replace('"', '\\"')
    clean_msg = message.replace("\\", "\\\\").replace('"', '\\"')
    snd = ' sound name "default"' if sound else ""
    script = f'display notification "{clean_msg}" with title "{clean_title}"{snd}'
    try:
        subprocess.run(["osascript", "-e", script], timeout=3)
        return True
    except Exception:
        return False


def show_interactive_popup(sender_name, message, timeout_seconds=120):
    """
    Memunculkan kotak dialog interaktif di tengah layar monitor Mac.
    Memiliki input text box agar orang di Mac bisa mengetik balasan langsung.

    Mengembalikan tuple: (status, reply_text)
      - ("REPLY", "teks balasan dari pengguna di Mac")
      - ("CLOSED", None) jika pengguna mengklik Tutup/Batal
      - ("TIMEOUT", None) jika timeout habis tanpa aktivitas
      - ("ERROR", str(error)) jika gagal
    """
    clean_sender = sender_name.replace("\\", "\\\\").replace('"', '\\"')
    clean_msg = message.replace("\\", "\\\\").replace('"', '\\"')

    # AppleScript modal dialog di atas semua aplikasi yang sedang aktif
    script = f"""
    tell application "System Events"
        activate
        try
            set dlgRes to display dialog "Pesan dari {clean_sender}:\\n\\n\\"{clean_msg}\\"\\n\\nKetik balasan Anda di bawah ini:" ¬
                default answer "" ¬
                with title "💬 Pesan Masuk ({clean_sender})" ¬
                buttons {{"Tutup", "Kirim Balasan"}} ¬
                default button "Kirim Balasan" ¬
                with icon note ¬
                giving up after {timeout_seconds}
            
            if gave up of dlgRes is true then
                return "TIMEOUT"
            else if button returned of dlgRes is "Kirim Balasan" then
                set txt to text returned of dlgRes
                if txt is not "" then
                    return "REPLY:" & txt
                else
                    return "CLOSED"
                end if
            else
                return "CLOSED"
            end if
        on error errMsg
            return "CLOSED"
        end try
    end tell
    """
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 5,
        )
        out = proc.stdout.strip()
        if out.startswith("REPLY:"):
            return "REPLY", out[6:].strip()
        elif out == "TIMEOUT":
            return "TIMEOUT", None
        else:
            return "CLOSED", None
    except subprocess.TimeoutExpired:
        return "TIMEOUT", None
    except Exception as e:
        return "ERROR", str(e)


if __name__ == "__main__":
    print("Mencoba dialog interaktif (timeout 3 detik)...")
    status, reply = show_interactive_popup(
        "Tes Otomatis", "Apakah pop-up ini berfungsi?", timeout_seconds=3
    )
    print("Hasil:", status, "| Balasan:", reply)
