/* Library — project cards, document table, search/tag/status filters.
   Read phase (P4): uploads, reindex and deletions arrive in P5. */
import { api } from "../api.js";
import { store } from "../store.js";
import { el, icon, rel, toast } from "../ui.js";
import { go } from "../router.js";
import { queueAdd, queueCard, tagsModal, uploadModal } from "./ingest.js";

const STATUS_META = {
  indexed: { chip: "st-ok", label: "Indexed" },
  error: { chip: "st-err", label: "Error" },
  pending: { chip: "st-warn", label: "Pending" },
  parsing: { chip: "st-info", label: "Parsing" },
  chunking: { chip: "st-info", label: "Chunking" },
  embedding: { chip: "st-info", label: "Embedding" },
  indexing: { chip: "st-info", label: "Indexing" },
  ocr: { chip: "st-info", label: "OCR" },
};

const parseJson = (s) => {
  try { return JSON.parse(s) || []; } catch { return []; }
};

/* ---------- project modal ---------- */
function newProjectModal(onCreated) {
  const ov = el("div", { class: "modalov open" });
  const nameIn = el("input", { type: "text", placeholder: "e.g. Thesis — Chapter 2", maxlength: "80" });
  const descIn = el("input", { type: "text", placeholder: "optional description", maxlength: "160" });
  const modal = el("div", { class: "modal" },
    el("h3", {}, "New project"),
    el("div", { class: "field" }, el("label", {}, "Name"), nameIn),
    el("div", { class: "field" }, el("label", {}, "Description"), descIn),
    el("div", { class: "mrow" },
      el("button", { class: "btn", onclick: () => ov.remove() }, "Cancel"),
      el("button", {
        class: "btn primary",
        onclick: async () => {
          const name = nameIn.value.trim();
          if (!name) { nameIn.focus(); return; }
          try {
            await api.post(`/projects?name=${encodeURIComponent(name)}&description=${encodeURIComponent(descIn.value.trim())}`);
            toast(`Project “${name}” created`, "ok");
            ov.remove();
            onCreated();
          } catch (e) { toast(e.message, "err"); }
        },
      }, "Create"),
    ),
  );
  ov.append(modal);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  document.body.append(ov);
  nameIn.focus();
  nameIn.addEventListener("keydown", (e) => e.key === "Enter" && modal.querySelector(".btn.primary").click());
  document.addEventListener("keydown", function esc(e) {
    if (e.key === "Escape") { ov.remove(); document.removeEventListener("keydown", esc); }
  });
}

