// roomscan web app: project, spaces, uploads, verify, run, job polling, results.
import { API_BASE, setApiBase } from "../config.js";
import { api, ApiError } from "./api.js";
import { files, kv, requestPersistence } from "./store.js";
import { Uploader } from "./upload.js";
import { $, h, fmtBytes, plural } from "./dom.js";
import { renderJob, renderResults } from "./results.js";

const KIND = {
  photos: { label: "Photos", unit: ["photo", "photos"] },
  video: { label: "Video", unit: ["video", "videos"] },
  lidar: { label: "LiDAR scan", unit: ["scan", "scans"] },
};
const STATUS = { ok: "OK", warn: "Check", retake: "Retake" };
const IS_IOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const DIR_OK = !IS_IOS && "webkitdirectory" in document.createElement("input");

const state = {
  pid: null,
  spaces: [],          // server space objects, walk order
  local: new Map(),    // sid -> local IDB records not yet confirmed
  verify: null,        // {result, at, dirty}
  jid: null,
  apiUp: null,
};
const cards = new Map(); // sid -> {root, refs}

// ---------- banner / API status ----------
function banner(msg, { kind = "error", action } = {}) {
  const b = $("#banner");
  if (!msg) { b.hidden = true; b.replaceChildren(); return; }
  b.className = "banner" + (kind === "info" ? " info" : "");
  b.setAttribute("role", kind === "info" ? "status" : "alert");
  b.replaceChildren(h("span", {}, msg), action ? h("button", { type: "button", class: "btn small", onclick: action.fn }, action.label) : null);
  b.hidden = false;
}

function explain(e) {
  if (e instanceof ApiError) {
    if (e.network) return `${e.message} (API: ${API_BASE})`;
    if (e.status === 404) return `Not found: ${e.message}`;
    if (e.status === 409) return `Check the captures first: ${e.message}`;
    if (e.status >= 500) return `The server had a problem (${e.status}): ${e.message}`;
    return e.message;
  }
  return e && e.message ? e.message : String(e);
}

async function checkHealth() {
  const pill = $("#api-status");
  try {
    await api.health();
    state.apiUp = true;
    pill.className = "pill up";
    pill.querySelector(".pill-text").textContent = "API online";
  } catch {
    state.apiUp = false;
    pill.className = "pill down";
    pill.querySelector(".pill-text").textContent = "API offline";
  }
  pill.title = `API ${API_BASE}: ${state.apiUp ? "online" : "unreachable"}. Click to check again.`;
  return state.apiUp;
}

// ---------- project ----------
async function loadProject() {
  let pid = kv.get("pid");
  try {
    if (pid) {
      try {
        applyProject(await api.getProject(pid));
        return;
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) throw e;
        banner("Your previous project was not found on the server, so a new one was started.", { kind: "info" });
      }
    }
    const r = await api.createProject();
    pid = r.project_id;
    kv.set("pid", pid);
    applyProject({ project_id: pid, spaces: [], last_job_id: null });
  } catch (e) {
    // Offline: show what we cached so files can still be queued.
    const cached = pid && kv.get("project." + pid);
    if (cached) applyProject(cached, { offline: true });
    banner(explain(e), { action: { label: "Retry", fn: () => { banner(null); init(); } } });
  }
}

function applyProject(p, { offline = false } = {}) {
  state.pid = p.project_id;
  $("#project-id").textContent = p.project_id;
  const order = kv.get("order." + state.pid, []);
  const pos = (s) => { const i = order.indexOf(s.space_id); return i < 0 ? 1e9 : i; };
  state.spaces = (p.spaces || []).map((s, i) => ({ ...s, _i: i })).sort((a, b) => pos(a) - pos(b) || a._i - b._i);
  state.spaces.forEach((s) => { delete s._i; uploader.setServerFiles(s.space_id, s.files); });
  saveProjectCache();
  state.verify = kv.get("verify." + state.pid);
  state.jid = kv.get("job." + state.pid) || p.last_job_id || null;
  if (!offline && state.jid) startPolling(state.jid);
}

function saveProjectCache() {
  kv.set("order." + state.pid, state.spaces.map((s) => s.space_id));
  kv.set("project." + state.pid, { project_id: state.pid, spaces: state.spaces });
}

async function newProject() {
  if (!confirm("Start a new, empty project? The current one stays on the server but this page will forget it.")) return;
  stopPolling();
  kv.set("pid", null);
  for (const s of state.spaces) await files.delSpace(s.space_id);
  location.reload();
}

