"""Citation metadata: auto-extract from documents and query Crossref.

Two paths produce verified citation metadata for a document:
  1. DOI lookup — if a DOI is known, fetch full bibliographic metadata from
     Crossref and store it for citation rendering.
  2. Title-based search — query Crossref by title (optionally filtered by
     author/year) so the user can confirm the correct match.

All network calls are optional; if the machine is offline or a match isn't
found, the system degrades to whatever metadata the user enters manually.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse

from app.httpclient import get

logger = logging.getLogger("app")

CROSSREF_API = "https://api.crossref.org/works"


class CrossrefError(Exception):
    pass


def _normalize(s: str) -> str:
    """Lowercase, de-accent, strip non-alnum for fuzzy matching."""
    s = s or ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _author_name(a: dict) -> str | None:
    fn = (a.get("given") or "").strip()
    ln = (a.get("family") or "").strip()
    if ln and fn:
        return f"{fn} {ln}"
    if ln:
        return ln
    if fn:
        return fn
    return None


def _normalize_crossref_item(item: dict) -> dict:
    """Map a Crossref work record to our canonical citation metadata dict."""
    title = ""
    if item.get("title"):
        title = item["title"][0]
    elif item.get("container-title"):
        title = item["container-title"][0]

    authors = [_author_name(a) for a in item.get("author", []) if _author_name(a)]

    publisher = ""
    if item.get("container-title"):
        publisher = item["container-title"][0]
    elif item.get("publisher"):
        publisher = str(item.get("publisher"))

    year = None
    for key in ("published-print", "published-online", "issued", "published"):
        parts = item.get(key) or {}
        if parts.get("date-parts") and parts["date-parts"][0]:
            try:
                year = int(parts["date-parts"][0][0])
                if year:
                    break
            except (TypeError, ValueError, IndexError):
                continue

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "journal": publisher,
        "doi": (item.get("DOI") or "").lower(),
        "volume": item.get("volume") or "",
        "issue": item.get("issue") or "",
        "pages": item.get("page") or item.get("article-number") or "",
        "url": item.get("URL") or "",
    }


def fetch_by_doi(doi: str, *, timeout: float = 20.0) -> dict | None:
    """Fetch citation metadata for a DOI. Returns canonical dict or None."""
    doi = (doi or "").strip()
    if not doi:
        return None
    try:
        r = get(f"{CROSSREF_API}/{urllib.parse.quote(doi)}", timeout=timeout)
        r.raise_for_status()
        item = r.json()["message"]
        return _normalize_crossref_item(item)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Crossref DOI lookup failed for %s: %s", doi, exc)
        raise CrossrefError(f"Crossref lookup failed: {exc}") from exc


def search_by_title(
    title: str,
    *,
    author: str | None = None,
    year: int | None = None,
    limit: int = 5,
    timeout: float = 20.0,
) -> list[dict]:
    """Search Crossref by title and return a ranked list of candidates.

    Returns a list of canonical metadata dicts, best match first, with a
    `confidence` float (0-1) for the top hit when it matches strongly.
    """
    q = (title or "").strip()
    if not q:
        return []
    params: dict = {"query.bibliographic": q, "rows": max(limit, 1)}
    try:
        r = get(CROSSREF_API, params=params, timeout=timeout,
                headers={"User-Agent": "ThesisRAG/1.0 (mailto:dev@example.com)"})
        r.raise_for_status()
        items = r.json()["message"]["items"]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Crossref title search failed: %s", exc)
        raise CrossrefError(f"Crossref search failed: {exc}") from exc

    qn = _normalize(q)
    candidates = []
    for item in items:
        meta = _normalize_crossref_item(item)
        title_n = _normalize(meta["title"])
        # fuzzy title similarity
        q_words, t_words = set(qn.split()), set(title_n.split())
        common = q_words & t_words
        denom = max(len(q_words), 1)
        confidence = len(common) / denom if q_words else 0.0

        # author filter (soft): favor matches sharing a surname
        if author:
            an = _normalize(author)
            surnames = [_normalize(a.split()[-1]) for a in meta["authors"] if a]
            if any(an.endswith(sn) or sn in an for sn in surnames if sn):
                confidence += 0.2
        if year and meta["year"] == year:
            confidence += 0.15

        meta["confidence"] = round(min(confidence, 1.0), 3)
        candidates.append(meta)

    candidates.sort(key=lambda m: m["confidence"], reverse=True)
    return candidates[:limit]


def title_from_pdf_first_page(text: str) -> str | None:
    """Naive best-effort title guess from the first page's raw text.

    Reserved for when Crossref can't help; usually the user confirms anyway.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    for line in lines[:3]:
        if 6 <= len(line) <= 200 and not re.match(r"^(doi|http|vol|iss)", line, re.IGNORECASE):
            return line
    return lines[0] if lines else None
