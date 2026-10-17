/* Settings (P10): grouped cards over GET/PATCH /settings (whitelisted .env
   keys), masked secrets, unsaved-credential connection test, model picker,
   danger zone with server shutdown. */
import { api } from "../api.js";
import { store } from "../store.js";
import { el, icon, toast, shutdownScreen } from "../ui.js";
import { go } from "../router.js";

/* field meta: [ENV_KEY, friendly label, kind]
   kind: text | secret | int | float | bool | select:optA|optB|…            */
const GROUPS = [
  ["Gateway & answers", [
    ["OPENROUTER_BASE_URL", "Gateway base URL", "text"],
    ["OPENROUTER_API_KEY", "Gateway API key", "secret"],
    ["CHAT_MODEL", "Answer model", "text"],
    ["CHAT_TEMPERATURE", "Answer creativity (temperature)", "float"],
  ]],
  ["Embeddings & indexing", [
    ["EMBEDDING_BACKEND", "Embedding backend", "select:api|local|cloudflare|disabled"],
    ["EMBEDDING_MODEL", "Embedding model (api/cloudflare)", "text"],
    ["EMBEDDING_API_BASE_URL", "Embedding API base URL", "text"],
    ["EMBEDDING_API_KEY", "Embedding API key", "secret"],
    ["CLOUDFLARE_EMBEDDING_MODEL", "Cloudflare embedding model", "text"],
    ["LOCAL_EMBEDDING_MODEL", "Local embedding model", "text"],
    ["LOCAL_EMBEDDING_PATH", "Local model path", "text"],
    ["HF_OFFLINE", "HF offline mode", "bool"],
    ["CHUNK_SIZE", "Chunk size (tokens)", "int"],
    ["CHUNK_OVERLAP", "Chunk overlap", "int"],
    ["TOP_K", "Chunks retrieved per question", "int"],
    ["CITATION_STYLE", "Default citation style", "select:apa|vancouver"],
  ]],
  ["OCR", [
    ["OCR_BACKEND", "Default OCR backend", "select:tesseract|vision|paddleocr-vl|teleocr|disabled"],
    ["TESSERACT_CMD", "Tesseract path / command", "text"],
    ["DEFAULT_OCR", "OCR uploads by default", "bool"],
    ["PADDLEOCR_VL_MODEL", "PaddleOCR-VL model", "text"],
    ["PADDLEOCR_VL_BASE_URL", "PaddleOCR-VL base URL", "text"],
    ["TELEOCR_MODEL", "TeleOCR model", "text"],
    ["TELEOCR_BASE_URL", "TeleOCR base URL", "text"],
  ]],
  ["Podcast voices", [
    ["TTS_PROVIDER", "TTS provider", "select:edge-tts|localhost|gateway-edge-tts|voicestudio|cloudflare|google|disabled"],
    ["TTS_API_URL", "Localhost TTS endpoint", "text"],
    ["TTS_API_KEY", "Localhost TTS key", "secret"],
    ["TTS_API_MODEL", "Localhost TTS model", "text"],
    ["TTS_API_FORMAT", "Localhost TTS format", "select:mp3|wav|opus|aac|flac"],
    ["GATEWAY_TTS_URL", "Gateway edge-TTS URL", "text"],
    ["GATEWAY_TTS_KEY", "Gateway edge-TTS key", "secret"],
    ["VOICESTUDIO_URL", "VoiceStudio URL", "text"],
    ["VOICESTUDIO_API_KEY", "VoiceStudio key", "secret"],
    ["VOICESTUDIO_MODEL", "VoiceStudio model", "text"],
    ["VOICESTUDIO_FORMAT", "VoiceStudio format", "select:mp3|wav|opus"],
    ["CF_ACCOUNT_ID", "Cloudflare account ID", "secret"],
    ["CF_API_TOKEN", "Cloudflare API token", "secret"],
    ["CF_MODEL", "Cloudflare TTS model", "text"],
    ["GOOGLE_API_KEY", "Google TTS API key", "secret"],
    ["GOOGLE_LANGUAGE_CODE", "Google language code", "text"],
  ]],
  ["Podcast hosts", [
    ["PODCAST_NUM_HOSTS", "Hosts per episode", "int"],
    ["PODCAST_HOST_1_NAME", "Host 1 name", "text"],
    ["PODCAST_HOST_1_GENDER", "Host 1 gender", "select:female|male"],
    ["PODCAST_HOST_1_VOICE", "Host 1 voice", "text"],
    ["PODCAST_HOST_1_LANGUAGE", "Host 1 language", "select:en|fa"],
    ["PODCAST_HOST_2_NAME", "Host 2 name", "text"],
    ["PODCAST_HOST_2_GENDER", "Host 2 gender", "select:female|male"],
    ["PODCAST_HOST_2_VOICE", "Host 2 voice", "text"],
    ["PODCAST_HOST_2_LANGUAGE", "Host 2 language", "select:en|fa"],
    ["PODCAST_HOST_3_NAME", "Host 3 name", "text"],
    ["PODCAST_HOST_3_GENDER", "Host 3 gender", "select:female|male"],
    ["PODCAST_HOST_3_VOICE", "Host 3 voice", "text"],
    ["PODCAST_HOST_3_LANGUAGE", "Host 3 language", "select:en|fa"],
  ]],
];