// ---------- local files ----------
async function refreshLocal() {
  let all = [];
  try { all = await files.all(); } catch { /* no IDB */ }
  state.local = new Map();
  for (const r of all) {
    if (r.pid !== state.pid) continue;
    if (!state.local.has(r.sid)) state.local.set(r.sid, []);
    state.local.get(r.sid).push(r);
  }
  updateAll();
}

const ACCEPT = {
  photos: (f) => /^image\//.test(f.type) || /\.(jpe?g|heic|heif|png|dng|tiff?)$/i.test(f.name),
  video: (f) => /^video\//.test(f.type) || /\.(mp4|mov|m4v|3gp|webm|mkv)$/i.test(f.name),
  lidar: (f) => /\.zip$/i.test(f.name) || /zip/.test(f.type),
};

async function addFiles(space, fileList) {
  const list = Array.from(fileList || []).filter((f) => !f.name.startsWith(".") && f.size > 0);
  const ok = list.filter(ACCEPT[space.kind] || (() => true));
  const skipped = list.length - ok.length;
  if (!ok.length) {
    if (list.length) banner(`None of the ${list.length} chosen files is a ${KIND[space.kind].label.toLowerCase()} file.`);
    return;
  }
  try {
    for (const f of ok) await files.add(state.pid, space.space_id, f);
  } catch (e) {
    banner("Could not keep the files on this device (storage full or private browsing?). " + explain(e));
  }
  if (skipped) banner(`${plural(skipped, "file")} skipped: not ${KIND[space.kind].label.toLowerCase()} files.`, { kind: "info" });
  markVerifyDirty();
  await refreshLocal();
  uploader.kick();
}

// ---------- spaces ----------
async function addSpace(ev) {
  ev.preventDefault();
  const form = ev.currentTarget;
  const name = $("#new-name").value.trim() || `Room ${state.spaces.length + 1}`;
  const kind = form.querySelector("input[name=kind]:checked").value;
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const s = await api.createSpace(state.pid, { name, kind, sizes: { length: null, width: null, height: null } });
    s.files = s.files || [];
    state.spaces.push(s);
    uploader.setServerFiles(s.space_id, s.files);
    saveProjectCache();
    markVerifyDirty();
    $("#new-name").value = "";
    updateNamePlaceholder();
    renderSpaces();
    const c = cards.get(s.space_id);
    if (c) c.refs.addBtn.focus();
  } catch (e) {
    banner("Could not add the space. " + explain(e));
  } finally {
    btn.disabled = false;
  }
}

function updateNamePlaceholder() {
  $("#new-name").placeholder = `Room ${state.spaces.length + 1}`;
}

async function deleteSpace(space) {
  const n = fileCount(space);
  if (!confirm(`Delete "${space.name}"${n ? ` and its ${plural(n, "file")}` : ""}?`)) return;
  uploader.paused.add(space.space_id);
  try {
    await api.deleteSpace(state.pid, space.space_id);
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 404)) {
      uploader.paused.delete(space.space_id);
      banner("Could not delete the space. " + explain(e));
      return;
    }
  }
  await files.delSpace(space.space_id);
  state.spaces = state.spaces.filter((s) => s.space_id !== space.space_id);
  saveProjectCache();
  markVerifyDirty();
  updateNamePlaceholder();
  renderSpaces();
  refreshLocal();
}

function move(space, delta) {
  const i = state.spaces.indexOf(space);
  const j = i + delta;
  if (j < 0 || j >= state.spaces.length) return;
  [state.spaces[i], state.spaces[j]] = [state.spaces[j], state.spaces[i]];
  saveProjectCache();
  markVerifyDirty();
  renderSpaces();
  const c = cards.get(space.space_id);
  if (c) (delta < 0 ? (j === 0 ? c.refs.down : c.refs.up) : (j === state.spaces.length - 1 ? c.refs.up : c.refs.down)).focus();
}

async function rename(space, input) {
  const name = input.value.trim();
  if (!name) { input.value = space.name; return; }
  if (name === space.name) return;
  try {
    const r = await api.patchSpace(state.pid, space.space_id, { name });
    space.name = (r && r.name) || name;
    saveProjectCache();
  } catch (e) {
    input.value = space.name;
    banner("Could not rename the space. " + explain(e));
  }
}

