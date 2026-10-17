/* Minimal shared state with pub/sub — keeps topbar chips, ctx panel and
   views in sync without a framework. */
const listeners = new Map();

export const store = {
  data: {
    view: "dashboard",
    /* shell payloads, loaded by app.js at boot */
    health: null,          /* /health            */
    indexStats: null,      /* /index/stats       */
    summary: null,         /* /settings/summary  */
    settings: null,        /* /settings (full)   */
    projects: [],          /* /projects          */
    podcasts: [],          /* /podcasts          */
    sessions: [],          /* /chat/sessions     */
    chatSession: null,     /* selected chat id   */
    ingestQueue: [],       /* active ingest jobs */
    queueTimer: null,      /* poll handle        */
    apiOnline: false,      /* last health poll   */
  },
  get(k) { return this.data[k]; },
  set(key, value) {
    this.data[key] = value;
    for (const fn of listeners.get(key) || []) {
      try { fn(value); } catch (e) { console.error("store listener", e); }
    }
  },
  on(key, fn) {
    if (!listeners.has(key)) listeners.set(key, []);
    listeners.get(key).push(fn);
    return () => {
      const arr = listeners.get(key) || [];
      const i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    };
  },
};
