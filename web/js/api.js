// Thin client for the roomscan API. Every call throws ApiError on failure.
import { API_BASE } from "../config.js";

export class ApiError extends Error {
  constructor(message, { status = 0, detail = null, network = false } = {}) {
    super(message);
    this.status = status;       // 0 = no HTTP response (network down, CORS, DNS)
    this.detail = detail;       // parsed JSON body, if any
    this.network = network;
  }
}

const enc = encodeURIComponent;
// The whole-home capture uses the same upload queue as rooms, under this pseudo space id.
export const CAPTURE = "_capture";
const filesPath = (pid, sid) => sid === CAPTURE ? `/api/projects/${enc(pid)}/capture/files` : `/api/projects/${enc(pid)}/spaces/${enc(sid)}/files`;

export function url(path) {
  if (/^https?:\/\//i.test(path)) return path;
  return API_BASE + (path.startsWith("/") ? path : "/" + path);
}

function detailText(body) {
  if (!body) return "";
  if (typeof body === "string") return body;
  const d = body.detail ?? body.error ?? body.message;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
  if (d && typeof d === "object") return d.message || JSON.stringify(d);
  return "";
}

async function req(method, path, body, { timeout = 30000 } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(url(path), {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
  } catch (e) {
    throw new ApiError(
      e.name === "AbortError" ? "The server took too long to answer." : "Cannot reach the server. Check your connection or the API address.",
      { network: true },
    );
  } finally {
    clearTimeout(t);
  }
  const text = await res.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch { data = text; } }
  if (!res.ok) {
    const msg = detailText(data) || `${res.status} ${res.statusText}`;
    throw new ApiError(msg, { status: res.status, detail: data });
  }
  return data;
}

export const api = {
  health: () => req("GET", "/api/health", undefined, { timeout: 8000 }),
  createProject: () => req("POST", "/api/projects", {}),
  getProject: (pid) => req("GET", `/api/projects/${enc(pid)}`),
  createSpace: (pid, body) => req("POST", `/api/projects/${enc(pid)}/spaces`, body),
  patchSpace: (pid, sid, body) => req("PATCH", `/api/projects/${enc(pid)}/spaces/${enc(sid)}`, body),
  deleteSpace: (pid, sid) => req("DELETE", `/api/projects/${enc(pid)}/spaces/${enc(sid)}`),
  setOrder: (pid, spaceIds) => req("PUT", `/api/projects/${enc(pid)}/order`, { space_ids: spaceIds }),
  listFiles: (pid, sid) => req("GET", filesPath(pid, sid)),
  deleteFile: (pid, sid, sha) => req("DELETE", `${filesPath(pid, sid)}/${enc(sha)}`),
  setCapture: (pid, kind) => req("PUT", `/api/projects/${enc(pid)}/capture`, { kind }),
  deleteCapture: (pid) => req("DELETE", `/api/projects/${enc(pid)}/capture`),
  verify: (pid) => req("POST", `/api/projects/${enc(pid)}/verify`, {}, { timeout: 120000 }),
  run: (pid, force = false) => req("POST", `/api/projects/${enc(pid)}/run`, force ? { damage: true, force: true } : { damage: true }),
  job: (jid) => req("GET", `/api/jobs/${enc(jid)}`),
  comparison: (jid) => req("GET", `/api/jobs/${enc(jid)}/comparison`),
  jobFileUrl: (jid, name) => url(`/api/jobs/${enc(jid)}/files/${enc(name)}`),
};

// Multipart PUT with upload progress (fetch has no upload progress).
export function uploadFile(pid, sid, { blob, name, sha256 }, onProgress, signal) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url(filesPath(pid, sid)));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { data = xhr.responseText; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(detailText(data) || `Upload failed (${xhr.status})`, { status: xhr.status, detail: data }));
    };
    xhr.onerror = () => reject(new ApiError("Upload interrupted: network error.", { network: true }));
    xhr.ontimeout = () => reject(new ApiError("Upload timed out.", { network: true }));
    if (signal) signal.addEventListener("abort", () => xhr.abort());
    xhr.onabort = () => reject(new ApiError("Upload cancelled.", { network: true }));
    const fd = new FormData();
    fd.append("sha256", sha256);
    fd.append("name", name);
    fd.append("file", blob, name);
    xhr.send(fd);
  });
}
