"""Photo tier: 2-8 stills per room, one folder per room, no depth, no poses.

    <capture>/<NN_room name>/*.jpg|heic     folders sorted by name = walk order
    (images directly in <capture> = a single room; a single photo file is also accepted)

Accepted: .heic / .heif (iPhone default), .jpg / .jpeg, .png, any case, any size (large
photos are shrunk while decoding), EXIF orientation applied. Ignored: hidden and system
files (.DS_Store, ._*, Thumbs.db, desktop.ini), Live Photo .mov, .aae edit sidecars, and
an edited copy IMG_E0001 next to its original IMG_0001. ProRAW .dng is not supported and
is reported as such. A photo that cannot be decoded is skipped with a warning.

Capture protocol this code relies on (docs/capture_protocol.md):
  * in each room, stand in the doorway you entered through and take the photos sweeping
    left to right, each overlapping the previous one ("sweep photos")
  * in every folder except the first, the LAST photo is the "look-back photo": same spot,
    turned around, looking into the room you came from

Stills of plain white rooms have almost no matchable features, so room geometry does not
depend on matching photos to each other:
  1. per photo: monocular metric depth, gravity from floor/ceiling/wall normals, wall
     directions (Manhattan), camera height above the floor
  2. per room: photos share one standpoint; headings follow from wall directions (known
     modulo 90 deg) unwrapped with the left-to-right order; each photo is rescaled so all
     see the same camera height; the room is the rectangle bounded by the outermost wall
     planes (geometry/boxfit.py), with the unseen wall behind the standpoint
  3. stitch: the look-back photo is located inside an earlier room with a learned matcher
     (EfficientLoFTR + PnP on that room's depth). That gives the door position on the
     parent's wall, hence the child's placement. If no match is found the previous folder
     is used as parent and the plan says the position is approximate. Residual overlaps
     are resolved at the shared wall.
The result goes through the same openings / damage / export code as the other tiers.
"""
from __future__ import annotations

import hashlib
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from roomscan.capture import Frame, PosedCapture
from roomscan.frontends.video import DEPTH_W, cached_depths
from roomscan.pipeline import DNG_HINT, IMAGE_EXT, RAW_EXT, InputError, listdir, unwrap

WORK_LONG = 960
DEFAULT_F35 = 26.0  # iPhone main camera, 35 mm equivalent
NOISE = 0.10  # wall thickness of a fused monocular reconstruction (m)
WALL_T = 0.10  # assumed wall thickness between adjacent rooms (m)
MIN_STEP_DEG = 12.0  # two sweep photos are at least this far apart in heading
DEFAULT_CAM_H = 1.40  # phone held at chest height (m), used only when no floor is visible
_EXT_RANK = {".heic": 0, ".heif": 1, ".jpg": 2, ".jpeg": 3, ".png": 4}  # same photo in two formats: keep first


def _photos_in(d: Path, notes: list[str]) -> tuple[list[Path], int]:
    """Photos of one folder in sweep order, and how many .dng files were passed over."""
    files = [p for p in listdir(d) if p.is_file()]
    n_raw = sum(p.suffix.lower() in RAW_EXT for p in files)
    imgs = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    by_stem: dict[str, list[Path]] = {}
    for p in imgs:
        by_stem.setdefault(p.stem.casefold(), []).append(p)
    drop = set()
    for stem, ps in by_stem.items():
        if len(ps) > 1:  # IMG_0001.HEIC and IMG_0001.JPG: one photo, two formats
            drop |= set(sorted(ps, key=lambda p: _EXT_RANK[p.suffix.lower()])[1:])
        m = re.fullmatch(r"img_e(\d+)", stem)
        if m and f"img_{m.group(1)}" in by_stem:  # iOS edit exported next to its original
            drop |= set(ps)
    if drop:
        notes.append(f"{d.name}: {len(drop)} duplicate or edited copies ignored "
                     f"({', '.join(sorted(p.name for p in drop)[:3])})")
    return [p for p in imgs if p not in drop], n_raw


