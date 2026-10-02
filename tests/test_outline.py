"""Room outlines follow walls, not furniture (fix-loop round 3)."""
import numpy as np
import pytest
from shapely.geometry import Polygon
from synth import furnished_room

from roomscan.geometry.layout import _settle, _vertices, extract_layout
from roomscan.geometry.pointcloud import Cloud

WARDROBE = (1.4, 2.6, 0.0, 0.6, 1.8)  # 1.2 m wide, 0.6 m deep, 1.8 m tall, against the z = 0 wall


def _one_room(cloud):
    lay = extract_layout(cloud)
    assert len(lay.rooms) == 1
    return lay.rooms[0]


@pytest.mark.parametrize("boxes", [
    [WARDROBE],
    [(0.3, 1.3, 0.0, 0.6, 2.0), (2.4, 3.6, 0.0, 0.55, 1.9)],  # two wardrobes: steps that snap to nothing
    [WARDROBE, (0.5, 1.5, 2.65, 3.0, 2.0)],  # and a bookcase on the opposite wall
])
def test_furniture_against_a_wall_seen_above_it_is_not_a_wall(boxes):
    room = _one_room(furnished_room(boxes))
    assert sorted(round(w.length, 2) for w in room.walls) == pytest.approx([3.0, 3.0, 4.0, 4.0], abs=0.02)
    assert Polygon(room.polygon).area == pytest.approx(12.0, abs=0.05)
    assert all(w.coverage > 0.8 for w in room.walls)


def test_without_the_ceiling_in_view_the_furniture_front_stays():
    # nothing seen above 1.6 m (a capture that never looked up): no wall above the wardrobe,
    # so its front is the best evidence of where the room ends, as before
    c = furnished_room([WARDROBE])
    low = c.points[:, 1] < -1.4 + 1.6
    room = _one_room(Cloud(c.points[low], c.normals[low], c.weight[low]))
    assert Polygon(room.polygon).area == pytest.approx(12.0 - 1.2 * 0.6, abs=0.05)
    assert min(w.length for w in room.walls) > 0.5  # a notch, but no zero-length steps


def _seg(orient, coord, c0=None, coverage=0.9):
    return {"orient": orient, "coord": coord, "_c0": coord if c0 is None else c0, "len": 1.0, "p": np.zeros(2),
            "q": np.zeros(2), "sigma": 0.005, "support": 100, "coverage": coverage, "spread": 0.003}


def test_a_snap_that_crosses_the_outline_is_undone_alone():
    # 4 x 3 m room with a 1.2 x 0.6 m notch in the y = 0 wall, counter-clockwise
    segs = [_seg("H", 0.0), _seg("V", 1.4), _seg("H", 0.6), _seg("V", 2.6), _seg("H", 0.0), _seg("V", 4.0),
            _seg("H", 3.0), _seg("V", 0.0)]
    assert Polygon(_vertices(segs)).is_valid
    segs[1] = _seg("V", 2.8, c0=1.4, coverage=0.3)  # snapped past the notch's other side: the outline crosses
    segs[5] = _seg("V", 4.02, c0=4.0)  # a good snap elsewhere
    assert not Polygon(_vertices(segs)).is_valid
    out = _settle(segs)
    assert out is not None and Polygon(_vertices(out)).is_valid
    assert [s["coord"] for s in out] == [0.0, 1.4, 0.6, 2.6, 0.0, 4.02, 3.0, 0.0]
    assert out[1]["coverage"] == 0.0 and out[5]["coverage"] == 0.9  # the undone wall has no evidence


def test_a_notch_whose_front_snapped_onto_the_wall_becomes_one_wall():
    segs = [_seg("H", 0.0), _seg("V", 1.4), _seg("H", 0.6), _seg("V", 2.6), _seg("H", 0.0), _seg("V", 4.0),
            _seg("H", 3.0), _seg("V", 0.0)]
    segs[2] = _seg("H", 0.01, c0=0.6)  # the wardrobe front snapped back onto the wall behind it
    out = _settle(segs)
    assert len(out) == 4  # wall, step, front, step, wall -> one wall
    assert Polygon(_vertices(out)).area == pytest.approx(12.0, abs=0.03)
