"""RoomPlan tier (frontends/roomplan.py): capture.json from our iOS app -> the output contract.
Synthetic captures from tests/roomplan_synth.py; no ML models are loaded."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).parent))
import roomplan_synth as RS  # noqa: E402

import roomscan.pipeline as pl  # noqa: E402
from roomscan.frontends import roomplan as RP  # noqa: E402
from roomscan.schema import Output  # noqa: E402


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "CACHE_DIR", tmp_path / "cache")  # zips extract here, not in the repo's .cache


def run(tmp_path, variant="merged", frames=0, damage=False, data=None, name="cap", **kw):
    d = RS.write_folder(tmp_path / variant / name, variant, frames, data=data)
    return pl.run(d, tmp_path / variant / "out", damage=damage, **kw), tmp_path / variant / "out"


def by_label(res):
    return {r["label"]: r for r in res["rooms"]}


def sides(room):
    p = np.array(room["polygon"])
    return sorted((p.max(0) - p.min(0)).tolist(), reverse=True)


def test_merged_two_rooms_match_truth(tmp_path):
    res, out = run(tmp_path, "merged")
    Output.model_validate(res)
    assert res["capture"]["tier"] == "roomplan" and res["capture"]["source"] == "roomplan_app"
    meta = res["capture"]["meta"]
    assert meta["device"]["model"] == "iPhone16,1" and meta["app_version"] == "0.1.0" and meta["frames"] == 0
    assert [r["name"] for r in meta["rooms"]] == ["Kitchen", "Hall"]
    assert meta["rooms"][0]["objects"][0]["category"] == "table"
    rooms = by_label(res)
    assert set(rooms) == {"kitchen", "hall"}  # section label, else the room name
    for label, name in (("kitchen", "Kitchen"), ("hall", "Hall")):
        r = rooms[label]
        assert abs(r["floor_area"]["value"] - RS.TRUTH[name]["area"]) < 0.01
        assert np.allclose(sides(r), RS.TRUTH[name]["sides"], atol=0.01)
        assert abs(r["ceiling_height"]["value"] - RS.H) < 0.01 and r["ceiling_source"] == "roomplan"
        assert sorted(w["length"]["value"] for w in r["walls"]) == pytest.approx(sorted(RS.TRUTH[name]["sides"] * 2),
                                                                               abs=0.01)
        for w in r["walls"]:
            lo, hi = w["length"]["ci90"]
            assert lo < w["length"]["value"] < hi and hi - lo < 0.12  # RoomPlan prior: a few cm
    assert abs(res["property"]["footprint_area"]["value"] - 21.0) < 0.01
    k = rooms["kitchen"]
    door = next(o for o in k["openings"] if o["type"] == "door")
    win = next(o for o in k["openings"] if o["type"] == "window")
    wall_of = {w["id"]: w for w in k["walls"]}
    assert wall_of[door["wall_id"]]["start"][0] == pytest.approx(wall_of[door["wall_id"]]["end"][0])  # wall x = 4
    assert door["width"]["value"] == pytest.approx(0.9) and door["height"]["value"] == pytest.approx(2.1)
    assert door["sill_height"]["value"] == pytest.approx(0.0, abs=0.01)
    assert door["connects_to"] == rooms["hall"]["id"]
    assert win["sill_height"]["value"] == pytest.approx(1.0, abs=0.01) and win["width"]["value"] == pytest.approx(1.2)
    assert door["id"] in wall_of[door["wall_id"]]["opening_ids"]
    hall_open = rooms["hall"]["openings"][0]  # no wall_id: on the nearest wall (x = 7)
    hw = next(w for w in rooms["hall"]["walls"] if w["id"] == hall_open["wall_id"])
    assert hw["start"][0] == pytest.approx(hw["end"][0]) == pytest.approx(7.0, abs=0.01)
    adj = res["property"]["adjacency"]
    assert len(adj) == 1 and adj[0]["kind"] == "door" and set(adj[0]["rooms"]) == {k["id"], rooms["hall"]["id"]}
    for f in ("result.json", "plan.png", "plan.svg", "stages.json"):
        assert (out / f).is_file(), f
    st = json.loads((out / "stages.json").read_text())
    assert [s["name"] for s in st["stages"]] == ["load", "layout", "openings", "export"]  # damage off
    assert all(s["status"] == "done" for s in st["stages"])
    assert next(s for s in st["stages"] if s["name"] == "load")["gate"] == "warn"  # no frames


def test_without_floor_polygons_chains_walls(tmp_path):
    """Walls shuffled, one reversed, corners jittered 1.5 cm: the outline is chained from them."""
    res, _ = run(tmp_path, "no_floor")
    Output.model_validate(res)
    rooms = by_label(res)
    for label, name in (("kitchen", "Kitchen"), ("hall", "Hall")):
        r = rooms[label]
        assert abs(r["floor_area"]["value"] - RS.TRUTH[name]["area"]) < 0.1
        assert np.allclose(sides(r), RS.TRUTH[name]["sides"], atol=0.03)
        # walls in order around the outline: each wall starts where the previous one ended
        ws = r["walls"]
        for a, b in zip(ws, ws[1:] + ws[:1]):
            assert np.linalg.norm(np.subtract(a["end"], b["start"])) < 0.05
    assert not any("convex hull" in w for w in res["warnings"])
    assert any(a["kind"] == "door" for a in res["property"]["adjacency"])


def test_open_outline_falls_back_to_hull(tmp_path):
    data = RS.capture("no_floor")
    data["rooms"] = data["rooms"][:1]
    data["rooms"][0]["walls"] = data["rooms"][0]["walls"][:3]  # one wall missing
    data["rooms"][0]["doors"] = []
    res, _ = run(tmp_path, "no_floor", data=data)
    assert any("convex hull" in w for w in res["warnings"])
    assert res["rooms"][0]["floor_area"]["value"] > 5.0


def test_separate_frames_side_by_side(tmp_path):
    res, _ = run(tmp_path, "separate")
    Output.model_validate(res)
    polys = [Polygon(r["polygon"]) for r in res["rooms"]]
    assert polys[0].intersection(polys[1]).area == 0.0 and polys[0].distance(polys[1]) > 0.5
    assert [round(p.area, 2) for p in polys] == [12.0, 9.0]
    assert res["property"]["adjacency"] == []
    assert all(o["connects_to"] is None for r in res["rooms"] for o in r["openings"])
    assert any("adjacency between them is unknown" in w for w in res["warnings"])
    assert res["property"]["stitch_method"] == "roomplan_side_by_side"


def test_single_room_zip_cli_autodetects(tmp_path):
    from typer.testing import CliRunner

    from roomscan.cli import app

    z = tmp_path / "Kitchen.zip"
    z.write_bytes(RS.zip_bytes("single"))
    assert pl.detect_tier(pl.prepare_input(z)) == "roomplan"
    out = tmp_path / "out"
    r = CliRunner().invoke(app, ["run", str(z), "--out", str(out), "-q"])  # damage on: no frames, no model
    assert r.exit_code == 0, r.output
    assert "[roomplan] 1 rooms" in r.output
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    Output.model_validate(res)
    assert res["rooms"][0]["floor_area"]["value"] == pytest.approx(12.0, abs=0.01)
    assert (out / "result.xlsx").is_file() and (out / "plan.png").is_file()
    assert res["damage"] == [] and any("no damage detection" in w for w in res["warnings"])


def test_folder_of_one_room_zips_is_one_plan(tmp_path):
    """The app uploads one zip per room; zips of one session (merged, shared frame) together give
    the whole home; zips that overlap are not in one frame and go side by side."""
    d = tmp_path / "home"
    d.mkdir()
    (d / "kitchen.zip").write_bytes(RS.zip_bytes(data=RS.single_room("Kitchen", RS.KITCHEN, 0, "s1", walls_prefix="K")))
    (d / "hall.zip").write_bytes(RS.zip_bytes(data=RS.single_room("Hall", RS.HALL, 0, "s1", walls_prefix="H")))
    res = pl.run(d, tmp_path / "out", damage=False)
    assert res["capture"]["tier"] == "roomplan" and len(res["rooms"]) == 2
    assert res["property"]["footprint_area"]["value"] == pytest.approx(21.0, abs=0.01)
    assert any(a["kind"] == "shared_wall" for a in res["property"]["adjacency"])
    assert res["property"]["stitch_method"] == "roomplan_world_frame"
    # same coordinates twice (separate sessions that both start at the origin): side by side
    (d / "hall.zip").write_bytes(RS.zip_bytes(data=RS.single_room("Hall", RS.KITCHEN, 0, "s1", walls_prefix="H")))
    res = pl.run(d, tmp_path / "out2", damage=False)
    assert res["property"]["stitch_method"] == "roomplan_side_by_side"
    assert any("overlap" in w for w in res["warnings"])


@pytest.mark.parametrize("mutate, words", [
    (lambda d: d.update(format="something/2"), "expected \"roomscan.roomplan/1\""),
    (lambda d: d.update(format="roomscan.roomplan/9"), "newer"),
    (lambda d: d.update(rooms=[]), "no rooms"),
    (lambda d: d["rooms"][0]["walls"][0].update(height=-1), "walls[0].height"),
    (lambda d: d["rooms"][0]["doors"][0].update(center=[1, 2]), "doors[0].center"),
    (lambda d: d.update(units="ft"), "units"),
    (lambda d: d.update(frames=[{"file": "frames/x.jpg", "room_index": 5, "transform": [0] * 16,
                                 "intrinsics": [0] * 9, "width": 4, "height": 3}]), "room_index"),
    (lambda d: d.update(frames=[{"file": "frames/x.jpg", "room_index": 0, "transform": [1] * 16,
                                 "intrinsics": [1, 0, 0, 0, 1, 0, 0, 0, 1], "width": 4, "height": 3}]), "transform"),
])
def test_malformed_capture_json_clear_errors(tmp_path, mutate, words):
    data = RS.capture("merged")
    mutate(data)
    d = RS.write_folder(tmp_path / "bad", data=data)
    with pytest.raises(pl.InputError, match=None) as e:
        pl.run(d, tmp_path / "out", damage=False)
    assert words in str(e.value)


def test_malformed_variant_and_bad_json(tmp_path):
    d = RS.write_folder(tmp_path / "m", "malformed")
    with pytest.raises(pl.InputError, match=r"walls\[0\]\.end must be a list of 2 numbers"):
        pl.run(d, tmp_path / "out", damage=False)
    (d / "capture.json").write_text("{not json", encoding="utf-8")
    assert pl.detect_tier(d) == "roomplan"
    with pytest.raises(pl.InputError, match="not valid JSON"):
        pl.run(d, tmp_path / "out", damage=False)


def test_confidence_widens_intervals(tmp_path):
    data = RS.capture("merged")
    for w in data["rooms"][1]["walls"]:
        w["confidence"] = "low"
    res, _ = run(tmp_path, data=data)
    rooms = by_label(res)

    def width(m):
        return m["ci90"][1] - m["ci90"][0]
    k3 = next(w for w in rooms["kitchen"]["walls"] if abs(w["length"]["value"] - 3.0) < 0.01)
    h3 = next(w for w in rooms["hall"]["walls"] if abs(w["length"]["value"] - 3.0) < 0.01)
    assert width(h3["length"]) > 2 * width(k3["length"])
    assert width(rooms["hall"]["floor_area"]) > width(rooms["kitchen"]["floor_area"])
    assert width(rooms["hall"]["ceiling_height"]) > width(rooms["kitchen"]["ceiling_height"])
    assert any("low RoomPlan confidence" in w for w in res["warnings"])


def test_known_sizes_only_compared(tmp_path):
    res, _ = run(tmp_path, measurements={"rooms": {"kitchen": {"length": 4.2, "width": 3.0}}})
    assert by_label(res)["kitchen"]["floor_area"]["value"] == pytest.approx(12.0, abs=0.01)  # never rescaled
    assert any(w.startswith("known sizes (RoomPlan self-check, not applied)") for w in res["warnings"])


def test_rendered_depth_and_damage_frames(tmp_path):
    """Frame poses (ARKit, column-major) map onto the RoomPlan surfaces: a camera 2 m from the
    x = 0 wall sees it at 2 m; the damage stage gets a few frames per room."""
    d = RS.write_folder(tmp_path / "f", "merged", frames_per_room=50)
    rp = RP.load_capture(d)
    built = RP.build_layout(rp)
    ops, _ = RP.place_openings(rp, built)
    scene = RP.PlaneScene(built.layout, ops, {i["id"]: i["_heights"] for i in built.info})
    cap, room_of = RP.posed_capture(rp, scene)
    f = cap.frames[0]  # Kitchen, camera at x = 2.0, y = 1.4, looking along -x
    dep = f.depth_fn()
    assert dep.shape == (192, 256)
    v, u = np.round(f.K[1, 2]).astype(int), np.round(f.K[0, 2]).astype(int)
    assert dep[v, u] == pytest.approx(2.0, abs=0.01)
    assert (dep > 0).mean() > 0.999  # walls, floor and ceiling all round: (nearly) every pixel hits one
    assert f.rgb_fn().shape == (RS.FRAME_H, RS.FRAME_W, 3)
    # a point on the wall projects back to where the camera looks
    from roomscan.damage.detect import SurfaceIndex
    from roomscan.geometry.pointcloud import backproject
    P = backproject(dep, f.K).reshape(-1, 3) @ f.T_wc[:3, :3].T + f.T_wc[:3, 3]
    idx, table, _ = SurfaceIndex(built.layout, 0.06).assign(P, f.T_wc[:3, 3])
    kinds = {table[i][2] for i in np.unique(idx[idx >= 0])}
    assert kinds >= {"wall", "floor", "ceiling"} and (idx >= 0).mean() > 0.95
    chosen = RP.damage_frames(cap, room_of, built.layout)
    per_room = {room_of[f.index] for f in chosen}
    assert per_room == {0, 1} and len(chosen) <= 2 * RP.DAMAGE_FRAMES_PER_ROOM


def test_damage_stage_with_zero_frames(tmp_path):
    res, out = run(tmp_path, "merged", damage=True)
    assert res["damage"] == [] and res["concealed_damage_flags"] == []
    st = {s["name"]: s for s in json.loads((out / "stages.json").read_text())["stages"]}
    assert st["damage"]["status"] == "done"


def test_capture_quality_verdicts(tmp_path):
    from roomscan import capture_quality as cq

    d = RS.write_folder(tmp_path / "ok", "merged", frames_per_room=2)
    assert cq.worst(cq.check_roomplan(d)) == cq.OK
    d = RS.write_folder(tmp_path / "noframes", "merged")
    fs = cq.check_roomplan(d)
    assert cq.worst(fs) == cq.WARN and any(f.check == "frames" and f.level == cq.WARN for f in fs)
    data = RS.capture("merged", 2)
    data["rooms"][0]["walls"][0]["confidence"] = "low"
    assert cq.worst(cq.check_roomplan(RS.write_folder(tmp_path / "low", data=data))) == cq.WARN
    data = RS.capture("merged", 2)
    data["rooms"][1]["walls"] = []
    fs = cq.check_roomplan(RS.write_folder(tmp_path / "nowalls", data=data))
    assert cq.worst(fs) == cq.RETAKE and any("no walls" in f.message for f in fs)
    assert cq.worst(cq.check_roomplan(RS.write_folder(tmp_path / "bad", "malformed"))) == cq.RETAKE
