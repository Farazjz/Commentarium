"""Log Viewer page – inspect & debug errors live."""
from __future__ import annotations

import time

import streamlit as st

from app.logging_setup import get_recent_logs
from app.ui.helpers import banner

banner("🪵 Log Viewer", "Live application logs. Filter by level and tail the latest output.")

LEVELS = ["all", "debug", "info", "warning", "error", "critical"]

with st.sidebar:
    st.subheader("Log filters")
    level = st.selectbox("Level", LEVELS, index=0)
    n = st.slider("Lines shown", 50, 500, 200, step=50)
    auto = st.checkbox("Auto-refresh (3s)", value=False)

lines = get_recent_logs(n)
if level != "all":
    level_lower = level.lower()
    lines = [ln for ln in lines if level_lower in ln.lower()]

# Reverse so newest is at the bottom; Streamlit code shows text top-to-bottom
lines_for_display = lines  # chronological
if not lines_for_display:
    st.info("No log output yet.")
else:
    # Display in a scrollable container (st.code has no height arg in this
    # Streamlit version, so wrap it in a container with a max-height).
    with st.container(height=600):
        st.code("\n".join(lines_for_display), language="text")

if auto:
    time.sleep(3)
    st.rerun()
