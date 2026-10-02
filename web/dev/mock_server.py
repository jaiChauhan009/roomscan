"""Mock roomscan API for exercising the web UI without the real backend.

Standard library only. In-memory state; restart = fresh. Implements the web contract:
projects, rooms (spaces of kind "photos", photos optional), two optional whole-home captures
(a video AND / OR a LiDAR scan: PUT/GET/DELETE /captures/{video|lidar}, /captures/{kind}/files, one
file each), files (multipart PUT, idempotent by sha256), verify, run (one run per present input:
rooms' photos, whole-home video, whole-home LiDAR), jobs (a fake job that walks
through its stages in real time), job files and comparison (with a tier column), health.

    python web/dev/mock_server.py                 # http://localhost:8000
    python web/dev/mock_server.py --port 8001 --stage-seconds 0.5
    python -m http.server -d web 5173             # then open http://localhost:5173

Knobs for manual testing:
  * a photo space with more than 12 photos -> verify says RETAKE (run answers 409)
  * a file whose name contains "blur"       -> verify WARN naming that file
  * a space whose name contains "fail"      -> the job fails at the "openings" stage
"""
from __future__ import annotations

import argparse
import email.parser
import email.policy
import hashlib
import io
import json
import re
import struct
import threading
import time
import uuid
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

STAGES_3D = ["load", "drift", "fuse", "layout", "openings", "damage", "export"]      # LiDAR / video
STAGES_PHOTO = ["load+depth", "room_fit", "stitch", "openings", "damage", "export"]
STAGE_SECONDS = 2.0
LOCK = threading.Lock()
PROJECTS: dict[str, dict] = {}
JOBS: dict[str, dict] = {}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------- verify logic
CAP_SID = {"video": "_capture_video", "lidar": "_capture_lidar"}
SID_CAP = {v: k for k, v in CAP_SID.items()}
TITLES = {"photos": "Rooms (photos)", "video": "Whole home (video)", "lidar": "Whole home (LiDAR)"}


def verify_space(sp: dict, has_capture: bool = False) -> dict:
    files = sp["files"]
    names = [f["name"] for f in files]
    findings = []
    if not files and has_capture and sp["kind"] == "photos":
        findings.append({"level": "ok", "check": "photos in room", "message": "No photos: a size reference for the whole-home capture.", "files": []})
    elif not files:
        findings.append({"level": "retake", "check": "files", "message": "No files yet: add photos, or a whole-home capture.", "files": []})
    elif sp["kind"] == "photos":
        n = len(files)
        if n > 12:
            findings.append({"level": "retake", "check": "photo count",
                             "message": f"{n} photos: more than 12 means they were taken while walking. Stand in the doorway, take 5-6 photos turning left to right, then one looking back.",
                             "files": []})
        elif n < 2 or n > 8:
            findings.append({"level": "warn", "check": "photo count",
                             "message": f"{n} photos; 2 to 8 per room works best.", "files": []})
        blurred = [x for x in names if "blur" in x.lower()]
        if blurred:
            findings.append({"level": "warn", "check": "sharpness",
                             "message": "These photos are blurred. Remove them or retake them holding the phone still.",
                             "files": blurred})
    elif sp["kind"] == "video":
        if len(files) > 1:
            findings.append({"level": "warn", "check": "clips", "message": "More than one clip: only the first is used.", "files": names[1:]})
    elif sp["kind"] == "lidar":
        bad = [x for x in names if not x.lower().endswith(".zip")]
        if bad:
            findings.append({"level": "retake", "check": "format", "message": "Not a Stray Scanner .zip.", "files": bad})
    levels = {f["level"] for f in findings}
    status = "retake" if "retake" in levels else "warn" if "warn" in levels else "ok"
    return {"space_id": sp["space_id"], "name": sp["name"], "status": status, "findings": findings}


