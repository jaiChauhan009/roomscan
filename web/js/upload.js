// Upload queue. Files live in IndexedDB until the server confirms them, so a reload,
// a closed tab or a dropped connection loses nothing: the queue resumes on next load.
//
// For each pending record: SHA-256 (stored), skip if the server already lists that hash
// for the space, else multipart PUT with progress. Network / 5xx / 429 errors retry with
// exponential backoff (1 s .. 60 s, woken early by the "online" event). Other 4xx errors
// park the record in state "error" until the user retries or removes it.
import { api, uploadFile, ApiError } from "./api.js";
import { sha256Blob } from "./sha256.js";
import { files } from "./store.js";

export class Uploader {
  constructor({ getPid, onFileUploaded, onChange }) {
    this.getPid = getPid;
    this.onFileUploaded = onFileUploaded; // (sid, fileRecord) => void
    this.onChange = onChange;             // () => void, re-render progress
    this.running = false;
    this.progress = new Map();            // sid -> {name, loaded, total, phase}
    this.backoff = 0;
    this.retryAt = 0;
    this.lastError = null;
    this._wake = null;
    this.server = new Map();              // sid -> Set(sha256) known on server
    this.paused = new Set();              // sids being deleted
    addEventListener("online", () => this.kick());
    document.addEventListener("visibilitychange", () => { if (!document.hidden) this.kick(); });
  }

  setServerFiles(sid, list) {
    this.server.set(sid, new Set((list || []).map((f) => f.sha256)));
  }

  kick() {
    if (this._wake) { const w = this._wake; this._wake = null; w(); }
    if (!this.running) this._loop();
  }

  _sleep(ms) {
    return new Promise((resolve) => {
      const t = setTimeout(() => { this._wake = null; resolve(); }, ms);
      this._wake = () => { clearTimeout(t); resolve(); };
    });
  }

  async _next() {
    const all = await files.all();
    return all
      .filter((r) => r.state === "pending" && r.blob && !this.paused.has(r.sid))
      .sort((a, b) => a.added - b.added)[0] || null;
  }

  async _loop() {
    this.running = true;
    try {
      for (;;) {
        const pid = this.getPid();
        if (!pid) break;
        const rec = await this._next();
        if (!rec) break;
        try {
          await this._one(pid, rec);
          this.backoff = 0;
          this.lastError = null;
          this.retryAt = 0;
        } catch (e) {
          const retryable = !(e instanceof ApiError) || e.network || e.status >= 500 || e.status === 429 || e.status === 408;
          this.progress.delete(rec.sid);
          if (retryable) {
            this.backoff = Math.min(this.backoff ? this.backoff * 2 : 1000, 60000);
            this.lastError = e.message;
            this.retryAt = Date.now() + this.backoff;
            this.onChange();
            await this._sleep(this.backoff);
            this.retryAt = 0;
          } else {
            await files.patch(rec.id, { state: "error", error: e.message || String(e) });
          }
        }
        this.onChange();
      }
    } finally {
      this.running = false;
      this.onChange();
    }
  }

  async _one(pid, rec) {
    const sid = rec.sid;
    const p = { name: rec.name, loaded: 0, total: rec.size, phase: "hash" };
    this.progress.set(sid, p);
    this.onChange();
    if (!rec.sha256) {
      rec.sha256 = await sha256Blob(rec.blob, (l, t) => { p.loaded = l; p.total = t; this.onChange(); });
      await files.patch(rec.id, { sha256: rec.sha256 });
    }
    if (!this.server.has(sid)) {
      const r = await api.listFiles(pid, sid);
      this.setServerFiles(sid, r && r.files);
    }
    if (this.paused.has(sid)) return;
    let fileRec;
    if (this.server.get(sid).has(rec.sha256)) {
      fileRec = { name: rec.name, sha256: rec.sha256, size: rec.size, _dup: true };
    } else {
      p.phase = "upload"; p.loaded = 0; p.total = rec.size;
      this.onChange();
      fileRec = await uploadFile(pid, sid, rec, (l, t) => { p.loaded = l; p.total = t; this.onChange(); });
      this.server.get(sid).add(rec.sha256);
    }
    await files.del(rec.id);
    this.progress.delete(sid);
    this.onFileUploaded(sid, fileRec && fileRec.sha256 ? fileRec : { name: rec.name, sha256: rec.sha256, size: rec.size });
  }
}
