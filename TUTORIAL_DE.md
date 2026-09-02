# Lokale Heim-KI mit Open WebUI, Ollama & RAG — komplett ohne Cloud

**Tutorial: Ein privater KI-Assistent im eigenen LAN, der auch eigene Dokumente durchsuchen kann**

> Basierend auf einem Setup von Matthias Kallenbach (LinkedIn-Post). Ziel: Eine ChatGPT-ähnliche Oberfläche für alle im Haushalt („Mama kann auch die Heim-KI benutzen"), bei der **keine Daten das eigene Netzwerk verlassen**.

**Dieses Repo enthält neben dem Tutorial die fertigen Konfigurationsdateien:**
[`docker-compose.yml`](docker-compose.yml) (Linux) · [`docker-compose.macos.yml`](docker-compose.macos.yml) (macOS) · [`.env.example`](.env.example) · [`nginx/`](nginx) (HTTP- und HTTPS-Konfiguration) · [`scripts/`](scripts) (Dokument-Sync, Backup, Wake-on-LAN, systemd- und launchd-Units)

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
                        │        │         │ bge-m3, Index │  │
                        │        │         │ in Open WebUI │  │
                        │        │         │ (Upload oder  │  │
                        │        │         │  Nachtlauf)   │  │
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
| **[Docling](https://github.com/docling-project/docling)** | Open-Source-Tool von IBM Research: konvertiert PDF, DOCX, PPTX, HTML usw. in sauberes, strukturiertes Markdown/JSON — inkl. Tabellen und Layout-Erkennung. Läuft wahlweise als Container oder nativ auf dem Host; auf Apple Silicon ist der native Weg rund doppelt so schnell wie der Container in der hier ausgelieferten Konfiguration mit 6 Threads (§7). (Im Original-Post „Dockling" geschrieben — gemeint ist Docling.) |
| **Vektorspeicher von Open WebUI** | Vektordatenbank. Speichert die von bge-m3 erzeugten Embeddings und liefert bei einer Frage die passenden Dokument-Schnipsel zurück. Sie steckt bereits in Open WebUI — kein eigener Dienst, kein eigener Container. |
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

Beide Ports sind bewusst **nur an `127.0.0.1` gebunden**: Aus dem LAN kommt man ausschließlich über den NGINX aus §6 — so gibt es genau einen Eingang. Ollama selbst hat keinerlei Authentifizierung, deshalb steht sein vHost dort hinter einer IP-Allowlist **und** einer Basic Auth (`satisfy all`); ohne beides gibt es keinen Zugriff auf `/api/pull`, `/api/delete` und Konsorten. (Das heißt auch: `http://<docker-host-ip>:3000` funktioniert von anderen Rechnern aus *nicht* — erst §6 einrichten und `http://chat.heim.lan` benutzen, oder zum Testen per SSH-Tunnel `ssh -L 3000:localhost:3000 <docker-host>`.)

Modelle laden:

```bash
# Ein kleines Chat-Modell, das in 4 GB VRAM passt:
docker exec ollama ollama pull llama3.2:3b

# Das Embedding-Modell für RAG:
docker exec ollama ollama pull bge-m3
```

**Erster Login & Registrierung:** Beim ersten Aufruf einen Admin-Account anlegen. Danach die offene Selbstregistrierung schließen — sonst kann sich jeder im LAN ein Konto anlegen: Open WebUI liest `ENABLE_SIGNUP` nur beim allerersten Start aus der `.env`, danach kommt der Wert aus der Datenbank — ein späteres `ENABLE_SIGNUP=false` in der `.env` plus erneutes `docker compose up -d` bleibt also wirkungslos. Stattdessen in den *Admin-Einstellungen* den Schalter für die Selbstregistrierung umlegen. Dank `DEFAULT_USER_ROLE=pending` landen neue Konten ohnehin erst einmal auf „Ausstehend" und müssen von einem Admin einzeln freigegeben werden.

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

Die Hinweise oben zu **Erster Login & Registrierung** gelten unverändert. Statt der VRAM-Klimmzüge gilt auf dem Mac die Unified-Memory-Faustregel: macOS gönnt den Modellen grob bis zu ~⅔ des RAM — auf einem 16-GB-Mac laufen `llama3.2:3b` und `bge-m3` bequem nebeneinander, sogar 7B–8B-Modelle sind drin. Wer `OLLAMA_KEEP_ALIVE` o. ä. setzen will: `launchctl setenv OLLAMA_KEEP_ALIVE 30m` (danach `brew services restart ollama`) wirkt sofort, ist aber nach einem Neustart stillschweigend wieder weg — dauerhaft verdrahtet man solche Variablen in einem eigenen LaunchAgent statt über `brew services` — das mitgelieferte [`scripts/launchd/de.heim-ki.ollama-ws.plist`](scripts/launchd/de.heim-ki.ollama-ws.plist) taugt dafür allerdings *nicht* mehr als Vorlage: Es ist fest auf die Workstation-Rolle aus §5 zugeschnitten (`OLLAMA_HOST` dort hart auf `127.0.0.1:11434` verdrahtet, für den SSH-Tunnel). Für den Docker-Host reicht eine eigene, einfache Kopie mit anderem `Label` und `OLLAMA_KEEP_ALIVE` in den `EnvironmentVariables`. Und weil Ollama nativ standardmäßig nur an `127.0.0.1` lauscht, gilt dieselbe Sicherheitslogik wie unter Linux: Der NGINX aus §6 ist der einzige Eingang aus dem LAN.

**Quellen:**
- Ollama Docker-Image: https://hub.docker.com/r/ollama/ollama
- Open WebUI Doku: https://docs.openwebui.com

---

## 5. Ollama „on demand" auf der Workstation (Windows oder macOS)

Die Workstation hat typischerweise mehr GPU-Leistung, läuft aber nicht rund um die Uhr. Deshalb läuft dort ein zweiter Ollama-Server, der nur verfügbar ist, wenn der Rechner an ist.

### Windows: native App (empfohlen)

[Ollama für Windows](https://ollama.com/download/windows) installieren, dann als *Benutzer-Umgebungsvariable* (`Systemsteuerung → Umgebungsvariablen`) `OLLAMA_HOST=0.0.0.0` setzen und Ollama neu starten, damit es aus dem LAN erreichbar ist. Zuletzt den Port in der Windows-Firewall freigeben (PowerShell als Administrator):

```powershell
# -RemoteAddress und -Profile sind wichtig: ohne sie gilt die Regel in JEDEM
# Netz, auch im Profil "Öffentlich" — und Ollama hat keine Authentifizierung.
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -LocalPort 11434 `
  -Protocol TCP -Action Allow -RemoteAddress <DOCKER-HOST-IP> -Profile Private
```

Fertig — kein WSL, kein Portproxy, und Ollama startet automatisch mit Windows.

**Hinweis zum mitgelieferten NGINX:** Die Konfiguration aus §6 geht für `ollama-ws.heim.lan` vom SSH-Tunnel-Modell aus §5 (macOS) aus und proxyt fest auf `127.0.0.1:11435`. Wer die Workstation stattdessen wie hier direkt exponiert, muss den `ollama-ws`-Serverblock von Hand auf `proxy_pass http://<WORKSTATION-IP>:11434;` umstellen — die Firewall-Regel oben (nur `<DOCKER-HOST-IP>`, Profil „Privat") bleibt dann die einzige Zugriffsschranke auf der Workstation selbst.

### macOS: Mac als Workstation

Ein Mac (z. B. ein MacBook Pro mit viel RAM) funktioniert genauso gut als On-demand-Workstation. Ollama bindet dafür **nicht** an `0.0.0.0` — Ollama hat keinerlei Authentifizierung, und an `0.0.0.0` gebunden lauschte das Notebook in jedem Netz, dem es beitritt, auch im Café- oder Hotel-WLAN. Stattdessen bleibt Ollama strikt auf `127.0.0.1`, und ein **SSH-Reverse-Tunnel** legt genau diesen einen Port gezielt auf dem Docker-Host offen — dort ebenfalls nur auf dessen Loopback, wo NGINX (§6) ihn hinter IP-Allowlist und Basic Auth abholt.

**1. Lokales Ollama fest auf Loopback verdrahten:** Der mitgelieferte LaunchAgent [`scripts/launchd/de.heim-ki.ollama-ws.plist`](scripts/launchd/de.heim-ki.ollama-ws.plist) bindet `OLLAMA_HOST` fest an `127.0.0.1:11434` — hier ist nichts mehr zu ersetzen:

```bash
brew install ollama    # nur die Binärdatei — KEIN "brew services start ollama" dazu,
                       # sonst streiten sich zwei Instanzen um Port 11434

mkdir -p ~/Library/LaunchAgents /opt/heim-ki/logs
cp scripts/launchd/de.heim-ki.ollama-ws.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/de.heim-ki.ollama-ws.plist
```

**2. Schlüsselpaar für den Tunnel erzeugen** (auf der Workstation):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ollama_tunnel -N ""
```

**3. Host-Key des Docker-Hosts hinterlegen** — und den Fingerprint prüfen, sonst könnte sich im LAN etwas als Docker-Host ausgeben:

```bash
ssh-keyscan <DOCKER-HOST-IP> >> ~/.ssh/known_hosts
```

Fingerprint gegen `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` **auf dem Docker-Host** abgleichen.

**4. Auf dem Docker-Host** einen eigenen, stark eingeschränkten Systemaccount für den Tunnel anlegen:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin ollama-tunnel
sudo -u ollama-tunnel mkdir -p ~ollama-tunnel/.ssh
sudo -u ollama-tunnel tee ~ollama-tunnel/.ssh/authorized_keys <<'EOF'
restrict,port-forwarding,permitlisten="127.0.0.1:11435",permitlisten="172.17.0.1:11435" ssh-ed25519 AAAA... kommentar
EOF
sudo -u ollama-tunnel chmod 600 ~ollama-tunnel/.ssh/authorized_keys
```

`restrict` schaltet zunächst alles ab (Port-, Agent-, X11-Forwarding, PTY); `port-forwarding` schaltet die Weiterleitung als einziges wieder an — ohne dieses Schlüsselwort käme der Tunnel gar nicht zustande, denn `permitlisten` begrenzt nur, es aktiviert nichts. Die beiden kommagetrennten `permitlisten`-Einträge begrenzen den Remote-Forward (`-R`) auf genau diese zwei Listener: `127.0.0.1:11435` für NGINX (§6) und `172.17.0.1:11435` — die Gateway-Adresse der Docker-Bridge (`docker0`), über die Open WebUI (selbst Container im Compose-Bridge-Netz) die Workstation direkt erreicht, ohne NGINX und ohne Credentials, da ein Request von dort mit Bridge-IP an der Allowlist scheiterte (403) und Open WebUI für Ollama-Verbindungen ohnehin Bearer- statt Basic-Auth schickt (401); diese zweite Adresse ist hostabhängig — auf dem Docker-Host mit `ip -4 addr show docker0` prüfen und bei Abweichung hier sowie in der `-R`-Zeile der Tunnel-plist anpassen. `nologin` ist unproblematisch, weil `ssh -N` keine Shell startet.

**Restrisiko:** `port-forwarding` reaktiviert jede Forward-Art, auch lokale (`-L`) und dynamische (`-D`) Forwards — `permitlisten` schränkt nur den Remote-Listener ein, nicht die Forward-Richtung. Ein kompromittierter Tunnel-Key erlaubt also weiterhin Pivoting vom Docker-Host aus; dafür gibt es keine per-Key-Option in `authorized_keys`. Wer das zusätzlich schließen will, ergänzt optional in der `sshd_config` des Docker-Hosts:

```
Match User ollama-tunnel
    GatewayPorts clientspecified
    AllowTcpForwarding remote
    PermitListen 127.0.0.1:11435 172.17.0.1:11435
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
```

`AllowTcpForwarding remote` ist der First-Class-Mechanismus, der genau `-R` erlaubt und `-L`/`-D` ausschließt und bleibt wie der Rest des Blocks optionale Zusatzhärtung gegen das Pivoting-Restrisiko; `GatewayPorts clientspecified` dagegen ist nicht optional, sobald der zweite Listener genutzt wird — per Default bindet sshd Remote-Forwardings lautlos auf Loopback und ignoriert eine abweichende `bind_address` in `-R`, ohne Fehlermeldung, sodass der Bridge-Listener einfach fehlt; wer den Match-Block oben nicht anlegen will, muss `GatewayPorts clientspecified` trotzdem irgendwo für den Nutzer `ollama-tunnel` setzen (global oder in einem eigenen, kleineren Match-Block).

**5. Tunnel-Agent auf der Workstation installieren** ([`scripts/launchd/de.heim-ki.ollama-tunnel.plist`](scripts/launchd/de.heim-ki.ollama-tunnel.plist), `DOCKER-HOST-IP` und `BENUTZER` in der Datei vorher ersetzen):

```bash
mkdir -p ~/Library/LaunchAgents /opt/heim-ki/logs
cp scripts/launchd/de.heim-ki.ollama-tunnel.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/de.heim-ki.ollama-tunnel.plist
```

**6. Verhalten bei abwesendem Notebook:** Ist der Mac aus oder unterwegs, steht der Tunnel nicht — `ollama-ws.heim.lan` antwortet dann mit `502 Bad Gateway`. Das ist so gewollt, kein Fehlerfall.

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

# -RemoteAddress und -Profile sind wichtig: ohne sie gilt die Regel in JEDEM
# Netz, auch im Profil "Öffentlich" — und Ollama hat keine Authentifizierung.
New-NetFirewallRule -DisplayName "Ollama WSL" -Direction Inbound -LocalPort 11434 `
  -Protocol TCP -Action Allow -RemoteAddress <DOCKER-HOST-IP> -Profile Private
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
> 4. **macOS-Host: ganz ohne DNS (mDNS/Bonjour)** — siehe unten.

> ℹ️ **Bei anderen Routern ist es teils noch enger.** Ein Telekom **Speedport Smart 3** etwa kennt überhaupt keine eigenen DNS-Einträge, weder A-Records noch eine Ausnahmeliste; vergibt man dort eigene Gerätenamen, schaltet er die lokale Namensauflösung sogar ganz ab. Die „festen DNS-Server" unter *Internet → Internetverbindung* sind etwas anderes — das ist nur der Upstream-Resolver. Bleiben Option 1, 2 oder — auf einem Mac — Option 4.

**Der bequeme Weg auf einem macOS-Host: `.local` statt `heim.lan`**

Läuft die Heim-KI auf einem Mac, braucht es für den Chat **gar keine DNS-Einrichtung**. Zwei Dinge greifen ineinander:

- macOS meldet seinen lokalen Hostnamen automatisch per **Bonjour/mDNS** im LAN an. Der Rechner ist damit ohne Router-Eintrag und ohne `hosts`-Datei unter `<lokaler-hostname>.local` erreichbar.
- NGINX benutzt den **ersten** Server-Block auf einem Listen-Socket als *Default Server*. In [`nginx/heim-ki.conf`](nginx/heim-ki.conf) ist das der Chat-vHost — jeder Name, der auf keinen `server_name` passt, landet also bei Open WebUI.

Zusammen heißt das: `http://<name>.local` führt sofort in den Chat. Einen kürzeren Namen setzt man mit

```bash
sudo scutil --set LocalHostName heim-ki     # danach: http://heim-ki.local
```

(dasselbe geht über *Systemeinstellungen → Allgemein → Teilen → Lokaler Hostname*). Prüfen lässt sich das vom Client aus mit `ping heim-ki.local`, ausführlicher mit `dns-sd -G v4 heim-ki.local` (macOS) oder `avahi-resolve -n heim-ki.local` (Linux).

Drei Einschränkungen, die man kennen sollte:

- **Nur der Chat.** Die beiden Ollama-vHosts prüfen ihren `server_name` wirklich — für `ollama.heim.lan` braucht es weiterhin echtes DNS oder einen `hosts`-Eintrag. Für den Alltag im Browser ist das kein Verlust.
- **Android ist der Wackelkandidat.** macOS, iOS, Windows 10+ und Linux mit Avahi lösen `.local` von Haus aus auf; Android-Browser tun das nur unzuverlässig — dort bleibt die IP-Adresse.
- **Mit HTTPS (§10) passt das Zertifikat nicht**, wenn der Name nicht drinsteht. Deshalb den `.local`-Namen gleich in den mkcert-Aufruf aufnehmen: `mkcert chat.heim.lan ollama.heim.lan ollama-ws.heim.lan heim-ki.local`.

**Schritt 2 — NGINX-Konfiguration:** Die fertige Konfiguration liegt in diesem Repo unter [`nginx/heim-ki.conf`](nginx/heim-ki.conf) — vor dem Kopieren `<LAN-CIDR>` durch das eigene Heimnetz ersetzen (z. B. `192.168.1.0/24`). Die wichtigsten Blöcke (gekürzt):

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

# Ollama API (Docker-Host) — Workstation-Block analog, siehe Datei (proxyt
# dort auf 127.0.0.1:11435, den SSH-Tunnel-Zielport aus §5)
server {
    listen 80;
    server_name ollama.heim.lan;

    # Ollama bringt KEINE eigene Authentifizierung mit — deshalb IP-Allowlist
    # UND Basic Auth (satisfy all = beides nötig, nicht nur eins von beiden):
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
        # Vertretbar NUR, weil die auth_basic oben davorsteht:
        proxy_set_header Host 127.0.0.1:11434;
        proxy_read_timeout 600s;   # große Modelle brauchen Zeit
        proxy_buffering off;       # Token-Streaming
    }
}
```

**Basic-Auth-Passwortdatei anlegen:** Die Ollama-vHosts oben verlangen zusätzlich zur IP-Allowlist eine Basic Auth — dafür einmalig eine Passwortdatei anlegen:

```bash
# Linux (Debian/Ubuntu):
sudo apt install apache2-utils   # Paket mit htpasswd (Debian/Ubuntu)
sudo htpasswd -c /etc/nginx/heim-ki.htpasswd heim-ki
sudo chown root:www-data /etc/nginx/heim-ki.htpasswd
sudo chmod 640 /etc/nginx/heim-ki.htpasswd

# macOS: htpasswd ist Teil des in macOS eingebauten (veralteten) Apache und
# liegt bereits unter /usr/sbin/htpasswd — kein Homebrew-Paket nötig. NGINX
# läuft hier als root (siehe "sudo brew services start nginx" weiter unten),
# Besitz/Modus daher entsprechend eng fassen; eine www-data-Gruppe gibt es
# unter macOS nicht:
sudo mkdir -p /opt/homebrew/etc/nginx
sudo /usr/sbin/htpasswd -c /opt/homebrew/etc/nginx/heim-ki.htpasswd heim-ki
sudo chown root:wheel /opt/homebrew/etc/nginx/heim-ki.htpasswd
sudo chmod 600 /opt/homebrew/etc/nginx/heim-ki.htpasswd
```

⚠️ **Nur für diese HTTP-Variante:** Basic Auth überträgt das Passwort base64-kodiert im Klartext, bei jedem Request — jedes mitlesende Gerät im LAN kennt es danach. Für den Dauerbetrieb §10 (HTTPS) einrichten; diese Datei ist als Zwischenschritt vor der mkcert-Einrichtung gedacht.

**Windows-Workstation:** Vor `nginx -t` im `ollama-ws.heim.lan`-Block `proxy_pass http://127.0.0.1:11435;` durch `proxy_pass http://<WORKSTATION-IP>:11434;` ersetzen — der Tunnel-Port oben gilt nur für den macOS-Weg aus §5, unter Windows gibt es kein Tunnel-Äquivalent.

Installieren und aktivieren:

```bash
# Linux (Debian/Ubuntu):
sudo cp nginx/heim-ki.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/heim-ki.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# macOS (Homebrew):
sudo cp nginx/heim-ki.conf /opt/homebrew/etc/nginx/servers/

# Platzhalter und Linux-Pfade in der KOPIERTEN Datei anpassen (das eigene
# Heimnetz eintragen; die htpasswd liegt unter Homebrew woanders als unter
# Debian):
sudo sed -i '' \
  -e 's|<LAN-CIDR>|192.168.1.0/24|g' \
  -e 's|/etc/nginx/heim-ki.htpasswd|/opt/homebrew/etc/nginx/heim-ki.htpasswd|g' \
  /opt/homebrew/etc/nginx/servers/heim-ki.conf

sudo nginx -t && sudo brew services restart nginx
```

> **Bleibt der Platzhalter stehen**, bricht schon der Konfigurationstest ab — `allow <LAN-CIDR>;` ist keine gültige Direktive:
>
> ```
> nginx: [emerg] invalid parameter "<LAN-CIDR>" in .../heim-ki.conf:<Zeile der allow-Direktive>
> ```
>
> Dann lädt NGINX die **ganze** Datei nicht, also auch den Chat-vHost nicht. Deshalb steht `nginx -t` hier wie in der Linux-Zeile vor dem Start und nicht dahinter.

> **Zwei Warnungen beim `sudo brew services start`** sind normal und kein Fehler: „Taking root:admin ownership of some nginx paths" heißt nur, dass ein späteres `brew upgrade nginx` ebenfalls `sudo` braucht. Und „`nginx` must be run as non-root to start at user login!" ist hier sogar erwünscht — als Root-**LaunchDaemon** startet NGINX beim *Boot* statt erst bei der Anmeldung und ist damit der einzige Baustein, der einen Neustart ohne Login übersteht (Ollama und Docker Desktop hängen weiterhin an der Sitzung, siehe §3). Root ist ohnehin Pflicht, Port 80/443 sind privilegiert.

Die Konfiguration selbst ist auf beiden Systemen identisch — Open WebUI lauscht auf `127.0.0.1:3000` und Ollama auf `127.0.0.1:11434`, egal ob Ollama im Container (Linux) oder nativ (macOS) läuft.

**Falls Ollama über den Proxy `403 Forbidden` liefert:** Das ist Ollamas Schutz gegen fremde Host-/Origin-Header. Die Konfiguration oben umgeht das bereits (`proxy_set_header Host 127.0.0.1:11434;`). `OLLAMA_ORIGINS=*` löst das Problem zwar auch, ist aber keine gute Idee: damit darf jede beliebige Webseite, die jemand im Haushalt öffnet, Requests an die API schicken. Die Umgehung des Host-Checks ist hier nur deshalb vertretbar, weil im NGINX Allowlist und Basic Auth davorstehen.

Jetzt sind beide Ollama-APIs unter ordentlichen Base-URLs im LAN erreichbar — für Menschen und CLI-Clients trägt man dafür in Open WebUI unter *Admin-Einstellungen → Verbindungen* `http://ollama.heim.lan` ein. Für die Workstation gilt das **nicht**: Open WebUI läuft selbst als Container im Compose-Bridge-Netz, ein Request von dort an `ollama-ws.heim.lan` trüge die Bridge-IP statt einer LAN-Adresse (403 an der Allowlist) und selbst mit erlaubter IP schickt Open WebUI für Ollama-Verbindungen Bearer-Token statt der von NGINX erwarteten Basic Auth (401) — der vHost bleibt also den menschlichen und CLI-Clients vorbehalten. Für die Workstation trägt man stattdessen `http://host.docker.internal:11435` ein, wie es der zweite Tunnel-Listener aus §5 auf der Docker-Bridge-Gateway-Adresse bereitstellt (`extra_hosts` in der `docker-compose.yml` macht den Namen im Container auflösbar). Ist die Workstation aus, nutzt man einfach die Modelle des Docker-Hosts.

Ob dieser Weg wirklich steht, prüft man aus dem Container heraus — das ist genau die Perspektive, aus der Open WebUI zugreift:

```bash
docker exec open-webui curl -sS http://host.docker.internal:11435/api/tags
```

Kommt hier die Modellliste der Workstation, passt alles. Ein Verbindungsfehler heißt in aller Regel: die Bridge-Gateway-Adresse aus §5 stimmt auf diesem Host nicht (mit `ip -4 addr show docker0` prüfen) oder der Tunnel läuft nicht.

**Tipp:** Wer lieber klickt statt Configs schreibt, nimmt den [Nginx Proxy Manager](https://nginxproxymanager.com) als Container.

**Quelle:** NGINX Reverse-Proxy-Doku: https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/

---

## 7. RAG einrichten: Docling + bge-m3 + Open WebUI

Jetzt der Teil, der „nachts alles durchknödelt": Dokumente werden mit Docling in sauberen Text konvertiert, mit bge-m3 in Vektoren verwandelt und im Vektorspeicher von Open WebUI abgelegt.

Dorthin führen zwei Wege, und sie schließen sich nicht aus:

- **Variante A** — Dokumente über den Browser hochladen, Docling läuft als Container. Der einfache Weg, und für die meisten der richtige.
- **Variante C** — ein Ordner auf dem Host wird nachts automatisch abgeglichen, die Konvertierung läuft dabei *nativ* auf dem Host. Das Ergebnis landet in derselben Art von Wissenssammlung wie bei Variante A.

Beide enden also im selben Speicher. Das ist keine Selbstverständlichkeit: Ein früherer Stand dieses Tutorials hatte hier eine **Variante B** mit eigener Vektordatenbank und einem Such-Werkzeug im Chat — zwei getrennte Wissensspeicher, von denen der eine `#Sammlung` und Zitate konnte und der andere nicht. Sie ist ersetzt worden; wer sie gebaut hat, findet den Rückbau am Ende dieses Abschnitts unter „Migration von Variante B".

### Variante A (empfohlen): Open WebUI erledigt das RAG

Open WebUI bringt die komplette RAG-Pipeline bereits mit — die Vektordatenbank ist eingebaut, und Docling wird als Extraktions-Engine offiziell unterstützt.

**1. Docling-Container starten:** Der Docling-Server ist in beiden Compose-Dateien dieses Repos bereits enthalten — als Compose-*Profil* `rag`, damit das Basis-Setup schlank bleibt:

```bash
# Linux:
docker compose --profile rag up -d

# macOS:
docker compose -f docker-compose.macos.yml --profile rag up -d
```

Ein Port-Mapping braucht Docling nicht: Open WebUI erreicht den Container über das Compose-Netz direkt unter `http://docling:5001`. (Standardmäßig läuft das CPU-Image; wer die GPU für schnellere OCR mitnutzen will, trägt in der `.env` die CUDA-Variante ein — siehe [`.env.example`](.env.example). Achtung: Docling ist bei OCR-lastigen PDFs RAM-hungrig.)

> **Timeout bei großen Scans:** Open WebUI konvertiert *synchron* (`POST /v1/convert/file`) und wartet ohne eigenes Zeitlimit — abgebrochen wird also von docling-serve, dessen `max_sync_wait` per Default bei **120 Sekunden** liegt. Im Log sieht das so aus: `"POST /v1/convert/file HTTP/1.1" 504`. Ein gescanntes 190-Seiten-Heft braucht mit OCR auf der CPU aber gemessene 346,0 bis 766,4 Sekunden, also 6 bis 13 Minuten je nach Thread-Zahl (Tabelle gleich unten). Die Compose-Dateien setzen `DOCLING_SERVE_MAX_SYNC_WAIT` deshalb auf 3600 Sekunden — bewusst großzügig und nicht knapp bemessen, damit auch langsamere Hardware und deutlich größere Dokumente darunter bleiben; über die `.env` lässt sich der Wert anpassen.

**Die Thread-Zahl des Containers ist einen Blick wert.** Docling parallelisiert Layout-Erkennung und OCR über OpenMP; wie viele Threads es dafür aufmacht, sagt ihm `OMP_NUM_THREADS`. Beide Compose-Dateien füttern das aus `DOCLING_OMP_THREADS` in der [`.env`](.env.example), Default **6**. Der Wert ist gemessen, nicht geraten — dasselbe gescannte 188-Seiten-Heft (37,7 MB, keine Textebene) fünfmal durch denselben Container:

| `DOCLING_OMP_THREADS` | Dauer |
|---|---|
| 2 | 766,4 s |
| 4 | 509,8 s |
| 6 | **346,0 s** |
| 8 | 419,3 s |
| 12 | 656,8 s |

Die Ausgabe war in allen fünf Läufen byte-identisch (815.904 Zeichen) — es geht hier also ausschließlich um Zeit, nicht um Qualität. Und mehr ist deutlich nicht besser: Ab 8 Threads geht es wieder bergauf, weil ONNX Runtime und Torch zusätzlich ihre eigenen Thread-Pools aufmachen und sich die Kerne dann gegenseitig überbuchen. 12 Threads sind fast doppelt so langsam wie 6.

> **Wer nachmisst, misst an einem großen Dokument.** Die Zahlen oben stammen von einem Mac Studio (M4 Max); auf anderer Hardware liegt das Optimum woanders. Aber Vorsicht bei der Messung selbst: an einem 8-Seiten-PDF war ausgerechnet der schlechteste dieser Werte der schnellste — bei kurzen Läufen misst man das Laden der Modelle, nicht die Konvertierung. Ein Testdokument, das mindestens ein paar Minuten braucht, ist Pflicht.

**2. Open WebUI konfigurieren** unter *Admin-Einstellungen → Dokumente*:
- **Inhaltsextraktion / Content Extraction Engine:** `Docling` mit URL `http://docling:5001`
- **Embedding-Modell:** Engine `Ollama`, Modell `bge-m3`, URL `http://ollama:11434` — **auf einem macOS-Host stattdessen** `http://host.docker.internal:11434` (dort läuft Ollama ja nativ auf dem Mac, nicht als Container im Compose-Netz)
- **Embedding Batch Size:** `1` → **`64`**
- **Concurrent Requests** (beim Embedding): `0` → **`4`**

Die letzten beiden Werte sind bei großen Dokumenten nicht optional. Mit dem Default `batch_size=1` und `concurrent_requests=0` („unbegrenzt") schickt Open WebUI **jeden einzelnen Chunk als eigene Anfrage — und alle gleichzeitig**. Ein 190-Seiten-Scan ergibt rund 1000 Chunks, Ollamas Warteschlange fasst per Default aber nur 512 (`OLLAMA_MAX_QUEUE`). Der Import stirbt dann nach erfolgreicher Extraktion mit:

```
Ollama embed error (503): server busy, please try again.
maximum pending requests exceeded
```

Mit `64` und `4` werden daraus ~16 Batch-Anfragen, von denen höchstens vier parallel laufen — schonender *und* schneller, weil bge-m3 einen Batch fast so schnell verarbeitet wie einen Einzeltext.

> **Achtung:** Beide Werte sind *PersistentConfig* — Open WebUI liest sie nur beim allerersten Start aus der Umgebung und nimmt sie danach aus der Datenbank. Ein Eintrag in der `.env` oder der Compose-Datei bleibt also wirkungslos; die Änderung muss durch die Oberfläche. Nachsehen lässt sich der gespeicherte Stand mit:
>
> ```bash
> docker exec open-webui python3 -c "
> import sqlite3
> db = sqlite3.connect('file:/app/backend/data/webui.db?mode=ro', uri=True)
> for k, v in db.execute(\"select key, value from config where key like 'rag.embedding%'\"):
>     print(k, '=', v)"
> ```

**Noch ein Handgriff:** `bge-m3` steht nach dem `ollama pull` auch in der Modellauswahl des Chats — wo es nichts zu suchen hat. Wählt man es dort versehentlich aus, bricht die Antwort ab mit `"bge-m3:latest" does not support chat`; im Backend-Log steht dazu nichts, Open WebUI lehnt die Anfrage direkt ab. Deshalb unter *Admin-Bereich → Einstellungen → Modelle* bei `bge-m3` die **Sichtbarkeit ausschalten**. Als Embedding-Modell bleibt es voll funktionsfähig — dafür spricht Open WebUI direkt mit Ollama und geht nicht über die Modellliste.

**3. Dokumente reinschmeißen:** In Open WebUI unter *Arbeitsbereich → Wissen* eine Sammlung anlegen und „alle Docs, die hier so rumfliegen" hochladen. Im Chat bindet man die Sammlung mit `#Sammlungsname` ein — fertig ist die Dokumenten-KI.

**Quellen:**
- Open WebUI RAG-Doku: https://docs.openwebui.com/features/rag
- Docling Serve: https://github.com/docling-project/docling-serve

### Variante C: Nächtlicher Ordner-Abgleich mit nativer Konvertierung

Wer einen Ordner auf dem Host einfach vollkippen und den Rest der Maschine überlassen will, bekommt hier die zweite Hälfte. Das Ziel ist dasselbe wie bei Variante A — nur der Weg dorthin ist ein anderer:

```
/srv/dokumente ──► doc-sync.py ──► Open-WebUI-API ──► Wissenssammlung
                   (Host, nachts    (/api/v1/files      "Heim-Dokumente"
                    per Timer;       + /knowledge)       — im Chat als
                    Docling nativ)                        #Heim-Dokumente)
```

Am Ende steht eine ganz normale Wissenssammlung: dieselbe, die man auch von Hand hätte befüllen können. Chunking, Embedding und Zitate erledigt Open WebUI genau wie bei Variante A. Es gibt keinen zweiten Wissensspeicher und kein Werkzeug, das man im Chat erst zuschalten müsste.

**Warum nativ statt im Container?** Weil Docker Desktop auf Apple Silicon die GPU nicht in den Container durchreicht. Im Log des Docling-Containers steht deshalb dauerhaft:

```
Accelerator device: 'cpu'
```

Nativ auf demselben Mac sieht Docling die Hardware dagegen und wählt von selbst den schnellen Weg. Am selben gescannten 188-Seiten-Heft gemessen:

| Weg | Dauer | Zeichen |
|---|---|---|
| Container, 4 Threads (der Stand vor diesem Umbau) | 509,8 s | 815.904 |
| Container, 6 Threads (Optimum, siehe Variante A) | 346,0 s | 815.904 |
| **Nativ (MPS + Apple Vision)** | **181,5 s** | 841.999 |

> **Der häufigste Irrtum an dieser Stelle:** Die Thread-Kurve aus Variante A gilt **nur für den Container**. Nativ ist die Thread-Zahl schlicht wirkungslos — 4, 6, 8 und 12 Threads ergaben 180,2 / 180,2 / 181,4 / 180,8 Sekunden, eine Spanne von 0,7 %. Der native Weg hängt an MPS und Apple Vision, nicht an CPU-Threads. Deshalb setzt weder die systemd-Unit noch die launchd-plist ein `OMP_NUM_THREADS`, und wer dort eine 6 einträgt, gewinnt nichts. `DOCLING_OMP_THREADS` in der `.env` betrifft ausschließlich den Container aus Variante A.

**Unter Linux fällt der Gewinn kleiner aus.** Dort reicht Docker die NVIDIA-Karte sehr wohl in den Container durch: Mit dem CUDA-Image `docling-serve-cu124` (in der [`.env`](.env.example) einstellbar) rechnet auch der Container auf der GPU. Variante C bleibt dort trotzdem sinnvoll — wegen des automatischen Ordner-Abgleichs —, aber der Zeitvorsprung gegenüber Variante A ist bei weitem nicht so groß wie in der Tabelle oben.

**Und was Variante C *nicht* löst:** Sie macht das Warten im Browser nicht kürzer. Open WebUI entkoppelt Upload und Verarbeitung längst von sich aus — der Upload legt die Datei ab und startet einen `BackgroundTask`, die Datei steht bis zum Ende auf `pending`, und man darf den Tab in Ruhe zumachen. Wer also nur den hängenden Ladebalken loswerden will, braucht Variante C nicht. Was C bringt, ist Rechenzeit und ein Ordner, der sich von selbst abgleicht — nicht Entkopplung. Es lohnt, das vor dem Nachbauen ehrlich zu sortieren.

**Zur OCR-Qualität:** Apple Vision und RapidOCR wurden am selben Dokument verglichen. Beide verlesen sich — bei Umlauten, bei Ziffern, in Tabellenzellen —, nur an unterschiedlichen Stellen; ein systematischer Rückstand des einen gegenüber dem anderen war nicht zu erkennen. Deshalb ist in [`scripts/docconvert.py`](scripts/docconvert.py) auch kein OCR-Motor fest verdrahtet: Docling entscheidet selbst (`device='auto'` löst zu `mps` auf, `ocr_engine=auto` wählt `ocrmac`, also Apple Vision). Aus demselben Grund steht dort keine plattformabhängige Fallunterscheidung im Code: Dieselbe Automatik greift unter Linux ebenso, wählt dort aber CUDA und den dort verfügbaren OCR-Motor.

**1. Ordner, Skripte und venv anlegen** — mit eigenem venv, damit der nächtliche Aufruf dieselbe Umgebung nutzt wie die Installation. Unter Linux liegt alles unter `/srv`, unter macOS unter `/opt/heim-ki` (auf dem Mac ist `/srv` wegen des versiegelten Systemvolumes nicht anlegbar):

```bash
# Linux:
sudo mkdir -p /srv/scripts /srv/dokumente
sudo cp scripts/doc-sync.py scripts/docconvert.py scripts/webui_client.py \
        scripts/docscan.py scripts/requirements.txt /srv/scripts/

# Systemaccount für den nächtlichen Lauf:
sudo useradd --system --no-create-home --shell /usr/sbin/nologin heim-ki
sudo install -d -o heim-ki -g heim-ki -m 0750 /srv/heim-ki

# Cache-Verzeichnisse, auf die HOME, HF_HOME und XDG_CACHE_HOME der Unit
# zeigen. Vieles spricht dafür, dass die Bibliotheken sie beim ersten Lauf
# selbst anlegen — nachgewiesen ist es nicht, und der Schritt kostet nichts:
sudo install -d -o heim-ki -g heim-ki -m 0750 \
     /srv/heim-ki/.home /srv/heim-ki/.cache /srv/heim-ki/.cache/huggingface

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
```

```bash
# macOS (Pfade analog, plus Log- und Backup-Verzeichnis für die launchd-Jobs;
# alles gehört danach dem eingeloggten Nutzer, denn die Jobs laufen als
# LaunchAgents in dessen Session — siehe §3, "Unbeaufsichtigter Betrieb". Anders
# als unter Linux gibt es hier keine Rechtegrenze zwischen Login-Nutzer und
# Dienst-Account zu schützen, denn beide sind identisch — "$USER" ist hier
# also unproblematisch, wo es unter Linux ein Fehler wäre):
sudo mkdir -p /opt/heim-ki/scripts /opt/heim-ki/dokumente /opt/heim-ki/logs /opt/heim-ki/backups
sudo chown -R "$USER" /opt/heim-ki
cp scripts/doc-sync.py scripts/docconvert.py scripts/webui_client.py \
   scripts/docscan.py scripts/requirements.txt /opt/heim-ki/scripts/
python3 -m venv /opt/heim-ki/scripts/.venv
/opt/heim-ki/scripts/.venv/bin/pip install -r /opt/heim-ki/scripts/requirements.txt
```

**Alle vier Python-Dateien müssen mit.** `doc-sync.py` ist nur die Ablaufsteuerung; es importiert `docscan` (Dateiauswahl samt Symlink-Schutz), `docconvert` (die Docling-Konvertierung) und `webui_client` (die REST-Aufrufe). Fehlt eines davon, bricht der Lauf beim Import ab. `requirements.txt` zieht `docling>=2.0,<3` und `requests>=2.31` — die Obergrenze bei docling ist Absicht: Ein Major-Sprung könnte die Pipeline-Optionen in `docconvert.py` lautlos umwerfen, und das würde man erst an schlechteren Konvertaten merken.

**2. API-Schlüssel global freischalten** — der Schritt, ohne den Variante C gar nicht erst anfängt. Open WebUI liefert die API-Schlüssel **ab Werk abgeschaltet** aus; in `open_webui/config.py` steht `ENABLE_API_KEYS = os.getenv('ENABLE_API_KEYS', 'False')`. Solange das so bleibt, gibt es in der Oberfläche keinen Schlüssel zu erzeugen, und `doc-sync.py` hat nichts, womit es sich anmelden könnte.

Der Schalter sitzt in den *Admin-Einstellungen* im Bereich *Authentifizierung* und heißt dort **API-Schlüssel** („Erlaubt Benutzern, API-Schlüssel für den programmatischen Zugriff zu erstellen"). Einschalten und speichern.

> **Achtung:** Auch dieser Wert ist *PersistentConfig* — Open WebUI liest ihn nur beim allerersten Start aus der Umgebung und nimmt ihn danach aus der Datenbank (Schlüssel `auth.enable_api_keys`). Ein `ENABLE_API_KEYS=true` in der `.env` oder der Compose-Datei bleibt bei einer bereits laufenden Installation also wirkungslos; die Änderung muss durch die Oberfläche. Nachsehen lässt sich der gespeicherte Stand mit:
>
> ```bash
> docker exec open-webui python3 -c "
> import sqlite3
> db = sqlite3.connect('file:/app/backend/data/webui.db?mode=ro', uri=True)
> for k, v in db.execute(\"select key, value from config where key like 'auth.%api_key%' or key = 'auth.enable_api_keys'\"):
>     print(k, '=', v)"
> ```
>
> Erwartet wird `auth.enable_api_keys = true`. Steht dort `false`, ist der Schalter noch nicht gesetzt — und der nächste Schritt läuft ins Leere.

**3. API-Key in Open WebUI erzeugen:** `doc-sync.py` spricht mit Open WebUI über dessen REST-API und braucht dafür einen persönlichen Schlüssel. In der Oberfläche unter *Einstellungen → Konto* im Abschnitt *API-Schlüssel* einen erzeugen und in die Datei schreiben, die das Skript liest (`WEBUI_API_KEY_FILE`):

```bash
# Linux — die Datei gehört dem Dienst-Account und sonst niemandem:
sudo install -o heim-ki -g heim-ki -m 600 /dev/null /srv/heim-ki/webui-api-key
sudo -u heim-ki tee /srv/heim-ki/webui-api-key >/dev/null <<< 'sk-…'

# macOS — umask in der Subshell, damit die Datei gar nicht erst mit
# offenen Rechten entsteht; ein nachträgliches chmod käme einen Moment zu spät:
(umask 077; printf '%s' 'sk-…' > /opt/heim-ki/webui-api-key)
ls -l /opt/heim-ki/webui-api-key    # erwartet: -rw-------
```

Der Modus `600` ist kein Schmuck: Der Schlüssel liegt im Klartext auf der Platte und trägt die Rechte des Kontos, mit dem er erzeugt wurde. Wer ihn lesen kann, kann in Open WebUI alles, was dieser Nutzer kann — Chats inklusive. (Fehlt der Abschnitt unter *Konto* ganz, ist Schritt 2 noch offen; bei Nicht-Admin-Konten fehlt er zusätzlich, solange die Gruppe nicht das Recht `features.api_keys` hat.)

> **Den Schlüssel deshalb aus einem eigens angelegten, nicht-administrativen Konto ausstellen — nicht aus dem Admin-Konto.** Derselbe Prozess, der den Schlüssel im Klartext hält, lässt auch Docling laufen und parst damit fremde PDFs, DOCX- und HTML-Dateien aus einem Verzeichnis, in das der Login-Nutzer schreiben darf. Dokumentenparser sind die größte Angriffsfläche dieses Aufbaus, und der Schaden eines Fehlers in ihnen soll nicht „vollständiger Lesezugriff auf die Chats aller Haushaltsmitglieder" sein. Also in Open WebUI ein eigenes Konto für den Sync anlegen (normale Rolle, kein Admin), seiner Gruppe das Recht `features.api_keys` geben und den Schlüssel dort erzeugen. Die Wissenssammlung aus Schritt 4 gehört dann diesem Konto und muss den übrigen Nutzern unter *Arbeitsbereich → Wissen* gegebenenfalls noch freigegeben werden.
>
> Unter macOS gibt es zur Linux-Sandbox der systemd-Unit **keine Entsprechung**: Der LaunchAgent läuft als der angemeldete Nutzer mit dessen vollen Rechten, weil er die GPU und den Docker-Socket der Sitzung braucht. Ausgerechnet auf der Plattform, für die dieser Umbau optimiert ist, gibt es für den Punkt oben also gar keine technische Eindämmung — umso wichtiger ist dort das eigene Konto.

**4. Wissenssammlung anlegen:** Das Skript sucht die Sammlung **über ihren Namen** — `KNOWLEDGE_NAME`, Default `Heim-Dokumente`. Entweder legt man sie vorher in der Oberfläche unter *Arbeitsbereich → Wissen* an, oder man überlässt das dem ersten Lauf mit `--create` (Schritt 5).

Findet das Skript die Sammlung nicht und fehlt `--create`, bricht es ab:

```
FEHLER: Sammlung 'Heim-Dokumente' existiert nicht. Mit --create anlegen — oder KNOWLEDGE_NAME auf Tippfehler prüfen.
```

Das ist Absicht und der einzige Grund, warum `--create` überhaupt existiert. Würde das Skript stillschweigend anlegen, was es nicht findet, dann erzeugte ein Tippfehler im Namen — oder ein `KNOWLEDGE_NAME`, das in der Unit anders steht als in der plist — beim nächsten Lauf klaglos eine zweite, leere Sammlung. Im Chat sähe man davon nichts: `#Heim-Dokumente` fände weiterhin die alte, während der Nachtjob fleißig die neue befüllt. Ein Abbruch mit klarer Meldung ist da die freundlichere Variante.

**5. Erster Lauf von Hand.** Dokumente vorher nach `/srv/dokumente` bzw. `/opt/heim-ki/dokumente` legen.

```bash
# Linux — als der Dienst-Account, damit Manifest und Cache gleich die
# richtigen Rechte bekommen. Die Pfade zu Dokumenten, Manifest und Cache sind
# die Defaults aus doc-sync.py und brauchen keine Variablen; HOME, HF_HOME und
# XDG_CACHE_HOME dagegen schon: heim-ki hat wegen "--no-create-home" gar kein
# $HOME, und ohne diese drei würde Doclings Modell-Download entweder in ein
# Verzeichnis laufen, das der Account nicht anlegen darf, oder in den Cache des
# aufrufenden Nutzers — der Dienst lüde dann später alles ein zweites Mal. Es
# sind exakt die Werte aus scripts/systemd/doc-sync.service:
sudo -u heim-ki env \
  HOME=/srv/heim-ki/.home \
  HF_HOME=/srv/heim-ki/.cache/huggingface \
  XDG_CACHE_HOME=/srv/heim-ki/.cache \
  /srv/scripts/.venv/bin/python /srv/scripts/doc-sync.py --create

# macOS — die Defaults im Skript sind die Linux-Pfade, hier also alle setzen.
# Genau diese Werte stehen später auch in der plist:
DOCS_DIR=/opt/heim-ki/dokumente \
STATE_FILE=/opt/heim-ki/doc-sync-state.json \
CACHE_DIR=/opt/heim-ki/cache \
LOCK_FILE=/opt/heim-ki/doc-sync.lock \
WEBUI_URL=http://127.0.0.1:3000 \
WEBUI_API_KEY_FILE=/opt/heim-ki/webui-api-key \
KNOWLEDGE_NAME=Heim-Dokumente \
  /opt/heim-ki/scripts/.venv/bin/python /opt/heim-ki/scripts/doc-sync.py --create
```

Der allererste Lauf lädt zusätzlich Doclings Layout- und Tabellenmodelle über den HuggingFace Hub nach — das dauert spürbar und passiert nur einmal. Genau deshalb stehen die drei Cache-Variablen oben: Sie sorgen dafür, dass der Download unter `/srv/heim-ki` landet, also dort, wo der Dienst ihn später wiederfindet. (Die vollständige Liste der Umgebungsvariablen samt Defaults steht im Kopf von [`scripts/doc-sync.py`](scripts/doc-sync.py); nachlesen ist verlässlicher als raten.)

Dieser Handaufruf ist bewusst nur die Erstbefüllung, kein Test der Einrichtung: Er umgeht die systemd-Unit und damit deren Sandbox, Fehler aus `ProtectSystem=strict` und Verwandten zeigen sich hier also nicht. Der eigentliche Testlauf folgt im nächsten Schritt über `systemctl start`.

Was das Skript dabei tut, und warum:

- Es merkt sich für jede Quelldatei deren SHA-256 in `STATE_FILE`. Unveränderte Dateien überspringt es — der teure Schritt ist die Konvertierung, nicht der Upload. (Open WebUI bringt zwar einen eigenen Abgleich mit, der vergleicht aber die Prüfsumme des *hochgeladenen Markdowns* — die Frage „hat sich die Quelldatei geändert?" beantwortet er also erst, nachdem konvertiert wurde — und genau das ist der teure Schritt.)
- Das Manifest wird nach *jeder* Datei atomar geschrieben (erst Temp-Datei, dann `os.replace`). Ein Abbruch mittendrin kostet damit höchstens die eine gerade laufende Datei, nicht den ganzen Nachtlauf.
- Gelöschte Dateien werden aus der Sammlung entfernt, geänderte ersetzt.
- Der Zielname enthält den ganzen Relativpfad: aus `steuer/2025.pdf` wird `steuer_2025.md`. Gleichnamige Dateien in verschiedenen Unterordnern überschreiben sich dadurch nicht gegenseitig.
- Das fertige Markdown landet zusätzlich unter `CACHE_DIR`, benannt nach dem Hash der Quelle. Wer die Sammlung neu aufbaut, zahlt die Konvertierung nicht ein zweites Mal. **`CACHE_DIR` darf dafür nicht innerhalb von `DOCS_DIR` liegen** — die Konvertate sind Markdown, und Markdown liest der nächste Scan wieder als Quelldatei ein. Das Skript prüft das und bricht mit einer klaren Meldung ab, statt sich selbst zu indexieren.
- Ein `flock` auf `LOCK_FILE` verhindert, dass ein Handaufruf und der Nachtlauf gleichzeitig schreiben.
- Eine kaputte Datei bricht den Lauf nicht ab; sie wird protokolliert, und der Exit-Code ist am Ende ungleich 0.

**6. Nächtlich laufen lassen:**

*Linux* — als systemd-Timer ([`scripts/systemd/`](scripts/systemd/)), 2:00 Uhr; dank `Persistent=true` werden verpasste Läufe nachgeholt, und die Logs landen im journald:

```bash
sudo cp scripts/systemd/doc-sync.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload

# Unit gegenprüfen, bevor sie scharf geschaltet wird — meldet Tippfehler und
# Direktiven, die diese systemd-Version nicht kennt. Diesen Schritt bitte
# wirklich ausführen: die Unit in diesem Repo ist auf einem macOS-Rechner
# entstanden und konnte dort nicht gegen ein echtes systemd geprüft werden.
systemd-analyze verify /etc/systemd/system/doc-sync.service

sudo systemctl enable --now doc-sync.timer

# Logs ansehen:
journalctl -u doc-sync.service
```

**Testlauf:** `sudo systemctl start doc-sync.service`, danach `journalctl -u doc-sync.service -n 50`. Bewusst über die Unit und nicht per direktem Python-Aufruf: Nur so laufen die Sandbox-Direktiven (`ProtectSystem=strict` usw.) mit, und nur so zeigen sich Fehler, die genau diese Sandbox verursacht. Ein `sudo -u heim-ki /srv/scripts/.venv/bin/python …` umgeht die Unit komplett und sähe solche Fehler nie.

> **Warum die Unit kein `PrivateDevices=yes` setzt.** Die Vorgänger-Unit hatte es. Die Direktive blendet `/dev/nvidia*` aus, `torch.cuda.is_available()` liefert dann `False`, und Docling rechnet Layout und OCR auf der CPU — also genau der Zustand, den dieser Umbau abschaffen soll. Die Zeile fehlt deshalb absichtlich und ist in der `.service`-Datei auch so kommentiert. Wer auf einem Host ohne GPU arbeitet oder die Härtung höher gewichtet als die Laufzeit, kann sie ergänzen und nimmt dafür einen deutlich längeren Nachtlauf in Kauf.

Beim allerersten Lauf lädt Docling seine Modelle über HuggingFace nach. Der Dienst läuft als `heim-ki` (`--no-create-home`), hat also gar kein `$HOME`, und `ProtectHome=yes` blendet `/home` ohnehin aus — die Unit lenkt `HOME`, `HF_HOME` und `XDG_CACHE_HOME` deshalb aktiv unter `/srv/heim-ki` um. Das Verzeichnis muss existieren und `heim-ki` gehören (oben mit `install -d -o heim-ki -g heim-ki` erledigt), sonst startet die Unit nicht. Bekannte Einschränkung: Sollte eine künftige docling-Version stattdessen paketrelativ ins venv schreiben wollen, scheitert das unter `ProtectSystem=strict` trotzdem — das ist beim ersten Lauf mit der tatsächlich installierten Version zu prüfen.

*macOS* — als LaunchAgent ([`scripts/launchd/de.heim-ki.doc-sync.plist`](scripts/launchd/de.heim-ki.doc-sync.plist)); die `/opt/heim-ki`-Pfade stehen als `EnvironmentVariables` bereits in der plist:

```bash
mkdir -p ~/Library/LaunchAgents
cp scripts/launchd/de.heim-ki.doc-sync.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/de.heim-ki.doc-sync.plist

# Logs ansehen:
tail -f /opt/heim-ki/logs/doc-sync.log
```

Bewusst ein **LaunchAgent in der Nutzer-Session**, kein root-Daemon: Der Sync braucht um 2:00 Uhr Open WebUI im Container (Docker Desktop/OrbStack laufen nur in einer angemeldeten Session) — und er braucht die GPU, die einem Daemon ohne Session nicht zur Verfügung steht. Letzteres wiegt hier besonders schwer: Ohne MPS ist der ganze Grund für Variante C dahin, und man merkt es nur an der Laufzeit. Also §3, „Unbeaufsichtigter Betrieb" einrichten (automatische Anmeldung, „Start at login", `pmset`), sonst läuft der Nachtjob ins Leere.

(launchd holt einen verpassten Lauf nach, wenn der Mac zur geplanten Zeit nur geschlafen hat — nach einem kompletten Shutdown oder ohne angemeldete Session allerdings nicht.)

**7. Benutzen:** Im Chat die Sammlung mit `#Heim-Dokumente` einbinden und fragen („Was steht in meinem Mietvertrag zur Kündigungsfrist?"). Die Antwort kommt mit Quellenangaben — die Dokumente liegen ja in einer ganz gewöhnlichen Wissenssammlung, und für Open WebUI ist nicht zu unterscheiden, ob sie über den Browser oder über den Nachtjob hineingekommen sind.

> **Hinweis:** Variante C nutzt *nicht* den Docling-Container aus Variante A, sondern die Docling-Python-Bibliothek direkt auf dem Host — das Compose-Profil `rag` braucht sie also nicht. Beide Varianten lassen sich trotzdem parallel betreiben: Sie schreiben in denselben Speicher. Man kann dieselbe Sammlung mischen oder dem Nachtjob über `KNOWLEDGE_NAME` eine eigene geben.

**Quellen:**
- Docling Doku: https://docling-project.github.io/docling/
- Open WebUI RAG-Doku: https://docs.openwebui.com/features/rag
- Ollama Embeddings: https://docs.ollama.com/api

### Migration von Variante B

Wer den früheren Stand dieses Tutorials gebaut hat — eigener Indexer, eigene ChromaDB, Such-Werkzeug im Chat —, räumt so auf. **Zuerst Variante C einrichten und einmal erfolgreich durchlaufen lassen**; danach ist der alte Index entbehrlich.

```bash
# Linux:
sudo systemctl disable --now rag-indexer.timer
sudo rm -f /etc/systemd/system/rag-indexer.{service,timer}
sudo systemctl daemon-reload

# macOS:
launchctl unload -w ~/Library/LaunchAgents/de.heim-ki.rag-indexer.plist
rm -f ~/Library/LaunchAgents/de.heim-ki.rag-indexer.plist

# Beide: der Chroma-Container ist aus den Compose-Dateien verschwunden,
# sein Volume bleibt aber liegen. Es enthält den alten RAG-Index und wird
# nicht mehr gebraucht — löschen ist eine bewusste Entscheidung:
docker rm -f chroma
docker volume rm chroma-data
```

Dazu noch vier Kleinigkeiten, die sonst leise liegenbleiben:

- **Das Werkzeug im Chat:** In Open WebUI unter *Arbeitsbereich → Werkzeuge* das Werkzeug „Heim-Dokumente durchsuchen" löschen. Es zeigt sonst weiter auf eine Datenbank, die es nicht mehr gibt — und das Modell ruft es trotzdem auf, wenn es beim Modell noch aktiviert ist. Die Vorlage `tools/heim_docs_suche.py` ist aus dem Repo entfernt.
- **`.env` aufräumen:** `CHROMA_IMAGE` und `CHROMA_HOST_PORT` sind wirkungslos geworden und können raus; ebenso ein eventuelles `CHROMA_URL` in der eigenen Unit oder plist.
- **Altes Manifest:** `rag-index-state.json` wird von niemandem mehr gelesen. `doc-sync.py` führt sein eigenes (`doc-sync-state.json`) und fängt bei null an — der erste Lauf konvertiert deshalb den kompletten Bestand noch einmal. Danach kann die alte Datei weg — je nach Alter der Installation liegt sie unter `/srv/rag-index-state.json` oder `/srv/heim-ki/rag-index-state.json` (unter macOS analog in `/opt/heim-ki/`):
  ```bash
  sudo rm -f /srv/rag-index-state.json /srv/heim-ki/rag-index-state.json
  ```
- **Backups:** [`scripts/backup.sh`](scripts/backup.sh) sichert das Manifest jetzt als `doc-sync-state-*.json` statt `rag-index-state-*.json`. Das automatische Aufräumen über `KEEP_DAYS` kennt nur noch den neuen Namen — die alten `rag-index-state-*.json` im Backup-Verzeichnis bleiben also liegen, statt nach 14 Tagen zu verschwinden. Neue kommen keine hinzu; wer die vorhandenen loswerden will, löscht sie von Hand.

---

## 8. Ergebnis

- Alle im Haushalt erreichen unter **`http://chat.heim.lan`** eine ChatGPT-ähnliche Oberfläche — ohne Ports, ohne IP-Adressen.
- Die KI kennt die eigenen Dokumente (RAG mit Docling + bge-m3 + Open WebUI) — per Upload im Browser, auf Wunsch zusätzlich als nächtlicher Ordner-Abgleich.
- Braucht man mehr Leistung, startet man die Workstation — Open WebUI erreicht deren Ollama sofort über die in §5/§6 eingerichtete Verbindung `http://host.docker.internal:11435`; Menschen und CLI-Clients im LAN nutzen weiterhin `http://ollama-ws.heim.lan`.
- **Keine Daten fließen „nach Amiland"** — alles bleibt im eigenen LAN.

### Abnahme: hat die Härtung wirklich gegriffen?

Die Absicherungen aus §5–§7 haben eine unangenehme Eigenschaft: Wenn sie *nicht* greifen, merkt man davon im Alltag nichts. Die Oberfläche funktioniert, die Modelle antworten — nur steht die unauthentifizierte Ollama-API weiterhin offen im Netz. Diese acht Prüfungen einmal nach der Einrichtung durchgehen; sie brauchen den echten Host und lassen sich nicht vorab abhaken.

```bash
# 1. NGINX-Konfiguration syntaktisch in Ordnung?
sudo nginx -t

# 2. Ohne Zugangsdaten abgewiesen? Erwartet: 401
curl -si http://ollama.heim.lan/api/tags | head -1

# 3. Mit Zugangsdaten durchgelassen? Erwartet: 200
curl -si -u heim-ki:PASSWORT http://ollama.heim.lan/api/tags | head -1

# 4. Von einem Gerät AUSSERHALB der <LAN-CIDR> aus. Erwartet: 403
curl -si -u heim-ki:PASSWORT http://ollama.heim.lan/api/tags | head -1

# 5. Lauscht Ollama wirklich nur auf Loopback? Erwartet: nur 127.0.0.1,
#    nirgends 0.0.0.0 oder eine LAN-Adresse
sudo ss -ltnp | grep 11434          # Docker-Host
lsof -iTCP:11434 -sTCP:LISTEN       # auf der Mac-Workstation

# 6. Steht der Tunnel, und auf welchen Adressen? Erwartet: 127.0.0.1:11435
#    für NGINX und die docker0-Gateway-Adresse für Open WebUI
sudo ss -ltn | grep 11435
ip -4 addr show docker0             # stimmt die Gateway-Adresse mit §5 überein?

# 7. Erreicht Open WebUI die Workstation? Erwartet: Modellliste
docker exec open-webui curl -sS http://host.docker.internal:11435/api/tags

# 8. Läuft der Dokument-Sync unter voller Sandbox durch? (nur Variante C)
systemd-analyze verify /etc/systemd/system/doc-sync.service
sudo systemctl start doc-sync.service
journalctl -u doc-sync.service -n 50
```

Zu Prüfung 3 und 4: `401` heißt „Zugangsdaten fehlen", `403` heißt „Quell-IP nicht in der Allowlist". Beide Antworten sind gute Nachrichten — sie belegen, dass `satisfy all` greift. Bekommt man an Stelle 2 dagegen eine Modellliste, ist die `auth_basic`-Konfiguration wirkungslos; kommt `500`, fehlt die htpasswd-Datei oder der Pfad in `auth_basic_user_file` stimmt nicht (`nginx -t` merkt das nicht, weil die Datei erst zur Laufzeit geöffnet wird).

Zu Prüfung 8: Der erste Lauf lädt die Docling-Modelle herunter und dauert entsprechend. Er ist der eigentliche Test der Sandbox — ein direkter Aufruf per `sudo -u heim-ki …` umgeht die Unit und würde Schreibfehler, die erst `ProtectSystem=strict` verursacht, gar nicht zeigen. Das vorgeschaltete `systemd-analyze verify` ist hier ebenfalls Teil der Abnahme und nicht bloß Zierde: Die Unit im Repo ist auf einem macOS-Rechner entstanden und dort nie gegen ein echtes systemd gelaufen.

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

**Vor größeren Versionssprüngen** ein Backup ziehen (siehe §11): `sudo /srv/scripts/backup.sh` (macOS: `BACKUP_DIR=/opt/heim-ki/backups STATE_FILE=/opt/heim-ki/doc-sync-state.json /opt/heim-ki/scripts/backup.sh` — ohne sudo, denn als root sähe die docker-CLI den Docker-Desktop-Daemon nicht) — oder einfach den nächtlichen Backup-Timer abwarten.

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

# Auf einem macOS-Host, der zusätzlich per Bonjour erreichbar sein soll
# (§6, Option 4), den .local-Namen mit aufnehmen — sonst meckert der Browser
# beim Aufruf über .local über das Zertifikat:
#   mkcert chat.heim.lan ollama.heim.lan ollama-ws.heim.lan heim-ki.local
# Achtung: Die Dateinamen unten heißen dann "chat.heim.lan+3.pem" statt "+2".

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

Gesichert werden muss, was nicht wiederbeschaffbar ist: das **Open-WebUI-Volume** (Nutzer, Chats, Wissenssammlungen — und damit auch die Vektoren) sowie das **Manifest des Dokument-Sync** (`doc-sync-state.json`, nur bei Variante C). Die Ollama-Modelle sind bewusst ausgenommen — die holt `ollama pull` jederzeit neu, und auch der Markdown-Cache unter `CACHE_DIR` ist kein Backup wert: Er lässt sich jederzeit neu erzeugen, nur eben nicht umsonst.

Das Skript [`scripts/backup.sh`](scripts/backup.sh) erledigt genau das (inklusive Aufräumen alter Stände, Standard: 14 Tage) und läuft täglich um 3:30 Uhr — also nach dem nächtlichen Dokument-Sync. Für das Tar-Packen der Volumes startet es einen kleinen Alpine-Container; das Image ist per `ALPINE_IMAGE` auf einen festen Digest gepinnt (überschreibbar per Umgebungsvariable), damit nicht bei jedem Lauf ein frisches, ungeprüftes `:latest`-Image gezogen wird.

*Linux* — per systemd-Timer:

```bash
sudo mkdir -p /srv/scripts    # existiert schon, falls §7 Variante C eingerichtet wurde
sudo cp scripts/backup.sh /srv/scripts/
sudo chown root:root /srv/scripts/backup.sh
sudo chmod 755 /srv/scripts/backup.sh
sudo cp scripts/systemd/heim-ki-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now heim-ki-backup.timer

# Manuell laufen lassen / Logs:
sudo systemctl start heim-ki-backup.service
journalctl -u heim-ki-backup.service
```

Das Backup bleibt bewusst root, weil es den Docker-Socket braucht — die docker-Gruppe wäre root-äquivalent, brächte also keinen echten Gewinn. Der eigentliche Schutz liegt darin, dass `/srv/scripts` root gehört (§7): Der Login-User kann `backup.sh` nicht verändern, selbst wenn das Skript root-Rechte hat.

*macOS* — per launchd-Job ([`scripts/launchd/de.heim-ki.backup.plist`](scripts/launchd/de.heim-ki.backup.plist), Ziel `/opt/heim-ki/backups`). Auch dieser Job läuft als **LaunchAgent in der Nutzer-Session**, denn die docker-CLI erreicht den Daemon von Docker Desktop/OrbStack nur dort (der Socket liegt unter `~/.docker/run/docker.sock`, nicht unter `/var/run/docker.sock`). Ist Docker nachts nicht erreichbar, bricht `backup.sh` mit Fehler ab, statt still ein leeres Backup zu schreiben — für zuverlässige Nachtläufe also §3, „Unbeaufsichtigter Betrieb" einrichten:

```bash
# Verzeichnisse existieren schon, falls §7 Variante C eingerichtet wurde — sonst:
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

**Restore** (macOS: `docker compose` jeweils mit `-f docker-compose.macos.yml` und dem Backup-Pfad `/opt/heim-ki/backups`):

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

Bei Verdacht, dass eine der Absicherungen aus §5–§7 nicht greift, zuerst die Abnahme-Checkliste in §8 durchgehen — sie grenzt die Ursache meist schneller ein als die Tabelle hier.

| Symptom | Ursache & Abhilfe |
|---|---|
| Container sehen die GPU nicht | Test: `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`. Schlägt das fehl: `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`. Nach einem Treiber-Update: Host neu starten. |
| Antworten plötzlich sehr langsam | `docker exec ollama ollama ps` zeigt, ob das Modell (teilweise) auf der CPU läuft (`XX%/YY% CPU/GPU`). Abhilfe: kleineres/stärker quantisiertes Modell, oder §4-Hinweise (`OLLAMA_MAX_LOADED_MODELS=1`). |
| Erster Prompt „hängt" | Das Modell wird gerade ins VRAM geladen — bei größeren Modellen dauert das. `OLLAMA_KEEP_ALIVE` (§4) verhindert häufiges Neuladen. |
| `nginx: [emerg] invalid parameter "<LAN-CIDR>"` | In der kopierten Konfiguration steht der Platzhalter noch drin (§6). Durch das eigene Netz ersetzen, z. B. `192.168.2.0/24` — solange der Test scheitert, lädt NGINX die ganze Datei nicht, auch `chat.heim.lan` bleibt tot. |
| `403 Forbidden` von `ollama.heim.lan` | Seit der Security-Härtung meist die IP-Allowlist (`allow <LAN-CIDR>; deny all;` in §6) — Zugriff von außerhalb des erlaubten Netzes wird mit 403 abgewiesen; prüfen, von welcher IP der Client kommt. Erst danach kommt Ollamas eigener Host-Header-Schutz in Frage — die Konfiguration aus §6 sendet deshalb `Host 127.0.0.1:11434`; prüfen, ob wirklich die Repo-Konfiguration aktiv ist (`nginx -T \| grep -A5 ollama`). |
| `401 Unauthorized` von `ollama.heim.lan` | Fehlende oder falsche Basic-Auth-Credentials — die Ollama-vHosts verlangen seit der Härtung zusätzlich zur IP-Allowlist `auth_basic` (§6); Zugangsdaten aus der Passwortdatei prüfen bzw. neu setzen (`sudo htpasswd /etc/nginx/heim-ki.htpasswd heim-ki`). |
| Upload scheitert mit `413 Request Entity Too Large` | `client_max_body_size` fehlt/zu klein — in `nginx/heim-ki.conf` enthalten (100 MB), NGINX neu laden. |
| Docling-Container stürzt ab / Host swappt bei großen PDFs | Docling-OCR ist RAM-hungrig. Große Scans aufteilen, oder dem Service in der Compose-Datei ein `mem_limit` geben; notfalls Dokumente einzeln hochladen. |
| `ollama-ws.heim.lan` nach Windows-Neustart tot (WSL-Weg) | Die WSL-IP ist gewandert — Portproxy neu setzen oder auf *mirrored networking* bzw. die native App umstellen (§5). |
| Chat antwortet `"bge-m3:latest" does not support chat` | Im Chat ist das Embedding-Modell als Antwortmodell ausgewählt. Unten im Eingabefeld ein echtes Chat-Modell wählen und die Frage neu senden; damit es nicht wieder passiert, `bge-m3` unter *Admin-Bereich → Einstellungen → Modelle* ausblenden (§7 Variante A). |
| Upload bricht ab mit `Ollama embed error (503): ... maximum pending requests exceeded` | Die Extraktion war erfolgreich, das Einbetten überrennt Ollamas Warteschlange. In den *Admin-Einstellungen → Dokumente* **Embedding Batch Size** auf 64 und **Concurrent Requests** auf 4 setzen (§7 Variante A) — nicht über die `.env`, die Werte kommen aus der Datenbank. |
| Upload bricht ab, `"POST /v1/convert/file" 504` in `docker logs docling` | Die Konvertierung überschreitet `max_sync_wait` von docling-serve (Image-Default 120 s). `DOCLING_SERVE_MAX_SYNC_WAIT` in der `.env` erhöhen (§7 Variante A). Bricht es *sofort* ab und ist das Ergebnis leer, ist eher das PDF defekt — prüfen mit `python3 -c "from pypdf import PdfReader; print(len(PdfReader('datei.pdf').pages))"`; „Stream has ended unexpectedly" heißt: unvollständig heruntergeladen. |
| `port is already allocated` beim Start | Ein anderer Dienst hält den Host-Port. Wer? `lsof -nP -iTCP:<port> -sTCP:LISTEN`, dazu `docker ps --format '{{.Names}}\t{{.Ports}}'`. Achtung: ein Container auf `0.0.0.0:<port>` blockiert auch ein `127.0.0.1:<port>`. |
| Doc-Sync bricht ab: `Sammlung '…' existiert nicht` | Gewollter Abbruch, kein Fehler im Skript (§7 Variante C): Die Sammlung wird über `KNOWLEDGE_NAME` **nach Namen** gesucht. Erst den Namen gegen *Arbeitsbereich → Wissen* prüfen — Groß-/Kleinschreibung und Bindestriche zählen. Ist er wirklich neu, einmalig mit `--create` starten. |
| Doc-Sync läuft, aber der Chat findet nichts | Landen die Dateien in der Sammlung, die der Chat einbindet? (`#Heim-Dokumente` bindet die Sammlung mit *diesem* Namen ein — bei zwei ähnlich benannten erwischt man leicht die falsche.) Logs: Linux `journalctl -u doc-sync.service`, macOS `/opt/heim-ki/logs/doc-sync.log`. |
| Doc-Sync ist nachts viel langsamer als beim Handlauf | Deutet auf fehlenden GPU-Zugriff. Linux: Steht `PrivateDevices=yes` in der Unit? (Gehört dort *nicht* hin, §7 Variante C.) macOS: Der LaunchAgent braucht eine angemeldete Session, sonst fehlt MPS. Im Log nach `Accelerator device:` suchen — steht dort `'cpu'`, ist genau das die Ursache. |
| macOS: Ollama quälend langsam, Mac-Lüfter dreht | Läuft Ollama versehentlich als Container? Unter macOS haben Container **keinen GPU-Zugriff** — Ollama muss nativ laufen (`brew services start ollama`, §3) und Open WebUI über `docker-compose.macos.yml` auf `host.docker.internal:11434` zeigen. |
| macOS: Open WebUI erreicht Ollama nicht | Läuft das native Ollama? (`ollama ps`, `brew services list`). In den Open-WebUI-*Verbindungen* muss `http://host.docker.internal:11434` stehen, nicht `http://ollama:11434` — den Ollama-Service-Namen gibt es in der macOS-Compose-Datei nicht. |
| macOS: Nachtjobs (Doc-Sync/Backup) sind nicht gelaufen | Die Jobs laufen als LaunchAgents nur in einer **angemeldeten Session** mit laufendem Docker Desktop/OrbStack und wachem Mac — §3, „Unbeaufsichtigter Betrieb" (Auto-Login, „Start at login", `pmset`). Status: `launchctl list \| grep heim-ki`; Logs: `/opt/heim-ki/logs/*.log`. |
| Allgemeine Diagnose | `docker compose ps` (Healthchecks!; macOS: mit `-f docker-compose.macos.yml`, der Docling-Dienst zusätzlich mit `--profile rag` — oder einfach `docker ps`), `docker logs open-webui`, `docker stats`; nur Linux: `docker logs ollama`, `nvidia-smi` (macOS: Ollama nativ → `ollama ps`, Logs von `brew services`). |

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
| NGINX Reverse Proxy | https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/ |
| mkcert | https://github.com/FiloSottile/mkcert |
| NVIDIA Container Toolkit | https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html |
| WSL2 | https://learn.microsoft.com/de-de/windows/wsl/install |
| Homebrew (macOS) | https://brew.sh |
| Docker Desktop für Mac | https://docs.docker.com/desktop/setup/install/mac-install/ |
