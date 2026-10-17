/* Citations (P7): bibliography export, verification workflow, resolve tool.
   Everything runs on the P1/P7 REST surface — no new backend needed. */
import { api } from "../api.js";
import { store } from "../store.js";
import { el, icon, toast } from "../ui.js";
import { go } from "../router.js";

const FIELDS = [
  ["title", "Title", "text"],
  ["authors", "Authors (comma-separated)", "text"],
  ["year", "Year", "text"],
  ["journal", "Journal", "text"],
  ["doi", "DOI", "text"],
  ["url", "URL", "text"],
];

const state = { style: "apa" };

const parseList = (s) => { try { return JSON.parse(s) || []; } catch { return []; } };

/* ---------- edit / verify modal (with Crossref suggestions) ---------- */
function citationModal(doc, autoData, onSaved) {
  const from = (autoData && autoData.auto && autoData.auto[0]) || null;
  const ov = el("div", { class: "modalov open" });
  const modal = el("div", { class: "modal citedit", style: "width:520px" });
  const head = el("h3", {}, `Citation — ${doc.filename}`);

  const fromAuthors = from && from.authors ? from.authors.join(", ") : "";
  const inv = {};
  for (const [key] of FIELDS) {
    if (key === "authors") {
      inv.authors = el("input", { type: "text",
        value: doc.authors ? parseList(doc.authors).join(", ") : fromAuthors });
    } else {
      const initial = (doc[key] && String(doc[key])) ||
        (from && from[key] != null && String(from[key])) || "";
      inv[key] = el("input", { type: "text", value: initial });
    }
  }

  /* crossref candidates strip */
  let candidates = (autoData && autoData.auto) || [];
  const candWrap = el("div");
  const drawCands = () => {
    candWrap.textContent = "";
    if (!candidates.length) return;
    candWrap.append(el("div", { class: "chead" }, "Crossref suggestions"));
    candidates.slice(0, 4).forEach((c, i) => {
      const conf = c.confidence !== undefined ? ` · ${(Math.round(c.confidence * 100))}% match` : "";
      const row = el("div", { class: `cand ${i === 0 ? "on" : ""}`, onclick: () => {
        candWrap.querySelectorAll(".cand").forEach((n) => n.classList.remove("on"));
        row.classList.add("on");
        for (const [k] of FIELDS) {
          if (k === "authors") inv.authors.value = (c.authors || []).join(", ");
          else inv[k].value = (c[k] || "") === 0 ? "" : String(c[k] ?? "");
        }
      } },
        el("div", { class: "cdt" }, el("span", {}, c.title || "(untitled)"), el("span", { class: "sp" }, conf)),
        el("div", { class: "cdm" }, [c.journal, c.year, c.doi].filter(Boolean).join(" · "))));
      candWrap.append(row);
    });
  };
  drawCands();

  const autoBtn = el("button", { class: "btn sm", onclick: async () => {
    autoBtn.disabled = true;
    toast("Querying Crossref…", "info");
    try {
      const res = await api.get(`/documents/${encodeURIComponent(doc.id)}/citations/auto`);
      candidates = res.auto || [];
      drawCands();
      toast(candidates.length && candidates[0].confidence > 0 ? `${candidates.length} suggestions` : "No strong matches", "ok");
    } catch (e) { toast(e.message, "err"); }
    autoBtn.disabled = false;
  } }, icon("search"), "Auto (Crossref)");

  const grid = el("div",
    ...[[inv.title, "Title"], [inv.authors, "Authors (comma-separated)"],
      [inv.year, "Year"], [inv.journal, "Journal"], [inv.doi, "DOI"], [inv.url, "URL"]]
      .map(([inp, label]) => el("div", { class: "field" }, el("label", {}, label), inp)));

  modal.append(head,
    el("div", { style: "display:flex;gap:10px;margin-bottom:12px" },
      el("span", { class: `stchip ${doc.citation_verified ? "st-ok" : "st-info"}` },
        doc.citation_verified ? "verified" : doc.title ? "auto metadata" : "no metadata"),
      el("span", { style: "margin-left:auto" }, autoBtn)),
    !candidates.length ? el("span") : candWrap,
    grid,
    el("div", { class: "mrow" },
      el("button", { class: "btn", onclick: () => ov.remove() }, "Cancel"),
      el("button", {
        class: "btn primary",
        onclick: async () => {
          const fields = {};
          const authors = inv.authors.value.split(",").map((x) => x.trim()).filter(Boolean);
          if (authors.length) fields.authors = authors;
          for (const [k] of FIELDS) {
            if (k === "authors") continue;
            const v = inv[k].value.trim();
            if (!v) continue;
            fields[k] = k === "year" ? (parseInt(v, 10) || null) : v;
          }
          try {
            await api.post(`/documents/${encodeURIComponent(doc.id)}/citations/confirm`, { metadata: fields });
            toast("Citation saved as verified", "ok");
            ov.remove();
            onSaved && onSaved();
          } catch (e) { toast(e.message, "err"); }
        } }, "Save as verified")));
  ov.append(modal);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  document.body.append(ov);
}

