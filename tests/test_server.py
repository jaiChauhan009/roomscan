"""Web API (server/): projects (rooms + one optional whole-home video / LiDAR capture), uploads,
verify, jobs, cache, comparison and restart, with the engine mocked; one optional real run on
../data/single_room."""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import roomscan.pipeline as pl  # noqa: E402
from server.app import create_app  # noqa: E402
from server.store import read_json, write_json  # noqa: E402

# ../data next to the repository (also found from a worktree checked out below it)
DATA = next((p / "data" for p in Path(__file__).resolve().parents if (p / "data" / "single_room").is_dir()),
            Path(__file__).resolve().parents[2] / "data")


# ---------------------------------------------------------------- helpers

def texture(h: int, w: int, seed: int) -> np.ndarray:
    r = np.random.default_rng(seed)
    img = cv2.resize(r.integers(0, 255, (h // 8, w // 8), dtype=np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
    for _ in range(40):
        p = tuple(int(v) for v in r.integers(0, [w, h])), tuple(int(v) for v in r.integers(0, [w, h]))
        cv2.line(img, *p, int(r.integers(0, 255)), 2)
    return img


def jpeg(seed: int, h: int = 1200, w: int = 1600) -> bytes:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(texture(h, w, seed), cv2.COLOR_GRAY2BGR))
    assert ok
    return buf.tobytes()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def upload(c: TestClient, pid: str, sid: str, name: str, data: bytes, digest: str | None = None):
    return c.put(f"/api/projects/{pid}/spaces/{sid}/files", files={"file": (name, data)},
                 data={"sha256": digest or sha(data), "name": name})


def upload_cap(c: TestClient, pid: str, kind: str, name: str, data: bytes, digest: str | None = None):
    return c.put(f"/api/projects/{pid}/captures/{kind}/files", files={"file": (name, data)},
                 data={"sha256": digest or sha(data), "name": name})


def capture(c, pid, kind):
    r = c.put(f"/api/projects/{pid}/captures/{kind}", json={})
    assert r.status_code == 200, r.text
    return r.json()


def space(c, pid, name, kind="photos", **sizes):
    r = c.post(f"/api/projects/{pid}/spaces",
               json={"name": name, "kind": kind, "sizes": {"length": None, "width": None, "height": None, **sizes}})
    assert r.status_code == 200, r.text
    return r.json()


def wait(c: TestClient, jid: str, timeout: float = 30.0) -> dict:
    t = time.time()
    while time.time() - t < timeout:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {jid} did not finish: {j}")


def box(rid: str, L: float, W: float, H: float = 2.5) -> dict:
    m = lambda v, s=0.03: {"value": v, "ci90": [v - s, v + s], "sigma": s / 1.645, "unit": "m"}  # noqa: E731
    pts = [(0, 0), (L, 0), (L, W), (0, W)]
    walls = [{"id": f"{rid}_w{i}", "start": a, "end": b, "length": m(float(np.hypot(b[0] - a[0], b[1] - a[1]))),
              "height": m(H), "area": m(1.0), "opening_ids": [], "evidence_coverage": 1.0}
             for i, (a, b) in enumerate(zip(pts, pts[1:] + pts[:1]), 1)]
    return {"id": rid, "label": "room", "polygon": pts, "floor_area": m(L * W), "perimeter": m(2 * (L + W)),
            "ceiling_height": m(H, 0.05), "ceiling_source": "ceiling_plane", "walls": walls, "openings": [],
            "surfaces": []}


class FakeEngine:
    """Stands in for roomscan.pipeline.run: one 4.0 x 3.0 x 2.5 m room per photo folder (a
    whole-home capture: the rooms in `home_rooms`), the files the real run writes, and a log of
    the calls."""

    def __init__(self):
        self.calls: list[dict] = []
        self.home_rooms = [("room_1", 4.0, 3.0)]

    def run(self, path, out_dir, tier="auto", damage=True, progress=True, measurements=None, **kw):
        path, out_dir = Path(path), Path(out_dir)
        if measurements is not None:
            kw["measurements"] = measurements
        call = {"path": path, "tier": tier, "damage": damage, "kw": kw}
        if path.is_dir():
            call["folders"] = sorted(p.name for p in path.iterdir() if p.is_dir())
            call["files"] = sorted(str(p.relative_to(path)).replace("\\", "/") for p in path.rglob("*") if p.is_file())
            if (path / "measurements.yaml").is_file():
                call["measurements"] = (path / "measurements.yaml").read_text(encoding="utf-8")
        self.calls.append(call)
        if "on_stage" in kw:
            kw["on_stage"]("load+depth", "running", None, None)
            kw["on_stage"]("load+depth", "done", 1.5, "3 photos")
            kw["on_stage"]("extra", "done", 0.1, None)
        rooms = ([box(rid, 4.0, 3.0) for rid in call["folders"]] if tier == "photo"
                 else [box(*r) for r in self.home_rooms])
        res = {"capture": {"id": path.name, "tier": tier}, "property": {}, "warnings": ["fake"],
               "rooms": rooms, "damage": [], "concealed_damage_flags": [], "scope": []}
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.json").write_text(json.dumps(res), encoding="utf-8")
        (out_dir / "plan.png").write_bytes(b"\x89PNG fake")
        (out_dir / "plan.svg").write_text("<svg/>", encoding="utf-8")
        return res


@pytest.fixture
def engine(monkeypatch):
    fe = FakeEngine()
    monkeypatch.setattr(pl, "run", fe.run)
    return fe


@pytest.fixture
def client(tmp_path, engine):
    with TestClient(create_app(tmp_path / "data", engine_cache=False)) as c:
        yield c


def photo_project(c, n=3, **sizes):
    pid = c.post("/api/projects").json()["project_id"]
    k = space(c, pid, "Kitchen", **sizes)
    h = space(c, pid, "Hall / entry")
    for s, off in ((k, 0), (h, 10)):
        for i in range(n):
            assert upload(c, pid, s["space_id"], f"IMG_{i:04d}.jpg", jpeg(off + i)).status_code == 200
    return pid, k, h


# ---------------------------------------------------------------- tests

def test_health(client):
    r = client.get("/api/health").json()
    assert r["ok"] is True and r["version"].startswith("0.")


def test_projects_spaces_and_uploads(client):
    pid = client.post("/api/projects").json()["project_id"]
    s = space(client, pid, "Kitchen", length=4.1)
    assert s["sizes"] == {"length": 4.1, "width": None, "height": None} and s["files"] == []
    sid = s["space_id"]
    data = jpeg(1, 240, 320)
    # wrong hash -> rejected, nothing stored
    r = upload(client, pid, sid, "a.jpg", data, digest="0" * 64)
    assert r.status_code == 400 and "mismatch" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}/spaces/{sid}/files").json() == {"files": []}
    assert upload(client, pid, sid, "a.jpg", data, digest="xyz").status_code == 422
    # upload twice -> one record
    r1, r2 = upload(client, pid, sid, "a.jpg", data), upload(client, pid, sid, "again.jpg", data)
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    assert r1.json() == {"name": "a.jpg", "sha256": sha(data), "size": len(data)}
    files = client.get(f"/api/projects/{pid}/spaces/{sid}/files").json()["files"]
    assert [f["sha256"] for f in files] == [sha(data)]
    # path tricks in names are neutralised
    other = jpeg(2, 240, 320)
    assert upload(client, pid, sid, "../../evil.jpg", other).json()["name"] == "evil.jpg"
    # patch, delete file, delete space
    r = client.patch(f"/api/projects/{pid}/spaces/{sid}", json={"name": "Kitchen 2", "sizes": {"width": 3.0}})
    assert r.json()["name"] == "Kitchen 2" and r.json()["sizes"] == {"length": 4.1, "width": 3.0, "height": None}
    assert client.delete(f"/api/projects/{pid}/spaces/{sid}/files/{sha(other)}").status_code == 200
    assert client.delete(f"/api/projects/{pid}/spaces/{sid}/files/{sha(other)}").status_code == 404
    p = client.get(f"/api/projects/{pid}").json()
    assert p["last_job_id"] is None and len(p["spaces"][0]["files"]) == 1
    assert client.delete(f"/api/projects/{pid}/spaces/{sid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").json()["spaces"] == []
    assert client.get("/api/projects/ffffffffffff").status_code == 404
    assert client.post(f"/api/projects/{pid}/spaces", json={"name": "x", "kind": "laser"}).status_code == 422
    r = client.post(f"/api/projects/{pid}/spaces", json={"name": "x", "kind": "video"})
    assert r.status_code == 422 and "/captures/video" in r.json()["detail"]
    assert client.post(f"/api/projects/{pid}/spaces", json={"name": "no kind"}).json()["kind"] == "photos"


def test_upload_size_limit(client, monkeypatch):
    monkeypatch.setenv("ROOMSCAN_MAX_FILE_MB", "0.001")
    pid = client.post("/api/projects").json()["project_id"]
    sid = space(client, pid, "a")["space_id"]
    assert upload(client, pid, sid, "a.jpg", b"x" * 5000).status_code == 413


def test_verify_run_poll_download_compare_and_cache(client, engine):
    pid, k, h = photo_project(client, length=4.2, width=2.9, height=2.6)
    v = client.post(f"/api/projects/{pid}/verify").json()
    assert v["ok"] is True, v
    assert [s["name"] for s in v["spaces"]] == ["Kitchen", "Hall / entry"]
    assert all(s["status"] in ("ok", "warn") for s in v["spaces"])
    assert all({"level", "check", "message", "files"} <= set(f) for s in v["spaces"] for f in s["findings"])

    r = client.post(f"/api/projects/{pid}/run", json={"damage": True}).json()
    assert r["cached"] is False
    j = wait(client, r["job_id"])
    assert j["status"] == "done", j
    assert [s["name"] for s in j["stages"]] == ["verify", "Rooms (photos): load+process", "Rooms (photos): export"]
    assert all(s["status"] == "done" for s in j["stages"])
    assert j["runs"][0]["tier"] == "photos" and j["runs"][0]["space_ids"] == [k["space_id"], h["space_id"]]
    # the capture as materialised for the engine
    call = engine.calls[0]
    assert call["tier"] == "photo" and call["folders"] == ["01_Kitchen", "02_Hall _ entry"]
    assert "01_Kitchen/IMG_0000.jpg" in call["files"]
    assert "01_Kitchen" in call["measurements"] and "length: 4.2" in call["measurements"]
    # outputs
    assert set(j["outputs"]) == {"result_json", "result_xlsx", "plan_png", "plan_svg"}
    for url in j["outputs"].values():
        assert client.get(url).status_code == 200
    assert client.get(j["outputs"]["result_json"]).json()["rooms"][0]["id"] == "01_Kitchen"
    assert client.get(f"/api/jobs/{r['job_id']}/files/job.json").status_code == 404
    assert client.get(f"/api/projects/{pid}").json()["last_job_id"] == r["job_id"]
    # comparison: given vs computed
    rows = client.get(f"/api/jobs/{r['job_id']}/comparison").json()["rows"]
    kit = {x["quantity"]: x for x in rows if x["space"] == "Kitchen"}
    assert kit["length"]["computed"] == 4.0 and kit["length"]["given"] == 4.2
    assert kit["length"]["diff"] == pytest.approx(-0.2) and kit["length"]["diff_pct"] == pytest.approx(-4.8)
    assert kit["width"]["computed"] == 3.0 and kit["height"]["computed"] == 2.5
    assert kit["length"]["ci90"] == pytest.approx([3.97, 4.03])
    hall = {x["quantity"]: x for x in rows if x["space"] == "Hall / entry"}
    assert hall["length"]["given"] is None and hall["length"]["diff"] is None and hall["length"]["computed"] == 4.0

    # identical run -> the finished job, no engine call
    r2 = client.post(f"/api/projects/{pid}/run", json={"damage": True}).json()
    assert r2 == {"job_id": r["job_id"], "cached": True} and len(engine.calls) == 1
    # a different flag or capture -> a new job
    r3 = client.post(f"/api/projects/{pid}/run", json={"damage": False}).json()
    assert r3["cached"] is False and r3["job_id"] != r["job_id"]
    assert wait(client, r3["job_id"])["status"] == "done" and engine.calls[-1]["damage"] is False
    client.patch(f"/api/projects/{pid}/spaces/{k['space_id']}", json={"sizes": {"length": 4.0}})
    assert client.post(f"/api/projects/{pid}/run", json={}).json()["cached"] is False


def test_engine_stage_callback(client, engine, monkeypatch):
    """An engine with on_stage: its stage plan is shown (prefixed by the run), updated live, extra
    stages appended; every room's sizes go to the whole-home run as an unnamed list."""
    from roomscan.stages import PLAN, StageFailed

    def run(path, out_dir, tier="auto", damage=True, progress=True, measurements=None, on_stage=None):
        if tier == "video":
            engine.calls.append({"measurements": measurements})
            on_stage("load", "running", None, None)
            raise StageFailed("layout: no room found")
        return engine.run(path, out_dir, tier=tier, damage=damage, progress=progress, on_stage=on_stage)
    monkeypatch.setattr(pl, "run", run)
    pid, k, h = photo_project(client, n=2)
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={"damage": False}).json()["job_id"])
    names = [s["name"] for s in j["stages"]]
    assert names == ["verify"] + [f"Rooms (photos): {s}" for s in PLAN["photo"] if s != "damage"] + \
        ["Rooms (photos): extra"]
    assert j["stages"][1] == {"name": "Rooms (photos): load+depth", "status": "done", "seconds": 1.5,
                              "note": "3 photos"}
    assert j["stages"][2]["status"] == "pending"  # the fake engine reports no more
    # a whole-home video: the engine's stage error is that run's error
    client.patch(f"/api/projects/{pid}/spaces/{k['space_id']}", json={"sizes": {"length": 5.0}})
    space(client, pid, "Lounge", width=3.0, height=2.4)  # sizes only, no photos
    capture(client, pid, "video")
    assert upload_cap(client, pid, "video", "walk.mp4", b"fake").status_code == 200
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={"force": True}).json()["job_id"])
    assert engine.calls[-1]["measurements"] == {"rooms": [{"length": 5.0}, {"width": 3.0, "height": 2.4}]}
    vrun = j["runs"][1]
    assert vrun["title"] == "Whole home (video)" and vrun["whole_home"] is True
    assert j["status"] == "done" and vrun["status"] == "failed" and vrun["error"] == "layout: no room found"
    assert "Whole home (video): layout: no room found" in j["error"]
    load = next(s for s in j["stages"] if s["name"] == "Whole home (video): load")
    assert load["status"] == "failed"


