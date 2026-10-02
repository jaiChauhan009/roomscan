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
