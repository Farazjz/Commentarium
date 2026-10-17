/* OCR to Markdown (P9): upload PDFs/images, run backend jobs with per-page
   progress, manage saved .md outputs with Rendered/Raw preview. */
import { api } from "../api.js";
import { store } from "../store.js";
import { el, icon, rel, fmtBytes, toast } from "../ui.js";
import { go } from "../router.js";

const BACKENDS = ["tesseract", "vision", "paddleocr-vl", "teleocr"];

function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

function mdToHtml(md) {
  const lines = String(md).split("\n");
  let inCode = false, out = "";
  for (const ln of lines) {
    if (ln.trim().startsWith("```")) { out += inCode ? "</code></pre>" : "<pre><code>"; inCode = !inCode; continue; }
    if (inCode) { out += esc(ln) + "\n"; continue; }
    const t = ln.trim();
    if (/^#{1,4}\s/.test(t)) out += `<h${Math.min((t.match(/^#+/) || ["#"])[0].length, 4)}>${esc(t.replace(/^#+\s*/, ""))}</h${Math.min((t.match(/^#+/) || ["#"])[0].length, 4)}>`;
    else if (t === "---" || t === "***") out += "<hr>";
    else if (/^\s*[-*]\s/.test(ln)) out += `<p style="padding-left:14px">• ${esc(t.replace(/^[-*]\s*/, ""))}</p>`;
    else out += t ? `<p>${esc(t)}</p>` : "";
  }
  if (inCode) out += "</code></pre>";
  return out;
}

/* ---------------- upload modal ---------------- */
function ocrUploadModal(queueAdd) {
  let files = [];
  const ov = el("div", { class: "modalov open" });
  const listEl = el("div", { style: "font-size:12px;color:var(--dim);min-height:18px" }, "No files selected");
  const drawList = () => {
    listEl.textContent = "";
    if (!files.length) { listEl.textContent = "No files selected"; return; }
    for (const f of files) listEl.append(el("div", { class: "kv", style: "padding:4px 0" },
      el("span", { class: "k", style: "width:auto;font-size:12px" }, f.name),
      el("span", { style: "margin-left:auto;font-size:10.5px;color:var(--faint)" }, fmtBytes(f.size))));
  };

  const drop = el("div", { class: "empty", style: "padding:16px;border:1.5px dashed var(--border2);border-radius:12px;margin-bottom:12px" },
    el("div", { class: "eic", style: "width:36px;height:36px;margin-bottom:8px" }, icon("scan")),
    el("p", { style: "margin:0 0 8px" }, "Drop scanned PDFs or images here"));
  const pickIn = el("input", { type: "file", multiple: "", accept: ".pdf,.png,.jpg,.jpeg,.webp,.bmp,.tiff", style: "display:none" });
  pickIn.addEventListener("change", () => { files.push(...pickIn.files); drawList(); });
  drop.append(el("button", { class: "btn sm", onclick: () => pickIn.click() }, "Browse"), pickIn);
  ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.style.borderColor = "var(--accent)"; }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.style.borderColor = "var(--border2)"; }));
  drop.addEventListener("drop", (e) => { files.push(...e.dataTransfer.files); drawList(); });

  const backendSel = el("select", { style: "width:auto;height:34px;padding:0 10px" },
    ...BACKENDS.map((b) => el("option", { value: b }, b)));
  const dpiSel = el("select", { style: "width:auto;height:34px;padding:0 10px" },
    ...[150, 200, 300].map((d) => el("option", { value: String(d), ...(d === 200 ? { selected: "" } : {}) }, `${d} DPI`)));
  let force = false;
  const forceSw = el("div", { class: `sw ${force ? "on" : ""}`, onclick: () => {
    force = !force; forceSw.classList.toggle("on", force);
  } });
  const modelIn = el("input", { type: "text", placeholder: "vision model (optional)", style: "height:32px;font-size:12.5px" });

  const modal = el("div", { class: "modal" },
    el("h3", {}, "OCR → Markdown"),
    drop, listEl,
    el("div", { class: "kv", style: "padding:8px 0" }, el("span", { class: "k", style: "width:auto" }, "Backend"), backendSel),
    el("div", { class: "kv", style: "padding:8px 0" }, el("span", { class: "k", style: "width:auto" }, "Rasterize DPI (scans)"), dpiSel),
    el("div", { class: "kv", style: "padding:8px 0" }, el("span", { class: "k", style: "width:auto" }, "Force OCR (ignore text layer)"), forceSw),
    el("div", { class: "field" }, el("label", {}, "Custom model (vision backend only)"), modelIn),
    el("div", { class: "mrow" },
      el("button", { class: "btn", onclick: () => ov.remove() }, "Cancel"),
      el("button", {
        class: "btn primary",
        onclick: async () => {
          if (!files.length) { toast("Pick at least one file", "info"); return; }
          ov.remove();
          const entries = [];
          for (const f of files) {
            const fd = new FormData();
            fd.append("file", f);
            fd.append("backend", backendSel.value);
            fd.append("dpi", dpiSel.value);
            fd.append("force_ocr", force ? "true" : "false");
            fd.append("model", modelIn.value.trim());
            try {
              const r = await api.upload("/ocr/extract", fd);
              entries.push({ job_id: r.job_id, filename: r.filename, progress: 0, status: r.status || "queued", message: "" });
            } catch (e) { toast(`${f.name}: ${e.message}`, "err"); }
          }
          if (entries.length) { queueAdd(entries); toast(`Queued ${entries.length} OCR job${entries.length > 1 ? "s" : ""}`, "ok"); }
        } }, icon("scan"), "Extract")));
  ov.append(modal);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  document.body.append(ov);
}

