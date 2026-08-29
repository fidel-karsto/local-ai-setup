#!/usr/bin/env bash
# Backup der Heim-KI-Daten (README §11)
#
# Sichert:
#   - Docker-Volume open-webui-data   (Nutzer, Chats, Wissenssammlungen)
#   - Docker-Volume chroma-data       (RAG-Index, Variante B)
#   - Manifest des Indexers           (STATE_FILE, Default /srv/rag-index-state.json)
# Bewusst NICHT gesichert: ollama-data — Modelle sind jederzeit per
# "ollama pull" wiederherstellbar und würden das Backup nur aufblähen.
#
# Konfiguration über Umgebungsvariablen (Default in Klammern):
#   BACKUP_DIR (/srv/backups/heim-ki)         Zielverzeichnis (gern ein NAS-Mount)
#   KEEP_DAYS  (14)                           Sicherungen älter als N Tage löschen
#   STATE_FILE (/srv/rag-index-state.json)    Indexer-Manifest
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/srv/backups/heim-ki}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STATE_FILE="${STATE_FILE:-/srv/rag-index-state.json}"
STAMP="$(date +%F)"

# Ohne erreichbaren Docker-Daemon würden unten beide Volumes als "existiert
# nicht" übersprungen und das Backup wäre still leer — deshalb hart abbrechen.
# (macOS: Docker Desktop/OrbStack laufen nur in einer angemeldeten
# Nutzer-Session — siehe README §3, "Unbeaufsichtigter Betrieb".)
if ! docker info >/dev/null 2>&1; then
    echo "FEHLER: Docker-Daemon nicht erreichbar — Backup abgebrochen." >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

backup_volume() {
    local vol="$1"
    if docker volume inspect "$vol" >/dev/null 2>&1; then
        docker run --rm \
            -v "$vol":/data:ro \
            -v "$BACKUP_DIR":/backup \
            alpine tar czf "/backup/${vol}-${STAMP}.tar.gz" -C /data .
        echo "Gesichert: ${vol} -> ${vol}-${STAMP}.tar.gz"
    else
        echo "Übersprungen (Volume existiert nicht): ${vol}"
    fi
}

backup_volume open-webui-data
backup_volume chroma-data

if [ -f "$STATE_FILE" ]; then
    cp "$STATE_FILE" "$BACKUP_DIR/rag-index-state-${STAMP}.json"
    echo "Gesichert: $STATE_FILE"
fi

# Alte Sicherungen aufräumen
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name '*.tar.gz' -o -name 'rag-index-state-*.json' \) \
    -mtime +"$KEEP_DAYS" -delete

echo "Fertig. Ablage: $BACKUP_DIR"
