"""The printed A4 marker wired into the photo and video tiers: synthetic rooms whose depth
'model' is off by a known factor, with and without a marker pasted on a wall."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from roomscan import known_sizes as KS
from roomscan import markers
from roomscan.frontends import photos as P
from roomscan.frontends.video import DEPTH_W, clip_marker_scale
from tests.test_markers import look_at_pose, paste_marker, plane_depth

IW, IH = 960, 720  # photo working resolution
F35 = 32  # 35 mm equivalent focal length written to the end-to-end test's EXIF
F = F35 / 43.267 * float(np.hypot(IW, IH))  # ~888 px, as photos.read_photo derives it
K_IMG = np.array([[F, 0, IW / 2], [0, F, IH / 2], [0, 0, 1.0]])
# room in the standpoint frame (camera at the origin, 1.40 m above the floor, -Z into the room)
X0, X1, Z0, Z1, FLOOR, CEIL = -2.0, 2.0, -2.9, 0.10, -1.40, 1.20  # 4.0 m x 3.0 m x 2.6 m
TRUE = (4.0, 3.0)
MARKER_C = np.array([0.0, -0.2, Z0])  # on the far wall, 1.2 m above the floor
OFF = 1.6  # the depth model's error on these photos: model depth = 1.6 x true


def cam_rotation(heading_deg: float, pitch_deg: float) -> np.ndarray:
    """Camera -> world (columns: right, down, forward), heading clockwise from -Z seen from above."""
    h, p = np.radians([heading_deg, pitch_deg])
    f = np.array([np.sin(h) * np.cos(p), np.sin(p), -np.cos(h) * np.cos(p)])
    r = np.array([np.cos(h), 0.0, np.sin(h)])
    return np.column_stack([r, np.cross(f, r), f])


def box_depth(R_wc: np.ndarray, K: np.ndarray, shape) -> np.ndarray:
    """z-depth (m) of the room's inside surfaces at every pixel centre."""
    h, w = shape
    u, v = np.meshgrid(np.arange(w) + 0.0, np.arange(h) + 0.0)
    rays = np.stack([u, v, np.ones_like(u)], -1) @ np.linalg.inv(K).T  # camera, z = 1
    d = rays @ R_wc.T
    t = np.full((h, w), np.inf)
    for ax, lo, hi in ((0, X0, X1), (1, FLOOR, CEIL), (2, Z0, Z1)):
        c = d[..., ax]
        with np.errstate(divide="ignore", invalid="ignore"):
            tt = np.where(c > 1e-9, hi / c, np.where(c < -1e-9, lo / c, np.inf))
        t = np.minimum(t, tt)
    return t.astype(np.float32)


def photo(heading: float, pitch: float = -12.0, with_marker: bool = True, seed: int = 0) -> P.Photo:
    R_wc = cam_rotation(heading, pitch)
    rng = np.random.default_rng(seed)
    bg = cv2.GaussianBlur(rng.uniform(150, 230, (IH, IW)).astype(np.float32), (0, 0), 5).astype(np.uint8)
    img = cv2.cvtColor(bg, cv2.COLOR_GRAY2RGB)
    if with_marker:
        # marker axes in the world: x right (+X), y up (+Y), z towards the camera (+Z)
        R = R_wc.T
        t = R_wc.T @ MARKER_C
        if (t[2] > 0.3) and abs(np.degrees(np.arctan2(t[0], t[2]))) < 24:
            img = paste_marker(img, K_IMG, R, t)
    dh = int(round(DEPTH_W * IH / IW))
    Kd = K_IMG.copy()
    Kd[0] *= DEPTH_W / IW
    Kd[1] *= dh / IH
    depth = box_depth(R_wc, Kd, (dh, DEPTH_W)) * OFF
    return P.Photo(path=Path(f"p{heading:+.0f}.jpg"), img=img, K=K_IMG.copy(), depth=depth, Kd=Kd)


def fitted_room(name: str = "02_kitchen", with_marker: bool = True) -> P.RoomFit:
    sweep = [photo(h, with_marker=with_marker, seed=i) for i, h in enumerate((-55, -18, 18, 55))]
    rf = P.fit_room(name, sweep, [], 1.0)
    assert rf is not None
    return rf


