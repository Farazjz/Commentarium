/* Chat — grounded Q&A over the corpus (P6).
   Sessions in the context panel; bubbles + live sources panel here. */
import { api } from "../api.js";
import { store } from "../store.js";
import { el, icon, rel, toast } from "../ui.js";
import { go } from "../router.js";

let opts = { distributed: false, style: "apa" };
let lastSources = [];   // rich sources of the latest answer (right panel)
let docMap = null;      // document_id -> {filename, title, page_count}
let busy = false;

const parseJson = (s) => { try { return JSON.parse(s) || []; } catch { return []; } };

async function ensureDocMap() {
  if (docMap) return docMap;
  const docs = await api.get("/documents").catch(() => []);
  docMap = Object.fromEntries((docs || []).map((d) => [d.id, d]));
  return docMap;
}

export function invalidateSession() {
  store.set("chatSession", null);
  lastSources = [];
}

/* ---------- message bubbles ---------- */
function userMsg(text) {
  return el("div", { class: "msg user" }, text);
}

function aiMsg(m, meta) {
  const box = el("div", { class: "msg ai" });
  box.append(el("div", {}, m.content || ""));
  const refs = meta?.references || [];
  if (refs.length) {
    box.append(el("div", { class: "refs" },
      el("b", {}, `references (${refs.length}) · ${(meta?.citation_style || "apa").toUpperCase()}`),
      el("ol", {}, ...refs.map((r) => el("li", {}, r.text)))));
  }
  return box;
}

function typingMsg() {
  return el("div", { class: "msg ai", id: "typingBubble" },
    el("div", { class: "typing" }, el("i"), el("i"), el("i")));
}

/* ---------- sources panel ---------- */
function srcPanel() {
  const panel = el("div", { class: "card srcpanel" });
  panel.append(el("div", { class: "ct", style: "padding:14px 14px 9px;margin:0" },
    el("h3", {}, "Sources"),
    el("span", { class: "hint" }, lastSources.length ? `${lastSources.length} used` : "none yet")));
  const list = el("div", { class: "srclist", style: "padding:2px 12px 12px" });
  if (!lastSources.length) {
    list.append(el("div", { class: "muted", style: "font-size:12px;padding:6px 2px" },
      "Sources used by the latest answer will appear here — with page numbers and a text snippet."));
  }
  for (const s of lastSources) {
    const d = (docMap || {})[s.document_id];
    list.append(el("div", { class: "src" },
      el("div", { class: "st" },
        el("span", {}, (d && (d.title || d.filename)) || s.document_id),
        el("span", { class: "sp" }, `p.${s.page}`)),
      s.section ? el("div", { class: "countchip" }, s.section) : el("span"),
      s.text ? el("div", { class: "sx" }, s.text) : el("span")));
  }
  panel.append(list);
  return panel;
}

/* ---------- ask flow ---------- */
async function send(textarea, msgsEl, redraw) {
  const q = textarea.value.trim();
  if (!q || busy) return;
  busy = true;
  textarea.value = "";
  lastSources = [];

  let sid = store.get("chatSession");
  try {
    const project = store.get("chatProject") ?? store.get("libProject") ?? "";
    if (!sid) {
      const s = await api.post(`/chat/sessions?project_id=${encodeURIComponent(project)}&title=${encodeURIComponent(q.slice(0, 48))}`);
      sid = s.id;
      store.set("chatSession", sid);
    }
    msgsEl.append(userMsg(q), typingMsg());
    msgsEl.scrollTop = msgsEl.scrollHeight;
    textarea.focus();

    const res = await api.post("/chat/ask", {
      question: q,
      session_id: sid,
      project_id: store.get("chatProject") ?? store.get("libProject") ?? undefined,
      distributed: opts.distributed,
      style: opts.style,
    });
    document.querySelector("#typingBubble")?.remove();
    msgsEl.append(aiMsg({ content: res.answer }, res));
    msgsEl.scrollTop = msgsEl.scrollHeight;
    lastSources = res.sources || [];
    await ensureDocMap();
    redraw();
    await api.get("/chat/sessions").then((s) => store.set("sessions", s)).catch(() => null);
  } catch (e) {
    document.querySelector("#typingBubble")?.remove();
    msgsEl.append(el("div", { class: "msg ai", style: "border-color:color-mix(in srgb,var(--err) 40%,transparent)" },
      el("b", { style: "color:var(--err)" }, "Ask failed: "), e.message));
    msgsEl.scrollTop = msgsEl.scrollHeight;
  } finally {
    busy = false;
  }
}