/* ---------------- outputs (right column) ---------------- */
let selectedOut = null;
let previewData = null;   // {name, content}

function outputsCard(files, { onSelect, onDelete, onDownload }) {
  const card = el("div", { class: "card" });
  card.append(el("div", { class: "ct", style: "padding:14px 14px 0;margin:0" },
    el("h3", {}, "Saved markdown"),
    el("span", { class: "hint" }, files.length ? `${files.length} files` : "")));
  if (!files.length) {
    card.append(el("div", { class: "muted", style: "padding:12px 14px 14px;font-size:12.5px" },
      "Extracted files land here — then go to the Library to index them into your corpus."));
    return card;
  }
  const list = el("div", { style: "max-height:240px;overflow-y:auto;padding:6px" });
  for (const f of files) {
    const row = el("div", {
      class: `ctxrow ${selectedOut === f.name ? "on" : ""}`,
      style: "padding:7px 9px",
      onclick: () => { selectedOut = f.name; onSelect(f.name); },
    },
      el("span", { class: "ic mut" }, icon("book")),
      el("span", { style: "min-width:0;overflow:hidden" },
        el("div", { style: "font-size:13px" }, f.name),
        el("div", { class: "countchip" }, `${fmtBytes(f.size_bytes)} · ${rel(f.modified)}`)),
      el("span", { class: "ibtn", title: "Delete", onclick: (e) => { e.stopPropagation(); onDelete(f.name); } }, icon("trash")),
      el("a", { class: "ibtn", title: "Download", href: `/ocr/output/${encodeURIComponent(f.name)}`, download: "", onclick: (e) => e.stopPropagation() }, icon("down")));
    list.append(row);
  }
  card.append(list);
  void onDownload;
  return card;
}

