// Job progress and results rendering.
import { api, url } from "./api.js";
import { $, h, fmtNum, fmtSecs } from "./dom.js";

const STATUS_LABEL = { queued: "Queued", running: "Running", done: "Done", failed: "Failed" };

// outputs values may be a bare file name, a path, or a URL: normalise to a fetchable URL.
export function outputUrl(jid, value, fallbackName) {
  if (!value) return fallbackName ? api.jobFileUrl(jid, fallbackName) : null;
  if (/^https?:\/\//i.test(value) || value.startsWith("/api/")) return url(value);
  const base = String(value).split(/[\\/]/).pop();
  return api.jobFileUrl(jid, base);
}

export function renderJob(job, jid) {
  $("#job").hidden = false;
  $("#job-id").textContent = jid;
  const st = job.status || "queued";
  const badge = $("#job-status");
  badge.className = "badge " + st;
  badge.textContent = STATUS_LABEL[st] || st;
  $("#job-h").textContent = st === "done" ? "Computed" : st === "failed" ? "Computing failed" : "Computing";

  const list = $("#stages");
  list.replaceChildren(...(job.stages || []).map((s) => {
    const state = s.status || "pending";
    const icon = { done: "✓", running: "…", failed: "!", skipped: "–" }[state] || "";
    return h("li", { class: "stage " + state },
      h("span", { class: "st-icon", "aria-hidden": "true" }, icon),
      h("span", { class: "st-name" }, prettyStage(s.name),
        h("span", { class: "visually-hidden" }, ` (${state})`),
        s.note ? h("span", { class: "st-note" }, s.note) : null),
      h("span", { class: "st-time" }, s.seconds != null ? fmtSecs(s.seconds) : ""));
  }));
  if (!(job.stages || []).length && job.stage) list.replaceChildren(h("li", { class: "stage running" }, h("span", { class: "st-icon" }, "…"), h("span", { class: "st-name" }, prettyStage(job.stage))));

  const err = $("#job-error");
  err.hidden = st !== "failed";
  err.textContent = st === "failed" ? (job.error || "The computation failed without a message.") : "";
  $("#job-actions").hidden = !(st === "failed" || st === "done");
  $("#run-again").textContent = st === "failed" ? "Run again" : "Compute again";
}

const STAGE_LABEL = {
  load: "Reading capture",
  drift: "Correcting drift",
  fuse: "Building 3D model",
  layout: "Finding rooms & walls",
  openings: "Doors & windows",
  damage: "Damage check",
  export: "Writing results",
  "load+depth": "Reading photos & depth",
  room_fit: "Fitting rooms",
  stitch: "Joining rooms",
};

function prettyStage(n) {
  if (!n) return "";
  // "Rooms (photos): load+depth" -> "Rooms (photos) · Reading photos & depth"
  const i = String(n).indexOf(": ");
  if (i > 0) return `${String(n).slice(0, i)} · ${prettyStage(String(n).slice(i + 2))}`;
  if (STAGE_LABEL[n]) return STAGE_LABEL[n];
  const s = String(n).replace(/[_-]+/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function m(meas, d = 2, unit = "") {
  if (!meas || meas.value == null) {
    if (meas && meas.lower_bound != null) return [`≥ ${fmtNum(meas.lower_bound, d)}${unit}`, "not observed"];
    return ["–", ""];
  }
  const ci = meas.ci90 ? `90 %: ${fmtNum(meas.ci90[0], d)}–${fmtNum(meas.ci90[1], d)}` : "";
  return [`${fmtNum(meas.value, d)}${unit}`, ci];
}

function cell(meas, d = 2, unit = "") {
  const [v, ci] = m(meas, d, unit);
  return h("td", { class: "num" }, v, ci ? h("span", { class: "ci" }, ci) : null);
}

let selectedLabel = null;

export async function renderResults(job, jid, partial = false) {
  const sec = $("#results");
  sec.hidden = false;
  // one tab per run that finished: the rooms' photos, the whole-home video / LiDAR scan
  const runs = (job.runs || []).filter((r) => r.status === "done" && r.outputs && Object.keys(r.outputs).length);
  const tabs = $("#run-tabs");
  const show = (r) => {
    selectedLabel = r ? r.label : null;
    tabs.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.label === (r && r.label))));
    renderRun(r ? r.outputs : (job.outputs || {}), jid);
  };
  // one tab per finished run: Rooms (photos) / Whole home (video) / Whole home (LiDAR)
  tabs.replaceChildren(...runs.map((r) => h("button", { type: "button", class: "btn small", "data-label": r.label, onclick: () => show(r) }, r.title || r.label)));
  tabs.hidden = !runs.length && !(job.runs || []).some((r) => r.status === "failed");
  const failed = (job.runs || []).filter((r) => r.status === "failed");
  if (failed.length) tabs.append(h("p", { class: "small muted" }, failed.map((r) => `${r.title || r.label} failed: ${r.error || "error"}`).join(" · ")));
  const waiting = (job.runs || []).filter((r) => r.status === "running" || r.status === "pending");
  if (partial && waiting.length) {
    tabs.hidden = false;
    tabs.append(h("p", { class: "small muted" },
      `Still processing: ${waiting.map((r) => r.title || r.label).join(", ")}. Its results appear here when it finishes.`));
  }
  show(runs.find((r) => r.label === selectedLabel) || runs[0] || null);
  if (partial) {
    $("#comparison-table").replaceChildren(h("p", { class: "small muted" }, "The size comparison appears when every run has finished."));
    return;
  }
  try {
    const cmp = await api.comparison(jid);
    renderComparison(cmp && cmp.rows || []);
  } catch (e) {
    $("#comparison-table").replaceChildren(h("p", { class: "small muted" }, "Comparison not available: " + e.message));
  }
}

