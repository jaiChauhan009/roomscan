"""Room layout extraction from a fused, gravity-aligned point cloud.

Pipeline:
  1. floor / ceiling heights, dominant (Manhattan) wall direction
  2. rasterise into a 2 cm top-down grid in the Manhattan-aligned "plan" frame
  3. per cell vertical coverage of wall-facing points -> full-height walls and
     door lintels (wall above ~2.1 m, nothing below)
  4. free floor space minus walls and lintels -> connected components = rooms
  5. room mask -> rectilinear polygon -> each edge snapped to the real wall plane
     using the 3D points (sub-centimetre)
  6. the snapped outline settled: steps between walls that snapped onto one plane
     removed (furniture notches), conflicting snaps undone one at a time
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy import ndimage as ndi
from shapely.geometry import Point, Polygon

from roomscan.geometry.planes import (UP_T, WALL_T, Level, area_level, ceiling_level, floor_level,
                                      manhattan_yaw, wall_top_level)
from roomscan.geometry.pointcloud import Cloud

RES = 0.02  # plan grid resolution (m)
HBIN = 0.10  # height bin for vertical coverage (m)
LOW = (0.3, 1.9)  # height band (above floor) that must be covered for a full wall
LINTEL_MIN = 2.15  # door heads sit below this; wall above it and nothing below = doorway
MIN_ROOM_AREA = 1.0  # m2
WALL_BAND_FRAC = 0.5  # share of the observed low band a wall must cover (old rule: 0.8 m of 1.6 m)
STEP_TOL = 0.06  # m: parallel walls this close to one plane after snapping are one wall
FULL_HEIGHT_GAP = 0.3  # m: a plane reaching this close to the ceiling is a wall, not furniture
SQUARE_MAX = 0.5  # m: a raster diagonal shorter than this is a cut corner, not a slanted wall
SLOT_MAX = 0.4  # m: antiparallel walls closer than this, cut into a room, are a slot, not walls
SLOT_DEPTH = 1.5  # m: deepest such slot that is filled (a longer one may be a thin partition)


# ceiling sources whose height is a measurement (not a lower bound): a fitted ceiling plane, or
# RoomPlan's wall heights (frontends/roomplan.py)
MEASURED_CEILING = ("ceiling_plane", "roomplan")


@dataclass
class PlanFrame:
    """Rotation of world XZ into the Manhattan-aligned plan frame (a, b)."""
    yaw: float
    a0: float = 0.0
    b0: float = 0.0
    res: float = RES
    shape: tuple[int, int] = (0, 0)  # rows (b), cols (a)

    def to_plan(self, xz: np.ndarray) -> np.ndarray:
        c, s = np.cos(self.yaw), np.sin(self.yaw)
        return np.stack([c * xz[:, 0] + s * xz[:, 1], -s * xz[:, 0] + c * xz[:, 1]], 1)

    def to_world(self, ab: np.ndarray) -> np.ndarray:
        c, s = np.cos(self.yaw), np.sin(self.yaw)
        return np.stack([c * ab[:, 0] - s * ab[:, 1], s * ab[:, 0] + c * ab[:, 1]], 1)

    def to_cell(self, ab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        col = np.floor((ab[:, 0] - self.a0) / self.res).astype(int)
        row = np.floor((ab[:, 1] - self.b0) / self.res).astype(int)
        return row, col

    def cell_center(self, row, col) -> np.ndarray:
        return np.stack([self.a0 + (np.asarray(col) + 0.5) * self.res,
                         self.b0 + (np.asarray(row) + 0.5) * self.res], -1)


@dataclass
class Wall:
    id: str
    orient: str  # "H" (constant b), "V" (constant a) or "D" (diagonal)
    start: np.ndarray  # plan (a, b)
    end: np.ndarray
    inward: np.ndarray  # unit normal pointing into the room
    sigma: float  # 1-sigma of the wall plane position (m)
    support: int  # number of 3D points that fixed the plane (0 = raster only)
    coverage: float = 0.0  # fraction of the wall length with surface evidence
    spread: float = 0.0  # robust std of wall points about the plane (sharpness, m)

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.end - self.start))


@dataclass
class Room:
    id: str
    label_id: int
    polygon: np.ndarray  # (K,2) plan coords, counter-clockwise
    walls: list[Wall]
    floor: Level
    ceiling: Level | None
    ceiling_source: str  # "ceiling_plane" | "wall_top" | "none"
    mask: np.ndarray = field(repr=False, default=None)  # grid mask (rows, cols)

    @property
    def area(self) -> float:
        return float(Polygon(self.polygon).area)

    @property
    def height(self) -> float | None:
        return None if self.ceiling is None else self.ceiling.value - self.floor.value


@dataclass
class Layout:
    frame: PlanFrame
    floor: Level
    ceiling: Level | None
    rooms: list[Room]
    labels: np.ndarray  # room label per grid cell (0 = none)
    grids: dict  # diagnostic rasters


def observed_band_top(cloud: Cloud, floor_y: float) -> float:
    """Top of the height band the capture actually observed on walls.

    90th percentile of wall-point heights, capped at LOW[1]. A capture shot with the phone
    pointing down sees walls only up to ~1.5 m; the wall test must not demand evidence
    above that (fix-loop declaration, fixloop/declaration.md).
    """
    h = cloud.points[:, 1] - floor_y
    m = (np.abs(cloud.normals[:, 1]) < WALL_T) & (h > LOW[0])
    if m.sum() < 500:
        return LOW[1]
    return float(np.clip(np.percentile(h[m], 90), LOW[0] + 0.6, LOW[1]))


def _close_gaps(walls: np.ndarray) -> np.ndarray:
    """Walls with small unobserved gaps (< 50 cm) closed, so rooms stay sealed."""
    return ndi.binary_closing(ndi.binary_dilation(walls, np.ones((3, 3))), np.ones((25, 25)))


def _enclosed(walls: np.ndarray) -> np.ndarray:
    """Cells that have a wall somewhere in each of the four axis directions."""
    return (np.maximum.accumulate(walls, axis=0) & np.maximum.accumulate(walls[::-1], axis=0)[::-1]
            & np.maximum.accumulate(walls, axis=1) & np.maximum.accumulate(walls[:, ::-1], axis=1)[:, ::-1])


def _coverage_grids(cloud: Cloud, ab: np.ndarray, frame: PlanFrame, floor_y: float,
                    top_h: float, band_top: float = LOW[1]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    h = cloud.points[:, 1] - floor_y
    wallm = (np.abs(cloud.normals[:, 1]) < WALL_T) & (h > 0.05) & (h < top_h)
    row, col = frame.to_cell(ab[wallm])
    hb = np.floor(h[wallm] / HBIN).astype(int)
    nb = int(np.ceil(top_h / HBIN)) + 1
    occ = np.zeros((frame.shape[0], frame.shape[1], nb), bool)
    occ[row, col, hb] = True
    # tolerate 1-cell noise in wall position
    occ = ndi.binary_dilation(occ, structure=np.ones((3, 3, 1), bool))
    lo0, lo1 = int(round(LOW[0] / HBIN)), int(round(band_top / HBIN))
    hi0, hi1 = int(np.ceil(LINTEL_MIN / HBIN)), max(int((top_h - 0.12) / HBIN), int(np.ceil(LINTEL_MIN / HBIN)) + 3)
    low_cov = occ[:, :, lo0:lo1].sum(-1)
    high_cov = occ[:, :, hi0:hi1].sum(-1)
    return low_cov, high_cov, np.array([lo1 - lo0, hi1 - hi0])


def _rectilinear_polygon(mask: np.ndarray, frame: PlanFrame, min_seg: float = 0.16,
                         ang_tol_deg: float = 20.0) -> list[dict]:
    """Room mask -> list of lines {orient, coord | p,d} in plan metres (CCW order)."""
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cnts, key=cv2.contourArea)
    approx = cv2.approxPolyDP(c, 2.0, True)[:, 0, :].astype(float)  # (x=col, y=row)
    pts = frame.cell_center(approx[:, 1], approx[:, 0])
    if Polygon(pts).exterior.is_ccw is False:
        pts = pts[::-1]
    tan = np.tan(np.deg2rad(ang_tol_deg))
    segs = []
    n = len(pts)
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        d = q - p
        L = float(np.linalg.norm(d))
        if L < 1e-6:
            continue
        if abs(d[1]) <= tan * abs(d[0]):
            segs.append({"orient": "H", "coord": (p[1] + q[1]) / 2, "len": L, "p": p, "q": q})
        elif abs(d[0]) <= tan * abs(d[1]):
            segs.append({"orient": "V", "coord": (p[0] + q[0]) / 2, "len": L, "p": p, "q": q})
        else:
            segs.append({"orient": "D", "coord": 0.0, "len": L, "p": p, "q": q})

    def merge_same(s):
        changed = True
        while changed and len(s) > 1:
            changed = False
            for i in range(len(s)):
                a, b = s[i], s[(i + 1) % len(s)]
                if a["orient"] == b["orient"] and a["orient"] != "D" and len(s) > 1:
                    L = a["len"] + b["len"]
                    a = {"orient": a["orient"], "coord": (a["coord"] * a["len"] + b["coord"] * b["len"]) / L,
                         "len": L, "p": a["p"], "q": b["q"]}
                    j = (i + 1) % len(s)
                    s[i] = a
                    s.pop(j)
                    changed = True
                    break
        return s

    segs = merge_same(segs)
    changed = True
    while changed and len(segs) > 4:
        changed = False
        k = int(np.argmin([s["len"] for s in segs]))
        if segs[k]["len"] < min_seg:
            prev, nxt = segs[k - 1], segs[(k + 1) % len(segs)]
            if prev["orient"] == nxt["orient"] != segs[k]["orient"] or segs[k]["orient"] == "D":
                segs.pop(k)
                segs = merge_same(segs)
                changed = True
    return segs


def _square_corners(segs: list[dict], max_len: float = SQUARE_MAX) -> list[dict]:
    """Short diagonal segments replaced by the axis-aligned corner their neighbours imply.

    A diagonal of a few decimetres in the raster outline is a corner the contour cut (often
    furniture in it), not a slanted wall. It is not snapped, so once its neighbours snap
    onto their planes its line stretches into a long slanted wall, and it keeps
    `_collapse_steps` from merging the walls either side of a notch. Between a horizontal
    and a vertical edge it is dropped (their lines meet in the corner); between two parallel
    edges it becomes the perpendicular step through its middle. Longer diagonals (a real
    slanted wall) and runs of diagonals stay. The original list when the result is not a
    simple counter-clockwise polygon.
    """
    out = [dict(s) for s in segs]
    while len(out) > 4:
        n = len(out)
        cands = [i for i, s in enumerate(out) if s["orient"] == "D" and s["len"] < max_len
                 and out[i - 1]["orient"] != "D" and out[(i + 1) % n]["orient"] != "D"]
        if not cands:
            break
        i = min(cands, key=lambda k: out[k]["len"])
        prev, nxt = out[i - 1], out[(i + 1) % n]
        if prev["orient"] != nxt["orient"]:
            out.pop(i)
        else:  # a step between parallel edges: the perpendicular through the diagonal's middle
            s = out[i]
            mid = (s["p"] + s["q"]) / 2
            orient = "V" if prev["orient"] == "H" else "H"
            out[i] = {**s, "orient": orient, "coord": float(mid[0] if orient == "V" else mid[1])}
    if len(out) == len(segs) and all(a["orient"] == b["orient"] for a, b in zip(out, segs)):
        return segs
    return out if _outline_ok(out) else segs


def _fallback_segments(mask: np.ndarray, frame: PlanFrame) -> list[dict]:
    """Plain simplified contour (no snapping) for shapes the rectilinear pass breaks."""
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cnts, key=cv2.contourArea)
    approx = cv2.approxPolyDP(c, 4.0, True)[:, 0, :].astype(float)
    pts = frame.cell_center(approx[:, 1], approx[:, 0])
    if not Polygon(pts).exterior.is_ccw:
        pts = pts[::-1]
    return [{"orient": "D", "coord": 0.0, "len": float(np.linalg.norm(pts[(i + 1) % len(pts)] - pts[i])),
             "p": pts[i], "q": pts[(i + 1) % len(pts)], "sigma": 0.05, "support": 0, "coverage": 0.0}
            for i in range(len(pts))]


def _line_of(seg: dict) -> tuple[np.ndarray, np.ndarray]:
    if seg["orient"] == "H":
        return np.array([0.0, seg["coord"]]), np.array([1.0, 0.0])
    if seg["orient"] == "V":
        return np.array([seg["coord"], 0.0]), np.array([0.0, 1.0])
    d = seg["q"] - seg["p"]
    return seg["p"], d / np.linalg.norm(d)


def _intersect(l1, l2) -> np.ndarray:
    (p1, d1), (p2, d2) = l1, l2
    A = np.array([d1, -d2]).T
    if abs(np.linalg.det(A)) < 1e-9:
        return (p1 + p2) / 2
    t = np.linalg.solve(A, p2 - p1)
    return p1 + t[0] * d1


def _vertices(segs: list[dict]) -> np.ndarray:
    lines = [_line_of(s) for s in segs]
    return np.array([_intersect(lines[i - 1], lines[i]) for i in range(len(lines))])


def _clearance(others: np.ndarray, frame: PlanFrame, axis: int, coord: float, lo: float, hi: float,
               outward: float, max_d: float, frac: float = 0.0) -> float:
    """How far a wall line can move outward before it enters another room's cells.

    The line is constant in plan coordinate `axis` at `coord` and spans [lo, hi] along the
    other coordinate; `outward` is +1 or -1 along `axis`. With `frac`, the line stops only
    where more than that share of its span is in `others`.
    """
    res = frame.res
    k = np.arange(int(np.ceil(max_d / res)) + 1)
    origin = (frame.a0, frame.b0)
    lines = np.floor((coord + outward * (k + 0.5) * res - origin[axis]) / res).astype(int)
    span = slice(max(int(np.floor((lo - origin[1 - axis]) / res)), 0),
                 max(int(np.ceil((hi - origin[1 - axis]) / res)), 0))
    rows_or_cols = others.shape[0] if axis == 1 else others.shape[1]
    ok = (lines >= 0) & (lines < rows_or_cols)
    hit = np.zeros(len(k), bool)
    if not ok.any() or span.stop <= span.start:
        return max_d
    if axis == 1:  # line of constant b: scan rows
        cells = others[lines[ok], span]
        hit[ok] = cells.mean(axis=1) > frac if frac else cells.any(axis=1)
    else:  # constant a: scan columns
        cells = others[span, lines[ok]]
        hit[ok] = cells.mean(axis=0) > frac if frac else cells.any(axis=0)
    return float(k[np.argmax(hit)] * res) if hit.any() else max_d


def _refine_walls(segs: list[dict], verts: np.ndarray, cloud_ab: np.ndarray, cloud: Cloud,
                  floor_y: float, poly: Polygon, search_in: float = 0.15,
                  search_out: float = 0.9, others: np.ndarray | None = None,
                  frame: PlanFrame | None = None, ceiling_h: float | None = None,
                  own: np.ndarray | None = None) -> list[dict]:
    """Snap each axis-aligned edge onto the real wall plane seen in the 3D points.

    An edge searches up to `search_out` outward (the room's mask stops at furniture in front
    of a wall), but never past the first cell of another room (`others`): the best-covered
    plane beyond that belongs to the neighbour, and snapping to it made rooms overlap.
    A wall rises to the ceiling and furniture does not: given the room's ceiling height, a
    plane whose points reach within FULL_HEIGHT_GAP of it beats a lower one if it covers at
    least 80 % as much of the edge (a wardrobe front against the wall seen above it).
    """
    n = len(segs)
    h = cloud.points[:, 1] - floor_y
    for i, s in enumerate(segs):
        if s["orient"] == "D":
            continue
        p, q = verts[i], verts[(i + 1) % n]
        axis = 1 if s["orient"] == "H" else 0  # coordinate that is constant along the wall
        along = 1 - axis
        mid = (p + q) / 2
        inward = np.zeros(2)
        inward[axis] = 1.0
        if not poly.contains(Point(mid + 0.05 * inward)):
            inward = -inward
        lo, hi = sorted([p[along], q[along]])
        shrink = min(0.1, (hi - lo) * 0.2)
        cand = ((cloud_ab[:, along] > lo + shrink) & (cloud_ab[:, along] < hi - shrink) & (h > 0.3)
                & (np.abs(cloud.normals[:, 1]) < WALL_T))
        # signed distance outward from the current edge
        off = (s["coord"] - cloud_ab[:, axis]) * inward[axis]
        reach = search_out if others is None else _clearance(
            others, frame, axis, s["coord"], lo + shrink, hi - shrink, -inward[axis], search_out)
        if own is not None:  # nor past the room's own floor (the far side of a slot)
            reach = min(reach, _clearance(own, frame, axis, s["coord"], lo + shrink, hi - shrink,
                                          -inward[axis], search_out, frac=0.5))
        cand &= (off > -search_in) & (off < reach)
        nn = s["_nrm"][cand] @ inward
        cand_idx = np.where(cand)[0][nn > 0.8]
        if len(cand_idx) < 30:
            s["sigma"], s["support"], s["coverage"] = 0.03, 0, 0.0
            continue
        o = off[cand_idx]
        u = cloud_ab[cand_idx, along]
        hc = h[cand_idx]
        # candidate planes = peaks in offset; score by how much of the wall length they cover
        bins = np.arange(-search_in, search_out + 0.01, 0.01)
        hist, e = np.histogram(o, bins=bins, weights=cloud.weight[cand_idx])
        hs = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
        best, best_score, best_cov, best_full = None, 0.0, 0.0, False
        tall, tall_score, tall_cov = None, 0.0, 0.0  # the best plane that reaches the ceiling
        top = [k for k in np.argsort(hs)[::-1][:6] if hs[k] > 0]
        # The six highest bins are often all one dense peak (a furniture front). A wall seen
        # only above the furniture has far fewer points, so where the ceiling is known every
        # other distinct peak (highest bin within 3 cm) is a candidate too, for the
        # reaches-the-ceiling rule only.
        extra = [] if ceiling_h is None else [
            k for k in np.flatnonzero(hs > 0) if k not in top and hs[k] == hs[max(0, k - 3):k + 4].max()]
        for pk in top + extra:
            c0 = e[pk] + 0.005
            sel = np.abs(o - c0) < 0.03
            ub = np.unique(np.floor(u[sel] / 0.05))
            cov = len(ub) * 0.05 / max(hi - lo - 2 * shrink, 0.05)
            score = cov - 0.05 * max(c0, 0)  # prefer nearer planes at equal coverage
            full = ceiling_h is not None and float(np.percentile(hc[sel], 95)) >= ceiling_h - FULL_HEIGHT_GAP
            if score > best_score and pk in top:
                best, best_score, best_cov, best_full = c0, score, cov, full
            if full and score > tall_score:
                tall, tall_score, tall_cov = c0, score, cov
        if tall is not None and not best_full and tall_cov >= 0.8 * best_cov:
            best = tall
        if best is None:
            s["sigma"], s["support"], s["coverage"] = 0.03, 0, 0.0
            continue
        sel = np.abs(o - best) < 0.025
        med = float(np.median(o[sel]))
        mad = float(np.median(np.abs(o[sel] - med))) * 1.4826
        s["coord"] = s["coord"] - med * inward[axis]
        s["sigma"] = max(mad / np.sqrt(sel.sum()), 0.002)
        s["spread"] = mad
        s["support"] = int(sel.sum())
        s["coverage"] = float(min(1.0, len(np.unique(np.floor(u[sel] / 0.05))) * 0.05 / max(hi - lo, 0.05)))
    return segs


def _outline_ok(segs: list[dict]) -> bool:
    """A simple polygon, still counter-clockwise (wall normals point inside)."""
    v = _vertices(segs)
    if len(v) < 3:
        return False
    p = Polygon(v)
    return p.is_valid and p.area > 0 and p.exterior.is_ccw


def _merge_walls(a: dict, b: dict) -> dict:
    """One wall from two parallel ones: the line their points support, both their evidence."""
    wa, wb = a.get("support", 0), b.get("support", 0)
    if wa + wb:
        coord = (a["coord"] * wa + b["coord"] * wb) / (wa + wb)
    else:
        coord = (a["coord"] * a["len"] + b["coord"] * b["len"]) / max(a["len"] + b["len"], 1e-9)
    sig = [x["sigma"] for x in (a, b) if x.get("support", 0)]
    return {**a, "coord": coord, "len": a["len"] + b["len"], "q": b["q"], "support": wa + wb,
            "sigma": float(np.sqrt(1 / sum(1 / s ** 2 for s in sig))) if sig else 0.03,
            "coverage": (a.get("coverage", 0.0) * a["len"] + b.get("coverage", 0.0) * b["len"])
            / max(a["len"] + b["len"], 1e-9),
            "spread": max(a.get("spread", 0.0), b.get("spread", 0.0))}


def _collapse_steps(segs: list[dict]) -> list[dict]:
    """Merge parallel walls that run the same way and that snapping put within STEP_TOL of
    one plane, dropping the step between them: a furniture notch whose front snapped back
    onto the wall behind it. A step left between two such walls is at most STEP_TOL long."""
    segs = list(segs)
    i = 0
    while len(segs) > 4 and i < len(segs):
        n = len(segs)
        ia, ib = (i - 1) % n, (i + 1) % n
        a, s, b = segs[ia], segs[i], segs[ib]
        if (a["orient"] == b["orient"] in ("H", "V") and s["orient"] != a["orient"]
                and abs(a["coord"] - b["coord"]) <= STEP_TOL):
            v = _vertices(segs)
            k = 0 if a["orient"] == "H" else 1
            if (v[i][k] - v[ia][k]) * (v[(ib + 1) % n][k] - v[ib][k]) > 0:  # a and b run the same way
                m = _merge_walls(a, b)
                segs = [m if t == ia else segs[t] for t in range(n) if t not in (i, ib)]
                i = 0
                continue
        i += 1
    return segs


def _fill_slots(segs: list[dict], others: np.ndarray | None = None, frame: PlanFrame | None = None,
                width: float = SLOT_MAX, depth: float = SLOT_DEPTH) -> list[dict]:
    """The snapped outline without narrow slots cut into the room.

    A slot is two antiparallel walls less than `width` apart joined by a short end, both of
    its corners reflex: the outline runs into the room and straight back out. Snapping
    leaves one where furniture made a notch whose sides then snapped onto the walls either
    side of it (a radiator or a shelf end between two pieces), and no room has walls a
    hand's width apart. The slot's three walls become one step at its middle; the walls
    either side of its mouth merge when they lie on one plane. Kept when the filled slot
    would reach another room's cells, or the outline would not stay simple.
    """
    out = list(segs)
    i = 0
    while len(out) > 6 and i < len(out):
        n = len(out)
        a, s, b = out[i], out[(i + 1) % n], out[(i + 2) % n]
        if not (a["orient"] == b["orient"] in ("H", "V") and s["orient"] not in ("D", a["orient"])
                and abs(a["coord"] - b["coord"]) < width):
            i += 1
            continue
        v = _vertices(out)
        p0, p1, p2, p3 = v[i], v[(i + 1) % n], v[(i + 2) % n], v[(i + 3) % n]
        d0, d1, d2 = p1 - p0, p2 - p1, p3 - p2
        right = (d0[0] * d1[1] - d0[1] * d1[0] < 0) and (d1[0] * d2[1] - d1[1] * d2[0] < 0)
        if not right or max(np.linalg.norm(d0), np.linalg.norm(d2)) > depth:
            i += 1
            continue
        step = {**a, "coord": (a["coord"] + b["coord"]) / 2, "len": float(np.linalg.norm(p3 - p0)),
                "sigma": 0.03, "support": 0, "coverage": 0.0, "spread": 0.0}
        cand = [t for k, t in enumerate(out) if k not in (i, (i + 1) % n, (i + 2) % n)]
        cand.insert(i if i + 2 < n else 0, step)  # where the slot was (it may wrap the list's end)
        cand = _collapse_steps(cand)
        if not _outline_ok(cand) or _overlaps(cand, out, others, frame):
            i += 1
            continue
        out = cand
        i = 0
    return out


def _overlaps(new: list[dict], old: list[dict], others: np.ndarray | None, frame: PlanFrame | None) -> bool:
    """Whether the area `new` adds to `old` covers a cell of another room."""
    if others is None or frame is None or not others.any():
        return False
    added = Polygon(_vertices(new)).difference(Polygon(_vertices(old)))
    if added.is_empty:
        return False
    a0, b0, a1, b1 = added.bounds
    r0, c0 = max(int((b0 - frame.b0) / frame.res) - 1, 0), max(int((a0 - frame.a0) / frame.res) - 1, 0)
    r1, c1 = int((b1 - frame.b0) / frame.res) + 2, int((a1 - frame.a0) / frame.res) + 2
    sub = others[r0:r1, c0:c1]
    if not sub.any():
        return False
    rr, cc = np.nonzero(sub)
    centers = frame.cell_center(rr + r0, cc + c0)
    return any(added.contains(Point(x, y)) for x, y in centers)


def _revert(segs: list[dict], i: int) -> list[dict]:
    """The outline with edge i back where the raster put it, as an edge without evidence."""
    out = list(segs)
    out[i] = {**segs[i], "coord": segs[i]["_c0"], "sigma": 0.03, "support": 0, "coverage": 0.0, "spread": 0.0}
    return out


def _settle(segs: list[dict]) -> list[dict] | None:
    """The snapped outline as a simple polygon, without steps snapping flattened.

    Snaps can conflict: one edge of a notch snaps past its neighbour and the outline crosses
    itself. Undo the snap whose undoing resolves it (the least covered one if several do; if
    none does alone, the least covered) and try again, rather than discarding every snap in
    the room. None when even the unsnapped outline fails.
    """
    cur = list(segs)
    moved = [i for i, s in enumerate(cur) if s["orient"] != "D" and s["coord"] != s["_c0"]]
    while True:
        out = _collapse_steps(cur)
        if _outline_ok(out):
            return out
        if not moved:
            return cur if _outline_ok(cur) else None
        fixes = [i for i in moved if _outline_ok(_collapse_steps(_revert(cur, i)))]
        pick = min(fixes or moved, key=lambda i: (cur[i].get("coverage", 0.0), i))
        cur = _revert(cur, pick)
        moved.remove(pick)


def extract_layout(cloud: Cloud, res: float = RES, adaptive_band: bool = True) -> Layout:
    floor = floor_level(cloud)
    if floor is None:
        raise RuntimeError("no floor found in capture")
    ceil = ceiling_level(cloud, floor.value, highest=True)
    ceil_src = "ceiling_plane"
    if ceil is None:
        ceil = wall_top_level(cloud, floor.value)
        ceil_src = "wall_top" if ceil else "none"
    top_h = (ceil.value - floor.value) if ceil else 2.6
    top_h = float(np.clip(top_h, 2.3, 4.5))

    yaw = manhattan_yaw(cloud)
    frame = PlanFrame(yaw=yaw, res=res)
    ab = frame.to_plan(cloud.points[:, [0, 2]])
    nab = frame.to_plan(cloud.normals[:, [0, 2]])
    keep = (cloud.points[:, 1] > floor.value - 0.1) & (cloud.points[:, 1] < floor.value + top_h + 0.1)
    lo = np.percentile(ab[keep], 0.2, axis=0) - 0.5
    hi = np.percentile(ab[keep], 99.8, axis=0) + 0.5
    frame.a0, frame.b0 = float(lo[0]), float(lo[1])
    frame.shape = (int((hi[1] - lo[1]) / res) + 1, int((hi[0] - lo[0]) / res) + 1)
    inb = keep & (ab[:, 0] > lo[0]) & (ab[:, 0] < hi[0]) & (ab[:, 1] > lo[1]) & (ab[:, 1] < hi[1])
    sub = cloud.subset(inb)
    ab, nab = ab[inb], nab[inb]

    band_top = observed_band_top(sub, floor.value) if adaptive_band else LOW[1]
    low_cov, high_cov, nbins = _coverage_grids(sub, ab, frame, floor.value, top_h, band_top)
    # full-height surface: covered over at least half of the observed low band (and never
    # less than 0.5 m), or partly covered there and continuing above door-head height
    # (window walls: sill below, wall above)
    need = max(5, int(np.ceil(WALL_BAND_FRAC * nbins[0]))) if adaptive_band else 8
    wall = (low_cov >= need) | ((low_cov >= 5) & (high_cov >= 2))
    # wall above door-head height and not a full wall below: a doorway, or a low window or
    # wall mirror (wall seen up to ~0.6 m and above, nothing between). Either way a barrier;
    # with `low_cov <= 2` the window case was neither, and the room leaked round its walls
    lintel = (high_cov >= 2) & ~wall
    # lintel underside: narrow downward strips between door-head height and the ceiling.
    # Wide downward areas at that height are dropped ceilings, not door heads.
    hgt = sub.points[:, 1] - floor.value
    soff = (sub.normals[:, 1] < -UP_T) & (hgt > 1.85) & (hgt < top_h - 0.15)
    r, c = frame.to_cell(ab[soff])
    soffit = np.zeros(frame.shape, bool)
    soffit[r, c] = True
    soffit = ndi.binary_closing(soffit, np.ones((3, 3)))
    soffit &= ~ndi.binary_dilation(ndi.binary_opening(soffit, np.ones((20, 20))), np.ones((5, 5)))
    lintel |= ndi.binary_opening(soffit, np.ones((2, 2)))
    lintel = ndi.binary_closing(lintel, np.ones((13, 13)))
    barrier = _close_gaps(wall | lintel)

    fl = (sub.normals[:, 1] > UP_T) & (np.abs(sub.points[:, 1] - floor.value) < 0.05)
    r, c = frame.to_cell(ab[fl])
    floor_obs = np.zeros(frame.shape, bool)
    floor_obs[r, c] = True
    footprint = ndi.binary_fill_holes(ndi.binary_closing(floor_obs, np.ones((11, 11))))
    free = footprint & ~barrier
    free = ndi.binary_opening(free, np.ones((3, 3)))

    # Rooms are regions enclosed by walls (so furniture-covered floor still counts).
    # Space far from any observation is treated as solid, so a wall with an unscanned
    # gap cannot leak the room into the outside.
    observed = footprint | wall | lintel
    sealed = barrier | ~ndi.binary_dilation(observed, np.ones((21, 21)))
    lab, nlab = ndi.label(~sealed)
    border = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]]).tolist())
    ceil_map = _ceiling_map(sub, ab, frame, floor.value)
    # gap-closed walls, as in the sealing test: a wall seen only above a bed head has short
    # gaps, and every row through one would otherwise cut an unsealed room in two
    enclosed = _enclosed(barrier)
    region = np.zeros(frame.shape, np.int32)
    k = 0
    for li in range(1, nlab + 1):
        m = lab == li
        if li in border:
            # not sealed (large unscanned wall gap): keep cells that have a wall in all four
            # axis directions, plus observed floor
            m &= enclosed | ndi.binary_dilation(footprint, np.ones((5, 5)))
            m &= enclosed
        m = ndi.binary_opening(m, np.ones((5, 5)))
        if (m & free).sum() * res * res < MIN_ROOM_AREA:
            continue
        for mm in _split_necks(m, ceil_map, res, high_cov=high_cov):
            if (mm & free).sum() * res * res >= MIN_ROOM_AREA:
                k += 1
                region[mm] = k

    rooms: list[Room] = []
    labels = np.zeros(frame.shape, np.int32)
    for li in range(1, k + 1):
        m = region == li
        # fill furniture notches / holes so the polygon follows walls, not furniture
        m = ndi.binary_fill_holes(ndi.binary_closing(m, np.ones((9, 9))) | m)
        segs = _rectilinear_polygon(m, frame)
        if len(segs) < 3:
            continue
        others = (region > 0) & (region != li)
        squared = _square_corners(segs)
        if squared is not segs and not _overlaps(squared, segs, ndi.binary_dilation(others, np.ones((25, 25))),
                                                 frame):
            segs = squared  # (not where a restored corner would reach towards a neighbour)
        verts = _vertices(segs)
        poly = Polygon(verts)
        if not poly.is_valid or poly.area < MIN_ROOM_AREA:
            continue
        for s in segs:
            s["_nrm"] = nab
            s["_c0"] = s["coord"]  # where the raster put it, for undoing a snap
        # the room's own ceiling (rooms of one flat differ), where the capture saw it
        seen = ceil_map[m][np.isfinite(ceil_map[m])]
        room_ceil = float(np.median(seen)) if len(seen) >= 50 else (top_h if ceil_src == "ceiling_plane" else None)
        others = (region > 0) & (region != li)
        # the room's own cells, 8 cm in from its outline (the raster outline is 4 cm off in places)
        own = ndi.binary_erosion(m, np.ones((9, 9)))
        segs = _refine_walls(segs, verts, ab, sub, floor.value, poly, others=others, frame=frame,
                             ceiling_h=room_ceil, own=own)
        settled = _settle(segs)
        if settled is not None:
            settled = _fill_slots(settled, others, frame)
            # each final wall's evidence, measured where it ended up (merged walls span the old step)
            v = _vertices(settled)
            polished = _refine_walls([dict(s) for s in settled], v, ab, sub, floor.value, Polygon(v),
                                     search_in=0.03, search_out=0.03, others=others, frame=frame)
            segs = polished if _outline_ok(polished) else settled
            verts = _vertices(segs)
        else:  # last resort: the raster contour, without snapping
            segs = _fallback_segments(m, frame)
            verts = _vertices(segs)
            if not Polygon(verts).is_valid:
                continue
        rid = f"room_{len(rooms) + 1}"
        labels[m] = len(rooms) + 1
        walls = []
        for i, s in enumerate(segs):
            p, q = verts[i], verts[(i + 1) % len(verts)]
            d = (q - p) / max(np.linalg.norm(q - p), 1e-9)
            inward = np.array([-d[1], d[0]])  # CCW polygon: interior on the left
            walls.append(Wall(id=f"{rid}_w{i + 1}", orient=s["orient"], start=p, end=q, inward=inward,
                              sigma=float(s.get("sigma", 0.03)), support=int(s.get("support", 0)),
                              coverage=float(s.get("coverage", 0.0)), spread=float(s.get("spread", 0.0))))
        rooms.append(Room(id=rid, label_id=len(rooms) + 1, polygon=verts, walls=walls, floor=floor,
                          ceiling=ceil, ceiling_source=ceil_src, mask=m))

    _per_room_levels(rooms, sub, ab, frame, floor, ceil_src)
    return Layout(frame=frame, floor=floor, ceiling=ceil, rooms=rooms, labels=labels,
                  grids={"wall": wall, "lintel": lintel, "footprint": footprint, "free": free,
                         "floor_obs": floor_obs, "low_cov": low_cov, "high_cov": high_cov,
                         "band_top": band_top, "wall_need_bins": need})


def _ceiling_map(cloud: Cloud, ab: np.ndarray, frame: PlanFrame, floor_y: float) -> np.ndarray:
    """Per-cell ceiling height above floor (NaN where unobserved)."""
    h = cloud.points[:, 1] - floor_y
    m = (cloud.normals[:, 1] < -UP_T) & (h > 1.9)
    r, c = frame.to_cell(ab[m])
    s = np.zeros(frame.shape)
    n = np.zeros(frame.shape)
    np.add.at(s, (r, c), h[m])
    np.add.at(n, (r, c), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, s / n, np.nan)


def _furniture_gap(seg: np.ndarray, x: int, y: int, m: np.ndarray, ceil_map: np.ndarray,
                   high_cov: np.ndarray | None, cx: float, cy: float) -> bool:
    """A neck between two pieces of furniture rather than a doorway.

    Both conditions together (each alone also matches real doorways): the capture saw the
    ceiling right over the neck, at both parts' ceiling height (it looked up there), and yet
    nothing beside the neck continues above door-head height (a doorway's jambs carry wall
    up to the ceiling; furniture tops out below it).
    """
    if high_cov is None or not (np.isfinite(cx) and np.isfinite(cy)):
        return False
    mx, my = seg == x, seg == y
    neck = (mx & ndi.binary_dilation(my)) | (my & ndi.binary_dilation(mx))
    cn = ceil_map[ndi.binary_dilation(neck, np.ones((11, 11)))]  # +-10 cm round the neck
    seen = np.isfinite(cn)
    if seen.mean() < 0.6:
        return False
    cmed = float(np.median(cn[seen]))
    if abs(cmed - cx) > 0.12 or abs(cmed - cy) > 0.12:
        return False
    flanks = ndi.binary_dilation(neck, np.ones((15, 15))) & ~m  # +-14 cm, outside the region
    return int((high_cov[flanks] >= 1).sum()) <= 2


def _split_necks(m: np.ndarray, ceil_map: np.ndarray, res: float,
                 high_cov: np.ndarray | None = None) -> list[np.ndarray]:
    """Split a sealed region at narrow necks (doorways without a detected lintel).

    Watershed on the distance transform gives candidate rooms; two neighbours stay
    merged when the opening between them is about as wide as the narrower of the two
    (open-plan continuation) and their ceilings are at the same height, or when the
    neck is a gap between furniture (`_furniture_gap`).
    """
    from skimage.morphology import h_maxima
    from skimage.segmentation import watershed

    dist = ndi.distance_transform_edt(m) * res
    markers, nm = ndi.label(h_maxima(dist, 0.15))
    if nm <= 1:
        return [m]
    ws = watershed(-dist, markers, mask=m)
    ids = list(range(1, nm + 1))
    parent = {i: i for i in ids}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    while True:
        seg = np.vectorize(lambda v: find(v) if v else 0)(ws) if len(ids) < 60 else ws
        uniq = [u for u in np.unique(seg) if u]
        if len(uniq) <= 1:
            break
        info = {}
        for u in uniq:
            mu = seg == u
            cm = ceil_map[mu]
            info[u] = (float(dist[mu].max()), float(np.nanmedian(cm)) if np.isfinite(cm).any() else np.nan,
                       mu.sum() * res * res)
        # boundary lengths between neighbouring segments
        pairs = {}
        for a_sl, b_sl in [((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
                           ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))]:
            A, B = seg[a_sl], seg[b_sl]
            d = (A != B) & (A > 0) & (B > 0)
            for x, y in zip(A[d], B[d]):
                key = (min(x, y), max(x, y))
                pairs[key] = pairs.get(key, 0) + 1
        best = None
        for (x, y), cnt in pairs.items():
            blen = cnt * res
            wx, cx, ax = info[x]
            wy, cy, ay = info[y]
            same_ceiling = not (np.isfinite(cx) and np.isfinite(cy) and abs(cx - cy) > 0.12)
            open_plan = blen >= 0.8 * 2 * min(wx, wy)
            tiny = min(ax, ay) < 1.5
            if not (open_plan or tiny) and same_ceiling and _furniture_gap(seg, x, y, m, ceil_map, high_cov,
                                                                           cx, cy):
                open_plan = True
            if (same_ceiling and open_plan) or tiny:
                score = blen / (2 * min(wx, wy)) + (10 if tiny else 0)
                if best is None or score > best[0]:
                    best = (score, x, y)
        if best is None:
            break
        parent[find(best[2])] = find(best[1])
    seg = np.vectorize(lambda v: find(v) if v else 0)(ws)
    return [seg == u for u in np.unique(seg) if u]


def _per_room_levels(rooms: list[Room], cloud: Cloud, ab: np.ndarray, frame: PlanFrame,
                     floor: Level, ceil_src: str) -> None:
    """Floor and ceiling heights measured inside each room footprint."""
    row, col = frame.to_cell(ab)
    ok = (row >= 0) & (row < frame.shape[0]) & (col >= 0) & (col < frame.shape[1])
    for room in rooms:
        inner = ndi.binary_erosion(room.mask, np.ones((7, 7)))  # stay 6 cm off the walls
        inside = np.zeros(len(ab), bool)
        inside[ok] = inner[row[ok], col[ok]]
        up = inside & (cloud.normals[:, 1] > UP_T)
        down = inside & (cloud.normals[:, 1] < -UP_T)
        f = floor_level(cloud, inside)
        if f is not None and abs(f.value - floor.value) < 0.25:
            # the peak is the most-looked-at patch; every 25 cm patch of the floor counts once
            room.floor = area_level(cloud.points[up, 1], ab[up], f)
        c = ceiling_level(cloud, room.floor.value, inside)
        if c is not None:
            c = area_level(cloud.points[down, 1], ab[down], c)
            room.ceiling, room.ceiling_source = c, "ceiling_plane"
        else:
            near = np.zeros(len(ab), bool)
            near[ok] = ndi.binary_dilation(room.mask, np.ones((25, 25)))[row[ok], col[ok]]
            wt = wall_top_level(cloud, room.floor.value, near)
            if wt is not None:
                room.ceiling, room.ceiling_source = wt, "wall_top"