def test_verify_asks_for_retake_and_run_refuses(client, engine):
    pid = client.post("/api/projects").json()["project_id"]
    one = space(client, pid, "one photo")
    walk = space(client, pid, "walking")
    upload(client, pid, one["space_id"], "only.jpg", jpeg(0))
    for i in range(40):
        upload(client, pid, walk["space_id"], f"IMG_{i:04d}.jpg", jpeg(100 + i, 240, 320))
    v = client.post(f"/api/projects/{pid}/verify").json()
    assert v["ok"] is False and v["captures"] == []
    st = {s["name"]: s for s in v["spaces"]}
    assert st["one photo"]["status"] == "retake"
    f = next(f for f in st["one photo"]["findings"] if f["check"] == "photos in room")
    assert f["level"] == "retake" and f["files"] == ["only.jpg"]
    assert st["walking"]["status"] == "retake"
    assert next(f for f in st["walking"]["findings"] if f["check"] == "photo count")["level"] == "retake"
    r = client.post(f"/api/projects/{pid}/run", json={})
    assert r.status_code == 409
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={"force": True}).json()["job_id"])
    assert j["status"] == "done" and "forced" in j["stages"][0]["note"]


def test_unverified_retake_fails_in_job(client, engine):
    pid = client.post("/api/projects").json()["project_id"]
    capture(client, pid, "video")
    upload_cap(client, pid, "video", "notes.txt", b"not a video")
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={}).json()["job_id"])
    assert j["status"] == "failed" and "retake" in j["error"] and engine.calls == []
    assert j["stages"][0]["status"] == "failed"