/* ---------- document table ---------- */
function docTable(docs, { search, onSearch, statusFilter, onStatus, tags, activeTags, onTag, projectMode, onAction }) {
  const q = search.trim().toLowerCase();
  const filtered = docs.filter((d) => {
    if (statusFilter !== "all" && (d.status || "pending") !== statusFilter) return false;
    if (activeTags.size) {
      const dt = parseJson(d.tags).map(x => String(x).toLowerCase());
      if (![...activeTags].some((t) => dt.includes(t.toLowerCase()))) return false;
    }
    if (!q) return true;
    const hay = [d.filename, d.title, d.journal, d.doi, parseJson(d.authors).join(" "), parseJson(d.tags).join(" ")]
      .filter(Boolean).join(" ").toLowerCase();
    return hay.includes(q);
  });

  const toolbar = el("div", { class: "ptoolbar" },
    el("div", { class: "searchbox" },
      icon("search"),
      el("input", { type: "text", placeholder: "Search title, author, tag…", value: search,
        oninput: (e) => onSearch(e.target.value) })),
    el("div", { class: "seg" },
      ...["all", "indexed", "pending", "error"].map((s) =>
        el("button", { class: s === statusFilter ? "on" : "", onclick: () => onStatus(s) },
          s === "all" ? "All" : STATUS_META[s].label))),
    ...tags.slice(0, 14).map((t) =>
      el("button", { class: `chip ${activeTags.has(t.tag) ? "on" : ""}`, onclick: () => onTag(t.tag) },
        "#", t.tag)),
    el("span", { class: "countchip", style: "margin-left:auto" },
      `${filtered.length} of ${docs.length} shown`),
  );

  const chip = (d) => {
    const m = STATUS_META[d.status || "pending"] || STATUS_META.pending;
    return el("span", { class: `stchip ${m.chip}` }, m.label);
  };

  const rows = filtered.map((d) => {
    const authors = parseJson(d.authors);
    const who = authors.length ? authors.slice(0, 2).join(", ") + (authors.length > 2 ? " et al." : "") : "";
    const sub = [who, d.journal || "", d.year ? String(d.year) : ""].filter(Boolean).join(" · ");
    const dTags = parseJson(d.tags);
    return el("tr", {},
      el("td", {},
        el("div", { class: "dtitle", title: d.filename }, d.title || d.filename),
        el("div", { class: "dsub" }, sub || d.filename)),
      el("td", {}, chip(d)),
      el("td", { class: "countchip" }, el("span", { class: "pageno" }, d.page_count ? String(d.page_count) : "—")),
      el("td", {},
        d.title
          ? el("span", { class: `stchip ${d.citation_verified ? "st-ok" : "st-info"}` },
              d.citation_verified ? "verified" : "auto", " · ", (d.citation_style || "apa").toUpperCase())
          : el("span", { class: "countchip" }, "—")),
      el("td", {}, dTags.length
        ? dTags.slice(0, 2).map((t) => el("span", { class: "tagmini" }, t)).concat(
            dTags.length > 2 ? el("span", { class: "tagmini more" }, `+${dTags.length - 2}`) : [])
        : el("span", { class: "countchip" }, "—")),
      el("td", { class: "countchip" }, rel(d.created_at)),
      el("td", {},
        el("span", { class: "ibtn", title: "Edit tags", onclick: () => onAction(d, "tags") }, icon("edit")),
        el("span", { class: "ibtn", title: "Re-index", onclick: () => onAction(d, "reindex") }, icon("refresh")),
        el("span", { class: "ibtn", title: "Delete document", style: "color:var(--err)",
          onclick: () => onAction(d, "delete") }, icon("trash")),
        el("span", { class: "ibtn", title: "Chat with sources (arrives in P6)",
          onclick: () => go("chat") }, icon("chat"))),
    );
  });

  const tableCard = el("div", { class: "card" },
    el("div", { class: "tblwrap" },
      el("table", { class: "tbl" },
        el("thead", {}, el("tr", {},
          el("th", {}, "Document"), el("th", {}, "Status"), el("th", {}, "Pages"),
          el("th", {}, "Citation"), el("th", {}, "Tags"), el("th", {}, "Added"), el("th", {}, ""))),
        el("tbody", {}, rows.length ? rows
          : el("tr", {}, el("td", { colspan: "7", style: "text-align:center;padding:34px;color:var(--dim)" },
              docs.length ? "No documents match the current filters." : "No documents yet — upload arrives with the next phase."))))));

  return [toolbar, tableCard];
}

/* ---------- project cards ---------- */
function projectCards(projects, selectedId, onSelect) {
  if (!projects.length) return el("div");
  return el("div", { class: "pgrid" },
    ...projects.map((p) => el("div", {
      class: `card pcard ${(selectedId === p.id) ? "on" : ""}`,
      onclick: () => onSelect(p.id),
    },
      el("span", { class: "pcbtn" }, el("span", { class: "ibtn", title: "Open in Library" }, icon("book"))),
      el("div", { class: "pname" }, p.name || "(unnamed)"),
      el("div", { class: "pdesc" }, p.description || "No description."),
      el("div", { class: "pmeta" },
        el("span", { class: "chip static" }, icon("book"), `${p.doc_count ?? 0} docs`),
        p.chat_model ? el("span", { class: "chip static" }, icon("gear"), p.chat_model) : el("span"),
        p.chat_temperature !== null && p.chat_temperature !== undefined
          ? el("span", { class: "chip static" }, `t ${p.chat_temperature}`) : el("span")))));
}

