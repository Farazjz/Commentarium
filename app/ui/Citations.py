"""Citations page – verify document metadata and control citation formatting.

Workflow: pick a document -> auto-extract metadata (Crossref via DOI/title) ->
review/edit fields -> verify & save -> see the rendered APA 7 / Vancouver
reference + the default style selector (per-document + global).
"""
from __future__ import annotations

import json
import logging

import streamlit as st

from app.citations.formatters import format_reference, in_text_label
from app.citations.service import auto_metadata, confirm_metadata
from app.config import get_settings
from app.db.metadata import MetadataStore
from app.ui.helpers import banner, render_last_errors

logger = logging.getLogger("app")

banner("📝 Citations", "Verify document metadata and control citation formatting for your thesis.")

# ------------------------------------------------------------------ project nav
meta = MetadataStore()
try:
    projects = {p["id"]: p["name"] for p in meta.list_projects()}
    all_docs = {d["id"]: d for d in meta.list_documents()}
finally:
    meta.close()

if not projects or not all_docs:
    st.info("No documents yet. Upload and index files in the **Projects & Files** page first.")
    render_last_errors()
    st.stop()

sel_doc = st.selectbox(
    "Document",
    options=list(all_docs.keys()),
    format_func=lambda x: f"{all_docs[x]['filename']} ({all_docs[x]['id'][:8]}…)",
)

doc = all_docs[sel_doc]
style_cfg = get_settings()

# ------------------------------------------------------------------ current state
st.markdown("### Current metadata")
cur = {
    "Title": doc.get("title") or "—",
    "Authors": doc.get("authors") or "—",
    "Year": doc.get("year") or "—",
    "Journal": doc.get("journal") or "—",
    "DOI": doc.get("doi") or "—",
}
for k, v in cur.items():
    if k == "Authors" and isinstance(v, str):
        try:
            v = ", ".join(json.loads(v or "[]"))
        except json.JSONDecodeError:
            pass
    st.markdown(f"**{k}:** {v}")
verified = bool(doc.get("citation_verified"))
st.caption("✅ Verified (for citation)" if verified else "⏳ Not yet verified")

# ------------------------------------------------------------------ auto-extract
st.markdown("### 🔎 Auto-extract from Crossref")
if st.button("Look up metadata (DOI / title)", use_container_width=True):
    with st.spinner("Querying Crossref…"):
        try:
            res = auto_metadata(sel_doc)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Crossref lookup failed: {exc}")
            res = {}
    candidates = res.get("auto", [])
    if not candidates:
        st.warning("No metadata found. Enter DOI/title manually below, or add the DOI and retry.")
    else:
        st.session_state["cite_candidates"] = candidates
        st.session_state["cite_picked"] = 0
        st.success(f"Found {len(candidates)} candidate(s) from Crossref ({res.get('source')}).")

# ------------------------------------------------------------------ candidate picker
candidates = st.session_state.get("cite_candidates")
if candidates:
    labels = []
    for c in candidates:
        conf = c.get("confidence", 0)
        labels.append(
            f"{c.get('title','?')[:60]} — {', '.join((c.get('authors') or [])[:2])} "
            f"({c.get('year')}) [conf {conf:.2f}]"
        )
    pick = st.selectbox("Choose the correct match", range(len(labels)),
                        format_func=lambda i: labels[i],
                        key="cite_pick_sel")
    if st.button("Use this match", key="use_match"):
        st.session_state["cite_pick_data"] = candidates[pick]
        st.rerun()

# ------------------------------------------------------------------ manual edit form
st.markdown("### ✏️ Verify / edit metadata")
cand = st.session_state.get("cite_pick_data")

def prefill(field, default="", **kw):
    """Value for a form field: picked Crossref candidate first, then the
    document's currently-saved value, then the supplied default."""
    # preferred source:
    if cand is not None:
        v = cand.get(field)
        if v not in (None, ""):
            if isinstance(v, list):
                return ", ".join(str(x) for x in v)
            if field == "year" and isinstance(v, int):
                return str(v)
            return v
    # fall back to saved document metadata
    dval = doc.get(field)
    if field == "authors" and isinstance(dval, str):
        try:
            dval = json.loads(dval or "[]")
        except json.JSONDecodeError:
            dval = []
    if isinstance(dval, list):
        return ", ".join(str(x) for x in dval)
    if dval not in (None, ""):
        return str(dval)
    return default

# Clear any legacy session-state value left over from the older build where
# "cite_form" was (incorrectly) used as both a form key and a state key.
# If that stale value survives in the browser session, Streamlit refuses to
# create the form at all.
for _legacy in ("cite_form",):
    if _legacy in st.session_state:
        try:
            del st.session_state[_legacy]
        except Exception:  # noqa: BLE001
            pass

with st.form("cite_verify_form"):
    title = st.text_input("Title", value=prefill("title", doc.get("title") or ""))
    authors = st.text_input("Authors (comma-separated)", value=prefill("authors"))
    c1, c2, c3 = st.columns(3)
    year = c1.text_input("Year", value=prefill("year", doc.get("year") or ""))
    journal = c2.text_input("Journal", value=prefill("journal", doc.get("journal") or ""))
    doi = c3.text_input("DOI", value=prefill("doi", doc.get("doi") or ""))
    c4, c5, c6 = st.columns(3)
    volume = c4.text_input("Volume", value=prefill("volume"))
    issue = c5.text_input("Issue", value=prefill("issue"))
    pages = c6.text_input("Pages", value=prefill("pages"))

    saved = st.form_submit_button("✅ Verify & save citation metadata")

if saved:
    author_list = [a.strip() for a in authors.split(",") if a.strip()]
    y = None
    if year.strip().isdigit():
        y = int(year.strip())
    try:
        confirm_metadata(
            sel_doc,
            {
                "title": title.strip(),
                "authors": author_list,
                "year": y,
                "journal": journal.strip(),
                "doi": doi.strip(),
                "volume": volume.strip(),
                "issue": issue.strip(),
                "pages": pages.strip(),
            },
        )
        st.success("Citation metadata saved & verified.")
        st.session_state.pop("cite_pick_data", None)
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Save failed: {exc}")

st.divider()

# ------------------------------------------------------------------ style + preview
st.markdown("### 🎨 Citation style & preview")
style_map = {"apa": "APA 7", "vancouver": "Vancouver (NLM)"}
default_style = style_cfg.citation_style
style = st.radio(
    "Formatting style",
    options=["apa", "vancouver"],
    format_func=lambda s: style_map[s],
    horizontal=True,
    index=0 if default_style == "apa" else 1,
    key="cite_style_radio",
)

# per-document style override control
doc_style = (doc.get("citation_style") or "").strip()
if doc_style and doc_style != default_style:
    st.caption(f"⚠️ This document has its own override: **{style_map.get(doc_style, doc_style)}**")

meta = MetadataStore()
try:
    fresh = meta.get_document(sel_doc)
finally:
    meta.close()

if fresh and fresh.get("citation_verified") and (fresh.get("title") or fresh.get("authors")):
    st.markdown("**In-text marker:** " + in_text_label(fresh, style))
    st.markdown("**Full reference:**")
    st.code(format_reference(fresh, style), language=None)
else:
    st.caption("Save verified metadata above to preview the formatted reference.")

render_last_errors()
