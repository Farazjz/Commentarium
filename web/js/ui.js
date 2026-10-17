/* Tiny DOM + UI helpers for the v2.0 SPA (no dependencies). */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const icon = (id, cls = "icon") => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("class", cls);
  const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
  u.setAttribute("href", `#i-${id}`);
  s.append(u);
  return s;
};

/* Full-page notice shown after the servers have been asked to shut down
   (used by the rail exit button, the dashboard quick action and the
   Settings danger zone). Replaces the app so fetch failures from the
   dying server don't leave a frozen page behind. */
export function shutdownScreen() {
  document.body.innerHTML =
    "<div style='display:grid;place-items:center;min-height:100vh;background:#0f0d15;" +
    "font-family:Inter,system-ui;color:#9a93a8;text-align:center'>" +
    "<div>Servers stopped — relaunch Commentarium to continue." +
    "<br><small style='opacity:.7'>You can close this tab.</small></div></div>";
}

export function toast(message, type = "info", ms = 4200) {
  let holder = document.querySelector(".toasts");
  if (!holder) {
    holder = el("div", { class: "toasts" });
    document.body.append(holder);
  }
  const kind = type === "ok" ? "check" : type === "err" ? "alert" : "dna";
  const t = el("div", { class: `toast ${type}` }, icon(kind), el("span", {}, message));
  holder.append(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; }, ms - 300);
  setTimeout(() => t.remove(), ms);
}

/* "@2026-10-17 03:41:30" | ISO -> "2 h ago"-style relative label */
export function rel(when) {
  if (!when) return "";
  const d = new Date(String(when).includes("T") ? when : String(when).replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return String(when);
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function fmtDur(sec) {
  const s = Math.round(Number(sec) || 0);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtBytes(n) {
  n = Number(n) || 0;
  if (n > 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n > 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}
