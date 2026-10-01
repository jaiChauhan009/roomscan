"""Room layout extraction from a fused, gravity-aligned point cloud.

Pipeline:
  1. floor / ceiling heights, dominant (Manhattan) wall direction
  2. rasterise into a 2 cm top-down grid in the Manhattan-aligned "plan" frame
  3. per cell vertical coverage of wall-facing points -> full-height walls and
     door lintels (wall above ~2.1 m, nothing below)
  4. free floor space minus walls and lintels -> connected components = rooms
  5. room mask -> rectilinear polygon -> each edge snapped to the real wall plane
     using the 3D points (sub-centimetre)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy import ndimage as ndi
from shapely.geometry import Point, Polygon

from roomscan.geometry.planes import (UP_T, WALL_T, Level, ceiling_level, floor_level,
                                      manhattan_yaw, wall_top_level)
from roomscan.geometry.pointcloud import Cloud

RES = 0.02  # plan grid resolution (m)
HBIN = 0.10  # height bin for vertical coverage (m)
LOW = (0.3, 1.9)  # height band (above floor) that must be covered for a full wall
LINTEL_MIN = 2.15  # door heads sit below this; wall above it and nothing below = doorway
MIN_ROOM_AREA = 1.0  # m2


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


def _coverage_grids(cloud: Cloud, ab: np.ndarray, frame: PlanFrame, floor_y: float,
                    top_h: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    h = cloud.points[:, 1] - floor_y
    wallm = (np.abs(cloud.normals[:, 1]) < WALL_T) & (h > 0.05) & (h < top_h)
    row, col = frame.to_cell(ab[wallm])
    hb = np.floor(h[wallm] / HBIN).astype(int)
    nb = int(np.ceil(top_h / HBIN)) + 1
    occ = np.zeros((frame.shape[0], frame.shape[1], nb), bool)
    occ[row, col, hb] = True
    # tolerate 1-cell noise in wall position
    occ = ndi.binary_dilation(occ, structure=np.ones((3, 3, 1), bool))
    lo0, lo1 = int(LOW[0] / HBIN), int(LOW[1] / HBIN)
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


def _refine_walls(segs: list[dict], verts: np.ndarray, cloud_ab: np.ndarray, cloud: Cloud,
                  floor_y: float, poly: Polygon, search_in: float = 0.15,
                  search_out: float = 0.9) -> list[dict]:
    """Snap each axis-aligned edge onto the real wall plane seen in the 3D points."""
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
        cand &= (off > -search_in) & (off < search_out)
        nn = s["_nrm"][cand] @ inward
        cand_idx = np.where(cand)[0][nn > 0.8]
        if len(cand_idx) < 30:
            s["sigma"], s["support"], s["coverage"] = 0.03, 0, 0.0
            continue
        o = off[cand_idx]
        u = cloud_ab[cand_idx, along]
        # candidate planes = peaks in offset; score by how much of the wall length they cover
        bins = np.arange(-search_in, search_out + 0.01, 0.01)
        hist, e = np.histogram(o, bins=bins, weights=cloud.weight[cand_idx])
        hs = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
        best, best_score = None, 0.0
        for pk in np.argsort(hs)[::-1][:6]:
            if hs[pk] <= 0:
                break
            c0 = e[pk] + 0.005
            sel = np.abs(o - c0) < 0.03
            ub = np.unique(np.floor(u[sel] / 0.05))
            cov = len(ub) * 0.05 / max(hi - lo - 2 * shrink, 0.05)
            score = cov - 0.05 * max(c0, 0)  # prefer nearer planes at equal coverage
            if score > best_score:
                best, best_score = c0, score
        if best is None:
            s["sigma"], s["support"], s["coverage"] = 0.03, 0, 0.0
            continue
        sel = np.abs(o - best) < 0.025
        med = float(np.median(o[sel]))
        mad = float(np.median(np.abs(o[sel] - med))) * 1.4826
        s["coord"] = s["coord"] - med * inward[axis]
        s["sigma"] = max(mad / np.sqrt(sel.sum()), 0.002)
        s["support"] = int(sel.sum())
        s["coverage"] = float(min(1.0, len(np.unique(np.floor(u[sel] / 0.05))) * 0.05 / max(hi - lo, 0.05)))
    return segs


def extract_layout(cloud: Cloud, res: float = RES) -> Layout:
    floor = floor_level(cloud)
    if floor is None:
        raise RuntimeError("no floor found in capture")
    ceil = ceiling_level(cloud, floor.value)
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

    low_cov, high_cov, nbins = _coverage_grids(sub, ab, frame, floor.value, top_h)
    # full-height surface: well covered in the 0.3-1.9 m band, or partly covered there and
    # continuing above door-head height (window walls: sill below, wall above)
    wall = (low_cov >= 8) | ((low_cov >= 5) & (high_cov >= 2))
    lintel = (high_cov >= 2) & (low_cov <= 2)
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
    # close small gaps in walls (unobserved patches, < 50 cm) so rooms stay sealed
    barrier = ndi.binary_closing(ndi.binary_dilation(wall | lintel, np.ones((3, 3))), np.ones((25, 25)))

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
    wb = wall | lintel
    enclosed = (np.maximum.accumulate(wb, axis=0) & np.maximum.accumulate(wb[::-1], axis=0)[::-1]
                & np.maximum.accumulate(wb, axis=1) & np.maximum.accumulate(wb[:, ::-1], axis=1)[:, ::-1])
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
        for mm in _split_necks(m, ceil_map, res):
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
        verts = _vertices(segs)
        poly = Polygon(verts)
        if not poly.is_valid or poly.area < MIN_ROOM_AREA:
            continue
        for s in segs:
            s["_nrm"] = nab
        segs = _refine_walls(segs, verts, ab, sub, floor.value, poly)
        verts = _vertices(segs)
        if not Polygon(verts).is_valid:
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
                              coverage=float(s.get("coverage", 0.0))))
        rooms.append(Room(id=rid, label_id=len(rooms) + 1, polygon=verts, walls=walls, floor=floor,
                          ceiling=ceil, ceiling_source=ceil_src, mask=m))

    _per_room_levels(rooms, sub, ab, frame, floor, ceil_src)
    return Layout(frame=frame, floor=floor, ceiling=ceil, rooms=rooms, labels=labels,
                  grids={"wall": wall, "lintel": lintel, "footprint": footprint, "free": free,
                         "floor_obs": floor_obs, "low_cov": low_cov, "high_cov": high_cov})


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


def _split_necks(m: np.ndarray, ceil_map: np.ndarray, res: float) -> list[np.ndarray]:
    """Split a sealed region at narrow necks (doorways without a detected lintel).

    Watershed on the distance transform gives candidate rooms; two neighbours stay
    merged when the opening between them is about as wide as the narrower of the two
    (open-plan continuation) and their ceilings are at the same height.
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
        f = floor_level(cloud, inside)
        if f is not None and abs(f.value - floor.value) < 0.25:
            room.floor = f
        c = ceiling_level(cloud, room.floor.value, inside)
        if c is not None:
            room.ceiling, room.ceiling_source = c, "ceiling_plane"
        else:
            near = np.zeros(len(ab), bool)
            near[ok] = ndi.binary_dilation(room.mask, np.ones((25, 25)))[row[ok], col[ok]]
            wt = wall_top_level(cloud, room.floor.value, near)
            if wt is not None:
                room.ceiling, room.ceiling_source = wt, "wall_top"
