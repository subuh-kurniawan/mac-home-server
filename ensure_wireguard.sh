#!/bin/zsh
# Memastikan tunnel WireGuard "MAC-MINI" otomatis aktif saat Mac dinyalakan & reconnect jika terputus.
TUNNEL="MAC-MINI"
STATUS=$(/usr/sbin/scutil --nc status "$TUNNEL" 2>/dev/null | head -n 1)
if [ "$STATUS" != "Connected" ] && [ "$STATUS" != "Connecting" ]; then
    /usr/sbin/scutil --nc start "$TUNNEL" >/dev/null 2>&1
fi
