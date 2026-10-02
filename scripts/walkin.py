"""Walk-in test runner: a capture straight off the phone in, a cold pipeline run, the numbers
an examiner checks with a laser measurer out.

usage:
    python scripts/walkin.py <capture> [--out DIR] [--tier auto|lidar|video|photo] [--no-damage] [--keep-temp]

<capture> is what docs/capture_protocol.md hands over, in any of these forms:
    LiDAR   a Stray Scanner recording folder, its .zip (Files app > Compress), or a folder
            or drive holding exactly one of either
    video   a .mov / .mp4 clip, or a folder or drive holding exactly one clip
    photos  a folder or drive holding one folder of photos per room, named in walk order
            (01_hall, 02_kitchen, ...); photos straight in the folder are one room
Hidden and system entries (System Volume Information, .Trashes, __MACOSX, ._ files) are
ignored. Photos are copied to a clean folder first, in the order they were taken.
--tier says which tier the examiners chose; the run stops if the input is something else.

Cold run: the pipeline's caches (fused clouds, depth maps) go to a fresh temporary folder,
which is also the working directory, and are deleted afterwards; the repository's .cache/
is neither read nor written. Model weights come from the local Hugging Face cache
(scripts/fetch_weights.py); when every model the tier needs is there, the run is forced
offline. The pipeline is the one `roomscan run` calls, with the same defaults.

Prints the time of every stage, then one block per room: floor area, every wall with its
90 % interval and the side of plan.png it is on, ceiling height (or "not observed"),
openings with widths. Writes result.json, result.xlsx, plan.png, plan.svg and this report (walkin.txt)
to --out (default runs/walkin_<capture>_<date>_<time>).
Before the run, the capture's quality is checked (roomscan.capture_quality, seconds): per
check OK / WARN / RETAKE with one line of advice (too many photos taken while walking,
chat-app copies, blur, walking too fast, never looking up at the ceiling, ...). The normal
run prints it and goes on; --check-only prints it and stops.
Exit status: 0 done; 2 bad input (one-line message); 1 the pipeline failed (one-line
message, traceback in <out>/walkin_error.txt); 3 (--check-only) the quality check advises
a retake.
"""
from __future__ import annotations

import argparse
import gc
import os
import shutil
import stat
import sys
import tempfile
import time
import traceback
import warnings
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from roomscan.frontends.lidar_stray import is_stray
from roomscan.frontends.photos import IMAGE_EXT
from roomscan.pipeline import VIDEO_EXT

SKIP_NAMES = {"system volume information", "$recycle.bin", "__macosx", "thumbs.db", "desktop.ini"}
MIN_WALL_M = 0.25  # as bench/evaluate.py: shorter segments are jamb returns and noise, not walls
TIER_NAME = {"lidar": "LiDAR", "video": "video", "photo": "photo"}
HEIF = {".heic", ".heif"}


class InputError(Exception):
    """Input that cannot be run; reported in one line, exit status 2."""


@dataclass
class Capture:
    path: Path
    tier: str
    name: str
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- finding the capture

def _visible(p: Path) -> bool:
    n = p.name
    if not n or n[0] in ".$~" or n.lower() in SKIP_NAMES:
        return False
    try:
        attrs = getattr(p.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return not attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM)


def _entries(d: Path) -> list[Path]:
    try:
        return sorted(p for p in d.iterdir() if _visible(p))
    except OSError as e:
        raise InputError(f"cannot read {d}: {e.strerror or e}") from None


def _images(d: Path) -> list[Path]:
    return [p for p in _entries(d) if p.suffix.lower() in IMAGE_EXT and p.is_file()]


def _names(ps: list[Path]) -> str:
    return ", ".join(p.name for p in ps[:4]) + (", ..." if len(ps) > 4 else "")


def _unzip(z: Path, work: Path) -> Path:
    dest = work / "input" / z.stem
    try:
        with zipfile.ZipFile(z) as zf:
            members = [m for m in zf.infolist()
                       if not any(part.startswith(("__MACOSX", "._")) or part == ".DS_Store"
                                  for part in m.filename.replace("\\", "/").split("/"))]
            if not members:
                raise InputError(f"{z.name} is empty")
            print(f"unzipping {z.name} ({len(members)} files) ...", flush=True)
            for m in members:
                zf.extract(m, dest)
    except zipfile.BadZipFile:
        raise InputError(f"{z.name} is not a readable zip file (incomplete copy?)") from None
    return dest


