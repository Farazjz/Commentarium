"""OCR to Markdown page.

Upload PDFs or images, run OCR via the configured or selected backend,
and export clean .md (Markdown) files with live preview and download options.
"""
from __future__ import annotations

import datetime
import io
import logging
import zipfile
from pathlib import Path

import streamlit as st

from app.config import get_settings
from app.ingest.ocr import is_tesseract_available, ocr_page
from app.ui.helpers import banner, render_last_errors

logger = logging.getLogger("app")

banner("🔍 OCR to Markdown", "Upload PDF documents or images, extract text with OCR, and export clean .md files.")

cfg = get_settings()
out_dir = cfg.ocr_output_dir

# --- Sidebar / Controls -------------------------------------------------------
with st.sidebar:
    st.subheader("⚙️ OCR Configuration")

    backends = ["tesseract", "vision", "paddleocr-vl", "teleocr"]
    default_backend_idx = backends.index(cfg.ocr_backend) if cfg.ocr_backend in backends else 0

    selected_backend = st.selectbox(
        "OCR Backend",
        backends,
        index=default_backend_idx,
        help="Select the OCR engine to process pages and images.",
    )

    custom_model = None
    if selected_backend == "vision":
        custom_model = st.text_input(
            "Vision model",
            value=cfg.chat_model,
            help="Chat/vision model identifier for OCR.",
        ).strip() or None
        if not cfg.has_openrouter_key:
            st.warning("⚠️ OpenRouter API key is not set. Vision OCR will fail until configured in Settings.")
    elif selected_backend == "tesseract":
        if not is_tesseract_available():
            st.warning("⚠️ Tesseract executable not detected. Install Tesseract or set path in Settings.")
    elif selected_backend in ("paddleocr-vl", "teleocr"):
        base_url = cfg.paddleocr_vl_base_url if selected_backend == "paddleocr-vl" else cfg.teleocr_base_url
        if base_url:
            st.caption(f"Using local server: `{base_url}`")
        else:
            st.caption("No local server URL set in Settings. Will fall back to gateway vision model.")

    st.divider()
    st.subheader("📄 PDF Processing Mode")
    pdf_mode = st.radio(
        "PDF text extraction",
        ["Smart OCR (extract text, OCR scanned pages)", "Force Full OCR (rasterize & OCR all pages)"],
        index=0,
        help="Smart OCR reads digital text first and only OCRs pages without embedded text. Force Full OCR rasterizes and runs OCR on every page.",
    )
    force_full_ocr = pdf_mode.startswith("Force")

    dpi = st.slider(
        "Page rasterization DPI",
        min_value=100,
        max_value=300,
        value=200,
        step=50,
        help="Higher DPI improves OCR accuracy for small text but takes longer.",
    )

# --- File Uploader -----------------------------------------------------------
uploaded_files = st.file_uploader(
    "Upload PDF or image files",
    type=["pdf", "png", "jpg", "jpeg", "webp", "bmp", "tiff"],
    accept_multiple_files=True,
    help="Select one or multiple PDF files or images to run OCR on.",
)

if "ocr_results" not in st.session_state:
    st.session_state["ocr_results"] = {}


def _process_image(file_bytes: bytes, filename: str) -> str:
    """Run OCR on a single image and return Markdown string."""
    text = ocr_page(file_bytes, backend=selected_backend, model=custom_model) or ""
    text = text.strip()
    return f"# {filename}\n\n{text}\n"


def _process_pdf(file_bytes: bytes, filename: str, force_ocr: bool, raster_dpi: int) -> str:
    """Run OCR / text extraction on each page of a PDF and return Markdown string."""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = doc.page_count
    md_parts = [f"# {filename}\n"]

    progress_text = st.empty()
    bar = st.progress(0.0)

    for idx in range(total_pages):
        page_num = idx + 1
        progress_text.text(f"Processing {filename} – Page {page_num} of {total_pages}…")
        bar.progress(page_num / total_pages)

        page = doc.load_page(idx)
        text = ""

        if not force_ocr:
            text = page.get_text("text").strip()

        if not text:
            try:
                pix = page.get_pixmap(dpi=raster_dpi)
                img_bytes = pix.tobytes("png")
                ocr_text = ocr_page(img_bytes, backend=selected_backend, model=custom_model)
                if ocr_text:
                    text = ocr_text.strip()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to rasterize/OCR page %d of %s: %s", page_num, filename, exc)

        md_parts.append(f"## Page {page_num}\n\n{text if text else '*(No text detected)*'}\n")

    doc.close()
    progress_text.empty()
    bar.empty()
    return "\n---\n\n".join(md_parts)


