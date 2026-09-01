"""Dünner Client für die Open-WebUI-REST-API (scripts/doc-sync.py).

Weiß nichts von Docling und nichts vom Manifest — nur HTTP. Verifiziert
gegen Open WebUI v0.11.1.
"""
from __future__ import annotations

import requests

# Open WebUI liefert Listen mit 30 Einträgen pro Seite (PAGE_ITEM_COUNT).
# Der Client verlässt sich nicht auf diesen Wert, sondern blättert, bis
# "total" beisammen ist — dann bleibt er auch bei einer Änderung heil.

# Obergrenze fürs Blättern: Die Schleife terminiert für jedes endliche
# "total" (gesammelt wächst pro Durchlauf), aber ein fehlerhaft großes
# "total" vom Server würde in diesem unbeaufsichtigten Nacht-Job unbemerkt
# tausende HTTP-Anfragen auslösen. 200 Seiten à 30 Einträge = 6000 Dokumente,
# weit jenseits dessen, wofür dieses Setup gedacht ist.
MAX_SEITEN = 200


class WebUIError(RuntimeError):
    """Open WebUI hat mit einem Fehlerstatus geantwortet."""


class WebUIClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 60):
        self.basis = base_url.rstrip("/")
        self.kopf = {"Authorization": f"Bearer {api_key}"}
        self.timeout = timeout

    # --- innen -------------------------------------------------------------

    def _pruefe(self, antwort: requests.Response, was: str) -> dict:
        if not antwort.ok:
            raise WebUIError(f"{was}: HTTP {antwort.status_code} — {antwort.text[:200]}")
        return antwort.json()

    def _get(self, pfad: str, params: dict | None = None) -> dict:
        antwort = requests.get(
            f"{self.basis}{pfad}", headers=self.kopf, params=params, timeout=self.timeout
        )
        return self._pruefe(antwort, f"GET {pfad}")

    def _post(self, pfad: str, nutzlast: dict) -> dict:
        antwort = requests.post(
            f"{self.basis}{pfad}", headers=self.kopf, json=nutzlast, timeout=self.timeout
        )
        return self._pruefe(antwort, f"POST {pfad}")

    def _alle_seiten(self, pfad: str) -> list[dict]:
        gesammelt: list[dict] = []
        seite = 1
        while True:
            daten = self._get(pfad, {"page": seite})
            eintraege = daten.get("items", [])
            gesammelt.extend(eintraege)
            total = daten.get("total", 0)
            if not eintraege or len(gesammelt) >= total:
                return gesammelt
            if seite >= MAX_SEITEN:
                raise WebUIError(
                    f"{pfad}: Abbruch nach {len(gesammelt)} geholten Einträgen "
                    f"(MAX_SEITEN={MAX_SEITEN} erreicht), aber Server meldet total={total}"
                )
            seite += 1

    # --- außen -------------------------------------------------------------

    def knowledge_id_by_name(self, name: str) -> str | None:
        for eintrag in self._alle_seiten("/api/v1/knowledge/"):
            if eintrag.get("name") == name:
                return eintrag["id"]
        return None

    def create_knowledge(self, name: str, description: str) -> str:
        # Beide Felder sind in KnowledgeForm Pflicht.
        return self._post(
            "/api/v1/knowledge/create", {"name": name, "description": description}
        )["id"]

    def knowledge_file_ids(self, knowledge_id: str) -> set[str]:
        return {
            eintrag["id"]
            for eintrag in self._alle_seiten(f"/api/v1/knowledge/{knowledge_id}/files")
        }

    def upload_markdown(self, dateiname: str, text: str) -> str:
        """Lädt Text als Markdown hoch und liefert die File-ID.

        Der Content-Type text/markdown ist der Kern des Ganzen: Open WebUIs
        _is_text_file() greift darüber und nimmt den TextLoader — der
        Docling-Container wird gar nicht erst gefragt.
        """
        antwort = requests.post(
            f"{self.basis}/api/v1/files/",
            headers=self.kopf,
            files={"file": (dateiname, text.encode("utf-8"), "text/markdown")},
            timeout=self.timeout,
        )
        return self._pruefe(antwort, "POST /api/v1/files/")["id"]

    def add_file_to_knowledge(self, knowledge_id: str, file_id: str) -> None:
        self._post(f"/api/v1/knowledge/{knowledge_id}/file/add", {"file_id": file_id})

    def remove_file_from_knowledge(self, knowledge_id: str, file_id: str) -> None:
        self._post(f"/api/v1/knowledge/{knowledge_id}/file/remove", {"file_id": file_id})
