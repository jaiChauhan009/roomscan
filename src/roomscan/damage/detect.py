"""Damage detection on structural surfaces.

Design for CPU-only, no damage training data:
  1. geometry first: every depth pixel of a keyframe is assigned to a structural surface
     (a wall of the room the camera is in, its floor or its ceiling) or to "not structure"
     (furniture, clutter). Only image tiles that lie on structure are examined.
  2. zero-shot classification of tiles with CLIP ViT-B/32 (MIT licence, weights
     `openai/clip-vit-base-patch32`): damage prompts against a broad set of benign
     prompts (clean wall, switch, outlet, picture, shadow ...). ~30 ms per tile on CPU.
     Grounding DINO was tried first: 8 s per image on CPU and it reported stains and
     holes on undamaged walls.
  3. the damaged region inside positive tiles = surface pixels whose colour departs from
     the surface's own median colour; its metric extent comes from projecting those
     pixels onto the surface plane (area = sum of per-pixel footprints).
  4. observations of the same class on the same surface seen from several frames are
     merged; the spread between frames feeds the interval.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np

from roomscan.capture import Frame, PosedCapture
from roomscan.geometry.layout import Layout
from roomscan.geometry.pointcloud import backproject

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
# CLIP loads from pytorch_model.bin; stop transformers also fetching the hub's converted copy (605 MB, unused)
os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "1")
CLIP_ID = "openai/clip-vit-base-patch32"

DAMAGE_PROMPTS = {
    "water_stain": ["a water stain on a wall", "a brown water damage stain on a ceiling",
                    "a damp patch with a tide mark on a painted wall"],
    "crack": ["a crack in a wall", "a long crack in plaster"],
    "mold": ["black mold growing on a wall", "mildew spots on a wall"],
    "peeling_paint": ["peeling paint on a wall", "flaking blistered paint"],
    "hole": ["a hole in a wall", "broken drywall with a hole"],
}
BENIGN_PROMPTS = [
    "a clean painted wall", "a plain white wall", "a plain ceiling", "a clean floor",
    "a light switch on a wall", "a power outlet on a wall", "a picture frame on a wall",
    "a door", "a window", "a piece of furniture", "a shadow on a wall", "a ceiling light",
    "a curtain", "a mirror", "a shelf", "bathroom tiles", "a kitchen cabinet", "a thermostat on a wall",
    "a skirting board", "the corner of a room", "an air vent", "a recessed ceiling spotlight",
    "a smoke detector on a ceiling", "a lamp", "a poster on a wall", "a painting on a wall", "a door handle",
]
MIN_AREA = 0.01  # m2: smaller regions are fixtures or noise, not reportable damage
MIN_CRACK_LEN = 0.20  # m
DIM_MEAN = 45.0  # mean brightness (0-255) below which a frame is dim (the low-light warning uses it too)
DIM_TARGET = 110.0  # mean brightness a dim frame is lifted to
DIM_MAX_GAIN = 8.0  # at most 3 stops: below that there is little left to recover but noise
# CLIP threshold in dim frames (lifted first, see lift_dim): the lifted sensor noise reads as
# faint stains. On the undamaged sample flat darkened 4x and 8x with sensor noise, the usual
# 0.6 let through 3 and 5 false regions scoring 0.64-0.74 (none in the dark frames as they
# were, none in normal light); with 0.8 none, also 4x darker without noise, while a stain
# painted on the single-room sample is still found at 4x and 8x (0.94-0.96).
DIM_THRESHOLD = 0.8
GROW_STEPS = 12  # px at 960 wide: how far an outline grows from its strong-contrast pixels


def lift_dim(img: np.ndarray) -> np.ndarray:
    """A dim frame (mean brightness below DIM_MEAN) brought to a normal exposure by one gain
    on its stored values; for the camera's gamma-encoded output that is what a longer
    exposure gives. CLIP was trained on well-exposed photos and misses stains in dark tiles.
    A frame at or above DIM_MEAN is returned as it is (the same array), so it is processed
    exactly as before."""
    m = float(img.mean())
    if m >= DIM_MEAN or m <= 0.0:
        return img
    return cv2.convertScaleAbs(img, alpha=min(DIM_TARGET / m, DIM_MAX_GAIN))


@dataclass
class Observation:
    damage_class: str
    surface_id: str
    room_id: str
    surface_type: str
    score: float
    area: float
    uv_min: np.ndarray  # (u, v) on the surface: u along wall / plan a, v height / plan b
    uv_max: np.ndarray
    center_world: np.ndarray  # plan a, plan b, height above floor
    frame: int
    coarse: bool = False  # outline from CLIP tiles rather than from the colour mask


@dataclass
class Region:
    damage_class: str
    surface_id: str
    room_id: str
    surface_type: str
    score: float
    area: float
    area_sigma: float
    extent_u: float
    extent_v: float
    extent_sigma: float
    center: np.ndarray
    frames: list[int] = field(default_factory=list)
    bottom: float = 0.0  # height above floor of the lowest / highest point (walls)
    top: float = 0.0
    u_range: tuple[float, float] = (0.0, 0.0)


_clip_lock = threading.Lock()


def _clip():
    with _clip_lock:  # one load, also when preload_clip's thread is still at it
        return _load_clip()


def preload_clip() -> threading.Thread:
    """Start loading CLIP in a background thread: the imports and weights take 5-7 s cold,
    time the caller spends decoding video meanwhile (OpenCV and numpy release the GIL).
    classify_tiles waits for it; a failed load is retried, and reported, there. The caller
    joins the thread when done, so no load is left running when the process exits."""
    def run():
        try:
            _clip()
        except Exception:  # noqa: BLE001 - the foreground load reports it
            pass

    th = threading.Thread(target=run, name="clip-preload", daemon=True)
    th.start()
    return th


@lru_cache(maxsize=1)
def _load_clip():
    import torch
    from transformers import CLIPModel, CLIPProcessor

    from roomscan.ml.hub import pretrained

    model = pretrained(CLIPModel, CLIP_ID).eval()
    proc = pretrained(CLIPProcessor, CLIP_ID)
    prompts, owner = [], []
    for cls, ps in DAMAGE_PROMPTS.items():
        prompts += ps
        owner += [cls] * len(ps)
    prompts += BENIGN_PROMPTS
    owner += ["benign"] * len(BENIGN_PROMPTS)
    with torch.no_grad():
        t = model.get_text_features(**proc(text=prompts, return_tensors="pt", padding=True))
        t = getattr(t, "pooler_output", t)
        t = t / t.norm(dim=-1, keepdim=True)
    return model, proc, t, owner


def classify_tiles(tiles: list[np.ndarray]) -> tuple[list[str], np.ndarray]:
    """Per tile: best damage class and its probability mass against benign prompts."""
    import torch

    model, proc, tfeat, owner = _clip()
    classes = list(DAMAGE_PROMPTS)
    out_cls, out_p = [], []
    for i in range(0, len(tiles), 32):
        with torch.no_grad():
            f = model.get_image_features(**proc(images=tiles[i:i + 32], return_tensors="pt"))
            f = getattr(f, "pooler_output", f)
            f = f / f.norm(dim=-1, keepdim=True)
            p = (100.0 * f @ tfeat.T).softmax(-1).numpy()
        per = np.stack([p[:, [j for j, o in enumerate(owner) if o == c]].sum(1) for c in classes], 1)
        out_cls += [classes[k] for k in per.argmax(1)]
        out_p.append(per.max(1))
    return out_cls, np.concatenate(out_p) if out_p else np.zeros(0)


def _sharpness_chunks(n: int, max_frames: int) -> list[np.ndarray]:
    """Per chunk of the capture, the frames select_frames compares (at most two)."""
    return [chunk[:: max(1, len(chunk) // 2)][:2] for chunk in np.array_split(np.arange(n), max_frames)]


def sharpness_candidates(cap: PosedCapture, max_frames: int) -> list[Frame]:
    """Every frame select_frames(cap, max_frames) will read (images not needed to know)."""
    fr = [f for f in cap.frames if f.rgb_fn is not None]
    if len(fr) <= max_frames:
        return fr
    return [fr[i] for c in _sharpness_chunks(len(fr), max_frames) for i in c]


def select_frames(cap: PosedCapture, max_frames: int = 24) -> list[Frame]:
    """Sharp frames spread over the capture."""
    fr = [f for f in cap.frames if f.rgb_fn is not None]
    if len(fr) <= max_frames:
        return fr
    out = []
    for chunk in _sharpness_chunks(len(fr), max_frames):
        best, best_s = None, -1.0
        for i in chunk:
            img = fr[i].rgb_fn()
            if img is None:
                continue
            s = cv2.Laplacian(cv2.cvtColor(cv2.resize(img, (320, 240)), cv2.COLOR_RGB2GRAY), cv2.CV_32F).var()
            if s > best_s:
                best, best_s = i, s
        if best is not None:
            out.append(fr[best])
    return out


def surface_cells(layout: Layout, step: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Sample points (world) and inward normals on every wall, floor and observed ceiling."""
    from shapely.geometry import Point, Polygon

    fr = layout.frame
    pts, nrm = [], []
    for room in layout.rooms:
        h = room.height or 2.4
        for w in room.walls:
            if w.length < 0.3:
                continue
            d = (w.end - w.start) / w.length
            n_xz = fr.to_world(w.inward[None])[0]
            for u in np.arange(step / 2, w.length, step):
                xz = fr.to_world((w.start + d * u)[None])[0]
                for v in np.arange(0.4, h - 0.1, 0.8):
                    pts.append([xz[0], room.floor.value + v, xz[1]])
                    nrm.append([n_xz[0], 0.0, n_xz[1]])
        poly = Polygon(room.polygon)
        lo, hi = room.polygon.min(0), room.polygon.max(0)
        for a_ in np.arange(lo[0] + 0.4, hi[0], 2 * step):
            for b_ in np.arange(lo[1] + 0.4, hi[1], 2 * step):
                if not poly.contains(Point(a_, b_)):
                    continue
                xz = fr.to_world(np.array([[a_, b_]]))[0]
                pts.append([xz[0], room.floor.value, xz[1]])
                nrm.append([0.0, 1.0, 0.0])
                if room.ceiling_source == "ceiling_plane" and room.height:
                    pts.append([xz[0], room.floor.value + room.height, xz[1]])
                    nrm.append([0.0, -1.0, 0.0])
    return np.array(pts), np.array(nrm)