def _exif(p: Path, decode: bool = False) -> dict:
    """The photo's EXIF sub-IFD (capture time, focal length); decode=True also decodes the pixels."""
    from PIL import Image
    if p.suffix.lower() in HEIF:
        from roomscan.heif import register
        register()
    with warnings.catch_warnings(), Image.open(p) as im:
        warnings.simplefilter("ignore")  # PIL's "Corrupt EXIF data" on some exports
        if decode:
            im.load()
        return dict(im.getexif().get_ifd(0x8769))


def _shot_time(p: Path) -> str | None:
    """EXIF capture time with sub-seconds, as a sortable string."""
    try:
        ex = _exif(p)
    except Exception:
        return None
    t = ex.get(0x9003)  # DateTimeOriginal
    sub = str(ex.get(0x9291) or "0").strip()  # SubsecTimeOriginal
    return f"{t}.{sub.ljust(3, '0')[:6]}" if t else None


def _stage_photos(src: Path, rooms: list[Path], loose: list[Path], work: Path) -> Capture:
    """Copy the photos to a clean folder (no hidden or system entries), in capture order."""
    name = src.name or "photos"  # a drive root has no name
    dest = work / "staged" / name  # not under input/, where an unzipped source may live
    notes = []
    if not rooms and len(loose) > 8:
        raise InputError(f"{len(loose)} photos directly in {src} and no room folders: put each room's photos in "
                         f"its own folder named in walk order (01_hall, 02_kitchen, ...); one room = one folder")
    if rooms and loose:
        notes.append(f"{len(loose)} photo(s) directly in {src} are not in a room folder: ignored")
    if rooms and not all(r.name[:1].isdigit() for r in rooms):
        notes.append("room folders are not numbered (01_hall, 02_kitchen, ...): walk order taken as "
                     "alphabetical: " + ", ".join(r.name for r in rooms))
    groups = [(r.name, _images(r)) for r in rooms] if rooms else [(name, loose)]
    for room, imgs in groups:
        times = [_shot_time(p) for p in imgs]
        order = imgs
        if None not in times:
            order = [p for _, _, p in sorted(zip(times, [p.name for p in imgs], imgs))]
            if order != imgs:
                notes.append(f"{room}: file names are not in the order the photos were taken; "
                             f"using the capture time")
        if not 2 <= len(imgs) <= 8:
            notes.append(f"{room}: {len(imgs)} photos (the protocol asks for 2 to 8)")
        out = dest / room if rooms else dest
        out.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(order):
            shutil.copy2(f, out / f"{i:03d}_{f.name}")  # the pipeline takes photos in name order
    return Capture(dest, "photo", name, notes)


def _orig(f: Path) -> str:
    """A staged photo's original file name."""
    return f.name.split("_", 1)[1] if f.name[:3].isdigit() else f.name


