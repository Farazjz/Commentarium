"""Embedding backends.

- "local":      sentence-transformers. Uses a local model directory when
                LOCAL_EMBEDDING_PATH is set (fully offline); otherwise it loads
                the model id (downloading once from HuggingFace). HF_OFFLINE=1
                forces the local cache and never touches the network.
- "api":        any OpenAI-compatible /embeddings endpoint (OpenRouter, Google
                Gemini, OpenAI, a local proxy…). Uses EMBEDDING_API_BASE_URL +
                EMBEDDING_API_KEY + EMBEDDING_MODEL; empty base/key fall back to
                the OpenRouter gateway settings.
- "cloudflare": Cloudflare Workers AI embeddings (needs CF_ACCOUNT_ID + CF_API_TOKEN).

Exposes a small facade so the ingestion pipeline and retrieval don't care
which backend is active.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.config import get_settings

logger = logging.getLogger("app")

_EMBEDDER_CACHE: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Config resolution helpers
# ---------------------------------------------------------------------------
def embedding_api_config():
    """Return (base_url, api_key, model) for the OpenAI-compatible api backend.

    Falls back to the main OpenRouter gateway settings when the dedicated
    embedding fields are left empty.
    """
    cfg = get_settings()
    base = (cfg.embedding_api_base_url or "").strip() or cfg.openrouter_base_url
    key = (cfg.embedding_api_key or "").strip() or cfg.openrouter_api_key
    model = (cfg.embedding_model or "").strip()
    return base, key, model


def _resolve_base_url(url: str) -> str:
    url = url.strip() or "http://localhost:8000"
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    return url.rstrip("/")


def current_embedding_label() -> str:
    """Human/stable label for the active embedding backend+model.

    Used for the /index/stats model-matching check and display.
    """
    cfg = get_settings()
    backend = (cfg.embedding_backend or "local").strip().lower()
    if backend in ("api", "openrouter"):
        return cfg.embedding_model or embedding_api_config()[2] or "api-embedding"
    if backend == "cloudflare":
        return cfg.cloudflare_embedding_model or "@cf/baai/bge-base-en-v1.5"
    if (cfg.local_embedding_path or "").strip():
        return (cfg.local_embedding_path or "").strip()
    return cfg.local_embedding_model or "bge-small-en-v1.5"


def _session_or_none():
    """Return a lazily-created sentence-transformers model (or None).

    Prefers LOCAL_EMBEDDING_PATH (a local model folder) for fully-offline use.
    Respects HF_OFFLINE so the loader never tries the network when offline is
    requested.
    """
    key = "local_embedder"
    if key in _EMBEDDER_CACHE:
        return _EMBEDDER_CACHE[key]
    try:
        from sentence_transformers import SentenceTransformer

        cfg = get_settings()

        # Apply offline env flags before loading so sentence-transformers uses
        # its on-disk cache instead of dialing HuggingFace. Also disable any
        # stray HTTP(S) proxy so the loader cannot hit a dead proxy (a common
        # cause of the "unable to connect to proxy" error).
        if cfg.hf_offline:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
                os.environ.pop(var, None)

        local_path = (cfg.local_embedding_path or "").strip()
        if local_path:
            if os.path.isdir(local_path):
                model = SentenceTransformer(local_path)
                logger.info("Loaded local embedding model from path %s", local_path)
            else:
                logger.warning(
                    "LOCAL_EMBEDDING_PATH '%s' is not a directory; treating as a "
                    "HuggingFace model id.", local_path,
                )
                model = SentenceTransformer(local_path if local_path else cfg.local_embedding_model)
        else:
            model = SentenceTransformer(cfg.local_embedding_model)

        _EMBEDDER_CACHE[key] = model
        logger.info("Loaded local embedding model %s", cfg.local_embedding_model)
        return model
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load local embedding model")
        _EMBEDDER_CACHE[key] = None
        raise RuntimeError(
            "Local embedding model unavailable. The model could not be loaded "
            "(offline or not cached). In Settings pick an online embedding "
            "backend (OpenAI-compatible API or Cloudflare), or set a local "
            "model path / enable download. Detail: "
            f"{exc}"
        ) from exc


def embed_texts_api(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Embed via any OpenAI-compatible /embeddings endpoint."""
    import httpx

    from app.models_openrouter import _client_embedding

    client = _client_embedding()
    model = (model or "").strip() or embedding_api_config()[2]
    if not model:
        raise RuntimeError(
            "No embedding model configured for the API backend. Set EMBEDDING_MODEL in Settings."
        )
    try:
        resp = client.embeddings.create(model=model, input=texts)
        items = sorted(resp.data, key=lambda x: x.index)
        return [list(it.embedding) for it in items]
    except Exception as exc:  # noqa: BLE001
        logger.exception("API embedding failed (model=%s)", model)
        raise RuntimeError(f"API embedding failed: {exc}") from exc


def _embed_cloudflare(texts: list[str]) -> list[list[float]]:
    """Embed via Cloudflare Workers AI embeddings REST endpoint."""
    import base64

    import httpx

    cfg = get_settings()
    account = cfg.cf_account_id.strip()
    token = cfg.cf_api_token.strip()
    model = cfg.cloudflare_embedding_model.strip() or "@cf/baai/bge-base-en-v1.5"
    if not account or not token:
        raise RuntimeError("Cloudflare embeddings need CF_ACCOUNT_ID and CF_API_TOKEN (Settings).")

    endpoint = (
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    out: list[list[float]] = []
    with httpx.Client(timeout=60) as client:
        # Cloudflare's embeddings model takes a {"text": [...]} input (single call).
        try:
            resp = client.post(endpoint, headers=headers, json={"text": texts})
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Cloudflare embedding failed")
            raise RuntimeError(
                f"Cloudflare embedding failed: {exc}"
                if not getattr(exc, "response", None)
                else f"Cloudflare embedding failed: {exc.response.status_code} {exc.response.text[:200]}"
            ) from exc

    embeddings = []
    if isinstance(result, dict):
        # {"data":[{"embedding":[...],...}]} or {"embeddings":[[...],...]}
        embeddings = result.get("data") or result.get("embeddings") or []
    elif isinstance(result, list):
        embeddings = result  # sometimes the raw list of vectors

    for item in embeddings:
        if isinstance(item, dict):
            out.append(list(item.get("embedding") or []))
        else:
            out.append(list(item or []))
    if not out:
        logger.error("Cloudflare returned no embeddings: %s", (data if 'data' in dir() else result))
        raise RuntimeError("Cloudflare embeddings returned no vectors.")
    return out


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts using the configured backend."""
    if not texts:
        return []
    cfg = get_settings()
    backend = (cfg.embedding_backend or "local").strip().lower()

    if backend == "api" or backend == "openrouter":
        return embed_texts_api(texts)

    if backend == "cloudflare":
        return _embed_cloudflare(texts)

    # local
    model = _session_or_none()
    if model is None:
        raise RuntimeError("Local embedding model unavailable.")
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]
