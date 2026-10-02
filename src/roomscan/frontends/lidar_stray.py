"""LiDAR tier front end: Stray Scanner export (iPhone/iPad Pro).

Folder layout::

    rgb.mp4               1920x1440 video, one frame per odometry row
    depth/000000.png      256x192 uint16 depth in millimetres (.npy in older app versions)
    confidence/000000.png 256x192 uint8 ARKit confidence (0 low, 1 medium, 2 high)
    odometry.csv          timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy, ...
    camera_matrix.csv     3x3 intrinsics at RGB resolution
    imu.csv               accelerometer / gyro (unused: ARKit already fuses it)

Required: odometry.csv and depth/. Optional: confidence/ (without it depth is not
confidence-filtered), rgb.mp4 (without it there is no colour, so no damage detection),
camera_matrix.csv (needed only when odometry.csv has no fx, fy, cx, cy columns), imu.csv.
What is missing is reported in the output warnings.

Pose convention (verified on sample data): the quaternion + translation map
OpenCV camera coordinates into the ARKit world frame (+Y up).
"""
from __future__ import annotations

import warnings
from pathlib import Path

import cv2
import numpy as np

from roomscan.capture import Frame, PosedCapture
from roomscan.pipeline import InputError, visible

DEPTH_W, DEPTH_H = 256, 192
RGB_SIZE = (1920, 1440)  # Stray Scanner's colour stream; used when rgb.mp4 is missing
POSE_COLS = ["timestamp", "frame", "x", "y", "z", "qx", "qy", "qz", "qw"]
K_COLS = ["fx", "fy", "cx", "cy"]


def is_stray(path: Path) -> bool:
    return (path / "odometry.csv").is_file() and (path / "depth").is_dir()


def find_stray_root(path: Path, max_depth: int = 2) -> Path | None:
    """The Stray Scanner export folder: `path` itself, or the only export up to two folder
    levels below it (a parent folder, or the double folder Windows "Extract all" makes).
    Several exports below `path` raise InputError: one run takes one recording."""
    path = Path(path)
    if not path.is_dir():
        return None
    if is_stray(path):
        return path
    found, level = [], [path]
    for _ in range(max_depth):
        nxt = []
        for d in level:
            try:
                subs = sorted(p for p in d.iterdir() if p.is_dir() and visible(p))
            except OSError:
                continue
            for s in subs:
                (found if is_stray(s) else nxt).append(s)
        if found:
            break
        level = nxt
    if len(found) > 1:
        names = ", ".join(str(p.relative_to(path)) for p in found[:4]) + (", ..." if len(found) > 4 else "")
        raise InputError(f"{path} holds {len(found)} Stray Scanner recordings ({names}): pass one recording folder")
    return found[0] if found else None


def read_png(p: Path) -> np.ndarray | None:
    """cv2.imread that also works on non-ASCII Windows paths (cv2.imread returns None there)."""
    try:
        buf = np.fromfile(str(p), np.uint8)
    except OSError:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_UNCHANGED) if buf.size else None


def read_depth(p: Path, shape: tuple[int, int]) -> np.ndarray:
    """Depth in metres. An unreadable frame becomes all-invalid (zeros) instead of a crash."""
    try:
        d = np.load(p) if p.suffix.lower() == ".npy" else read_png(p)
    except (OSError, ValueError):
        d = None
    if d is None or d.ndim != 2:
        return np.zeros(shape, np.float32)
    return d.astype(np.float32) / 1000.0