def dims(rf: P.RoomFit) -> tuple[float, float]:
    d = KS.dims_of(rf.room)
    return d.length, d.width


def test_model_scale_is_off_without_marker():
    rf = fitted_room(with_marker=False)
    assert P.room_marker_scale(rf) is None
    before = dims(rf)
    assert before[0] / TRUE[0] == pytest.approx(OFF, rel=0.05)  # the synthetic error survives the fit
    warnings: list[str] = []
    plan, meta, rel = P.apply_scales([rf], None, warnings)
    assert plan is None and rel == {} and meta["source"] == "model" and meta["marker"] == {}
    assert dims(rf) == before  # nothing changes without a marker
    assert not any("marker" in w for w in warnings)


def test_marker_sets_room_scale():
    rf = fitted_room()
    warnings: list[str] = []
    plan, meta, rel = P.apply_scales([rf], None, warnings)
    L, W = dims(rf)
    assert L == pytest.approx(TRUE[0], rel=0.02) and W == pytest.approx(TRUE[1], rel=0.02), (L, W)
    assert meta["source"] == "marker" and meta["rooms"] == {"02_kitchen": "marker"}
    m = meta["marker"]["02_kitchen"]
    assert m["n"] == 2 and m["scale"] == pytest.approx(1 / OFF, rel=0.02)
    assert rel["02_kitchen"] == pytest.approx(max(m["spread"], markers.MARKER_REL_FLOOR))
    assert any(w.startswith("room 02_kitchen: scale from A4 marker (n=2") for w in warnings)
    assert rf.cam_height == pytest.approx(1.40, rel=0.02)


def test_typed_length_beats_marker_and_marker_beats_median():
    a, b = fitted_room("01_hall"), fitted_room("02_kitchen")
    c = fitted_room("03_bath", with_marker=False)
    # a typed length 10 % off the truth for the hall: it must win over the hall's marker
    known = KS.parse({"rooms": {"01_hall": {"length": 4.4}}})
    warnings: list[str] = []
    plan, meta, rel = P.apply_scales([a, b, c], known, warnings)
    assert dims(a)[0] == pytest.approx(4.4, rel=1e-6)
    assert dims(b)[0] == pytest.approx(TRUE[0], rel=0.02)  # its own marker, not the hall's typed scale
    # no number, no marker: the measured rooms' median, as before
    assert dims(c)[0] == pytest.approx(4.4, rel=0.03)
    assert meta["rooms"] == {"01_hall": "known_sizes", "02_kitchen": "marker", "03_bath": "known_sizes"}
    assert meta["source"] == "mixed" and set(rel) == {"02_kitchen"}
    assert "02_kitchen" not in plan.per_room
    assert any("01_hall" in w and "typed size sets the scale" in w for w in warnings)


def test_marker_interval_replaces_scale_prior():
    from roomscan.uncertainty.intervals import measure

    prior = measure(4.0, 0.02, "photo", "wall_length")
    mk = measure(4.0, 0.02, "photo", "wall_length", scale_rel=0.02)
    assert mk.sigma < prior.sigma
    area_prior, area_mk = measure(12.0, 0.1, "photo", "area", "m2"), measure(12.0, 0.1, "photo", "area", "m2",
                                                                              scale_rel=0.02)
    assert area_mk.sigma < area_prior.sigma
    assert measure(0.1, 0.01, "photo", "damage", scale_rel=0.02).sigma == measure(0.1, 0.01, "photo", "damage").sigma


def test_build_output_uses_marker_rel_per_room():
    from roomscan.export.build import build_output
    from roomscan.geometry.boxfit import layout_from_rooms
    from tests.test_known_sizes import rect_room

    rooms = [rect_room("a", 4.0, 3.0), rect_room("b", 3.0, 2.0)]
    rooms[1].polygon = rooms[1].polygon + [5.0, 0.0]
    layout = layout_from_rooms(rooms, yaw=0.0)
    info = {"id": "t", "tier": "photo", "source": "test", "n_frames_used": 1,
            "meta": {"scale": {"source": "mixed", "rooms": {"a": "marker", "b": "model"}}}}
    out = build_output(layout, [], "photo", info, {"enabled": False}, scale_rel={"a": 0.02})
    base = build_output(layout, [], "photo", info, {"enabled": False})
    ra, rb = out.rooms
    assert ra.walls[0].length.sigma < base.rooms[0].walls[0].length.sigma
    assert rb.walls[0].length.sigma == base.rooms[1].walls[0].length.sigma
    assert out.property.bbox.sigma == base.property.bbox.sigma  # not every room has a marker
    import json

    from roomscan.schema import Output
    Output.model_validate(json.loads(out.model_dump_json()))
    assert out.capture.meta["scale"]["rooms"]["a"] == "marker"