# ---------------------------------------------------------------- job logic
def job_view(job: dict) -> dict:
    stage_s = job["stage_seconds"]
    STAGES = job["stages"]
    elapsed = time.time() - job["started"]
    fail_at = next(i for i, n in enumerate(STAGES) if n.endswith(": openings")) if job["fail"] else None
    stages, status, current, error = [], "running", None, None
    if job["cached"]:
        elapsed = 1e9
    if elapsed < stage_s * 0.5 and not job["cached"]:
        status = "queued"
    for i, name in enumerate(STAGES):
        t0 = stage_s * (i + 0.5)
        if fail_at is not None and i > fail_at:
            stages.append({"name": name, "status": "pending", "seconds": None, "note": None})
            continue
        if elapsed >= t0 + stage_s:
            if fail_at == i:
                stages.append({"name": name, "status": "failed", "seconds": round(stage_s * 0.7, 2), "note": "no wall evidence in space 'fail'"})
                status, error = "failed", "Stage 'openings' failed: no wall evidence in space 'fail'. Retake it or remove it."
            else:
                note = "cached" if job["cached"] else None
                if name.endswith(": damage") and not job["damage"]:
                    stages.append({"name": name, "status": "skipped", "seconds": 0, "note": "not requested"})
                else:
                    if name.endswith((": layout", ": room_fit")) and not note:
                        note = f"{len(job['spaces'])} room(s), {4 * len(job['spaces'])} walls"
                    stages.append({"name": name, "status": "done", "seconds": round(stage_s * (0.8 + 0.1 * i), 2), "note": note})
        elif elapsed >= t0:
            stages.append({"name": name, "status": "running", "seconds": round(elapsed - t0, 1), "note": None})
            current = name
        else:
            stages.append({"name": name, "status": "pending", "seconds": None, "note": None})
    if status != "failed" and all(s["status"] in ("done", "skipped") for s in stages):
        status = "done"
    runs = []
    for r in job["runs"]:
        pre = r["prefix"]
        outs = ({k: f"/api/jobs/{job['job_id']}/files/{pre}{fn}" for k, fn in
                 (("result_json", "result.json"), ("result_xlsx", "result.xlsx"), ("plan_png", "plan.png"), ("plan_svg", "plan.svg"))}
                if status == "done" else {})
        runs.append({"tier": r["tier"], "title": r["title"], "label": r["label"], "prefix": pre,
                     "status": "done" if status == "done" else ("failed" if status == "failed" else "running"),
                     "error": None, "outputs": outs})
    outputs = runs[0]["outputs"] if runs and status == "done" else {"result_json": None, "result_xlsx": None, "plan_png": None, "plan_svg": None}
    return {"job_id": job["job_id"], "status": status, "stage": current, "stages": stages, "error": error,
            "outputs": outputs, "runs": runs}


def room_dims(sp: dict, i: int) -> tuple[float, float, float]:
    s = sp.get("sizes") or {}
    return (float(s.get("length") or 3.0 + 0.6 * i), float(s.get("width") or 2.8 + 0.3 * i), float(s.get("height") or 2.5))


def measurement(v: float, rel: float = 0.04, unit: str = "m") -> dict:
    sig = v * rel / 1.645
    return {"value": round(v, 3), "ci90": [round(v * (1 - rel), 3), round(v * (1 + rel), 3)], "sigma": round(sig, 4), "unit": unit}


def run_spaces(job: dict, run: dict | None) -> list[dict]:
    if run is None:
        run = job["runs"][0]
    return [s for s in job["spaces"] if s["space_id"] in run["space_ids"]]