async function saveSizes(space, refs) {
  const read = (el) => {
    const v = el.value.trim().replace(",", ".");
    if (!v) return null;
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : NaN;
  };
  const sizes = { length: read(refs.L), width: read(refs.B), height: read(refs.H) };
  const bad = Object.entries(sizes).filter(([, v]) => Number.isNaN(v));
  [refs.L, refs.B, refs.H].forEach((el) => el.setAttribute("aria-invalid", "false"));
  if (bad.length) {
    bad.forEach(([k]) => refs[{ length: "L", width: "B", height: "H" }[k]].setAttribute("aria-invalid", "true"));
    refs.sizeMsg.textContent = "Sizes must be positive numbers in metres, e.g. 2.45.";
    return;
  }
  const outOfRange = Object.values(sizes).some((v) => v != null && (v < 0.3 || v > 50));
  refs.sizeMsg.textContent = outOfRange ? "That looks unusual for a room in metres. Check it is not in cm or feet." : "Saving…";
  try {
    const r = await api.patchSpace(state.pid, space.space_id, { sizes });
    space.sizes = (r && r.sizes) || sizes;
    saveProjectCache();
    if (!outOfRange) refs.sizeMsg.textContent = "Saved.";
  } catch (e) {
    refs.sizeMsg.textContent = "Not saved: " + explain(e);
  }
}

async function clearFiles(space) {
  const n = fileCount(space);
  if (!n || !confirm(`Remove all ${plural(n, "file")} from "${space.name}"?`)) return;
  uploader.paused.add(space.space_id);
  try {
    await files.delSpace(space.space_id);
    for (const f of [...(space.files || [])]) {
      await api.deleteFile(state.pid, space.space_id, f.sha256).catch((e) => { if (!(e.status === 404)) throw e; });
      space.files = space.files.filter((x) => x.sha256 !== f.sha256);
    }
  } catch (e) {
    banner("Could not remove every file. " + explain(e));
  } finally {
    uploader.paused.delete(space.space_id);
    uploader.setServerFiles(space.space_id, space.files);
    saveProjectCache();
    markVerifyDirty();
    refreshLocal();
  }
}

async function removeNamed(space, names) {
  const want = new Set(names.map(baseName));
  const server = (space.files || []).filter((f) => want.has(baseName(f.name)) || want.has(f.sha256));
  const local = (state.local.get(space.space_id) || []).filter((r) => want.has(baseName(r.name)));
  const n = server.length + local.length;
  if (!n) { banner("Those files are already gone.", { kind: "info" }); return; }
  if (!confirm(`Remove ${plural(n, "file")} from "${space.name}"?\n\n${names.join("\n")}`)) return;
  try {
    for (const r of local) await files.del(r.id);
    for (const f of server) {
      await api.deleteFile(state.pid, space.space_id, f.sha256).catch((e) => { if (e.status !== 404) throw e; });
      space.files = space.files.filter((x) => x.sha256 !== f.sha256);
    }
  } catch (e) {
    banner("Could not remove every file. " + explain(e));
  }
  uploader.setServerFiles(space.space_id, space.files);
  saveProjectCache();
  markVerifyDirty();
  refreshLocal();
}

const baseName = (n) => String(n || "").split(/[\\/]/).pop();

function fileCount(space) {
  return (space.files || []).length + (state.local.get(space.space_id) || []).length;
}

function pickerButton(label, { accept, multiple = true, directory = false, capture = null, onFiles, primary = false }) {
  const input = h("input", { type: "file", class: "visually-hidden", tabindex: "-1", "aria-hidden": "true", accept });
  if (multiple) input.multiple = true;
  if (directory) { input.webkitdirectory = true; input.setAttribute("webkitdirectory", ""); }
  if (capture) input.setAttribute("capture", capture);
  input.addEventListener("change", () => { onFiles(input.files); input.value = ""; });
  const btn = h("button", { type: "button", class: "btn small" + (primary ? " primary" : ""), onclick: () => input.click() }, label);
  return [btn, input];
}

