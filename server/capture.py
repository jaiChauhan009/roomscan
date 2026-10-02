"""Project spaces -> capture folders the engine reads (materialise), and the pre-run check
(verify) built on roomscan.capture_quality plus structural checks per space kind."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

import yaml

from server.store import Store, safe_name, safe_relpath

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
LEVELS = {"OK": "ok", "WARN": "warn", "RETAKE": "retake"}
_RANK = {"ok": 0, "warn": 1, "retake": 2}
# what a verify of a zipped Stray export needs: everything but the frames and the clip
_STRAY_BULK = ("depth/", "confidence/", "rgb.mp4")


def worst(levels) -> str:
    return max(levels, key=_RANK.__getitem__, default="ok")


def content_key(p: dict) -> str:
    """Hash of what the engine would see: spaces in walk order, names, sizes, file names and hashes."""
    spaces = [{"kind": s["kind"], "name": s["name"], "sizes": s["sizes"],
               "files": sorted((f["sha256"], f["name"]) for f in s["files"])} for s in p["spaces"]]
    return hashlib.sha256(json.dumps(spaces, sort_keys=True).encode()).hexdigest()


def cache_key(p: dict, damage: bool, engine: str) -> str:
    return hashlib.sha256(f"{content_key(p)}|damage={bool(damage)}|engine={engine}".encode()).hexdigest()


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def _unique(name: str, used: set[str]) -> str:
    stem, ext = os.path.splitext(name)
    out, i = name, 1
    while out.casefold() in used:
        i += 1
        out = f"{stem}_{i}{ext}"
    used.add(out.casefold())
    return out


def plan_runs(p: dict) -> list[dict]:
    """One run per tier for photos (all photo spaces = one walk), one per video / LiDAR space.
    Each run: tier, label (output prefix), space_ids, and for photos the folder of each space."""
    runs: list[dict] = []
    photos = [s for s in p["spaces"] if s["kind"] == "photos"]
    if photos:
        folders = {s["space_id"]: f"{i:02d}_{safe_name(s['name'])}" for i, s in enumerate(photos, 1)}
        runs.append({"tier": "photos", "engine_tier": "photo", "label": "photos",
                     "space_ids": [s["space_id"] for s in photos], "folders": folders})
    for kind, eng in (("video", "video"), ("lidar", "lidar")):
        for i, s in enumerate((s for s in p["spaces"] if s["kind"] == kind), 1):
            runs.append({"tier": kind, "engine_tier": eng, "label": f"{kind}_{i:02d}_{safe_name(s['name'])}",
                         "space_ids": [s["space_id"]], "folders": {}})
    return runs


def _extract_zip(zpath: Path, dest: Path, lite: bool) -> None:
    with zipfile.ZipFile(zpath) as z:
        members = [m for m in z.infolist() if not m.is_dir()]
        need = sum(m.file_size for m in members)
        dest.mkdir(parents=True, exist_ok=True)
        if not lite and need > shutil.disk_usage(dest).free:
            raise ValueError(f"not enough disk space to unzip {zpath.name} ({need / 1e9:.1f} GB)")
        for m in members:
            rel = safe_relpath(m.filename)
            bulky = any(f"/{b}" in f"/{rel}" for b in _STRAY_BULK)
            out = dest / rel
            if lite and bulky:
                # a verify needs only that frames exist: write empty stand-ins for the first few
                if rel.endswith("rgb.mp4") or not out.parent.exists() or len(os.listdir(out.parent)) < 3:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.touch()
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)


def materialise(store: Store, p: dict, work: Path, lite: bool = False) -> list[dict]:
    """Write every run's capture under `work`; return the runs with `path` set (the folder
    or file to give roomscan.pipeline.run) and `files` (space_id -> {stored name: path}).
    `lite`: for verify, a zipped LiDAR export is extracted without its frames."""
    work = Path(work)
    runs = plan_runs(p)
    spaces = {s["space_id"]: s for s in p["spaces"]}
    for r in runs:
        r["files"] = {}
        if r["tier"] == "photos":
            root = work / "photos"
            root.mkdir(parents=True, exist_ok=True)
            meas = {}
            for sid in r["space_ids"]:
                s, folder = spaces[sid], r["folders"][sid]
                used: set[str] = set()
                d = root / folder
                d.mkdir(parents=True, exist_ok=True)
                r["files"][sid] = {}
                for f in s["files"]:
                    dst = d / _unique(Path(safe_relpath(f["name"])).name, used)
                    link_or_copy(store.file_path(p["project_id"], sid, f["sha256"]), dst)
                    r["files"][sid][f["name"]] = dst
                sz = {k: v for k, v in (s.get("sizes") or {}).items() if v is not None}
                if sz:
                    meas[folder] = sz
            if meas:
                (root / "measurements.yaml").write_text(yaml.safe_dump({"rooms": meas}, sort_keys=False),
                                                        encoding="utf-8")
            r["path"] = root
            continue
        sid = r["space_ids"][0]
        s, d = spaces[sid], work / r["label"]
        d.mkdir(parents=True, exist_ok=True)
        r["files"][sid] = {}
        zips = [f for f in s["files"] if f["name"].lower().endswith(".zip")]
        if r["tier"] == "lidar" and len(zips) == 1:
            src = store.file_path(p["project_id"], sid, zips[0]["sha256"])
            try:
                _extract_zip(src, d, lite)
            except (zipfile.BadZipFile, ValueError, OSError, NotImplementedError, RuntimeError) as e:
                r["error"] = f"{zips[0]['name']}: cannot unzip ({e})"
            r["files"][sid][zips[0]["name"]] = src
            r["path"] = d
            continue
        used = set()
        for f in s["files"]:
            rel = safe_relpath(f["name"]) if r["tier"] == "lidar" else _unique(Path(safe_relpath(f["name"])).name, used)
            dst = d / rel
            src = store.file_path(p["project_id"], sid, f["sha256"])
            if lite and r["tier"] == "lidar" and any(f"/{b}" in f"/{rel}" for b in _STRAY_BULK):
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.touch()
            else:
                link_or_copy(src, dst)
            r["files"][sid][f["name"]] = dst
        if r["tier"] == "video":
            vids = [q for q in r["files"][sid].values() if q.suffix.lower() in VIDEO_EXT]
            r["path"] = vids[0] if len(vids) == 1 else d
        else:
            r["path"] = d
    return runs


# ---------------------------------------------------------------- verify

def finding(level: str, check: str, message: str, files: list[str] | None = None) -> dict:
    return {"level": level, "check": check, "message": message, "files": files or []}


def _from_cq(fs, names: list[str]) -> list[dict]:
    out = []
    for f in fs:
        lvl = LEVELS.get(f.level, "warn")
        msg = f.message + (f" -> {f.advice}" if f.advice and lvl != "ok" else "")
        files = [n for n in names if Path(n).name and Path(n).name in f.message]
        out.append(finding(lvl, f.check, msg, files))
    return out


def _guard(fn, check: str, *a, **k) -> list:
    try:
        return fn(*a, **k)
    except Exception as e:  # a check must never break verify: report it instead
        return [_Err(check, f"could not check: {type(e).__name__}: {e}")]


class _Err:
    def __init__(self, check: str, message: str):
        self.check, self.level, self.message, self.advice = check, "RETAKE", message, ""


def verify_space(s: dict, run: dict) -> list[dict]:
    from roomscan import capture_quality as cq

    names = [f["name"] for f in s["files"]]
    paths = run["files"].get(s["space_id"], {})
    F: list[dict] = []
    if not names:
        return [finding("retake", "files", "no files uploaded")]
    ext = {n: Path(n).suffix.lower() for n in names}
    if s["kind"] == "photos":
        photos = [n for n in names if ext[n] in IMAGE_EXT]
        dng = [n for n in names if ext[n] == ".dng"]
        other = [n for n in names if n not in photos and n not in dng]
        if dng:
            F.append(finding("retake", "file types", f"{len(dng)} ProRAW / RAW .dng photo(s): not supported -> "
                             "turn ProRAW off (Settings > Camera > Formats) and take them again, or export "
                             "them as JPEG", dng))
        if other:
            F.append(finding("warn", "file types", f"{len(other)} file(s) that are not photos are ignored "
                             f"(.heic, .jpg, .png expected)", other))
        F.append(finding("retake" if len(photos) < 2 else "ok", "photos in space",
                         f"{len(photos)} photo(s)" + (" -> take at least 2 photos of this room from the doorway"
                                                      if len(photos) < 2 else ""), photos if len(photos) < 2 else []))
        if photos:
            folder = run["folders"][s["space_id"]]
            F += _from_cq(_guard(cq.check_photos, "photo check", {folder: sorted((paths[n] for n in photos), key=lambda q: q.name.casefold())},
                                 look_back=False), photos)
    elif s["kind"] == "video":
        vids = [n for n in names if ext[n] in VIDEO_EXT]
        other = [n for n in names if n not in vids]
        if len(vids) != 1:
            F.append(finding("retake", "video count",
                             f"{len(vids)} videos -> a video space takes exactly one clip (.mov, .mp4); "
                             f"add a space per clip", vids))
        else:
            F.append(finding("ok", "video count", "1 video", []))
            F += _from_cq(_guard(cq.check_video, "video check", paths[vids[0]]), vids)
        if other:
            F.append(finding("warn", "file types", f"{len(other)} file(s) that are not videos are ignored", other))
    else:
        from roomscan.frontends.lidar_stray import find_stray_root
        if run.get("error"):
            return [finding("retake", "lidar export", run["error"])]
        zips = [n for n in names if ext[n] == ".zip"]
        if len(zips) > 1:
            return [finding("retake", "lidar export", f"{len(zips)} zip files -> a LiDAR space takes one Stray "
                            "Scanner export: add a space per scan", zips)]
        try:
            root = find_stray_root(Path(run["path"]))
        except Exception as e:  # several exports
            root, err = None, str(e)
        else:
            err = ""
        if root is None:
            F.append(finding("retake", "lidar export",
                             "no Stray Scanner export found (needs odometry.csv, depth/, rgb.mp4)"
                             + (f": {err}" if err else "") + " -> upload the export as one .zip", zips))
        else:
            F.append(finding("ok", "lidar export", "Stray Scanner export found"))
            F += _from_cq(_guard(cq.check_lidar, "lidar check", root), names)
    return F


def verify_project(store: Store, p: dict, work: Path | None = None, runs: list[dict] | None = None) -> dict:
    """The verify response, on runs already materialised or materialised (lite) into
    `work`, a scratch folder the caller deletes."""
    from roomscan import capture_quality as cq

    project: list[dict] = []
    if not p["spaces"]:
        project.append(finding("retake", "spaces", "the project has no spaces -> add a room (photos), a video "
                               "or a LiDAR scan"))
    if runs is None:
        runs = materialise(store, p, work, lite=True)
    by_space = {sid: r for r in runs for sid in r["space_ids"]}
    out = []
    for s in p["spaces"]:
        fs = verify_space(s, by_space[s["space_id"]])
        out.append({"space_id": s["space_id"], "name": s["name"], "status": worst(f["level"] for f in fs),
                    "findings": fs})
    kinds = sorted({s["kind"] for s in p["spaces"]})
    if len(kinds) > 1:
        project.append(finding("ok", "tiers", f"spaces of {len(kinds)} kinds ({', '.join(kinds)}): each kind is "
                               f"run separately (photos together as one walk; each video and LiDAR scan on its "
                               f"own) and gives its own plan"))
    photo_run = next((r for r in runs if r["tier"] == "photos"), None)
    if photo_run and len(photo_run["space_ids"]) > 1:
        rooms = {}
        for sid in photo_run["space_ids"]:
            fl = [q for q in photo_run["files"][sid].values() if q.suffix.lower() in IMAGE_EXT]
            if fl:
                rooms[photo_run["folders"][sid]] = sorted(fl, key=lambda q: q.name.casefold())
        if len(rooms) > 1:
            fs = _guard(cq.check_photos, "look-back photo", rooms, look_back=True)
            project += _from_cq([f for f in fs if f.check == "look-back photo"], [])
    ok = all(sp["status"] != "retake" for sp in out) and all(f["level"] != "retake" for f in project)
    return {"ok": ok, "spaces": out, "project_findings": project}
