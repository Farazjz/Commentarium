/* Podcast studio (P8): generate deep dives as background jobs, play MP3s
   with a waveform-styled player, regenerate/delete episodes. */
import { api } from "../api.js";
import { store } from "../store.js";
import { el, icon, rel, fmtDur, toast } from "../ui.js";
import { go } from "../router.js";

const PROVIDERS = ["edge-tts", "localhost", "gateway-edge-tts", "voicestudio", "cloudflare", "google"];
const DUR = { short: "Short (2-5 min)", medium: "Medium (6-10 min)", long: "Long (11-20 min)" };

let audio = null;         // single shared <audio>
let playingId = null;     // podcast id currently loaded
let paused = true;

const parseIds = (s) => { try { return JSON.parse(s) || []; } catch { return []; } };

/* ---------------- generation poller ---------------- */
function startPodPing() {
  if (store.get("podTimer")) return;
  const tick = async () => {
    let pods = [];
    try { pods = await api.get("/podcasts"); } catch { /* offline */ }
    const gen = (pods || []).filter((p) => p.status === "generating");
    store.set("podcasts", pods || []);
    if (!gen.length) {
      store.set("podTimer", null);
      if (store.get("podWasGenerating")) {
        store.set("podWasGenerating", false);
        toast("Podcast generation finished", "ok");
        if (store.get("view") === "podcast") go("podcast");
      }
      return;
    }
    store.set("podWasGenerating", true);
    if (store.get("view") === "podcast") go("podcast");
    store.set("podTimer", setTimeout(tick, 3000));
  };
  store.set("podTimer", setTimeout(tick, 2500));
}

/* ---------------- player ---------------- */
function ensureAudio(podId) {
  if (playingId !== podId || !audio) {
    audio = new Audio(`/podcasts/${encodeURIComponent(podId)}/audio`);
    playingId = podId;
    paused = true;
  }
  return audio;
}

function togglePlay(podId, btn) {
  const a = ensureAudio(podId);
  if (paused) {
    a.play().then(() => { paused = false; syncButtons(); }).catch((e) => {
      toast(e.message || "Audio failed to load", "err");
      paused = true;
      syncButtons();
    });
  } else {
    a.pause();
    paused = true;
    syncButtons();
  }
  function syncButtons() {
    document.querySelectorAll(".pbtn").forEach((b) =>
      b.classList.toggle("paused", !(playingId === b.dataset.pid && !paused)));
    const playerCard = document.querySelector("#playerCard");
    if (playerCard) {
      const wave = playerCard.querySelector(".gravity");
      if (wave) wave.classList.toggle("on", !paused);
    }
  }
}

function playerCard(pods) {
  const active = playingId ? pods.find((p) => p.id === playingId) : null;
  if (!active) return null;
  const card = el("div", { class: "player", id: "playerCard" });
  const tgl = el("button", { class: `pbtn ${paused ? "paused" : ""}`, "data-pid": active.id,
    onclick: () => togglePlay(active.id) },
    el("span", { class: paused ? "" : "hide", style: "display:" + (paused ? "block" : "none") }, icon("play")),
    el("span", { style: "display:" + (paused ? "none" : "block") }, icon("pause")));
  const time = el("span", { class: "ptime" }, `0:00 / ${fmtDur(active.duration_sec)}`);
  const wave = el("div", { class: `gravity ${paused ? "" : "on"}` },
    ...Array.from({ length: 34 }, () => el("i")));
  audio = ensureAudio(active.id);
  audio.ontimeupdate = () => {
    if (audio.duration) time.textContent = `${fmtSec(audio.currentTime)} / ${fmtSec(audio.duration)}`;
  };
  audio.onended = () => { paused = true; sync(); };
  function sync() {
    tgl.classList.toggle("paused", paused);
    tgl.querySelectorAll("span").forEach((s, i) => s.style.display = i === 0 ? (paused ? "block" : "none") : (paused ? "none" : "block"));
    wave.classList.toggle("on", !paused);
  }
  card.append(
    el("div", { class: "ptitle" }, icon("mic"), active.title,
      el("span", { class: "countchip", style: "margin-left:auto" }, `${active.tts_provider || "tts"} · ${rel(active.created_at)}`)),
    el("div", { class: "pbar" },
      el("span", { class: "pc" }, tgl), wave, el("span", { class: "pc" }, time),
      el("a", { class: "pc btn sm", href: `/podcasts/${encodeURIComponent(active.id)}/audio`,
        download: "" }, icon("down"), "MP3")));
  return card;
}

