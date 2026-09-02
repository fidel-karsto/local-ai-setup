#!/usr/bin/env bash
# Backup der Heim-KI-Daten (README §11)
#
# Sichert:
#   - Docker-Volume open-webui-data   (Nutzer, Chats, Wissenssammlungen)
#   - Manifest des Doc-Sync           (STATE_FILE, Default /srv/heim-ki/doc-sync-state.json)
# Bewusst NICHT gesichert: ollama-data — Modelle sind jederzeit per
# "ollama pull" wiederherstellbar und würden das Backup nur aufblähen.
#
# Konfiguration über Umgebungsvariablen (Default in Klammern):
#   BACKUP_DIR (/srv/backups/heim-ki)         Zielverzeichnis (gern ein NAS-Mount)
#   KEEP_DAYS  (14)                           Sicherungen älter als N Tage löschen
#   STATE_FILE (/srv/heim-ki/doc-sync-state.json)   Doc-Sync-Manifest
#   ALPINE_IMAGE                              Tar-Helfer, auf Digest gepinnt
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/srv/backups/heim-ki}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STATE_FILE="${STATE_FILE:-/srv/heim-ki/doc-sync-state.json}"

# Auf einen Digest gepinnt wie die Images in .env.example: dieser Container
# läuft als root und sieht beide Daten-Volumes. Es ist bewusst die
# Manifest-Liste (nicht ein einzelnes Plattform-Manifest), damit der Pin auf
# amd64 und arm64 gleichermaßen zieht.
# Stand: alpine 3.22.5, 2026-08-30. Auffrischen mit:
#   docker pull alpine:3.22
#   docker inspect --format='{{index .RepoDigests 0}}' alpine:3.22
ALPINE_IMAGE="${ALPINE_IMAGE:-alpine@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce}"

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
            "$ALPINE_IMAGE" tar czf "/backup/${vol}-${STAMP}.tar.gz" -C /data .
        echo "Gesichert: ${vol} -> ${vol}-${STAMP}.tar.gz"
    else
        echo "Übersprungen (Volume existiert nicht): ${vol}"
    fi
}

backup_volume open-webui-data

if [ -f "$STATE_FILE" ]; then
    cp "$STATE_FILE" "$BACKUP_DIR/doc-sync-state-${STAMP}.json"
    echo "Gesichert: $STATE_FILE"
fi

# Alte Sicherungen aufräumen
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name '*.tar.gz' -o -name 'doc-sync-state-*.json' \) \
    -mtime +"$KEEP_DAYS" -delete

echo "Fertig. Ablage: $BACKUP_DIR"
