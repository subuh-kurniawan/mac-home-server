#!/usr/bin/env python3
"""
Mac Remote Command Center - Standalone GUI Client.
Aplikasi klien desktop mandiri tanpa perlu browser.
Cukup masukkan IP server Mac (misal: 10.66.66.4 atau 192.168.0.200) lalu klik Sambungkan.
Mendukung: Intercom Pop-up Chat, Google Home Announcer, Kendali Hardware Mac, File Manager, dan Terminal.
"""

import html
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "client_config.json")


def get_ssl_context():
    """SSL Context tanpa verifikasi untuk sertifikat self-signed HTTPS."""
    try:
        return ssl._create_unverified_context()
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


SSL_CTX = get_ssl_context()


class MacRemoteClientApp:

    def __init__(self, root):
        self.root = root
        self.root.title("Mac Remote Command Center - Native Client")
        self.root.geometry("860x680")
        self.root.minsize(760, 560)

        # Muat konfigurasi tersimpan
        self.config = self.load_config()
        self.server_ip = self.config.get("server_ip", "10.66.66.4")
        self.server_port = self.config.get("server_port", "8443")
        self.current_remote_path = "~"
        self.is_connected = False

        self.setup_styles()
        self.create_widgets()

        # Cek koneksi awal di background
        self.root.after(300, self.connect_server)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"server_ip": "10.66.66.4", "server_port": "8443"}

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(
                    {
                        "server_ip": self.entry_ip.get().strip(),
                        "server_port": self.entry_port.get().strip(),
                    },
                    f,
                    indent=2,
                )
        except Exception:
            pass

    def get_base_url(self):
        ip = self.entry_ip.get().strip() or "10.66.66.4"
        port = self.entry_port.get().strip() or "8443"
        return f"https://{ip}:{port}"

    def api_request(self, endpoint, method="GET", data=None, timeout=10):
        url = f"{self.get_base_url()}{endpoint}"
        try:
            req_data = None
            headers = {"User-Agent": "MacRemoteNativeClient/1.0"}
            if data is not None:
                req_data = json.dumps(data).encode("utf-8")
                headers["Content-Type"] = "application/json"

            req = urllib.request.Request(
                url, data=req_data, headers=headers, method=method
            )
            with urllib.request.urlopen(
                req, timeout=timeout, context=SSL_CTX
            ) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    return json.loads(raw)
                except Exception:
                    return {"status": "success", "raw": raw}
        except urllib.error.HTTPError as e:
            try:
                err_data = json.loads(e.read().decode("utf-8"))
                return {
                    "status": "error",
                    "message": err_data.get("message", str(e)),
                }
            except Exception:
                return {"status": "error", "message": f"HTTP {e.code}: {e.reason}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ================= UI & STYLES =================
    def setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use("clam")

        # Ultra-Premium Dark Obsidian Palette
        self.bg_color = "#070a11"       # Deep Obsidian
        self.panel_color = "#111827"    # Frosted Slate Dark
        self.card_color = "#162032"     # Elevated Panel
        self.accent_color = "#0ea5e9"   # Electric Sky Cyan
        self.accent_glow = "#38bdf8"    # Neon Cyan
        self.text_color = "#f8fafc"     # Pure White
        self.text_muted = "#94a3b8"     # Crisp Muted
        self.success_color = "#10b981"  # Neon Emerald
        self.danger_color = "#f43f5e"   # Crimson
        self.border_color = "#243452"   # Subtle Slate Border

        self.root.configure(bg=self.bg_color)

        self.style.configure(".", background=self.bg_color, foreground=self.text_color)
        self.style.configure(
            "TFrame", background=self.bg_color
        )
        self.style.configure(
            "Panel.TFrame", background=self.panel_color, relief="flat"
        )

        self.style.configure(
            "TLabel", background=self.bg_color, foreground=self.text_color, font=("Helvetica", 10)
        )
        self.style.configure(
            "Panel.TLabel", background=self.panel_color, foreground=self.text_color, font=("Helvetica", 10)
        )
        self.style.configure(
            "Header.TLabel", background=self.panel_color, foreground=self.accent_glow, font=("Helvetica", 12, "bold")
        )

        self.style.configure(
            "TButton",
            background="#1e293b",
            foreground="#f8fafc",
            font=("Helvetica", 10, "bold"),
            padding=[12, 6],
            relief="flat",
            borderwidth=0,
        )
        self.style.map(
            "TButton",
            background=[("active", "#334155"), ("disabled", "#0f172a")],
            foreground=[("disabled", "#64748b")],
        )

        self.style.configure(
            "Primary.TButton",
            background=self.accent_color,
            foreground="#ffffff",
            font=("Helvetica", 10, "bold"),
            padding=[12, 6],
            relief="flat",
        )
        self.style.map(
            "Primary.TButton",
            background=[("active", "#0284c7"), ("disabled", "#0f172a")],
        )

        self.style.configure(
            "Success.TButton",
            background=self.success_color,
            foreground="#ffffff",
            font=("Helvetica", 10, "bold"),
            padding=[12, 6],
            relief="flat",
        )
        self.style.map(
            "Success.TButton",
            background=[("active", "#059669"), ("disabled", "#0f172a")],
        )

        self.style.configure(
            "Danger.TButton",
            background=self.danger_color,
            foreground="#ffffff",
            font=("Helvetica", 10, "bold"),
            padding=[12, 6],
            relief="flat",
        )
        self.style.map(
            "Danger.TButton",
            background=[("active", "#e11d48"), ("disabled", "#0f172a")],
        )

        self.style.configure(
            "TNotebook", background=self.bg_color, borderwidth=0
        )
        self.style.configure(
            "TNotebook.Tab",
            background="#111827",
            foreground="#94a3b8",
            padding=[16, 9],
            font=("Helvetica", 10, "bold"),
            borderwidth=0,
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", self.accent_color)],
            foreground=[("selected", "#ffffff")],
        )

        self.style.configure(
            "Treeview",
            background="#111827",
            foreground="#f8fafc",
            fieldbackground="#111827",
            rowheight=28,
            font=("Helvetica", 10),
            borderwidth=0,
        )
        self.style.configure(
            "Treeview.Heading",
            background="#1e293b",
            foreground=self.accent_glow,
            font=("Helvetica", 10, "bold"),
            padding=6,
        )
        self.style.map("Treeview", background=[("selected", "#0284c7")])

    def create_widgets(self):
        # 1. TOP BAR: Server Connection
        top_frame = tk.Frame(self.root, bg=self.panel_color, pady=10, padx=14)
        top_frame.pack(fill="x", side="top")

        lbl_title = tk.Label(
            top_frame,
            text="🍎 Mac Remote PRO",
            font=("Helvetica", 13, "bold"),
            bg=self.panel_color,
            fg="#38bdf8",
        )
        lbl_title.pack(side="left", padx=(0, 16))

        tk.Label(
            top_frame,
            text="IP Server:",
            bg=self.panel_color,
            fg=self.text_muted,
            font=("Helvetica", 10),
        ).pack(side="left", padx=(0, 4))
        self.entry_ip = tk.Entry(
            top_frame,
            width=15,
            bg="#0f172a",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Helvetica", 10),
        )
        self.entry_ip.insert(0, self.server_ip)
        self.entry_ip.pack(side="left", padx=(0, 8))

        tk.Label(
            top_frame,
            text="Port:",
            bg=self.panel_color,
            fg=self.text_muted,
            font=("Helvetica", 10),
        ).pack(side="left", padx=(0, 4))
        self.entry_port = tk.Entry(
            top_frame,
            width=6,
            bg="#0f172a",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Helvetica", 10),
        )
        self.entry_port.insert(0, self.server_port)
        self.entry_port.pack(side="left", padx=(0, 10))

        btn_connect = ttk.Button(
            top_frame,
            text="🔗 Sambungkan",
            style="Primary.TButton",
            command=self.connect_server,
        )
        btn_connect.pack(side="left", padx=(0, 14))

        self.lbl_status_icon = tk.Label(
            top_frame,
            text="🔴",
            font=("Helvetica", 12),
            bg=self.panel_color,
            fg="#ef4444",
        )
        self.lbl_status_icon.pack(side="left")

        self.lbl_status_text = tk.Label(
            top_frame,
            text="Belum Terhubung",
            font=("Helvetica", 9),
            bg=self.panel_color,
            fg=self.text_muted,
        )
        self.lbl_status_text.pack(side="left", padx=(4, 0))

        # 2. TABBED NOTEBOOK
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(10, 12))

        # Tab Pages
        self.tab_chat = ttk.Frame(self.notebook)
        self.tab_cast = ttk.Frame(self.notebook)
        self.tab_hardware = ttk.Frame(self.notebook)
        self.tab_files = ttk.Frame(self.notebook)
        self.tab_terminal = ttk.Frame(self.notebook)
        self.tab_media = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_chat, text="💬 Intercom & Pop-Up")
        self.notebook.add(self.tab_cast, text="📢 Google Home")
        self.notebook.add(self.tab_hardware, text="🎛️ Kendali Mac")
        self.notebook.add(self.tab_files, text="📁 File Manager")
        self.notebook.add(self.tab_terminal, text="🚀 Terminal & Aplikasi")
        self.notebook.add(self.tab_media, text="🎵 Media Player")

        # Build each tab
        self.build_chat_tab()
        self.build_cast_tab()
        self.build_hardware_tab()
        self.build_files_tab()
        self.build_terminal_tab()
        self.build_media_tab()

    def connect_server(self):
        self.save_config()
        self.lbl_status_icon.configure(text="🟡", fg="#eab308")
        self.lbl_status_text.configure(text="Sedang menyambungkan...")

        def _do():
            res = self.api_request("/api/status", timeout=4)
            if res.get("status") == "success":
                self.is_connected = True
                self.root.after(0, lambda: self.on_connected(res))
            else:
                self.is_connected = False
                msg = res.get("message", "Gagal menghubungi server")
                self.root.after(0, lambda: self.on_connect_failed(msg))

        threading.Thread(target=_do, daemon=True).start()

    def on_connected(self, data):
        self.lbl_status_icon.configure(text="🟢", fg="#10b981")
        hostname = data.get("hostname", "Mac mini")
        self.lbl_status_text.configure(text=f"Terhubung ({hostname})")
        # Muat daftar perangkat Google Home & folder awal
        self.scan_cast_speakers()
        self.load_remote_folder("~")

    def on_connect_failed(self, msg):
        self.lbl_status_icon.configure(text="🔴", fg="#ef4444")
        self.lbl_status_text.configure(text=f"Terputus ({msg[:25]})")

    # ================= TAB 1: INTERCOM POP-UP CHAT =================
    def build_chat_tab(self):
        frame = tk.Frame(self.tab_chat, bg=self.bg_color)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        lbl_desc = tk.Label(
            frame,
            text="💬 Kirim Pop-Up ke Layar Mac yang bisa langsung dibalas oleh orang di depan komputer:",
            bg=self.bg_color,
            fg=self.text_muted,
            font=("Helvetica", 9),
        )
        lbl_desc.pack(anchor="w", pady=(0, 6))

        # Chat Log
        self.chat_log = ScrolledText(
            frame,
            wrap="word",
            bg=self.panel_color,
            fg="#f8fafc",
            font=("Helvetica", 10),
            height=14,
            relief="flat",
        )
        self.chat_log.pack(fill="both", expand=True, pady=(0, 8))
        self.chat_log.insert(
            "end",
            "Selamat datang di Intercom Pop-Up Chat!\nKetik pesan di bawah dan klik 'Kirim Pop-Up & Tunggu Balasan'.\n"
            + "-" * 60
            + "\n",
        )
        self.chat_log.configure(state="disabled")

        # Input Area
        input_box = tk.Frame(frame, bg=self.bg_color)
        input_box.pack(fill="x")

        self.chat_input = tk.Entry(
            input_box,
            bg=self.panel_color,
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Helvetica", 11),
        )
        self.chat_input.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=5)
        self.chat_input.bind("<Return>", lambda e: self.send_popup_chat())

        self.btn_send_popup = ttk.Button(
            input_box,
            text="💬 Kirim Pop-Up (Bisa Dibalas)",
            style="Success.TButton",
            command=self.send_popup_chat,
        )
        self.btn_send_popup.pack(side="left", padx=(0, 6))

        btn_banner = ttk.Button(
            input_box, text="🔔 Banner Saja", command=self.send_banner_chat
        )
        btn_banner.pack(side="left", padx=(0, 6))

        btn_voice = ttk.Button(
            input_box, text="🎙️ Kirim Suara ke Mac", command=self.send_voice_file_to_mac
        )
        btn_voice.pack(side="left")

    def append_chat_log(self, text, prefix=""):
        self.chat_log.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.chat_log.insert("end", f"[{ts}] {prefix}{text}\n")
        self.chat_log.see("end")
        self.chat_log.configure(state="disabled")

    def send_popup_chat(self):
        msg = self.chat_input.get().strip()
        if not msg:
            return
        self.chat_input.delete(0, "end")
        self.append_chat_log(msg, prefix="📤 Anda -> Mac: ")
        self.btn_send_popup.configure(
            text="⏳ Menunggu Balasan...", state="disabled"
        )

        def _do():
            res = self.api_request(
                "/api/remote/ask",
                method="POST",
                data={"sender": "Klien Desktop", "message": msg, "timeout": 120},
                timeout=130,
            )
            self.root.after(0, lambda: self.on_popup_reply(res))

        threading.Thread(target=_do, daemon=True).start()

    def on_popup_reply(self, res):
        self.btn_send_popup.configure(
            text="💬 Kirim Pop-Up (Bisa Dibalas)", state="normal"
        )
        if res.get("status") == "success":
            act = res.get("result")
            reply = res.get("reply")
            if act == "REPLY" and reply:
                self.append_chat_log(f'"{reply}"', prefix="📥 BALASAN DARI MAC: ")
            elif act == "CLOSED":
                self.append_chat_log("Dialog ditutup tanpa balasan teks.", prefix="ℹ️ ")
            elif act == "TIMEOUT":
                self.append_chat_log("Waktu tunggu habis (tidak ada respon dari Mac).", prefix="⏳ ")
        else:
            self.append_chat_log(res.get("message", "Gagal mengirim"), prefix="❌ ")

    def send_banner_chat(self):
        msg = self.chat_input.get().strip()
        if not msg:
            return
        self.chat_input.delete(0, "end")
        self.append_chat_log(msg, prefix="🔔 Banner -> Mac: ")

        def _do():
            self.api_request(
                "/api/remote/notify",
                method="POST",
                data={"title": "Pesan dari Klien", "message": msg},
            )

        threading.Thread(target=_do, daemon=True).start()

    def send_voice_file_to_mac(self):
        fpath = filedialog.askopenfilename(
            title="Pilih File Suara untuk Diputar di Speaker Mac",
            filetypes=[("Audio Files", "*.wav *.mp3 *.m4a *.webm *.ogg *.flac *.caf *.aiff"), ("All Files", "*.*")]
        )
        if not fpath:
            return

        fname = os.path.basename(fpath)
        self.append_chat_log(f"Mengirim '{fname}' ke speaker Mac...", prefix="🎙️ ")

        def _do():
            try:
                with open(fpath, "rb") as f:
                    audio_data = f.read()

                url = f"{self.get_base_url()}/api/remote/play_voice"
                req = urllib.request.Request(
                    url,
                    data=audio_data,
                    headers={
                        "User-Agent": "MacRemoteNativeClient/1.0",
                        "Content-Type": "audio/octet-stream"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
                    raw = resp.read().decode("utf-8")
                    res = json.loads(raw)
                if res.get("status") == "success":
                    self.root.after(0, lambda: self.append_chat_log("Suara berhasil berbunyi di speaker Mac mini!", prefix="🔊 "))
                else:
                    self.root.after(0, lambda: self.append_chat_log(f"Gagal memutar di Mac: {res.get('message')}", prefix="❌ "))
            except Exception as e:
                self.root.after(0, lambda: self.append_chat_log(f"Error kirim suara: {str(e)}", prefix="❌ "))

        threading.Thread(target=_do, daemon=True).start()

    # ================= TAB 2: GOOGLE HOME ANNOUNCER =================
    def build_cast_tab(self):
        frame = tk.Frame(self.tab_cast, bg=self.bg_color)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        # Panel Pemilihan Speaker
        p_select = tk.Frame(frame, bg=self.panel_color, padx=14, pady=12)
        p_select.pack(fill="x", pady=(0, 12))

        tk.Label(
            p_select,
            text="📢 Speaker Google Cast Terpilih:",
            bg=self.panel_color,
            fg="#38bdf8",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        sel_box = tk.Frame(p_select, bg=self.panel_color)
        sel_box.pack(fill="x")

        self.cast_combo = ttk.Combobox(
            sel_box,
            values=["Family Room speaker (Google Home - 192.168.0.105)"],
            font=("Helvetica", 10),
        )
        self.cast_combo.set("Family Room speaker (Google Home - 192.168.0.105)")
        self.cast_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))

        btn_scan_cast = ttk.Button(
            sel_box, text="🔍 Pindai Ulang Speaker", command=self.scan_cast_speakers
        )
        btn_scan_cast.pack(side="left")

        # Pilihan Mode Suara (3 Suara TTS + 1 File Rekaman Asli)
        p_mode = tk.Frame(frame, bg=self.panel_color, padx=14, pady=8)
        p_mode.pack(fill="x", pady=(0, 10))

        tk.Label(
            p_mode,
            text="PILIH SUARA BROADCAST:",
            bg=self.panel_color,
            fg="#94a3b8",
            font=("Helvetica", 9, "bold"),
        ).pack(side="left", padx=(0, 10))

        self.cast_voice_mode = tk.StringVar(value="wanita1")
        rb_wanita1 = tk.Radiobutton(
            p_mode,
            text="👩 1. Wanita (Gadis)",
            variable=self.cast_voice_mode,
            value="wanita1",
            bg=self.panel_color,
            fg="#f8fafc",
            selectcolor="#070a11",
            activebackground=self.panel_color,
            activeforeground="#38bdf8",
            font=("Helvetica", 9),
        )
        rb_wanita1.pack(side="left", padx=(0, 10))

        rb_pria = tk.Radiobutton(
            p_mode,
            text="👨 2. Pria (Ardi)",
            variable=self.cast_voice_mode,
            value="pria",
            bg=self.panel_color,
            fg="#f8fafc",
            selectcolor="#070a11",
            activebackground=self.panel_color,
            activeforeground="#38bdf8",
            font=("Helvetica", 9),
        )
        rb_pria.pack(side="left", padx=(0, 10))

        rb_wanita2 = tk.Radiobutton(
            p_mode,
            text="👧 3. Wanita 2 (Google ID)",
            variable=self.cast_voice_mode,
            value="wanita2",
            bg=self.panel_color,
            fg="#f8fafc",
            selectcolor="#070a11",
            activebackground=self.panel_color,
            activeforeground="#38bdf8",
            font=("Helvetica", 9),
        )
        rb_wanita2.pack(side="left")

        # Panel Input Pengumuman
        p_msg = tk.Frame(frame, bg=self.panel_color, padx=14, pady=12)
        p_msg.pack(fill="both", expand=True)

        tk.Label(
            p_msg,
            text="Tulis Kalimat Pengumuman (Bahasa Indonesia Alami):",
            bg=self.panel_color,
            fg="#f8fafc",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        self.cast_text = ScrolledText(
            p_msg,
            wrap="word",
            bg="#0f172a",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Helvetica", 11),
            height=6,
        )
        self.cast_text.pack(fill="both", expand=True, pady=(0, 10))
        self.cast_text.insert(
            "end",
            "Perhatian, makan malam sudah siap di meja makan. Silakan berkumpul!",
        )

        btn_row = tk.Frame(p_msg, bg=self.panel_color)
        btn_row.pack(fill="x")

        self.btn_broadcast = ttk.Button(
            btn_row,
            text="📢 Siarkan Suara ke Google Home",
            style="Primary.TButton",
            command=self.broadcast_cast,
        )
        self.btn_broadcast.pack(side="left", padx=(0, 10))

        btn_voice_file = ttk.Button(
            btn_row,
            text="🎙️ 4. Pilih & Siarkan File Suara Asli",
            command=self.broadcast_voice_file,
        )
        btn_voice_file.pack(side="left")

        self.lbl_cast_result = tk.Label(
            btn_row,
            text="",
            bg=self.panel_color,
            fg=self.text_muted,
            font=("Helvetica", 9),
        )
        self.lbl_cast_result.pack(side="left", padx=(12, 0))

    def scan_cast_speakers(self):
        def _do():
            res = self.api_request("/api/cast/devices?refresh=true")
            if res.get("status") == "success" and res.get("devices"):
                opts = [
                    f"{d['name']} ({d.get('model', 'Google Home')} - {d['host']})"
                    for d in res["devices"]
                ]
                self.root.after(0, lambda: self.cast_combo.configure(values=opts))
                if opts:
                    self.root.after(0, lambda: self.cast_combo.set(opts[0]))

        threading.Thread(target=_do, daemon=True).start()

    def broadcast_cast(self):
        text = self.cast_text.get("1.0", "end").strip()
        if not text:
            return
        selected = self.cast_combo.get()
        # Ambil IP jika ada
        target = selected
        if " - " in selected and selected.endswith(")"):
            target = selected.split(" - ")[-1].rstrip(")")

        self.btn_broadcast.configure(
            text="⏳ Menyiarkan...", state="disabled"
        )
        self.lbl_cast_result.configure(text="Sedang mengirim audio...", fg="#eab308")

        mode = getattr(self, "cast_voice_mode", None)
        mode_val = mode.get() if mode else "wanita1"

        def _do():
            res = self.api_request(
                "/api/cast/broadcast",
                method="POST",
                data={"text": text, "device": target, "mode": "google", "voice_type": mode_val},
                timeout=25,
            )
            self.root.after(0, lambda: self.on_broadcast_done(res))

        threading.Thread(target=_do, daemon=True).start()

    def broadcast_voice_file(self):
        fpath = filedialog.askopenfilename(
            title="Pilih File Suara Asli untuk Disiarkan",
            filetypes=[("Audio Files", "*.mp3 *.wav *.m4a *.webm *.ogg *.flac"), ("All Files", "*.*")]
        )
        if not fpath:
            return

        selected = self.cast_combo.get()
        target = selected
        if " - " in selected and selected.endswith(")"):
            target = selected.split(" - ")[-1].rstrip(")")

        self.lbl_cast_result.configure(text="Mengunggah & menyiarkan suara asli...", fg="#eab308")

        def _do():
            try:
                with open(fpath, "rb") as f:
                    audio_data = f.read()
                
                url = f"{self.get_base_url()}/api/cast/voice_note"
                req = urllib.request.Request(
                    url,
                    data=audio_data,
                    headers={
                        "User-Agent": "MacRemoteNativeClient/1.0",
                        "Content-Type": "audio/octet-stream"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
                    raw = resp.read().decode("utf-8")
                    res = json.loads(raw)
                self.root.after(0, lambda: self.on_broadcast_done(res))
            except Exception as e:
                self.root.after(0, lambda: self.on_broadcast_done({"status": "error", "message": str(e)}))

        threading.Thread(target=_do, daemon=True).start()

    def on_broadcast_done(self, res):
        self.btn_broadcast.configure(
            text="📢 Siarkan Suara ke Google Home", state="normal"
        )
        if res.get("status") == "success":
            self.lbl_cast_result.configure(
                text="✅ Berhasil disiarkan ke speaker!", fg="#10b981"
            )
        else:
            self.lbl_cast_result.configure(
                text=f"❌ {res.get('message', 'Gagal')}", fg="#ef4444"
            )

    # ================= TAB 3: KENDALI MAC HARDWARE =================
    def build_hardware_tab(self):
        frame = tk.Frame(self.tab_hardware, bg=self.bg_color)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        # 1. Volume
        p_vol = tk.Frame(frame, bg=self.panel_color, padx=14, pady=10)
        p_vol.pack(fill="x", pady=(0, 8))

        tk.Label(
            p_vol,
            text="🔊 Volume Speaker Mac:",
            bg=self.panel_color,
            fg="#38bdf8",
            font=("Helvetica", 10, "bold"),
        ).pack(side="left", padx=(0, 10))
        self.vol_scale = ttk.Scale(p_vol, from_=0, to=100, orient="horizontal")
        self.vol_scale.set(70)
        self.vol_scale.pack(side="left", fill="x", expand=True, padx=(0, 10))

        ttk.Button(
            p_vol, text="Set Volume", command=self.apply_volume
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            p_vol, text="🔇 Mute", command=self.mute_volume
        ).pack(side="left")

        # 2. TTS Mac Speaker
        p_tts = tk.Frame(frame, bg=self.panel_color, padx=14, pady=10)
        p_tts.pack(fill="x", pady=(0, 8))

        tk.Label(
            p_tts,
            text="🗣️ Ucapkan di Speaker Mac:",
            bg=self.panel_color,
            fg="#38bdf8",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        tts_row = tk.Frame(p_tts, bg=self.panel_color)
        tts_row.pack(fill="x")
        self.tts_entry = tk.Entry(
            tts_row,
            bg="#0f172a",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Helvetica", 10),
        )
        self.tts_entry.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)
        self.tts_entry.insert(
            0, "Halo! Suara bahasa Indonesia ini langsung berbunyi di speaker Mac."
        )
        ttk.Button(
            tts_row, text="🔊 Ucapkan", command=self.speak_mac
        ).pack(side="left")

        # 3. Screenshot & Sleep
        p_screen = tk.Frame(frame, bg=self.panel_color, padx=14, pady=10)
        p_screen.pack(fill="x", pady=(0, 8))

        tk.Label(
            p_screen,
            text="🖥️ Layar Monitor Mac & Live Screen:",
            bg=self.panel_color,
            fg="#38bdf8",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        sc_row = tk.Frame(p_screen, bg=self.panel_color)
        sc_row.pack(fill="x")

        ttk.Button(
            sc_row,
            text="📺 Buka Live Screen Viewer",
            style="Primary.TButton",
            command=self.open_live_screen_viewer,
        ).pack(side="left", padx=(0, 10))
        ttk.Button(
            sc_row,
            text="📸 Ambil Screenshot",
            command=self.fetch_screenshot,
        ).pack(side="left", padx=(0, 10))
        ttk.Button(
            sc_row, text="🔒 Kunci / Matikan Layar", command=self.sleep_display
        ).pack(side="left")

        # 3b. Smart Automation Scenarios
        p_scen = tk.Frame(frame, bg=self.panel_color, padx=14, pady=10)
        p_scen.pack(fill="x", pady=(0, 8))

        tk.Label(
            p_scen,
            text="⚡ Smart Automation Presets (Skenario 1-Klik):",
            bg=self.panel_color,
            fg="#38bdf8",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        scen_row = tk.Frame(p_scen, bg=self.panel_color)
        scen_row.pack(fill="x")

        ttk.Button(
            scen_row,
            text="🌙 Mode Malam",
            command=lambda: self.run_scenario("night"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            scen_row,
            text="💼 Mode Fokus Kerja",
            command=lambda: self.run_scenario("focus"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            scen_row,
            text="🚪 Mode Tinggalkan Meja",
            command=lambda: self.run_scenario("away"),
        ).pack(side="left")

        # 4. Universal Clipboard
        p_clip = tk.Frame(frame, bg=self.panel_color, padx=14, pady=10)
        p_clip.pack(fill="both", expand=True)

        tk.Label(
            p_clip,
            text="📋 Universal Clipboard Sinkronisasi:",
            bg=self.panel_color,
            fg="#38bdf8",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        self.clip_text = tk.Text(
            p_clip,
            height=3,
            bg="#0f172a",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Helvetica", 10),
        )
        self.clip_text.pack(fill="both", expand=True, pady=(0, 6))

        clip_btns = tk.Frame(p_clip, bg=self.panel_color)
        clip_btns.pack(fill="x")
        ttk.Button(
            clip_btns, text="📋 Baca dari Mac", command=self.get_clipboard
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            clip_btns, text="📤 Kirim ke Clipboard Mac", command=self.set_clipboard
        ).pack(side="left")

    def apply_volume(self):
        val = int(self.vol_scale.get())
        threading.Thread(
            target=lambda: self.api_request(
                "/api/remote/volume", method="POST", data={"volume": val}
            ),
            daemon=True,
        ).start()

    def mute_volume(self):
        self.vol_scale.set(0)
        threading.Thread(
            target=lambda: self.api_request(
                "/api/remote/volume", method="POST", data={"volume": 0}
            ),
            daemon=True,
        ).start()

    def speak_mac(self):
        txt = self.tts_entry.get().strip()
        if not txt:
            return
        threading.Thread(
            target=lambda: self.api_request(
                "/api/remote/tts", method="POST", data={"text": txt}
            ),
            daemon=True,
        ).start()

    def sleep_display(self):
        threading.Thread(
            target=lambda: self.api_request(
                "/api/remote/displaysleep", method="POST", data={}
            ),
            daemon=True,
        ).start()
        messagebox.showinfo(
            "Info", "Perintah mematikan layar telah dikirim ke Mac."
        )

    def fetch_screenshot(self):
        # Unduh screenshot langsung dan simpan ke file lokal sementara
        def _do():
            url = f"{self.get_base_url()}/api/remote/screenshot?format=jpg&t={int(time.time())}"
            try:
                local_path = os.path.join(
                    BASE_DIR, "screenshot_preview.jpg"
                )
                req = urllib.request.Request(
                    url, headers={"User-Agent": "MacRemoteClient/1.0"}
                )
                with urllib.request.urlopen(
                    req, timeout=10, context=SSL_CTX
                ) as resp:
                    with open(local_path, "wb") as f:
                        f.write(resp.read())
                # Buka file preview dengan aplikasi penampil gambar default sistem
                if sys.platform == "darwin":
                    subprocess.run(["open", local_path])
                elif sys.platform == "win32":
                    os.startfile(local_path)
                else:
                    subprocess.run(["xdg-open", local_path])
            except Exception as e:
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "Gagal", f"Gagal mengambil screenshot: {e}"
                    ),
                )

        threading.Thread(target=_do, daemon=True).start()

    def get_clipboard(self):
        def _do():
            res = self.api_request("/api/remote/clipboard/get")
            if res.get("status") == "success":
                txt = res.get("text", "")
                self.root.after(0, lambda: self._update_clip(txt))

        threading.Thread(target=_do, daemon=True).start()

    def _update_clip(self, txt):
        self.clip_text.delete("1.0", "end")
        self.clip_text.insert("end", txt)

    def set_clipboard(self):
        txt = self.clip_text.get("1.0", "end").strip()
        threading.Thread(
            target=lambda: self.api_request(
                "/api/remote/clipboard/set", method="POST", data={"text": txt}
            ),
            daemon=True,
        ).start()
        messagebox.showinfo("Sukses", "Teks telah dikirim ke clipboard Mac!")

    # ================= TAB 4: FILE MANAGER =================
    def build_files_tab(self):
        frame = tk.Frame(self.tab_files, bg=self.bg_color)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        # Nav bar
        nav = tk.Frame(frame, bg=self.panel_color, padx=10, pady=8)
        nav.pack(fill="x", pady=(0, 8))

        ttk.Button(
            nav, text="⬆️ Naik", width=8, command=self.folder_up
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            nav, text="🔄 Refresh", width=8, command=lambda: self.load_remote_folder(self.current_remote_path)
        ).pack(side="left", padx=(0, 8))

        self.path_entry = tk.Entry(
            nav,
            bg="#0f172a",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Helvetica", 10),
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=3)
        self.path_entry.bind(
            "<Return>", lambda e: self.load_remote_folder(self.path_entry.get().strip())
        )

        ttk.Button(
            nav, text="⬇️ Unduh", command=self.download_selected_file
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            nav, text="⬆️ Unggah", command=self.upload_file_to_remote
        ).pack(side="left")

        # Treeview files
        cols = ("name", "type", "size", "modified")
        self.file_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.file_tree.heading("name", text="Nama File / Folder")
        self.file_tree.heading("type", text="Tipe")
        self.file_tree.heading("size", text="Ukuran")
        self.file_tree.heading("modified", text="Modifikasi")

        self.file_tree.column("name", width=340)
        self.file_tree.column("type", width=90, anchor="center")
        self.file_tree.column("size", width=100, anchor="e")
        self.file_tree.column("modified", width=150, anchor="center")

        self.file_tree.pack(fill="both", expand=True)
        self.file_tree.bind("<Double-1>", self.on_file_double_click)

        # Context Menu (Klik Kanan) untuk Operasi CRUD
        self.file_context_menu = tk.Menu(
            self.root,
            tearoff=0,
            bg="#1e293b",
            fg="#f8fafc",
            activebackground="#3b82f6",
            activeforeground="#ffffff",
        )
        self.file_context_menu.add_command(
            label="📂 Buka / Masuk Folder", command=self.on_context_open
        )
        self.file_context_menu.add_command(
            label="⬇️ Unduh File", command=self.download_selected_file
        )
        self.file_context_menu.add_command(
            label="✏️ Ganti Nama", command=self.rename_selected_file
        )
        self.file_context_menu.add_command(
            label="🗑️ Hapus", command=self.delete_selected_file
        )
        self.file_context_menu.add_separator()
        self.file_context_menu.add_command(
            label="📁 Buat Folder Baru", command=self.create_remote_folder
        )
        self.file_context_menu.add_command(
            label="📄 Buat Berkas Baru", command=self.create_remote_file
        )
        self.file_context_menu.add_command(
            label="⬆️ Unggah File", command=self.upload_file_to_remote
        )
        self.file_context_menu.add_command(
            label="🔄 Segarkan Folder",
            command=lambda: self.load_remote_folder(self.current_remote_path),
        )

        self.file_tree.bind("<Button-2>", self.show_tree_context_menu)
        self.file_tree.bind("<Button-3>", self.show_tree_context_menu)

    def load_remote_folder(self, rel_path):
        def _do():
            ep = f"/api/list?path={urllib.parse.quote(rel_path)}"
            res = self.api_request(ep)
            if res.get("status") == "success":
                self.current_remote_path = res.get("current_path", rel_path)
                items = res.get("items", [])
                self.root.after(0, lambda: self._render_files(items))

        threading.Thread(target=_do, daemon=True).start()

    def _render_files(self, items):
        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, self.current_remote_path)
        for row in self.file_tree.get_children():
            self.file_tree.delete(row)

        for it in items:
            is_dir = it.get("is_dir", False)
            icon = "📁 " if is_dir else "📄 "
            t_str = "Folder" if is_dir else "File"
            sz_str = "--" if is_dir else self._fmt_size(it.get("size", 0))
            self.file_tree.insert(
                "",
                "end",
                values=(icon + it["name"], t_str, sz_str, it.get("modified", "")),
            )

    def _fmt_size(self, b):
        for unit in ["B", "KB", "MB", "GB"]:
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"

    def show_tree_context_menu(self, event):
        row_id = self.file_tree.identify_row(event.y)
        if row_id:
            self.file_tree.selection_set(row_id)
        try:
            self.file_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.file_context_menu.grab_release()

    def on_context_open(self):
        sel = self.file_tree.selection()
        if not sel:
            return
        item_vals = self.file_tree.item(sel[0], "values")
        name = item_vals[0].replace("📁 ", "").replace("📄 ", "")
        is_folder = item_vals[1] == "Folder"
        if is_folder:
            new_p = (
                os.path.join(self.current_remote_path, name)
                if self.current_remote_path != "~"
                else name
            )
            self.load_remote_folder(new_p)
        else:
            self.download_selected_file()

    def rename_selected_file(self):
        sel = self.file_tree.selection()
        if not sel:
            return
        item_vals = self.file_tree.item(sel[0], "values")
        old_name = item_vals[0].replace("📁 ", "").replace("📄 ", "")
        new_name = simpledialog.askstring(
            "Ganti Nama",
            f"Masukkan nama baru untuk '{old_name}':",
            initialvalue=old_name,
        )
        if not new_name or new_name == old_name:
            return
        old_rel = (
            os.path.join(self.current_remote_path, old_name)
            if self.current_remote_path != "~"
            else old_name
        )

        def _do():
            res = self.api_request(
                "/api/rename",
                method="POST",
                data={"path": old_rel, "new_name": new_name},
            )
            if res.get("status") == "success":
                self.root.after(
                    0, lambda: self.load_remote_folder(self.current_remote_path)
                )
            else:
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "Gagal", res.get("message", "Gagal mengganti nama")
                    ),
                )

        threading.Thread(target=_do, daemon=True).start()

    def delete_selected_file(self):
        sel = self.file_tree.selection()
        if not sel:
            return
        item_vals = self.file_tree.item(sel[0], "values")
        name = item_vals[0].replace("📁 ", "").replace("📄 ", "")
        is_folder = item_vals[1] == "Folder"
        if not messagebox.askyesno(
            "Hapus",
            f"Apakah Anda yakin ingin menghapus {'folder' if is_folder else 'file'} '{name}'?",
        ):
            return
        rel = (
            os.path.join(self.current_remote_path, name)
            if self.current_remote_path != "~"
            else name
        )

        def _do():
            res = self.api_request(
                "/api/delete", method="POST", data={"path": rel}
            )
            if res.get("status") == "success":
                self.root.after(
                    0, lambda: self.load_remote_folder(self.current_remote_path)
                )
            else:
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "Gagal", res.get("message", "Gagal menghapus")
                    ),
                )

        threading.Thread(target=_do, daemon=True).start()

    def create_remote_folder(self):
        name = simpledialog.askstring("Folder Baru", "Masukkan nama folder baru:")
        if not name:
            return

        def _do():
            res = self.api_request(
                "/api/mkdir",
                method="POST",
                data={"path": self.current_remote_path, "name": name},
            )
            if res.get("status") == "success":
                self.root.after(
                    0, lambda: self.load_remote_folder(self.current_remote_path)
                )
            else:
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "Gagal", res.get("message", "Gagal membuat folder")
                    ),
                )

        threading.Thread(target=_do, daemon=True).start()

    def create_remote_file(self):
        name = simpledialog.askstring(
            "Berkas Baru",
            "Masukkan nama file baru (contoh: catatan.txt):",
            initialvalue="catatan.txt",
        )
        if not name:
            return

        def _do():
            res = self.api_request(
                "/api/newfile",
                method="POST",
                data={"path": self.current_remote_path, "name": name},
            )
            if res.get("status") == "success":
                self.root.after(
                    0, lambda: self.load_remote_folder(self.current_remote_path)
                )
            else:
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "Gagal", res.get("message", "Gagal membuat file")
                    ),
                )

        threading.Thread(target=_do, daemon=True).start()
        sel = self.file_tree.selection()
        if not sel:
            return
        item_vals = self.file_tree.item(sel[0], "values")
        name = item_vals[0].replace("📁 ", "").replace("📄 ", "")
        is_folder = item_vals[1] == "Folder"

        if is_folder:
            new_p = (
                os.path.join(self.current_remote_path, name)
                if self.current_remote_path != "~"
                else name
            )
            self.load_remote_folder(new_p)

    def folder_up(self):
        curr = self.current_remote_path.rstrip("/")
        if not curr or curr in ["~", "."]:
            return
        parent = os.path.dirname(curr)
        self.load_remote_folder(parent if parent else "~")

    def download_selected_file(self):
        sel = self.file_tree.selection()
        if not sel:
            messagebox.showwarning("Pilih File", "Pilih file yang ingin diunduh.")
            return
        item_vals = self.file_tree.item(sel[0], "values")
        name = item_vals[0].replace("📁 ", "").replace("📄 ", "")
        if item_vals[1] == "Folder":
            messagebox.showinfo("Folder", "Fitur unduh saat ini khusus file.")
            return

        save_path = filedialog.asksaveasfilename(initialfile=name)
        if not save_path:
            return

        rel = (
            os.path.join(self.current_remote_path, name)
            if self.current_remote_path != "~"
            else name
        )

        def _do():
            url = f"{self.get_base_url()}/api/download?path={urllib.parse.quote(rel)}"
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "MacRemoteClient/1.0"}
                )
                with urllib.request.urlopen(req, context=SSL_CTX) as resp:
                    with open(save_path, "wb") as f:
                        f.write(resp.read())
                self.root.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Sukses", f"File berhasil diunduh ke:\n{save_path}"
                    ),
                )
            except Exception as e:
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "Gagal", f"Gagal mengunduh: {e}"
                    ),
                )

        threading.Thread(target=_do, daemon=True).start()

    def upload_file_to_remote(self):
        filepath = filedialog.askopenfilename()
        if not filepath:
            return
        filename = os.path.basename(filepath)

        def _do():
            # Menggunakan endpoint upload multipart / raw
            try:
                boundary = "----WebKitFormBoundaryMacRemoteClient"
                with open(filepath, "rb") as f:
                    file_content = f.read()

                body = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                    f"Content-Type: application/octet-stream\r\n\r\n"
                ).encode("utf-8") + file_content + f"\r\n--{boundary}--\r\n".encode("utf-8")

                url = f"{self.get_base_url()}/api/upload?path={urllib.parse.quote(self.current_remote_path)}"
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "User-Agent": "MacRemoteClient/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, context=SSL_CTX) as resp:
                    resp.read()

                self.root.after(
                    0,
                    lambda: [
                        messagebox.showinfo("Sukses", f"File '{filename}' berhasil diunggah!"),
                        self.load_remote_folder(self.current_remote_path),
                    ],
                )
            except Exception as e:
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "Gagal", f"Gagal mengunggah file: {e}"
                    ),
                )

        threading.Thread(target=_do, daemon=True).start()

    # ================= TAB 5: TERMINAL & APLIKASI =================
    def build_terminal_tab(self):
        frame = tk.Frame(self.tab_terminal, bg=self.bg_color)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        # Quick Apps
        p_apps = tk.Frame(frame, bg=self.panel_color, padx=12, pady=10)
        p_apps.pack(fill="x", pady=(0, 10))

        tk.Label(
            p_apps,
            text="🚀 Buka Aplikasi di Mac:",
            bg=self.panel_color,
            fg="#38bdf8",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        apps_row = tk.Frame(p_apps, bg=self.panel_color)
        apps_row.pack(fill="x")

        for app_name in [
            "Google Chrome",
            "Safari",
            "Terminal",
            "Notes",
            "Music",
            "System Settings",
            "Calculator",
        ]:
            ttk.Button(
                apps_row,
                text=app_name,
                command=lambda an=app_name: self.open_app(an),
            ).pack(side="left", padx=(0, 6))

        # Terminal Runner
        p_term = tk.Frame(frame, bg=self.panel_color, padx=12, pady=10)
        p_term.pack(fill="both", expand=True)

        tk.Label(
            p_term,
            text="💻 Interactive Terminal Runner:",
            bg=self.panel_color,
            fg="#38bdf8",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        t_row = tk.Frame(p_term, bg=self.panel_color)
        t_row.pack(fill="x", pady=(0, 8))

        self.term_entry = tk.Entry(
            t_row,
            bg="#0f172a",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Menlo", 10),
        )
        self.term_entry.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)
        self.term_entry.insert(0, "uptime")
        self.term_entry.bind("<Return>", lambda e: self.run_terminal_cmd())

        ttk.Button(
            t_row,
            text="Jalankan Perintah",
            style="Primary.TButton",
            command=self.run_terminal_cmd,
        ).pack(side="left")

        self.term_output = ScrolledText(
            p_term,
            wrap="word",
            bg="#0f172a",
            fg="#a7f3d0",
            font=("Menlo", 9),
            relief="flat",
        )
        self.term_output.pack(fill="both", expand=True)

    def open_app(self, app_name):
        threading.Thread(
            target=lambda: self.api_request(
                "/api/remote/openapp", method="POST", data={"app": app_name}
            ),
            daemon=True,
        ).start()
        messagebox.showinfo(
            "Aplikasi Dibuka", f"Memerintahkan Mac untuk membuka {app_name}."
        )

    def run_terminal_cmd(self):
        cmd = self.term_entry.get().strip()
        if not cmd:
            return
        self.term_output.insert("end", f"$ {cmd}\n")

        def _do():
            res = self.api_request(
                "/api/terminal", method="POST", data={"command": cmd}
            )
            out = (
                res.get("output", "")
                if res.get("status") == "success"
                else f"Error: {res.get('message')}"
            )
            self.root.after(0, lambda: self._append_term_out(out))

        threading.Thread(target=_do, daemon=True).start()

    def _append_term_out(self, out):
        self.term_output.insert("end", f"{out}\n\n")
        self.term_output.see("end")

    # ================= TAB 6: MEDIA PLAYER =================
    def build_media_tab(self):
        frame = tk.Frame(self.tab_media, bg=self.bg_color)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        p_m = tk.Frame(frame, bg=self.panel_color, padx=16, pady=20)
        p_m.pack(fill="x")

        self.lbl_now_playing = tk.Label(
            p_m,
            text="🎵 Musik Mac (Apple Music / Spotify)",
            bg=self.panel_color,
            fg="#38bdf8",
            font=("Helvetica", 12, "bold"),
        )
        self.lbl_now_playing.pack(pady=(0, 10))

        self.lbl_track_info = tk.Label(
            p_m,
            text="(Klik tombol kontrol untuk memutar lagu)",
            bg=self.panel_color,
            fg="#f8fafc",
            font=("Helvetica", 11),
        )
        self.lbl_track_info.pack(pady=(0, 16))

        c_row = tk.Frame(p_m, bg=self.panel_color)
        c_row.pack()

        ttk.Button(
            c_row,
            text="⏮️ Prev",
            width=8,
            command=lambda: self.control_media("prev"),
        ).pack(side="left", padx=6)
        ttk.Button(
            c_row,
            text="⏯️ Play / Pause",
            style="Primary.TButton",
            command=lambda: self.control_media("playpause"),
        ).pack(side="left", padx=6)
        ttk.Button(
            c_row,
            text="⏭️ Next",
            width=8,
            command=lambda: self.control_media("next"),
        ).pack(side="left", padx=6)
        ttk.Button(
            c_row,
            text="🔄 Status",
            width=8,
            command=lambda: self.control_media("info"),
        ).pack(side="left", padx=6)

    def control_media(self, action):
        def _do():
            res = self.api_request(
                "/api/remote/music", method="POST", data={"action": action}
            )
            out = res.get("output", "OK")
            self.root.after(0, lambda: self.lbl_track_info.configure(text=out))

        threading.Thread(target=_do, daemon=True).start()


def main():
    root = tk.Tk()
    app = MacRemoteClientApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()


    def run_scenario(self, preset):
        def _do():
            res = self.api_request("/api/scenario", method="POST", data={"preset": preset})
            msg = res.get("message", "Perintah berhasil dikirim")
            self.root.after(0, lambda: messagebox.showinfo("Smart Skenario", msg))
        threading.Thread(target=_do, daemon=True).start()

    def open_live_screen_viewer(self):
        win = tk.Toplevel(self.root)
        win.title("📺 Live Screen Remote Desktop - Mac mini")
        win.geometry("960x600")
        win.configure(bg="#070a11")

        top_bar = tk.Frame(win, bg="#111827", padx=10, pady=8)
        top_bar.pack(fill="x", side="top")

        lbl_info = tk.Label(
            top_bar,
            text="💡 Klik pada gambar layar untuk mengirim klik langsung ke Mac!",
            bg="#111827",
            fg="#38bdf8",
            font=("Helvetica", 10),
        )
        lbl_info.pack(side="left", padx=(0, 14))

        auto_refresh_var = tk.BooleanVar(value=False)

        canvas_frame = tk.Frame(win, bg="#000000")
        canvas_frame.pack(fill="both", expand=True)

        lbl_img = tk.Label(canvas_frame, bg="#000000")
        lbl_img.pack(fill="both", expand=True)

        photo_ref = [None]
        is_updating = [False]

        def update_frame():
            if is_updating[0]:
                return
            is_updating[0] = True
            def _fetch():
                url = f"{self.get_base_url()}/api/remote/screenshot?format=png&t={int(time.time()*1000)}"
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "MacRemoteClient/1.0"})
                    with urllib.request.urlopen(req, timeout=8, context=SSL_CTX) as resp:
                        png_data = resp.read()
                    
                    def _render():
                        try:
                            # Use base64 or temporary file for PhotoImage
                            import base64
                            b64 = base64.b64encode(png_data).decode("ascii")
                            photo = tk.PhotoImage(data=b64)
                            # Subsample if too big for window
                            w = photo.width()
                            if w > 1200:
                                photo = photo.subsample(2, 2)
                            photo_ref[0] = photo
                            lbl_img.configure(image=photo)
                        except Exception as ex:
                            pass
                        finally:
                            is_updating[0] = False

                    win.after(0, _render)
                except Exception:
                    is_updating[0] = False

            threading.Thread(target=_fetch, daemon=True).start()

        def on_screen_click(event):
            # Compute relative click position
            w = lbl_img.winfo_width()
            h = lbl_img.winfo_height()
            if w > 0 and h > 0:
                px = event.x / w
                py = event.y / h
                def _send():
                    self.api_request("/api/remote/screen_click", method="POST", data={"px": px, "py": py})
                    win.after(350, update_frame)
                threading.Thread(target=_send, daemon=True).start()

        lbl_img.bind("<Button-1>", on_screen_click)

        def toggle_auto():
            if auto_refresh_var.get() and win.winfo_exists():
                update_frame()
                win.after(2000, toggle_auto)

        chk_auto = tk.Checkbutton(
            top_bar,
            text="▶️ Auto-Refresh (2s)",
            variable=auto_refresh_var,
            command=toggle_auto,
            bg="#111827",
            fg="#f8fafc",
            selectcolor="#070a11",
            activebackground="#111827",
            activeforeground="#38bdf8",
        )
        chk_auto.pack(side="left", padx=(0, 10))

        btn_refresh = ttk.Button(top_bar, text="🔄 Refresh Frame", command=update_frame)
        btn_refresh.pack(side="left")

        # Initial frame
        win.after(200, update_frame)
