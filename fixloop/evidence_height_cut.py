"""Root-cause experiment for the repeatability failure.

Hypothesis: the two sample scans of the same flat give different room sets because room
separation relies on door lintels (wall above 2.15 m with nothing below), and scan B
("floor only") never looked above ~1.8 m, so it contains no lintels.

Experiment: take scan A (which does include the upper walls and ceiling), delete every
depth sample higher than a cut height, and run the same layout code. If the hypothesis
is right, A-with-the-top-removed should lose rooms the way B does.

usage: python fixloop/evidence_height_cut.py <scan A> [cut heights ...]
"""
import copy
import sys
from pathlib import Path

import numpy as np

from roomscan.capture import PosedCapture
from roomscan.frontends.lidar_stray import load_stray
from roomscan.geometry.drift import correct_drift
from roomscan.geometry.layout import extract_layout
from roomscan.geometry.planes import floor_level
from roomscan.geometry.pointcloud import backproject, fuse_capture


def cut_above(cap: PosedCapture, y_max: float) -> PosedCapture:
    frames = []
    for f in cap.frames:
        g = copy.copy(f)

        def depth(f=f):
            d = f.depth_fn().copy()
            y = (backproject(d, f.K).reshape(-1, 3) @ f.T_wc[:3, :3].T + f.T_wc[:3, 3])[:, 1].reshape(d.shape)
            d[y > y_max] = 0
            return d

        g.depth_fn = depth
        frames.append(g)
    return PosedCapture(cap.tier, cap.name, frames, dict(cap.meta))


def summarise(tag: str, cloud) -> dict:
    layout = extract_layout(cloud)
    g = layout.grids
    rooms = sorted(round(r.area, 1) for r in layout.rooms)
    row = {"case": tag, "rooms": len(layout.rooms), "footprint_m2": round(sum(rooms), 1),
           "lintel_cells": int(g["lintel"].sum()), "wall_cells": int(g["wall"].sum()), "room_areas": rooms}
    print(row, flush=True)
    return row


def main(scan: str, *cuts: str):
    cap = load_stray(Path(scan), stride=5)
    cap, _ = correct_drift(cap, progress=False)
    full = fuse_capture(cap, progress=False)
    floor = floor_level(full).value
    rows = [summarise("full scan", full)]
    for c in (float(x) for x in (cuts or ("2.6", "2.2", "1.8"))):
        rows.append(summarise(f"points above {c:.1f} m removed", fuse_capture(cut_above(cap, floor + c), progress=False)))
    return rows


if __name__ == "__main__":
    main(*sys.argv[1:])
