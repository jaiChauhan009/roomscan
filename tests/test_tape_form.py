"""The tape form lists a plan's walls and turns a filled copy into ground-truth YAML."""
import importlib.util
import sys
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location("tape_form", Path(__file__).parent.parent / "scripts" / "tape_form.py")
tape_form = importlib.util.module_from_spec(spec)
sys.modules["tape_form"] = tape_form
spec.loader.exec_module(tape_form)


def _res():
    m = lambda v: {"value": v}  # noqa: E731
    pts = [(0, 0), (4, 0), (4, 3), (0, 3)]
    walls = [{"id": f"room_1_w{i + 1}", "start": list(p), "end": list(pts[(i + 1) % 4]),
              "length": m(4.0 if i % 2 == 0 else 3.0)} for i, p in enumerate(pts)]
    walls.append({"id": "room_1_w5", "start": [0, 0], "end": [0.2, 0], "length": m(0.2)})  # too short to tape
    return {"capture": {"id": "flat"}, "rooms": [{"id": "room_1", "polygon": pts, "floor_area": m(12.0),
            "ceiling_height": m(2.6), "walls": walls,
            "openings": [{"id": "op_1", "type": "door", "wall_id": "room_1_w2", "width": m(0.9)}]}]}


def test_form_round_trips_to_ground_truth(tmp_path):
    md = tape_form.form(_res(), 0.5)
    assert md.count("| wall ") == 4  # the 0.2 m piece is left out
    filled = md.replace("| 4.000 | |", "| 4.000 | 4.012 |").replace("| 2.6 | |", "| 2.6 | 2.61 |")
    f = tmp_path / "form.md"
    f.write_text(filled, encoding="utf-8-sig")  # as Notepad saves it
    gt = yaml.safe_load(tape_form.to_yaml(f))
    room = gt["rooms"][0]
    assert gt["capture"] == "flat" and room["match"] == "room_1"
    assert room["walls"] == [4.012, 4.012] and room["ceiling_height"] == 2.61
    assert "openings" not in room  # the door was left blank
