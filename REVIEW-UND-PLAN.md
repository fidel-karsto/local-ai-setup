# Review des Tutorials & Plan für Erweiterungen

Review des in `README.md` enthaltenen Tutorials („Lokale Heim-KI mit Open WebUI, Ollama & RAG")
auf **Vollständigkeit** und **Plausibilität**, plus ein konkreter Umsetzungsplan für die
notwendigen und sinnvollen Ergänzungen.

Stand: 2026-08-28 · Bezug: `README.md` auf `main` (dcf46be)

> **Umsetzungsstatus:**
> - ✅ **Phase 1** (README-Korrekturen) — umgesetzt
> - ✅ **Phase 2** (Repo als ausführbares Setup) — umgesetzt
> - ✅ **Phase 3** (Indexer v2 + Retrieval-Anbindung) — umgesetzt als **Variante B1**:
>   ChromaDB-Server im Compose-Profil `rag-batch`, gehärteter Indexer
>   (`scripts/rag-indexer.py`) mit systemd-Timer (`scripts/systemd/`),
>   Open-WebUI-Tool `tools/heim_docs_suche.py` für die Suche im Chat
> - ✅ **Phase 4** (Betrieb & Komfort) — umgesetzt: HTTPS via mkcert
>   (`nginx/heim-ki-https.conf`, README §10), Backup + Restore
>   (`scripts/backup.sh` + systemd-Timer, README §11), Wake-on-LAN
>   (`scripts/wol.sh`, README §12), Troubleshooting-Kapitel (README §13);
>   Compose-Volumes mit festen Namen für eindeutiges Backup/Restore
> - ✅ **Nachtrag: macOS-Variante** — der Docker-Host und die Workstation können
>   auch ein Apple-Silicon-Mac sein: Ollama nativ (Container haben unter macOS
>   keinen GPU-Zugriff), Rest über `docker-compose.macos.yml`; launchd-Units
>   (`scripts/launchd/`) als Pendant zu den systemd-Timern; NGINX/mkcert/WoL
>   per Homebrew; Pfade unter `/opt/heim-ki` statt `/srv`

---

## Teil 1: Review-Ergebnis

**Gesamturteil:** Das Tutorial ist technisch weitgehend plausibel und deutlich vollständiger
als der ursprüngliche LinkedIn-Post (Compose-Datei, NGINX-Konfiguration und RAG-Skript sind
im Post so sicher nicht enthalten gewesen). Es ist als Anleitung nachvollziehbar aufgebaut.
Es gibt aber **eine echte Funktionslücke** (Variante B erzeugt einen Index, den nichts
abfragt), **zwei Plausibilitätsprobleme** (Fritz!Box-DNS, NGINX-Installation fehlt) und
mehrere fehlende Betriebs- und Sicherheitsthemen, die bei einem Setup „für den ganzen
Haushalt" nicht optional sind.

### 1.1 Funktionale Lücken (blockierend oder irreführend)

| # | Fund | Ort | Bewertung |
|---|---|---|---|
| **F1** | **Variante B ist unvollständig:** Das nächtliche Skript befüllt eine eigene ChromaDB unter `/srv/chroma` — aber nichts fragt diese Datenbank je ab. Open WebUI nutzt seine *interne* Chroma-Instanz (im Volume `open-webui-data`) und sieht den nächtlich gebauten Index nie. Das Tutorial endet mit „Indexieren", der Retrieval-Teil fehlt komplett. | §7, Variante B | Wer Variante B baut, hat am Ende eine tote Datenbank. Es fehlt entweder (a) ein Open-WebUI-**Tool/Function**, das die externe Chroma abfragt, (b) eine kleine Retrieval-API, oder (c) der Hinweis, stattdessen Dokumente per Skript über die **Open-WebUI-API** in eine Knowledge-Sammlung hochzuladen (dann macht Open WebUI Chunking+Embedding selbst und der Nachtjob wird trotzdem erreicht). |
| **F2** | **NGINX wird nie installiert.** §6 sagt „Der NGINX läuft ohnehin schon auf dem Docker-Host", aber kein Schritt installiert ihn (`apt install nginx`). Wer dem Tutorial linear folgt, scheitert hier. Außerdem existiert das `sites-available`/`sites-enabled`-Schema nur auf Debian/Ubuntu. | §6 | Ein Zweizeiler (Installation + Hinweis auf Distribution) fehlt. Alternativ NGINX gleich als Container in die Compose-Datei aufnehmen — dann ist alles an einem Ort. |
| **F3** | **Fritz!Box kann keine frei wählbaren DNS-Namen.** §6 empfiehlt, „im Router (z. B. Fritz!Box …) die drei Namen auf die IP zeigen zu lassen". Eine Fritz!Box vergibt aber nur `<gerätename>.fritz.box` und unterstützt weder eigene Zonen wie `heim.lan` noch mehrere Namen (CNAMEs) für ein Gerät. Die drei Namen `chat.heim.lan`, `ollama.heim.lan`, `ollama-ws.heim.lan` auf eine IP sind mit Fritz!Box-Bordmitteln nicht abbildbar. | §6, Schritt 1 | Realistische Wege: **Pi-hole/AdGuard Home** (Local DNS Records) als DNS im LAN, oder `hosts`-Einträge auf den Clients, oder mit `*.fritz.box`-Namen leben. Das Tutorial sollte das ehrlich sagen, sonst ist genau der „Clou des Setups" für Fritz!Box-Haushalte nicht reproduzierbar. |

### 1.2 Plausibel, aber mit Stolperfallen (sollten ergänzt werden)

| # | Fund | Ort |
|---|---|---|
| **S1** | **4 GB VRAM sind für Chat + Embeddings gleichzeitig knapp.** `llama3.2:3b` (~2 GB) und `bge-m3` (~1,2 GB) passen einzeln, aber bei paralleler Nutzung (RAG lädt beide!) kommt es zu Modell-Eviction bzw. CPU-Offload und spürbaren Latenzen. Hinweise auf `OLLAMA_KEEP_ALIVE`, `OLLAMA_MAX_LOADED_MODELS` und die Option, Embeddings auf CPU zu rechnen, fehlen. | §2, §4 |
| **S2** | **Ollama hinter Reverse Proxy kann 403 liefern.** Mit `proxy_set_header Host $host;` erreicht Ollama der Host `ollama.heim.lan`; je nach Ollama-Version wird das (Schutz vor DNS-Rebinding) abgelehnt. Fix gehört ins Tutorial: `proxy_set_header Host 127.0.0.1:11434;` bzw. `OLLAMA_ORIGINS` setzen. | §6 |
| **S3** | **WSL2-Weg ist fragiler als beschrieben.** Die WSL-IP ändert sich bei jedem Neustart → der `netsh portproxy` zeigt danach ins Leere. `export OLLAMA_HOST=…` in `~/.bashrc` + `ollama serve` ist zudem kein Dienst (Terminal muss offen bleiben). Fehlende Hinweise: WSL2 **mirrored networking** (Windows 11 22H2+, macht portproxy überflüssig), systemd-Service in WSL, oder — wie schon angedeutet — schlicht die native Windows-App als Standardempfehlung statt als „Alternativ". | §5 |
| **S4** | **Offene Ports / offene Registrierung.** (a) `ports: "11434:11434"` publiziert Ollama auf allen Interfaces, obwohl der Kommentar „nur intern nötig" sagt → `127.0.0.1:11434:11434` binden, NGINX proxyt ja. (b) Open WebUI hat nach dem ersten Admin standardmäßig offene Selbstregistrierung — für ein Familien-Setup okay, sollte aber bewusst konfiguriert werden (`ENABLE_SIGNUP=false` bzw. Default-Rolle „pending"). (c) Beide Ollama-APIs sind unauthentifiziert im LAN erreichbar — erwähnenswert. | §4, §6 |
| **S5** | **Indexer-Skript ist als Dauerlösung zu naiv:** indexiert jede Nacht *alles* neu (voller Embedding-Lauf), erkennt keine geänderten/gelöschten Dateien, `f.name` als ID-Präfix kollidiert bei gleichnamigen Dateien in Unterordnern, verwaiste Chunks werden nie gelöscht, und `pip install` (global) passt nicht zum Cron-Aufruf mit `/usr/bin/python3` (venv fehlt). Für bessere Retrieval-Qualität sollte zudem `chunker.contextualize(chunk)` statt `chunk.text` eingebettet werden. | §7, Variante B |
| **S6** | **Docling-Serve-Details:** Es gibt CPU- und CUDA-Image-Varianten; das Image ist RAM-hungrig (OCR!). Das `ports: 5001:5001` ist unnötig, da Open WebUI den Container über das Compose-Netz erreicht. Ein Healthcheck fehlt. | §7, Variante A |

