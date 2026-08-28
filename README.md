# Lokale Heim-KI mit Open WebUI, Ollama & RAG — komplett ohne Cloud

**Tutorial: Ein privater KI-Assistent im eigenen LAN, der auch eigene Dokumente durchsuchen kann**

> Basierend auf einem Setup von Matthias Kallenbach (LinkedIn-Post). Ziel: Eine ChatGPT-ähnliche Oberfläche für alle im Haushalt („Mama kann auch die Heim-KI benutzen"), bei der **keine Daten das eigene Netzwerk verlassen**.

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
                        │  Workstation (Windows + WSL2)       │
                        │  Ollama "on demand" — nur an,       │
                        │  wenn der Rechner läuft; für        │
                        │  größere Modelle mit stärkerer GPU  │
                        └─────────────────────────────────────┘
```

**Die Komponenten im Einzelnen:**

| Komponente | Rolle |
|---|---|
| **[Ollama](https://ollama.com)** | Lokaler LLM-Server. Läuft als Docker-Container auf dem Docker-Host (mit 4-GB-CUDA-GPU) **und** zusätzlich „on demand" auf der Windows-Workstation (WSL2). |
| **[Open WebUI](https://github.com/open-webui/open-webui)** | ChatGPT-ähnliche Weboberfläche für Ollama — mit Nutzerverwaltung, Dokumenten-Upload und eingebautem RAG. |
| **[bge-m3](https://ollama.com/library/bge-m3)** | Mehrsprachiges Embedding-Modell (BAAI). Wandelt Texte in Vektoren um — die Grundlage für die Dokumentensuche (RAG). Sehr gut für Deutsch geeignet. |
| **[Docling](https://github.com/docling-project/docling)** | Open-Source-Tool von IBM Research: konvertiert PDF, DOCX, PPTX, HTML usw. in sauberes, strukturiertes Markdown/JSON — inkl. Tabellen und Layout-Erkennung. (Im Original-Post „Dockling" geschrieben — gemeint ist Docling.) |
| **[ChromaDB](https://www.trychroma.com)** | Vektordatenbank. Speichert die von bge-m3 erzeugten Embeddings und liefert bei einer Frage die passenden Dokument-Schnipsel zurück. |
| **[NGINX](https://nginx.org)** | Reverse Proxy auf dem Docker-Host. Macht aus `http://192.168.x.y:11434` schöne, sprechende Namen wie `http://ollama.heim.lan` — „ohne komische Ports". |

**RAG** (Retrieval-Augmented Generation) bedeutet: Bevor das Sprachmodell antwortet, sucht das System in den eigenen Dokumenten nach relevanten Passagen und gibt sie dem Modell als Kontext mit. So kann die Heim-KI Fragen zu den eigenen PDFs, Verträgen, Anleitungen etc. beantworten.

---

## 2. Voraussetzungen

**Hardware:**
- Ein Linux-Rechner als Docker-Host, der dauerhaft läuft (Mini-PC, alter Desktop, Homeserver), mit einer NVIDIA-GPU mit mindestens 4 GB VRAM. Das reicht für kleine Modelle (z. B. 3B–7B quantisiert) und Embeddings.
- Optional: Eine Windows-Workstation mit stärkerer GPU für größere Modelle „on demand".

**Software:**
- Docker + Docker Compose auf dem Host → [Installationsanleitung](https://docs.docker.com/engine/install/)
- NVIDIA-Treiber + **NVIDIA Container Toolkit** (damit Container die GPU nutzen können) → [Installationsanleitung](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- Auf der Workstation: WSL2 → [Microsoft-Doku](https://learn.microsoft.com/de-de/windows/wsl/install)

**Netzwerk:**
- Möglichkeit, lokale DNS-Namen zu vergeben (z. B. im Router — Fritz!Box kann das —, per Pi-hole, oder notfalls per `hosts`-Datei auf den Clients).

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

Eine `docker-compose.yml` anlegen:

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    restart: unless-stopped
    volumes:
      - ollama-data:/root/.ollama
    ports:
      - "11434:11434"      # nur intern nötig, NGINX proxyt später
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]

  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: open-webui
    restart: unless-stopped
    depends_on:
      - ollama
    environment:
      - OLLAMA_BASE_URL=http://ollama:11434
    volumes:
      - open-webui-data:/app/backend/data
    ports:
      - "3000:8080"

volumes:
  ollama-data:
  open-webui-data:
```

Starten und Modelle laden:

```bash
docker compose up -d

# Ein kleines Chat-Modell, das in 4 GB VRAM passt:
docker exec ollama ollama pull llama3.2:3b

# Das Embedding-Modell für RAG:
docker exec ollama ollama pull bge-m3
```

Open WebUI ist jetzt unter `http://<docker-host-ip>:3000` erreichbar. Beim ersten Aufruf einen Admin-Account anlegen.

**Quellen:**
- Ollama Docker-Image: https://hub.docker.com/r/ollama/ollama
- Open WebUI Doku: https://docs.openwebui.com

---

## 5. Ollama „on demand" auf der Windows-Workstation (WSL2)

Die Workstation hat typischerweise die stärkere GPU, läuft aber nicht rund um die Uhr. Deshalb läuft dort ein zweiter Ollama-Server, der nur verfügbar ist, wenn der Rechner an ist.

In WSL2 (Ubuntu):

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

Alternativ (einfacher): [Ollama für Windows](https://ollama.com/download/windows) nativ installieren und die Umgebungsvariable `OLLAMA_HOST=0.0.0.0` setzen — dann entfällt das Portproxy-Gefrickel.

---

## 6. NGINX Reverse Proxy: sprechende Namen statt Ports

Der Clou des Setups: Statt IP-Adressen und Ports (`192.168.1.10:3000`, `:11434`) gibt es saubere Namen im LAN. Der NGINX läuft ohnehin schon auf dem Docker-Host und proxyt:

| Name | Ziel |
|---|---|
| `chat.heim.lan` | Open WebUI (Port 3000) |
| `ollama.heim.lan` | Ollama auf dem Docker-Host (Port 11434) |
| `ollama-ws.heim.lan` | Ollama auf der Workstation (on demand) |

**Schritt 1 — DNS:** Im Router (z. B. Fritz!Box unter *Heimnetz → Netzwerk*) oder in Pi-hole die drei Namen auf die IP des Docker-Hosts zeigen lassen. (`ollama-ws.heim.lan` zeigt ebenfalls auf den Docker-Host — NGINX leitet dann zur Workstation weiter. So bleibt die Konfiguration an einer Stelle.)

**Schritt 2 — NGINX-Konfiguration** (`/etc/nginx/sites-available/heim-ki.conf`):

```nginx
# Open WebUI
server {
    listen 80;
    server_name chat.heim.lan;

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

# Ollama API (Docker-Host)
server {
    listen 80;
    server_name ollama.heim.lan;

    location / {
        proxy_pass http://127.0.0.1:11434;
        proxy_set_header Host $host;
        proxy_read_timeout 600s;   # große Modelle brauchen Zeit
        proxy_buffering off;        # Token-Streaming
    }
}

# Ollama API (Workstation, on demand)
server {
    listen 80;
    server_name ollama-ws.heim.lan;

    location / {
        proxy_pass http://<WORKSTATION-IP>:11434;
        proxy_set_header Host $host;
        proxy_read_timeout 600s;
        proxy_buffering off;
    }
}
```

Aktivieren:

```bash
sudo ln -s /etc/nginx/sites-available/heim-ki.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Jetzt sind beide Ollama-APIs unter ordentlichen Base-URLs im LAN erreichbar — genau wie im Post beschrieben. In Open WebUI kann man unter *Admin-Einstellungen → Verbindungen* beide URLs (`http://ollama.heim.lan` und `http://ollama-ws.heim.lan`) als Ollama-Endpunkte eintragen. Ist die Workstation aus, nutzt man einfach die Modelle des Docker-Hosts.

**Tipp:** Wer lieber klickt statt Configs schreibt, nimmt den [Nginx Proxy Manager](https://nginxproxymanager.com) als Container.

**Quelle:** NGINX Reverse-Proxy-Doku: https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/

---

## 7. RAG einrichten: Docling + ChromaDB + bge-m3

Jetzt der Teil, der „nachts alles durchknödelt": Dokumente werden mit Docling in sauberen Text konvertiert, mit bge-m3 in Vektoren verwandelt und in ChromaDB abgelegt.

### Variante A (empfohlen): Open WebUI erledigt das RAG

Open WebUI bringt die komplette RAG-Pipeline bereits mit — ChromaDB ist die eingebaute Standard-Vektordatenbank, und Docling wird als Extraktions-Engine offiziell unterstützt.

**1. Docling als Server-Container ergänzen** (in der `docker-compose.yml`):

```yaml
  docling:
    image: ghcr.io/docling-project/docling-serve:latest
    container_name: docling
    restart: unless-stopped
    ports:
      - "5001:5001"
```

**2. Open WebUI konfigurieren** unter *Admin-Einstellungen → Dokumente*:
- **Inhaltsextraktion / Content Extraction Engine:** `Docling` mit URL `http://docling:5001`
- **Embedding-Modell:** Engine `Ollama`, Modell `bge-m3`, URL `http://ollama:11434`

**3. Dokumente reinschmeißen:** In Open WebUI unter *Arbeitsbereich → Wissen* eine Sammlung anlegen und „alle Docs, die hier so rumfliegen" hochladen. Im Chat bindet man die Sammlung mit `#Sammlungsname` ein — fertig ist die Dokumenten-KI.

**Quellen:**
- Open WebUI RAG-Doku: https://docs.openwebui.com/features/rag
- Docling Serve: https://github.com/docling-project/docling-serve

### Variante B: Eigene Pipeline als nächtlicher Batch-Job

Wer es wie im Post als eigenständigen Nachtjob bauen will (z. B. um einen ganzen Ordner automatisch zu indexieren), schreibt ein kleines Python-Skript:

```bash
pip install docling chromadb ollama
```

```python
#!/usr/bin/env python3
"""Nächtlicher RAG-Indexer: Docling -> bge-m3 (Ollama) -> ChromaDB"""
from pathlib import Path
import chromadb
import ollama
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker

DOCS_DIR = Path("/srv/dokumente")           # hier fliegen die Docs rum
client = chromadb.PersistentClient(path="/srv/chroma")
collection = client.get_or_create_collection("heim-docs")
converter = DocumentConverter()
chunker = HybridChunker()

for f in DOCS_DIR.rglob("*"):
    if f.suffix.lower() not in {".pdf", ".docx", ".pptx", ".html", ".md"}:
        continue
    doc = converter.convert(f).document          # Docling: Datei -> Struktur
    for i, chunk in enumerate(chunker.chunk(doc)):
        text = chunk.text
        emb = ollama.embed(model="bge-m3", input=text)["embeddings"][0]
        collection.upsert(
            ids=[f"{f.name}-{i}"],
            embeddings=[emb],
            documents=[text],
            metadatas=[{"quelle": str(f)}],
        )
    print(f"Indexiert: {f.name}")
```

Als Cronjob nachts um 2 Uhr laufen lassen:

```bash
crontab -e
# 0 2 * * * /usr/bin/python3 /srv/scripts/rag-indexer.py >> /var/log/rag-indexer.log 2>&1
```

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

## 9. Weiterführende Ideen

- **HTTPS im LAN:** Mit [Caddy](https://caddyserver.com) oder eigener CA (z. B. [mkcert](https://github.com/FiloSottile/mkcert)) Zertifikate für die `.lan`-Domains ausstellen.
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
