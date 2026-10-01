"""Embedding backends.

- "local": sentence-transformers (free, offline after first model download).
- "openrouter": the gateway's /embeddings endpoint (provider-prefixed id).

Exposes a small facade so the ingestion pipeline and retrieval don't care
which backend is active.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger("app")

_EMBEDDER_CACHE: dict[str, Any] = {}


def get_embedding_dimension() -> int:
    """Return the dimensionality of the active embedding backend."""
    cfg = get_settings()
    if cfg.embedding_backend == "openrouter":
        # 9Router text-embedding-3-large -> 3072 (configurable via a probe)
        try:
            from app.models_openrouter import test_embedding

            res = test_embedding()
            if res.get("ok"):
                return int(res["dimensions"])
        except Exception:  # noqa: BLE001
            pass
        return 3072
    # local
    model = _session_or_none()
    if model is not None:
        try:
            return model.get_sentence_embedding_dimension()
        except Exception:  # noqa: BLE001
            pass
    return 384  # bge-small default


def _session_or_none():
    """Return a lazily-created sentence-transformers model (or None)."""
    key = "local_embedder"
    if key in _EMBEDDER_CACHE:
        return _EMBEDDER_CACHE[key]
    try:
        from sentence_transformers import SentenceTransformer

        cfg = get_settings()
        model = SentenceTransformer(cfg.local_embedding_model)
        _EMBEDDER_CACHE[key] = model
        logger.info("Loaded local embedding model %s", cfg.local_embedding_model)
        return model
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load local embedding model")
        _EMBEDDER_CACHE[key] = None
        raise RuntimeError(f"Local embedding model unavailable: {exc}") from exc


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts using the configured backend."""
    if not texts:
        return []
    cfg = get_settings()
    if cfg.embedding_backend == "openrouter":
        from app.models_openrouter import embed_texts as _gateway

        return _gateway(texts)
    # local
    model = _session_or_none()
    if model is None:
        raise RuntimeError("Local embedding model unavailable.")
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]
