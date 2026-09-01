# Design: Dokumentkonvertierung nativ auf den Host holen (Variante C)

Datum: 2026-09-01
Status: abgestimmt, bereit für den Implementierungsplan
Grundlage: Messungen am Stand von `main` (Commit `251b712`) auf dem Mac-Studio-Host

## Ausgangslage

Der Upload eines gescannten PDFs nach Open WebUI funktioniert, das Dokument
steht danach aber minutenlang nicht zur Verfügung. Gemessen am
188-Seiten-Scan `1988_01_64er.pdf` (37,7 MB, keine Textebene — 11.485 Zeichen
über alle 188 Seiten, ausschließlich Wasserzeichen):

```
docling-serve, Ist-Zustand: 517,8 s   (aus dem Log des echten Uploads)
```

### Was die Messungen ergeben haben

Alle Zahlen: dasselbe Dokument, volle 188 Seiten, `table_mode=accurate`. Die
Container-Läufe liefern untereinander byte-identische 815.904 Zeichen — die
Thread-Zahl beeinflusst nur die Laufzeit. Der native Lauf kommt auf 841.999
Zeichen, weil dort eine andere OCR-Engine arbeitet (Apple Vision statt
RapidOCR); ein Qualitätsvergleich der beiden Texte steht noch aus.

| Weg | Zeit | Faktor |
|---|---|---|
| Container, `OMP_NUM_THREADS=2` | 766,4 s | 0,67× |
| Container, `OMP_NUM_THREADS=4` (Ist-Zustand) | 509,8 s | 1,0× |
| Container, `OMP_NUM_THREADS=6` | 346,0 s | 1,47× |
| Container, `OMP_NUM_THREADS=8` | 419,3 s | 1,22× |
| Container, `OMP_NUM_THREADS=12` | 656,8 s | 0,78× |
| **Nativ auf dem Host** (MPS + Apple Vision OCR, `num_threads=12`) | **181,5 s** | **2,8×** |

Der native Wert wurde mit `AcceleratorOptions(num_threads=12, device=MPS)`
gemessen. Für den nativen Weg wurde die Thread-Zahl **nicht** durchgemessen —
angesichts der U-Kurve im Container ist dort noch Luft, das ist beim Bau von
`docconvert.py` nachzuholen.

Drei Befunde tragen das Design:

1. **Auf Apple Silicon kann der Container die GPU nicht nutzen und wird es
   nie.** Docker Desktop reicht dort kein MPS durch; das Log zeigt dauerhaft
   `Accelerator device: 'cpu'`. OCR läuft mit RapidOCR auf der CPU. Unter
   Linux ist das anders — dort gibt es mit `docling-serve-cu124` ein
   CUDA-Image, das die NVIDIA-Karte im Container erreicht.
2. **Nativ wählt Docling auf macOS automatisch den schnellen Weg.** Mit
   `ocr_engine=auto` fällt die Wahl auf `ocrmac` (Apple Vision); die Ausgabe ist
   byte-identisch zum expliziten Setzen von `OcrMacOptions`. Das Layout-Modell
   läuft auf MPS.
3. **Die Thread-Zahl hat ein Optimum bei 6, nicht am oberen Ende.** Der
   Container zieht schon mit dem Default von 4 Threads 400–790 % CPU: ONNX
   Runtime und Torch öffnen eigene Thread-Pools, unabhängig von
   `OMP_NUM_THREADS`. Ab 8 überbucht das die 12 Performance-Kerne, bei 12 ist
   der Lauf langsamer als im Ist-Zustand. Speicherdruck ist es nicht
   (5,5–6,2 GiB von 15,6 GiB). Kurze Läufe führen hier in die Irre: auf einem
   Ausschnitt von 8 Seiten war 12 noch 2,2× schneller als 4, auf dem vollen
   Dokument ist es 29 % langsamer — die Kurve zeigt sich erst bei voller
   Dokumentlänge.

### Was *nicht* das Problem ist

