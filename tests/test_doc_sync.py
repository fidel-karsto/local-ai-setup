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
        # Unterschiedlicher Inhalt, damit die beiden Dateien nicht denselben
        # sha256-Digest (und damit denselben Cache-Eintrag) teilen — sonst
        # würde der Cache-Treffer von "gut.pdf" die absichtlich scheiternde
        # Konvertierung von "kaputt.pdf" verdecken.
        (self.docs / "kaputt.pdf").write_bytes(b"%PDF- kaputt")
        (self.docs / "gut.pdf").write_bytes(b"%PDF- gut")
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
