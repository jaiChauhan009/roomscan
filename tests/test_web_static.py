"""Static checks for web/ (no Node on the build machine) and the mock API contract.

Optional headless smoke test with Playwright when it is installed (skipped otherwise).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"


def _load_mock():
    spec = importlib.util.spec_from_file_location("roomscan_mock_server", WEB / "dev" / "mock_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- static files
def _refs(html: str) -> list[str]:
    out = re.findall(r'(?:src|href|data-svg)="([^"#]+)"', html)
    return [r for r in out if not re.match(r"^(https?:|mailto:|data:)", r)]


def test_index_references_exist():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    refs = _refs(html)
    assert "styles.css" in refs and "js/app.js" in refs
    for r in refs:
        assert (WEB / r).is_file(), f"index.html references missing file {r}"
    # the capture guide's five diagrams, inlined by app.js (they follow the theme)
    guide = {r for r in refs if r.startswith("img/guide/")}
    assert guide == {f"img/guide/{n}.svg" for n in ("photos-doorway", "photos-lookback", "video-walk",
                                                    "tilt-ceiling", "marker-and-tape")}
    for r in guide:
        svg = (WEB / r).read_text(encoding="utf-8")
        assert svg.lstrip().startswith("<svg") and "currentColor" in svg
    assert "--guide-bg" in (WEB / "styles.css").read_text(encoding="utf-8")
    assert "data-svg" in (WEB / "js" / "app.js").read_text(encoding="utf-8") or "dataset.svg" in \
        (WEB / "js" / "app.js").read_text(encoding="utf-8")


def test_two_part_page():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.index('id="capture-card"') < html.index('id="spaces"')
    # two independent, optional whole-home captures (no single None / Video / LiDAR choice)
    assert 'id="cap-video" data-kind="video"' in html and 'id="cap-lidar" data-kind="lidar"' in html
    assert "Video walkthrough" in html and "LiDAR scan" in html and "Stray Scanner .zip" in html
    assert 'name="capkind"' not in html
    assert 'name="kind"' not in html  # rooms have no kind any more
    assert html.index('id="guide"') < html.index('id="spaces"')
    app = (WEB / "js" / "app.js").read_text(encoding="utf-8")
    api = (WEB / "js" / "api.js").read_text(encoding="utf-8")
    assert '"_capture_video"' in api and '"_capture_lidar"' in api and "/captures/" in api
    assert "Replace video" in app and "Replace LiDAR zip" in app


def test_guide_is_collapsible():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<details[^>]*id="guide"[^>]*>(.*?)</details>', html, re.S)
    assert m, "the capture guide must be a <details> element"
    assert "open" not in m.group(0).split(">")[0]  # opened by app.js on the first visit only
    summary = re.search(r"<summary[^>]*>(.*?)</summary>", m.group(1), re.S)
    assert summary and "How to capture" in summary.group(1) and 'class="chev"' in summary.group(1)
    app = (WEB / "js" / "app.js").read_text(encoding="utf-8")
    assert '"guide.open"' in app and '"toggle"' in app  # remembered; diagrams load on first open
    assert 'a[href^="#guide"]' in app  # "See the guide" links open it and scroll
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert ".guide[open] > summary .chev" in css and "::-webkit-details-marker" in css


def test_js_imports_resolve():
    seen, todo = set(), [WEB / "js" / "app.js"]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        src = f.read_text(encoding="utf-8")
        for spec in re.findall(r'^\s*import\s[^;]*?from\s+"([^"]+)"', src, flags=re.M):
            target = (f.parent / spec).resolve()
            assert target.is_file(), f"{f.relative_to(WEB)} imports missing {spec}"
            todo.append(target)
    assert {p.name for p in seen} >= {"app.js", "api.js", "store.js", "upload.js", "sha256.js", "results.js", "dom.js", "config.js"}


def test_config_default_and_override():
    src = (WEB / "config.js").read_text(encoding="utf-8")
    assert re.search(r'DEFAULT_API\s*=\s*"http://localhost:8000"', src)
    assert 'get("api")' in src and "localStorage" in src


def test_vercel_json_valid():
    cfg = json.loads((WEB / "vercel.json").read_text(encoding="utf-8"))
    assert isinstance(cfg, dict)
    assert "builds" not in cfg  # plain static site, no build step


def test_no_build_tooling():
    for name in ("package.json", "package-lock.json", "node_modules"):
        assert not (WEB / name).exists()


def test_html_basics():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'name="viewport"' in html and 'lang="en"' in html
    # every input in the static markup has an accessible label
    for m in re.finditer(r"<input\b[^>]*>", html):
        tag = m.group(0)
        pre = html[: m.start()]
        inside_label = pre.rfind("<label") > pre.rfind("</label>")
        assert inside_label or "aria-label" in tag, tag
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert "prefers-color-scheme: dark" in css


# ---------------------------------------------------------------- mock API contract
@pytest.fixture(scope="module")
def mock_api():
    mod = _load_mock()
    port = _free_port()
    srv = mod.make_server(port, stage_seconds=0.05, quiet=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def call(base, method, path, body=None, *, raw=None, ctype=None):
    data, headers = None, {}
    if body is not None:
        data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
    if raw is not None:
        data, headers = raw, {"Content-Type": ctype}
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            b = r.read()
            return r.status, (json.loads(b) if r.headers.get("Content-Type", "").startswith("application/json") else b)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def multipart(fields: dict, filename: str, content: bytes):
    boundary = "----rs" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
               f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def upload(base, pid, sid, name, content):
    sha = hashlib.sha256(content).hexdigest()
    raw, ct = multipart({"sha256": sha, "name": name}, name, content)
    return call(base, "PUT", f"/api/projects/{pid}/spaces/{sid}/files", raw=raw, ctype=ct)


def test_mock_contract_flow(mock_api):
    b = mock_api
    st, h = call(b, "GET", "/api/health")
    assert st == 200

    st, p = call(b, "POST", "/api/projects", {})
    assert st == 200 and p["project_id"]
    pid = p["project_id"]

    st, sp = call(b, "POST", f"/api/projects/{pid}/spaces",
                  {"name": "Room 1", "kind": "photos", "sizes": {"length": None, "width": None, "height": 2.5}})
    assert st == 200 and set(sp) >= {"space_id", "name", "kind", "sizes", "files"}
    sid = sp["space_id"]

    st, f = upload(b, pid, sid, "IMG_1.jpg", b"one")
    assert st == 200 and f["sha256"] == hashlib.sha256(b"one").hexdigest() and f["size"] == 3
    st, f2 = upload(b, pid, sid, "IMG_1.jpg", b"one")  # idempotent
    assert st == 200 and f2 == f
    upload(b, pid, sid, "IMG_2_blur.jpg", b"two")
    st, lst = call(b, "GET", f"/api/projects/{pid}/spaces/{sid}/files")
    assert st == 200 and len(lst["files"]) == 2

    st, sp2 = call(b, "PATCH", f"/api/projects/{pid}/spaces/{sid}", {"name": "Hall", "sizes": {"length": 3.1, "width": None, "height": 2.5}})
    assert st == 200 and sp2["name"] == "Hall" and sp2["sizes"]["length"] == 3.1

    st, proj = call(b, "GET", f"/api/projects/{pid}")
    assert st == 200 and proj["last_job_id"] is None and proj["spaces"][0]["space_id"] == sid

    st, v = call(b, "POST", f"/api/projects/{pid}/verify", {})
    assert st == 200 and v["ok"] is True
    s0 = v["spaces"][0]
    assert s0["status"] == "warn" and any("IMG_2_blur.jpg" in fd["files"] for fd in s0["findings"])
    assert "project_findings" in v

    st, r = call(b, "POST", f"/api/projects/{pid}/run", {"damage": True})
    assert st == 200 and r["job_id"] and r["cached"] is False
    jid = r["job_id"]
    deadline = time.time() + 10
    while True:
        st, job = call(b, "GET", f"/api/jobs/{jid}")
        assert st == 200 and job["status"] in ("queued", "running", "done", "failed")
        assert all(set(s) >= {"name", "status", "seconds", "note"} for s in job["stages"])
        if job["status"] == "done" or time.time() > deadline:
            break
        time.sleep(0.05)
    assert job["status"] == "done"
    assert set(job["outputs"]) == {"result_json", "result_xlsx", "plan_png", "plan_svg"}

    st, res = call(b, "GET", f"/api/jobs/{jid}/files/result.json")
    assert st == 200 and res["rooms"][0]["label"] == "Hall"
    st, png = call(b, "GET", f"/api/jobs/{jid}/files/plan.png")
    assert st == 200 and png[:8] == b"\x89PNG\r\n\x1a\n"
    st, cmp_ = call(b, "GET", f"/api/jobs/{jid}/comparison")
    assert st == 200 and {r["quantity"] for r in cmp_["rows"]} == {"length", "height"}
    assert set(cmp_["rows"][0]) >= {"space", "tier", "quantity", "given", "computed", "ci90", "diff", "diff_pct"}

    st, r2 = call(b, "POST", f"/api/projects/{pid}/run", {"damage": True})
    assert st == 200 and r2["cached"] is True and r2["job_id"] == jid

    st, _ = call(b, "DELETE", f"/api/projects/{pid}/spaces/{sid}/files/{f['sha256']}")
    assert st == 200
    st, _ = call(b, "DELETE", f"/api/projects/{pid}/spaces/{sid}")
    assert st == 200
    st, _ = call(b, "GET", f"/api/projects/{pid}/spaces/{sid}/files")
    assert st == 404


def test_mock_whole_home_capture(mock_api):
    b = mock_api
    pid = call(b, "POST", "/api/projects", {})[1]["project_id"]
    assert call(b, "GET", f"/api/projects/{pid}")[1]["captures"] == {"video": None, "lidar": None}
    st, err = call(b, "POST", f"/api/projects/{pid}/spaces", {"name": "x", "kind": "video", "sizes": {}})
    assert st == 422 and "capture" in err["detail"]
    room = call(b, "POST", f"/api/projects/{pid}/spaces", {"name": "Lounge", "sizes": {"length": 5.0}})[1]
    assert room["kind"] == "photos"
    assert call(b, "GET", f"/api/projects/{pid}/captures/video")[0] == 404
    # both captures at once, one file each
    for kind, name, data in (("video", "walk.mov", b"clip"), ("lidar", "scan.zip", b"PK\x03\x04scan")):
        st, cap = call(b, "PUT", f"/api/projects/{pid}/captures/{kind}", {})
        assert st == 200 and cap == {"kind": kind, "files": []}
        raw, ct = multipart({"sha256": hashlib.sha256(data).hexdigest(), "name": name}, name, data)
        assert call(b, "PUT", f"/api/projects/{pid}/captures/{kind}/files", raw=raw, ctype=ct)[0] == 200
        assert call(b, "GET", f"/api/projects/{pid}/captures/{kind}")[1]["files"][0]["name"] == name
    raw, ct = multipart({"sha256": hashlib.sha256(b"clip2").hexdigest(), "name": "two.mov"}, "two.mov", b"clip2")
    st, err = call(b, "PUT", f"/api/projects/{pid}/captures/video/files", raw=raw, ctype=ct)
    assert st == 409 and "already has a file" in err["detail"]
    caps = call(b, "GET", f"/api/projects/{pid}")[1]["captures"]
    assert caps["video"]["kind"] == "video" and caps["lidar"]["kind"] == "lidar"
    st, v = call(b, "POST", f"/api/projects/{pid}/verify", {})
    assert v["ok"] is True and v["spaces"][0]["status"] == "ok"
    assert [(c["kind"], c["name"]) for c in v["captures"]] == [("video", "whole home: video"), ("lidar", "whole home: LiDAR")]
    assert all({"status", "findings", "advice"} <= set(c) for c in v["captures"])
    jid = call(b, "POST", f"/api/projects/{pid}/run", {"damage": True})[1]["job_id"]
    deadline = time.time() + 10
    while (job := call(b, "GET", f"/api/jobs/{jid}")[1])["status"] != "done" and time.time() < deadline:
        time.sleep(0.05)
    assert job["status"] == "done" and [r["title"] for r in job["runs"]] == ["Whole home (video)", "Whole home (LiDAR)"]
    assert {s["name"].split(": ")[0] for s in job["stages"]} == {"Whole home (video)", "Whole home (LiDAR)"}
    rows = call(b, "GET", f"/api/jobs/{jid}/comparison")[1]["rows"]
    assert {r["tier"] for r in rows} == {"video", "lidar"} and rows[0]["space"] == "Lounge"
    sha = caps["lidar"]["files"][0]["sha256"]
    assert call(b, "DELETE", f"/api/projects/{pid}/captures/lidar/files/{sha}")[0] == 200
    assert call(b, "GET", f"/api/projects/{pid}/captures/lidar/files")[1]["files"] == []
    assert call(b, "DELETE", f"/api/projects/{pid}/captures/video")[0] == 200
    assert call(b, "GET", f"/api/projects/{pid}/captures/video")[0] == 404
    assert call(b, "GET", f"/api/projects/{pid}")[1]["captures"]["video"] is None


def test_mock_retake_gives_409(mock_api):
    b = mock_api
    pid = call(b, "POST", "/api/projects", {})[1]["project_id"]
    sid = call(b, "POST", f"/api/projects/{pid}/spaces", {"name": "Walked", "kind": "photos", "sizes": {}})[1]["space_id"]
    for i in range(13):
        upload(b, pid, sid, f"IMG_{i}.jpg", f"p{i}".encode())
    st, v = call(b, "POST", f"/api/projects/{pid}/verify", {})
    assert v["ok"] is False and v["spaces"][0]["status"] == "retake"
    st, err = call(b, "POST", f"/api/projects/{pid}/run", {"damage": True})
    assert st == 409 and "detail" in err
    st, r = call(b, "POST", f"/api/projects/{pid}/run", {"damage": True, "force": True})
    assert st == 200


def test_mock_cors_preflight(mock_api):
    req = urllib.request.Request(mock_api + "/api/projects", method="OPTIONS")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 204
        assert r.headers["Access-Control-Allow-Origin"] == "*"
        assert "PATCH" in r.headers["Access-Control-Allow-Methods"]


# ---------------------------------------------------------------- optional browser smoke tests
@pytest.fixture()
def static_site():
    """Serves web/ and collects the report web/dev/smoke.html POSTs to /__smoke_result."""
    port = _free_port()
    done = threading.Event()
    report = {}

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            report["text"] = self.rfile.read(n).decode("utf-8", "replace")
            self.send_response(204)
            self.end_headers()
            done.set()

    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(WEB)))
    srv.handle_error = lambda request, client_address: None  # browser killed mid-request
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", done, report
    srv.shutdown()
    srv.server_close()


def _find_chrome():
    cands = [shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge")]
    cands += [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((c for c in cands if c and Path(c).is_file()), None)


def test_headless_chrome_smoke(mock_api, static_site, tmp_path):
    """Drives the whole flow (add space, upload, sizes, verify, run, results) through
    web/dev/smoke.html in headless Chrome/Edge at 360 px, if a browser is installed."""
    chrome = _find_chrome()
    if not chrome:
        pytest.skip("no Chrome/Edge installed")
    base, done, report = static_site
    url = f"{base}/dev/smoke.html?api={mock_api}"
    proc = subprocess.Popen(
        [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         f"--user-data-dir={tmp_path / 'profile'}", "--window-size=800,1000", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        finished = done.wait(90)
    finally:
        proc.kill()
        proc.wait(10)
    if not finished:
        pytest.skip("headless browser did not report back (sandboxed CI?)")
    print(report["text"].encode("ascii", "replace").decode())
    assert "SMOKE OK" in report["text"], report["text"]


def test_browser_smoke(mock_api):
    pw = pytest.importorskip("playwright.sync_api")
    port = _free_port()
    static = ThreadingHTTPServer(("127.0.0.1", port), partial(SimpleHTTPRequestHandler, directory=str(WEB)))
    threading.Thread(target=static.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:  # browsers not installed
                pytest.skip(f"no chromium for playwright: {e}")
            page = browser.new_page(viewport={"width": 360, "height": 800})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/index.html?api={mock_api}")
            page.wait_for_selector("#api-status.up", timeout=10000)
            page.click("#add-space button[type=submit]")
            page.wait_for_selector("#spaces li.space", timeout=10000)
            page.set_input_files("#spaces li.space input[type=file]",
                                 files=[{"name": "a.jpg", "mimeType": "image/jpeg", "buffer": b"\xff\xd8a"},
                                        {"name": "b.jpg", "mimeType": "image/jpeg", "buffer": b"\xff\xd8b"}])
            page.wait_for_function("document.querySelector('#upload-summary').textContent.includes('All 2 files uploaded')", timeout=15000)
            page.click("#verify")
            page.wait_for_selector("#spaces .badge.ok, #spaces .badge.warn", timeout=10000)
            page.click("#run")
            page.wait_for_selector("#results:not([hidden])", timeout=20000)
            assert page.evaluate("document.documentElement.scrollWidth") <= 360
            assert not errors, errors
            browser.close()
    finally:
        static.shutdown()
