"""Projects & Files page – manage projects, documents, and their content.

Redesigned as a proper workspace:
  * Project card grid with Edit / Delete per project.
  * Projects are expandable into a detail panel showing that project's
    documents (upload, per-doc summary, re-index, delete, chunk counts).
  * A per-document content/actions card for the focused workflow.
"""
from __future__ import annotations

import json
import logging

import streamlit as st

from app.config import get_settings
from app.db.metadata import MetadataStore
from app.db.vectorstore import VectorStore
from app.ingest.pipeline import ingest_document, store_upload
from app.ui.helpers import banner, render_last_errors

logger = logging.getLogger("app")
_NS = "default"


def _open_meta() -> MetadataStore:
    return MetadataStore()


def _documents(project_id: str) -> list[dict]:
    meta = _open_meta()
    try:
        return meta.list_documents(project_id)
    finally:
        meta.close()


def _chunk_count_for(project_id: str) -> int:
    """Count chunks across a project's documents (vs.count is per-document)."""
    total = 0
    vs = VectorStore(_NS)
    try:
        for d in _documents(project_id):
            total += vs.count(d["id"])
    finally:
        vs.close()
    return total


def _status_icon(status: str) -> str:
    return {
        "indexed": "✅", "error": "❌", "pending": "⏳",
        "parsing": "🔄", "chunking": "🔄", "embedding": "🔄", "indexing": "🔄",
    }.get(status, "❓")


# Summary kind labels for the per-document dropdown
def _summary_meta():
    return {
        "brief": ("📄 Brief summary", "Concise overview (aim/methods/findings)."),
        "detailed": ("📖 Detailed", "Longer structured walkthrough."),
        "key_points": ("🔑 Key points", "Bulleted takeaways."),
        "tldr": ("⚡ TL;DR", "One-line elevator pitch."),
        "quiz": ("🎓 Quiz", "Study quiz (3 MCQs + answer key)."),
    }


def _summary_cache_key(document_id: str, kind: str) -> str:
    return f"doc_summary_{document_id}_{kind}"