if uploaded_files:
    st.write(f"**{len(uploaded_files)} file(s) ready for OCR:**")
    for uf in uploaded_files:
        st.caption(f"• **{uf.name}** ({uf.size / 1024:.1f} KB)")

    if st.button("🚀 Run OCR & Generate Markdown", type="primary", use_container_width=True):
        results = {}
        for uf in uploaded_files:
            file_bytes = uf.getvalue()
            filename = uf.name
            stem = Path(filename).stem
            is_pdf = filename.lower().endswith(".pdf")

            with st.spinner(f"Processing {filename} with backend `{selected_backend}`…"):
                try:
                    if is_pdf:
                        md_content = _process_pdf(file_bytes, filename, force_full_ocr, dpi)
                    else:
                        md_content = _process_image(file_bytes, filename)

                    results[filename] = {
                        "stem": stem,
                        "content": md_content,
                        "size": len(md_content.encode("utf-8")),
                    }

                    # Save to data/ocr_output/
                    out_path = out_dir / f"{stem}.md"
                    out_path.write_text(md_content, encoding="utf-8")
                    logger.info("Saved OCR output to %s", out_path)

                except Exception as exc:  # noqa: BLE001
                    logger.exception("OCR failed for %s: %s", filename, exc)
                    st.error(f"Failed to process {filename}: {exc}")

        st.session_state["ocr_results"] = results
        st.success(f"Successfully processed {len(results)} file(s)! Saved to `{out_dir.as_posix()}`.")

# --- Current Results Display -------------------------------------------------
results = st.session_state.get("ocr_results", {})
if results:
    st.divider()
    st.subheader("📑 OCR Output & Markdown Previews")

    if len(results) > 1:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, data in results.items():
                zf.writestr(f"{data['stem']}.md", data["content"])
        zip_buffer.seek(0)
        st.download_button(
            label="📦 Download All (.zip)",
            data=zip_buffer,
            file_name="ocr_markdown_results.zip",
            mime="application/zip",
            use_container_width=True,
        )

    for fname, data in results.items():
        stem = data["stem"]
        content = data["content"]
        words = len(content.split())

        with st.expander(f"📄 {fname} ({words} words, {data['size']} bytes)", expanded=True):
            c1, c2 = st.columns([1, 4])
            with c1:
                st.download_button(
                    label=f"📥 Download {stem}.md",
                    data=content.encode("utf-8"),
                    file_name=f"{stem}.md",
                    mime="text/markdown",
                    key=f"dl_res_{fname}",
                )
            with c2:
                st.caption(f"Saved locally: `{out_dir / f'{stem}.md'}`")

            tab_rendered, tab_raw = st.tabs(["👁️ Rendered Preview", "📝 Raw Markdown"])
            with tab_rendered:
                st.markdown(content)
            with tab_raw:
                st.code(content, language="markdown")

# --- Saved History / Saved Files ----------------------------------------------
st.divider()
st.subheader("📁 Saved Markdown Files")

saved_files = sorted(out_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)

if not saved_files:
    st.caption("No generated markdown files in `data/ocr_output/` yet.")
else:
    st.caption(f"Found {len(saved_files)} saved document(s) in `{out_dir.as_posix()}`:")

    for p in saved_files:
        stat = p.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size_kb = stat.st_size / 1024

        col_info, col_dl, col_del = st.columns([4, 1.5, 1])
        with col_info:
            st.write(f"📄 **{p.name}** ({size_kb:.1f} KB, modified {mtime})")
        with col_dl:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                st.download_button(
                    label="📥 Download",
                    data=content.encode("utf-8"),
                    file_name=p.name,
                    mime="text/markdown",
                    key=f"hist_dl_{p.name}",
                )
            except Exception as e:
                st.error(str(e))
        with col_del:
            if st.button("🗑️ Delete", key=f"hist_del_{p.name}"):
                p.unlink(missing_ok=True)
                st.rerun()

render_last_errors()
