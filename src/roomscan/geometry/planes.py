"""Gravity-aligned plane estimation: floor, ceiling, dominant wall direction."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from roomscan.geometry.pointcloud import Cloud

UP_T = 0.85  # |n_y| above this: horizontal surface
WALL_T = 0.25  # |n_y| below this: vertical surface


@dataclass
class Level:
    value: float  # world y of the plane
    sigma: float  # 1-sigma standard error of the plane position (m)
    support: int  # number of points supporting it


def _mode_refine(y: np.ndarray, w: np.ndarray | None = None, bin_m: float = 0.01,
                 band: float = 0.03) -> Level | None:
    if len(y) < 50:
        return None
    w = np.ones_like(y) if w is None else w
    edges = np.arange(y.min(), y.max() + bin_m, bin_m)
    if len(edges) < 2:
        return Level(float(np.median(y)), 0.01, len(y))
    h, e = np.histogram(y, bins=edges, weights=w)
    # light smoothing so a plane straddling two bins still wins
    hs = np.convolve(h, [0.25, 0.5, 0.25], mode="same")
    c = e[np.argmax(hs)] + bin_m / 2
    sel = np.abs(y - c) < band
    ys = y[sel]
    med = float(np.median(ys))
    mad = float(np.median(np.abs(ys - med))) * 1.4826
    return Level(med, max(mad / np.sqrt(max(len(ys), 1)), 1e-4), int(sel.sum()))


def floor_level(cloud: Cloud, mask: np.ndarray | None = None) -> Level | None:
    m = cloud.normals[:, 1] > UP_T
    if mask is not None:
        m &= mask
    y = cloud.points[m, 1]
    if len(y) < 50:
        return None
    # the floor is the dominant upward plane in the lower part of the scene
    lo = np.percentile(y, 1)
    y = y[y < lo + 0.6]
    return _mode_refine(y, cloud.weight[m][cloud.points[m, 1] < lo + 0.6])


def ceiling_level(cloud: Cloud, floor_y: float, mask: np.ndarray | None = None,
                  min_height: float = 1.9) -> Level | None:
    m = (cloud.normals[:, 1] < -UP_T) & (cloud.points[:, 1] > floor_y + min_height)
    if mask is not None:
        m &= mask
    y = cloud.points[m, 1]
    if len(y) < 200:
        return None
    # the ceiling is the dominant downward plane near the top of the scene
    hi = np.percentile(y, 99)
    sel = y > hi - 0.6
    return _mode_refine(y[sel], cloud.weight[m][sel])


def wall_top_level(cloud: Cloud, floor_y: float, mask: np.ndarray | None = None) -> Level | None:
    """Fallback ceiling estimate when the ceiling was not scanned: top of wall evidence."""
    m = np.abs(cloud.normals[:, 1]) < WALL_T
    if mask is not None:
        m &= mask
    y = cloud.points[m, 1]
    if len(y) < 200:
        return None
    top = float(np.percentile(y, 99.5))
    return Level(top, 0.10, int(m.sum()))


def manhattan_yaw(cloud: Cloud) -> float:
    """Dominant wall orientation (radians) in the XZ plane, modulo 90 degrees."""
    m = np.abs(cloud.normals[:, 1]) < WALL_T
    n = cloud.normals[m]
    th = np.arctan2(n[:, 2], n[:, 0])
    w = cloud.weight[m] * (1 - np.abs(n[:, 1]))
    # 4-theta trick: angles differing by 90 deg map to the same direction
    hist, e = np.histogram(np.mod(4 * th, 2 * np.pi), bins=720, weights=w)
    hs = np.convolve(np.r_[hist[-5:], hist, hist[:5]], np.ones(11) / 11, mode="same")[5:-5]
    c = e[np.argmax(hs)] + (e[1] - e[0]) / 2
    sel = np.abs(np.angle(np.exp(1j * (np.mod(4 * th, 2 * np.pi) - c)))) < np.deg2rad(8)
    c = np.angle(np.sum(w[sel] * np.exp(1j * 4 * th[sel])))
    return float(c / 4)
