/* app.js — bootstrap: theme, navigation, shell data, topbar chips,
   context panel, guard banner, command palette, exit button. */
import { api } from "./api.js";
import { store } from "./store.js";
import { el, icon, toast } from "./ui.js";
import { VIEWS, currentView, render, onViewRender } from "./router.js";
import { render as renderDashboard } from "./views/dashboard.js";
import { render as renderLibrary, renderCtx as renderCtxLibrary } from "./views/library.js";
import { render as renderChat, renderCtx as renderCtxChat } from "./views/chat.js";
import { render as renderCitations, renderCtx as renderCtxCitations } from "./views/citations.js";
import { render as renderPodcast, renderCtx as renderCtxPodcast } from "./views/podcast.js";
import { renderPhase, renderCtxPhase } from "./views/phases.js";

/* ============ theme ============ */
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("cm-theme", t); } catch { /* private mode */ }
}
try { setTheme(localStorage.getItem("cm-theme") || "dark"); } catch { setTheme("dark"); }

/* ============ view rendering ============ */
const content = () => document.querySelector(".content");
function renderView(view) {
  const root = el("div", { id: "view" });
  const c = content();
  if (!c) return;
  c.textContent = "";
  c.append(root);
  renderCtx(view);

  if (view === "dashboard") {
    renderDashboard(root).catch((e) => { console.error(e); toast(e.message || String(e), "err"); });
  } else if (view === "library") {
    renderLibrary(root).catch((e) => { console.error(e); toast(e.message || String(e), "err"); });
  } else if (view === "chat") {
    renderChat(root).catch((e) => { console.error(e); toast(e.message || String(e), "err"); });
  } else if (view === "citations") {
    renderCitations(root).catch((e) => { console.error(e); toast(e.message || String(e), "err"); });
  } else if (view === "podcast") {
    renderPodcast(root).catch((e) => { console.error(e); toast(e.message || String(e), "err"); });
  } else {
    renderPhase(view, root);
  }
}
onViewRender(renderView);

/* ============ context panel ============ */
function renderCtx(view) {
  const ctx = document.querySelector(".ctx");
  if (!ctx) return;
  ctx.textContent = "";
  if (view === "dashboard") {
    const projects = store.get("projects") || [];
    const idx = store.get("indexStats");
    const panel = el("div", { class: "ctxpanel", "data-view": "dashboard" });
    panel.append(el("div", { class: "chead" }, "Projects"));
    if (!projects.length) {
      panel.append(el("div", { class: "ctxrow", style: "cursor:default" },
        el("span", { class: "ic mut" }, "+"), "No projects yet — go to Library"));
    } else {
      projects.forEach((p, i) => panel.append(el("div", { class: `ctxrow ${i === 0 ? "on" : ""}` },
        el("span", { class: p.chat_model ? "ic" : "ic mut" }, (p.name || "–").slice(0, 2).toUpperCase()),
        p.name || "(unnamed)",
        el("span", { class: "n" }, p.chat_model ? "model" : ""))));
    }
    panel.append(el("div", { class: "chead", style: "margin-top:20px" }, "System"));
    panel.append(
      kvRow("API server", store.get("health") ? `:${store.get("health").port}` : "…", store.get("health")?.status === "ok"),
      kvRow("Corpus", idx ? `${idx.indexed_documents?.length ?? 0}/${idx.document_count ?? 0} synced` : "…", !!idx?.embedding_model_matches),
      kvRow("Chunks", idx ? String(idx.chunks) : "…", !!idx),
      kvRow("Model / dims", idx ? `${idx.embedding_dim ?? "?"}d` : "…", !!idx),
    );
    ctx.append(panel);
  } else if (view === "library") {
    renderCtxLibrary(ctx);
  } else if (view === "chat") {
    renderCtxChat(ctx);
  } else if (view === "citations") {
    renderCtxCitations(ctx);
  } else if (view === "podcast") {
    renderCtxPodcast(ctx);
  } else {
    renderCtxPhase(view, ctx);
  }
  /* keep at most one active ctx panel */
  ctx.querySelectorAll(".ctxpanel").forEach(p => p.classList.toggle("active", true));
}
function kvRow(k, v, ok) {
  return el("div", { class: "kv" },
    el("span", { class: "k" }, k),
    el("span", { class: "v", style: "display:flex;align-items:center;gap:7px;min-width:0" },
      el("span", { class: v === "…" ? "dot warn" : "dot" }),
      el("span", {}, v)));
}

