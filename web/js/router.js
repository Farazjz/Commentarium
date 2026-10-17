/* Hash router: #/dashboard, #/library, ... — collapsible rail + crumb + title. */
import { store } from "./store.js";

export const VIEWS = {
  dashboard: { title: "Dashboard", tip: "Dashboard" },
  library:   { title: "Library",   tip: "Library · Projects & files" },
  chat:      { title: "Chat",      tip: "Chat" },
  podcast:   { title: "Podcast studio", tip: "Podcast studio" },
  citations: { title: "Citations", tip: "Citations" },
  ocr:       { title: "OCR to Markdown", tip: "OCR to Markdown" },
  settings:  { title: "Settings",  tip: "Settings" },
  logs:      { title: "Log viewer", tip: "Log viewer" },
};

const RAIL_ORDER = ["dashboard", "library", "chat", "podcast", "citations", "ocr", "settings", "logs"];

export function currentView() {
  const hash = (location.hash || "").replace(/^#\/?/, "").toLowerCase();
  return VIEWS[hash] ? hash : "dashboard";
}

export function go(view) {
  if (!VIEWS[view]) view = "dashboard";
  if (currentView() === view) return render(view);
  location.hash = `#/${view}`;
}

let renderFn = null;

export function onViewRender(fn) {
  renderFn = fn;
}

export function render(view) {
  view = VIEWS[view] ? view : "dashboard";
  store.set("view", view);
  document.title = `Commentarium · ${VIEWS[view].title}`;
  document.querySelectorAll(".railbtn[data-view]").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view)
  );
  const crumb = document.querySelector("#crumbView");
  if (crumb) crumb.textContent = VIEWS[view].title;
  const content = document.querySelector(".content");
  if (content) content.scrollTop = 0;
  if (renderFn) renderFn(view);
}
