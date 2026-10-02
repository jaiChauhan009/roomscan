"""The processing stages as a queue with a gate after each one.

Each tier runs a fixed list of stages. A stage is announced as running, then done with its
time; a gate then checks the stage's output and records ok / warn / fail with a note in plain
words. A failed gate stops the run with an error naming the stage. Everything is reported to
an optional callback (the web server shows it live) and written to stages.json next to
result.json. The expensive stages keep their own caches (drift, fused cloud, video depth),
which act as checkpoints: a re-run with the same input resumes from them.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

PLAN = {
    "lidar": ["load", "drift", "fuse", "layout", "openings", "damage", "export"],
    "video": ["load", "drift", "fuse", "layout", "openings", "damage", "export"],
    "photo": ["load+depth", "room_fit", "stitch", "openings", "damage", "export"],
}

OnStage = Callable[[str, str, float | None, str | None], None]  # name, status, seconds, note


class StageFailed(RuntimeError):
    """A stage's output failed its gate; the message names the stage and why."""


class Stages(dict):
    """Drop-in for the pipelines' `timing` dict: setting timing[name] = seconds marks that
    stage done (and the next planned stage running)."""

    def __init__(self, tier: str, on_stage: OnStage | None = None, skip: tuple = ()):
        super().__init__()
        self.plan = [s for s in PLAN.get(tier, []) if s not in skip]
        self.on_stage = on_stage
        self.records: dict[str, dict] = {s: {"name": s, "status": "pending", "seconds": None, "gate": None,
                                              "note": None} for s in self.plan}
        self.t0 = time.time()
        if self.plan:
            self._set(self.plan[0], "running")

    def _emit(self, name: str) -> None:
        r = self.records[name]
        if self.on_stage is not None:
            try:
                self.on_stage(name, r["status"], r["seconds"], r["note"])
            except Exception:  # noqa: BLE001 - a broken listener must not stop the run
                pass

    def _set(self, name: str, status: str) -> None:
        if name not in self.records:
            self.records[name] = {"name": name, "status": status, "seconds": None, "gate": None, "note": None}
            self.plan.append(name)
        self.records[name]["status"] = status
        self._emit(name)

    def __setitem__(self, name: str, seconds: float) -> None:
        super().__setitem__(name, seconds)
        if name not in self.records:
            self.records[name] = {"name": name, "status": "running", "seconds": None, "gate": None, "note": None}
            self.plan.append(name)
        self.records[name]["seconds"] = round(float(seconds), 2)
        self._set(name, "done")
        i = self.plan.index(name)
        nxt = next((s for s in self.plan[i + 1:] if self.records[s]["status"] == "pending"), None)
        if nxt:
            self._set(nxt, "running")

    def skip(self, name: str, note: str) -> None:
        if name in self.records and self.records[name]["status"] in ("pending", "running"):
            self.records[name].update(status="skipped", note=note)
            self._emit(name)
            i = self.plan.index(name)
            nxt = next((s for s in self.plan[i + 1:] if self.records[s]["status"] == "pending"), None)
            if nxt:
                self._set(nxt, "running")

    def gate(self, name: str, level: str, note: str) -> None:
        """Record a stage's check: level ok | warn | fail. fail stops the run."""
        rec = self.records.setdefault(name, {"name": name, "status": "done", "seconds": None, "gate": None,
                                             "note": None})
        rec["gate"], rec["note"] = level, note
        if level == "fail":
            rec["status"] = "failed"
        self._emit(name)
        if level == "fail":
            raise StageFailed(f"stage '{name}' failed its check: {note}")

    def fail_running(self, note: str) -> None:
        for r in self.records.values():
            if r["status"] == "running":
                r.update(status="failed", note=note)
                self._emit(r["name"])

    def write(self, out_dir: Path) -> None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        data = {"total_seconds": round(time.time() - self.t0, 2),
                "stages": [self.records[s] for s in self.plan]}
        (Path(out_dir) / "stages.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- gates

def gate_load(st: Stages, name: str, n_frames: int, tier: str) -> None:
    need = 2 if tier == "photo" else 10
    if n_frames < need:
        st.gate(name, "fail", f"only {n_frames} usable frame(s): the capture is too short or unreadable; retake it")
    st.gate(name, "ok", f"{n_frames} frames")


def gate_drift(st: Stages, info: dict) -> None:
    if not info.get("enabled"):
        st.gate("drift", "ok", "off")
        return
    mv = info.get("max_submap_shift_m") or info.get("largest_move_m")
    loops = info.get("loop_edges") or info.get("n_loops")
    note = f"{loops if loops is not None else 0} loop closure(s)" + (f", largest correction {mv:.2f} m" if mv else "")
    st.gate("drift", "warn" if mv and mv > 1.0 else "ok",
            note + ("; a large correction: the walk may have lost tracking" if mv and mv > 1.0 else ""))


def gate_fuse(st: Stages, n_points: int) -> None:
    if n_points < 5000:
        st.gate("fuse", "fail", f"only {n_points} 3D points: depth missing or the capture too short")
    st.gate("fuse", "ok", f"{n_points:,} points")


def gate_layout(st: Stages, layout, name: str = "layout") -> None:
    from shapely.geometry import Polygon
    rooms = list(getattr(layout, "rooms", []))
    if not rooms:
        st.gate(name, "warn", "no closed room found: walls not seen all round; the plan is incomplete")
        return
    polys = [Polygon(r.polygon) for r in rooms]
    overlap = sum(a.intersection(b).area for i, a in enumerate(polys) for b in polys[i + 1:])
    walls = [w for r in rooms for w in r.walls]
    bare = sum(getattr(w, "coverage", 1.0) == 0 for w in walls)
    level = "warn" if overlap > 0.1 else "ok"
    st.gate(name, level, f"{len(rooms)} room(s), {len(walls)} walls ({bare} without wall evidence)"
            + (f"; rooms overlap by {overlap:.2f} m2" if overlap > 0.1 else ""))


def gate_openings(st: Stages, openings: list) -> None:
    odd = [o for o in openings if not 0.3 <= getattr(o, "width", 1.0) <= 4.0]
    st.gate("openings", "warn" if odd else "ok",
            f"{len(openings)} opening(s)" + (f"; {len(odd)} with an implausible width" if odd else ""))


def gate_damage(st: Stages, regions: list) -> None:
    st.gate("damage", "ok", f"{len(regions)} region(s)")


def gate_export(st: Stages, res: dict) -> None:
    missing = []

    def walk(x, path="result"):
        if isinstance(x, dict):
            if "value" in x and "ci90" in x and x["value"] is not None and not x["ci90"]:
                missing.append(path)
            for k, v in x.items():
                walk(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
    walk(res)
    st.gate("export", "fail" if missing else "ok",
            f"{len(missing)} measurement(s) without a 90 % interval: {missing[:3]}" if missing
            else "every measurement has a 90 % interval")