export async function render(root) {
  await ensureDocMap();
  const sessions = await api.get("/chat/sessions").catch(() => []);
  if (!sessions.some((s) => s.id === store.get("chatSession"))) store.set("chatSession", null);
  store.set("sessions", sessions);

  root.append(el("div", { class: "vhead" },
    el("h2", {}, "Chat"),
    el("span", { class: "vhsub" }, "grounded in your library · cites pages"),
    el("div", { class: "right" },
      el("button", { class: "btn sm", onclick: () => { store.set("chatSession", null); lastSources = []; go("chat"); } },
        icon("plus"), "New chat"))));

  opts.distributed = opts.distributed ?? false;
  const segMode = el("div", { class: "seg" },
    el("button", { class: opts.distributed ? "" : "on", onclick: () => { opts.distributed = false; go("chat"); } }, "Specific facts"),
    el("button", { class: opts.distributed ? "on" : "", onclick: () => { opts.distributed = true; go("chat"); } }, "Distributed"));
  const segStyle = el("div", { class: "seg" },
    el("button", { class: opts.style === "apa" ? "on" : "", onclick: () => { opts.style = "apa"; go("chat"); } }, "APA 7"),
    el("button", { class: opts.style === "vancouver" ? "on" : "", onclick: () => { opts.style = "vancouver"; go("chat"); } }, "Vancouver"));

  const ta = el("textarea", { placeholder: "Ask your library… (Enter to send, Shift+Enter for a new line)" });
  const sendBtn = el("button", { class: "btn primary", onclick: () => send(ta, msgs, draw) }, icon("send"), "Ask");
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(ta, msgs, draw); }
  });

  const msgs = el("div", { class: "card msgs" });
  const draw = () => {
    const bar = document.querySelector("#srcHolder");
    if (bar) { bar.textContent = ""; bar.append(srcPanel()); }
  };

  root.append(el("div", { class: "chatopts" },
    el("span", { class: "countchip" }, "project"),
    (() => {
      const projects = store.get("projects") || [];
      const cur = store.get("chatProject") ?? store.get("libProject") ?? "";
      const sel = el("select", { onchange: (e) => {
        store.set("chatProject", e.target.value);
        store.set("chatSession", null);
        lastSources = [];
        go("chat");
      } },
        el("option", { value: "" }, "All projects"),
        ...projects.map((p) => el("option", { value: p.id, ...(p.id === cur ? { selected: "" } : {}) }, p.name || "(unnamed)")));
      return sel;
    })(),
    el("span", { class: "countchip" }, "mode"), segMode,
    el("span", { class: "countchip" }, "style"), segStyle));

  const grid = el("div", { class: "chatgrid" });
  const chatcol = el("div", { class: "chatcol" });
  const srcHolder = el("div", { id: "srcHolder" });
  grid.append(chatcol, srcHolder);
  root.append(grid);

  chatcol.append(msgs,
    el("div", { class: "inputbar" }, ta, sendBtn),
    el("div", { class: "hint" }, "Answers are grounded in retrieved chunks · citations resolve in your chosen style"));
  srcHolder.append(srcPanel());

  /* ---- history ---- */
  const sid = store.get("chatSession");
  if (!sid) {
    msgs.append(el("div", { class: "empty", style: "margin:auto" },
      el("div", { class: "eic" }, icon("chat")),
      el("h3", {}, "Start a conversation"),
      el("p", {}, "Every answer is retrieved from your indexed papers and cites the pages it used. Create a new chat or pick a session from the left.")));
  } else {
    const history = await api.get(`/chat/sessions/${encodeURIComponent(sid)}/messages`).catch(() => []);
    for (const m of history || []) {
      if (m.role === "assistant") {
        /* stored text still carries [SRC:doc|p12] markers — strip for display */
        const clean = String(m.content || "").replace(/\[SRC:[A-Za-z0-9_-]+\|p\d+\]/g, "");
        msgs.append(aiMsg({ content: clean }, null));
      } else {
        msgs.append(userMsg(m.content));
      }
    }
    /* panel: use latest assistant message sources */
    const lastSrc = (history || []).filter((m) => m.role === "assistant").slice(-1)[0];
    const srcs = lastSrc ? parseJson(lastSrc.sources) : [];
    lastSources = srcs.map((s) => ({ document_id: s.document_id, page: s.page }));
    if (lastSources.length) draw();
  }

  draw();
  ta.focus();
}

