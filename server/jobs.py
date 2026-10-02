"""Jobs: one worker thread runs the engine on one job at a time (the models need the
memory), job state is a JSON file updated as stages progress."""
from __future__ import annotations

import gc
import inspect
import queue
import shutil
import threading
import time
import traceback
from pathlib import Path

from server.capture import content_key, given_sizes, materialise, n_retake, room_measurements, verify_project
from server.store import QUANTITIES, Store, new_id, read_json

OUTPUT_FILES = {"result_json": "result.json", "result_xlsx": "result.xlsx", "plan_png": "plan.png",
                "plan_svg": "plan.svg"}


def run_prefix(job: dict, run: dict) -> str:
    return f"{run['label']}/" if len(job["runs"]) > 1 else ""


def engine_reports_stages() -> bool:
    import roomscan.pipeline as pl
    return "on_stage" in inspect.signature(pl.run).parameters


def run_stage_plan(engine_tier: str, damage: bool) -> list[str]:
    """The stages one engine run reports: the engine's own plan when it calls on_stage,
    else the coarse load+process / export."""
    if engine_reports_stages():
        try:
            from roomscan.stages import PLAN
            plan = list(PLAN.get(engine_tier, []))
        except ImportError:
            plan = []
        return [s for s in plan if damage or s != "damage"]
    return ["load+process", "export"]


def stage_name(run: dict, stage: str) -> str:
    """Stage names carry the run: "Rooms (photos): load+depth", "Whole home (video): fuse"."""
    return f"{run['title']}: {stage}"


def new_job(p: dict, runs: list[dict], damage: bool, force: bool, key: str, engine: str) -> dict:
    multi = len(runs) > 1
    stages = [{"name": "verify", "status": "pending", "seconds": None, "note": None}]
    for r in runs:
        for st in run_stage_plan(r["engine_tier"], damage):
            stages.append({"name": stage_name(r, st), "status": "pending", "seconds": None, "note": None})
    return {"job_id": new_id(), "project_id": p["project_id"], "created": time.time(), "started": None,
            "finished": None, "status": "queued", "stage": None, "stages": stages, "error": None,
            "outputs": {}, "damage": bool(damage), "force": bool(force), "cache_key": key, "engine": engine,
            "spaces": p["spaces"], "captures": p.get("captures"),
            "runs": [{"tier": r["tier"], "title": r["title"], "label": r["label"], "space_ids": r["space_ids"],
                      "whole_home": bool(r.get("whole_home")),
                      "folders": r["folders"], "status": "pending", "error": None, "outputs": {},
                      "prefix": f"{r['label']}/" if multi else ""} for r in runs]}


def public_job(j: dict) -> dict:
    keys = ("job_id", "project_id", "status", "stage", "stages", "error", "outputs", "runs", "damage",
            "created", "started", "finished")
    out = {k: j.get(k) for k in keys}
    out["runs"] = [{k: v for k, v in r.items() if k != "folders"} for r in j.get("runs", [])]
    return out


