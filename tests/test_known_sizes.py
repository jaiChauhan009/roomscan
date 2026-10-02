"""Known sizes (measurements.yaml): parsing, matching, scale, intervals; synthetic inputs only."""
import numpy as np
import pytest

from roomscan import known_sizes as KS
from roomscan.geometry.layout import Room, Wall
from roomscan.geometry.planes import Level


def rect_room(name: str, a: float, b: float, h: float | None = 2.5, src: str = "ceiling_plane") -> Room:
    verts = np.array([[0, 0], [a, 0], [a, b], [0, b]], float)
    walls = []
    for i in range(4):
        p, q = verts[i], verts[(i + 1) % 4]
        d = (q - p) / np.linalg.norm(q - p)
        walls.append(Wall(id=f"{name}_w{i + 1}", orient="H" if i % 2 == 0 else "V", start=p.copy(), end=q.copy(),
                          inward=np.array([-d[1], d[0]]), sigma=0.02, support=500, coverage=0.95, spread=0.01))
    return Room(id=name, label_id=0, polygon=verts, walls=walls, floor=Level(0.0, 0.01, 500),
                ceiling=None if h is None else Level(h, 0.01, 500), ceiling_source=src if h else "none")


def test_parse_forms_and_errors(tmp_path):
    ks = KS.parse({"rooms": {"01_hall": {"length": 3.1, "width": "4.20 m"}, "02_kitchen": {"height": 2.6},
                             "any": {"height": 2.5}}, "scale_reference": {"length": 1.0}})
    assert ks.named["01_hall"].length == 4.2 and ks.named["01_hall"].width == 3.1  # longer side is the length
    assert ks.named["02_kitchen"].given() == {"height": 2.6} and ks.any.height == 2.5
    lst = KS.parse({"rooms": [{"length": 4.0}, {"name": "hall", "height": 2.4}]})
    assert len(lst.listed) == 1 and "hall" in lst.named
    for bad in ({"rooms": {"a": {"length": 420}}}, {"rooms": {"a": {"lenght": 4.2}}},
                {"rooms": {"a": {"height": "tall"}}}, {"rooms": {"a": {}}}, ["x"]):
        with pytest.raises(KS.MeasurementsError):
            KS.parse(bad)
    assert KS.parse(None).empty()


def test_find_file(tmp_path):
    cap = tmp_path / "photos"
    cap.mkdir()
    assert KS.find_file(cap) is None
    (cap / "measurements.yaml").write_text("rooms: {any: {height: 2.5}}\n")
    assert KS.find_file(cap) == cap / "measurements.yaml"
    vid = tmp_path / "walk.mp4"
    vid.write_bytes(b"")
    (tmp_path / "walk.mp4.measurements.yaml").write_text("rooms: [{length: 4}]\n")
    assert KS.find_file(vid).name == "walk.mp4.measurements.yaml"
    assert KS.load(KS.find_file(vid)).listed[0].length == 4.0


def test_room_mode_per_room_scale_and_median_for_unmeasured():
    # fitted rooms are 0.8x the truth (hall) and 1.25x (kitchen); the bath has no number
    rooms = [KS.dims_of(rect_room("01_hall", 4.0 * 0.8, 3.0 * 0.8, 2.5 * 0.8)),
             KS.dims_of(rect_room("02_kitchen", 3.0 * 1.25, 2.0 * 1.25, 2.5 * 1.25)),
             KS.dims_of(rect_room("03_bath", 2.0, 1.5, 2.4))]
    ks = KS.parse({"rooms": {"hall": {"length": 4.0}, "02_kitchen": {"height": 2.5, "width": 2.0}}})
    p = KS.plan(ks, rooms, "room")
    assert p.per_room["01_hall"] == pytest.approx(1.25)
    assert p.per_room["02_kitchen"] == pytest.approx(0.8)
    assert p.per_room["03_bath"] == pytest.approx(np.median([1.25, 0.8]))
    assert p.spread == pytest.approx(0.0, abs=1e-9)
    assert any("03_bath" in w for w in p.warnings)


def test_disagreeing_numbers_warn_and_widen():
    rooms = [KS.dims_of(rect_room("r", 4.0, 3.0, 2.5))]
    p = KS.plan(KS.parse({"rooms": {"r": {"length": 4.0, "width": 3.6, "height": 2.5}}}), rooms, "room")
    assert p.per_room["r"] == pytest.approx(1.0)
    assert p.spread == pytest.approx(0.2)
    assert any("disagree" in w for w in p.warnings)


def test_global_mode_median_over_rooms_and_best_match():
    # video: one map, scale 0.5 everywhere; numbers come as an unnamed list
    rooms = [KS.dims_of(rect_room("room_1", 2.0, 1.0, None)), KS.dims_of(rect_room("room_2", 1.5, 1.4, None))]
    ks = KS.parse({"rooms": [{"length": 3.0, "width": 2.8}, {"length": 4.0, "width": 2.0}]})
    p = KS.plan(ks, rooms, "global")
    assert p.assigned["room_1"].length == 4.0 and p.assigned["room_2"].length == 3.0  # by aspect ratio
    assert p.scale == pytest.approx(2.0)
    assert not p.per_room
    # ceiling given for 'any' room but no ceiling plane: noted, not used
    p2 = KS.plan(KS.parse({"rooms": {"any": {"height": 2.5}}}), rooms, "global")
    assert p2 is not None and not p2.ratios and any("no ceiling plane" in w for w in p2.warnings)