Open WebUI v0.11.1 **entkoppelt Upload und Verarbeitung bereits**:
`upload_file_handler` legt die Datei mit `data.status = 'pending'` an und
schiebt die Verarbeitung in einen `BackgroundTask`
(`routers/files.py:426-435`); dafür existiert sogar
`GET /api/v1/knowledge/{id}/files/pending`. Ein eigener Batch-Job bringt also
**keine** Entkopplung, die es nicht schon gäbe. Der Gewinn liegt allein in der
Rechenzeit und darin, die Last von der interaktiven Maschine zu nehmen.

## Leitentscheidungen

1. **Die teure Konvertierung verlässt den Container** und läuft nativ auf dem
   Host. Auf Apple Silicon ist das der einzige Ort, an dem GPU und Apple
   Vision zur Verfügung stehen; unter Linux ist der Gewinn kleiner, weil dort
   auch der Container an die GPU käme.
2. **Das Ergebnis geht als Markdown in die normale Open-WebUI-Sammlung**, nicht
   in einen zweiten Speicher. Damit teilen sich Batch-Bestand und laufende
   Einzel-Uploads einen Index, `#Sammlung` funktioniert im Chat, Zitate auch.
3. **Variante B entfällt.** Ihr Zweck (nächtlicher Batch) wird von C erfüllt,
   ohne den Wissensspeicher zu spalten. ChromaDB fällt damit ganz aus dem
   Setup — Open WebUI bringt seine eigene Vektordatenbank mit.
4. **Variante A bleibt** als Weg für spontane Browser-Uploads anderer Nutzer im
   LAN — bekommt aber die Thread-Zahl als konfigurierbaren Schalter
   (Abschnitt 6), weil dort 1,47× ohne Architekturänderung zu holen sind.

---

## 1. Architektur

```
/opt/heim-ki/dokumente/…           ← Ablageordner für PDFs und Office-Dateien
        │
        │   scripts/doc-sync.py      (nativ auf dem Host, eigenes venv)
        │
        ├── docscan.scan()           Dateiauswahl + Symlink-Schutz  [unverändert aus B]
        ├── Manifest vs. SHA-256     nur Neues und Geändertes
        ├── docconvert.to_markdown() Docling nativ: MPS bzw. CUDA + Apple Vision
        └── webui_client             POST /api/v1/files/  →  /knowledge/{id}/file/add
                    │
                    ▼
        Wissenssammlung „Heim-Dokumente" in Open WebUI
             → im Chat als #Heim-Dokumente, mit Zitaten
```

### Module

| Modul | Aufgabe | Abhängigkeiten |
|---|---|---|
| `scripts/docscan.py` | Welche Dateien kommen infrage? Symlink-Schutz. Unverändert aus Variante B übernommen, samt Test. | keine |
| `scripts/docconvert.py` | *neu* — Pfad → Markdown-String. Kapselt die Docling-Optionen. Kein Netz, kein Zustand. | `docling` |
| `scripts/webui_client.py` | *neu* — API-Client: hochladen, zur Sammlung hinzufügen, entfernen, auflisten, Sammlung nach Namen auflösen. Weiß nichts von Docling. | `requests` |
| `scripts/doc-sync.py` | *neu* — Orchestrierung, Manifest, Lockfile, Logging, Exit-Code. | die drei oben |

Der Zuschnitt folgt dem, was Variante B schon richtig gemacht hat: `docscan`
bleibt importierbar ohne schwere Abhängigkeiten, damit die
sicherheitskritische Containment-Prüfung ohne venv testbar bleibt.

### Warum Markdown als `text/markdown`

Beim API-Upload setzen wir den Content-Type des Multipart-Teils selbst.
Open WebUIs `_is_text_file` greift dann über den `text/`-Zweig
(`retrieval/loaders/main.py:338-343`) und wählt den `TextLoader` — der
Docling-Container wird gar nicht erst gefragt, der Upload kostet nur noch die
Einbettung.

Das ist bewusst der API-Weg und nicht der Browser-Weg: Beim Drag-and-drop
bestimmt der Browser den Content-Type, und `md` fehlt in Open WebUIs
Endungsliste `known_source_ext` (`main.py:37-55`). Über die API hängt nichts
an fremden Annahmen.

### Warum ein eigenes Manifest trotz `sync/diff`