class Worker:
    def __init__(self, store: Store, engine_cache: Path | None = None):
        self.store = store
        self.q: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.engine_cache = engine_cache
        self._saved_cache: dict = {}

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._redirect_engine_cache()
        for j in sorted(self.store.jobs(), key=lambda j: j.get("created", 0)):
            if j["status"] == "running":
                self._fail(j, "server restarted while this job was running: run it again")
            elif j["status"] == "queued":
                self.q.put(j["job_id"])
        self.thread = threading.Thread(target=self._loop, name="roomscan-worker", daemon=True)
        self.thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.q.put(None)
        if self.thread:
            self.thread.join(timeout)
        self._restore_engine_cache()

    def _redirect_engine_cache(self) -> None:
        """The engine caches under ./.cache by default; keep its cache in DATA_DIR instead."""
        if self.engine_cache is None:
            return
        self.engine_cache.mkdir(parents=True, exist_ok=True)
        import roomscan.pipeline as pl
        self._saved_cache["pipeline"] = pl.CACHE_DIR
        pl.CACHE_DIR = self.engine_cache
        try:
            import roomscan.frontends.video as vid
        except Exception:  # video front end needs the ml extra
            return
        self._saved_cache["video"] = vid.CACHE
        vid.CACHE = self.engine_cache

    def _restore_engine_cache(self) -> None:
        import roomscan.pipeline as pl
        if "pipeline" in self._saved_cache:
            pl.CACHE_DIR = self._saved_cache.pop("pipeline")
        if "video" in self._saved_cache:
            import roomscan.frontends.video as vid
            vid.CACHE = self._saved_cache.pop("video")

    def submit(self, job: dict) -> None:
        self.store.save_job(job)
        self.q.put(job["job_id"])

    def _loop(self) -> None:
        while True:
            jid = self.q.get()
            if jid is None:
                return
            try:
                self.run_job(jid)
            except Exception as e:  # never let the worker die
                try:
                    self._fail(self.store.job(jid), f"internal error: {type(e).__name__}: {e}")
                except Exception:
                    pass
            finally:
                gc.collect()

    # ---------------------------------------------------------------- job state
    def _save(self, j: dict) -> None:
        with self.store.lock:
            self.store.save_job(j)

    def _fail(self, j: dict, msg: str) -> None:
        j["status"], j["error"], j["finished"] = "failed", msg, time.time()
        for st in j["stages"]:
            if st["status"] == "running":
                st["status"] = "failed"
        self._save(j)

    def _stage(self, j: dict, name: str, status: str, seconds: float | None = None, note: str | None = None):
        st = next((s for s in j["stages"] if s["name"] == name), None)
        if st is None:
            # a stage not in the plan: after the last stage of the same run (same "label: " prefix)
            st = {"name": name, "status": "pending", "seconds": None, "note": None}
            pre = name.split(": ", 1)[0] + ": " if ": " in name else ""
            idx = max((i for i, s in enumerate(j["stages"]) if s["name"] != "verify"
                       and s["name"].startswith(pre)), default=len(j["stages"]) - 1) + 1
            j["stages"].insert(idx, st)
        st["status"] = status
        if seconds is not None:
            st["seconds"] = round(float(seconds), 2)
        if note is not None:
            st["note"] = str(note)
        if status == "running":
            j["stage"] = name
        self._save(j)

    # ---------------------------------------------------------------- run
    def run_job(self, jid: str) -> None:
        import roomscan.pipeline as pl
        from roomscan.export.sheet import write_sheet

        st = self.store
        j = st.job(jid)
        if j["status"] != "queued":
            return
        j["status"], j["started"] = "running", time.time()
        self._save(j)
        jdir = st.jdir(jid)
        work, out = jdir / "work", jdir / "out"
        shutil.rmtree(work, ignore_errors=True)
        try:
            # ---- verify (on the capture as the engine will see it)
            t = time.time()
            self._stage(j, "verify", "running")
            p = {"project_id": j["project_id"], "spaces": j["spaces"], "captures": j.get("captures") or {}}
            runs = materialise(st, p, work)
            try:
                proj = st.project(j["project_id"])
                last = proj.get("last_verify")
            except KeyError:
                last = None
            if last and last.get("content_key") == content_key(p):
                ver = last["result"]
            else:
                ver = verify_project(st, p, runs=runs)
            n_bad = n_retake(ver)
            note = f"{n_bad} retake finding(s)" if n_bad else "ok"
            if n_bad and not j["force"]:
                self._stage(j, "verify", "failed", time.time() - t, note)
                self._fail(j, f"capture check found {n_bad} problem(s) that need a retake: run verify for "
                              f"details, or run with force")
                return
            self._stage(j, "verify", "done", time.time() - t, note + (" (forced)" if n_bad else ""))

            # ---- the photo walk, then each whole-home capture
            params = inspect.signature(pl.run).parameters
            live = "on_stage" in params  # the engine reports its own stages
            try:
                from roomscan.stages import StageFailed
            except ImportError:
                StageFailed = pl.InputError
            errors = []
            for r, jr in zip(runs, j["runs"]):
                odir = out / jr["prefix"] if jr["prefix"] else out
                mine = [s for s in j["stages"] if s["name"].startswith(r["title"] + ": ")]
                jr["status"] = "running"
                if r.get("error"):
                    jr["status"], jr["error"] = "failed", r["error"]
                    self._stage(j, mine[0]["name"], "failed", note=r["error"])
                    errors.append(f"{r['title']}: {r['error']}")
                    continue
                t = time.time()
                kw = dict(tier=r["engine_tier"], damage=j["damage"], progress=False)
                if live:
                    def on_stage(name, status, seconds=None, note=None, _r=r):
                        self._stage(j, stage_name(_r, str(name)), str(status), seconds, note)
                    kw["on_stage"] = on_stage
                else:
                    self._stage(j, stage_name(r, "load+process"), "running")
                if "measurements" in params and r.get("whole_home"):
                    # photos: measurements.yaml (by folder name) sits in the capture folder; the
                    # whole-home clip or scan has rooms room_1.., so every room's sizes go as an
                    # unnamed list the engine matches on aspect and size (LiDAR: compared only)
                    meas = room_measurements(j["spaces"])
                    if meas:
                        kw["measurements"] = meas
                try:
                    res = pl.run(Path(r["path"]), odir, **kw)
                except Exception as e:
                    user = isinstance(e, (pl.InputError, StageFailed))
                    msg = str(e) if user else f"{type(e).__name__}: {e}"
                    if not user:
                        traceback.print_exc()
                    jr["status"], jr["error"] = "failed", msg
                    if not any(s["status"] == "failed" for s in mine):
                        first = next((s for s in mine if s["status"] != "done"), mine[0])
                        self._stage(j, first["name"], "failed", time.time() - t, msg[:300])
                    errors.append(f"{r['title']}: {msg}")
                    gc.collect()
                    continue
                if not live:
                    self._stage(j, stage_name(r, "load+process"), "done", time.time() - t)
                    t = time.time()
                    self._stage(j, stage_name(r, "export"), "running")
                if not isinstance(res, dict):
                    res = read_json(odir / "result.json")
                write_sheet(res, odir / "result.xlsx")
                jr["status"] = "done"
                jr["n_rooms"] = len(res.get("rooms", []))
                jr["warnings"] = list(res.get("warnings", []))[:50]
                jr["outputs"] = {k: f"/api/jobs/{jid}/files/{jr['prefix']}{fn}" for k, fn in OUTPUT_FILES.items()
                                 if (odir / fn).is_file()}
                if not live:
                    self._stage(j, stage_name(r, "export"), "done", time.time() - t)
                del res
                gc.collect()
            done = [jr for jr in j["runs"] if jr["status"] == "done"]
            j["stage"] = None
            if not done:
                self._fail(j, "; ".join(errors) or "nothing to run")
                return
            j["outputs"] = dict(done[0]["outputs"])
            j["error"] = "; ".join(errors) or None
            j["status"], j["finished"] = "done", time.time()
            self._save(j)
        except Exception as e:
            traceback.print_exc()
            self._fail(j, f"{type(e).__name__}: {e}")
        finally:
            shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- comparison