function buildCard(space) {
  const sid = space.space_id;
  const refs = {};
  const kind = space.kind;
  const nameId = "name-" + sid;
  refs.num = h("span", { class: "space-num", "aria-hidden": "true" });
  refs.name = h("input", { id: nameId, class: "name-input", type: "text", maxlength: "60", value: space.name, "aria-label": "Space name" });
  refs.name.addEventListener("change", () => rename(space, refs.name));
  refs.name.addEventListener("keydown", (e) => { if (e.key === "Enter") refs.name.blur(); });
  refs.up = h("button", { type: "button", class: "btn icon small ghost", "aria-label": `Move ${space.name} up`, onclick: () => move(space, -1) }, "↑");
  refs.down = h("button", { type: "button", class: "btn icon small ghost", "aria-label": `Move ${space.name} down`, onclick: () => move(space, 1) }, "↓");
  const del = h("button", { type: "button", class: "btn icon small ghost danger", "aria-label": `Delete ${space.name}`, onclick: () => deleteSpace(space) }, "✕");

  refs.badge = h("span", { class: "badge", hidden: true });
  refs.count = h("span", { class: "count" });
  refs.progressText = h("span", { class: "small muted" });
  refs.barFill = h("i");
  refs.bar = h("div", { class: "bar", role: "progressbar", "aria-label": "Upload progress", "aria-valuemin": "0", "aria-valuemax": "100" }, refs.barFill);
  refs.progress = h("div", { class: "progress", hidden: true }, refs.progressText, refs.bar);
  refs.errors = h("div", { class: "small", hidden: true });
  refs.thumbs = h("div", { class: "thumbs", "aria-hidden": "true" });

  const add = (files) => addFiles(space, files);
  const btns = [];
  if (kind === "photos") {
    btns.push(...pickerButton("Add photos", { accept: "image/*,.heic,.heif", onFiles: add, primary: true }));
    if (DIR_OK) btns.push(...pickerButton("Add folder", { directory: true, onFiles: add }));
  } else if (kind === "video") {
    btns.push(...pickerButton("Add video", { accept: "video/*", onFiles: add, primary: true }));
    btns.push(...pickerButton("Record", { accept: "video/*", multiple: false, capture: "environment", onFiles: add }));
  } else {
    btns.push(...pickerButton("Add LiDAR zip", { accept: ".zip,application/zip,application/x-zip-compressed", onFiles: add, primary: true }));
  }
  refs.addBtn = btns[0];
  refs.clear = h("button", { type: "button", class: "btn small ghost", onclick: () => clearFiles(space) }, "Clear files");

  const num = (key, label, full) => {
    const id = `${key}-${sid}`;
    const el = h("input", { id, type: "text", inputmode: "decimal", autocomplete: "off", placeholder: "–", "aria-describedby": "hint-" + sid, value: space.sizes && space.sizes[full] != null ? String(space.sizes[full]) : "" });
    el.addEventListener("change", () => saveSizes(space, refs));
    refs[key] = el;
    return h("label", { class: "field", for: id }, h("span", {}, `${label} (m)`), el);
  };
  refs.sizeMsg = h("p", { class: "small muted sizes-hint", "aria-live": "polite" });
  const sizes = h("div", { class: "sizes", role: "group", "aria-label": "Known sizes in metres, optional" },
    num("L", "L length", "length"), num("B", "B breadth", "width"), num("H", "H height", "height"),
    h("p", { id: "hint-" + sid, class: "small muted sizes-hint" }, kind === "lidar"
      ? "Optional, to compare with ours. One is enough, e.g. the ceiling height."
      : "Optional. One is enough: give a length or breadth to improve the scale (a height alone is only compared)."),
    refs.sizeMsg);

  refs.findings = h("div", {});
  const root = h("li", { class: "card space", "data-sid": sid },
    h("div", { class: "space-head" }, refs.num, refs.name, h("div", { class: "space-tools" }, refs.up, refs.down, del)),
    h("div", { class: "space-body" },
      h("div", {},
        h("div", { class: "space-meta" }, h("span", { class: "kind-tag" }, KIND[kind] ? KIND[kind].label : kind), refs.count, refs.badge, refs.thumbs),
        refs.progress, refs.errors,
        h("div", { class: "add-btns" }, btns, refs.clear)),
      sizes),
    refs.findings);
  return { root, refs, space };
}

