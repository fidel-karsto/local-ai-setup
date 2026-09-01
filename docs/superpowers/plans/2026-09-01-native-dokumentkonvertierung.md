# Native Dokumentkonvertierung (Variante C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die teure Docling-Konvertierung verlässt den Container und läuft nativ auf dem Host; das Ergebnis landet als Markdown in der normalen Open-WebUI-Wissenssammlung. Variante B (Chroma + eigener Indexer) wird zurückgebaut.

**Architecture:** Ein Host-Skript `doc-sync.py` scannt einen Ablageordner, konvertiert neue und geänderte Dateien mit der Docling-Bibliothek nativ (GPU + Apple Vision), lädt das Markdown über die Open-WebUI-API hoch und hängt es an eine Wissenssammlung. Ein SHA-256-Manifest über die *Quelldateien* verhindert Doppelarbeit, ein Markdown-Cache rettet teure Konvertate über fehlgeschlagene Uploads.

**Tech Stack:** Python 3 (stdlib `unittest`, kein pytest), `docling>=2.0,<3`, `requests`, Open-WebUI-REST-API v0.11.1, launchd (macOS) / systemd (Linux).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-09-01-native-dokumentkonvertierung-design.md` — bei Widersprüchen gilt die Spec.
- **Sprache:** Code-Kommentare, Docstrings, Log-Ausgaben und Doku auf Deutsch (Ausnahme: `TUTORIAL_EN.md` und `README.md` sind Englisch). Bezeichner wie im Bestand gemischt deutsch/englisch — orientiere dich an `scripts/docscan.py`.
- **Tests:** stdlib `unittest`, ausgeführt mit `python3 -m unittest discover -s tests -v`. Kein pytest, keine neuen Test-Abhängigkeiten.
- **`scripts/docscan.py` bleibt unverändert** und importierbar ohne schwere Abhängigkeiten — die Containment-Prüfung ist sicherheitskritisch und muss ohne venv testbar bleiben.
- **Keine plattformabhängigen Fallunterscheidungen im Konvertierungscode:** Verifiziert wurde, dass Doclings `device='auto'` auf macOS zu `mps` auflöst und `ocr_engine=auto` selbstständig `ocrmac` wählt. Unter Linux wählt dieselbe Einstellung CUDA.
- **Defaults im Code sind die Linux-Pfade** (`/srv/…`); die macOS-Pfade (`/opt/heim-ki/…`) stehen ausschließlich in plist und Doku. Das entspricht dem Vorgehen bei Variante B.
- **Commit-Sprache:** Deutsch, Präfix nach Conventional Commits (`feat:`, `docs:`, `refactor:`, `test:`), Umlaute in Commit-Messages ausgeschrieben (`ae`, `oe`, `ue`) wie im Bestand.

---

### Task 1: `docconvert.py` — Konvertierung nativ

**Files:**
- Create: `scripts/docconvert.py`
- Create: `tests/test_docconvert.py`

**Interfaces:**
- Consumes: nichts aus früheren Tasks.
- Produces: `docconvert.to_markdown(path: pathlib.Path) -> str` — konvertiert eine Datei nach Markdown, wirft bei kaputten Dateien eine Exception durch.

- [ ] **Step 1: Write the failing test**

Erstelle `tests/test_docconvert.py`. Der Test baut sich ein minimales, gültiges PDF selbst zusammen — das vermeidet eine Binärdatei im Repo und eine zusätzliche Abhängigkeit wie `reportlab`. Diese PDF-Bytes sind verifiziert: docling erzeugt daraus `## Hallo Welt`.

```python
"""Test für die native Docling-Konvertierung (scripts/docconvert.py).

Langsam: der erste Aufruf lädt Doclings Layout-Modelle. Deshalb hier nur ein
einziger, minimaler Durchlauf — die Konvertierungsqualität ist Sache von
docling, nicht dieses Repos.

Ausführen:
    python3 -m unittest tests.test_docconvert -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def minimal_pdf(text: str) -> bytes:
    """Ein gültiges einseitiges PDF mit einer Textzeile, von Hand gebaut."""
    inhalt = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("ascii")
    objekte = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(inhalt)).encode() + b" >>\nstream\n" + inhalt + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objekte, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objekte) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objekte) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


class TestDocconvert(unittest.TestCase):
    def test_pdf_wird_zu_markdown(self):
        import docconvert

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "mini.pdf"
            pdf.write_bytes(minimal_pdf("Hallo Welt"))
            markdown = docconvert.to_markdown(pdf)

        self.assertIn("Hallo Welt", markdown)

    def test_kaputte_datei_wirft(self):
        import docconvert

        with tempfile.TemporaryDirectory() as tmp:
            kaputt = Path(tmp) / "kaputt.pdf"
            kaputt.write_bytes(b"das ist kein PDF")
            with self.assertRaises(Exception):
                docconvert.to_markdown(kaputt)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_docconvert -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'docconvert'`

- [ ] **Step 3: Write minimal implementation**

Erstelle `scripts/docconvert.py`:

```python
"""Native Docling-Konvertierung für den Dokument-Sync (scripts/doc-sync.py).

Bewusst ohne Netz- und Zustandslogik: Die Konvertierung ist der teure,
GPU-nahe Teil und soll unabhängig von Open WebUI testbar bleiben.

Beschleuniger und OCR-Motor werden NICHT im Code festgelegt. Doclings
Default `device='auto'` wählt auf macOS MPS und unter Linux CUDA, und
`ocr_engine=auto` wählt auf macOS von selbst `ocrmac` (Apple Vision) — genau
die Kombination, die in der Messung 2,8x schneller war als der Container. Die
Thread-Zahl kommt aus der Umgebung: docling liest OMP_NUM_THREADS selbst
(AcceleratorOptions), gesetzt wird sie in der launchd-plist bzw. der
systemd-Unit.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption


@lru_cache(maxsize=1)
def _converter() -> DocumentConverter:
    """Ein Converter pro Prozess — das Laden der Layout-Modelle dauert Sekunden."""
    optionen = PdfPipelineOptions()
    optionen.do_ocr = True
    optionen.do_table_structure = True
    # Seitenbilder braucht der Sync nicht; sie kosten nur Speicher:
    optionen.generate_page_images = False
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=optionen)}
    )


def to_markdown(path: Path) -> str:
    """Konvertiert eine Datei nach Markdown.

    Wirft durch, wenn docling die Datei nicht lesen kann — der Aufrufer
    entscheidet, ob das einen Lauf abbricht.
    """
    return _converter().convert(path).document.export_to_markdown()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_docconvert -v`
Expected: PASS (2 Tests). Der erste Lauf dauert länger, weil docling seine Modelle lädt.

- [ ] **Step 5: Native Thread-Zahl nachmessen**

Die Spec hält fest, dass die Thread-Kurve nur für den Container vermessen wurde (Optimum 6 von 4/6/8/12). Für den nativen Weg fehlt sie — der gemessene Wert von 181,5 s entstand mit `num_threads=12`.

Nimm ein großes, echtes Dokument aus `DOCS_DIR` (mindestens 100 Seiten, sonst zeigt sich die Kurve nicht — auf 8 Seiten war im Container ausgerechnet der schlechteste Wert der schnellste) und miss:

```bash
for t in 4 6 8 12; do
  OMP_NUM_THREADS=$t python3 -c "
import time, sys
sys.path.insert(0, 'scripts')
import docconvert
t0 = time.time()
md = docconvert.to_markdown(__import__('pathlib').Path('$PFAD_ZUM_DOKUMENT'))
print(f'threads=$t  {time.time()-t0:.1f}s  {len(md)} Zeichen')"
done
```

Trage den schnellsten Wert als `OMP_NUM_THREADS` in die Betriebsdateien aus Task 4 ein. Halte die vier Messwerte fest — sie kommen in Task 7 ins Tutorial.

- [ ] **Step 6: OCR-Qualität gegen den Container abgleichen**

