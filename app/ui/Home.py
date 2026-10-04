"""Home page – overview and quick status."""
from __future__ import annotations

import streamlit as st

from app.config import get_settings
from app.ui.helpers import banner, render_last_errors

banner(
    "🧬 Commentarium",
    "A local research assistant: upload papers, chat over them, and export accurate citations.",
)

cfg = get_settings()

c1, c2, c3 = st.columns(3)
c1.metric("OpenRouter key", "✅ set" if cfg.has_openrouter_key else "❌ missing")
c2.metric("Embedding backend", cfg.embedding_backend)
c3.metric("OCR backend", cfg.ocr_backend)

st.markdown("### Getting started")
st.markdown(
    "1. Go to **⚙️ Settings** and paste your OpenRouter API key, then **Test connection**.\n"
    "2. Pick your chat model (and embedding backend if you want non-local embeddings).\n"
    "3. Go to **📁 Projects & Files** to create a project and upload your PDF/Word papers.\n"
    "4. Use **💬 Chat** to ask questions over your files — answers come with page-level citations and per-project model controls.\n"
    "5. Try **🎙 Podcast studio** to generate a NotebookLM-style audio deep-dive of your papers.\n"
    "6. Check **🪵 Log Viewer** any time to debug errors.\n"
)

st.markdown("### About citations")
st.info(
    "Every answer is grounded in the pages of your uploaded documents. "
    "References are rendered from metadata you verify (authors, year, journal) in "
    "**📝 Citations**, in **APA 7** or **Vancouver** style."
)

render_last_errors()
