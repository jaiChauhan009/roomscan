"""Evidence for fix-loop round 3: room outlines keep furniture notches.

usage: python fixloop/round3/evidence_outline.py [fallback|pairing|synthetic|planes|all]

  fallback   instrumented layout on scans A and B: rooms whose outline was a valid polygon
             before wall snapping and self-intersects after it, so the room is drawn from the
             raw raster contour and every wall loses its evidence
  pairing    why the repeatability gate pairs no room: wall counts per room, and for every
             room of A the cheapest room of B with the cost and the gate's threshold
  synthetic  a 4 x 3 m room (exact truth) with furniture against the walls
  planes     per wall segment of scan A, the planes snapping chooses between: which segments
             stop at a plane that ends below door height while a plane further out, as well
             covered, rises to the ceiling (a furniture front chosen over the wall behind it).
             Offsets are outward from the unsnapped edge.

Reads the scans from ../data (or $ROOMSCAN_DATA) and the benchmark runs in bench/reports/runs.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Point, Polygon

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "tests"))

import roomscan.geometry.layout as L  # noqa: E402
from evaluate import _wall_cost, pred_walls  # noqa: E402

DATA = Path(os.environ.get("ROOMSCAN_DATA", ROOT.parent / "data"))
SCANS = {"A": "single_scan_with_ceiling", "B": "single_scan_floor_only"}
RUNS = ROOT / "bench" / "reports" / "runs"


def load_cloud(scan: str):
    from roomscan.frontends.lidar_stray import load_stray
    from roomscan.geometry.drift import correct_drift
    from roomscan.pipeline import cached_fuse
    path = DATA / SCANS[scan]
    key = (str(path.resolve()), "lidar", 5)  # as bench/run_all.py runs it
    cap, _ = correct_drift(load_stray(path, stride=5), key=key, progress=False)
    return cached_fuse(cap, key + ("loop",), progress=False)


class Spy:
    """Records what extract_layout does with every room outline."""

    def __init__(self):
        self.events, self.calls = [], []
        self._ref, self._fb = L._refine_walls, L._fallback_segments

    def __enter__(self):
        def ref(segs, verts, *a, **k):
            if k.get("search_out") == 0.03:  # after the fix: re-measuring the settled walls, not snapping
                return self._ref(segs, verts, *a, **k)
            before = [s["coord"] for s in segs]
            self.calls.append((segs, verts, a, k))
            planes = [candidates(s, verts[i], verts[(i + 1) % len(verts)], *a, **k)
                      if s["orient"] != "D" else [] for i, s in enumerate(segs)]
            out = self._ref(segs, verts, *a, **k)
            after = L._vertices(out)
            ev = {"n": len(segs), "orients": "".join(s["orient"] for s in segs),
                  "valid_before": Polygon(verts).is_valid, "valid_after": Polygon(after).is_valid,
                  "moves": [None if s["orient"] == "D" else round(float(s["coord"] - b), 3)
                            for s, b in zip(out, before)],
                  "lens_before": [round(float(np.linalg.norm(verts[(i + 1) % len(verts)] - verts[i])), 2)
                                  for i in range(len(verts))],
                  "planes": planes, "segs": out, "verts": verts, "fallback": False}
            if not ev["valid_after"]:  # which single snap alone breaks it
                ev["culprits"] = []
                for i, s in enumerate(out):
                    if s["orient"] == "D" or abs(ev["moves"][i]) < 1e-9:
                        continue
                    trial = [dict(x, coord=x["coord"] if j == i or x["orient"] == "D" else before[j])
                             for j, x in enumerate(out)]
                    if not Polygon(L._vertices(trial)).is_valid:
                        ev["culprits"].append(i)
            self.events.append(ev)
            return out

        def fb(mask, frame):
            self.events[-1]["fallback"] = True
            return self._fb(mask, frame)

        L._refine_walls, L._fallback_segments = ref, fb
        return self

    def __exit__(self, *exc):
        L._refine_walls, L._fallback_segments = self._ref, self._fb


def candidates(s, p, q, cloud_ab, cloud, floor_y, poly, search_in=0.15, search_out=0.9, others=None, frame=None,
               ceiling_h=None):
    """The planes _refine_walls scores for one segment (same selection and score), with the
    height range each plane's points span. (ceiling_h: accepted so this also runs on the fixed
    code; the scores shown are the unfixed ones.)"""
    axis = 1 if s["orient"] == "H" else 0
    along = 1 - axis
    inward = np.zeros(2)
    inward[axis] = 1.0
    if not poly.contains(Point((p + q) / 2 + 0.05 * inward)):
        inward = -inward
    lo, hi = sorted([p[along], q[along]])
    shrink = min(0.1, (hi - lo) * 0.2)
    h = cloud.points[:, 1] - floor_y
    cand = ((cloud_ab[:, along] > lo + shrink) & (cloud_ab[:, along] < hi - shrink) & (h > 0.3)
            & (np.abs(cloud.normals[:, 1]) < L.WALL_T))
    off = (s["coord"] - cloud_ab[:, axis]) * inward[axis]
    reach = search_out if others is None else L._clearance(
        others, frame, axis, s["coord"], lo + shrink, hi - shrink, -inward[axis], search_out)
    cand &= (off > -search_in) & (off < reach)
    idx = np.where(cand)[0][s["_nrm"][cand] @ inward > 0.8]
    if len(idx) < 30:
        return []
    o, u, hh = off[idx], cloud_ab[idx, along], h[idx]
    hist, e = np.histogram(o, bins=np.arange(-search_in, search_out + 0.01, 0.01), weights=cloud.weight[idx])
    hs = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
    out = []
    for pk in np.argsort(hs)[::-1][:6]:
        if hs[pk] <= 0:
            break
        c0 = e[pk] + 0.005
        sel = np.abs(o - c0) < 0.03
        cov = len(np.unique(np.floor(u[sel] / 0.05))) * 0.05 / max(hi - lo - 2 * shrink, 0.05)
        # coverage counts 5 cm bins over the segment's inner span, so it can exceed 1 by a bin; shown capped
        out.append({"offset": round(float(c0), 3), "cov": round(min(cov, 1.0), 2),
                    "score": round(cov - 0.05 * max(c0, 0), 3),
                    "top": round(float(np.percentile(hh[sel], 95)), 2),
                    "bottom": round(float(np.percentile(hh[sel], 5)), 2)})
    out.sort(key=lambda c: -c["score"])
    return out


def section_fallback():
    for scan in "AB":
        res = json.loads((RUNS / f"apt_lidar_{scan.lower()}" / "result.json").read_text(encoding="utf-8"))
        with Spy() as spy:
            lay = L.extract_layout(load_cloud(scan))
        print(f"\n== scan {scan}: {len(lay.rooms)} rooms (benchmark run: {len(res['rooms'])})")
        for ev, room in zip(spy.events, res["rooms"]):
            cov = [w["evidence_coverage"] for w in room["walls"]]
            print(f"  {room['id']}: {ev['n']} segments ({ev['orients']}), polygon valid before snapping "
                  f"{ev['valid_before']}, after {ev['valid_after']}" +
                  (f" -> RAW CONTOUR: {len(cov)} walls, {sum(c == 0 for c in cov)} without evidence, "
                   f"{room['floor_area']['value']:.2f} m2" if ev["fallback"] else ""))
            if ev["fallback"]:
                print(f"     snaps (change of each wall line's plan coordinate, m): {ev['moves']}")
                print(f"     segment lengths before snapping (m): {ev['lens_before']}")
                print(f"     single snaps that alone make it self-intersect: "
                      f"{[(i, ev['orients'][i], ev['moves'][i]) for i in ev['culprits']] or 'none (several together)'}")
        lens = [w["length"]["value"] for r in res["rooms"] for w in r["walls"]]
        print(f"  benchmark run: {len(lens)} walls; {sum(x < 0.25 for x in lens)} shorter than 0.25 m, "
              f"{sum(0.25 <= x < 0.5 for x in lens)} of 0.25-0.5 m")


def section_pairing():
    A = json.loads((RUNS / "apt_lidar_a" / "result.json").read_text(encoding="utf-8"))["rooms"]
    B = json.loads((RUNS / "apt_lidar_b" / "result.json").read_text(encoding="utf-8"))["rooms"]
    print("\nwalls of 0.25 m or more per room (what bench/repeatability.py compares):")
    for name, rooms in (("A", A), ("B", B)):
        for r in rooms:
            pw = [round(w["length"]["value"], 2) for w in pred_walls(r)]
            print(f"  {name} {r['id']} {r['floor_area']['value']:.2f} m2, {len(pw)} walls: {pw}")
    print("\ncheapest room of B for each room of A. cost = wall cost (mean length difference, + 0.3 per wall "
          "count difference) + 0.1 x area difference; a pair needs cost <= 0.35 + 0.02 x area")
    for ra in A:
        wa = [w["length"]["value"] for w in pred_walls(ra)]
        best = min(((_wall_cost(wa, [w["length"]["value"] for w in pred_walls(rb)])[0]
                     + 0.1 * abs(ra["floor_area"]["value"] - rb["floor_area"]["value"]), rb) for rb in B),
                   key=lambda x: x[0])
        nb = len(pred_walls(best[1]))
        print(f"  A {ra['id']} ({ra['floor_area']['value']:.2f} m2, {len(wa)} walls) -> B {best[1]['id']} "
              f"({best[1]['floor_area']['value']:.2f} m2, {nb} walls): cost {best[0]:.2f}, of which wall count "
              f"{0.3 * abs(len(wa) - nb):.1f}; threshold {0.35 + 0.02 * ra['floor_area']['value']:.2f}")


def furnished(boxes, W=4.0, D=3.0, H=2.7, fy=-1.4, seed=0):
    """Box room with furniture against its walls: boxes = (x0, x1, z0, z1, height). The wall
    and floor a piece hides are removed, its front, sides and top added."""
    from synth import _plane, box_room

    from roomscan.geometry.pointcloud import Cloud
    room = box_room(W, D, H, floor_y=fy, seed=seed)
    p = room.points
    keep = np.ones(len(p), bool)
    parts = []
    for x0, x1, z0, z1, h in boxes:
        inside = (p[:, 0] > x0 - 0.01) & (p[:, 0] < x1 + 0.01) & (p[:, 2] > z0 - 0.01) & (p[:, 2] < z1 + 0.01)
        keep &= ~(inside & (p[:, 1] < fy + h))
        front = (_plane((x0, fy, z1), (1, 0, 0), (0, 1, 0), (0, 0, 1), x1 - x0, h, 0.02) if z0 < 0.05 else
                 _plane((x0, fy, z0), (1, 0, 0), (0, 1, 0), (0, 0, -1), x1 - x0, h, 0.02))
        parts += [front, _plane((x0, fy, z0), (0, 0, 1), (0, 1, 0), (-1, 0, 0), z1 - z0, h, 0.02),
                  _plane((x1, fy, z0), (0, 0, 1), (0, 1, 0), (1, 0, 0), z1 - z0, h, 0.02),
                  _plane((x0, fy + h, z0), (1, 0, 0), (0, 0, 1), (0, 1, 0), x1 - x0, z1 - z0, 0.02)]
    P = np.concatenate([p[keep]] + [q for q, _ in parts])
    N = np.concatenate([room.normals[keep]] + [n for _, n in parts])
    return Cloud(P.astype(np.float32), N.astype(np.float32), np.ones(len(P), np.float32))


ROOMS = [("empty", []),
         ("wardrobe 1.2 m wide, 0.6 m deep, 1.8 m tall", [(1.4, 2.6, 0.0, 0.6, 1.8)]),
         ("two wardrobes, 2.0 and 1.9 m tall", [(0.3, 1.3, 0.0, 0.6, 2.0), (2.4, 3.6, 0.0, 0.55, 1.9)]),
         ("wardrobe + bookcase on the opposite wall", [(1.4, 2.6, 0.0, 0.6, 1.8), (0.5, 1.5, 2.65, 3.0, 2.0)])]


def section_synthetic():
    print("\nexact truth: 4 walls, 3.00 / 4.00 / 3.00 / 4.00 m, 12.00 m2")
    for label, boxes in ROOMS:
        with Spy() as spy:
            lay = L.extract_layout(furnished(boxes))
        for r, ev in zip(lay.rooms, spy.events):
            print(f"  {label}: {len(r.walls)} walls, {Polygon(r.polygon).area:.2f} m2, lengths "
                  f"{[round(w.length, 2) for w in r.walls]}, coverage {[round(w.coverage, 2) for w in r.walls]}")
            for i, (s, pl) in enumerate(zip(ev["segs"], ev["planes"])):
                if len(pl) > 1 and s["orient"] != "D":
                    far = [c for c in pl[1:] if c["offset"] > pl[0]["offset"] + 0.1]
                    if far:
                        print(f"     segment {i}: chose plane at {pl[0]['offset']:+.2f} m (coverage {pl[0]['cov']}, "
                              f"heights {pl[0]['bottom']}-{pl[0]['top']} m) over {far[0]['offset']:+.2f} m "
                              f"(coverage {far[0]['cov']}, heights {far[0]['bottom']}-{far[0]['top']} m)")


def section_planes():
    cloud = load_cloud("A")
    floor = L.floor_level(cloud)
    ceil = L.ceiling_level(cloud, floor.value, highest=True)
    top_h = ceil.value - floor.value
    with Spy() as spy:
        lay = L.extract_layout(cloud)
    print(f"\nscan A, ceiling {top_h:.2f} m above the floor. Segments whose chosen plane ends below 2.0 m while a "
          f"plane further out with at least 0.6 coverage rises to within 0.3 m of the ceiling:")
    n_seg = n_hit = 0
    for k, ev in enumerate(spy.events):
        for i, (s, pl) in enumerate(zip(ev["segs"], ev["planes"])):
            if s["orient"] == "D" or not pl:
                continue
            n_seg += 1
            ch = pl[0]
            wall = [c for c in pl if c["offset"] > ch["offset"] + 0.05 and c["cov"] >= 0.6 and c["top"] >= top_h - 0.3]
            if ch["top"] < 2.0 and wall:
                n_hit += 1
                L_seg = ev["lens_before"][i]
                print(f"  outline {k + 1} segment {i} ({L_seg} m): chosen {ch['offset']:+.2f} m, coverage {ch['cov']}, "
                      f"heights {ch['bottom']}-{ch['top']} m; wall at {wall[0]['offset']:+.2f} m, coverage "
                      f"{wall[0]['cov']}, heights {wall[0]['bottom']}-{wall[0]['top']} m")
    print(f"  {n_hit} of {n_seg} snapped segments")


SECTIONS = {"fallback": section_fallback, "pairing": section_pairing, "synthetic": section_synthetic,
            "planes": section_planes}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    for name, fn in SECTIONS.items():
        if which in (name, "all"):
            print(f"\n######## {name}")
            fn()
