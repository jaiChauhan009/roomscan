"""On-disk state: projects, their spaces and uploaded files, and jobs. Everything is JSON
under DATA_DIR so a restart keeps it.

    DATA_DIR/projects/<pid>/project.json
    DATA_DIR/projects/<pid>/files/<sid>/<sha256>      file bytes, named by content hash
    DATA_DIR/projects/<pid>/files/_capture_video/<sha256>   the whole-home video's file
    DATA_DIR/projects/<pid>/files/_capture_lidar/<sha256>   the whole-home LiDAR scan's file
    DATA_DIR/jobs/<jid>/job.json
    DATA_DIR/jobs/<jid>/out/[<prefix>/]result.json ... outputs of each run
    DATA_DIR/jobs/<jid>/work/                          materialised capture (deleted after the run)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

KINDS = ("photos", "video", "lidar")
CAPTURE_KINDS = ("video", "lidar")  # whole-home captures: a project may have one of each


def capture_id(kind: str) -> str:
    """A whole-home capture's file folder (space ids are 12 hex characters)."""
    return f"_capture_{kind}"
QUANTITIES = ("length", "width", "height")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def write_json(path: Path, data) -> None:
    """Atomic: a crash mid-write leaves the previous file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    # Windows refuses to replace a file another thread has open for reading: retry briefly
    for i in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 49:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.02)


def read_json(path: Path):
    for i in range(50):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except PermissionError:  # being replaced right now (Windows)
            if i == 49:
                raise
            time.sleep(0.02)


def safe_name(s: str, fallback: str = "space") -> str:
    """A file-system safe component: letters, digits, space, - _ . ( ); no leading dots."""
    s = re.sub(r"[^\w\-. ()]+", "_", str(s), flags=re.UNICODE).strip(" ._")
    return s[:80] or fallback


def safe_relpath(name: str) -> str:
    """A client file name, possibly with sub-folders (files of a LiDAR export:
    depth/000001.png), reduced to safe components; '..' and absolute parts are dropped."""
    parts = [p for p in re.split(r"[\\/]+", str(name)) if p not in ("", ".", "..")]
    parts = [safe_name(p, "file") for p in parts]
    return "/".join(parts) or "file"


def engine_version() -> str:
    """roomscan version plus a hash of the engine's source, so a code change invalidates the cache."""
    import roomscan
    root = Path(roomscan.__file__).parent
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes())
    return f"{roomscan.__version__}+{h.hexdigest()[:10]}"


class Store:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)
        self.lock = threading.RLock()
        (self.root / "projects").mkdir(parents=True, exist_ok=True)
        (self.root / "jobs").mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- projects
    def pdir(self, pid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", pid or ""):
            raise KeyError(pid)
        return self.root / "projects" / pid

    def create_project(self) -> dict:
        p = {"project_id": new_id(), "created": time.time(), "spaces": [], "captures": {k: None for k in CAPTURE_KINDS},
             "last_job_id": None, "last_verify": None}
        with self.lock:
            write_json(self.pdir(p["project_id"]) / "project.json", p)
        return p

    def project(self, pid: str) -> dict:
        f = self.pdir(pid) / "project.json"
        if not f.is_file():
            raise KeyError(pid)
        p = read_json(f)
        caps = p.setdefault("captures", {})
        old = p.pop("capture", None)  # before: at most one capture, files under _capture/
        if old and not caps.get(old["kind"]):
            caps[old["kind"]] = old
            src = self.pdir(pid) / "files" / "_capture"
            if src.is_dir() and not (self.pdir(pid) / "files" / capture_id(old["kind"])).exists():
                src.rename(self.pdir(pid) / "files" / capture_id(old["kind"]))
        for k in CAPTURE_KINDS:
            caps.setdefault(k, None)
        return p

    def save_project(self, p: dict) -> None:
        write_json(self.pdir(p["project_id"]) / "project.json", p)

    def space(self, p: dict, sid: str) -> dict:
        for s in p["spaces"]:
            if s["space_id"] == sid:
                return s
        raise KeyError(sid)

    def file_path(self, pid: str, sid: str, sha: str) -> Path:
        return self.pdir(pid) / "files" / sid / sha

    def delete_space_files(self, pid: str, sid: str) -> None:
        shutil.rmtree(self.pdir(pid) / "files" / sid, ignore_errors=True)

    # ---------------------------------------------------------------- jobs
    def jdir(self, jid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", jid or ""):
            raise KeyError(jid)
        return self.root / "jobs" / jid

    def job(self, jid: str) -> dict:
        f = self.jdir(jid) / "job.json"
        if not f.is_file():
            raise KeyError(jid)
        return read_json(f)

    def save_job(self, j: dict) -> None:
        write_json(self.jdir(j["job_id"]) / "job.json", j)

    def jobs(self) -> list[dict]:
        out = []
        for d in (self.root / "jobs").iterdir():
            try:
                out.append(read_json(d / "job.json"))
            except (OSError, ValueError):
                continue
        return out

    def find_cached(self, key: str) -> dict | None:
        """The newest done job with this cache key, else a queued / running one."""
        js = [j for j in self.jobs() if j.get("cache_key") == key]
        for status in ("done", "running", "queued"):
            hit = sorted((j for j in js if j["status"] == status), key=lambda j: j.get("created", 0))
            if hit:
                return hit[-1]
        return None

    def cleanup(self, ttl_days: float) -> int:
        """Delete finished jobs older than the TTL; return how many."""
        cutoff, n = time.time() - ttl_days * 86400, 0
        for d in list((self.root / "jobs").iterdir()):
            try:
                j = read_json(d / "job.json")
            except (OSError, ValueError):
                if d.is_dir() and d.stat().st_mtime < cutoff:  # half-written job
                    shutil.rmtree(d, ignore_errors=True)
                continue
            if j.get("status") in ("done", "failed") and (j.get("finished") or j.get("created", 0)) < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        return n
