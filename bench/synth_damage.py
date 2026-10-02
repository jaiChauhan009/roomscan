"""Damage stage on synthetic staged damage of exactly known size (two classes).

usage: python bench/synth_damage.py [stray scan] [--out bench/reports/synth_damage.md]

The sample scans are undamaged, and real staged damage needs the iPhone session. Until
then this paints two regions onto two walls of a real scan, in world coordinates, so
every frame shows them where its own depth and pose put that wall (furniture in front
hides them, as it would hide real damage):
  - a water stain: a brown blob with a darker tide mark, about 0.5 m across;
  - a crack: a dark jagged line about 0.7 m long and 6 mm wide.
Each goes where the fused cloud shows bare, well-observed wall away from openings. The
damage stage runs on the clean capture (anything reported there is a false positive)
and on the painted one, which is scored by the benchmark's own staged-damage scoring
(evaluate._score_damage: right wall and class, width and height against the painted
bounding box, phantoms; thresholds in gates.yaml).

Painted damage is a proxy: it tests frame choice, surface assignment, merging and
sizing; how the classifier reacts to real stains and cracks needs real ones.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from evaluate import GATES, _score_damage  # noqa: E402
from roomscan.capture import PosedCapture  # noqa: E402
from roomscan.damage.pipeline import assess_damage  # noqa: E402
from roomscan.frontends.lidar_stray import load_stray  # noqa: E402
from roomscan.geometry.drift import correct_drift  # noqa: E402
from roomscan.geometry.layout import extract_layout  # noqa: E402
from roomscan.geometry.openings import detect_openings  # noqa: E402
from roomscan.geometry.pointcloud import backproject  # noqa: E402
from roomscan.pipeline import cached_fuse  # noqa: E402

STAIN_RGB = np.array([150, 105, 60], np.float32)
CRACK_RGB = np.array([45, 40, 38], np.float32)
ON_WALL = 0.05  # m: a pixel whose depth puts it this close to the wall plane shows the paint


def stain(R: float = 0.25, seed: int = 0) -> dict:
    phase = np.random.default_rng(seed).uniform(0, 2 * np.pi, 2)
    radius = lambda ang: R * (1 + 0.12 * np.sin(3 * ang + phase[0]) + 0.08 * np.sin(5 * ang + phase[1]))  # noqa: E731
    ang = np.linspace(-np.pi, np.pi, 3601)
    x, y = radius(ang) * np.cos(ang), radius(ang) * np.sin(ang)
    return {"class": "water_stain", "radius": radius, "box": (x.min(), x.max(), y.min(), y.max())}


def crack(length: float = 0.7, width: float = 0.006, seed: int = 1) -> dict:
    """Jagged line rising at 35 deg, in wall coordinates relative to its centre."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0, length, 24)
    line = np.stack([t * np.cos(np.deg2rad(35)), t * np.sin(np.deg2rad(35)) + np.cumsum(rng.normal(0, 0.012, 24))], 1)
    line -= (line.min(0) + line.max(0)) / 2
    lo, hi = line.min(0) - width / 2, line.max(0) + width / 2
    return {"class": "crack", "line": line, "width": width, "box": (lo[0], hi[0], lo[1], hi[1])}


def _dist_to_polyline(p: np.ndarray, line: np.ndarray) -> np.ndarray:
    a, b = line[:-1], line[1:]
    ab = b - a
    t = np.clip(((p[:, None, :] - a) * ab).sum(-1) / (ab ** 2).sum(-1), 0, 1)
    return np.min(np.linalg.norm(p[:, None, :] - (a + t[..., None] * ab), axis=-1), axis=1)


def _wall_coords(ab, h, wall, room):
    d = (wall.end - wall.start) / wall.length
    return (ab - wall.start) @ wall.inward, (ab - wall.start) @ d, h - room.floor.value