def quat_to_R(qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    x, y, z, w = qx, qy, qz, qw
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def _camera_matrix(path: Path) -> np.ndarray | None:
    try:
        K = np.loadtxt(path / "camera_matrix.csv", delimiter=",")
        return K if K.shape == (3, 3) and np.isfinite(K).all() else None
    except (OSError, ValueError):
        return None


def read_odometry(path: Path) -> np.ndarray:
    """Return (N, 13) array: timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy.

    Columns are found by header name. Exports without per-frame intrinsics (older app
    versions) get fx, fy, cx, cy from camera_matrix.csv. Unparseable rows are dropped."""
    f = path / "odometry.csv"
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            names = [h.strip().lower() for h in fh.readline().split(",")]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # empty file, ragged last row of an interrupted export
            raw = np.genfromtxt(f, delimiter=",", skip_header=1, dtype=np.float64, invalid_raise=False)
    except (OSError, ValueError) as e:
        raise InputError(f"cannot read {f}: {e}") from e
    raw = np.atleast_2d(raw)
    idx = {n: i for i, n in enumerate(names)}
    by_name = all(c in idx for c in POSE_COLS)
    pose_i = [idx[c] for c in POSE_COLS] if by_name else list(range(9))
    if raw.size == 0 or raw.shape[1] <= max(pose_i):
        raise InputError(f"{f} has no camera poses: the recording is empty or the file is damaged")
    out = np.full((len(raw), 13), np.nan)
    out[:, :9] = raw[:, pose_i]
    k_i = [idx[c] for c in K_COLS] if all(c in idx for c in K_COLS) else ([] if by_name else [9, 10, 11, 12])
    if k_i and max(k_i) < raw.shape[1]:
        out[:, 9:] = raw[:, k_i]
    no_k = ~np.isfinite(out[:, 9:]).all(1)
    if no_k.any():
        K = _camera_matrix(path)
        if K is None:
            raise InputError(f"{path} has no camera intrinsics: odometry.csv has no fx, fy, cx, cy and "
                             f"camera_matrix.csv is missing or unreadable")
        out[no_k, 9:] = (K[0, 0], K[1, 1], K[0, 2], K[1, 2])
    out = out[np.isfinite(out[:, :9]).all(1)]
    if not len(out):
        raise InputError(f"{f} has no readable camera poses: the file is damaged")
    return out


class VideoReader:
    """Random access to video frames. Decoded frames are kept as JPEG bytes so repeated
    requests (frame selection, then detection) do not seek the video again.

    Frames are returned as stored (no rotation from container metadata): the intrinsics
    in odometry.csv refer to the stored frames.

    Every step back means decoding from the start again (see get), so a caller that knows
    which frames it will read calls prefetch() once: one forward pass decodes them all and
    holds them, exactly as decoded, until they are requested."""

    def __init__(self, path: Path, max_cache: int = 64):
        self.path = path
        self.cap = None
        self.pos = -1
        self.cache: dict[int, np.ndarray] = {}
        self.max_cache = max_cache
        self.ahead: dict[int, np.ndarray] = {}  # prefetched frames (BGR, as decoded), not yet requested

    def _open(self) -> cv2.VideoCapture:
        from roomscan.frontends.video import quiet_ffmpeg

        quiet_ffmpeg()
        cap = cv2.VideoCapture(str(self.path))
        cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
        return cap

    def _decode(self, idx: int) -> np.ndarray | None:
        """Frame idx as decoded (BGR). Decode forward to it; never seek. Stray's rgb.mp4 has
        a variable frame rate and OpenCV seeks by time (N / average fps), which on the sample
        scan landed 4-80 frames late: colour no longer matched its pose and depth. Going back
        reopens."""
        if self.cap is None or idx <= self.pos:
            if self.cap is not None:
                self.cap.release()
            self.cap, self.pos = self._open(), -1
        while self.pos < idx - 1:
            if not self.cap.grab():
                return None
            self.pos += 1
        ok, img = self.cap.read()
        if not ok:
            return None
        self.pos = idx
        return img

    def prefetch(self, indices) -> None:
        """Decode these frames in one forward pass and hold them until get() asks for them.

        A held frame is the very array a direct decode returns (decoding forward from the
        start is deterministic), and get() treats it exactly like one, so prefetching never
        changes what get() returns, only how often the video is decoded. Frames held from an
        earlier call are dropped; prefetch([]) releases them."""
        self.ahead = {}
        for idx in sorted({int(i) for i in indices if int(i) >= 0 and int(i) not in self.cache}):
            img = self._decode(idx)
            if img is None:  # end of the video or a broken frame: get() will try again itself
                break
            self.ahead[idx] = img

    def frame(self, idx: int) -> "VideoFrame":
        """The rgb_fn of frame idx."""
        return VideoFrame(self, idx)

    def get(self, idx: int) -> np.ndarray | None:
        if idx in self.cache:
            return cv2.imdecode(self.cache[idx], cv2.IMREAD_COLOR)[:, :, ::-1].copy()
        img = self.ahead.pop(idx, None)
        if img is None:
            img = self._decode(idx)
            if img is None:
                return None
        if len(self.cache) >= self.max_cache:
            self.cache.pop(next(iter(self.cache)))
        self.cache[idx] = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1]
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    def size(self) -> tuple[int, int]:
        """(width, height) of the stored frames; (0, 0) when the file cannot be decoded."""
        cap = self._open()
        ok = cap.isOpened() and cap.grab()
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return (w, h) if ok else (0, 0)


