#!/usr/bin/env python3
"""Dokument-Sync: Docling nativ -> Markdown -> Open-WebUI-Wissenssammlung.

Siehe docs/superpowers/specs/2026-09-01-native-dokumentkonvertierung-design.md.

Warum nicht Open WebUIs eigener Abgleich (POST /knowledge/{id}/sync/diff):
Der vergleicht meta.file_hash, also die Prüfsumme des HOCHGELADENEN Markdowns.
Die Frage "hat sich die Quell-PDF geändert?" beantwortet er damit erst,
nachdem konvertiert wurde — und genau das ist der teure Schritt. Deshalb hier
ein eigenes Manifest über die Quelldateien.

Konfiguration über Umgebungsvariablen (Default in Klammern):
  DOCS_DIR            (/srv/dokumente)                    Ablageordner
  STATE_FILE          (/srv/heim-ki/doc-sync-state.json)  Manifest
  CACHE_DIR           (/srv/heim-ki/cache)                Markdown-Cache
  LOCK_FILE           (/srv/heim-ki/doc-sync.lock)        Lauf-Sperre
  WEBUI_URL           (http://127.0.0.1:3000)             Open WebUI
  WEBUI_API_KEY_FILE  (/srv/heim-ki/webui-api-key)        API-Key (Modus 0600)
  KNOWLEDGE_NAME      (Heim-Dokumente)                    Zielsammlung

Aufruf von Hand (zusätzlich zum nächtlichen Lauf):
  /srv/scripts/.venv/bin/python /srv/scripts/doc-sync.py
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import docscan

DOCS_DIR = Path(os.environ.get("DOCS_DIR", "/srv/dokumente"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "/srv/heim-ki/doc-sync-state.json"))
CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/srv/heim-ki/cache"))
LOCK_FILE = Path(os.environ.get("LOCK_FILE", "/srv/heim-ki/doc-sync.lock"))
WEBUI_URL = os.environ.get("WEBUI_URL", "http://127.0.0.1:3000")
WEBUI_API_KEY_FILE = Path(
    os.environ.get("WEBUI_API_KEY_FILE", "/srv/heim-ki/webui-api-key")
)
KNOWLEDGE_NAME = os.environ.get("KNOWLEDGE_NAME", "Heim-Dokumente")


class LaufLaeuftBereits(RuntimeError):
    """Ein anderer doc-sync-Lauf hält die Sperre."""


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


@contextmanager
def lauf_sperre(pfad: Path):
    """Verhindert, dass Nachtlauf und Handaufruf gleichzeitig schreiben."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with pfad.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LaufLaeuftBereits(
                f"Ein anderer Lauf hält bereits {pfad} — dieser Lauf endet."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def zielname(rel: str) -> str:
    """'steuer/2025.pdf' -> 'steuer_2025.md'.

    Der ganze Relativpfad fließt ein, damit gleichnamige Dateien aus
    verschiedenen Unterordnern nicht denselben Namen bekommen.
    """
    return Path(rel).with_suffix(".md").as_posix().replace("/", "_")


def load_state(state_file: Path) -> dict:
    if state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {}


def save_state(state_file: Path, state: dict) -> None:
    # Erst in eine Temp-Datei, dann atomar ersetzen — ein Absturz mittendrin
    # hinterlässt so nie ein kaputtes Manifest.
    state_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(state_file.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_name, state_file)


