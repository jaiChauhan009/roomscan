"""FastAPI app: `uvicorn server.app:app`. All routes under /api; see server/README.md.

Environment:
    DATA_DIR                  projects, uploads, jobs (default ./server_data)
    ROOMSCAN_CORS             allowed origins, comma separated (default *)
    ROOMSCAN_MAX_FILE_MB      per-file upload limit (default 2048)
    ROOMSCAN_RESULT_TTL_DAYS  finished jobs older than this are deleted at startup (default 7)
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from server.capture import VIDEO_EXT, cache_key, content_key, has_input, plan_runs, verify_project
from server.jobs import OUTPUT_FILES, Worker, comparison, new_job, public_job
from server.store import CAPTURE_KINDS, Store, capture_id, engine_version, new_id, safe_relpath


class Sizes(BaseModel):
    length: float | None = Field(default=None, gt=0, lt=1000)
    width: float | None = Field(default=None, gt=0, lt=1000)
    height: float | None = Field(default=None, gt=0, lt=100)


class SpaceIn(BaseModel):
    """A room: name, optional sizes, optional photos. Video and LiDAR are whole-home captures
    (PUT /api/projects/{pid}/captures/{kind}), not rooms."""
    name: str = Field(min_length=1, max_length=120)
    kind: str = "photos"
    sizes: Sizes = Sizes()


CaptureKind = Literal["video", "lidar"]


class SpacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sizes: Sizes | None = None


class OrderIn(BaseModel):
    space_ids: list[str]


class RunIn(BaseModel):
    damage: bool = True
    force: bool = False
    email: str | None = None  # send the results here when the job finishes (if email is configured)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def create_app(data_dir: str | Path | None = None, engine_cache: bool = True) -> FastAPI:
    """`engine_cache`: keep the engine's .cache under DATA_DIR/engine_cache while running."""
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        root = Path(data_dir or os.environ.get("DATA_DIR", "server_data")).resolve()
        store = Store(root)
        store.cleanup(_env_float("ROOMSCAN_RESULT_TTL_DAYS", 7))
        worker = Worker(store, root / "engine_cache" if engine_cache else None)
        worker.start()
        state.update(store=store, worker=worker, engine=engine_version())
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(title="roomscan", version=_version(), lifespan=lifespan)
    origins = [o.strip() for o in os.environ.get("ROOMSCAN_CORS", "*").split(",") if o.strip()] or ["*"]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"],
                       allow_credentials=origins != ["*"])

    def store() -> Store:
        return state["store"]

    def project_or_404(pid: str) -> dict:
        try:
            return store().project(pid)
        except KeyError:
            raise HTTPException(404, f"project {pid} not found")

    def space_or_404(p: dict, sid: str) -> dict:
        try:
            return store().space(p, sid)
        except KeyError:
            raise HTTPException(404, f"space {sid} not found")

    def job_or_404(jid: str) -> dict:
        try:
            return store().job(jid)
        except KeyError:
            raise HTTPException(404, f"job {jid} not found (finished jobs are kept "
                                     f"{_env_float('ROOMSCAN_RESULT_TTL_DAYS', 7):g} days)")

    def public_space(s: dict) -> dict:
        return {k: s[k] for k in ("space_id", "name", "kind", "sizes", "files")}

    def public_capture(c: dict | None) -> dict | None:
        return {"kind": c["kind"], "files": c["files"]} if c else None

    def public_project(p: dict) -> dict:
        caps = p.get("captures") or {}
        return {"project_id": p["project_id"], "spaces": [public_space(s) for s in p["spaces"]],
                "captures": {k: public_capture(caps.get(k)) for k in CAPTURE_KINDS},
                "last_job_id": p.get("last_job_id")}

    def capture_or_404(p: dict, kind: str) -> dict:
        c = (p.get("captures") or {}).get(kind)
        if not c:
            raise HTTPException(404, f"the project has no whole-home {kind} capture "
                                     f"(PUT /api/projects/{{pid}}/captures/{kind} first)")
        return c

    def is_main_file(kind: str, name: str) -> bool:
        """The one file a whole-home capture holds: the clip, or the zipped LiDAR export."""
        ext = Path(name).suffix.lower()
        return ext in VIDEO_EXT if kind == "video" else ext == ".zip"

    def second_file_error(c: dict, name: str, sha: str) -> None:
        if not is_main_file(c["kind"], name):
            return
        other = next((f for f in c["files"] if f["sha256"] != sha and is_main_file(c["kind"], f["name"])), None)
        if other:
            what = "video" if c["kind"] == "video" else "LiDAR scan (.zip)"
            raise HTTPException(409, f"the whole-home {c['kind']} capture already has a {what}: {other['name']}. "
                                     f"It takes exactly one: delete that file first (DELETE .../captures/"
                                     f"{c['kind']}/files/{other['sha256']}) to replace it")

    def receive(pid: str, sid: str, file: UploadFile, sha256: str, name: str | None, holder, check=None) -> dict:
        """Store an upload by content hash (idempotent); `holder(p)` is the space or capture dict
        whose `files` list gets the record; `check(holder, fname, sha)` may refuse it."""
        sha = sha256.strip().lower()
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise HTTPException(422, "sha256 must be 64 hex characters")
        fname = safe_relpath(name or file.filename or "file")
        h0 = holder(project_or_404(pid))
        existing = next((f for f in h0["files"] if f["sha256"] == sha), None)
        dest = store().file_path(pid, sid, sha)
        if existing and dest.is_file():
            return existing
        if check:
            check(h0, fname, sha)
        limit = int(_env_float("ROOMSCAN_MAX_FILE_MB", 2048) * 1024 * 1024)
        dest.parent.mkdir(parents=True, exist_ok=True)
        h, size = hashlib.sha256(), 0
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := file.file.read(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"{fname} is larger than the {limit // 2**20} MB limit")
                    h.update(chunk)
                    out.write(chunk)
            if h.hexdigest() != sha:
                raise HTTPException(400, f"{fname}: sha256 mismatch (received {h.hexdigest()}): the upload "
                                         f"was damaged or the hash is wrong; upload it again")
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        rec = {"name": fname, "sha256": sha, "size": size}
        with store().lock:
            p = project_or_404(pid)
            hd = holder(p)
            existing = next((f for f in hd["files"] if f["sha256"] == sha), None)
            if existing:
                return existing
            if check:
                try:
                    check(hd, fname, sha)  # a second file may have landed meanwhile
                except HTTPException:
                    if not any(f["sha256"] == sha for f in hd["files"]):
                        dest.unlink(missing_ok=True)
                    raise
            hd["files"].append(rec)
            store().save_project(p)
        return rec

    # ---------------------------------------------------------------- health
    @app.get("/api/health")
    def health():
        from server import notify
        return {"ok": True, "version": state.get("engine") or _version(), "email": notify.enabled()}

    # ---------------------------------------------------------------- projects / spaces
    @app.post("/api/projects")
    def create_project():
        return {"project_id": store().create_project()["project_id"]}

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return public_project(project_or_404(pid))

    @app.post("/api/projects/{pid}/spaces")
    def add_space(pid: str, body: SpaceIn):
        if body.kind != "photos":
            raise HTTPException(422, f"a space is a room (kind \"photos\"); {body.kind} is a whole-home capture: "
                                     f"PUT /api/projects/{{pid}}/captures/{body.kind}"
                                     if body.kind in ("video", "lidar") else
                                     f"kind must be \"photos\" (got {body.kind!r})")
        with store().lock:
            p = project_or_404(pid)
            s = {"space_id": new_id(), "name": body.name.strip(), "kind": "photos",
                 "sizes": body.sizes.model_dump(), "files": []}
            p["spaces"].append(s)
            store().save_project(p)
        return public_space(s)

    @app.put("/api/projects/{pid}/order")
    def set_order(pid: str, body: OrderIn):
        """Walk order of the spaces (photo folders are numbered in it)."""
        with store().lock:
            p = project_or_404(pid)
            by_id = {s["space_id"]: s for s in p["spaces"]}
            if len(body.space_ids) != len(by_id) or set(body.space_ids) != set(by_id):
                raise HTTPException(422, "space_ids must list every space of the project exactly once "
                                         f"(has: {', '.join(by_id)})")
            p["spaces"] = [by_id[sid] for sid in body.space_ids]
            store().save_project(p)
        return public_project(p)

    @app.patch("/api/projects/{pid}/spaces/{sid}")
    def patch_space(pid: str, sid: str, body: SpacePatch):
        with store().lock:
            p = project_or_404(pid)
            s = space_or_404(p, sid)
            if body.name is not None:
                s["name"] = body.name.strip()
            if body.sizes is not None:
                s["sizes"] = {**s["sizes"], **body.sizes.model_dump(exclude_unset=True)}
            store().save_project(p)
        return public_space(s)

    @app.delete("/api/projects/{pid}/spaces/{sid}")
    def delete_space(pid: str, sid: str):
        with store().lock:
            p = project_or_404(pid)
            space_or_404(p, sid)
            p["spaces"] = [s for s in p["spaces"] if s["space_id"] != sid]
            store().save_project(p)
            store().delete_space_files(pid, sid)
        return {"ok": True}

    @app.get("/api/projects/{pid}/spaces/{sid}/files")
    def list_files(pid: str, sid: str):
        return {"files": space_or_404(project_or_404(pid), sid)["files"]}

    @app.delete("/api/projects/{pid}/spaces/{sid}/files/{sha}")
    def delete_file(pid: str, sid: str, sha: str):
        sha = sha.lower()
        with store().lock:
            p = project_or_404(pid)
            s = space_or_404(p, sid)
            if not any(f["sha256"] == sha for f in s["files"]):
                raise HTTPException(404, f"file {sha} not in space {sid}")
            s["files"] = [f for f in s["files"] if f["sha256"] != sha]
            store().save_project(p)
            store().file_path(pid, sid, sha).unlink(missing_ok=True)
        return {"ok": True}

    @app.put("/api/projects/{pid}/spaces/{sid}/files")
    def upload(pid: str, sid: str, file: UploadFile = File(...), sha256: str = Form(...),
               name: str = Form(None)):
        space_or_404(project_or_404(pid), sid)
        return receive(pid, sid, file, sha256, name, lambda p: space_or_404(p, sid))

    # ---------------------------------------------------------------- whole-home captures
    @app.put("/api/projects/{pid}/captures/{kind}")
    def put_capture(pid: str, kind: CaptureKind, body: dict | None = None):
        """Create the whole-home video or LiDAR capture if absent (idempotent); a project may have both."""
        with store().lock:
            p = project_or_404(pid)
            caps = p.setdefault("captures", {})
            if not caps.get(kind):
                caps[kind] = {"kind": kind, "files": []}
                store().save_project(p)
                store().delete_space_files(pid, capture_id(kind))
        return public_capture(caps[kind])

    @app.get("/api/projects/{pid}/captures/{kind}")
    def get_capture(pid: str, kind: CaptureKind):
        return public_capture(capture_or_404(project_or_404(pid), kind))

    @app.delete("/api/projects/{pid}/captures/{kind}")
    def delete_capture(pid: str, kind: CaptureKind):
        with store().lock:
            p = project_or_404(pid)
            capture_or_404(p, kind)
            p["captures"][kind] = None
            store().save_project(p)
            store().delete_space_files(pid, capture_id(kind))
        return {"ok": True}

    @app.get("/api/projects/{pid}/captures/{kind}/files")
    def list_capture_files(pid: str, kind: CaptureKind):
        return {"files": capture_or_404(project_or_404(pid), kind)["files"]}

    @app.put("/api/projects/{pid}/captures/{kind}/files")
    def upload_capture(pid: str, kind: CaptureKind, file: UploadFile = File(...), sha256: str = Form(...),
                       name: str = Form(None)):
        capture_or_404(project_or_404(pid), kind)
        return receive(pid, capture_id(kind), file, sha256, name, lambda p: capture_or_404(p, kind),
                       second_file_error)

    @app.delete("/api/projects/{pid}/captures/{kind}/files/{sha}")
    def delete_capture_file(pid: str, kind: CaptureKind, sha: str):
        sha = sha.lower()
        with store().lock:
            p = project_or_404(pid)
            c = capture_or_404(p, kind)
            if not any(f["sha256"] == sha for f in c["files"]):
                raise HTTPException(404, f"file {sha} not in the whole-home {kind} capture")
            c["files"] = [f for f in c["files"] if f["sha256"] != sha]
            store().save_project(p)
            store().file_path(pid, capture_id(kind), sha).unlink(missing_ok=True)
        return {"ok": True}

    # ---------------------------------------------------------------- verify / run
    @app.post("/api/projects/{pid}/verify")
    def verify(pid: str):
        p = project_or_404(pid)
        work = Path(tempfile.mkdtemp(prefix="verify_", dir=store().root))
        try:
            res = verify_project(store(), p, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        with store().lock:
            q = project_or_404(pid)
            q["last_verify"] = {"content_key": content_key(p), "at": time.time(), "result": res}
            store().save_project(q)
        return res

    @app.post("/api/projects/{pid}/run")
    def run(pid: str, body: RunIn | None = None):
        body = body or RunIn()
        from server import notify
        email = (body.email or "").strip() or None
        if email and not notify.valid_email(email):
            raise HTTPException(422, f"'{email}' is not an email address")
        with store().lock:
            p = project_or_404(pid)
            if not has_input(p):
                raise HTTPException(409, "nothing to compute: add photos to a room, or one whole-home video / "
                                         "LiDAR scan")
            last = p.get("last_verify")
            fresh = last and last.get("content_key") == content_key(p)
            if fresh and not body.force and not last["result"]["ok"]:
                raise HTTPException(409, "the last verify asked for a retake: fix the capture and verify again, "
                                         "or run with {\"force\": true}")
            key = cache_key(p, body.damage, state["engine"])
            hit = store().find_cached(key)
            if hit:
                p["last_job_id"] = hit["job_id"]
                store().save_project(p)
                if email:
                    if hit["status"] == "done":  # already finished: email it now
                        notify.notify_async({**hit, "notify_email": email}, store().jdir(hit["job_id"]) / "out")
                    else:  # still running: email when it finishes
                        hit["notify_email"] = email
                        store().save_job(hit)
                return {"job_id": hit["job_id"], "cached": hit["status"] == "done"}
            job = new_job(p, plan_runs(p), body.damage, body.force, key, state["engine"])
            job["notify_email"] = email
            p["last_job_id"] = job["job_id"]
            store().save_project(p)
            state["worker"].submit(job)
        return {"job_id": job["job_id"], "cached": False}

    # ---------------------------------------------------------------- jobs
    @app.get("/api/jobs/{jid}")
    def get_job(jid: str):
        return public_job(job_or_404(jid))

    @app.get("/api/jobs/{jid}/files/{name:path}")
    def job_file(jid: str, name: str):
        j = job_or_404(jid)
        allowed = {f"{r['prefix']}{fn}" for r in j["runs"] for fn in (*OUTPUT_FILES.values(), "stages.json")}
        if name not in allowed:
            raise HTTPException(404, f"{name}: not an output of this job (one of: {', '.join(sorted(allowed))})")
        f = store().jdir(jid) / "out" / name
        if not f.is_file():
            raise HTTPException(404, f"{name} not available (job {j['status']})")
        return FileResponse(f, filename=f.name)

    @app.get("/api/jobs/{jid}/comparison")
    def get_comparison(jid: str):
        j = job_or_404(jid)
        if j["status"] != "done":
            raise HTTPException(409, f"job is {j['status']}")
        return comparison(store(), j)

    return app


def _version() -> str:
    try:
        import roomscan
        return roomscan.__version__
    except Exception:
        return "unknown"


app = create_app()