def _render_project_cards() -> None:
    """Render the project grid with edit/delete actions."""
    meta = _open_meta()
    try:
        projects = meta.list_projects()
    finally:
        meta.close()

    if not projects:
        st.info("No projects yet. Create your first project above to get started.")
        return

    for p in projects:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"### 📁 {p['name']}")
                desc = (p.get("description") or "").strip()
                if desc:
                    st.caption(desc)
                else:
                    st.caption("_No description_")
                st.caption(
                    f"🗂 {p['doc_count']} document(s) · created {p['created_at'][:10]}"
                )
            with c2:
                edit_clicked = st.button("✏️ Edit", key=f"edit_{p['id']}", use_container_width=True)
                delete_clicked = st.button("🗑 Delete", key=f"del_{p['id']}", use_container_width=True)

            # ---- Edit dialog
            if edit_clicked:
                with st.form(f"edit_form_{p['id']}"):
                    name = st.text_input("Project name", value=p["name"], key=f"en_{p['id']}")
                    desc = st.text_area("Description", value=p.get("description") or "", key=f"ed_{p['id']}")
                    sub = st.form_submit_button("Save changes")
                if sub and name.strip():
                    m = _open_meta()
                    try:
                        m.update_project(p["id"], name=name.strip(), description=desc.strip())
                    finally:
                        m.close()
                    st.success(f"Updated **{name.strip()}**")
                    st.rerun()

            # ---- Delete: set pending in session_state so the confirm button
            #      survives the rerun that Streamlit triggers after every click.
            if delete_clicked:
                st.session_state["pending_del_project"] = p["id"]

            pending = st.session_state.get("pending_del_project")
            if pending == p["id"]:
                if st.button(
                    f"⚠️ Confirm delete '{p['name']}' and ALL its data?",
                    key=f"confirm_del_{p['id']}",
                ):
                    m = _open_meta()
                    vs = VectorStore(_NS)
                    try:
                        docs = m.list_documents(p["id"])
                        chunks = 0
                        for d in docs:
                            chunks += vs.delete_document(d["id"])
                            if d.get("stored_path"):
                                from pathlib import Path
                                Path(d["stored_path"]).unlink(missing_ok=True)
                        from app.db.chat import ChatStore
                        chat = ChatStore()
                        try:
                            for sess in chat.list_sessions(p["id"]):
                                chat.delete_session(sess["id"])
                        finally:
                            chat.close()
                        m.delete_project(p["id"])
                    finally:
                        vs.close()
                        m.close()
                    st.session_state.pop("pending_del_project", None)
                    st.success(
                        f"Deleted **{p['name']}** ({len(docs)} document(s), "
                        f"{chunks} chunks removed)."
                    )
                    st.rerun()
                if st.button("✖ Cancel", key=f"cancel_del_{p['id']}"):
                    st.session_state.pop("pending_del_project", None)
                    st.rerun()

            st.divider()
            # ---- Upload documents into this project (multi-file)
            with st.container(border=True):
                uploaded = st.file_uploader(
                    f"Upload papers to **{p['name']}** (PDF / DOCX / TXT / MD…)",
                    type=["pdf", "docx", "doc", "txt", "md", "markdown"],
                    accept_multiple_files=True,
                    key=f"upload_{p['id']}",
                )
                # Indexing mode: OCR+index vs direct embed. Applied to ALL files
                # in this batch. DOCX/TXT/MD are unaffected (already have text).
                up_mode = st.radio(
                    "Indexing mode for this batch",
                    ["OCR + index (handles scanned/pdf-image pages)",
                     "Direct index (text only, faster)"],
                    index=0 if get_settings().default_ocr else 1,
                    key=f"up_mode_{p['id']}",
                    horizontal=True,
                    help="OCR + index: image/scanned pages are OCR'd before "
                         "embedding (best for old/scan-heavy PDFs). Direct index: "
                         "extract text only, no OCR (fastest for Word/text files "
                         "and text-based PDFs). You can still re-index a "
                         "document with the other mode later.",
                )
                up_ocr = up_mode.startswith("OCR")
                up_submit = st.button(
                    "⬆ Upload & index", key=f"up_btn_{p['id']}",
                    use_container_width=True,
                    disabled=not (uploaded and len(uploaded) > 0),
                )
                if up_submit and uploaded:
                    for uf in uploaded:
                        try:
                            data = uf.getvalue()
                            if len(data) == 0:
                                st.error(f"**{uf.name}** is empty — skipped.")
                                continue
                            with st.spinner(f"Ingesting {uf.name} ({'OCR' if up_ocr else 'direct'})…"):
                                doc = store_upload(p["id"], data, uf.name)
                                s = ingest_document(doc["id"], ocr=up_ocr)
                            st.success(
                                f"Uploaded **{uf.name}** → "
                                f"{s['chunks']} chunks / {s['pages']} pages "
                                f"({ 'OCR' if up_ocr else 'direct' } index)."
                            )
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"**{uf.name}** upload/ingest failed: {exc}")
                    st.rerun()

            # documents in this project
            docs = _documents(p["id"])
            if not docs:
                st.caption("No documents in this project yet.")
            else:
                with st.expander(f"📄 {len(docs)} document(s) in this project", expanded=False):
                    _render_doc_table(p["id"], docs)


