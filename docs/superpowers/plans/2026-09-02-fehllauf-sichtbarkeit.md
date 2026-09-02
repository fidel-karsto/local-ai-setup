# Sichtbarkeit fehlgeschlagener Nachtläufe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein fehlgeschlagener nächtlicher `doc-sync`-Lauf soll auffallen, ohne dass jemand ins Log sieht.

**Architecture:** Ein Shell-Wrapper startet `doc-sync.py`, wertet dessen Exit-Code aus und meldet jeden Wert außer 0 — auf macOS als Systemmitteilung, unter Linux ins journald mit Fehlerpriorität. Der Exit-Code wird unverändert weitergereicht, damit launchd und systemd den Fehlschlag weiterhin selbst sehen. `doc-sync.py` bleibt unangetastet und plattformfrei.

**Tech Stack:** Bash, `osascript` (macOS), `systemd-cat`/`logger` (Linux), stdlib `unittest` mit `subprocess` für die Tests.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-09-02-fehllauf-sichtbarkeit-design.md` — bei Widersprüchen gilt die Spec.
- **`scripts/doc-sync.py` wird in diesem Plan NICHT verändert.** Die Plattformfreiheit dieses Moduls ist eine tragende Entwurfsentscheidung; das Plattformwissen gehört ausschließlich in den Wrapper.
- **Kommentare, Meldungstexte und Doku auf Deutsch** (Ausnahme: `TUTORIAL_EN.md`). Commit-Messages in reinem ASCII mit ausgeschriebenen Umlauten (ae/oe/ue), keine Halbgeviertstriche.
- **Tests:** stdlib `unittest`, ausgeführt mit `python3 -m unittest discover -s tests -v`. Kein pytest, keine neuen Abhängigkeiten. Die Suite muss auf einem nackten System-Python lauffähig bleiben (aktuell 36 Tests, 15 davon übersprungen).
- **Defaults im Code sind die Linux-Pfade** (`/srv/…`); die macOS-Pfade (`/opt/heim-ki/…`) stehen ausschließlich in plist und Doku.
- **Kein Konfigurationsschalter, der nur Tests dient.** Die Tests fälschen `osascript` und den Interpreter über den `PATH`.

---

### Task 1: Der Wrapper

**Files:**
- Create: `scripts/doc-sync-run.sh`
- Create: `tests/test_doc_sync_run.py`

**Interfaces:**
- Consumes: `scripts/doc-sync.py` (unverändert), aufgerufen über die Umgebungsvariablen `DOC_SYNC_PYTHON` und `DOC_SYNC_SKRIPT`.
- Produces: ausführbares `scripts/doc-sync-run.sh`, das alle Argumente durchreicht und den Exit-Code des Python weitergibt. Task 2 trägt es in plist und Unit ein.

- [ ] **Step 1: Write the failing test**

Erstelle `tests/test_doc_sync_run.py`. Die Tests fälschen sowohl den Interpreter als auch `osascript` über den `PATH` — damit wird die echte Verzweigung geprüft und nicht eine Test-Sonderbehandlung.

```python
"""Tests für den Melde-Wrapper (scripts/doc-sync-run.sh).

Der Wrapper existiert, damit ein fehlgeschlagener Nachtlauf auffällt. Geprüft
wird deshalb genau das: dass er den Melder bei Misserfolg aufruft, bei Erfolg
schweigt, und dass er den Exit-Code unter keinen Umständen verfälscht.

Interpreter und `osascript` werden über den PATH gefälscht, statt dem Skript
einen Testschalter zu geben — so läuft die echte Verzweigung.

Ausführen (kein pytest nötig):
    python3 -m unittest tests.test_doc_sync_run -v
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().parent.parent / "scripts" / "doc-sync-run.sh"


class TestDocSyncRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        # Spur, die der gefälschte Melder hinterlässt:
        self.melder_spur = self.tmp / "gemeldet.txt"
        self._schreibe(
            self.bin / "osascript",
            f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{self.melder_spur}"\nexit 0\n',
        )
        # Spur, die der gefälschte Interpreter hinterlässt:
        self.python_spur = self.tmp / "aufgerufen.txt"

    def tearDown(self):
        self._tmp.cleanup()

    def _schreibe(self, pfad: Path, inhalt: str) -> None:
        pfad.write_text(inhalt, encoding="utf-8")
        pfad.chmod(0o755)

    def _fake_python(self, exit_code: int) -> Path:
        pfad = self.bin / "fake-python"
        self._schreibe(
            pfad,
            f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{self.python_spur}"\n'
            f"exit {exit_code}\n",
        )
        return pfad

    def _lauf(self, exit_code: int, argumente=()):
        python = self._fake_python(exit_code)
        umgebung = dict(os.environ)
        # Der gefälschte PATH steht vorn, damit der Wrapper unser osascript
        # findet und nicht das echte des Systems.
        umgebung["PATH"] = f"{self.bin}:{umgebung['PATH']}"
        umgebung["DOC_SYNC_PYTHON"] = str(python)
        umgebung["DOC_SYNC_SKRIPT"] = str(self.tmp / "doc-sync.py")
        return subprocess.run(
            [str(WRAPPER), *argumente],
            env=umgebung,
            capture_output=True,
            text=True,
        )

    def test_erfolg_meldet_nichts(self):
        ergebnis = self._lauf(0)
        self.assertEqual(ergebnis.returncode, 0)
        self.assertFalse(self.melder_spur.exists(), "bei Erfolg darf nichts gemeldet werden")

    def test_fehler_meldet_und_behaelt_den_exit_code(self):
        ergebnis = self._lauf(3)
        self.assertEqual(ergebnis.returncode, 3, "der Exit-Code muss unveraendert durchgereicht werden")
        self.assertTrue(self.melder_spur.exists(), "bei Misserfolg muss gemeldet werden")
        self.assertIn("3", self.melder_spur.read_text(encoding="utf-8"))

    def test_scheiternder_melder_verfaelscht_den_exit_code_nicht(self):
        # Ein fehlender Mitteilungs-Berechtigung darf den echten Fehler nicht
        # verdecken — sonst meldet der Wrapper Erfolg, obwohl nichts klappte.
        self._schreibe(self.bin / "osascript", "#!/bin/sh\nexit 1\n")
        ergebnis = self._lauf(3)
        self.assertEqual(ergebnis.returncode, 3)

    def test_argumente_kommen_beim_python_an(self):
        self._lauf(0, argumente=["--create"])
        aufgerufen = self.python_spur.read_text(encoding="utf-8")
        self.assertIn("--create", aufgerufen)

    def test_wrapper_ist_ausfuehrbar(self):
        self.assertTrue(os.access(WRAPPER, os.X_OK), "Wrapper muss das Ausfuehrbar-Bit tragen")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_doc_sync_run -v`
Expected: FAIL — alle Tests scheitern, weil `scripts/doc-sync-run.sh` nicht existiert (`FileNotFoundError` bzw. `PermissionError`).

- [ ] **Step 3: Write minimal implementation**

Erstelle `scripts/doc-sync-run.sh`:

```bash
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
```

Danach das Ausführbar-Bit setzen:

```bash
chmod +x scripts/doc-sync-run.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_doc_sync_run -v`
Expected: PASS (5 Tests)

- [ ] **Step 5: Syntaxprüfung und Gesamtsuite**

```bash
bash -n scripts/doc-sync-run.sh
python3 -m unittest discover -s tests
```
Expected: `bash -n` ohne Ausgabe; die Suite meldet 41 Tests, `OK (skipped=15)`.

- [ ] **Step 6: Commit**

```bash
git add scripts/doc-sync-run.sh tests/test_doc_sync_run.py
git commit -m "feat(doc-sync): Fehlschlaege des Nachtlaufs sichtbar machen

Ein Wrapper wertet den Exit-Code aus und meldet jeden Wert ausser 0 -- auf
macOS als Systemmitteilung, unter Linux ins journald mit Fehlerprioritaet.
Der Exit-Code wird unveraendert durchgereicht, damit launchd und systemd den
Fehlschlag weiterhin selbst sehen.

Bewusst der Exit-Code statt der Fehlerzaehlung von doc-sync.py: so werden auch
Abstuerze erfasst, die das Skript selbst nie protokolliert.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Einbindung in plist und Unit

**Files:**
- Modify: `scripts/launchd/de.heim-ki.doc-sync.plist` (`ProgramArguments`, `EnvironmentVariables`)
- Modify: `scripts/systemd/doc-sync.service:13` (`ExecStart`)

**Interfaces:**
- Consumes: `scripts/doc-sync-run.sh` aus Task 1 samt `DOC_SYNC_PYTHON` und `DOC_SYNC_SKRIPT`.
- Produces: nichts für spätere Tasks außer den Pfaden, die Task 3 in der Doku nennt.

- [ ] **Step 1: launchd-plist umstellen**

In `scripts/launchd/de.heim-ki.doc-sync.plist` den Block

```xml
    <key>ProgramArguments</key>
    <array>
        <string>/opt/heim-ki/scripts/.venv/bin/python</string>
        <string>/opt/heim-ki/scripts/doc-sync.py</string>
    </array>
```

ersetzen durch

```xml
    <!-- Aufgerufen wird der Wrapper, nicht das Python direkt: er wertet den
         Exit-Code aus und schickt bei einem Fehlschlag eine Mitteilung.
         Ohne ihn scheitert der Nachtlauf lautlos, und die Sammlung bleibt
         einfach unvollstaendig. -->
    <key>ProgramArguments</key>
    <array>
        <string>/opt/heim-ki/scripts/doc-sync-run.sh</string>
    </array>
```

und im bestehenden `EnvironmentVariables`-Dict die beiden Einträge ergänzen (die übrigen Einträge bleiben unverändert):

```xml
        <key>DOC_SYNC_PYTHON</key>
        <string>/opt/heim-ki/scripts/.venv/bin/python</string>
        <key>DOC_SYNC_SKRIPT</key>
        <string>/opt/heim-ki/scripts/doc-sync.py</string>
```

- [ ] **Step 2: systemd-Unit umstellen**

In `scripts/systemd/doc-sync.service` die Zeile

```ini
ExecStart=/srv/scripts/.venv/bin/python /srv/scripts/doc-sync.py
```

ersetzen durch

```ini
# Aufgerufen wird der Wrapper, nicht das Python direkt: er schreibt bei einem
# Fehlschlag einen Eintrag mit Fehlerprioritaet ins journald (journalctl -p err).
# Der Exit-Code wird durchgereicht, die Unit gilt also weiterhin als failed.
ExecStart=/srv/scripts/doc-sync-run.sh
Environment=DOC_SYNC_PYTHON=/srv/scripts/.venv/bin/python
Environment=DOC_SYNC_SKRIPT=/srv/scripts/doc-sync.py
```

- [ ] **Step 3: Beide Dateien prüfen**

```bash
plutil -lint scripts/launchd/de.heim-ki.doc-sync.plist
grep -n "ExecStart\|DOC_SYNC_" scripts/systemd/doc-sync.service
```
Expected: `OK` für die plist; im Unit erscheinen `ExecStart=/srv/scripts/doc-sync-run.sh` sowie die beiden `Environment=DOC_SYNC_*`-Zeilen.

Auf einem Linux-Host zusätzlich `systemd-analyze verify scripts/systemd/doc-sync.service` — auf macOS gibt es das Kommando nicht; dann im Bericht als nicht durchgeführt vermerken statt es zu überspringen.

- [ ] **Step 4: Echten Fehlschlag auf diesem Host nachstellen**

Der Wrapper soll nicht nur in Tests funktionieren. Provoziere einen echten Fehler, indem du auf eine nicht existierende Sammlung zeigst (ohne `--create` bricht `doc-sync.py` dann mit Exit-Code 1 ab):

```bash
DOCS_DIR=/opt/heim-ki/dokumente \
STATE_FILE=/opt/heim-ki/doc-sync-state.json \
CACHE_DIR=/opt/heim-ki/cache \
LOCK_FILE=/opt/heim-ki/doc-sync.lock \
WEBUI_API_KEY_FILE=/opt/heim-ki/webui-api-key \
KNOWLEDGE_NAME=GibtEsNicht \
DOC_SYNC_PYTHON=/opt/heim-ki/scripts/.venv/bin/python \
DOC_SYNC_SKRIPT=/opt/heim-ki/scripts/doc-sync.py \
  ./scripts/doc-sync-run.sh
echo "Exit-Code: $?"
```

Expected: Die Ausgabe endet mit `FEHLER: Sammlung 'GibtEsNicht' existiert nicht.`, `Exit-Code: 1`, und im Mitteilungszentrum erscheint „Heim-KI — Dokument-Sync fehlgeschlagen". Bestätige im Bericht ausdrücklich, ob die Mitteilung **sichtbar** war — der Exit-Code von `osascript` belegt das nicht, macOS unterdrückt Mitteilungen ohne Berechtigung stillschweigend.

- [ ] **Step 5: Commit**

```bash
git add scripts/launchd/de.heim-ki.doc-sync.plist scripts/systemd/doc-sync.service
git commit -m "feat(doc-sync): Zeitplan-Jobs ueber den Melde-Wrapper starten

launchd und systemd rufen nicht mehr das Python direkt, sondern den Wrapper.
Der Exit-Code wird durchgereicht, beide Mechanismen sehen einen Fehlschlag
also weiterhin selbst; die Meldung kommt zusaetzlich.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Doku in beiden Sprachen

**Files:**
- Modify: `TUTORIAL_DE.md` — §7, der Abschnitt zum Kopieren der Skripte und der Abschnitt zum nächtlichen Lauf
- Modify: `TUTORIAL_EN.md` — die entsprechenden Stellen

**Interfaces:**
- Consumes: `scripts/doc-sync-run.sh` und die Änderungen an plist und Unit aus Task 1 und 2.
- Produces: nichts.

- [ ] **Step 1: Bestand sichten**

```bash
grep -n "docscan.py scripts/requirements.txt\|cp scripts/doc-sync.py" TUTORIAL_DE.md TUTORIAL_EN.md
grep -n "launchctl load\|systemctl enable" TUTORIAL_DE.md TUTORIAL_EN.md
```

- [ ] **Step 2: Kopierbefehle ergänzen**

In beiden Tutorials kopieren die `cp`-Befehle in Schritt 1 vier Python-Dateien und `requirements.txt`. Ergänze in **allen** Vorkommen (Linux- wie macOS-Block, beide Sprachen) `scripts/doc-sync-run.sh` und setze danach das Ausführbar-Bit. Beispiel für den macOS-Block:

```bash
cp scripts/doc-sync.py scripts/docconvert.py scripts/webui_client.py \
   scripts/docscan.py scripts/doc-sync-run.sh scripts/requirements.txt /opt/heim-ki/scripts/
chmod +x /opt/heim-ki/scripts/doc-sync-run.sh
```

Der Satz „**Alle vier Python-Dateien müssen mit**" (englisch „All four Python files have to come along") muss mitziehen: Es sind jetzt vier Python-Dateien **und** der Wrapper.

- [ ] **Step 3: Absatz zur Sichtbarkeit einfügen**

In beiden Tutorials, im Abschnitt zum nächtlichen Lauf (direkt vor oder nach `launchctl load` / `systemctl enable`), einen Absatz mit diesem Inhalt ergänzen — im Ton des umgebenden Textes, nicht als Aufzählung abgeschrieben:

- Aufgerufen wird der Wrapper `doc-sync-run.sh`, nicht das Python direkt.
- Er wertet den Exit-Code aus und meldet jeden Wert außer 0: auf macOS als Systemmitteilung, unter Linux als Eintrag mit Fehlerpriorität im journald (`journalctl -u doc-sync -p err`).
- Warum der Exit-Code und nicht die Fehlerzählung des Skripts: So werden auch Abstürze erfasst, die `doc-sync.py` selbst nie protokolliert.
- Der Exit-Code wird durchgereicht, `systemctl status` zeigt den Fehlschlag also weiterhin.
- **Die Grenze, ausdrücklich benannt:** Ein Lauf, der gar nicht stattfindet, meldet nichts. War der Mac aus oder ist niemand angemeldet, läuft auch kein LaunchAgent — und ein Prozess, der nicht startet, kann sich nicht beschweren. In einem Heim-Setup ist das der wahrscheinlichste Grund für einen veralteten Index. Wer das ausschließen will, muss von außen prüfen, etwa das Alter von `doc-sync-state.json` betrachten.
- Auf macOS erscheint eine Mitteilung nur, wenn Skript-Mitteilungen erlaubt sind. Ausprobieren lässt sich das mit:

```bash
osascript -e 'display notification "Test" with title "Heim-KI"'
```

Erscheint nichts, ist die Berechtigung in den Systemeinstellungen unter *Mitteilungen* freizugeben.

- [ ] **Step 4: Gegenprüfen**

```bash
grep -n "doc-sync-run" TUTORIAL_DE.md TUTORIAL_EN.md
diff <(grep "^## \|^### " TUTORIAL_DE.md) <(grep "^## \|^### " TUTORIAL_EN.md) | grep -c "^[<>]"
```
Expected: `doc-sync-run` erscheint in beiden Dateien sowohl im Kopierbefehl als auch im neuen Absatz. Der zweite Befehl zählt nur Überschriftenzeilen, die sich unterscheiden — die Zahl muss der Anzahl der Überschriften entsprechen (die Überschriften sind übersetzt), und die Gesamtzahl der Überschriften muss in beiden Dateien gleich sein:

```bash
grep -c "^## \|^### " TUTORIAL_DE.md TUTORIAL_EN.md
```
Expected: beide Dateien nennen dieselbe Zahl.

- [ ] **Step 5: Commit**

```bash
git add TUTORIAL_DE.md TUTORIAL_EN.md
git commit -m "docs(rag): Melde-Wrapper in beiden Tutorials beschreiben

Inklusive der ausdruecklich benannten Grenze: ein Lauf, der gar nicht
stattfindet, meldet nichts.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Abschluss

- [ ] **Gesamte Testsuite auf beiden Interpretern**

```bash
python3 -m unittest discover -s tests
/opt/heim-ki/scripts/.venv/bin/python -m unittest discover -s tests
```
Expected: System-Python 41 Tests, `OK (skipped=15)`; venv 41 Tests, `OK`.

- [ ] **Wrapper im echten Zeitplan**

Nur wenn der LaunchAgent bereits installiert ist: nach dem Umstellen der plist einmal neu laden und von Hand auslösen.

```bash
launchctl unload ~/Library/LaunchAgents/de.heim-ki.doc-sync.plist
cp scripts/launchd/de.heim-ki.doc-sync.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/de.heim-ki.doc-sync.plist
launchctl start de.heim-ki.doc-sync
tail -n 20 /opt/heim-ki/logs/doc-sync.log
```
Expected: Der Lauf erscheint im Log. Bei einem erfolgreichen Lauf kommt keine Mitteilung — das ist so gewollt.