/* ---------- document row ---------- */
function citRow(doc, onAction) {
  const authors = parseList(doc.authors);
  const hasMeta = !!doc.title;
  return el("div", { class: "citrow" },
    el("div", { class: "cmain" },
      el("div", { class: "ctitle" }, doc.title || doc.filename),
      el("div", { class: "cmeta" },
        [authors.slice(0, 3).join(", ") + (authors.length > 3 ? " et al." : ""),
          doc.journal, doc.year, doc.doi].filter(Boolean).join(" · ") || "no citation metadata")),
    el("div", { class: "cacts" },
      el("span", { class: hasMeta ? (doc.citation_verified ? "stchip st-ok" : "stchip st-info")
        : "stchip st-warn" }, hasMeta ? (doc.citation_verified ? "verified" : "auto") : "missing"),
      el("span", { class: "ibtn", title: "Edit & verify", onclick: () => onAction(doc, "edit") }, icon("edit")),
      doc.doi ? el("span", { class: "ibtn", title: "Open DOI", onclick: () => window.open(`https://doi.org/${doc.doi}`) }, icon("up")) : el("span")));
}

export async function render(root) {
  root.append(el("div", { class: "vhead" },
    el("h2", {}, "Citations"),
    el("span", { class: "vhsub" }, "verify metadata · export bibliographies"),
    el("div", { class: "right" },
      el("button", { class: "btn sm", onclick: () => go("chat") }, icon("chat"), "From chat"),
      el("button", { class: "btn sm" , onclick: () => go("library") }, icon("book"), "Library"))));

  const seg = el("div", { class: "seg" },
    el("button", { class: state.style === "apa" ? "on" : "", onclick: () => { state.style = "apa"; go("citations"); } }, "APA 7"),
    el("button", { class: state.style === "vancouver" ? "on" : "", onclick: () => { state.style = "vancouver"; go("citations"); } }, "Vancouver"));
  root.append(el("div", { class: "chatopts", style: "margin-bottom:13px" },
    el("span", { class: "countchip" }, "style"), seg,
    el("span", { class: "countchip" },
      store.get("libProject") ? "project scope" : "all projects")));

  const docs = await api.get("/documents").catch(() => null);
  if (!docs) { root.append(el("div", { class: "banner" }, icon("alert"), el("b", {}, "Couldn't load documents."))); return; }
  const withMeta = docs.filter((d) => d.title);
  const missing = docs.filter((d) => !d.title);
  const verified = withMeta.filter((d) => d.citation_verified);

  root.append(el("div", { class: "stats", style: "grid-template-columns:repeat(3,1fr)" },
    el("div", { class: "card stat" }, el("div", { class: "num" }, String(verified.length)), el("div", { class: "lbl" }, "verified references")),
    el("div", { class: "card stat" }, el("div", { class: "num" }, String(withMeta.length - verified.length)), el("div", { class: "lbl" }, "auto metadata · unverified")),
    el("div", { class: "card stat" }, el("div", { class: "num" }, String(missing.length)), el("div", { class: "lbl" }, "no metadata yet"))));
  store.set("citCounts", { verified: verified.length, auto: withMeta.length - verified.length, missing: missing.length });

  /* --- bibliography export card --- */
  const pid = store.get("libProject");
  const exp = await api.get(`/citations/export?style=${state.style}${pid ? `&project_id=${encodeURIComponent(pid)}` : ""}`)
    .catch(() => null);
  const bibCard = el("div", { class: "card cardpad mt" },
    el("div", { class: "ct" },
      el("h3", {}, `Bibliography — ${state.style === "apa" ? "APA 7" : "Vancouver"}`),
      el("span", { class: "hint" }, exp ? `${exp.count} entries` : "…"),
      el("div", { style: "display:flex;gap:8px" },
        el("button", { class: "btn sm", onclick: () => {
          if (!exp || !exp.text) return;
          navigator.clipboard.writeText(exp.text).then(() => toast("Copied to clipboard", "ok"));
        } }, icon("copy"), "Copy"),
        el("button", { class: "btn sm", onclick: () => {
          if (!exp) return;
          const blob = new Blob([exp.text || ""], { type: "text/plain" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = `commentarium-references-${state.style}.txt`;
          a.click();
          URL.revokeObjectURL(a.href);
        } }, icon("down"), "Download .txt"))));
  if (!exp || !exp.references || !exp.references.length) {
    bibCard.append(el("div", { class: "muted", style: "padding:8px 2px" },
      "Verified documents (with titles) appear here. Check a paper in the list below to add it."));
  } else {
    bibCard.append(el("div", { class: "bibwrap bib" }, exp.text));
  }
  root.append(bibCard);

  /* --- verification list --- */
  const listCard = el("div", { class: "card cardpad mt" },
    el("div", { class: "ct" }, el("h3", {}, "Reference desk"),
      el("span", { class: "hint" }, "titles feed the bibliography")));
  if (!docs.length) listCard.append(el("div", { class: "muted", style: "padding:8px" }, "Upload papers in the Library first."));
  else {
    for (const d of verified) listCard.append(citRow(d, rowAction));
    for (const d of withMeta.filter((x) => !x.citation_verified)) listCard.append(citRow(d, rowAction));
    for (const d of missing) listCard.append(citRow(d, rowAction));
  }
  root.append(listCard);

  /* --- resolve tool --- */
  const ta = el("textarea", { class: "tf", placeholder:
    "Paste text containing raw [SRC:document_id|p12] markers to resolve them in the current style…" });
  const outBox = el("div", { class: "resolveout", style: "display:none" });
  const resCard = el("div", { class: "card cardpad mt" },
    el("div", { class: "ct" }, el("h3", {}, "Raw citation resolver"),
      el("span", { class: "hint" }, "debug / old transcripts")),
    el("div", { class: "field" }, ta),
    el("div", { style: "display:flex;gap:9px" },
      el("button", { class: "btn primary sm", onclick: async () => {
        try {
          const r = await api.post("/citations/resolve", { answer_text: ta.value, sources: [], style: state.style });
          outBox.style.display = "";
          const refs = (r.references || []).map((x) => `${x.key}. ${x.text}`).join("\n");
          outBox.textContent = (r.answer || "(no SRC markers found — nothing replaced)")
            + (refs ? `\n\nreferences\n${refs}` : "");
        } catch (e) { toast(e.message, "err"); }
      } }, icon("refresh"), "Resolve"),
      el("button", { class: "btn sm", onclick: () => { ta.value = ""; outBox.style.display = "none"; } }, "Clear")));
  root.append(resCard);

  async function rowAction(d, act) {
    if (act === "edit") {
      try {
        const auto = await api.get(`/documents/${encodeURIComponent(d.id)}/citations/auto`).catch(() => null);
        citationModal(d, auto, () => go("citations"));
      } catch (e) { toast(e.message, "err"); citationModal(d, null, () => go("citations")); }
    }
  }
}

/* ---------- context panel ---------- */
export function renderCtx(ctxP) {
  const c = store.get("citCounts");
  const panel = el("div", { class: "ctxpanel", "data-view": "citations" });
  panel.append(el("div", { class: "chead" }, "Reference desk"));
  panel.append(
    kvRow("Style", state.style === "apa" ? "APA 7" : "Vancouver", true),
    kvRow("Verified", c ? String(c.verified) : "…", !!c && c.verified > 0),
    kvRow("Auto · unverified", c ? String(c.auto) : "…", !!c && c.auto === 0),
    kvRow("No metadata", c ? String(c.missing) : "…", !!c && c.missing === 0),
  );
  panel.append(el("div", { class: "chead", style: "margin-top:18px" }, "Scope"));
  panel.append(el("div", { class: "kv" },
    el("span", { class: "k" }, "Project"),
    el("span", { class: "v" }, store.get("libProject")
      ? (((store.get("projects") || []).find((p) => p.id === store.get("libProject")) || {}).name || "project")
      : "all projects")));
  ctxP.append(panel);
}

function kvRow(k, v, ok) {
  return el("div", { class: "kv" },
    el("span", { class: "k" }, k),
    el("span", { class: "v", style: "display:flex;align-items:center;gap:7px;min-width:0" },
      el("span", { class: ok ? "dot" : "dot warn" }), el("span", {}, v)));
}
