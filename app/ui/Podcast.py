"""Podcast page – NotebookLM-style 'deep dive' audio of your papers.

Pick documents (or a whole project), preview each host's voice, then generate a
multi-host conversation script and listen to audio from your chosen TTS provider.
"""
from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from app.config import get_settings
from app.db.metadata import MetadataStore
from app.ui.helpers import banner, render_last_errors

logger = logging.getLogger("app")


def _delete_podcast(pod_id: str) -> None:
    """Delete a podcast row and its audio file (missing audio ignored)."""
    m = MetadataStore()
    try:
        p = m.get_podcast(pod_id)
        if p and p.get("audio_path"):
            Path(p["audio_path"]).unlink(missing_ok=True)
        m.delete_podcast(pod_id)
    finally:
        m.close()


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

# ---- Duration setting ----
st.markdown("### ⏱ Episode duration")
duration_label = st.radio(
    "Target length",
    ["Short (2–5 min)", "Medium (6–10 min)", "Long (11–20 min)"],
    index=1,  # default Medium
    horizontal=True,
    key="pod_duration",
    help="Approximate audio duration. Controls the script word count target.",
)
duration_setting = {"Short (2–5 min)": "short", "Medium (6–10 min)": "medium", "Long (11–20 min)": "long"}[duration_label]

# ---- Custom system prompt ----
st.markdown("### 🤖 System prompt (optional)")
use_custom_prompt = st.checkbox(
    "Use custom system prompt",
    value=False,
    key="pod_use_custom_prompt",
    help="Enable to provide your own system prompt. Leave disabled to use the built-in default.",
)
custom_prompt = ""
if use_custom_prompt:
    custom_prompt = st.text_area(
        "Custom system prompt",
        value="",
        height=180,
        key="pod_custom_prompt",
        help=(
            "Write a custom system prompt that will be used to generate the episode script. "
            "The prompt should describe the host(s), format rules, and any special instructions. "
            "Variables available: {hosts}, {cast}, {line_format}."
        ),
    )
    # Show a preview of the default prompt for reference
    with st.expander("💡 Show default prompt (for reference)", expanded=False):
        cfg = get_settings()
        from app.podcast.hosts import get_hosts
        from app.podcast.generate import _build_script_system
        default_hosts = get_hosts(cfg)
        st.code(_build_script_system(default_hosts), language="text")

col_g, col_m = st.columns(2)
gen = col_g.button("🎙 Generate podcast", use_container_width=True)
if col_m.button("↻ Refresh list", use_container_width=True):
    st.rerun()

cfg = get_settings()
provider_override = ""
lang_map: dict[int, str] = {}
if cfg.tts_provider == "disabled":
    st.caption("ℹ️ TTS is **disabled** (Settings). Generating will save a transcript only.")