def find_rooms(path: Path, warnings: list[str] | None = None) -> dict[str, list[Path]]:
    """Room name -> photos in sweep order (natural file-name order, as Explorer shows it).

    `path`: a folder of per-room folders, one room's folder, or a single photo. What is
    ignored or dropped is appended to `warnings`."""
    notes = warnings if warnings is not None else []
    path = Path(path)
    if path.is_file():
        ext = path.suffix.lower()
        if ext in RAW_EXT:
            raise InputError(f"{path.name}: {DNG_HINT}")
        if ext not in IMAGE_EXT:
            raise InputError(f"{path.name} is not a photo (.heic, .jpg, .png)")
        return {path.stem: [path]}
    path = unwrap(path)
    if (path / "odometry.csv").is_file():  # its depth/ PNGs are not photos
        raise InputError(f"{path} is a Stray Scanner export, not photos: run it with --tier lidar (or auto)")
    rooms, n_raw = {}, 0
    for d in (p for p in listdir(path) if p.is_dir()):
        imgs, nr = _photos_in(d, notes)
        n_raw += nr
        if imgs:
            rooms[d.name] = imgs
    direct, nr = _photos_in(path, notes)
    n_raw += nr
    if direct and not rooms:
        rooms[path.name] = direct
    elif direct:
        notes.append(f"{len(direct)} photos next to the room folders ignored: put them in a room folder")
    if n_raw and not rooms:
        raise InputError(f"{path}: {DNG_HINT}")
    if n_raw:
        notes.append(f"{n_raw} .dng (ProRAW) photos skipped: not supported")
    return rooms


_heif_registered = False


def _register_heif() -> None:
    """Open HEIC whatever the file is called (a HEIC renamed .jpg is still a HEIC)."""
    global _heif_registered
    if not _heif_registered:
        try:
            import os

            import pillow_heif
            pillow_heif.register_heif_opener()
            # iPhone HEIC is a grid of 512 px tiles: decode them in parallel (default 4 threads)
            pillow_heif.options.DECODE_THREADS = max(pillow_heif.options.DECODE_THREADS, min(8, os.cpu_count() or 4))
        except ImportError:  # HEIC photos are then reported as unreadable
            pass
        _heif_registered = True


# EXIF orientation -> PIL transpose (as ImageOps.exif_transpose), applied after shrinking
_TRANSPOSE = {2: "FLIP_LEFT_RIGHT", 3: "ROTATE_180", 4: "FLIP_TOP_BOTTOM", 5: "TRANSPOSE",
              6: "ROTATE_270", 7: "TRANSVERSE", 8: "ROTATE_90"}


def _decode(p: Path):
    """Upright RGB PIL image and the 35 mm equivalent focal length if recorded. Photos of
    4x WORK_LONG or more (12-48 MP) are cut to about 2x WORK_LONG early: JPEG while
    decoding (draft mode, the full-size image never exists), HEIC right after decoding
    (box reduce), so the resampling and colour conversion run on the small image."""
    from PIL import Image

    _register_heif()
    im = Image.open(p)
    f35, orientation = None, 1
    try:  # a malformed EXIF block costs the focal length / orientation, not the photo
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # PIL's "Corrupt EXIF data": nothing the user can act on
            exif = im.getexif()
            orientation = exif.get(0x0112, 1)
            f35 = exif.get_ifd(0x8769).get(0xA405)
    except Exception:
        pass
    w0, h0 = im.size
    if max(w0, h0) >= 4 * WORK_LONG:  # 12-48 MP: decode reduced, then box-shrink to ~2x target
        s = 2 * WORK_LONG / max(w0, h0)
        im.draft(None, (int(w0 * s) + 1, int(h0 * s) + 1))  # JPEG: DCT-domain scaling; others: no-op
        if im.mode not in ("RGB", "RGBA", "L"):
            im = im.convert("RGB")
        f = max(im.size) // (2 * WORK_LONG)
        if f >= 2:
            im = im.reduce(f)
    im = im.convert("RGB")
    if isinstance(orientation, int) and orientation in _TRANSPOSE:
        im = im.transpose(getattr(Image.Transpose, _TRANSPOSE[orientation]))
    return im, f35


