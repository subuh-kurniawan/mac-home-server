#!/usr/bin/env python3
"""
Radar Pemindai Jaringan Wi-Fi & LAN (Network Scanner).
Mendeteksi semua perangkat yang sedang terhubung ke Wi-Fi / LAN rumah/kantor secara paralel.
Mengidentifikasi: Router, Mac mini, Google Home / Nest, Smartphone, Smart TV, dsb.
"""

import concurrent.futures
import json
import os
import re
import socket
import subprocess
import time
import urllib.request

COMMON_PORTS = [80, 443, 8008, 8009, 8080, 8443, 5000, 7000, 22]


def _get_arp_table():
    arp_dict = {}
    try:
        proc = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=2)
        # Format: ? (192.168.0.105) at 48:d6:d5:5d:95:57 on en0 ifscope [ethernet]
        for line in proc.stdout.splitlines():
            m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-fA-F:]+)", line)
            if m:
                arp_dict[m.group(1)] = m.group(2).lower()
    except Exception:
        pass
    return arp_dict


def _probe_single_host(ip, arp_table, lan_ip):
    # 1. Ping check
    res = subprocess.run(["ping", "-c", "1", "-W", "250", ip], stdout=subprocess.DEVNULL)
    if res.returncode != 0:
        return None

    mac = arp_table.get(ip, "Unknown")
    open_ports = []
    for p in COMMON_PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.15)
        if s.connect_ex((ip, p)) == 0:
            open_ports.append(p)
        s.close()

    # Identifikasi Nama & Kategori Perangkat
    device_name = "Perangkat LAN"
    device_category = "Perangkat Lain"
    icon = "📱"

    # A. Deteksi Mac mini ini
    if ip == lan_ip or ip in ["192.168.0.200", "192.168.0.116"]:
        device_name = "Subuh's Mac mini (Host Server)"
        device_category = "Komputer Server"
        icon = "🍎"

    # B. Deteksi Router
    elif ip.endswith(".1"):
        device_name = "Router Gateway Wi-Fi"
        device_category = "Router / Akses Poin"
        icon = "🌐"

    # C. Deteksi Google Home / Nest / Cast
    elif 8008 in open_ports or 8009 in open_ports or ip == "192.168.0.105":
        device_category = "Smart Speaker"
        icon = "📢"
        device_name = "Google Cast / Home"
        try:
            url = f"http://{ip}:8008/setup/eureka_info?params=name,device_info"
            with urllib.request.urlopen(url, timeout=0.8) as resp:
                data = json.loads(resp.read().decode())
                device_name = f"{data.get('name', 'Google Home')} ({data.get('model_name', 'Google Home')})"
        except Exception:
            if ip == "192.168.0.105":
                device_name = "Family Room speaker (Google Home)"

    # D. Deteksi Apple AirPlay / Apple TV
    elif 7000 in open_ports or 5000 in open_ports:
        device_name = "Apple TV / AirPlay Device"
        device_category = "Media Player"
        icon = "📺"

    # E. Deteksi Web Server
    elif 80 in open_ports or 443 in open_ports or 8080 in open_ports:
        device_name = "Web Server / IP Camera"
        device_category = "Web Device"
        icon = "📷"

    # F. Coba hostname DNS lokal
    if device_name == "Perangkat LAN":
        try:
            h = socket.gethostbyaddr(ip)[0]
            if h and not h.startswith(ip):
                device_name = h.split(".")[0]
        except Exception:
            pass

    return {
        "ip": ip,
        "mac": mac,
        "name": device_name,
        "category": device_category,
        "icon": icon,
        "open_ports": open_ports,
    }


def scan_network(subnet_prefix="192.168.0"):
    """Memindai seluruh subnet lokal (254 IP) dalam 1-2 detik."""
    arp_table = _get_arp_table()

    # Dapatkan IP LAN lokal
    lan_ip = "192.168.0.200"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    ips = [f"{subnet_prefix}.{i}" for i in range(1, 255)]

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [
            executor.submit(_probe_single_host, ip, arp_table, lan_ip)
            for ip in ips
        ]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                results.append(res)

    # Urutkan berdasarkan IP
    def _ip_key(it):
        try:
            return [int(x) for x in it["ip"].split(".")]
        except Exception:
            return [0, 0, 0, 0]

    results.sort(key=_ip_key)
    return results


def format_radar_message(devices=None):
    """Format teks hasil radar jaringan untuk chat Telegram."""
    if devices is None:
        devices = scan_network()

    if not devices:
        return "⚠️ Tidak ada perangkat yang terdeteksi di Wi-Fi saat ini."

    lines = [
        f"📡 <b>Radar Jaringan Wi-Fi ({len(devices)} Perangkat Aktif):</b>\n"
    ]
    for d in devices:
        p_str = (
            f" [Port: {','.join(map(str, d['open_ports']))}]"
            if d["open_ports"]
            else ""
        )
        lines.append(
            f"{d['icon']} <b>{d['name']}</b>\n"
            f"   IP: <code>{d['ip']}</code> | MAC: <code>{d['mac']}</code>{p_str}"
        )

    lines.append("\n💡 <i>Pindai dilakukan secara langsung pada subnet Wi-Fi lokal.</i>")
    return "\n".join(lines)


if __name__ == "__main__":
    print("Memindai jaringan...")
    devs = scan_network()
    print(format_radar_message(devs))