def _render_doc_table(project_id: str, docs: list[dict]) -> None:
    """Outline rows of documents with inline summary/re-index/delete actions."""
    vs = VectorStore(_NS)
    try:
        for d in docs:
            status = d["status"]
            pages = d["page_count"] if d["page_count"] else "–"
            nchunks = vs.count(d["id"]) if status == "indexed" else 0
            with st.container(border=True):
                c1, c2 = st.columns([3, 1])
                with c1:
                    # Badge showing which indexing mode produced the current
                    # chunk set (set on the last successful re-index / ingest).
                    index_mode = d.get("index_mode")
                    badge = ""
                    if index_mode == "ocr":
                        badge = " 🤖 **OCR**"
                    elif index_mode == "direct":
                        badge = " ⚡ **Direct**"
                    st.markdown(f"**{d['filename']}**{badge} {_status_icon(status)}")
                    st.caption(
                        f"{d['file_type'].upper()} · {pages} page(s) · {nchunks} chunk(s) "
                        f"· {status}"
                        + (f" · {index_mode} index" if index_mode else "")
                    )
                    # tags
                    try:
                        tags = json.loads(d.get("tags") or "[]")
                    except json.JSONDecodeError:
                        tags = []
                    if tags:
                        st.caption("🏷 " + "  ".join(f"`{t}`" for t in tags))
                with c2:
                    summ = st.button("📝 Summary", key=f"sum_{d['id']}", use_container_width=True)
                    reidx = st.button("🔄 Re-index", key=f"re_{d['id']}", use_container_width=True)
                    dele = st.button("🗑 Delete", key=f"del_{d['id']}", use_container_width=True)

                # ---- Summary: persist open state so the panel survives the
                #      Streamlit rerun (otherwise the click "does nothing").
                sum_key = f"doc_summary_open_{d['id']}"
                if summ:
                    st.session_state[sum_key] = True
                if st.session_state.get(sum_key):
                    _render_summary_panel(d["id"], d["filename"], sum_key)
                if reidx:
                    try:
                        # Choose re-index mode: direct vs OCR+index.
                        re_mode = st.radio(
                            "Re-index mode",
                            ["Direct (text only)", "OCR + index"],
                            index=0,
                            key=f"re_mode_{d['id']}",
                            horizontal=True,
                        )
                        with st.spinner(f"Re-indexing {d['filename']} ("
                                        f"{'OCR' if re_mode.startswith('OCR') else 'direct'})…"):
                            s = ingest_document(
                                d["id"], reindex=True,
                                ocr=re_mode.startswith("OCR"),
                            )
                        st.success(f"Re-indexed: {s['chunks']} chunks / {s['pages']} pages")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Re-index failed: {exc}")
                if dele:
                    try:
                        removed = vs.delete_document(d["id"])
                        m = _open_meta()
                        try:
                            m.clear_summaries(d["id"])
                            m.delete_document(d["id"])
                        finally:
                            m.close()
                        st.success(f"Deleted **{d['filename']}** ({removed} chunks).")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Delete failed: {exc}")
                if d.get("error"):
                    st.caption(f"⚠️ {d['error']}")

                # notes + tags editor (collapsible via popover, since Streamlit
                # forbids nesting expanders inside the project's expander)
                with st.popover("🏷 Tags & 📝 notes"):
                    _render_tag_notes_editor(d)
    finally:
        vs.close()


def _render_tag_notes_editor(d: dict) -> None:
    """Inline editor for a document's tags (comma-separated) and freeform notes."""
    try:
        tags = json.loads(d.get("tags") or "[]")
    except json.JSONDecodeError:
        tags = []
    current_tags = ", ".join(str(t) for t in tags)
    tag_val = st.text_input("Tags (comma-separated)", value=current_tags, key=f"tag_in_{d['id']}")
    notes_val = st.text_area(
        "Notes", value=(d.get("notes") or ""), key=f"notes_in_{d['id']}",
        height=90, placeholder="Your own notes about this paper…",
    )
    if st.button("💾 Save tags & notes", key=f"save_meta_{d['id']}", use_container_width=True):
        new_tags = [t.strip() for t in tag_val.split(",") if t.strip()]
        m = _open_meta()
        try:
            m.set_tags(d["id"], new_tags)
            m.set_notes(d["id"], notes_val.strip())
        finally:
            m.close()
        st.success("Saved tags & notes.")
        st.rerun()


