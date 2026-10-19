/* Logs (P10): live tail of the server's in-memory log buffer with level
   filters, auto-refresh, clear-all. GET /logs?level=&n= · DELETE /logs. */
import { api } from "../api.js";
import { store } from "../store.js";
import { el, icon, toast } from "../ui.js";
import { go } from "../router.js";

const LEVELS = ["all", "debug", "info", "warning", "error"];
const NSIZES = [100, 300, 500, 1000];

let level = "all";
let n = 500;
let auto = true;

function lineClass(line) {
  const l = line.toLowerCase();
  if (l.includes("| error") || l.includes("| critical")) return "lg-err";
  if (l.includes("| warning")) return "lg-warn";
  return "";
}

export async function render(root) {
  root.append(el("div", { class: "vhead" },
    el("h2", {}, "Server logs"),
    el("span", { class: "vhsub" }, "in-memory ring buffer, newest last"),
    el("div", { class: "right", style: "display:flex;gap:7px" },
      el("div", { class: "segs" },
        ...NSIZES.map((sz) => el("button", {
          class: `chip ${n === sz ? "on" : ""}`, "data-size": sz,
          onclick: (e) => {
            n = sz;
            root.querySelectorAll("[data-size]").forEach((c) => c.classList.toggle("on", +c.dataset.size === sz));
            go("logs");
          },
        }, sz)))),
    el("div", { class: "right", style: "display:flex;gap:7px" },
      el("button", {
        class: `btn sm ${auto ? "" : "danger"}`, "data-auto": "1",
        onclick: (e) => {
          auto = !auto;
          e.target.closest("button").classList.toggle("danger", !auto);
          e.target.closest("button").textContent = auto ? "Auto 2s ●" : "Auto off";
          if (auto) go("logs"); else tick();
        },
      }, auto ? "Auto 2s ●" : "Auto off"),
      el("button", { class: "btn sm", onclick: () => go("logs") }, icon("refresh"), "Refresh"),
      el("button", { class: "btn sm danger", style: "color:var(--err)", onclick: async () => {
        if (!confirm("Clear the in-memory log buffer?")) return;
        try { await api.del("/logs"); toast("Log buffer cleared", "ok"); go("logs"); }
        catch (e) { toast(e.message, "err"); }
      } }, icon("trash"), "Clear"))));

  /* level chips */
  root.append(el("div", { class: "chips", style: "margin-bottom:12px" },
    ...LEVELS.map((lv) => el("button", { class: `chip ${level === lv ? "on" : ""}`, "data-level": lv,
      onclick: (e) => {
        level = lv;
        root.querySelectorAll("[data-level]").forEach((c) => c.classList.toggle("on", c.dataset.level === lv));
        go("logs");
      } }, lv === "all" ? "All levels" : lv))));

  const card = el("div", { class: "card" }, el("div", { class: "tblwrap" }));
  const box = el("pre", { class: "logbox" }, "loading…");
  card.append(box);
  root.append(card);
  const count = el("span", { class: "mini", style: "display:block;margin-top:8px;color:var(--faint)" }, "");
  root.append(count);

  async function tick() {
    try {
      const r = await api.get(`/logs?level=${encodeURIComponent(level)}&n=${n}`);
      box.textContent = "";
      const lines = r.lines || [];
      if (!lines.length) {
        box.append(el("span", { class: "muted" }, "(no log lines)"));
      } else {
        const frag = document.createDocumentFragment();
        for (const ln of lines) {
          const span = document.createElement("span");
          span.textContent = ln + "\n";
          const cls = lineClass(ln);
          if (cls) span.className = cls;
          frag.append(span);
        }
        box.append(frag);
        box.scrollTop = box.scrollHeight;
      }
      count.textContent = `${r.count} lines (last ${n}, level: ${level})`;
    } catch (e) {
      box.textContent = e.message;
    }
    if (auto && store.get("view") === "logs") {
      if (store.get("logTimer")) clearTimeout(store.get("logTimer"));
      store.set("logTimer", setTimeout(tick, 2000));
    }
  }
  tick();
}

export function renderCtx(ctxP) {
  const panel = el("div", { class: "ctxpanel", "data-view": "logs" });
  panel.append(el("div", { class: "chead" }, "Log tail"));
  panel.append(
    kvRow("Filter", level === "all" ? "all levels" : level, level === "all"),
    kvRow("Size", `${n} lines`, true),
    kvRow("Auto-refresh", auto ? "every 2 s" : "off", auto),
  );
  panel.append(el("div", { class: "chead", style: "margin-top:16px" }, "Levels"));
  panel.append(el("div", { class: "muted", style: "font-size:12px;line-height:1.6" },
    "warning – non-fatal fallbacks (DOI faults, OCR skips)",
    el("br"), "error – failed requests & background jobs"));
  ctxP.append(panel);
}

function kvRow(k, v, ok) {
  return el("div", { class: "kv" },
    el("span", { class: "k" }, k),
    el("span", { class: "v", style: "display:flex;align-items:center;gap:7px" },
      el("span", { class: ok ? "dot" : "dot warn" }), el("span", {}, v)));
}
