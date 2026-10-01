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


def detect_openings(cap: PosedCapture, layout: Layout, frame_stride: int = 2,
                    pixel_stride: int = 3, max_depth: float = 10.0) -> list[Opening]:
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

    for f in cap.frames[::frame_stride]:
        d = f.depth_fn()
        if f.conf_fn is not None:
            c = f.conf_fn()
            if c is not None and c.shape == d.shape:
                d = np.where(c >= 1, d, 0)
        P = backproject(d, f.K)[::pixel_stride, ::pixel_stride].reshape(-1, 3)
        dd = d[::pixel_stride, ::pixel_stride].reshape(-1)
        ok = (dd > 0.2) & (dd < max_depth)
        if ok.sum() < 50:
            continue
        Pw = P[ok] @ f.T_wc[:3, :3].T + f.T_wc[:3, 3]
        C = f.T_wc[:3, 3]
        pab = fr.to_plan(Pw[:, [0, 2]])
        cab = fr.to_plan(C[None, [0, 2]])[0]
        # signed distance beyond the wall (positive = outside the room)
        sP = ((pab[:, None, :] - S[None]) * NO[None]).sum(-1)  # (N,W)
        sC = ((cab[None, :] - S) * NO).sum(-1)  # (W,)
        inside_cam = sC < -0.2
        # surface hits
        uP = ((pab[:, None, :] - S[None]) * D[None]).sum(-1)
        hP = Pw[:, 1:2] - F[None]
        hitm = (np.abs(sP) < 0.04) & (uP > 0) & (uP < L[None]) & (hP > 0) & (hP < H[None])
        # rays crossing the plane: camera inside, sample > 15 cm beyond the wall
        crossm = inside_cam[None] & (sP > 0.15)
        t = np.where(crossm, sC[None] / np.where(crossm, sC[None] - sP, 1), 0)
        X = cab[None, None, :] + t[..., None] * (pab[:, None, :] - cab[None, None, :])
        uX = ((X - S[None]) * D[None]).sum(-1)
        hX = (C[1] + t * (Pw[:, 1:2] - C[1])) - F[None]
        crossm &= (uX > 0) & (uX < L[None]) & (hX > 0) & (hX < H[None])
        for wi in np.where(hitm.any(0) | crossm.any(0))[0]:
            a = accs[wi]
            m = hitm[:, wi]
            if m.any():
                np.add.at(a.hit, (np.clip((hP[m, wi] / HB).astype(int), 0, a.nh - 1),
                                  np.clip((uP[m, wi] / UB).astype(int), 0, a.nu - 1)), 1)
            m = crossm[:, wi]
            if m.any():
                np.add.at(a.pas, (np.clip((hX[m, wi] / HB).astype(int), 0, a.nh - 1),
                                  np.clip((uX[m, wi] / UB).astype(int), 0, a.nu - 1)), 1)

    openings: list[Opening] = []
    for a in accs:
        openings += _extract(a, len(openings))
    _link_rooms(openings, layout)
    return openings


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