def test_capture_endpoints_and_second_file_rejected(client):
    pid = client.post("/api/projects").json()["project_id"]
    assert client.get(f"/api/projects/{pid}").json()["captures"] == {"video": None, "lidar": None}
    assert client.get(f"/api/projects/{pid}/captures/video").status_code == 404
    assert upload_cap(client, pid, "video", "a.mov", b"one").status_code == 404
    assert client.put(f"/api/projects/{pid}/captures/photos", json={}).status_code == 422
    assert capture(client, pid, "video") == {"kind": "video", "files": []}
    # no body is fine too; both kinds side by side
    r = client.put(f"/api/projects/{pid}/captures/lidar")
    assert r.status_code == 200 and r.json() == {"kind": "lidar", "files": []}
    # one clip; the same clip again is idempotent; a second clip is refused with a clear message
    r1, r2 = upload_cap(client, pid, "video", "a.mov", b"one"), upload_cap(client, pid, "video", "again.mov", b"one")
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    r = upload_cap(client, pid, "video", "b.mp4", b"two")
    assert r.status_code == 409 and "already has a video: a.mov" in r.json()["detail"]
    assert "/captures/video/files/" in r.json()["detail"]
    assert upload_cap(client, pid, "video", "c.mov", b"x", digest="0" * 64).status_code in (400, 409)
    # one zip in the LiDAR capture, a second zip refused; the video is untouched
    z1 = upload_cap(client, pid, "lidar", "scan.zip", b"zip one")
    assert z1.status_code == 200
    r = upload_cap(client, pid, "lidar", "scan2.zip", b"zip two")
    assert r.status_code == 409 and "LiDAR scan (.zip): scan.zip" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}/captures/video/files").json()["files"] == [r1.json()]
    assert client.get(f"/api/projects/{pid}/captures/lidar").json() == {"kind": "lidar", "files": [z1.json()]}
    assert client.get(f"/api/projects/{pid}").json()["captures"] == {
        "video": {"kind": "video", "files": [r1.json()]}, "lidar": {"kind": "lidar", "files": [z1.json()]}}
    # PUT again keeps the file; delete the file, then the second clip is accepted
    assert capture(client, pid, "video")["files"] == [r1.json()]
    assert client.delete(f"/api/projects/{pid}/captures/video/files/{sha(b'one')}").status_code == 200
    assert client.delete(f"/api/projects/{pid}/captures/video/files/{sha(b'one')}").status_code == 404
    assert client.delete(f"/api/projects/{pid}/captures/lidar/files/{sha(b'one')}").status_code == 404
    assert upload_cap(client, pid, "video", "b.mp4", b"two").status_code == 200
    # deleting one capture leaves the other
    assert client.delete(f"/api/projects/{pid}/captures/video").status_code == 200
    assert client.get(f"/api/projects/{pid}").json()["captures"] == {
        "video": None, "lidar": {"kind": "lidar", "files": [z1.json()]}}
    assert client.delete(f"/api/projects/{pid}/captures/video").status_code == 404


