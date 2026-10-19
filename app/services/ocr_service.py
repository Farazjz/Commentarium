"""OCR → Markdown extraction service (v2.0).

Server-side port of the Streamlit OCR page's in-process pipeline so the SPA
(or anything else) can run it over HTTP:

- PDFs: "smart" mode reads embedded text and only OCRs pages without a text
  layer; "forced" mode rasterizes every page at a given DPI and OCRs it.
- Images: OCR directly (tesseract / vision / paddleocr-vl / teleocr).
- Runs are cancellable per page (a `threading.Event`), progress is reported
  as (0..1, message) so the SPA can render live per-page status via /jobs.
- Markdown files are written to `data/ocr_output/`.

This mirrors v1 behaviour 1:1 (same markdown structure, same save location);
the Streamlit page stays untouched.
"""
from __future__ import annotations

import datetime
import logging
import re
import threading
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger("app")

BACKENDS: tuple[str, ...] = ("tesseract", "vision", "paddleocr-vl", "teleocr")
ALLOWED_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")


class OCRServiceError(Exception):
    """Raised for invalid requests / backend failures."""


class OCRCancelled(OCRServiceError):
    """Raised when a job's cancel event fired mid-extraction."""


# ---------------------------------------------------------------------------
# Output management (data/ocr_output)
# ---------------------------------------------------------------------------

def _safe_stem(stem: str) -> str:
    stem = re.sub(r"[^\w\-. ]+", "_", (stem or "").strip()) or "output"
    return stem[:120]


def save_output(stem: str, content: str) -> Path:
    """Write the markdown result to data/ocr_output/<stem>.md (v1 behaviour)."""
    path = get_settings().ocr_output_dir / f"{_safe_stem(stem)}.md"
    path.write_text(content, encoding="utf-8")
    logger.info("Saved OCR output to %s", path)
    return path


def list_output() -> list[dict]:
    """Saved .md files, newest first."""
    out = get_settings().ocr_output_dir
    files = []
    for p in sorted(out.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        st_ = p.stat()
        files.append(
            {
                "name": p.name,
                "size_bytes": st_.st_size,
                "modified": datetime.datetime.fromtimestamp(st_.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return files


def _resolve(name: str) -> Path | None:
    """Resolve a requested output file name safely (no traversal, .md only)."""
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    if not name.lower().endswith(".md"):
        return None
    base = get_settings().ocr_output_dir
    p = (base / name).resolve()
    if p.parent != base.resolve() or not p.exists():
        return None
    return p


def delete_output(name: str) -> bool:
    p = _resolve(name)
    if not p:
        return False
    p.unlink(missing_ok=True)
    logger.info("Deleted OCR output %s", p)
    return True


def read_output(name: str) -> str | None:
    p = _resolve(name)
    if not p:
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def output_path(name: str) -> Path | None:
    return _resolve(name)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_markdown(
    data: bytes,
    filename: str,
    *,
    backend: str = "tesseract",
    force_ocr: bool = False,
    dpi: int = 200,
    model: str | None = None,
    progress_cb=None,                # progress_cb(progress 0..1, message)
    cancel: threading.Event | None = None,
) -> dict:
    """Extract Markdown from one PDF or image. Returns metadata + content.

    Raises OCRCancelled when `cancel` fires; OCRServiceError on bad input.
    """
    if not data:
        raise OCRServiceError("Empty upload.")
    name = (filename or "output").strip() or "output"
    if not name.lower().endswith(ALLOWED_SUFFIXES):
        raise OCRServiceError(f"Unsupported file type: {name} (use {', '.join(ALLOWED_SUFFIXES)})")
    backend = (backend or "").strip().lower()
    if backend not in BACKENDS:
        raise OCRServiceError(f"Unknown OCR backend: {backend or '(empty)'}")

    dpi = max(72, min(int(dpi or 200), 600))

    def _cancelled() -> bool:
        return bool(cancel and cancel.is_set())

    if name.lower().endswith(".pdf"):
        content, pages = _extract_pdf(data, name, backend, force_ocr, dpi, model, progress_cb, _cancelled)
    else:
        content, pages = _extract_image(data, name, backend, model, _cancelled)
        if progress_cb:
            progress_cb(1.0, f"{name} · done")

    return {
        "filename": name,
        "stem": Path(name).stem,
        "pages": pages,
        "content": content,
        "wc": len(content.split()),
        "size_bytes": len(content.encode("utf-8")),
        "backend": backend,
        "force_ocr": bool(force_ocr),
        "dpi": dpi if name.lower().endswith(".pdf") else None,
    }


def _rasterize_ocr(page, dpi: int, backend: str, model: str | None) -> str:
    """Render one PDF page to PNG at `dpi` and run OCR on the raster."""
    pix = page.get_pixmap(dpi=dpi)
    text = ocr_page_tool(pix.tobytes("png"), backend=backend, model=model)
    return (text or "").strip()


def _extract_pdf(data, filename, backend, force_ocr, dpi, model, progress_cb, cancelled):
    import fitz  # PyMuPDF

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise OCRServiceError(f"Could not open PDF: {exc}") from exc

    total = doc.page_count
    parts = [f"# {filename}\n"]
    try:
        for idx in range(total):
            if cancelled():
                raise OCRCancelled("Cancelled")
            page = doc.load_page(idx)
            text = ""
            if not force_ocr:
                text = page.get_text("text").strip()
            if not text:
                try:
                    text = _rasterize_ocr(page, dpi, backend, model)
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to rasterize/OCR page %d of %s", idx + 1, filename)
            parts.append(f"## Page {idx + 1}\n\n{text if text else '*(No text detected)*'}\n")
            if progress_cb:
                progress_cb((idx + 1) / total, f"{filename} · page {idx + 1}/{total}")
    finally:
        doc.close()
    return "\n---\n\n".join(parts), total


def _extract_image(data, filename, backend, model, cancelled):
    if cancelled():
        raise OCRCancelled("Cancelled")
    text = ocr_page_tool(data, backend=backend, model=model) or ""
    return f"# {filename}\n\n{text.strip()}\n", 1


def ocr_page_tool(image_bytes: bytes, *, backend: str, model: str | None = None) -> str | None:
    """Thin wrapper over the v1 OCR backend dispatcher (same signatures)."""
    from app.ingest.ocr import ocr_page

    return ocr_page(image_bytes, backend=backend, model=model or None)


def extract_and_save(
    data: bytes,
    filename: str,
    *,
    backend: str,
    force_ocr: bool = False,
    dpi: int = 200,
    model: str | None = None,
    progress_cb=None,
    cancel: threading.Event | None = None,
) -> dict:
    """Convenience: extract + save to data/ocr_output, returning the enriched result."""
    result = extract_markdown(
        data, filename, backend=backend, force_ocr=force_ocr, dpi=dpi,
        model=model, progress_cb=progress_cb, cancel=cancel,
    )
    path = save_output(result["stem"], result["content"])
    result["saved_name"] = path.name
    return result