def read_photo(p: Path) -> tuple[np.ndarray, np.ndarray, dict]:
    """Upright RGB image (long side WORK_LONG), intrinsics, metadata. InputError if the file
    is not a readable photo."""
    from PIL import Image

    try:
        im, f35 = _decode(p)
    except MemoryError:
        raise
    except Exception as e:  # not an image, truncated copy, broken HEIC container ...
        why = "not an image or a format this build cannot read" if type(e).__name__ == "UnidentifiedImageError" \
            else f"{type(e).__name__}: {e}"
        raise InputError(f"cannot read photo {p.name} ({why})") from e
    w0, h0 = im.size
    s = WORK_LONG / max(w0, h0)
    im = im.resize((int(round(w0 * s)), int(round(h0 * s))), Image.LANCZOS)
    img = np.asarray(im)
    h, w = img.shape[:2]
    try:
        f35 = float(f35) if f35 else None
    except (TypeError, ValueError):
        f35 = None
    src = "exif" if f35 and 5.0 < f35 < 500.0 else "default"
    f35 = f35 if src == "exif" else DEFAULT_F35
    f = f35 / 43.267 * float(np.hypot(w, h))  # 35 mm equivalent is defined on the diagonal
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]])
    return img, K, {"f35": f35, "focal_source": src}


def rot_y(alpha: float) -> np.ndarray:
    """Rotation about +Y that turns the heading clockwise (seen from above) by alpha."""
    c, s = np.cos(alpha), np.sin(alpha)
    return np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]])


def heading_of(fwd: np.ndarray) -> float:
    """Heading of a forward vector: 0 = -Z, positive clockwise seen from above."""
    return float(np.arctan2(fwd[0], -fwd[2]))


def _rot_to_y(up: np.ndarray) -> np.ndarray:
    y = np.array([0.0, 1.0, 0.0])
    v = np.cross(up, y)
    c = float(up @ y)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c))


def refine_up(N: np.ndarray, hint: np.ndarray) -> np.ndarray:
    """World up in camera coordinates from surface normals, starting from the image-up hint."""
    up = hint / np.linalg.norm(hint)
    for _ in range(5):
        d = N @ up
        horiz = np.abs(d) > np.cos(np.deg2rad(22))  # floor / ceiling
        walls = np.abs(d) < np.sin(np.deg2rad(22))
        cand = []
        if horiz.sum() > 0.04 * len(N):
            u = (N[horiz] * np.sign(d[horiz])[:, None]).mean(0)
            cand.append((horiz.sum(), u / np.linalg.norm(u)))
        if walls.sum() > 0.04 * len(N):
            M = N[walls].T @ N[walls] / walls.sum()
            ev, evec = np.linalg.eigh(M)
            if ev[1] > 0.1 * ev[2]:  # two wall directions: up is perpendicular to both
                u = evec[:, 0] * np.sign(evec[:, 0] @ up)
            else:  # one wall direction: remove its component from the current estimate
                n = evec[:, 2]
                u = up - (up @ n) * n
                u /= np.linalg.norm(u)
            cand.append((walls.sum(), u))
        if not cand:
            break
        new = sum(w * u for w, u in cand)
        new /= np.linalg.norm(new)
        if np.degrees(np.arccos(np.clip(new @ hint / np.linalg.norm(hint), -1, 1))) > 50:
            break  # drifted away from the image-up prior: keep the last good estimate
        up = new
    return up


def _mode(v: np.ndarray, bin_m: float = 0.03) -> float | None:
    if len(v) < 80:
        return None
    h, e = np.histogram(v, bins=np.arange(v.min(), v.max() + bin_m, bin_m))
    if len(h) == 0:
        return None
    hs = np.convolve(h, [0.25, 0.5, 0.25], mode="same")
    c = e[np.argmax(hs)] + bin_m / 2
    sel = np.abs(v - c) < 2 * bin_m
    return float(np.median(v[sel]))


@dataclass
class Photo:
    path: Path
    img: np.ndarray
    K: np.ndarray
    depth: np.ndarray  # DEPTH_W wide, model units
    Kd: np.ndarray
    Rg: np.ndarray = None  # camera -> gravity frame
    cam_h: float | None = None  # camera height above floor, model units
    ceil_h: float | None = None  # ceiling height above camera, model units
    theta: float | None = None  # wall direction (mod 90 deg) in the gravity frame
    heading_g: float = 0.0  # heading of the optical axis in the gravity frame
    scale: float = 1.0  # multiplies depth to get metres
    T_wc: np.ndarray = None  # camera -> room frame (metres)


