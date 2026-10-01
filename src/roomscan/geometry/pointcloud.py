"""Depth back-projection, per-pixel normals and voxel fusion."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from roomscan.capture import Frame, PosedCapture


@dataclass
class Cloud:
    points: np.ndarray  # (N,3) world
    normals: np.ndarray  # (N,3) world, unit, oriented towards the camera
    weight: np.ndarray  # (N,) number of fused observations

    def __len__(self) -> int:
        return len(self.points)

    def subset(self, mask: np.ndarray) -> "Cloud":
        return Cloud(self.points[mask], self.normals[mask], self.weight[mask])


def backproject(depth: np.ndarray, K: np.ndarray) -> np.ndarray:
    """HxW depth -> HxWx3 camera-frame points (OpenCV convention)."""
    h, w = depth.shape
    u, v = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    z = depth
    x = (u - K[0, 2]) * z / K[0, 0]
    y = (v - K[1, 2]) * z / K[1, 1]
    return np.stack([x, y, z], axis=-1)


def image_normals(P: np.ndarray, step: int = 2) -> np.ndarray:
    """Normals from central differences on the (lightly smoothed) point image."""
    Ps = cv2.blur(P, (3, 3))
    dx = np.zeros_like(Ps)
    dy = np.zeros_like(Ps)
    dx[:, step:-step] = Ps[:, 2 * step:] - Ps[:, :-2 * step]
    dy[step:-step] = Ps[2 * step:] - Ps[:-2 * step]
    n = np.cross(dx, dy)
    norm = np.linalg.norm(n, axis=-1, keepdims=True)
    n = n / np.maximum(norm, 1e-9)
    # orient towards camera (camera at origin): n . P < 0
    flip = (n * P).sum(-1) > 0
    n[flip] *= -1
    n[norm[..., 0] < 1e-9] = 0
    return n


def frame_points(frame: Frame, pixel_stride: int = 2, max_depth: float = 5.0,
                 min_conf: int = 2) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """World points, world normals and camera-frame depth for one frame."""
    d = frame.depth_fn()
    P = backproject(d, frame.K)
    N = image_normals(P)
    valid = (d > 0.1) & (d < max_depth) & (np.abs(N).sum(-1) > 0)
    if frame.conf_fn is not None:
        c = frame.conf_fn()
        if c is not None and c.shape == d.shape:
            valid &= c >= min_conf
    # reject depth discontinuities (mixed pixels at edges)
    gx = np.abs(cv2.Sobel(d, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(d, cv2.CV_32F, 0, 1, ksize=3))
    valid &= (gx + gy) < 0.25 * np.maximum(d, 0.3)
    sel = np.zeros_like(valid)
    sel[::pixel_stride, ::pixel_stride] = True
    valid &= sel
    R, t = frame.T_wc[:3, :3], frame.T_wc[:3, 3]
    p = P[valid] @ R.T + t
    n = N[valid] @ R.T
    return p.astype(np.float32), n.astype(np.float32), d[valid]


def voxel_fuse(points: np.ndarray, normals: np.ndarray, weight: np.ndarray, voxel: float) -> Cloud:
    keys = np.floor(points / voxel).astype(np.int64)
    keys -= keys.min(0)
    dims = keys.max(0) + 1
    lin = (keys[:, 0] * dims[1] + keys[:, 1]) * dims[2] + keys[:, 2]
    uniq, inv = np.unique(lin, return_inverse=True)
    w = np.bincount(inv, weights=weight)
    out_p = np.stack([np.bincount(inv, weights=points[:, i] * weight) for i in range(3)], 1) / w[:, None]
    out_n = np.stack([np.bincount(inv, weights=normals[:, i] * weight) for i in range(3)], 1)
    out_n /= np.maximum(np.linalg.norm(out_n, axis=1, keepdims=True), 1e-9)
    return Cloud(out_p.astype(np.float32), out_n.astype(np.float32), w.astype(np.float32))


def fuse_capture(cap: PosedCapture, voxel: float = 0.02, pixel_stride: int = 2,
                 max_depth: float = 5.0, chunk: int = 200, progress: bool = True) -> Cloud:
    """Fuse all frames into a voxel-downsampled cloud with normals."""
    from tqdm import tqdm

    acc: Cloud | None = None
    buf_p, buf_n, buf_w = [], [], []
    it = tqdm(cap.frames, desc="fuse", disable=not progress)
    for i, fr in enumerate(it):
        p, n, d = frame_points(fr, pixel_stride, max_depth)
        buf_p.append(p)
        buf_n.append(n)
        buf_w.append(1.0 / (1.0 + d ** 2))  # near observations are more precise
        if len(buf_p) >= chunk or i == len(cap.frames) - 1:
            P, N, W = np.concatenate(buf_p), np.concatenate(buf_n), np.concatenate(buf_w)
            if acc is not None:
                P = np.concatenate([acc.points, P])
                N = np.concatenate([acc.normals * acc.weight[:, None], N * W[:, None]])
                N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
                W = np.concatenate([acc.weight, W])
            acc = voxel_fuse(P, N, W, voxel)
            buf_p, buf_n, buf_w = [], [], []
    return acc