def test_store_files_and_old_single_capture(tmp_path, engine):
    """Files live under files/_capture_video/ and files/_capture_lidar/; a project saved with the
    old single "capture" reads as captures[kind] with its files moved."""
    data = tmp_path / "data"
    with TestClient(create_app(data, engine_cache=False)) as c:
        pid = c.post("/api/projects").json()["project_id"]
        capture(c, pid, "video")
        capture(c, pid, "lidar")
        upload_cap(c, pid, "video", "a.mov", b"clip")
        upload_cap(c, pid, "lidar", "s.zip", b"zip")
        files = data / "projects" / pid / "files"
        assert (files / "_capture_video" / sha(b"clip")).is_file()
        assert (files / "_capture_lidar" / sha(b"zip")).is_file()
        pj = files.parent / "project.json"
        old = json.loads(pj.read_text(encoding="utf-8"))
        old["capture"] = old.pop("captures")["lidar"]
        pj.write_text(json.dumps(old), encoding="utf-8")
        (files / "_capture_lidar").rename(files / "_capture")
        caps = c.get(f"/api/projects/{pid}").json()["captures"]
        assert caps["video"] is None and caps["lidar"]["files"][0]["name"] == "s.zip"
        assert c.get(f"/api/projects/{pid}/captures/lidar/files").status_code == 200