def photo_geometry(ph: Photo) -> None:
    from roomscan.geometry.pointcloud import backproject, image_normals

    Pc = backproject(ph.depth, ph.Kd)
    Nc = image_normals(Pc)
    ok = (ph.depth > 0.2) & (ph.depth < 12) & (np.abs(Nc).sum(-1) > 0)
    P, N = Pc[ok], Nc[ok]
    up = refine_up(N, np.array([0.0, -1.0, 0.0]))
    ph.Rg = _rot_to_y(up)
    Pg, Ng = P @ ph.Rg.T, N @ ph.Rg.T
    fl = (Ng[:, 1] > 0.85) & (Pg[:, 1] < -0.3)
    if fl.sum() > 200:
        y = Pg[fl, 1]
        m = _mode(y[y < np.percentile(y, 5) + 0.5])  # lowest dominant upward plane
        ph.cam_h = None if m is None else -m
    ce = (Ng[:, 1] < -0.85) & (Pg[:, 1] > 0.2)
    if ce.sum() > 200:
        y = Pg[ce, 1]
        m = _mode(y[y > np.percentile(y, 95) - 0.5])
        ph.ceil_h = m
    wl = np.abs(Ng[:, 1]) < 0.3
    if wl.sum() > 200:
        psi = np.arctan2(Ng[wl, 2], Ng[wl, 0])
        z = np.exp(1j * 4 * psi).mean()
        ph.theta = float(np.angle(z) / 4) if abs(z) > 0.15 else None
    ph.heading_g = heading_of(ph.Rg @ np.array([0.0, 0.0, 1.0]))


@dataclass
class RoomFit:
    name: str
    sweep: list[Photo]
    look_back: Photo | None
    room: object = None  # geometry.layout.Room in the room frame
    cloud: object = None
    cam_height: float = 1.4
    doorway: bool = True
    G: np.ndarray = field(default_factory=lambda: np.eye(4))  # room frame -> global
    parent: str | None = None
    link: str = "root"
    door_global: np.ndarray | None = None


def fit_room(name: str, sweep: list[Photo], warnings: list[str], bias: float) -> RoomFit | None:
    from roomscan.geometry.boxfit import fit_box
    from roomscan.geometry.pointcloud import backproject, image_normals, voxel_fuse

    for ph in sweep:
        photo_geometry(ph)
    heights = [ph.cam_h for ph in sweep if ph.cam_h]
    if not heights:
        # no floor in view: assume the usual phone height; the room gets a wide interval
        warnings.append(f"{name}: floor not visible in any photo; scale taken from an assumed phone height "
                        f"of {DEFAULT_CAM_H:.2f} m")
        heights = [DEFAULT_CAM_H * bias]
    h_med = float(np.median(heights))
    for ph in sweep:
        # same person, same phone height: per-photo depth scale is tied to the camera height
        ph.scale = (h_med / ph.cam_h if ph.cam_h else 1.0) / bias
    H = h_med / bias
    # headings: wall direction gives heading modulo 90 deg; the sweep order unwraps it
    q = np.pi / 2
    known = [ph for ph in sweep if ph.theta is not None]
    if not known:
        warnings.append(f"{name}: no wall recognised in any photo; room missing from the plan")
        return None
    Phi, prev = [], None
    for ph in sweep:
        if ph.theta is None:  # no wall direction: assume an even step from the previous photo
            Phi.append(None)
            continue
        rel = np.mod(ph.heading_g - ph.theta, q)
        if prev is None:
            cur = rel
        else:
            d = np.mod(rel - prev, q)
            cur = prev + (d if d >= np.deg2rad(MIN_STEP_DEG) else d + q)
        Phi.append(cur)
        prev = cur
    vals = [p for p in Phi if p is not None]
    for i, p in enumerate(Phi):
        if p is None:
            Phi[i] = (Phi[i - 1] + np.deg2rad(45)) if i and Phi[i - 1] is not None else vals[0]
    Phi = np.array(Phi, float)
    span = Phi.max() - Phi.min()
    doorway = span <= np.deg2rad(200)
    if doorway:  # forward (-Z) = into the room: centre of the sweep
        Phi -= np.round(((Phi.max() + Phi.min()) / 2) / q) * q
    ps, ns, ws = [], [], []
    for ph, phi in zip(sweep, Phi):
        R = rot_y(phi - ph.heading_g) @ ph.Rg
        T = np.eye(4)
        T[:3, :3], T[:3, 3] = R, (0.0, H, 0.0)
        ph.T_wc = T
        d = ph.depth * ph.scale
        Pc = backproject(d, ph.Kd)
        Nc = image_normals(Pc)
        gx = np.abs(cv2.Sobel(d, cv2.CV_32F, 1, 0, ksize=3)) + np.abs(cv2.Sobel(d, cv2.CV_32F, 0, 1, ksize=3))
        ok = (d > 0.2) & (d < 8) & (np.abs(Nc).sum(-1) > 0) & (gx < 0.25 * np.maximum(d, 0.3))
        ps.append(Pc[ok] @ R.T + T[:3, 3])
        ns.append(Nc[ok] @ R.T)
        ws.append(1.0 / (1.0 + d[ok] ** 2))
    cloud = voxel_fuse(np.concatenate(ps).astype(np.float32), np.concatenate(ns).astype(np.float32),
                       np.concatenate(ws).astype(np.float32), 0.03)
    fixed = {(1, 1): (WALL_T, 0.15)} if doorway else {}  # wall behind the doorway standpoint
    room = fit_box(cloud, 0.0, name, noise=NOISE, fixed=fixed, floor_y=0.0)
    if room is None:
        warnings.append(f"{name}: no floor / walls recovered; room missing from the plan")
        return None
    return RoomFit(name=name, sweep=sweep, look_back=None, room=room, cloud=cloud, cam_height=H, doorway=doorway)


