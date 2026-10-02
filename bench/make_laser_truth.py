"""Ground truth for an ARKitScenes room from its Faro laser scans, independent of roomscan.

usage: python bench/make_laser_truth.py <video_id> [...] [--data DIR] [--no-yaml] [--delete-laser]

Reads <data>/arkitscenes/<video_id>/laser/<scan>.npz (sampled scan columns, made by
scripts/fetch_arkitscenes.py) and <scan>_pose.txt (its last line is the scanner position in the
visit's registered frame, +Z up), writes bench/ground_truth/arkit_<video_id>.yaml (the format of
TEMPLATE.yaml, read by bench/evaluate.py) and a top view <data>/arkitscenes/<video_id>/laser_truth.png.
Nothing from roomscan is imported: the numbers come from this file's own plane fits.

Method
  1. Each fetched slice is one vertical sweep of the scanner at one azimuth. Within a sweep, a
     vertical surface shows as points at constant horizontal range and a horizontal surface as points
     at constant height, so every point is labelled vertical / horizontal / neither from the slope of
     the profile over +-K_PROF points (the scanner's own ordering; no normals of a sparse cloud needed).
  2. Floor = lowest strong peak of the heights of horizontal points below the scanner, ceiling =
     highest strong peak above it (strong: >= 30 % of the largest peak on that side). Each is refined
     by a trimmed least-squares plane z = a x + b y + c.
  3. Wall directions: the rotation (0-90 deg) that makes the top-view histograms of the vertical points
     sharpest. In that frame each side's wall is the innermost histogram peak of vertical points that
     lies beyond every scanner position, is flat (10 x 10 cm cells with points within 1.5 cm), reaches
     the ceiling along >= 30 % of the room (furniture does not) and is solid (fewer points 3-15 cm
     behind it than on it: a curtain is not, the wall shows behind it; a partition with a door is).
     It is refined by a trimmed least-squares plane u = p0 + p1 v + p2 z (free yaw and tilt) over the
     whole wall height.
  4. Reported at 1 m above the floor (TEMPLATE.yaml's height): walls = [width, depth, width, depth], the
     mean plane-to-plane distances along the room; floor_area = footprint = width x depth; ceiling
     height floor-to-ceiling at the room's centre. The `laser:` block adds what shows how far the room
     is from that rectangle: width/depth range along the room, the corner-to-corner lengths of the
     quadrilateral the free planes make, the ceiling height at the corners, every fit's rms.
  5. Screening (rule 4 of scripts/fetch_arkitscenes.py): opposite walls parallel and neighbours
     perpendicular within 2 deg; ceiling found in >= 97 % of the 20 cm cells inside the room that a
     fetched slice passes over; <= 10 % of ceiling-height points more than 20 cm outside the room;
     every wall hit along >= 50 % of the 10 cm bins that a fetched slice reaches; every wall a clean plane
     (fit rms <= 1.5 cm).
Not measured: openings, damage, adjacency (one room per capture), anything off the four walls.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
K_PROF = 10  # profile half-window (points); one step is ~0.02 deg of elevation
SLOPE = 0.2  # |d range| < SLOPE |d height|: vertical surface (and vice versa)
STEP = 0.15  # larger jumps over the window are depth edges, not surfaces
WALL_H = 1.0  # walls are measured at this height above the floor


def say(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ loading and per-column labels
def load_scans(vdir: Path) -> list[dict]:
    scans = []
    for f in sorted((vdir / "laser").glob("*.npz")):
        z = np.load(f)
        pose = np.loadtxt(f.with_name(f.stem + "_pose.txt"), delimiter=",")
        c = pose[3, :3]
        sp = int(z["slice_points"])
        xyz = z["xyz"]
        scans.append({"id": f.stem, "xyz": xyz, "c": c, "sp": sp, "n": len(xyz) // sp, "R": pose[:3, :3]})
    if not scans:
        sys.exit(f"no laser scans in {vdir / 'laser'}: run scripts/fetch_arkitscenes.py --video {vdir.name}")
    return scans


def label_slices(s: dict) -> dict:
    """Per point: vertical / horizontal flags; per slice: azimuth (deg) around the scanner."""
    P = s["xyz"] - s["c"]
    n, sp = s["n"], s["sp"]
    P = P[: n * sp].reshape(n, sp, 3)
    rho = np.hypot(P[..., 0], P[..., 1])
    el = np.arctan2(P[..., 2], rho)
    o = np.argsort(el, axis=1)
    rho_s = np.take_along_axis(rho, o, 1)
    z_s = np.take_along_axis(P[..., 2], o, 1)
    k = K_PROF
    dr = np.zeros_like(rho_s)
    dz = np.zeros_like(rho_s)
    dr[:, k:-k] = rho_s[:, 2 * k:] - rho_s[:, :-2 * k]
    dz[:, k:-k] = z_s[:, 2 * k:] - z_s[:, :-2 * k]
    step = np.hypot(dr, dz)
    ok = (step > 1e-4) & (step < STEP)
    vert_s = ok & (np.abs(dr) < SLOPE * np.abs(dz))
    hor_s = ok & (np.abs(dz) < SLOPE * np.abs(dr))
    inv = np.argsort(o, axis=1)  # back to the original order
    vert = np.take_along_axis(vert_s, inv, 1).reshape(-1)
    hor = np.take_along_axis(hor_s, inv, 1).reshape(-1)
    az = np.degrees(np.arctan2(P[..., 1], P[..., 0]))
    far = rho > 0.5
    slice_az = np.array([np.degrees(np.angle(np.mean(np.exp(1j * np.radians(a[m]))))) if m.any() else np.nan
                         for a, m in zip(az, far)])
    spread = np.array([np.median(np.abs((a[m] - sa + 180) % 360 - 180)) if m.any() else np.nan
                       for a, m, sa in zip(az, far, slice_az)])
    return {"vert": vert, "hor": hor, "slice_az": slice_az, "az_spread_med": float(np.nanmedian(spread))}


# ------------------------------------------------------------------ robust fits
def trimmed_lsq(A: np.ndarray, b: np.ndarray, iters: int = 6) -> tuple[np.ndarray, np.ndarray, float]:
    """x minimising |A x - b| after dropping residuals beyond 3 robust sigmas; (x, inlier mask, rms)."""
    m = np.ones(len(b), bool)
    x = np.zeros(A.shape[1])
    for _ in range(iters):
        x, *_ = np.linalg.lstsq(A[m], b[m], rcond=None)
        r = A @ x - b
        sig = max(1.4826 * np.median(np.abs(r[m])), 0.002)
        m_new = np.abs(r) < 3 * sig
        if (m_new == m).all():
            break
        m = m_new
    r = A @ x - b
    return x, m, float(np.sqrt(np.mean(r[m] ** 2)))


def hist_peaks(vals: np.ndarray, lo: float, hi: float, res: float, smooth: int = 1) -> tuple[np.ndarray, np.ndarray]:
    edges = np.arange(lo, hi + res, res)
    h, _ = np.histogram(vals, edges)
    h = h.astype(float)
    if smooth:
        kern = np.exp(-0.5 * (np.arange(-3 * smooth, 3 * smooth + 1) / smooth) ** 2)
        h = np.convolve(h, kern / kern.sum(), mode="same")
    c = 0.5 * (edges[1:] + edges[:-1])
    pk = np.where((h[1:-1] > h[:-2]) & (h[1:-1] >= h[2:]))[0] + 1
    return c[pk], h[pk]


def horizontal_plane(P: np.ndarray, z0: float, band: float = 0.03) -> dict:
    m = np.abs(P[:, 2] - z0) < band
    Q = P[m]
    A = np.c_[Q[:, 0], Q[:, 1], np.ones(len(Q))]
    x, inl, rms = trimmed_lsq(A, Q[:, 2])
    tilt = float(np.degrees(np.arctan(np.hypot(x[0], x[1]))))
    return {"abc": x, "rms": rms, "n": int(inl.sum()), "tilt_deg": tilt}


def plane_z(pl: dict, x, y):
    a, b, c = pl["abc"]
    return a * x + b * y + c


# ------------------------------------------------------------------ the room
def measure(vdir: Path) -> dict:
    scans = load_scans(vdir)
    for s in scans:
        s.update(label_slices(s))
    C = np.array([s["c"] for s in scans])
    P = np.concatenate([s["xyz"] for s in scans])
    V = np.concatenate([s["vert"] for s in scans])
    H = np.concatenate([s["hor"] for s in scans])
    near = np.zeros(len(P), bool)
    for c in C:
        near |= ((P[:, 0] - c[0]) ** 2 + (P[:, 1] - c[1]) ** 2) < 64.0  # within 8 m of a scanner
    info = {"scans": [s["id"] for s in scans], "n_points": int(len(P)),
            "slices": {s["id"]: int(s["n"]) for s in scans},
            "slice_azimuth_spread_deg": {s["id"]: round(s["az_spread_med"], 3) for s in scans},
            "scanner_xyz": {s["id"]: [round(float(v), 4) for v in s["c"]] for s in scans}}

    # floor and ceiling
    zlo, zhi = C[:, 2].min(), C[:, 2].max()
    hz = P[H & near, 2]
    pk, cnt = hist_peaks(hz, hz.min() - 0.1, hz.max() + 0.1, 0.01)  # padded: the floor is the lowest bin
    below, above = (pk < zlo - 0.4), (pk > zhi + 0.3)
    if not below.any() or not above.any():
        raise RuntimeError("no floor or no ceiling among the horizontal laser points")
    fb = pk[below][cnt[below] >= 0.3 * cnt[below].max()].min()
    cb = pk[above][cnt[above] >= 0.3 * cnt[above].max()].max()
    floor = horizontal_plane(P[H & near], fb)
    ceil = horizontal_plane(P[H & near], cb)
    zf, zc = plane_z(floor, *C[:, :2].mean(0)), plane_z(ceil, *C[:, :2].mean(0))
    info["floor"] = {"rms_m": round(floor["rms"], 4), "tilt_deg": round(floor["tilt_deg"], 3), "n": floor["n"]}
    info["ceiling"] = {"rms_m": round(ceil["rms"], 4), "tilt_deg": round(ceil["tilt_deg"], 3), "n": ceil["n"]}

    # wall directions: sharpest top-view histograms of the vertical points
    hrel = P[:, 2] - zf
    wall_pts = V & near & (hrel > 0.2) & (P[:, 2] < zc - 0.1)
    XY = P[wall_pts, :2] - C[:, :2].mean(0)
    XY = XY[:: max(1, len(XY) // 300000)]  # the direction search only; walls are fitted on every point

    def sharp(th):
        c_, s_ = np.cos(th), np.sin(th)
        u, v = XY[:, 0] * c_ + XY[:, 1] * s_, -XY[:, 0] * s_ + XY[:, 1] * c_
        hu = np.bincount(np.floor((u - u.min()) / 0.02).astype(int))
        hv = np.bincount(np.floor((v - v.min()) / 0.02).astype(int))
        return float((hu.astype(float) ** 2).sum() + (hv.astype(float) ** 2).sum())

    coarse = np.radians(np.arange(0, 90, 0.5))
    t0 = coarse[int(np.argmax([sharp(t) for t in coarse]))]
    fine = t0 + np.radians(np.arange(-0.6, 0.61, 0.02))
    th = float(fine[int(np.argmax([sharp(t) for t in fine]))])
    O = C[:, :2].mean(0)
    cth, sth = np.cos(th), np.sin(th)

    def to_uv(xy):
        d = xy - O
        return np.stack([d[..., 0] * cth + d[..., 1] * sth, -d[..., 0] * sth + d[..., 1] * cth], -1)

    def to_xy(uv):
        return O + np.stack([uv[..., 0] * cth - uv[..., 1] * sth, uv[..., 0] * sth + uv[..., 1] * cth], -1)

    UV = to_uv(P[:, :2])
    ceil_pts = H & near & (np.abs(P[:, 2] - plane_z(ceil, P[:, 0], P[:, 1])) < 0.03)
    ext = {ax: np.percentile(UV[ceil_pts, ax], [0.5, 99.5]) for ax in (0, 1)}
    top = V & near & (P[:, 2] > zc - 0.45) & (P[:, 2] < zc - 0.08)
    allwall = V & near & (hrel > 0.1) & (P[:, 2] < zc - 0.05)
    plot_data = {"P": P, "V": V, "H": H, "UV": UV, "Q": None, "C": to_uv(C[:, :2]), "zf": zf, "zc": zc,
                 "ceil_pts": ceil_pts, "top": top}
    walls = {}
    CU = to_uv(C[:, :2])
    for ax in (0, 1):
        other = 1 - ax
        inside = (UV[:, other] > ext[other][0] - 0.1) & (UV[:, other] < ext[other][1] + 0.1)
        sel = allwall & inside & (P[:, 2] < zc - 0.08)
        pk, cnt = hist_peaks(UV[sel, ax], UV[sel, ax].min() - 0.05, UV[sel, ax].max() + 0.05, 0.01)
        pk = pk[cnt >= 0.02 * cnt.max()]
        su, so, sz = UV[sel, ax], UV[sel, other], P[sel, 2] - zf
        cell_id = (np.floor((so - so.min()) / 0.1).astype(np.int64) * 1000 + np.floor(sz / 0.1).astype(np.int64))
        room_h = zc - zf
        n_bins = max(1, int(np.ceil((ext[other][1] - ext[other][0]) / 0.1)))
        any_pt = near & inside & (hrel > 0.1) & (P[:, 2] < zc - 0.05)
        bu = UV[any_pt, ax]
        for side, sgn in (("lo", -1.0), ("hi", 1.0)):
            # The wall on this side is the innermost flat vertical surface that
            #   * lies beyond every scanner position (scanners stand inside the room),
            #   * reaches the ceiling (its top 25 cm) along >= 30 % of the room: furniture does not,
            #   * is solid: fewer points 3-15 cm behind it than on it (only door and window frames).
            #     A curtain is not: the wall shows behind it. A partition with a door is, so it wins
            #     over the far wall seen through the door.
            cand = np.sort(pk[sgn * pk > sgn * (CU[:, ax].min() if sgn < 0 else CU[:, ax].max()) + 0.2])
            u0 = None
            for c0 in cand[::int(sgn)]:  # innermost first
                m = np.abs(su - c0) < 0.015
                near_ceiling = m & (sz > room_h - 0.25)
                reach = len(np.unique(np.floor((so[near_ceiling] - ext[other][0]) / 0.1))) / n_bins
                behind = int(((sgn * (bu - c0) > 0.03) & (sgn * (bu - c0) < 0.15)).sum())
                if reach >= 0.3 and behind < 0.5 * m.sum() and len(np.unique(cell_id[m])) >= 20:
                    u0 = float(c0)
                    break
            if u0 is None:
                return {"info": info, "accepted": False, "_plot": plot_data, "theta_deg": float(np.degrees(th)),
                        "reasons": [f"no wall reaching the ceiling on side {'uv'[ax]}{side}: the room is open "
                                    f"to another space there or is not a rectangle"]}
            cells = len(np.unique(cell_id[np.abs(su - u0) < 0.015]))
            m = allwall & inside & (np.abs(UV[:, ax] - u0) < 0.03)
            A = np.c_[np.ones(m.sum()), UV[m, other], P[m, 2] - zf]
            p, inl, rms = trimmed_lsq(A, UV[m, ax])
            walls[f"{'uv'[ax]}{side}"] = {"ax": ax, "p": p, "rms": rms, "n": int(inl.sum()), "peak": u0,
                                          "cells": int(cells)}

    # quadrilateral at WALL_H above the floor
    z1 = WALL_H  # heights in the wall fits are relative to zf

    def line(w):  # coordinate[ax] = a + b * coordinate[other]
        return w["p"][0] + w["p"][2] * z1, w["p"][1]

    def corner(wu, wv):
        a, b = line(wu)  # u = a + b v
        c, d = line(wv)  # v = c + d u
        u = (a + b * c) / (1 - b * d)
        return np.array([u, c + d * u])

    order = [("ulo", "vlo"), ("uhi", "vlo"), ("uhi", "vhi"), ("ulo", "vhi")]
    Q = np.array([corner(walls[a], walls[b]) for a, b in order])  # uv corners, counter-clockwise
    lengths = [float(np.linalg.norm(Q[(i + 1) % 4] - Q[i])) for i in range(4)]
    area = float(0.5 * abs(np.dot(Q[:, 0], np.roll(Q[:, 1], -1)) - np.dot(Q[:, 1], np.roll(Q[:, 0], -1))))
    vs = np.linspace(max(Q[0, 1], Q[1, 1]), min(Q[2, 1], Q[3, 1]), 50)
    us = np.linspace(max(Q[0, 0], Q[3, 0]), min(Q[1, 0], Q[2, 0]), 50)
    la, lb = line(walls["ulo"]), line(walls["uhi"])
    width = (lb[0] + lb[1] * vs) - (la[0] + la[1] * vs)
    la, lb = line(walls["vlo"]), line(walls["vhi"])
    depth = (lb[0] + lb[1] * us) - (la[0] + la[1] * us)
    cen_xy = to_xy(Q.mean(0))
    corners_xy = to_xy(Q)
    heights = [float(plane_z(ceil, *p) - plane_z(floor, *p)) for p in [cen_xy, *corners_xy]]

    def ang(b):
        return np.degrees(np.arctan(b))

    par = [abs(ang(walls["ulo"]["p"][1]) - ang(walls["uhi"]["p"][1])),
           abs(ang(walls["vlo"]["p"][1]) - ang(walls["vhi"]["p"][1]))]
    perp = []
    for a, b in order:
        nu = np.array([1.0, -walls[a]["p"][1]])
        nv = np.array([-walls[b]["p"][1], 1.0])
        cosang = abs(nu @ nv) / np.linalg.norm(nu) / np.linalg.norm(nv)
        perp.append(abs(90 - np.degrees(np.arccos(np.clip(cosang, 0, 1)))))

    # ---------------- screening: coverage, outside points, wall support (only where a slice reaches)
    rays = [(to_uv(s["c"][:2]), np.radians(s["slice_az"][np.isfinite(s["slice_az"])] - np.degrees(th)))
            for s in scans]

    def reachable(pts_uv: np.ndarray, half: float) -> np.ndarray:
        ok = np.zeros(len(pts_uv), bool)
        for c, az in rays:
            d = pts_uv - c
            phi = np.arctan2(d[:, 1], d[:, 0])
            w = np.arctan2(half, np.maximum(np.linalg.norm(d, axis=1), 1e-3))
            diff = np.abs((phi[:, None] - az[None, :] + np.pi) % (2 * np.pi) - np.pi).min(1)
            ok |= diff < w
        return ok

    lo_u, hi_u = Q[:, 0].min(), Q[:, 0].max()
    lo_v, hi_v = Q[:, 1].min(), Q[:, 1].max()
    gu = np.arange(lo_u + 0.15, hi_u - 0.15, 0.2) + 0.1
    gv = np.arange(lo_v + 0.15, hi_v - 0.15, 0.2) + 0.1
    G = np.array([(u, v) for u in gu for v in gv])
    poly = Polygon(Q)
    G = G[shapely.contains_xy(poly, G[:, 0], G[:, 1])]
    reach = reachable(G, 0.141)
    cu = np.floor((UV[ceil_pts, 0] - (lo_u + 0.15)) / 0.2).astype(int)
    cv_ = np.floor((UV[ceil_pts, 1] - (lo_v + 0.15)) / 0.2).astype(int)
    hit = set(zip(cu.tolist(), cv_.tolist()))
    gi = np.floor((G[:, 0] - (lo_u + 0.15)) / 0.2).astype(int)
    gj = np.floor((G[:, 1] - (lo_v + 0.15)) / 0.2).astype(int)
    covered = np.array([(i, j) in hit for i, j in zip(gi, gj)])
    coverage = float(covered[reach].mean()) if reach.any() else 0.0
    inside_ceil = shapely.contains_xy(poly.buffer(0.2), UV[ceil_pts, 0], UV[ceil_pts, 1])
    outside_frac = float(1 - inside_ceil.mean())
    support = {}
    for i, (a, b) in enumerate([("vlo", None), ("uhi", None), ("vhi", None), ("ulo", None)]):
        p0, p1 = Q[i], Q[(i + 1) % 4]
        L = np.linalg.norm(p1 - p0)
        t = (p1 - p0) / L
        nrm = np.array([-t[1], t[0]])
        s_ = np.arange(0.15, L - 0.15, 0.1) + 0.05
        bins = p0 + s_[:, None] * t
        # a bin is reached when some sampled ray meets the wall line inside it
        reached = np.zeros(len(bins), bool)
        for c, az in rays:
            dirs = np.stack([np.cos(az), np.sin(az)], 1)
            den = dirs @ nrm
            tt = ((p0 - c) @ nrm) / np.where(np.abs(den) > 1e-6, den, np.nan)
            hitp = c + tt[:, None] * dirs
            ss = (hitp - p0) @ t
            ok = (tt > 0) & np.isfinite(ss) & (ss > 0.15) & (ss < L - 0.15)
            idx = np.floor((ss[ok] - 0.15) / 0.1).astype(int)
            reached[idx[(idx >= 0) & (idx < len(bins))]] = True
        w = walls[a]
        res = UV[:, w["ax"]] - (w["p"][0] + w["p"][1] * UV[:, 1 - w["ax"]] + w["p"][2] * (P[:, 2] - zf))
        on = top & (np.abs(res) < 0.03)
        ss = (UV[on] - p0) @ t
        idx = np.floor((ss - 0.15) / 0.1).astype(int)
        has = np.zeros(len(bins), bool)
        has[idx[(idx >= 0) & (idx < len(bins))]] = True
        support[a] = float(has[reached].mean()) if reached.any() else 0.0
    inside_scanners = [bool(shapely.contains_xy(poly, *to_uv(c[:2]))) for c in C]
    checks = {"parallel_deg": [round(x, 2) for x in par], "perpendicular_deg": [round(float(x), 2) for x in perp],
              "ceiling_coverage": round(coverage, 3), "ceiling_cells_reached": int(reach.sum()),
              "ceiling_points_outside": round(outside_frac, 3),
              "wall_support": {k: round(v, 3) for k, v in support.items()},
              "scanners_inside": inside_scanners}
    reasons = []
    if max(par) > 2:
        reasons.append(f"opposite walls not parallel within 2 deg ({max(par):.1f})")
    if max(perp) > 2:
        reasons.append(f"walls not perpendicular within 2 deg ({max(perp):.1f})")
    if coverage < 0.97:
        reasons.append(f"ceiling covers {coverage:.1%} of the rectangle (< 97 %): a corner or strip is missing, "
                       f"not a rectangle")
    if outside_frac > 0.10:
        reasons.append(f"{outside_frac:.0%} of ceiling-height points lie outside the rectangle (> 10 %)")
    for k, v in support.items():
        if v < 0.5:
            reasons.append(f"wall {k} seen along {v:.0%} of its length (< 50 %)")
    if not all(inside_scanners):
        reasons.append("a laser scanner position lies outside the room")
    for k, w in walls.items():
        if w["rms"] > 0.015:
            reasons.append(f"wall {k} is not a clean plane (fit rms {w['rms'] * 100:.1f} cm > 1.5 cm)")
    return {"info": info, "walls_fit": {k: {"rms_m": round(w["rms"], 4), "n": w["n"], "cells_10cm": w["cells"],
                                            "yaw_deg": round(float(ang(w["p"][1])), 3),
                                            "tilt_deg": round(float(ang(w["p"][2])), 3)} for k, w in walls.items()},
            "lengths": lengths, "area": area, "width": width, "depth": depth, "heights": heights,
            "checks": checks, "accepted": not reasons, "reasons": reasons, "theta_deg": float(np.degrees(th)),
            "_plot": dict(plot_data, Q=Q)}


def plot(res: dict, out: Path, title: str) -> None:
    import cv2
    d = res["_plot"]
    UV, Q = d["UV"], d["Q"]
    ref = Q if Q is not None else np.percentile(UV[d["ceil_pts"]], [1, 99], axis=0)
    lo = np.minimum(np.percentile(UV[d["top"]], 0.5, axis=0), ref.min(0)) - 0.5
    hi = np.maximum(np.percentile(UV[d["top"]], 99.5, axis=0), ref.max(0)) + 0.5
    lo, hi = np.maximum(lo, ref.min(0) - 3), np.minimum(hi, ref.max(0) + 3)
    s = 100.0  # px per metre
    W, Hh = int((hi[0] - lo[0]) * s) + 1, int((hi[1] - lo[1]) * s) + 1
    img = np.full((Hh, W, 3), 255, np.uint8)

    def px(uv):
        uv = np.atleast_2d(uv)
        return np.stack([(uv[:, 0] - lo[0]) * s, (hi[1] - uv[:, 1]) * s], 1).astype(int)

    for m, col in ((d["ceil_pts"], (235, 206, 135)), (d["V"] & ((d["P"][:, 2] - d["zf"]) > 0.1), (150, 150, 150)),
                   (d["top"], (40, 40, 40))):
        p = px(UV[m])
        k = (p[:, 0] >= 0) & (p[:, 0] < W) & (p[:, 1] >= 0) & (p[:, 1] < Hh)
        img[p[k, 1], p[k, 0]] = col
    for c in px(d["C"]):
        cv2.circle(img, tuple(int(x) for x in c), 6, (0, 160, 0), -1)
    txt = [title]
    if Q is not None:
        cv2.polylines(img, [px(Q)], True, (0, 0, 220), 2)
        L = res["lengths"]
        for i in range(4):
            mid = px(0.5 * (Q[i] + Q[(i + 1) % 4]))[0]
            cv2.putText(img, f"{L[i]:.3f}", (int(mid[0]) + 4, int(mid[1]) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 0, 220), 2)
        txt.append(f"ceiling {res['heights'][0]:.3f} m, area {res['area']:.2f} m2")
    reasons = "; ".join(res["reasons"])
    txt += ["accepted"] if res["accepted"] else ["REJECTED: " + reasons[i:i + 90] for i in range(0, len(reasons), 90)]
    for i, t in enumerate(txt):
        cv2.putText(img, t, (8, 22 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1)
    cv2.imencode(".png", img)[1].tofile(str(out))


def truth_numbers(res: dict) -> dict:
    """What evaluate.py reads: a rectangle of the mean plane-to-plane width and depth (walls in order
    around the room: the quadrilateral's sides 1 and 3 run along the width)."""
    w, d = float(np.mean(res["width"])), float(np.mean(res["depth"]))
    return {"walls": [w, d, w, d], "area": w * d, "ceiling": res["heights"][0]}


def write_yaml(vid: str, res: dict, meta: dict, out: Path) -> None:
    info, chk = res["info"], res["checks"]
    t = truth_numbers(res)
    L = [round(x, 3) for x in res["lengths"]]
    h = res["heights"]
    w, dp = res["width"], res["depth"]
    scans = ", ".join(info["scans"])
    lines = [
        f"# Laser ground truth for ARKitScenes video {vid}: written by bench/make_laser_truth.py {vid}",
        "# (regenerate with that command; see its docstring for the method). Measured: floor and ceiling",
        "# planes, the four wall planes, at 1 m above the floor. NOT measured: openings, damage, adjacency.",
        f"capture: arkit_{vid}",
        f"source: laser_scanner (ARKitScenes visit {meta['visit_id']}, Faro scans {scans})",
        "method: >-",
        "  per-column surface labels of sampled Faro scan columns (no roomscan code); floor/ceiling =",
        "  trimmed least-squares planes on the height peaks of horizontal points; each wall = trimmed",
        "  least-squares plane on the innermost flat, solid vertical surface beyond the scanners that",
        "  reaches the ceiling; walls = [width, depth, width, depth], the mean plane-to-plane distances",
        "  1 m above the floor;",
        "  floor_area = footprint = width x depth; ceiling_height = floor to ceiling at the room's centre",
        "instrument: Faro laser scanner (ARKitScenes raw laser_scanner_point_clouds, registered scans)",
        "measured_by: bench/make_laser_truth.py (automatic plane fits)",
        f"date: '{datetime.date.today().isoformat()}'",
        f"footprint: {t['area']:.3f}",
        "rooms:",
        "  - name: room",
        f"    ceiling_height: {t['ceiling']:.3f}",
        f"    walls: [{', '.join(f'{x:.3f}' for x in t['walls'])}]",
        f"    floor_area: {t['area']:.3f}",
        "laser:                        # detail for people; bench/evaluate.py does not read it",
        f"  width_min_max_m: [{w.min():.3f}, {w.max():.3f}]    # plane-to-plane width along the room",
        f"  depth_min_max_m: [{dp.min():.3f}, {dp.max():.3f}]",
        f"  corner_to_corner_m: [{', '.join(f'{x:.3f}' for x in L)}]   # the four planes' quadrilateral, same order",
        f"  quadrilateral_area_m2: {res['area']:.3f}",
        f"  ceiling_at_corners_m: [{', '.join(f'{x:.3f}' for x in h[1:])}]",
        f"  floor_fit: {json.dumps(info['floor'])}",
        f"  ceiling_fit: {json.dumps(info['ceiling'])}",
        f"  wall_fits: {json.dumps(res['walls_fit'])}",
        f"  checks: {json.dumps(chk)}",
        f"  points_used: {info['n_points']}",
        f"  slices_per_scan: {json.dumps(info['slices'])}",
        f"  sky_direction: {meta.get('sky_direction')}",
        "  license: ARKitScenes data, Apple licence (non-commercial), github.com/apple/ARKitScenes/blob/main/LICENSE",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id", nargs="+")
    ap.add_argument("--data", default=os.environ.get("ROOMSCAN_DATA", str(ROOT.parent / "data")))
    ap.add_argument("--no-yaml", action="store_true", help="screen and plot only")
    ap.add_argument("--delete-laser", action="store_true", help="delete the laser slices once the YAML is written")
    a = ap.parse_args()
    for vid in a.video_id:
        vdir = Path(a.data) / "arkitscenes" / vid
        meta_f = vdir / "raw" / "meta.json"
        meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
        if "visit_id" not in meta:
            import csv
            row = next(r for r in csv.DictReader(open(Path(a.data) / "arkitscenes" / "_meta" / "metadata.csv"))
                       if r["video_id"] == vid)
            meta = {"visit_id": str(int(float(row["visit_id"]))), "sky_direction": row["sky_direction"]}
        t0 = datetime.datetime.now()
        try:
            res = measure(vdir)
        except RuntimeError as e:
            say(f"{vid} (visit {meta['visit_id']}): REJECTED: {e}")
            continue
        plot(res, vdir / "laser_truth.png", f"{vid} visit {meta['visit_id']}")
        secs = (datetime.datetime.now() - t0).total_seconds()
        if "lengths" not in res:
            say(f"{vid} (visit {meta['visit_id']}): REJECTED: {'; '.join(res['reasons'])} ({secs:.0f} s)")
            continue
        t, w, d = truth_numbers(res), res["width"], res["depth"]
        say(f"{vid} (visit {meta['visit_id']}): {'ACCEPTED' if res['accepted'] else 'REJECTED'} "
            f"width {t['walls'][0]:.3f} ({w.min():.3f}-{w.max():.3f}) depth {t['walls'][1]:.3f} ({d.min():.3f}-"
            f"{d.max():.3f}) area {t['area']:.2f} ceiling {t['ceiling']:.3f} (corners {min(res['heights'][1:]):.3f}-"
            f"{max(res['heights'][1:]):.3f}) | corner to corner {' '.join(f'{x:.3f}' for x in res['lengths'])} "
            f"({secs:.0f} s)")
        say(f"   checks {json.dumps(res['checks'])}")
        say(f"   fits floor {res['info']['floor']} ceiling {res['info']['ceiling']}")
        say(f"   walls {json.dumps(res['walls_fit'])}")
        say(f"   slice azimuth spread {res['info']['slice_azimuth_spread_deg']}")
        for r in res["reasons"]:
            say(f"   reason: {r}")
        if res["accepted"] and not a.no_yaml:
            out = HERE / "ground_truth" / f"arkit_{vid}.yaml"
            write_yaml(vid, res, meta, out)
            say(f"   wrote {out}")
            if a.delete_laser:
                shutil.rmtree(vdir / "laser")
                say(f"   deleted {vdir / 'laser'}")


if __name__ == "__main__":
    main()