def test_rooms_without_photos_need_a_capture(client, engine):
    pid = client.post("/api/projects").json()["project_id"]
    r = space(client, pid, "Kitchen", length=4.0)
    v = client.post(f"/api/projects/{pid}/verify").json()
    assert v["ok"] is False and v["spaces"][0]["status"] == "retake"
    assert any(f["check"] == "nothing to compute" for f in v["project_findings"])
    r409 = client.post(f"/api/projects/{pid}/run", json={"force": True})
    assert r409.status_code == 409 and "nothing to compute" in r409.json()["detail"]
    capture(client, pid, "video")
    upload_cap(client, pid, "video", "walk.mov", b"clip")
    v = client.post(f"/api/projects/{pid}/verify").json()
    sp = v["spaces"][0]
    assert sp["space_id"] == r["space_id"] and sp["status"] == "ok" and "size" in sp["findings"][0]["message"]
    assert [c["name"] for c in v["captures"]] == ["whole home: video"] and v["captures"][0]["kind"] == "video"
    assert {"kind", "name", "status", "findings", "advice"} == set(v["captures"][0])
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={"force": True}).json()["job_id"])
    assert j["status"] == "done" and [r["tier"] for r in j["runs"]] == ["video"]
    assert j["runs"][0]["prefix"] == "" and engine.calls[-1]["tier"] == "video"
    assert engine.calls[-1]["kw"]["measurements"] == {"rooms": [{"length": 4.0}]}
    assert all(s["name"] == "verify" or s["name"].startswith("Whole home (video): ") for s in j["stages"])


def test_structural_checks_capture(client):
    pid = client.post("/api/projects").json()["project_id"]
    capture(client, pid, "lidar")
    upload_cap(client, pid, "lidar", "notes.txt", b"hello")
    empty = space(client, pid, "empty")
    res = client.post(f"/api/projects/{pid}/verify").json()
    (cap,) = res["captures"]
    assert cap["status"] == "retake" and cap["name"] == "whole home: LiDAR" and cap["kind"] == "lidar"
    assert "Stray Scanner" in cap["advice"]
    assert res["spaces"][0]["status"] == "ok" and empty["kind"] == "photos"  # a size reference, no sizes
    assert res["ok"] is False


def test_lidar_zip_verify_reads_odometry(client):
    """A zipped Stray export: verify reads odometry.csv without extracting the frames."""
    rows = ["timestamp, frame, x, y, z, qx, qy, qz, qw"]
    for i in range(300):
        t = i / 10
        rows.append(f"{t},{i},{0.01 * i},0,0,0,0,0,1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scan/odometry.csv", "\n".join(rows))
        z.writestr("scan/camera_matrix.csv", "1,0,0\n0,1,0\n0,0,1")
        z.writestr("scan/rgb.mp4", b"x" * 100)
        for i in range(10):
            z.writestr(f"scan/depth/{i:06d}.png", b"x")
    pid = client.post("/api/projects").json()["project_id"]
    capture(client, pid, "lidar")
    upload_cap(client, pid, "lidar", "scan.zip", buf.getvalue())
    (cap,) = client.post(f"/api/projects/{pid}/verify").json()["captures"]
    checks = {f["check"]: f["level"] for f in cap["findings"]}
    assert checks["lidar export"] == "ok" and "duration" in checks, cap


