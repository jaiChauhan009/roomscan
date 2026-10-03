"""Project -> capture folders the engine reads (materialise), and the pre-run check (verify)
built on roomscan.capture_quality plus structural checks.

A project has rooms (spaces of kind "photos": a name, optional sizes, optional photos) and up
to two whole-home capture containers (video, LiDAR). Each holds up to MAX_ITEMS items: every
video file is an item, every Stray Scanner .zip is an item, and an export uploaded as loose
files (odometry.csv, depth/ ...) is one item. Runs: the rooms with photos together as one photo
walk, then one run per LiDAR item, then one per video item (fastest first); each whole-home run
gets every room's sizes as unnamed known sizes.

A LiDAR .zip holding a capture.json is a RoomPlan capture from our iOS app (engine tier
"roomplan"); the app uploads one zip per room as soon as the room is scanned. Each is its own
run ("Room (RoomPlan): Kitchen"). Two or more of them that say they share the session's world
frame (merged = true, and the same session_id when the app gives one) also get one combined run
("Whole home (RoomPlan: 3 rooms)"); the engine lays the rooms side by side instead if they
overlap (not one frame after all)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

import yaml

from server.store import CAPTURE_KINDS, Store, capture_id, safe_name, safe_relpath

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
LEVELS = {"OK": "ok", "WARN": "warn", "RETAKE": "retake"}
_RANK = {"ok": 0, "warn": 1, "retake": 2}
# what a verify of a zipped Stray export needs: everything but the frames and the clip
_STRAY_BULK = ("depth/", "confidence/", "rgb.mp4")
MAX_ITEMS = 5  # whole-home items (videos / LiDAR scans) per kind
TITLES = {"photos": "Rooms (photos)", "video": "Whole home (video)", "lidar": "Whole home (LiDAR)"}
KIND_WORD = {"video": "video", "lidar": "LiDAR"}
RETAKE_ADVICE = {
    "video": "Record the whole home again as one clip: Video mode at 1x, walk slowly through every room "
             "(one step every two seconds), tilt up to the ceiling once in each room, keep some floor in "
             "view and never turn on the spot. Then delete the old clip and upload the new one.",
    "lidar": "Scan the whole home again as one Stray Scanner recording: walk slowly round every room, tilt "
             "up to the ceiling and down to the floor once per room; then Files app > Stray Scanner > press "
             "and hold the newest folder > Compress, delete the old .zip here and upload the new one.",
    "roomplan": "Scan the room again with the roomscan app: walk slowly along every wall, keep each wall in "
                "view for a moment, then delete the old .zip here; the app uploads the new scan.",
}
_RP_INFO: dict[str, dict | None] = {}  # sha256 -> roomplan_info (a stored file never changes)


def worst(levels) -> str:
    return max(levels, key=_RANK.__getitem__, default="ok")


def content_key(p: dict) -> str:
    """Hash of what the engine would see: rooms in walk order, names, sizes, file names and
    hashes, and the whole-home captures."""
    def files(s):
        return sorted((f["sha256"], f["name"]) for f in s["files"])

    spaces = [{"kind": s["kind"], "name": s["name"], "sizes": s["sizes"], "files": files(s)} for s in p["spaces"]]
    # every item of every capture, in order (the order sets run numbers and titles)
    captures = {k: {"kind": c["kind"], "items": [[it["name"], files(it)] for it in capture_items(c)]}
                for k, c in present(p)}
    return hashlib.sha256(json.dumps({"spaces": spaces, "captures": captures}, sort_keys=True).encode()).hexdigest()


def is_main_file(kind: str, name: str) -> bool:
    """A file that is an item on its own: a clip, or a zipped LiDAR export."""
    ext = Path(name).suffix.lower()
    return ext in VIDEO_EXT if kind == "video" else ext == ".zip"


def capture_items(cap: dict) -> list[dict]:
    """The items of a whole-home capture, in upload order: [{name, files: [file records]}].
    Video: one per clip (other files are no item). LiDAR: one per .zip, and all loose files
    (an export uploaded unzipped) together as one item named after their top folder."""
    kind, items, loose = cap["kind"], [], None
    for f in cap.get("files") or []:
        if is_main_file(kind, f["name"]):
            items.append({"name": Path(safe_relpath(f["name"])).name, "files": [f]})
        elif kind == "lidar":
            if loose is None:
                parts = safe_relpath(f["name"]).split("/")
                loose = {"name": parts[0] if len(parts) > 1 else "loose files", "files": [], "loose": True}
                items.append(loose)
            loose["files"].append(f)
    return items


def extra_files(cap: dict) -> list[dict]:
    """Files of a video capture that are not clips (ignored)."""
    return [f for f in cap.get("files") or [] if cap["kind"] == "video" and not is_main_file("video", f["name"])]


def run_title(kind: str, i: int, n: int, name: str) -> str:
    """"Whole home (LiDAR: scan.zip)"; with 2+ items of the kind "Whole home (video 2: b.mov)"."""
    return f"Whole home ({KIND_WORD[kind]}{f' {i}' if n > 1 else ''}: {name})"


def present(p: dict) -> list[tuple[str, dict]]:
    """The project's whole-home captures, (kind, capture) in run order: LiDAR, then video (fastest first)."""
    caps = p.get("captures") or {}
    return [(k, caps[k]) for k in CAPTURE_KINDS if caps.get(k)]


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
    """Something to compute: a room with photos, or a whole-home capture with a file (a capture
    with files but no video / scan among them fails verify)."""
    return any(s["files"] for s in p["spaces"]) or any(c["files"] for _, c in present(p))


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


