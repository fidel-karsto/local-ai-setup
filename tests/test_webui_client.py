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

# Wird pro Test gesetzt: {(methode, pfad-ohne-query): [(status, rumpf), ...]}
# Die Liste wird der Reihe nach abgearbeitet; der letzte Eintrag bleibt für
# alle weiteren Aufrufe stehen. So lässt sich Seitenweise-Blättern testen,
# ohne einen zweiten Server aufzusetzen.
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
        folge = ANTWORTEN.get((methode, pfad))
        if not folge:
            status, nutzlast = 404, {"detail": "nicht gefunden"}
        else:
            status, nutzlast = folge[0] if len(folge) == 1 else folge.pop(0)
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
        ANTWORTEN[("GET", "/api/v1/knowledge/")] = [
            (
                200,
                {
                    "items": [{"id": "k1", "name": "Andere"}, {"id": "k2", "name": "Heim-Dokumente"}],
                    "total": 2,
                },
            )
        ]
        self.assertEqual(self.client.knowledge_id_by_name("Heim-Dokumente"), "k2")

    def test_unbekannte_sammlung_liefert_none(self):
        ANTWORTEN[("GET", "/api/v1/knowledge/")] = [(200, {"items": [], "total": 0})]
        self.assertIsNone(self.client.knowledge_id_by_name("Heim-Dokumente"))

    def test_authorization_header_wird_gesetzt(self):
        ANTWORTEN[("GET", "/api/v1/knowledge/")] = [(200, {"items": [], "total": 0})]
        self.client.knowledge_id_by_name("egal")
        _, _, headers, _ = AUFRUFE[0]
        self.assertEqual(headers["Authorization"], "Bearer sk-test")

    def test_upload_schickt_text_markdown(self):
        ANTWORTEN[("POST", "/api/v1/files/")] = [(200, {"id": "f1"})]
        file_id = self.client.upload_markdown("heft.md", "# Inhalt")
        self.assertEqual(file_id, "f1")

        _, _, headers, rumpf = AUFRUFE[0]
        self.assertIn("multipart/form-data", headers["Content-Type"])
        self.assertIn(b"text/markdown", rumpf)
        self.assertIn(b'filename="heft.md"', rumpf)
        self.assertIn("# Inhalt".encode("utf-8"), rumpf)

    def test_datei_zur_sammlung_hinzufuegen(self):
        ANTWORTEN[("POST", "/api/v1/knowledge/k2/file/add")] = [(200, {"id": "k2"})]
        self.client.add_file_to_knowledge("k2", "f1")
        _, _, _, rumpf = AUFRUFE[0]
        self.assertEqual(json.loads(rumpf), {"file_id": "f1"})

    def test_datei_aus_sammlung_entfernen(self):
        ANTWORTEN[("POST", "/api/v1/knowledge/k2/file/remove")] = [(200, {"id": "k2"})]
        self.client.remove_file_from_knowledge("k2", "f1")
        _, _, _, rumpf = AUFRUFE[0]
        self.assertEqual(json.loads(rumpf), {"file_id": "f1"})

    def test_dateiliste_ueber_mehrere_seiten(self):
        # 30 Einträge pro Seite: der Client muss weiterblättern, bis er
        # "total" beisammen hat, sonst fehlen ab Dokument 31 die IDs und der
        # Sync lädt sie bei jedem Lauf erneut hoch.
        ANTWORTEN[("GET", "/api/v1/knowledge/k2/files")] = [
            (200, {"items": [{"id": f"f{i}"} for i in range(30)], "total": 31}),
            (200, {"items": [{"id": "f30"}], "total": 31}),
        ]

        ids = self.client.knowledge_file_ids("k2")

        self.assertEqual(len(ids), 31)
        self.assertIn("f30", ids)
        # Zweite Seite wurde wirklich angefordert:
        self.assertIn("page=2", AUFRUFE[1][1])

    def test_fehlerstatus_wirft_webui_error(self):
        ANTWORTEN[("POST", "/api/v1/files/")] = [(401, {"detail": "Not authenticated"})]
        with self.assertRaises(webui_client.WebUIError) as ctx:
            self.client.upload_markdown("heft.md", "# Inhalt")
        self.assertIn("401", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
