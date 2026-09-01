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
