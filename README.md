# Lokale Heim-KI mit Open WebUI, Ollama & RAG — komplett ohne Cloud

**Tutorial: Ein privater KI-Assistent im eigenen LAN, der auch eigene Dokumente durchsuchen kann**

> Basierend auf einem Setup von Matthias Kallenbach (LinkedIn-Post). Ziel: Eine ChatGPT-ähnliche Oberfläche für alle im Haushalt („Mama kann auch die Heim-KI benutzen"), bei der **keine Daten das eigene Netzwerk verlassen**.

**Dieses Repo enthält neben dem Tutorial die fertigen Konfigurationsdateien:**
[`docker-compose.yml`](docker-compose.yml) · [`.env.example`](.env.example) · [`nginx/heim-ki.conf`](nginx/heim-ki.conf) · [`scripts/rag-indexer.py`](scripts/rag-indexer.py) · [`scripts/systemd/`](scripts/systemd) · [`tools/heim_docs_suche.py`](tools/heim_docs_suche.py)

---

## 1. Was wird gebaut? (Architektur-Überblick)

Das Setup besteht aus zwei Maschinen und mehreren Diensten:

```
                        ┌─────────────────────────────────────┐
                        │  Docker-Host (Linux, läuft 24/7)    │
                        │  GPU: NVIDIA mit 4 GB VRAM (CUDA)   │
  LAN-Clients           │                                     │
  (Browser, Handy,      │  ┌───────────┐   ┌───────────────┐  │
   "Mama") ──────────►  │  │  NGINX    │──►│  Open WebUI   │  │
                        │  │  Reverse  │   └───────┬───────┘  │
  chat.heim.lan         │  │  Proxy    │           │          │
  ollama.heim.lan       │  │  (Port    │   ┌───────▼───────┐  │
  ollama-ws.heim.lan    │  │  80/443)  │──►│ Ollama        │  │
                        │  └─────┬─────┘   │ (Container,   │  │
                        │        │         │  GPU-Zugriff) │  │
                        │        │         │  + bge-m3     │  │
                        │        │         └───────────────┘  │
                        │        │         ┌───────────────┐  │
                        │        │         │ RAG-Pipeline: │  │
                        │        │         │ Docling +     │  │
                        │        │         │ ChromaDB +    │  │
                        │        │         │ bge-m3        │  │
                        │        │         │ (nächtlicher  │  │
                        │        │         │  Batch-Job)   │  │
                        │        │         └───────────────┘  │
                        │        │                            │
                        └────────┼────────────────────────────┘
                                 │
                        ┌────────▼────────────────────────────┐
                        │  Workstation (Windows, nativ o. WSL2)│
                        │  Ollama "on demand" — nur an,       │
                        │  wenn der Rechner läuft; für        │
                        │  größere Modelle mit stärkerer GPU  │
                        └─────────────────────────────────────┘
```

**Die Komponenten im Einzelnen:**

| Komponente | Rolle |
|---|---|
| **[Ollama](https://ollama.com)** | Lokaler LLM-Server. Läuft als Docker-Container auf dem Docker-Host (mit 4-GB-CUDA-GPU) **und** zusätzlich „on demand" auf der Windows-Workstation (nativ oder WSL2). |
| **[Open WebUI](https://github.com/open-webui/open-webui)** | ChatGPT-ähnliche Weboberfläche für Ollama — mit Nutzerverwaltung, Dokumenten-Upload und eingebautem RAG. |
| **[bge-m3](https://ollama.com/library/bge-m3)** | Mehrsprachiges Embedding-Modell (BAAI). Wandelt Texte in Vektoren um — die Grundlage für die Dokumentensuche (RAG). Sehr gut für Deutsch geeignet. |
| **[Docling](https://github.com/docling-project/docling)** | Open-Source-Tool von IBM Research: konvertiert PDF, DOCX, PPTX, HTML usw. in sauberes, strukturiertes Markdown/JSON — inkl. Tabellen und Layout-Erkennung. (Im Original-Post „Dockling" geschrieben — gemeint ist Docling.) |
| **[ChromaDB](https://www.trychroma.com)** | Vektordatenbank. Speichert die von bge-m3 erzeugten Embeddings und liefert bei einer Frage die passenden Dokument-Schnipsel zurück. |
| **[NGINX](https://nginx.org)** | Reverse Proxy auf dem Docker-Host. Macht aus `http://192.168.x.y:11434` schöne, sprechende Namen wie `http://ollama.heim.lan` — „ohne komische Ports". |

**RAG** (Retrieval-Augmented Generation) bedeutet: Bevor das Sprachmodell antwortet, sucht das System in den eigenen Dokumenten nach relevanten Passagen und gibt sie dem Modell als Kontext mit. So kann die Heim-KI Fragen zu den eigenen PDFs, Verträgen, Anleitungen etc. beantworten.

---

## 2. Voraussetzungen

**Hardware:**
- Ein Linux-Rechner als Docker-Host, der dauerhaft läuft (Mini-PC, alter Desktop, Homeserver), mit einer NVIDIA-GPU mit mindestens 4 GB VRAM. Das reicht für kleine Modelle (z. B. 3B–7B quantisiert) und Embeddings. **Aber:** 4 GB sind knapp, wenn Chat- und Embedding-Modell gleichzeitig gebraucht werden — und genau das passiert bei RAG. Siehe „Realistische Erwartungen bei 4 GB VRAM" in §4.
- Optional: Eine Windows-Workstation mit stärkerer GPU für größere Modelle „on demand".

**Software:**
- Docker + Docker Compose auf dem Host → [Installationsanleitung](https://docs.docker.com/engine/install/)
- NVIDIA-Treiber + **NVIDIA Container Toolkit** (damit Container die GPU nutzen können) → [Installationsanleitung](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- Auf der Workstation: nichts Besonderes — Ollama gibt es nativ für Windows. (WSL2 nur für den Fortgeschrittenen-Weg in §5 → [Microsoft-Doku](https://learn.microsoft.com/de-de/windows/wsl/install))

**Netzwerk:**
- Möglichkeit, lokale DNS-Namen zu vergeben — realistisch per **Pi-hole/AdGuard Home** oder notfalls per `hosts`-Datei auf den Clients. Eine Fritz!Box allein kann das *nicht* frei konfigurierbar (Details und Optionen in §6, Schritt 1).

---

## 3. Docker-Host vorbereiten (GPU-Zugriff)

NVIDIA Container Toolkit installieren (Ubuntu/Debian, gekürzt — Details im Link oben):

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Test:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Wenn `nvidia-smi` die GPU anzeigt, ist alles bereit.

---

## 4. Ollama + Open WebUI als Container

Die fertige [`docker-compose.yml`](docker-compose.yml) liegt in diesem Repo; konfiguriert wird über eine `.env`:

```bash
git clone https://github.com/fidel-karsto/local-ai-setup.git && cd local-ai-setup
cp .env.example .env      # bei Bedarf anpassen (Image-Versionen, Zeitzone, …)
docker compose up -d
```

Die wichtigsten Punkte der Compose-Datei (gekürzt — die Datei im Repo ist die Referenz):

```yaml
services:
  ollama:
    image: ollama/ollama:0.33.1          # gepinnt statt :latest — siehe "Updates" (§9)
    environment:
      - OLLAMA_KEEP_ALIVE=30m            # Modelle nicht sofort aus dem VRAM entladen
      - OLLAMA_MAX_LOADED_MODELS=1       # bei 4 GB VRAM: nur 1 Modell gleichzeitig
    volumes:
      - ollama-data:/root/.ollama
    ports:
      - "127.0.0.1:11434:11434"          # nur localhost — NGINX (§6) ist der LAN-Eingang
    deploy: …                            # GPU-Reservierung, siehe Datei

  open-webui:
    image: ghcr.io/open-webui/open-webui:v0.11.1
    environment:
      - OLLAMA_BASE_URL=http://ollama:11434
    volumes:
      - open-webui-data:/app/backend/data
    ports:
      - "127.0.0.1:3000:8080"            # nur localhost, s. o.
```

Beide Ports sind bewusst **nur an `127.0.0.1` gebunden**: Aus dem LAN kommt man ausschließlich über den NGINX aus §6 — so gibt es genau einen Eingang, und die unauthentifizierte Ollama-API liegt nicht offen im Netz. (Das heißt auch: `http://<docker-host-ip>:3000` funktioniert von anderen Rechnern aus *nicht* — erst §6 einrichten und `http://chat.heim.lan` benutzen, oder zum Testen per SSH-Tunnel `ssh -L 3000:localhost:3000 <docker-host>`.)

Modelle laden:

```bash
# Ein kleines Chat-Modell, das in 4 GB VRAM passt:
docker exec ollama ollama pull llama3.2:3b

# Das Embedding-Modell für RAG:
docker exec ollama ollama pull bge-m3
```

**Erster Login & Registrierung:** Beim ersten Aufruf einen Admin-Account anlegen. Danach die offene Selbstregistrierung schließen — sonst kann sich jeder im LAN ein Konto anlegen: in der `.env` `ENABLE_SIGNUP=false` setzen und `docker compose up -d` erneut ausführen (oder in den *Admin-Einstellungen* die Standardrolle neuer Nutzer auf „Ausstehend" stellen und Familienmitglieder einzeln freigeben).

**Realistische Erwartungen bei 4 GB VRAM:** `llama3.2:3b` (~2 GB) und `bge-m3` (~1,2 GB) passen jeweils einzeln bequem ins VRAM — bei RAG werden aber beide kurz hintereinander gebraucht (erst Embedding der Frage, dann Antwort des Chat-Modells). Mit `OLLAMA_MAX_LOADED_MODELS=1` wechseln sich die Modelle ab (kurze Ladepausen pro Anfrage); ohne das Limit landet ein Teil der Schichten auf der CPU (funktioniert, ist aber spürbar langsamer). `OLLAMA_KEEP_ALIVE=30m` verhindert zumindest, dass Modelle schon nach 5 Minuten Leerlauf wieder entladen werden. Wer regelmäßig RAG nutzt, profitiert deutlich von mehr VRAM — oder rechnet die Embeddings bewusst auf der CPU.

**Quellen:**
- Ollama Docker-Image: https://hub.docker.com/r/ollama/ollama
- Open WebUI Doku: https://docs.openwebui.com

---

## 5. Ollama „on demand" auf der Windows-Workstation

Die Workstation hat typischerweise die stärkere GPU, läuft aber nicht rund um die Uhr. Deshalb läuft dort ein zweiter Ollama-Server, der nur verfügbar ist, wenn der Rechner an ist.

### Empfohlener Weg: native Windows-App

[Ollama für Windows](https://ollama.com/download/windows) installieren, dann als *Benutzer-Umgebungsvariable* (`Systemsteuerung → Umgebungsvariablen`) `OLLAMA_HOST=0.0.0.0` setzen und Ollama neu starten, damit es aus dem LAN erreichbar ist. Zuletzt den Port in der Windows-Firewall freigeben (PowerShell als Administrator):

```powershell
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
```

Fertig — kein WSL, kein Portproxy, und Ollama startet automatisch mit Windows.

### Alternative für Fortgeschrittene: WSL2

Wer Ollama lieber in WSL2 (Ubuntu) betreibt:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Damit Ollama auch aus dem LAN (und nicht nur von localhost) erreichbar ist, muss es an alle Interfaces binden:

```bash
# In WSL, z. B. in ~/.bashrc oder als systemd-Override:
export OLLAMA_HOST=0.0.0.0:11434
ollama serve
```

Zusätzlich muss Windows den Port 11434 in die WSL-VM weiterleiten (PowerShell als Administrator; `<WSL-IP>` mit `wsl hostname -I` ermitteln):

```powershell
netsh interface portproxy add v4tov4 listenport=11434 listenaddress=0.0.0.0 connectport=11434 connectaddress=<WSL-IP>
New-NetFirewallRule -DisplayName "Ollama WSL" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
```

**Stolperfallen des WSL-Wegs:**
- Die WSL-IP **ändert sich bei jedem Windows-Neustart** — der `portproxy` zeigt danach ins Leere und muss neu gesetzt werden (z. B. per Skript in der Aufgabenplanung). Eleganter: auf Windows 11 22H2+ in der `.wslconfig` das *mirrored networking* aktivieren (`networkingMode=mirrored`), dann teilt sich WSL die Windows-IP und der Portproxy entfällt komplett.
- `ollama serve` im Terminal ist **kein Dienst** — Terminal zu, Ollama weg. Für dauerhaften Betrieb in WSL einen systemd-Service einrichten (systemd in `/etc/wsl.conf` aktivieren; das Install-Skript legt `ollama.service` bereits an, dort `Environment="OLLAMA_HOST=0.0.0.0:11434"` als Override setzen).

---

## 6. NGINX Reverse Proxy: sprechende Namen statt Ports

Der Clou des Setups: Statt IP-Adressen und Ports (`192.168.1.10:3000`, `:11434`) gibt es saubere Namen im LAN. Dafür läuft ein NGINX direkt auf dem Docker-Host — falls noch nicht vorhanden, installieren:

```bash
sudo apt install nginx
```

(Das unten verwendete `sites-available`/`sites-enabled`-Schema ist Debian/Ubuntu-spezifisch. Auf anderen Distributionen die Konfiguration stattdessen nach `/etc/nginx/conf.d/heim-ki.conf` legen.)

Der NGINX proxyt dann:

| Name | Ziel |
|---|---|
| `chat.heim.lan` | Open WebUI (Port 3000) |
| `ollama.heim.lan` | Ollama auf dem Docker-Host (Port 11434) |
| `ollama-ws.heim.lan` | Ollama auf der Workstation (on demand) |

**Schritt 1 — DNS:** Alle drei Namen müssen auf die IP des Docker-Hosts zeigen. (`ollama-ws.heim.lan` zeigt ebenfalls auf den Docker-Host — NGINX leitet dann zur Workstation weiter. So bleibt die Konfiguration an einer Stelle.)

> ⚠️ **Eine Fritz!Box reicht dafür allein nicht aus.** Sie vergibt nur Namen nach dem Schema `<gerätename>.fritz.box` und unterstützt weder eigene Domains wie `heim.lan` noch mehrere Namen (CNAMEs) für dieselbe IP. Realistische Optionen:
>
> 1. **Pi-hole oder AdGuard Home** im LAN betreiben (z. B. als weiterer Container auf dem Docker-Host) und dort unter *Local DNS Records* die drei Namen auf die IP des Docker-Hosts eintragen; anschließend Pi-hole/AdGuard als DNS-Server im Router hinterlegen. Sauberste Lösung — und blockt nebenbei Werbung.
> 2. **`hosts`-Datei auf jedem Client** (Windows: `C:\Windows\System32\drivers\etc\hosts`, Linux/macOS: `/etc/hosts`): drei Zeilen mit `<docker-host-ip> chat.heim.lan ollama.heim.lan ollama-ws.heim.lan`. Funktioniert sofort, skaliert aber schlecht — auf Smartphones praktisch nicht machbar.
> 3. **Kompromiss ohne Zusatzsoftware:** Den Docker-Host in der Fritz!Box z. B. `chat` nennen — dann erreicht der Haushalt Open WebUI unter `http://chat.fritz.box` (dafür in der NGINX-Konfiguration `server_name chat.fritz.box;` ergänzen bzw. als `default_server` arbeiten). Die beiden Ollama-API-Namen entfallen dabei; wer die APIs direkt braucht, nutzt dann `<docker-host-ip>:Port`.

**Schritt 2 — NGINX-Konfiguration:** Die fertige Konfiguration liegt in diesem Repo unter [`nginx/heim-ki.conf`](nginx/heim-ki.conf) — vor dem Kopieren `<WORKSTATION-IP>` durch die IP der Workstation ersetzen. Die wichtigsten Blöcke (gekürzt):

```nginx
# Open WebUI
server {
    listen 80;
    server_name chat.heim.lan;

    # Dokumenten-Upload (RAG): NGINX-Standardlimit von 1 MB reicht für PDFs nicht
    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # WebSockets für den Chat-Stream:
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }
}

# Ollama API (Docker-Host) — Workstation-Block analog, siehe Datei
server {
    listen 80;
    server_name ollama.heim.lan;

    location / {
        proxy_pass http://127.0.0.1:11434;
        # Ollama lehnt fremde Host-Header je nach Version als Schutz vor
        # DNS-Rebinding ab (403) — deshalb localhost senden, nicht $host:
        proxy_set_header Host 127.0.0.1:11434;
        proxy_read_timeout 600s;   # große Modelle brauchen Zeit
        proxy_buffering off;       # Token-Streaming
    }
}
```

Installieren und aktivieren:

```bash
sudo cp nginx/heim-ki.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/heim-ki.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**Falls Ollama über den Proxy `403 Forbidden` liefert:** Das ist Ollamas Schutz gegen fremde Host-/Origin-Header. Die Konfiguration oben umgeht das bereits (`proxy_set_header Host 127.0.0.1:11434;`); alternativ kann man Ollama mit `OLLAMA_ORIGINS=*` (bzw. einer konkreten Liste) starten.

Jetzt sind beide Ollama-APIs unter ordentlichen Base-URLs im LAN erreichbar — genau wie im Post beschrieben. In Open WebUI kann man unter *Admin-Einstellungen → Verbindungen* beide URLs (`http://ollama.heim.lan` und `http://ollama-ws.heim.lan`) als Ollama-Endpunkte eintragen. Ist die Workstation aus, nutzt man einfach die Modelle des Docker-Hosts.

**Tipp:** Wer lieber klickt statt Configs schreibt, nimmt den [Nginx Proxy Manager](https://nginxproxymanager.com) als Container.

**Quelle:** NGINX Reverse-Proxy-Doku: https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/

---

## 7. RAG einrichten: Docling + ChromaDB + bge-m3

Jetzt der Teil, der „nachts alles durchknödelt": Dokumente werden mit Docling in sauberen Text konvertiert, mit bge-m3 in Vektoren verwandelt und in ChromaDB abgelegt.

### Variante A (empfohlen): Open WebUI erledigt das RAG

Open WebUI bringt die komplette RAG-Pipeline bereits mit — ChromaDB ist die eingebaute Standard-Vektordatenbank, und Docling wird als Extraktions-Engine offiziell unterstützt.

**1. Docling-Container starten:** Der Docling-Server ist in der [`docker-compose.yml`](docker-compose.yml) dieses Repos bereits enthalten — als Compose-*Profil* `rag`, damit das Basis-Setup schlank bleibt:

```bash
docker compose --profile rag up -d
```

Ein Port-Mapping braucht Docling nicht: Open WebUI erreicht den Container über das Compose-Netz direkt unter `http://docling:5001`. (Standardmäßig läuft das CPU-Image; wer die GPU für schnellere OCR mitnutzen will, trägt in der `.env` die CUDA-Variante ein — siehe [`.env.example`](.env.example). Achtung: Docling ist bei OCR-lastigen PDFs RAM-hungrig.)

**2. Open WebUI konfigurieren** unter *Admin-Einstellungen → Dokumente*:
- **Inhaltsextraktion / Content Extraction Engine:** `Docling` mit URL `http://docling:5001`
- **Embedding-Modell:** Engine `Ollama`, Modell `bge-m3`, URL `http://ollama:11434`

**3. Dokumente reinschmeißen:** In Open WebUI unter *Arbeitsbereich → Wissen* eine Sammlung anlegen und „alle Docs, die hier so rumfliegen" hochladen. Im Chat bindet man die Sammlung mit `#Sammlungsname` ein — fertig ist die Dokumenten-KI.

**Quellen:**
- Open WebUI RAG-Doku: https://docs.openwebui.com/features/rag
- Docling Serve: https://github.com/docling-project/docling-serve

### Variante B: Eigene Pipeline als nächtlicher Batch-Job (mit Suche als Open-WebUI-Tool)

Wer es wie im Post als eigenständigen Nachtjob bauen will (z. B. um einen ganzen Ordner automatisch zu indexieren), bekommt hier die komplette Kette — inklusive des Teils, der im Post fehlte: der **Anbindung an die Chats**. Die Architektur:

```
/srv/dokumente ──► rag-indexer.py ──► ChromaDB-Server ◄── Open-WebUI-Tool
                   (Host, nachts       (Container,         "Heim-Dokumente
                    per systemd-        Profil "rag")       durchsuchen"
                    Timer)                                  (im Chat)
```

**1. ChromaDB-Server starten:** Der Container ist in der [`docker-compose.yml`](docker-compose.yml) enthalten — im eigenen Profil `rag-batch`, denn Variante B braucht Chroma, aber nicht den Docling-Server aus Variante A:

```bash
docker compose --profile rag-batch up -d
```

Der Indexer auf dem Host erreicht ihn unter `127.0.0.1:8000`, das Open-WebUI-Tool über das Compose-Netz unter `http://chroma:8000`.

**2. Indexer einrichten** ([`scripts/rag-indexer.py`](scripts/rag-indexer.py)) — mit eigenem venv, damit der Zeitplan-Aufruf dieselbe Umgebung nutzt wie die Installation:

```bash
sudo mkdir -p /srv/scripts /srv/dokumente
sudo cp scripts/rag-indexer.py scripts/requirements.txt /srv/scripts/
python3 -m venv /srv/scripts/.venv
/srv/scripts/.venv/bin/pip install -r /srv/scripts/requirements.txt

# Testlauf (Dokumente vorher nach /srv/dokumente legen):
/srv/scripts/.venv/bin/python /srv/scripts/rag-indexer.py
```

Das Skript ist auf Dauerbetrieb ausgelegt: Es überspringt unveränderte Dateien (SHA-256-Manifest in `/srv/rag-index-state.json`), entfernt die Chunks gelöschter oder geänderter Dateien, bettet Chunks *mit* Überschriften-Kontext ein (`chunker.contextualize`) und bricht bei einer kaputten Datei nicht den ganzen Lauf ab. Pfade und URLs sind per Umgebungsvariablen konfigurierbar (siehe Skript-Kopf).

**3. Nächtlich laufen lassen** — als systemd-Timer ([`scripts/systemd/`](scripts/systemd/)); der holt dank `Persistent=true` auch verpasste Läufe nach, und die Logs landen im journald:

```bash
sudo cp scripts/systemd/rag-indexer.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rag-indexer.timer

# Logs ansehen:
journalctl -u rag-indexer.service
```

(Wer lieber Cron mag: `0 2 * * * /srv/scripts/.venv/bin/python /srv/scripts/rag-indexer.py >> /var/log/rag-indexer.log 2>&1`)

**4. Suche in Open WebUI anbinden:** Den Inhalt von [`tools/heim_docs_suche.py`](tools/heim_docs_suche.py) in Open WebUI unter *Arbeitsbereich → Werkzeuge → +* als neues Werkzeug einfügen und speichern. Anschließend das Werkzeug beim gewünschten Modell aktivieren (*Admin-Einstellungen → Modelle → Modell bearbeiten → Werkzeuge*) oder im Chat über das ⊕-Menü zuschalten. URLs, Collection und Trefferanzahl lassen sich über die *Ventile* (Valves) des Werkzeugs anpassen.

**5. Benutzen:** Im Chat einfach nach Inhalten der eigenen Dokumente fragen („Was steht in meinem Mietvertrag zur Kündigungsfrist?") — das Modell ruft das Werkzeug auf, das die passenden Textstellen samt Quellenangabe aus dem Nachtindex holt.

> **Hinweis:** Variante B nutzt bewusst *nicht* Docling-Serve aus Variante A, sondern die Docling-Python-Bibliothek direkt im Indexer — deshalb das getrennte Compose-Profil: `rag` startet Docling (Variante A), `rag-batch` startet Chroma (Variante B). Beide Varianten lassen sich auch parallel betreiben.

**Quellen:**
- Docling Doku: https://docling-project.github.io/docling/
- ChromaDB Doku: https://docs.trychroma.com
- Ollama Embeddings: https://docs.ollama.com/api

---

## 8. Ergebnis

- Alle im Haushalt erreichen unter **`http://chat.heim.lan`** eine ChatGPT-ähnliche Oberfläche — ohne Ports, ohne IP-Adressen.
- Die KI kennt die eigenen Dokumente (RAG mit Docling + ChromaDB + bge-m3).
- Braucht man mehr Leistung, startet man die Workstation — deren Ollama ist sofort unter `http://ollama-ws.heim.lan` verfügbar.
- **Keine Daten fließen „nach Amiland"** — alles bleibt im eigenen LAN.

## 9. Updates

Die Image-Versionen sind in der [`.env`](.env.example) **gepinnt** — bewusst kein `:latest`/`:main`, damit das Setup reproduzierbar bleibt und Updates ein bewusster Schritt sind (Open WebUI released sehr häufig, teils mit Verhaltensänderungen). Aktualisieren:

```bash
# 1. Release Notes prüfen:
#    https://github.com/open-webui/open-webui/releases
#    https://github.com/ollama/ollama/releases
#    https://github.com/docling-project/docling-serve/releases
# 2. Versionen in der .env hochziehen, dann:
docker compose pull && docker compose up -d
```

**Vor größeren Versionssprüngen** das Open-WebUI-Volume sichern — dort liegen Nutzer, Chats und Wissenssammlungen:

```bash
docker run --rm -v open-webui-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/open-webui-data-$(date +%F).tar.gz -C /data .
```

(Das `ollama-data`-Volume enthält nur die Modelle — die sind jederzeit per `ollama pull` wiederherstellbar und müssen nicht gesichert werden.)

## 10. Weiterführende Ideen

- **HTTPS im LAN:** Mit [Caddy](https://caddyserver.com) oder eigener CA (z. B. [mkcert](https://github.com/FiloSottile/mkcert)) Zertifikate für die `.lan`-Domains ausstellen. Das ist mehr als Kosmetik: Ohne HTTPS (Secure Context) blockieren Browser den Mikrofon-Zugriff — die Sprach-Ein-/Ausgabe von Open WebUI funktioniert über `http://` von anderen Geräten aus nicht.
- **Wake-on-LAN** für die Workstation, um sie bei Bedarf aus der Ferne zu starten.
- **Modell-Empfehlungen für 4 GB VRAM:** `llama3.2:3b`, `qwen2.5:3b`, `phi3:mini` — alle in der [Ollama Library](https://ollama.com/library).

## Quellenübersicht

| Projekt | Link |
|---|---|
| Ollama | https://ollama.com · https://github.com/ollama/ollama |
| Open WebUI | https://github.com/open-webui/open-webui · https://docs.openwebui.com |
| bge-m3 (BAAI) | https://ollama.com/library/bge-m3 · https://huggingface.co/BAAI/bge-m3 |
| Docling (IBM Research) | https://github.com/docling-project/docling |
| Docling Serve | https://github.com/docling-project/docling-serve |
| ChromaDB | https://www.trychroma.com |
| NGINX Reverse Proxy | https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/ |
| NVIDIA Container Toolkit | https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html |
| WSL2 | https://learn.microsoft.com/de-de/windows/wsl/install |
