"""Citation resolution: turn source tags from answers into a reference list.

An answer's `sources` are `{document_id, page}` pairs produced by Phase 3.
This layer looks up each document's verified citation metadata and renders:
  - APA: in-text "(Author, Year)" marks and a References list.
  - Vancouver: numbered in-text "[n]" marks and a numbered reference list.

The source tags are replaced inline so the displayed answer reads with
citations, and a bibliography is produced for export.
"""
from __future__ import annotations

import logging
import re

from app.citations.formatters import format_reference, in_text_label, _parse_authors
from app.db.metadata import MetadataStore

logger = logging.getLogger("app")

# Inline [SRC:document_id|p12] markers in the answer text.
_SRC_RE = re.compile(r"\[SRC:([A-Za-z0-9_\-]+)\|p(\d+)\]")


def _load_docs(source_ids: set[str]) -> dict[str, dict]:
    """Load document metadata dicts by id (id -> full row)."""
    if not source_ids:
        return {}
    meta = MetadataStore()
    try:
        out = {}
        for doc_id in source_ids:
            d = meta.get_document(doc_id)
            if d:
                out[doc_id] = d
        return out
    finally:
        meta.close()


def _order_sources(sources: list[dict], doc_map: dict[str, dict]) -> list[dict]:
    """Return sources sorted by (first author surname, year) — APA style."""
    def key(s):
        d = doc_map.get(s["document_id"], {})
        authors = _parse_authors(d.get("authors"))
        surname = "zzzz"
        if authors:
            surname = authors[0].split()[-1].lower()
        title = (d.get("title") or "").lower()
        return (surname, d.get("year") or 0, title)
    return sorted(sources, key=key)


def resolve_citations(
    answer_text: str,
    sources: list[dict],
    *,
    style: str = "apa",
) -> dict:
    """Resolve source tags into in-text citations and a bibliography.

    answer_text may still contain raw [SRC:...] tags (from the generator) or
    already-stripped text; if tags remain they are replaced with the
    appropriate in-text marker.
    """
    style = (style or "apa").lower().strip()
    if style not in ("apa", "vancouver"):
        style = "apa"

    # Deduplicate sources by (document_id, page)
    uniq: dict[tuple[str, int], dict] = {}
    for s in sources:
        key = (s["document_id"], s.get("page"))
        uniq.setdefault(key, s)
    uniq_sources = list(uniq.values())

    doc_ids = {s["document_id"] for s in uniq_sources}
    doc_map = _load_docs(doc_ids)

    # --- build a stable index for vancouver / citation keys ---
    seen_doc_label: dict[str, str] = {}
    references: list[dict] = []

    if style == "apa":
        ordered = _order_sources(uniq_sources, doc_map)
        # group by document (identical reference) with distinct page markers
        by_doc: dict[str, dict] = {}
        for s in uniq_sources:
            d = doc_map.get(s["document_id"])
            if not d:
                continue
            label = in_text_label(d, "apa")
            by_doc.setdefault(s["document_id"], {"doc": d, "label": label, "pages": set()})
            by_doc[s["document_id"]]["pages"].add(s.get("page"))

        for doc_id, entry in by_doc.items():
            references.append({
                "document_id": doc_id,
                "key": entry["label"],
                "text": format_reference(entry["doc"], "apa"),
                "pages": sorted(entry["pages"]),
            })

        def repl(m):
            doc_id, page = m.group(1), m.group(2)
            d = doc_map.get(doc_id)
            if not d:
                return ""
            label = in_text_label(d, "apa")
            # label looks like "(Smith et al., 2021)"; strip outer parens
            inner = label.strip().strip("()")
            return f"({inner})"
        intext = _SRC_RE.sub(repl, answer_text)

    else:  # vancouver — numbered by first appearance
        order: dict[str, int] = {}
        counter = 0
        for s in uniq_sources:
            d = doc_map.get(s["document_id"])
            if not d:
                continue
            if s["document_id"] not in order:
                counter += 1
                order[s["document_id"]] = counter
                references.append({
                    "document_id": s["document_id"],
                    "key": str(counter),
                    "text": format_reference(d, "vancouver"),
                    "pages": sorted({x.get("page") for x in uniq_sources if x["document_id"] == s["document_id"]}),
                })

        def repl_v(m):
            doc_id = m.group(1)
            n = order.get(doc_id)
            return f"[{n}]" if n else ""
        intext = _SRC_RE.sub(repl_v, answer_text)

    return {
        "style": style,
        "answer": intext.strip(),
        "references": references,
    }


def format_bibliography(references: list[dict], style: str = "apa") -> str:
    """Render the references list as plain text (for copy/export)."""
    out = []
    for i, ref in enumerate(references, start=1):
        if style == "apa":
            out.append(f"{i}. {ref['text']}")
        else:
            out.append(f"{ref['key']}. {ref['text']}")
    return "\n".join(out)