### 1.3 Fehlende Themen (Vollständigkeit — im Post vermutlich nie enthalten, gehören aber in ein „Heim-Setup"-Tutorial)

- **Backups:** Die Volumes `ollama-data` (Modelle, verschmerzbar) und vor allem `open-webui-data` (Nutzer, Chats, Knowledge-Sammlungen!) sowie `/srv/chroma` werden nirgends gesichert.
- **Updates:** Open WebUI released sehr häufig; kein Wort zu `docker compose pull` / Watchtower und dem Risiko von `:latest`/`:main`-Tags (nicht reproduzierbar).
- **Monitoring/Diagnose:** `nvidia-smi`, `docker stats`, `docker logs`, `ollama ps` — ein kurzer „Wenn's klemmt"-Abschnitt fehlt.
- **HTTPS ist mehr als Kosmetik:** Ohne Secure Context blockieren Browser Mikrofon-Zugriff — Sprach-Ein-/Ausgabe von Open WebUI („Mama spricht mit der KI") funktioniert über `http://chat.heim.lan` von anderen Geräten aus nicht. Das steht bisher nur als „weiterführende Idee", ist aber praktisch fast Pflicht.
- **Compose-Hygiene:** keine Healthchecks, `depends_on` ohne Bedingung, keine gepinnten Image-Versionen.
- **Wake-on-LAN** ist als Idee genannt, ohne Umsetzung — passt gut als optionales Skript.

### 1.4 Was ausdrücklich in Ordnung ist

- Architektur (zwei Ollama-Instanzen, NGINX als zentraler Einstieg, DNS auf den Docker-Host mit Weiterleitung zur Workstation) ist schlüssig und gut erklärt.
- NVIDIA-Container-Toolkit-Installation und GPU-Test (§3) entsprechen der offiziellen Doku.
- Compose-GPU-Deklaration (`deploy.resources.reservations.devices`) ist korrekt.
- Variante A (Open WebUI mit Docling als Extraction Engine + `bge-m3` über Ollama) ist der richtige empfohlene Weg und korrekt konfiguriert (`http://docling:5001`, `http://ollama:11434` funktionieren im Compose-Netz).
- WebSocket-Header und `proxy_buffering off` in der NGINX-Konfiguration sind richtig gesetzt; die Docling-Schreibweise-Korrektur („Dockling") ist ein nettes Detail.

---

## Teil 2: Umsetzungsplan

Vier Phasen, nach Dringlichkeit sortiert. Phase 1+2 sind notwendig, 3+4 sinnvoll.

### Phase 1 — README-Korrekturen (notwendig, nur Doku)

**Ziel:** Wer dem Tutorial linear folgt, kommt ohne Sackgasse durch.

1. **§6 DNS-Abschnitt korrigieren (F3):** Fritz!Box-Aussage ersetzen durch ehrliche Optionen:
   Pi-hole/AdGuard Home (empfohlen, mit Mini-Anleitung „Local DNS Record anlegen"),
   `hosts`-Datei, oder `*.fritz.box`-Gerätenamen als Kompromiss.
2. **§6 NGINX-Installation ergänzen (F2):** `sudo apt install nginx` + Hinweis auf
   Debian/Ubuntu-spezifisches `sites-available`-Schema.
3. **§6 Ollama-403-Falle dokumentieren (S2):** `proxy_set_header Host 127.0.0.1:11434;`
   in die beiden Ollama-Serverblöcke, plus Hinweis auf `OLLAMA_ORIGINS`.
4. **§4 Ports härten (S4):** `127.0.0.1:11434:11434` und `127.0.0.1:3000:8080` (NGINX ist
   der einzige LAN-Eingang); Absatz „Nach dem ersten Login: Registrierung deaktivieren".
5. **§2/§4 VRAM-Realismus (S1):** Absatz zu gleichzeitigem Chat+Embedding-Betrieb,
   `OLLAMA_KEEP_ALIVE=30m`, `OLLAMA_MAX_LOADED_MODELS`, Erwartungsmanagement bei 4 GB.
6. **§5 WSL-Abschnitt umbauen (S3):** Native Windows-App als Hauptweg, WSL2 als
   Fortgeschrittenen-Variante mit Hinweis auf wechselnde WSL-IP und mirrored networking.
7. **§7 Variante B mit Warnhinweis versehen (F1):** klarstellen, dass der Index ohne
   Retrieval-Anbindung (→ Phase 3) nicht von Open WebUI genutzt wird.

**Akzeptanz:** README enthält keinen Schritt mehr, der ein nicht installiertes Werkzeug
voraussetzt oder eine Fähigkeit behauptet, die die genannte Hardware/Software nicht hat.

### Phase 2 — Repo wird ausführbares Setup (notwendig, Dateien statt Copy-Paste)

**Ziel:** Die Code-Blöcke aus dem README als echte, versionierte Dateien.

```
├── docker-compose.yml          # ollama, open-webui, docling (+ profiles)
├── .env.example                # WORKSTATION_IP, TZ, Image-Tags, OLLAMA_KEEP_ALIVE …
├── nginx/heim-ki.conf          # inkl. Host-Header-Fix aus Phase 1
└── scripts/
    └── rag-indexer.py          # Skript aus §7 Variante B
```

- Image-Tags pinnen (z. B. `open-webui:v0.6.x` statt `:main`) und im README einen
  Update-Abschnitt ergänzen (`docker compose pull && docker compose up -d`).
- Healthchecks für ollama (`ollama list`), open-webui (`/health`) und docling ergänzen;
  `depends_on` mit `condition: service_healthy`.
- Docling als Compose-**Profile** (`profiles: [rag]`), damit das Basis-Setup schlank bleibt;
  CPU-/CUDA-Image-Variante über `.env` wählbar.
- README-Codeblöcke durch Verweise auf die Dateien ersetzen (Blöcke gekürzt beibehalten,
  Dateien sind die Wahrheit).

**Akzeptanz:** `cp .env.example .env && docker compose up -d` ergibt ein lauffähiges
Basis-Setup; `docker compose --profile rag up -d` ergänzt Docling.

### Phase 3 — Variante B komplettieren (sinnvoll, schließt F1/S5)

**Ziel:** Der nächtliche Index wird tatsächlich abfragbar und der Indexer betriebstauglich.

1. **Indexer härten** (`scripts/rag-indexer.py`):
   - Änderungserkennung per Datei-Hash/mtime (Manifest in Chroma-Metadata oder JSON-Datei) —
     nur neue/geänderte Dateien werden konvertiert und eingebettet.
   - Gelöschte Dateien → zugehörige Chunks aus der Collection entfernen.
   - IDs aus relativem Pfad + Chunk-Hash statt `f.name` (keine Kollisionen).
   - `chunker.contextualize(chunk)` für die Embedding-Texte.
   - venv unter `/srv/scripts/.venv` + `requirements.txt`; Aufruf im Zeitplan entsprechend.
   - systemd-Timer (`rag-indexer.service` + `.timer`) statt Cron: Logs via journald,
     `Persistent=true` holt verpasste Läufe nach. Cron bleibt als Alternative dokumentiert.
2. **Retrieval-Anbindung** — eine von zwei Varianten (Entscheidung bei Umsetzung):
   - **B1 (empfohlen):** Open-WebUI-**Tool** (`tools/heim_docs_suche.py`), das die externe
     ChromaDB abfragt (Frage → bge-m3-Embedding → Top-k-Chunks als Kontext). Chroma dafür
     als `chromadb/chroma`-Container mit ins Compose (Profile `rag`), damit Indexer und
     Tool dieselbe DB über HTTP erreichen.
   - **B2 (einfacher):** Indexer-Skript lädt Dokumente stattdessen per Open-WebUI-API in
     eine Knowledge-Sammlung hoch — Open WebUI übernimmt Chunking/Embedding selbst, keine
     eigene Chroma nötig. (Weniger Kontrolle, dafür null Zusatzinfrastruktur.)
3. README §7 entsprechend umschreiben.

**Akzeptanz:** Eine nachts neu abgelegte PDF ist am nächsten Morgen im Chat per
`#heim-docs` (bzw. Tool-Aufruf) auffindbar; zweifacher Indexer-Lauf ohne Dateiänderung
erzeugt keine erneuten Embedding-Aufrufe.

### Phase 4 — Betrieb & Komfort (sinnvoll, optional)

1. **HTTPS im LAN** (Priorität in dieser Phase, wegen Mikrofon/Secure Context):
   Anleitung + Konfiguration für mkcert-CA auf dem Docker-Host, Zertifikate für die drei
   Namen, NGINX auf 443 umstellen, CA-Root auf den Familien-Geräten installieren.
   Alternative Caddy dokumentieren.
2. **Backup:** `scripts/backup.sh` — `open-webui-data`-Volume und `/srv/chroma` als
   tar auf ein Ziel-Verzeichnis/NAS, systemd-Timer, Restore-Anleitung im README.
   (`ollama-data` ausdrücklich ausgenommen: Modelle sind re-downloadbar.)
3. **Wake-on-LAN:** kurzer Abschnitt + `scripts/wol.sh` (`wakeonlan <MAC>`), Hinweis auf
   BIOS/Windows-Einstellungen („Fast Startup" deaktivieren).
4. **Troubleshooting-Abschnitt** im README: GPU nicht sichtbar (`nvidia-smi` im Container),
   Modell passt nicht in VRAM (`ollama ps`), Docling-OOM, 403 vom Proxy, WSL-IP gewandert.

**Akzeptanz:** `https://chat.heim.lan` ohne Zertifikatswarnung auf eingerichteten Geräten;
Backup-Timer läuft und Restore ist einmal dokumentiert durchgespielt.

### Reihenfolge & Aufwand (grob)

| Phase | Inhalt | Aufwand |
|---|---|---|
| 1 | README-Korrekturen | ~1 h |
| 2 | Compose/NGINX/Skript als Dateien, Pinning, Healthchecks | ~2 h |
| 3 | Indexer v2 + Retrieval-Anbindung | ~3–4 h |
| 4 | HTTPS, Backup, WoL, Troubleshooting | ~2–3 h |

Phase 1 und 2 können in einem PR zusammengefasst werden; Phase 3 und 4 je ein eigener PR.
