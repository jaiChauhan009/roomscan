// API base URL. Order of precedence:
//   1. ?api=https://host:port in the page URL (also saved to localStorage)
//   2. localStorage "roomscan.api"
//   3. window.ROOMSCAN_API, set by env.js (the one file to edit for a deployment)
//   4. DEFAULT_API below
// Use ?api=reset to forget a saved value.
export const DEFAULT_API = "http://localhost:8000";

const KEY = "roomscan.api";

function safeGet(k) {
  try { return localStorage.getItem(k); } catch { return null; }
}
function safeSet(k, v) {
  try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ }
}

// The deployment's API origin from env.js, when it is loaded and looks like a URL.
export function deployedApi() {
  const v = typeof window !== "undefined" ? window.ROOMSCAN_API : undefined;
  return (typeof v === "string" && /^https?:\/\//.test(v) ? v : DEFAULT_API).replace(/\/+$/, "");
}

export function resolveApiBase(search = (typeof location !== "undefined" ? location.search : "")) {
  const q = new URLSearchParams(search).get("api");
  if (q === "reset") { safeSet(KEY, null); return deployedApi(); }
  if (q) {
    const v = q.replace(/\/+$/, "");
    safeSet(KEY, v);
    return v;
  }
  return (safeGet(KEY) || deployedApi()).replace(/\/+$/, "");
}

export function setApiBase(v) {
  safeSet(KEY, v ? v.replace(/\/+$/, "") : null);
}

export const API_BASE = resolveApiBase();
