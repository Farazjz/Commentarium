"""Answer generation: run the grounded RAG prompt and extract source tags.

The model returns text with inline `[SRC:<doc_id>|p<page>]` tags. This module
parses those tags into a structured list of sources so downstream layers can
render accurate citations (and so the app can display "grounded in page X of
doc Y" even before full Phase 4 metadata is configured).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.config import get_settings
from app.models_openrouter import LLMClientError, chat_completion
from app.rag.prompts import build_messages
from app.rag.retrieval import RetrievedChunk

logger = logging.getLogger("app")

# Matches [SRC:document_id|p12]  (document_id may contain letters/digits/_-)
_SRC_RE = re.compile(r"\[SRC:([A-Za-z0-9_\-]+)\|p(\d+)\]")


@dataclass
class AnswerSource:
    document_id: str
    page: int

    def __post_init__(self) -> None:
        self.page = int(self.page)


@dataclass
class Answer:
    text: str
    sources: list[AnswerSource] = field(default_factory=list)
    used_chunks: list[dict] = field(default_factory=list)  # resolved chunk info


def extract_sources(text: str) -> list[AnswerSource]:
    """Parse all [SRC:doc|pN] tags from the model's answer text."""
    sources: list[AnswerSource] = []
    seen: set[tuple[str, int]] = set()
    for m in _SRC_RE.finditer(text):
        key = (m.group(1), int(m.group(2)))
        if key not in seen:
            seen.add(key)
            sources.append(AnswerSource(document_id=key[0], page=key[1]))
    return sources


def strip_tags(text: str) -> str:
    """Remove [SRC:...] tags for clean display (citations rendered separately)."""
    return _SRC_RE.sub("", text).strip()


def generate_answer(
    question: str,
    chunks: list[RetrievedChunk],
    *,
    history: list[dict] | None = None,
    temperature: float | None = None,
    model: str | None = None,
) -> Answer:
    """Generate a grounded answer and parse its source tags.

    Raises LLMClientError on API/configuration problems.
    """
    messages = build_messages(question, chunks, history)
    raw = chat_completion(
        messages, temperature=temperature if temperature is not None else 0.2,
        model=model,
    )

    sources = extract_sources(raw)
    clean_text = strip_tags(raw)

    # map resolved sources back to the chunk that produced them (for context/preview)
    chunk_by_doc_page: dict[tuple[str, int], dict] = {}
    for c in chunks:
        page = c.page if c.page is not None else 0
        chunk_by_doc_page[(c.document_id, page)] = {
            "document_id": c.document_id,
            "page": c.page,
            "section": c.section,
            "text": c.text,
            "score": c.score,
        }
    used = [
        chunk_by_doc_page[(s.document_id, s.page)]
        for s in sources
        if (s.document_id, s.page) in chunk_by_doc_page
    ]

    logger.info("Generated answer with %d source tags", len(sources))
    return Answer(text=clean_text, sources=sources, used_chunks=used)
