"""Application configuration loaded from .env (or environment variables).

Central place for all tunable settings. Values are read once at import
time; the Streamlit UI can override runtime settings (models, backends)
per-process without touching this file.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = parent of this file's directory (app/ -> project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env if present (does nothing when the file is missing)
load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # --- AI provider ---
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- Models (defaults; overridable in UI) ---
    chat_model: str = ""
    embedding_model: str = ""

    # --- Embedding backend: "local" | "api" (OpenAI-compatible) | "cloudflare" ---
    #   "local"      -> sentence-transformers, fully offline (see local_embedding_*)
    #   "api"        -> any OpenAI-compatible /embeddings endpoint (OpenRouter,
    #                   Google Gemini, OpenAI, a local proxy…) using
    #                   embedding_api_base_url / embedding_api_key / EMBEDDING_MODEL.
    #                   Empty base/key fall back to the OpenRouter gateway settings.
    #   "cloudflare" -> Cloudflare Workers AI embeddings (cf_account_id + cf_api_token)
    embedding_backend: str = "local"
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Point at a local SentenceTransformer model directory for fully-offline use
    # (e.g. a folder with config.json + model.safetensors). Empty = use the model
    # id above (downloads once from HuggingFace on first use).
    local_embedding_path: str = ""
    # Force offline HuggingFace loading (use the local cache, never hit the network).
    hf_offline: bool = False

    # OpenAI-compatible embedding endpoint (embedding_backend="api").
    # Leave empty to fall back to OPENROUTER_BASE_URL / OPENROUTER_API_KEY.
    embedding_api_base_url: str = ""
    embedding_api_key: str = ""

    # Cloudflare Workers AI embedding (embedding_backend="cloudflare").
    # Reuses CF_ACCOUNT_ID / CF_API_TOKEN from the podcast settings.
    cloudflare_embedding_model: str = "@cf/baai/bge-base-en-v1.5"

    # --- OCR backend: "tesseract" | "vision" | "paddleocr-vl" | "teleocr" | "disabled" ---
    ocr_backend: str = "tesseract"
    tesseract_cmd: str = ""
    # Vision-Language OCR backends: a local OpenAI-compatible VLM server URL
    # (PaddlePaddle `mllm_server`, vLLM, SGLang, LM Studio...) OR empty to fall
    # back to the configured chat gateway's vision model.
    paddleocr_vl_model: str = "PaddleOCR-community/PaddleOCR-VL-1.6"
    paddleocr_vl_base_url: str = ""
    teleocr_model: str = "XingChen-AGI/TeleOCR"
    teleocr_base_url: str = ""

    # --- Storage ---
    data_dir: Path = Path("./data")

    # --- App ---
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    # --- RAG tuning (used in later phases) ---
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 8
    citation_style: str = "apa"  # "apa" | "vancouver"
    chat_temperature: float = 0.2

    # --- Podcast (NotebookLM-style deep dives) ---
    # TTS provider:
    #   "localhost" -> any OpenAI-compatible /v1/audio/speech endpoint (local & private)
    #   "cloudflare" -> Cloudflare Workers AI TTS (needs account id + API token)
    #   "google"    -> Google Cloud Text-to-Speech (needs API key)
    #   "edge-tts"  -> free MP3 via Microsoft Edge TTS (needs internet + pip install)
    #   "disabled"  -> transcript only (no audio)
    tts_provider: str = "edge-tts"

    # --- Localhost OpenAI-compatible server (tts_provider="localhost") ---
    tts_api_url: str = "http://localhost:20128/v1/audio/speech"
    tts_api_key: str = ""
    tts_api_model: str = ""          # e.g. "tts-1", "kokoro", "silero" ... (default "tts-1")
    tts_api_format: str = "mp3"      # "mp3" | "wav" | "opus" | "aac" | "flac"

    # --- Cloudflare Workers AI (tts_provider="cloudflare") ---
    cf_account_id: str = ""
    cf_api_token: str = ""
    cf_model: str = "@cf/microsoft/windows-captioning-or-tts"  # or @cf/playai/tts-*-v1

    # --- Google Cloud Text-to-Speech (tts_provider="google") ---
    google_api_key: str = ""
    google_language_code: str = "en-US"

    # --- Podcast hosts (1-3 people, with names, genders and voices) ---
    # Each host has a name (spoken in the script and matched to its voice),
    # a gender (female/male/neutral) used to pick the right Google/TTS voice,
    # and a voice id for the active provider's TTS.
    podcast_num_hosts: int = 2
    podcast_host_1_name: str = "Alice"
    podcast_host_1_gender: str = "female"
    podcast_host_1_voice: str = "en-US-JennyNeural"
    podcast_host_2_name: str = "Sam"
    podcast_host_2_gender: str = "male"
    podcast_host_2_voice: str = "en-US-ChristopherNeural"
    podcast_host_3_name: str = "Priya"
    podcast_host_3_gender: str = "female"
    podcast_host_3_voice: str = "en-US-AriaNeural"

    @property
    def resolved_data_dir(self) -> Path:
        """Absolute data dir, created on first access."""
        p = self.data_dir
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def uploads_dir(self) -> Path:
        p = self.resolved_data_dir / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        p = self.resolved_data_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def vectorstore_dir(self) -> Path:
        p = self.resolved_data_dir / "chroma"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def podcast_dir(self) -> Path:
        p = self.resolved_data_dir / "podcasts"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.resolved_data_dir / "app.db"

    @property
    def has_openrouter_key(self) -> bool:
        return bool(self.openrouter_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
