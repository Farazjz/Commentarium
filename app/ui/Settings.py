"""Settings page – configure API key, models, backends.

Changes persist to the local .env file so they survive restarts.
"""
from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from app.config import PROJECT_ROOT, get_settings
from app.models_openrouter import fetch_models, test_connection, test_embedding
from app.ui.helpers import banner

logger = logging.getLogger("app")

# ---------------------------------------------------------------------------
# Tiny .env writer (preserves unrelated keys)
# ---------------------------------------------------------------------------
def _update_env(updates: dict[str, str]) -> None:
    env_path = PROJECT_ROOT / ".env"
    lines: dict[str, str] = {}
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw and not raw.startswith("#") and "=" in raw:
                k, v = raw.split("=", 1)
                lines[k.strip()] = v.strip()
    lines.update(updates)
    body = "\n".join(f"{k}={v}" for k, v in lines.items()) + "\n"
    env_path.write_text(body, encoding="utf-8")
    logger.info("Settings updated in .env: %s", ", ".join(updates.keys()))


def _apply(updates: dict[str, str]) -> None:
    """Persist to .env, clear cached config, then re-run the page so the UI
    reflects the newly saved state (widget values + model dropdown etc.)."""
    _update_env(updates)
    st.cache_data.clear()
    get_settings.cache_clear()
    st.rerun()


banner("⚙️ Settings", "API keys, models and backends. Saved to .env for next run.")

cfg = get_settings()

# ---------------------------------------------------------------------------
# 1. OpenRouter connection
# ---------------------------------------------------------------------------
st.markdown("### 1 · OpenRouter")
api_key = st.text_input(
    "OpenRouter API key",
    value=cfg.openrouter_api_key,
    type="password",
    help="Get one at https://openrouter.ai/keys",
)
base_url = st.text_input(
    "Base URL (optional)",
    value=cfg.openrouter_base_url,
    help="Leave default unless using a proxy/gateway.",
)

col_test, col_save = st.columns([1, 1])
save_conf = col_save.button("💾 Save key", use_container_width=True)
if save_conf:
    _apply({"OPENROUTER_API_KEY": api_key.strip(), "OPENROUTER_BASE_URL": base_url.strip() or ""})
    st.success("Saved.")

if col_test.button("🔌 Test connection", use_container_width=True):
    with st.spinner("Testing…"):
        result = test_connection(api_key=api_key, base_url=base_url)
    if result.get("ok"):
        st.success(f"Connection OK — {result.get('models_count', '?')} models visible.")
    else:
        st.error(f"Connection failed: {result.get('error')}")

# ---------------------------------------------------------------------------
# 2. Models (requires key -> refresh list)
# ---------------------------------------------------------------------------
st.markdown("### 2 · Models")
st.caption("Pick your default models. Use the 🔄 refresh button to re-fetch the gateway's list.")

# temperature (global default, per-project overrides available in Chat)
chat_temperature = st.slider(
    "Default chat temperature",
    min_value=0.0, max_value=1.5, value=float(cfg.chat_temperature), step=0.05,
    help="Creativity of answers. Lower = more factual/grounded, higher = more varied. "
         "You can override this per-project in the Chat page.",
)

available = []
if cfg.has_openrouter_key:
    # Cache the fetched model list in session_state so it is NOT re-fetched
    # on every rerun (that previously reset the user's dropdown selection).
    if "models_cache" not in st.session_state:
        with st.spinner("Loading models…"):
            st.session_state["models_cache"] = fetch_models()
    available = st.session_state["models_cache"]
    if not available:
        st.warning("Could not fetch models. Check your key / internet in the Log Viewer.")
else:
    st.info("Save your key above (or enter one) to load the model dropdown.")

col_refresh, _ = st.columns([1, 3])
if col_refresh.button("🔄 Refresh model list"):
    with st.spinner("Loading models…"):
        st.session_state["models_cache"] = fetch_models()
    st.success("Model list refreshed.")

chat_options = [m["id"] for m in available if not m.get("embedding")]
# Guarantee the saved chat model is always selectable, even if the gateway
# reordered/dropped it on a later fetch (prevents reload resetting to index 0).
if cfg.chat_model and cfg.chat_model not in chat_options:
    chat_options.insert(0, cfg.chat_model)
if not chat_options:
    chat_options = []

# Seed the keyed widget with the saved value on first load, then let
# session_state own it (so a rerun cannot reset it to the first option).
if "chat_model_select" not in st.session_state:
    st.session_state["chat_model_select"] = (
        cfg.chat_model
        if cfg.chat_model in chat_options
        else (chat_options[0] if chat_options else "")
    )

chat_model = st.selectbox(
    "Default chat model",
    options=chat_options,
    key="chat_model_select",
    help="Used for answering your questions.",
)

st.divider()

# ---------------------------------------------------------------------------
# Embedding backend + model
# ---------------------------------------------------------------------------
embedding_backend = st.radio(
    "Embedding backend",
    ["local", "openrouter"],
    index=0 if cfg.embedding_backend == "local" else 1,
    horizontal=True,
    help="local = free & private (sentence-transformers). openrouter = uses EMBEDDING_MODEL.",
)