/* ---------------- main render ---------------- */
export async function render(root) {
  const active = store.get("ocrQueue") || [];

  root.append(el("div", { class: "vhead" },
    el("h2", {}, "OCR to Markdown"),
    el("span", { class: "vhsub" }, "scanned papers → clean editable markdown"),
    el("div", { class: "right" },
      el("button", { class: "btn primary", onclick: () => ocrUploadModal(queueAdd) },
        icon("scan"), "Extract from file"))));

  const grid = el("div", { class: "ocrgrid" });
  const left = el("div");
  const right = el("div");
  grid.append(left, right);
  root.append(grid);

  /* left: queue (when active) + config + guidance */
  if (active.length) {
    const qcard = el("div", { class: "card cardpad", style: "margin-bottom:14px" },
      el("div", { class: "ct" }, el("h3", {}, "OCR queue"),
        el("span", { class: "hint" }, `${active.filter((x) => x.status !== "failed").length} active`)));
    for (const t of active) {
      qcard.append(el("div", { class: "qrow" },
        el("span", { class: "qname" }, t.filename),
        el("span", { class: "qmsg" }, t.message || ""),
        t.progress != null
          ? el("div", { class: "vmeter", style: "width:120px" }, el("i", { style: `width:${Math.round(t.progress * 100)}%` }))
          : el("span"),
        el("span", { class: `stchip ${t.status === "completed" ? "st-ok" : t.status === "failed" ? "st-err" : "st-info"}` },
          t.status === "completed" ? "done" : t.status),
        t.progress != null ? el("span", { class: "qpct" }, `${Math.round(t.progress * 100)}%`) : el("span")));
    }
    left.append(qcard);
  }

  left.append(el("div", { class: "card cardpad", style: "margin-bottom:14px" },
    el("div", { class: "ct" }, el("h3", {}, "How it works")),
    ...[
      ["Smart by default", "Pages with a text layer are read directly; blank scanned pages are rasterized and OCR'd."],
      ["Force OCR", "Skips the text layer entirely — for mixed files where embedded text is garbage."],
      ["Backends", "tesseract (local), vision (your gateway VLM), paddleocr-vl and teleocr (local servers)."],
      ["Into the library", "Save the .md into a project folder and index it like any other document."],
    ].map(([k, v]) => el("div", { class: "kv" },
      el("span", { class: "k", style: "width:auto;font-weight:600;color:var(--text)" }, k),
      el("span", { class: "v", style: "color:var(--dim);font-weight:400" }, v)))));

  const cfg = store.get("summary") || {};
  left.append(el("div", { class: "card cardpad" },
    el("div", { class: "ct" }, el("h3", {}, "Server defaults"),
      el("button", { class: "btn sm", onclick: () => go("settings") }, icon("gear"), "Settings")),
    el("div", { class: "kv" }, el("span", { class: "k" }, "Backend"), el("span", { class: "v" }, cfg.ocr_backend || "—")),
    el("div", { class: "kv" }, el("span", { class: "k" }, "Output dir"), el("span", { class: "v" }, "data/ocr_output/"))));

  /* right: outputs + preview */
  const listing = await api.get("/ocr/output").catch(() => ({ files: [] }));
  const files = (listing && listing.files) || [];
  right.append(outputsCard(files, {
    onSelect: (name) => { loadPreview(name); go("ocr"); },
    onDelete: async (name) => {
      if (!confirm(`Delete ${name}?`)) return;
      try { await api.del(`/ocr/output/${encodeURIComponent(name)}`); toast("Deleted", "ok"); }
      catch (e) { toast(e.message, "err"); }
      if (selectedOut === name) { selectedOut = null; previewData = null; }
      go("ocr");
    },
  }));

  const prevCard = el("div", { class: "card cardpad mt" });
  let pmode = "rendered";
  const tabR = el("button", { class: "chip on", onclick: () => { pmode = "rendered"; syncTabs(); } }, "Rendered");
  const tabRaw = el("button", { class: "chip", onclick: () => { pmode = "raw"; syncTabs(); } }, "Raw markdown");
  const tabwrap = el("span", { class: "twotabs" }, tabR, tabRaw);
  function syncTabs() {
    tabR.classList.toggle("on", pmode === "rendered");
    tabRaw.classList.toggle("on", pmode === "raw");
    draw();
  }
  prevCard.append(el("div", { class: "ct" },
    el("h3", {}, "Preview"),
    tabwrap,
    el("span", { class: "chip static" }, selectedOut || "no file selected")));
  const box = el("div", { class: "preview" });
  prevCard.append(box);
  right.append(prevCard);

  function draw() {
    if (!previewData) { box.innerHTML = ""; box.append(el("span", { class: "muted" }, "Select a saved file above, or extract a new one — finished OCR lands here automatically.")); return; }
    if (pmode === "raw") { box.innerHTML = ""; box.append(el("pre", { style: "white-space:pre-wrap;font-size:11.5px;margin:0" }, previewData.content || "")); }
    else box.innerHTML = mdToHtml(previewData.content || "");
  }

  async function loadPreview(name) {
    if (!name) { previewData = null; draw(); return; }
    if (previewData?.name !== name) {
      try { previewData = await api.get(`/ocr/output/${encodeURIComponent(name)}/content`); }
      catch (e) { box.innerHTML = ""; box.append(el("span", { style: "color:var(--err)" }, e.message)); return; }
    }
    draw();
  }
  loadPreview(selectedOut);
}

