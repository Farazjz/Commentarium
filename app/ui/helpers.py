"""Shared Streamlit UI helpers."""
from __future__ import annotations

import streamlit as st

from app.logging_setup import get_recent_logs


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