def result_json(job: dict, run: dict | None = None) -> dict:
    run = run or job["runs"][0]
    spaces = run_spaces(job, run)
    rooms, x = [], 0.0
    for i, sp in enumerate(spaces):
        L, W, H = room_dims(sp, i)
        L, W, H = L * 1.02, W * 0.99, H * 1.01
        rid = f"room_{i + 1}"
        rooms.append({
            "id": rid, "label": sp["name"] if run["tier"] == "photos" else rid, "polygon": [[x, 0], [x + L, 0], [x + L, W], [x, W]],
            "floor_area": measurement(L * W, 0.06, "m2"), "perimeter": measurement(2 * (L + W)),
            "ceiling_height": measurement(H, 0.03), "ceiling_source": "ceiling_plane",
            "walls": [], "openings": [], "surfaces": [],
        })
        x += L
    fp = sum(r["floor_area"]["value"] for r in rooms)
    damage = [] if not job["damage"] or not rooms else [{
        "id": "dmg_1", "surface_id": "room_1_wall_2", "room_id": "room_1", "damage_class": "water_stain", "score": 0.81,
        "area": measurement(0.18, 0.2, "m2"), "extent_u": measurement(0.5, 0.2), "extent_v": measurement(0.36, 0.2),
        "center": [1.2, 0.0, 2.1], "evidence_frames": [3, 4]}]
    flags = [] if not damage else [{
        "id": "flag_1", "surface_id": "room_1_wall_2", "room_id": "room_1", "rule_id": "C1",
        "rule": "Water stain near the ceiling: possible leak above", "triggered_by": ["dmg_1"], "risk": "medium",
        "recommendation": "Check the floor above / roof for a leak."}]
    return {
        "schema_version": "1.0.0", "units": "metres; areas in square metres", "interval": "90 % (ci90)",
        "capture": {"id": job["pid"], "tier": {"photos": "photo"}.get(run["tier"], run["tier"]),
                    "source": "mock", "n_frames_used": sum(len(s["files"]) for s in spaces)},
        "property": {"footprint_area": measurement(fp, 0.05, "m2"), "bbox": measurement(x, 0.03),
                     "room_ids": [r["id"] for r in rooms], "adjacency": [], "stitch_method": "mock", "drift_correction": {}},
        "rooms": rooms, "damage": damage, "concealed_damage_flags": flags, "scope": [],
        "warnings": ["This is a mock result from web/dev/mock_server.py."],
        "timing_s": {s.split(": ")[-1]: round(job["stage_seconds"], 2) for s in job["stages"] if s.startswith(run["title"])},
    }


def comparison(job: dict) -> dict:
    rows = []
    for run in job["runs"]:
        for i, sp in enumerate(run_spaces(job, run)):
            rows += comparison_rows(sp, run)
    return {"rows": rows}


def comparison_rows(sp: dict, run: dict) -> list[dict]:
    rows = []
    if True:
        s = sp.get("sizes") or {}
        for q, f in (("length", 1.02), ("width", 0.99), ("height", 1.01)):
            g = s.get(q)
            if g is None:
                continue
            g = float(g)
            c = g * f * (1.08 if "far" in sp["name"].lower() else 1.0) * (1.03 if run["tier"] != "photos" else 1.0)
            rows.append({"space": sp["name"], "space_id": sp["space_id"], "tier": run["tier"], "run": run["title"],
                         "quantity": q, "given": g, "computed": round(c, 3),
                         "ci90": [round(c * 0.96, 3), round(c * 1.04, 3)], "diff": round(c - g, 3),
                         "diff_pct": round(100 * (c - g) / g, 2)})
    return rows


def plan_svg(job: dict, run: dict | None = None) -> bytes:
    res = result_json(job, run)
    W = max(1.0, sum(r["polygon"][1][0] - r["polygon"][0][0] for r in res["rooms"]))
    Hh = max([r["polygon"][2][1] for r in res["rooms"]] or [1.0])
    s = 80
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-20 -20 {W * s + 40:.0f} {Hh * s + 40:.0f}">',
             '<rect x="-20" y="-20" width="100%" height="100%" fill="#fff"/>']
    for r in res["rooms"]:
        (x0, y0), _, (x1, y1), _ = r["polygon"]
        parts.append(f'<rect x="{x0 * s:.1f}" y="{y0 * s:.1f}" width="{(x1 - x0) * s:.1f}" height="{(y1 - y0) * s:.1f}" fill="#eef3ff" stroke="#123" stroke-width="4"/>')
        parts.append(f'<text x="{(x0 + x1) / 2 * s:.1f}" y="{(y0 + y1) / 2 * s:.1f}" font-family="sans-serif" font-size="18" text-anchor="middle">{r["label"]} {r["floor_area"]["value"]:.1f} m²</text>')
    parts.append("</svg>")
    return "".join(parts).encode()


