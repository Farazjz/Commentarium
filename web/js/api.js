/* Fetch wrapper for the Commentarium API (same origin — no CORS needed).
   Throws ApiError with a readable message; the app shows toasts on failure. */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new ApiError("API unreachable — is the Commentarium API running?", 0);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      msg = body.error || body.detail || msg;
    } catch { /* non-JSON error body */ }
    throw new ApiError(msg, res.status);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  get: (path) => request(path),
  post: (path, body) =>
    request(path, {
      method: "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: (path, body) =>
    request(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  del: (path) => request(path, { method: "DELETE" }),
};

export const loadAll = async (...calls) => {
  const results = await Promise.allSettled(calls.map(([path]) => api.get(path)));
  const errors = results.filter(r => r.status === "rejected");
  if (errors.length && errors[0].reason) {
    // surface the first failure as a toast, but never block the view
    import("./ui.js").then(({ toast }) => toast(errors[0].reason.message, "err"));
  }
  return results.map(r => (r.status === "fulfilled" ? r.value : null));
};
