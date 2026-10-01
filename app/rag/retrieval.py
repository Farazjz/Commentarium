"""Retrieval: turn a user question into relevant source chunks.

Pipeline: embed the question -> cosine search the vector store -> return the
top-k chunks with their document/page metadata (the foundation for citations).

Two modes:
  - focused (default): global top-k (best chunks overall).
  - distributed: top-K per document, so every article contributes. Better for
    overview / comparison questions ("which papers are about X?").

Section weighting down-weights noise chunks (References, author blocks) so they
don't crowd out substantive content (Abstract / Intro / Methods / Results).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.config import get_settings
from app.db.vectorstore import VectorStore
from app.ingest.embeddings import embed_texts

logger = logging.getLogger("app")
_NS = "default"

# Boosts applied to a chunk's cosine score based on its section (0..~1.2).
_SECTION_WEIGHT = {
    # substantive -> small boost
    "abstract": 1.20,
    "introduction": 1.15,
    "methods": 1.10,
    "materials and methods": 1.10,
    "results": 1.10,
    "discussion": 1.05,
    "conclusion": 1.05,
    # noise -> heavy penalty
    "references": 0.10,
    "bibliography": 0.10,
    "author": 0.20,
}
_NOISE_HEAD_RE = re.compile(
    r"^(references?|bibliography|author affiliation|corresponding author|"
    r"author information|acknowledg?ements|funding|supplementary)\b",
    re.IGNORECASE,
)
_ACK_RE = re.compile(
    r"^\s*(corresponding author|author affiliation|running head|journal of|"
    r"volume|issue|doi:)\b",
    re.IGNORECASE,
)


def _section_weight(section: str) -> float:
    """Return a multiplier for a chunk, penalizing noise sections."""
    s = _noise_or_section(section or "")
    return _SECTION_WEIGHT.get(s, 1.0)


def _noise_or_section(section: str) -> str:
    """Map a detected section to a weight key (normalized)."""
    low = (section or "").strip().lower()
    # exact + acronym matches
    if low in ("references", "bibliography", "reference", "author", "authors"):
        return low if low != "reference" else "references"
    if _NOISE_HEAD_RE.match(low):
        return _NOISE_HEAD_RE.match(low).group(1).lower()
    return low


def _is_noise_text(text: str) -> bool:
    """Heuristic: is this chunk mostly author/affiliation/noise content?"""
    head = (text or "").lstrip()
    if _ACK_RE.match(head):
        return True
    return False


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: str
    project_id: str
    text: str
    page: int | None
    section: str
    score: float


@dataclass
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk] = field(default_factory=list)


def retrieve(
    query: str,
    *,
    project_id: str | None = None,
    top_k: int | None = None,
    distributed: bool = False,
    per_doc: int | None = None,
) -> RetrievalResult:
    """Embed the query and return the most relevant chunks.

    distributed=True: return top-per_doc chunks per document (for overview
    questions). Otherwise: global top_k.
    All candidate scores are section-weighted before ranking.
    """
    cfg = get_settings()
    top_k = top_k or cfg.top_k

    if not query.strip():
        return RetrievalResult(query=query)

    query_vec = embed_texts([query.strip()])
    if not query_vec:
        logger.warning("Embedding of query produced no vector")
        return RetrievalResult(query=query)

    vs = VectorStore(_NS)
    try:
        if distributed:
            per_doc = per_doc or 4
            raw_hits = vs.search_distributed(
                query_vec[0], per_doc=per_doc, project_id=project_id
            )
            # For overview questions, guarantee each document's Abstract/Intro
            # is included so the model can state each paper's topic — even when
            # the cosine-top-k lands on Results or data tables instead.
            _abs_intro = {"abstract", "introduction", "materials and methods"}
            doc_ids_in_hits = {h["document_id"] for h in raw_hits}
            for did in doc_ids_in_hits:
                try:
                    doc_chunks = vs.chunks_for_document(did, prefer_sections=True)
                except Exception:  # noqa: BLE001
                    continue
                seen_ids = {h["chunk_id"] for h in raw_hits}
                for c in doc_chunks:
                    sec = (c.get("section") or "").strip().lower()
                    if sec in _abs_intro and c["chunk_id"] not in seen_ids:
                        raw_hits.append({
                            "chunk_id": c["chunk_id"],
                            "document_id": c["document_id"],
                            "project_id": project_id or "",
                            "text": c["text"],
                            "source_page": c["source_page"],
                            "section": c["section"],
                            "offset": c["offset"],
                            "score": 0.001,
                        })
                        seen_ids.add(c["chunk_id"])
        else:
            raw_hits = vs.search(query_vec[0], top_k=top_k, project_id=project_id)
    except Exception as exc:  # noqa: BLE001
        from app.db.vectorstore import DimensionMismatchError  # cheap local import

        vs.close()
        if isinstance(exc, DimensionMismatchError):
            raise RuntimeError(
                str(exc) + " Re-index all documents with the new model (Projects "
                "page → Re-index per document) or switch the embedding model back "
                "in Settings."
            ) from exc
        raise
    finally:
        vs.close()

    # Section weighting: discard hard-noise chunks and rescore the rest.
    weighted = []
    for h in raw_hits:
        if _is_noise_text(h["text"]):
            continue
        w = _section_weight(h.get("section") or "")
        h["score"] = h["score"] * w
        weighted.append(h)
    weighted.sort(key=lambda h: -h["score"])
    if not distributed:
        weighted = weighted[:top_k]
    else:
        # Cap the blended overview pool so we don't flood the prompt with
        # near-duplicate abstract/ref chunks.
        weighted = weighted[: (top_k or 8) * 3]

    chunks = [
        RetrievedChunk(
            chunk_id=h["chunk_id"],
            document_id=h["document_id"],
            project_id=h["project_id"],
            text=h["text"],
            page=h["source_page"],
            section=h["section"],
            score=h["score"],
        )
        for h in weighted
    ]
    logger.info(
        "Retrieved %d chunks (project=%s, distributed=%s, top_k=%s)",
        len(chunks), project_id, distributed, top_k,
    )
    return RetrievalResult(query=query, chunks=chunks)