async function renderRun(o, jid) {
  const jsonUrl = outputUrl(jid, o.result_json, "result.json");
  const xlsxUrl = outputUrl(jid, o.result_xlsx, "result.xlsx");
  const pngUrl = outputUrl(jid, o.plan_png, "plan.png");
  const svgUrl = outputUrl(jid, o.plan_svg, "plan.svg");

  const dl = (href, label, file) => href ? h("a", { class: "btn small", href, download: file, target: "_blank", rel: "noopener" }, "⬇ " + label) : null;
  $("#downloads").replaceChildren(
    dl(jsonUrl, "result.json", "result.json"), dl(xlsxUrl, "result.xlsx", "result.xlsx"),
    dl(pngUrl, "plan.png", "plan.png"), dl(svgUrl, "plan.svg", "plan.svg"));
  const img = $("#plan-img");
  img.src = pngUrl || svgUrl || "";
  img.onerror = () => { if (svgUrl && img.src !== svgUrl) img.src = svgUrl; };
  $("#plan-svg-link").href = svgUrl || pngUrl || "#";

  let res = null;
  try {
    const r = await fetch(jsonUrl);
    if (!r.ok) throw new Error(`${r.status}`);
    res = await r.json();
  } catch (e) {
    $("#summary").replaceChildren(h("div", {}, h("dt", {}, "Result"), h("dd", {}, "Could not load result.json (" + e.message + ")")));
  }
  if (res) renderResultJson(res);
}

