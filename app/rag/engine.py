"""Chat engine: orchestrate retrieval + generation + history persistence.

`ask(...)` performs one turn: retrieve relevant chunks, generate a grounded
answer, persist both sides of the conversation, and return the answer with
its resolved sources (which the UI presents alongside citations).
"""
from __future__ import annotations

import logging

from app.db.chat import ChatStore
from app.db.metadata import MetadataStore
from app.config import get_settings
from app.citations.resolver import resolve_citations
from app.models_openrouter import LLMClientError
from app.rag.generator import Answer, generate_answer
from app.rag.retrieval import retrieve

logger = logging.getLogger("app")


class ChatError(Exception):
    pass


_OVERVIEW_HINTS = [
    "how many", "what are", "summarize", "overview", "list the", "which papers",
    "which articles", "describe each", "compare", "all documents", "all papers",
]


def _looks_like_overview(question: str) -> bool:
    """Heuristic: is the user asking about the whole corpus, not a detail?"""
    q = (question or "").lower()
    return any(h in q for h in _OVERVIEW_HINTS)


def _resolve_runtime(
    *,
    project_id: str | None,
    style: str | None,
    top_k: int | None,
    temperature: float | None,
    model: str | None,
) -> dict:
    """Resolve per-project overrides vs. global defaults for a chat turn.

    Precedence: an explicit call argument > per-project override > global cfg.
    """
    cfg = get_settings()
    resolved = {
        "style": style or cfg.citation_style,
        "top_k": top_k,
        "temperature": temperature if temperature is not None else cfg.chat_temperature,
        "model": model,
    }
    if project_id:
        meta = MetadataStore()
        try:
            proj = meta.get_project(project_id)
        finally:
            meta.close()
        if proj:
            # per-project top_k applies only when the caller didn't override it
            if resolved["top_k"] is None:
                resolved["top_k"] = proj.get("top_k") or cfg.top_k
            # per-project temperature applies only when the caller didn't override
            if resolved["temperature"] == cfg.chat_temperature:
                ptemp = proj.get("chat_temperature")
                if ptemp:
                    resolved["temperature"] = float(ptemp)
            # per-project model applies only when the caller didn't override
            if not resolved["model"]:
                resolved["model"] = proj.get("chat_model") or cfg.chat_model
    if resolved["top_k"] is None:
        resolved["top_k"] = cfg.top_k
    if not resolved["model"]:
        resolved["model"] = cfg.chat_model
    return resolved


def ask(
    question: str,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    top_k: int | None = None,
    style: str | None = None,
    distributed: bool | None = None,
    temperature: float | None = None,
    model: str | None = None,
) -> dict:
    """Ask a question over the corpus (optionally constrained to a project).

    Returns:
        {
          "answer": display text (tags stripped),
          "sources": [ {document_id, page, section, text, score} ],
          "session_id": str,
          "retrieved": int,
        }
    Raises ChatError on downstream failures.
    """
    cfg = get_settings()
    runtime = _resolve_runtime(
        project_id=project_id,
        style=style,
        top_k=top_k,
        temperature=temperature,
        model=model,
    )
    style = runtime["style"]
    top_k = runtime["top_k"]
    temperature = runtime["temperature"]
    model = runtime["model"]
    store = ChatStore()
    try:
        # ensure a session exists
        if not session_id:
            session = store.create_session(project_id or "", "Untitled")
            session_id = session["id"]

        # persist the user question
        store.add_message(session_id, "user", question)

        # retrieve (auto-detect overview questions unless caller overrides)
        if distributed is None:
            distributed = _looks_like_overview(question)
        try:
            result = retrieve(question, project_id=project_id, top_k=top_k, distributed=distributed)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Retrieval failed")
            raise ChatError(f"Retrieval failed: {exc}") from exc

        # generate with recent history
        history = store.history_for_llm(session_id, max_turns=6)
        try:
            answer: Answer = generate_answer(
                question, result.chunks, history=history,
                temperature=temperature, model=model,
            )
        except LLMClientError as exc:
            logger.error("Generation failed: %s", exc)
            raise ChatError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Generation failed unexpectedly")
            raise ChatError(f"Generation failed: {exc}") from exc

        # persist assistant answer + sources
        source_dicts = [
            {"document_id": s.document_id, "page": s.page}
            for s in answer.sources
        ]
        store.add_message(session_id, "assistant", answer.text, source_dicts)

        # resolve citations for this answer using the configured style
        resolved = resolve_citations(answer.text, source_dicts, style=style)

        return {
            "answer": resolved["answer"],
            "sources": answer.sources,
            "used_chunks": answer.used_chunks,
            "session_id": session_id,
            "retrieved": len(result.chunks),
            "source_dicts": source_dicts,
            "citation_style": style,
            "references": resolved["references"],
        }
    finally:
        store.close()