/* ============ topbar status chips ============ */
function chips() {
  const h = store.get("health");
  const idx = store.get("indexStats");
  const sum = store.get("summary");
  return [
    { dot: h?.status === "ok" ? "" : "warn", text: h ? `API :${h.port}` : "API …" },
    { dot: idx?.embedding_model_matches === false ? "warn" : "", text: idx ? `embed ${idx.embedding_dim ?? "?"}d` : "embed …" },
    { dot: "run", text: sum?.chat_model ? `${sum.chat_model} · t ${sum.chat_temperature ?? ""}` : "model …" },
  ];
}
function updateTopbar() {
  const holder = document.querySelector(".status");
  if (!holder) return;
  holder.textContent = "";
  chips().forEach(c => holder.append(el("span", { class: "pill" },
    el("span", { class: `dot ${c.dot}` }), c.text)));
  /* embedding guard banner — shown app-wide while the corpus is out of sync */
  let banner = document.querySelector("#guardBanner");
  const mismatch = store.get("indexStats")?.embedding_model_matches === false;
  if (mismatch && !banner) {
    const idx = store.get("indexStats");
    banner = el("div", { id: "guardBanner", class: "banner" },
      icon("shield"),
      el("div", {},
        el("b", {}, "Embedding-dimension guard: "),
        `corpus vectors were built with ${idx.embedding_model || "(unknown)"}, but ${idx.current_embedding_model || "the current model"} is configured. Ingest/chat will fail loudly until you switch back or re-index.`,
        "  ",
        el("a", { href: "#/settings" }, "Settings →"),
      ));
    const content = document.querySelector(".content");
    content.prepend(banner);
  } else if (!mismatch && banner) {
    banner.remove();
  }
}

async function refreshCore() {
  try {
    store.set("health", await api.get("/health"));
    store.set("apiOnline", true);
  } catch {
    store.set("apiOnline", false);
  }
  try { store.set("summary", await api.get("/settings/summary")); } catch { /* offline */ }
  try {
    const idx = await api.get("/index/stats");
    store.set("indexStats", idx);
  } catch { /* offline */ }
  const full = await api.get("/settings").catch(() => null);
  if (full) store.set("settings", full);
  const projects = await api.get("/projects").catch(() => null);
  if (projects) store.set("projects", projects);
  updateTopbar();
  renderCtx(store.get("view"));
}

/* ============ command palette ============ */
function paletteItems() {
  const nav = Object.entries(VIEWS).map(([id, v]) => ({
    act: `go:${id}`, label: v.title, go: v.tip, grp: "Navigate",
  }));
  return [
    ...nav,
    { act: "theme", label: "Toggle dark / light", go: "theme", grp: "Actions" },
  ];
}
function openPalette() {
  const ov = document.querySelector("#paletteOverlay");
  const inp = ov.querySelector("input");
  inp.value = "";
  filterPalette("");
  ov.classList.add("open");
  inp.focus();
}
function closePalette() { document.querySelector("#paletteOverlay").classList.remove("open"); }
function filterPalette(q) {
  const list = document.querySelector("#cmdList");
  const items = paletteItems().filter(it => !q || it.label.toLowerCase().includes(q.toLowerCase()));
  list.textContent = "";
  let lastGrp = "";
  for (const it of items) {
    if (it.grp !== lastGrp) {
      list.append(el("div", { class: "cmdgrp" }, it.grp));
      lastGrp = it.grp;
    }
    const row = el("div", { class: "cmditem", "data-act": it.act },
      icon(it.act.startsWith("go:") ? "home" : "sun"), it.label, el("span", { class: "go" }, it.go));
    row.addEventListener("click", () => { runPaletteAct(it.act); closePalette(); });
    list.append(row);
  }
}
function runPaletteAct(act) {
  if (act === "theme") setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  else if (act.startsWith("go:")) go(act.slice(3));
}

/* ============ exit ============ */
async function exitApp() {
  if (!confirm("Shut down the Commentarium API server?\n\nThe web UI will go offline too.")) return;
  try { await api.post("/shutdown"); toast("Shutting down…", "ok"); } catch (e) { toast(e.message, "err"); }
}

/* ============ boot ============ */
function boot() {
  document.querySelectorAll(".railbtn[data-view]").forEach(b =>
    b.addEventListener("click", () => go(b.dataset.view)));
  const rail = document.querySelector(".rail");
  document.querySelector("#railExit")?.addEventListener("click", exitApp);
  document.querySelector("#themeBtn")?.addEventListener("click", () =>
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));

  const cp = document.querySelector("#cmdOpen");
  cp?.addEventListener("click", openPalette);
  const inp = document.querySelector("#cmdInput");
  inp?.addEventListener("input", () => filterPalette(inp.value));
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      document.querySelector("#paletteOverlay").classList.contains("open") ? closePalette() : openPalette();
    }
    if (e.key === "Escape") closePalette();
  });
  const ov = document.querySelector("#paletteOverlay");
  ov?.addEventListener("click", (e) => { if (e.target === ov) closePalette(); });

  window.addEventListener("hashchange", () => render(currentView()));

  /* chat ctx refreshes whenever the session list changes (after ask/rename/delete) */
  store.on("sessions", () => { if (currentView() === "chat") renderCtx("chat"); });

  // initial shell data, then first render
  store.set("view", currentView());
  render(currentView());
  refreshCore();
  setInterval(refreshCore, 20000);   // gentle keep-alive + status refresh
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
