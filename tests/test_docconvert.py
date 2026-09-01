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
