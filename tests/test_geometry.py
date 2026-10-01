"""Geometry back end against synthetic rooms with exactly known dimensions."""
import numpy as np
import pytest
from synth import box_room, merge

from roomscan.geometry.boxfit import fit_box
from roomscan.geometry.layout import extract_layout
from roomscan.geometry.planes import ceiling_level, floor_level, manhattan_yaw
from roomscan.geometry.pointcloud import Cloud


def _rotate(cloud: Cloud, deg: float) -> Cloud:
    a = np.deg2rad(deg)
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]], np.float32)
    return Cloud(cloud.points @ R.T, cloud.normals @ R.T, cloud.weight)


def _dims(room):
    return sorted(round(w.length, 3) for w in room.walls if w.length > 0.25)


def test_floor_and_ceiling_levels():
    c = box_room(height=2.7, floor_y=-1.4)
    f = floor_level(c)
    ce = ceiling_level(c, f.value)
    assert abs(f.value + 1.4) < 0.003
    assert abs((ce.value - f.value) - 2.7) < 0.005


def test_floor_ignores_ghost_points_below_a_glossy_floor():
    c = box_room(height=2.7, floor_y=-1.4)
    rng = np.random.default_rng(1)
    n = 4000  # mirrored clutter 0.5-1.5 m below the floor, as a reflective floor produces
    ghost = np.stack([rng.uniform(0, 4, n), rng.uniform(-2.9, -1.9, n), rng.uniform(0, 3, n)], 1)
    gn = np.tile([0.0, 1.0, 0.0], (n, 1))
    c2 = Cloud(np.concatenate([c.points, ghost]).astype(np.float32),
               np.concatenate([c.normals, gn]).astype(np.float32), np.ones(len(c.points) + n, np.float32))
    assert abs(floor_level(c2).value + 1.4) < 0.005


@pytest.mark.parametrize("deg", [0, 17, 33])
def test_manhattan_direction_found_at_any_rotation(deg):
    yaw = manhattan_yaw(_rotate(box_room(), deg))
    err = np.rad2deg(yaw) % 90
    target = (-deg) % 90
    assert min(abs(err - target), 90 - abs(err - target)) < 0.3


@pytest.mark.parametrize("deg", [0, 25])
def test_single_room_dimensions_within_one_centimetre(deg):
    layout = extract_layout(_rotate(box_room(4.0, 3.0, 2.7), deg))
    assert len(layout.rooms) == 1
    room = layout.rooms[0]
    assert _dims(room) == pytest.approx([3.0, 3.0, 4.0, 4.0], abs=0.01)
    assert room.height == pytest.approx(2.7, abs=0.005)
    assert room.area == pytest.approx(12.0, abs=0.08)


def test_two_rooms_joined_by_a_door_are_separated():
    a = box_room(4.0, 3.0, 2.7, origin=(0, 0), door=(1, 1.0, 0.9, 2.05))
    b = box_room(3.0, 3.0, 2.7, origin=(4.12, 0), door=(3, 1.1, 0.9, 2.05), seed=1)
    layout = extract_layout(merge(a, b))
    assert len(layout.rooms) == 2
    areas = sorted(r.area for r in layout.rooms)
    assert areas == pytest.approx([9.0, 12.0], abs=0.15)


def test_box_fit_recovers_rectangle_from_noisy_cloud():
    c = box_room(4.0, 3.0, 2.6, noise=0.03, step=0.04)
    room = fit_box(c, 0.0, "r", noise=0.08)
    assert _dims(room) == pytest.approx([3.0, 3.0, 4.0, 4.0], abs=0.04)
    assert room.height == pytest.approx(2.6, abs=0.03)
