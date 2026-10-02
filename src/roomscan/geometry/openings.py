"""Door / window detection by ray evidence on each wall plane.

For every wall we accumulate, on a (u along wall, h above floor) grid:
  * hits: depth samples lying on the wall plane
  * pass: camera rays that cross the wall plane and hit something beyond it
A cell is "open" when rays consistently pass through it. Open regions touching the
floor are doors, the rest windows. Width is measured from the open/closed transition
at 1 cm resolution along the wall.

Limitations (documented in the report): a closed door leaf looks like wall (the capture
protocol asks for interior doors to be opened), and a mirror looks like an opening.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi

from roomscan.capture import PosedCapture
from roomscan.geometry.layout import Layout, Room, Wall
from roomscan.geometry.pointcloud import backproject

UB, HB = 0.01, 0.05  # grid resolution along wall / height


@dataclass
class Opening:
    id: str
    kind: str  # "door" | "window" | "opening"
    room_id: str
    wall_id: str
    u0: float  # start along the wall from wall.start (m)
    u1: float
    bottom: float  # height above floor (m)
    top: float
    sigma_w: float  # 1-sigma of width (m)
    connects: str | None = None  # other room id for doors/openings between rooms
    support: int = 0
    note: str | None = None  # set when the opening is inferred rather than measured

    @property
    def width(self) -> float:
        return self.u1 - self.u0

    @property
    def height(self) -> float:
        return self.top - self.bottom


class _WallAcc:
    def __init__(self, room: Room, wall: Wall, height: float):
        self.room, self.wall = room, wall
        self.d = (wall.end - wall.start) / max(wall.length, 1e-9)
        self.n_out = -wall.inward
        self.nu = int(np.ceil(wall.length / UB)) + 1
        self.nh = int(np.ceil(height / HB)) + 1
        self.height = height
        self.hit = np.zeros((self.nh, self.nu), np.float32)
        self.pas = np.zeros((self.nh, self.nu), np.float32)
        self.beyond: list[np.ndarray] = []  # sample of (u, h, depth beyond wall) for the mirror test


def detect_openings(cap: PosedCapture, layout: Layout, frame_stride: int = 2,
                    pixel_stride: int = 3, max_depth: float = 10.0, cloud=None,
                    mirrors: list | None = None) -> list[Opening]:
    """cloud: fused cloud, enables the mirror test. mirrors: list that receives a record
    for every candidate rejected as a mirror."""
    fr = layout.frame
    accs: list[_WallAcc] = []
    for room in layout.rooms:
        h = room.height if room.height else 2.4
        for w in room.walls:
            if w.length >= 0.5:
                accs.append(_WallAcc(room, w, min(h, 3.5)))
    if not accs:
        return []
    S = np.array([a.wall.start for a in accs])  # (W,2)
    D = np.array([a.d for a in accs])
    NO = np.array([a.n_out for a in accs])
    L = np.array([a.wall.length for a in accs])
    F = np.array([a.room.floor.value for a in accs])
    H = np.array([a.height for a in accs])

    NH = np.array([a.nh for a in accs], np.int64)
    NU = np.array([a.nu for a in accs], np.int64)
    # every wall's (nh, nu) grid as one slice of a flat array: counts are gathered as flat
    # cell numbers for the whole capture and added up once at the end
    base = np.concatenate([[0], np.cumsum(NH * NU)]).astype(np.int64)
    hit_cells: list[np.ndarray] = []
    pas_cells: list[np.ndarray] = []

    def cells(wi, h, u):
        return base[wi] + np.clip((h / HB).astype(int), 0, NH[wi] - 1) * NU[wi] + np.clip((u / UB).astype(int), 0, NU[wi] - 1)

    def evidence(f):
        """One frame: (hit cells, pass cells, [(wall, mirror-test samples)]), or None."""
        d = f.depth_fn()
        if f.conf_fn is not None:
            c = f.conf_fn()
            if c is not None and c.shape == d.shape:
                d = np.where(c >= 1, d, 0)
        P = backproject(d, f.K)[::pixel_stride, ::pixel_stride].reshape(-1, 3)
        dd = d[::pixel_stride, ::pixel_stride].reshape(-1)
        ok = (dd > 0.2) & (dd < max_depth)
        if ok.sum() < 50:
            return None
        Pw = P[ok] @ f.T_wc[:3, :3].T + f.T_wc[:3, 3]
        C = f.T_wc[:3, 3]
        pab = fr.to_plan(Pw[:, [0, 2]])
        cab = fr.to_plan(C[None, [0, 2]])[0]
        # signed distance beyond the wall (positive = outside the room), (N, W). Written out
        # per plan axis rather than as a sum over a length-2 axis: same terms in the same
        # order, without numpy's slow short-axis reduction.
        sP = (pab[:, 0:1] - S[:, 0]) * NO[:, 0] + (pab[:, 1:2] - S[:, 1]) * NO[:, 1]
        sC = ((cab[None, :] - S) * NO).sum(-1)  # (W,)
        inside_cam = sC < -0.2
        # Only the few (sample, wall) pairs that pass the plane tests are evaluated further.
        # surface hits: on the wall plane, inside its extent
        r, w = np.nonzero(np.abs(sP) < 0.04)
        uP = (pab[r, 0] - S[w, 0]) * D[w, 0] + (pab[r, 1] - S[w, 1]) * D[w, 1]
        hP = Pw[r, 1] - F[w]
        m = (uP > 0) & (uP < L[w]) & (hP > 0) & (hP < H[w])
        hits = cells(w[m], hP[m], uP[m])
        # rays crossing the plane: camera inside, sample > 15 cm beyond the wall
        r, w = np.nonzero(inside_cam[None] & (sP > 0.15))
        t = sC[w] / (sC[w] - sP[r, w])
        X0 = cab[0] + t * (pab[r, 0] - cab[0])
        X1 = cab[1] + t * (pab[r, 1] - cab[1])
        uX = (X0 - S[w, 0]) * D[w, 0] + (X1 - S[w, 1]) * D[w, 1]
        hX = (C[1] + t * (Pw[r, 1] - C[1])) - F[w]
        m = (uX > 0) & (uX < L[w]) & (hX > 0) & (hX < H[w])
        r, w, uX, hX = r[m], w[m], uX[m], hX[m]
        beyond = []
        if cloud is not None and len(w):
            # every 7th crossing sample of each wall (samples in image order) for the mirror test
            order = np.argsort(w, kind="stable")
            starts = np.flatnonzero(np.r_[True, w[order][1:] != w[order][:-1]])
            for k0, k1 in zip(starts, np.r_[starts[1:], len(order)]):
                sel = order[k0:k1][::7]
                beyond.append((w[sel[0]], np.concatenate([np.stack([uX[sel], hX[sel]], 1), Pw[r[sel]]], 1)))
        return hits, cells(w, hX, uX), beyond

    # frames in parallel (numpy and the PNG decoder release the GIL), results taken in frame
    # order: the evidence is exactly what a loop over the frames gathers
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as ex:
        for res in ex.map(evidence, cap.frames[::frame_stride]):
            if res is None:
                continue
            hit_cells.append(res[0])
            pas_cells.append(res[1])
            for wi, samples in res[2]:
                accs[wi].beyond.append(samples)

    n_cells = int(base[-1])
    for cell_list, attr in ((hit_cells, "hit"), (pas_cells, "pas")):
        flat = np.concatenate(cell_list) if cell_list else np.zeros(0, np.int64)
        # exact: integer counts, as many single additions of 1 into float32 gave
        counts = np.bincount(flat, minlength=n_cells).astype(np.float32)
        for wi, a in enumerate(accs):
            setattr(a, attr, counts[base[wi]:base[wi + 1]].reshape(a.nh, a.nu).copy())

    occupied = _VoxelSet(cloud.points, 0.06) if cloud is not None else None
    openings: list[Opening] = []
    for a in accs:
        for op in _extract(a, len(openings)):
            if occupied is not None and _is_mirror(a, op, fr, occupied):
                if mirrors is not None:
                    mirrors.append({"room_id": op.room_id, "wall_id": op.wall_id, "width": round(op.width, 3),
                                    "bottom": round(op.bottom, 2), "top": round(op.top, 2)})
                continue
            op.id = f"op_{len(openings) + 1}"
            openings.append(op)
    _link_rooms(openings, layout)
    return openings


_NEIGHBOURS = np.array([(0, 0, 0), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)], np.int64)


class _VoxelSet:
    """Voxels holding a cloud point, plus their 6-neighbourhood so a point within ~1 voxel of
    a surface counts. Voxel keys are packed into one int64 and kept sorted: membership is a
    binary search (a Python set of key tuples took 20 s to build for a 2.4 M point flat)."""

    def __init__(self, points: np.ndarray, voxel: float):
        points = points[np.isfinite(points).all(1)]
        k = np.floor(points / voxel).astype(np.int64)
        self.lo = (k.min(0) if len(k) else np.zeros(3, np.int64)) - 1
        self.span = (k.max(0) if len(k) else np.zeros(3, np.int64)) + 2 - self.lo
        self.keys = np.unique(self._pack(k))

    def _pack(self, k: np.ndarray) -> np.ndarray:
        q = k - self.lo
        return (q[:, 0] * self.span[1] + q[:, 1]) * self.span[2] + q[:, 2]

    def contains(self, keys: np.ndarray) -> np.ndarray:
        """keys (M, 3) int64 voxel keys -> (M,) bool: the voxel or a face neighbour holds a point."""
        out = np.zeros(len(keys), bool)
        if not len(self.keys):
            return out
        for o in _NEIGHBOURS:
            q = keys - o  # the point voxel that would have this key as its (o) neighbour
            inside = ((q >= self.lo) & (q < self.lo + self.span)).all(1)
            c = self._pack(q[inside])
            pos = np.searchsorted(self.keys, c)
            found = np.zeros(len(c), bool)
            ok = pos < len(self.keys)
            found[ok] = self.keys[pos[ok]] == c[ok]
            out[np.flatnonzero(inside)[found]] = True
        return out


def _is_mirror(a: _WallAcc, op: Opening, fr, occupied: _VoxelSet, voxel: float = 0.06) -> bool:
    """A mirror shows the room behind the wall plane. Reflect what is seen "through" the
    opening back across the wall: if it lands on real surfaces of this room, it is a mirror."""
    if not a.beyond:
        return False
    B = np.concatenate(a.beyond)
    m = (B[:, 0] > op.u0) & (B[:, 0] < op.u1) & (B[:, 1] > max(op.bottom, 0.2)) & (B[:, 1] < op.top)
    # floor-level points are excluded: a real doorway shows floor on both sides, which reflects onto floor
    if m.sum() < 60:
        return False
    Pw = B[m, 2:5]
    n_plan = fr.to_world(a.n_out[None])[0]
    n3 = np.array([n_plan[0], 0.0, n_plan[1]])
    s_plan = fr.to_world(a.wall.start[None])[0]
    s0 = np.array([s_plan[0], 0.0, s_plan[1]])
    dist = (Pw - s0) @ n3  # > 0 beyond the wall
    refl = Pw - 2 * dist[:, None] * n3
    keys = np.floor(refl / voxel).astype(np.int64)
    frac = np.mean(occupied.contains(keys))
    return bool(frac > 0.6)


def _extract(a: _WallAcc, start_idx: int) -> list[Opening]:
    # smooth along u a little, then decide open cells
    hit = ndi.uniform_filter(a.hit, size=(1, 3))
    pas = ndi.uniform_filter(a.pas, size=(1, 3))
    open_ = (pas >= 1.0) & (pas > 2.0 * hit)
    open_ = ndi.binary_closing(open_, np.ones((3, 5)))
    open_ = ndi.binary_opening(open_, np.ones((3, 5)))
    lab, n = ndi.label(open_)
    out = []
    for i in range(1, n + 1):
        rows, cols = np.where(lab == i)
        bottom, top = rows.min() * HB, (rows.max() + 1) * HB
        if top - bottom < 0.4:
            continue
        # width from the column profile over the opening's height range
        r0, r1 = rows.min(), rows.max() + 1
        band = slice(r0 + (r1 - r0) // 5, r1 - (r1 - r0) // 5 or r1)
        frac = (pas[band] > 2.0 * hit[band]).mean(0)
        c0, c1 = cols.min(), cols.max() + 1
        u0, u1 = _edge(frac, c0, -1), _edge(frac, c1 - 1, +1)
        width = u1 - u0
        if width < 0.35 or width > 0.9 * a.wall.length + 0.5:
            continue
        kind = "door" if bottom < 0.25 and top > 1.6 else ("window" if bottom >= 0.25 else "opening")
        support = int(a.pas[r0:r1, c0:c1].sum())
        if support < 20:
            continue
        sigma = 0.01 + 0.5 * UB + 0.02 / np.sqrt(max(support / 50, 1))
        out.append(Opening(id=f"op_{start_idx + len(out) + 1}", kind=kind, room_id=a.room.id,
                           wall_id=a.wall.id, u0=u0, u1=u1, bottom=float(bottom), top=float(top),
                           sigma_w=float(sigma), support=support))
    return out


def _edge(frac: np.ndarray, c: int, direction: int) -> float:
    """Sub-cell position where the open fraction crosses 0.5 walking outward from c."""
    n = len(frac)
    i = c
    while 0 <= i + direction < n and frac[i + direction] >= 0.5:
        i += direction
    j = i + direction
    if not (0 <= j < n):
        return (i + (1 if direction > 0 else 0)) * UB
    fi, fj = frac[i], frac[j]
    t = (fi - 0.5) / max(fi - fj, 1e-6)
    # boundary between cell centres i and j
    return (i + 0.5 + direction * t) * UB


def _link_rooms(openings: list[Opening], layout: Layout) -> None:
    """Door/opening -> neighbouring room: step through the wall and look up the room label."""
    fr = layout.frame
    walls = {w.id: w for r in layout.rooms for w in r.walls}
    by_label = {r.label_id: r.id for r in layout.rooms}
    for op in openings:
        if op.kind == "window":
            continue
        w = walls[op.wall_id]
        d = (w.end - w.start) / max(w.length, 1e-9)
        mid = w.start + d * (op.u0 + op.u1) / 2
        for step in np.arange(0.1, 1.0, 0.05):
            p = mid - w.inward * step
            r, c = fr.to_cell(p[None])
            if 0 <= r[0] < fr.shape[0] and 0 <= c[0] < fr.shape[1]:
                other = by_label.get(int(layout.labels[r[0], c[0]]))
                if other is not None and other != op.room_id:
                    op.connects = other
                    break


def dedupe_openings(openings: list[Opening]) -> list[Opening]:
    """The same doorway is seen from both rooms: keep the better-supported record."""
    keep: list[Opening] = []
    pairs: dict[tuple, Opening] = {}
    for op in openings:
        if op.connects is None:
            keep.append(op)
            continue
        key = tuple(sorted([op.room_id, op.connects]))
        other = pairs.get(key)
        if other is None or op.support > other.support:
            pairs[key] = op
    # several doors may join the same pair of rooms; a coarse key is fine for flats
    return keep + list(pairs.values())