def _bbox_dims(room: dict):
    """(length, width, ci90 of length, ci90 of width) from the room polygon in its plan frame;
    the intervals take the half-widths of the longest wall along each side."""
    xs = [p[0] for p in room["polygon"]]
    ys = [p[1] for p in room["polygon"]]
    if not xs:
        return None
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)

    def ci_along(axis: int, value: float):
        best = None
        for w in room.get("walls", []):
            d = (abs(w["end"][0] - w["start"][0]), abs(w["end"][1] - w["start"][1]))
            if d[axis] < 2 * d[1 - axis]:
                continue
            m = w.get("length") or {}
            if m.get("value") is None or not m.get("ci90"):
                continue
            if best is None or m["value"] > best["value"]:
                best = m
        if best is None:
            return None
        lo, hi = best["value"] - best["ci90"][0], best["ci90"][1] - best["value"]
        return [round(value - lo, 3), round(value + hi, 3)]

    cx, cy = ci_along(0, dx), ci_along(1, dy)
    return (dx, cx, dy, cy) if dx >= dy else (dy, cy, dx, cx)


def _dims(room: dict) -> dict:
    """{length, width, height} computed for a result room, each (value, ci90)."""
    comp: dict = {q: (None, None) for q in QUANTITIES}
    dims = _bbox_dims(room)
    if dims:
        comp["length"] = (round(dims[0], 3), dims[1])
        comp["width"] = (round(dims[2], 3), dims[3])
    ch = room.get("ceiling_height") or {}
    if ch.get("value") is not None:
        comp["height"] = (round(ch["value"], 3), list(ch["ci90"]) if ch.get("ci90") else None)
    return comp