def test_photos_and_whole_home_run_separately(client, engine):
    """Photos run + whole-home run; comparison rows per room for each run with its tier: the
    photo run by folder, the whole-home run by the engine's matching (aspect, size)."""
    engine.home_rooms = [("room_1", 4.0, 4.0), ("room_2", 6.0, 3.0)]
    pid, k, h = photo_project(client, n=2)
    client.patch(f"/api/projects/{pid}/spaces/{k['space_id']}", json={"sizes": {"length": 6.2, "width": 2.9}})
    client.patch(f"/api/projects/{pid}/spaces/{h['space_id']}", json={"sizes": {"length": 3.9, "width": 3.9}})
    capture(client, pid, "video")
    upload_cap(client, pid, "video", "walk.mov", b"fake clip")
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={"force": True}).json()["job_id"])
    assert j["status"] == "done", j
    assert [(r["tier"], r["title"], r["prefix"]) for r in j["runs"]] == [
        ("photos", "Rooms (photos)", "photos/"), ("video", "Whole home (video)", "whole_home_video/")]
    assert engine.calls[1]["tier"] == "video" and engine.calls[1]["path"].name == "walk.mov"
    assert engine.calls[1]["kw"]["measurements"] == {"rooms": [{"length": 6.2, "width": 2.9},
                                                               {"length": 3.9, "width": 3.9}]}
    assert "measurements" not in engine.calls[0]["kw"] and "01_Kitchen" in engine.calls[0]["measurements"]
    assert j["outputs"]["result_json"].endswith("/files/photos/result.json")
    assert client.get(j["runs"][1]["outputs"]["plan_svg"]).status_code == 200
    names = [s["name"] for s in j["stages"]]
    assert "Rooms (photos): load+process" in names and "Whole home (video): load+process" in names
    rows = client.get(f"/api/jobs/{j['job_id']}/comparison").json()["rows"]
    assert {(r["space"], r["tier"]) for r in rows} == {("Kitchen", "photos"), ("Hall / entry", "photos"),
                                                        ("Kitchen", "video"), ("Hall / entry", "video")}
    kv = {r["quantity"]: r for r in rows if r["space"] == "Kitchen" and r["tier"] == "video"}
    assert kv["length"]["room_id"] == "room_2" and kv["length"]["computed"] == 6.0
    assert kv["length"]["diff"] == pytest.approx(-0.2) and kv["width"]["computed"] == 3.0
    hv = {r["quantity"]: r for r in rows if r["space"] == "Hall / entry" and r["tier"] == "video"}
    assert hv["length"]["room_id"] == "room_1" and hv["width"]["computed"] == 4.0
    kp = {r["quantity"]: r for r in rows if r["space"] == "Kitchen" and r["tier"] == "photos"}
    assert kp["length"]["room_id"] == "01_Kitchen" and kp["length"]["computed"] == 4.0
    assert all(r["run"] in ("Rooms (photos)", "Whole home (video)") for r in rows)


