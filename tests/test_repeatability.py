"""Repeatability pairs the same physical wall in two scans, by position."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
from repeatability import compare  # noqa: E402


def _plan(pts, name, turn=0, shift=(0.0, 0.0), ceiling=2.6):
    pts = np.asarray(pts, float)
    c, s = np.cos(turn), np.sin(turn)
    pts = pts @ np.array([[c, -s], [s, c]]).T + shift
    m = lambda v: {"value": v, "ci90": [v - 0.01, v + 0.01], "sigma": 0.005, "unit": "m"}  # noqa: E731
    walls = []
    for i in range(len(pts)):
        p, q = pts[i], pts[(i + 1) % len(pts)]
        walls.append({"id": f"room_1_w{i + 1}", "start": p.tolist(), "end": q.tolist(),
                      "length": m(float(np.linalg.norm(q - p)))})
    area = 0.5 * abs(np.dot(pts[:, 0], np.roll(pts[:, 1], 1)) - np.dot(pts[:, 1], np.roll(pts[:, 0], 1)))
    room = {"id": "room_1", "walls": walls, "floor_area": {"value": area}, "ceiling_height": {"value": ceiling}}
    return {"capture": {"id": name, "tier": "lidar"}, "rooms": [room],
            "property": {"footprint_area": {"value": area}}}


def test_walls_pair_by_position_not_by_order():
    # a 3.3 x 4.16 m room; scan a has a furniture notch on the left side, scan b on the right
    a = _plan([(0, 0), (3.3, 0), (3.3, 4.16), (0.33, 4.16), (0.33, 2.67), (0, 2.67)], "a")
    b = _plan([(0, 0), (3.3, 0), (3.3, 0.63), (2.99, 0.63), (2.99, 4.16), (0, 4.16)], "b",
              turn=np.pi, shift=(10.0, 5.0), ceiling=2.62)  # drawn turned round, elsewhere
    res = compare(a, b)
    rows = {(round(w["a"], 2), round(w["b"], 2)): w["ok"] for w in res["walls"]}
    # b is turned round: its left wall is a's right wall (no notch on either): agrees
    assert rows.get((4.16, 4.16)) is True
    # b's top is a's bottom wall, cut 0.31 m short by b's notch: the same wall, a real disagreement
    assert rows.get((3.3, 2.99)) is False
    assert len(rows) == 2  # nothing else is paired: no wall with a wall it is not
    assert res["rooms_paired"] == 1 and res["summary"]["ceiling_max_spread_m"] == 0.02
