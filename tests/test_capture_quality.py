"""Capture-quality checks on synthetic inputs: generated images, clips and camera poses."""
from __future__ import annotations

import cv2
import numpy as np
import piexif
import pytest
from PIL import Image
from scipy.spatial.transform import Rotation

from roomscan import capture_quality as cq

RNG = np.random.default_rng(0)


def texture(h: int = 480, w: int = 640, seed: int = 0) -> np.ndarray:
    """A sharp, busy greyscale scene: random blocks plus a few lines."""
    r = np.random.default_rng(seed)
    img = cv2.resize(r.integers(0, 255, (h // 8, w // 8), dtype=np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
    for _ in range(40):
        p = tuple(int(v) for v in r.integers(0, [w, h])), tuple(int(v) for v in r.integers(0, [w, h]))
        cv2.line(img, *p, int(r.integers(0, 255)), 2)
    return img


def level(findings, check):
    return next(f.level for f in findings if f.check == check)


# ---------------------------------------------------------------- image metrics

def test_detail_ratio_separates_sharp_from_blurred():
    g = texture()
    assert cq.detail_ratio(g) > 0.2
    for sigma in (1.5, 3.0):
        assert cq.detail_ratio(cv2.GaussianBlur(g, (0, 0), sigma)) < cq.BLUR_DETAIL


def test_image_motion_measures_a_pan():
    big = texture(480, 960)
    a, b = big[:, 100:420], big[:, 132:452]          # 32 px of a 320 px frame = 0.1 widths
    assert cq.image_motion(a, b) == pytest.approx(0.1, abs=0.01)
    assert cq.image_motion(a, a) < 0.005


def test_blank_frame():
    assert cq.is_blank(np.full((240, 320), 128, np.uint8) + RNG.integers(0, 5, (240, 320)).astype(np.uint8))
    assert not cq.is_blank(texture(240, 320))


def test_overlap_inliers_high_for_overlap_low_for_unrelated():
    big = texture(480, 960, seed=1)
    a, b = big[:, :640], big[:, 200:840]
    fa, fb = cq.orb_features(a), cq.orb_features(b)
    assert cq.overlap_inliers(fa, fb) > 50
    assert cq.overlap_inliers(fa, cq.orb_features(texture(480, 640, seed=7))) < cq.LOOKBACK_MIN


# ---------------------------------------------------------------- photos

def _save(path, img, focal=True):
    kw = {"exif": piexif.dump({"Exif": {piexif.ExifIFD.FocalLength: (26, 1)}})} if focal else {}
    Image.fromarray(img).save(path, quality=92, **kw)
    return path


def _room(dir_, n, size=(1500, 2000), focal=True, seed=0, blur_last=False):
    """n photos panning across one wide scene (neighbours overlap)."""
    dir_.mkdir(parents=True)
    h, w = size
    scene = texture(h, w + 200 * n, seed=seed)
    out = []
    for i in range(n):
        img = scene[:, 200 * i: 200 * i + w]
        if blur_last and i == n - 1:
            img = cv2.GaussianBlur(img, (0, 0), 8)
        out.append(_save(dir_ / f"IMG_{i:03d}.jpg", img, focal))
    return out


def test_protocol_photos_pass(tmp_path):
    rooms = {"01_hall": _room(tmp_path / "01_hall", 4), "02_kitchen": _room(tmp_path / "02_kitchen", 5, seed=1)}
    F = cq.check_photos(rooms)
    assert {f.check: f.level for f in F if f.check != "look-back photo"} == {
        "folders": cq.OK, "photo count": cq.OK, "originals": cq.OK, "sharpness": cq.OK, "overlap": cq.OK}
    assert cq.worst(F) in (cq.OK, cq.WARN)  # the synthetic rooms have no look-back photo


def test_photos_taken_while_walking_retake(tmp_path):
    rooms = {"1_hall": _room(tmp_path / "1_hall", 14, size=(300, 400)), "2_bath": _room(tmp_path / "2_bath", 9, size=(300, 400))}
    F = cq.check_photos(rooms, look_back=False)
    f = next(f for f in F if f.check == "photo count")
    assert f.level == cq.RETAKE and "doorway" in f.advice and "1_hall: 14" in f.message


def test_chat_app_copies_retake_and_unnumbered_warn(tmp_path):
    rooms = {"hall": _room(tmp_path / "hall", 3, size=(960, 1280), focal=False),
             "kitchen": _room(tmp_path / "kitchen", 3, size=(960, 1280), focal=False, seed=2)}
    F = cq.check_photos(rooms, look_back=False)
    assert level(F, "originals") == cq.RETAKE
    assert "chat" in next(f.advice for f in F if f.check == "originals")
    assert level(F, "folders") == cq.WARN
    assert cq.worst(F) == cq.RETAKE


def test_missing_focal_only_warns(tmp_path):
    F = cq.check_photos({"01_hall": _room(tmp_path / "01_hall", 3, focal=False)})
    assert level(F, "originals") == cq.WARN


def test_blurred_photo_named(tmp_path):
    F = cq.check_photos({"01_hall": _room(tmp_path / "01_hall", 4, blur_last=True)})
    f = next(f for f in F if f.check == "sharpness")
    assert f.level == cq.WARN and "01_hall/IMG_003.jpg" in f.message and "IMG_002" not in f.message


def test_look_back_found(tmp_path):
    hall = _room(tmp_path / "01_hall", 3, seed=3)
    kitchen = _room(tmp_path / "02_kitchen", 3, seed=4)
    _save(tmp_path / "02_kitchen" / "IMG_099.jpg", np.asarray(Image.open(hall[1])))  # looking back at the hall
    F = cq.check_photos({"01_hall": hall, "02_kitchen": kitchen + [tmp_path / "02_kitchen" / "IMG_099.jpg"]})
    assert level(F, "look-back photo") == cq.OK
    F = cq.check_photos({"01_hall": hall, "02_kitchen": kitchen})
    assert level(F, "look-back photo") == cq.WARN


# ---------------------------------------------------------------- video

def _clip(path, size=(1280, 720), px_per_frame=2, seconds=3, fps=30):
    w, h = size
    n = seconds * fps
    scene = cv2.cvtColor(texture(h, w + px_per_frame * n + 8, seed=5), cv2.COLOR_GRAY2BGR)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(n):
        vw.write(np.ascontiguousarray(scene[:, px_per_frame * i: px_per_frame * i + w]))
    vw.release()
    return path


def test_slow_original_clip_ok(tmp_path):
    F = cq.check_video(_clip(tmp_path / "slow.mp4", px_per_frame=2))  # 60 px/s = 0.05 widths/s
    assert level(F, "resolution") == cq.OK and level(F, "motion") == cq.OK
    assert level(F, "blank frames") == cq.OK and level(F, "sharpness") == cq.OK
    assert level(F, "duration") == cq.WARN  # 3 s


def test_fast_pan_retake(tmp_path):
    F = cq.check_video(_clip(tmp_path / "fast.mp4", px_per_frame=40))  # 1200 px/s = 0.94 widths/s
    f = next(f for f in F if f.check == "motion")
    assert f.level == cq.RETAKE and "slow down" in f.advice


def test_chat_app_clip_and_blank_frames():
    info = {"duration": 60.0, "width": 464, "height": 832, "fps": 60.0}
    m = {"speed": np.full(40, 0.2), "blank": np.r_[np.ones(20, bool), np.zeros(20, bool)], "sharp": np.full(40, 0.4)}
    F = cq.video_findings(info, m)
    assert level(F, "resolution") == cq.RETAKE
    assert level(F, "blank frames") == cq.RETAKE
    assert level(F, "motion") == cq.OK


# ---------------------------------------------------------------- LiDAR

LEVEL = Rotation.from_quat([1, 0, 0, 0])  # OpenCV camera (z forward, y down) looking along world -Z, +Y up


def _quats(pitch_deg, yaw_deg=None):
    p = np.atleast_1d(pitch_deg).astype(float)
    y = np.zeros_like(p) if yaw_deg is None else np.asarray(yaw_deg, float)
    r = [Rotation.from_euler("y", yi, degrees=True) * Rotation.from_euler("x", pi, degrees=True) * LEVEL
         for pi, yi in zip(p, y)]
    return np.array([q.as_quat() for q in r])  # qx, qy, qz, qw


def test_camera_pitch_from_stray_quaternions():
    assert cq.camera_pitch_deg(_quats([0, 25, -40, 60], [0, 90, 200, 10])) == pytest.approx([0, 25, -40, 60], abs=1e-6)


def _scan(pitch, n=600, fps=30.0, jump_at=None):
    t = np.arange(n) / fps
    xyz = np.c_[np.linspace(0, 3, n), np.full(n, 1.4), np.zeros(n)]
    if jump_at is not None:
        xyz[jump_at:, 0] += 0.5
    return t, xyz, _quats(pitch, np.linspace(0, 360, n))


def test_scan_that_looks_up_is_ok():
    n = 900  # 30 s
    pitch = np.where((np.arange(n) > 300) & (np.arange(n) < 360), 50.0, -10.0)
    F = cq.lidar_findings(*_scan(pitch, n=n))
    assert level(F, "ceiling") == cq.OK and level(F, "tracking") == cq.OK and cq.worst(F) == cq.OK


def test_scan_that_never_looks_up_retake():
    F = cq.lidar_findings(*_scan(np.full(600, -15.0)))
    f = next(f for f in F if f.check == "ceiling")
    assert f.level == cq.RETAKE and "ceiling" in f.advice


def test_tracking_jump_and_missing_rgb_warn():
    F = cq.lidar_findings(*_scan(np.full(600, 40.0), jump_at=200), has_rgb=False)
    assert level(F, "tracking") == cq.WARN and level(F, "colour video") == cq.WARN
    assert cq.pose_jumps(_scan(np.zeros(600), jump_at=200)[1]) == 1


def test_fast_turning_warns():
    t = np.arange(300) / 30.0  # 10 s, 3 full turns = 108 deg/s
    q = _quats(np.zeros(300), np.linspace(0, 1080, 300))
    F = cq.lidar_findings(t, np.zeros((300, 3)), q)
    assert level(F, "turn speed") == cq.WARN
    assert np.median(cq.turn_rate_deg_s(t, q)) == pytest.approx(108, rel=0.02)


def test_check_lidar_reads_odometry(tmp_path):
    t, xyz, q = _scan(np.full(900, 35.0), n=900)
    rows = ["timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy"]
    rows += [f"{ti}, {i:06d}, {x}, {y}, {z}, {a}, {b}, {c}, {d}, 1500, 1500, 960, 720"
             for i, (ti, (x, y, z), (a, b, c, d)) in enumerate(zip(t, xyz, q))]
    (tmp_path / "odometry.csv").write_text("\n".join(rows) + "\n")
    F = cq.check_lidar(tmp_path)
    assert level(F, "ceiling") == cq.OK and level(F, "colour video") == cq.WARN


def test_report_lines():
    F = [cq.Finding("a", cq.OK, "fine", "never shown"), cq.Finding("b", cq.RETAKE, "bad", "do this")]
    L = cq.report_lines(F)
    assert "RETAKE" in L[0] and L[2].strip() == "RETAKE b: bad -> do this" and "never shown" not in L[1]
    assert cq.worst([]) == cq.OK