def test_photos_video_and_lidar_three_runs(client, engine):
    """Both whole-home captures and photos: three runs with their prefixes; comparison rows for
    each run with its tier; every room's sizes go to both whole-home runs."""
    engine.home_rooms = [("room_1", 4.0, 4.0), ("room_2", 6.0, 3.0)]
    pid, k, h = photo_project(client, n=2)
    client.patch(f"/api/projects/{pid}/spaces/{k['space_id']}", json={"sizes": {"length": 6.2, "width": 2.9}})
    space(client, pid, "Lounge", height=2.4)  # no photos: fine with a capture
    capture(client, pid, "video")
    capture(client, pid, "lidar")
    upload_cap(client, pid, "video", "walk.mov", b"fake clip")
    upload_cap(client, pid, "lidar", "odometry.csv", b"t,f\n")
    v = client.post(f"/api/projects/{pid}/verify").json()
    assert [(c["kind"], c["name"]) for c in v["captures"]] == [("lidar", "whole home: LiDAR"),
                                                               ("video", "whole home: video")]
    assert next(f for f in v["project_findings"] if f["check"] == "tiers")["message"].startswith("3 runs")
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={"force": True}).json()["job_id"])
    assert j["status"] == "done", j
    assert [(r["tier"], r["title"], r["prefix"]) for r in j["runs"]] == [
        ("photos", "Rooms (photos)", "photos/"), ("lidar", "Whole home (LiDAR)", "whole_home_lidar/"),
        ("video", "Whole home (video)", "whole_home_video/")]  # fastest first
    assert [c["tier"] for c in engine.calls] == ["photo", "lidar", "video"]
    meas = {"rooms": [{"length": 6.2, "width": 2.9}, {"height": 2.4}]}
    assert engine.calls[1]["kw"]["measurements"] == meas and engine.calls[2]["kw"]["measurements"] == meas
    assert engine.calls[1]["files"] == ["odometry.csv"]  # the LiDAR run
    for r in j["runs"]:
        assert r["outputs"]["result_json"].endswith(f"/files/{r['prefix']}result.json")
        assert client.get(r["outputs"]["plan_svg"]).status_code == 200
    pre = {s["name"].split(": ", 1)[0] for s in j["stages"] if s["name"] != "verify"}
    assert pre == {"Rooms (photos)", "Whole home (video)", "Whole home (LiDAR)"}
    rows = client.get(f"/api/jobs/{j['job_id']}/comparison").json()["rows"]
    assert {(r["space"], r["tier"]) for r in rows} == {
        ("Kitchen", "photos"), ("Hall / entry", "photos"),
        ("Kitchen", "video"), ("Hall / entry", "video"), ("Lounge", "video"),
        ("Kitchen", "lidar"), ("Hall / entry", "lidar"), ("Lounge", "lidar")}
    for tier in ("video", "lidar"):
        kv = {r["quantity"]: r for r in rows if r["space"] == "Kitchen" and r["tier"] == tier}
        assert kv["length"]["room_id"] == "room_2" and kv["length"]["diff"] == pytest.approx(-0.2)
    # a different capture changes the cache key
    client.delete(f"/api/projects/{pid}/captures/lidar")
    r = client.post(f"/api/projects/{pid}/run", json={"force": True}).json()
    assert r["cached"] is False and r["job_id"] != j["job_id"]
    assert [x["tier"] for x in wait(client, r["job_id"])["runs"]] == ["photos", "video"]


def test_whole_home_rooms_without_photos_and_height_only(client, engine):
    """LiDAR whole home + rooms that are size references only; a height-only room is compared
    with the largest room left; a room without sizes gets no match."""
    engine.home_rooms = [("room_1", 5.0, 4.0), ("room_2", 3.0, 2.0)]
    pid = client.post("/api/projects").json()["project_id"]
    space(client, pid, "Bed", length=3.1, width=2.1)
    space(client, pid, "Living", height=2.6)
    space(client, pid, "Bath")
    capture(client, pid, "lidar")
    upload_cap(client, pid, "lidar", "scan.zip", b"not really a zip")
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={"force": True}).json()["job_id"])
    # an unreadable zip fails the run before the engine
    assert j["status"] == "failed" and "cannot unzip" in j["error"]
    rows = []
    # engine reached with a folder of loose export files instead
    client.delete(f"/api/projects/{pid}/captures/lidar/files/{sha(b'not really a zip')}")
    upload_cap(client, pid, "lidar", "odometry.csv", b"t,f\n")
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={"force": True}).json()["job_id"])
    assert j["status"] == "done", j
    assert engine.calls[-1]["tier"] == "lidar"
    assert engine.calls[-1]["kw"]["measurements"] == {"rooms": [{"length": 3.1, "width": 2.1}, {"height": 2.6}]}
    rows = client.get(f"/api/jobs/{j['job_id']}/comparison").json()["rows"]
    by = {(r["space"], r["quantity"]): r for r in rows}
    assert {r["tier"] for r in rows} == {"lidar"}
    assert by[("Bed", "length")]["room_id"] == "room_2" and by[("Bed", "length")]["computed"] == 3.0
    assert by[("Living", "height")]["room_id"] == "room_1" and by[("Living", "height")]["computed"] == 2.5
    assert by[("Bath", "length")]["room_id"] is None and by[("Bath", "length")]["computed"] is None


def test_failed_engine_run_reports_error(client, monkeypatch):
    def boom(*a, **k):
        raise pl.InputError("no closed room found")
    monkeypatch.setattr(pl, "run", boom)
    pid, *_ = photo_project(client, n=2)
    jid = client.post(f"/api/projects/{pid}/run", json={}).json()["job_id"]
    j = wait(client, jid)
    assert j["status"] == "failed" and "no closed room" in j["error"]
    assert client.get(f"/api/jobs/{jid}/comparison").status_code == 409
    # a failed job is not a cache hit
    assert client.post(f"/api/projects/{pid}/run", json={}).json()["job_id"] != jid


