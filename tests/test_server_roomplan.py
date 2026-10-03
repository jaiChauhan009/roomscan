"""Web API with RoomPlan zips (our iOS app) in the LiDAR capture: recognised by their
capture.json, one run per zip titled by the room, a combined whole-home run per session of
two or more zips in one world frame, and a verify built on capture_quality.check_roomplan."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import roomplan_synth as RS  # noqa: E402
from test_server import capture, client, engine, space, upload_cap, wait  # noqa: E402,F401

import roomscan.pipeline as pl  # noqa: E402
from server.app import create_app  # noqa: E402


def rp_zip(name, box, session="s1", merged=True, frames=2, prefix="W") -> bytes:
    return RS.zip_bytes(data=RS.single_room(name, box, 0, session, merged, prefix, frames_per_room=frames))


def test_roomplan_zips_verify_plan_and_combined_run(client, engine):
    pid = client.post("/api/projects").json()["project_id"]
    k = space(client, pid, "Kitchen", length=4.0)
    space(client, pid, "Hall")
    capture(client, pid, "lidar")
    assert upload_cap(client, pid, "lidar", "kitchen.zip", rp_zip("Kitchen", RS.KITCHEN, prefix="K")).status_code == 200
    assert upload_cap(client, pid, "lidar", "hall.zip", rp_zip("Hall", RS.HALL, prefix="H")).status_code == 200
    v = client.post(f"/api/projects/{pid}/verify").json()
    assert [(c["item"], c["name"], c["status"]) for c in v["captures"]] == [
        ("kitchen.zip", "Room (RoomPlan): Kitchen", "ok"), ("hall.zip", "Room (RoomPlan): Hall", "ok")]
    assert {f["check"] for f in v["captures"][0]["findings"]} >= {"roomplan export", "walls", "frames"}
    assert v["ok"] is True
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={}).json()["job_id"])
    assert j["status"] == "done", j
    assert [(r["title"], r["prefix"]) for r in j["runs"]] == [
        ("Room (RoomPlan): Kitchen", "roomplan_1/"), ("Room (RoomPlan): Hall", "roomplan_2/"),
        ("Whole home (RoomPlan: 2 rooms)", "roomplan_home/")]
    assert [c["tier"] for c in engine.calls] == ["roomplan"] * 3
    assert "capture.json" in engine.calls[0]["files"] and any(f.startswith("frames/") for f in engine.calls[0]["files"])
    assert len(engine.calls[2]["folders"]) == 2
    assert sum(f.endswith("capture.json") for f in engine.calls[2]["files"]) == 2
    # the Kitchen run is compared with the Kitchen only; its typed size goes with it
    assert j["runs"][0]["space_ids"] == [k["space_id"]]
    assert engine.calls[0]["kw"]["measurements"] == {"rooms": [{"length": 4.0}]}
    assert any(s["name"].startswith("Whole home (RoomPlan: 2 rooms): ") for s in j["stages"])


def test_roomplan_separate_sessions_no_combined_run(client, engine):
    pid = client.post("/api/projects").json()["project_id"]
    capture(client, pid, "lidar")
    upload_cap(client, pid, "lidar", "a.zip", rp_zip("Kitchen", RS.KITCHEN, session=None, merged=False))
    upload_cap(client, pid, "lidar", "b.zip", rp_zip("Kitchen", RS.KITCHEN, session=None, merged=False, frames=0))
    v = client.post(f"/api/projects/{pid}/verify").json()
    # same room name twice: titles stay unique
    assert [c["name"] for c in v["captures"]] == ["Room (RoomPlan): Kitchen", "Room (RoomPlan): Kitchen (b.zip)"]
    assert v["captures"][1]["status"] == "warn"  # no frames: no damage detection
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={}).json()["job_id"])
    assert [r["title"] for r in j["runs"]] == ["Room (RoomPlan): Kitchen", "Room (RoomPlan): Kitchen (b.zip)"]


def test_roomplan_bad_zips_ask_for_retake(client, engine):
    pid = client.post("/api/projects").json()["project_id"]
    capture(client, pid, "lidar")
    upload_cap(client, pid, "lidar", "bad.zip", RS.zip_bytes("malformed"))
    data = RS.single_room("Empty", RS.KITCHEN)
    data["rooms"][0]["walls"] = []
    upload_cap(client, pid, "lidar", "empty.zip", RS.zip_bytes(data=data))
    v = client.post(f"/api/projects/{pid}/verify").json()
    assert [c["status"] for c in v["captures"]] == ["retake", "retake"] and v["ok"] is False
    assert "walls[0].end" in v["captures"][0]["findings"][0]["message"]
    assert any("no walls" in f["message"] for f in v["captures"][1]["findings"])
    assert "roomscan app" in v["captures"][1]["advice"]
    r = client.post(f"/api/projects/{pid}/run", json={})
    assert r.status_code == 409


def test_roomplan_real_engine_run(tmp_path, monkeypatch):
    """The real engine on one single-room zip through the server (damage off: no model loads)."""
    monkeypatch.setattr(pl, "CACHE_DIR", tmp_path / "cache")
    with TestClient(create_app(tmp_path / "data", engine_cache=False)) as c:
        pid = c.post("/api/projects").json()["project_id"]
        capture(c, pid, "lidar")
        upload_cap(c, pid, "lidar", "kitchen.zip", rp_zip("Kitchen", RS.KITCHEN, frames=0))
        j = wait(c, c.post(f"/api/projects/{pid}/run", json={"damage": False}).json()["job_id"], timeout=120)
        assert j["status"] == "done", j
        run = j["runs"][0]
        assert run["title"] == "Room (RoomPlan): Kitchen" and run["n_rooms"] == 1
        res = json.loads(c.get(run["outputs"]["result_json"]).content)
        assert res["capture"]["tier"] == "roomplan"
        assert res["rooms"][0]["floor_area"]["value"] == pytest.approx(12.0, abs=0.01)
        assert {"plan_png", "plan_svg", "result_xlsx"} <= set(run["outputs"])
        stages = [s for s in j["stages"] if s["name"].startswith("Room (RoomPlan): Kitchen: ")]
        assert [s["name"].rsplit(": ", 1)[1] for s in stages] == ["load", "layout", "openings", "export"]
        assert all(s["status"] == "done" for s in stages)
