"""
title: Heim-Dokumente durchsuchen
author: local-ai-setup
description: Durchsucht den nächtlich gebauten RAG-Index (ChromaDB + bge-m3) über die privaten Dokumente und liefert passende Textstellen mit Quellenangabe.
version: 0.1.0
license: MIT
"""

from urllib.parse import urlparse

import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        chroma_url: str = Field(
            default="http://chroma:8000",
            description="ChromaDB-Server, aus Sicht des Open-WebUI-Containers (Compose-Netz)",
        )
        ollama_url: str = Field(
            default="http://ollama:11434",
            description="Ollama-Server für das Embedding der Suchfrage",
        )
        embed_model: str = Field(
            default="bge-m3",
            description="Embedding-Modell — muss zum Modell des Indexers passen",
        )
        collection: str = Field(
            default="heim-docs",
            description="Name der Chroma-Collection (siehe scripts/rag-indexer.py)",
        )
        top_k: int = Field(
            default=5,
            description="Anzahl der zurückgegebenen Textstellen",
        )

    def __init__(self):
        self.valves = self.Valves()

    def suche_heim_dokumente(self, frage: str) -> str:
        """
        Durchsucht die privaten Heim-Dokumente (Verträge, Anleitungen,
        Rechnungen, PDFs …) nach Textstellen, die zur Frage passen.
        Immer benutzen, wenn nach Inhalten der eigenen Dokumente gefragt wird.

        :param frage: Die Suchfrage in natürlicher Sprache.
        :return: Die relevantesten Textstellen mit Quellenangabe.
        """
        v = self.valves

        try:
            r = requests.post(
                f"{v.ollama_url.rstrip('/')}/api/embed",
                json={"model": v.embed_model, "input": frage},
                timeout=60,
            )
            r.raise_for_status()
            embedding = r.json()["embeddings"][0]
        except Exception as exc:
            return f"Fehler beim Embedding der Frage (Ollama, {v.ollama_url}): {exc}"

        try:
            import chromadb  # im Open-WebUI-Image bereits enthalten

            u = urlparse(v.chroma_url)
            client = chromadb.HttpClient(host=u.hostname, port=u.port or 8000)
            collection = client.get_collection(v.collection)
            res = collection.query(
                query_embeddings=[embedding],
                n_results=v.top_k,
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            return (
                f"Fehler bei der Suche in ChromaDB ({v.chroma_url}): {exc}\n"
                "Läuft der Chroma-Container (docker compose --profile rag-batch up -d) "
                "und hat der Indexer schon Daten geschrieben?"
            )

        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        if not docs:
            return "Keine passenden Textstellen im Dokumenten-Index gefunden."

        teile = []
        for i, (doc, meta) in enumerate(zip(docs, metas), start=1):
            quelle = (meta or {}).get("relpfad") or (meta or {}).get("quelle") or "unbekannt"
            teile.append(f"[{i}] Quelle: {quelle}\n{doc}")
        return "\n\n---\n\n".join(teile)
