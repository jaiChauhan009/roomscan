"""Output contract, intervals, rules and the evaluation harness."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from synth import box_room, merge

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))

from roomscan.damage.detect import Region  # noqa: E402
from roomscan.damage.pipeline import concealed_flags, scope_items  # noqa: E402
from roomscan.export.build import build_output  # noqa: E402
from roomscan.geometry.layout import extract_layout  # noqa: E402
from roomscan.schema import Output  # noqa: E402
from roomscan.uncertainty.intervals import measure  # noqa: E402


@pytest.fixture(scope="module")
def layout():
    a = box_room(4.0, 3.0, 2.7, origin=(0, 0), door=(1, 1.0, 0.9, 2.05))
    b = box_room(1.6, 2.0, 2.7, origin=(4.12, 0), door=(3, 1.1, 0.9, 2.05), seed=1)  # bathroom-sized
    return extract_layout(merge(a, b))


def _output(layout, tier="lidar"):
    info = {"id": "synthetic", "tier": tier, "source": "test", "n_frames_used": 0, "meta": {}}
    return build_output(layout, [], tier, info, {"enabled": False, "method": "none"})


def test_every_measurement_has_an_interval(layout):
    out = json.loads(_output(layout).model_dump_json())
    Output.model_validate(out)  # round-trips through the published schema

    def walk(x):
        if isinstance(x, dict):
            if set(x) >= {"value", "ci90", "sigma"}:
                if x["value"] is not None:
                    assert x["ci90"][0] <= x["value"] <= x["ci90"][1]
                    assert x["sigma"] > 0 or x.get("note") == "count"
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(out)
    assert len(out["rooms"]) == 2


def test_footprint_counts_overlapping_floor_once(layout):
    import copy

    from shapely.geometry import Polygon
    lay = copy.deepcopy(layout)
    big, small = sorted(lay.rooms, key=lambda r: r.area, reverse=True)
    toward = big.polygon.mean(0) - small.polygon.mean(0)
    small.polygon = small.polygon + 0.5 * toward / np.linalg.norm(toward)  # push it 0.5 m into the big room
    polys = [Polygon(r.polygon) for r in lay.rooms]
    overlap = polys[0].intersection(polys[1]).area
    assert overlap > 0.5
    fp = _output(lay).property.footprint_area.value
    assert fp == pytest.approx(sum(p.area for p in polys) - overlap, abs=1e-3)


def test_intervals_widen_as_sensor_data_thins():
    widths = [np.diff(measure(4.0, 0.005, tier, "wall_length").ci90)[0] for tier in ("lidar", "video", "photo")]
    assert widths[0] < widths[1] < widths[2]


def test_unobserved_ceiling_is_reported_as_missing_not_guessed():
    m = measure(None, 0, "lidar", "ceiling_height", lower_bound=2.1, note="ceiling not captured")
    assert m.value is None and m.ci90 is None and m.lower_bound == 2.1


def _region(layout, room, wall, cls="water_stain", bottom=1.0, top=1.4, area=0.2):
    return Region(cls, wall.id, room.id, "wall", 0.9, area, 0.05, 0.5, top - bottom, 0.05,
                  np.zeros(3), [1], bottom=bottom, top=top, u_range=(1.0, 1.5))


def test_concealed_rules_fire_with_their_id(layout):
    big = max(layout.rooms, key=lambda r: r.area)
    shared = max(big.walls, key=lambda w: w.end[0] + w.start[0])  # wall towards the small room
    low = _region(layout, big, shared, bottom=0.1, top=0.5)
    flags = concealed_flags([("dmg_1", low)], layout, [])
    ids = {f.rule_id for f in flags}
    assert "CD-02" in ids  # reaches the floor
    assert "CD-04" in ids  # backs onto a bathroom-sized room
    assert all(f.rule and f.triggered_by == ["dmg_1"] for f in flags)
    mid = _region(layout, big, min(big.walls, key=lambda w: w.end[0] + w.start[0]), bottom=1.0, top=1.4)
    assert concealed_flags([("dmg_1", mid)], layout, []) == []


def test_scope_items_are_keyed_to_surfaces(layout):
    big = max(layout.rooms, key=lambda r: r.area)
    wall = big.walls[0]
    regs = [("dmg_1", _region(layout, big, wall)), ("dmg_2", _region(layout, big, wall, cls="crack"))]
    items = scope_items(regs, [], layout, [], "lidar")
    assert {i.surface_id for i in items} == {wall.id}
    assert [i.code for i in items].count("PNT-WALL") == 1  # one repaint per wall
    repaint = next(i for i in items if i.code == "PNT-WALL")
    assert repaint.quantity.value == pytest.approx(wall.length * big.height, rel=0.02)


def test_evaluation_against_exact_truth(layout):
    from evaluate import evaluate

    out = json.loads(_output(layout).model_dump_json())
    gt = {"source": "synthetic", "rooms": [
        {"name": "main", "ceiling_height": 2.7, "walls": [4.0, 3.0, 4.0, 3.0]},
        {"name": "bath", "ceiling_height": 2.7, "walls": [1.6, 2.0, 1.6, 2.0]}], "footprint": 15.2}
    res = evaluate(out, gt)
    g = res["gates"]
    assert g["wall_length"]["pass"] and g["ceiling_height"]["pass"] and g["footprint"]["pass"]
    assert g["no_room_overlap"]["pass"] and g["rooms_found"]["pass"]
    gt["rooms"][0]["walls"] = [4.3, 3.0, 4.3, 3.0]  # a 30 cm error must fail the wall gate
    assert not evaluate(out, gt)["gates"]["wall_length"]["pass"]


def test_staged_damage_is_scored_by_surface_class_and_size(layout):
    from evaluate import _wall_cost, evaluate, pred_walls

    out = json.loads(_output(layout).model_dump_json())
    gt = {"source": "synthetic", "rooms": [
        {"name": "main", "ceiling_height": 2.7, "walls": [4.0, 3.0, 4.0, 3.0]},
        {"name": "bath", "ceiling_height": 2.7, "walls": [1.6, 2.0, 1.6, 2.0]}],
        "damage": [{"room": "main", "surface": "wall", "wall": 2, "class": "water_stain", "width": 0.40,
                    "height": 0.30, "left": 0.6, "bottom": 0.10},
                   {"room": "main", "surface": "wall", "wall": 3, "class": "crack", "width": 0.70, "height": 0.75,
                    "left": 0.05, "bottom": 1.30}]}
    main = max(out["rooms"], key=lambda r: r["floor_area"]["value"])
    pw = pred_walls(main)
    paired = dict(_wall_cost(gt["rooms"][0]["walls"], [w["length"]["value"] for w in pw])[1])

    def region(rid, wall_index, cls, w, h, z):
        m = lambda v: {"value": v, "ci90": [v * 0.8, v * 1.2], "sigma": v * 0.1, "unit": "m"}
        return {"id": rid, "surface_id": pw[paired[wall_index]]["id"], "room_id": main["id"], "damage_class": cls,
                "score": 0.9, "area": {**m(w * h * 0.6), "unit": "m2"}, "extent_u": m(w), "extent_v": m(h),
                "center": [0.0, 0.0, z], "evidence_frames": [1]}

    out["damage"] = [region("d1", 1, "water_stain", 0.44, 0.28, 0.25)]  # staged wall 2: found, sizes +10 % / -7 %
    res = evaluate(out, gt)
    st = {d.get("class"): d["status"] for d in res["damage"]}
    assert st == {"water_stain": "found", "crack": "missed"} and not res["gates"]["damage"]["pass"]
    assert res["gates"]["damage"]["median_extent_rel_err"] == pytest.approx(0.085, abs=0.01)
    out["damage"].append(region("d2", 2, "mold", 0.7, 0.75, 1.7))  # right wall, wrong class
    out["damage"].append(region("d3", 0, "hole", 0.1, 0.1, 1.0))  # nothing staged there: phantom
    g = evaluate(out, gt)["gates"]["damage"]
    assert (g["found"], g["wrong_class"], g["missed"], g["phantom"]) == (1, 1, 0, 1) and not g["pass"]
    out["damage"] = [region("d1", 1, "water_stain", 0.44, 0.28, 0.25), region("d2", 2, "crack", 0.65, 0.8, 1.7)]
    assert evaluate(out, gt)["gates"]["damage"]["pass"]
