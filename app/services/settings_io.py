"""Server-side settings I/O for the v2.0 web UI.

Ports the Streamlit Settings page's `.env` writer into the backend so the SPA
can read and mutate configuration over HTTP:

- writes are **whitelisted** (unknown keys are rejected with HTTP 400),
- `.env` is rewritten line-in-place (comments, ordering and unrelated keys
  are preserved),
- every written value is mirrored into `os.environ`, because pydantic-settings
  ranks live environment variables **above** the .env file (the API process
  loaded the old values into os.environ at startup; without mirroring, a new
  `Settings()` would silently keep the stale ones),
- the `lru_cache`d `get_settings()` is cleared so subsequent endpoints see
  the fresh values.

Heavy subsystems (embedding backend, OCR servers) that keep long-lived
handles still require a restart to fully re-bind — documents warn about that
where relevant.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings

logger = logging.getLogger("app")

_env_lock = threading.Lock()

# --- .env keys the web UI may change (PATCH with anything else -> 400) ------
ALLOWED_KEYS: frozenset[str] = frozenset({
    # gateway
    "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "CHAT_MODEL", "CHAT_TEMPERATURE",
    # embeddings
    "EMBEDDING_BACKEND", "EMBEDDING_MODEL", "LOCAL_EMBEDDING_MODEL",
    "LOCAL_EMBEDDING_PATH", "HF_OFFLINE",
    "EMBEDDING_API_BASE_URL", "EMBEDDING_API_KEY", "CLOUDFLARE_EMBEDDING_MODEL",
    # OCR & RAG tuning
    "OCR_BACKEND", "TESSERACT_CMD", "DEFAULT_OCR",
    "PADDLEOCR_VL_MODEL", "PADDLEOCR_VL_BASE_URL",
    "TELEOCR_MODEL", "TELEOCR_BASE_URL",
    "CHUNK_SIZE", "CHUNK_OVERLAP", "TOP_K", "CITATION_STYLE",
    # podcast audio
    "TTS_PROVIDER", "TTS_API_URL", "TTS_API_KEY", "TTS_API_MODEL", "TTS_API_FORMAT",
    "VOICESTUDIO_URL", "VOICESTUDIO_API_KEY", "VOICESTUDIO_MODEL", "VOICESTUDIO_FORMAT",
    "GATEWAY_TTS_URL", "GATEWAY_TTS_KEY",
    "CF_ACCOUNT_ID", "CF_API_TOKEN", "CF_MODEL",
    "GOOGLE_API_KEY", "GOOGLE_LANGUAGE_CODE",
    "PODCAST_NUM_HOSTS",
    "PODCAST_HOST_1_NAME", "PODCAST_HOST_1_GENDER", "PODCAST_HOST_1_VOICE",
    "PODCAST_HOST_1_LANGUAGE",
    "PODCAST_HOST_2_NAME", "PODCAST_HOST_2_GENDER", "PODCAST_HOST_2_VOICE",
    "PODCAST_HOST_2_LANGUAGE",
    "PODCAST_HOST_3_NAME", "PODCAST_HOST_3_GENDER", "PODCAST_HOST_3_VOICE",
    "PODCAST_HOST_3_LANGUAGE",
})

_SECRET_KEYS: frozenset[str] = frozenset({
    "OPENROUTER_API_KEY", "EMBEDDING_API_KEY", "TTS_API_KEY",
    "VOICESTUDIO_API_KEY", "GATEWAY_TTS_KEY",
    "CF_ACCOUNT_ID", "CF_API_TOKEN", "GOOGLE_API_KEY",
})

_BOOL_KEYS: frozenset[str] = frozenset({"HF_OFFLINE", "DEFAULT_OCR"})
_INT_KEYS: frozenset[str] = frozenset({"CHUNK_SIZE", "CHUNK_OVERLAP", "TOP_K", "PODCAST_NUM_HOSTS"})
_FLOAT_KEYS: frozenset[str] = frozenset({"CHAT_TEMPERATURE"})


# ---------------------------------------------------------------------------
# .env writer (line-in-place; keeps comments and unrelated keys)
# ---------------------------------------------------------------------------

def update_env(updates: dict[str, str | int | float | bool | None]) -> list[str]:
    """Persist `updates` (env-key -> value) to .env in place and mirror into
    os.environ, then clear the cached settings. Returns the applied keys.

    Raises ValueError when a key is not in ALLOWED_KEYS.
    """
    clean: dict[str, str] = {}
    for key, value in (updates or {}).items():
        key = str(key).strip().upper()
        if key not in ALLOWED_KEYS:
            raise ValueError(f"Settings key not allowed: {key}")
        clean[key] = "" if value is None else str(value).strip()

    if not clean:
        return []

    env_path: Path = PROJECT_ROOT / ".env"
    with _env_lock:
        existing: list[str] = []
        if env_path.exists():
            existing = env_path.read_text(encoding="utf-8", errors="replace").splitlines()

        seen: set[str] = set()
        out: list[str] = []
        for raw in existing:
            s = raw.strip()
            if s and not s.startswith("#") and "=" in s:
                k = s.split("=", 1)[0].strip()
                if k in clean:
                    out.append(f"{k}={clean[k]}")
                    seen.add(k)
                    continue
            out.append(raw)
        for key, value in clean.items():
            if key not in seen:
                out.append(f"{key}={value}")

        body = "\n".join(out)
        if not body.endswith("\n"):
            body += "\n"
        env_path.write_text(body, encoding="utf-8")
        logger.info("Settings updated in .env: %s", ", ".join(clean.keys()))

    # Mirror into os.environ: live env vars outrank the .env file in
    # pydantic-settings, and the API process already loaded the old values
    # at startup. Removing a key that became empty lets the (empty) .env
    # value flow through.
    for key, value in clean.items():
        if value == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()
    return list(clean.keys())


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def mask_secret(value: str | None) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 10:
        return "•••••"
    return f"{v[:4]}••••{v[-4:]}"


def full_settings() -> dict:
    """Complete settings snapshot for `GET /settings`, secrets masked."""
    cfg = get_settings()
    out: dict = {}
    for key in sorted(ALLOWED_KEYS):
        raw = getattr(cfg, key.lower(), None)
        if key in _BOOL_KEYS:
            out[key] = bool(raw)
        elif key in _INT_KEYS:
            try:
                out[key] = int(raw or 0)
            except (TypeError, ValueError):
                out[key] = 0
        elif key in _FLOAT_KEYS:
            try:
                out[key] = float(raw or 0.0)
            except (TypeError, ValueError):
                out[key] = 0.0
        elif key in _SECRET_KEYS:
            out[key] = mask_secret(str(raw or ""))
        else:
            out[key] = str(raw or "")
    # non-writable context fields useful to the SPA
    out["_port"] = cfg.port
    return out


def has_secret(key: str) -> bool:
    """True when a secret key is set (for 'configured' chips without values)."""
    val = str(getattr(get_settings(), key.lower(), "") or "").strip()
    return bool(val)
