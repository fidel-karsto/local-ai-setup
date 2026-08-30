"""Tests für die Dateiauswahl des RAG-Indexers.

Ausführen (kein pytest nötig):
    python3 -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import docscan


class TestDocscan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        wurzel = Path(self._tmp.name)
        self.docs = wurzel / "dokumente"
        self.docs.mkdir()
        self.aussen = wurzel / "geheim"
        self.aussen.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_normale_datei_wird_indexiert(self):
        (self.docs / "notiz.md").write_text("hallo", encoding="utf-8")
        self.assertEqual(list(docscan.scan(self.docs)), ["notiz.md"])

    def test_symlink_nach_aussen_wird_uebersprungen(self):
        geheim = self.aussen / "id_rsa"
        geheim.write_text("PRIVATE KEY", encoding="utf-8")
        (self.docs / "notiz.md").symlink_to(geheim)
        self.assertEqual(docscan.scan(self.docs), {})

    def test_symlink_innerhalb_wird_ebenfalls_uebersprungen(self):
        (self.docs / "echt.md").write_text("hallo", encoding="utf-8")
        (self.docs / "kopie.md").symlink_to(self.docs / "echt.md")
        self.assertEqual(list(docscan.scan(self.docs)), ["echt.md"])

    def test_toter_symlink_bricht_den_lauf_nicht_ab(self):
        (self.docs / "weg.md").symlink_to(self.aussen / "gibtsnicht.md")
        self.assertEqual(docscan.scan(self.docs), {})

    def test_unbekannte_endung_wird_ignoriert(self):
        (self.docs / "notiz.txt").write_text("hallo", encoding="utf-8")
        self.assertEqual(docscan.scan(self.docs), {})

    def test_endung_wird_case_insensitiv_geprueft(self):
        (self.docs / "Rechnung.PDF").write_bytes(b"%PDF-")
        self.assertEqual(list(docscan.scan(self.docs)), ["Rechnung.PDF"])

    def test_unterordner_bleibt_im_relpfad(self):
        (self.docs / "steuer").mkdir()
        (self.docs / "steuer" / "2025.pdf").write_bytes(b"%PDF-")
        self.assertEqual(list(docscan.scan(self.docs)), ["steuer/2025.pdf"])

    def test_verlinkter_ordner_wird_nicht_betreten(self):
        (self.aussen / "heimlich.md").write_text("geheim", encoding="utf-8")
        (self.docs / "ordner").symlink_to(self.aussen, target_is_directory=True)
        self.assertEqual(docscan.scan(self.docs), {})


if __name__ == "__main__":
    unittest.main()