const fmtSec = (s) => fmtDur(s);

/* ---------------- generate modal ---------------- */
function generateModal(docs, projects) {
  if (!docs.length) { toast("Upload and index papers first (Library)", "info"); return; }
  const defaultPid = store.get("libProject") || (projects[0] || {}).id || "";
  const picked = new Set(docs.slice(0, 1).map((d) => d.id));   // convenience: first paper preselected
  const list = el("div", { class: "docpick" },
    ...docs.map((d) => {
      const lab = el("label", {},
        el("input", { type: "checkbox", ...(picked.has(d.id) ? { checked: "" } : {}), onchange: (e) =>
          e.target.checked ? picked.add(d.id) : picked.delete(d.id) }),
        el("span", { style: "overflow:hidden;text-overflow:ellipsis;white-space:nowrap" },
          d.title || d.filename));
      lab.dataset.doc = "1";
      return lab;
    }));
  const titleIn = el("input", { type: "text", value: "Deep dive: " + (docs[0].title || docs[0].filename), maxlength: "90" });
  const projSel = el("select", {}, ...projects.map((p) =>
    el("option", { value: p.id, ...(p.id === defaultPid ? { selected: "" } : {}) }, p.name || "(unnamed)")));
  let dur = "medium";
  const mrow = el("div", { class: "genrow" }, ...Object.entries(DUR).map(([k, label]) =>
    el("button", { class: `chip ${k === dur ? "on" : ""}`, onclick: (e) => {
      dur = k; mrow.querySelectorAll(".chip").forEach((c) => c.classList.remove("on"));
      e.target.classList.add("on");
    } }, label)));
  const provSel = el("select", {}, ...PROVIDERS.map((p) => el("option", { value: p, ...(p === (store.get("summary") || {}).tts_provider ? { selected: "" } : {}) }, p)));

  const langSel = (idx) => {
    const cur = idx === 1 ? "en" : "en";
    return el("select", { style: "width:auto;height:32px;font-size:12px;padding:0 8px" },
      el("option", { value: "en", ...(cur === "en" ? { selected: "" } : {}) }, `Host ${idx}: English`),
      el("option", { value: "fa", ...(cur === "fa" ? { selected: "" } : {}) }, `Host ${idx}: فارسی`));
  };
  const l1 = langSel(1), l2 = langSel(2);

  const ov = el("div", { class: "modalov open" });
  const modal = el("div", { class: "modal citedit", style: "width:540px" },
    el("h3", {}, "New podcast deep dive"),
    el("div", { class: "field" }, el("label", {}, "Title"), titleIn),
    el("div", { class: "field" }, el("label", {}, "Papers"), list),
    el("div", { class: "f2" },
      el("div", { class: "field" }, el("label", {}, "Project"), projSel),
      el("div", { class: "field" }, el("label", {}, "TTS provider"), provSel)),
    el("div", { class: "field" }, el("label", {}, "Target length"), mrow),
    el("div", { class: "f2" },
      el("div", { class: "field" }, el("label", {}, "Story host"), l1),
      el("div", { class: "field" }, el("label", {}, "Expert host"), l2)),
    el("div", { class: "muted", style: "font-size:11.5px;margin-bottom:10px" },
      "Runs as a background job: summarize → script → synthesize. You can leave this modal and keep working."),
    el("div", { class: "mrow" },
      el("button", { class: "btn", onclick: () => ov.remove() }, "Cancel"),
      el("button", {
        class: "btn primary",
        onclick: async () => {
          if (!picked.size) { toast("Select at least one paper", "info"); return; }
          const body = {
            project_id: projSel.value || defaultPid,
            document_ids: [...picked],
            title: titleIn.value.trim() || "Untitled deep dive",
            provider: provSel.value,
            duration_setting: dur,
            host_languages: { 1: l1.value, 2: l2.value },
          };
          try {
            const r = await api.post("/podcasts/generate-async", body);
            toast(`Episode queued — job ${r.job_id}`, "ok");
            ov.remove();
            store.set("libProject", body.project_id);
            startPodPing();
            go("podcast");
          } catch (e) { toast(e.message, "err"); }
        } }, icon("mic"), "Generate")));
  ov.append(modal);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  document.body.append(ov);
}

