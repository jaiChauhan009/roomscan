"""Round 4 evidence: why the laser rooms' ceilings read low.

usage: python fixloop/round4/evidence_ceiling.py [capture ...]   (default: the four laser rooms)

For each room of each capture (fused cloud from the pipeline cache, layout from the checked-out
code) prints, for the floor and the ceiling inside the room (6 cm off the walls, as
layout._per_room_levels):
  * mode     the current estimate (planes._mode_refine: weighted 1 cm histogram peak, median +-3 cm)
  * plane    a trimmed least-squares plane y = a.x + b.z + c over every point within 12 cm of the
             mode, read at the room's centre (what bench/make_laser_truth.py reports)
  * cellmed  each 25 cm cell's median height (points within 6 cm of the mode), then the median
             over cells (each patch of surface counts once, however long the camera looked at it)
  * how the surface is spread: share of points / of cells more than 1 cm above / below the mode
and the height (ceiling - floor) by each estimator against the laser truth where there is one.
Also the signed wall-length errors of the benchmark (bench/reports/benchmark.json) for the
depth-scale question. Output: evidence_output.txt (python ... > fixloop/round4/evidence_output.txt).
"""
from __future__ import annotations

import json
import sys

import numpy as np
from scipy import ndimage as ndi

from _common import LASER, ROOT, load_cloud, truth

from roomscan.geometry.layout import extract_layout
from roomscan.geometry.planes import UP_T, _mode_refine

CELL = 0.25
WINDOW = 0.06  # cell medians: points within 6 cm of the mode (a 10 cm step or a low table top is not floor)


def trimmed_plane(xz, y, it=5, k=2.5):
    A = np.c_[xz, np.ones(len(y))]
    keep = np.ones(len(y), bool)
    for _ in range(it):
        c, *_ = np.linalg.lstsq(A[keep], y[keep], rcond=None)
        r = y - A @ c
        s = 1.4826 * np.median(np.abs(r[keep]))
        keep = np.abs(r) < max(k * s, 0.005)
    return c, s


def cell_medians(xz, y, min_pts=20):
    g = np.floor(xz / CELL).astype(np.int64)
    key = g[:, 0] * 100000 + g[:, 1]
    order = np.argsort(key)
    key, y, g = key[order], y[order], g[order]
    starts = np.r_[0, np.nonzero(np.diff(key))[0] + 1, len(key)]
    meds = [np.median(y[a:b]) for a, b in zip(starts[:-1], starts[1:]) if b - a >= min_pts]
    return np.array(meds)


def surface(P, ab, m, mode, cen):
    near = m & (np.abs(P[:, 1] - mode) < 0.12)
    y, xz = P[near, 1], ab[near] - cen
    c, rms = trimmed_plane(xz, y)
    w = np.abs(y - mode) < WINDOW
    cm = cell_medians(xz[w], y[w])
    tilt = float(np.degrees(np.arctan(np.hypot(c[0], c[1]))))
    return {"mode": mode, "plane": float(c[2]), "cellmed": float(np.median(cm)), "tilt_deg": tilt, "rms": float(rms),
            "pts_above": float(np.mean(y > mode + 0.01)), "pts_below": float(np.mean(y < mode - 0.01)),
            "cells_above": float(np.mean(cm > mode + 0.01)), "cells_below": float(np.mean(cm < mode - 0.01)),
            "n": int(near.sum()), "cells": len(cm)}


def main(names):
    bench = json.loads((ROOT / "bench" / "reports" / "benchmark.json").read_text(encoding="utf-8"))["evaluations"]
    for name in names:
        cloud = load_cloud(name)
        lay = extract_layout(cloud)
        gt = truth(name)
        gt_h = gt["rooms"][0]["ceiling_height"] if gt and name.startswith("arkit") else None
        print(f"\n== {name}  rooms {len(lay.rooms)}  truth ceiling_height {gt_h}")
        fr = lay.frame
        ab = fr.to_plan(cloud.points[:, [0, 2]])
        row, col = fr.to_cell(ab)
        ok = (row >= 0) & (row < fr.shape[0]) & (col >= 0) & (col < fr.shape[1])
        P, N = cloud.points, cloud.normals
        rooms = sorted(lay.rooms, key=lambda r: -r.mask.sum())
        for room in rooms:
            if room.ceiling_source != "ceiling_plane" or room.ceiling is None:
                print(f"  {room.id}: ceiling {room.ceiling_source}, skipped")
                continue
            inner = ndi.binary_erosion(room.mask, np.ones((7, 7)))
            inside = np.zeros(len(ab), bool)
            inside[ok] = inner[row[ok], col[ok]]
            rr, cc = np.nonzero(room.mask)
            cen = np.array([fr.a0 + (cc.mean() + 0.5) * fr.res, fr.b0 + (rr.mean() + 0.5) * fr.res])
            f = surface(P, ab, inside & (N[:, 1] > UP_T), room.floor.value, cen)
            c = surface(P, ab, inside & (N[:, 1] < -UP_T), room.ceiling.value, cen)
            area = room.mask.sum() * fr.res ** 2
            print(f"  {room.id} ({area:.1f} m2 mask)")
            for lab, s in (("floor", f), ("ceiling", c)):
                print(f"    {lab:7s} mode {s['mode']:+.4f} plane {s['plane'] - s['mode']:+.4f} cellmed "
                      f"{s['cellmed'] - s['mode']:+.4f} (vs mode) tilt {s['tilt_deg']:.2f} deg rms {s['rms']:.4f} | "
                      f"pts >1cm above/below mode {s['pts_above']:.2f}/{s['pts_below']:.2f}, "
                      f"cells {s['cells_above']:.2f}/{s['cells_below']:.2f} of {s['cells']}")
            line = []
            for est in ("mode", "plane", "cellmed"):
                h = c[est] - f[est]
                line.append(f"{est} {h:.4f}" + (f" ({h - gt_h:+.4f})" if gt_h and room is rooms[0] else ""))
            print("    height: " + ", ".join(line))
        if name in bench and bench[name]["walls"]:
            w = [(x["gt"], x["err"]) for x in bench[name]["walls"] if x["err"] is not None and abs(x["err"]) < 0.1]
            if w:
                print("  walls within 10 cm of the laser (benchmark): signed rel err "
                      + ", ".join(f"{e / g:+.4f}" for g, e in w))


if __name__ == "__main__":
    main(sys.argv[1:] or LASER)