def test_lidar_check_reports_differences():
    lines = KS.lidar_check(KS.parse({"rooms": {"room_1": {"length": 4.1, "height": 2.5}}}),
                           [KS.dims_of(rect_room("room_1", 4.0, 3.0, 2.5))])
    assert any("length given 4.10 m, scanned 4.00 m (-2.4 %)" in s for s in lines)
    assert any("height given 2.50 m" in s for s in lines)


def _output(room: Room, tier: str = "photo"):
    from roomscan.export.build import build_output
    from roomscan.geometry.boxfit import layout_from_rooms

    layout = layout_from_rooms([room], yaw=0.0)
    info = {"id": "t", "tier": tier, "source": "test", "n_frames_used": 1, "meta": {}}
    return build_output(layout, [], tier, info, {"enabled": False}), layout


def test_finish_tightens_measured_and_records_scale():
    room = rect_room("01_hall", 4.0 * 0.7, 3.0 * 0.7, 2.6 * 0.7)
    known = KS.parse({"rooms": {"01_hall": {"height": 2.6}}})
    p = KS.plan(known, [KS.dims_of(room)], "room")
    KS.scale_room(room, p.per_room["01_hall"])
    assert room.height == pytest.approx(2.6) and room.walls[0].length == pytest.approx(4.0)
    out, layout = _output(room)
    before = out.rooms[0].walls[0].length.model_copy()
    KS.finish(out, p, layout.rooms, known, mode="room")
    ch = out.rooms[0].ceiling_height
    assert ch.value == pytest.approx(2.6) and ch.ci90[1] - ch.ci90[0] == pytest.approx(0.02, abs=1e-3)
    assert out.rooms[0].walls[0].height.value == pytest.approx(2.6)
    assert out.rooms[0].walls[0].length == before  # one number: nothing to widen with, nothing measured
    assert out.capture.meta["known_size_scale"] == pytest.approx(1 / 0.7, rel=1e-3)
    assert any("known sizes" in w for w in out.warnings)


def test_finish_measured_wall_and_widened_rest():
    room = rect_room("r", 4.0, 3.0, 2.5)
    known = KS.parse({"rooms": {"r": {"length": 4.0, "width": 3.3}}})  # 10 % apart
    p = KS.plan(known, [KS.dims_of(room)], "room")
    KS.scale_room(room, p.per_room["r"])
    out, layout = _output(room)
    area0 = out.rooms[0].floor_area.sigma
    KS.finish(out, p, layout.rooms, known, mode="room")
    walls = sorted(out.rooms[0].walls, key=lambda w: -w.length.value)
    long_w = walls[0].length
    assert long_w.note and "length given" in long_w.note
    assert long_w.ci90[0] <= 4.0 <= long_w.ci90[1] and long_w.ci90[1] - long_w.ci90[0] < 0.5
    assert out.rooms[0].floor_area.sigma > area0  # the disagreement widens the rest


def test_scale_capture_and_room_fit():
    from roomscan.capture import Frame, PosedCapture
    from roomscan.frontends.photos import RoomFit, scale_room_fit
    from roomscan.frontends.video import scale_capture

    T = np.eye(4)
    T[:3, 3] = (1.0, 1.5, -2.0)
    cap = PosedCapture("video", "c", [Frame(0, 0.0, T, np.eye(3), lambda: np.full((2, 2), 3.0, np.float32))],
                       meta={"metric_scale": 0.5})
    c2 = scale_capture(cap, 2.0)
    assert np.allclose(c2.frames[0].T_wc[:3, 3], (2.0, 3.0, -4.0)) and np.allclose(c2.frames[0].depth_fn(), 6.0)
    assert np.allclose(cap.frames[0].T_wc[:3, 3], (1.0, 1.5, -2.0))  # original untouched

    class Ph:
        scale = 1.0
        T_wc = T.copy()
    rf = RoomFit(name="r", sweep=[Ph()], look_back=None, room=rect_room("r", 4.0, 3.0, 2.5), cam_height=1.4)
    scale_room_fit(rf, 0.5)
    assert rf.room.walls[1].length == pytest.approx(1.5) and rf.room.height == pytest.approx(1.25)
    assert rf.cam_height == pytest.approx(0.7) and rf.sweep[0].scale == 0.5
    assert np.allclose(rf.sweep[0].T_wc[:3, 3], (0.5, 0.75, -1.0))


def test_pipeline_loader(tmp_path):
    from roomscan.pipeline import InputError, load_known_sizes

    assert load_known_sizes(None, tmp_path) is None
    assert load_known_sizes({"rooms": {"any": {"height": 2.5}}}).any.height == 2.5
    (tmp_path / "measurements.yaml").write_text("rooms:\n  01_hall: {length: 4.2, width: 3.1}\n")
    assert load_known_sizes(None, tmp_path).named["01_hall"].width == 3.1
    (tmp_path / "measurements.yaml").write_text("rooms:\n  01_hall: {length: 420}\n")
    with pytest.raises(InputError):
        load_known_sizes(None, tmp_path)
    with pytest.raises(InputError):
        load_known_sizes(tmp_path / "nope.yaml")