/* ---------------- main view ---------------- */
export async function render(root) {
  const [pods, docs] = await Promise.all([
    api.get("/podcasts").catch(() => null),
    api.get("/documents").catch(() => []),
  ]);
  if (pods !== null) store.set("podcasts", pods);
  const podcasts = pods || [];
  const projects = store.get("projects") || [];

  root.append(el("div", { class: "vhead" },
    el("h2", {}, "Podcast studio"),
    el("span", { class: "vhsub" }, "two-host deep dives over your papers"),
    el("div", { class: "right" },
      el("button", { class: "btn primary sm", onclick: () => generateModal(docs || [], projects) },
        icon("mic"), "New episode"))));

  const player = playerCard(podcasts);
  if (player) root.append(player, el("div", { style: "height:14px" }));

  /* generation progress — keyed on /jobs kind=podcast (episodes appear when done) */
  let jobsList = [];
  try { jobsList = await api.get("/jobs"); } catch { /* offline */ }
  const activeJobs = (jobsList || []).filter((j) => j.kind === "podcast" &&
    (j.status === "running" || j.status === "queued"));
  if (activeJobs.length) {
    const card = el("div", { class: "card cardpad", style: "margin-bottom:14px" },
      el("div", { class: "ct" }, el("h3", {}, "Generating…"),
        el("button", { class: "btn sm", onclick: () => go("podcast") }, icon("refresh"), "Refresh")));
    for (const j of activeJobs) {
      card.append(el("div", { class: "kv" },
        el("span", { class: "k", style: "width:auto" }, el("b", { style: "font-weight:600" }, j.label)),
        el("span", { class: "v", style: "display:flex;align-items:center;gap:9px" },
          el("span", { class: "stchip st-info" }, j.message || j.status),
          j.progress ? el("span", { class: "countchip" }, `${Math.round(j.progress * 100)}%`) : el("span"))));
    }
    root.append(card);
    startPodPing();
  }

  const listCard = el("div", { class: "card" },
    el("div", { class: "tblwrap" }));
  if (!podcasts.length) {
    listCard.append(el("div", { class: "empty" },
      el("div", { class: "eic" }, icon("mic")),
      el("h3", {}, "No episodes yet"),
      el("p", {}, "Generate a NotebookLM-style deep dive over one or more indexed papers — two AI hosts discuss, you get an MP3."),
      el("button", { class: "btn primary", onclick: () => generateModal(docs || [], projects) }, icon("play"), "Create first episode")));
  } else {
    for (const p of podcasts) {
      listCard.append(epRow(p, docs || [], (p.id === playingId) ? paused : true));
    }
  }
  root.append(listCard);

  function epRow(p, allDocs, isPaused) {
    const ready = p.status === "ready" && p.duration_sec;
    const names = p.document_filenames || [];
    return el("div", { class: "eprow" },
      ready
        ? el("button", { class: `epplay ${isPaused ? "" : "paused"}`, "data-testid": "play", onclick: () => {
            playingId = p.id;
            const r = render(root);   // re-render mounts fresh player card
            void r;
            const card = document.querySelector("#playerCard .pbtn");
            if (card) card.click();
          } }, icon(isPaused ? "play" : "pause"))
        : el("span", { class: "epplay paused", style: "cursor:default" }, icon(p.status === "error" ? "alert" : "clock")),
      el("div", { class: "epmain" },
        el("div", { class: "eptitle" }, p.title || "Untitled episode"),
        el("div", { class: "epmeta" },
          el("span", { class: `stchip ${p.status === "ready" ? "st-ok" : p.status === "error" ? "st-err" : "st-warn"}` },
            p.status === "ready" ? fmtDur(p.duration_sec) : p.status),
          el("span", { class: "stchip st-info" }, p.tts_provider || "tts"),
          ...(p.status === "error" && p.error ? [el("span", { class: "countchip", title: p.error }, p.error.slice(0, 60))] : []),
          names.map((n) => el("span", { class: "dochip" }, icon("book"), n))),
        el("div", { class: "countchip", style: "margin-top:4px" }, rel(p.created_at))),
      el("div", { class: "epacts" },
        ready ? el("a", { class: "ibtn", title: "Download MP3",
          href: `/podcasts/${encodeURIComponent(p.id)}/audio`, download: "" }, icon("down")) : el("span"),
        el("span", { class: "ibtn", title: "Regenerate", onclick: async () => {
          if (!confirm(`Regenerate “${p.title}”? The old audio is replaced.`)) return;
          try { await api.post(`/podcasts/${encodeURIComponent(p.id)}/regenerate-async`, {}); startPodPing(); toast("Regeneration queued", "ok"); }
          catch (e) { toast(e.message, "err"); }
        } }, icon("edit")),
        el("span", { class: "ibtn", title: "Delete", onclick: async () => {
          if (!confirm(`Delete “${p.title}” and its audio?`)) return;
          try { await api.del(`/podcasts/${encodeURIComponent(p.id)}`); if (playingId === p.id) { playingId = null; audio = null; } toast("Episode deleted", "ok"); }
          catch (e) { toast(e.message, "err"); }
          go("podcast");
        } }, icon("trash"))));
  }
}