/* ---------------- queue machinery ---------------- */
const TERMINAL = ["completed", "failed", "cancelled"];

function queueAdd(entries) {
  const q = store.get("ocrQueue") || [];
  q.push(...entries);
  store.set("ocrQueue", q);
  startPing();
}

function startPing() {
  if (store.get("ocrTimer")) return;
  const tick = async () => {
    const q = store.get("ocrQueue") || [];
    const active = q.filter((x) => !TERMINAL.includes(x.status));
    let done = 0;
    for (const item of q.filter((x) => !TERMINAL.includes(x.status))) {
      try {
        const j = await api.get(`/jobs/${item.job_id}`);
        if (j) {
          item.status = j.status;
          item.message = j.message || "";
          item.progress = j.progress;
          if (j.status === "completed") done++;
        }
      } catch { /* keep polling */ }
    }
    const remaining = q.filter((x) => !TERMINAL.includes(x.status));
    if (done) toast(`OCR finished for ${done} file${done > 1 ? "s" : ""}`, "ok");
    store.set("ocrQueue", remaining);
    if (!remaining.length) { store.set("ocrTimer", null); }
    else store.set("ocrTimer", setTimeout(tick, 2000));
    if (store.get("view") === "ocr") go("ocr");
  };
  store.set("ocrTimer", setTimeout(tick, 1200));
}

/* ---------------- context panel ---------------- */
export function renderCtx(ctxP) {
  const q = store.get("ocrQueue") || [];
  const panel = el("div", { class: "ctxpanel", "data-view": "ocr" });
  panel.append(el("div", { class: "chead" }, "OCR"));
  panel.append(
    kvRow("Queue", q.length ? `${q.filter((x) => !TERMINAL.includes(x.status)).length} active` : "idle", !q.length),
    kvRow("Default backend", (store.get("summary") || {}).ocr_backend || "—", true),
    kvRow("Selected", selectedOut || "—", !!selectedOut),
  );
  if (selectedOut) {
    panel.append(el("div", { class: "chead", style: "margin-top:16px" }, "File"));
    panel.append(el("div", { class: "kv" }, el("span", { class: "k" }, "Name"),
      el("span", { class: "v" }, selectedOut)),
      el("div", { class: "kv" }, el("span", { class: "k" }, "Actions"),
        el("span", { class: "v" }, "library → index it")));
  }
  ctxP.append(panel);
}

function kvRow(k, v, ok) {
  return el("div", { class: "kv" },
    el("span", { class: "k" }, k),
    el("span", { class: "v", style: "display:flex;align-items:center;gap:7px;min-width:0" },
      el("span", { class: ok ? "dot" : "dot warn" }), el("span", {}, v)));
}
