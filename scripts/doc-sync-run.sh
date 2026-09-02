#!/usr/bin/env bash
# Startet den Dokument-Sync und macht einen Fehlschlag sichtbar (Tutorial §7).
#
# Warum ein Wrapper und nicht eine Meldung in doc-sync.py: Ausgewertet wird hier
# ausschliesslich der Exit-Code. Damit faengt dieser Weg auch Abstuerze, die
# doc-sync.py selbst nie protokolliert — etwa einen Traceback, bevor die
# Fehlerzaehlung ueberhaupt erreicht wird. Ausserdem bleibt doc-sync.py frei
# von Plattformwissen; es enthaelt bewusst keine einzige Fallunterscheidung
# nach Betriebssystem.
#
# Konfiguration über Umgebungsvariablen (Default in Klammern):
#   DOC_SYNC_PYTHON  (/srv/scripts/.venv/bin/python)   Interpreter
#   DOC_SYNC_SKRIPT  (/srv/scripts/doc-sync.py)        Skript
#
# Alle Argumente werden unveraendert an doc-sync.py durchgereicht.

# Bewusst OHNE -e: Dieser Wrapper existiert, um einen Fehlschlag auszuwerten,
# nicht um bei ihm abzubrechen. Mit -e wuerde er aussteigen, bevor er meldet.
set -uo pipefail

PYTHON="${DOC_SYNC_PYTHON:-/srv/scripts/.venv/bin/python}"
SKRIPT="${DOC_SYNC_SKRIPT:-/srv/scripts/doc-sync.py}"

"$PYTHON" "$SKRIPT" "$@"
code=$?

if [ "$code" -eq 0 ]; then
    exit 0
fi

# In den AppleScript-Ausdruck wird ausschliesslich die Zahl des Exit-Codes
# interpoliert. Der Hinweis auf das Log steht je Zweig als fester Text: ein aus
# der Umgebung uebernommener Pfad mit Anfuehrungszeichen wuerde den Aufruf
# zerlegen.
if command -v osascript >/dev/null 2>&1; then
    # || true: Eine fehlende Mitteilungsberechtigung darf den echten
    # Exit-Code nicht verdraengen.
    osascript -e "display notification \"Exit-Code ${code}. Log: /opt/heim-ki/logs/doc-sync.log\" with title \"Heim-KI\" subtitle \"Dokument-Sync fehlgeschlagen\" sound name \"Basso\"" >/dev/null 2>&1 || true
elif command -v systemd-cat >/dev/null 2>&1; then
    printf 'Dokument-Sync fehlgeschlagen (Exit-Code %s). Log: journalctl -u doc-sync\n' "$code" \
        | systemd-cat -t doc-sync -p err || true
elif command -v logger >/dev/null 2>&1; then
    logger -t doc-sync -p user.err \
        "Dokument-Sync fehlgeschlagen (Exit-Code ${code})." || true
else
    printf 'Dokument-Sync fehlgeschlagen (Exit-Code %s).\n' "$code" >&2
fi

exit "$code"