def resolve(path: Path, work: Path, depth: int = 0) -> Capture:
    """What the phone handed over -> (capture path, tier); InputError if it is not a capture."""
    if not path.exists():
        raise InputError(f"{path} does not exist")
    if path.is_file():
        ext = path.suffix.lower()
        if ext == ".zip":
            cap = resolve(_unzip(path, work), work, depth + 1)
            cap.name = path.stem if cap.tier == "photo" else cap.name
            return cap
        if ext in VIDEO_EXT:
            return Capture(path, "video", path.stem)
        if ext in IMAGE_EXT:
            raise InputError(f"{path.name} is a single photo: give the folder that holds one folder of photos per room")
        raise InputError(f"{path.name} is not a capture: give a Stray Scanner folder or .zip, a .mov/.mp4 clip, "
                         f"or a folder of room photo folders")
    if is_stray(path):
        return Capture(path, "lidar", path.name)
    entries = _entries(path)
    dirs = [p for p in entries if p.is_dir()]
    files = [p for p in entries if p.is_file()]
    strays = [p for p in dirs if is_stray(p)]
    if len(strays) > 1:
        raise InputError(f"{path} holds {len(strays)} Stray Scanner recordings ({_names(strays)}): give one of them")
    if strays:
        return Capture(strays[0], "lidar", strays[0].name)
    rooms = [p for p in dirs if _images(p)]
    loose = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXT]
    zips = [p for p in files if p.suffix.lower() == ".zip"]
    if (rooms or loose) and videos:
        raise InputError(f"{path} holds both photos and video ({_names(videos)}): give the clip itself, "
                         f"or a folder that holds only the room folders")
    if rooms or loose:
        return _stage_photos(path, rooms, loose, work)
    if len(videos) > 1:
        raise InputError(f"{path} holds {len(videos)} videos ({_names(videos)}): give one of them")
    if videos:
        return Capture(videos[0], "video", videos[0].stem)
    if len(zips) > 1:
        raise InputError(f"{path} holds {len(zips)} zip files ({_names(zips)}): give one of them")
    if zips:
        return resolve(zips[0], work, depth + 1)
    if len(dirs) == 1 and depth < 3:  # one wrapper folder, e.g. an unzipped "Stray Scanner" folder
        return resolve(dirs[0], work, depth + 1)
    raise InputError(f"no capture found in {path}: expected a Stray Scanner recording (odometry.csv and depth/), "
                     f"a .mov/.mp4 clip, or one folder of photos per room")


# ---------------------------------------------------------------- checks before the long run

def _check_lidar(cap: Capture) -> str:
    root = cap.path
    try:
        rows = [ln for ln in (root / "odometry.csv").read_text(encoding="utf-8", errors="replace").splitlines()[1:]
                if ln.strip()]
        n_depth = sum(1 for p in (root / "depth").iterdir() if p.suffix.lower() == ".png")
    except OSError as e:
        raise InputError(f"{root}: cannot read the recording ({e.strerror or e})") from None
    if n_depth < 10 or len(rows) < 10:
        raise InputError(f"{root.name}: the recording is empty ({n_depth} depth frames, {len(rows)} poses)")
    try:
        dur = float(rows[-1].split(",")[0]) - float(rows[0].split(",")[0])
    except ValueError:
        dur = float("nan")
    if n_depth < 0.95 * len(rows):
        cap.notes.append(f"depth/ has {n_depth} frames for {len(rows)} poses: the copy may be incomplete")
    if not (root / "rgb.mp4").exists():
        cap.notes.append("no rgb.mp4 in the recording: no colour images, so no damage detection")
    return f"Stray Scanner recording {root.name}: {n_depth} depth frames over {dur:.0f} s"


def _check_video(cap: Capture) -> str:
    import cv2

    f = cap.path
    vc = cv2.VideoCapture(str(f))
    try:
        if not vc.isOpened():
            raise InputError(f"{f.name}: cannot open the video (incomplete copy, or not a video)")
        n, fps = int(vc.get(cv2.CAP_PROP_FRAME_COUNT) or 0), float(vc.get(cv2.CAP_PROP_FPS) or 0)
        cc = int(vc.get(cv2.CAP_PROP_FOURCC) or 0)
        codec = "".join(chr((cc >> 8 * i) & 0xFF) for i in range(4)).strip() or "unknown"
        ok, img = vc.read()
    finally:
        vc.release()
    if not ok or img is None:
        raise InputError(f"{f.name}: cannot decode the video (codec {codec}); record it with "
                         f"Settings > Camera > Formats > Most Compatible")
    dur = n / fps if fps > 0 else 0.0
    if 0 < dur < 5:
        raise InputError(f"{f.name} is {dur:.1f} s long: too short for a walkthrough")
    return f"video {f.name}: {img.shape[1]}x{img.shape[0]}, {fps:.0f} fps, {dur:.0f} s, codec {codec}"


def _check_photos(cap: Capture) -> str:
    from roomscan.frontends.photos import find_rooms

    rooms = find_rooms(cap.path)
    no_focal = []
    for room, files in rooms.items():
        for i, f in enumerate(files):
            try:
                # decode one photo per room: catches formats this machine cannot read
                if not _exif(f, decode=i == 0).get(0xA405):
                    no_focal.append(f"{room}/{_orig(f)}")
            except Exception as e:
                raise InputError(f"{room}/{_orig(f)}: cannot read the photo ({type(e).__name__}: {e})") from None
    if no_focal:
        cap.notes.append(f"{len(no_focal)} photo(s) carry no focal length (e.g. {no_focal[0]}): the iPhone "
                         f"main-camera default is assumed; send originals, not through a chat app")
    return f"{len(rooms)} room folder(s): " + ", ".join(f"{r} ({len(fl)})" for r, fl in rooms.items())


