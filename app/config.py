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

    # --- Embedding backend: "local" | "openrouter" ---
    embedding_backend: str = "local"
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"

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
    # TTS: "edge-tts" (free, no key, needs internet) | "disabled" (transcript only)
    tts_backend: str = "edge-tts"
    podcast_host_a_voice: str = "en-US-ChristopherNeural"
    podcast_host_b_voice: str = "en-US-JennyNeural"

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
