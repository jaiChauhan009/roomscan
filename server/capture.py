"""Project -> capture folders the engine reads (materialise), and the pre-run check (verify)
built on roomscan.capture_quality plus structural checks.

A project has rooms (spaces of kind "photos": a name, optional sizes, optional photos) and at
most one whole-home capture (one video clip or one LiDAR scan of the whole home). Runs: the
rooms with photos together as one photo walk, and the whole-home capture as a second run that
gets every room's sizes as unnamed known sizes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

import yaml

from server.store import CAPTURE_ID, Store, safe_name, safe_relpath

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
LEVELS = {"OK": "ok", "WARN": "warn", "RETAKE": "retake"}
_RANK = {"ok": 0, "warn": 1, "retake": 2}
# what a verify of a zipped Stray export needs: everything but the frames and the clip
_STRAY_BULK = ("depth/", "confidence/", "rgb.mp4")
TITLES = {"photos": "Rooms (photos)", "video": "Whole home (video)", "lidar": "Whole home (LiDAR)"}
CAPTURE_NAMES = {"video": "whole home: video", "lidar": "whole home: LiDAR"}
RETAKE_ADVICE = {
    "video": "Record the whole home again as one clip: Video mode at 1x, walk slowly through every room "
             "(one step every two seconds), tilt up to the ceiling once in each room, keep some floor in "
             "view and never turn on the spot. Then delete the old clip and upload the new one.",
    "lidar": "Scan the whole home again as one Stray Scanner recording: walk slowly round every room, tilt "
             "up to the ceiling and down to the floor once per room; then Files app > Stray Scanner > press "
             "and hold the newest folder > Compress, delete the old .zip here and upload the new one.",
}


def worst(levels) -> str:
    return max(levels, key=_RANK.__getitem__, default="ok")


def content_key(p: dict) -> str:
    """Hash of what the engine would see: rooms in walk order, names, sizes, file names and
    hashes, and the whole-home capture."""
    def files(s):
        return sorted((f["sha256"], f["name"]) for f in s["files"])

    spaces = [{"kind": s["kind"], "name": s["name"], "sizes": s["sizes"], "files": files(s)} for s in p["spaces"]]
    cap = p.get("capture")
    capture = {"kind": cap["kind"], "files": files(cap)} if cap else None
    return hashlib.sha256(json.dumps({"spaces": spaces, "capture": capture}, sort_keys=True).encode()).hexdigest()


def cache_key(p: dict, damage: bool, engine: str) -> str:
    return hashlib.sha256(f"{content_key(p)}|damage={bool(damage)}|engine={engine}".encode()).hexdigest()


def given_sizes(s: dict) -> dict:
    return {k: v for k, v in (s.get("sizes") or {}).items() if v is not None}


def room_measurements(spaces: list[dict]) -> dict | None:
    """Known sizes for the whole-home run: every room's sizes as an unnamed list (the engine's
    rooms are room_1.., matched on aspect and size; LiDAR only compares). None without sizes."""
    rooms = [r for r in (given_sizes(s) for s in spaces) if r]
    return {"rooms": rooms} if rooms else None


def has_input(p: dict) -> bool:
    """Something to compute: a room with photos, or a whole-home capture with a file."""
    cap = p.get("capture")
    return any(s["files"] for s in p["spaces"]) or bool(cap and cap["files"])


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
    """[the rooms with photos, as one walk] + [the whole-home capture]. Each run: tier, title
    (stage name prefix), label (output prefix), space_ids (the rooms it reports on: for the
    whole-home run every room), and for photos the folder of each room."""
    runs: list[dict] = []
    photos = [s for s in p["spaces"] if s["kind"] == "photos" and s["files"]]
    if photos:
        folders = {s["space_id"]: f"{i:02d}_{safe_name(s['name'])}" for i, s in enumerate(photos, 1)}
        runs.append({"tier": "photos", "engine_tier": "photo", "label": "photos", "title": TITLES["photos"],
                     "space_ids": [s["space_id"] for s in photos], "folders": folders})
    cap = p.get("capture")
    if cap:
        runs.append({"tier": cap["kind"], "engine_tier": cap["kind"], "label": "whole_home",
                     "title": TITLES[cap["kind"]], "space_ids": [s["space_id"] for s in p["spaces"]],
                     "folders": {}, "whole_home": True})
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
    or file to give roomscan.pipeline.run) and `files` (space_id, or CAPTURE_ID for the
    whole-home capture -> {stored name: path}).
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
                sz = given_sizes(s)
                if sz:
                    meas[folder] = sz
            if meas:
                (root / "measurements.yaml").write_text(yaml.safe_dump({"rooms": meas}, sort_keys=False),
                                                        encoding="utf-8")
            r["path"] = root
            continue
        # the whole-home capture
        s, sid, d = p["capture"], CAPTURE_ID, work / r["label"]
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


def verify_room(s: dict, run: dict | None, has_capture: bool) -> list[dict]:
    """A room: its photos (if any). Without photos it is a size reference for the whole-home
    capture; without photos and without a capture there is nothing to measure it with."""
    from roomscan import capture_quality as cq

    names = [f["name"] for f in s["files"]]
    if not names:
        if has_capture:
            msg = ("no photos: this room's sizes are a reference for the whole-home capture"
                   if given_sizes(s) else "no photos and no sizes: the whole-home capture finds the rooms on its "
                   "own, so this room adds nothing (give it a length or width, or photos)")
            return [finding("ok", "photos in room", msg)]
        return [finding("retake", "photos in room", "no photos -> add 2 to 8 photos of this room from the doorway, "
                        "or add a whole-home video / LiDAR scan above")]
    paths = run["files"].get(s["space_id"], {}) if run else {}
    F: list[dict] = []
    ext = {n: Path(n).suffix.lower() for n in names}
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
    F.append(finding("retake" if len(photos) < 2 else "ok", "photos in room",
                     f"{len(photos)} photo(s)" + (" -> take at least 2 photos of this room from the doorway"
                                                  if len(photos) < 2 else ""), photos if len(photos) < 2 else []))
    if photos and run:
        folder = run["folders"][s["space_id"]]
        F += _from_cq(_guard(cq.check_photos, "photo check",
                             {folder: sorted((paths[n] for n in photos), key=lambda q: q.name.casefold())},
                             look_back=False), photos)
    return F


def verify_capture(cap: dict, run: dict) -> list[dict]:
    """The whole-home capture: exactly one clip, or one Stray Scanner export."""
    from roomscan import capture_quality as cq

    names = [f["name"] for f in cap["files"]]
    paths = run["files"].get(CAPTURE_ID, {})
    kind = cap["kind"]
    if not names:
        what = "video" if kind == "video" else "LiDAR scan (.zip)"
        return [finding("retake", "files", f"no {what} uploaded -> add it, or remove the whole-home capture")]
    ext = {n: Path(n).suffix.lower() for n in names}
    F: list[dict] = []
    if kind == "video":
        vids = [n for n in names if ext[n] in VIDEO_EXT]
        other = [n for n in names if n not in vids]
        if len(vids) != 1:
            F.append(finding("retake", "video count",
                             f"{len(vids)} videos -> the whole-home capture is exactly one clip (.mov, .mp4) of "
                             f"every room", vids))
        else:
            F.append(finding("ok", "video count", "1 video", []))
            F += _from_cq(_guard(cq.check_video, "video check", paths[vids[0]]), vids)
        if other:
            F.append(finding("warn", "file types", f"{len(other)} file(s) that are not videos are ignored", other))
        return F
    from roomscan.frontends.lidar_stray import find_stray_root
    if run.get("error"):
        return [finding("retake", "lidar export", run["error"])]
    zips = [n for n in names if ext[n] == ".zip"]
    if len(zips) > 1:
        return [finding("retake", "lidar export", f"{len(zips)} zip files -> the whole-home capture is one Stray "
                        "Scanner export: keep one", zips)]
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
    `work`, a scratch folder the caller deletes:
    {"ok", "spaces": [per room], "capture": {...} | None, "project_findings"}."""
    from roomscan import capture_quality as cq

    project: list[dict] = []
    cap = p.get("capture")
    if not has_input(p):
        project.append(finding("retake", "nothing to compute",
                               "no photos and no whole-home capture -> add photos to a room, or one video / "
                               "LiDAR scan of the whole home"))
    if runs is None:
        runs = materialise(store, p, work, lite=True)
    photo_run = next((r for r in runs if r["tier"] == "photos"), None)
    home_run = next((r for r in runs if r.get("whole_home")), None)
    out = []
    for s in p["spaces"]:
        fs = verify_room(s, photo_run if photo_run and s["space_id"] in photo_run["space_ids"] else None,
                         bool(cap))
        out.append({"space_id": s["space_id"], "name": s["name"], "status": worst(f["level"] for f in fs),
                    "findings": fs})
    capture = None
    if cap and home_run:
        fs = verify_capture(cap, home_run)
        status = worst(f["level"] for f in fs)
        capture = {"name": CAPTURE_NAMES[cap["kind"]], "kind": cap["kind"], "status": status, "findings": fs,
                   "advice": RETAKE_ADVICE[cap["kind"]] if status != "ok" else None}
    if photo_run and home_run:
        project.append(finding("ok", "tiers", f"two runs: the rooms' photos as one walk, and the whole-home "
                               f"{'video' if cap['kind'] == 'video' else 'LiDAR scan'}; each gives its own plan"))
    if photo_run and len(photo_run["space_ids"]) > 1:
        rooms = {}
        for sid in photo_run["space_ids"]:
            fl = [q for q in photo_run["files"][sid].values() if q.suffix.lower() in IMAGE_EXT]
            if fl:
                rooms[photo_run["folders"][sid]] = sorted(fl, key=lambda q: q.name.casefold())
        if len(rooms) > 1:
            fs = _guard(cq.check_photos, "look-back photo", rooms, look_back=True)
            project += _from_cq([f for f in fs if f.check == "look-back photo"], [])
    ok = (all(sp["status"] != "retake" for sp in out) and all(f["level"] != "retake" for f in project)
          and (capture is None or capture["status"] != "retake"))
    return {"ok": ok, "spaces": out, "capture": capture, "project_findings": project}


def n_retake(ver: dict) -> int:
    """Retake findings in a verify response."""
    cap = ver.get("capture") or {"findings": []}
    return (sum(1 for s in ver["spaces"] for f in s["findings"] if f["level"] == "retake")
            + sum(1 for f in cap["findings"] if f["level"] == "retake")
            + sum(1 for f in ver["project_findings"] if f["level"] == "retake"))