CHECKS = {"lidar": _check_lidar, "video": _check_video, "photo": _check_photos}


def quality(cap: Capture) -> tuple[str, list[str]]:
    """(worst level, report lines) of the capture-quality check; never stops the run."""
    from roomscan import capture_quality as cq
    try:
        if cap.tier == "photo":
            from roomscan.frontends.photos import find_rooms
            found = cq.check_photos(find_rooms(cap.path), name=_orig)
        elif cap.tier == "video":
            found = cq.check_video(cap.path)
        else:
            found = cq.check_lidar(cap.path)
    except Exception as e:  # a checker bug must not cost the walk-in run
        return cq.OK, [f"capture quality: not checked ({type(e).__name__}: {e})"]
    return cq.worst(found), cq.report_lines(found)


# ---------------------------------------------------------------- model weights

def _hub_dir() -> Path:
    for k in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(k):
            return Path(os.environ[k])
    home = os.environ.get("HF_HOME") or str(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
                                            / "huggingface")
    return Path(home) / "hub"


def _weights_cached(repo: str) -> bool:
    """A complete snapshot (config, processor config, weights) in the local Hugging Face cache."""
    d = _hub_dir() / f"models--{repo.replace('/', '--')}"
    try:
        snap = d / "snapshots" / (d / "refs" / "main").read_text().strip()
        names = {p.name for p in snap.iterdir()}
    except OSError:
        return False
    return {"config.json", "preprocessor_config.json"} <= names and any(
        n.endswith((".safetensors", ".bin")) for n in names)


def _models(tier: str, damage: bool) -> dict[str, str]:
    from roomscan.damage.detect import CLIP_ID
    from roomscan.ml.depth import MODEL_ID as DEPTH_ID
    from roomscan.ml.matching import MODEL_ID as MATCH_ID

    need = {}
    if tier in ("video", "photo"):
        need["depth"] = DEPTH_ID
    if tier == "photo":
        need["photo matching"] = MATCH_ID
    if damage:
        need["damage"] = CLIP_ID
    return need


def _go_offline(need: dict[str, str]) -> str:
    missing = [f"{k} ({v})" for k, v in need.items() if not _weights_cached(v)]
    if missing:
        return ("NOT in the local cache: " + ", ".join(missing) + "; they will be downloaded if there is a network "
                "(run scripts/fetch_weights.py beforehand)")
    if "huggingface_hub" in sys.modules:  # too late for the environment variable alone
        import huggingface_hub.constants as hc
        hc.HF_HUB_OFFLINE = True
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    return "all in the local cache; running offline" if need else "none needed"


# ---------------------------------------------------------------- report

def _short(wid: str, rid: str) -> str:
    return wid[len(rid) + 1:] if wid.startswith(rid + "_") else wid


def _ci(m: dict, nd: int = 3) -> str:
    return f"{m['ci90'][0]:.{nd}f} - {m['ci90'][1]:.{nd}f}" if m.get("ci90") else "n/a"


def _num(m: dict, nd: int = 3) -> str:
    return "n/a" if m.get("value") is None else f"{m['value']:.{nd}f}"


def _sides(room: dict) -> dict[str, str]:
    """Side of the room each wall bounds, as seen on plan.png (plan y grows downward)."""
    poly = np.asarray(room["polygon"], float)
    if len(poly) < 3:
        return {}
    x, y = poly[:, 0], poly[:, 1]
    ccw = float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)) > 0  # interior left of every wall
    out = {}
    for w in room["walls"]:
        d = np.subtract(w["end"], w["start"], dtype=float)
        n = float(np.hypot(*d))
        if n < 1e-9:
            continue
        o = np.array([d[1], -d[0]]) / n if ccw else np.array([-d[1], d[0]]) / n  # outward normal
        side = [s for s, ok in (("top" if o[1] < 0 else "bottom", abs(o[1]) > 0.38),
                                ("left" if o[0] < 0 else "right", abs(o[0]) > 0.38)) if ok]
        out[w["id"]] = "-".join(side)
    return out


