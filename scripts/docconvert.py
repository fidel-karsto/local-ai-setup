"""Native Docling-Konvertierung für den Dokument-Sync (scripts/doc-sync.py).

Bewusst ohne Netz- und Zustandslogik: Die Konvertierung ist der teure,
GPU-nahe Teil und soll unabhängig von Open WebUI testbar bleiben.

Beschleuniger und OCR-Motor werden NICHT im Code festgelegt. Doclings
Default `device='auto'` wählt auf macOS MPS und unter Linux CUDA, und
`ocr_engine=auto` wählt auf macOS `ocrmac` (Apple Vision) — genau die
Kombination, die in der Messung 2,8x schneller war als der Container mit
4 Threads (gegen die heute ausgelieferten 6 Threads sind es 1,9x).

ACHTUNG, das ist keine Selbstverständlichkeit: Die Automatik findet Apple
Vision nur, wenn das Paket `ocrmac` installiert ist. Fehlt es, fällt sie
kommentarlos auf RapidOCR zurück — dasselbe Dokument brauchte dann 1058,0 s
statt 184,9 s und damit mehr als der Container, den dieser Weg ersetzt.
`ocrmac` steht deshalb in scripts/requirements.txt und ist dort nicht
optional. Prüfen lässt sich der tatsächlich gewählte Motor an der Logzeile
"Auto OCR model selected ..." bei aktivem INFO-Logging.

Auch die Thread-Zahl steht bewusst nicht im Code — und ebenso wenig in der
launchd-plist oder der systemd-Unit: nativ gemessen war sie wirkungslos
(4/6/8/12 Threads ergaben 180,2 / 180,2 / 181,4 / 180,8 Sekunden). Der native
Weg hängt an MPS und Apple Vision, nicht an CPU-Threads. Wer trotzdem
eingreifen will, setzt OMP_NUM_THREADS in der Umgebung; docling liest die
Variable selbst (AcceleratorOptions). DOCLING_OMP_THREADS aus der .env
betrifft ausschließlich den Docling-Container.
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
