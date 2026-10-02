"""Printed scale marker (roomscan.markers): synthetic renders with a known camera, and a
semi-real test pasting the marker onto a wall of the Stray LiDAR sample scan."""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from roomscan import markers

W, H = 1280, 960
K0 = np.array([[1000.0, 0, 640], [0, 1000.0, 480], [0, 0, 1]])
SIDE = markers.MARKER_SIDE_M


def marker_texture(px: int = 240) -> tuple[np.ndarray, float]:
    """Marker on white paper; returns (texture, pixels per metre). The black square is
    `px` pixels wide and sits in the middle of a paper twice its size."""
    m = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(markers.MARKER_DICT),
                                      markers.MARKER_ID, px, borderBits=1)
    tex = np.full((2 * px, 2 * px), 255, np.uint8)
    tex[px // 2:px // 2 + px, px // 2:px // 2 + px] = m
    return tex, px / SIDE


def look_at_pose(dist: float, yaw_deg: float = 0.0, pitch_deg: float = 0.0, roll_deg: float = 0.0,
                 offset=(0.0, 0.0)) -> tuple[np.ndarray, np.ndarray]:
    """Marker-to-camera rotation and translation: marker centre at `dist` m along the
    optical axis (shifted by `offset` m), rotated about its own axes by yaw / pitch."""
    Rflip = np.diag([1.0, -1.0, -1.0])  # marker z towards the camera, y up
    ry, rp, rr = np.radians([yaw_deg, pitch_deg, roll_deg])
    Ry = cv2.Rodrigues(np.array([0, ry, 0]))[0]
    Rx = cv2.Rodrigues(np.array([rp, 0, 0]))[0]
    Rz = cv2.Rodrigues(np.array([0, 0, rr]))[0]
    R = Ry @ Rx @ Rflip @ Rz
    t = np.array([offset[0], offset[1], dist])
    return R, t


def paste_marker(img: np.ndarray, K: np.ndarray, R: np.ndarray, t: np.ndarray, ss: int = 3,
                 px: int = 240) -> np.ndarray:
    """Render the marker (pose R, t: marker coords -> camera) into img (grey or RGB) with
    `ss`x supersampling, so small markers are anti-aliased like a real camera would."""
    tex, ppm = marker_texture(px)
    c = tex.shape[0] / 2
    # texture px -> marker plane (x right, y up, metres)
    A = np.array([[1 / ppm, 0, -c / ppm], [0, -1 / ppm, c / ppm], [0, 0, 1]])
    S = np.diag([ss, ss, 1.0])
    Kss = S @ K
    Kss[0, 2] += (ss - 1) / 2  # pixel centres: image px (x) covers ss*x .. ss*x + ss - 1
    Kss[1, 2] += (ss - 1) / 2
    Hm = Kss @ np.column_stack([R[:, 0], R[:, 1], t]) @ A
    h, w = img.shape[:2]
    big = cv2.warpPerspective(tex, Hm, (w * ss, h * ss), flags=cv2.INTER_LINEAR, borderValue=0)
    alpha = cv2.warpPerspective(np.full_like(tex, 255), Hm, (w * ss, h * ss), flags=cv2.INTER_LINEAR)
    big = cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)
    a = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)[..., None] / 255
    base = img.astype(np.float32)
    if base.ndim == 3:
        out = base * (1 - a) + big[..., None] * a
    else:
        out = base * (1 - a[..., 0]) + big * a[..., 0]
    return np.clip(out, 0, 255).astype(np.uint8)


