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
    """The floor is the best-supported upward-facing plane.

    Not "the lowest plane": a glossy floor gives LiDAR ghost points below the real floor
    (the room mirrored in it), and those would drag a lowest-point rule down.
    """
    m = cloud.normals[:, 1] > UP_T
    if mask is not None:
        m &= mask
    y = cloud.points[m, 1]
    if len(y) < 50:
        return None
    # tables and beds also face up, but only the lower half of the scene's height can be floor
    allY = cloud.points[:, 1] if mask is None else cloud.points[mask, 1]
    low = y < (np.percentile(allY, 1) + np.percentile(allY, 99)) / 2
    if low.sum() < 50:
        return None
    return _mode_refine(y[low], cloud.weight[m][low])


def ceiling_level(cloud: Cloud, floor_y: float, mask: np.ndarray | None = None,
                  min_height: float = 1.9, highest: bool = False) -> Level | None:
    """Ceiling plane at least min_height above the floor.

    Default: the best-supported downward-facing plane (the ceiling of one room).
    highest=True: the highest plane with substantial support. Used for the whole capture,
    where bathrooms and corridors often have dropped ceilings that outweigh the main one;
    taking the best-supported plane there cut everything above 2.4 m off a 3.0 m flat.
    """
    m = (cloud.normals[:, 1] < -UP_T) & (cloud.points[:, 1] > floor_y + min_height)
    if mask is not None:
        m &= mask
    y = cloud.points[m, 1]
    if len(y) < 200:
        return None
    w = cloud.weight[m]
    if highest:
        bin_m = 0.02
        h, e = np.histogram(y, bins=np.arange(y.min(), y.max() + bin_m, bin_m), weights=w)
        if len(h) == 0:
            return None
        hs = np.convolve(h, [0.25, 0.5, 0.25], mode="same")
        strong = np.where(hs >= 0.15 * hs.max())[0]
        top = e[strong.max()] + bin_m / 2
        sel = np.abs(y - top) < 0.15
        return _mode_refine(y[sel], w[sel])
    return _mode_refine(y, w)


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