def select_frames_coverage(cap: PosedCapture, layout: Layout, max_frames: int = 64,
                           max_candidates: int = 500, images: bool = True) -> list[Frame] | None:
    """Greedy set cover: pick frames until every surface patch has been seen.

    Sampling frames evenly in time misses surfaces that the walk only glanced at (on the
    sample apartment one wall is in view in 11 of 1949 frames). A patch counts as seen by
    a frame when it projects inside the image, lies 0.6-4.5 m away at less than 70 deg
    incidence and the frame's depth agrees (so it is not hidden behind something).
    Among frames that add coverage, slow camera rotation (less motion blur) is preferred.

    images=False: decide from poses and depth only; None when that is not possible (no
    surface patches: the fallback picks frames by image sharpness).
    """
    fr = [f for f in cap.frames if f.rgb_fn is not None]
    if len(fr) <= min(max_frames, 12):
        return fr
    P, N = surface_cells(layout)
    if not len(P):
        return select_frames(cap, max_frames) if images else None
    cand = fr[:: max(1, len(fr) // max_candidates)]
    V = np.zeros((len(cand), len(P)), bool)
    blur = np.zeros(len(cand))
    for i, f in enumerate(cand):
        R, t = f.T_wc[:3, :3], f.T_wc[:3, 3]
        pc = (P - t) @ R
        z = pc[:, 2]
        d = f.depth_fn()
        h, w = d.shape
        with np.errstate(divide="ignore", invalid="ignore"):
            u = f.K[0, 0] * pc[:, 0] / z + f.K[0, 2]
            v = f.K[1, 1] * pc[:, 1] / z + f.K[1, 2]
        dist = np.linalg.norm(pc, axis=1)
        facing = ((t - P) * N).sum(1) > np.cos(np.deg2rad(70)) * dist
        ok = (z > 0.6) & (z < 4.5) & (u > 0.08 * w) & (u < 0.92 * w) & (v > 0.08 * h) & (v < 0.92 * h) & facing
        ui, vi = np.clip(u[ok].astype(int), 0, w - 1), np.clip(v[ok].astype(int), 0, h - 1)
        seen = np.abs(d[vi, ui] - z[ok]) < np.maximum(0.15, 0.08 * z[ok])
        idx = np.where(ok)[0][seen]
        V[i, idx] = True
        j = min(i + 1, len(cand) - 1) if i + 1 < len(cand) else i - 1
        dt = abs(cand[j].timestamp - f.timestamp) or 1.0
        cosang = np.clip((np.trace(cand[j].T_wc[:3, :3].T @ R) - 1) / 2, -1, 1)
        blur[i] = np.arccos(cosang) / dt  # rad/s
    quality = 1.0 / (1.0 + blur / 0.5)
    # every patch should be seen twice: a region is easier to confirm from two viewpoints
    count = np.zeros(len(P), int)
    chosen = []
    for _ in range(max_frames):
        gain = (V & (count < 2)[None]).sum(1) * quality
        gain[chosen] = 0
        k = int(np.argmax(gain))
        if gain[k] < 1.0:
            break
        chosen.append(k)
        count += V[k]
    return [cand[k] for k in sorted(chosen)]


class SurfaceIndex:
    """Assigns world points seen from a camera to wall / floor / ceiling surfaces."""

    def __init__(self, layout: Layout, tol: float):
        self.layout, self.tol = layout, tol
        self.walls = [(r, w) for r in layout.rooms for w in r.walls if w.length > 0.2]
        self.S = np.array([w.start for _, w in self.walls])
        self.D = np.array([(w.end - w.start) / w.length for _, w in self.walls])
        self.IN = np.array([w.inward for _, w in self.walls])
        self.L = np.array([w.length for _, w in self.walls])
        self.rooms = {r.label_id: r for r in layout.rooms}

    def assign(self, Pw: np.ndarray, cam: np.ndarray):
        """Pw (N,3) world -> (surface index array, table). Index -1 = not structure.

        Table rows: (surface_id, room_id, type, wall-or-None).
        """
        fr = self.layout.frame
        ab = fr.to_plan(Pw[:, [0, 2]])
        cab = fr.to_plan(cam[None, [0, 2]])[0]
        r, c = fr.to_cell(ab)
        ok = (r >= 0) & (r < fr.shape[0]) & (c >= 0) & (c < fr.shape[1])
        lab = np.zeros(len(Pw), int)
        lab[ok] = self.layout.labels[r[ok], c[ok]]
        idx = np.full(len(Pw), -1)
        table = []
        # per point: the wall facing the camera whose plane is nearest (within tol, inside
        # the wall's extent); lowest wall number on a tie
        best, has = np.zeros(len(Pw), np.intp), np.zeros(len(Pw), bool)
        if len(self.walls):
            J = np.flatnonzero(((cab[None] - self.S) * self.IN).sum(1) > 0.05)  # walls facing the camera
            # distance inside the room, (N, J), written out per plan axis (same terms, same
            # order as a sum over the axis, without numpy's slow length-2 reduction); only
            # the few (point, wall) pairs near a plane are evaluated further
            s = (ab[:, 0:1] - self.S[J, 0]) * self.IN[J, 0] + (ab[:, 1:2] - self.S[J, 1]) * self.IN[J, 1]
            pr, pc = np.nonzero(np.abs(s) < self.tol)
            w = J[pc]
            u = (ab[pr, 0] - self.S[w, 0]) * self.D[w, 0] + (ab[pr, 1] - self.S[w, 1]) * self.D[w, 1]
            k = (u > 0) & (u < self.L[w])
            pr, w, cost = pr[k], w[k], np.abs(s[pr[k], pc[k]])
            order = np.lexsort((w, cost, pr))  # by point, then distance, then wall number
            first = order[np.r_[True, pr[order][1:] != pr[order][:-1]]] if len(order) else order
            best[pr[first]] = w[first]
            has[pr[first]] = True
        wall_rows = {}
        for wi in np.unique(best[has]):
            room, w = self.walls[wi]
            h = Pw[:, 1] - room.floor.value
            top = room.height if room.height else 3.0
            m = has & (best == wi) & (h > 0.08) & (h < top - 0.05)
            if m.any():
                wall_rows[wi] = len(table)
                table.append((w.id, room.id, "wall", w))
                idx[m] = wall_rows[wi]
        for lid, room in self.rooms.items():
            m = (lab == lid) & (idx < 0)
            if not m.any():
                continue
            h = Pw[:, 1] - room.floor.value
            fm = m & (np.abs(h) < self.tol)
            if fm.any():
                idx[fm] = len(table)
                table.append((f"{room.id}_floor", room.id, "floor", None))
            if room.height and room.ceiling_source == "ceiling_plane":
                cm = m & (np.abs(h - room.height) < self.tol)
                if cm.any():
                    idx[cm] = len(table)
                    table.append((f"{room.id}_ceiling", room.id, "ceiling", None))
        return idx, table, ab


def detect_frame(frame: Frame, sidx: SurfaceIndex, threshold: float, work_w: int = 960) -> list[Observation]:
    img = frame.rgb_fn()
    if img is None:
        return []
    d = frame.depth_fn()
    if frame.conf_fn is not None:
        c = frame.conf_fn()
        if c is not None and c.shape == d.shape:
            d = np.where(c >= 1, d, 0)
    dh, dw = d.shape
    Pc = backproject(d, frame.K)
    R, t = frame.T_wc[:3, :3], frame.T_wc[:3, 3]
    Pw = Pc.reshape(-1, 3) @ R.T + t
    valid = (d.reshape(-1) > 0.2)
    idx = np.full(dh * dw, -1)
    si, table, ab = sidx.assign(Pw[valid], t)
    idx[valid] = si
    idx = idx.reshape(dh, dw)
    if not table:
        return []
    s = work_w / img.shape[1]
    small = cv2.resize(img, (work_w, int(round(img.shape[0] * s))), interpolation=cv2.INTER_AREA)
    lifted = lift_dim(small)
    if lifted is not small:
        # A dim frame: CLIP sees it lifted to a normal exposure (dark tiles hide stains from
        # it), and has to be surer, because the sensor noise is lifted with the picture.
        small, threshold = lifted, max(threshold, DIM_THRESHOLD)
    H, W = small.shape[:2]
    idx_img = cv2.resize(idx.astype(np.float32), (W, H), interpolation=cv2.INTER_NEAREST).astype(int)
    up_cam = frame.T_wc[:3, :3].T @ np.array([0.0, 1.0, 0.0])  # world up in camera axes
    k_up = int(np.round(np.degrees(np.arctan2(up_cam[0], -up_cam[1])) / 90)) % 4  # np.rot90 turns CCW
    tiles, boxes, surf = [], [], []
    for size in (224, 384):
        step = size // 2
        for y0 in range(0, max(H - size, 0) + 1, step):
            for x0 in range(0, max(W - size, 0) + 1, step):
                sub = idx_img[y0:y0 + size, x0:x0 + size]
                counts = np.bincount(sub[sub >= 0], minlength=len(table))  # pixels per surface
                if counts.max() < 0.5 * sub.size:  # also when no pixel is on a surface
                    continue
                # CLIP was trained on upright photos; the phone is often held sideways
                tiles.append(np.ascontiguousarray(np.rot90(small[y0:y0 + size, x0:x0 + size], k_up)))
                boxes.append((x0, y0, size))
                surf.append(int(counts.argmax()))  # the most common surface, lowest number on a tie
    if not tiles:
        return []
    cls, prob = classify_tiles(tiles)
    lab = cv2.cvtColor(cv2.GaussianBlur(small, (0, 0), 2), cv2.COLOR_RGB2LAB).astype(np.float32)
    z_img = cv2.resize(d, (W, H), interpolation=cv2.INTER_LINEAR)
    obs = []
    bg = None
    for sid in sorted(set(surf)):
        on = idx_img == sid
        for c in set(cl for cl, p, sf in zip(cls, prob, surf) if sf == sid and p >= threshold):
            pos = np.zeros((H, W), bool)
            best_p = 0.0
            for (x0, y0, size), cl, p, sf in zip(boxes, cls, prob, surf):
                if sf == sid and cl == c and p >= threshold:
                    pos[y0:y0 + size, x0:x0 + size] = True
                    best_p = max(best_p, float(p))
            region = pos & on
            if region.sum() < 200:
                continue
            # damaged pixels = those departing from the local surface colour. The reference
            # is the median inside the positive tiles (damage is the minority there), which
            # follows lighting gradients better than a whole-surface median.
            if bg is None:
                bg = _background(small)
            mask = _outline(lab, region, bg)
            if mask.any():
                # one damage outline per positive tile: the component with the most colour
                # contrast inside it (the stain, not the shadow or shelf edge beside it that
                # the tile also holds)
                n, cc = cv2.connectedComponents(mask)
                dE = np.linalg.norm(lab - bg, axis=-1)
                keep = np.zeros(n, bool)
                for (x0, y0, size), cl, p, sf in zip(boxes, cls, prob, surf):
                    if sf == sid and cl == c and p >= threshold:
                        sub = cc[y0:y0 + size, x0:x0 + size]
                        mass = np.bincount(sub.ravel(), weights=dE[y0:y0 + size, x0:x0 + size].ravel(),
                                           minlength=n)
                        mass[0] = 0
                        if mass.max() > 0:
                            keep[int(mass.argmax())] = True
                mask = keep[cc].astype(np.uint8)
            found = _components(mask, z_img, frame, s, table[sid], sidx, c, best_p, coarse=False)
            if not found:
                # colour gave nothing usable (patterned background, low contrast): localise
                # with finer CLIP tiles instead. Coarser outline, so a wider interval.
                found = _components(_coarse_mask(small, region, c, k_up, threshold), z_img, frame, s,
                                    table[sid], sidx, c, best_p, coarse=True)
            obs += found
    return obs


def _outline(lab: np.ndarray, region: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """Damaged pixels inside a positive region (uint8 mask).

    Colour is compared with the surrounding wall (bg: the surface colour around each pixel,
    a median over a window about twice the size of a 224 tile, so lighting gradients across
    a wall cancel and a 0.5 m stain does not set its own reference). Strong pixels depart
    from it by more than max(8, 3x the region's median departure); the outline grows from
    them into connected weaker pixels (more than max(5, 1.5x)), at most GROW_STEPS pixels,
    so a stain's faint body joins its tide mark, and holes inside an outline are filled.
    Pixels within 3 px of the region's edge are left out: there the depth-based surface
    labels, upsampled from the depth map, spill onto furniture and plants in front."""
    core = cv2.erode(region.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    if core.sum() < 200:
        return np.zeros(region.shape, np.uint8)
    dE = np.linalg.norm(lab - bg, axis=-1)
    m = float(np.median(dE[core]))
    strong = core & (dE > max(8.0, 3.0 * m))
    strong = cv2.morphologyEx(strong.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    weak = (core & (dE > max(5.0, 1.5 * m))).astype(np.uint8)
    mask = strong
    for _ in range(GROW_STEPS):  # bounded: a lighting gradient must not carry it across the wall
        grown = cv2.dilate(mask, np.ones((3, 3), np.uint8)) & (weak | strong)
        if (grown == mask).all():
            break
        mask = grown
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, cnts, -1, 1, thickness=cv2.FILLED)
    return (filled.astype(bool) & core).astype(np.uint8)


def _background(small: np.ndarray) -> np.ndarray:
    """Blurred-LAB colour of the surroundings of each pixel (see _outline)."""
    H, W = small.shape[:2]
    lo = cv2.resize(small, (W // 8, H // 8), interpolation=cv2.INTER_AREA)
    lo = cv2.medianBlur(cv2.cvtColor(lo, cv2.COLOR_RGB2LAB), 51)
    return cv2.resize(lo, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32)


def _coarse_mask(small: np.ndarray, region: np.ndarray, cls: str, k_up: int, threshold: float,
                 size: int = 112) -> np.ndarray:
    H, W = region.shape
    tiles, boxes = [], []
    for y0 in range(0, H - size + 1, size // 2):
        for x0 in range(0, W - size + 1, size // 2):
            if region[y0:y0 + size, x0:x0 + size].mean() > 0.6:
                tiles.append(np.ascontiguousarray(np.rot90(small[y0:y0 + size, x0:x0 + size], k_up)))
                boxes.append((x0, y0))
    votes = np.zeros((H, W), np.float32)
    seen = np.zeros((H, W), np.float32)
    if tiles:
        c, p = classify_tiles(tiles)
        for (x0, y0), ci, pi in zip(boxes, c, p):
            seen[y0:y0 + size, x0:x0 + size] += 1
            if ci == cls and pi >= threshold:
                votes[y0:y0 + size, x0:x0 + size] += 1
    # a pixel is damage when most of the fine tiles covering it say so
    return ((votes >= 0.75 * np.maximum(seen, 1)) & (seen > 0) & region).astype(np.uint8)


def _components(mask: np.ndarray, z_img, frame, s, row, sidx, cls: str, score: float, coarse: bool) -> list:
    out = []
    n, cc, stats, _ = cv2.connectedComponentsWithStats(mask)
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] < 60:
            continue
        o = _measure(cc == k, z_img, frame, s, row, sidx, cls, score)
        if o is None:
            continue
        if cls == "crack":
            if float(np.max(o.uv_max - o.uv_min)) < MIN_CRACK_LEN:
                continue
        elif o.area < MIN_AREA:
            continue
        o.coarse = coarse
        out.append(o)
    return out
    return obs


def _measure(mask: np.ndarray, z: np.ndarray, frame: Frame, s: float, row, sidx: SurfaceIndex,
             cls: str, score: float) -> Observation | None:
    surface_id, room_id, stype, wall = row
    K = frame.K_rgb.copy()
    K[:2] *= s
    v, u = np.where(mask)
    zz = z[v, u]
    ok = zz > 0.2
    if ok.sum() < 20:
        return None
    u, v, zz = u[ok], v[ok], zz[ok]
    Pc = np.stack([(u - K[0, 2]) * zz / K[0, 0], (v - K[1, 2]) * zz / K[1, 1], zz], 1)
    R, t = frame.T_wc[:3, :3], frame.T_wc[:3, 3]
    Pw = Pc @ R.T + t
    fr = sidx.layout.frame
    ab = fr.to_plan(Pw[:, [0, 2]])
    room = next(r for r in sidx.layout.rooms if r.id == room_id)
    hgt = Pw[:, 1] - room.floor.value
    if stype == "wall":
        n_plan = fr.to_world(wall.inward[None])[0]
        n3 = np.array([n_plan[0], 0.0, n_plan[1]])
        d = (wall.end - wall.start) / wall.length
        uu, vv = (ab - wall.start) @ d, hgt
    else:
        n3 = np.array([0.0, 1.0, 0.0])
        uu, vv = ab[:, 0], ab[:, 1]
    rays = Pc / np.linalg.norm(Pc, axis=1, keepdims=True)
    cos = np.abs((rays @ R.T) @ n3)
    foot = zz ** 2 / (K[0, 0] * K[1, 1]) / np.clip(cos, 0.2, 1.0)
    lo = np.array([np.percentile(uu, 2), np.percentile(vv, 2)])
    hi = np.array([np.percentile(uu, 98), np.percentile(vv, 98)])
    centre = np.array([np.median(ab[:, 0]), np.median(ab[:, 1]), float(np.median(hgt))])
    return Observation(cls, surface_id, room_id, stype, score, float(foot.sum()), lo, hi, centre, frame.index)


def merge(observations: list[Observation]) -> list[Region]:
    regions: list[Region] = []
    groups: list[list[Observation]] = []
    for o in sorted(observations, key=lambda o: -o.score):
        for g in groups:
            h = g[0]
            if h.damage_class == o.damage_class and h.surface_id == o.surface_id:
                gap = np.maximum(np.maximum(h.uv_min, o.uv_min) - np.minimum(h.uv_max, o.uv_max), 0)
                if np.linalg.norm(gap) < 0.25:
                    g.append(o)
                    break
        else:
            groups.append([o])
    for g in groups:
        areas = np.array([o.area for o in g])
        ext = np.array([o.uv_max - o.uv_min for o in g])
        a = float(np.median(areas))
        spread = float(np.median(np.abs(areas - a)) * 1.4826) if len(g) > 1 else 0.3 * a
        if all(o.coarse for o in g):
            spread = max(spread, 0.5 * a)
        e = np.median(ext, axis=0)
        es = float(np.median(np.abs(ext - e)) * 1.4826) if len(g) > 1 else 0.15 * float(e.max())
        lo = np.median([o.uv_min for o in g], axis=0)
        hi = np.median([o.uv_max for o in g], axis=0)
        h = g[0]
        regions.append(Region(h.damage_class, h.surface_id, h.room_id, h.surface_type,
                              score=float(max(o.score for o in g)), area=a, area_sigma=spread,
                              extent_u=float(e[0]), extent_v=float(e[1]), extent_sigma=es,
                              center=np.median([o.center_world for o in g], axis=0),
                              frames=sorted({o.frame for o in g}),
                              bottom=float(lo[1]), top=float(hi[1]), u_range=(float(lo[0]), float(hi[0]))))
    return regions


@contextmanager
def colour_prefetched(frames: list[Frame]):
    """Colour frames read from a video (LiDAR tier: VideoReader) are decoded in one forward
    pass for all of `frames` up front, and released afterwards. Images are unchanged
    (VideoReader.prefetch); other tiers hold their images in memory already."""
    readers: dict[int, tuple] = {}
    for f in frames:
        r = getattr(f.rgb_fn, "reader", None)
        if r is not None and hasattr(r, "prefetch"):
            readers.setdefault(id(r), (r, []))[1].append(f.rgb_fn.idx)
    for r, idx in readers.values():
        r.prefetch(idx)
    try:
        yield
    finally:
        for r, _ in readers.values():
            r.prefetch([])


def detect_damage(cap: PosedCapture, layout: Layout, threshold: float = 0.6, max_frames: int = 64,
                  progress: bool = False, frames: list[Frame] | None = None) -> list[Region]:
    """frames: the frames to examine, as select_frames_coverage(cap, layout, max_frames)
    chose them (chosen here when not given)."""
    from tqdm import tqdm

    tol = 0.06 if cap.tier == "lidar" else 0.20
    sidx = SurfaceIndex(layout, tol)
    obs = []
    if frames is None:
        frames = select_frames_coverage(cap, layout, max_frames)
    # in time order: a LiDAR capture's colour frames are decoded forward from the video, and
    # every step back means decoding from the start again
    frames = sorted(frames, key=lambda f: f.timestamp)
    for f in tqdm(frames, desc="damage", disable=not progress):
        obs += detect_frame(f, sidx, threshold)
    return merge(obs)