def _render_summary_panel(document_id: str, filename: str, open_key: str | None = None) -> None:
    """Let the user pick a summary kind, then generate & show it (cached)."""
    vs = VectorStore(_NS)
    try:
        has = vs.count(document_id)
    finally:
        vs.close()
    if not has:
        st.warning("This document has no indexed content. Re-index it first.")
        return

    smeta = _summary_meta()
    choices = list(smeta.keys())
    default_idx = choices.index("brief") if "brief" in choices else 0
    kind = st.selectbox(
        "Summary type",
        choices,
        format_func=lambda k: smeta[k][0],
        index=default_idx,
        key=f"sum_kind_{document_id}",
    )

    st.session_state.setdefault(f"doc_summary_done_{document_id}", {})
    done = st.session_state[f"doc_summary_done_{document_id}"]

    # Auto-generate the currently-selected kind if it has never been produced
    # for this doc (so clicking "Summary" shows content immediately).
    if kind not in done:
        with st.spinner(f"Generating {smeta[kind][0]}…"):
            try:
                from app.rag.summarize import summarize_document
                summary = summarize_document(document_id, kind=kind, force=False)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not generate summary: {exc}")
                return
        st.session_state[_summary_cache_key(document_id, kind)] = summary
        done[kind] = True
        st.session_state[f"doc_summary_done_{document_id}"] = done

    summary = st.session_state[_summary_cache_key(document_id, kind)]

    if summary:
        st.markdown(smeta[kind][1])
        st.markdown(summary["summary"])
        st.caption(
            f"Based on {summary['chunks_used']} chunks · pages {summary.get('pages')} "
            + ("· cached" if summary.get("cached") else "· regenerated")
        )

    # Regenerate button (only re-runs the LLM for the current kind).
    if st.button("🔄 Regenerate", key=f"reg_sum_{document_id}", use_container_width=True):
        with st.spinner("Regenerating…"):
            try:
                from app.rag.summarize import summarize_document
                summary = summarize_document(document_id, kind=kind, force=True)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not regenerate summary: {exc}")
        st.session_state[_summary_cache_key(document_id, kind)] = summary
        st.markdown(summary["summary"])
        st.session_state[f"doc_summary_done_{document_id}"][kind] = True

    if st.button("✖ Close summary", key=f"close_sum_{document_id}", use_container_width=True):
        if open_key:
            st.session_state.pop(open_key, None)
        st.rerun()


banner(
    "📁 Projects & Files",
    "Create and manage projects, upload papers, and keep track of every document.",
)

# ------------------------------------------------------------------ create form
st.markdown("### ＋ Create a project")
with st.form("new_project", clear_on_submit=True):
    c1, c2 = st.columns([3, 2])
    pname = c1.text_input("Project name")
    pdesc = c2.text_input("Description (optional)")
    submitted = st.form_submit_button("Create project", use_container_width=True)
    if submitted and pname.strip():
        meta = _open_meta()
        try:
            proj = meta.create_project(pname.strip(), pdesc.strip())
            st.success(f"Created project **{proj['name']}**")
        finally:
            meta.close()
        st.rerun()

st.divider()

# ------------------------------------------------------------------ project grid
st.markdown("### 🗂 Your projects")

# tag filter
meta_tags = _open_meta()
try:
    all_tags = meta_tags.list_tags()
finally:
    meta_tags.close()
if all_tags:
    with st.expander("🏷 Filter documents by tag", expanded=False):
        chosen_tag = st.selectbox(
            "Tag", ["(all)"] + all_tags,
            key="tag_filter",
        )
        if chosen_tag != "(all)":
            mf = _open_meta()
            try:
                match_docs = mf.list_documents_by_tag(chosen_tag)
            finally:
                mf.close()
            st.caption(f"{len(match_docs)} document(s) tagged **{chosen_tag}**")
            for md in match_docs:
                st.markdown(f"- `{md['filename']}` ({md['project_id'][:8]}…)")

_render_project_cards()

render_last_errors()
