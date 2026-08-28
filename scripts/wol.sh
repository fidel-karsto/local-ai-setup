#!/usr/bin/env bash
# Workstation per Wake-on-LAN aufwecken (README §12)
#
# Aufruf:  ./wol.sh AA:BB:CC:DD:EE:FF
# oder:    WOL_MAC=AA:BB:CC:DD:EE:FF ./wol.sh
# (MAC-Adresse der Workstation: unter Windows "ipconfig /all" -> Physische Adresse)
set -euo pipefail

MAC="${1:-${WOL_MAC:-}}"
if [ -z "$MAC" ]; then
    echo "Aufruf: $0 <MAC-Adresse>   (oder WOL_MAC als Umgebungsvariable setzen)" >&2
    exit 1
fi

if command -v wakeonlan >/dev/null 2>&1; then
    wakeonlan "$MAC"
elif command -v etherwake >/dev/null 2>&1; then
    sudo etherwake "$MAC"
else
    echo "Weder 'wakeonlan' noch 'etherwake' gefunden — installieren mit:" >&2
    echo "  sudo apt install wakeonlan" >&2
    exit 1
fi