function renderSpaces() {
  const list = $("#spaces");
  const live = new Set(state.spaces.map((s) => s.space_id));
  for (const [sid, c] of cards) if (!live.has(sid)) { c.root.remove(); cards.delete(sid); }
  state.spaces.forEach((s, i) => {
    let c = cards.get(s.space_id);
    if (!c) { c = buildCard(s); cards.set(s.space_id, c); }
    c.space = s;
    if (list.children[i] !== c.root) list.insertBefore(c.root, list.children[i] || null);
  });
  $("#spaces-count").textContent = state.spaces.length ? `(${state.spaces.length})` : "";
  if (!state.spaces.length) list.replaceChildren(h("li", { class: "muted small", id: "no-spaces" }, "No spaces yet. Add the first room below."));
  else { const e = $("#no-spaces"); if (e) e.remove(); }
  updateAll();
}

function updateCard(c, i) {
  const { refs, space } = c;
  const sid = space.space_id;
  refs.num.textContent = String(i + 1);
  refs.up.disabled = i === 0;
  refs.down.disabled = i === state.spaces.length - 1;
  refs.up.setAttribute("aria-label", `Move ${space.name} up`);
  refs.down.setAttribute("aria-label", `Move ${space.name} down`);

  const local = state.local.get(sid) || [];
  const srv = space.files || [];
  const n = srv.length + local.length;
  const bytes = srv.reduce((a, f) => a + (f.size || 0), 0) + local.reduce((a, r) => a + (r.size || 0), 0);
  const unit = KIND[space.kind] ? KIND[space.kind].unit : ["file", "files"];
  refs.count.textContent = n ? `${plural(n, unit[0], unit[1])} · ${fmtBytes(bytes)}` : `No ${unit[1]} yet`;
  refs.clear.hidden = !n;

  const waiting = local.filter((r) => r.state === "pending");
  const failed = local.filter((r) => r.state === "error");
  const p = uploader.progress.get(sid);
  if (waiting.length || p) {
    refs.progress.hidden = false;
    let pct = 0, txt;
    if (p) {
      pct = p.total ? Math.round((100 * p.loaded) / p.total) : 0;
      txt = `${p.phase === "hash" ? "Preparing" : "Uploading"} ${baseName(p.name)} · ${pct} %`;
    } else txt = "Waiting to upload";
    if (waiting.length > 1 || (!p && waiting.length)) txt += ` · ${plural(waiting.length, "file")} left`;
    if (uploader.retryAt && !p) txt = `Connection problem (${uploader.lastError || "network"}). Retrying automatically · ${plural(waiting.length, "file")} waiting`;
    refs.progressText.textContent = txt;
    refs.barFill.style.width = pct + "%";
    refs.bar.setAttribute("aria-valuenow", String(pct));
  } else refs.progress.hidden = true;

  if (failed.length) {
    refs.errors.hidden = false;
    refs.errors.replaceChildren(
      h("div", { class: "error-box" }, `${plural(failed.length, "file")} could not be uploaded: ${failed[0].error || "error"}`),
      h("button", { type: "button", class: "btn small", onclick: async () => { for (const r of failed) await files.patch(r.id, { state: "pending", error: null }); await refreshLocal(); uploader.kick(); } }, "Retry"),
      " ",
      h("button", { type: "button", class: "btn small ghost", onclick: async () => { for (const r of failed) await files.del(r.id); refreshLocal(); } }, "Remove them"));
  } else refs.errors.hidden = true;

  // up to 3 tiny thumbnails of photos still on the device
  if (space.kind === "photos") {
    const imgs = local.filter((r) => r.blob && /^image\/(jpeg|png|webp|gif)/.test(r.type)).slice(0, 3);
    const key = imgs.map((r) => r.id).join(",");
    if (refs.thumbs.dataset.key !== key) {
      refs.thumbs.querySelectorAll("img").forEach((im) => URL.revokeObjectURL(im.src));
      refs.thumbs.replaceChildren(...imgs.map((r) => h("img", { src: URL.createObjectURL(r.blob), alt: "", loading: "lazy" })));
      refs.thumbs.dataset.key = key;
    }
  }

  // verify status
  const v = state.verify && state.verify.result;
  const sv = v && (v.spaces || []).find((x) => x.space_id === sid);
  if (sv) {
    refs.badge.hidden = false;
    refs.badge.className = "badge " + sv.status + (state.verify.dirty ? " stale" : "");
    refs.badge.textContent = (STATUS[sv.status] || sv.status) + (state.verify.dirty ? " (re-check)" : "");
    const key = JSON.stringify(sv.findings) + state.verify.at;
    if (refs.findings.dataset.key !== key) {
      refs.findings.dataset.key = key;
      refs.findings.replaceChildren(renderFindings(sv.findings || [], space, sv.status));
    }
  } else {
    refs.badge.hidden = true;
    refs.findings.replaceChildren();
    refs.findings.dataset.key = "";
  }
}

