"""Citation services (Phase 4): auto-extract/verify and resolution helpers."""
from __future__ import annotations

import logging

from app.citations.crossref import (
    CrossrefError,
    fetch_by_doi,
    search_by_title,
    title_from_pdf_first_page,
)
from app.db.metadata import MetadataStore

logger = logging.getLogger("app")


def auto_metadata(document_id: str) -> dict:
    """Best-effort auto-fill of a document's citation metadata.

    Strategy:
      1. If a DOI is already stored, fetch full metadata from Crossref.
      2. Else search Crossref by title (guessed from the filename or first
         page) and return ranked candidates.
    Stores nothing automatically — returns data for the user to confirm.
    """
    meta = MetadataStore()
    try:
        doc = meta.get_document(document_id)
        if not doc:
            raise ValueError(f"Document {document_id} not found.")
    finally:
        meta.close()

    doi = (doc.get("doi") or "").strip()

    # 1. DOI present -> authoritative fetch
    if doi:
        try:
            data = fetch_by_doi(doi)
        except CrossrefError as exc:
            data = None
            logger.warning("DOI fetch failed for %s: %s", document_id, exc)
        if data:
            return {"document_id": document_id, "auto": [data], "source": "doi"}

    # 2. Title search — prefer a real title extracted from the PDF's first page
    #    (far more accurate than guessing from the filename).
    title_guess = (
        (doc.get("title") or "").strip()
        or _title_from_pdf(doc.get("stored_path"))
        or _title_from_filename(doc.get("filename") or "")
    )
    if title_guess:
        try:
            candidates = search_by_title(title_guess)
        except CrossrefError as exc:
            logger.warning("Crossref title search failed: %s", exc)
            candidates = []
        if candidates:
            return {"document_id": document_id, "auto": candidates, "source": "title"}
        return {
            "document_id": document_id,
            "auto": [{"title": title_guess, "confidence": 0.0}],
            "source": "none",
        }

    return {"document_id": document_id, "auto": [], "source": "none"}


def confirm_metadata(document_id: str, meta_fields: dict) -> dict:
    """Save user-confirmed citation metadata for a document."""
    store = MetadataStore()
    try:
        store.set_citation_metadata(document_id, verified=True, **meta_fields)
        doc = store.get_document(document_id)
        return {"document_id": document_id, "saved": True, "doc": doc}
    finally:
        store.close()


def _title_from_pdf(stored_path) -> str:
    """Extract a plausible title from the first page of a stored PDF, if any."""
    import re

    from app.ingest.parsers import parse_pdf

    if not stored_path:
        return ""
    try:
        pages = parse_pdf(stored_path, use_ocr=False)
    except Exception:  # noqa: BLE001
        return ""
    if not pages or not pages[0].get("text"):
        return ""
    first = pages[0]["text"]
    lines = [ln.strip() for ln in first.splitlines() if ln.strip()]

    # Lines that look like headers/copyright/author/affiliation noise, never a title.
    skip = re.compile(
        r"^(doi|http|issn|isbn|vol\.?\s*\d|issue|no\.?\s*\d|©|copyright|"
        r"licensed|received|accepted|author affiliation|corresponding|journal|"
        r"article|\d{4}$|^p\.?\d|\bcell\b|\*$)",
        re.IGNORECASE,
    )
    # Candidate title = accumulate short consecutive lines that aren't noise.
    title_parts: list[str] = []
    for line in lines:
        if skip.match(line) or re.match(r"^\d{1,3}$", line):
            if title_parts:
                break  # stop at the first non-title boundary after starting
            continue
        # a plausible title line: sentence-like, not an abstract/author block
        if line.endswith((":", "-", "©")) or len(line) < 4:
            continue
        title_parts.append(line)
        if sum(len(p) for p in title_parts) >= 50:
            break
        if len(title_parts) >= 3:
            break
    if not title_parts:
        return ""
    title = " ".join(title_parts).strip()
    # strip trailing punctuation-only tokens
    return title.rstrip(" .,;:!?") or ""


def _title_from_filename(filename: str) -> str:
    """Guess a searchable title from a filename."""

    import re

    name = filename or ""
    name = re.sub(r"\.(pdf|docx?)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[\-_]+", " ", name)
    return name.strip() or ""


def list_verified_documents() -> list[dict]:
    """Documents that have citation metadata confirmed by the user."""
    store = MetadataStore()
    try:
        docs = store.list_documents()
    finally:
        store.close()
    return [d for d in docs if d.get("citation_verified")]
