// Local persistence.
//  - IndexedDB "roomscan" / store "files": one record per picked file, holding the Blob
//    until the server has confirmed it (then the Blob is dropped, metadata kept).
//  - localStorage: small JSON state (project id, walk order, last verify, job id).

const DB_NAME = "roomscan";
const DB_VERSION = 1;
let dbp = null;

function openDb() {
  if (dbp) return dbp;
  dbp = new Promise((resolve, reject) => {
    if (!("indexedDB" in globalThis)) { reject(new Error("IndexedDB unavailable")); return; }
    const r = indexedDB.open(DB_NAME, DB_VERSION);
    r.onupgradeneeded = () => {
      const db = r.result;
      if (!db.objectStoreNames.contains("files")) {
        const s = db.createObjectStore("files", { keyPath: "id", autoIncrement: true });
        s.createIndex("sid", "sid");
      }
    };
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  return dbp;
}

function tx(mode, fn) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const t = db.transaction("files", mode);
    const s = t.objectStore("files");
    let out;
    Promise.resolve(fn(s)).then((v) => { out = v; });
    t.oncomplete = () => resolve(out);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error || new Error("IndexedDB transaction aborted (storage full?)"));
  }));
}

const reqP = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });

export const files = {
  async add(pid, sid, file) {
    const rec = {
      pid, sid,
      name: file.webkitRelativePath || file.name,
      size: file.size,
      type: file.type || "",
      lastModified: file.lastModified || 0,
      blob: file,
      sha256: null,
      state: "pending",
      error: null,
      added: Date.now(),
    };
    const id = await tx("readwrite", (s) => reqP(s.add(rec)));
    return { ...rec, id };
  },
  get: (id) => tx("readonly", (s) => reqP(s.get(id))),
  put: (rec) => tx("readwrite", (s) => reqP(s.put(rec))),
  del: (id) => tx("readwrite", (s) => reqP(s.delete(id))),
  all: () => tx("readonly", (s) => reqP(s.getAll())),
  bySpace: (sid) => tx("readonly", (s) => reqP(s.index("sid").getAll(sid))),
  async delSpace(sid) {
    const recs = await files.bySpace(sid);
    await tx("readwrite", (s) => { recs.forEach((r) => s.delete(r.id)); });
  },
  async patch(id, fields) {
    // Callback style (no await) so the put is issued while the transaction is active.
    return tx("readwrite", (s) => new Promise((resolve) => {
      const g = s.get(id);
      g.onsuccess = () => {
        const r = g.result;
        if (!r) { resolve(null); return; }
        Object.assign(r, fields);
        s.put(r);
        resolve(r);
      };
    }));
  },
};

// ---- small JSON state in localStorage ----
const P = "roomscan.";
export const kv = {
  get(k, dflt = null) {
    try { const v = localStorage.getItem(P + k); return v == null ? dflt : JSON.parse(v); } catch { return dflt; }
  },
  set(k, v) {
    try { v == null ? localStorage.removeItem(P + k) : localStorage.setItem(P + k, JSON.stringify(v)); } catch { /* ignore */ }
  },
};

export async function requestPersistence() {
  try { if (navigator.storage && navigator.storage.persist) await navigator.storage.persist(); } catch { /* ignore */ }
}