function renderFindings(list, space, status) {
  const shown = list.filter((f) => f.level !== "ok" && f.level !== "info");
  if (!shown.length) return status === "ok" ? h("p", { class: "small muted" }, "Looks good.") : document.createTextNode("");
  return h("ul", { class: "findings", "aria-label": `Findings for ${space ? space.name : "the project"}` }, shown.map((f) => {
    const lvl = f.level === "retake" ? "retake" : f.level === "warn" ? "warn" : "info";
    const fl = f.files || [];
    return h("li", { class: "finding " + lvl },
      h("p", {}, h("span", { class: "badge " + lvl }, lvl === "retake" ? "Retake" : lvl === "warn" ? "Check" : "Note"), " ",
        f.check ? h("strong", {}, humanCheck(f.check) + ": ") : null, f.message || ""),
      fl.length ? h("div", { class: "chips" }, fl.slice(0, 20).map((n) => h("span", { class: "chip" }, baseName(n))), fl.length > 20 ? h("span", { class: "chip" }, `+${fl.length - 20} more`) : null) : null,
      space && fl.length ? h("button", { type: "button", class: "btn small", onclick: () => removeNamed(space, fl) }, `Remove ${fl.length === 1 ? "this file" : `these ${fl.length} files`}`) : null,
      space && lvl === "retake" && !fl.length ? h("p", { class: "small" }, "Retake this space: clear its files, capture it again following the guide, and add the new files.") : null);
  }));
}

const humanCheck = (c) => { const s = String(c).replace(/[_-]+/g, " "); return s.charAt(0).toUpperCase() + s.slice(1); };

let rafPending = false;
function updateAll() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => {
    rafPending = false;
    state.spaces.forEach((s, i) => { const c = cards.get(s.space_id); if (c) updateCard(c, i); });
    updateRunControls();
  });
}

// ---------- verify / run ----------
function markVerifyDirty() {
  if (state.verify && !state.verify.dirty) {
    state.verify.dirty = true;
    kv.set("verify." + state.pid, state.verify);
  }
  updateAll();
}

function pendingUploads() {
  let n = 0;
  for (const recs of state.local.values()) n += recs.length;
  return n;
}

function updateRunControls() {
  const pend = pendingUploads();
  const total = state.spaces.reduce((a, s) => a + fileCount(s), 0);
  const empty = state.spaces.filter((s) => !fileCount(s));
  $("#upload-summary").textContent = !state.spaces.length ? "" :
    pend ? `${plural(pend, "file")} still uploading. Keep this page open (you can reload: nothing is lost).`
      : `All ${plural(total, "file")} uploaded.`;
  const v = state.verify;
  const hasRetake = v && v.result && (v.result.spaces || []).some((s) => s.status === "retake");
  const verified = v && v.result && !v.dirty;
  const ready = state.spaces.length && !pend && !empty.length;
  $("#verify").disabled = !ready;
  $("#run").disabled = !(ready && verified && !hasRetake);
  $("#run-anyway").hidden = !(ready && verified && hasRetake);
  let hint;
  if (!state.spaces.length) hint = "Add a space first.";
  else if (empty.length) hint = `Add files to ${empty.map((s) => s.name).join(", ")}.`;
  else if (pend) hint = "Wait for the uploads to finish, then check the captures.";
  else if (!v || !v.result) hint = "Check the captures first.";
  else if (v.dirty) hint = "Something changed since the last check: check the captures again.";
  else if (hasRetake) hint = "Some spaces need a retake (see the red badges). Fix them, or run anyway.";
  else hint = v.result.spaces.some((s) => s.status === "warn") ? "Usable, with warnings. Ready to compute." : "All good. Ready to compute.";
  $("#run-hint").textContent = hint;
}

async function doVerify() {
  const btn = $("#verify");
  btn.disabled = true;
  btn.textContent = "Checking…";
  try {
    const result = await api.verify(state.pid);
    state.verify = { result, at: Date.now(), dirty: false };
    kv.set("verify." + state.pid, state.verify);
    const pf = (result.project_findings || []);
    $("#project-findings").replaceChildren(pf.length ? renderFindings(pf, null, "warn") : "");
    const bad = (result.spaces || []).filter((s) => s.status !== "ok");
    banner(bad.length ? `${plural(bad.length, "space")} need${bad.length === 1 ? "s" : ""} attention: ${bad.map((s) => `${s.name} (${STATUS[s.status] || s.status})`).join(", ")}.` : "All spaces look good.", { kind: "info" });
  } catch (e) {
    banner("Check failed. " + explain(e));
  } finally {
    btn.textContent = "Check captures";
    updateAll();
  }
}

