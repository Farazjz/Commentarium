/* Phase placeholders for views that arrive in P4–P10. Each keeps its layout
   so later phases drop straight in. */
import { el, icon } from "../ui.js";
import { go } from "../router.js";

const PHASES = {
  library: {
    icon: "book", phase: "P4 + P5",
    blurb: "Projects and the document table replace the mockup's sample rows with your real library.",
    now: [
      "Project cards from /projects",
      "Document table with status chips & tag filters (/projects/{id}/documents)",
      "Drag & drop upload with live ingest progress (P5)",
    ],
    later: ["Re-index / delete actions", "Tags & notes inline editors", "Summary dots (Brief / Key points / Quiz…)"],
    ctx: "Your projects and their document counts already drive the panel on the left — loaded live from the API.",
  },
  chat: {
    icon: "chat", phase: "P6",
    blurb: "The citation-chip experience from the mockup, now grounded in real retrieval.",
    now: ["Sessions list (/chat/sessions + history)", "POST /chat/ask with page-level sources panel"],
    later: ["Clickable [1][2][3] citation chips", "Per-project model & temperature chips", "Mode toggle: specific facts vs. distributed"],
    ctx: "Context will list your sessions; sources panel streams from used_chunks.",
  },
  podcast: {
    icon: "mic", phase: "P8",
    blurb: "NotebookLM-style deep dives with a real audio player bound to /podcasts/{id}/audio.",
    now: ["Episode list from /podcasts (status, duration, TTS chips)", "Generate via /podcasts/generate with job progress"],
    later: ["Waveform-animated player", "Host voice pickers (edge-tts / VoiceStudio / api)", "fa (Persian) auto-translate pipeline"],
    ctx: "Voices + engine summary arrive with P8's context panel.",
  },
  citations: {
    icon: "quote", phase: "P7",
    blurb: "Verification workflow over your documents' metadata, resolved in APA 7 or Vancouver.",
    now: ["/documents/{id}/citations/auto — Crossref with confidence", "/citations/resolve for the bibliography"],
    later: ["APA ⇄ Vancouver live toggle", "Export & copy (/citations/export already live in the API)"],
    ctx: "Style toggle will live here, synced with the main view.",
  },
  ocr: {
    icon: "scan", phase: "P9",
    blurb: "Scanned PDFs & images → clean Markdown, with a per-file queue and live preview.",
    now: ["New API surface lands in P3: POST /ocr/extract + /ocr/output management"],
    later: ["Rendered ⇄ Raw preview tabs", "Send-to-library from OCR output", "PaddleOCR-VL / TeleOCR / Tesseract backends"],
    ctx: "Backend + DPI + force-OCR controls belong to this panel.",
  },
  settings: {
    icon: "gear", phase: "P10",
    blurb: "Grouped cards over the full settings API — already built and tested in the backend.",
    now: ["GET/PATCH /settings (whitelisted, masked secrets)", "POST /settings/test-connection with unsaved values", "GET /settings/models (602 models live earlier today)"],
    later: ["Embedding-dim guard banner", "Podcast audio & voice config", "Danger zone: exit & shutdown"],
    ctx: "Section navigation (gateway / embeddings / OCR…).",
  },
  logs: {
    icon: "list", phase: "P10",
    blurb: "The live log stream with level filters, from GET /logs (polls every 2 s).",
    now: ["GET /logs?level=&n=", "DELETE /logs to clear the in-app buffer"],
    later: ["Level chips with counts", "Search + source toggles", "Auto-tail with pause-on-scroll"],
    ctx: "Level counts summarise the current buffer.",
  },
};

export function renderPhase(view, root) {
  const p = PHASES[view];
  if (!p) return;
  root.append(
    el("div", { class: "card phase" },
      el("div", { class: "pic" }, icon(p.icon)),
      el("h2", {}, `${p.phase} — the ${view} view of the mockup`),
      el("p", { class: "pd" }, p.blurb),
      el("ul", {}, p.now.map(x => el("li", {}, icon("check"), x))),
      el("div", { style: "margin-top:16px;display:flex;gap:10px;justify-content:center" },
        el("button", { class: "btn", onclick: () => go("dashboard") }, icon("home"), "Back to dashboard"),
        el("button", { class: "btn", onclick: () => window.open("/docs/design/new-ui-mockup.html") },
          icon("book"), "Mockup reference"),
      ),
    ),
    el("div", { class: "phwrap" },
      el("div", { class: "card phcard" }, el("h4", {}, icon("dna"), "In this phase"), el("p", {}, p.now.join(" · "))),
      el("div", { class: "card phcard" }, el("h4", {}, icon("clock"), "Then"), el("p", {}, p.later.join(" · "))),
    ),
    el("div", { class: "footnote" }, p.ctx),
  );
}

export function renderCtxPhase(view, ctx) {
  const p = PHASES[view] || {};
  ctx.append(
    el("div", { class: "ctxpanel", "data-view": view },
      el("div", { class: "chead" }, "Phase"),
      el("div", { class: "ctxrow", style: "cursor:default" },
        el("span", { class: "ic mut" }, "→"), `${p.phase || "—"} · ${view} roadmap`),
      el("div", { class: "kv" }, el("span", { class: "k" }, "Mockup"), el("span", { class: "v" }, `docs/design/new-ui-mockup.html`)),
      el("div", { class: "kv" }, el("span", { class: "k" }, "Shell"), el("span", { class: "v" }, "live")),
    ),
  );
}