Die Spec hält das als offenen Punkt fest: Nativ arbeitet Apple Vision (`ocrmac`), im Container RapidOCR. Beim 188-Seiten-Scan lieferte nativ 841.999 Zeichen gegen 815.904 im Container — mehr Text, aber ob er *besser* ist, wurde nie geprüft. Vor dem Umstieg auf einen ganzen Bestand ist das eine Stichprobe wert.

Konvertiere dasselbe Dokument auf beiden Wegen und vergleiche zwei, drei Textseiten von Hand:

```bash
# nativ (dieser Code):
python3 -c "
import sys; sys.path.insert(0, 'scripts')
import docconvert, pathlib
pathlib.Path('/tmp/nativ.md').write_text(docconvert.to_markdown(pathlib.Path('$PFAD_ZUM_DOKUMENT')))"

# im Container, zum Vergleich:
docker cp "$PFAD_ZUM_DOKUMENT" docling:/tmp/vergleich.pdf
docker exec docling python3 -c "
from docling.document_converter import DocumentConverter
open('/tmp/container.md','w').write(DocumentConverter().convert('/tmp/vergleich.pdf').document.export_to_markdown())"
docker cp docling:/tmp/container.md /tmp/container.md

diff <(fold -w 80 /tmp/nativ.md) <(fold -w 80 /tmp/container.md) | head -60
```

Achte auf Umlaute, Ziffern und Spaltenumbrüche — dort unterscheiden sich OCR-Motoren am ehesten. Fällt die Qualität nativ schlechter aus, ist das ein Grund, in `docconvert.py` doch einen Motor festzunageln; halte den Befund für Task 7 fest.

- [ ] **Step 7: Commit**

```bash
git add scripts/docconvert.py tests/test_docconvert.py
git commit -m "feat(docconvert): Docling-Konvertierung nativ auf dem Host

Kapselt die Docling-Optionen ohne Netz- und Zustandslogik. Beschleuniger und
OCR-Motor bleiben bewusst auf 'auto': das waehlt auf macOS MPS und ocrmac
(Apple Vision), unter Linux CUDA.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `webui_client.py` — Open-WebUI-API

**Files:**
- Create: `scripts/webui_client.py`
- Create: `tests/test_webui_client.py`

**Interfaces:**
- Consumes: nichts aus früheren Tasks.
- Produces:
  - `webui_client.WebUIError` — Exception bei HTTP-Fehlern
  - `webui_client.WebUIClient(base_url: str, api_key: str, timeout: int = 60)`
  - `.knowledge_id_by_name(name: str) -> str | None`
  - `.create_knowledge(name: str, description: str) -> str`
  - `.knowledge_file_ids(knowledge_id: str) -> set[str]`
  - `.upload_markdown(dateiname: str, text: str) -> str` (liefert die File-ID)
  - `.add_file_to_knowledge(knowledge_id: str, file_id: str) -> None`
  - `.remove_file_from_knowledge(knowledge_id: str, file_id: str) -> None`

Die Endpunkte und Antwortformate sind an Open WebUI v0.11.1 verifiziert:
`GET /api/v1/knowledge/?page=N` und `GET /api/v1/knowledge/{id}/files?page=N` liefern beide `{"items": [...], "total": n}` mit **30 Einträgen pro Seite**; `POST /api/v1/knowledge/create` erwartet `{"name", "description"}` (beide Pflicht); `POST /api/v1/knowledge/{id}/file/add` und `/file/remove` erwarten `{"file_id"}`; `POST /api/v1/files/` nimmt einen Multipart-Teil namens `file`.

- [ ] **Step 1: Write the failing test**

Erstelle `tests/test_webui_client.py`. Der Test startet einen echten HTTP-Server aus der stdlib — damit wird auch der Multipart-Content-Type geprüft, auf dem das ganze Design steht (nur so umgeht der Upload den Docling-Container).

```python
"""Tests für den Open-WebUI-API-Client (scripts/webui_client.py).

Läuft gegen einen Fake-Server aus der stdlib statt gegen Mocks: Der
Content-Type des Multipart-Teils ist die tragende Annahme des Designs
(text/markdown -> Open WebUI nimmt den TextLoader statt Docling), und den
prüft man nur auf dem Draht zuverlässig.

Ausführen:
    python3 -m unittest tests.test_webui_client -v
"""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import webui_client

# Wird pro Test gesetzt: {(methode, pfad-ohne-query): (status, rumpf)}
ANTWORTEN = {}
# Sammelt (methode, pfad-mit-query, headers, rohkörper)
AUFRUFE = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keine Testausgabe zumüllen

    def _antworte(self, methode):
        laenge = int(self.headers.get("Content-Length") or 0)
        rumpf = self.rfile.read(laenge) if laenge else b""
        pfad = self.path.split("?")[0]
        AUFRUFE.append((methode, self.path, dict(self.headers), rumpf))
        status, nutzlast = ANTWORTEN.get((methode, pfad), (404, {"detail": "nicht gefunden"}))
        kodiert = json.dumps(nutzlast).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(kodiert)))
        self.end_headers()
        self.wfile.write(kodiert)

    def do_GET(self):
        self._antworte("GET")

    def do_POST(self):
        self._antworte("POST")


class TestWebUIClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.basis = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        ANTWORTEN.clear()
        AUFRUFE.clear()
        self.client = webui_client.WebUIClient(self.basis, "sk-test")

    def test_sammlung_nach_namen_finden(self):
        ANTWORTEN[("GET", "/api/v1/knowledge/")] = (
            200,
            {"items": [{"id": "k1", "name": "Andere"}, {"id": "k2", "name": "Heim-Dokumente"}], "total": 2},
        )
        self.assertEqual(self.client.knowledge_id_by_name("Heim-Dokumente"), "k2")

    def test_unbekannte_sammlung_liefert_none(self):
        ANTWORTEN[("GET", "/api/v1/knowledge/")] = (200, {"items": [], "total": 0})
        self.assertIsNone(self.client.knowledge_id_by_name("Heim-Dokumente"))

    def test_authorization_header_wird_gesetzt(self):
        ANTWORTEN[("GET", "/api/v1/knowledge/")] = (200, {"items": [], "total": 0})
        self.client.knowledge_id_by_name("egal")
        _, _, headers, _ = AUFRUFE[0]
        self.assertEqual(headers["Authorization"], "Bearer sk-test")

    def test_upload_schickt_text_markdown(self):
        ANTWORTEN[("POST", "/api/v1/files/")] = (200, {"id": "f1"})
        file_id = self.client.upload_markdown("heft.md", "# Inhalt")
        self.assertEqual(file_id, "f1")

        _, _, headers, rumpf = AUFRUFE[0]
        self.assertIn("multipart/form-data", headers["Content-Type"])
        self.assertIn(b"text/markdown", rumpf)
        self.assertIn(b'filename="heft.md"', rumpf)
        self.assertIn("# Inhalt".encode("utf-8"), rumpf)

    def test_datei_zur_sammlung_hinzufuegen(self):
        ANTWORTEN[("POST", "/api/v1/knowledge/k2/file/add")] = (200, {"id": "k2"})
        self.client.add_file_to_knowledge("k2", "f1")
        _, _, _, rumpf = AUFRUFE[0]
        self.assertEqual(json.loads(rumpf), {"file_id": "f1"})

    def test_datei_aus_sammlung_entfernen(self):
        ANTWORTEN[("POST", "/api/v1/knowledge/k2/file/remove")] = (200, {"id": "k2"})
        self.client.remove_file_from_knowledge("k2", "f1")
        _, _, _, rumpf = AUFRUFE[0]
        self.assertEqual(json.loads(rumpf), {"file_id": "f1"})

    def test_dateiliste_ueber_mehrere_seiten(self):
        # 30 Einträge pro Seite: der Client muss weiterblättern, bis er
        # "total" beisammen hat, sonst fehlen ab Dokument 31 die IDs und der
        # Sync lädt sie bei jedem Lauf erneut hoch.
        seiten = [
            {"items": [{"id": f"f{i}"} for i in range(30)], "total": 31},
            {"items": [{"id": "f30"}], "total": 31},
        ]

        class MehrseitigerHandler(Handler):
            def _antworte(self, methode):
                laenge = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(laenge) if laenge else b""
                AUFRUFE.append((methode, self.path, dict(self.headers), b""))
                seite = int(self.path.split("page=")[1].split("&")[0])
                kodiert = json.dumps(seiten[seite - 1]).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(kodiert)))
                self.end_headers()
                self.wfile.write(kodiert)

        server = ThreadingHTTPServer(("127.0.0.1", 0), MehrseitigerHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            client = webui_client.WebUIClient(f"http://127.0.0.1:{server.server_address[1]}", "sk-test")
            ids = client.knowledge_file_ids("k2")
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(len(ids), 31)
        self.assertIn("f30", ids)

    def test_fehlerstatus_wirft_webui_error(self):
        ANTWORTEN[("POST", "/api/v1/files/")] = (401, {"detail": "Not authenticated"})
        with self.assertRaises(webui_client.WebUIError) as ctx:
            self.client.upload_markdown("heft.md", "# Inhalt")
        self.assertIn("401", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_webui_client -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'webui_client'`

- [ ] **Step 3: Write minimal implementation**

Erstelle `scripts/webui_client.py`:

```python
"""Dünner Client für die Open-WebUI-REST-API (scripts/doc-sync.py).

Weiß nichts von Docling und nichts vom Manifest — nur HTTP. Verifiziert
gegen Open WebUI v0.11.1.
"""
from __future__ import annotations

import requests

# Open WebUI liefert Listen mit 30 Einträgen pro Seite (PAGE_ITEM_COUNT).
# Der Client verlässt sich nicht auf diesen Wert, sondern blättert, bis
# "total" beisammen ist — dann bleibt er auch bei einer Änderung heil.


class WebUIError(RuntimeError):
    """Open WebUI hat mit einem Fehlerstatus geantwortet."""


class WebUIClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 60):
        self.basis = base_url.rstrip("/")
        self.kopf = {"Authorization": f"Bearer {api_key}"}
        self.timeout = timeout

    # --- innen -------------------------------------------------------------

    def _pruefe(self, antwort: requests.Response, was: str) -> dict:
        if not antwort.ok:
            raise WebUIError(f"{was}: HTTP {antwort.status_code} — {antwort.text[:200]}")
        return antwort.json()

    def _get(self, pfad: str, params: dict | None = None) -> dict:
        antwort = requests.get(
            f"{self.basis}{pfad}", headers=self.kopf, params=params, timeout=self.timeout
        )
        return self._pruefe(antwort, f"GET {pfad}")

    def _post(self, pfad: str, nutzlast: dict) -> dict:
        antwort = requests.post(
            f"{self.basis}{pfad}", headers=self.kopf, json=nutzlast, timeout=self.timeout
        )
        return self._pruefe(antwort, f"POST {pfad}")

    def _alle_seiten(self, pfad: str) -> list[dict]:
        gesammelt: list[dict] = []
        seite = 1
        while True:
            daten = self._get(pfad, {"page": seite})
            eintraege = daten.get("items", [])
            gesammelt.extend(eintraege)
            if not eintraege or len(gesammelt) >= daten.get("total", 0):
                return gesammelt
            seite += 1

    # --- außen -------------------------------------------------------------

    def knowledge_id_by_name(self, name: str) -> str | None:
        for eintrag in self._alle_seiten("/api/v1/knowledge/"):
            if eintrag.get("name") == name:
                return eintrag["id"]
        return None

    def create_knowledge(self, name: str, description: str) -> str:
        # Beide Felder sind in KnowledgeForm Pflicht.
        return self._post(
            "/api/v1/knowledge/create", {"name": name, "description": description}
        )["id"]

    def knowledge_file_ids(self, knowledge_id: str) -> set[str]:
        return {
            eintrag["id"]
            for eintrag in self._alle_seiten(f"/api/v1/knowledge/{knowledge_id}/files")
        }

    def upload_markdown(self, dateiname: str, text: str) -> str:
        """Lädt Text als Markdown hoch und liefert die File-ID.

        Der Content-Type text/markdown ist der Kern des Ganzen: Open WebUIs
        _is_text_file() greift darüber und nimmt den TextLoader — der
        Docling-Container wird gar nicht erst gefragt.
        """
        antwort = requests.post(
            f"{self.basis}/api/v1/files/",
            headers=self.kopf,
            files={"file": (dateiname, text.encode("utf-8"), "text/markdown")},
            timeout=self.timeout,
        )
        return self._pruefe(antwort, "POST /api/v1/files/")["id"]

    def add_file_to_knowledge(self, knowledge_id: str, file_id: str) -> None:
        self._post(f"/api/v1/knowledge/{knowledge_id}/file/add", {"file_id": file_id})

    def remove_file_from_knowledge(self, knowledge_id: str, file_id: str) -> None:
        self._post(f"/api/v1/knowledge/{knowledge_id}/file/remove", {"file_id": file_id})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_webui_client -v`
Expected: PASS (8 Tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/webui_client.py tests/test_webui_client.py
git commit -m "feat(webui-client): API-Client fuer Open-WebUI-Wissenssammlungen

Laedt Markdown als text/markdown hoch, damit Open WebUI den TextLoader nimmt
und den Docling-Container gar nicht erst fragt. Tests laufen gegen einen
Fake-Server aus der stdlib, damit der Content-Type auf dem Draht geprueft wird.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `doc-sync.py` — Orchestrierung

**Files:**
- Create: `scripts/doc-sync.py`
- Create: `tests/test_doc_sync.py`

**Interfaces:**
- Consumes: `docscan.scan()`, `docconvert.to_markdown()` (Task 1), `webui_client.WebUIClient` (Task 2).
- Produces: `sync(...)` als testbarer Kern und `main()` als Umgebungs-Verdrahtung.
  - `doc_sync.LaufLaeuftBereits` — Exception, wenn das Lockfile belegt ist
  - `doc_sync.lauf_sperre(pfad: Path)` — Kontextmanager
  - `doc_sync.zielname(rel: str) -> str`
  - `doc_sync.sync(*, docs_dir: Path, state_file: Path, cache_dir: Path, client, knowledge_id: str, convert) -> int` (liefert die Zahl der Fehler)

Der Modulname enthält einen Bindestrich, ist also nicht direkt importierbar. Der Test lädt ihn deshalb über `importlib.util.spec_from_file_location` — genau wie ein Aufrufer, der die Datei als Skript startet.

- [ ] **Step 1: Write the failing test**

Erstelle `tests/test_doc_sync.py`:

```python
"""Tests für die Sync-Orchestrierung (scripts/doc-sync.py).

Konvertierung und HTTP sind hier durch Fakes ersetzt: geprüft wird die
Buchführung — was wird übersprungen, was neu hochgeladen, was entfernt.

Ausführen:
    python3 -m unittest tests.test_doc_sync -v
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("doc_sync", SCRIPTS / "doc-sync.py")
doc_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doc_sync)


class FakeClient:
    """Merkt sich den Zustand der Sammlung im Speicher."""

    def __init__(self, vorhandene_ids=()):
        self.dateien = {}  # file_id -> (dateiname, text)
        self.in_sammlung = set(vorhandene_ids)
        self.entfernt = []
        self.zaehler = 0
        self.upload_faellt_aus = False

    def knowledge_file_ids(self, knowledge_id):
        return set(self.in_sammlung)

    def upload_markdown(self, dateiname, text):
        if self.upload_faellt_aus:
            raise RuntimeError("Upload abgelehnt")
        self.zaehler += 1
        file_id = f"f{self.zaehler}"
        self.dateien[file_id] = (dateiname, text)
        return file_id

    def add_file_to_knowledge(self, knowledge_id, file_id):
        self.in_sammlung.add(file_id)

    def remove_file_from_knowledge(self, knowledge_id, file_id):
        self.entfernt.append(file_id)
        self.in_sammlung.discard(file_id)


class SyncTestBasis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        wurzel = Path(self._tmp.name)
        self.docs = wurzel / "dokumente"
        self.docs.mkdir()
        self.state = wurzel / "state.json"
        self.cache = wurzel / "cache"
        self.konvertierungen = []

    def tearDown(self):
        self._tmp.cleanup()

    def konvertiere(self, pfad):
        self.konvertierungen.append(pfad.name)
        return f"# {pfad.stem}"

    def lauf(self, client):
        return doc_sync.sync(
            docs_dir=self.docs,
            state_file=self.state,
            cache_dir=self.cache,
            client=client,
            knowledge_id="k1",
            convert=self.konvertiere,
        )

    def manifest(self):
        return json.loads(self.state.read_text(encoding="utf-8"))


class TestSync(SyncTestBasis):
    def test_neue_datei_wird_konvertiert_und_hochgeladen(self):
        (self.docs / "heft.pdf").write_bytes(b"%PDF-")
        client = FakeClient()

        fehler = self.lauf(client)

        self.assertEqual(fehler, 0)
        self.assertEqual(self.konvertierungen, ["heft.pdf"])
        self.assertEqual(client.dateien["f1"], ("heft.md", "# heft"))
        self.assertEqual(client.in_sammlung, {"f1"})
        self.assertEqual(self.manifest()["heft.pdf"]["file_id"], "f1")

    def test_unveraenderte_datei_wird_uebersprungen(self):
        (self.docs / "heft.pdf").write_bytes(b"%PDF-")
        client = FakeClient()
        self.lauf(client)
        self.konvertierungen.clear()

        fehler = self.lauf(client)

        self.assertEqual(fehler, 0)
        self.assertEqual(self.konvertierungen, [])
        self.assertEqual(client.zaehler, 1)

    def test_geaenderte_datei_ersetzt_die_alte(self):
        pfad = self.docs / "heft.pdf"
        pfad.write_bytes(b"%PDF-")
        client = FakeClient()
        self.lauf(client)

        pfad.write_bytes(b"%PDF- anders")
        fehler = self.lauf(client)

        self.assertEqual(fehler, 0)
        self.assertEqual(client.entfernt, ["f1"])
        self.assertEqual(client.in_sammlung, {"f2"})
        self.assertEqual(self.manifest()["heft.pdf"]["file_id"], "f2")

    def test_geloeschte_datei_verschwindet_aus_sammlung_und_manifest(self):
        pfad = self.docs / "heft.pdf"
        pfad.write_bytes(b"%PDF-")
        client = FakeClient()
        self.lauf(client)

        pfad.unlink()
        fehler = self.lauf(client)

        self.assertEqual(fehler, 0)
        self.assertEqual(client.entfernt, ["f1"])
        self.assertEqual(self.manifest(), {})

    def test_serverseitig_fehlende_datei_wird_neu_hochgeladen(self):
        (self.docs / "heft.pdf").write_bytes(b"%PDF-")
        client = FakeClient()
        self.lauf(client)

        # Sammlung in der Oberfläche geleert — das Manifest weiß noch von f1:
        client.in_sammlung.clear()
        self.konvertierungen.clear()
        fehler = self.lauf(client)

        self.assertEqual(fehler, 0)
        self.assertEqual(client.in_sammlung, {"f2"})
        # Der Cache erspart die teure Konvertierung:
        self.assertEqual(self.konvertierungen, [])

    def test_cache_ueberlebt_gescheiterten_upload(self):
        (self.docs / "heft.pdf").write_bytes(b"%PDF-")
        client = FakeClient()
        client.upload_faellt_aus = True

        fehler = self.lauf(client)
        self.assertEqual(fehler, 1)
        self.assertEqual(self.konvertierungen, ["heft.pdf"])
        self.assertEqual(self.manifest(), {})

        client.upload_faellt_aus = False
        self.konvertierungen.clear()
        fehler = self.lauf(client)

        self.assertEqual(fehler, 0)
        self.assertEqual(self.konvertierungen, [])  # aus dem Cache
        self.assertEqual(client.in_sammlung, {"f1"})

    def test_kaputte_datei_bricht_den_lauf_nicht_ab(self):
        (self.docs / "kaputt.pdf").write_bytes(b"%PDF-")
        (self.docs / "gut.pdf").write_bytes(b"%PDF-")
        client = FakeClient()

        def konvertiere(pfad):
            if pfad.name == "kaputt.pdf":
                raise ValueError("unlesbar")
            return f"# {pfad.stem}"

        fehler = doc_sync.sync(
            docs_dir=self.docs,
            state_file=self.state,
            cache_dir=self.cache,
            client=client,
            knowledge_id="k1",
            convert=konvertiere,
        )

        self.assertEqual(fehler, 1)
        self.assertEqual(client.in_sammlung, {"f1"})
        self.assertIn("gut.pdf", self.manifest())
        self.assertNotIn("kaputt.pdf", self.manifest())

    def test_zielname_macht_unterordner_eindeutig(self):
        self.assertEqual(doc_sync.zielname("steuer/2025.pdf"), "steuer_2025.md")
        self.assertEqual(doc_sync.zielname("heft.pdf"), "heft.md")

    def test_cache_im_dokumentenordner_wird_abgelehnt(self):
        # docscan.SUFFIXES enthält ".md" — läge der Cache unter DOCS_DIR,
        # würde der nächste Lauf die eigenen Konvertate als Dokumente
        # einlesen und daraus wieder Konvertate machen.
        (self.docs / "heft.pdf").write_bytes(b"%PDF-")
        with self.assertRaises(ValueError):
            doc_sync.sync(
                docs_dir=self.docs,
                state_file=self.state,
                cache_dir=self.docs / "cache",
                client=FakeClient(),
                knowledge_id="k1",
                convert=self.konvertiere,
            )


class TestLaufSperre(unittest.TestCase):
    def test_zweiter_lauf_wird_abgewiesen(self):
        with tempfile.TemporaryDirectory() as tmp:
            sperre = Path(tmp) / "doc-sync.lock"
            with doc_sync.lauf_sperre(sperre):
                with self.assertRaises(doc_sync.LaufLaeuftBereits):
                    with doc_sync.lauf_sperre(sperre):
                        pass

    def test_sperre_wird_wieder_freigegeben(self):
        with tempfile.TemporaryDirectory() as tmp:
            sperre = Path(tmp) / "doc-sync.lock"
            with doc_sync.lauf_sperre(sperre):
                pass
            with doc_sync.lauf_sperre(sperre):
                pass  # darf nicht werfen


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_doc_sync -v`
Expected: FAIL mit `FileNotFoundError` beim Laden von `scripts/doc-sync.py`

- [ ] **Step 3: Write minimal implementation**

Erstelle `scripts/doc-sync.py`:

```python
#!/usr/bin/env python3
"""Dokument-Sync: Docling nativ -> Markdown -> Open-WebUI-Wissenssammlung.

Siehe docs/superpowers/specs/2026-09-01-native-dokumentkonvertierung-design.md.

Warum nicht Open WebUIs eigener Abgleich (POST /knowledge/{id}/sync/diff):
Der vergleicht meta.file_hash, also die Prüfsumme des HOCHGELADENEN Markdowns.
Die Frage "hat sich die Quell-PDF geändert?" beantwortet er damit erst,
nachdem konvertiert wurde — und genau das ist der teure Schritt. Deshalb hier
ein eigenes Manifest über die Quelldateien.

Konfiguration über Umgebungsvariablen (Default in Klammern):
  DOCS_DIR            (/srv/dokumente)                    Ablageordner
  STATE_FILE          (/srv/heim-ki/doc-sync-state.json)  Manifest
  CACHE_DIR           (/srv/heim-ki/cache)                Markdown-Cache
  LOCK_FILE           (/srv/heim-ki/doc-sync.lock)        Lauf-Sperre
  WEBUI_URL           (http://127.0.0.1:3000)             Open WebUI
  WEBUI_API_KEY_FILE  (/srv/heim-ki/webui-api-key)        API-Key (Modus 0600)
  KNOWLEDGE_NAME      (Heim-Dokumente)                    Zielsammlung

Aufruf von Hand (zusätzlich zum nächtlichen Lauf):
  /srv/scripts/.venv/bin/python /srv/scripts/doc-sync.py
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import docscan

DOCS_DIR = Path(os.environ.get("DOCS_DIR", "/srv/dokumente"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "/srv/heim-ki/doc-sync-state.json"))
CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/srv/heim-ki/cache"))
LOCK_FILE = Path(os.environ.get("LOCK_FILE", "/srv/heim-ki/doc-sync.lock"))
WEBUI_URL = os.environ.get("WEBUI_URL", "http://127.0.0.1:3000")
WEBUI_API_KEY_FILE = Path(
    os.environ.get("WEBUI_API_KEY_FILE", "/srv/heim-ki/webui-api-key")
)
KNOWLEDGE_NAME = os.environ.get("KNOWLEDGE_NAME", "Heim-Dokumente")


class LaufLaeuftBereits(RuntimeError):
    """Ein anderer doc-sync-Lauf hält die Sperre."""


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


@contextmanager
def lauf_sperre(pfad: Path):
    """Verhindert, dass Nachtlauf und Handaufruf gleichzeitig schreiben."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with pfad.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LaufLaeuftBereits(
                f"Ein anderer Lauf hält bereits {pfad} — dieser Lauf endet."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def zielname(rel: str) -> str:
    """'steuer/2025.pdf' -> 'steuer_2025.md'.

    Der ganze Relativpfad fließt ein, damit gleichnamige Dateien aus
    verschiedenen Unterordnern nicht denselben Namen bekommen.
    """
    return Path(rel).with_suffix(".md").as_posix().replace("/", "_")


def load_state(state_file: Path) -> dict:
    if state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {}


def save_state(state_file: Path, state: dict) -> None:
    # Erst in eine Temp-Datei, dann atomar ersetzen — ein Absturz mittendrin
    # hinterlässt so nie ein kaputtes Manifest.
    state_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(state_file.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_name, state_file)


def markdown_holen(cache_dir: Path, digest: str, path: Path, convert) -> str:
    """Konvertat aus dem Cache oder frisch — der Cache ist reiner Beschleuniger."""
    cache_datei = cache_dir / f"{digest}.md"
    if cache_datei.exists():
        return cache_datei.read_text(encoding="utf-8")
    markdown = convert(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_datei.write_text(markdown, encoding="utf-8")
    return markdown


def sync(*, docs_dir: Path, state_file: Path, cache_dir: Path, client,
         knowledge_id: str, convert) -> int:
    """Gleicht docs_dir gegen die Sammlung ab. Liefert die Zahl der Fehler."""
    # docscan.SUFFIXES enthält ".md": läge der Cache unter docs_dir, würde der
    # nächste Lauf die eigenen Konvertate als Dokumente einlesen.
    aufgeloest_docs = docs_dir.resolve()
    aufgeloest_cache = cache_dir.resolve()
    if aufgeloest_cache == aufgeloest_docs or aufgeloest_docs in aufgeloest_cache.parents:
        raise ValueError(
            f"CACHE_DIR ({cache_dir}) darf nicht in DOCS_DIR ({docs_dir}) liegen — "
            f"die Konvertate würden sich sonst selbst indexieren."
        )

    state = load_state(state_file)
    current = docscan.scan(docs_dir)
    in_sammlung = client.knowledge_file_ids(knowledge_id)

    # 1) Dateien, die es nicht mehr gibt
    for rel in sorted(set(state) - set(current)):
        file_id = state[rel]["file_id"]
        if file_id in in_sammlung:
            client.remove_file_from_knowledge(knowledge_id, file_id)
        del state[rel]
        save_state(state_file, state)
        log(f"Entfernt (Datei gelöscht): {rel}")

    # 2) Neue und geänderte Dateien
    fehler = 0
    for rel, path in current.items():
        try:
            digest = sha256_file(path)
            eintrag = state.get(rel)
            if eintrag and eintrag["sha256"] == digest and eintrag["file_id"] in in_sammlung:
                continue

            if eintrag and eintrag["file_id"] in in_sammlung:
                client.remove_file_from_knowledge(knowledge_id, eintrag["file_id"])

            markdown = markdown_holen(cache_dir, digest, path, convert)
            file_id = client.upload_markdown(zielname(rel), markdown)
            client.add_file_to_knowledge(knowledge_id, file_id)

            state[rel] = {"sha256": digest, "file_id": file_id}
            save_state(state_file, state)
            log(f"Übernommen: {rel} ({len(markdown)} Zeichen)")
        except Exception as exc:
            fehler += 1
            log(f"FEHLER bei {rel}: {exc}")

    log(f"Fertig. {len(current)} Dateien im Bestand, {fehler} Fehler.")
    return fehler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--create",
        action="store_true",
        help="Die Wissenssammlung anlegen, falls sie noch nicht existiert.",
    )
    args = parser.parse_args()

    if not DOCS_DIR.is_dir():
        log(f"FEHLER: Dokumentenordner {DOCS_DIR} existiert nicht.")
        return 1
    if not WEBUI_API_KEY_FILE.is_file():
        log(f"FEHLER: API-Key-Datei {WEBUI_API_KEY_FILE} fehlt.")
        return 1

    # Erst hier importieren: beide Module ziehen schwere Abhängigkeiten, und
    # die Fehlermeldungen oben sollen auch ohne venv lesbar sein.
    import docconvert
    import webui_client

    api_key = WEBUI_API_KEY_FILE.read_text(encoding="utf-8").strip()
    client = webui_client.WebUIClient(WEBUI_URL, api_key)

    try:
        knowledge_id = client.knowledge_id_by_name(KNOWLEDGE_NAME)
        if knowledge_id is None:
            if not args.create:
                log(
                    f"FEHLER: Sammlung {KNOWLEDGE_NAME!r} existiert nicht. "
                    f"Mit --create anlegen — oder KNOWLEDGE_NAME auf Tippfehler prüfen."
                )
                return 1
            knowledge_id = client.create_knowledge(
                KNOWLEDGE_NAME, "Automatisch befüllt von doc-sync.py"
            )
            log(f"Sammlung {KNOWLEDGE_NAME!r} angelegt ({knowledge_id}).")

        with lauf_sperre(LOCK_FILE):
            fehler = sync(
                docs_dir=DOCS_DIR,
                state_file=STATE_FILE,
                cache_dir=CACHE_DIR,
                client=client,
                knowledge_id=knowledge_id,
                convert=docconvert.to_markdown,
            )
    except LaufLaeuftBereits as exc:
        log(str(exc))
        return 1
    except webui_client.WebUIError as exc:
        log(f"FEHLER: Open WebUI nicht erreichbar oder lehnt ab — {exc}")
        return 1

    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_doc_sync -v`
Expected: PASS (11 Tests)

- [ ] **Step 5: Gesamte Testsuite laufen lassen**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — inklusive der unveränderten `test_docscan`-Tests.

- [ ] **Step 6: Commit**

```bash
git add scripts/doc-sync.py tests/test_doc_sync.py
git commit -m "feat(doc-sync): Orchestrierung von Konvertierung und Upload

Manifest ueber die Quelldateien (nicht ueber die Konvertate), Markdown-Cache
gegen verlorene OCR-Zeit bei gescheiterten Uploads, Lockfile gegen die
Kollision von Nachtlauf und Handaufruf, Abgleich gegen die tatsaechliche
Sammlung gegen Drift.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Betriebsdateien

**Files:**
- Modify: `scripts/requirements.txt`
- Create: `scripts/systemd/doc-sync.service`
- Create: `scripts/systemd/doc-sync.timer`
- Create: `scripts/launchd/de.heim-ki.doc-sync.plist`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `scripts/doc-sync.py` (Task 3) und die Umgebungsvariablen aus dessen Kopf.
- Produces: nichts für spätere Tasks außer den Dateinamen, die Task 7 und 8 in der Doku nennen.

Setze in Unit und plist den in Task 1, Step 5 gemessenen Wert für `OMP_NUM_THREADS` ein. Unten steht `6` als Platzhalterwert aus der Container-Messung — **er ist durch dein Messergebnis zu ersetzen**.

- [ ] **Step 1: `requirements.txt` anpassen**

Variante B fällt weg, damit auch `chromadb` und `ollama`; `requests` kommt dazu. Ersetze den Inhalt von `scripts/requirements.txt`:

```
# Abhängigkeiten des Dokument-Sync (scripts/doc-sync.py)
# Installation:
#   python3 -m venv /srv/scripts/.venv
#   /srv/scripts/.venv/bin/pip install -r requirements.txt
# docling < 3: ein Major-Sprung könnte die Pipeline-Optionen in
# scripts/docconvert.py lautlos umwerfen.
docling>=2.0,<3
requests>=2.31
```

- [ ] **Step 2: systemd-Unit anlegen**

Erstelle `scripts/systemd/doc-sync.service`. Entscheidender Unterschied zur alten `rag-indexer.service`: **kein `PrivateDevices=yes`** — das hatte dem Dienst `/dev/nvidia*` genommen, und ohne GPU ist der ganze Sinn dieses Umbaus dahin.

```ini
[Unit]
Description=Dokument-Sync: Docling nativ -> Open-WebUI-Wissenssammlung
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
# Läuft bewusst NICHT als root: gebraucht werden nur Lesezugriff auf
# /srv/dokumente und HTTP zu Open WebUI. Den Account anlegen mit:
#   sudo useradd --system --no-create-home --shell /usr/sbin/nologin heim-ki
User=heim-ki
Group=heim-ki
ExecStart=/srv/scripts/.venv/bin/python /srv/scripts/doc-sync.py
Nice=10

# Gemessenes Optimum eintragen (siehe Tutorial §7): mehr Threads sind NICHT
# besser — im Container war 12 langsamer als 4.
Environment=OMP_NUM_THREADS=6

# --- Sandbox ---------------------------------------------------------------
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
# ACHTUNG: Hier steht bewusst KEIN PrivateDevices=yes. Die Vorgänger-Unit
# (rag-indexer.service) hatte es, und es verbarg /dev/nvidia* vor dem Dienst —
# torch.cuda.is_available() lieferte False und Docling rechnete auf der CPU.
# Genau das soll dieser Umbau abstellen. Wer die GPU nicht braucht, kann die
# Zeile ergänzen und nimmt dafür einen deutlich längeren Lauf in Kauf.
ProtectKernelTunables=yes
ProtectControlGroups=yes
# AF_UNIX wird für die Namensauflösung über nscd/systemd-resolved gebraucht:
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
# Muss existieren und heim-ki gehören, sonst startet die Unit nicht:
#   sudo install -d -o heim-ki -g heim-ki -m 0750 /srv/heim-ki
ReadWritePaths=/srv/heim-ki

# docling lädt seine Layout- und OCR-Modelle beim ersten Lauf über den
# HuggingFace Hub nach — per Default nach $HOME/.cache. Für heim-ki
# (--no-create-home) existiert kein $HOME, und ProtectHome=yes blendet /home
# ohnehin aus. Deshalb aktiv unter den freigegebenen ReadWritePaths umlenken:
Environment=HOME=/srv/heim-ki/.home
Environment=HF_HOME=/srv/heim-ki/.cache/huggingface
Environment=XDG_CACHE_HOME=/srv/heim-ki/.cache

# Konfiguration bei Bedarf (Defaults siehe Kopf von doc-sync.py):
# Environment=DOCS_DIR=/srv/dokumente
# Environment=STATE_FILE=/srv/heim-ki/doc-sync-state.json
# Environment=CACHE_DIR=/srv/heim-ki/cache
# Environment=WEBUI_URL=http://127.0.0.1:3000
# Environment=WEBUI_API_KEY_FILE=/srv/heim-ki/webui-api-key
# Environment=KNOWLEDGE_NAME=Heim-Dokumente
```

- [ ] **Step 3: systemd-Timer anlegen**

Erstelle `scripts/systemd/doc-sync.timer`:

```ini
[Unit]
Description=Nächtlicher Lauf des Dokument-Sync (2 Uhr)

[Timer]
OnCalendar=*-*-* 02:00:00
# Verpasste Läufe nachholen (z. B. wenn der Host nachts aus war):
Persistent=true
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: launchd-plist anlegen**

Erstelle `scripts/launchd/de.heim-ki.doc-sync.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<!-- macOS-Pendant zum systemd-Timer (Tutorial §7):
     nächtlicher Dokument-Sync um 2:00 Uhr.

     Bewusst ein LaunchAgent (Nutzer-Session), KEIN LaunchDaemon: Der Sync
     braucht Open WebUI im Container (Docker Desktop/OrbStack laufen nur in
     einer angemeldeten Session) — und er braucht die GPU, die einem
     root-Daemon ohne Session nicht zur Verfügung steht. Für den Betrieb ohne
     angesteckten Monitor: automatische Anmeldung aktivieren, siehe §3
     "Unbeaufsichtigter Betrieb".

     Installation (ohne sudo):
       mkdir -p ~/Library/LaunchAgents
       cp scripts/launchd/de.heim-ki.doc-sync.plist ~/Library/LaunchAgents/
       launchctl load -w ~/Library/LaunchAgents/de.heim-ki.doc-sync.plist

     Verpasste Läufe werden beim Aufwachen aus dem Ruhezustand nachgeholt,
     nicht aber nach einem kompletten Shutdown oder ohne angemeldete Session. -->
<dict>
    <key>Label</key>
    <string>de.heim-ki.doc-sync</string>

    <key>ProgramArguments</key>
    <array>
        <string>/opt/heim-ki/scripts/.venv/bin/python</string>
        <string>/opt/heim-ki/scripts/doc-sync.py</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>DOCS_DIR</key>
        <string>/opt/heim-ki/dokumente</string>
        <key>STATE_FILE</key>
        <string>/opt/heim-ki/doc-sync-state.json</string>
        <key>CACHE_DIR</key>
        <string>/opt/heim-ki/cache</string>
        <key>LOCK_FILE</key>
        <string>/opt/heim-ki/doc-sync.lock</string>
        <key>WEBUI_URL</key>
        <string>http://127.0.0.1:3000</string>
        <key>WEBUI_API_KEY_FILE</key>
        <string>/opt/heim-ki/webui-api-key</string>
        <key>KNOWLEDGE_NAME</key>
        <string>Heim-Dokumente</string>
        <!-- Gemessenes Optimum eintragen (siehe Tutorial §7): mehr Threads
             sind NICHT besser. -->
        <key>OMP_NUM_THREADS</key>
        <string>6</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>2</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/opt/heim-ki/logs/doc-sync.log</string>
    <key>StandardErrorPath</key>
    <string>/opt/heim-ki/logs/doc-sync.log</string>
</dict>
</plist>
```

- [ ] **Step 5: `.gitignore` ergänzen**

Der API-Key liegt betrieblich außerhalb des Repos. Der Eintrag ist Versicherung gegen einen Testlauf im Arbeitsverzeichnis. Hänge an `.gitignore` an:

```
webui-api-key
```

- [ ] **Step 6: systemd-Unit gegenprüfen (nur auf einem Linux-Host)**

Run: `systemd-analyze verify scripts/systemd/doc-sync.service`
Expected: keine Ausgabe. Meldungen über einen fehlenden `heim-ki`-Account sind auf einer Entwicklungsmaschine zu erwarten und kein Fehler. Auf macOS entfällt dieser Schritt.

- [ ] **Step 7: Commit**

```bash
git add scripts/requirements.txt scripts/systemd/doc-sync.service scripts/systemd/doc-sync.timer scripts/launchd/de.heim-ki.doc-sync.plist .gitignore
git commit -m "feat(doc-sync): Units, plist und Abhaengigkeiten

Die systemd-Unit setzt bewusst KEIN PrivateDevices=yes: in der Vorgaengerunit
nahm das dem Dienst /dev/nvidia* und zwang Docling auf die CPU — genau das,
was dieser Umbau abstellt. chromadb und ollama entfallen als Abhaengigkeit.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Thread-Schalter für Variante A

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml` (Dienst `docling`)
- Modify: `docker-compose.macos.yml` (Dienst `docling`)

**Interfaces:**
- Consumes: nichts.
- Produces: `.env`-Variable `DOCLING_OMP_THREADS`, die Task 7 und 8 in der Doku erklären.

Unabhängig von C: Der Docling-Container läuft mit dem Bibliotheks-Default von 4 Threads. Gemessen am 188-Seiten-Scan waren 6 Threads 1,47× schneller (509,8 s → 346,0 s) bei byte-identischer Ausgabe. Das kommt allen Browser-Uploads zugute.

- [ ] **Step 1: `.env.example` ergänzen**

Füge im Abschnitt „Docling (RAG-Variante A)" nach der `DOCLING_SERVE_MAX_SYNC_WAIT`-Erklärung ein:

```bash
# Threads für Doclings Layout- und OCR-Modelle. Der Bibliotheks-Default ist 4;
# gemessen am 188-Seiten-Scan war 6 mit Abstand am schnellsten:
#   2 Threads: 766,4 s | 4: 509,8 s | 6: 346,0 s | 8: 419,3 s | 12: 656,8 s
# Mehr ist also NICHT besser — ONNX Runtime und Torch machen zusätzlich eigene
# Thread-Pools auf, ab 8 überbucht das die Kerne. Der Wert stammt von einem
# Mac Studio (M4 Max); auf anderer Hardware nachmessen, und zwar an einem
# GROSSEN Dokument: auf 8 Seiten gemessen war ausgerechnet der schlechteste
# Wert der schnellste.
DOCLING_OMP_THREADS=6
```

- [ ] **Step 2: Beide Compose-Dateien anpassen**

In `docker-compose.yml` und `docker-compose.macos.yml` jeweils im `environment`-Block des Dienstes `docling` unter der `DOCLING_SERVE_MAX_SYNC_WAIT`-Zeile ergänzen:

```yaml
      # Thread-Zahl für Layout und OCR. Der Image-Default 4 lässt Leistung
      # liegen, mehr als 6 kostet welche — Messwerte in der .env.example:
      - OMP_NUM_THREADS=${DOCLING_OMP_THREADS:-6}
```

- [ ] **Step 3: Compose-Dateien gegenprüfen**

```bash
docker compose -f docker-compose.yml config >/dev/null && echo "docker-compose.yml ok"
docker compose -f docker-compose.macos.yml config >/dev/null && echo "docker-compose.macos.yml ok"
```
Expected: beide Zeilen mit `ok`.

- [ ] **Step 4: Wirkung im laufenden Container prüfen (nur wenn das rag-Profil läuft)**

```bash
docker compose -f docker-compose.macos.yml --profile rag up -d docling
docker exec docling sh -c 'echo $OMP_NUM_THREADS'
```
Expected: `6` (bzw. der in der `.env` gesetzte Wert).

- [ ] **Step 5: Commit**

```bash
git add .env.example docker-compose.yml docker-compose.macos.yml
git commit -m "perf(docling): Thread-Zahl konfigurierbar, Default 6 statt 4

Gemessen am 188-Seiten-Scan: 6 Threads brauchen 346,0 s statt 509,8 s bei
byte-identischer Ausgabe. Mehr ist nicht besser — 12 Threads sind mit 656,8 s
langsamer als der Default.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Rückbau Variante B (Code)

**Files:**
- Delete: `scripts/rag-indexer.py`
- Delete: `tools/heim_docs_suche.py` (und damit das leere Verzeichnis `tools/`)
- Delete: `scripts/systemd/rag-indexer.service`, `scripts/systemd/rag-indexer.timer`
- Delete: `scripts/launchd/de.heim-ki.rag-indexer.plist`
- Modify: `docker-compose.yml` (Dienst `chroma`, Volume `chroma-data`)
- Modify: `docker-compose.macos.yml` (Dienst `chroma`, Volume `chroma-data`)
- Modify: `.env.example` (`CHROMA_IMAGE`, `CHROMA_HOST_PORT`)
- Modify: `scripts/backup.sh:6`, `scripts/backup.sh:58`

**Interfaces:**
- Consumes: nichts.
- Produces: nichts. Danach existiert im Repo kein Verweis mehr auf Chroma außer in den Doku-Dateien, die Task 7 und 8 nachziehen.

`scripts/docscan.py` und `tests/test_docscan.py` bleiben — `doc-sync.py` benutzt sie.

- [ ] **Step 1: Dateien löschen**

```bash
git rm scripts/rag-indexer.py \
       tools/heim_docs_suche.py \
       scripts/systemd/rag-indexer.service \
       scripts/systemd/rag-indexer.timer \
       scripts/launchd/de.heim-ki.rag-indexer.plist
```

- [ ] **Step 2: `chroma` aus beiden Compose-Dateien entfernen**

Entferne in `docker-compose.yml` und `docker-compose.macos.yml` jeweils den kompletten `chroma`-Dienstblock (samt Kommentarkopf und `profiles: ["rag-batch"]`) und im `volumes`-Block den Eintrag:

```yaml
  chroma-data:
    name: chroma-data
```

Passe außerdem den Kopfkommentar von `docker-compose.yml` an: Der Hinweis auf das Profil `rag-batch` entfällt, `rag` bleibt.

- [ ] **Step 3: `.env.example` bereinigen**

Entferne den kompletten Abschnitt „ChromaDB (RAG-Variante B)" samt `CHROMA_IMAGE`-Zeile im Abschnitt „Image-Versionen".

Die lokale `.env` ist per `.gitignore` ausgenommen und wird von diesem Commit nicht erfasst — sie enthält aber dieselben beiden Einträge (aktuell `CHROMA_HOST_PORT=8001`). Räume sie von Hand mit auf, sonst bleiben tote Variablen liegen:

```bash
grep -n "CHROMA" .env
```

- [ ] **Step 4: `scripts/backup.sh` bereinigen**

Entferne Zeile 6 (`#   - Docker-Volume chroma-data       (RAG-Index, Variante B)`) und Zeile 58 (`backup_volume chroma-data`).

- [ ] **Step 5: Gegenprüfen, dass nichts hängen bleibt**

```bash
docker compose -f docker-compose.yml config >/dev/null && echo "compose ok"
docker compose -f docker-compose.macos.yml config >/dev/null && echo "compose macos ok"
grep -rn "chroma\|rag-indexer\|heim_docs_suche\|rag-batch" \
  --include="*.sh" --include="*.yml" --include="*.py" --include="*.plist" \
  --include="*.service" --include="*.timer" --include=".env.example" . \
  | grep -v "^./.superpowers\|^./docs/superpowers"
```
Expected: die beiden `ok`-Zeilen, und der `grep` liefert **keine** Treffer. Treffer in `docs/superpowers/` sind historische Pläne und Specs und bleiben unangetastet.

- [ ] **Step 6: Testsuite laufen lassen**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS — der Rückbau darf keinen Test brechen.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: Variante B (Chroma + eigener Indexer) zurueckbauen

Variante C leistet dasselbe (naechtlicher Batch), ohne den Wissensspeicher zu
spalten: Open WebUI bringt seine eigene Vektordatenbank mit. Damit entfallen
der Chroma-Dienst, der eigene Indexer und das Such-Werkzeug.

docscan.py bleibt — doc-sync.py benutzt die Dateiauswahl samt Symlink-Schutz
unveraendert weiter.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Doku deutsch (`TUTORIAL_DE.md`, `README.md`)

**Files:**
- Modify: `TUTORIAL_DE.md` — §7 ab Zeile 513 („RAG einrichten"), insbesondere Variante B ab Zeile 568
- Modify: `README.md:34-35` (Komponententabelle)
- Modify: `REVIEW-UND-PLAN.md` (nur, falls dort Variante B als offener Punkt steht)

**Interfaces:**
- Consumes: alle Dateinamen und Variablennamen aus Task 1 bis 6.
- Produces: den deutschen Text, den Task 8 ins Englische überträgt.

- [ ] **Step 1: Bestand sichten**

```bash
grep -n "chroma\|Chroma\|rag-indexer\|heim_docs_suche\|rag-batch\|Variante B" TUTORIAL_DE.md
grep -n "Chroma\|ChromaDB" README.md
grep -n "chroma\|rag-indexer" REVIEW-UND-PLAN.md
```
Verschaffe dir einen Überblick, bevor du schreibst — §7 hat rund 32 Fundstellen.

- [ ] **Step 2: `README.md` anpassen**

Die Komponententabelle nennt ChromaDB als Speicher der Vektoren. Ersetze die Zeile

```markdown
| [ChromaDB](https://docs.trychroma.com) | stores the document vectors |
```

durch

```markdown
| Open WebUI's built-in vector store | stores the document vectors |
```

und ersetze die Docling-Zeile

```markdown
| [Docling](https://github.com/docling-project/docling-serve) | turns PDFs and scans into clean text (OCR included) |
```

durch

```markdown
| [Docling](https://github.com/docling-project/docling) | turns PDFs and scans into clean text (OCR included) — in a container, or natively on the host for roughly 3x the speed |
```

- [ ] **Step 3: §7 in `TUTORIAL_DE.md` neu fassen**

Struktur des Abschnitts danach:

1. **Variante A (unverändert im Kern)** — Open WebUI + Docling-Container. Ergänze die Thread-Einstellung aus Task 5 mit der vollständigen Messkurve und der Warnung, an einem *großen* Dokument nachzumessen.
2. **Variante B ersetzen durch Variante C** — Überschrift und Inhalt komplett neu. Inhalt:
   - Warum überhaupt: die Messwerte (Container 509,8 s gegen nativ 181,5 s), und der Hinweis, dass Docker auf Apple Silicon kein MPS durchreicht.
   - Was C *nicht* löst: Open WebUI entkoppelt Upload und Verarbeitung schon von sich aus (`BackgroundTask`, Status `pending`). Wer nur die Wartezeit im Browser meint, braucht C nicht.
   - Einrichtung: Ordner und venv anlegen (Pfade wie bisher: `/srv/…` unter Linux, `/opt/heim-ki/…` unter macOS), `scripts/docscan.py`, `scripts/docconvert.py`, `scripts/webui_client.py`, `scripts/doc-sync.py` und `scripts/requirements.txt` kopieren.
   - API-Key in Open WebUI erzeugen (*Einstellungen → Konto → API-Schlüssel*), nach `/opt/heim-ki/webui-api-key` schreiben, `chmod 600`.
   - Sammlung anlegen: entweder in der Oberfläche unter *Arbeitsbereich → Wissen*, oder beim ersten Lauf mit `--create`. Erklären, warum das Skript sonst abbricht (Tippfehler im Namen erzeugt sonst still eine zweite Sammlung).
   - Erster Lauf von Hand, mit den Umgebungsvariablen für macOS.
   - Nächtlicher Lauf: `launchctl load -w` bzw. `systemctl enable --now doc-sync.timer`.
   - Der Linux-Hinweis zu `PrivateDevices=yes`: warum die neue Unit es weglässt.
   - Benutzung im Chat mit `#Heim-Dokumente`.
3. **Migration von Variante B** — eigener kurzer Block: Timer/Agent abschalten, Chroma-Container stoppen, Volume bewusst löschen.

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

Behalte den Ton des Bestands: erklärend, mit Begründungen und den Stolpersteinen, die tatsächlich auftreten. Die Hinweise zu Embedding-Batchgröße und Concurrent Requests aus Variante A bleiben unverändert bestehen — sie gelten weiter für Browser-Uploads.

- [ ] **Step 4: Querverweise prüfen**

```bash
grep -n "Variante B\|rag-batch\|Chroma\|chroma" TUTORIAL_DE.md
grep -n "§7\|Abschnitt 7" TUTORIAL_DE.md README.md
```
Expected: Treffer nur noch im Migrationsblock. Alle Verweise auf §7 zeigen weiterhin auf einen existierenden Abschnitt.

- [ ] **Step 5: Commit**

```bash
git add TUTORIAL_DE.md README.md REVIEW-UND-PLAN.md
git commit -m "docs(rag): Variante C statt Variante B im deutschen Tutorial

Native Konvertierung mit Messwerten begruendet (509,8 s im Container gegen
181,5 s nativ), Thread-Kurve dokumentiert, Migrationsweg von Variante B
inklusive bewusstem Loeschen des chroma-data-Volumes.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Doku englisch (`TUTORIAL_EN.md`)

**Files:**
- Modify: `TUTORIAL_EN.md` — §7, rund 32 Fundstellen zu Variante B

**Interfaces:**
- Consumes: den deutschen §7 aus Task 7 als Vorlage.
- Produces: nichts.

- [ ] **Step 1: Deutschen Abschnitt als Vorlage nehmen**

```bash
git show HEAD:TUTORIAL_DE.md | sed -n '/^## 7\./,/^## 8\./p' > /tmp/tutorial-de-abschnitt7.md
grep -n "^## \|^### " TUTORIAL_EN.md | sed -n '20,30p'
```

- [ ] **Step 2: §7 in `TUTORIAL_EN.md` übertragen**

Übertrage den in Task 7 neu gefassten Abschnitt sinngemäß ins Englische. Keine wörtliche Übersetzung — der Bestand ist idiomatisches Englisch, kein übersetztes Deutsch. Prüfe insbesondere:

- Die Messwerte sind identisch (Zahlen und Einheiten nicht anpassen).
- Die Pfade, Dateinamen und Umgebungsvariablen sind identisch.
- Code-Blöcke und Kommentare *im Code* bleiben so, wie sie in den Dateien stehen — die Kommentare in `doc-sync.service` und der plist sind deutsch und werden nicht übersetzt.

- [ ] **Step 3: Gegenprüfen, dass beide Tutorials dieselbe Struktur haben**

```bash
diff <(grep -n "^## \|^### " TUTORIAL_DE.md | sed 's/^[0-9]*://') \
     <(grep -n "^## \|^### " TUTORIAL_EN.md | sed 's/^[0-9]*://')
```
Expected: Unterschiede nur in der Sprache der Überschriften, nicht in ihrer Anzahl oder Reihenfolge.

```bash
grep -n "chroma\|Chroma\|rag-indexer\|heim_docs_suche\|rag-batch" TUTORIAL_EN.md
```
Expected: Treffer nur noch im Migrationsblock.

- [ ] **Step 4: Commit**

```bash
git add TUTORIAL_EN.md
git commit -m "docs(rag): Variante C statt Variante B im englischen Tutorial

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Abschluss

- [ ] **Gesamte Testsuite**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Ende-zu-Ende auf dem echten Host**

Lege zwei Dokumente in `DOCS_DIR` (eines davon ein Scan) und starte den Sync von Hand:

```bash
DOCS_DIR=/opt/heim-ki/dokumente \
STATE_FILE=/opt/heim-ki/doc-sync-state.json \
CACHE_DIR=/opt/heim-ki/cache \
LOCK_FILE=/opt/heim-ki/doc-sync.lock \
WEBUI_API_KEY_FILE=/opt/heim-ki/webui-api-key \
OMP_NUM_THREADS=6 \
  /opt/heim-ki/scripts/.venv/bin/python /opt/heim-ki/scripts/doc-sync.py --create
```

Prüfe danach:
1. Die Sammlung „Heim-Dokumente" enthält beide Dokumente als `.md`.
2. Ein zweiter Lauf konvertiert nichts mehr („Fertig. 2 Dateien im Bestand, 0 Fehler." ohne „Übernommen"-Zeilen).
3. Eine Frage im Chat mit `#Heim-Dokumente` liefert eine Antwort mit Quellenangabe.
4. `docker logs docling` zeigt für diese beiden Dokumente **keinen** `POST /v1/convert/file` — der Beleg, dass der Upload den Container umgangen hat.
