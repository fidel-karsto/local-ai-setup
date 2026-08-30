# Security-Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die drei Befunde des Security-Reviews vom 2026-08-30 beheben und vier
kleinere Härtungen mitnehmen, ohne die Bedienbarkeit des Heim-Setups zu zerstören.

**Architecture:** Drei unabhängige Stoßrichtungen. (1) Die unauthentifizierte
Ollama-API verschwindet aus dem LAN — auf dem Docker-Host durch `satisfy all`
(IP-Allowlist *und* Basic Auth) im NGINX, auf der Workstation durch
Loopback-Bindung plus SSH-Reverse-Tunnel. (2) Der nächtliche RAG-Indexer verliert
root und läuft als eigener Systemaccount; der eigentliche Fix ist aber, dass
`/srv/scripts` wieder `root:root` gehört, damit root nichts User-Schreibbares
mehr ausführt. (3) Kleinkram: Symlink-Containment im Indexer, Log-Pfad raus aus
`/tmp`, alpine-Image gepinnt, ENABLE_SIGNUP-Doku korrigiert.

**Tech Stack:** NGINX, systemd, launchd, Docker Compose, Python 3.9+ (stdlib
`unittest` — keine neue Test-Abhängigkeit), Bash, OpenSSH.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-30-security-remediation-design.md`. Bei Widerspruch gewinnt die Spec.
- **Branch:** `security-remediation`. Nicht auf `main` committen.
- **Sprache:** Alle Kommentare, Doku und Commit-Messages auf Deutsch. `TUTORIAL_EN.md` ist die englische Übersetzung von `TUTORIAL_DE.md` und wird **immer synchron** mitgepflegt — gleiche Abschnitte, gleiche Zeilenreihenfolge.
- **Platzhalter-Konvention:** In NGINX-/Shell-/Markdown-Dateien spitze Klammern (`<LAN-CIDR>`, `<DOCKER-HOST-IP>`). **In `.plist`-Dateien NIEMALS spitze Klammern** — das ist XML, `<` bricht die Datei und `plutil -lint` schlägt fehl. Dort blanke Namen: `DOCKER-HOST-IP`, `BENUTZER`.
- **Pfad-Konvention:** Linux `/srv/...`, macOS `/opt/heim-ki/...`. Neu: State-Verzeichnis `/srv/heim-ki` (Linux) bzw. weiterhin `/opt/heim-ki` (macOS).
- **Keine neuen Laufzeit-Abhängigkeiten.** `scripts/requirements.txt` bleibt unverändert.
- **Zielhost-Prüfungen:** `nginx -t`, `systemd-analyze`, Live-`curl` und der Indexer-Lauf können in dieser Arbeitsumgebung (macOS, kein NGINX, kein systemd) **nicht** ausgeführt werden. Sie gehören in die Doku, dürfen aber niemals als „bestanden" berichtet werden. Nur `python3 -m unittest`, `bash -n` und `plutil -lint` laufen hier wirklich.

---

## File Structure

**Neu:**

| Datei | Verantwortung |
|---|---|
| `scripts/docscan.py` | Dateiauswahl für den Indexer: welche Datei ist indexierbar und liegt sicher innerhalb von `DOCS_DIR`. Bewusst ohne schwere Importe, damit die sicherheitskritische Prüfung eigenständig testbar ist. |
| `tests/test_docscan.py` | Regressionstests für ebendiese Prüfung (stdlib `unittest`). |
| `scripts/launchd/de.heim-ki.ollama-tunnel.plist` | Hält den SSH-Reverse-Tunnel Workstation → Docker-Host. |

**Geändert:**

| Datei | Änderung |
|---|---|
| `scripts/rag-indexer.py` | Nutzt `docscan.scan()`; `STATE_FILE`-Default umgezogen. |
| `scripts/backup.sh` | `STATE_FILE`-Default umgezogen; alpine-Image gepinnt. |
| `scripts/systemd/rag-indexer.service` | `User=heim-ki` + Sandbox. |
| `scripts/systemd/heim-ki-backup.service` | Bleibt root, moderate Härtung + Begründung. |
| `scripts/launchd/de.heim-ki.ollama-ws.plist` | `OLLAMA_HOST` auf Loopback; Log-Pfad raus aus `/tmp`. |
| `nginx/heim-ki.conf` | `satisfy all` auf beiden Ollama-vHosts; Workstation über Tunnel-Port; Klartext-Warnung. |
| `nginx/heim-ki-https.conf` | dito, ohne Klartext-Warnung. |
| `docker-compose.yml`, `docker-compose.macos.yml` | `DEFAULT_USER_ROLE=pending` explizit. |
| `.env.example` | ENABLE_SIGNUP-Kommentar präzisiert. |
| `TUTORIAL_DE.md`, `TUTORIAL_EN.md` | Installationsschritte, Migration, korrigierte Falschaussagen. |
| `REVIEW-UND-PLAN.md` | Kurzer Erledigt-Verweis bei S4 (c). |

---

## Task 1: Symlink-Containment als testbares Modul

Der Indexer läuft nachts über `/srv/dokumente` und dereferenziert Symlinks
(`p.is_file()` folgt ihnen). Ein Link `notiz.md -> /etc/shadow` landet damit im
RAG-Index und ist danach über den Chat abrufbar. Die Prüfung wandert in ein
eigenes Modul, damit sie überhaupt testbar ist — `rag-indexer.py` importiert
`chromadb`, `ollama` und `docling` auf Modulebene und ist ohne installiertes venv
nicht importierbar.

**Files:**
- Create: `scripts/docscan.py`
- Create: `tests/test_docscan.py`
- Modify: `scripts/rag-indexer.py:55` (SUFFIXES entfällt), `:112-116` (Aufruf)

**Interfaces:**
- Consumes: nichts
- Produces:
  - `docscan.SUFFIXES: set[str]` — die indexierbaren Endungen, umgezogen aus `rag-indexer.py:55`
  - `docscan.is_indexable(path: Path, docs_dir: Path) -> bool`
  - `docscan.scan(docs_dir: Path) -> dict[str, Path]` — Abbildung `relpfad -> Pfad`, exakt das, was `rag-indexer.py` bisher in `current` baute

- [ ] **Step 1: Testdatei schreiben (schlägt fehl, Modul fehlt noch)**

Datei `tests/test_docscan.py`:

```python
"""Tests für die Dateiauswahl des RAG-Indexers.

Ausführen (kein pytest nötig):
    python3 -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import docscan


class TestDocscan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        wurzel = Path(self._tmp.name)
        self.docs = wurzel / "dokumente"
        self.docs.mkdir()
        self.aussen = wurzel / "geheim"
        self.aussen.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_normale_datei_wird_indexiert(self):
        (self.docs / "notiz.md").write_text("hallo", encoding="utf-8")
        self.assertEqual(list(docscan.scan(self.docs)), ["notiz.md"])

    def test_symlink_nach_aussen_wird_uebersprungen(self):
        geheim = self.aussen / "id_rsa"
        geheim.write_text("PRIVATE KEY", encoding="utf-8")
        (self.docs / "notiz.md").symlink_to(geheim)
        self.assertEqual(docscan.scan(self.docs), {})

    def test_symlink_innerhalb_wird_ebenfalls_uebersprungen(self):
        (self.docs / "echt.md").write_text("hallo", encoding="utf-8")
        (self.docs / "kopie.md").symlink_to(self.docs / "echt.md")
        self.assertEqual(list(docscan.scan(self.docs)), ["echt.md"])

    def test_toter_symlink_bricht_den_lauf_nicht_ab(self):
        (self.docs / "weg.md").symlink_to(self.aussen / "gibtsnicht.md")
        self.assertEqual(docscan.scan(self.docs), {})

    def test_unbekannte_endung_wird_ignoriert(self):
        (self.docs / "notiz.txt").write_text("hallo", encoding="utf-8")
        self.assertEqual(docscan.scan(self.docs), {})

    def test_endung_wird_case_insensitiv_geprueft(self):
        (self.docs / "Rechnung.PDF").write_bytes(b"%PDF-")
        self.assertEqual(list(docscan.scan(self.docs)), ["Rechnung.PDF"])

    def test_unterordner_bleibt_im_relpfad(self):
        (self.docs / "steuer").mkdir()
        (self.docs / "steuer" / "2025.pdf").write_bytes(b"%PDF-")
        self.assertEqual(list(docscan.scan(self.docs)), ["steuer/2025.pdf"])

    def test_verlinkter_ordner_wird_nicht_betreten(self):
        (self.aussen / "heimlich.md").write_text("geheim", encoding="utf-8")
        (self.docs / "ordner").symlink_to(self.aussen, target_is_directory=True)
        self.assertEqual(docscan.scan(self.docs), {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'docscan'`

- [ ] **Step 3: Modul `scripts/docscan.py` anlegen**

```python
"""Dateiauswahl für den RAG-Indexer (siehe rag-indexer.py).