def markdown_holen(cache_dir: Path, digest: str, path: Path, convert) -> str:
    """Konvertat aus dem Cache oder frisch — der Cache ist reiner Beschleuniger."""
    cache_datei = cache_dir / f"{digest}.md"
    if cache_datei.exists():
        return cache_datei.read_text(encoding="utf-8")
    markdown = convert(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_datei.write_text(markdown, encoding="utf-8")
    return markdown


def sync(*, docs_dir: Path, state_file: Path, cache_dir: Path, client,
         knowledge_id: str, convert) -> int:
    """Gleicht docs_dir gegen die Sammlung ab. Liefert die Zahl der Fehler."""
    # docscan.SUFFIXES enthält ".md": läge der Cache unter docs_dir, würde der
    # nächste Lauf die eigenen Konvertate als Dokumente einlesen.
    aufgeloest_docs = docs_dir.resolve()
    aufgeloest_cache = cache_dir.resolve()
    if aufgeloest_cache == aufgeloest_docs or aufgeloest_docs in aufgeloest_cache.parents:
        raise ValueError(
            f"CACHE_DIR ({cache_dir}) darf nicht in DOCS_DIR ({docs_dir}) liegen — "
            f"die Konvertate würden sich sonst selbst indexieren."
        )

    state = load_state(state_file)
    if not state_file.exists():
        # Manifest von Anfang an anlegen, auch wenn der erste Lauf komplett
        # scheitert (z.B. Upload abgelehnt) und die Schleifen unten daher
        # nie speichern — sonst ist state_file danach nicht lesbar.
        save_state(state_file, state)
    current = docscan.scan(docs_dir)
    in_sammlung = client.knowledge_file_ids(knowledge_id)

    # 1) Dateien, die es nicht mehr gibt
    for rel in sorted(set(state) - set(current)):
        file_id = state[rel]["file_id"]
        if file_id in in_sammlung:
            client.remove_file_from_knowledge(knowledge_id, file_id)
        del state[rel]
        save_state(state_file, state)
        log(f"Entfernt (Datei gelöscht): {rel}")

    # 2) Neue und geänderte Dateien
    fehler = 0
    for rel, path in current.items():
        try:
            digest = sha256_file(path)
            eintrag = state.get(rel)
            if eintrag and eintrag["sha256"] == digest and eintrag["file_id"] in in_sammlung:
                continue

            if eintrag and eintrag["file_id"] in in_sammlung:
                client.remove_file_from_knowledge(knowledge_id, eintrag["file_id"])

            markdown = markdown_holen(cache_dir, digest, path, convert)
            file_id = client.upload_markdown(zielname(rel), markdown)
            client.add_file_to_knowledge(knowledge_id, file_id)

            state[rel] = {"sha256": digest, "file_id": file_id}
            save_state(state_file, state)
            log(f"Übernommen: {rel} ({len(markdown)} Zeichen)")
        except Exception as exc:
            fehler += 1
            log(f"FEHLER bei {rel}: {exc}")

    log(f"Fertig. {len(current)} Dateien im Bestand, {fehler} Fehler.")
    return fehler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--create",
        action="store_true",
        help="Die Wissenssammlung anlegen, falls sie noch nicht existiert.",
    )
    args = parser.parse_args()

    if not DOCS_DIR.is_dir():
        log(f"FEHLER: Dokumentenordner {DOCS_DIR} existiert nicht.")
        return 1
    if not WEBUI_API_KEY_FILE.is_file():
        log(f"FEHLER: API-Key-Datei {WEBUI_API_KEY_FILE} fehlt.")
        return 1

    # Erst hier importieren: beide Module ziehen schwere Abhängigkeiten, und
    # die Fehlermeldungen oben sollen auch ohne venv lesbar sein.
    import docconvert
    import webui_client

    api_key = WEBUI_API_KEY_FILE.read_text(encoding="utf-8").strip()
    client = webui_client.WebUIClient(WEBUI_URL, api_key)

    try:
        knowledge_id = client.knowledge_id_by_name(KNOWLEDGE_NAME)
        if knowledge_id is None:
            if not args.create:
                log(
                    f"FEHLER: Sammlung {KNOWLEDGE_NAME!r} existiert nicht. "
                    f"Mit --create anlegen — oder KNOWLEDGE_NAME auf Tippfehler prüfen."
                )
                return 1
            knowledge_id = client.create_knowledge(
                KNOWLEDGE_NAME, "Automatisch befüllt von doc-sync.py"
            )
            log(f"Sammlung {KNOWLEDGE_NAME!r} angelegt ({knowledge_id}).")

        with lauf_sperre(LOCK_FILE):
            fehler = sync(
                docs_dir=DOCS_DIR,
                state_file=STATE_FILE,
                cache_dir=CACHE_DIR,
                client=client,
                knowledge_id=knowledge_id,
                convert=docconvert.to_markdown,
            )
    except LaufLaeuftBereits as exc:
        log(str(exc))
        return 1
    except webui_client.WebUIError as exc:
        log(f"FEHLER: Open WebUI nicht erreichbar oder lehnt ab — {exc}")
        return 1

    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
