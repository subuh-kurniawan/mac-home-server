# Mac Home Server & Remote Command Center 🍏⚡

[![English](https://img.shields.io/badge/Language-English-blue.svg)](README.en.md)
[![Bahasa Indonesia](https://img.shields.io/badge/Language-Bahasa%20Indonesia-red.svg)](README.md)
[![GitHub Pages: Live Showcase](https://img.shields.io/badge/GitHub%20Pages-Live%20Showcase-2ea44f.svg?logo=githubpages&logoColor=white)](https://subuh-kurniawan.github.io/mac-home-server/en.html)
[![Platform: macOS](https://img.shields.io/badge/Platform-macOS%20Apple%20Silicon%20%7C%20Intel-black.svg?logo=apple)](https://apple.com)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![Google Cast: Supported](https://img.shields.io/badge/Google%20Cast-Google%20Home%20%7C%20Android%20TV-4285F4.svg?logo=google&logoColor=white)](https://google.com)
[![Telegram Bot: Integrated](https://img.shields.io/badge/Telegram%20Bot-Subuh__macBot-26A5E4.svg?logo=telegram&logoColor=white)](https://t.me/Subuh_macBot)
[![VPN: WireGuard](https://img.shields.io/badge/WireGuard-VPN%20Ready-88171A.svg?logo=wireguard&logoColor=white)](https://wireguard.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 🌐 **Live Website / GitHub Pages Showcase:** [https://subuh-kurniawan.github.io/mac-home-server/en.html](https://subuh-kurniawan.github.io/mac-home-server/en.html)
>
> 💡 **Giving Aging Macs a Second Life as a Modern Smart Home Media Cast Server & Command Hub**
>
> Many older Mac computers (such as vintage Mac minis, MacBooks, or older iMacs) still boast rock-solid hardware reliability, excellent power efficiency, and whisper-quiet operation, yet frequently end up gathering dust in a forgotten corner. This project was conceived to **repurpose older Mac hardware into a feature-packed, always-on 24/7 Home Media Cast Server & Remote Command Center**.
>
> By transforming an older Mac into an ultra-low-power private cloud hub, your entire smart home ecosystem—from Google Home/Nest speakers, Xiaomi TV Stick (Android TV), media streaming libraries, automated voice announcements, to remote orchestration via Telegram Bot and WireGuard VPN—can be commanded seamlessly without purchasing costly dedicated server appliances.

---

## 🔮 Future Development Roadmap

While initially crafted leveraging macOS-native capabilities (such as AppleScript, CoreAudio `afconvert`, VNC Apple Screen Sharing, and macOS `launchd`), **the architecture is modular and designed for multi-platform expansion**:

- 🐧 **Multi-Platform Support (Linux & Raspberry Pi):** Porting the core engine to single-board computers (Raspberry Pi 4/5) or Linux distributions (Ubuntu/Debian) for flexible hardware options.
- 🪟 **Windows OS Compatibility:** Adapting audio synthesis, terminal hooks, and hardware control routines for recycled Windows laptops/PCs.
- 🌐 **Expanded Smart Home Protocols:** Native Apple AirPlay 2 (HomePod), DLNA/UPnP support, and bridges to Home Assistant, MQTT, and Tuya/Bardi IoT devices.
- 📱 **Native Mobile Companion App:** Dedicated Android & iOS companion apps featuring lock-screen interactive widgets for 1-tap Quick Actions.

---

## 🏗️ System Architecture

The project adopts a modular layered architecture separating user interfaces, encrypted networking, core business orchestration, audio transcoding, and IoT device connectivity:

```mermaid
flowchart TD
    subgraph ClientLayer ["1. Client & Interface Layer"]
        WebUI["Web Dashboard PWA\n(Mobile & Desktop Responsive)"]
        TeleClient["Telegram Client App\n(Smartphone / Desktop)"]
        VNCClient["VNC Viewer / Apple Screen Sharing"]
    end

    subgraph NetworkLayer ["2. Networking & Security Layer"]
        WG["WireGuard VPN Tunnel\n(Encrypted Out-of-Home Access)"]
        LAN["Local Wi-Fi / Ethernet Subnet\n(192.168.0.x Subnet)"]
        SSL["Local TLS/SSL Encryption\n(HTTPS Port 8443)"]
    end

    subgraph CoreServer ["3. Core Server & Orchestration Engine (Mac mini)"]
        HTTPServer["HTTPS Web Server\n(server.py :8443)"]
        CastServer["Media Streaming Server\n(HTTP 206 Partial Content :8080)"]
        BotEngine["Telegram Bot Engine\n(telegram_bot.py)"]
        QAEngine["Quick Action & Broadcast Engine\n(quick_action_runner.py)"]
        ScheduleEngine["Smart Reminders Daemon\n(smart_reminders.py)"]
        DB[(SQLite WAL Database\ndatabase.db)]
    end

    subgraph AudioEngine ["4. Voice Synthesis & Audio Engine (CoreAudio)"]
        TTS["Edge TTS & macOS 'say'\n(Natural Indonesian / English Voices)"]
        ChimeGen["Algorithmic Chime Generator\n(Intro E5-G5-C6 + Outro G5-E5-C5)"]
        Afconvert["Apple afconvert CLI\n(Stereo 48kHz AAC .m4a)"]
    end

    subgraph DeviceLayer ["5. Smart Devices & IoT Integration"]
        GH["Google Home / Nest Speaker\n(Cast Audio & Auto-Volume)"]
        TVStick["Xiaomi TV Stick Mi\n(Android TV Cast & Remote v2)"]
        MacLocal["Mac Built-in Speaker & Display\n(CoreAudio afplay & AppleScript)"]
        GA["Google Assistant Cloud SDK\n(Embedded Assistant v1alpha2)"]
    end

    ClientLayer --> NetworkLayer
    NetworkLayer --> CoreServer
    CoreServer --> AudioEngine
    AudioEngine --> CoreServer
    CoreServer <--> DB
    CoreServer --> DeviceLayer
```

---

## 🔄 End-to-End Workflow Flowchart

The following diagram illustrates how user commands from the Web Dashboard or Telegram Bot are authenticated, synthesized into broadcast audio, and pushed to target endpoints:

```mermaid
flowchart TD
    Start(["User Initiates Action"]):::startNode
    InputChoice{"Command Gateway"}:::decisionNode

    Start --> InputChoice
    InputChoice -->|"Web Dashboard PWA"| WebAction["Access via Browser / PWA (Port 8443)"]
    InputChoice -->|"Telegram Bot"| TeleAction["Send Command / Voice / /qa"]

    WebAction --> AuthCheck{"Verify TLS Session"}
    TeleAction --> WhiteListCheck{"Check Chat ID Whitelist"}

    AuthCheck -->|Valid| Router["Server Dispatcher (server.py)"]
    WhiteListCheck -->|Authorized| BotRouter["Bot Dispatcher (telegram_bot.py)"]
    AuthCheck -->|Invalid| DeniedWeb["Access Denied (401 / 403)"]:::errorNode
    WhiteListCheck -->|Unauthorized| DeniedTele["Ignore Message"]:::errorNode

    BotRouter --> Router

    Router --> ActionType{"Operation Type"}:::decisionNode

    %% Flow 1: Quick Action Broadcast
    ActionType -->|"Quick Action / Broadcast"| QAFlow["Query QA Config (SQLite DB)"]
    QAFlow --> VolumeUp["1. Save Original Volume & Boost to 70%"]
    VolumeUp --> GenAudio["2. Synthesize Intro Chime + Voice TTS + Outro Chime"]
    GenAudio --> Transcode["3. Transcode to 48kHz AAC .m4a (afconvert)"]
    Transcode --> StreamCast["4. Stream via HTTP Server (Port 8080) to Google Home"]
    StreamCast --> WaitFinish["5. Monitor Cast Playback Completion"]
    WaitFinish --> VolumeRestore["6. Restore Volume to Baseline (50%)"]

    %% Flow 2: Google Assistant SDK
    ActionType -->|"Google Assistant Query"| GAFlow["Dispatch Text to gRPC Assistant API"]
    GAFlow --> CloudResp["Receive Assistant Audio Response"]
    CloudResp --> CastOrTele["Stream Audio to Google Home & Reply to Telegram"]

    %% Flow 3: Android TV Remote
    ActionType -->|"TV Stick Mi Control"| TVFlow["Connect TLS Port 6466 (AndroidTVRemote2)"]
    TVFlow --> ExecKey["Send D-Pad / Power / Volume / App Deep-link"]

    %% Flow 4: File Explorer & Media Playback
    ActionType -->|"File Manager / Media Library"| FileFlow["Stream Video/Audio Range 206 or Download Files"]

    %% Flow 5: macOS System Control
    ActionType -->|"Task Manager / Screen / Shell"| MacFlow["Execute AppleScript / ZSH Command / Screenshot"]

    VolumeRestore --> Done(["Complete / Success Response"]):::successNode
    CastOrTele --> Done
    ExecKey --> Done
    FileFlow --> Done
    MacFlow --> Done

    classDef startNode fill:#2563eb,stroke:#1d4ed8,stroke-width:2px,color:#fff
    classDef decisionNode fill:#f59e0b,stroke:#d97706,stroke-width:2px,color:#fff
    classDef successNode fill:#10b981,stroke:#059669,stroke-width:2px,color:#fff
    classDef errorNode fill:#ef4444,stroke:#dc2626,stroke-width:2px,color:#fff
```

---

## 🛠️ Technology Stack & Dependencies

| Category | Technology / Library | Version / Standard | System Role & Purpose |
| :--- | :--- | :--- | :--- |
| **Language & Runtime** | **Python** | 3.10+ | Primary server backend, automation workers, and bot orchestration. |
| | **JavaScript (ES6+)** | Vanilla JS | High-performance PWA frontend logic with zero framework overhead. |
| | **HTML5 & CSS3** | Modern PWA | Responsive mobile-first liquid glass aesthetics, dark/light themes. |
| **Web & Streaming Server** | **Python `http.server` & `ssl`** | Built-in | Multi-threaded HTTPS Server (Port 8443) with X.509 TLS encryption. |
| | **HTTP Range 206 Server** | Custom Engine | Media streaming server (Port 8080) with byte-range seeking for Chromecast. |
| **Cast & Smart Home IoT** | **PyChromecast** | Latest | Google Cast v2 protocol over TLS (Port 8009) for audio/video streaming. |
| | **AndroidTVRemote2** | Latest | Android TV Remote v2 protocol (TLS Port 6466) for D-Pad, Power, Volume, and app launching. |
| | **Google Assistant SDK** | `v1alpha2` (gRPC) | Headless programmatic text command execution without physical mics. |
| **Audio Processing** | **macOS CoreAudio `afconvert`** | Native CLI | Lossless transcoding of PCM/WAV to Google Cast-compliant AAC (`.m4a`, 48kHz Stereo). |
| | **Algorithmic Chime Bell** | Python `wave` + `math` | Pure synthetic harmonic chime generator (Intro: *E5-G5-C6*, Outro: *G5-E5-C5*). |
| | **Edge TTS & macOS `say`** | Cloud / Built-in | Natural multilingual Text-to-Speech synthesis (Indonesian & English). |
| | **CoreAudio `afplay`** | Native CLI | Hardware audio playback directly through Mac built-in speakers. |
| **Database & Config** | **SQLite 3** | WAL Mode | Persistent ACID storage for Media Library, Macro Scenarios, Schedules, and Quick Actions. |
| | **JSON Config Engine** | Schema-validated | Static configuration for cast targets (`cast_devices.json`) and Telegram bot (`bot_config.json`). |
| **Networking & Remote** | **WireGuard VPN** | UDP Protocol | Encrypted peer-to-peer VPN tunnel for secure remote access without port forwarding. |
| | **Telegram Bot API** | `python-telegram-bot` | 2-way bot interface for telemetry, remote commands, voice notes, and screenshots. |
| | **Apple Screen Sharing** | VNC (Port 5900) | Native high-performance macOS remote desktop access. |
| **System Automation** | **AppleScript (`osascript`)** | Native macOS | OS-level interaction with clipboard, hardware audio levels, and app launching. |
| | **macOS `launchd`** | LaunchDaemons | System supervisor ensuring 24/7 continuous operation and automatic reboot recovery. |

---

## 📸 Dashboard Screenshots (Visual Preview)

### 1. 📁 File Manager (Kodbox-Style Web Explorer)
Access and organize Mac mini storage directly from your phone or laptop with fast navigation, file uploads, Range 206 video streaming, and ZIP extraction.
![File Manager](docs/screenshots/01-file-manager.webp)

---

### 2. 📢📺 Cast Center & Google Assistant Hub
Unified control to broadcast audio to **Google Home Speakers**, stream video/IPTV to **TV Stick Mi**, and execute text commands to the Google Assistant API.
![Cast Center & Assistant Hub](docs/screenshots/02-cast-and-assistant-hub.webp)

---

### 3. 🎛️ Live Audio Monitor & Instant Stop
Real-time playback monitoring on Google Home and Mac speakers with collision prevention (*Anti-Overlap*), volume sliders, and emergency *Stop All*.
![Live Audio Monitor](docs/screenshots/03-live-audio-monitor.webp)

---

### 4. 🎵🎬 Media Library Server (SQLite Database)
Store and play music, audiobooks, or videos on the server with 1-click casting to TV Stick Mi, Google Home (automatic audio extractor), or local Mac speakers.
![Media Library](docs/screenshots/04-media-library-server.webp)

---

### 5. ⏰ Smart Voice Schedules (Cast & Say)
Automate spoken announcements based on specific dates, countdown timers, or recurring daily schedules with natural TTS voices.
![Voice Schedules](docs/screenshots/05-smart-voice-schedules.webp)

---

### 6. 📊 Mac Task Manager & Process Monitor
Monitor CPU, memory usage, and running macOS processes with the ability to terminate applications directly from the web interface.
![Task Manager](docs/screenshots/06-mac-task-manager.webp)

---

### 7. ⚡ 1-Click Automation & Universal Clipboard Sync
Macro routines (*Night Mode, Focus Mode, Away Mode*) combined with bi-directional clipboard synchronization between your Mac and mobile devices.
![Automation & Clipboard](docs/screenshots/07-automation-scenarios.webp)

---

### 8. 💻 Mac mini Web Terminal
Execute secure zsh/bash shell commands directly from your browser.
![Web Terminal](docs/screenshots/08-web-terminal.webp)

---

### 9. 🚀 App Launcher & URL Opener
Launch applications (Google Chrome, Safari, VS Code, Terminal) or open target URLs on the Mac display with one touch.
![App Launcher](docs/screenshots/09-app-launcher.webp)

---

### 10. 📺 Live Screen Sharing & Remote Desktop (VNC)
Live visual preview of the Mac display with direct connectivity to macOS Apple Screen Sharing over VNC.
![Live Screen VNC](docs/screenshots/10-live-screen-vnc.webp)

---

## 🌟 Key Highlights

1. **⚡ Quick Action Broadcast & Audio Engine:**
   - **Floating Action Button (FAB `⚡`):** Instant access from any dashboard view.
   - **Harmonic Chimes:** Synthetic opening chime (*E5-G5-C6*, 2.65s) + Natural TTS Voice + Closing chime (*G5-E5-C5*, 1.85s).
   - **Auto Volume Restore:** Volume automatically boosts (default 70%) during broadcast, then restores to 50% (or baseline) once done.
   - **Quiet Hours:** Automatic nighttime volume capping (e.g., 22:00–06:00 max 40%).
   - Full CRUD via Web Dashboard & Telegram Bot.

2. **📢 Static-First Google Cast Hub:**
   - Supports Google Home/Nest Audio & Android TV.
   - *Static-First Mode:* `<1ms` response time utilizing static IP cache without flooding the Wi-Fi router with subnet scans.
   - Android TV Remote v2 (D-Pad, Power, Volume, Home, Back, App Launcher).

3. **🤖 Telegram Bot Controller (`@Subuh_macBot`):**
   - Full Mac control on the go via Telegram.
   - Capture desktop screenshot (`/screen`), check system health (`/status`), execute shell commands (`/cmd`).
   - Quick Action management (`/qa`, `/qaadd`, 1-tap voice toggles).
   - Push voice messages directly to household speakers.

4. **🧠 Google Assistant Embedded SDK (v1alpha2):**
   - Execute Google Assistant commands programmatically via text API without physical microphone hardware.
   - Spoken answers are streamed to Google Home and posted back to Telegram.

5. **⚙️ Centralized Settings Center (5-Tab CRUD):**
   - Telegram Bot Management (Token, Whitelisted Chat IDs with name labels).
   - Cast Devices & Android TV IP Management.
   - Audio Preferences & Quiet Hours volume limit.
   - Google Assistant SDK OAuth Status.
   - SSL Certificate Info & 1-Click System Backup (ZIP).

---

## 📋 System Requirements

- **OS:** macOS (Apple Silicon M1/M2/M3/M4 or Intel Core i5/i7)
- **Runtime:** Python 3.10+
- **Network:** LAN Wi-Fi / Ethernet, optional WireGuard VPN
- **Compatible Devices:** Google Home / Nest Audio / Chromecast / Android TV Stick

---

## 🚀 Quickstart Installation

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/subuh-kurniawan/mac-home-server.git
   cd mac-home-server
   ```

2. **Install Python Dependencies:**
   ```bash
   pip3 install pychromecast google-assistant-grpc google-auth-oauthlib "cryptography<45" androidtvremote2
   ```

3. **Initial Configuration:**
   Copy the example configuration templates:
   ```bash
   cp bot_config.example.json bot_config.json
   cp cast_devices.example.json cast_devices.json
   cp tv_remote_paired.example.json tv_remote_paired.json
   ```
   Edit `bot_config.json` with your Telegram Bot Token and authorized Chat IDs.

4. **Generate Local SSL Certificate (HTTPS):**
   ```bash
   openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=localhost"
   ```

5. **Run Server & Bot Daemon:**
   ```bash
   # Run HTTPS Dashboard & Media Streaming Server (Port 8443 & 8080)
   python3 server.py &

   # Run Telegram Bot Controller
   python3 telegram_bot.py &
   ```

6. **Access Dashboard:**
   Open your browser at `https://localhost:8443` or via your local LAN / WireGuard IP.

---

## 🛡️ Security & Privacy Notice

This repository has been fully sanitized:
- Private keys (`.key`), local certificates (`.pem`, `.crt`), and SQLite databases (`database.db`) are ignored by `.gitignore`.
- Telegram bot tokens, chat IDs, and Google OAuth credentials are excluded (sample templates provided).
- Personal audio libraries and backup archives are excluded from source tracking.

---

## 📄 License

This project is licensed under the **MIT License**.