def locate_in_room(look: Photo, rf: RoomFit, min_inliers: int = 40):
    """Pose of a photo inside an already fitted room: (position xz, heading, inliers)."""
    from roomscan.ml.matching import match_pair

    best = None
    for p in rf.sweep:
        pa, pb, _ = match_pair(p.img, look.img, long_side=512)
        if len(pa) < min_inliers:
            continue
        sx, sy = p.depth.shape[1] / p.img.shape[1], p.depth.shape[0] / p.img.shape[0]
        u = np.clip((pa[:, 0] * sx).astype(int), 0, p.depth.shape[1] - 1)
        v = np.clip((pa[:, 1] * sy).astype(int), 0, p.depth.shape[0] - 1)
        z = p.depth[v, u] * p.scale
        X = np.stack([(pa[:, 0] - p.K[0, 2]) * z / p.K[0, 0], (pa[:, 1] - p.K[1, 2]) * z / p.K[1, 1], z], 1)
        Xw = X @ p.T_wc[:3, :3].T + p.T_wc[:3, 3]
        ok = z > 0.2
        if ok.sum() < min_inliers:
            continue
        try:
            good, rvec, tvec, inl = cv2.solvePnPRansac(Xw[ok].astype(np.float64), pb[ok].astype(np.float64),
                                                       look.K, None, iterationsCount=800, reprojectionError=6.0,
                                                       confidence=0.999, flags=cv2.SOLVEPNP_SQPNP)
        except cv2.error:
            continue
        if not good or inl is None or len(inl) < min_inliers:
            continue
        R, _ = cv2.Rodrigues(rvec)
        C = -R.T @ tvec[:, 0]
        fwd = R.T @ np.array([0.0, 0.0, 1.0])
        if best is None or len(inl) > best[2]:
            best = (C[[0, 2]], heading_of(fwd), int(len(inl)))
        if best[2] >= 150:
            break
    return best


def _side_of(room, direction: np.ndarray) -> tuple[int, float]:
    """Rectangle side whose outward normal is `direction` (axis unit vector): axis, coord."""
    axis = int(np.argmax(np.abs(direction)))
    lo, hi = room.polygon[:, axis].min(), room.polygon[:, axis].max()
    return axis, float(hi if direction[axis] > 0 else lo)


def _transform_room(rf: RoomFit, G: np.ndarray) -> None:
    """Apply a room->global transform (rotation by k*90 deg about Y + translation in xz)."""
    R2 = G[np.ix_([0, 2], [0, 2])]
    t2 = G[[0, 2], 3]
    r = rf.room
    r.polygon = r.polygon @ R2.T + t2
    for i, w in enumerate(r.walls):
        w.start, w.end = r.polygon[i].copy(), r.polygon[(i + 1) % len(r.polygon)].copy()
        d = (w.end - w.start) / max(np.linalg.norm(w.end - w.start), 1e-9)
        w.inward = np.array([-d[1], d[0]])
        w.orient = "H" if abs(d[0]) > abs(d[1]) else "V"
    for ph in rf.sweep + ([rf.look_back] if rf.look_back is not None and rf.look_back.T_wc is not None else []):
        ph.T_wc = G @ ph.T_wc
    rf.G = G


