"""The stage queue: stages run in order, each gate checks its output, a failed gate stops the run."""
import json

import pytest

from roomscan import stages as S


def test_stages_report_in_order_and_gates_pass(tmp_path):
    events = []
    st = S.Stages("lidar", lambda n, s, sec, note: events.append((n, s)), skip=("damage",))
    st["load"] = 0.3
    S.gate_load(st, "load", 300, "lidar")
    st["drift"] = 2.0
    S.gate_drift(st, {"enabled": True, "loop_edges": 3, "max_submap_shift_m": 0.12})
    assert ("load", "running") == events[0]
    assert ("load", "done") in events and ("drift", "running") in events
    st.write(tmp_path)
    data = json.loads((tmp_path / "stages.json").read_text())
    names = [s["name"] for s in data["stages"]]
    assert names == ["load", "drift", "fuse", "layout", "openings", "export"]  # damage skipped by request
    assert data["stages"][1]["gate"] == "ok" and "3 loop closure" in data["stages"][1]["note"]


def test_a_failed_gate_stops_the_run_and_names_the_stage():
    st = S.Stages("lidar")
    st["load"] = 0.1
    with pytest.raises(S.StageFailed, match="stage 'load'.*retake"):
        S.gate_load(st, "load", 3, "lidar")
    assert st.records["load"]["status"] == "failed"


def test_export_gate_requires_an_interval_on_every_measurement():
    st = S.Stages("photo")
    ok = {"rooms": [{"floor_area": {"value": 10.0, "ci90": [9.0, 11.0]}}]}
    S.gate_export(st, ok)
    with pytest.raises(S.StageFailed, match="without a 90 % interval"):
        S.gate_export(st, {"rooms": [{"floor_area": {"value": 10.0, "ci90": None}}]})


def test_a_crash_marks_the_running_stage_failed():
    events = []
    st = S.Stages("video", lambda n, s, sec, note: events.append((n, s, note)))
    st["load"] = 1.0
    st.fail_running("RuntimeError: tracking lost")
    assert ("drift", "failed", "RuntimeError: tracking lost") in events