def background(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    bg = cv2.GaussianBlur(rng.uniform(60, 200, (H, W)).astype(np.float32), (0, 0), 6)
    return cv2.cvtColor(bg.astype(np.uint8), cv2.COLOR_GRAY2RGB)


def render(dist: float, yaw: float = 0.0, pitch: float = 0.0, blur: float = 0.0, noise: float = 0.0,
           seed: int = 0, offset=(0.0, 0.0)):
    R, t = look_at_pose(dist, yaw, pitch, offset=offset)
    img = paste_marker(background(seed), K0, R, t)
    if blur > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    if noise > 0:
        rng = np.random.default_rng(seed + 1)
        img = np.clip(img + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    return img, R, t


def plane_depth(R: np.ndarray, t: np.ndarray, K: np.ndarray, shape) -> np.ndarray:
    """z-depth of the marker's (infinite) plane at every pixel centre."""
    h, w = shape
    u, v = np.meshgrid(np.arange(w) + 0.0, np.arange(h) + 0.0)
    rays = np.stack([u, v, np.ones_like(u)], -1) @ np.linalg.inv(K).T
    n = R[:, 2]
    return ((n @ t) / (rays @ n)).astype(np.float32)


@pytest.mark.parametrize("dist", [1.0, 2.0, 3.0, 4.0])
def test_distance_frontal(dist):
    img, R, t = render(dist, yaw=10, pitch=-5, noise=2, seed=int(dist * 10))
    dets = [d for d in markers.detect(img, K0) if d.ok]
    assert len(dets) == 1
    d = dets[0]
    err = abs(d.distance - np.linalg.norm(t)) / np.linalg.norm(t)
    assert err < (0.01 if dist <= 2 else 0.02), (dist, d.distance, err)
    assert np.degrees(np.arccos(abs(d.normal @ R[:, 2]))) < 5


@pytest.mark.parametrize("yaw,pitch,blur,noise", [(0, 0, 1.0, 4), (40, 0, 0.7, 3), (0, 35, 0.0, 6),
                                                  (30, 25, 1.2, 4), (50, 0, 0.5, 2)])
def test_distance_2m_hard(yaw, pitch, blur, noise):
    img, R, t = render(2.0, yaw, pitch, blur=blur, noise=noise, seed=yaw + pitch, offset=(0.3, -0.2))
    dets = [d for d in markers.detect(img, K0) if d.ok]
    assert len(dets) == 1, [(d.reason, d.blur, d.tilt_deg) for d in markers.detect(img, K0)]
    assert abs(dets[0].distance / np.linalg.norm(t) - 1) < 0.02


def test_rejects_tiny():
    img, _, _ = render(5.5)  # 180 mm at 5.5 m and f=1000: ~33 px
    ds = markers.detect(img, K0)
    assert ds and not any(d.ok for d in ds) and ds[0].reason == "small"


def test_rejects_oblique():
    img, _, _ = render(1.5, yaw=68)
    ds = markers.detect(img, K0)
    assert ds and not any(d.ok for d in ds) and ds[0].reason == "oblique"


def test_rejects_blurred():
    img, _, _ = render(1.0, blur=6.0)
    ds = markers.detect(img, K0)
    assert all(not d.ok for d in ds)
    assert not ds or ds[0].reason == "blurred"


def test_no_marker():
    assert markers.detect(background(), K0) == []
    assert markers.marker_scale([background()], K0, [np.ones((H, W), np.float32)]) is None


def test_marker_scale_recovers_known_scale():
    """Depth 'model' = true plane depth / 1.7 at a quarter resolution, with per-pixel
    noise: marker_scale must give 1.7 over several frames."""
    rng = np.random.default_rng(3)
    imgs, deps = [], []
    for k, (dist, yaw) in enumerate([(1.2, 0), (2.0, 30), (2.8, -20), (3.5, 10), (2.2, 45)]):
        img, R, t = render(dist, yaw, 5, noise=3, seed=k)
        z = plane_depth(R, t, K0, (H, W))
        small = cv2.resize(z, (W // 4, H // 4), interpolation=cv2.INTER_AREA) / 1.7
        small *= rng.normal(1, 0.02, small.shape).astype(np.float32)
        imgs.append(img)
        deps.append(small)
    imgs.append(background(9))
    deps.append(None)
    res = markers.marker_scale(imgs, [K0] * len(imgs), deps)
    assert res is not None and res["n"] == 5
    assert abs(res["scale"] / 1.7 - 1) < 0.015, res
    assert res["spread"] < 0.02 and res["quality"] == "good"


def test_marker_scale_flags_inconsistent():
    imgs, deps = [], []
    for k, s in enumerate([1.0, 1.4, 0.7]):
        img, R, t = render(2.0, 10 * k, seed=k)
        imgs.append(img)
        deps.append(plane_depth(R, t, K0, (H, W)) / s)
    res = markers.marker_scale(imgs, K0, deps)
    assert res["n"] == 3 and res["quality"] == "poor" and res["spread"] > 0.1


# ---------------------------------------------------------------- semi-real (Stray LiDAR)

def _find_data() -> Path | None:
    env = os.environ.get("ROOMSCAN_DATA")
    cands = [Path(env)] if env else []
    cands += [p / "data" for p in Path(__file__).resolve().parents]
    for c in cands:
        if (c / "single_scan_with_ceiling").is_dir():
            return c / "single_scan_with_ceiling"
    return None


SCAN = _find_data()


def wall_paste_pose(depth: np.ndarray, conf: np.ndarray | None, K_rgb: np.ndarray, rgb_shape,
                    side: float = SIDE):
    """Find a flat, high-confidence patch in the LiDAR depth near the image centre and
    return a marker pose (R, t) lying on it, or None. The plane is fitted to LiDAR points
    (metres), so the pasted marker is at the wall's true depth and orientation."""
    dh, dw = depth.shape
    h, w = rgb_shape
    Kd = K_rgb.copy()
    Kd[0] *= dw / w
    Kd[1] *= dh / h
    Kinv = np.linalg.inv(Kd)
    best = None
    for cy in range(dh // 4, 3 * dh // 4, 8):
        for cx in range(dw // 4, 3 * dw // 4, 8):
            z0 = depth[cy, cx]
            if not (0.5 < z0 < 4.0):
                continue
            r = int(np.ceil(0.75 * side * Kd[0, 0] / z0))  # window ~1.5 marker sides
            ys, xs = slice(cy - r, cy + r + 1), slice(cx - r, cx + r + 1)
            if cy - r < 0 or cx - r < 0 or cy + r >= dh or cx + r >= dw:
                continue
            zz = depth[ys, xs]
            ok = (zz > 0) & np.isfinite(zz)
            if conf is not None:
                ok &= conf[ys, xs] >= 2
            if ok.mean() < 0.95:
                continue
            v, u = np.nonzero(ok)
            pts = (np.stack([u + cx - r, v + cy - r, np.ones_like(u)], 1) @ Kinv.T) * zz[ok][:, None]
            c = pts.mean(0)
            _, sv, vt = np.linalg.svd(pts - c)
            n = vt[2]
            res = np.abs((pts - c) @ n)
            if np.percentile(res, 90) > 0.008:
                continue
            if n @ c > 0:
                n = -n
            tilt = np.degrees(np.arccos(-n @ c / np.linalg.norm(c)))
            score = np.percentile(res, 90) + 0.0005 * tilt
            if best is None or score < best[0]:
                best = (score, n, c, tilt)
    if best is None:
        return None
    _, n, c, tilt = best
    # centre on the plane along the ray through the window centre
    ray = c / np.linalg.norm(c)
    t = ray * ((n @ c) / (n @ ray))
    zc = n  # marker z towards the camera (n points to the camera)
    x = np.cross(np.array([0.0, -1.0, 0.0]), zc)  # marker y roughly up (camera y is down)
    x /= np.linalg.norm(x)
    y = np.cross(zc, x)
    R = np.column_stack([x, y, zc])
    return R, t, tilt


@pytest.mark.skipif(SCAN is None, reason="sample data not present (scripts/fetch_data.py)")
def test_semi_real_lidar_scale_is_one():
    from roomscan.frontends.lidar_stray import VideoReader, find_stray_root, read_depth, read_png

    root = find_stray_root(SCAN)
    K = np.loadtxt(root / "camera_matrix.csv", delimiter=",")
    reader = VideoReader(root / "rgb.mp4", max_cache=4)
    frames = [60, 300, 600, 900]
    reader.prefetch(frames)
    imgs, deps, dists = [], [], []
    for f in frames:
        img = reader.get(f)
        dep = read_depth(root / "depth" / f"{f:06d}.png", (192, 256))
        conf = read_png(root / "confidence" / f"{f:06d}.png")
        if img is None:
            continue
        pose = wall_paste_pose(dep, conf, K, img.shape[:2])
        if pose is None:
            continue
        R, t, _ = pose
        imgs.append(paste_marker(img, K, R, t, ss=2, px=200))
        deps.append(dep)
        dists.append(np.linalg.norm(t))
    reader.prefetch([])
    assert len(imgs) >= 2
    res = markers.marker_scale(imgs, K, deps)
    assert res is not None and res["n"] >= 2, res
    assert abs(res["scale"] - 1) < 0.03, res
    for d, true in zip(sorted(res["distances"]), sorted(dists)):
        assert abs(d / true - 1) < 0.03


def test_printable_page_is_a4_and_detectable():
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_marker", Path(__file__).resolve().parents[1] / "scripts" / "make_marker.py")
    mm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mm)
    page = np.array(mm.make_page(dpi=150))
    assert page.shape == (round(297 / 25.4 * 150), round(210 / 25.4 * 150))
    ds = markers.detect(page, K0)
    assert len(ds) == 1 and ds[0].ok and ds[0].marker_id == markers.MARKER_ID
    assert abs(ds[0].side_px / 150 * 25.4 - 180) < 0.5  # mm on paper