def match_rooms(rooms: list[dict], spaces: list[dict]) -> dict[str, dict]:
    """Whole-home run: user room (space_id) -> the engine room its sizes describe, matched as the
    engine does (roomscan.known_sizes.assign: aspect ratio, then size rank). A room with only a
    height gets the largest room not matched otherwise; a room without sizes gets none."""
    from roomscan import known_sizes as KS

    dims, by_name = [], {}
    for r in rooms:
        d = _dims(r)
        if d["length"][0] is None:
            continue
        dims.append(KS.RoomDims(r["id"], d["length"][0], d["width"][0], d["height"][0]))
        by_name[r["id"]] = r
    known, owner = [], {}
    for s in spaces:
        g = given_sizes(s)
        if g.get("length") is None and g.get("width") is None:
            continue
        L, W = g.get("length"), g.get("width")
        if L is not None and W is not None and W > L:
            L, W = W, L
        k = KS.Known(label=f"#{len(known) + 1}", length=L, width=W, height=g.get("height"))
        known.append(k)
        owner[id(k)] = s["space_id"]
    out: dict[str, dict] = {}
    if known and dims:
        try:
            got = KS.assign(KS.KnownSizes(listed=known), dims, [])
        except Exception:  # scipy missing or odd input: fall back to floor area below
            got = {}
        for name, k in got.items():
            if id(k) in owner:
                out[owner[id(k)]] = by_name[name]
    area = lambda r: (r.get("floor_area") or {}).get("value") or 0.0  # noqa: E731
    used = {r["id"] for r in out.values()}
    for s in spaces:
        g = given_sizes(s)
        if s["space_id"] in out or not g:
            continue
        free = [r for r in rooms if r["id"] not in used] or rooms
        if g.get("length") and g.get("width"):
            r = min(free, key=lambda r: abs(area(r) - g["length"] * g["width"]))
        else:
            r = max(free, key=area)
        out[s["space_id"]] = r
        used.add(r["id"])
    return out


def comparison(store: Store, j: dict) -> dict:
    """Rows per user room and quantity for every run that produced rooms, with its tier."""
    try:
        current = {s["space_id"]: s for s in store.project(j["project_id"])["spaces"]}
    except KeyError:
        current = {}
    snap = {s["space_id"]: s for s in j["spaces"]}
    out_dir = store.jdir(j["job_id"]) / "out"
    rows = []
    for jr in j["runs"]:
        try:
            res = read_json(out_dir / jr["prefix"] / "result.json") if jr["status"] == "done" else None
        except (OSError, ValueError):
            res = None
        rooms = (res or {}).get("rooms") or []
        if not rooms:
            continue
        spaces = [s for s in (current.get(sid) or snap.get(sid) for sid in jr["space_ids"]) if s is not None]
        if jr["tier"] == "photos":
            by_id = {r["id"]: r for r in rooms}
            match = {s["space_id"]: by_id.get(jr["folders"].get(s["space_id"])) for s in spaces}
        else:
            match = match_rooms(rooms, spaces)
        for s in spaces:
            sizes = s.get("sizes") or {}
            room = match.get(s["space_id"])
            comp = _dims(room) if room else {q: (None, None) for q in QUANTITIES}
            for q in QUANTITIES:
                given, (val, ci) = sizes.get(q), comp[q]
                diff = round(val - given, 3) if given is not None and val is not None else None
                rows.append({"space": s["name"], "space_id": s["space_id"], "tier": jr["tier"],
                             "run": jr.get("title", jr["label"]), "room_id": room["id"] if room else None,
                             "quantity": q, "given": given, "computed": val, "ci90": ci, "diff": diff,
                             "diff_pct": round(100 * diff / given, 1) if diff is not None and given else None})
    return {"rows": rows}