def test_restart_keeps_state_and_fails_interrupted_jobs(tmp_path, engine, monkeypatch):
    d = tmp_path / "data"
    with TestClient(create_app(d, engine_cache=False)) as c:
        pid, k, _ = photo_project(c, n=2, length=4.0)
        jid = c.post(f"/api/projects/{pid}/run", json={}).json()["job_id"]
        assert wait(c, jid)["status"] == "done"
    # a job that was running when the server stopped, and an expired one
    write_json(d / "jobs" / "aaaaaaaaaaaa" / "job.json",
               {**read_json(d / "jobs" / jid / "job.json"), "job_id": "aaaaaaaaaaaa", "status": "running",
                "cache_key": "x"})
    write_json(d / "jobs" / "bbbbbbbbbbbb" / "job.json",
               {**read_json(d / "jobs" / jid / "job.json"), "job_id": "bbbbbbbbbbbb", "finished": 1.0})
    monkeypatch.setenv("ROOMSCAN_RESULT_TTL_DAYS", "7")
    with TestClient(create_app(d, engine_cache=False)) as c:
        p = c.get(f"/api/projects/{pid}").json()
        assert p["last_job_id"] == jid and len(p["spaces"][0]["files"]) == 2
        assert c.get(f"/api/jobs/{jid}").json()["status"] == "done"
        assert c.get(f"/api/jobs/{jid}/files/result.xlsx").status_code == 200
        bad = c.get("/api/jobs/aaaaaaaaaaaa").json()
        assert bad["status"] == "failed" and "server restarted" in bad["error"]
        assert c.get("/api/jobs/bbbbbbbbbbbb").status_code == 404  # past the TTL
        assert c.post(f"/api/projects/{pid}/run", json={}).json() == {"job_id": jid, "cached": True}
        assert len(engine.calls) == 1


def test_walk_order_and_size_nulls(client, engine):
    pid, k, h = photo_project(client, n=2, length=4.0)
    r = client.put(f"/api/projects/{pid}/order", json={"space_ids": [h["space_id"], k["space_id"]]})
    assert [s["name"] for s in r.json()["spaces"]] == ["Hall / entry", "Kitchen"]
    assert [s["name"] for s in client.get(f"/api/projects/{pid}").json()["spaces"]] == ["Hall / entry", "Kitchen"]
    bad = client.put(f"/api/projects/{pid}/order", json={"space_ids": [h["space_id"]]})
    assert bad.status_code == 422 and "exactly once" in bad.json()["detail"]
    # an explicit null clears a size
    r = client.patch(f"/api/projects/{pid}/spaces/{k['space_id']}",
                     json={"sizes": {"length": None, "width": 3.0, "height": None}})
    assert r.json()["sizes"] == {"length": None, "width": 3.0, "height": None}
    j = wait(client, client.post(f"/api/projects/{pid}/run", json={}).json()["job_id"])
    assert j["status"] == "done" and engine.calls[-1]["folders"] == ["01_Hall _ entry", "02_Kitchen"]
    assert "02_Kitchen" in engine.calls[-1]["measurements"]


def test_cors(client):
    r = client.options("/api/health", headers={"Origin": "http://x.test", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") in ("*", "http://x.test")


@pytest.mark.skipif(not (DATA / "single_room").is_dir(), reason="needs ../data/single_room")
def test_real_run_single_room(tmp_path, monkeypatch):
    """The real engine on the smallest capture (a Stray Scanner LiDAR scan, about a minute)."""
    pytest.importorskip("torch")
    src = next(p for p in (DATA / "single_room").iterdir() if p.is_dir())
    zpath = tmp_path / "scan.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for f in src.rglob("*"):
            if f.is_file():
                z.write(f, f"{src.name}/{f.relative_to(src).as_posix()}")
    with TestClient(create_app(tmp_path / "data")) as c:
        pid = c.post("/api/projects").json()["project_id"]
        space(c, pid, "single room", height=2.5)
        capture(c, pid, "lidar")
        r = c.put(f"/api/projects/{pid}/captures/lidar/files",
                  files={"file": ("scan.zip", zpath.open("rb"))},
                  data={"sha256": hashlib.sha256(zpath.read_bytes()).hexdigest(), "name": "scan.zip"})
        assert r.status_code == 200, r.text
        v = c.post(f"/api/projects/{pid}/verify").json()
        assert v["captures"][0]["findings"][0]["check"] == "lidar export"
        j = wait(c, c.post(f"/api/projects/{pid}/run", json={"damage": False, "force": True}).json()["job_id"],
                 timeout=900)
        assert j["status"] == "done", j
        res = c.get(j["outputs"]["result_json"]).json()
        assert res["rooms"]
        assert c.get(j["outputs"]["result_xlsx"]).status_code == 200
        rows = c.get(f"/api/jobs/{j['job_id']}/comparison").json()["rows"]
        assert {r["quantity"] for r in rows} == {"length", "width", "height"}
        assert all(r["computed"] for r in rows if r["quantity"] in ("length", "width"))
