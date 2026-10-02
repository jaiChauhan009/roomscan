"""Optional printed scale marker: metric scale for the video and photo tiers.

The monocular depth model (roomscan.ml.depth) guesses absolute scale per image with
0.4x-3.4x error. A printed ArUco marker of known side (scripts/make_marker.py, A4,
DICT_4X4_50 id 0, 180 mm black square) stuck flat on a wall fixes it: its pose from
solvePnP (IPPE_SQUARE, LM-refined) gives the true metric depth of every pixel it covers, and the
ratio to the model's depth over those pixels is the frame's scale. Pooled over all
detections in a room or clip (median, MAD spread) it replaces the guessed scale.
Caveat (measured on the LiDAR sample): the ratio is exact to <1 % *at the marker*, but
the model's own scale varies 0.42-1.37x between frames and ~6 % (median) within a frame,
so pooling across frames only makes sense for depth already made scale-consistent.

Pure functions, no I/O. Images are RGB (or grey) uint8, K the 3x3 intrinsics of that
image (pixels), depth maps any resolution with the image's aspect ratio (they are
sampled at proportionally scaled coordinates), 0 / non-finite = invalid.

Interface for the pipeline::

    res = marker_scale(images, Ks, depths)   # lists of equal length; depths may hold None
    if res is not None:                       # {"scale", "spread", "n", "quality", ...}
        depth_metric = res["scale"] * depth_model_units
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MARKER_DICT = cv2.aruco.DICT_4X4_50
MARKER_ID = 0
MARKER_SIDE_M = 0.180  # printed side of the black square (scripts/make_marker.py)

MIN_SIDE_PX = 40.0      # smaller: corner error of ~0.3 px is > 1 % of the side
MAX_TILT_DEG = 60.0     # angle between marker normal and the viewing ray
MAX_BLUR = 0.06         # edge width / marker side; heavier blur biases corners
MAX_REPROJ_PX = 1.5     # PnP reprojection RMS: larger means the quad is not a flat square
INNER = 0.7             # sample depth over the central 70 % x 70 % of the marker

_OBJ_UNIT = np.array([[-0.5, 0.5, 0], [0.5, 0.5, 0], [0.5, -0.5, 0], [-0.5, -0.5, 0]], np.float64)


@dataclass
class Detection:
    """One marker seen in one image. `ok` is False with a `reason` when rejected."""

    marker_id: int
    corners: np.ndarray            # (4, 2) px: top-left, top-right, bottom-right, bottom-left
    image_shape: tuple[int, int]   # (h, w)
    side_px: float = 0.0
    side_m: float = MARKER_SIDE_M   # printed side used for the pose
    rvec: np.ndarray | None = None
    tvec: np.ndarray | None = None  # marker centre in camera coordinates (m)
    distance: float = float("nan")  # camera centre to marker centre (m)
    normal: np.ndarray | None = None  # unit plane normal in camera coordinates, towards the camera
    tilt_deg: float = float("nan")
    blur: float = float("nan")      # estimated edge width / side
    reproj_px: float = float("nan")
    ok: bool = False
    reason: str = ""


def _detector() -> cv2.aruco.ArucoDetector:
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.cornerRefinementWinSize = 5
    params.cornerRefinementMaxIterations = 50
    params.cornerRefinementMinAccuracy = 0.01
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(MARKER_DICT), params)


def _gray(image: np.ndarray) -> np.ndarray:
    g = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    return g if g.dtype == np.uint8 else np.clip(g, 0, 255).astype(np.uint8)


def _blur_ratio(gray: np.ndarray, corners: np.ndarray, side_px: float) -> float:
    """Edge width / marker side. The marker is rectified at its own pixel size; the
    steepest intensity step across its edges is contrast / (sigma sqrt(2 pi)) for a
    Gaussian blur, so contrast / max gradient estimates the edge width in pixels."""
    s = int(np.clip(round(side_px), 16, 400))
    m = s // 6  # include part of the white margin around the black border
    dst = np.array([[m, m], [m + s, m], [m + s, m + s], [m, m + s]], np.float32)
    H = cv2.getPerspectiveTransform(corners.astype(np.float32), dst)
    patch = cv2.warpPerspective(gray, H, (s + 2 * m, s + 2 * m), flags=cv2.INTER_LINEAR).astype(np.float32)
    gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    g = np.hypot(gx, gy)[2:-2, 2:-2]
    lo, hi = np.percentile(patch, [5, 95])
    contrast = hi - lo
    if contrast < 20:
        return float("inf")
    width = contrast / max(float(np.percentile(g, 99.5)), 1e-6)
    return float(max(width - 1.0, 0.0) / s)  # a perfect step still spans ~1 px


def _pose(corners: np.ndarray, K: np.ndarray, dist, side_m: float):
    """IPPE_SQUARE's two solutions plus SQPnP's (IPPE degenerates on an exactly
    fronto-parallel square, e.g. a flat scan of the page), each LM-refined; the one with
    the lowest reprojection RMS wins."""
    obj = _OBJ_UNIT * side_m
    img = corners.astype(np.float64).reshape(4, 1, 2)
    cands = []
    for flag in (cv2.SOLVEPNP_IPPE_SQUARE, cv2.SOLVEPNP_SQPNP):
        try:
            n, rvecs, tvecs, _ = cv2.solvePnPGeneric(obj, img, K, dist, flags=flag)
        except cv2.error:
            continue
        cands += [(r, t) for r, t in zip(rvecs[:n], tvecs[:n])]
    best = None
    for r, t in cands:
        r, t = r.copy(), t.copy()
        if t[2, 0] <= 0:
            continue
        r, t = cv2.solvePnPRefineLM(obj, img, K, dist, r, t)
        p, _ = cv2.projectPoints(obj, r, t, K, dist)
        err = float(np.sqrt(np.mean(np.sum((p - img) ** 2, axis=2))))
        if best is None or err < best[2]:
            best = (r.reshape(3), t.reshape(3), err)
    return best


def detect(image: np.ndarray, K: np.ndarray, side_m: float = MARKER_SIDE_M, dist=None,
           marker_id: int | None = MARKER_ID, min_side_px: float = MIN_SIDE_PX,
           max_tilt_deg: float = MAX_TILT_DEG, max_blur: float = MAX_BLUR) -> list[Detection]:
    """All scale markers in one image with their pose; rejected ones have ok=False and a
    reason ("small", "oblique", "blurred", "pose"). marker_id None accepts any id."""
    gray = _gray(image)
    K = np.asarray(K, np.float64)
    dist = np.zeros(5) if dist is None else np.asarray(dist, np.float64)
    corners_list, ids, _ = _detector().detectMarkers(gray)
    out: list[Detection] = []
    if ids is None:
        return out
    for c, i in zip(corners_list, ids.ravel()):
        if marker_id is not None and int(i) != marker_id:
            continue
        c = c.reshape(4, 2).astype(np.float64)
        sides = np.linalg.norm(c - np.roll(c, -1, axis=0), axis=1)
        d = Detection(int(i), c, gray.shape[:2], side_px=float(sides.mean()), side_m=side_m)
        out.append(d)
        if sides.min() < min_side_px:
            d.reason = "small"
            continue
        p = _pose(c, K, dist, side_m)
        if p is None:
            d.reason = "pose"
            continue
        d.rvec, d.tvec, d.reproj_px = p
        R = cv2.Rodrigues(d.rvec)[0]
        nrm = R[:, 2]
        if nrm @ d.tvec > 0:  # make it face the camera
            nrm = -nrm
        d.normal = nrm
        d.distance = float(np.linalg.norm(d.tvec))
        d.tilt_deg = float(np.degrees(np.arccos(np.clip(-nrm @ d.tvec / d.distance, -1, 1))))
        d.blur = _blur_ratio(gray, c, d.side_px)
        if d.reproj_px > MAX_REPROJ_PX:
            d.reason = "pose"
        elif d.tilt_deg > max_tilt_deg:
            d.reason = "oblique"
        elif d.blur > max_blur:
            d.reason = "blurred"
        else:
            d.ok = True
    return out


def marker_depth_ratio(det: Detection, depth: np.ndarray, K: np.ndarray) -> float | None:
    """Metric z-depth of the marker plane / depth map value, robust median over the
    marker's central INNER x INNER area (border and white margin excluded), sampled at
    the depth map's own pixels. None when too few valid depth pixels fall inside."""
    if not det.ok or depth is None:
        return None
    depth = np.asarray(depth, np.float32)
    h, w = det.image_shape
    dh, dw = depth.shape[:2]
    sx, sy = dw / w, dh / h
    # central quad in image px, from the marker plane via the pose
    R = cv2.Rodrigues(det.rvec)[0]
    inner = (_OBJ_UNIT * det.side_m * INNER) @ R.T + det.tvec
    q = inner @ np.asarray(K, np.float64).T
    q = q[:, :2] / q[:, 2:3]
    qd = (q * [sx, sy]).astype(np.float32)
    x0, y0 = np.floor(qd.min(0)).astype(int)
    x1, y1 = np.ceil(qd.max(0)).astype(int) + 1
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, dw), min(y1, dh)
    if x1 <= x0 or y1 <= y0:
        return None
    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.fillConvexPoly(mask, np.round((qd - [x0, y0]) * 16).astype(np.int32), 1, lineType=cv2.LINE_8, shift=4)
    yy, xx = np.nonzero(mask)
    if len(yy) < 4:
        # marker covers less than a few depth pixels: use the nearest pixel to the centre
        c = qd.mean(0)
        xx, yy = np.array([int(np.clip(c[0], 0, dw - 1)) - x0]), np.array([int(np.clip(c[1], 0, dh - 1)) - y0])
        if xx[0] < 0 or yy[0] < 0:
            return None
    pred = depth[yy + y0, xx + x0]
    # ray of each depth pixel centre (in image px) meets the marker plane n.X = n.t
    u = (xx + x0 + 0.5) / sx - 0.5
    v = (yy + y0 + 0.5) / sy - 0.5
    Kinv = np.linalg.inv(np.asarray(K, np.float64))
    rays = np.stack([u, v, np.ones_like(u)], 1) @ Kinv.T  # z = 1
    nrm = det.normal
    z = (nrm @ det.tvec) / (rays @ nrm)
    good = np.isfinite(pred) & (pred > 0) & np.isfinite(z) & (z > 0)
    if good.sum() < max(1, 0.5 * len(pred)):
        return None
    return float(np.median(z[good] / pred[good]))