def place_child(child: RoomFit, parent: RoomFit, door_xz: np.ndarray, look_heading: float,
                clamp: bool = True) -> None:
    """Attach `child` to `parent` (already global) at a door seen from inside the parent.

    look_heading: heading of the look-back photo in the global frame; the child lies
    behind the photographer, so the child's forward axis is the opposite direction.
    """
    k = int(np.round((look_heading + np.pi) / (np.pi / 2))) % 4
    ang = k * np.pi / 2
    fwd = np.array([np.sin(ang), -np.cos(ang)])  # child's forward (-Z) in global xz
    axis, coord = _side_of(parent.room, fwd)
    along = 1 - axis
    lo, hi = parent.room.polygon[:, along].min(), parent.room.polygon[:, along].max()
    pos = np.zeros(2)
    pos[along] = float(np.clip(door_xz[along], lo + 0.4, hi - 0.4)) if clamp else float(door_xz[along])
    # child's standpoint sits one wall thickness beyond the parent's wall; its own back
    # wall is WALL_T behind the standpoint
    pos[axis] = coord + np.sign(fwd[axis]) * 2 * WALL_T
    G = np.eye(4)
    G[:3, :3] = rot_y(ang)
    G[[0, 2], 3] = pos
    _transform_room(child, G)
    child.parent = parent.name
    child.door_global = pos.copy()