/* ---------------- context panel ---------------- */
export function renderCtx(ctxP) {
  const pods = store.get("podcasts") || [];
  const ready = pods.filter((p) => p.status === "ready");
  const gen = pods.filter((p) => p.status === "generating" || p.status === "pending");
  const err = pods.filter((p) => p.status === "error");
  const panel = el("div", { class: "ctxpanel", "data-view": "podcast" });
  panel.append(el("div", { class: "chead" }, "Episodes"));
  panel.append(
    kvRow("Ready", String(ready.length), ready.length > 0),
    kvRow("Generating", String(gen.length), gen.length === 0),
    kvRow("Failed", String(err.length), err.length === 0),
    kvRow("Total audio", `${Math.round(ready.reduce((a, p) => a + (p.duration_sec || 0), 0) / 60)} min`, ready.length > 0),
  );
  panel.append(el("div", { class: "chead", style: "margin-top:18px" }, "Engine"));
  panel.append(
    kvRow("Provider", (store.get("summary") || {}).tts_provider || "edge-tts", true),
    kvRow("Doc scope", store.get("libProject") ? "library selection" : "any project", true),
  );
  ctxP.append(panel);

  if (!ready.length) return;
  panel.append(el("div", { class: "chead", style: "margin-top:18px" }, "Listen"));
  ready.slice(0, 5).forEach((p) => panel.append(el("div", {
    class: "ctxrow", onclick: () => {
      playingId = p.id;
      paused = false;
      go("podcast");
      const card = document.querySelector("#playerCard .pbtn");
      if (card) card.click();
    } },
    el("span", { class: "ic mut" }, "▶"),
    el("span", { style: "overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, p.title || "Untitled"),
    el("span", { class: "mini" }, fmtDur(p.duration_sec)))));
}

function kvRow(k, v, ok) {
  return el("div", { class: "kv" },
    el("span", { class: "k" }, k),
    el("span", { class: "v", style: "display:flex;align-items:center;gap:7px;min-width:0" },
      el("span", { class: ok ? "dot" : "dot warn" }), el("span", {}, v)));
}
