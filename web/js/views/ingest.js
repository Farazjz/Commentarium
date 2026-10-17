/* Async ingest queue (P5): uploads -> background jobs -> live status.

   The queue lives in the store so it survives view re-renders; the poller
   re-renders the Library every 2.5 s while jobs are active, which refetches
   /documents so document rows morph through parsing -> embedding -> indexed
   straight from the metadata DB (no fake progress). */
import { api } from "../api.js";
import { store } from "../store.js";
import { el, icon, toast } from "../ui.js";
import { go } from "../router.js";

const TERMINAL = ["completed", "failed", "cancelled"];
const MODE_LABELS = { auto: "auto", direct: "text only", ocr: "force OCR" };

export function queueAdd(entries) {
  const q = store.get("ingestQueue") || [];
  q.push(...entries);
  store.set("ingestQueue", q);
  startQueuePing();
}

function startQueuePing() {
  if (store.get("queueTimer")) return;
  const tick = async () => {
    const prev = store.get("ingestQueue") || [];
    const active = prev.filter((x) => !TERMINAL.includes(x.status));
    const doneCount = prev.length - active.length;
    store.set("ingestQueue", active);
    if (!active.length) {
      store.set("queueTimer", null);
      try { store.set("indexStats", await api.get("/index/stats")); } catch { /* offline */ }
      try { store.set("projects", await api.get("/projects")); } catch { /* offline */ }
      if (doneCount) toast(`Indexed ${doneCount} document${doneCount > 1 ? "s" : ""} — queue clear`, "ok");
      return;
    }
    for (const item of active) {
      try {
        const j = await api.get(`/jobs/${item.job_id}`);
        if (j && j.status !== item.status) {
          item.status = j.status;
          item.error = j.error;
          item.result = j.result;
        }
      } catch { /* transient — keep polling */ }
    }
    if (store.get("view") === "library") go("library"); // live statuses in the table
    store.set("queueTimer", setTimeout(tick, 2500));
  };
  store.set("queueTimer", setTimeout(tick, 1500));
}

/* ---------- upload modal ---------- */
export function uploadModal({ projects, defaultProject }) {
  if (!projects || !projects.length) {
    toast("Create a project before uploading", "info");
    return;
  }
  let files = [];
  const ov = el("div", { class: "modalov open" });

  const listEl = el("div", { class: "muted", style: "font-size:12.5px;min-height:20px" }, "No files selected");
  const drawList = () => {
    listEl.textContent = "";
    if (!files.length) { listEl.textContent = "No files selected"; return; }
    files.forEach((f, i) => listEl.append(el("div", { class: "kv", style: "padding:5px 0" },
      el("span", { class: "k", style: "width:auto;font-size:12px" }, `${f.name}`),
      el("span", { style: "margin-left:auto;font-size:11px;color:var(--faint);white-space:nowrap" },
        `${(f.size / 1024 / 1024).toFixed(2)} MB`),
      el("span", { class: "ibtn", onclick: () => { files.splice(i, 1); drawList(); } }, icon("x")))));
  };

  const drop = el("div", {
    class: "empty",
    style: "padding:18px;border:1.5px dashed var(--border2);border-radius:12px;margin-bottom:12px",
  },
    el("div", { class: "eic", style: "width:38px;height:38px;margin-bottom:8px" }, icon("up")),
    el("p", { style: "margin:0" }, "Drop PDF/DOCX here or"));
  const pick = el("input", { type: "file", multiple: "", accept: ".pdf,.docx", style: "display:none" });
  pick.addEventListener("change", () => { files.push(...pick.files); drawList(); });
  const pickBtn = el("button", { class: "btn sm", onclick: () => pick.click() }, "Browse");
  drop.append(pickBtn, pick);
  ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.style.borderColor = "var(--accent)";
  }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.style.borderColor = "var(--border2)";
  }));
  drop.addEventListener("drop", (e) => { files.push(...e.dataTransfer.files); drawList(); });

  const sel = el("select", {},
    ...projects.map((p) => el("option", { value: p.id, ...(p.id === defaultProject ? { selected: "" } : {}) },
      p.name || "(unnamed)")));

  let mode = "auto";
  const seg = el("div", { class: "seg" },
    ...["auto", "direct", "ocr"].map((m) =>
      el("button", { class: m === mode ? "on" : "", onclick: (e) => {
        mode = m;
        seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.textContent === MODE_LABELS[m]));
      } }, MODE_LABELS[m])));

  const modal = el("div", { class: "modal" },
    el("h3", {}, "Upload papers"),
    drop, listEl,
    el("div", { class: "field", style: "margin-top:12px" }, el("label", {}, "Project"), sel),
    el("div", { class: "field" }, el("label", {}, "Indexing mode"), seg),
    el("div", { class: "mrow" },
      el("button", { class: "btn", onclick: () => ov.remove() }, "Cancel"),
      el("button", {
        class: "btn primary",
        onclick: async () => {
          if (!files.length) { toast("Pick at least one file", "info"); return; }
          const pid = sel.value;
          const entries = [];
          ov.remove();
          for (const f of files) {
            const fd = new FormData();
            fd.append("file", f);
            fd.append("ocr_mode", mode);
            try {
              const r = await api.upload(`/projects/${encodeURIComponent(pid)}/ingest-async`, fd);
              entries.push({ job_id: r.job_id, doc_id: r.document.id,
                filename: r.document.filename || f.name, mode });
            } catch (e) { toast(`${f.name}: ${e.message}`, "err"); }
          }
          if (entries.length) {
            store.set("libProject", pid);
            queueAdd(entries);
            toast(`Queued ${entries.length} upload${entries.length > 1 ? "s" : ""}`, "ok");
          }
        },
      }, "Upload & index"),
    ),
  );
  modal.append(pick);
  ov.append(modal);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  document.body.append(ov);
}

