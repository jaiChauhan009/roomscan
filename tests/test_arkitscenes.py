"""ARKitScenes benchmark tooling on synthetic data (no binaries, no network).

  * scripts/arkitscenes_to_stray.py: a box room rendered in ARKitScenes' own conventions (Z-up world,
    world-to-camera angle-axis trajectory at 10 Hz, 60 Hz frames named by timestamp, .pincam files)
    is converted and read back by roomscan's unchanged Stray loader: poses, depth, colour order, frame
    matching, sky direction and the pose check
  * scripts/fetch_arkitscenes.py: zip members and PLY layout read through byte ranges
  * bench/make_laser_truth.py: a synthetic Faro scan of a box room (with a wardrobe and a curtain in
    front of walls) gives the room's width, depth and ceiling height; an open side is rejected
"""
from __future__ import annotations

import importlib.util
import io
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from roomscan.frontends import lidar_stray

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str):
    spec = importlib.util.spec_from_file_location(Path(rel).stem, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


conv = _load("scripts/arkitscenes_to_stray.py")
fetch = _load("scripts/fetch_arkitscenes.py")
truth = _load("bench/make_laser_truth.py")

ROOM = np.array([4.0, 3.0, 2.5])  # box room [0, 4] x [0, 3] x [0, 2.5], Z up (ARKitScenes world)
K = (212.0, 212.0, 128.0, 96.0)
VID = "41234567"


def _cam(yaw: float, pitch: float, roll: float = 0.0) -> np.ndarray:
    """Camera-to-world rotation, OpenCV camera axes, Z-up world."""
    f = np.array([np.cos(yaw) * np.cos(pitch), np.sin(yaw) * np.cos(pitch), np.sin(pitch)])
    right = np.cross(f, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(f, right)
    R = np.stack([right, down, f], 1)
    return R @ Rotation.from_rotvec([0, 0, roll]).as_matrix()


def _box_hit(c: np.ndarray, d: np.ndarray, lo, hi) -> np.ndarray:
    """Distance along rays d (N, 3) from c (inside) to the walls of the box [lo, hi]."""
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(d > 0, (np.asarray(hi) - c) / d, (np.asarray(lo) - c) / d)
    return np.nanmin(np.where(t > 0, t, np.inf), axis=1)


def _render(R: np.ndarray, c: np.ndarray) -> np.ndarray:
    fx, fy, cx, cy = K
    v, u = np.mgrid[0:192, 0:256]
    ray = np.stack([(u - cx) / fx, (v - cy) / fy, np.ones_like(u, float)], -1).reshape(-1, 3)
    t = _box_hit(c, ray @ R.T, [0, 0, 0], ROOM)  # camera ray has z = 1, so t is the depth
    return np.round(t * 1000).astype(np.uint16).reshape(192, 256)


def _bits(i: int) -> np.ndarray:
    """A colour frame carrying its pose index as six black/white blocks (survives video coding)."""
    img = np.zeros((192, 256, 3), np.uint8)
    for b in range(6):
        if (i >> b) & 1:
            img[:, 40 * b:40 * (b + 1)] = 255
    return img


def _unbits(img: np.ndarray) -> int:
    return sum(1 << b for b in range(6) if img[:, 40 * b + 8:40 * (b + 1) - 8].mean() > 128)


def _world_of_room() -> np.ndarray:
    """ARKit's world is not aligned with the room: room frame -> ARKitScenes world (Z up)."""
    M = np.eye(4)
    M[:3, :3] = Rotation.from_euler("z", 25, degrees=True).as_matrix()
    M[:3, 3] = (0.7, -1.2, 0.0)
    return M


def make_arkitscenes(raw: Path, n: int = 36, jump_before: int = 0) -> list[np.ndarray]:
    """Raw ARKitScenes sequence of the box room; returns the true camera-to-world poses (Z-up world).
    Poses before `jump_before` are stored 0.9 m too low, as after ARKit's start-up jump on a real capture."""
    for sub in ("lowres_depth", "confidence", "lowres_wide", "lowres_wide_intrinsics"):
        (raw / sub).mkdir(parents=True)
    lines, poses = [], []
    for i in range(n):
        R = _cam(2 * np.pi * i / 12, [-0.5, 0.0, 0.45][i % 3])
        c = np.array([2.0 + 0.3 * np.sin(i / 6), 1.5 + 0.3 * np.cos(i / 5), 1.4 + 0.1 * np.sin(i / 4)])  # walking pace
        T = np.eye(4)
        T[:3, :3], T[:3, 3] = R, c  # camera in the room frame
        Tw = _world_of_room() @ T
        poses.append(Tw)
        Ts = Tw.copy()
        if i < jump_before:
            Ts[2, 3] -= 0.9
        E = np.linalg.inv(Ts)  # the file stores world-to-camera
        t = 100.0 + 0.1 * i
        lines.append(f"{t:.8f} " + " ".join(f"{x:.12f}" for x in Rotation.from_matrix(E[:3, :3]).as_rotvec())
                     + " " + " ".join(f"{x:.12f}" for x in E[:3, 3]))
        if i == 7:
            continue  # a pose without frames: skipped
        ft = t + (0.002 if i % 2 else -0.002)  # frames are a few ms off their pose
        stem = f"{VID}_{ft:.3f}"
        cv2.imencode(".png", _render(R, c))[1].tofile(str(raw / "lowres_depth" / f"{stem}.png"))
        cv2.imencode(".png", np.full((192, 256), 2, np.uint8))[1].tofile(str(raw / "confidence" / f"{stem}.png"))
        cv2.imencode(".png", _bits(i))[1].tofile(str(raw / "lowres_wide" / f"{stem}.png"))
        kt = ft + (0.001 if i == 4 else 0.0)  # intrinsics may be named 1 ms off
        (raw / "lowres_wide_intrinsics" / f"{VID}_{kt:.3f}.pincam").write_text("256 192 %g %g %g %g" % K)
        # a 60 Hz frame between poses, with no pose of its own: not used
        mid = f"{VID}_{t + 0.05:.3f}"
        for sub in ("lowres_depth", "confidence", "lowres_wide"):
            cv2.imencode(".png", np.zeros((192, 256), np.uint8))[1].tofile(str(raw / sub / f"{mid}.png"))
    (raw / "lowres_wide.traj").write_text("\n".join(lines) + "\n")
    return poses


def test_converted_sequence_reads_back_exactly(tmp_path):
    raw = tmp_path / "raw"
    poses = make_arkitscenes(raw)
    rep = conv.convert(raw, tmp_path / "out")
    assert rep["n_frames"] == 35 and rep["rgb_frames"] == 35  # 36 poses, one without frames
    assert rep["pose_check"]["used_is_sharpest"]
    assert rep["sky_direction_from_poses"] == "Up"
    cap = lidar_stray.load_stray(tmp_path / "out")  # roomscan's loader, unchanged
    assert len(cap.frames) == 35 and cap.meta["input_warnings"] == []
    W = np.eye(4)
    W[:3, :3] = conv.W_ZUP_TO_YUP
    true_idx = [i for i in range(36) if i != 7]
    for f, i in zip(cap.frames, true_idx):
        assert np.allclose(f.T_wc, W @ poses[i], atol=1e-6)
        assert np.allclose(f.K, [[K[0], 0, K[2]], [0, K[1], K[3]], [0, 0, 1]])
    # depth is copied byte for byte and lands on the room's surfaces: floor at y = 0, ceiling at 2.5
    src = sorted((raw / "lowres_depth").glob("*.png"), key=lambda p: conv.stem_time(p.stem))[0]
    assert (tmp_path / "out" / "depth" / "000000.png").read_bytes() == src.read_bytes()
    pts = []
    for f in cap.frames:
        d = f.depth_fn()
        v, u = np.mgrid[0:192:8, 0:256:8]
        z = d[v, u].ravel()
        X = np.stack([(u.ravel() - K[2]) * z / K[0], (v.ravel() - K[3]) * z / K[1], z], 1)
        pts.append(X @ f.T_wc[:3, :3].T + f.T_wc[:3, 3])
    P = np.concatenate(pts)
    assert P[:, 1].min() == pytest.approx(0.0, abs=0.002) and P[:, 1].max() == pytest.approx(2.5, abs=0.002)
    room = (P - (W @ _world_of_room())[:3, 3]) @ (W @ _world_of_room())[:3, :3]  # back to the room frame
    assert np.allclose(room.min(0), [0, 0, 0], atol=0.002) and np.allclose(room.max(0), ROOM, atol=0.002)
    # colour frames are in pose order: frame k shows the index of its own pose
    for k in (0, 6, 7, 20, 34, 3):
        img = cap.frames[k].rgb_fn()
        assert img.shape == (192, 256, 3) and _unbits(img) == true_idx[k]


def test_poses_before_a_tracking_jump_are_dropped(tmp_path):
    raw = tmp_path / "raw"
    poses = make_arkitscenes(raw, jump_before=4)
    rep = conv.convert(raw, tmp_path / "out")
    assert [j["t"] for j in rep["tracking_jumps"]] == [0.3] and rep["poses_kept"] == [4, 35]
    assert rep["n_frames"] == 31 and rep["pose_check"]["used_is_sharpest"]  # 32 poses, one without frames
    cap = lidar_stray.load_stray(tmp_path / "out")
    W = np.eye(4)
    W[:3, :3] = conv.W_ZUP_TO_YUP
    assert np.allclose(cap.frames[0].T_wc, W @ poses[4], atol=1e-6)


def test_pose_readings_and_sky_direction():
    E = np.linalg.inv(np.array([[0, 0, 1, 2.0], [-1, 0, 0, 1.0], [0, -1, 0, 1.4], [0, 0, 0, 1]]))
    T = conv.stray_pose(E[None])[0]
    assert np.allclose(T[:3, 3], [2.0, 1.4, -1.0])  # Z-up position (2, 1, 1.4) -> Y up
    assert np.allclose(T[:3, :3] @ [0, 0, 1.0], [1, 0, 0])  # still looks along world x
    assert np.allclose(T[:3, :3] @ [0, -1.0, 0], [0, 1, 0])  # image up is world up
    sky = {}
    for name, roll in (("Up", 0.0), ("Left", np.pi / 2), ("Down", np.pi), ("Right", -np.pi / 2)):
        Tz = np.eye(4)
        Tz[:3, :3] = _cam(0.3, -0.2, roll)
        sky[name] = conv.sky_from_poses(conv.stray_pose(np.linalg.inv(Tz)[None]))
    assert sky == {"Up": "Up", "Left": "Left", "Down": "Down", "Right": "Right"}


def test_frame_matching_rules():
    frames = {f"{VID}_{t:.3f}": t for t in np.round(np.arange(10.0, 11.0, 1 / 60), 3)}
    m = fetch.match_frames(np.array([10.0, 10.1, 10.2041, 10.3091]), frames)
    assert sorted(m.values()) == [10.0, 10.1, 10.2041]  # 10.3091 is 7 ms from the nearest frame


class _Remote:
    """Serves byte ranges of an in-memory file in place of the CDN."""

    def __init__(self, data: bytes):
        self.data = data

    def __call__(self, url, rng=None, method="GET", tries=8):
        if method == "HEAD":
            return {"Content-Length": str(len(self.data))}, b""
        lo, hi = rng if rng else (0, len(self.data) - 1)
        return {}, self.data[lo:hi + 1]


def test_zip_members_and_ply_layout_through_ranges(monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("lowres_depth/a_1.000.png", b"stored" * 50, compress_type=zipfile.ZIP_STORED)
        z.writestr("lowres_depth/a_1.100.png", bytes(range(256)) * 40, compress_type=zipfile.ZIP_DEFLATED)
    monkeypatch.setattr(fetch, "request", _Remote(buf.getvalue()))
    rf, members = fetch.zip_index("https://example/x.zip")
    assert sorted(members) == ["a_1.000.png", "a_1.100.png"]
    assert fetch.zip_member(rf, members["a_1.000.png"]) == b"stored" * 50
    assert fetch.zip_member(rf, members["a_1.100.png"]) == bytes(range(256)) * 40
    dt = np.dtype([("x", "<f8"), ("y", "<f8"), ("z", "<f8"), ("red", "u1"), ("green", "u1"), ("blue", "u1"),
                   ("alpha", "u1"), ("quality", "<f8"), ("radius", "<f8")])
    head = (b"ply\nformat binary_little_endian 1.0\ncomment VCGLIB generated\nelement vertex 3\n"
            + b"".join(f"property {t} {n}\n".encode() for n, t in [("x", "double"), ("y", "double"), ("z", "double"),
                       ("red", "uchar"), ("green", "uchar"), ("blue", "uchar"), ("alpha", "uchar"),
                       ("quality", "double"), ("radius", "double")])
            + b"element face 0\nproperty list uchar int vertex_indices\nend_header\n")
    v = np.zeros(3, dt)
    v["z"] = [1.0, 2.0, 3.0]
    monkeypatch.setattr(fetch, "request", _Remote(head + v.tobytes()))
    hdr, n, got = fetch.ply_layout("https://example/s.ply")
    assert (hdr, n, got.itemsize) == (len(head), 3, 44) and got.names == dt.names


# ---------------------------------------------------------------- laser truth on a synthetic scan

def _slabs(c, d, lo, hi):
    with np.errstate(divide="ignore", invalid="ignore"):
        t1, t2 = (np.asarray(lo) - c) / d, (np.asarray(hi) - c) / d
    return np.nanmax(np.minimum(t1, t2), axis=1), np.nanmin(np.maximum(t1, t2), axis=1)


def _cast(c, d, boxes, annex=None):
    """Range along rays d from c inside the room LROOM, stopped by solid boxes; with an annex, rays
    leaving through the wall y = 0 between the annex's x limits go on to the annex's far walls."""
    t = _box_hit(c, d, [0, 0, 0], LROOM)
    if annex is not None:
        p = c + t[:, None] * d
        through = (np.abs(p[:, 1]) < 1e-6) & (p[:, 0] > annex[0][0]) & (p[:, 0] < annex[1][0])
        t = np.where(through, _slabs(c, d, *annex)[1], t)
    for lo, hi in boxes:
        tn, tf = _slabs(c, d, lo, hi)
        t = np.where((tn < tf) & (tn > 0) & (tn < t), tn, t)
    return t


LROOM = np.array([4.2, 3.1, 2.6])


def make_laser(folder: Path, annex: bool = False, n_slices: int = 240, yaw_deg: float = 20.0) -> None:
    """A Faro-like scan: columns of 4400 points (elevation -60..+90 deg) at evenly spaced azimuths,
    1 mm range noise, in a frame turned by yaw_deg and lifted 30 m (like ARKitScenes' registered frames).
    With `annex`, a 2 m wide opening in the wall y = 0 leads to a 2.5 m deep annex under the same ceiling."""
    folder.mkdir(parents=True)
    rng = np.random.default_rng(2)
    c = np.array([1.6, 1.3, 1.25])
    wardrobe = ([0.0, 1.0, 0.0], [0.6, 2.2, 2.0])  # against wall x = 0, 2.0 m tall
    curtain = ([2.5, 3.0, 0.2], [3.8, 3.04, 2.45])  # 6-10 cm in front of wall y = 3.1
    pts = []
    for k in range(n_slices):
        az = 2 * np.pi * k / n_slices
        el = np.radians(np.linspace(-60, 90, 4400))
        d = np.stack([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)], 1)
        t = _cast(c, d, [wardrobe, curtain], ([1.0, -2.5, 0.0], [3.0, 0.0, 2.6]) if annex else None)
        p = c + (t + rng.normal(0, 0.001, len(t)))[:, None] * d
        pts.append(p)
    P = np.concatenate(pts)
    a = np.radians(yaw_deg)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    off = np.array([5.0, -2.0, 30.0])
    np.savez(folder / "100001.npz", xyz=P @ Rz.T + off, rgb=np.zeros((len(P), 3), np.uint8),
             starts=np.arange(n_slices), slice_points=4400, n_points=n_slices * 4400, file_bytes=0)
    cw = Rz @ c + off
    (folder / "100001_pose.txt").write_text("1,0,0,0.0\n0,1,0,0.0\n0,0,1,0.0\n" + ",".join(f"{x:.8f}" for x in cw) + ",1.0\n")


def test_laser_truth_of_a_box_room(tmp_path):
    make_laser(tmp_path / "v" / "laser")
    res = truth.measure(tmp_path / "v")
    assert res["accepted"], res["reasons"]
    t = truth.truth_numbers(res)
    w, d = sorted(t["walls"][:2])
    assert w == pytest.approx(3.1, abs=0.003) and d == pytest.approx(4.2, abs=0.003)
    assert t["ceiling"] == pytest.approx(2.6, abs=0.003)
    assert t["area"] == pytest.approx(3.1 * 4.2, abs=0.03)


def test_laser_truth_rejects_a_room_open_to_an_annex(tmp_path):
    make_laser(tmp_path / "v" / "laser", annex=True)
    res = truth.measure(tmp_path / "v")
    assert not res["accepted"] and res["reasons"]