def run_photo_tier(path: Path, out_dir: Path, use_cache: bool = True, progress: bool = True,
                   damage: bool = True) -> dict:
    import json
    import time

    from roomscan.export.build import build_output
    from roomscan.export.render import render_plan
    from roomscan.geometry.boxfit import layout_from_rooms
    from roomscan.geometry.openings import Opening, detect_openings
    from roomscan.geometry.pointcloud import fuse_capture
    from roomscan.ml.depth import DEPTH_SCALE_BIAS

    timing, warnings = {}, []
    t0 = time.time()
    rooms = find_rooms(path, warnings)
    if not rooms:
        raise InputError(f"no photos (.heic, .jpg, .png) found in {path}")
    read: dict[Path, tuple] = {}
    errors: list[str] = []
    for rn, fl in rooms.items():
        for f in fl:
            try:
                read[f] = read_photo(f)
            except InputError as e:  # one bad file must not cost the whole capture
                errors.append(str(e))
                warnings.append(f"{rn}: {e}; photo skipped")
    for rn in [rn for rn, fl in rooms.items() if not any(f in read for f in fl)]:
        warnings.append(f"{rn}: no readable photo; room left out")
    rooms = {rn: [f for f in fl if f in read] for rn, fl in rooms.items() if any(f in read for f in fl)}
    if not rooms:
        raise InputError(f"none of the photos in {path} could be read: {errors[0]}")
    files = [f for fl in rooms.values() for f in fl]
    loaded = [read[f] for f in files]
    if any(m["focal_source"] == "default" for _, _, m in loaded):
        warnings.append("some photos have no EXIF focal length: iPhone main-camera default used")
    key = hashlib.sha1("|".join(f"{f.resolve()}:{f.stat().st_size}" for f in files).encode()).hexdigest()[:16]
    depths = cached_depths([im for im, _, _ in loaded], key, use_cache, progress)
    photos: dict[Path, Photo] = {}
    for f, (img, K, _), d in zip(files, loaded, depths):
        dh = int(round(DEPTH_W * d.shape[0] / d.shape[1]))
        ds = cv2.resize(d, (DEPTH_W, dh), interpolation=cv2.INTER_AREA).astype(np.float32)
        Kd = K.copy()
        Kd[0] *= DEPTH_W / img.shape[1]
        Kd[1] *= dh / img.shape[0]
        photos[f] = Photo(path=f, img=img, K=K, depth=ds, Kd=Kd)
    timing["load+depth"] = time.time() - t0

    t = time.time()
    fits: list[RoomFit] = []
    multi = len(rooms) > 1
    for ri, (rn, fl) in enumerate(rooms.items()):
        if not 2 <= len(fl) <= 8:
            warnings.append(f"{rn}: {len(fl)} photos (protocol asks for 2 to 8)")
        has_look_back = multi and ri > 0 and len(fl) >= 3
        sweep = [photos[f] for f in (fl[:-1] if has_look_back else fl)]
        rf = fit_room(rn, sweep, warnings, DEPTH_SCALE_BIAS)
        if rf is None:
            continue
        if has_look_back:
            rf.look_back = photos[fl[-1]]
            photo_geometry(rf.look_back)
        fits.append(rf)
    if not fits:
        why = "; ".join(w for w in warnings if "room missing" in w)[:300]
        raise InputError(f"no room could be reconstructed from the photos in {path} ({why or 'no floor or walls'}): "
                         f"each photo should show floor, walls and ceiling (docs/capture_protocol.md)")
    timing["room_fit"] = time.time() - t

    t = time.time()
    placed = [fits[0]]
    beside = []
    for rf in fits[1:]:
        done = False
        if rf.look_back is not None:
            # previous folder first, then the other placed rooms
            cands = sorted(placed, key=lambda p: 0 if p is placed[-1] else 1)
            best = None
            for cand in cands:
                r = locate_in_room(rf.look_back, cand)
                if r is not None and (best is None or r[2] > best[1][2]):
                    best = (cand, r)
                    if r[2] >= 150:
                        break
            if best is not None:
                cand, (xz, hd, n_in) = best
                place_child(rf, cand, xz, hd)
                rf.link = f"look-back photo matched in {cand.name} ({n_in} inliers)"
                done = True
        if not done and placed:
            # no appearance link: assume the previous folder is the parent, door mid-wall on
            # the side whose depth best matches what the look-back photo sees
            parent = placed[-1]
            ext = parent.room.polygon.max(0) - parent.room.polygon.min(0)
            far = None
            if rf.look_back is not None and rf.look_back.cam_h:
                lb = rf.look_back
                s = rf.cam_height / lb.cam_h
                far = float(np.median(lb.depth[lb.depth.shape[0] // 3: 2 * lb.depth.shape[0] // 3,
                                               lb.depth.shape[1] // 3: 2 * lb.depth.shape[1] // 3]) * s)
            axis = int(np.argmin(np.abs(ext - far))) if far else int(np.argmax(ext))
            centre = (parent.room.polygon.max(0) + parent.room.polygon.min(0)) / 2
            hd = 0.0 if axis == 1 else np.pi / 2  # look-back heading along that axis
            place_child(rf, parent, centre, hd)
            rf.link = f"no photo match: attached to previous folder {parent.name}, door position assumed"
            warnings.append(f"{rf.name}: look-back photo could not be matched in any earlier room; attached to "
                            f"{parent.name} by capture order, position along the wall is a guess")
            done = True
        placed.append(rf)
    n_fix = _resolve_overlaps([rf.room for rf in placed], warnings)
    timing["stitch"] = time.time() - t

    t = time.time()
    frames = []
    for rf in placed:
        for ph in rf.sweep:
            frames.append(Frame(index=len(frames), timestamp=float(len(frames)), T_wc=ph.T_wc, K=ph.Kd,
                                depth_fn=(lambda d=ph.depth * ph.scale: d), rgb_fn=(lambda im=ph.img: im),
                                K_rgb=ph.K, depth_sigma_rel=0.08))
    cap = PosedCapture("photo", Path(path).stem if Path(path).is_file() else Path(path).name, frames,
                       meta={"source": "photos", "n_photos": len(files), "n_rooms": len(rooms),
                             "depth_model": "Depth-Anything-V2-Metric-Indoor-Small",
                             "matcher": "EfficientLoFTR (look-back photos only)"})
    layout = layout_from_rooms([rf.room for rf in placed], yaw=0.0)
    cloud = fuse_capture(cap, voxel=0.03, pixel_stride=1, max_depth=8.0, progress=False)
    openings = detect_openings(cap, layout, frame_stride=1, pixel_stride=1)
    # the entry door of each linked room: known to exist at the standpoint, width not measured
    for rf in placed:
        if rf.parent is None or not rf.doorway:
            continue
        w = rf.room.walls[2]  # the wall behind the standpoint
        d = (w.end - w.start) / max(w.length, 1e-9)
        half = min(0.4, w.length / 2)
        u = float(np.clip((rf.door_global - w.start) @ d, half, w.length - half))
        openings.append(Opening(id=f"op_{len(openings) + 1}", kind="door", room_id=rf.room.id, wall_id=w.id,
                                u0=u - half, u1=u + half, bottom=0.0, top=2.0,
                                sigma_w=0.15, connects=rf.parent, support=0,
                                note="entry door at the capture standpoint; width assumed 0.80 m, not measured"))
    timing["openings"] = time.time() - t
    dmg, flags, scope = [], [], []
    if damage:
        t = time.time()
        from roomscan.damage.pipeline import assess_damage
        dmg, flags, scope, dw = assess_damage(cap, layout, openings, cloud, progress=progress)
        warnings += dw
        timing["damage"] = time.time() - t
    info = {"id": cap.name, "tier": "photo", "source": "photo_folders", "n_frames_used": len(frames),
            "meta": dict(cap.meta, links={rf.name: rf.link for rf in placed}, overlaps_resolved=n_fix,
                         camera_height_m={rf.name: round(rf.cam_height, 3) for rf in placed})}
    drift_info = {"enabled": False, "method": "not applicable (independent stills, no odometry to drift)"}
    stitch = "look-back photo located in parent room (EfficientLoFTR + depth PnP); capture-order fallback"
    out = build_output(layout, openings, "photo", info, drift_info, dmg, flags, scope, warnings, timing, stitch)
    out_dir.mkdir(parents=True, exist_ok=True)
    # UTF-8, not the Windows locale code page: room names are the user's folder names
    (out_dir / "result.json").write_text(out.model_dump_json(indent=2), encoding="utf-8")
    render_plan(out, out_dir / "plan.png")
    return json.loads(out.model_dump_json())


def _resolve_overlaps(rooms: list, warnings: list[str], gap: float = WALL_T) -> int:
    """Rectangles from independent noisy fits can overlap slightly: meet at the midline.

    A large overlap means the placement of one room is wrong; it is pushed out along the
    axis of least penetration and the plan says so.
    """
    n_fix = 0
    for _ in range(4):
        moved = False
        for i in range(len(rooms)):
            for j in range(i + 1, len(rooms)):
                A, B = rooms[i], rooms[j]
                a0, a1 = A.polygon.min(0), A.polygon.max(0)
                b0, b1 = B.polygon.min(0), B.polygon.max(0)
                ov = np.minimum(a1, b1) - np.maximum(a0, b0)
                if (ov <= -gap + 1e-6).any():
                    continue  # already separated by a wall thickness
                axis = int(np.argmin(ov))
                pen = float(ov[axis]) + gap
                small = min(float((a1 - a0)[axis]), float((b1 - b0)[axis]))
                first, second = (A, B) if (a0 + a1)[axis] < (b0 + b1)[axis] else (B, A)
                if pen < 0.5 * small:
                    _move_side(first, axis, +1, -pen / 2)
                    _move_side(second, axis, -1, +pen / 2)
                else:
                    _translate(second, axis, pen)
                    warnings.append(f"{A.id} / {B.id}: fitted rooms overlapped by {ov[axis]:.2f} m; "
                                    f"{second.id} moved, relative position unreliable")
                n_fix += 1
                moved = True
        if not moved:
            break
    return n_fix


def _move_side(room, axis: int, sign: int, delta: float) -> None:
    """Shift the wall on the +/- side of a rectangle along `axis` by delta."""
    lo, hi = room.polygon[:, axis].min(), room.polygon[:, axis].max()
    target = hi if sign > 0 else lo
    sel = np.isclose(room.polygon[:, axis], target)
    room.polygon[sel, axis] += delta
    for i, w in enumerate(room.walls):
        w.start, w.end = room.polygon[i].copy(), room.polygon[(i + 1) % len(room.polygon)].copy()
        if np.isclose(w.start[axis], target + delta) and np.isclose(w.end[axis], target + delta):
            w.sigma = float(np.hypot(w.sigma, abs(delta)))


def _translate(room, axis: int, delta: float) -> None:
    room.polygon[:, axis] += delta
    for i, w in enumerate(room.walls):
        w.start, w.end = room.polygon[i].copy(), room.polygon[(i + 1) % len(room.polygon)].copy()