else:
    labels = {
        "localhost": f"🌐 Localhost API → `{cfg.tts_api_url or '(not set)'}`",
        "gateway-edge-tts": f"🌐 Gateway edge-tts → `{(cfg.gateway_tts_url or cfg.tts_api_url) or '(not set)'}`",
        "cloudflare": "☁️ Cloudflare Workers AI",
        "google": "🔎 Google Cloud TTS",
        "voicestudio": "🎙 VoiceStudio (local server)",
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

    # ---- per-host episode language (overrides the configured host language) ----
    from app.podcast.hosts import LANGUAGES
    lang_cols = st.columns(len(hosts))
    lang_map: dict[int, str] = {}
    for col, host in zip(lang_cols, hosts):
        with col:
            lang_map[host.index] = col.selectbox(
                f"{host.name} — language",
                list(LANGUAGES),
                index=LANGUAGES.index(host.language) if host.language in LANGUAGES else 0,
                key=f"pod_host_lang_{host.index}",
                format_func=lambda code: {"en": "English", "fa": "فارسی (Farsi)"}.get(code, code),
                help="Dialogue language for this host in this episode: drives the script language and the language sent to VoiceStudio (en or fa).",
            )

    # ---- voice previews -----------------------------------------------
    st.markdown("**🔊 Preview voices** — hear each host before you generate.")
    prev_columns = st.columns(len(hosts))
    from app.podcast.generate import preview_voice
    for col, host in zip(prev_columns, hosts):
        with col:
            st.markdown(f"**{host.name}** ({host.gender})")
            if prev_col := col.button(
                f"▶ Preview {host.name}", key=f"prev_{host.index}",
                use_container_width=True,
            ):
                try:
                    audio_bytes, ext = preview_voice(
                        provider=provider_override or None,
                        voice=host.voice or None,
                        name=host.name,
                        gender=host.gender,
                    )
                    st.audio(audio_bytes, format=f"audio/{ext}", key=f"prev_audio_{host.index}")
                except Exception as exc:  # noqa: BLE001
                    st.caption(f"⚠️ {exc}")

if gen:
    if not picked:
        st.error("Pick at least one document first.")
    else:
        import importlib
        import app.podcast.generate as pod_gen
        importlib.reload(pod_gen)
        generate_podcast = pod_gen.generate_podcast

        progress_bar = st.progress(0.0, text="Starting…")
        status_text = st.empty()

        def _on_progress(done, total, action, message):
            pct = min(100, max(0, int(100 * done / max(total, 1)))) / 100
            progress_bar.progress(pct, text=message or action)

        # Only pass custom prompt if the user explicitly checked the box and typed content
        eff_custom_prompt = custom_prompt.strip() if (use_custom_prompt and custom_prompt.strip()) else None

        try:
            pod = generate_podcast(
                project_id=sel_id,
                document_ids=picked,
                title=title.strip() or "Untitled deep dive",
                provider=provider_override or None,
                system_prompt=eff_custom_prompt,
                duration_setting=duration_setting,
                host_languages=lang_map,
                progress=_on_progress,
            )
        except Exception as exc:  # noqa: BLE001
            progress_bar.empty()
            st.error(f"Generation failed: {exc}")
            pod = None
        if pod:
            if pod.get("status") == "error":
                progress_bar.empty()
                st.error(f"Generation failed: {pod.get('error')}")
            else:
                progress_bar.progress(1.0, text="Done ✅")
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
        # Show duration setting and custom prompt indicator
        ds = pod.get("duration_setting") or "medium"
        ds_label = {"short": "Short (2–5 min)", "medium": "Medium (6–10 min)", "long": "Long (11–20 min)"}.get(ds, ds)
        has_custom = "🤖 Custom prompt" if pod.get("system_prompt") else "📋 Default prompt"
        st.caption(
            f"{len(pod.get('document_ids', []))} paper(s) · {ds_label} · {has_custom} · created {pod['created_at'][:16]}"
        )
        if pod["status"] == "error":
            st.caption(f"⚠️ {pod.get('error')}")
            c_del = st.button("🗑 Delete", key=f"del_pod_{pod['id']}_err", use_container_width=False)
            if c_del:
                _delete_podcast(pod["id"])
                st.rerun()
            continue
        if pod["status"] != "ready":
            st.caption("_Still generating… refresh to check._")
            if st.button("🗑 Delete", key=f"del_pod_{pod['id']}_gen", use_container_width=False):
                _delete_podcast(pod["id"])
                st.rerun()
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

        c1, c2, c3, c4 = st.columns(4)
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
        # Regenerate button (uses stored settings: system_prompt, duration_setting)
        if c3.button("🔄 Regenerate", key=f"regen_pod_{pod['id']}", use_container_width=True):
            import importlib
            import app.podcast.generate as pod_gen
            importlib.reload(pod_gen)
            generate_podcast = pod_gen.generate_podcast

            prog = st.progress(0.0, text="Regenerating…")
            def _rp(done, total, action, message):
                pct = min(100, max(0, int(100 * done / max(total, 1)))) / 100
                prog.progress(pct, text=message or action)
            try:
                # Use stored settings unless user wants to override
                new_pod = generate_podcast(
                    project_id=pod["project_id"],
                    document_ids=pod["document_ids"],
                    title=pod["title"],
                    system_prompt=pod.get("system_prompt") or None,
                    duration_setting=pod.get("duration_setting") or "medium",
                    progress=_rp,
                )
                prog.progress(1.0, text="Done ✅")
                st.success("Regenerated!")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                prog.empty()
                st.error(f"Regenerate failed: {exc}")
        if c4.button("🗑 Delete", key=f"del_pod_{pod['id']}", use_container_width=True):
            _delete_podcast(pod["id"])
            st.success("Deleted episode.")
            st.rerun()

render_last_errors()