/* ---------- context panel: sessions ---------- */
export function renderCtx(ctxP) {
  const scope = store.get("chatProject") ?? store.get("libProject") ?? "";
  const sessions = (store.get("sessions") || []).filter((s) => !scope || s.project_id === scope);
  const sid = store.get("chatSession");
  const panel = el("div", { class: "ctxpanel", "data-view": "chat" });
  panel.append(
    el("div", { class: "chead" }, "Chat threads"),
    scope ? el("div", { class: "mini", style: "padding:2px 8px;color:var(--faint)" },
      ((store.get("projects") || []).find((p) => p.id === scope) || {}).name || scope) : el("span"),
    el("div", {
      class: `ctxrow ${!sid ? "on" : ""}`,
      onclick: () => { store.set("chatSession", null); lastSources = []; go("chat"); },
    }, el("span", { class: "ic mut" }, "+"), "New chat"),
  );
  if (!sessions.length) {
    panel.append(el("div", { class: "muted", style: "font-size:12px;padding:6px 8px" },
      "No conversations yet."));
  }
  for (const s of sessions) {
    const row = el("div", { class: `ctxrow ${sid === s.id ? "on" : ""}`,
      onclick: () => { store.set("chatSession", s.id); lastSources = []; go("chat"); } },
      el("span", { class: "ic mut" }, "·"),
      el("span", { style: "overflow:hidden;text-overflow:ellipsis;white-space:nowrap" },
        s.title || "Untitled"),
      el("span", { class: "mini" }, rel(s.updated_at)));
    row.append(
      el("span", { class: "ibtn", title: "Rename", onclick: (e) => {
        e.stopPropagation();
        renameModal(s);
      } }, icon("edit")),
      el("span", { class: "ibtn", title: "Delete", onclick: async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete chat “${s.title || "Untitled"}”?`)) return;
        try {
          await api.del(`/chat/sessions/${encodeURIComponent(s.id)}`);
          if (store.get("chatSession") === s.id) store.set("chatSession", null);
          toast("Chat deleted", "ok");
          const fresh = await api.get("/chat/sessions").catch(() => []);
          store.set("sessions", fresh);
        } catch (err) { toast(err.message, "err"); }
      } }, icon("trash")),
    );
    panel.append(row);
  }
  ctxP.append(panel);
}

function renameModal(s) {
  const inp = el("input", { type: "text", value: s.title || "", maxlength: "60" });
  const ov = el("div", { class: "modalov open" });
  const modal = el("div", { class: "modal" },
    el("h3", {}, "Rename chat"),
    el("div", { class: "field" }, el("label", {}, "Title"), inp),
    el("div", { class: "mrow" },
      el("button", { class: "btn", onclick: () => ov.remove() }, "Cancel"),
      el("button", {
        class: "btn primary",
        onclick: async () => {
          const t = inp.value.trim() || "Untitled";
          try {
            await api.patch(`/chat/sessions/${encodeURIComponent(s.id)}`, { title: t });
            ov.remove();
            const fresh = await api.get("/chat/sessions").catch(() => []);
            store.set("sessions", fresh);
            toast("Renamed", "ok");
          } catch (e) { toast(e.message, "err"); }
        },
      }, "Save")));
  ov.append(modal);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  document.body.append(ov);
  inp.focus();
}