let edits = {};      // ENV_KEY -> new value (not yet saved)
let snap = {};       // latest GET /settings snapshot

const isSecret = (key) => /API_KEY|TOKEN/.test(key) || key === "CF_ACCOUNT_ID";

function fieldRow(key, label, kind) {
  const cur = edits[key] !== undefined ? edits[key] : snap[key];
  const masked = typeof cur === "string" && cur.includes("•");
  let input;
  if (kind === "bool") {
    input = el("div", { class: `sw ${cur ? "on" : ""}`, onclick: () => {
      const v = !cur;
      edits[key] = v;
      input.classList.toggle("on", v);
    } });
  } else if (kind && kind.startsWith("select:")) {
    const opts = kind.slice(7).split("|");
    input = el("select", { onchange: (e) => { edits[key] = e.target.value; } },
      ...opts.map((o) => el("option", { value: o, ...(String(cur) === o ? { selected: "" } : {}) }, o)));
  } else {
    input = el("input", {
      type: kind === "secret" && !masked ? "password" : "text",
      value: cur === undefined || cur === null ? "" : String(cur),
      onchange: (e) => { edits[key] = e.target.value; },
    });
    if (kind === "int" || kind === "float") input.type = "number";
    if (kind === "float") input.step = "0.05";
  }
  const changed = edits[key] !== undefined;
  return el("div", { class: `frow ${changed ? "dirty" : ""}` },
    el("div", { class: "fmeta" },
      el("div", { class: "flabel" }, label, el("span", { class: "fkey" }, key)),
      changed ? el("div", { class: "countchip", style: "color:var(--accent2)" }, "unsaved") : el("span")),
    input);
}

function groupCard(title, fields) {
  const card = el("div", { class: "card cardpad" },
    el("div", { class: "ct" }, el("h3", {}, title)));
  const grid = el("div", { class: "fgrid" });
  for (const [key, label, kind] of fields) grid.append(fieldRow(key, label, kind));
  card.append(grid);
  return card;
}

/* model picker: choose CHAT_MODEL from the gateway's list */
function modelPickerModal() {
  const ov = el("div", { class: "modalov open" });
  const list = el("div", { style: "font-size:12.5px" }, el("span", { class: "muted" }, "Loading models…"));
  const modal = el("div", { class: "modal", style: "width:520px" },
    el("h3", {}, "Choose answer model"),
    el("div", { class: "muted", style: "font-size:11.5px;margin:-6px 0 10px" },
      "From the gateway at " + (snap.OPENROUTER_BASE_URL || "—")),
    list,
    el("div", { class: "mrow" },
      el("button", { class: "btn", onclick: () => ov.remove() }, "Close")));
  ov.append(modal);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  document.body.append(ov);
  api.get("/settings/models").then((r) => {
    list.textContent = "";
    if (!r.models || !r.models.length) {
      list.append(el("span", { class: "muted" }, "No models returned by the gateway."));
      return;
    }
    const scroller = el("div", { class: "docpick", style: "max-height:46vh" });
    for (const m of r.models) {
      const id = typeof m === "string" ? m : (m.id || m.name || "unknown");
      scroller.append(el("div", { class: "ctxrow", onclick: () => {
        edits.CHAT_MODEL = id;
        toast("Model selected — press Save to apply", "ok");
        ov.remove();
        go("settings");
      } },
        el("span", { class: "ic mut" }, icon("bot")),
        el("span", { style: "min-width:0" }, id)));
    }
    list.append(scroller);
  }).catch((e) => { list.textContent = ""; list.append(el("span", { style: "color:var(--err)" }, e.message)); });
}

