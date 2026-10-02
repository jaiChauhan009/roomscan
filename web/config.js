// API base URL. Order of precedence:
//   1. ?api=https://host:port in the page URL (also saved to localStorage)
//   2. localStorage "roomscan.api"
//   3. DEFAULT_API below
// Use ?api=reset to forget a saved value.
export const DEFAULT_API = "http://localhost:8000";

const KEY = "roomscan.api";

function safeGet(k) {
  try { return localStorage.getItem(k); } catch { return null; }
}
function safeSet(k, v) {
  try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ }
}

export function resolveApiBase(search = (typeof location !== "undefined" ? location.search : "")) {
  const q = new URLSearchParams(search).get("api");
  if (q === "reset") { safeSet(KEY, null); return DEFAULT_API; }
  if (q) {
    const v = q.replace(/\/+$/, "");
    safeSet(KEY, v);
    return v;
  }
  return (safeGet(KEY) || DEFAULT_API).replace(/\/+$/, "");
}

export function setApiBase(v) {
  safeSet(KEY, v ? v.replace(/\/+$/, "") : null);
}

export const API_BASE = resolveApiBase();
