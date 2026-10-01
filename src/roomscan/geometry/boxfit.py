"""Robust rectangular room fit for thin or noisy input (photo / video tiers).

The generic layout extraction needs walls that seal each room. A few stills or a noisy
monocular reconstruction do not give that, so each room is fitted as the rectangle
bounded by the outermost strong wall plane on each of the four Manhattan sides.
A side with no wall evidence falls back to the extent of the observed floor and gets a
wide sigma, which the interval model turns into a wide interval.
"""
from __future__ import annotations

import cv2
import numpy as np

from roomscan.geometry.layout import Layout, PlanFrame, Room, Wall
from roomscan.geometry.planes import (UP_T, Level, ceiling_level, floor_level, manhattan_yaw,
                                      wall_top_level)
from roomscan.geometry.pointcloud import Cloud


def _side_plane(c: np.ndarray, w: np.ndarray, h: np.ndarray, sign: int, bin_m: float, tol: float):
    """Outermost strong wall plane along one axis. Returns (coord, sigma, support, spread)."""
    if len(c) < 50:
        return None
    edges = np.arange(c.min() - bin_m, c.max() + 2 * bin_m, bin_m)
    hist, e = np.histogram(c, bins=edges, weights=w)
    hs = np.convolve(hist, np.ones(3) / 3, mode="same")
    order = np.argsort(hs)[::-1]
    strongest = hs[order[0]]
    if strongest <= 0:
        return None
    best = None
    for k in order[:12]:
        if hs[k] < 0.3 * strongest:
            break
        c0 = e[k] + bin_m / 2
        sel = np.abs(c - c0) < tol
        if sel.sum() < 30:
            continue
        # a wall spans most of the room height; furniture fronts do not
        if np.percentile(h[sel], 95) - np.percentile(h[sel], 5) < 1.0:
            continue
        if best is None or sign * c0 > sign * best:
            best = c0
    if best is None:
        return None
    sel = np.abs(c - best) < tol
    med = float(np.median(c[sel]))
    mad = float(np.median(np.abs(c[sel] - med))) * 1.4826
    return med, max(mad / np.sqrt(sel.sum()), 0.003), int(sel.sum()), mad