def alpha(rg: dict, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Paint opacity at wall coordinates (u along the wall, v above the floor)."""
    du, dv = u - rg["u0"], v - rg["v0"]
    a = np.zeros(len(u), np.float32)
    x0, x1, y0, y1 = rg["box"]
    near = (du > x0 - 0.02) & (du < x1 + 0.02) & (dv > y0 - 0.02) & (dv < y1 + 0.02)
    if not near.any():
        return a
    du, dv = du[near], dv[near]
    if rg["class"] == "water_stain":
        rel = np.hypot(du, dv) / rg["radius"](np.arctan2(dv, du))
        body = np.clip(1.2 - rel, 0, 1) * 0.75 * (rel < 1)
        a[near] = np.maximum(body, 0.8 * ((np.abs(rel - 0.9) < 0.08) & (rel < 1)))  # darker tide mark at the edge
    else:
        dist = _dist_to_polyline(np.stack([du, dv], 1), rg["line"])
        a[near] = np.clip(1.5 - dist / (rg["width"] / 2), 0, 1) * 0.9
    return a


def paint(cap: PosedCapture, layout, regions: list[dict], stats: dict) -> PosedCapture:
    """The capture with every colour frame showing the regions on their walls."""
    fr = layout.frame

    def wrap(f):
        orig = f.rgb_fn

        def fn():
            img = orig()
            stats["read"] += 1
            if img is None:
                return None
            h, w = img.shape[:2]
            z = cv2.resize(f.depth_fn(), (w // 2, h // 2), interpolation=cv2.INTER_LINEAR).reshape(-1)
            K = f.K_rgb.copy()
            K[:2] *= 0.5
            Pw = backproject(z.reshape(h // 2, w // 2), K).reshape(-1, 3) @ f.T_wc[:3, :3].T + f.T_wc[:3, 3]
            ab = fr.to_plan(Pw[:, [0, 2]])
            out, seen = img.astype(np.float32), False
            for rg in regions:
                s, u, v = _wall_coords(ab, Pw[:, 1], rg["wall"], rg["room"])
                a = alpha(rg, u, v) * ((np.abs(s) < ON_WALL) & (z > 0.2))
                if not a.any():
                    continue
                seen = True
                stats["frames_" + rg["class"]].add(f.index)
                a = cv2.resize(a.reshape(h // 2, w // 2), (w, h))
                a = cv2.GaussianBlur(a, (0, 0), 1.5 if rg["class"] == "crack" else 3)[..., None]
                out = out * (1 - a) + rg["rgb"] * a
            stats["painted"] += seen
            return out.astype(np.uint8)

        g = type(f)(**{**f.__dict__})
        g.rgb_fn = fn
        return g

    return PosedCapture(cap.tier, cap.name, [wrap(f) for f in cap.frames], dict(cap.meta))


def bare_spot(cloud, layout, wall, room, openings, box, v0) -> tuple[float, float] | None:
    """(u0, visible fraction): where along the wall the region's box is most completely
    covered by fused points on the wall plane (not behind furniture), clear of openings."""
    ab = layout.frame.to_plan(cloud.points[:, [0, 2]])
    s, u, v = _wall_coords(ab, cloud.points[:, 1], wall, room)
    x0, x1, y0, y1 = box
    keep = (np.abs(s) < 0.03) & (v > v0 + y0) & (v < v0 + y1)
    u, v = u[keep], v[keep]
    nu, nv = max(1, round((x1 - x0) / 0.04)), max(1, round((y1 - y0) / 0.04))  # ~4 cm cells: 2 cm voxels fill them
    best = None
    for u0 in np.arange(0.4 - x0, wall.length - 0.4 - x1, 0.1):  # 40 cm from the corners
        if any(o.wall_id == wall.id and o.u0 - 0.3 < u0 + x1 and u0 + x0 < o.u1 + 0.3 for o in openings):
            continue
        m = (u > u0 + x0) & (u < u0 + x1)
        iu = np.minimum(((u[m] - u0 - x0) / (x1 - x0) * nu).astype(int), nu - 1)
        iv = np.minimum(((v[m] - v0 - y0) / (y1 - y0) * nv).astype(int), nv - 1)
        frac = len(np.unique(iu * nv + iv)) / (nu * nv)
        if best is None or frac > best[1] + 1e-9 or (abs(frac - best[1]) < 1e-9 and abs(u0 - wall.length / 2)
                                                      < abs(best[0] - wall.length / 2)):
            best = (float(u0), float(frac))
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scan", nargs="?", default=str(Path(os.environ.get("ROOMSCAN_DATA", HERE.parent.parent / "data")) / "single_scan_with_ceiling"))
    ap.add_argument("--out", default=str(HERE / "reports" / "synth_damage.md"))
    a = ap.parse_args()
    t0 = time.time()
    path = Path(a.scan)
    key = (str(path.resolve()), "lidar", 5)
    cap = load_stray(path, stride=5)  # as the walk-in runs it: stride 5, loop-closure drift correction
    cap, _ = correct_drift(cap, key=key, progress=False)
    cloud = cached_fuse(cap, key + ("loop",), progress=False)
    layout = extract_layout(cloud)
    ops = detect_openings(cap, layout, cloud=cloud)
    clean, _, _, _ = assess_damage(cap, layout, ops, cloud)

    # the stain goes on the longest wall with a bare spot, the crack on the longest such
    # wall of another room (another wall of the same room if there is none)
    regions, used_rooms = [], set()
    cands = sorted(((w, r) for r in layout.rooms for w in r.walls if w.coverage > 0.8 and w.length > 1.5),
                   key=lambda x: -x[0].length)
    for spec, v0, rgb in ((stain(), 1.3, STAIN_RGB), (crack(), 1.4, CRACK_RGB)):
        order = [x for x in cands if x[1].id not in used_rooms] + [x for x in cands if x[1].id in used_rooms]
        for w, r in order:
            if any(rg["wall"].id == w.id for rg in regions):
                continue
            spot = bare_spot(cloud, layout, w, r, ops, spec["box"], v0)
            if spot and spot[1] >= 0.9:
                regions.append({**spec, "wall": w, "room": r, "u0": spot[0], "v0": v0, "rgb": rgb,
                                "visible": spot[1]})
                used_rooms.add(r.id)
                break
    if len(regions) < 2:
        sys.exit("no two bare, well-observed walls to paint on")

    stats = {"read": 0, "painted": 0, "frames_water_stain": set(), "frames_crack": set()}
    painted, flags, scope, _ = assess_damage(paint(cap, layout, regions, stats), layout, ops, cloud)
    if stats["read"] == 0:
        sys.exit("the damage stage read no colour frame through the painted capture")

    gt = {"damage": []}
    wall_ids = {}
    for rg in regions:
        x0, x1, y0, y1 = rg["box"]
        widx = next(i for i, w in enumerate(rg["room"].walls) if w.id == rg["wall"].id)
        wall_ids[rg["room"].id] = {i: w.id for i, w in enumerate(rg["room"].walls)}
        gt["damage"].append({"room": rg["room"].id, "surface": "wall", "wall": widx + 1, "class": rg["class"],
                             "width": round(x1 - x0, 3), "height": round(y1 - y0, 3),
                             "left": round(rg["u0"] + x0, 3), "bottom": round(rg["v0"] + y0, 3)})
    pred = {"damage": [json.loads(d.model_dump_json()) for d in painted]}
    rows, summary = _score_damage(pred, gt, {r: r for r in wall_ids}, wall_ids)

    g = GATES.get("damage", {})
    md = ["# Damage stage on synthetic staged damage", "",
          f"Capture `{path.name}` (LiDAR tier, stride 5, loop closure). Regenerate with "
          "`uv run python bench/synth_damage.py`; painted damage is a proxy (see the script's docstring).", "",
          "## Painted", "",
          "| class | wall | left m | bottom m | width m | height m | bare wall under it | frames showing it |",
          "|---|---|---|---|---|---|---|---|"]
    for rg, d in zip(regions, gt["damage"]):
        md.append(f"| {d['class']} | {rg['wall'].id} | {d['left']:.2f} | {d['bottom']:.2f} | {d['width']:.3f} | "
                  f"{d['height']:.3f} | {rg['visible']:.0%} | {len(stats['frames_' + rg['class']])} |")
    md += ["", f"Frames the damage stage read: {stats['read']}, of which {stats['painted']} show painted damage.", "",
           "## Clean capture (nothing painted)", "",
           (f"{len(clean)} region(s) reported, all false positives:" if clean else
            "No region reported (the flat is undamaged, so anything reported would be a false positive)."), ""]
    md += [f"- {d.damage_class} on {d.surface_id}, {d.area.value:.3f} m2, score {d.score}" for d in clean]
    md += ["", "## Painted capture, scored as staged damage", "",
           "| painted | status | reported | width m: true / ours (rel. err) | height m: true / ours (rel. err) | "
           "centre height err m |", "|---|---|---|---|---|---|"]
    for r in rows:
        if r["status"] == "phantom":
            continue
        fmt = lambda t, p, e: f"{t:.3f} / {p:.3f} ({e:+.0%})" if p is not None and e is not None else f"{t:.3f} / -"  # noqa: E731
        md.append(f"| {r['class']} | {r['status']} | {r.get('pred_class', '-')} | "
                  f"{fmt(r['width'], r.get('pred_width'), r.get('width_rel_err'))} | "
                  f"{fmt(r['height'], r.get('pred_height'), r.get('height_rel_err'))} | "
                  f"{r.get('centre_height_err', '-')} |")
    ph = [d for d in painted if d.id in {r["pred_id"] for r in rows if r["status"] == "phantom"}]
    md += ["", f"Phantoms: {len(ph)}" + "".join(f"; {d.damage_class} on {d.surface_id} ({d.area.value:.3f} m2)"
                                                 for d in ph) + ".",
           f"Concealed-damage flags: {', '.join(sorted({f.rule_id for f in flags})) or 'none'}; "
           f"scope items: {len(scope)}.", "",
           f"Gate (gates.yaml, assumed: every staged region found with its class, extents within "
           f"{g.get('extent_rel', 0.30):.0%}, at most {g.get('max_phantom', 0)} phantoms): "
           f"found {summary['found']}/{summary['staged']}, wrong class {summary['wrong_class']}, "
           f"missed {summary['missed']}, phantoms {summary['phantom']}, median extent error "
           f"{summary['median_extent_rel_err']} -> **{'PASS' if summary['pass'] else 'FAIL'}**.", "",
           f"Run time {time.time() - t0:.0f} s."]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
