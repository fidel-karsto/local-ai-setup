#!/usr/bin/env python3
"""Nächtlicher RAG-Indexer: Docling -> bge-m3 (Ollama) -> ChromaDB (Server)

README §7, Variante B. Indexiert die Dokumente aus DOCS_DIR in einen
ChromaDB-Server (Container aus der docker-compose.yml, Profil "rag").
Die Suche aus den Chats übernimmt das Open-WebUI-Tool
tools/heim_docs_suche.py, das dieselbe Collection abfragt.

Eigenschaften:
- Änderungserkennung per SHA-256 (Manifest in STATE_FILE): unveränderte
  Dateien werden übersprungen, ein zweiter Lauf ohne Änderungen macht nichts.
- Gelöschte oder geänderte Dateien: alte Chunks werden aus der Collection
  entfernt (Filter über das Metadatum "relpfad").
- Chunk-IDs aus Pfad-Hash + laufender Nummer — keine Kollisionen bei
  gleichnamigen Dateien in verschiedenen Unterordnern.
- Eingebettet wird chunker.contextualize(chunk) (Text inkl.
  Überschriften-Kontext), nicht der nackte chunk.text.
- Eine fehlerhafte Datei bricht den Lauf nicht ab (Exit-Code 1 am Ende).
- Symlinks werden übersprungen: ein Link aus DOCS_DIR heraus würde sonst
  fremde Dateien in den Index (und damit in die Chat-Antworten) tragen.

Konfiguration über Umgebungsvariablen (Default in Klammern):
  DOCS_DIR    (/srv/dokumente)              Dokumentenordner
  STATE_FILE  (/srv/heim-ki/rag-index-state.json)  Manifest für die Änderungserkennung
  CHROMA_URL  (http://127.0.0.1:8000)       ChromaDB-Server
  OLLAMA_URL  (http://127.0.0.1:11434)      Ollama für Embeddings
  EMBED_MODEL (bge-m3)
  COLLECTION  (heim-docs)

Installation (siehe README §7):
  python3 -m venv /srv/scripts/.venv
  /srv/scripts/.venv/bin/pip install -r requirements.txt
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import chromadb
import ollama
import docscan
from docling.chunking import HybridChunker
from docling.document_converter import DocumentConverter

DOCS_DIR = Path(os.environ.get("DOCS_DIR", "/srv/dokumente"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "/srv/heim-ki/rag-index-state.json"))
CHROMA_URL = os.environ.get("CHROMA_URL", "http://127.0.0.1:8000")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
COLLECTION = os.environ.get("COLLECTION", "heim-docs")

EMBED_BATCH = 32


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def doc_id_prefix(relpath: str) -> str:
    return hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:16]


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    # erst in Temp-Datei schreiben, dann atomar ersetzen — ein Absturz
    # mittendrin hinterlässt so nie ein kaputtes Manifest
    fd, tmp_name = tempfile.mkstemp(dir=str(STATE_FILE.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_name, STATE_FILE)


def embed_texts(client: ollama.Client, texts: list[str]) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        resp = client.embed(model=EMBED_MODEL, input=texts[i : i + EMBED_BATCH])
        embeddings.extend(resp["embeddings"])
    return embeddings


def main() -> int:
    if not DOCS_DIR.is_dir():
        log(f"FEHLER: Dokumentenordner {DOCS_DIR} existiert nicht.")
        return 1

    chroma = urlparse(CHROMA_URL)
    client = chromadb.HttpClient(host=chroma.hostname, port=chroma.port or 8000)
    collection = client.get_or_create_collection(COLLECTION)
    embedder = ollama.Client(host=OLLAMA_URL)

    converter = DocumentConverter()
    chunker = HybridChunker()

    state = load_state()
    current = docscan.scan(DOCS_DIR)

    # 1) Chunks von gelöschten Dateien entfernen
    for rel in sorted(set(state) - set(current)):
        collection.delete(where={"relpfad": rel})
        del state[rel]
        save_state(state)
        log(f"Entfernt (Datei gelöscht): {rel}")

    # 2) Neue und geänderte Dateien indexieren
    fehler = 0
    for rel, path in current.items():
        digest = sha256_file(path)
        if state.get(rel) == digest:
            continue
        try:
            doc = converter.convert(path).document
            texte = [chunker.contextualize(chunk=c) for c in chunker.chunk(doc)]
            collection.delete(where={"relpfad": rel})  # alte Chunk-Reste weg
            if texte:
                prefix = doc_id_prefix(rel)
                collection.upsert(
                    ids=[f"{prefix}-{i}" for i in range(len(texte))],
                    embeddings=embed_texts(embedder, texte),
                    documents=texte,
                    metadatas=[{"quelle": str(path), "relpfad": rel} for _ in texte],
                )
                log(f"Indexiert: {rel} ({len(texte)} Chunks)")
            else:
                log(f"Übersprungen (kein Text extrahiert): {rel}")
            state[rel] = digest
            save_state(state)
        except Exception as exc:
            fehler += 1
            log(f"FEHLER bei {rel}: {exc}")

    log(f"Fertig. {len(current)} Dateien im Bestand, {fehler} Fehler.")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
