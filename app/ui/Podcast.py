"""Podcast page – NotebookLM-style 'deep dive' audio of your papers.

Pick documents (or a whole project), generate a two-host conversation script,
and listen to an MP3 generated with edge-tts (free, no API key).
"""
from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from app.config import get_settings
from app.db.metadata import MetadataStore
from app.ui.helpers import banner, render_last_errors

logger = logging.getLogger("app")


banner(
    "🎙 Podcast studio",
    "NotebookLM-style deep dives: the app writes a multi-host conversation about "
    "your papers and reads it aloud via your chosen TTS provider (local, Cloudflare, Google, or edge-tts).",
)

# ------------------------------------------------------------------ project nav
meta = MetadataStore()
try:
    projects = {p["id"]: p["name"] for p in meta.list_projects()}
finally:
    meta.close()

if not projects:
    st.info("No projects yet. Upload and index documents in the **Projects & Files** page first.")
    render_last_errors()
    st.stop()

sel_label = st.selectbox("Project", list(projects.values()), key="pod_project")
sel_id = next(pid for pid, name in projects.items() if name == sel_label)

meta = MetadataStore()
try:
    docs = meta.list_documents(sel_id)
finally:
    meta.close()

indexed = [d for d in docs if d["status"] == "indexed"]
if not indexed:
    st.info("No indexed documents in this project yet. Ingest them in **Projects & Files** first.")
    render_last_errors()
    st.stop()

# ------------------------------------------------------------------ selection
st.markdown("### 📚 Select papers to discuss")
sel_all = st.checkbox("Select all documents", value=False, key="pod_all")
doc_names = {d["id"]: d["filename"] for d in indexed}
choices = list(doc_names.keys())

if sel_all:
    picked = choices
    st.caption(f"{len(picked)} document(s) selected.")
else:
    picked = st.multiselect(
        "Documents",
        choices,
        format_func=lambda x: doc_names[x],
        key="pod_docs",
    )

title = st.text_input(
    "Episode title",
    value=f"Deep dive: {sel_label}",
    key="pod_title",
)

col_g, col_m = st.columns(2)
gen = col_g.button("🎙 Generate podcast", use_container_width=True)
if col_m.button("↻ Refresh list", use_container_width=True):
    st.rerun()

cfg = get_settings()
provider_override = ""
if cfg.tts_provider == "disabled":
    st.caption("ℹ️ TTS is **disabled** (Settings). Generating will save a transcript only.")
else:
    labels = {
        "localhost": f"🌐 Localhost API → `{cfg.tts_api_url or '(not set)'}`",
        "cloudflare": "☁️ Cloudflare Workers AI",
        "google": "🔎 Google Cloud TTS",
        "edge-tts": "🎙 edge-tts (free MP3, internet)",
    }
    from app.podcast.hosts import PROVIDER_LABELS, get_hosts
    hosts = get_hosts(cfg)
    cast = ", ".join(f"{h.name} ({h.gender})" for h in hosts)
    st.caption(f"🔊 TTS provider: **{labels.get(cfg.tts_provider, cfg.tts_provider)}** · Cast: {cast}")

    # Optional per-episode provider override
    from app.podcast.hosts import PROVIDERS
    override_choice = st.selectbox(
        "TTS provider for this episode (optional override)",
        ["(use default from Settings)"] + [p for p in PROVIDERS if p != "disabled"],
        format_func=lambda p: ("(use default from Settings)" if p.startswith("(use") else PROVIDER_LABELS.get(p, p)),
        key="pod_provider_override",
    )
    provider_override = "" if override_choice.startswith("(use") else override_choice

if gen:
    if not picked:
        st.error("Pick at least one document first.")
    else:
        from app.podcast.generate import generate_podcast

        with st.spinner("Writing the episode script + audio… this takes a minute"):
            try:
                pod = generate_podcast(
                    project_id=sel_id,
                    document_ids=picked,
                    title=title.strip() or "Untitled deep dive",
                    provider=provider_override or None,
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Generation failed: {exc}")
                pod = None
        if pod:
            if pod.get("status") == "error":
                st.error(f"Generation failed: {pod.get('error')}")
            else:
                st.success("Podcast ready!")
                st.rerun()

# ------------------------------------------------------------------ existing episodes
st.divider()
st.markdown("### 📼 Episodes")

meta = MetadataStore()
try:
    pods = meta.list_podcasts(sel_id)
finally:
    meta.close()

if not pods:
    st.caption("No episodes yet. Select documents above and hit **Generate podcast**.")
    render_last_errors()
    st.stop()

for pod in pods:
    with st.container(border=True):
        status_icon = {
            "ready": "✅", "error": "❌", "generating": "🔄", "pending": "⏳",
        }.get(pod["status"], "❓")
        st.markdown(f"**{pod['title']}** {status_icon}")
        st.caption(
            f"{len(pod.get('document_ids', []))} paper(s) · created {pod['created_at'][:16]}"
        )
        if pod["status"] == "error":
            st.caption(f"⚠️ {pod.get('error')}")
            continue
        if pod["status"] != "ready":
            st.caption("_Still generating… refresh to check._")
            continue

        # audio
        if pod.get("audio_path") and Path(pod["audio_path"]).exists():
            try:
                audio_bytes = Path(pod["audio_path"]).read_bytes()
                st.audio(audio_bytes, format="audio/mp3")
            except Exception as exc:  # noqa: BLE001
                st.caption(f"(Could not load audio: {exc})")
        elif pod.get("script"):
            st.caption("📝 Transcript only (no audio generated).")

        # transcript
        if pod.get("script"):
            with st.expander("📝 Read transcript", expanded=False):
                st.markdown(pod["script"])

        c1, c2, c3 = st.columns(3)
        if pod.get("audio_path") and Path(pod["audio_path"]).exists():
            try:
                data = Path(pod["audio_path"]).read_bytes()
                c1.download_button(
                    "⬇ MP3",
                    data=data,
                    file_name=f"{pod['title']}.mp3",
                    mime="audio/mpeg",
                    key=f"dl_audio_{pod['id']}",
                )
            except Exception:  # noqa: BLE001
                pass
        if pod.get("script"):
            c2.download_button(
                "⬇ Transcript",
                data=pod["script"],
                file_name=f"{pod['title']}.md",
                mime="text/markdown",
                key=f"dl_txt_{pod['id']}",
            )
        if c3.button("🗑 Delete", key=f"del_pod_{pod['id']}"):
            m = MetadataStore()
            try:
                p = m.get_podcast(pod["id"])
                if p and p.get("audio_path"):
                    Path(p["audio_path"]).unlink(missing_ok=True)
                m.delete_podcast(pod["id"])
            finally:
                m.close()
            st.success("Deleted episode.")
            st.rerun()

render_last_errors()
