# Lokale Heim-KI mit Open WebUI, Ollama & RAG — komplett ohne Cloud

**Tutorial: Ein privater KI-Assistent im eigenen LAN, der auch eigene Dokumente durchsuchen kann**

> Basierend auf einem Setup von Matthias Kallenbach (LinkedIn-Post). Ziel: Eine ChatGPT-ähnliche Oberfläche für alle im Haushalt („Mama kann auch die Heim-KI benutzen"), bei der **keine Daten das eigene Netzwerk verlassen**.

**Dieses Repo enthält neben dem Tutorial die fertigen Konfigurationsdateien:**
[`docker-compose.yml`](docker-compose.yml) (Linux) · [`docker-compose.macos.yml`](docker-compose.macos.yml) (macOS) · [`.env.example`](.env.example) · [`nginx/`](nginx) (HTTP- und HTTPS-Konfiguration) · [`scripts/`](scripts) (RAG-Indexer, Backup, Wake-on-LAN, systemd- und launchd-Units) · [`tools/heim_docs_suche.py`](tools/heim_docs_suche.py) (Open-WebUI-Werkzeug)

Der Docker-Host kann ein **Linux-Rechner mit NVIDIA-GPU** oder ein **Apple-Silicon-Mac** sein — wo sich die Wege unterscheiden, steht es im jeweiligen Abschnitt dabei.

---

## 1. Was wird gebaut? (Architektur-Überblick)

Das Setup besteht aus zwei Maschinen und mehreren Diensten:

```
                        ┌─────────────────────────────────────┐
                        │  Docker-Host (Linux o. macOS, 24/7) │
                        │  GPU: NVIDIA (CUDA) o. Apple Silicon│
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
                        │  Workstation (Windows oder macOS)   │
                        │  Ollama "on demand" — nur an,       │
                        │  wenn der Rechner läuft; für        │
                        │  größere Modelle mit stärkerer GPU  │
                        └─────────────────────────────────────┘
```

**Die Komponenten im Einzelnen:**

| Komponente | Rolle |
|---|---|
| **[Ollama](https://ollama.com)** | Lokaler LLM-Server. Läuft auf dem Docker-Host (Linux: als Container mit CUDA-GPU; macOS: nativ mit Metal, siehe §3) **und** zusätzlich „on demand" auf der Workstation (Windows oder macOS). |
| **[Open WebUI](https://github.com/open-webui/open-webui)** | ChatGPT-ähnliche Weboberfläche für Ollama — mit Nutzerverwaltung, Dokumenten-Upload und eingebautem RAG. |
| **[bge-m3](https://ollama.com/library/bge-m3)** | Mehrsprachiges Embedding-Modell (BAAI). Wandelt Texte in Vektoren um — die Grundlage für die Dokumentensuche (RAG). Sehr gut für Deutsch geeignet. |
| **[Docling](https://github.com/docling-project/docling)** | Open-Source-Tool von IBM Research: konvertiert PDF, DOCX, PPTX, HTML usw. in sauberes, strukturiertes Markdown/JSON — inkl. Tabellen und Layout-Erkennung. (Im Original-Post „Dockling" geschrieben — gemeint ist Docling.) |
| **[ChromaDB](https://www.trychroma.com)** | Vektordatenbank. Speichert die von bge-m3 erzeugten Embeddings und liefert bei einer Frage die passenden Dokument-Schnipsel zurück. |
| **[NGINX](https://nginx.org)** | Reverse Proxy auf dem Docker-Host. Macht aus `http://192.168.x.y:11434` schöne, sprechende Namen wie `http://ollama.heim.lan` — „ohne komische Ports". |

**RAG** (Retrieval-Augmented Generation) bedeutet: Bevor das Sprachmodell antwortet, sucht das System in den eigenen Dokumenten nach relevanten Passagen und gibt sie dem Modell als Kontext mit. So kann die Heim-KI Fragen zu den eigenen PDFs, Verträgen, Anleitungen etc. beantworten.

---

## 2. Voraussetzungen

**Hardware** (eine der beiden Host-Varianten):
- **Linux-Host:** Ein Rechner, der dauerhaft läuft (Mini-PC, alter Desktop, Homeserver), mit einer NVIDIA-GPU mit mindestens 4 GB VRAM. Das reicht für kleine Modelle (z. B. 3B–7B quantisiert) und Embeddings. **Aber:** 4 GB sind knapp, wenn Chat- und Embedding-Modell gleichzeitig gebraucht werden — und genau das passiert bei RAG. Siehe „Realistische Erwartungen bei 4 GB VRAM" in §4.
- **macOS-Host:** Ein Apple-Silicon-Mac (z. B. Mac mini) mit mindestens 16 GB RAM. Dank Unified Memory steht dem Modell ein großer Teil des RAM als „VRAM" zur Verfügung — ein 16-GB-Mac fährt bei RAG oft entspannter als eine 4-GB-NVIDIA-Karte. **Wichtig:** Docker-Container haben unter macOS keinen GPU-Zugriff, deshalb läuft Ollama auf dem Mac *nativ* (§3/§4).
- Optional: Eine Workstation (Windows oder Mac) mit mehr GPU-Leistung für größere Modelle „on demand".

**Software:**
- Docker + Docker Compose auf dem Host → Linux: [Docker Engine](https://docs.docker.com/engine/install/); macOS: [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/) oder [OrbStack](https://orbstack.dev)
- Nur Linux-Host: NVIDIA-Treiber + **NVIDIA Container Toolkit** (damit Container die GPU nutzen können) → [Installationsanleitung](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- Nur macOS-Host: [Homebrew](https://brew.sh) für Ollama, NGINX & Co.
- Auf der Workstation: nichts Besonderes — Ollama gibt es nativ für Windows und macOS. (WSL2 nur für den Fortgeschrittenen-Weg in §5 → [Microsoft-Doku](https://learn.microsoft.com/de-de/windows/wsl/install))

**Netzwerk:**
- Möglichkeit, lokale DNS-Namen zu vergeben — realistisch per **Pi-hole/AdGuard Home** oder notfalls per `hosts`-Datei auf den Clients. Eine Fritz!Box allein kann das *nicht* frei konfigurierbar (Details und Optionen in §6, Schritt 1).

---

## 3. Docker-Host vorbereiten (GPU-Zugriff)

### Linux (NVIDIA)

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

### macOS (Apple Silicon)

Auf dem Mac entfällt der ganze Toolkit-Teil — dafür gilt eine andere Grundregel: **Docker-Container haben unter macOS keinen GPU-Zugriff** (Docker läuft dort in einer VM ohne Metal-Durchreichung). Ollama in einem Container wäre auf einem Mac also reine CPU-Quälerei. Deshalb wird auf dem macOS-Host nur **Ollama nativ** installiert (nutzt die GPU über Metal automatisch, ganz ohne Konfiguration), und der Rest läuft in Containern:

```bash
brew install ollama
brew services start ollama    # startet Ollama als Dienst bei jedem Login
```

Docker Desktop (oder OrbStack) installieren und starten — mehr Vorbereitung braucht es nicht. Der Test hier ist schlicht:

```bash
ollama run llama3.2:3b "Sag Hallo"    # antwortet flott? Metal läuft.
```

> ⚠️ **Unbeaufsichtigter Betrieb (24/7-Host, z. B. headless Mac mini):** Anders als systemd-Dienste unter Linux hängen auf dem Mac *alle* Bausteine an einer angemeldeten Nutzer-Session: `brew services start ollama` (ohne sudo) legt einen LaunchAgent an, der erst beim **Login** startet — nicht beim Boot —, und Docker Desktop/OrbStack laufen ebenfalls nur innerhalb einer Session. Nach einem Neustart (Stromausfall, Update) ohne Anmeldung ist die Heim-KI sonst tot. Deshalb einmalig einrichten:
>
> 1. **Automatische Anmeldung** aktivieren: *Systemeinstellungen → Benutzer & Gruppen → Automatisch anmelden* (geht nicht bei aktiviertem FileVault).
> 2. **Docker Desktop/OrbStack beim Login starten** lassen (Einstellung „Start at login").
> 3. **Ruhezustand deaktivieren** — ein schlafender Mac beantwortet keine Anfragen und führt keine Nachtjobs aus: `sudo pmset -a sleep 0 disksleep 0`. Praktisch außerdem: `sudo pmset -a autorestart 1` (automatischer Neustart nach Stromausfall).
>
> Auch die Nachtjobs aus §7/§11 laufen aus genau diesem Grund als LaunchAgents in der Nutzer-Session, nicht als root-Daemons.

---

## 4. Ollama + Open WebUI als Container

### Linux-Host

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

### macOS-Host

Auf dem Mac läuft Ollama bereits nativ (§3) — in Container kommt nur der Rest. Dafür gibt es die eigene Compose-Datei [`docker-compose.macos.yml`](docker-compose.macos.yml): sie enthält keinen Ollama-Service und zeigt stattdessen mit `OLLAMA_BASE_URL=http://host.docker.internal:11434` auf das native Ollama des Mac:

```bash
git clone https://github.com/fidel-karsto/local-ai-setup.git && cd local-ai-setup
cp .env.example .env
docker compose -f docker-compose.macos.yml up -d

# Modelle direkt nativ laden (kein "docker exec" nötig):
ollama pull llama3.2:3b
ollama pull bge-m3
```

Die Hinweise oben zu **Erster Login & Registrierung** gelten unverändert. Statt der VRAM-Klimmzüge gilt auf dem Mac die Unified-Memory-Faustregel: macOS gönnt den Modellen grob bis zu ~⅔ des RAM — auf einem 16-GB-Mac laufen `llama3.2:3b` und `bge-m3` bequem nebeneinander, sogar 7B–8B-Modelle sind drin. Wer `OLLAMA_KEEP_ALIVE` o. ä. setzen will: `launchctl setenv OLLAMA_KEEP_ALIVE 30m` (danach `brew services restart ollama`) wirkt sofort, ist aber nach einem Neustart stillschweigend wieder weg — dauerhaft verdrahtet man solche Variablen in einem eigenen LaunchAgent statt über `brew services` (Vorlage: [`scripts/launchd/de.heim-ki.ollama-ws.plist`](scripts/launchd/de.heim-ki.ollama-ws.plist), dort `OLLAMA_HOST` weglassen bzw. ersetzen). Und weil Ollama nativ standardmäßig nur an `127.0.0.1` lauscht, gilt dieselbe Sicherheitslogik wie unter Linux: Der NGINX aus §6 ist der einzige Eingang aus dem LAN.

**Quellen:**
- Ollama Docker-Image: https://hub.docker.com/r/ollama/ollama
- Open WebUI Doku: https://docs.openwebui.com

---

## 5. Ollama „on demand" auf der Workstation (Windows oder macOS)

Die Workstation hat typischerweise mehr GPU-Leistung, läuft aber nicht rund um die Uhr. Deshalb läuft dort ein zweiter Ollama-Server, der nur verfügbar ist, wenn der Rechner an ist.

### Windows: native App (empfohlen)

[Ollama für Windows](https://ollama.com/download/windows) installieren, dann als *Benutzer-Umgebungsvariable* (`Systemsteuerung → Umgebungsvariablen`) `OLLAMA_HOST=0.0.0.0` setzen und Ollama neu starten, damit es aus dem LAN erreichbar ist. Zuletzt den Port in der Windows-Firewall freigeben (PowerShell als Administrator):

```powershell
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
```

Fertig — kein WSL, kein Portproxy, und Ollama startet automatisch mit Windows.

### macOS: Mac als Workstation

Ein Mac (z. B. ein MacBook Pro mit viel RAM) funktioniert genauso gut als On-demand-Workstation. Damit Ollama aus dem LAN erreichbar ist (nicht nur von localhost), muss `OLLAMA_HOST=0.0.0.0:11434` gesetzt sein — und zwar so, dass es Neustarts überlebt. `launchctl setenv` plus `brew services` ist dafür *nicht* verlässlich: brew services generiert seine Job-Definition bei jedem Restart/Upgrade neu, und per `launchctl` gesetzte Variablen sind nach einem Reboot weg — Ollama lauscht dann wieder still nur auf `127.0.0.1`, ohne dass auf der Workstation irgendetwas fehlschlägt. Deshalb liegt in diesem Repo ein fertiger LaunchAgent mit fest verdrahtetem `OLLAMA_HOST` ([`scripts/launchd/de.heim-ki.ollama-ws.plist`](scripts/launchd/de.heim-ki.ollama-ws.plist)):

```bash
brew install ollama    # nur die Binärdatei — KEIN "brew services start ollama" dazu,
                       # sonst streiten sich zwei Instanzen um Port 11434

mkdir -p ~/Library/LaunchAgents
cp scripts/launchd/de.heim-ki.ollama-ws.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/de.heim-ki.ollama-ws.plist
```

Damit startet Ollama bei jedem Login LAN-erreichbar. Fragt die macOS-Firewall beim ersten eingehenden Zugriff nach, „eingehende Verbindungen erlauben" bestätigen. (Wer stattdessen die [Ollama-App für macOS](https://ollama.com/download/mac) nutzt: Die liest `launchctl setenv OLLAMA_HOST "0.0.0.0:11434"` aus — das ist der von Ollama dokumentierte Weg für die App, muss aber nach jedem Neustart erneut gesetzt werden, bevor die App startet.)

**Achtung Ruhezustand:** Ein zugeklapptes MacBook schläft — und ein schlafender Mac beantwortet keine Ollama-Anfragen. Für den Workstation-Einsatz den Ruhezustand am Netzteil deaktivieren (*Systemeinstellungen → Energie*, „Ruhezustand des Computers verhindern") oder `caffeinate` nutzen.

### Windows-Alternative für Fortgeschrittene: WSL2

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
# Linux (Debian/Ubuntu):
sudo apt install nginx

# macOS:
brew install nginx
```

(Das unten verwendete `sites-available`/`sites-enabled`-Schema ist Debian/Ubuntu-spezifisch. Auf anderen Linux-Distributionen die Konfiguration stattdessen nach `/etc/nginx/conf.d/heim-ki.conf` legen. **macOS/Homebrew:** Die Konfiguration nach `/opt/homebrew/etc/nginx/servers/heim-ki.conf` kopieren und NGINX als Root starten, weil Port 80/443 privilegiert sind: `sudo brew services start nginx`. Test/Reload: `sudo nginx -t && sudo nginx -s reload`.)

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
# Linux (Debian/Ubuntu):
sudo cp nginx/heim-ki.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/heim-ki.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# macOS (Homebrew):
sudo cp nginx/heim-ki.conf /opt/homebrew/etc/nginx/servers/
sudo brew services restart nginx
```

Die Konfiguration selbst ist auf beiden Systemen identisch — Open WebUI lauscht auf `127.0.0.1:3000` und Ollama auf `127.0.0.1:11434`, egal ob Ollama im Container (Linux) oder nativ (macOS) läuft.

**Falls Ollama über den Proxy `403 Forbidden` liefert:** Das ist Ollamas Schutz gegen fremde Host-/Origin-Header. Die Konfiguration oben umgeht das bereits (`proxy_set_header Host 127.0.0.1:11434;`); alternativ kann man Ollama mit `OLLAMA_ORIGINS=*` (bzw. einer konkreten Liste) starten.

Jetzt sind beide Ollama-APIs unter ordentlichen Base-URLs im LAN erreichbar — genau wie im Post beschrieben. In Open WebUI kann man unter *Admin-Einstellungen → Verbindungen* beide URLs (`http://ollama.heim.lan` und `http://ollama-ws.heim.lan`) als Ollama-Endpunkte eintragen. Ist die Workstation aus, nutzt man einfach die Modelle des Docker-Hosts.

**Tipp:** Wer lieber klickt statt Configs schreibt, nimmt den [Nginx Proxy Manager](https://nginxproxymanager.com) als Container.

**Quelle:** NGINX Reverse-Proxy-Doku: https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/

---

## 7. RAG einrichten: Docling + ChromaDB + bge-m3

Jetzt der Teil, der „nachts alles durchknödelt": Dokumente werden mit Docling in sauberen Text konvertiert, mit bge-m3 in Vektoren verwandelt und in ChromaDB abgelegt.

### Variante A (empfohlen): Open WebUI erledigt das RAG

Open WebUI bringt die komplette RAG-Pipeline bereits mit — ChromaDB ist die eingebaute Standard-Vektordatenbank, und Docling wird als Extraktions-Engine offiziell unterstützt.

**1. Docling-Container starten:** Der Docling-Server ist in beiden Compose-Dateien dieses Repos bereits enthalten — als Compose-*Profil* `rag`, damit das Basis-Setup schlank bleibt:

```bash
# Linux:
docker compose --profile rag up -d

# macOS:
docker compose -f docker-compose.macos.yml --profile rag up -d
```

Ein Port-Mapping braucht Docling nicht: Open WebUI erreicht den Container über das Compose-Netz direkt unter `http://docling:5001`. (Standardmäßig läuft das CPU-Image; wer die GPU für schnellere OCR mitnutzen will, trägt in der `.env` die CUDA-Variante ein — siehe [`.env.example`](.env.example). Achtung: Docling ist bei OCR-lastigen PDFs RAM-hungrig.)

**2. Open WebUI konfigurieren** unter *Admin-Einstellungen → Dokumente*:
- **Inhaltsextraktion / Content Extraction Engine:** `Docling` mit URL `http://docling:5001`
- **Embedding-Modell:** Engine `Ollama`, Modell `bge-m3`, URL `http://ollama:11434` — **auf einem macOS-Host stattdessen** `http://host.docker.internal:11434` (dort läuft Ollama ja nativ auf dem Mac, nicht als Container im Compose-Netz)

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

**1. ChromaDB-Server starten:** Der Container ist in beiden Compose-Dateien enthalten — im eigenen Profil `rag-batch`, denn Variante B braucht Chroma, aber nicht den Docling-Server aus Variante A:

```bash
# Linux:
docker compose --profile rag-batch up -d

# macOS:
docker compose -f docker-compose.macos.yml --profile rag-batch up -d
```

Der Indexer auf dem Host erreicht ihn unter `127.0.0.1:8000`, das Open-WebUI-Tool über das Compose-Netz unter `http://chroma:8000`.

**2. Indexer einrichten** ([`scripts/rag-indexer.py`](scripts/rag-indexer.py)) — mit eigenem venv, damit der Zeitplan-Aufruf dieselbe Umgebung nutzt wie die Installation. Unter Linux liegt alles unter `/srv`, unter macOS unter `/opt/heim-ki` (auf dem Mac ist `/srv` wegen des versiegelten Systemvolumes nicht anlegbar):

```bash
# Linux:
sudo mkdir -p /srv/scripts /srv/dokumente
sudo cp scripts/rag-indexer.py scripts/requirements.txt /srv/scripts/
sudo chown -R "$USER" /srv/scripts /srv/dokumente   # sonst scheitern venv-Anlage,
                                                    # Testlauf und Dokumente-Ablegen
python3 -m venv /srv/scripts/.venv
/srv/scripts/.venv/bin/pip install -r /srv/scripts/requirements.txt

# Testlauf (Dokumente vorher nach /srv/dokumente legen; mit sudo, weil das
# Manifest /srv/rag-index-state.json angelegt wird — wie später beim Timer):
sudo /srv/scripts/.venv/bin/python /srv/scripts/rag-indexer.py
```

```bash
# macOS (Pfade analog, plus Log- und Backup-Verzeichnis für die launchd-Jobs;
# alles gehört danach dem eingeloggten Nutzer, denn die Jobs laufen als
# LaunchAgents in dessen Session — siehe §3, "Unbeaufsichtigter Betrieb"):
sudo mkdir -p /opt/heim-ki/scripts /opt/heim-ki/dokumente /opt/heim-ki/logs /opt/heim-ki/backups
sudo chown -R "$USER" /opt/heim-ki
cp scripts/rag-indexer.py scripts/requirements.txt /opt/heim-ki/scripts/
python3 -m venv /opt/heim-ki/scripts/.venv
/opt/heim-ki/scripts/.venv/bin/pip install -r /opt/heim-ki/scripts/requirements.txt

# Testlauf (Dokumente vorher nach /opt/heim-ki/dokumente legen):
DOCS_DIR=/opt/heim-ki/dokumente STATE_FILE=/opt/heim-ki/rag-index-state.json \
  /opt/heim-ki/scripts/.venv/bin/python /opt/heim-ki/scripts/rag-indexer.py
```

Das Skript ist auf Dauerbetrieb ausgelegt: Es überspringt unveränderte Dateien (SHA-256-Manifest, Pfad per `STATE_FILE`), entfernt die Chunks gelöschter oder geänderter Dateien, bettet Chunks *mit* Überschriften-Kontext ein (`chunker.contextualize`) und bricht bei einer kaputten Datei nicht den ganzen Lauf ab. Pfade und URLs sind per Umgebungsvariablen konfigurierbar (siehe Skript-Kopf).

**3. Nächtlich laufen lassen:**

*Linux* — als systemd-Timer ([`scripts/systemd/`](scripts/systemd/)); der holt dank `Persistent=true` auch verpasste Läufe nach, und die Logs landen im journald:

```bash
sudo cp scripts/systemd/rag-indexer.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rag-indexer.timer

# Logs ansehen:
journalctl -u rag-indexer.service
```

(Wer lieber Cron mag: `0 2 * * * /srv/scripts/.venv/bin/python /srv/scripts/rag-indexer.py >> /var/log/rag-indexer.log 2>&1`)

*macOS* — als launchd-Job ([`scripts/launchd/`](scripts/launchd/)); die Pfade in der plist passen zu `/opt/heim-ki`. Der Job läuft bewusst als **LaunchAgent in der Nutzer-Session** (nicht als root-Daemon), denn er braucht um 2:00 Uhr das native Ollama und den Chroma-Container — und beide existieren nur in einer angemeldeten Session mit laufendem Docker Desktop/OrbStack (→ §3, „Unbeaufsichtigter Betrieb": automatische Anmeldung + „Start at login" einrichten, sonst läuft der Index nachts ins Leere):

```bash
mkdir -p ~/Library/LaunchAgents
cp scripts/launchd/de.heim-ki.rag-indexer.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/de.heim-ki.rag-indexer.plist

# Logs ansehen:
tail -f /opt/heim-ki/logs/rag-indexer.log
```

(launchd holt einen verpassten Lauf nach, wenn der Mac zur geplanten Zeit nur geschlafen hat — nach einem kompletten Shutdown oder ohne angemeldete Session allerdings nicht.)

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
# (macOS: jeweils mit "-f docker-compose.macos.yml"; das native Ollama
#  aktualisiert Homebrew: brew upgrade ollama)
```

**Vor größeren Versionssprüngen** ein Backup ziehen (siehe §11): `sudo /srv/scripts/backup.sh` (macOS: `BACKUP_DIR=/opt/heim-ki/backups STATE_FILE=/opt/heim-ki/rag-index-state.json /opt/heim-ki/scripts/backup.sh` — ohne sudo, denn als root sähe die docker-CLI den Docker-Desktop-Daemon nicht) — oder einfach den nächtlichen Backup-Timer abwarten.

---

## 10. HTTPS im LAN

HTTPS im LAN ist mehr als Kosmetik: Ohne Secure Context blockieren Browser den **Mikrofon-Zugriff** — die Sprach-Ein-/Ausgabe von Open WebUI funktioniert über `http://` von anderen Geräten aus schlicht nicht. Außerdem verschwinden die „Nicht sicher"-Warnungen in der Adressleiste, die im Familienbetrieb nur Fragen aufwerfen.

Der Weg mit [mkcert](https://github.com/FiloSottile/mkcert): eine eigene kleine Zertifizierungsstelle (CA) auf dem Docker-Host, die Zertifikate für die drei `.lan`-Namen ausstellt.

**Schritt 1 — CA anlegen und Zertifikat ausstellen** (auf dem Docker-Host):

```bash
sudo apt install mkcert libnss3-tools    # Linux
brew install mkcert nss                  # macOS ("nss" nur für Firefox nötig)

mkcert -install        # legt die lokale CA an und trägt sie auf DIESEM Rechner ein

# Ein Zertifikat für alle drei Namen:
mkcert chat.heim.lan ollama.heim.lan ollama-ws.heim.lan

# Linux:
sudo mkdir -p /etc/nginx/certs
sudo cp chat.heim.lan+2.pem     /etc/nginx/certs/heim-ki.pem
sudo cp chat.heim.lan+2-key.pem /etc/nginx/certs/heim-ki-key.pem

# macOS (Homebrew-NGINX):
sudo mkdir -p /opt/homebrew/etc/nginx/certs
sudo cp chat.heim.lan+2.pem     /opt/homebrew/etc/nginx/certs/heim-ki.pem
sudo cp chat.heim.lan+2-key.pem /opt/homebrew/etc/nginx/certs/heim-ki-key.pem
```

**Schritt 2 — NGINX auf HTTPS umstellen:** Die fertige Konfiguration liegt unter [`nginx/heim-ki-https.conf`](nginx/heim-ki-https.conf) — sie leitet Port 80 auf 443 um und **ersetzt** die HTTP-Variante:

```bash
# Linux (Debian/Ubuntu):
sudo cp nginx/heim-ki-https.conf /etc/nginx/sites-available/
sudo rm -f /etc/nginx/sites-enabled/heim-ki.conf
sudo ln -s /etc/nginx/sites-available/heim-ki-https.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# macOS (Homebrew): Zertifikatspfad in der Datei auf /opt/homebrew/etc/nginx/certs
# anpassen, dann:
sudo rm -f /opt/homebrew/etc/nginx/servers/heim-ki.conf
sudo cp nginx/heim-ki-https.conf /opt/homebrew/etc/nginx/servers/
sudo nginx -t && sudo brew services restart nginx
```

**Schritt 3 — CA auf den Familien-Geräten installieren.** Das ist der Preis von LAN-HTTPS: Jedes Gerät muss der eigenen CA einmalig vertrauen. Die CA-Datei liegt unter `$(mkcert -CAROOT)/rootCA.pem` — **nur die `rootCA.pem` verteilen, niemals die `rootCA-key.pem`!**

- **Windows:** Doppelklick auf `rootCA.pem` → *Zertifikat installieren* → Speicherort *Vertrauenswürdige Stammzertifizierungsstellen*.
- **Android:** Datei aufs Gerät kopieren → *Einstellungen → Sicherheit → Zertifikat installieren (CA-Zertifikat)*.
- **iOS/iPadOS:** `rootCA.pem` z. B. per AirDrop/Mail öffnen → Profil installieren → zusätzlich unter *Einstellungen → Allgemein → Info → Zertifikatsvertrauen* aktivieren.
- **macOS/Linux:** in den Schlüsselbund bzw. System-Truststore importieren; Firefox verwaltet seinen eigenen Speicher (*Einstellungen → Zertifikate → Importieren*).

**Alternative:** Wer statt des Host-NGINX lieber [Caddy](https://caddyserver.com) als Container einsetzt, bekommt mit `tls internal` dasselbe automatisch (Caddy bringt seine eigene CA mit) — das Verteilen der CA-Datei auf die Geräte bleibt aber auch dort nötig.

---

## 11. Backup & Restore

Gesichert werden muss, was nicht wiederbeschaffbar ist: das **Open-WebUI-Volume** (Nutzer, Chats, Wissenssammlungen), das **Chroma-Volume** (RAG-Index aus Variante B) und das Indexer-Manifest. Die Ollama-Modelle sind bewusst ausgenommen — die holt `ollama pull` jederzeit neu.

Das Skript [`scripts/backup.sh`](scripts/backup.sh) erledigt genau das (inklusive Aufräumen alter Stände, Standard: 14 Tage) und läuft täglich um 3:30 Uhr — nach dem RAG-Indexer.

*Linux* — per systemd-Timer:

```bash
sudo mkdir -p /srv/scripts    # existiert schon, falls §7 Variante B eingerichtet wurde
sudo cp scripts/backup.sh /srv/scripts/ && sudo chmod +x /srv/scripts/backup.sh
sudo cp scripts/systemd/heim-ki-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heim-ki-backup.timer

# Manuell laufen lassen / Logs:
sudo systemctl start heim-ki-backup.service
journalctl -u heim-ki-backup.service
```

*macOS* — per launchd-Job ([`scripts/launchd/de.heim-ki.backup.plist`](scripts/launchd/de.heim-ki.backup.plist), Ziel `/opt/heim-ki/backups`). Auch dieser Job läuft als **LaunchAgent in der Nutzer-Session**, denn die docker-CLI erreicht den Daemon von Docker Desktop/OrbStack nur dort (der Socket liegt unter `~/.docker/run/docker.sock`, nicht unter `/var/run/docker.sock`). Ist Docker nachts nicht erreichbar, bricht `backup.sh` mit Fehler ab, statt still ein leeres Backup zu schreiben — für zuverlässige Nachtläufe also §3, „Unbeaufsichtigter Betrieb" einrichten:

```bash
# Verzeichnisse existieren schon, falls §7 Variante B eingerichtet wurde — sonst:
sudo mkdir -p /opt/heim-ki/scripts /opt/heim-ki/logs /opt/heim-ki/backups
sudo chown -R "$USER" /opt/heim-ki

cp scripts/backup.sh /opt/heim-ki/scripts/ && chmod +x /opt/heim-ki/scripts/backup.sh
mkdir -p ~/Library/LaunchAgents
cp scripts/launchd/de.heim-ki.backup.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/de.heim-ki.backup.plist

# Manuell laufen lassen / Logs:
launchctl start de.heim-ki.backup
tail -f /opt/heim-ki/logs/backup.log
```

Zielverzeichnis ist `/srv/backups/heim-ki` bzw. `/opt/heim-ki/backups` (per `BACKUP_DIR` änderbar — idealerweise ein NAS-Mount, damit die Sicherung nicht auf derselben Platte liegt wie die Daten).

**Restore** (Beispiel Open-WebUI-Volume; für `chroma-data` analog — macOS: `docker compose` jeweils mit `-f docker-compose.macos.yml` und dem Backup-Pfad `/opt/heim-ki/backups`):

```bash
docker compose down
docker run --rm -v open-webui-data:/data -v /srv/backups/heim-ki:/backup alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/open-webui-data-JJJJ-MM-TT.tar.gz -C /data"
docker compose up -d
```

---

## 12. Wake-on-LAN für die Workstation

Damit die Workstation mit der großen GPU nicht durchlaufen muss, weckt man sie bei Bedarf aus dem LAN:

**Einmalig auf einer Windows-Workstation einrichten:**
1. Im **BIOS/UEFI** „Wake on LAN" (o. ä.) aktivieren.
2. In Windows im **Geräte-Manager** beim Netzwerkadapter unter *Energieverwaltung* „Gerät kann den Computer aus dem Ruhezustand aktivieren" und unter *Erweitert* „Wake on Magic Packet" aktivieren.
3. Den **Windows-Schnellstart deaktivieren** (*Energieoptionen → Auswählen, was beim Drücken von Netzschaltern geschehen soll*) — mit aktivem Schnellstart ist „Herunterfahren" ein Hybrid-Zustand, aus dem WoL oft nicht funktioniert.
4. Die MAC-Adresse notieren: `ipconfig /all` → „Physische Adresse".

**Bei einem Mac als Workstation:** In den *Systemeinstellungen → Energie* (bzw. *Batterie → Optionen*) „**Bei Netzwerkzugriff aufwecken**" aktivieren — das weckt den Mac aus dem **Ruhezustand** (nicht aus dem ausgeschalteten Zustand, Macs unterstützen kein WoL aus dem Shutdown). Funktioniert zuverlässig über Ethernet; bei reinem WLAN-Betrieb ist es Glückssache. MAC-Adresse: *Systemeinstellungen → Netzwerk → Details*.

**Wecken vom Docker-Host** mit [`scripts/wol.sh`](scripts/wol.sh):

```bash
sudo apt install wakeonlan     # Linux
brew install wakeonlan         # macOS-Host

./scripts/wol.sh AA:BB:CC:DD:EE:FF
```

Eine Minute später ist das Workstation-Ollama unter `ollama-ws.heim.lan` verfügbar (die native App startet unter Windows automatisch mit; auf dem Mac sorgt der LaunchAgent aus §5 dafür). **Tipp:** Eine Fritz!Box kann das auch ohne Skript — in den Gerätedetails unter *Heimnetz → Netzwerk* gibt es den Knopf „Computer starten".

---

## 13. Troubleshooting

| Symptom | Ursache & Abhilfe |
|---|---|
| Container sehen die GPU nicht | Test: `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`. Schlägt das fehl: `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`. Nach einem Treiber-Update: Host neu starten. |
| Antworten plötzlich sehr langsam | `docker exec ollama ollama ps` zeigt, ob das Modell (teilweise) auf der CPU läuft (`XX%/YY% CPU/GPU`). Abhilfe: kleineres/stärker quantisiertes Modell, oder §4-Hinweise (`OLLAMA_MAX_LOADED_MODELS=1`). |
| Erster Prompt „hängt" | Das Modell wird gerade ins VRAM geladen — bei größeren Modellen dauert das. `OLLAMA_KEEP_ALIVE` (§4) verhindert häufiges Neuladen. |
| `403 Forbidden` von `ollama.heim.lan` | Host-Header-Schutz von Ollama — die Konfiguration aus §6 sendet deshalb `Host 127.0.0.1:11434`; prüfen, ob wirklich die Repo-Konfiguration aktiv ist (`nginx -T \| grep -A5 ollama`). |
| Upload scheitert mit `413 Request Entity Too Large` | `client_max_body_size` fehlt/zu klein — in `nginx/heim-ki.conf` enthalten (100 MB), NGINX neu laden. |
| Docling-Container stürzt ab / Host swappt bei großen PDFs | Docling-OCR ist RAM-hungrig. Große Scans aufteilen, oder dem Service in der Compose-Datei ein `mem_limit` geben; notfalls Dokumente einzeln hochladen. |
| `ollama-ws.heim.lan` nach Windows-Neustart tot (WSL-Weg) | Die WSL-IP ist gewandert — Portproxy neu setzen oder auf *mirrored networking* bzw. die native App umstellen (§5). |
| Werkzeug „Heim-Dokumente" findet nichts | Läuft Chroma? (`docker ps` → `chroma (healthy)`; ein bloßes `docker compose ps` zeigt Profil-Dienste wie `chroma` nur mit `--profile rag-batch` — und auf macOS nur mit `-f docker-compose.macos.yml`). Hat der Indexer geschrieben? (Linux: `journalctl -u rag-indexer.service`, macOS: `/opt/heim-ki/logs/rag-indexer.log`). Stimmen Collection-Name und `chroma_url` in den Valves des Werkzeugs? |
| macOS: Ollama quälend langsam, Mac-Lüfter dreht | Läuft Ollama versehentlich als Container? Unter macOS haben Container **keinen GPU-Zugriff** — Ollama muss nativ laufen (`brew services start ollama`, §3) und Open WebUI über `docker-compose.macos.yml` auf `host.docker.internal:11434` zeigen. |
| macOS: Open WebUI erreicht Ollama nicht | Läuft das native Ollama? (`ollama ps`, `brew services list`). In den Open-WebUI-*Verbindungen* muss `http://host.docker.internal:11434` stehen, nicht `http://ollama:11434` — den Ollama-Service-Namen gibt es in der macOS-Compose-Datei nicht. |
| macOS: Nachtjobs (Indexer/Backup) sind nicht gelaufen | Die Jobs laufen als LaunchAgents nur in einer **angemeldeten Session** mit laufendem Docker Desktop/OrbStack und wachem Mac — §3, „Unbeaufsichtigter Betrieb" (Auto-Login, „Start at login", `pmset`). Status: `launchctl list \| grep heim-ki`; Logs: `/opt/heim-ki/logs/*.log`. |
| Allgemeine Diagnose | `docker compose ps` (Healthchecks!; macOS: mit `-f docker-compose.macos.yml`, Profil-Dienste zusätzlich mit `--profile rag`/`rag-batch` — oder einfach `docker ps`), `docker logs open-webui`, `docker stats`; nur Linux: `docker logs ollama`, `nvidia-smi` (macOS: Ollama nativ → `ollama ps`, Logs von `brew services`). |

---

## 14. Weiterführende Ideen

- **Modell-Empfehlungen für 4 GB VRAM:** `llama3.2:3b`, `qwen2.5:3b`, `phi3:mini` — alle in der [Ollama Library](https://ollama.com/library).
- **Zugriff von unterwegs:** Statt Portfreigaben ein VPN ins Heimnetz — die Fritz!Box kann WireGuard direkt, alternativ [Tailscale](https://tailscale.com). So bleibt die Heim-KI auch unterwegs erreichbar, ohne dass irgendetwas im Internet exponiert wird.
- **Automatische Update-Benachrichtigungen:** z. B. Watchtower im Monitor-Modus (`WATCHTOWER_MONITOR_ONLY=true`), damit Updates gemeldet, aber bewusst eingespielt werden (§9).

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
| mkcert | https://github.com/FiloSottile/mkcert |
| NVIDIA Container Toolkit | https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html |
| WSL2 | https://learn.microsoft.com/de-de/windows/wsl/install |
| Homebrew (macOS) | https://brew.sh |
| Docker Desktop für Mac | https://docs.docker.com/desktop/setup/install/mac-install/ |