Open WebUI bietet `POST /api/v1/knowledge/{id}/sync/diff` an, vergleicht dort
aber `meta.file_hash` — die Prüfsumme der *hochgeladenen* Datei, also des
Markdowns. Die Frage „hat sich die Quell-PDF geändert?" beantwortet das erst,
nachdem konvertiert wurde — und genau das ist der teure Schritt.

`doc-sync.py` führt deshalb ein Manifest über die *Quelldateien*:

```json
{ "<relpfad>": { "sha256": "…", "file_id": "…" } }
```

Damit Manifest und Wirklichkeit nicht auseinanderlaufen (etwa weil die
Sammlung in der Oberfläche gelöscht wurde), gleicht jeder Lauf zusätzlich
gegen `GET /api/v1/knowledge/{id}/files` ab und lädt fehlende Einträge neu
hoch.

## 2. Betrieb

Eigenes venv wie bei Variante B: `/opt/heim-ki/scripts/.venv` unter macOS,
`/srv/scripts/.venv` unter Linux.

**Auslösung zweifach:** nächtlich per launchd-Agent (macOS) bzw.
systemd-Timer (Linux), dasselbe Skript zusätzlich jederzeit von Hand
aufrufbar.

**Lauf-Sperre:** Weil beide Wege denselben Zustand schreiben, nimmt `doc-sync`
ein Lockfile. Ein zweiter Lauf beendet sich mit einer Meldung, statt Manifest
und Sammlung durcheinanderzubringen.

**API-Key:** `/opt/heim-ki/webui-api-key`, Rechte `0600`, Pfad über
`WEBUI_API_KEY_FILE`. Nicht als Klartext in plist oder Unit, und in die
`.gitignore`.

**Sammlung:** wird über `KNOWLEDGE_NAME` (Default `Heim-Dokumente`) nach Namen
aufgelöst. Existiert sie nicht, **bricht der Lauf mit klarer Meldung ab**;
angelegt wird sie nur auf ausdrückliches `--create`. Grund: Ein Tippfehler im
Namen würde sonst still eine zweite, leere Sammlung erzeugen.

**Linux-Stolperstein:** Die systemd-Unit von Variante B setzte
`PrivateDevices=yes` und nahm dem Dienst damit `/dev/nvidia*` — der Job
rechnete auf der CPU. Für C wäre das der Verlust des gesamten Nutzens. Die
neue Unit muss das anders lösen, und die Doku muss es benennen.

### Konfiguration über Umgebungsvariablen

| Variable | Default | Bedeutung |
|---|---|---|
| `DOCS_DIR` | `/srv/dokumente` | Ablageordner |
| `STATE_FILE` | `/srv/heim-ki/doc-sync-state.json` | Manifest |
| `CACHE_DIR` | `/srv/heim-ki/cache` | Markdown-Cache |
| `WEBUI_URL` | `http://127.0.0.1:3000` | Open WebUI |
| `WEBUI_API_KEY_FILE` | `/srv/heim-ki/webui-api-key` | API-Key |
| `KNOWLEDGE_NAME` | `Heim-Dokumente` | Zielsammlung |

Die macOS-Pfade (`/opt/heim-ki/…`) kommen aus plist und Doku, nicht als
zweiter Satz Defaults — das entspricht dem Vorgehen bei Variante B.

## 3. Fehlerbehandlung

Wie bei Variante B: Eine kaputte Datei bricht den Lauf nicht ab, wird gezählt,
am Ende steht Exit-Code 1. Das Manifest wird nach jeder Datei atomar
geschrieben (`tempfile` + `os.replace`).

**Markdown-Cache.** Scheitert der Upload nach erfolgreicher Konvertierung
(Open WebUI gerade neu gestartet), wären bei diesem Dokumentbestand mehrere
Minuten OCR verloren, die der nächste Lauf erneut zahlen müsste. `doc-sync`
legt das Konvertat deshalb unter `CACHE_DIR/<sha256>.md` ab und nimmt es beim
nächsten Lauf von dort. Der Cache ist ein reiner Beschleuniger — geht er
verloren, kostet das Zeit, nie Korrektheit.

