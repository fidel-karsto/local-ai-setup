# Design: Umsetzung der Security-Review-Empfehlungen

Datum: 2026-08-30
Status: abgestimmt, bereit für den Implementierungsplan
Grundlage: Security-Review des Stands von `main` (Commit `04e558d`)

## Ausgangslage

Der Review fand zwei bestätigte Befunde (beide MEDIUM nach Verifikation) und
einen dritten, der inhaltlich zutrifft, aber unter der Confidence-Schwelle des
Reports blieb:

| # | Befund | Ort |
|---|---|---|
| 1 | Unauthentifizierte Ollama-API LAN-weit per NGINX veröffentlicht; `Host`-Rewrite hebelt Ollamas Rebinding-Schutz aus | `nginx/heim-ki.conf:32-57`, `nginx/heim-ki-https.conf:51-83` |
| 2 | root-systemd-Units führen user-schreibbare Dateien aus (lokale Rechte-Eskalation) | `scripts/systemd/rag-indexer.service:7-9`, `scripts/systemd/heim-ki-backup.service:6-8` |
| 3 | Ollama der Workstation an `0.0.0.0` gebunden, persistent, ohne Auth | `scripts/launchd/de.heim-ki.ollama-ws.plist:34-35` |

Befund 1 ist im Repo bereits als offener Punkt vermerkt
(`REVIEW-UND-PLAN.md:54`, Punkt S4 (c): "Beide Ollama-APIs sind
unauthentifiziert im LAN erreichbar — erwähnenswert") und wurde nie behoben.

Dazu vier kleinere Härtungen, die im Review als LOW eingestuft oder als False
Positive verworfen wurden, aber billig zu beheben sind (Abschnitt 4).

## Leitentscheidungen

1. **Ollama-vHosts bleiben erhalten**, bekommen aber `satisfy all` — Client muss
   aus einem erlaubten Netz kommen *und* Credentials vorweisen.
2. **Workstation bindet an Loopback**, Erreichbarkeit über einen
   SSH-Reverse-Tunnel statt über ein offenes Netzwerk-Interface.
3. **Indexer verliert root**, Backup behält es. Begründung: `backup.sh` braucht
   den Docker-Socket, und die `docker`-Gruppe ist root-äquivalent — ein
   Service-Account brächte dort keinen Sicherheitsgewinn.

---

## 1. NGINX: Ollama-vHosts absichern

Betrifft `nginx/heim-ki.conf` und `nginx/heim-ki-https.conf`, jeweils die beiden
Ollama-`server`-Blöcke. Der `chat.heim.lan`-Block bleibt unverändert — Open WebUI
bringt eigene Authentifizierung mit.

```nginx
satisfy all;
allow <LAN-CIDR>;        # z. B. 192.168.1.0/24 — oder einzelne IPs
deny  all;
auth_basic           "Heim-KI Ollama";
auth_basic_user_file /etc/nginx/heim-ki.htpasswd;

location / {
    proxy_pass http://127.0.0.1:11434;
    proxy_set_header Authorization "";   # Credentials nicht an Ollama weiterreichen
    proxy_set_header Host 127.0.0.1:11434;
    proxy_read_timeout 600s;
    proxy_buffering off;
}
```

### Bewusst beibehalten: der `Host`-Rewrite

`proxy_set_header Host 127.0.0.1:11434;` hebelt Ollamas Schutz gegen
DNS-Rebinding aus. Das bleibt so, ist aber nach dieser Änderung vertretbar: mit
`satisfy all` davor kann ein browsergetriebener Cross-Origin-Request keine
gültigen Credentials beibringen. Diese Abhängigkeit wird als Kommentar in beiden
Dateien festgehalten — wer die Auth später entfernt, muss den Rewrite mitentfernen.

### Warnung: Basic Auth über Klartext-HTTP

`heim-ki.conf` hört auf Port 80. Basic Auth überträgt das Passwort dort
base64-kodiert im Klartext, bei *jedem* Request. Jedes mitlesende Gerät im LAN
kommt so an die Credentials.

Konsequenz für die Doku: die HTTPS-Variante wird als der unterstützte Weg
beschrieben, `heim-ki.conf` bekommt einen deutlichen Warnhinweis im Dateikopf.
Die Datei wird **nicht** entfernt (sie ist der dokumentierte Einstieg vor der
mkcert-Einrichtung).

### Neuer Installationsschritt

```bash
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/heim-ki.htpasswd heim-ki
sudo chown root:www-data /etc/nginx/heim-ki.htpasswd
sudo chmod 640 /etc/nginx/heim-ki.htpasswd
```

---

## 2. Workstation: Loopback + SSH-Reverse-Tunnel

### `scripts/launchd/de.heim-ki.ollama-ws.plist`

- `OLLAMA_HOST`: `0.0.0.0:11434` → `127.0.0.1:11434`
- `StandardOutPath`/`StandardErrorPath`: `/tmp/ollama-ws.log` →
  `/opt/heim-ki/logs/ollama-ws.log` (siehe auch Abschnitt 4)
- Der Kommentarkopf begründet aktuell die `0.0.0.0`-Bindung und muss neu
  geschrieben werden.

Nach dieser Änderung ist Port 11434 auf **keinem** Netzwerk-Interface offen —
im Café-WLAN so wenig wie im Heimnetz.

### Neu: `scripts/launchd/de.heim-ki.ollama-tunnel.plist`

```
ssh -N -T \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -R 127.0.0.1:11435:127.0.0.1:11434 \
    ollama-tunnel@<DOCKER-HOST-IP>
```

`RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=30`. Logs nach
`/opt/heim-ki/logs/ollama-tunnel.log`.

`ExitOnForwardFailure=yes` ist wichtig: ohne die Option bliebe eine
SSH-Verbindung bestehen, deren Portweiterleitung fehlgeschlagen ist, und
launchd merkte nichts davon.

### Docker-Host: eigener Tunnel-Account

Account `ollama-tunnel`, Shell `/usr/sbin/nologin`. Das funktioniert, weil
`ssh -N` kein Kommando ausführt und die Login-Shell folglich nie startet.

`~ollama-tunnel/.ssh/authorized_keys`:

```
restrict,port-forwarding,permitlisten="127.0.0.1:11435" ssh-ed25519 AAAA...
```

`restrict` schaltet alles ab, `port-forwarding` schaltet nur die Weiterleitung
wieder an, `permitlisten` begrenzt sie auf genau einen Port. `permitlisten` ist
das Gegenstück zu `permitopen` für Remote-Forwards (`-R`); `permitopen` allein
würde hier nicht greifen. Der Schlüssel kann damit exakt eine Sache: Port 11435
auf Loopback öffnen.

### NGINX

```nginx
proxy_pass http://127.0.0.1:11435;
```

`<WORKSTATION-IP>` verschwindet damit vollständig aus beiden NGINX-Dateien und
aus den zugehörigen Doku-Abschnitten.

### Akzeptierte Verhaltensänderung

Ist das Notebook aus oder unterwegs, liefert `ollama-ws.heim.lan` künftig
**502 Bad Gateway** statt eines Verbindungsfehlers. Für eine On-Demand-Maschine
ist das korrekt und wird so dokumentiert.

---

## 3. systemd: Indexer entprivilegieren, Backup absichern

### Die eigentliche Ursache: Besitzverhältnisse

`/srv/scripts` wird `root:root 0755`; das venv wird mit `sudo` gebaut statt
nachträglich an den Login-User übereignet. Der Schritt
`sudo chown -R "$USER" /srv/scripts /srv/dokumente` aus den Tutorials entfällt
für `/srv/scripts`. Das allein schließt die Eskalation für **beide** Units —
alles Weitere ist Tiefenstaffelung.

### `rag-indexer.service`

```ini
[Service]
User=heim-ki
Group=heim-ki
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ReadWritePaths=/srv/heim-ki
```

`AF_UNIX` bleibt erlaubt, weil die glibc-Namensauflösung darüber mit `nscd`/
`systemd-resolved` spricht; ohne sie schlägt die DNS-Auflösung je nach
Host-Konfiguration fehl.

Der Account `heim-ki` wird als Systemaccount ohne Login angelegt:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin heim-ki
```

### `heim-ki-backup.service`

Bleibt root. Ergänzt werden `NoNewPrivileges=yes`, `ProtectHome=yes`,
`PrivateTmp=yes` — mehr nicht. Eine Unit mit Zugriff auf den Docker-Socket
härter zu sandboxen erzeugt den Eindruck einer Sicherheitsgrenze, die es nicht
gibt: wer den Socket hat, ist root. Diese Begründung kommt als Kommentar in die
Unit, damit sie nicht später "vervollständigt" wird.

### Verschiebung des State-File

`ProtectSystem=strict` macht das Dateisystem schreibgeschützt. Das Manifest muss
deshalb an einen deklarierten, schreibbaren Ort:

`/srv/rag-index-state.json` → **`/srv/heim-ki/rag-index-state.json`**
(Verzeichnis `heim-ki:heim-ki 0750`)

Das spiegelt die auf macOS bereits bestehende `/opt/heim-ki`-Konvention.
Betroffene Stellen:

- `scripts/rag-indexer.py:22` (Docstring) und `:49` (Default)
- `scripts/backup.sh:7` und `:14` (Kommentare), `:19` (Default)
- `scripts/systemd/rag-indexer.service:13` und
  `scripts/systemd/heim-ki-backup.service` (auskommentierte Beispiele)
- beide Tutorials

**Breaking Change:** Bestehende Installationen müssen die Datei verschieben,
sonst indexiert der nächste Lauf alle Dokumente erneut. Migrationshinweis:

```bash
sudo mkdir -p /srv/heim-ki
sudo mv /srv/rag-index-state.json /srv/heim-ki/
sudo chown -R heim-ki:heim-ki /srv/heim-ki
```

### `/srv/dokumente`

Wird `$USER:heim-ki`, Modus `2750`. Der Login-User behält Schreibrechte, der
Dienst bekommt Leserechte, und das Verzeichnis ist nicht mehr world-readable.
Das setgid-Bit sorgt dafür, dass neu abgelegte Dokumente die Gruppe erben.

---

## 4. Kleinere Härtungen

### `scripts/rag-indexer.py:112-116` — Symlink-Containment

Aktuell dereferenziert `p.is_file()` Symlinks ohne Containment-Prüfung. Ein Link
`notiz.md -> /etc/shadow` im Dokumentenordner landet damit im Index und ist
anschließend über den Chat abrufbar. (`rglob` steigt zwar nicht in verlinkte
*Verzeichnisse* ab, liefert verlinkte *Dateien* aber aus; das Suffix stammt vom
Linknamen, der `SUFFIXES`-Filter greift also nicht.)

Gefiltert wird künftig auf `not p.is_symlink()` und
`p.resolve().is_relative_to(DOCS_DIR.resolve())`.

### `scripts/backup.sh:39` — alpine-Image pinnen

`alpine` ohne Tag, verwendet vom nächtlichen root-Job mit Docker-Zugriff. Wird
auf einen Digest gepinnt (`alpine@sha256:…`), passend zur bereits bestehenden
Pinning-Konvention in `.env.example:4-13`. Kommentar dazu, wie der Digest
aufgefrischt wird.

### ENABLE_SIGNUP — Doku-Korrektur

Die Anweisung in beiden Tutorials, `ENABLE_SIGNUP=false` in `.env` zu setzen und
neu zu starten, ist wirkungslos: Open WebUI liest PersistentConfig-Werte nur beim
ersten Start aus der Umgebung, danach gewinnt der DB-Wert. Korrekt ist der
Toggle in den Admin-Settings. Der Hinweis in `.env.example:20-22` wird
entsprechend präzisiert.

Zusätzlich wird `DEFAULT_USER_ROLE=pending` in `docker-compose.yml` und
`docker-compose.macos.yml` explizit gesetzt, statt sich auf den Upstream-Default
zu verlassen.

---

## 5. Dokumentation

`TUTORIAL_DE.md` und `TUTORIAL_EN.md` werden synchron gepflegt. Neu bzw. geändert:

- Anlegen des `heim-ki`-Accounts, venv-Bau mit `sudo`, entfallender `chown`
- htpasswd-Erstellung und der Klartext-HTTP-Warnhinweis
- SSH-Tunnel: Key-Erzeugung, `authorized_keys`-Zeile, Tunnel-LaunchAgent
- neuer State-File-Pfad inklusive Migrationsabschnitt für Bestandsinstallationen
- 502-Verhalten bei abwesender Workstation

Zwei Aussagen werden durch diese Änderungen falsch und sind zu korrigieren:

1. Die Behauptung, die unauthentifizierte Ollama-API sei nicht im Netz
   erreichbar (`TUTORIAL_EN.md:173` und die deutsche Entsprechung). Sie war
   schon vor dieser Änderung falsch — die vHosts veröffentlichten sie ja.
2. Die beiden Windows-Firewall-Regeln ohne `-RemoteAddress`/`-Profile`
   (Zeile 220 „Ollama" und Zeile 262 „Ollama WSL", in beiden Tutorials), die
   damit auch im Profil "Öffentlich" greifen.

Ebenfalls zu streichen: die Empfehlung `OLLAMA_ORIGINS=*` (Zeile 355 in beiden
Tutorials), die einen browsergetriebenen CSRF-Pfad öffnet. Der dort ebenfalls
genannte `Host`-Rewrite bleibt die empfohlene Lösung (vgl. Abschnitt 1).

`REVIEW-UND-PLAN.md` ist ein historisches Review-Dokument, kein laufender
Tracker. Punkt **S4 (c)** ("Beide Ollama-APIs sind unauthentifiziert im LAN
erreichbar", Zeile 54) bekommt lediglich einen kurzen Verweis darauf, dass er
mit dieser Arbeit erledigt ist — die Tabelle wird nicht umgeschrieben.

---

## Verifikation

| Prüfung | Kommando | Hier ausführbar |
|---|---|---|
| NGINX-Syntax | `nginx -t` | nein (Zielhost) |
| Unit-Syntax | `systemd-analyze verify` | nein (Linux) |
| Sandbox-Bewertung | `systemd-analyze security rag-indexer.service` | nein (Linux) |
| Python | `python -m py_compile scripts/rag-indexer.py` | ja |
| Shell | `bash -n scripts/backup.sh`, `shellcheck` | ja |
| plists | `plutil -lint scripts/launchd/*.plist` | ja |
| Auth greift | `curl` ohne Credentials → 401 | nein (Zielhost) |
| Allowlist greift | `curl` von nicht erlaubter IP → 403 | nein (Zielhost) |
| Symlink wird übersprungen | Testlink in `DOCS_DIR`, Indexer-Lauf | nein (Zielhost) |

Die host-gebundenen Prüfungen kann nur der Betreiber ausführen. Beim Abschluss
wird berichtet, welche Prüfungen tatsächlich gelaufen sind — nicht die Tabelle
als Ganzes als bestanden dargestellt.

## Bewusst nicht Teil dieses Designs

- **Chroma** (`docker-compose.yml:104-105`) ist unauthentifiziert, aber
  loopback-gebunden und nicht per NGINX veröffentlicht. Bleibt innerhalb der
  vorgesehenen Grenze.
- **Keine per-User-Filterung im RAG-Index**: jeder Open-WebUI-Account fragt
  dieselbe `heim-docs`-Collection ab. Das ist für ein Familien-Setup eine
  bewusste Design-Entscheidung, keine Schwachstelle — eine Änderung wäre ein
  eigenes Feature.
- **TLS-Feinheiten** (`ssl_protocols`, HSTS) in `heim-ki-https.conf`: die
  Defaults sind vertretbar.