Bewusst ein eigenes Modul ohne schwere Importe: die Containment-Prüfung ist
sicherheitskritisch und soll ohne installiertes venv testbar sein
(tests/test_docscan.py).
"""
from __future__ import annotations

from pathlib import Path

SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".md"}


def is_indexable(path: Path, docs_dir: Path) -> bool:
    """True, wenn path eine indexierbare Datei INNERHALB von docs_dir ist.

    Symlinks werden grundsätzlich abgelehnt. Grund: path.is_file() folgt
    ihnen, und /srv/dokumente ist für den Login-User beschreibbar — ein Link
    "notiz.md -> /etc/shadow" käme sonst in den Index und wäre anschließend
    über den Chat abrufbar. Die Endung stammt dabei vom Linknamen, der
    SUFFIXES-Filter allein schützt also nicht.
    """
    if path.is_symlink():
        return False
    if path.suffix.lower() not in SUFFIXES:
        return False
    if not path.is_file():
        return False
    try:
        aufgeloest = path.resolve(strict=True)
    except OSError:
        # Toter Link, Rechteproblem, Schleife — im Zweifel nicht indexieren.
        return False
    return aufgeloest.is_relative_to(docs_dir.resolve())


def scan(docs_dir: Path) -> dict[str, Path]:
    """Alle indexierbaren Dateien unter docs_dir als {relpfad: Pfad}.

    rglob steigt nicht in verlinkte Verzeichnisse ab; verlinkte *Dateien*
    liefert es aber aus, die filtert is_indexable() heraus.
    """
    return {
        str(p.relative_to(docs_dir)): p
        for p in sorted(docs_dir.rglob("*"))
        if is_indexable(p, docs_dir)
    }
```

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — `Ran 8 tests` … `OK`

- [ ] **Step 5: `rag-indexer.py` auf das Modul umstellen**

In `scripts/rag-indexer.py` die Zeile `import chromadb` ergänzen um einen
lokalen Import — direkt nach `import ollama` (Zeile 44) einfügen:

```python
import docscan
```

Die Konstante in Zeile 55 ersatzlos löschen:

```python
SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".md"}
```

Und die Zeilen 112-116 ersetzen. Vorher:

```python
    current = {
        str(p.relative_to(DOCS_DIR)): p
        for p in sorted(DOCS_DIR.rglob("*"))
        if p.is_file() and p.suffix.lower() in SUFFIXES
    }
```

Nachher:

```python
    current = docscan.scan(DOCS_DIR)
```

Im Docstring-Kopf unter „Eigenschaften" ergänzen:

```
- Symlinks werden übersprungen: ein Link aus DOCS_DIR heraus würde sonst
  fremde Dateien in den Index (und damit in die Chat-Antworten) tragen.
```

- [ ] **Step 6: Syntaxprüfung**

Run: `python3 -m py_compile scripts/rag-indexer.py scripts/docscan.py && echo OK`
Expected: `OK`

Run: `grep -n "SUFFIXES" scripts/rag-indexer.py`
Expected: keine Ausgabe (die Konstante lebt jetzt nur noch in `docscan.py`)

- [ ] **Step 7: Commit**

```bash
git add scripts/docscan.py tests/test_docscan.py scripts/rag-indexer.py
git commit -m "Indexer: Symlinks überspringen und Containment prüfen

Ein Symlink in /srv/dokumente wurde bisher dereferenziert und sein Inhalt
indexiert — über den Chat abrufbar. Die Dateiauswahl wandert nach
scripts/docscan.py, damit sie ohne venv testbar ist.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: State-File nach `/srv/heim-ki` umziehen

`ProtectSystem=strict` (Task 3) macht das Dateisystem schreibgeschützt. Das
Manifest muss deshalb an einen deklarierten, schreibbaren Ort. Diese Task ist
Voraussetzung für Task 3 und wird separat gehalten, weil sie ein Breaking Change
für Bestandsinstallationen ist.

**Files:**
- Modify: `scripts/rag-indexer.py:22` (Docstring), `:49` (Default)
- Modify: `scripts/backup.sh:7`, `:14` (Kommentare), `:19` (Default)

**Interfaces:**
- Consumes: nichts
- Produces: neuer Default-Pfad `/srv/heim-ki/rag-index-state.json`, auf den sich Task 3 (`ReadWritePaths=/srv/heim-ki`) und Task 8 (Migrationsabschnitt) stützen

- [ ] **Step 1: `rag-indexer.py` anpassen**

Zeile 22, vorher:

```
  STATE_FILE  (/srv/rag-index-state.json)   Manifest für die Änderungserkennung
```

nachher:

```
  STATE_FILE  (/srv/heim-ki/rag-index-state.json)  Manifest für die Änderungserkennung
```

Zeile 49, vorher:

```python
STATE_FILE = Path(os.environ.get("STATE_FILE", "/srv/rag-index-state.json"))
```

nachher:

```python
STATE_FILE = Path(os.environ.get("STATE_FILE", "/srv/heim-ki/rag-index-state.json"))
```

- [ ] **Step 2: `backup.sh` anpassen**

Zeile 7, vorher:

```
#   - Manifest des Indexers           (STATE_FILE, Default /srv/rag-index-state.json)
```

nachher:

```
#   - Manifest des Indexers           (STATE_FILE, Default /srv/heim-ki/rag-index-state.json)
```

Zeile 14, vorher:

```
#   STATE_FILE (/srv/rag-index-state.json)    Indexer-Manifest
```

nachher:

```
#   STATE_FILE (/srv/heim-ki/rag-index-state.json)  Indexer-Manifest
```

Zeile 19, vorher:

```bash
STATE_FILE="${STATE_FILE:-/srv/rag-index-state.json}"
```

nachher:

```bash
STATE_FILE="${STATE_FILE:-/srv/heim-ki/rag-index-state.json}"
```

- [ ] **Step 3: Prüfen, dass kein alter Pfad in Code oder Units übrigbleibt**

Run: `grep -rn "/srv/rag-index-state.json" scripts/ tools/ docker-compose*.yml .env.example`
Expected: keine Ausgabe

(`TUTORIAL_*.md` bleibt hier bewusst noch unverändert — die Doku kommt in Task 8.)

- [ ] **Step 4: Syntaxprüfung**

Run: `python3 -m py_compile scripts/rag-indexer.py && bash -n scripts/backup.sh && echo OK`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add scripts/rag-indexer.py scripts/backup.sh
git commit -m "State-File nach /srv/heim-ki verschieben

Vorbereitung für ProtectSystem=strict in rag-indexer.service: das Manifest
braucht ein deklariertes, schreibbares Verzeichnis. Breaking Change für
Bestandsinstallationen — Migration siehe Tutorial.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: systemd-Units entprivilegieren

Kern des Befunds: root führt nachts Dateien aus, die dem Login-User gehören. Der
Besitzverhältnis-Teil des Fixes ist eine Doku-Änderung (Task 8); hier kommen die
Unit-Dateien.

**Files:**
- Modify: `scripts/systemd/rag-indexer.service` (vollständig ersetzen)
- Modify: `scripts/systemd/heim-ki-backup.service` (vollständig ersetzen)

**Interfaces:**
- Consumes: `/srv/heim-ki/rag-index-state.json` aus Task 2
- Produces: Systemaccount-Name `heim-ki`, auf den sich Task 8 (`useradd`, `chown`) stützt

- [ ] **Step 1: `scripts/systemd/rag-indexer.service` ersetzen**

Vollständiger neuer Inhalt:

```ini
[Unit]
Description=RAG-Indexer: Docling -> bge-m3 (Ollama) -> ChromaDB
# Ollama- und Chroma-Container müssen laufen:
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
# Läuft bewusst NICHT als root: der Indexer braucht nur Lesezugriff auf
# /srv/dokumente und HTTP zu Ollama und Chroma. Den Account anlegen mit:
#   sudo useradd --system --no-create-home --shell /usr/sbin/nologin heim-ki
User=heim-ki
Group=heim-ki
ExecStart=/srv/scripts/.venv/bin/python /srv/scripts/rag-indexer.py
Nice=10

# --- Sandbox ---------------------------------------------------------------
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
# AF_UNIX wird für die Namensauflösung über nscd/systemd-resolved gebraucht —
# ohne sie schlägt je nach Host-Konfiguration schon der DNS-Lookup fehl:
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
# Muss existieren und heim-ki gehören, sonst startet die Unit nicht:
#   sudo install -d -o heim-ki -g heim-ki -m 0750 /srv/heim-ki
ReadWritePaths=/srv/heim-ki

# Konfiguration bei Bedarf (Defaults siehe Skript-Kopf):
# Environment=DOCS_DIR=/srv/dokumente
# Environment=STATE_FILE=/srv/heim-ki/rag-index-state.json
# Environment=CHROMA_URL=http://127.0.0.1:8000
# Environment=OLLAMA_URL=http://127.0.0.1:11434
```

- [ ] **Step 2: `scripts/systemd/heim-ki-backup.service` ersetzen**

Vollständiger neuer Inhalt:

```ini
[Unit]
Description=Backup der Heim-KI-Daten (Open-WebUI- und Chroma-Volume)
Wants=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=/srv/scripts/backup.sh
Nice=10

# Läuft weiterhin als root, weil das Skript den Docker-Socket braucht. Eine
# Mitgliedschaft in der docker-Gruppe wäre root-äquivalent und brächte nichts.
#
# Aus demselben Grund hier bewusst KEIN ProtectSystem=strict und keine engere
# Sandbox: wer den Docker-Socket hat, ist root — eine Sandbox drumherum
# suggeriert eine Grenze, die es nicht gibt. Der eigentliche Schutz ist, dass
# /srv/scripts root:root gehört und der Login-User dort nichts ändern kann
# (siehe Tutorial §11).
#
# ProtectHome=yes setzt voraus, dass BACKUP_DIR nicht unter /home liegt.
# Bei einem NAS-Mount unterhalb von /home die Zeile entfernen.
NoNewPrivileges=yes
ProtectHome=yes
PrivateTmp=yes

# Konfiguration bei Bedarf (Defaults siehe Skript-Kopf):
# Environment=BACKUP_DIR=/srv/backups/heim-ki
# Environment=KEEP_DAYS=14
# Environment=STATE_FILE=/srv/heim-ki/rag-index-state.json
```

- [ ] **Step 3: Prüfen, dass beide Units den Service-Account bzw. die Begründung tragen**

Run: `grep -c "^User=heim-ki" scripts/systemd/rag-indexer.service`
Expected: `1`

Run: `grep -c "^User=" scripts/systemd/heim-ki-backup.service`
Expected: `0` (bleibt bewusst root)

- [ ] **Step 4: Auf dem Zielhost prüfen (kann hier NICHT ausgeführt werden)**

Diese Befehle gehören in den Übergabebericht als „vom Betreiber auszuführen":

```bash
systemd-analyze verify /etc/systemd/system/rag-indexer.service
systemd-analyze verify /etc/systemd/system/heim-ki-backup.service
systemd-analyze security rag-indexer.service
sudo systemctl start rag-indexer.service && journalctl -u rag-indexer.service -n 50
```

Nicht als bestanden melden, solange sie nicht wirklich gelaufen sind.

- [ ] **Step 5: Commit**

```bash
git add scripts/systemd/rag-indexer.service scripts/systemd/heim-ki-backup.service
git commit -m "systemd: Indexer als eigener Account, Backup begründet als root

Beide Units liefen als root und führten Dateien aus, die laut Tutorial dem
Login-User gehören — ein Schreibzugriff genügte für root. Der Indexer braucht
kein root und bekommt einen Systemaccount plus Sandbox. Das Backup bleibt
root (Docker-Socket) und dokumentiert, warum eine Sandbox dort nichts bringt.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: alpine-Image in `backup.sh` pinnen

Der nächtliche root-Job startet `alpine` ohne Tag und ohne Digest. Das Repo pinnt
alle anderen Images bereits (`.env.example:4-13`); hier fehlt es.

**Files:**
- Modify: `scripts/backup.sh:11-14` (Konfigurationsblock), `:39` (docker run)

**Interfaces:**
- Consumes: nichts
- Produces: Umgebungsvariable `ALPINE_IMAGE`, erwähnt in Task 8

- [ ] **Step 1: Digest ermitteln**

Auf einer Maschine mit Docker ausführen:

```bash
docker pull alpine:3.22
docker inspect --format='{{index .RepoDigests 0}}' alpine:3.22
```

Ausgabe sieht aus wie `alpine@sha256:<64 Hexzeichen>`. Diesen kompletten String
in Step 2 einsetzen. Ist kein Docker verfügbar, geht auch:

```bash
docker buildx imagetools inspect alpine:3.22 --format '{{.Manifest.Digest}}'
```

Ohne ermittelten Digest **nicht** weitermachen und keinen Platzhalter
committen — dann diese Task überspringen und im Bericht als offen melden.

- [ ] **Step 2: Konfigurationsblock ergänzen**

In `scripts/backup.sh` den Kommentarblock (Zeilen 11-14) um eine Zeile erweitern.
Vorher:

```bash
# Konfiguration über Umgebungsvariablen (Default in Klammern):
#   BACKUP_DIR (/srv/backups/heim-ki)         Zielverzeichnis (gern ein NAS-Mount)
#   KEEP_DAYS  (14)                           Sicherungen älter als N Tage löschen
#   STATE_FILE (/srv/heim-ki/rag-index-state.json)  Indexer-Manifest
```

Nachher:

```bash
# Konfiguration über Umgebungsvariablen (Default in Klammern):
#   BACKUP_DIR (/srv/backups/heim-ki)         Zielverzeichnis (gern ein NAS-Mount)
#   KEEP_DAYS  (14)                           Sicherungen älter als N Tage löschen
#   STATE_FILE (/srv/heim-ki/rag-index-state.json)  Indexer-Manifest
#   ALPINE_IMAGE                              Tar-Helfer, auf Digest gepinnt
```

Direkt unter `STATE_FILE="${STATE_FILE:-...}"` (Zeile 19) einfügen:

```bash
# Auf einen Digest gepinnt wie die Images in .env.example: dieser Container
# läuft als root mit Zugriff auf beide Daten-Volumes. Auffrischen mit:
#   docker pull alpine:3.22
#   docker inspect --format='{{index .RepoDigests 0}}' alpine:3.22
ALPINE_IMAGE="${ALPINE_IMAGE:-<HIER-DEN-DIGEST-AUS-STEP-1-EINSETZEN>}"
```

- [ ] **Step 3: Verwendung umstellen**

Zeile 39 (innerhalb `backup_volume()`), vorher:

```bash
            alpine tar czf "/backup/${vol}-${STAMP}.tar.gz" -C /data .
```

nachher:

```bash
            "$ALPINE_IMAGE" tar czf "/backup/${vol}-${STAMP}.tar.gz" -C /data .
```

- [ ] **Step 4: Prüfen**

Run: `bash -n scripts/backup.sh && echo OK`
Expected: `OK`

Run: `grep -n "alpine@sha256:" scripts/backup.sh`
Expected: genau eine Zeile, mit vollständigem 64-stelligem Digest — **kein**
`<HIER-...>` mehr im Text.

- [ ] **Step 5: Commit**

```bash
git add scripts/backup.sh
git commit -m "backup.sh: alpine-Image auf Digest pinnen

Der nächtliche root-Job zog bisher 'alpine' ohne Tag. Passt jetzt zur
Pinning-Konvention aus .env.example.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: NGINX — Ollama-vHosts hinter Auth und Allowlist

`docker-compose.yml:27` bindet Ollama absichtlich an `127.0.0.1`, damit die
unauthentifizierte API nicht im Netz liegt. Die beiden Ollama-vHosts
veröffentlichen sie trotzdem LAN-weit, ohne jede Zugangskontrolle. Diese Task
setzt `satisfy all` davor. Die `proxy_pass`-Adresse des Workstation-vHosts ändert
sich erst in Task 6.

**Files:**
- Modify: `nginx/heim-ki.conf:1-8` (Kopf), `:31-57` (beide Ollama-Blöcke)
- Modify: `nginx/heim-ki-https.conf:1-18` (Kopf), `:51-83` (beide Ollama-Blöcke)

**Interfaces:**
- Consumes: nichts
- Produces: htpasswd-Pfad `/etc/nginx/heim-ki.htpasswd` und Platzhalter `<LAN-CIDR>`, beide in Task 8 dokumentiert

- [ ] **Step 1: Kopf von `nginx/heim-ki.conf` ersetzen (Zeilen 1-8)**

```nginx
# NGINX Reverse Proxy für die Heim-KI (README §6)
#
# ACHTUNG — Klartext: Diese Variante hört auf Port 80. Die Basic Auth der
# Ollama-vHosts überträgt das Passwort dort base64-kodiert im KLARTEXT, bei
# jedem Request. Jedes mitlesende Gerät im LAN kennt es danach. Für den
# Dauerbetrieb die HTTPS-Variante heim-ki-https.conf nutzen (README §10);
# diese Datei ist als Zwischenschritt vor der mkcert-Einrichtung gedacht.
#
# Installation (Debian/Ubuntu):
#   sudo cp nginx/heim-ki.conf /etc/nginx/sites-available/
#   sudo ln -s /etc/nginx/sites-available/heim-ki.conf /etc/nginx/sites-enabled/
#   sudo nginx -t && sudo systemctl reload nginx
#
# Vorher unten ersetzen:
#   <LAN-CIDR>        das eigene Heimnetz, z. B. 192.168.1.0/24
#   <WORKSTATION-IP>  die IP der Workstation
#
# Und die Passwortdatei anlegen (Paket apache2-utils):
#   sudo htpasswd -c /etc/nginx/heim-ki.htpasswd heim-ki
#   sudo chown root:www-data /etc/nginx/heim-ki.htpasswd
#   sudo chmod 640 /etc/nginx/heim-ki.htpasswd
```

- [ ] **Step 2: Beide Ollama-Blöcke in `nginx/heim-ki.conf` ersetzen (Zeilen 31-57)**

```nginx
# Ollama API (Docker-Host)
server {
    listen 80;
    server_name ollama.heim.lan;

    # Ollama bringt KEINE eigene Authentifizierung mit und veröffentlicht
    # Modellverwaltung (/api/pull, /api/delete, /api/push) ohne Nachfrage.
    # satisfy all = erlaubtes Netz UND gültige Credentials sind nötig.
    satisfy all;
    allow <LAN-CIDR>;
    deny  all;
    auth_basic           "Heim-KI Ollama";
    auth_basic_user_file /etc/nginx/heim-ki.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:11434;
        # Credentials nicht an Ollama weiterreichen — es kann nichts damit
        # anfangen und würde sie nur mitloggen:
        proxy_set_header Authorization "";
        # Ollama lehnt fremde Host-Header je nach Version als Schutz vor
        # DNS-Rebinding ab (403) — deshalb localhost senden, nicht $host.
        # Das hebelt diesen Schutz aus und ist NUR vertretbar, weil die
        # auth_basic oben davorsteht: ein Browser-Request von einer fremden
        # Seite kann keine gültigen Credentials beibringen. Wer die Auth
        # entfernt, muss auch diese Zeile entfernen.
        proxy_set_header Host 127.0.0.1:11434;
        proxy_read_timeout 600s;   # große Modelle brauchen Zeit
        proxy_buffering off;       # Token-Streaming
    }
}

# Ollama API (Workstation, on demand)
server {
    listen 80;
    server_name ollama-ws.heim.lan;

    satisfy all;
    allow <LAN-CIDR>;
    deny  all;
    auth_basic           "Heim-KI Ollama";
    auth_basic_user_file /etc/nginx/heim-ki.htpasswd;

    location / {
        proxy_pass http://<WORKSTATION-IP>:11434;
        proxy_set_header Authorization "";
        proxy_set_header Host 127.0.0.1:11434;   # s. o.
        proxy_read_timeout 600s;
        proxy_buffering off;
    }
}
```

- [ ] **Step 3: Kopf von `nginx/heim-ki-https.conf` ergänzen**

Nach der Zeile `# Vorher: <WORKSTATION-IP> unten durch die IP der Windows-Workstation ersetzen.`
(Zeile 18) anfügen:

```nginx
# Ebenfalls vorher: <LAN-CIDR> unten durch das eigene Heimnetz ersetzen
# (z. B. 192.168.1.0/24) und die Passwortdatei für die Ollama-vHosts anlegen
# (Paket apache2-utils):
#   sudo htpasswd -c /etc/nginx/heim-ki.htpasswd heim-ki
#   sudo chown root:www-data /etc/nginx/heim-ki.htpasswd
#   sudo chmod 640 /etc/nginx/heim-ki.htpasswd
```

- [ ] **Step 4: Beide Ollama-Blöcke in `nginx/heim-ki-https.conf` ersetzen (Zeilen 51-83)**

```nginx
# Ollama API (Docker-Host)
server {
    listen 443 ssl;
    server_name ollama.heim.lan;

    ssl_certificate     /etc/nginx/certs/heim-ki.pem;
    ssl_certificate_key /etc/nginx/certs/heim-ki-key.pem;

    # Ollama bringt KEINE eigene Authentifizierung mit und veröffentlicht
    # Modellverwaltung (/api/pull, /api/delete, /api/push) ohne Nachfrage.
    # satisfy all = erlaubtes Netz UND gültige Credentials sind nötig.
    satisfy all;
    allow <LAN-CIDR>;
    deny  all;
    auth_basic           "Heim-KI Ollama";
    auth_basic_user_file /etc/nginx/heim-ki.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:11434;
        # Credentials nicht an Ollama weiterreichen:
        proxy_set_header Authorization "";
        # Ollama lehnt fremde Host-Header je nach Version als Schutz vor
        # DNS-Rebinding ab (403) — deshalb localhost senden, nicht $host.
        # Das hebelt diesen Schutz aus und ist NUR vertretbar, weil die
        # auth_basic oben davorsteht. Wer die Auth entfernt, muss auch
        # diese Zeile entfernen.
        proxy_set_header Host 127.0.0.1:11434;
        proxy_read_timeout 600s;   # große Modelle brauchen Zeit
        proxy_buffering off;       # Token-Streaming
    }
}

# Ollama API (Workstation, on demand)
server {
    listen 443 ssl;
    server_name ollama-ws.heim.lan;

    ssl_certificate     /etc/nginx/certs/heim-ki.pem;
    ssl_certificate_key /etc/nginx/certs/heim-ki-key.pem;

    satisfy all;
    allow <LAN-CIDR>;
    deny  all;
    auth_basic           "Heim-KI Ollama";
    auth_basic_user_file /etc/nginx/heim-ki.htpasswd;

    location / {
        proxy_pass http://<WORKSTATION-IP>:11434;
        proxy_set_header Authorization "";
        proxy_set_header Host 127.0.0.1:11434;   # s. o.
        proxy_read_timeout 600s;
        proxy_buffering off;
    }
}
```

- [ ] **Step 5: Prüfen**

Run: `grep -c "satisfy all" nginx/heim-ki.conf nginx/heim-ki-https.conf`
Expected: je `2` (nur die Ollama-vHosts, nicht `chat.heim.lan`)

Run: `grep -n "auth_basic" nginx/heim-ki.conf | wc -l`
Expected: `4` (zwei Blöcke × `auth_basic` + `auth_basic_user_file`)

Run: `awk '/server_name chat.heim.lan/,/^}/' nginx/heim-ki.conf | grep -c auth_basic`
Expected: `0` — Open WebUI behält seine eigene Anmeldung, davor gehört keine zweite

- [ ] **Step 6: Auf dem Zielhost prüfen (kann hier NICHT ausgeführt werden)**

```bash
sudo nginx -t
curl -si http://ollama.heim.lan/api/tags | head -1          # erwartet: 401
curl -si -u heim-ki:PASS http://ollama.heim.lan/api/tags | head -1   # erwartet: 200
# von einem Gerät außerhalb <LAN-CIDR>:
curl -si -u heim-ki:PASS http://ollama.heim.lan/api/tags | head -1   # erwartet: 403
```

- [ ] **Step 7: Commit**

```bash
git add nginx/heim-ki.conf nginx/heim-ki-https.conf
git commit -m "NGINX: Ollama-vHosts hinter Allowlist und Basic Auth

Die vHosts veröffentlichten die unauthentifizierte Ollama-API LAN-weit und
machten die 127.0.0.1-Bindung aus docker-compose.yml wirkungslos. Jetzt
satisfy all: erlaubtes Netz UND Credentials. Der Host-Rewrite bleibt, ist
aber als abhängig von der Auth dokumentiert.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Workstation auf Loopback + SSH-Reverse-Tunnel

`OLLAMA_HOST=0.0.0.0:11434` lässt das Notebook in *jedem* Netz lauschen, dem es
beitritt — auch im Café-WLAN. Und weil der Port direkt erreichbar ist, lässt sich
die NGINX-Auth aus Task 5 einfach umgehen.

**Files:**
- Modify: `scripts/launchd/de.heim-ki.ollama-ws.plist` (vollständig ersetzen)
- Create: `scripts/launchd/de.heim-ki.ollama-tunnel.plist`
- Modify: `nginx/heim-ki.conf`, `nginx/heim-ki-https.conf` (je der `ollama-ws`-Block)

**Interfaces:**
- Consumes: die `ollama-ws`-Blöcke aus Task 5
- Produces: Tunnel-Port `127.0.0.1:11435` auf dem Docker-Host, Account `ollama-tunnel`, Schlüssel `~/.ssh/id_ollama_tunnel` — alle drei in Task 8 dokumentiert

**Achtung XML:** In `.plist`-Dateien dürfen die Platzhalter **keine spitzen
Klammern** tragen. `<DOCKER-HOST-IP>` würde die Datei ungültig machen und
`plutil -lint` scheitern lassen. Hier heißen sie blank `DOCKER-HOST-IP` und
`BENUTZER`.

- [ ] **Step 1: `scripts/launchd/de.heim-ki.ollama-ws.plist` ersetzen**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<!-- Ollama auf einer Mac-Workstation (README §5).

     Ollama lauscht hier NUR auf 127.0.0.1. Erreichbar wird es für den
     Docker-Host über den SSH-Reverse-Tunnel aus
     de.heim-ki.ollama-tunnel.plist, der es dort auf 127.0.0.1:11435 legt.

     Warum nicht OLLAMA_HOST=0.0.0.0? Ollama hat keinerlei Authentifizierung.
     An 0.0.0.0 gebunden lauscht das Notebook in JEDEM Netz, dem es beitritt —
     auch im Café- oder Hotel-WLAN, wo jeder Nachbar Modelle löschen, eigene
     nachladen oder auf fremde Kosten rechnen lassen kann.

     Warum ein eigener LaunchAgent statt "brew services start ollama" plus
     "launchctl setenv OLLAMA_HOST …"? brew services generiert seine plist bei
     jedem Restart/Upgrade neu, und per launchctl gesetzte Variablen sind nach
     einem Neustart weg. Hier ist OLLAMA_HOST fest verdrahtet.

     Installation (ohne sudo; NICHT zusätzlich "brew services start ollama"
     ausführen — zwei Instanzen streiten sich sonst um Port 11434):
       mkdir -p ~/Library/LaunchAgents /opt/heim-ki/logs
       cp scripts/launchd/de.heim-ki.ollama-ws.plist ~/Library/LaunchAgents/
       launchctl load -w ~/Library/LaunchAgents/de.heim-ki.ollama-ws.plist -->
<dict>
    <key>Label</key>
    <string>de.heim-ki.ollama-ws</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>-c</string>
        <string>exec ollama serve</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <!-- ollama liegt in /opt/homebrew/bin (Apple Silicon) bzw. /usr/local/bin (Intel) -->
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>OLLAMA_HOST</key>
        <string>127.0.0.1:11434</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>

    <!-- Nicht nach /tmp: das ist world-writable, ein anderer lokaler Account
         könnte den Pfad als Symlink vorbelegen. Wie die Schwester-Agents. -->
    <key>StandardOutPath</key>
    <string>/opt/heim-ki/logs/ollama-ws.log</string>
    <key>StandardErrorPath</key>
    <string>/opt/heim-ki/logs/ollama-ws.log</string>
</dict>
</plist>
```

- [ ] **Step 2: `scripts/launchd/de.heim-ki.ollama-tunnel.plist` anlegen**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<!-- SSH-Reverse-Tunnel: stellt das lokale Ollama (127.0.0.1:11434) auf dem
     Docker-Host unter 127.0.0.1:11435 bereit, wo der NGINX es abholt
     (vHost ollama-ws.heim.lan).

     Ist das Notebook aus oder unterwegs, steht der Tunnel nicht und
     ollama-ws.heim.lan antwortet mit 502. Das ist so gewollt.

     Einmalige Vorbereitung auf der Workstation:
       ssh-keygen -t ed25519 -f ~/.ssh/id_ollama_tunnel -N ""
       ssh-keyscan DOCKER-HOST-IP >> ~/.ssh/known_hosts   # Fingerprint prüfen!
     Auf dem Docker-Host (siehe Tutorial §5):
       sudo useradd --system --create-home --shell /usr/sbin/nologin ollama-tunnel
       # in ~ollama-tunnel/.ssh/authorized_keys, EINE Zeile:
       #   restrict,port-forwarding,permitlisten="127.0.0.1:11435" ssh-ed25519 AAAA...

     Installation (DOCKER-HOST-IP und BENUTZER unten vorher ersetzen):
       mkdir -p ~/Library/LaunchAgents /opt/heim-ki/logs
       cp scripts/launchd/de.heim-ki.ollama-tunnel.plist ~/Library/LaunchAgents/
       launchctl load -w ~/Library/LaunchAgents/de.heim-ki.ollama-tunnel.plist -->
<dict>
    <key>Label</key>
    <string>de.heim-ki.ollama-tunnel</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/ssh</string>
        <string>-N</string>
        <string>-T</string>
        <!-- Ohne ExitOnForwardFailure bliebe eine SSH-Verbindung bestehen,
             deren Weiterleitung fehlgeschlagen ist — launchd merkte nichts
             davon und der Tunnel wäre still tot. -->
        <string>-o</string><string>ExitOnForwardFailure=yes</string>
        <string>-o</string><string>ServerAliveInterval=30</string>
        <string>-o</string><string>ServerAliveCountMax=3</string>
        <string>-o</string><string>IdentitiesOnly=yes</string>
        <string>-i</string><string>/Users/BENUTZER/.ssh/id_ollama_tunnel</string>
        <string>-R</string><string>127.0.0.1:11435:127.0.0.1:11434</string>
        <string>ollama-tunnel@DOCKER-HOST-IP</string>
    </array>

    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>

    <key>StandardOutPath</key>
    <string>/opt/heim-ki/logs/ollama-tunnel.log</string>
    <key>StandardErrorPath</key>
    <string>/opt/heim-ki/logs/ollama-tunnel.log</string>
</dict>
</plist>
```

- [ ] **Step 3: NGINX auf den Tunnel-Port umstellen**

In `nginx/heim-ki.conf` und `nginx/heim-ki-https.conf` jeweils im
`ollama-ws.heim.lan`-Block ersetzen. Vorher:

```nginx
        proxy_pass http://<WORKSTATION-IP>:11434;
```

nachher:

```nginx
        # Die Workstation lauscht nur auf ihrem eigenen Loopback und legt den
        # Port per SSH-Reverse-Tunnel hierher (siehe scripts/launchd/
        # de.heim-ki.ollama-tunnel.plist). Steht der Tunnel nicht, weil das
        # Notebook aus oder unterwegs ist, gibt es hier 502 — so gewollt.
        proxy_pass http://127.0.0.1:11435;
```

Außerdem in beiden Dateiköpfen die Zeile über `<WORKSTATION-IP>` streichen — der
Platzhalter kommt jetzt nirgends mehr vor. In `nginx/heim-ki.conf` also aus

```nginx
# Vorher unten ersetzen:
#   <LAN-CIDR>        das eigene Heimnetz, z. B. 192.168.1.0/24
#   <WORKSTATION-IP>  die IP der Workstation
```

schlicht

```nginx
# Vorher unten ersetzen:
#   <LAN-CIDR>  das eigene Heimnetz, z. B. 192.168.1.0/24
```

und in `nginx/heim-ki-https.conf` die Zeile
`# Vorher: <WORKSTATION-IP> unten durch die IP der Windows-Workstation ersetzen.`
ersatzlos löschen.

- [ ] **Step 4: Prüfen**

Run: `plutil -lint scripts/launchd/*.plist`
Expected: für alle vier Dateien `OK`

Run: `grep -rn "WORKSTATION-IP" nginx/`
Expected: keine Ausgabe

Run: `grep -rn "0\.0\.0\.0" scripts/launchd/`
Expected: keine Ausgabe

Run: `grep -rn "/tmp/" scripts/launchd/`
Expected: keine Ausgabe

- [ ] **Step 5: Auf dem Zielhost prüfen (kann hier NICHT ausgeführt werden)**

```bash
# Auf der Workstation, nach launchctl load:
lsof -iTCP:11434 -sTCP:LISTEN     # erwartet: NUR 127.0.0.1, kein *
# Auf dem Docker-Host:
ss -ltn 'sport = :11435'          # erwartet: 127.0.0.1:11435
curl -si -u heim-ki:PASS http://ollama-ws.heim.lan/api/tags | head -1   # erwartet: 200
# Notebook herunterfahren, dann erneut:
curl -si -u heim-ki:PASS http://ollama-ws.heim.lan/api/tags | head -1   # erwartet: 502
```

- [ ] **Step 6: Commit**

```bash
git add scripts/launchd/de.heim-ki.ollama-ws.plist \
        scripts/launchd/de.heim-ki.ollama-tunnel.plist \
        nginx/heim-ki.conf nginx/heim-ki-https.conf
git commit -m "Workstation: Ollama auf Loopback, Zugang per SSH-Reverse-Tunnel

OLLAMA_HOST=0.0.0.0 ließ das Notebook in jedem Netz lauschen, dem es beitrat,
und machte die NGINX-Auth umgehbar. Der Port ist jetzt auf keinem Interface
offen; der Docker-Host bekommt ihn über einen auf permitlisten beschränkten
SSH-Tunnel. Log-Pfad wandert aus dem world-writable /tmp.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Open-WebUI-Defaults schärfen

`ENABLE_SIGNUP` bleibt aus Bootstrap-Gründen `true` — ohne offene Registrierung
gäbe es keinen Weg zum ersten Admin-Account. Aber die Standardrolle wird
explizit gesetzt, statt sich auf den Upstream-Default zu verlassen.

**Files:**
- Modify: `docker-compose.yml:52-55`
- Modify: `docker-compose.macos.yml:25-26`
- Modify: `.env.example:19-22`

**Interfaces:**
- Consumes: nichts
- Produces: nichts (Task 8 dokumentiert die Korrektur)

- [ ] **Step 1: `docker-compose.yml` ändern (Zeilen 52-55)**

Vorher:

```yaml
      # Nach dem Anlegen des Admin-Accounts in der .env auf "false" setzen
      # (oder alternativ in den Admin-Einstellungen die Standardrolle neuer
      # Nutzer auf "Ausstehend" stellen):
      - ENABLE_SIGNUP=${ENABLE_SIGNUP:-true}
```

Nachher:

```yaml
      # Muss beim ersten Start "true" sein, sonst lässt sich der Admin-Account
      # gar nicht anlegen. Danach in den ADMIN-EINSTELLUNGEN abschalten — ein
      # späteres ENABLE_SIGNUP=false in der .env bleibt wirkungslos, weil Open
      # WebUI diesen Wert nur beim allerersten Start aus der Umgebung liest und
      # ihn danach aus der Datenbank nimmt.
      - ENABLE_SIGNUP=${ENABLE_SIGNUP:-true}
      # Neue Konten landen auf "ausstehend" und müssen freigegeben werden —
      # damit ist eine offene Registrierung für sich genommen harmlos:
      - DEFAULT_USER_ROLE=${DEFAULT_USER_ROLE:-pending}
```

- [ ] **Step 2: `docker-compose.macos.yml` ändern (Zeilen 25-26)**

Vorher:

```yaml
      # Nach dem Anlegen des Admin-Accounts in der .env auf "false" setzen:
      - ENABLE_SIGNUP=${ENABLE_SIGNUP:-true}
```

Nachher:

```yaml
      # Muss beim ersten Start "true" sein, sonst lässt sich der Admin-Account
      # gar nicht anlegen. Danach in den ADMIN-EINSTELLUNGEN abschalten — ein
      # späteres ENABLE_SIGNUP=false in der .env bleibt wirkungslos (siehe
      # Kommentar in docker-compose.yml).
      - ENABLE_SIGNUP=${ENABLE_SIGNUP:-true}
      # Neue Konten landen auf "ausstehend" und müssen freigegeben werden:
      - DEFAULT_USER_ROLE=${DEFAULT_USER_ROLE:-pending}
```

- [ ] **Step 3: `.env.example` ändern (Zeilen 19-22)**

Vorher:

```
# --- Open WebUI --------------------------------------------------------------
# Nach dem Anlegen des Admin-Accounts auf false setzen, sonst kann sich
# jeder im LAN selbst registrieren:
ENABLE_SIGNUP=true
```

Nachher:

```
# --- Open WebUI --------------------------------------------------------------
# Beim ERSTEN Start muss das "true" sein, sonst lässt sich kein Admin-Account
# anlegen. Zum Abschalten danach die Admin-Einstellungen benutzen: ein Ändern
# auf "false" an dieser Stelle wirkt bei einer bereits laufenden Installation
# NICHT mehr (Open WebUI liest den Wert nur beim allerersten Start hier).
ENABLE_SIGNUP=true
# Standardrolle neuer Konten. "pending" heißt: ein Admin muss freigeben.
DEFAULT_USER_ROLE=pending
```

- [ ] **Step 4: Prüfen**

Run: `grep -c "DEFAULT_USER_ROLE" docker-compose.yml docker-compose.macos.yml .env.example`
Expected: je `1`

Run: `docker compose config --quiet && echo OK`
Expected: `OK` (nur falls Docker verfügbar; sonst überspringen und im Bericht vermerken)

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml docker-compose.macos.yml .env.example
git commit -m "Open WebUI: Standardrolle explizit auf pending

DEFAULT_USER_ROLE wird jetzt gesetzt statt auf den Upstream-Default vertraut.
Außerdem korrigiert: ENABLE_SIGNUP=false in der .env wirkt bei laufender
Installation nicht, weil Open WebUI den Wert nur beim ersten Start von dort
liest.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Tutorials und Falschaussagen

Ohne diese Task sind die vorigen sieben nicht benutzbar: die Installationswege
stimmen nicht mehr, und zwei Aussagen in den Tutorials sind sachlich falsch.
`TUTORIAL_DE.md` und `TUTORIAL_EN.md` sind Übersetzungen voneinander und werden
**immer gemeinsam** geändert.

**Files:**
- Modify: `TUTORIAL_DE.md` und `TUTORIAL_EN.md` — Zeilen 173, 203, 220, 238, 262, 355, 418-431, 456-458, 464, 514, 582-586, 598, 610
- Modify: `REVIEW-UND-PLAN.md:54`

**Interfaces:**
- Consumes: alles aus den Tasks 1-7
- Produces: nichts

- [ ] **Step 1: Falschaussage zur Netz-Erreichbarkeit korrigieren (Zeile 173)**

`TUTORIAL_DE.md:173`, vorher:

```
Beide Ports sind bewusst **nur an `127.0.0.1` gebunden**: Aus dem LAN kommt man ausschließlich über den NGINX aus §6 — so gibt es genau einen Eingang, und die unauthentifizierte Ollama-API liegt nicht offen im Netz.
```

nachher:

```
Beide Ports sind bewusst **nur an `127.0.0.1` gebunden**: Aus dem LAN kommt man ausschließlich über den NGINX aus §6 — so gibt es genau einen Eingang. Ollama selbst hat keinerlei Authentifizierung, deshalb steht sein vHost dort hinter einer IP-Allowlist **und** einer Basic Auth (`satisfy all`); ohne beides gibt es keinen Zugriff auf `/api/pull`, `/api/delete` und Konsorten.
```

`TUTORIAL_EN.md:173`, vorher:

```
Both ports are deliberately **bound to `127.0.0.1` only**: from the LAN, access happens exclusively through the NGINX from §6 — so there's exactly one entry point, and the unauthenticated Ollama API isn't exposed on the network.
```

nachher:

```
Both ports are deliberately **bound to `127.0.0.1` only**: from the LAN, access happens exclusively through the NGINX from §6 — so there's exactly one entry point. Ollama itself has no authentication whatsoever, so its vHost there sits behind an IP allowlist **and** basic auth (`satisfy all`); without both, there is no access to `/api/pull`, `/api/delete` and friends.
```

Der jeweils folgende Klammersatz ab „(Das heißt auch: …" bzw. „(This also means …"
bleibt unverändert.

- [ ] **Step 2: Windows-Firewall-Regeln einschränken (Zeilen 220 und 262)**

Beide Vorkommen in **beiden** Tutorials. Vorher:

```powershell
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
```

nachher (die WSL-Variante bei Zeile 262 analog, mit ihrem eigenen DisplayName
„Ollama WSL"):

```powershell
# -RemoteAddress und -Profile sind wichtig: ohne sie gilt die Regel in JEDEM
# Netz, auch im Profil "Öffentlich" — und Ollama hat keine Authentifizierung.
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -LocalPort 11434 `
  -Protocol TCP -Action Allow -RemoteAddress <DOCKER-HOST-IP> -Profile Private
```

- [ ] **Step 3: `OLLAMA_ORIGINS=*` streichen (Zeile 355)**

`TUTORIAL_DE.md:355`, vorher:

```
**Falls Ollama über den Proxy `403 Forbidden` liefert:** Das ist Ollamas Schutz gegen fremde Host-/Origin-Header. Die Konfiguration oben umgeht das bereits (`proxy_set_header Host 127.0.0.1:11434;`); alternativ kann man Ollama mit `OLLAMA_ORIGINS=*` (bzw. einer konkreten Liste) starten.
```

nachher:

```
**Falls Ollama über den Proxy `403 Forbidden` liefert:** Das ist Ollamas Schutz gegen fremde Host-/Origin-Header. Die Konfiguration oben umgeht das bereits (`proxy_set_header Host 127.0.0.1:11434;`). `OLLAMA_ORIGINS=*` löst das Problem zwar auch, ist aber keine gute Idee: damit darf jede beliebige Webseite, die jemand im Haushalt öffnet, Requests an die API schicken. Die Umgehung des Host-Checks ist hier nur deshalb vertretbar, weil im NGINX Allowlist und Basic Auth davorstehen.
```

`TUTORIAL_EN.md:355`, vorher:

```
**If Ollama returns `403 Forbidden` through the proxy:** That's Ollama's protection against foreign Host/Origin headers. The configuration above already works around this (`proxy_set_header Host 127.0.0.1:11434;`); alternatively, Ollama can be started with `OLLAMA_ORIGINS=*` (or a specific list).
```

nachher:

```
**If Ollama returns `403 Forbidden` through the proxy:** That's Ollama's protection against foreign Host/Origin headers. The configuration above already works around this (`proxy_set_header Host 127.0.0.1:11434;`). `OLLAMA_ORIGINS=*` would also fix it, but it's a bad idea: it lets any web page anyone in the household opens send requests to the API. Working around the host check is only defensible here because the allowlist and basic auth sit in front of it in NGINX.
```

- [ ] **Step 4: macOS-Abschnitt §5 um den Tunnel erweitern (Zeilen 203, 238)**

Zeile 203 beschreibt bisher, `OLLAMA_HOST` in der plist wegzulassen oder zu
ersetzen. Neu zu beschreiben:

1. Die `ollama-ws`-plist bindet an `127.0.0.1` — nichts mehr zu ersetzen.
2. Schlüssel erzeugen: `ssh-keygen -t ed25519 -f ~/.ssh/id_ollama_tunnel -N ""`
3. Host-Key hinterlegen: `ssh-keyscan <DOCKER-HOST-IP> >> ~/.ssh/known_hosts`,
   mit Hinweis, den Fingerprint gegen `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`
   auf dem Docker-Host zu prüfen.
4. Auf dem Docker-Host:
   ```bash
   sudo useradd --system --create-home --shell /usr/sbin/nologin ollama-tunnel
   sudo -u ollama-tunnel mkdir -p ~ollama-tunnel/.ssh
   sudo -u ollama-tunnel tee ~ollama-tunnel/.ssh/authorized_keys <<'EOF'
   restrict,port-forwarding,permitlisten="127.0.0.1:11435" ssh-ed25519 AAAA... kommentar
   EOF
   sudo -u ollama-tunnel chmod 600 ~ollama-tunnel/.ssh/authorized_keys
   ```
   Erklären, was die Optionen tun: `restrict` schaltet alles ab,
   `port-forwarding` schaltet nur die Weiterleitung wieder an, `permitlisten`
   begrenzt sie auf genau diesen einen Port. `nologin` ist unproblematisch, weil
   `ssh -N` keine Shell startet.
5. Tunnel-Agent installieren (Befehle aus dem plist-Kopf).
6. Hinweis auf das 502-Verhalten bei abwesendem Notebook.

Der macOS-Firewall-Hinweis bei Zeile 238 („eingehende Verbindungen erlauben")
entfällt — es kommen keine eingehenden Verbindungen mehr an.

- [ ] **Step 5: Indexer-Installation §7 umschreiben (Zeilen 418-431)**

Der Block enthält aktuell `sudo chown -R "$USER" /srv/scripts /srv/dokumente` —
genau die Ursache der Rechte-Eskalation. Neu:

```bash
sudo mkdir -p /srv/scripts /srv/dokumente
sudo cp scripts/rag-indexer.py scripts/docscan.py scripts/requirements.txt /srv/scripts/

# Systemaccount für den nächtlichen Lauf:
sudo useradd --system --no-create-home --shell /usr/sbin/nologin heim-ki
sudo install -d -o heim-ki -g heim-ki -m 0750 /srv/heim-ki

# /srv/scripts gehört bewusst root: der Timer führt den Inhalt aus, und was
# root ausführt, darf der Login-User nicht ändern können.
sudo chown -R root:root /srv/scripts
sudo chmod -R go-w /srv/scripts

# venv mit sudo bauen, damit es ebenfalls root gehört:
sudo python3 -m venv /srv/scripts/.venv
sudo /srv/scripts/.venv/bin/pip install -r /srv/scripts/requirements.txt

# Dokumente: der Login-User schreibt, der Dienst liest. setgid, damit neue
# Dateien die Gruppe erben; nicht world-readable.
sudo chown -R "$USER":heim-ki /srv/dokumente
sudo chmod 2750 /srv/dokumente

# Testlauf als der Account, der später auch der Timer ist:
sudo -u heim-ki /srv/scripts/.venv/bin/python /srv/scripts/rag-indexer.py
```

Wichtig: `scripts/docscan.py` ist neu und **muss** mitkopiert werden, sonst
scheitert der Import.

Der macOS-Zweig (Zeilen 439-446) behält `sudo chown -R "$USER" /opt/heim-ki` —
dort läuft der Job als LaunchAgent in der Nutzersitzung, es gibt keine
Privilegiengrenze zu schützen. Das im Text kurz begründen.

Die Cron-Alternative in Zeile 464 um `sudo -u heim-ki` ergänzen.

- [ ] **Step 6: Migrationsabschnitt für Bestandsinstallationen ergänzen**

Neuer Kasten in §7, direkt nach der Installation:

```bash
# Nur für Bestandsinstallationen: Manifest umziehen, sonst indexiert der
# nächste Lauf alle Dokumente noch einmal komplett neu.
sudo install -d -o heim-ki -g heim-ki -m 0750 /srv/heim-ki
sudo mv /srv/rag-index-state.json /srv/heim-ki/
sudo chown heim-ki:heim-ki /srv/heim-ki/rag-index-state.json
```

- [ ] **Step 7: Backup-Installation §11 anpassen (Zeilen 582-586, 610)**

Nach `sudo cp scripts/backup.sh /srv/scripts/` ergänzen:

```bash
sudo chown root:root /srv/scripts/backup.sh
sudo chmod 755 /srv/scripts/backup.sh
```

Und einen kurzen Absatz: Das Backup bleibt bewusst root, weil es den
Docker-Socket braucht; die docker-Gruppe wäre root-äquivalent. Der Schutz liegt
darin, dass `/srv/scripts` root gehört. Bei Zeile 610 den neuen `STATE_FILE`-Pfad
nachziehen; ebenso in Zeile 514 (`sudo /srv/scripts/backup.sh`-Beispiel).

- [ ] **Step 8: NGINX-Abschnitt §6 um htpasswd erweitern**

Neuer Schritt vor dem `nginx -t`:

```bash
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/heim-ki.htpasswd heim-ki
sudo chown root:www-data /etc/nginx/heim-ki.htpasswd
sudo chmod 640 /etc/nginx/heim-ki.htpasswd
```

Dazu der Hinweis, `<LAN-CIDR>` in der Konfiguration zu ersetzen, und die
Warnung, dass Basic Auth über die HTTP-Variante im Klartext läuft — für den
Dauerbetrieb §10 (HTTPS) einrichten.

- [ ] **Step 9: `REVIEW-UND-PLAN.md:54` ergänzen**

Im Tabellenfeld von Punkt **S4**, am Ende von Teil (c), anfügen:

```
(c) … erwähnenswert. — **Erledigt 2026-08-30:** beide Ollama-vHosts stehen
jetzt hinter Allowlist + Basic Auth, die Workstation lauscht nur noch auf
Loopback (SSH-Reverse-Tunnel). Siehe
docs/superpowers/specs/2026-08-30-security-remediation-design.md.
```

Die Tabelle sonst nicht umschreiben — das Dokument ist ein historisches Review,
kein laufender Tracker.

- [ ] **Step 10: Synchronität prüfen**

Run: `grep -c "" TUTORIAL_DE.md TUTORIAL_EN.md`
Expected: **identische** Zeilenzahl in beiden Dateien (sie sind Übersetzungen
voneinander; vor dieser Task sind es je 688)

Run: `grep -rn "chown -R \"\$USER\" /srv/scripts" TUTORIAL_DE.md TUTORIAL_EN.md`
Expected: keine Ausgabe

Run: `grep -c "OLLAMA_ORIGINS" TUTORIAL_DE.md TUTORIAL_EN.md`
Expected: je `1` — das Vorkommen bleibt, aber nur noch als Abratung. Gegenprobe:

Run: `grep -n "alternativ kann man Ollama mit\|alternatively, Ollama can be started" TUTORIAL_DE.md TUTORIAL_EN.md`
Expected: keine Ausgabe (die Empfehlung ist weg)

Run: `grep -rn "/srv/rag-index-state.json" TUTORIAL_DE.md TUTORIAL_EN.md`
Expected: nur Treffer im Migrationsabschnitt (der alte Pfad im `mv`-Befehl)

Run: `grep -c "docscan.py" TUTORIAL_DE.md TUTORIAL_EN.md`
Expected: je mindestens `1`

- [ ] **Step 11: Commit**

```bash
git add TUTORIAL_DE.md TUTORIAL_EN.md REVIEW-UND-PLAN.md
git commit -m "Tutorials: Installationswege an die Härtung angepasst

- /srv/scripts bleibt root, venv wird mit sudo gebaut, heim-ki-Account
- SSH-Reverse-Tunnel statt OLLAMA_HOST=0.0.0.0 auf der Workstation
- htpasswd-Schritt für die Ollama-vHosts, Klartext-Warnung bei HTTP
- Migrationsabschnitt für das verschobene Manifest
- korrigiert: die Ollama-API war entgegen der Behauptung sehr wohl im Netz
- Windows-Firewall-Regeln auf -RemoteAddress/-Profile eingeschränkt
- OLLAMA_ORIGINS=* als Empfehlung entfernt

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Abschluss

- [ ] **Alle hier ausführbaren Prüfungen noch einmal laufen lassen**

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/rag-indexer.py scripts/docscan.py
bash -n scripts/backup.sh scripts/wol.sh
plutil -lint scripts/launchd/*.plist
```

- [ ] **Ehrlichen Übergabebericht schreiben**

Der Bericht muss klar trennen:

- **Hier verifiziert:** unittest, `py_compile`, `bash -n`, `plutil -lint`.
- **Nur vom Betreiber prüfbar:** `nginx -t`, `systemd-analyze verify`,
  `systemd-analyze security`, die `curl`-Proben (401/403/200/502),
  `lsof`/`ss` auf den Listen-Adressen, der Indexer-Testlauf als `heim-ki`.

Diese zweite Liste **nicht** als bestanden darstellen. Falls Task 4 mangels
Docker-Digest übersprungen wurde, das ausdrücklich nennen.

- [ ] **Bestandsinstallationen: Reihenfolge beim Ausrollen nennen**

1. `/srv/heim-ki` anlegen und Manifest verschieben (Task 8, Step 6)
2. `heim-ki`-Account anlegen, `/srv/scripts` auf root umstellen, venv neu bauen
3. Units neu einspielen, `systemctl daemon-reload`
4. htpasswd anlegen, NGINX neu laden
5. Zuletzt die Workstation: erst Tunnel-Agent, dann `ollama-ws`-plist neu laden

Punkt 5 zuletzt, weil zwischen Loopback-Umstellung und stehendem Tunnel eine
Lücke entsteht, in der `ollama-ws.heim.lan` mit 502 antwortet.
