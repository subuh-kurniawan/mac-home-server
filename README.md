# Mac Home Server & Remote Command Center 🍏⚡

Sistem terintegrasi untuk kontrol Mac mini, Google Home, Android TV (Mi TV Stick), Pustaka Media, dan Telegram Bot dari jarak jauh (LAN & WireGuard VPN).

---

## 🌟 Fitur Utama

1. **Web Dashboard Responsif (HTTPS Port 8443)**
   - PWA (*Progressive Web App*) dapat di-install ke Home Screen iPhone / Android.
   - File Manager (*Kodbox-style*): upload, download, streaming video (Range 206), ZIP/UNZIP.
   - Live Screen & VNC viewer.
   - Task Manager & Process Monitor macOS.
   - Audio & Video Media Player terpusat.

2. **⚡ Quick Action Broadcast & Engine Audio Canggih**
   - Floating Action Button (FAB `⚡`) di semua layar.
   - **Bel Pengumuman Bandara/Hotel:** Nada pembuka (*E5-G5-C6*, 2.65s) + Suara TTS (*Gadis/Ardi/Google ID*) + Nada penutup (*G5-E5-C5*, 1.85s).
   - **Auto Volume Restore:** Volume naik ke 70% saat pengumuman, lalu otomatis kembali ke 50% (atau volume sebelumnya) setelah siaran selesai.
   - **Jam Tenang (Quiet Hours):** Pembatasan volume otomatis pada jam malam (misal 22:00–06:00).
   - CRUD lengkap (Create, Read, Update, Delete) via Web Dashboard & Telegram Bot.

3. **📢 Google Cast & Android TV Hub**
   - Mendukung Google Home Speaker & Android TV (Xiaomi TV Stick).
   - Universal Cast URL: YouTube, Shorts, IPTV HLS (`.m3u8`), Video MP4, Audio MP3, dan Gambar.
   - Mode *Static-First*: Akses instan `<1ms` menggunakan IP statis tanpa membebani Wi-Fi dengan scan 254 IP.
   - Remote Android TV V2 (D-Pad, Power, Volume, Home, Back, Launcher Aplikasi).

4. **🤖 Telegram Bot Controller (`@Subuh_macBot`)**
   - Kendali penuh Mac dari mana saja via chat Telegram.
   - Screenshot layar Mac (`/screen`), Status sistem (`/status`), Jalankan terminal (`/cmd`).
   - Manajemen Quick Action (`/qa`, `/qaadd`, edit parameter 1-tap).
   - Pengiriman Voice Note & Audio langsung ke speaker Google Home.

5. **🧠 Google Assistant Embedded SDK (v1alpha2)**
   - Eksekusi perintah Google Assistant langsung via API teks tanpa mikrofon.
   - Otomatis memutar balasan suara ke speaker Google Home & chat Telegram.

6. **⚙️ Pengaturan Terpusat (5 Tab CRUD)**
   - Manajemen Telegram Bot (Token, Whitelist Chat IDs + Label nama).
   - Manajemen Perangkat Cast & IP Android TV.
   - Preferensi Audio & Jam Tenang.
   - Status Google Assistant SDK.
   - Info Sertifikat SSL & 1-Klik Backup Sistem.

---

## 📋 Persyaratan Sistem

- macOS (Tested on macOS Monterey / Ventura / Sonoma / Sequoia).
- Python 3.10+.
- Perangkat Google Cast (Google Home / Nest Mini / Chromecast / Android TV).
- Akun Telegram (untuk Bot).

---

## 🚀 Panduan Instalasi & Menjalankan

1. **Clone Repository:**
   ```bash
   git clone https://github.com/subuh-kurniawan/mac-home-server.git
   cd mac-home-server
   ```

2. **Install Dependensi Python:**
   ```bash
   pip3 install pychromecast google-assistant-grpc google-auth-oauthlib "cryptography<45" androidtvremote2
   ```

3. **Konfigurasi Awal:**
   Salin berkas contoh konfigurasi:
   ```bash
   cp bot_config.example.json bot_config.json
   cp cast_devices.example.json cast_devices.json
   cp tv_remote_paired.example.json tv_remote_paired.json
   ```
   Edit `bot_config.json` dengan Token Telegram Bot Anda dan Chat ID Anda.

4. **Generate Sertifikat SSL Lokal (HTTPS):**
   ```bash
   openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=localhost"
   ```

5. **Jalankan Server & Bot:**
   ```bash
   # Menjalankan server web HTTPS & Media Server
   python3 server.py

   # Menjalankan Telegram Bot di background
   python3 telegram_bot.py
   ```

6. **Akses Dashboard:**
   Buka browser di `https://127.0.0.1:8443` atau melalui IP LAN / WireGuard Anda.

---

## 🛡️ Keamanan & Privasi

Repositori ini telah disterilkan dari seluruh data sensitif:
- Token bot dan API credentials tidak disertakan.
- Kunci privat (`.key`) dan sertifikat lokal (`.pem`, `.crt`) diabaikan oleh `.gitignore`.
- Database lokal (`database.db`) dan berkas pustaka audio diabaikan oleh `.gitignore`.

---

## 📄 Lisensi

Distributed under the MIT License.
