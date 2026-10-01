"""On-demand per-document summaries (multiple kinds).

Pulls a document's substantive chunks (Abstract/Intro/Methods/Results first),
then asks the configured chat model to produce the requested summary kind:
  - "brief"     : concise overview (default, the original behaviour)
  - "detailed"  : a fuller walkthrough of aim/methods/results/conclusion
  - "key_points": bulleted key findings / takeaways
  - "tldr"      : one-line elevator pitch
  - "quiz"      : a short self-test Q&A (study notes)

Summaries are cached in the metadata store (document_summaries), so requesting
the same document+kind again returns instantly without spending tokens.
"""
from __future__ import annotations

import logging

from app.db.metadata import MetadataStore
from app.db.vectorstore import VectorStore
from app.models_openrouter import LLMClientError, chat_completion

logger = logging.getLogger("app")
_NS = "default"

KINDS = ("brief", "detailed", "key_points", "tldr", "quiz")

_SYSTEM_PROMPTS = {
    "brief": (
        "You are a scientific writing assistant. Given passages from one research "
        "paper, write a concise, neutral summary (about 3-5 sentences) that covers "
        "the study's aim, methods, key findings, and conclusion. Do not invent "
        "details that are not in the passages. If the text is mostly references or "
        "author metadata (no real article content), say so honestly instead of "
        "guessing the topic."
    ),
    "detailed": (
        "You are a scientific writing assistant. Given passages from one research "
        "paper, write a detailed, well-structured summary (about 6-10 sentences or "
        "short paragraphs) organised by: Aim, Methods, Key findings, and "
        "Conclusion/Limitations. Ground every point strictly in the passages; do "
        "not invent details. If there is no real article content, say so honestly."
    ),
    "key_points": (
        "You are a research assistant. Given passages from one research paper, "
        "produce a concise bulleted list of the most important key points and "
        "takeaways (aim, methods, 2-5 key findings with any notable numbers, and "
        "conclusion). Use short bullets starting with '- '. Ground everything in "
        "the passages and never invent numbers or claims."
    ),
    "tldr": (
        "You are an expert at distilling research. Given passages from one paper, "
        "write a single one-line 'TL;DR' that captures the study in at most ~25 "
        "words. No bullets, no headings, just one tight sentence."
    ),
    "quiz": (
        "You are a study assistant. Given passages from one research paper, create "
        "a short self-test for a student: exactly 3 multiple-choice questions "
        "covering the most important concepts, followed by an answer key. Format "
        "as:\n\nQ1) <question>\nA) ...\nB) ...\nC) ...\nD) ...\n\nThen 'Answer key: "
        "1-B, 2-A, 3-D'. Base every question strictly on the passages."
    ),
}

_USER_TEMPLATE = (
    'Here are passages from the document "{filename}":\n\n{excerpt}\n\n'
    "{instruction}"
)


def summarize_document(
    document_id: str,
    *,
    kind: str = "brief",
    max_chars: int = 9000,
    use_cache: bool = True,
    force: bool = False,
) -> dict:
    """Return a summary of one document for the requested `kind`.

    Returns {"document_id", "filename", "kind", "summary", "chunks_used",
    "pages", "cached"} on success, or raises LLMClientError / ValueError.
    `force=True` regenerates and overwrites any cached summary.
    """
    kind = (kind or "brief").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"Unknown summary kind '{kind}'. Choose from {', '.join(KINDS)}.")

    meta = MetadataStore()
    try:
        doc = meta.get_document(document_id)
        if doc is None:
            raise ValueError(f"Document {document_id} not found")

        # cached hit (unless forced)
        if use_cache and not force:
            cached = meta.get_summary(document_id, kind)
            if cached:
                cached["document_id"] = document_id
                cached["filename"] = doc.get("filename")
                cached["kind"] = kind
                cached["cached"] = True
                return cached
    finally:
        meta.close()

    vs = VectorStore(_NS)
    try:
        chunks = vs.chunks_for_document(document_id, max_chars=max_chars)
    finally:
        vs.close()

    if not chunks:
        raise ValueError("This document has no indexed content yet. Ingest it first.")

    excerpt = "\n\n".join(
        f"[page {c['source_page']}] {c['text']}" for c in chunks
    )
    pages = sorted({c["source_page"] for c in chunks if c["source_page"]})

    instruction = {
        "brief": "Write a concise summary of what this paper is about.",
        "detailed": "Write a detailed summary of this paper (aim, methods, findings, conclusion).",
        "key_points": "List the key points and takeaways of this paper.",
        "tldr": "Give a single-line TL;DR of this paper.",
        "quiz": "Create a short study quiz (3 MCQs + answer key) from this paper.",
    }[kind]

    user_msg = _USER_TEMPLATE.format(
        filename=doc.get("filename"), excerpt=excerpt, instruction=instruction
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPTS[kind]},
        {"role": "user", "content": user_msg},
    ]

    try:
        summary = chat_completion(messages)
    except LLMClientError as exc:
        logger.warning("Summary generation failed for %s: %s", document_id, exc)
        raise

    summary = summary.strip()

    # cache the result
    meta = MetadataStore()
    try:
        meta.save_summary(
            document_id, kind, summary, chunks_used=len(chunks), pages=pages
        )
    finally:
        meta.close()

    return {
        "document_id": document_id,
        "filename": doc.get("filename"),
        "kind": kind,
        "summary": summary,
        "chunks_used": len(chunks),
        "pages": pages,
        "cached": False,
    }


def summarize_many_documents(
    document_ids: list[str],
    *,
    kind: str = "brief",
    max_chars: int = 16000,
    force: bool = False,
) -> list[dict]:
    """Summarize several documents (used by podcast generation for context)."""
    out = []
    for did in document_ids:
        try:
            out.append(
                summarize_document(
                    did, kind=kind, max_chars=max_chars,
                    use_cache=True, force=force,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not summarize %s for podcast: %s", did, exc)
    return out