async function doRun(force = false) {
  if (force && !confirm("Some spaces need a retake. Results for them may be wrong or missing. Compute anyway?")) return;
  $("#run").disabled = true;
  try {
    const r = await api.run(state.pid, force);
    state.jid = r.job_id;
    kv.set("job." + state.pid, r.job_id);
    $("#results").hidden = true;
    banner(r.cached ? "These captures were computed before: showing the saved result." : null, { kind: "info" });
    startPolling(r.job_id);
    $("#job").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      banner("The server wants the captures checked first (or a retake is needed). Press “Check captures”.");
      markVerifyDirty();
    } else banner("Could not start computing. " + explain(e));
  } finally {
    updateAll();
  }
}

// ---------- job polling ----------
let pollTimer = null;
let pollFails = 0;
let pollJid = null;
let resultsShownFor = null;

function stopPolling() { clearTimeout(pollTimer); pollTimer = null; pollJid = null; }

function startPolling(jid) {
  stopPolling();
  pollJid = jid;
  pollFails = 0;
  poll();
}

async function poll() {
  const jid = pollJid;
  if (!jid) return;
  let delay = 2500;
  try {
    const job = await api.job(jid);
    if (jid !== pollJid) return;
    pollFails = 0;
    $("#job-poll").textContent = "";
    renderJob(job, jid);
    if (job.status === "done") {
      if (resultsShownFor !== jid) { resultsShownFor = jid; renderResults(job, jid); }
      pollJid = null;
      return;
    }
    if (job.status === "failed") { pollJid = null; return; }
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      kv.set("job." + state.pid, null);
      $("#job").hidden = true;
      pollJid = null;
      return;
    }
    pollFails++;
    delay = Math.min(2500 * 2 ** pollFails, 30000);
    $("#job-poll").textContent = `· connection problem, retrying in ${Math.round(delay / 1000)} s`;
  }
  if (document.hidden) delay = Math.max(delay, 10000);
  pollTimer = setTimeout(poll, delay);
}

// ---------- wiring ----------
const uploader = new Uploader({
  getPid: () => state.pid,
  onChange: () => updateAll(),
  onFileUploaded: (sid, rec) => {
    const s = state.spaces.find((x) => x.space_id === sid);
    if (s) {
      s.files = s.files || [];
      if (!s.files.some((f) => f.sha256 === rec.sha256)) s.files.push({ name: rec.name, sha256: rec.sha256, size: rec.size });
      saveProjectCache();
    }
    refreshLocal();
  },
});

let started = false;
async function init() {
  if (!started) {
    started = true;
    $("#add-space").addEventListener("submit", addSpace);
    $("#verify").addEventListener("click", doVerify);
    $("#run").addEventListener("click", () => doRun(false));
    $("#run-anyway").addEventListener("click", () => doRun(true));
    $("#run-again").addEventListener("click", () => doRun(state.verify && state.verify.result && (state.verify.result.spaces || []).some((s) => s.status === "retake")));
    $("#new-project").addEventListener("click", newProject);
    $("#api-status").addEventListener("click", checkHealth);
    $("#api-input").value = API_BASE;
    $("#api-form").addEventListener("submit", (e) => {
      e.preventDefault();
      setApiBase($("#api-input").value.trim());
      const u = new URL(location.href);
      u.searchParams.delete("api");
      location.href = u.toString();
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && pollJid) { clearTimeout(pollTimer); poll(); } });
    addEventListener("online", () => { checkHealth(); if (pollJid) { clearTimeout(pollTimer); poll(); } });
    setInterval(checkHealth, 30000);
    requestPersistence();
  }
  checkHealth();
  await loadProject();
  updateNamePlaceholder();
  renderSpaces();
  await refreshLocal();
  if (state.verify && state.verify.result) {
    const pf = state.verify.result.project_findings || [];
    $("#project-findings").replaceChildren(pf.length ? renderFindings(pf, null, "warn") : "");
  }
  uploader.kick();
}

init();