function renderResultJson(res) {
  const rooms = res.rooms || [];
  const prop = res.property || {};
  const [fp, fpci] = m(prop.footprint_area, 1, " m²");
  const sigma = prop.footprint_area && prop.footprint_area.sigma;
  const totalS = res.timing_s ? Object.values(res.timing_s).reduce((a, b) => a + (Number(b) || 0), 0) : null;
  const item = (dt, dd, small) => h("div", {}, h("dt", {}, dt), h("dd", {}, dd, small ? h("small", {}, small) : null));
  $("#summary").replaceChildren(
    item("Rooms", String(rooms.length)),
    item("Footprint", fp, sigma != null ? `± ${fmtNum(1.645 * sigma, 1)} m² · ${fpci}` : fpci),
    item("Damage found", String((res.damage || []).length), (res.concealed_damage_flags || []).length ? `${res.concealed_damage_flags.length} concealed-damage flags` : null),
    item("Capture", (res.capture && res.capture.tier) || "–", totalS ? `computed in ${fmtSecs(totalS)}` : null),
  );

  const label = Object.fromEntries(rooms.map((r) => [r.id, r.label || r.id]));
  $("#rooms-table").replaceChildren(rooms.length ? h("table", {},
    h("caption", { class: "visually-hidden" }, "Rooms with floor area, perimeter and ceiling height, 90 % ranges below each value"),
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Room"), h("th", { scope: "col", class: "num" }, "Area m²"),
      h("th", { scope: "col", class: "num" }, "Perimeter m"), h("th", { scope: "col", class: "num" }, "Ceiling m"))),
    h("tbody", {}, rooms.map((r) => h("tr", {},
      h("th", { scope: "row" }, r.label || r.id),
      cell(r.floor_area, 2), cell(r.perimeter, 2), cell(r.ceiling_height, 2))))) : h("p", { class: "muted" }, "No rooms were measured."));

  const dmg = res.damage || [];
  const flags = res.concealed_damage_flags || [];
  $("#damage").replaceChildren(...[
    dmg.length ? h("ul", { class: "damage-list" }, dmg.map((d) => {
      const [a] = m(d.area, 2, " m²");
      return h("li", {}, h("strong", {}, prettyStage(d.damage_class)), ` in ${label[d.room_id] || d.room_id}`,
        h("span", { class: "small muted" }, ` · ${a} · on ${d.surface_id} · confidence ${Math.round((d.score || 0) * 100)} %`));
    })) : h("p", { class: "muted" }, "No visible damage detected."),
    flags.length ? h("div", {}, h("h4", {}, "Possible hidden damage"), h("ul", { class: "damage-list" }, flags.map((f) =>
      h("li", {}, h("span", { class: "badge " + (f.risk === "high" ? "retake" : f.risk === "medium" ? "warn" : "") }, f.risk), " ",
        `${label[f.room_id] || f.room_id}: ${f.rule}`, h("div", { class: "small muted" }, f.recommendation))))) : null,
  ].filter(Boolean));  // replaceChildren would print a null as the text "null"

  const w = res.warnings || [];
  $("#warnings-card").hidden = !w.length;
  $("#warnings").replaceChildren(...w.map((x) => h("li", {}, x)));
}

function renderComparison(rows) {
  const wrap = $("#comparison-table");
  rows = rows.filter((r) => r.given != null);
  if (!rows.length) {
    wrap.replaceChildren(h("p", { class: "small muted" }, "You did not type in any sizes, so there is nothing to compare. Add L / B / H to a room to see how close we are."));
    return;
  }
  wrap.replaceChildren(h("table", {},
    h("caption", { class: "visually-hidden" }, "Sizes you typed in against the computed sizes"),
    h("thead", {}, h("tr", {}, ...["Room", "Tier", "Size", "Yours m", "Ours m", "Ours 90 % range", "Difference", "Inside range?"]
      .map((t, i) => h("th", { scope: "col", class: i >= 3 && i <= 6 ? "num" : null }, t)))),
    h("tbody", {}, rows.map((r) => {
      const ci = Array.isArray(r.ci90) ? r.ci90 : null;
      const inside = ci && r.given != null ? r.given >= ci[0] && r.given <= ci[1] : null;
      const diff = r.diff != null ? `${r.diff > 0 ? "+" : ""}${fmtNum(r.diff, 2)} m` : "–";
      const pct = r.diff_pct != null ? ` (${r.diff_pct > 0 ? "+" : ""}${fmtNum(r.diff_pct, 1)} %)` : "";
      return h("tr", {},
        h("th", { scope: "row" }, r.space ?? ""),
        h("td", {}, TIER[r.tier] || r.tier || "–"),
        h("td", {}, QTY[r.quantity] || r.quantity || ""),
        h("td", { class: "num" }, fmtNum(r.given)),
        h("td", { class: "num" }, fmtNum(r.computed)),
        h("td", { class: "num" }, ci ? `${fmtNum(ci[0])}–${fmtNum(ci[1])}` : "–"),
        h("td", { class: "num" }, diff + pct),
        h("td", {}, inside == null ? "–" : inside ? h("span", { class: "in" }, "✓ yes") : h("span", { class: "out" }, "✗ no")));
    }))));
}

const TIER = { photos: "Photos", video: "Whole home: video", lidar: "Whole home: LiDAR" };
const QTY = { length: "Length (L)", width: "Breadth (B)", height: "Height (H)" };