def roomplan_info(store: Store | None, pid: str, f: dict) -> dict | None:
    """What a stored LiDAR .zip says about itself when it is a RoomPlan capture (it holds a
    capture.json): {rooms: [names], merged, session, error}; None for anything else (a Stray
    Scanner export) or without a store to read it from."""
    if store is None or Path(f["name"]).suffix.lower() != ".zip":
        return None
    sha = f["sha256"]
    if sha not in _RP_INFO:
        from roomscan.frontends.roomplan import zip_capture_json

        path = store.file_path(pid, capture_id("lidar"), sha)
        if not path.is_file():
            return None
        d = zip_capture_json(path)
        if d is None:
            info = None
        elif "_error" in d:
            info = {"rooms": [], "merged": False, "session": None, "error": d["_error"]}
        else:
            raw = d.get("rooms") if isinstance(d.get("rooms"), list) else []
            rooms = [str(r.get("name") or f"Room {i + 1}").strip() for i, r in enumerate(raw) if isinstance(r, dict)]
            info = {"rooms": rooms, "merged": d.get("merged", True) is True,
                    "session": None if d.get("session_id") is None else str(d.get("session_id")), "error": None}
        if len(_RP_INFO) > 512:
            _RP_INFO.clear()
        _RP_INFO[sha] = info
    return _RP_INFO[sha]


def _roomplan_spaces(p: dict, names: list[str]) -> list[str]:
    """The rooms a RoomPlan run reports on: those named like its rooms; all when none is."""
    want = {n.strip().casefold() for n in names}
    hit = [s["space_id"] for s in p["spaces"] if s["name"].strip().casefold() in want]
    return hit or [s["space_id"] for s in p["spaces"]]


