"""Tests für den Melde-Wrapper (scripts/doc-sync-run.sh).

Der Wrapper existiert, damit ein fehlgeschlagener Nachtlauf auffällt. Geprüft
wird deshalb genau das: dass er den Melder bei Misserfolg aufruft, bei Erfolg
schweigt, und dass er den Exit-Code unter keinen Umständen verfälscht.

Interpreter und `osascript` werden über den PATH gefälscht, statt dem Skript
einen Testschalter zu geben — so läuft die echte Verzweigung.

Ausführen (kein pytest nötig):
    python3 -m unittest tests.test_doc_sync_run -v
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().parent.parent / "scripts" / "doc-sync-run.sh"


class TestDocSyncRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        # Spur, die der gefälschte Melder hinterlässt:
        self.melder_spur = self.tmp / "gemeldet.txt"
        self._schreibe(
            self.bin / "osascript",
            f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{self.melder_spur}"\nexit 0\n',
        )
        # Spur, die der gefälschte Interpreter hinterlässt:
        self.python_spur = self.tmp / "aufgerufen.txt"

    def tearDown(self):
        self._tmp.cleanup()

    def _schreibe(self, pfad: Path, inhalt: str) -> None:
        pfad.write_text(inhalt, encoding="utf-8")
        pfad.chmod(0o755)

    def _fake_python(self, exit_code: int) -> Path:
        pfad = self.bin / "fake-python"
        self._schreibe(
            pfad,
            f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{self.python_spur}"\n'
            f"exit {exit_code}\n",
        )
        return pfad

    def _lauf(self, exit_code: int, argumente=()):
        python = self._fake_python(exit_code)
        umgebung = dict(os.environ)
        # Der gefälschte PATH steht vorn, damit der Wrapper unser osascript
        # findet und nicht das echte des Systems.
        umgebung["PATH"] = f"{self.bin}:{umgebung['PATH']}"
        umgebung["DOC_SYNC_PYTHON"] = str(python)
        umgebung["DOC_SYNC_SKRIPT"] = str(self.tmp / "doc-sync.py")
        return subprocess.run(
            [str(WRAPPER), *argumente],
            env=umgebung,
            capture_output=True,
            text=True,
        )

    def test_erfolg_meldet_nichts(self):
        ergebnis = self._lauf(0)
        self.assertEqual(ergebnis.returncode, 0)
        self.assertFalse(self.melder_spur.exists(), "bei Erfolg darf nichts gemeldet werden")

    def test_fehler_meldet_und_behaelt_den_exit_code(self):
        ergebnis = self._lauf(3)
        self.assertEqual(ergebnis.returncode, 3, "der Exit-Code muss unveraendert durchgereicht werden")
        self.assertTrue(self.melder_spur.exists(), "bei Misserfolg muss gemeldet werden")
        self.assertIn("3", self.melder_spur.read_text(encoding="utf-8"))

    def test_scheiternder_melder_verfaelscht_den_exit_code_nicht(self):
        # Eine fehlende Mitteilungsberechtigung darf den echten Fehler nicht
        # verdecken — sonst meldet der Wrapper Erfolg, obwohl nichts klappte.
        self._schreibe(self.bin / "osascript", "#!/bin/sh\nexit 1\n")
        ergebnis = self._lauf(3)
        self.assertEqual(ergebnis.returncode, 3)

    def test_argumente_kommen_beim_python_an(self):
        self._lauf(0, argumente=["--create"])
        aufgerufen = self.python_spur.read_text(encoding="utf-8")
        self.assertIn("--create", aufgerufen)

    def test_wrapper_ist_ausfuehrbar(self):
        self.assertTrue(os.access(WRAPPER, os.X_OK), "Wrapper muss das Ausfuehrbar-Bit tragen")


if __name__ == "__main__":
    unittest.main()
