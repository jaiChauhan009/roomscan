"""Round 3 before / after, from the two benchmark outputs.

usage: python fixloop/round3/compare.py [--before DIR] [--after DIR]
       (defaults: bench/reports as committed at tag fixloop3-before; fixloop/round3/after)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[2]


def _load(src, rel: str) -> dict:
    if src is None:  # the before run as committed at the tag
        out = subprocess.run(["git", "show", f"fixloop3-before:bench/reports/{rel}"], capture_output=True,
                             cwd=ROOT, check=True).stdout
        return json.loads(out.decode("utf-8"))
    return json.loads((Path(src) / rel).read_text(encoding="utf-8"))


def outline_stats(res: dict) -> dict:
    walls = [w for r in res["rooms"] for w in r["walls"]]
    return {"rooms": len(res["rooms"]),
            "rooms without any wall evidence": sum(all(w["evidence_coverage"] == 0 for w in r["walls"])
                                                   for r in res["rooms"]),
            "walls": len(walls), "walls without evidence": sum(w["evidence_coverage"] == 0 for w in walls),
            "walls under 0.5 m": sum((w["length"]["value"] or 0) < 0.5 for w in walls),
            "footprint m2": round(res["property"]["footprint_area"]["value"], 2),
            "room areas m2": [round(Polygon(r["polygon"]).area, 2) for r in res["rooms"]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before")
    ap.add_argument("--after", default=str(ROOT / "fixloop" / "round3" / "after"))
    x = ap.parse_args()
    before, after = x.before, x.after
    for cap in ("apt_lidar_a", "apt_lidar_b", "room_lidar", "apt_video_b"):
        b, a = (outline_stats(_load(d, f"runs/{cap}/result.json")) for d in (before, after))
        print(f"\n{cap}")
        for k in b:
            print(f"  {k:32s} {b[k]}  ->  {a[k]}")
    if not (Path(after) / "benchmark.json").exists():
        print("\n(after run not finished: no benchmark.json yet)")
        return
    rb, ra = (_load(d, "benchmark.json") for d in (before, after))
    for name, x in (("before", rb), ("after", ra)):
        for rep in x.get("repeatability", []):
            s = rep["summary"]
            print(f"\nrepeatability {name}: rooms {rep['rooms_a']} vs {rep['rooms_b']}, paired {rep['rooms_paired']} "
                  f"{rep['pairs']}, walls compared {s['walls_compared']}, within gate {s['walls_within_gate']}, "
                  f"median diff {s['median_abs_diff_m']} m -> {'PASS' if rep['pass'] else 'FAIL'}")
    for cap in ("apt_video_b", "apt_photo_a"):
        print(f"\n{cap} gates (before -> after)")
        gb, ga = rb["evaluations"][cap]["gates"], ra["evaluations"][cap]["gates"]
        for g in gb:
            keep = lambda d: {k: v for k, v in d.items() if k in ("pass", "pass_fraction", "median_abs_err_m",  # noqa: E731
                                                                  "rel_err", "coverage_of_90pct_intervals",
                                                                  "matched", "found")}
            print(f"  {g:16s} {keep(gb[g])}  ->  {keep(ga.get(g, {}))}")


if __name__ == "__main__":
    main()
