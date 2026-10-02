"""Wall-length intervals widen where a wall that ends the wall has no plane evidence, and stay
tight where both end walls are seen along their whole length (src/roomscan/export/build.py)."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest
from synth import box_room

from roomscan.export import build as B
from roomscan.export.build import build_output, end_sigma, evidence_deficit
from roomscan.geometry.layout import extract_layout


@pytest.fixture(scope="module")
def room_layout():
    return extract_layout(box_room(4.0, 3.0, 2.7, origin=(0, 0)))


def _with_coverage(layout, cov: dict[int, float], full: float = 0.95):
    """The layout's single room with every wall fully covered except those in `cov` (index -> coverage)."""
    lay = copy.deepcopy(layout)
    room = lay.rooms[0]
    for i, w in enumerate(room.walls):
        w.coverage = cov.get(i, full)
        w.sigma = 0.003
    return lay


def _room(layout, tier="lidar"):
    info = {"id": "synthetic", "tier": tier, "source": "test", "n_frames_used": 0, "meta": {}}
    return build_output(layout, [], tier, info, {"enabled": False, "method": "none"}).rooms[0]


def _half_width(m) -> float:
    return (m.ci90[1] - m.ci90[0]) / 2


def test_deficit_is_zero_at_full_coverage_and_one_without_a_plane():
    w = lambda c: SimpleNamespace(coverage=c, sigma=0.003)  # noqa: E731
    assert evidence_deficit(w(0.0)) == 1.0
    assert evidence_deficit(w(B.FULL_COVERAGE)) == 0.0
    assert evidence_deficit(w(0.97)) == 0.0
    assert evidence_deficit(w(B.FULL_COVERAGE / 2)) == pytest.approx(0.5)
    covs = np.linspace(0, 1, 21)
    d = [evidence_deficit(w(c)) for c in covs]
    assert all(a >= b for a, b in zip(d, d[1:]))  # less evidence, never a smaller deficit


def test_end_sigma_adds_the_evidence_term_for_lidar_only():
    full = SimpleNamespace(coverage=0.95, sigma=0.004)
    none = SimpleNamespace(coverage=0.0, sigma=0.03)
    assert end_sigma(full, "lidar") == pytest.approx(0.004)  # fully covered: the plane fit alone
    assert end_sigma(none, "lidar") == pytest.approx(np.hypot(0.03, B.EVIDENCE_K["lidar"]))
    for tier in ("video", "photo"):  # their scales were fitted on plane sigmas: unchanged
        assert end_sigma(none, tier) == pytest.approx(0.03)


def test_wall_lengths_stay_tight_between_fully_covered_walls(room_layout):
    room = _room(_with_coverage(room_layout, {}))
    assert len(room.walls) == 4
    for w in room.walls:
        assert _half_width(w.length) < 0.08  # a few cm, as before the evidence term


def test_only_the_walls_an_unevidenced_wall_ends_get_wide(room_layout):
    tight = _room(_with_coverage(room_layout, {}))
    weak = _room(_with_coverage(room_layout, {1: 0.0}))  # wall 1: no plane found
    hw0 = [_half_width(w.length) for w in tight.walls]
    hw1 = [_half_width(w.length) for w in weak.walls]
    for i in (0, 2):  # the two walls wall 1 ends
        assert hw1[i] > 10 * hw0[i] and hw1[i] > 0.5
        assert weak.walls[i].length.ci90[0] >= 0.0
    for i in (1, 3):  # walls whose ends are both fully covered
        assert hw1[i] == pytest.approx(hw0[i])
    # partial evidence widens less than none
    part = _room(_with_coverage(room_layout, {1: 0.6}))
    assert hw0[0] < _half_width(part.walls[0].length) < hw1[0]


def test_floor_area_and_perimeter_keep_plane_sigmas(room_layout):
    tight = _room(_with_coverage(room_layout, {}))
    weak = _room(_with_coverage(room_layout, {1: 0.0}))
    assert weak.floor_area.sigma == pytest.approx(tight.floor_area.sigma)
    assert weak.perimeter.sigma == pytest.approx(tight.perimeter.sigma)


def test_video_and_photo_lengths_do_not_change_with_coverage(room_layout):
    for tier in ("video", "photo"):
        tight = _room(_with_coverage(room_layout, {}), tier)
        weak = _room(_with_coverage(room_layout, {1: 0.0}), tier)
        assert [w.length.sigma for w in weak.walls] == [w.length.sigma for w in tight.walls]