def test_video_clip_pooling():
    """Keyframes whose depth is consistent only after their per-frame scale: the pooled marker
    scale is metres per map unit, from every frame that shows the marker."""
    W, H = 640, 480
    K = np.array([[520.0, 0, 320], [0, 520.0, 240], [0, 0, 1]])
    metres_per_unit = 2.3
    rng = np.random.default_rng(5)
    imgs, deps, scales = [], [], []
    for k, (dist, yaw) in enumerate([(1.5, 0), (2.0, 20), (2.2, -15), (1.8, 30), (None, 0), (None, 0)]):
        img = cv2.cvtColor(cv2.GaussianBlur(rng.uniform(80, 200, (H, W)).astype(np.float32), (0, 0), 5)
                           .astype(np.uint8), cv2.COLOR_GRAY2RGB)
        s = float(rng.uniform(0.5, 2.0))  # this frame's depth x s = map units
        if dist is None:
            z = np.full((H // 2, W // 2), 3.0, np.float32)
        else:
            R, t = look_at_pose(dist, yaw, 5)
            img = paste_marker(img, K, R, t)
            z = cv2.resize(plane_depth(R, t, K, (H, W)), (W // 2, H // 2), interpolation=cv2.INTER_AREA)
        imgs.append(img)
        deps.append(z / (metres_per_unit * s))
        scales.append(s)
    res = clip_marker_scale(imgs, K, deps, scales)
    assert res is not None and res["n"] == 4
    assert res["scale"] == pytest.approx(metres_per_unit, rel=0.015)
    assert res["quality"] == "good"
    assert clip_marker_scale(imgs[4:], K, deps[4:], scales[4:]) is None
    assert clip_marker_scale([], K, [], []) is None


@pytest.mark.parametrize("with_marker", [True, False])
def test_photo_tier_end_to_end_records_the_scale(tmp_path, monkeypatch, with_marker):
    """run_photo_tier on a one-room folder of JPEGs, with the depth model replaced by the
    synthetic (1.6x off) depth: the marker sets the room's size, the output records it, and
    the JSON stays valid. Without the marker the run is as before (model scale)."""
    from PIL import Image

    from roomscan.schema import Output

    room = tmp_path / "cap" / "02_kitchen"
    room.mkdir(parents=True)
    phs = [photo(h, with_marker=with_marker, seed=i) for i, h in enumerate((-55, -18, 18, 55))]
    for i, ph in enumerate(phs):
        ex = Image.Exif()
        ex.get_ifd(0x8769)[0xA405] = F35
        Image.fromarray(ph.img).save(room / f"IMG_{i:04d}.jpg", exif=ex.tobytes(), quality=95)
    monkeypatch.setattr(P, "cached_depths", lambda imgs, *a, **k: [ph.depth for ph in phs])
    res = P.run_photo_tier(tmp_path / "cap", tmp_path / "out", use_cache=False, progress=False, damage=False)
    Output.model_validate(res)
    sc = res["capture"]["meta"]["scale"]
    walls = sorted(w["length"]["value"] for w in res["rooms"][0]["walls"])
    if with_marker:
        assert sc["source"] == "marker" and sc["marker"]["02_kitchen"]["n"] == 2
        assert walls[-1] == pytest.approx(TRUE[0], rel=0.03) and walls[0] == pytest.approx(TRUE[1], rel=0.03)
        assert any("scale from A4 marker (n=2" in w for w in res["warnings"])
    else:
        assert sc == {"source": "model", "rooms": {"02_kitchen": "model"}, "marker": {}}
        assert walls[-1] > 1.3 * TRUE[0]  # the depth model's error, untouched