def fit_box(cloud: Cloud, yaw: float, room_id: str, noise: float = 0.05,
            fixed: dict | None = None, floor_y: float | None = None) -> Room | None:
    """Rectangle + floor/ceiling for one room's cloud. noise ~ wall thickness of the cloud.

    fixed[(axis, sign)] = (coord, sigma) is used for a side with no wall evidence (e.g. the
    wall behind a doorway standpoint) instead of the floor-extent fallback.
    """
    fixed = fixed or {}
    floor = floor_level(cloud)
    if floor is None and floor_y is not None:
        floor = Level(float(floor_y), 0.10, 0)  # floor not seen: caller knows where it is
    if floor is None:
        return None
    ceil = ceiling_level(cloud, floor.value)
    src = "ceiling_plane"
    if ceil is None:
        ceil = wall_top_level(cloud, floor.value)
        src = "wall_top" if ceil is not None else "none"
    frame = PlanFrame(yaw=yaw)
    ab = frame.to_plan(cloud.points[:, [0, 2]])
    nab = frame.to_plan(cloud.normals[:, [0, 2]])
    h = cloud.points[:, 1] - floor.value
    top = (ceil.value - floor.value) if ceil is not None else 2.6
    wallm = (np.abs(cloud.normals[:, 1]) < 0.3) & (h > 0.2) & (h < top + 0.1)
    floorm = (cloud.normals[:, 1] > UP_T) & (np.abs(h) < max(0.08, 2 * noise))
    bin_m, tol = max(0.02, noise / 2), max(0.03, 1.5 * noise)
    sides = {}
    for axis in (0, 1):
        for sign in (-1, 1):
            m = wallm & (nab[:, axis] * (-sign) > 0.7)
            r = _side_plane(ab[m, axis], cloud.weight[m], h[m], sign, bin_m, tol)
            if r is None and (axis, sign) in fixed:
                r = (float(fixed[(axis, sign)][0]), float(fixed[(axis, sign)][1]), 0, 0.0)
            if r is None:
                # no wall seen on this side: observed floor extent is a lower bound
                if floorm.sum() < 50:
                    return None
                q = np.percentile(ab[floorm, axis], 98 if sign > 0 else 2)
                r = (float(q), 0.30, 0, 0.0)
            sides[(axis, sign)] = r
    a0, a1 = sides[(0, -1)][0], sides[(0, 1)][0]
    b0, b1 = sides[(1, -1)][0], sides[(1, 1)][0]
    if a1 - a0 < 0.5 or b1 - b0 < 0.5:
        return None
    verts = np.array([[a0, b0], [a1, b0], [a1, b1], [a0, b1]])
    # edge i runs verts[i] -> verts[i+1]; the plane that fixes it:
    plane_of = [(1, -1), (0, 1), (1, 1), (0, -1)]
    walls = []
    for i in range(4):
        p, q = verts[i], verts[(i + 1) % 4]
        d = (q - p) / np.linalg.norm(q - p)
        axis, sign = plane_of[i]
        coord, sigma, support, spread = sides[plane_of[i]]
        along = 1 - axis
        m = wallm & (np.abs(ab[:, axis] - coord) < tol) & (nab[:, axis] * (-sign) > 0.7)
        lo, hi = sorted([p[along], q[along]])
        cells = np.unique(np.floor(ab[m, along][(ab[m, along] > lo) & (ab[m, along] < hi)] / 0.05))
        walls.append(Wall(id=f"{room_id}_w{i + 1}", orient="H" if axis == 1 else "V", start=p, end=q,
                          inward=np.array([-d[1], d[0]]), sigma=float(sigma), support=support,
                          coverage=float(min(1.0, len(cells) * 0.05 / max(hi - lo, 0.05))),
                          spread=float(spread)))
    return Room(id=room_id, label_id=0, polygon=verts, walls=walls, floor=floor, ceiling=ceil,
                ceiling_source=src, mask=None)


def layout_from_rooms(rooms: list[Room], yaw: float, res: float = 0.02) -> Layout:
    """Common plan grid, masks and labels for rooms given as plan-frame polygons."""
    allp = np.concatenate([r.polygon for r in rooms])
    lo, hi = allp.min(0) - 0.6, allp.max(0) + 0.6
    frame = PlanFrame(yaw=yaw, a0=float(lo[0]), b0=float(lo[1]), res=res,
                      shape=(int((hi[1] - lo[1]) / res) + 1, int((hi[0] - lo[0]) / res) + 1))
    labels = np.zeros(frame.shape, np.int32)
    for i, r in enumerate(rooms, start=1):
        r.label_id = i
        pix = np.round((r.polygon - [frame.a0, frame.b0]) / res - 0.5).astype(np.int32)
        m = np.zeros(frame.shape, np.uint8)
        cv2.fillPoly(m, [pix], 1)
        r.mask = m.astype(bool)
        labels[r.mask & (labels == 0)] = i
    empty = np.zeros(frame.shape, bool)
    floor = rooms[0].floor
    ceil = rooms[0].ceiling
    return Layout(frame=frame, floor=floor, ceiling=ceil, rooms=rooms, labels=labels,
                  grids={"wall": empty, "lintel": empty, "footprint": labels > 0, "free": labels > 0,
                         "floor_obs": labels > 0})


def box_layout(cloud: Cloud, noise: float = 0.05) -> Layout | None:
    """Single-room fallback layout for a whole capture."""
    yaw = manhattan_yaw(cloud)
    room = fit_box(cloud, yaw, "room_1", noise)
    return None if room is None else layout_from_rooms([room], yaw)
