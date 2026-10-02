"""The synthetic staged-damage benchmark paints what it scores, where the wall is bare."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))

from roomscan.geometry.layout import PlanFrame  # noqa: E402
from roomscan.geometry.pointcloud import Cloud  # noqa: E402
from synth_damage import alpha, bare_spot, crack, stain  # noqa: E402


def _painted_box(rg):
    u, v = np.meshgrid(np.arange(-0.6, 0.6, 0.002), np.arange(-0.6, 0.6, 0.002))
    a = alpha({**rg, "u0": 0.0, "v0": 0.0}, u.ravel(), v.ravel())
    on = a > 0.1
    return u.ravel()[on].min(), u.ravel()[on].max(), v.ravel()[on].min(), v.ravel()[on].max()


def test_scored_extent_is_the_painted_extent():
    for rg in (stain(), crack()):
        x0, x1, y0, y1 = _painted_box(rg)
        b = rg["box"]
        assert abs((x1 - x0) - (b[1] - b[0])) < 0.01 and abs((y1 - y0) - (b[3] - b[2])) < 0.01, rg["class"]
    b = stain()["box"]
    assert 0.45 < b[1] - b[0] < 0.6 and 0.45 < b[3] - b[2] < 0.6
    b = crack()["box"]
    assert 0.55 < b[1] - b[0] < 0.62  # 0.7 m at 35 deg, plus the width


def test_paint_goes_on_bare_wall_away_from_openings():
    # a 4 m wall along plan x (floor at -1.4), hidden from 0.6 to 1.9 m between x = 1.5 and 2.5
    x, y = np.meshgrid(np.arange(0.01, 4.0, 0.02), np.arange(-1.39, 1.3, 0.02))
    x, y = x.ravel(), y.ravel()
    seen = ~((x > 1.5) & (x < 2.5) & (y > -0.8) & (y < 0.5))
    p = np.stack([x[seen], y[seen], np.zeros(seen.sum())], 1).astype(np.float32)
    cloud = Cloud(p, np.tile(np.float32([0, 0, 1]), (len(p), 1)), np.ones(len(p), np.float32))
    layout = SimpleNamespace(frame=PlanFrame(yaw=0.0, res=0.02))
    wall = SimpleNamespace(id="w1", start=np.array([0.0, 0.0]), end=np.array([4.0, 0.0]), length=4.0,
                           inward=np.array([0.0, 1.0]))
    room = SimpleNamespace(floor=SimpleNamespace(value=-1.4))
    box = stain()["box"]  # at 1.3 m above the floor: inside the hidden band
    u0, frac = bare_spot(cloud, layout, wall, room, [], box, 1.3)
    assert frac == 1.0
    assert u0 + box[1] <= 1.53 or u0 + box[0] >= 2.47  # beside the hidden part (4 cm cells), not over it
    door = SimpleNamespace(wall_id="w1", u0=u0 - 0.4, u1=u0 + 0.4)  # an opening where it went
    u1, frac1 = bare_spot(cloud, layout, wall, room, [door], box, 1.3)
    assert frac1 == 1.0
    assert u1 + box[0] >= door.u1 + 0.3 - 1e-9 or u1 + box[1] <= door.u0 - 0.3 + 1e-9


def _wall_camera(depth):
    """A camera 2 m in front of the wall of _flat_wall (plan b = 0, facing it), with this
    depth image (192 x 256, f = 200 px)."""
    R = np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]]).T  # columns: camera x, y (down), z (to the wall)
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, [2.0, 0.0, 2.0]
    K = np.array([[200.0, 0, 128], [0, 200.0, 96], [0, 0, 1]])
    return SimpleNamespace(T_wc=T, K=K, depth_fn=lambda: depth)


def _flat_wall():
    layout = SimpleNamespace(frame=PlanFrame(yaw=0.0, res=0.02))
    wall = SimpleNamespace(id="w1", start=np.array([0.0, 0.0]), end=np.array([4.0, 0.0]), length=4.0,
                           inward=np.array([0.0, 1.0]))
    room = SimpleNamespace(floor=SimpleNamespace(value=-1.4))
    return layout, wall, room


def test_depth_check_rejects_see_through_and_curtain_folds():
    from synth_damage import DEPTH_MAX_STD, DEPTH_ON_PLANE, depth_check
    layout, wall, room = _flat_wall()
    box = crack()["box"]
    u = np.arange(256)
    bare = np.full((192, 256), 2.0, np.float32)
    window = bare.copy()
    window[:, (u > 108) & (u < 148)] = 3.5  # glass / the room beyond, x = 1.8 - 2.2 m
    folds = (bare + 0.03 * np.sin(u / 4.0)[None]).astype(np.float32)  # a curtain hung at the wall
    on, spread, n = depth_check([_wall_camera(bare)], layout, wall, room, box, 2.0, 1.4)
    assert n == 1 and on >= DEPTH_ON_PLANE and spread <= DEPTH_MAX_STD
    on, _, _ = depth_check([_wall_camera(window)], layout, wall, room, box, 2.0, 1.4)
    assert on < DEPTH_ON_PLANE
    on, spread, _ = depth_check([_wall_camera(folds)], layout, wall, room, box, 2.0, 1.4)
    assert spread > DEPTH_MAX_STD


def test_bare_spot_skips_a_window_in_the_wall_plane():
    # the fused cloud covers the whole wall (glass in the wall plane returns points too);
    # the frame's depth shows the window between x = 1.8 and 2.2
    layout, wall, room = _flat_wall()
    x, y = np.meshgrid(np.arange(0.01, 4.0, 0.02), np.arange(-1.39, 1.3, 0.02))
    p = np.stack([x.ravel(), y.ravel(), np.zeros(x.size)], 1).astype(np.float32)
    cloud = Cloud(p, np.tile(np.float32([0, 0, 1]), (len(p), 1)), np.ones(len(p), np.float32))
    box = crack()["box"]
    u = np.arange(256)
    window = np.full((192, 256), 2.0, np.float32)
    window[:, (u > 108) & (u < 148)] = 3.5
    u0, _ = bare_spot(cloud, layout, wall, room, [], box, 1.4)
    assert u0 + box[0] < 2.2 and u0 + box[1] > 1.8  # without frames: over the window (mid-wall)
    u0, frac = bare_spot(cloud, layout, wall, room, [], box, 1.4, frames=[_wall_camera(window)])
    assert frac == 1.0
    assert u0 + box[1] <= 1.8 + 1e-6 or u0 + box[0] >= 2.2 - 1e-6
