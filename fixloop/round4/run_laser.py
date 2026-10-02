"""Round 4 before/after run: the four laser rooms end to end (layout + export, no damage stage),
scored by bench/evaluate.py, plus the ceiling-height guards on the other LiDAR captures.

usage: python fixloop/round4/run_laser.py <before|after> [--guards-only | --laser-only]

Writes fixloop/round4/<label>/summary.json and summary.md:
  * laser rooms: bench/evaluate.py's ceiling rows (gt, pred, err, ok) and the ceiling_height gate
  * guards (apt_lidar_a / apt_lidar_b / room_lidar, own_lidar_1..3): every room's height from
    extract_layout (ceiling - floor) and its ceiling source; no truth, they must not move by
    more than ~1 cm unless argued.
Uses the pipeline cache (.cache), one cloud at a time; never bench/run_all.py.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from _common import GUARDS, LASER, ROOT, capture_path, load_cloud, truth

HERE = Path(__file__).resolve().parent


def laser_rows() -> dict:
    from evaluate import evaluate

    from roomscan.pipeline import run
    out = {}
    with tempfile.TemporaryDirectory() as td:
        for name in LASER:
            res = run(capture_path(name), Path(td) / name, tier="lidar", damage=False, progress=False)
            ev = evaluate(res, truth(name))
            out[name] = {"ceilings": ev["ceilings"], "gate": ev["gates"]["ceiling_height"]}
            print(name, [(c["gt"], c["pred"], c["err"]) for c in ev["ceilings"]], flush=True)
    return out


def guard_rows() -> dict:
    from roomscan.geometry.layout import extract_layout
    out = {}
    for name in GUARDS:
        lay = extract_layout(load_cloud(name))
        out[name] = [{"room": r.id, "area_m2": round(float(r.mask.sum()) * lay.frame.res ** 2, 2),
                      "source": r.ceiling_source,
                      "height": None if r.height is None else round(float(r.height), 4)} for r in lay.rooms]
        print(name, [(r["room"], r["source"], r["height"]) for r in out[name]], flush=True)
    return out


def write_md(label: str, s: dict) -> None:
    L = [f"# Round 4 {label} run", "", "Regenerate: `python fixloop/round4/run_laser.py " + label + "`", ""]
    if "laser" in s:
        L += ["## Laser rooms: ceiling height (bench/evaluate.py)", "",
              "| capture | truth (m) | ours (m) | error (cm) | <= 1.5 cm |", "|---|---|---|---|---|"]
        for n, v in s["laser"].items():
            for c in v["ceilings"]:
                err = "" if c["err"] is None else f"{100 * c['err']:+.2f}"
                L.append(f"| {n} | {c['gt']} | {c['pred']} | {err} | {'yes' if c['ok'] else 'no'} |")
        n_ok = sum(v["gate"]["n_ok"] for v in s["laser"].values())
        L += ["", f"Rooms within the gate: {n_ok} of {len(s['laser'])}", ""]
    if "guards" in s:
        L += ["## Guards: room heights (extract_layout, no truth)", "",
              "| capture | room | mask m2 | source | height (m) |", "|---|---|---|---|---|"]
        for n, rows in s["guards"].items():
            for r in rows:
                L.append(f"| {n} | {r['room']} | {r['area_m2']} | {r['source']} | {r['height']} |")
    (HERE / label / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    label = sys.argv[1]
    (HERE / label).mkdir(exist_ok=True)
    f = HERE / label / "summary.json"
    s = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    if "--guards-only" not in sys.argv:
        s["laser"] = laser_rows()
    if "--laser-only" not in sys.argv:
        s["guards"] = guard_rows()
    f.write_text(json.dumps(s, indent=1), encoding="utf-8")
    write_md(label, s)


if __name__ == "__main__":
    main()
