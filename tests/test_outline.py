"""Room outlines follow walls, not furniture (fix-loop round 3)."""
import numpy as np
import pytest
from shapely.geometry import Polygon
from synth import furnished_room

from roomscan.geometry.layout import _fill_slots, _settle, _square_corners, _vertices, extract_layout
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


@pytest.mark.parametrize("gap", [(1.5, 2.5, 0.6, 1.9), (1.4, 2.6, 0.7, 2.1)])  # wall mirror; low-sill window
def test_a_wall_seen_below_and_above_a_gap_still_closes_the_room(gap):
    # wall seen up to 0.6-0.7 m and above the gap, nothing between: not enough low wall for
    # a wall cell, too much for a doorway; the room used to leak round its walls (+27 %)
    x0, x1, v0, v1 = gap
    c = furnished_room([])
    p = c.points
    hide = (np.abs(p[:, 2]) < 0.05) & (p[:, 0] > x0) & (p[:, 0] < x1) & (p[:, 1] > -1.4 + v0) & (p[:, 1] < -1.4 + v1)
    room = _one_room(Cloud(p[~hide], c.normals[~hide], c.weight[~hide]))
    assert Polygon(room.polygon).area == pytest.approx(12.0, abs=0.05)
    assert all(w.coverage > 0.8 for w in room.walls)


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


def _raster_segs(pts):
    """Raster-style segments (as _rectilinear_polygon returns them) of a CCW vertex list."""
    pts = [np.asarray(p, float) for p in pts]
    out = []
    for p, q in zip(pts, pts[1:] + pts[:1]):
        d = q - p
        orient = "H" if abs(d[1]) < 1e-9 else "V" if abs(d[0]) < 1e-9 else "D"
        coord = p[1] if orient == "H" else p[0] if orient == "V" else 0.0
        out.append({"orient": orient, "coord": float(coord), "len": float(np.linalg.norm(d)), "p": p, "q": q})
    return out


def test_a_short_diagonal_is_the_corner_it_cut():
    # 4 x 3 m room whose raster contour cut the (2, 0) corner region with a 0.2 m diagonal
    segs = _raster_segs([(0, 0), (3.86, 0), (4, 0.14), (4, 3), (0, 3)])
    out = _square_corners(segs)
    assert [s["orient"] for s in out] == ["H", "V", "H", "V"]
    assert Polygon(_vertices(out)).area == pytest.approx(12.0, abs=1e-6)


def test_a_short_diagonal_between_parallel_walls_is_a_step():
    # the x = 4 wall steps out to 4.2 at y = 1.5 through a 0.25 m diagonal
    segs = _raster_segs([(0, 0), (4, 0), (4, 1.4), (4.2, 1.55), (4.2, 3), (0, 3)])
    out = _square_corners(segs)
    assert [s["orient"] for s in out] == ["H", "V", "H", "V", "H", "V"]
    assert out[2]["coord"] == pytest.approx(1.475)
    assert Polygon(_vertices(out)).area == pytest.approx(4 * 1.475 + 4.2 * 1.525, abs=1e-6)


def test_a_slanted_wall_stays_slanted():
    segs = _raster_segs([(0, 0), (3.3, 0), (4, 0.7), (4, 3), (0, 3)])  # a 1 m slanted wall
    assert _square_corners(segs) is segs


def _slot_room(width, depth=0.8):
    """4 x 3 m room (counter-clockwise) with a slot `width` wide and `depth` deep cut into it
    from the x = 4 wall, centred at y = 1.5."""
    y0, y1 = 1.5 - width / 2, 1.5 + width / 2
    return [_seg("H", 0.0), _seg("V", 4.0), _seg("H", y0), _seg("V", 4.0 - depth), _seg("H", y1),
            _seg("V", 4.01), _seg("H", 3.0), _seg("V", 0.0)]


def test_a_narrow_slot_cut_into_a_room_is_filled():
    out = _fill_slots(_slot_room(0.18))
    assert [s["orient"] for s in out] == ["H", "V", "H", "V"]  # the wall either side of it is one wall again
    assert Polygon(_vertices(out)).area == pytest.approx(4.005 * 3.0, abs=0.02)


@pytest.mark.parametrize("width, depth", [(0.6, 0.8), (0.18, 2.0)])
def test_a_wide_notch_or_a_long_partition_is_not_a_slot(width, depth):
    segs = _slot_room(width, depth)
    out = _fill_slots(segs)
    assert len(out) == len(segs) and all(a is b for a, b in zip(out, segs))


def test_a_slot_that_reaches_another_room_stays():
    from roomscan.geometry.layout import PlanFrame
    frame = PlanFrame(yaw=0.0, a0=-1.0, b0=-1.0, res=0.02, shape=(300, 350))
    others = np.zeros(frame.shape, bool)
    r, c = frame.to_cell(np.array([[3.7, 1.5]]))
    others[r[0], c[0]] = True  # a cell of the neighbour inside the slot
    segs = _slot_room(0.18)
    out = _fill_slots(segs, others, frame)
    assert len(out) == len(segs) and all(a is b for a, b in zip(out, segs))
