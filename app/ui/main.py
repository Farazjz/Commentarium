"""Streamlit UI for the Commentarium System.

Pages are kept in the `pages/` directory (each a single script). This file
is the shared navigation/entry point that applies common styling.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Streamlit runs this script with the working directory set to app/ui/,
# so the project root is not on sys.path automatically. Insert it so that
# `import app.*` resolves regardless of how the UI is launched.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.config import get_settings
from app.logging_setup import setup_logging

st.set_page_config(
    page_title="Commentarium",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Ensure logging is configured (cheap no-op after first call)
setup_logging()

st.sidebar.title("🧬 Commentarium")
st.sidebar.caption(
    "Chat with your research papers.\n"
    "Upload PDF/Word files, ask questions, and get cited answers."
)
st.sidebar.divider()

# --- Navigation --------------------------------------------------------------
pages = {
    "🏠 Home": "Home",
    "📁 Projects & Files": "Projects",
    "💬 Chat": "Chat",
    "🎙 Podcast": "Podcast",
    "📝 Citations": "Citations",
    "⚙️ Settings": "Settings",
    "🪵 Log Viewer": "Logs",
}
selection = st.sidebar.radio("Navigation", list(pages.keys()))

# Show a hint in the sidebar if the API key is missing.
cfg = get_settings()
if not cfg.has_openrouter_key:
    st.sidebar.warning("⚠️ OpenRouter API key not set. Go to **Settings**.")

# ------------------------------------------------------------------ Exit
st.sidebar.divider()
if st.sidebar.button("🛑 Exit & shut down servers", use_container_width=True):
    st.session_state["_exit_confirm"] = True
    st.rerun()

if st.session_state.get("_exit_confirm"):
    st.sidebar.warning("Are you sure? This stops the API and the web UI.")
    c1, c2 = st.sidebar.columns(2)
    if c1.button("Yes, shut down", type="primary", key="exit_yes"):
        from app.shutdown import shutdown_async

        st.session_state["_exit_confirm"] = False
        # Launch the shutdown in a background thread (it sleeps briefly so the
        # success message can flush to the browser before the processes die).
        shutdown_async()
        st.sidebar.success("Shutting down… the browser tab will close.")
        st.stop()
    if c2.button("Cancel", key="exit_no"):
        st.session_state["_exit_confirm"] = False
        st.rerun()

# Route via exec to the sub-page script. Each page expects the module to
# render itself directly when run.
_PAGE_SCRIPTS = {
    "Home": "app/ui/Home.py",
    "Projects": "app/ui/Projects.py",
    "Chat": "app/ui/Chat.py",
    "Podcast": "app/ui/Podcast.py",
    "Citations": "app/ui/Citations.py",
    "Settings": "app/ui/Settings.py",
    "Logs": "app/ui/Logs.py",
}


def _render(script_path: str) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("page", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


_render(_PAGE_SCRIPTS[pages[selection]])