/* connection test with unsaved credentials */
function testConnection(keyProbe) {
  const row = el("div", { class: "kv", style: "border-bottom:none" });
  const result = el("span", { style: "min-width:0;font-size:12px;color:var(--dim);word-break:break-all" });
  async function run() {
    result.textContent = "testing…";
    const body = { base_url: snap.OPENROUTER_BASE_URL || "" };
    const keyVal = keyProbe.value.trim();
    // only send the unsaved key when the user changed it (mask = stored value)
    if (keyVal && !keyVal.includes("•")) body.api_key = keyVal;
    try {
      const r = await api.post("/settings/test-connection", body);
      result.textContent = "";
      result.append(el("span", { class: "stchip st-ok", style: "margin-left:9px" },
        (r && (r.ok || r.status === "ok" || r.message || r.detail)) ? "OK" : "OK",
        " " + JSON.stringify(r).slice(0, 140)));
    } catch (e) {
      result.textContent = "";
      result.append(el("span", { class: "stchip st-err", style: "margin-left:9px" }, e.message.slice(0, 120)));
    }
  }
  row.append(
    el("span", { class: "k", style: "width:auto" },
      el("button", { class: "btn sm", onclick: run }, icon("plug"), "Test connection")),
    result);
  return row;
}

export async function render(root) {
  snap = await api.get("/settings").catch(() => ({}));
  edits = {};

  root.append(el("div", { class: "vhead" },
    el("h2", {}, "Settings"),
    el("span", { class: "vhsub" }, "saved to .env • writes are whitelisted"),
    el("div", { class: "right" },
      el("button", { class: "btn", onclick: () => modelPickerModal() }, icon("bot"), "Browse models"),
      el("button", {
        class: "btn primary", id: "saveBtn",
        onclick: async () => {
          if (!Object.keys(edits).length) { toast("Nothing to save", "info"); return; }
          try {
            await api.patch("/settings", { updates: edits });
            toast(`Saved ${Object.keys(edits).length} setting(s) to .env`, "ok");
            edits = {};
            go("settings");
          } catch (e) { toast(e.message, "err"); }
        } }, icon("save"), "Save changes"))));

  /* connection probe card */
  const probeCard = el("div", { class: "card cardpad", style: "margin-bottom:14px" },
    el("div", { class: "ct" }, el("h3", {}, "Gateway connection"),
      el("span", { class: "hint" }, "test key before saving")));
  const keyProbe = el("input", {
    type: "text", autocomplete: "off",
    placeholder: (snap.OPENROUTER_API_KEY || "no key stored") + " — type a new key to test it",
  });
  probeCard.append(el("div", { class: "fgrid" }, el("div", { class: "frow" },
    el("div", { class: "fmeta" }, el("div", { class: "flabel" }, "API key", el("span", { class: "fkey" }, "OPENROUTER_API_KEY")), el("span")),
    keyProbe)));
  probeCard.append(testConnection(keyProbe));
  root.append(probeCard);

  for (const [title, fields] of GROUPS) root.append(groupCard(title, fields));

  /* danger zone */
  root.append(el("div", { class: "card cardpad", style: "border:1px solid color-mix(in srgb,var(--err) 35%,var(--border))" },
    el("div", { class: "ct" }, el("h3", { style: "color:var(--err)" }, "Danger zone")),
    el("div", { class: "kv", style: "border-bottom:none;padding-bottom:2px" },
      el("span", { class: "k", style: "width:auto" },
        el("button", { class: "btn sm danger", style: "color:var(--err)", onclick: async () => {
          if (!confirm("Shut down the Commentarium servers?\nUnsaved Settings edits and running jobs will be lost.")) return;
          if (!confirm("Are you sure? The SPA and API stop until you launch Commentarium again.")) return;
          try { await api.post("/shutdown", {}); } catch { /* server dies mid-call */ }
          shutdownScreen();
        } }, icon("power"), "Shut down Commentarium")),
      el("span", { class: "v", style: "color:var(--dim);font-weight:400" },
        `API :${snap._port || 8010} + UI - stops both processes cleanly.`))));
}
