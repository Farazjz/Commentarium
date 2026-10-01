"""Shared Streamlit UI helpers."""
from __future__ import annotations

import logging
import os

import streamlit as st

from app.logging_setup import get_recent_logs

logger = logging.getLogger("app")


def banner(title: str, subtitle: str | None = None) -> None:
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)
    st.divider()


def render_last_errors(n: int = 200) -> None:
    """Show recent ERROR/CRITICAL log lines inline (helpful for debugging UI)."""
    lines = [ln for ln in get_recent_logs(n) if "ERROR" in ln or "CRITICAL" in ln]
    if not lines:
        return
    with st.expander(f"🔴 Recent errors ({len(lines)})", expanded=False):
        st.code("\n".join(lines[-10:]), language="text")


def browse_folder_dialog(title: str = "Select a folder") -> str:
    """Open a native folder picker and return the chosen path ('' if cancelled).

    Uses tkinter (ships with CPython, no extra dependency). Falls back to
    matching on an env var for headless environments.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title=title)
        root.destroy()
        return chosen or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("tkinter folder dialog unavailable: %s", exc)
        return ""