| Fall | Verhalten |
|---|---|
| Neue Datei | konvertieren → hochladen → zur Sammlung → Manifest |
| Unveränderte Datei | überspringen (SHA-256-Vergleich) |
| Geänderte Datei | alte Datei aus Sammlung entfernen → neu konvertieren und hochladen |
| Gelöschte Datei | aus Sammlung entfernen, Manifest-Eintrag weg |
| Im Manifest, aber serverseitig fehlend | neu hochladen (Cache spart die Konvertierung) |
| Konvertierung schlägt fehl | zählen, weitermachen, kein Manifest-Eintrag |
| Upload schlägt fehl | zählen, weitermachen, Konvertat bleibt im Cache |
| Open WebUI nicht erreichbar | sauberer Abbruch mit Meldung, nichts halb Geschriebenes |

## 4. Tests

`tests/test_docscan.py` bleibt unverändert bestehen.

| Neu | Deckt ab |
|---|---|
| `tests/test_docconvert.py` | Winziges erzeugtes PDF → Markdown nicht leer. Langsam, deshalb separat markiert. |
| `tests/test_webui_client.py` | Gegen einen Fake-HTTP-Server: Authorization-Header, Multipart-Content-Type (`text/markdown`), Behandlung von Fehlercodes. |
| `tests/test_doc_sync.py` | Orchestrierung mit Fake-Converter und Fake-Client: neu / unverändert / geändert / gelöscht, Reconcile bei serverseitig fehlender Datei, Cache-Treffer, kaputte Datei bricht den Lauf nicht ab, Lockfile greift. |

## 5. Repo-Umbau

**Entfällt:**

- `scripts/rag-indexer.py`
- `tools/heim_docs_suche.py`
- `scripts/systemd/rag-indexer.service`, `scripts/systemd/rag-indexer.timer`
- `scripts/launchd/de.heim-ki.rag-indexer.plist`
- `chroma`-Dienst, Profil `rag-batch` und Volume `chroma-data` in
  `docker-compose.yml` und `docker-compose.macos.yml`
- `CHROMA_IMAGE` und `CHROMA_HOST_PORT` in `.env.example` (und `.env`)
- der Chroma-Teil in `scripts/backup.sh`

**Kommt hinzu:** die drei neuen Skripte, ihre Tests, eine systemd-Unit samt
Timer und eine launchd-plist für `doc-sync`, dazu der Thread-Schalter aus
Abschnitt 6 in `.env.example` und beiden Compose-Dateien.

**Das Volume `chroma-data` wird nicht automatisch gelöscht.** Der Schritt
(`docker volume rm chroma-data`) gehört in die Doku als bewusste Handlung.

**Doku:** §7 in `TUTORIAL_DE.md` und `TUTORIAL_EN.md` (je 32 Fundstellen zu
Variante B), der RAG-Abschnitt in `README.md`, dazu `REVIEW-UND-PLAN.md`
gegenprüfen.

In die Doku gehört auch die Messkurve aus der Ausgangslage (Abschnitt 6).

## 6. Variante A: Thread-Zahl konfigurierbar machen

Unabhängig von C lässt sich der Docling-Container beschleunigen, indem die
Thread-Zahl vom Bibliotheks-Default 4 auf das gemessene Optimum 6 geht:
509,8 s → 346,0 s, also 1,47× — bei byte-identischer Ausgabe. Das kommt allen
Browser-Uploads über Variante A zugute.

Umsetzung als Schalter, nicht als fester Wert:

- neue Variable `DOCLING_OMP_THREADS` (Default `6`) in `.env.example`
- in `docker-compose.yml` und `docker-compose.macos.yml` als
  `OMP_NUM_THREADS=${DOCLING_OMP_THREADS:-6}` im `docling`-Dienst

Der Default 6 ist auf einem Mac Studio (M4 Max, 12 Performance- und
4 Effizienzkerne, Docker-VM mit 16 Kernen und 16 GB) gemessen und **gilt nicht
allgemein**. Deshalb gehört die vollständige Kurve aus der Ausgangslage ins
Tutorial, zusammen mit der Ansage: auf der eigenen Maschine nachmessen, und
zwar an einem *großen* Dokument — mehr Threads sind nicht besser, und kurze
Testläufe zeigen genau das Gegenteil.
