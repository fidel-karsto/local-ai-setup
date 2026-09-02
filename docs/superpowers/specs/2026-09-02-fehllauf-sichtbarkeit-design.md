# Design: Fehlgeschlagene doc-sync-Läufe sichtbar machen

Datum: 2026-09-02
Status: abgestimmt, bereit für den Implementierungsplan
Grundlage: Befund des Abschluss-Reviews zu `docs/superpowers/specs/2026-09-01-native-dokumentkonvertierung-design.md`

## Ausgangslage

`scripts/doc-sync.py` läuft unbeaufsichtigt aus einem nächtlichen Zeitplan.
Es liefert bei Fehlern Exit-Code 1 und protokolliert nach
`/opt/heim-ki/logs/doc-sync.log` (launchd) beziehungsweise ins journald
(systemd). Nichts davon erreicht den Betreiber: Es gibt kein `OnFailure=`,
keine Zusammenfassung, keinen Hinweis irgendwo.

Der Abschluss-Review hat das benannt und mit den übrigen offenen Punkten
verknüpft: Ein nicht aufgeräumter Waisen-Eintrag, ein korruptes Manifest, ein
Transportfehler in der Löschschleife — alle drei scheitern leise, und diese
fehlende Sichtbarkeit macht sie schlimmer, als sie einzeln wären.

Der erste echte Ende-zu-Ende-Lauf hat das bestätigt: Er scheiterte mit
`HTTP 400 — The content provided is empty.` Wäre er nachts gelaufen, hätte
nichts davon berichtet; die Sammlung wäre einfach leer geblieben.

## Leitentscheidungen

1. **Gemeldet wird nur der Fehlschlag.** Erfolgreiche Läufe bleiben still.
   Meldungen, die meistens nichts bedeuten, werden weggeklickt und verlieren
   ihren Zweck.
2. **Ausgewertet wird der Exit-Code, nicht die Fehlerzählung des Skripts.**
   Damit erfasst der Mechanismus auch Abstürze, die `doc-sync.py` selbst nie
   protokolliert — etwa ein `AttributeError` bei unerwarteter API-Antwort, den
   der Abschluss-Review als Risiko benannt hat.
3. **`doc-sync.py` bleibt plattformfrei.** Es enthält heute keine einzige
   Fallunterscheidung nach Betriebssystem, und das war eine tragende
   Entwurfsentscheidung. Das Plattformwissen bekommt ein eigener Wrapper.
4. **Der Kanal ist eine macOS-Mitteilung**, unter Linux das journald mit
   Fehlerpriorität. Auf einem Server ohne Bildschirm wäre eine
   Desktop-Mitteilung sinnlos.

Verifiziert vorab: `osascript -e 'display notification ...'` erscheint auf
diesem Host tatsächlich im Mitteilungszentrum. Der Exit-Code allein hätte das
nicht belegt — macOS unterdrückt Mitteilungen ohne Berechtigung
stillschweigend und meldet trotzdem Erfolg.

## Verworfene Alternative

**Die Mitteilung aus `doc-sync.py` heraus**, am Ende des Laufs bei
`fehler > 0`. Das wäre eine Datei weniger und würde auch beim Handaufruf
wirken, fängt aber ausschließlich die Fehler, die das Skript selbst zählt. Ein
Absturz vor dem Ende der Schleife erreicht diese Stelle nie. Genau diese Klasse
Fehler sieht man am wenigsten kommen.

**launchd-Bordmittel** scheiden aus: launchd kennt kein Gegenstück zu systemds
`OnFailure=`; `KeepAlive` startet neu, statt zu melden.

---

## 1. Die Komponente

Neu: `scripts/doc-sync-run.sh`. Startet das Python, wertet den Exit-Code aus,
meldet bei allem außer 0 und reicht den Code unverändert weiter.

```
"$PYTHON" "$SKRIPT" "$@"
code=$?
[ "$code" -eq 0 ] && exit 0
<melden>
exit "$code"
```

**Kein `set -e`.** Der Wrapper existiert, um einen Fehlschlag auszuwerten, nicht
um bei ihm abzubrechen — mit `-e` stiege er aus, bevor er meldet. Gesetzt wird
`set -uo pipefail`.

