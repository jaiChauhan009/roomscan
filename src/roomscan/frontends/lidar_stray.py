"""LiDAR tier front end: Stray Scanner export (iPhone/iPad Pro).

Folder layout::

    rgb.mp4               1920x1440 video, one frame per odometry row
    depth/000000.png      256x192 uint16 depth in millimetres
    confidence/000000.png 256x192 uint8 ARKit confidence (0 low, 1 medium, 2 high)
    odometry.csv          timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy, ...
    camera_matrix.csv     3x3 intrinsics at RGB resolution
    imu.csv               accelerometer / gyro (unused: ARKit already fuses it)

Pose convention (verified on sample data): the quaternion + translation map
OpenCV camera coordinates into the ARKit world frame (+Y up).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from roomscan.capture import Frame, PosedCapture

DEPTH_W, DEPTH_H = 256, 192


def is_stray(path: Path) -> bool:
    return (path / "odometry.csv").exists() and (path / "depth").is_dir()


def find_stray_root(path: Path) -> Path | None:
    """Accept either the scan folder itself or a parent with a single scan inside."""
    if is_stray(path):
        return path
    subs = [p for p in path.iterdir() if p.is_dir() and is_stray(p)] if path.is_dir() else []
    return subs[0] if len(subs) == 1 else None


def quat_to_R(qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    x, y, z, w = qx, qy, qz, qw
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def read_odometry(path: Path) -> np.ndarray:
    """Return (N, 13) array: timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy."""
    return np.genfromtxt(path / "odometry.csv", delimiter=",", skip_header=1, usecols=range(13))


class VideoReader:
    """Random access to video frames with a small sequential cache."""

    def __init__(self, path: Path):
        self.path = path
        self.cap = None
        self.pos = -1

    def get(self, idx: int) -> np.ndarray | None:
        if self.cap is None:
            self.cap = cv2.VideoCapture(str(self.path))
        if idx != self.pos + 1:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, img = self.cap.read()
        self.pos = idx
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if ok else None

    def size(self) -> tuple[int, int]:
        cap = cv2.VideoCapture(str(self.path))
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return w, h


def load_stray(path: Path, stride: int = 1) -> PosedCapture:
    root = find_stray_root(Path(path))
    if root is None:
        raise FileNotFoundError(f"{path} is not a Stray Scanner capture")
    odo = read_odometry(root)
    video = root / "rgb.mp4"
    reader = VideoReader(video) if video.exists() else None
    rgb_w, rgb_h = reader.size() if reader else (1920, 1440)
    n_depth = len(list((root / "depth").glob("*.png")))
    frames: list[Frame] = []
    for row in odo[: n_depth: stride]:
        ts, fid = row[0], int(row[1])
        x, y, z, qx, qy, qz, qw, fx, fy, cx, cy = row[2:13]
        T = np.eye(4)
        T[:3, :3] = quat_to_R(qx, qy, qz, qw)
        T[:3, 3] = (x, y, z)
        K_rgb = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])
        s = DEPTH_W / rgb_w
        K = K_rgb.copy()
        K[:2] *= s
        dpath = root / "depth" / f"{fid:06d}.png"
        cpath = root / "confidence" / f"{fid:06d}.png"
        frames.append(Frame(
            index=fid,
            timestamp=float(ts),
            T_wc=T,
            K=K,
            K_rgb=K_rgb,
            depth_fn=(lambda p=dpath: cv2.imread(str(p), cv2.IMREAD_UNCHANGED).astype(np.float32) / 1000.0),
            conf_fn=(lambda p=cpath: cv2.imread(str(p), cv2.IMREAD_UNCHANGED)) if cpath.exists() else None,
            rgb_fn=(lambda i=fid: reader.get(i)) if reader else None,
            depth_sigma_rel=0.01,
        ))
    return PosedCapture(tier="lidar", name=root.name, frames=frames,
                        meta={"source": "stray_scanner", "root": str(root), "rgb_size": (rgb_w, rgb_h),
                              "n_frames_total": n_depth})