/* ---------- tags modal ---------- */
export function tagsModal(doc, onSaved) {
  const tags = (() => { try { return JSON.parse(doc.tags) || []; } catch { return []; } })();
  const inp = el("input", { type: "text", value: tags.join(", "), placeholder: "e.g. caries, review" });
  const ov = el("div", { class: "modalov open" });
  const modal = el("div", { class: "modal" },
    el("h3", {}, `Tags — ${doc.filename}`),
    el("div", { class: "field" }, el("label", {}, "Comma-separated"), inp),
    el("div", { class: "mrow" },
      el("button", { class: "btn", onclick: () => ov.remove() }, "Cancel"),
      el("button", {
        class: "btn primary",
        onclick: async () => {
          const list = inp.value.split(",").map((x) => x.trim()).filter(Boolean);
          try {
            await api.patch(`/documents/${doc.id}/tags`, { tags: list });
            toast("Tags saved", "ok");
            ov.remove();
            onSaved && onSaved();
          } catch (e) { toast(e.message, "err"); }
        },
      }, "Save"),
    ),
  );
  ov.append(modal);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  document.body.append(ov);
  inp.focus();
}

/* ---------- queue card ---------- */
export function queueCard() {
  const q = store.get("ingestQueue") || [];
  if (!q.length) return null;
  const card = el("div", { class: "card cardpad mt", style: "margin-bottom:14px" },
    el("div", { class: "ct" }, el("h3", {}, "Ingest queue"),
      el("span", { class: "hint" }, `${q.filter((x) => x.status === "running").length || ""} running`)));
  for (const it of q) {
    const chip = it.status === "running" ? el("span", { class: "stchip st-info" }, "indexing…")
      : it.status === "queued" ? el("span", { class: "stchip st-warn" }, "queued")
      : it.status === "failed" ? el("span", { class: "stchip st-err" }, "failed")
      : el("span", { class: "stchip st-ok" }, "done");
    card.append(el("div", { class: "kv" },
      el("span", { class: "k", style: "width:auto" },
        el("b", { style: "font-weight:600" }, it.filename), " · ", MODE_LABELS[it.mode] || it.mode),
      el("span", { class: "v", style: "justify-content:flex-end;display:flex;gap:8px" },
        it.error ? el("span", { class: "muted", style: "font-size:11.5px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, it.error) : el("span"),
        chip)));
  }
  return card;
}
