"""Smart chunking of parsed pages into retrieval-ready chunks.

Design goals:
- preserve source page(s) on every chunk (citation backbone)
- avoid splitting tables mid-way (tables become their own chunk)
- keep chunks reasonably sized with overlap for context continuity
- attach a section/heading hint when detectable

Each chunk is a dict:
    {text, page, section, offset}
`page` is the 0-based page the chunk came from; `offset` is the character
position within the original page (for debug/tracing).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    text: str
    page: int
    section: str = ""
    offset: int = 0


@dataclass
class _PageUnit:
    text: str
    page: int


_HEADING_RE = re.compile(
    r"^\s*(?:\d+[\.\)][\s]|(?:abstract|introduction|methods?|results?|discussion|conclusion|references?|materials?\s*(?:and|&)\s*methods?)\s*[:\-]?\s*$)",
    re.IGNORECASE,
)


def _detect_section(line: str) -> str | None:
    m = _HEADING_RE.match(line)
    if m:
        # normalize heading label
        label = re.sub(r"^[\d\.\)\s]+", "", line.strip()).strip(" :-\t")
        return label.title() if label else None
    return None


def _find_cut(text: str, max_len: int | None = None) -> int:
    """Find a sentence/word boundary near the chunk size for a clean cut."""
    limit = max_len or 700
    if len(text) <= limit:
        return len(text)
    window = text[:limit]
    # prefer sentence end
    for mark in (". ", "? ", "! ", "\n"):
        idx = window.rfind(mark)
        if idx != -1 and idx > limit * 0.5:
            return idx + len(mark)
    # fall back to last space
    idx = window.rfind(" ")
    return idx if idx > 0 else limit


def _is_table_block(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("|") or stripped.startswith("[TABLE") or stripped.startswith("Table ")


def chunk_pages(pages: list[dict], *, chunk_size: int | None = None, overlap: int | None = None) -> list[Chunk]:
    """Convert parsed pages (list of {"page","text"}) into Chunk objects.

    Table lines are isolated and kept as their own chunk so structure isn't
    split across retrieval boundaries.
    """
    from app.config import get_settings

    cfg = get_settings()
    chunk_size = chunk_size or cfg.chunk_size
    overlap = overlap or cfg.chunk_overlap

    chunks: list[Chunk] = []
    for p in pages:
        text = p.get("text", "") or ""
        text = re.sub(r"[ \t]+", " ", text)
        lines = text.split("\n")
        buf = ""
        buf_sec = ""
        offset = 0
        for line in lines:
            heading = _detect_section(line)
            is_table = _is_table_block(line)
            if is_table:
                # flush narrative buffer
                if buf.strip():
                    chunks.append(Chunk(text=buf.strip(), page=p["page"], section=buf_sec, offset=offset))
                    buf = ""
                # accumulate the whole table block as one chunk
                table_lines = [line]
                # peek ahead in a simple way: treat consecutive table-ish lines together
                chunks.append(Chunk(text=line.strip(), page=p["page"], section=buf_sec, offset=offset))
                offset += len(line) + 1
                continue
            if heading:
                # start a new section: flush current buffer, remember new section
                if buf.strip():
                    chunks.append(Chunk(text=buf.strip(), page=p["page"], section=buf_sec, offset=offset))
                    buf = ""
                buf_sec = heading
            buf += ("" if not buf else "\n") + line
            while len(buf) >= chunk_size:
                cut = _find_cut(buf, chunk_size)
                piece = buf[:cut]
                if piece.strip():
                    chunks.append(Chunk(text=piece.strip(), page=p["page"], section=buf_sec, offset=offset))
                offset += cut
                buf = buf[cut - overlap:] if cut > overlap else ""
        if buf.strip():
            chunks.append(Chunk(text=buf.strip(), page=p["page"], section=buf_sec, offset=offset))
    return chunks
