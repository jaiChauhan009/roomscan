"""Seeded synthetic captures: changing the AR world origin must preserve metric results."""
import copy
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import roomplan_synth as RS
from roomscan import pipeline
from roomscan.schema import Output


@pytest.mark.parametrize("seed", range(8))
def test_random_world_pose_preserves_room_measurements(tmp_path, monkeypatch, seed):
    rng = np.random.default_rng(seed)
    angle = rng.uniform(-np.pi, np.pi)
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    translation = rng.uniform(-50, 50, size=2)
    data = copy.deepcopy(RS.capture("merged"))

    def transform(point):
        return (rotation @ np.asarray(point) + translation).tolist()

    for room in data["rooms"]:
        for wall in room["walls"]:
            wall["start"] = transform(wall["start"])
            wall["end"] = transform(wall["end"])
        rng.shuffle(room["walls"])
        room["floor"]["polygon"] = [transform(p) for p in room["floor"]["polygon"]]
        for opening in room["doors"] + room["windows"] + room["openings"]:
            center = opening["center"]
            center[0], center[2] = transform([center[0], center[2]])
        # This test measures the structural geometry only.
        room["objects"] = []
    monkeypatch.setattr(pipeline, "CACHE_DIR", tmp_path / "cache")
    source = RS.write_folder(tmp_path / "capture", data=data)
    result = pipeline.run(source, tmp_path / "out", damage=False)
    Output.model_validate(result)
    assert sorted(r["floor_area"]["value"] for r in result["rooms"]) == pytest.approx([9, 12], abs=0.02)
    assert result["property"]["footprint_area"]["value"] == pytest.approx(21, abs=0.03)
    assert all(r["ceiling_height"]["value"] == pytest.approx(2.6, abs=0.01) for r in result["rooms"])
    assert len(result["property"]["adjacency"]) == 1


def test_optional_synthetic_depth_is_backward_compatible(tmp_path, monkeypatch):
    import json
    from scripts.synthetic_roomplan import attach_synthetic_depth

    source = RS.write_folder(tmp_path / "capture", "single", frames_per_room=1)
    attach_synthetic_depth(source)
    metadata = json.loads((source / "capture.json").read_text())["frames"][0]["depth"]
    values = np.fromfile(source / metadata["file"], dtype="<f4")
    assert values.size == metadata["width"] * metadata["height"]
    assert np.all(values == 2)
    confidence = np.fromfile(source / metadata["confidence_file"], dtype=np.uint8)
    assert np.all(confidence == 2)
    monkeypatch.setattr(pipeline, "CACHE_DIR", tmp_path / "cache")
    result = pipeline.run(source, tmp_path / "out", damage=False)
    Output.model_validate(result)
    assert result["rooms"][0]["floor_area"]["value"] == pytest.approx(12)
