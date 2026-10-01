"""Settings page – configure API key, models, backends.

Changes persist to the local .env file so they survive restarts.
"""
from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from app.config import PROJECT_ROOT, get_settings
from app.models_openrouter import fetch_models, test_connection, test_embedding
from app.podcast.hosts import GENDERS, PROVIDERS, PROVIDER_LABELS, get_hosts
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

st.caption("**Podcast studio (NotebookLM-style)**")

# ---- TTS provider selector --------------------------------------------------
_provider_choices = list(PROVIDERS)
provider = st.selectbox(
    "Text-to-speech provider",
    _provider_choices,
    format_func=lambda k: PROVIDER_LABELS.get(k, k),
    index=_provider_choices.index(cfg.tts_provider) if cfg.tts_provider in _provider_choices else 0,
    help=(
        "localhost = your OpenAI-compatible /v1/audio/speech server (private). "
        "cloudflare = Cloudflare Workers AI. google = Google Cloud TTS. "
        "edge-tts = free MP3 (needs pip install edge-tts + internet). "
        "disabled = transcript only."
    ),
)

# ---- Provider-specific fields ------------------------------------------------
if provider == "localhost":
    st.caption("**Localhost server** (any OpenAI-compatible `/v1/audio/speech`, e.g. Kokoro, Silero, Piper, vLLM…)")
    api_url = st.text_input(
        "TTS API URL",
        value=cfg.tts_api_url or "http://localhost:20128/v1/audio/speech",
        help="Full URL of the speech endpoint, including /v1/audio/speech.",
    )
    c_api1, c_api2 = st.columns(2)
    api_model = c_api1.text_input(
        "TTS model id", value=cfg.tts_api_model or "tts-1",
        help="The model your server expects (e.g. 'tts-1', 'kokoro', 'silero').",
    )
    api_format = c_api2.selectbox(
        "Output format", ["mp3", "wav", "opus", "aac", "flac"],
        index=["mp3", "wav", "opus", "aac", "flac"].index(cfg.tts_api_format)
        if cfg.tts_api_format in ["mp3", "wav", "opus", "aac", "flac"] else 0,
        help="Format your server returns. mp3 recommended.",
    )
    api_key = st.text_input(
        "TTS API key (optional)", value=cfg.tts_api_key, type="password",
        help="Only if your TTS server requires an API key.",
    )
elif provider == "cloudflare":
    st.caption("**Cloudflare Workers AI TTS** (Models AI → TTS, e.g. `@cf/microsoft/windows-captioning-or-tts` or `@cf/playai/tts-*-v1`).")
    c_cf1, c_cf2 = st.columns(2)
    cf_account = c_cf1.text_input("Account ID", value=cfg.cf_account_id)
    cf_token = c_cf2.text_input("API Token", value=cfg.cf_api_token, type="password")
    cf_model = st.text_input("Model", value=cfg.cf_model or "@cf/microsoft/windows-captioning-or-tts")
elif provider == "google":
    st.caption("**Google Cloud Text-to-Speech** (an API key with the Cloud Text-to-Speech API enabled).")
    google_key = st.text_input("Google API Key", value=cfg.google_api_key, type="password")
    google_lang = st.text_input(
        "Language code", value=cfg.google_language_code or "en-US",
        help="e.g. en-US, en-GB, fr-FR, de-DE, es-ES…",
    )
else:
    # edge-tts / disabled: keep passthrough values
    api_url = cfg.tts_api_url
    api_model = cfg.tts_api_model
    api_format = cfg.tts_api_format
    api_key = cfg.tts_api_key
    cf_account = cfg.cf_account_id
    cf_token = cfg.cf_api_token
    cf_model = cfg.cf_model
    google_key = cfg.google_api_key
    google_lang = cfg.google_language_code

st.divider()

# ---- Podcast hosts (1-3 people) ---------------------------------------------
st.markdown("**🎤 Podcast hosts** (1-3 people, each with a name, gender and voice)")