def report(res: dict, out: Path, times: dict) -> list[str]:
    L = []
    rooms = res["rooms"]
    fp = res["property"]["footprint_area"]
    L += ["", "Stage times (s)"]
    for k, v in res["timing_s"].items():
        L.append(f"  {k:<14}{v:8.1f}")
    other = times["run"] - sum(res["timing_s"].values())
    L += [f"  {'other':<14}{other:8.1f}   model loading, plan rendering",
          f"  {'pipeline':<14}{times['run']:8.1f}",
          f"  {'total':<14}{times['total']:8.1f}   from start, including input checks and copying"]
    L += ["", f"Result: {TIER_NAME[res['capture']['tier']]} tier, {len(rooms)} room(s), footprint "
              f"{_num(fp, 2)} m2 (90 % interval {_ci(fp, 2)}). Lengths in metres; walls are named "
              f"by the side of the room they bound on plan.png."]
    for r in rooms:
        a, c = r["floor_area"], r["ceiling_height"]
        if c["value"] is None:
            ceil = "ceiling not observed" + (f" (at least {c['lower_bound']:.2f} m)" if c.get("lower_bound") else "")
        else:
            ceil = f"ceiling {c['value']:.3f} ({_ci(c)})"
        L += ["", f"{r['id']} ({r['label']}): floor area {_num(a, 2)} m2 ({_ci(a, 2)}), {ceil}",
              f"  {'wall':<8}{'side':<14}{'length':>8}   90 % interval"]
        sides = _sides(r)
        short = []
        for w in r["walls"]:
            m = w["length"]
            if m["value"] is None or m["value"] < MIN_WALL_M:
                short.append(f"{_short(w['id'], r['id'])} {m['value'] or 0:.3f}")
                continue
            L.append(f"  {_short(w['id'], r['id']):<8}{sides.get(w['id'], ''):<14}{m['value']:>8.3f}   {_ci(m)}")
        if short:
            L.append(f"  ({len(short)} segment(s) under {MIN_WALL_M:.2f} m not listed: {', '.join(short)})")
        # own openings, then doorways the neighbouring room reports (a doorway is reported once)
        ops = [(o, True) for o in r["openings"]]
        ops += [(o, False) for x in rooms if x["id"] != r["id"] for o in x["openings"]
                if o.get("connects_to") == r["id"]]
        for o, own in ops:
            w, h = o["width"], o["height"]
            if own:
                where = f"on {_short(o['wall_id'], r['id'])}" + (f", to {o['connects_to']}" if o["connects_to"] else "")
            else:
                where = f"to {o['room_id']} (on its wall {_short(o['wall_id'], o['room_id'])})"
            L.append(f"  {o['type']} {o['id']} {where}: width {_num(w)} ({_ci(w)})" +
                     (f", height {h['value']:.2f}" if h.get("value") else ""))
        if not ops:
            L.append("  no openings found")
    adj = [f"{a['rooms'][0]} - {a['rooms'][1]} ({a['kind']}{', ' + a['via'] if a.get('via') else ''})"
           for a in res["property"]["adjacency"]]
    L += ["", "Adjacency: " + ("; ".join(adj) if adj else "none")]
    if res["damage"]:
        L.append(f"Damage: {len(res['damage'])} region(s)")
        for d in res["damage"]:
            L.append(f"  {d['damage_class']} on {d['surface_id']}: area {_num(d['area'], 2)} m2, "
                     f"{_num(d['extent_u'], 2)} x {_num(d['extent_v'], 2)} m, "
                     f"centre {d['center'][2]:.2f} m above the floor")
    else:
        L.append("Damage: none found" if "damage" in res["timing_s"] else "Damage: not run (--no-damage)")
    L.append(f"Concealed-damage flags: {len(res['concealed_damage_flags'])}"
             + "".join(f"; {f['rule_id']} on {f['surface_id']}" for f in res["concealed_damage_flags"])
             + f". Scope items: {len(res['scope'])}.")
    if res["warnings"]:
        L += ["", "Warnings from the pipeline:"] + [f"  - {w}" for w in res["warnings"]]
    L += ["", f"Outputs in {out}: result.json, result.xlsx, plan.png, plan.svg, walkin.txt",
          f"Done in {times['total']:.0f} s."]
    return L


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description="Cold pipeline run on one capture, with a per-room table for the "
                                             "walk-in test.", epilog="See the module docstring for details.")
    ap.add_argument("capture", type=Path, help="Stray Scanner folder or .zip, video clip, or folder of room photo "
                                               "folders (a drive holding one of these also works)")
    ap.add_argument("--out", type=Path, help="output folder (default runs/walkin_<capture>_<date>_<time>)")
    ap.add_argument("--tier", default="auto", choices=["auto", "lidar", "video", "photo"],
                    help="the tier the examiners chose; stop if the input is something else")
    ap.add_argument("--no-damage", action="store_true", help="skip damage detection (geometry only, faster)")
    ap.add_argument("--keep-temp", action="store_true", help="keep the temporary folder (caches, unzipped input)")
    ap.add_argument("--check-only", action="store_true",
                    help="only find the capture, check it is complete and readable and check its quality "
                         "(seconds), then stop: use it while the phone is still there; exit 3 = retake advised")
    a = ap.parse_args(argv)

    t0 = time.time()
    cwd = Path.cwd()
    work = Path(tempfile.mkdtemp(prefix="roomscan_walkin_"))
    import roomscan.frontends.video as video_mod
    import roomscan.pipeline as pipeline
    saved = (pipeline.CACHE_DIR, video_mod.CACHE)
    try:
        try:
            cap = resolve(a.capture.absolute(), work)
            if a.tier != "auto" and a.tier != cap.tier:
                raise InputError(f"--tier {a.tier}, but {a.capture} is a {TIER_NAME[cap.tier]} capture")
            what = CHECKS[cap.tier](cap)
        except InputError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        level, qlines = quality(cap)
        if a.check_only:
            print("\n".join([f"ok: {a.capture} -> {TIER_NAME[cap.tier]} tier, {what}"]
                            + [f"  note: {n}" for n in cap.notes] + qlines))
            return 3 if level == "RETAKE" else 0
        out = (a.out or cwd / "runs" / f"walkin_{cap.name}_{datetime.now():%Y%m%d_%H%M%S}").absolute()
        models = _go_offline(_models(cap.tier, not a.no_damage))
        head = ["roomscan walk-in run",
                f"  input     {a.capture} -> {TIER_NAME[cap.tier]} tier, {what}",
                f"  output    {out}",
                f"  cold run  pipeline caches in a fresh temporary folder, deleted afterwards ({work})",
                f"  models    {models}",
                f"  started   {datetime.now():%H:%M:%S}"] + [f"  note      {n}" for n in cap.notes]
        head += [""] + qlines + (["  (going ahead with the run anyway)"] if level != "OK" else [])
        print("\n".join(head) + "\n", flush=True)

        os.chdir(work)  # any relative path the pipeline uses lands in the temporary folder
        pipeline.CACHE_DIR = video_mod.CACHE = work / ".cache"
        t1 = time.time()
        try:
            res = pipeline.run(cap.path, out, tier=cap.tier, damage=not a.no_damage, use_cache=True, progress=True)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 130
        except Exception as e:
            out.mkdir(parents=True, exist_ok=True)
            (out / "walkin_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            msg = (str(e).strip().splitlines() or [""])[0]
            print(f"\nerror: the pipeline failed after {time.time() - t1:.0f} s: {type(e).__name__}: {msg}\n"
                  f"       traceback in {out / 'walkin_error.txt'}", file=sys.stderr)
            return 1
        times = {"run": time.time() - t1, "total": time.time() - t0}
        lines = report(res, out, times)
        text = "\n".join(head + lines) + "\n"
        (out / "walkin.txt").write_text(text, encoding="utf-8", newline="\n")
        from roomscan.export.sheet import write_sheet
        write_sheet(res, out / "result.xlsx")  # the same result as a spreadsheet
        print("\n".join(lines), flush=True)
        return 0
    finally:
        os.chdir(cwd)
        pipeline.CACHE_DIR, video_mod.CACHE = saved
        gc.collect()  # release open video readers before deleting their files
        if a.keep_temp:
            print(f"temporary folder kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