def scale_from_depth(detections: list[tuple[Detection, np.ndarray, np.ndarray]]) -> dict | None:
    """Combine (detection, depth map, K) triples of one room or clip into one scale.

    scale = median of per-detection ratios metric / predicted depth; spread = 1.4826 MAD
    of the ratios relative to the scale (0 with one detection). quality: "good" (>= 3
    detections, spread <= 3 %), "fair" (>= 2 detections and spread <= 8 %, or a single
    one), "poor" otherwise. None when no detection gives a ratio."""
    ratios = []
    for det, depth, K in detections:
        r = marker_depth_ratio(det, depth, K)
        if r is not None and np.isfinite(r) and r > 0:
            ratios.append(r)
    if not ratios:
        return None
    r = np.asarray(ratios)
    s = float(np.median(r))
    spread = float(1.4826 * np.median(np.abs(r - s)) / s) if len(r) > 1 else 0.0
    n = len(r)
    if n >= 3 and spread <= 0.03:
        quality = "good"
    elif (n >= 2 and spread <= 0.08) or n == 1:
        quality = "fair"
    else:
        quality = "poor"
    return {"scale": s, "spread": spread, "n": n, "quality": quality, "ratios": [float(x) for x in r]}


def marker_scale(images, Ks, depths, side_m: float = MARKER_SIDE_M, dist=None,
                 marker_id: int | None = MARKER_ID) -> dict | None:
    """Scale of a room / clip from the printed marker, or None if no usable marker.

    images: RGB/grey uint8 frames; Ks: 3x3 intrinsics per image (or one K for all);
    depths: the depth model's map per image (model units; None for frames without one).
    Returns {"scale", "spread", "n", "quality", "ratios", "distances", "rejected"}, where
    metric depth = scale * model depth, spread is relative (MAD), n the detections used
    and rejected counts the reasons for detections that were not used."""
    Ks = [Ks] * len(images) if np.ndim(Ks) == 2 else list(Ks)
    used, rejected, distances = [], {}, []
    for img, K, dep in zip(images, Ks, depths):
        if img is None or dep is None:
            continue
        for d in detect(img, K, side_m=side_m, dist=dist, marker_id=marker_id):
            if not d.ok:
                rejected[d.reason] = rejected.get(d.reason, 0) + 1
                continue
            used.append((d, dep, K))
            distances.append(d.distance)
    res = scale_from_depth(used)
    if res is None:
        return None
    res["distances"] = distances
    res["rejected"] = rejected
    return res