class VideoFrame:
    """rgb_fn of one LiDAR frame: its colour image from the shared VideoReader. The reader
    and index are visible so a consumer can prefetch() the frames it is about to read."""

    __slots__ = ("reader", "idx")

    def __init__(self, reader: VideoReader, idx: int):
        self.reader, self.idx = reader, idx

    def __call__(self) -> np.ndarray | None:
        return self.reader.get(self.idx)


def _frame_files(folder: Path, exts: tuple[str, ...]) -> dict[int, Path]:
    """Frame number -> file for NNNNNN.<ext> files in a folder (empty if it does not exist)."""
    out: dict[int, Path] = {}
    if folder.is_dir():
        for p in folder.iterdir():
            if p.suffix.lower() in exts and p.stem.isdigit() and visible(p):
                out.setdefault(int(p.stem), p)
    return out


def load_stray(path: Path, stride: int = 1) -> PosedCapture:
    root = find_stray_root(Path(path))
    if root is None:
        raise InputError(f"{path} is not a Stray Scanner export: expected odometry.csv and a depth/ folder")
    notes: list[str] = []
    odo = read_odometry(root)
    depth_files = _frame_files(root / "depth", (".png", ".npy"))
    if not depth_files:
        raise InputError(f"{root / 'depth'} holds no depth frames: export the scan again")
    n_depth = len(depth_files)
    first = depth_files[min(depth_files)]
    try:
        d0 = np.load(first) if first.suffix.lower() == ".npy" else read_png(first)
    except (OSError, ValueError):
        d0 = None
    if d0 is None or d0.ndim != 2:
        raise InputError(f"cannot read the depth frames in {root / 'depth'} ({first.name} is not a depth image)")
    dh, dw = d0.shape
    conf_files = _frame_files(root / "confidence", (".png",))
    if not conf_files:
        notes.append("Stray Scanner export has no confidence/ folder: depth used without ARKit confidence "
                     "filtering, walls are noisier")
    video = root / "rgb.mp4"
    reader = VideoReader(video) if video.exists() else None
    rgb_w, rgb_h = reader.size() if reader else (0, 0)
    if not (rgb_w and rgb_h):
        notes.append(f"rgb.mp4 {'cannot be decoded' if reader else 'is missing'}: no colour frames, "
                     f"so no damage detection")
        reader, (rgb_w, rgb_h) = None, RGB_SIZE
    frames: list[Frame] = []
    n_missing = 0
    for row in odo[: n_depth: stride]:
        ts, fid = row[0], int(row[1])
        dpath = depth_files.get(fid)
        if dpath is None:
            n_missing += 1
            continue
        x, y, z, qx, qy, qz, qw, fx, fy, cx, cy = row[2:13]
        T = np.eye(4)
        T[:3, :3] = quat_to_R(qx, qy, qz, qw)
        T[:3, 3] = (x, y, z)
        K_rgb = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])
        K = K_rgb.copy()
        K[0] *= dw / rgb_w
        K[1] *= dh / rgb_h
        cpath = conf_files.get(fid)
        frames.append(Frame(
            index=fid,
            timestamp=float(ts),
            T_wc=T,
            K=K,
            K_rgb=K_rgb,
            depth_fn=(lambda p=dpath: read_depth(p, (dh, dw))),
            conf_fn=(lambda p=cpath: read_png(p)) if cpath is not None else None,
            rgb_fn=reader.frame(fid) if reader else None,
            depth_sigma_rel=0.01,
        ))
    if n_missing:
        notes.append(f"{n_missing} odometry rows have no depth frame and were skipped")
    if len(frames) < 2:
        raise InputError(f"{root} has {len(frames)} usable frame(s): the recording is too short to measure "
                         f"anything (record again following docs/capture_protocol.md)")
    return PosedCapture(tier="lidar", name=root.name, frames=frames,
                        meta={"source": "stray_scanner", "root": str(root), "rgb_size": (rgb_w, rgb_h),
                              "n_frames_total": n_depth, "input_warnings": notes})
