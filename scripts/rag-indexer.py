#!/usr/bin/env python3
"""Nächtlicher RAG-Indexer: Docling -> bge-m3 (Ollama) -> ChromaDB

README §7, Variante B. Achtung: Ohne Retrieval-Anbindung an Open WebUI wird
dieser Index von den Chats nicht genutzt — siehe REVIEW-UND-PLAN.md, Phase 3.

Bekannte Grenzen dieser Version (Härtung ist als Phase 3 geplant):
- indexiert bei jedem Lauf alle Dateien komplett neu (keine Änderungserkennung)
- entfernt keine Chunks gelöschter Dateien
- Datei-IDs kollidieren bei gleichnamigen Dateien in verschiedenen Unterordnern

Abhängigkeiten:  pip install docling chromadb ollama
Cron (nachts um 2 Uhr):
  0 2 * * * /usr/bin/python3 /srv/scripts/rag-indexer.py >> /var/log/rag-indexer.log 2>&1
"""
from pathlib import Path

import chromadb
import ollama
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker

DOCS_DIR = Path("/srv/dokumente")           # hier fliegen die Docs rum
client = chromadb.PersistentClient(path="/srv/chroma")
collection = client.get_or_create_collection("heim-docs")
converter = DocumentConverter()
chunker = HybridChunker()

for f in DOCS_DIR.rglob("*"):
    if f.suffix.lower() not in {".pdf", ".docx", ".pptx", ".html", ".md"}:
        continue
    doc = converter.convert(f).document          # Docling: Datei -> Struktur
    for i, chunk in enumerate(chunker.chunk(doc)):
        text = chunk.text
        emb = ollama.embed(model="bge-m3", input=text)["embeddings"][0]
        collection.upsert(
            ids=[f"{f.name}-{i}"],
            embeddings=[emb],
            documents=[text],
            metadatas=[{"quelle": str(f)}],
        )
    print(f"Indexiert: {f.name}")