/* ---------- main render ---------- */
export async function render(root) {
  const projects = store.get("projects");
  if (!projects || !projects.length) {
    root.append(el("div", { class: "vhead" },
      el("h2", {}, "Library"), el("span", { class: "vhsub" }, "projects · documents · tags")));
    root.append(el("div", { class: "card empty" },
      el("div", { class: "eic" }, icon("book")),
      el("h3", {}, "Your library is empty"),
      el("p", {}, "Create a project to start collecting papers. Uploads, indexing and the drop zone land in the next phase."),
      el("button", {
        class: "btn primary",
        onclick: () => newProjectModal(() => refresh()),
      }, icon("plus"), "New project"),
    ));
    return;
  }

  let search = "";
  let statusFilter = "all";
  const activeTags = new Set();

  root.append(el("div", { class: "vhead" },
    el("h2", {}, "Library"),
    el("span", { class: "vhsub" }, projectLabel()),
    el("div", { class: "right" },
      store.get("libProject")
        ? el("button", { class: "btn sm", onclick: () => { store.set("libProject", null); go("library"); } },
            icon("home"), "All documents")
        : el("span"),
      el("button", {
        class: "btn sm",
        onclick: () => uploadModal({
          projects: projects || [],
          defaultProject: store.get("libProject") || (projects && projects[0] && projects[0].id),
        }),
      }, icon("up"), "Upload"),
      el("button", { class: "btn sm", onclick: () => newProjectModal(() => refresh()) }, icon("plus"), "New project"))));

  const body = el("div");
  root.append(body);

  function projectLabel() {
    const sel = store.get("libProject");
    const p = sel ? (store.get("projects") || []).find((x) => x.id === sel) : null;
    return p ? p.name : "all projects";
  }

  const allDocs = await api.get(store.get("libProject")
    ? `/documents?project_id=${encodeURIComponent(store.get("libProject"))}`
    : "/documents").catch(() => null);
  const tags = await api.get("/tags").catch(() => []);
  if (allDocs === null) { body.append(el("div", { class: "banner" }, icon("alert"), el("b", {}, "Couldn't load documents."))); return; }

  function draw() {
    body.textContent = "";
    const qc = queueCard();
    if (qc) body.append(qc);
    const sel = store.get("libProject");
    if (!sel) {
      body.append(projectCards(projects || [], null, (id) => {
        store.set("libProject", id);
        rerenderProject();
      }));
    }
    const [toolbar, table] = docTable(allDocs, {
      search, onSearch: (v) => { search = v; draw(); },
      statusFilter, onStatus: (s) => { statusFilter = s; draw(); },
      tags: tags || [], activeTags, onTag: (t) => {
        activeTags.has(t) ? activeTags.delete(t) : activeTags.add(t);
        draw();
      },
      projectMode: !!sel,
      onAction: handleAction,
    });
    body.append(toolbar, table);
  }
  draw();

  async function handleAction(d, act) {
    if (act === "tags") {
      tagsModal(d, () => go("library"));
    } else if (act === "reindex") {
      try {
        const r = await api.post(`/documents/${encodeURIComponent(d.id)}/ingest-async?ocr_mode=auto`);
        queueAdd([{ job_id: r.job_id, doc_id: d.id, filename: d.filename, mode: "auto" }]);
        toast(`Re-index queued — ${d.filename}`, "ok");
      } catch (e) { toast(e.message, "err"); }
    } else if (act === "delete") {
      if (!confirm(`Delete “${d.filename}”?\n\nIts chunks, stored file and summaries are removed.`)) return;
      try { await api.del(`/documents/${encodeURIComponent(d.id)}`); toast("Document deleted", "ok"); go("library"); }
      catch (e) { toast(e.message, "err"); }
    }
  }

  /* entering a project re-fetches scoped docs; leaving shows all */
  function rerenderProject() { go("library"); }
}

async function refresh() {
  try { store.set("projects", await api.get("/projects")); } catch { /* offline */ }
  go("library");
}

/* ---------- context panel ---------- */
export function renderCtx(ctxP) {
  const projects = store.get("projects") || [];
  const sel = store.get("libProject");
  const panel = el("div", { class: "ctxpanel", "data-view": "library" });
  panel.append(
    el("div", { class: "chead" }, "Collections"),
    el("div", {
      class: `ctxrow ${!sel ? "on" : ""}`,
      onclick: () => { store.set("libProject", null); go("library"); },
    }, el("span", { class: "ic mut" }, "∑"), "All documents",
      el("span", { class: "n" }, String(projects.reduce((a, p) => a + (p.doc_count || 0), 0)))),
  );
  projects.forEach((p) => panel.append(el("div", {
    class: `ctxrow ${sel === p.id ? "on" : ""}`,
    onclick: () => { store.set("libProject", p.id); go("library"); },
  },
    el("span", { class: p.chat_model ? "ic" : "ic mut" }, (p.name || "–").slice(0, 2).toUpperCase()),
    el("span", { style: "overflow:hidden;text-overflow:ellipsis" }, p.name || "(unnamed)"),
    el("span", { class: "n" }, String(p.doc_count ?? 0)))));

  panel.append(el("div", { class: "chead", style: "margin-top:20px" }, "Status"));
  panel.append(
    kvRow("Selected", sel ? "1 project" : "All", true),
    kvRow("Model overrides", `${projects.filter((p) => p.chat_model).length} projects`, projects.some((p) => p.chat_model)),
  );
  ctxP.append(panel);
}

function kvRow(k, v, ok) {
  return el("div", { class: "kv" },
    el("span", { class: "k" }, k),
    el("span", { class: "v", style: "display:flex;align-items:center;gap:7px;min-width:0" },
      el("span", { class: ok ? "dot" : "dot warn" }), el("span", {}, v)));
}
