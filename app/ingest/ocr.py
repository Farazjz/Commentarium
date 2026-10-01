"""OCR for scanned PDF pages.

Backends (configurable via OCR settings):
- "tesseract": local OCR via Tesseract (free/private). Requires the
  tesseract executable to be installed & configured.
- "vision": use a vision-capable chat model via the gateway to read the page
  image. Costs tokens but handles dense figures/tables better.
- "paddleocr-vl": the PaddleOCR-VL-1.6 vision-language OCR model. Uses a local
  OpenAI-compatible VLM server (PaddlePaddle `mllm_server`, vLLM, SGLang) if
  configured, else falls back to the gateway's vision model.
- "teleocr": the XingChen-AGI/TeleOCR document-parsing VLM. Same local-server
  first, gateway-fallback behaviour as "paddleocr-vl".
- "disabled": never OCR; pages with no extractable text are skipped.

Graceful degradation: helpers like `is_tesseract_available()` let callers
decide how to proceed instead of hard-crashing.
"""
from __future__ import annotations

import base64
import io
import logging
import shutil
import subprocess
from pathlib import Path

from app.config import get_settings
from app.httpclient import post
from app.models_openrouter import LLMClientError

logger = logging.getLogger("app")

# Default transcription prompt for the vision-language OCR models.
_VL_OCR_PROMPT = (
    "Transcribe ALL text on this page exactly as written, preserving line breaks "
    "and table structure. Output only the text."
)


def is_tesseract_available() -> bool:
    """Return True if a working tesseract binary is configured/installed."""
    cfg = get_settings()
    candidates = [cfg.tesseract_cmd] if cfg.tesseract_cmd else []
    # fall back to PATH
    on_path = shutil.which("tesseract")
    if on_path:
        candidates.append(on_path)
    for c in candidates:
        if not c:
            continue
        try:
            r = subprocess.run([c, "--version"], capture_output=True, timeout=15)
            if r.returncode == 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _tesseract_bin() -> str | None:
    cfg = get_settings()
    if cfg.tesseract_cmd:
        return cfg.tesseract_cmd
    return shutil.which("tesseract")


def ocr_page_tesseract(image_bytes: bytes) -> str:
    """Run Tesseract on PNG image bytes and return recognized text."""
    import pytesseract  # imported lazily so it's optional

    binpath = _tesseract_bin()
    if not binpath:
        raise RuntimeError("Tesseract executable not found.")
    pytesseract.pytesseract.tesseract_cmd = binpath
    import PIL.Image as Image

    img = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(img)


def ocr_page_vision(image_bytes: bytes, model: str | None = None) -> str:
    """Ask a vision-capable model to transcribe a page image.

    model: falls back to cfg.chat_model (must be vision-capable).
    """
    cfg = get_settings()
    model = model or cfg.chat_model
    if not model:
        raise LLMClientError("No chat model selected for vision OCR.")
    from app.models_openrouter import _client

    b64 = base64.b64encode(image_bytes).decode("ascii")
    client = _client()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VL_OCR_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        max_tokens=4000,
    )
    return (resp.choices[0].message.content or "").strip()


def _vl_via_endpoint(
    image_bytes: bytes,
    *,
    base_url: str,
    model: str,
    api_key: str = "",
) -> str:
    """Transcribe a page via any OpenAI-compatible chat/completions endpoint.

    Used for local VLM servers (PaddlePaddle `mllm_server`, vLLM, SGLang, LM
    Studio, Ollama...) when the user configures a base URL.
    """
    if not base_url.strip():
        raise LLMClientError("No local VLM server URL configured for this OCR backend.")
    if not model.strip():
        raise LLMClientError("No model id configured for this OCR backend.")
    b64 = base64.b64encode(image_bytes).decode("ascii")
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VL_OCR_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 4000,
    }
    try:
        resp = post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return (
            data["choices"][0]["message"]["content"]
            or data["choices"][0]["message"].get("content") or ""
        ).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Local VLM OCR failed at %s: %s", url, exc)
        raise LLMClientError(f"Local VLM OCR failed: {exc}") from exc


def ocr_page_paddleocr_vl(image_bytes: bytes) -> str:
    """PaddleOCR-VL-1.6 via a local server, else the gateway's vision model."""
    cfg = get_settings()
    if cfg.paddleocr_vl_base_url:
        try:
            return _vl_via_endpoint(
                image_bytes,
                base_url=cfg.paddleocr_vl_base_url,
                model=cfg.paddleocr_vl_model,
            )
        except LLMClientError as exc:
            logger.info("Falling back to gateway for PaddleOCR-VL: %s", exc)
    # Fallback: gateway vision using the model id if it is served there,
    # otherwise the user's configured chat model.
    model = cfg.paddleocr_vl_model or cfg.chat_model
    return ocr_page_vision(image_bytes, model=model)


def ocr_page_teleocr(image_bytes: bytes) -> str:
    """TeleOCR via a local server, else the gateway's vision model."""
    cfg = get_settings()
    if cfg.teleocr_base_url:
        try:
            return _vl_via_endpoint(
                image_bytes,
                base_url=cfg.teleocr_base_url,
                model=cfg.teleocr_model,
            )
        except LLMClientError as exc:
            logger.info("Falling back to gateway for TeleOCR: %s", exc)
    model = cfg.teleocr_model or cfg.chat_model
    return ocr_page_vision(image_bytes, model=model)


def ocr_page(image_bytes: bytes) -> str | None:
    """Dispatch to the configured OCR backend. Returns text or None on failure."""
    cfg = get_settings()
    backend = cfg.ocr_backend
    if backend == "disabled":
        return None
    try:
        if backend == "vision":
            return ocr_page_vision(image_bytes)
        if backend == "paddleocr-vl":
            return ocr_page_paddleocr_vl(image_bytes)
        if backend == "teleocr":
            return ocr_page_teleocr(image_bytes)
        # default: tesseract
        if not is_tesseract_available():
            logger.warning("OCR backend=tesseract but no tesseract binary found; skipping OCR.")
            return None
        return ocr_page_tesseract(image_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.exception("OCR failed for page (backend=%s)", backend)
        return None