embedding_model = None
if embedding_backend == "openrouter":
    st.caption(
        "9Router serves embeddings via a separate provider, so the id must be "
        "provider-prefixed (e.g. `openrouter/openai/text-embedding-3-large`). "
        "Enter it below, then click **Test embedding**."
    )
    # Manual field pre-filled with whatever is saved (or the known-working id).
    manual_embed = st.text_input(
        "Embedding model ID",
        value=cfg.embedding_model or "openrouter/openai/text-embedding-3-large",
        placeholder="openrouter/openai/text-embedding-3-large",
        key="embed_model_manual",
        help="The exact model id your 9Router embedding provider serves (provider-prefixed).",
    )
    embedding_model = manual_embed

    col_embed_test, _ = st.columns([1, 3])
    if col_embed_test.button("🧪 Test embedding", use_container_width=True):
        with st.spinner("Testing embedding…"):
            res = test_embedding(
                api_key=api_key if isinstance(api_key, str) and api_key else None,
                base_url=base_url if isinstance(base_url, str) and base_url else None,
                model=manual_embed.strip() or None,
            )
        if res.get("ok"):
            st.success(f"Embedding OK — {res['dimensions']} dimensions (model `{res['model']}`).")
        else:
            st.error(f"Embedding failed: {res.get('error')}")
else:
    st.caption(f"Using local model: `{cfg.local_embedding_model}`")

col_save_models = st.button("💾 Save models", use_container_width=True)
if col_save_models:
    updates = {
        "CHAT_MODEL": chat_model.strip(),
        "EMBEDDING_BACKEND": embedding_backend,
        "CHAT_TEMPERATURE": str(chat_temperature),
    }
    if embedding_backend == "openrouter" and embedding_model:
        updates["EMBEDDING_MODEL"] = embedding_model.strip()
    _apply(updates)
    st.success("Models saved.")

st.divider()

# ---------------------------------------------------------------------------
# 3. OCR + RAG tuning
# ---------------------------------------------------------------------------
st.markdown("### 3 · OCR & RAG tuning")
_OCR_CHOICES = ["tesseract", "vision", "paddleocr-vl", "teleocr", "disabled"]
ocr_backend = st.selectbox(
    "OCR backend (for scanned PDF pages)",
    _OCR_CHOICES,
    index=_OCR_CHOICES.index(cfg.ocr_backend)
    if cfg.ocr_backend in _OCR_CHOICES
    else 0,
    help=(
        "tesseract = free/private local OCR (install Tesseract). "
        "vision = uses chat model. paddleocr-vl / teleocr = vision-language OCR "
        "models via a local server (or gateway fallback). disabled = no OCR."
    ),
)
tesseract_cmd = st.text_input("Tesseract executable path", value=cfg.tesseract_cmd or "")

st.caption("**Vision-language OCR** (PaddleOCR-VL / TeleOCR)")
paddle_vl_model = st.text_input(
    "PaddleOCR-VL model id",
    value=cfg.paddleocr_vl_model,
    help="HuggingFace id of the model to ask (server or gateway).",
)
paddle_vl_url = st.text_input(
    "PaddleOCR-VL local server URL (optional)",
    value=cfg.paddleocr_vl_base_url,
    placeholder="http://127.0.0.1:8089/v1  (mllm_server, vLLM, SGLang…)",
    help="Leave empty to fall back to the gateway's vision model.",
)
teleocr_model = st.text_input(
    "TeleOCR model id",
    value=cfg.teleocr_model,
    help="HuggingFace id of the model (e.g. XingChen-AGI/TeleOCR).",
)
teleocr_url = st.text_input(
    "TeleOCR local server URL (optional)",
    value=cfg.teleocr_base_url,
    placeholder="http://127.0.0.1:8000/v1  (vLLM, SGLang…)",
    help="Leave empty to fall back to the gateway's vision model.",
)

col1, col2 = st.columns(2)
chunk_size = col1.number_input("Chunk size (chars)", min_value=200, max_value=4000, value=cfg.chunk_size, step=50)
top_k = col2.number_input("Top-K retrieved chunks", min_value=1, max_value=30, value=cfg.top_k, step=1)

citation_style = st.selectbox(
    "Default citation style",
    ["apa", "vancouver"],
    index=0 if cfg.citation_style == "apa" else 1,
)

st.caption("**Podcast audio (NotebookLM-style)**")
tts_backend = st.selectbox(
    "Text-to-speech backend",
    ["edge-tts", "disabled"],
    index=0 if cfg.tts_backend == "edge-tts" else 1,
    help="edge-tts = free MP3 (requires `pip install edge-tts` + internet). disabled = transcript only.",
)
host_a_voice = st.text_input("Host A voice", value=cfg.podcast_host_a_voice, help="edge-tts voice name.")
host_b_voice = st.text_input("Host B voice", value=cfg.podcast_host_b_voice, help="edge-tts voice name.")

col_save_tune = st.button("💾 Save OCR & tuning", use_container_width=True)
if col_save_tune:
    _apply(
        {
            "OCR_BACKEND": ocr_backend,
            "TESSERACT_CMD": tesseract_cmd.strip(),
            "CHUNK_SIZE": str(chunk_size),
            "TOP_K": str(top_k),
            "CITATION_STYLE": citation_style,
            "PADDLEOCR_VL_MODEL": paddle_vl_model.strip(),
            "PADDLEOCR_VL_BASE_URL": paddle_vl_url.strip(),
            "TELEOCR_MODEL": teleocr_model.strip(),
            "TELEOCR_BASE_URL": teleocr_url.strip(),
            "TTS_BACKEND": tts_backend,
            "PODCAST_HOST_A_VOICE": host_a_voice.strip(),
            "PODCAST_HOST_B_VOICE": host_b_voice.strip(),
        }
    )
    st.success("OCR & tuning saved.")
