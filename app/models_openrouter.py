"""Shared OpenRouter (OpenAI-compatible) client.

Provides:
- a lazy OpenAI client pointed at OpenRouter
- a test_connection() helper used by the Settings page and /settings/test
- a list_models() helper to populate the model dropdowns
"""
from __future__ import annotations

import logging

import httpx
from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger("app")


class LLMClientError(Exception):
    pass


def _client() -> OpenAI:
    cfg = get_settings()
    if not cfg.has_openrouter_key:
        raise LLMClientError(
            "OpenRouter API key is not set. Go to Settings and paste your key."
        )
    return OpenAI(
        api_key=cfg.openrouter_api_key,
        base_url=cfg.openrouter_base_url,
    )


def _client_embedding() -> OpenAI:
    """OpenAI client pointed at the embedding API endpoint.

    Uses EMBEDDING_API_BASE_URL / EMBEDDING_API_KEY when set (any OpenAI-
    compatible embeddings provider), otherwise falls back to the main
    OpenRouter gateway settings.
    """
    from app.ingest.embeddings import embedding_api_config

    base, key, _ = embedding_api_config()
    if not key:
        raise LLMClientError(
            "No embedding API key configured. In Settings set an embedding "
            "API key (or a main OpenRouter key)."
        )
    return OpenAI(api_key=key, base_url=base)


def test_connection(*, api_key: str | None = None, base_url: str | None = None) -> dict:
    """Verify connectivity and that the API key is valid.

    Uses the provided api_key/base_url if given, otherwise falls back to the
    configured settings. This lets the Settings page test a key the user has
    typed but not yet saved.

    Returns a dict with 'ok' and optional 'error'/'models' info.
    """
    cfg = get_settings()
    key = (api_key or "").strip() or cfg.openrouter_api_key
    url = (base_url or "").strip() or cfg.openrouter_base_url
    if not key:
        return {"ok": False, "error": "No API key configured. Paste one above first."}
    try:
        # List models requires auth; a 401 means a bad key. This is the
        # lightest reliable auth check without spending tokens.
        resp = httpx.get(
            f"{url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20,
        )
        if resp.status_code == 401:
            return {"ok": False, "error": "Invalid API key (401 Unauthorized)."}
        if resp.status_code != 200:
            return {"ok": False, "error": f"Provider returned HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        models = data.get("data", [])
        return {"ok": True, "models_count": len(models)}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Could not reach {url}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Connection test failed")
        return {"ok": False, "error": str(exc)}


def fetch_models() -> list[dict]:
    """Return the list of available models with capability hints.

    Handles both the official OpenRouter schema (top-level `text`/`vision`
    booleans) and gateway schemas that nest capabilities under
    `capabilities` (e.g. local proxies). Each returned dict:

        id, name, context, text, vision, embedding, reasoner

    `embedding` is True when the model advertises embedding/similarity
    capability (authoritative) or its id clearly indicates an embedder.

    Used to populate the Settings dropdowns. Returns [] on any failure.
    """
    try:
        resp = httpx.get(
            f"{get_settings().openrouter_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {get_settings().openrouter_api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        out = []
        for m in data:
            mid = m.get("id", "") or ""
            name = m.get("name", "") or mid
            caps = m.get("capabilities") or {}
            low = (mid + " " + name).lower()

            # --- embedding detection --------------------------------------
            embed_caps = caps.get("embedding") or caps.get("embeddings") or caps.get("similarity")
            embed_by_id = any(
                token in low
                for token in ("text-embedding", "embedding", "embed", "bge-", "-e5", "instructor", "similarity", "rerank")
            )
            embedding = bool(embed_caps) or embed_by_id

            # --- text/vision/reasoning ------------------------------------
            if "text" in caps:                      # openrouter-style nested caps
                text = bool(caps.get("text"))
                vision = bool(caps.get("vision"))
            else:                                   # flat openrouter schema
                text = bool(m.get("text"))
                vision = bool(m.get("vision") or caps.get("vision"))
            reasoning = bool(caps.get("reasoning")) or bool(m.get("reasoning"))

            out.append(
                {
                    "id": mid,
                    "name": name,
                    "context": (m.get("context_length") or caps.get("contextWindow") or 0),
                    "text": text,
                    "vision": vision,
                    "embedding": embedding,
                    "reasoner": reasoning,
                }
            )
        # sort: embeddings last, then by id (stable)
        out.sort(key=lambda x: (x["embedding"], x["id"].lower()))
        return out
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch model list from OpenRouter")
        return []


def chat_completion(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1500,
) -> str:
    """Run a chat completion and return the assistant text.

    Raises LLMClientError on configuration/API failures so callers can
    surface a friendly message.
    """
    cfg = get_settings()
    model = model or cfg.chat_model
    if not model:
        raise LLMClientError("No chat model selected. Set one in Settings.")
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat completion failed (model=%s)", model)
        raise LLMClientError(f"Chat failed: {exc}") from exc


def embed_texts(
    texts: list[str],
    *,
    model: str | None = None,
) -> list[list[float]]:
    """Embed texts via the gateway's /embeddings endpoint.

    If the configured model id lacks a provider prefix (e.g. 9Router requires
    `openrouter/...`), it is left as-is unless the caller passes it prefixed;
    the endpoint itself enforces the prefix. Returns a list of embeddings.
    """
    cfg = get_settings()
    model = (model or "").strip() or cfg.embedding_model
    if not model:
        raise LLMClientError("No embedding model configured. Set one in Settings.")
    try:
        client = _client()
        resp = client.embeddings.create(model=model, input=texts)
        # Preserve input order (some gateways reorder; sort by index defensively)
        items = sorted(resp.data, key=lambda x: x.index)
        return [list(it.embedding) for it in items]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Embedding failed (model=%s)", model)
        raise LLMClientError(f"Embedding failed: {exc}") from exc


def test_embedding(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> dict:
    """Test that the embedding endpoint responds with a vector for a model."""
    from app.ingest.embeddings import embedding_api_config

    cfg = get_settings()
    default_base, default_key, default_model = embedding_api_config()
    key = (api_key or "").strip() or default_key or cfg.openrouter_api_key
    url = (base_url or "").strip() or default_base or cfg.openrouter_base_url
    model = (model or "").strip() or cfg.embedding_model or default_model
    if not model:
        return {"ok": False, "error": "No embedding model id entered."}
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/embeddings",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            json={"model": model, "input": "connection test"},
            timeout=60,
        )
        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:250]}",
            }
        data = resp.json().get("data", [])
        if not data:
            return {"ok": False, "error": "OK response, but no embedding data returned."}
        dims = len(data[0].get("embedding", []))
        return {"ok": True, "dimensions": dims, "model": model}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Could not reach {url}/embeddings: {exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Embedding test failed")
        return {"ok": False, "error": str(exc)}