def plan_png(job: dict, run: dict | None = None) -> bytes:
    """A small solid PNG with one rectangle per room (no PIL needed)."""
    res = result_json(job, run)
    rooms = res["rooms"]
    W = max(1.0, sum(r["polygon"][1][0] - r["polygon"][0][0] for r in rooms))
    Hh = max([r["polygon"][2][1] for r in rooms] or [1.0])
    s, pad = 60, 20
    w, h = int(W * s) + 2 * pad, int(Hh * s) + 2 * pad
    px = bytearray(b"\xff" * (w * h * 3))

    def put(x, y, c):
        if 0 <= x < w and 0 <= y < h:
            px[(y * w + x) * 3:(y * w + x) * 3 + 3] = c

    for r in rooms:
        (x0, y0), _, (x1, y1), _ = r["polygon"]
        X0, Y0, X1, Y1 = int(x0 * s) + pad, int(y0 * s) + pad, int(x1 * s) + pad, int(y1 * s) + pad
        for y in range(Y0, Y1):
            for x in range(X0, X1):
                edge = min(x - X0, X1 - 1 - x, y - Y0, Y1 - 1 - y) < 3
                put(x, y, b"\x11\x22\x33" if edge else b"\xee\xf3\xff")
    raw = b"".join(b"\x00" + bytes(px[y * w * 3:(y + 1) * w * 3]) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def result_xlsx(job: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("README.txt", "Mock result.xlsx placeholder from web/dev/mock_server.py\n")
    return buf.getvalue()


# ---------------------------------------------------------------- HTTP
class ApiErr(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def space_out(sp: dict) -> dict:
    return {k: sp[k] for k in ("space_id", "name", "kind", "sizes", "files")}


def get_project(pid: str) -> dict:
    p = PROJECTS.get(pid)
    if not p:
        raise ApiErr(404, f"project {pid} not found")
    return p


def get_space(p: dict, sid: str) -> dict:
    if sid in SID_CAP:
        kind = SID_CAP[sid]
        if not p["captures"].get(kind):
            raise ApiErr(404, f"the project has no whole-home {kind} capture")
        return p["captures"][kind]
    for sp in p["spaces"]:
        if sp["space_id"] == sid:
            return sp
    raise ApiErr(404, f"space {sid} not found")


def cap_out(p: dict, kind: str):
    c = p["captures"].get(kind)
    return {"kind": c["kind"], "files": c["files"]} if c else None


def caps_out(p: dict) -> dict:
    return {k: cap_out(p, k) for k in ("video", "lidar")}


def present_caps(p: dict) -> list[dict]:
    return [c for c in (p["captures"].get("video"), p["captures"].get("lidar")) if c]


def has_input(p: dict) -> bool:
    return any(s["files"] for s in p["spaces"]) or any(c["files"] for c in present_caps(p))


def plan_runs(p: dict) -> list[dict]:
    """[rooms with photos] + [whole-home video] + [whole-home LiDAR]; output prefixes only with two+ runs."""
    runs = []
    photos = [s["space_id"] for s in p["spaces"] if s["files"]]
    if photos:
        runs.append({"tier": "photos", "title": TITLES["photos"], "label": "photos", "space_ids": photos})
    for c in present_caps(p):
        if not c["files"]:
            continue
        runs.append({"tier": c["kind"], "title": TITLES[c["kind"]], "label": f"whole_home_{c['kind']}",
                     "space_ids": [s["space_id"] for s in p["spaces"]]})
    for r in runs:
        r["prefix"] = f"{r['label']}/" if len(runs) > 1 else ""
    return runs


def clean_sizes(s) -> dict:
    s = s or {}
    out = {}
    for k in ("length", "width", "height"):
        v = s.get(k)
        if v is not None:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ApiErr(422, f"sizes.{k} must be a number")
            if v <= 0:
                raise ApiErr(422, f"sizes.{k} must be positive")
        out[k] = v
    return out


def parse_multipart(ctype: str, body: bytes) -> dict:
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    out = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        out[name] = {"filename": part.get_filename(), "data": part.get_payload(decode=True) or b""}
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "roomscan-mock/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter
        if not getattr(self.server, "quiet", False):
            super().log_message(fmt, *args)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _jbody(self) -> dict:
        b = self._body()
        if not b:
            return {}
        try:
            return json.loads(b)
        except json.JSONDecodeError:
            raise ApiErr(422, "body is not JSON")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self): self._dispatch()
    def do_POST(self): self._dispatch()
    def do_PUT(self): self._dispatch()
    def do_PATCH(self): self._dispatch()
    def do_DELETE(self): self._dispatch()

    ROUTES = [
        ("GET", r"/api/health", "health"),
        ("POST", r"/api/projects", "create_project"),
        ("GET", r"/api/projects/(?P<pid>[^/]+)", "project"),
        ("POST", r"/api/projects/(?P<pid>[^/]+)/spaces", "create_space"),
        ("PATCH", r"/api/projects/(?P<pid>[^/]+)/spaces/(?P<sid>[^/]+)", "patch_space"),
        ("PUT", r"/api/projects/(?P<pid>[^/]+)/order", "set_order"),
        ("DELETE", r"/api/projects/(?P<pid>[^/]+)/spaces/(?P<sid>[^/]+)", "delete_space"),
        ("PUT", r"/api/projects/(?P<pid>[^/]+)/captures/(?P<kind>[^/]+)", "put_capture"),
        ("GET", r"/api/projects/(?P<pid>[^/]+)/captures/(?P<kind>[^/]+)", "get_capture"),
        ("DELETE", r"/api/projects/(?P<pid>[^/]+)/captures/(?P<kind>[^/]+)", "delete_capture"),
        ("GET", r"/api/projects/(?P<pid>[^/]+)/captures/(?P<sid>[^/]+)/files", "list_files"),
        ("PUT", r"/api/projects/(?P<pid>[^/]+)/captures/(?P<sid>[^/]+)/files", "put_file"),
        ("DELETE", r"/api/projects/(?P<pid>[^/]+)/captures/(?P<sid>[^/]+)/files/(?P<sha>[0-9a-fA-F]+)", "delete_file"),
        ("GET", r"/api/projects/(?P<pid>[^/]+)/spaces/(?P<sid>[^/]+)/files", "list_files"),
        ("PUT", r"/api/projects/(?P<pid>[^/]+)/spaces/(?P<sid>[^/]+)/files", "put_file"),
        ("DELETE", r"/api/projects/(?P<pid>[^/]+)/spaces/(?P<sid>[^/]+)/files/(?P<sha>[0-9a-fA-F]+)", "delete_file"),
        ("POST", r"/api/projects/(?P<pid>[^/]+)/verify", "verify"),
        ("POST", r"/api/projects/(?P<pid>[^/]+)/run", "run"),
        ("GET", r"/api/jobs/(?P<jid>[^/]+)", "job"),
        ("GET", r"/api/jobs/(?P<jid>[^/]+)/comparison", "comparison"),
        ("GET", r"/api/jobs/(?P<jid>[^/]+)/files/(?P<name>.+)", "job_file"),
    ]

    def _dispatch(self):
        path = unquote(urlparse(self.path).path).rstrip("/") or "/"
        try:
            for method, pat, fn in self.ROUTES:
                m = re.fullmatch(pat, path)
                if m and method == self.command:
                    with LOCK:
                        return getattr(self, "h_" + fn)(**m.groupdict())
            if any(re.fullmatch(p, path) for _, p, _ in self.ROUTES):
                raise ApiErr(405, "method not allowed")
            raise ApiErr(404, "not found")
        except ApiErr as e:
            self._json(e.status, {"detail": e.detail})
        except Exception as e:  # pragma: no cover
            self._json(500, {"detail": f"mock crashed: {e!r}"})

    # --- handlers
    def h_health(self):
        self._json(200, {"ok": True, "mock": True, "version": "mock"})

    def h_create_project(self):
        self._body()
        pid = new_id("p")
        PROJECTS[pid] = {"project_id": pid, "spaces": [], "captures": {"video": None, "lidar": None}, "last_job_id": None, "verify": None}
        self._json(200, {"project_id": pid})

    def h_project(self, pid):
        p = get_project(pid)
        self._json(200, {"project_id": pid, "spaces": [space_out(s) for s in p["spaces"]], "captures": caps_out(p),
                         "last_job_id": p["last_job_id"]})

    def h_put_capture(self, pid, kind):
        p = get_project(pid)
        self._body()
        if kind not in CAP_SID:
            raise ApiErr(422, "kind must be video or lidar")
        if not p["captures"].get(kind):
            p["captures"][kind] = {"space_id": CAP_SID[kind], "name": f"whole home ({kind})", "kind": kind, "sizes": {}, "files": []}
            p["verify"] = None
        self._json(200, cap_out(p, kind))

    def h_get_capture(self, pid, kind):
        p = get_project(pid)
        if kind not in CAP_SID:
            raise ApiErr(404, "no such capture kind")
        get_space(p, CAP_SID[kind])
        self._json(200, cap_out(p, kind))

    def h_delete_capture(self, pid, kind):
        p = get_project(pid)
        if kind not in CAP_SID:
            raise ApiErr(404, "no such capture kind")
        get_space(p, CAP_SID[kind])
        p["captures"][kind] = None
        p["verify"] = None
        self._json(200, {"ok": True})

    def h_create_space(self, pid):
        p = get_project(pid)
        b = self._jbody()
        kind = b.get("kind") or "photos"
        if kind in ("video", "lidar"):
            raise ApiErr(422, f"{kind} is a whole-home capture: PUT /api/projects/{pid}/captures/{kind}")
        if kind != "photos":
            raise ApiErr(422, "kind must be photos")
        name = (b.get("name") or f"Room {len(p['spaces']) + 1}").strip()
        sp = {"space_id": new_id("s"), "name": name, "kind": kind, "sizes": clean_sizes(b.get("sizes")), "files": []}
        p["spaces"].append(sp)
        p["verify"] = None
        self._json(200, space_out(sp))

    def h_patch_space(self, pid, sid):
        p = get_project(pid)
        sp = get_space(p, sid)
        b = self._jbody()
        if "name" in b and b["name"]:
            sp["name"] = str(b["name"]).strip()
        if "sizes" in b:
            sp["sizes"] = clean_sizes(b["sizes"])
        self._json(200, space_out(sp))

    def h_set_order(self, pid):
        p = get_project(pid)
        ids = self._jbody().get("space_ids") or []
        known = {s["space_id"]: s for s in p["spaces"]}
        if sorted(ids) != sorted(known):
            raise ApiErr(400, "space_ids must list every space of the project once")
        p["spaces"] = [known[i] for i in ids]
        p["verify"] = None
        self._json(200, {"project_id": pid, "spaces": [space_out(s) for s in p["spaces"]]})

    def h_delete_space(self, pid, sid):
        p = get_project(pid)
        get_space(p, sid)
        p["spaces"] = [s for s in p["spaces"] if s["space_id"] != sid]
        p["verify"] = None
        self._json(200, {"ok": True})

    def h_list_files(self, pid, sid):
        sid = CAP_SID.get(sid, sid)
        sp = get_space(get_project(pid), sid)
        self._json(200, {"files": sp["files"]})

    def h_put_file(self, pid, sid):
        sid = CAP_SID.get(sid, sid)
        p = get_project(pid)
        sp = get_space(p, sid)
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            raise ApiErr(415, "expected multipart/form-data")
        parts = parse_multipart(ctype, self._body())
        if "file" not in parts:
            raise ApiErr(422, "missing field: file")
        data = parts["file"]["data"]
        sha = hashlib.sha256(data).hexdigest()
        claimed = (parts.get("sha256", {}).get("data") or b"").decode().strip().lower()
        if claimed and claimed != sha:
            raise ApiErr(422, f"sha256 mismatch: sent {claimed[:12]}, got {sha[:12]}")
        name = (parts.get("name", {}).get("data") or b"").decode() or parts["file"]["filename"] or sha[:12]
        for f in sp["files"]:
            if f["sha256"] == sha:
                return self._json(200, f)
        if sid in SID_CAP and sp["files"]:
            raise ApiErr(409, f"the whole-home {'video' if sp['kind'] == 'video' else 'LiDAR scan (.zip)'} already has a file: "
                              f"{sp['files'][0]['name']}. Delete it first to replace it")
        rec = {"name": name, "sha256": sha, "size": len(data)}
        sp["files"].append(rec)
        p["verify"] = None
        self._json(200, rec)

    def h_delete_file(self, pid, sid, sha):
        sid = CAP_SID.get(sid, sid)
        p = get_project(pid)
        sp = get_space(p, sid)
        n = len(sp["files"])
        sp["files"] = [f for f in sp["files"] if f["sha256"] != sha.lower()]
        if len(sp["files"]) == n:
            raise ApiErr(404, "file not found")
        p["verify"] = None
        self._json(200, {"ok": True})

    def h_verify(self, pid):
        p = get_project(pid)
        self._body()
        caps = present_caps(p)
        spaces = [verify_space(s, any(c["files"] for c in caps)) for s in p["spaces"]]
        captures = []
        for cap in caps:
            v = verify_space(cap)
            captures.append({"kind": cap["kind"], "name": f"whole home: {'video' if cap['kind'] == 'video' else 'LiDAR'}",
                             "status": v["status"], "findings": v["findings"],
                             "advice": None if v["status"] == "ok" else "Capture the whole home again: walk slowly through every room, tilt up to the ceiling once per room."})
        pf = []
        if not has_input(p):
            pf.append({"level": "retake", "check": "nothing to compute", "message": "Add photos to a room, or a whole-home video / LiDAR scan.", "files": []})
        ok = (not any(s["status"] == "retake" for s in spaces) and not any(f["level"] == "retake" for f in pf)
              and not any(c["status"] == "retake" for c in captures))
        p["verify"] = {"ok": ok}
        self._json(200, {"ok": ok, "spaces": spaces, "captures": captures, "project_findings": pf})

    def h_run(self, pid):
        p = get_project(pid)
        b = self._jbody()
        force = bool(b.get("force"))
        if p["verify"] is not None and not p["verify"]["ok"] and not force:
            raise ApiErr(409, "verify found a space that needs a retake; fix it or pass force=true")
        if not has_input(p):
            raise ApiErr(409, "nothing to compute: add photos to a room, or one whole-home video / LiDAR scan")
        allsp = p["spaces"] + present_caps(p)
        key = hashlib.sha256(json.dumps([[s["name"], s["kind"], s["sizes"], sorted(f["sha256"] for f in s["files"])]
                                         for s in allsp], sort_keys=True).encode()).hexdigest()
        for j in JOBS.values():
            if j["pid"] == pid and j["key"] == key and job_view(j)["status"] == "done":
                p["last_job_id"] = j["job_id"]
                return self._json(200, {"job_id": j["job_id"], "cached": True})
        jid = new_id("j")
        JOBS[jid] = {"job_id": jid, "pid": pid, "key": key, "started": time.time(), "cached": False,
                     "damage": bool(b.get("damage", True)), "stage_seconds": self.server.stage_seconds,
                     "fail": any("fail" in s["name"].lower() for s in p["spaces"]),
                     "spaces": json.loads(json.dumps(p["spaces"])),
                     "runs": plan_runs(p)}
        JOBS[jid]["stages"] = [f"{r['title']}: {st}" for r in JOBS[jid]["runs"]
                               for st in (STAGES_PHOTO if r["tier"] == "photos" else STAGES_3D)]
        p["last_job_id"] = jid
        self._json(200, {"job_id": jid, "cached": False})

    def _job(self, jid):
        j = JOBS.get(jid)
        if not j:
            raise ApiErr(404, f"job {jid} not found")
        return j

    def h_job(self, jid):
        self._json(200, job_view(self._job(jid)))

    def h_comparison(self, jid):
        self._json(200, comparison(self._job(jid)))

    def h_job_file(self, jid, name):
        j = self._job(jid)
        if job_view(j)["status"] != "done":
            raise ApiErr(409, "job not finished")
        run = next((r for r in j["runs"] if r["prefix"] and name.startswith(r["prefix"])), j["runs"][0])
        name = name[len(run["prefix"]):] if run["prefix"] and name.startswith(run["prefix"]) else name
        makers = {
            "result.json": (lambda: json.dumps(result_json(j, run), indent=1).encode(), "application/json"),
            "result.xlsx": (lambda: result_xlsx(j), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "plan.png": (lambda: plan_png(j, run), "image/png"),
            "plan.svg": (lambda: plan_svg(j, run), "image/svg+xml"),
        }
        if name not in makers:
            raise ApiErr(404, f"no file {name}")
        fn, ctype = makers[name]
        self._send(200, fn(), ctype)


def make_server(port: int = 8000, host: str = "127.0.0.1", stage_seconds: float = STAGE_SECONDS, quiet: bool = False):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.stage_seconds = stage_seconds
    srv.quiet = quiet
    srv.daemon_threads = True
    if quiet:  # e.g. browser killed mid-request in tests
        srv.handle_error = lambda request, client_address: None
    return srv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach it from a phone on the same Wi-Fi")
    ap.add_argument("--stage-seconds", type=float, default=STAGE_SECONDS)
    a = ap.parse_args()
    srv = make_server(a.port, a.host, a.stage_seconds)
    print(f"roomscan mock API on http://{a.host}:{a.port}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