**Der Exit-Code wird durchgereicht.** Sonst sähe launchd lauter erfolgreiche
Läufe, und `systemctl status` meldete „active (exited)", obwohl nichts geklappt
hat. Die Mitteilung kommt zusätzlich, sie ersetzt nichts.

**Ein Fehler beim Melden darf den Lauf nicht überschreiben.** Der
`osascript`-Aufruf bekommt `|| true`; eine fehlende Mitteilungsberechtigung
soll nicht den echten Exit-Code verdrängen.

**Verzweigt wird nach vorhandenem Kommando, nicht nach `uname`:**

| Bedingung | Kanal |
|---|---|
| `osascript` vorhanden | macOS-Mitteilung, Ton `Basso` |
| sonst `systemd-cat` vorhanden | journald, Priorität `err` |
| sonst | `logger -p user.err`, ersatzweise stderr |

Konfiguration über Umgebungsvariablen, Defaults wie im übrigen Projekt die
Linux-Pfade:

| Variable | Default | Bedeutung |
|---|---|---|
| `DOC_SYNC_PYTHON` | `/srv/scripts/.venv/bin/python` | Interpreter |
| `DOC_SYNC_SKRIPT` | `/srv/scripts/doc-sync.py` | Skript |

Die Meldung nennt den Exit-Code und, als **fest im jeweiligen Zweig
verdrahteten Text**, wo das Log zu finden ist — auf macOS
`/opt/heim-ki/logs/doc-sync.log`, unter Linux `journalctl -u doc-sync`. In den
AppleScript-Ausdruck wird ausschliesslich die Zahl des Exit-Codes interpoliert;
ein aus der Umgebung übernommener Pfad mit Anführungszeichen würde den Aufruf
sonst zerlegen. Der Preis ist ein Log-Pfad, der bei abweichender Installation
nicht stimmt — das ist ein Hinweis in einer Meldung, kein Verhalten, und damit
das kleinere Übel gegenüber einer zerbrechlichen Zeichenkette.

## 2. Tests

`tests/test_doc_sync_run.py`, stdlib `unittest` wie im Repo üblich, ruft den
Wrapper über `subprocess` auf.

**Kein Konfigurationsschalter zum Umleiten der Meldung.** Stattdessen legen die
Tests ein gefälschtes `osascript` und ein gefälschtes Python in ein temporäres
Verzeichnis und stellen es dem `PATH` voran. Das prüft die echte Verzweigung
statt einer Test-Sonderbehandlung, und das Skript bleibt frei von
Konfiguration, die nur Tests dient.

| Fall | Erwartung |
|---|---|
| Python endet mit 0 | Wrapper endet mit 0, kein Melder aufgerufen |
| Python endet mit 3 | Melder aufgerufen, Wrapper endet ebenfalls mit 3 |
| Melder selbst scheitert | Exit-Code des Python bleibt erhalten |
| Argumente (`--create`) | kommen unverändert beim Python an |

## 3. Einbindung

- `scripts/launchd/de.heim-ki.doc-sync.plist`: `ProgramArguments` auf den
  Wrapper, dazu `DOC_SYNC_PYTHON` und `DOC_SYNC_SKRIPT` in den
  `EnvironmentVariables`.
- `scripts/systemd/doc-sync.service`: `ExecStart` auf den Wrapper. Die Unit
  meldet den Fehlschlag dadurch weiterhin selbst — der Wrapper ergänzt nur den
  Eintrag mit Fehlerpriorität.
- `TUTORIAL_DE.md` und `TUTORIAL_EN.md`: kurzer Absatz in §7, plus der Hinweis,
  dass der Wrapper mitkopiert werden muss.

## 4. Was dieser Entwurf ausdrücklich nicht löst

**Ein Lauf, der gar nicht stattfindet, meldet nichts.** War der Mac aus, ist
keine Sitzung angemeldet oder der LaunchAgent nicht geladen, läuft auch kein
Wrapper — und ein Prozess, der nicht startet, kann sich nicht beschweren. In
einem Heim-Setup ist das der wahrscheinlichste Grund für einen veralteten
Index. Dagegen hülfe nur eine Prüfung von außen, etwa ein zweiter Zeitplan-Job,
der das Alter des Manifests betrachtet. Das ist bewusst nicht Teil dieses
Entwurfs und gehört in die Doku als benannte Grenze, nicht als Fußnote.
