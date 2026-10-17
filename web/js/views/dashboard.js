/* Dashboard — fully live: stats, system status, checklist, activity feed. */
import { api, loadAll } from "../api.js";
import { store } from "../store.js";
import { el, icon, rel, fmtDur, toast } from "../ui.js";
import { go } from "../router.js";

const CHECK_KEY = "cm-v2-checks";
const DEFAULT_CHECKS = [
  "Connect your gateway & pick a model",
  "Create a project",
  "Upload papers & index them",
  "Ask a question with page citations",
  "Generate a podcast deep-dive",
  "Export an APA / Vancouver bibliography",
];

function loadChecks() {
  try { return JSON.parse(localStorage.getItem(CHECK_KEY)) || []; } catch { return []; }
}
function saveChecks(list) {
  try { localStorage.setItem(CHECK_KEY, JSON.stringify(list)); } catch { /* private mode */ }
}

function statCard(value, label, iconId, sub) {
  return el("div", { class: "card stat" },
    el("span", { class: "top" }, icon(iconId)),
    el("div", { class: "num" }, String(value ?? "—")),
    el("div", { class: "lbl" }, sub ? `${label} · ${sub}` : label));
}

const KIND_ICON = { document: "book", podcast: "mic" };

export async function render(root) {
  root.append(
    el("div", { class: "hero card" },
      el("div", { class: "skel", style: "width:46%;height:26px;margin-bottom:10px" }),
      el("div", { class: "skel", style: "width:70%;height:14px;margin-bottom:16px" }),
      el("div", { class: "qa" }, el("div", { class: "skel", style: "width:110px;height:34px" }),
        el("div", { class: "skel", style: "width:90px;height:34px" })),
    ),
    el("div", { class: "stats" },
      ...Array.from({ length: 4 }, () => el("div", { class: "card skcard skel skstat" }))),
    el("div", { class: "g2" },
      el("div", { class: "card cardpad" }, el("div", { class: "skel", style: "height:120px" })),
      el("div", { class: "card cardpad" }, el("div", { class: "skel", style: "height:120px" }))),
    el("div", { class: "card cardpad mt" }, el("div", { class: "skel skrow" }), el("div", { class: "skel skrow" })),
  );

  const data = await loadAll(
    ["/projects"],
    ["/index/stats"],
    ["/chat/sessions"],
    ["/podcasts"],
    ["/activity?limit=8"],
  );
  const [projects, indexStats, sessions, podcasts, activityFeed] = data;
  if (indexStats) store.set("indexStats", indexStats);
  if (projects) store.set("projects", projects);
  if (podcasts) store.set("podcasts", podcasts);
  if (sessions) store.set("sessions", sessions);

  root.textContent = "";

  /* ---- hero ---- */
  const docCount = indexStats?.document_count ?? null;
  root.append(
    el("div", { class: "hero card" },
      el("h1", {}, "Greetings — ", el("span", { class: "gradtext" }, "your Commentarium assistant"), " is ",
        indexStats ? el("b", {}, indexStats.embedding_model_matches ? "ready." : "out of sync.") : "warming up."),
      el("p", {}, indexStats
        ? `${docCount} papers in the library · ${indexStats.chunks} indexed chunks · corpus model ${indexStats.embedding_model || "(none stored)"} — everything still lives on this machine.`
        : "Connecting to the local API…"),
      el("div", { class: "qa" },
        el("button", { class: "btn primary", onclick: () => go("library") }, icon("up"), "Upload papers"),
        el("button", { class: "btn", onclick: () => go("chat") }, icon("chat"), "New chat"),
        el("button", { class: "btn", onclick: () => go("podcast") }, icon("mic"), "Podcast"),
        el("button", { class: "btn", onclick: () => go("ocr") }, icon("scan"), "OCR scan"),
        el("button", { class: "btn", onclick: () => go("settings") }, icon("gear"), "Settings"),
        el("button", { class: "btn", onclick: () => go("logs") }, icon("list"), "Log viewer"),
        el("button", { class: "btn danger", onclick: async () => {
          if (!confirm("Shut down the Commentarium servers?\nUnsaved edits and running jobs will be lost.")) return;
          if (!confirm("Are you sure? The SPA and API stop until you launch Commentarium again.")) return;
          try { await api.post("/shutdown", {}); } catch { /* server dies mid-call */ }
          document.body.innerHTML =
            "<div style='display:grid;place-items:center;height:100vh;font-family:Inter,system-ui'>" +
            "<div style='text-align:center;color:#888'>Servers stopped — relaunch Commentarium to continue." +
            "<br><small>You can close this tab.</small></div></div>";
        } }, icon("power"), "Exit & shut down"),
      ),
    ),
  );

  /* ---- stat cards ---- */
  const chunks = indexStats?.chunks ?? 0;
  root.append(el("div", { class: "stats" },
    statCard(projects ? projects.length : null, "Projects", "book", projects?.some(p => p.chat_model) ? "model overrides" : undefined),
    statCard(indexStats?.document_count ?? null, "Documents", "dna", `${chunks} chunks`),
    statCard(sessions ? sessions.length : null, "Chat sessions", "chat"),
    statCard(podcasts ? podcasts.length : null, "Podcast episodes", "mic",
      podcasts?.filter(p => p.status === "generating" || p.status === "pending").length
        ? `${podcasts.filter(p => p.status === "generating" || p.status === "pending").length} active`
        : undefined),
  ));

  /* ---- system status ---- */
  const health = store.get("health");
  const summary = store.get("summary");
  const full = store.get("settings");
  const rows = [
    ["API server", health ? `${health.status} · 127.0.0.1:${health.port}` : "…", health],
    ["Gateway", full ? `${full.OPENROUTER_BASE_URL || "(default)"} · ${summary?.chat_model || "no model"}` : "…", full],
    ["Embeddings", indexStats ? `${summary?.embedding_backend ?? ""} · ${indexStats.embedding_model || "—"} · ${indexStats.embedding_dim ?? "?"}d` : "…", indexStats],
    ["Corpus guard", indexStats ? `${indexStats.indexed_documents?.length ?? 0}/${indexStats.document_count ?? 0} vectors in sync` : "…", indexStats],
    ["OCR backend", summary?.ocr_backend, summary],
    ["Podcast TTS", summary?.tts_provider, summary],
  ];
  const statusCard = el("div", { class: "card cardpad" },
    el("div", { class: "ct" }, el("h3", {}, "System status"),
      el("button", { class: "btn sm", onclick: () => go("settings") }, icon("refresh"), "Settings")),
    ...rows.map(([k, v, ok]) =>
      el("div", { class: "kv" },
        el("span", { class: "k" }, k),
        el("span", { class: "v", style: "display:flex;align-items:center;gap:7px" },
          el("span", { class: v === "…" || !v ? "dot warn" : "dot" }),
          el("span", {}, String(v)),
          indexStats && k === "Embeddings" && !indexStats.embedding_model_matches ? el("span", { class: "stchip st-warn" }, "mismatch") : el("span"),
        ))),
  );

  /* ---- getting-started checklist (localStorage) ---- */
  const checks = loadChecks();
  const checklistCard = el("div", { class: "card cardpad" },
    el("div", { class: "ct" }, el("h3", {}, "Getting started"),
      el("span", { class: "hint" }, `${checks.length} of ${DEFAULT_CHECKS.length} done`)));
  DEFAULT_CHECKS.forEach((label) => {
    const row = el("div", { class: `checkrow ${checks.includes(label) ? "done" : ""}` },
      el("span", { class: "cb" }, icon("check")),
      el("span", {}, label));
    row.addEventListener("click", () => {
      row.classList.toggle("done");
      const now = [...checklistCard.querySelectorAll(".checkrow.done span:last-child")].map(n => n.textContent);
      saveChecks(now);
      const hint = checklistCard.querySelector(".hint");
      if (hint) hint.textContent = `${now.length} of ${DEFAULT_CHECKS.length} done`;
    });
    checklistCard.append(row);
  });

  root.append(el("div", { class: "g2 mt" }, statusCard, checklistCard));

  /* ---- recent activity ---- */
  const items = activityFeed?.items || [];
  const actCard = el("div", { class: "card cardpad mt" },
    el("div", { class: "ct" }, el("h3", {}, "Recent activity"),
      el("button", { class: "btn sm", onclick: () => go("logs") }, "Open log viewer")));
  if (!items.length) {
    actCard.append(el("div", { class: "muted", style: "padding:10px 2px" },
      "Nothing yet — upload a paper or generate a podcast and it will show up here."));
  } else {
    for (const it of items) {
      const detail =
        it.kind === "podcast"
          ? `MP3 · ${fmtDur(it.duration)} · ${it.tts_provider || summary?.tts_provider || "tts"}`
          : `${it.status === "indexed" ? "indexed" : it.status} · ${it.pages || "?"} p`;
      actCard.append(el("div", { class: "act" },
        el("span", { class: "ic" }, icon(KIND_ICON[it.kind] || "dna")),
        el("div", { style: "min-width:0" },
          el("div", {}, it.kind === "podcast" ? "Podcast " : "Indexed ", el("b", {}, it.title || "(untitled)")),
          el("div", { class: "t" }, detail)),
        el("span", { class: "when" }, rel(it.when))));
    }
  }
  root.append(actCard);
  root.append(el("div", { class: "footnote" },
    "Commentarium v2.0 — local-only research assistant · UI shell live, remaining views arrive per phase (P4–P10)"));
  return;
}
