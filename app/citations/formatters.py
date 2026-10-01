"""Bibliography formatters: APA 7 and Vancouver (NLM) reference strings.

Input is a canonical citation-metadata dict (the shape produced by
`app.citations.crossref` and stored on each document) plus a page range for
illustrative in-text / full-reference purposes.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("app")


def _parse_authors(authors) -> list[str]:
    """Normalize authors from list or JSON string into a list of names."""
    if authors is None:
        return []
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except json.JSONDecodeError:
            return []
    if not isinstance(authors, list):
        return []
    return [a for a in authors if a]


def _split_author(name: str) -> tuple[str, str]:
    """Split 'Given Family' into (family, given). Best effort."""
    parts = [p for p in (name or "").strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    # Last token = family, the rest = given
    return parts[-1], " ".join(parts[:-1])


def _strip_punct(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isalnum())


def format_reference(meta: dict, style: str = "apa") -> str:
    """Return a single formatted reference string for a document's metadata."""
    style = (style or "apa").lower().strip()
    if style not in ("apa", "vancouver"):
        style = "apa"
    return _format_apa(meta) if style == "apa" else _format_vancouver(meta)


# --------------------------------------------------------------------------
# APA 7
# --------------------------------------------------------------------------
def _format_apa(meta: dict) -> str:
    authors = _parse_authors(meta.get("authors"))
    title = (meta.get("title") or "").strip()
    journal = (meta.get("journal") or "").strip()
    year = meta.get("year")
    volume = (meta.get("volume") or "").strip()
    issue = (meta.get("issue") or "").strip()
    pages = (meta.get("pages") or "").strip()
    doi = (meta.get("doi") or "").strip()

    # --- authors: Family, G. & Family, G. ---
    apa_authors = []
    for name in authors:
        fam, given = _split_author(name)
        if not fam:
            continue
        initials = ""
        for word in given.replace(".", " ").split():
            if word:
                initials += word[0].upper() + ". "
        apa_authors.append(f"{fam}, {initials}".rstrip())
    ref = ""
    if apa_authors:
        if len(apa_authors) == 1:
            ref += apa_authors[0]
        elif len(apa_authors) <= 7:
            ref += ", ".join(apa_authors[:-1]) + f" & {apa_authors[-1]}"
        else:
            ref += f"{apa_authors[0]} ... {apa_authors[-1]}"
        if not ref.endswith("."):
            ref += "."
    else:
        ref += (meta.get("journal") or meta.get("publisher") or "Anonymous").strip() + "."
    ref += " "

    # (Year).
    if year:
        ref += f"({year}). "
    else:
        ref += "(n.d.). "

    # Title.
    if title:
        ref += f"{title}. "

    # Journal + volume(issue), pages.
    if journal:
        ref += f"{journal}"
        if volume:
            ref += f", {volume}"
            if issue:
                ref += f"({issue})"
        if pages:
            ref += f", {pages}"
        ref += "."

    # DOI
    if doi:
        ref += f" https://doi.org/{doi}"

    return ref.strip()


# --------------------------------------------------------------------------
# Vancouver (NLM)
# --------------------------------------------------------------------------
def _format_vancouver(meta: dict) -> str:
    authors = _parse_authors(meta.get("authors"))
    title = (meta.get("title") or "").strip()
    journal = (meta.get("journal") or "").strip()
    year = meta.get("year")
    volume = (meta.get("volume") or "").strip()
    issue = (meta.get("issue") or "").strip()
    pages = (meta.get("pages") or "").strip()
    doi = (meta.get("doi") or "").strip()

    # --- authors: Family GG, Family GG. (up to 6, then et al) ---
    van_authors = []
    for name in authors:
        fam, given = _split_author(name)
        initials = "".join(ch[0] for ch in given.replace(".", " ").split() if ch)
        initials = initials.upper()
        van_authors.append(f"{fam} {initials}".strip())
    # max 6 authors, then "et al."
    if len(van_authors) > 6:
        van_authors = van_authors[:6] + ["et al."]

    ref = ""
    if van_authors:
        ref += ", ".join(van_authors) + "."
    else:
        ref += (journal or "Anonymous").strip() + "."
    ref += " "

    # Title.
    if title:
        # Sentence case in Vancouver (capitalize first word)
        ref += f"{title}. "

    # Journal.
    if journal:
        ref += f"{journal}"

    # Year;month;day
    if year:
        ref += f";{year}"
    if volume:
        ref += f";{volume}"
        if issue:
            ref += f"({issue})"
    if pages:
        ref += f":{pages}"
    ref += "."

    if doi:
        ref += f" doi: {doi}"

    return ref.strip()


# --------------------------------------------------------------------------
# In-text / narrative renderings
# --------------------------------------------------------------------------
def in_text_label(meta: dict, style: str = "apa") -> str:
    """Short in-text marker for showing which source a claim comes from."""
    style = (style or "apa").lower().strip()
    authors = _parse_authors(meta.get("authors"))
    year = meta.get("year")

    if style == "apa":
        if len(authors) >= 3:
            label = f"{_split_author(authors[0])[0]} et al."
        elif len(authors) == 2:
            label = f"{_split_author(authors[0])[0]} & {_split_author(authors[1])[0]}"
        elif len(authors) == 1:
            label = _split_author(authors[0])[0]
        else:
            label = (meta.get("citation_key") or meta.get("title") or "?").strip()[:30]
        return f"({label}, {year or 'n.d.'})"
    else:  # vancouver numeric handled by resolver; return keyword label
        kw = _split_author(authors[0])[0] if authors else (meta.get("citation_key") or "ref")
        return f"[{kw} {year or ''}]".strip()