_hosts = get_hosts(cfg)

num_hosts = st.selectbox(
    "Number of hosts",
    [1, 2, 3],
    index=int(cfg.podcast_num_hosts) - 1 if 1 <= int(cfg.podcast_num_hosts) <= 3 else 1,
    key="pod_num_hosts",
    help="How many people should host the episode (1-3).",
)

host_values = {}
for i in range(1, num_hosts + 1):
    existing = next((h for h in _hosts if h.index == i), None)
    st.markdown(f"**Host {i}**")
    c_n, c_g, c_v = st.columns([2, 1, 2])
    name = c_n.text_input(
        f"Host {i} name", value=(existing.name if existing else f"Host {i}"),
        key=f"pod_name_{i}",
    )
    gender = c_g.selectbox(
        f"Host {i} gender",
        list(GENDERS),
        index=GENDERS.index(existing.gender) if existing and existing.gender in GENDERS else 0,
        key=f"pod_gender_{i}",
        format_func=lambda g: g.capitalize(),
    )
    voice = c_v.text_input(
        f"Host {i} voice id",
        value=(existing.voice if existing else ""),
        key=f"pod_voice_{i}",
        help="Voice id for the selected TTS provider (e.g. 'af_heart', 'en-US-JennyNeural', Google voice name).",
    )
    host_values[i] = (name, gender, voice)

col_save_tune = st.button("💾 Save OCR & tuning", use_container_width=True)
if col_save_tune:
    updates = {
        "OCR_BACKEND": ocr_backend,
        "TESSERACT_CMD": tesseract_cmd.strip(),
        "CHUNK_SIZE": str(chunk_size),
        "TOP_K": str(top_k),
        "CITATION_STYLE": citation_style,
        "PADDLEOCR_VL_MODEL": paddle_vl_model.strip(),
        "PADDLEOCR_VL_BASE_URL": paddle_vl_url.strip(),
        "TELEOCR_MODEL": teleocr_model.strip(),
        "TELEOCR_BASE_URL": teleocr_url.strip(),
        # TTS provider + settings
        "TTS_PROVIDER": provider,
        "TTS_API_URL": (api_url if provider == "localhost" else cfg.tts_api_url).strip(),
        "TTS_API_MODEL": (api_model if provider == "localhost" else cfg.tts_api_model).strip(),
        "TTS_API_FORMAT": api_format if provider == "localhost" else cfg.tts_api_format,
        "TTS_API_KEY": (api_key if provider == "localhost" else cfg.tts_api_key).strip(),
        "CF_ACCOUNT_ID": (cf_account if provider == "cloudflare" else cfg.cf_account_id).strip(),
        "CF_API_TOKEN": (cf_token if provider == "cloudflare" else cfg.cf_api_token).strip(),
        "CF_MODEL": (cf_model if provider == "cloudflare" else cfg.cf_model).strip(),
        "GOOGLE_API_KEY": (google_key if provider == "google" else cfg.google_api_key).strip(),
        "GOOGLE_LANGUAGE_CODE": (google_lang if provider == "google" else cfg.google_language_code).strip(),
        # hosts
        "PODCAST_NUM_HOSTS": str(num_hosts),
    }
    for i in range(1, 4):
        if i in host_values:
            name_v, gender_v, voice_v = host_values[i]
        else:
            name_v = str(getattr(cfg, f"podcast_host_{i}_name", "") or "")
            gender_v = str(getattr(cfg, f"podcast_host_{i}_gender", "") or "female")
            voice_v = str(getattr(cfg, f"podcast_host_{i}_voice", "") or "")
        updates[f"PODCAST_HOST_{i}_NAME"] = name_v.strip()
        updates[f"PODCAST_HOST_{i}_GENDER"] = gender_v
        updates[f"PODCAST_HOST_{i}_VOICE"] = voice_v.strip()
    _apply(updates)
    st.success("Podcast settings saved.")
