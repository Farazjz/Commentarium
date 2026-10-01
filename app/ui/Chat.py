"""Chat page – ask questions over your indexed papers with grounded answers.

Manages multiple chat sessions per project: create, rename, and delete them
from the sidebar. Includes a granularity toggle (Specific facts vs.
Summarize papers) that switches between focused and distributed retrieval, so
overview questions ("what are these papers about?") cover every document.
"""
from __future__ import annotations

import logging

import streamlit as st

from app.db.chat import ChatStore
from app.db.metadata import MetadataStore
from app.config import get_settings
from app.rag.engine import ask
from app.ui.helpers import banner, render_last_errors

logger = logging.getLogger("app")


def _refresh_sessions(project_id: str) -> list[dict]:
    store = ChatStore()
    try:
        return store.list_sessions(project_id)
    finally:
        store.close()


banner(
    "💬 Chat",
    "Ask questions over your indexed documents. Answers are grounded in source "
    "pages with citations.",
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

sel_label = st.sidebar.selectbox("Project", list(projects.values()))
sel_id = next(pid for pid, name in projects.items() if name == sel_label)

# ------------------------------------------------------------------ granularity
st.sidebar.divider()
mode = st.sidebar.radio(
    "Answer style",
    ["⚡ Specific facts", "📚 Summarize papers"],
    index=0,
    help="Specific facts searches the best matching pages. Summarize papers "
         "samples each document so overview questions cover every paper.",
)
# "Specific facts" leaves it to the engine to auto-detect overview questions
# (distributed=False would force a global top-k that fails on "which papers /
# what are they about" questions). "Summarize papers" always distributes.
use_distributed = True if mode.startswith("📚") else None

# ------------------------------------------------------------------ per-project model controls
st.sidebar.divider()
st.sidebar.markdown("**Project model options**")
_meta_ctrl = MetadataStore()
try:
    proj_meta = _meta_ctrl.get_project(sel_id)
finally:
    _meta_ctrl.close()
proj_chat_model = (proj_meta or {}).get("chat_model") or ""
proj_temp = (proj_meta or {}).get("chat_temperature") or None
proj_topk = (proj_meta or {}).get("top_k") or None

cfg_now = get_settings()
_chat_models = [m["id"] for m in st.session_state.get("models_cache", []) if not m.get("embedding")]
if cfg_now.chat_model and cfg_now.chat_model not in _chat_models:
    _chat_models.insert(0, cfg_now.chat_model)
_chat_opts = ["(use global model)"] + _chat_models if _chat_models else ["(use global model)"]
_model_choice = st.sidebar.selectbox(
    "Chat model for this project",
    options=_chat_opts,
    index=(_chat_opts.index(proj_chat_model) if proj_chat_model in _chat_opts else 0),
    key="proj_model_sel",
    help="Override the global chat model for just this project. '(use global model)' = no override.",
)
_temp_val = float(proj_temp) if proj_temp else float(cfg_now.chat_temperature)
override_temp = st.sidebar.checkbox(
    "Override temperature",
    value=bool(proj_temp),
    key="proj_temp_on",
    help="Tick to give this project its own temperature.",
)
proj_temperature = None
if override_temp:
    proj_temperature = st.sidebar.slider(
        "Temperature", 0.0, 1.5, _temp_val, 0.05,
        key="proj_temp_slider",
    )
_proj_model = "" if _model_choice.startswith("(use global") else _model_choice
if st.sidebar.button("💾 Save project model settings", use_container_width=True, key="save_proj_models"):
    meta_u = MetadataStore()
    try:
        meta_u.update_project(
            sel_id,
            chat_model=_proj_model,
            chat_temperature=(proj_temperature if override_temp else ""),
        )
    finally:
        meta_u.close()
    st.sidebar.success("Saved for this project.")
    st.rerun()

# ------------------------------------------------------------------ sessions
sessions = _refresh_sessions(sel_id)
session_ids = [s["id"] for s in sessions]
session_labels = {s["id"]: (s["title"] or "Untitled") for s in sessions}

# new-session button
if st.sidebar.button("＋ New chat", use_container_width=True):
    store = ChatStore()
    try:
        sess = store.create_session(sel_id, "New chat")
    finally:
        store.close()
    st.session_state["chat_session"] = sess["id"]
    st.rerun()

# session management (rename / delete) in a compact expander under the picker
with st.sidebar.expander("Manage sessions", expanded=False):
    if session_ids:
        manage_id = st.selectbox(
            "Session to manage",
            session_ids,
            format_func=lambda x: session_labels[x],
            key="chat_manage_sel",
        )
        new_title = st.text_input("Rename to", key="chat_rename_in")
        if st.button("✏️ Rename", use_container_width=True):
            if new_title.strip():
                store = ChatStore()
                try:
                    store.rename_session(manage_id, new_title.strip())
                finally:
                    store.close()
                st.success("Renamed.")
                st.rerun()
        if st.button("🗑 Delete session", use_container_width=True):
            if st.session_state.get("chat_session") == manage_id:
                st.session_state.pop("chat_session", None)
            store = ChatStore()
            try:
                store.delete_session(manage_id)
            finally:
                store.close()
            st.success("Deleted.")
            st.rerun()
    else:
        st.caption("No sessions yet.")

# session picker (main): the chosen session IS the active one
if session_ids:
    chosen = st.sidebar.selectbox(
        "Chat sessions",
        session_ids,
        format_func=lambda x: session_labels[x],
        key="chat_sel",
    )
    st.session_state["chat_session"] = chosen
else:
    store = ChatStore()
    try:
        if not st.session_state.get("chat_session"):
            sess = store.create_session(sel_id, "New chat")
            st.session_state["chat_session"] = sess["id"]
    finally:
        store.close()
    chosen = st.session_state["chat_session"]

cur_session = st.session_state.get("chat_session")
if not cur_session or (session_ids and cur_session not in session_ids):
    cur_session = session_ids[0] if session_ids else None

# ---------------------------------------------------------------- history
store = ChatStore()
try:
    msgs = store.get_messages(cur_session) if cur_session else []
finally:
    store.close()

for m in msgs:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant" and m.get("sources"):
            srcs = m["sources"]
            chips = ", ".join(
                f"`{s.get('document_id','?')[:8]}…` p{s.get('page')}" for s in srcs
            )
            st.caption(f"📚 Sources: {chips}")

# ---------------------------------------------------------------- input
prompt = st.chat_input("Ask about your documents…")

if prompt and prompt.strip():
    with st.chat_message("user"):
        st.markdown(prompt)
    # reset session if none
    if not cur_session:
        store = ChatStore()
        try:
            sess = store.create_session(sel_id, "New chat")
        finally:
            store.close()
        cur_session = sess["id"]
        st.session_state["chat_session"] = cur_session

    try:
        with st.chat_message("assistant"):
            with st.spinner("Retrieving and generating…"):
                result = ask(
                    prompt.strip(),
                    session_id=cur_session,
                    project_id=sel_id,
                    distributed=use_distributed,
                    model=_proj_model or None,
                    temperature=proj_temperature,
                )
        st.session_state["chat_session"] = result["session_id"]
        with st.chat_message("assistant"):
            st.markdown(result["answer"])
            if result.get("sources"):
                srcs = result["sources"]
                chips = ", ".join(
                    f"`{s.document_id[:8]}…` p{s.page}" for s in srcs
                )
                st.caption(f"📚 Sources: {chips}")
            refs = result.get("references") or []
            if refs:
                with st.expander(f"📖 References ({result.get('citation_style','apa').upper()})"):
                    for i, ref in enumerate(refs, start=1):
                        st.markdown(f"{i}. {ref['text']}")
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not get an answer: {exc}")

render_last_errors()