def plan_runs(p: dict, store: Store | None = None) -> list[dict]:
    """[the rooms with photos, as one walk] + [one per LiDAR item] + [one per video item], each
    when present. Each run: tier, title (stage name prefix), label (output prefix), space_ids
    (the rooms it reports on: for a whole-home run every room), for photos the folder of each
    room, for a whole-home run its item ({name, files}).
    With `store`, LiDAR zips are opened to recognise RoomPlan captures (engine tier "roomplan",
    one run each, plus a combined run per session of two or more; see the module docstring)."""
    runs: list[dict] = []
    photos = [s for s in p["spaces"] if s["kind"] == "photos" and s["files"]]
    if photos:
        # walk order as an NN_ prefix; a name that already starts with a number ("02_room_8",
        # "3 kitchen") keeps only ours, so it does not read "02_02_room_8"
        folders = {s["space_id"]: f"{i:02d}_{re.sub(r'^[0-9]+[ _.-]*', '', safe_name(s['name'])) or safe_name(s['name'])}"
                   for i, s in enumerate(photos, 1)}
        runs.append({"tier": "photos", "engine_tier": "photo", "label": "photos", "title": TITLES["photos"],
                     "space_ids": [s["space_id"] for s in photos], "folders": folders})
    titles: set[str] = set()

    def unique(title: str, item: str) -> str:
        t, k = title, 2
        if t in titles:
            t = f"{title} ({item})"
        while t in titles:
            t, k = f"{title} ({item} {k})", k + 1
        titles.add(t)
        return t
    for kind, cap in present(p):
        items = capture_items(cap)
        rp_runs = []
        for i, it in enumerate(items, 1):
            info = None
            if kind == "lidar" and not it.get("loose"):
                info = roomplan_info(store, p["project_id"], it["files"][0])
            if info is not None:
                rooms = info["rooms"]
                if len(rooms) == 1:
                    title = f"Room (RoomPlan): {rooms[0]}"
                elif rooms:
                    title = f"Whole home (RoomPlan: {', '.join(rooms[:3])}{', ...' if len(rooms) > 3 else ''})"
                else:
                    title = f"Whole home (RoomPlan: {it['name']})"
                run = {"tier": kind, "engine_tier": "roomplan", "label": f"roomplan_{i}",
                       "title": unique(title, it["name"]), "item": it, "roomplan": info,
                       "space_ids": _roomplan_spaces(p, rooms), "folders": {}, "whole_home": True}
                rp_runs.append(run)
            else:
                run = {"tier": kind, "engine_tier": kind,
                       "label": f"whole_home_{kind}" + (f"_{i}" if len(items) > 1 else ""),
                       "title": unique(run_title(kind, i, len(items), it["name"]), it["name"]), "item": it,
                       "space_ids": [s["space_id"] for s in p["spaces"]], "folders": {}, "whole_home": True}
            runs.append(run)
        # one combined run per RoomPlan session of two or more zips in one world frame
        groups: dict = {}
        for r in rp_runs:
            if r["roomplan"]["merged"] and not r["roomplan"]["error"] and r["roomplan"]["rooms"]:
                groups.setdefault(r["roomplan"]["session"], []).append(r)
        groups = {k: g for k, g in groups.items() if len(g) > 1}
        for n, g in enumerate(groups.values(), 1):
            k = sum(len(r["roomplan"]["rooms"]) for r in g)
            runs.append({"tier": kind, "engine_tier": "roomplan",
                         "label": "roomplan_home" + (f"_{n}" if len(groups) > 1 else ""),
                         "title": unique(f"Whole home (RoomPlan: {k} rooms)", f"session {n}"),
                         "items": [r["item"] for r in g], "combined": True,
                         "space_ids": [s["space_id"] for s in p["spaces"]], "folders": {}, "whole_home": True})
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
    or file to give roomscan.pipeline.run) and `files` (space_id, or capture_id(kind) for a
    whole-home capture -> {stored name: path}).
    `lite`: for verify, a zipped LiDAR export is extracted without its frames."""
    work = Path(work)
    runs = plan_runs(p, store)
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
        sid, d = capture_id(r["tier"]), work / r["label"]
        d.mkdir(parents=True, exist_ok=True)
        r["files"][sid] = {}
        if r.get("combined"):  # several RoomPlan zips, one folder each: the engine reads them as one
            for j, it in enumerate(r["items"], 1):
                z = it["files"][0]
                src = store.file_path(p["project_id"], sid, z["sha256"])
                try:
                    _extract_zip(src, d / f"{j:02d}_{safe_name(Path(z['name']).stem)}", False)
                except (zipfile.BadZipFile, ValueError, OSError, NotImplementedError, RuntimeError) as e:
                    r["error"] = f"{z['name']}: cannot unzip ({e})"
                r["files"][sid][z["name"]] = src
            r["path"] = d
            continue
        # one whole-home item: a clip, a zipped LiDAR export, or a LiDAR export's loose files
        it = r["item"]
        if r["tier"] == "lidar" and not it.get("loose"):
            z = it["files"][0]
            src = store.file_path(p["project_id"], sid, z["sha256"])
            try:
                # a RoomPlan zip is small (capture.json and keyframes): verify reads it whole
                _extract_zip(src, d, lite and r["engine_tier"] != "roomplan")
            except (zipfile.BadZipFile, ValueError, OSError, NotImplementedError, RuntimeError) as e:
                r["error"] = f"{z['name']}: cannot unzip ({e})"
            r["files"][sid][z["name"]] = src
            r["path"] = d
            continue
        used = set()
        for f in it["files"]:
            rel = safe_relpath(f["name"]) if r["tier"] == "lidar" else _unique(Path(safe_relpath(f["name"])).name, used)
            dst = d / rel
            src = store.file_path(p["project_id"], sid, f["sha256"])
            if lite and r["tier"] == "lidar" and any(f"/{b}" in f"/{rel}" for b in _STRAY_BULK):
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.touch()
            else:
                link_or_copy(src, dst)
            r["files"][sid][f["name"]] = dst
        r["path"] = next(iter(r["files"][sid].values())) if r["tier"] == "video" else d
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


def verify_item(kind: str, run: dict) -> list[dict]:
    """One whole-home item: a clip, or one Stray Scanner export (zipped or loose files)."""
    from roomscan import capture_quality as cq

    it = run["item"]
    names = [f["name"] for f in it["files"]]
    paths = run["files"].get(capture_id(kind), {})
    if run.get("engine_tier") == "roomplan":
        if run.get("error"):
            return [finding("retake", "roomplan export", run["error"])]
        return _from_cq(_guard(cq.check_roomplan, "roomplan check", Path(run["path"])), names)
    if kind == "video":
        return _from_cq(_guard(cq.check_video, "video check", paths[names[0]]), names)
    from roomscan.frontends.lidar_stray import find_stray_root
    if run.get("error"):
        return [finding("retake", "lidar export", run["error"])]
    try:
        root = find_stray_root(Path(run["path"]))
    except Exception as e:  # several exports in one item
        root, err = None, str(e)
    else:
        err = ""
    if root is None:
        return [finding("retake", "lidar export",
                        "no Stray Scanner export found (needs odometry.csv, depth/, rgb.mp4)"
                        + (f": {err}" if err else "") + " -> upload the export as one .zip",
                        [n for n in names if is_main_file(kind, n)])]
    return [finding("ok", "lidar export", "Stray Scanner export found")] + _from_cq(
        _guard(cq.check_lidar, "lidar check", root), names)


def verify_captures(cap: dict, runs: list[dict]) -> list[dict]:
    """Blocks for one whole-home capture container: one per item ({kind, item, name, status,
    findings, advice}); a container without items gets one block asking for a file."""
    kind = cap["kind"]
    extra = [f["name"] for f in extra_files(cap)]
    blocks = []
    for r in runs:
        if r.get("combined"):  # its zips are checked one by one
            continue
        fs = verify_item(kind, r)
        blocks.append({"kind": kind, "item": r["item"]["name"], "name": r["title"], "findings": fs,
                       "_advice": "roomplan" if r.get("engine_tier") == "roomplan" else kind})
    if not blocks:
        what = "video" if kind == "video" else "LiDAR scan (.zip)"
        blocks.append({"kind": kind, "item": None, "name": TITLES[kind], "findings": [
            finding("retake", "files", f"no {what} uploaded -> add one, or remove the whole-home {KIND_WORD[kind]}")]})
    if extra:
        blocks[0]["findings"].append(finding("warn", "file types", f"{len(extra)} file(s) that are not videos "
                                             f"are ignored", extra))
    for b in blocks:
        b["status"] = worst(f["level"] for f in b["findings"])
        b["advice"] = RETAKE_ADVICE[b.pop("_advice", kind)] if b["status"] != "ok" else None
    return blocks


def verify_project(store: Store, p: dict, work: Path | None = None, runs: list[dict] | None = None) -> dict:
    """The verify response, on runs already materialised or materialised (lite) into
    `work`, a scratch folder the caller deletes:
    {"ok", "spaces": [per room], "captures": [per whole-home item], "project_findings"}."""
    from roomscan import capture_quality as cq

    project: list[dict] = []
    caps = present(p)
    if not has_input(p):
        project.append(finding("retake", "nothing to compute",
                               "no photos and no whole-home capture -> add photos to a room, or one video / "
                               "LiDAR scan of the whole home"))
    if runs is None:
        runs = materialise(store, p, work, lite=True)
    photo_run = next((r for r in runs if r["tier"] == "photos"), None)
    home_runs = [r for r in runs if r.get("whole_home")]
    out = []
    for s in p["spaces"]:
        fs = verify_room(s, photo_run if photo_run and s["space_id"] in photo_run["space_ids"] else None,
                         bool(caps))
        out.append({"space_id": s["space_id"], "name": s["name"], "status": worst(f["level"] for f in fs),
                    "findings": fs})
    captures = []
    for kind, cap in caps:
        captures += verify_captures(cap, [r for r in home_runs if r["tier"] == kind])
    if len(runs) > 1:
        what = (["the rooms' photos as one walk"] if photo_run else []) + [
            r["title"].replace("Whole home (", "the whole-home ").rstrip(")") for r in home_runs]
        project.append(finding("ok", "tiers", f"{len(runs)} runs: {', '.join(what[:-1])} and {what[-1]}; "
                               f"each gives its own plan"))
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
          and all(c["status"] != "retake" for c in captures))
    return {"ok": ok, "spaces": out, "captures": captures, "project_findings": project}


def n_retake(ver: dict) -> int:
    """Retake findings in a verify response."""
    caps = ver.get("captures") or ([ver["capture"]] if ver.get("capture") else [])
    return (sum(1 for s in ver["spaces"] for f in s["findings"] if f["level"] == "retake")
            + sum(1 for c in caps for f in c["findings"] if f["level"] == "retake")
            + sum(1 for f in ver["project_findings"] if f["level"] == "retake"))
