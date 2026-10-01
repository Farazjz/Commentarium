"""Prompt templates for the RAG answer generator.

The key design: the model is instructed to answer ONLY from the provided
chunks and to attach a machine-parseable source tag to every claim:
    [SRC:<document_id>|p<page>]
These tags are later resolved by the citation layer (Phase 4) into real
in-text citations + a references list. If the model can't support a claim
from a chunk, it must say so rather than invent a source.
"""
from __future__ import annotations

from app.rag.retrieval import RetrievedChunk

SYSTEM_GROUNDED = """\
You are a meticulous research assistant helping a graduate student with a
masters thesis in biotechnology. You answer questions based ONLY on the
provided source passages from their uploaded papers.

RULES:
1. Answer strictly from the provided context below. Do NOT use outside
   knowledge unless the context is insufficient, in which case say so clearly.
2. For EVERY factual claim, append a source tag in this exact format after the
   relevant sentence or clause:
       [SRC:{document_id}|p{page}]
   Use the exact {document_id} and {page} values given for each passage.
3. If the same claim is supported by multiple passages, cite each of them with
   its own tag, e.g. [SRC:doc_a|p3][SRC:doc_b|p7].
4. Never invent a source that is not in the provided context. If nothing in the
   context answers the question, reply: "I couldn't find this in your uploaded
   documents." and do not fabricate tags.
5. Be precise and scientific. Quote figures, methods, and conclusions exactly
   when the user needs accuracy. Keep the response focused.
6. Use plain text or light markdown. Do not create a reference list yourself —
   the citation formatting is handled separately.
"""


def build_context(chunks: list[RetrievedChunk], doc_names: dict[str, str] | None = None) -> str:
    """Render the retrieved chunks into a numbered context block.

    doc_names: optional mapping of document_id -> filename so the model can
    refer to papers by their actual name (e.g. "2023-alkhalidi.pdf") rather
    than opaque IDs.
    """
    blocks = []
    for i, c in enumerate(chunks, start=1):
        page = c.page if c.page is not None else 0
        name = (doc_names or {}).get(c.document_id, c.document_id)
        header = f"[{i}] document={name} (id={c.document_id}) | page={page} | section={c.section or 'n/a'}"
        blocks.append(f"{header}\n{c.text}")
    return "\n\n".join(blocks)


def build_user_message(question: str, context: str, doc_count: int = 0) -> str:
    doc_note = ""
    if doc_count > 1:
        doc_note = (
            f"\nNOTE: You have been given passages from {doc_count} distinct "
            f"documents. If a document's content only includes references or "
            f"author metadata (not the actual article text), say you could not "
            f"determine its topic from the available context.\n"
        )
    return f"""CONTEXT (from the user's uploaded documents):
{context}
{doc_note}QUESTION:
{question}

Answer the question following the rules. Provide every claim with its [SRC:...] tag."""


def build_messages(question: str, chunks: list[RetrievedChunk], history: list[dict] | None = None) -> list[dict]:
    """Assemble the full chat messages for the LLM call.

    history: list of {"role","content"} prior turns (optional).
    """
    messages: list[dict] = [{"role": "system", "content": SYSTEM_GROUNDED}]
    if history:
        messages.extend(history)
    doc_ids = {c.document_id for c in chunks}
    doc_names: dict[str, str] = {}
    if doc_ids:
        from app.db.metadata import MetadataStore
        meta = MetadataStore()
        try:
            for did in doc_ids:
                d = meta.get_document(did)
                if d and d.get("filename"):
                    doc_names[did] = d["filename"]
        finally:
            meta.close()
    context = build_context(chunks, doc_names)
    messages.append({
        "role": "user",
        "content": build_user_message(question, context, doc_count=len(doc_ids)),
    })
    return messages
