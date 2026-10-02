"""Video tier speed-ups: threaded decoding; depth on a subset of keyframes (opt-in)."""
import cv2
import numpy as np

from roomscan.frontends import video as V
from roomscan.ml import depth as D


def _kfs(n, dt=0.25, shared_drop=0):
    """n keyframes dt apart; each shares all but `shared_drop` tracks with the one before."""
    out, ids = [], np.arange(400)
    for k in range(n):
        out.append({"t": k * dt, "ids": ids.copy()})
        ids = np.concatenate([ids[shared_drop:], np.arange(ids[-1] + 1, ids[-1] + 1 + shared_drop)])
    return out


def test_depth_keyframes_budget():
    use = V.depth_keyframes(_kfs(41, dt=0.25), min_dt=0.5)
    assert use[0] and use[-1]
    assert 19 <= sum(use) <= 23  # ~one per 0.5 s instead of four per second
    gaps = np.diff(np.flatnonzero(use))
    assert gaps.max() <= 2


def test_depth_keyframes_keep_track_continuity():
    # fast turn: each keyframe loses 150 of 400 tracks, so after two keyframes fewer than
    # 160 are shared with the last depth keyframe: depth is needed on every keyframe
    use = V.depth_keyframes(_kfs(12, dt=0.05, shared_drop=150), min_dt=10.0, min_shared=160)
    assert all(use)
    assert V.depth_keyframes([]) == []
    assert all(V.depth_keyframes(_kfs(9)))  # default: depth on every keyframe


def _scene(n_kf=12):
    """A textured wall 3 m in front of a camera sliding sideways; tracks are its points."""
    rng = np.random.default_rng(0)
    K = np.array([[500.0, 0, 320], [0, 500.0, 240], [0, 0, 1]])
    X = np.stack([rng.uniform(-4, 4, 3000), rng.uniform(-2, 2, 3000), np.full(3000, 3.0)], 1)
    kfs, truth = [], []
    for k in range(n_kf):
        c = np.array([0.08 * k, 0.01 * k, 0.0])
        Pc = X - c
        uv = Pc[:, :2] / Pc[:, 2:] * 500 + [320, 240]
        ok = (uv[:, 0] > 5) & (uv[:, 0] < 635) & (uv[:, 1] > 5) & (uv[:, 1] < 475)
        kfs.append({"ids": np.flatnonzero(ok).astype(np.int64), "pts": uv[ok].astype(np.float32),
                    "img": np.zeros((480, 640, 3), np.uint8), "t": 0.25 * k, "frame": k})
        truth.append(c)
    return kfs, K, truth


def test_solve_poses_with_depth_on_some_keyframes():
    kfs, K, truth = _scene()
    full = [np.full((60, 80), 3.0, np.float32) for _ in kfs]
    some = [d if i % 3 == 0 or i == len(kfs) - 1 else None for i, d in enumerate(full)]
    kept_f, poses_f, _, _ = V.solve_poses(kfs, full, K)
    kept_s, poses_s, scales_s, st = V.solve_poses(kfs, some, K)
    assert kept_f == list(range(len(kfs)))
    assert kept_s == [i for i, d in enumerate(some) if d is not None]
    assert st["segments"] == 1
    for i, T in zip(kept_s, poses_s):
        assert np.allclose(T[:3, 3], truth[i], atol=0.01)
        assert np.allclose(T, poses_f[kept_f.index(i)], atol=0.01)
    assert np.allclose(scales_s, 1.0, atol=0.01)


def test_cached_depths_only_wanted(monkeypatch, tmp_path):
    calls = []

    def fake(imgs, progress=False, px=None):
        calls.append(len(imgs))
        return [np.full((4, 4), float(im[0, 0, 0]), np.float32) for im in imgs]

    monkeypatch.setattr(D, "predict_depth", fake)
    monkeypatch.setattr(V, "CACHE", tmp_path)
    imgs = [np.full((8, 8, 3), i, np.uint8) for i in range(5)]
    want = [True, False, True, False, True]
    out = V.cached_depths(imgs, "k", True, False, want)
    assert calls == [3]
    assert [None if d is None else float(d[0, 0]) for d in out] == [0.0, None, 2.0, None, 4.0]
    again = V.cached_depths(imgs, "k", True, False, want)  # from the cache
    assert calls == [3]
    assert [None if d is None else float(d[0, 0]) for d in again] == [0.0, None, 2.0, None, 4.0]


def test_track_video_threaded_decoder(tmp_path):
    """Frames arrive in order with their times; every frame is counted; tracks follow motion."""
    rng = np.random.default_rng(1)
    tex = cv2.GaussianBlur((rng.random((600, 1400)) * 255).astype(np.uint8), (0, 0), 1.5)
    tex = cv2.cvtColor(tex, cv2.COLOR_GRAY2BGR)
    f = tmp_path / "clip.mp4"
    vw = cv2.VideoWriter(str(f), cv2.VideoWriter_fourcc(*"mp4v"), 25, (640, 480))
    for k in range(100):
        vw.write(np.ascontiguousarray(tex[60:540, 6 * k:6 * k + 640]))
    vw.release()
    tr = V.track_video(f)
    assert tr["n_frames"] == 100
    assert abs(tr["duration_s"] - 99 / 25) < 0.1
    kfs = tr["kfs"]
    assert len(kfs) >= 3
    assert all(a["frame"] < b["frame"] for a, b in zip(kfs, kfs[1:]))
    assert all(a["t"] < b["t"] for a, b in zip(kfs, kfs[1:]))
    # a track seen in two consecutive keyframes moved left by ~6 px per frame
    a, b = kfs[0], kfs[1]
    common, ia, ib = np.intersect1d(a["ids"], b["ids"], return_indices=True)
    assert len(common) > 100
    dx = np.median(b["pts"][ib, 0] - a["pts"][ia, 0])
    assert abs(dx + 6 * (b["frame"] - a["frame"])) < 2


def test_predict_depth_input_size(monkeypatch):
    """The tier's depth input size reaches the model loader; the default keeps one model."""
    import torch
    seen = []

    class _Proc:
        def __call__(self, images, return_tensors):
            return {"x": torch.zeros(len(images))}

    class _Model:
        def __call__(self, x):
            return type("O", (), {"predicted_depth": torch.ones(len(x), 2, 2)})()

    def loader(px=D.DEPTH_PX):
        seen.append(px)
        return _Proc(), _Model()

    monkeypatch.setattr(D, "_model", loader)
    imgs = [np.zeros((8, 8, 3), np.uint8)]
    D.predict_depth(imgs)
    D.predict_depth(imgs, px=D.DEPTH_PX)
    D.predict_depth(imgs, px=252)
    assert seen == [D.DEPTH_PX, D.DEPTH_PX, 252]
