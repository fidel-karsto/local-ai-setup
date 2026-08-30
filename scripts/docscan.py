"""Dateiauswahl für den RAG-Indexer (siehe rag-indexer.py).

Bewusst ein eigenes Modul ohne schwere Importe: die Containment-Prüfung ist
sicherheitskritisch und soll ohne installiertes venv testbar sein
(tests/test_docscan.py).
"""
from __future__ import annotations

from pathlib import Path

SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".md"}


def is_indexable(path: Path, docs_dir: Path) -> bool:
    """True, wenn path eine indexierbare Datei INNERHALB von docs_dir ist.

    Symlinks werden grundsätzlich abgelehnt. Grund: path.is_file() folgt
    ihnen, und /srv/dokumente ist für den Login-User beschreibbar — ein Link
    "notiz.md -> /etc/shadow" käme sonst in den Index und wäre anschließend
    über den Chat abrufbar. Die Endung stammt dabei vom Linknamen, der
    SUFFIXES-Filter allein schützt also nicht.

    Hardlinks innerhalb docs_dir auf eine Datei außerhalb bestehen diese
    Prüfung (sie haben kein eigenes "Ziel", resolve() zeigt auf den Pfad
    selbst) und werden hier bewusst nicht gesondert behandelt: das Anlegen
    eines Hardlinks auf Standard-Debian/Ubuntu erfordert ohnehin
    Leserechte auf die Zieldatei (fs.protected_hardlinks=1 ist Default),
    der Aufrufer könnte die Datei also auch direkt lesen.
    """
    if path.is_symlink():
        return False
    if path.suffix.lower() not in SUFFIXES:
        return False
    if not path.is_file():
        return False
    try:
        aufgeloest = path.resolve(strict=True)
    except OSError:
        # Tote Symlinks werden schon oben von is_symlink() abgefangen und
        # erreichen resolve() nie. Real erreichbar ist dieser Zweig nur über
        # eine Race (Datei verschwindet zwischen is_file() und resolve())
        # oder ein Rechteproblem/eine Schleife beim Auflösen — im Zweifel
        # nicht indexieren.
        return False
    return aufgeloest.is_relative_to(docs_dir.resolve())


def scan(docs_dir: Path) -> dict[str, Path]:
    """Alle indexierbaren Dateien unter docs_dir als {relpfad: Pfad}.

    rglob steigt nicht in verlinkte Verzeichnisse ab; verlinkte *Dateien*
    liefert es aber aus, die filtert is_indexable() heraus.
    """
    return {
        str(p.relative_to(docs_dir)): p
        for p in sorted(docs_dir.rglob("*"))
        if is_indexable(p, docs_dir)
    }
