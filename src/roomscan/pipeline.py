"""End-to-end pipeline: capture folder -> JSON + rendered plan. One call per capture."""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import shutil
import time
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np

from roomscan.capture import PosedCapture
from roomscan.geometry.pointcloud import Cloud, fuse_capture

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
RAW_EXT = {".dng"}  # iPhone ProRAW / RAW; other cameras' raw formats are rejected as unknown types
CACHE_DIR = Path(".cache")
TIERS = ("auto", "lidar", "video", "photo")
# OS and sync-tool metadata that sits next to real files (compared lower-case)
_JUNK = {"__macosx", "thumbs.db", "ehthumbs.db", "desktop.ini", "$recycle.bin", "system volume information",
         "@eadir", ".ds_store"}
DNG_HINT = ("ProRAW / RAW .dng photos are not supported: set Settings > Camera > Formats to High Efficiency "
            "or Most Compatible with ProRAW off and take the photos again, or export them as JPEG")


class InputError(ValueError):
    """The capture cannot be used as given: missing, empty, unsupported or damaged.

    The message tells the person running the command what is wrong and what to do; the CLI
    prints it as one line without a traceback."""


def visible(p: Path) -> bool:
    """False for hidden files and OS metadata that travel with photos: .DS_Store, macOS
    '._' AppleDouble files, __MACOSX, Thumbs.db, desktop.ini, Synology @eaDir."""
    n = p.name
    return not (n.startswith(".") or n.startswith("~$") or n.lower() in _JUNK)


def natural_key(p: Path):
    """Sort key matching Explorer / Finder order: case-insensitive, numbers by value
    ("room 2" before "room 10")."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", p.name.casefold())]


def listdir(path: Path) -> list[Path]:
    """Visible entries of a folder in natural order; InputError if it cannot be read."""
    try:
        return sorted((p for p in Path(path).iterdir() if visible(p)), key=natural_key)
    except PermissionError as e:
        raise InputError(f"cannot read folder {path}: permission denied") from e
    except NotADirectoryError as e:
        raise InputError(f"{path} is not a folder") from e
    except OSError as e:
        raise InputError(f"cannot read folder {path}: {e.strerror or e}") from e


def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXT and p.is_file() and visible(p)


def capture_videos(folder: Path) -> list[Path]:
    """Video files in a folder, without Live Photo companions (IMG_0001.MOV next to
    IMG_0001.HEIC is the moving part of a photo, not a walk-through clip)."""
    entries = [p for p in listdir(folder) if p.is_file()]
    stems = {p.stem.casefold() for p in entries if p.suffix.lower() in IMAGE_EXT | RAW_EXT}
    return [p for p in entries if p.suffix.lower() in VIDEO_EXT and p.stem.casefold() not in stems]


def unwrap(path: Path, max_levels: int = 3) -> Path:
    """Step into folders whose only content is one folder without photos of its own, e.g.
    the `x/x/...` that Windows "Extract all" makes of a zipped folder."""
    path = Path(path)
    for _ in range(max_levels):
        if not path.is_dir():
            break
        entries = listdir(path)
        if len(entries) != 1 or not entries[0].is_dir() or any(is_image(q) for q in listdir(entries[0])):
            break
        path = entries[0]
    return path


def _has_images(d: Path) -> bool:
    try:
        return d.is_dir() and any(is_image(q) for q in listdir(d))
    except InputError:  # unreadable sub-folder: not a room
        return False


def _describe(path: Path) -> str:
    """What a folder holds, by file type, for error messages."""
    counts: Counter = Counter()
    try:
        for p in itertools.islice(path.rglob("*"), 5000):
            if p.is_file() and visible(p):
                counts[p.suffix.lower() or "(no extension)"] += 1
    except OSError:
        pass
    if not counts:
        return "it is empty (hidden and system files are ignored)"
    return "it holds " + ", ".join(f"{n} {ext}" for ext, n in counts.most_common(6))


def _unsupported_file(path: Path) -> str:
    ext = path.suffix.lower() or "(no extension)"
    if ext in RAW_EXT:
        return f"{path.name}: {DNG_HINT}"
    return (f"{path.name}: unsupported file type {ext}. Give a video (.mov, .mp4), a photo (.heic, .jpg, .png), "
            f"a folder of per-room photo folders, a Stray Scanner folder, or a .zip of one of these")


def _unzip(zpath: Path) -> Path:
    """Extract a .zip once into .cache/inputs/ (reused while the zip is unchanged)."""
    st = zpath.stat()
    key = hashlib.sha1(f"{zpath.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:12]
    dest = CACHE_DIR / "inputs" / key / (zpath.stem.strip() or "capture")
    done = dest.parent / ".complete"
    if done.exists() and dest.is_dir():
        return dest
    shutil.rmtree(dest.parent, ignore_errors=True)
    try:
        with zipfile.ZipFile(zpath) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            if not members:
                raise InputError(f"{zpath.name} is an empty zip file")
            need = sum(m.file_size for m in members)
            dest.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(dest).free
            if need > free:
                raise InputError(f"not enough disk space to unzip {zpath.name}: needs {need / 1e9:.1f} GB, "
                                 f"{free / 1e9:.1f} GB free")
            z.extractall(dest)  # zipfile drops absolute paths and '..' components
    except InputError:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise
    except zipfile.BadZipFile as e:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise InputError(f"{zpath.name} is not a readable zip file ({e}): copy it again, or unzip it and "
                         f"pass the folder") from e
    except (NotImplementedError, RuntimeError, OSError) as e:  # compression method, password, file names
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise InputError(f"cannot unzip {zpath.name} ({type(e).__name__}: {e}): unzip it by hand and pass "
                         f"the folder") from e
    done.write_text("ok")
    return dest


def prepare_input(path: Path) -> Path:
    """What the user passed -> the file or folder the front ends read: a .zip (or a folder
    holding nothing but one .zip) is extracted."""
    path = Path(path)
    if not path.exists():
        raise InputError(f"{path} does not exist")
    if path.is_dir():
        entries = listdir(unwrap(path))
        if len(entries) == 1 and entries[0].is_file() and entries[0].suffix.lower() == ".zip":
            path = entries[0]
    if path.is_file() and path.suffix.lower() == ".zip":
        return _unzip(path)
    return path


def detect_tier(path: Path) -> str:
    """lidar | video | photo for a capture folder or file; InputError when nothing fits.

    A folder with photos is the photo tier even when Live Photo .MOV files sit next to the
    photos; a folder is the video tier only when it holds a clip and no photos."""
    from roomscan.frontends.lidar_stray import find_stray_root

    path = Path(path)
    if not path.exists():
        raise InputError(f"{path} does not exist")
    if path.is_file():
        ext = path.suffix.lower()
        if ext in VIDEO_EXT:
            return "video"
        if ext in IMAGE_EXT:
            return "photo"
        raise InputError(_unsupported_file(path))
    if find_stray_root(path) is not None:
        return "lidar"
    inner = unwrap(path)
    for d in dict.fromkeys([path, inner]):
        missing = [n for n in ("odometry.csv", "depth/") if not (d / n).exists()]
        if len(missing) == 1 and (d / "rgb.mp4").is_file():  # a Stray Scanner export with a part missing
            raise InputError(f"{d} looks like a Stray Scanner export without its {missing[0]}: export the scan "
                             f"again, or pass {d / 'rgb.mp4'} to run it as a video")
        entries = listdir(d)
        if any(is_image(p) for p in entries) or any(_has_images(p) for p in entries):
            return "photo"
        if capture_videos(d):
            return "video"
    if any(p.suffix.lower() in RAW_EXT and visible(p) for p in itertools.islice(inner.rglob("*"), 5000)):
        raise InputError(f"{path}: {DNG_HINT}")
    raise InputError(f"cannot use {path}: {_describe(path)}. Expected a Stray Scanner folder, a video "
                     f"(.mov, .mp4), or a folder of per-room photo folders (.heic, .jpg, .png)")


def _cache_key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def cached_fuse(cap: PosedCapture, key_parts: tuple, voxel: float = 0.02, use_cache: bool = True,
                progress: bool = True) -> Cloud:
    # key on the poses actually fused: a change to drift correction must not reuse an old cloud
    poses = hashlib.sha1(np.round(np.stack([f.T_wc for f in cap.frames]), 6).tobytes()).hexdigest()[:12]
    key = _cache_key(*key_parts, voxel, len(cap.frames), poses)
    f = CACHE_DIR / f"cloud_{key}.npz"
    if use_cache and f.exists():
        z = np.load(f)
        return Cloud(z["p"], z["n"], z["w"])
    cloud = fuse_capture(cap, voxel=voxel, progress=progress)
    if use_cache:
        CACHE_DIR.mkdir(exist_ok=True)
        np.savez_compressed(f, p=cloud.points, n=cloud.normals, w=cloud.weight)
    return cloud


DRIFT_MODES = {"off": None, "loop": dict(loop_closure=True, heading=False),
               "heading": dict(loop_closure=False, heading=True),
               "loop+heading": dict(loop_closure=True, heading=True)}


def load_known_sizes(measurements, *capture_paths: Path):
    """Known sizes for a capture: `measurements` (a measurements.yaml path, a parsed dict, or
    KnownSizes) or else the measurements.yaml found next to the capture. None when there is none."""
    from roomscan import known_sizes as KS

    try:
        if isinstance(measurements, KS.KnownSizes):
            return measurements
        if isinstance(measurements, dict):
            return KS.parse(measurements, source="measurements")
        f = Path(measurements) if measurements is not None else KS.find_file(*capture_paths)
        if f is None:
            return None
        if not f.is_file():
            raise InputError(f"measurements file {f} does not exist")
        return KS.load(f)
    except KS.MeasurementsError as e:
        raise InputError(f"measurements.yaml: {e}") from e


def run(path: Path, out_dir: Path, tier: str = "auto", stride: int = 5, drift: str = "loop",
        damage: bool = True, use_cache: bool = True, progress: bool = True, measurements=None,
        on_stage=None) -> dict:
    """Process one capture. on_stage(name, status, seconds, note) is called as each stage of
    the queue (roomscan.stages) starts, finishes and passes or fails its check."""
    if tier not in TIERS:
        raise InputError(f"unknown tier '{tier}': use one of {', '.join(TIERS)}")
    if drift not in DRIFT_MODES:
        raise InputError(f"unknown drift mode '{drift}': use one of {', '.join(DRIFT_MODES)}")
    if stride < 1:
        raise InputError(f"stride must be 1 or more (got {stride})")
    given = Path(path)
    path, out_dir = prepare_input(given), Path(out_dir)
    known = load_known_sizes(measurements, given, path)
    tier = detect_tier(path) if tier == "auto" else tier
    out_dir.mkdir(parents=True, exist_ok=True)
    from roomscan.stages import Stages
    timing = Stages(tier, on_stage, skip=() if damage else ("damage",))
    t0 = time.time()
    try:
        return _run_tier(path, out_dir, tier, stride, drift, damage, use_cache, progress, known, timing, t0)
    except Exception as e:
        msg = str(e).splitlines()[0][:200] if str(e) else ""
        timing.fail_running(f"{type(e).__name__}: {msg}")
        raise
    finally:
        timing.write(out_dir)


def _run_tier(path, out_dir, tier, stride, drift, damage, use_cache, progress, known, timing, t0):
    from roomscan.stages import gate_load
    if tier == "lidar":
        from roomscan.frontends.lidar_stray import load_stray
        cap = load_stray(path, stride=stride)
        source = "stray_scanner"
    elif tier == "video":
        from roomscan.frontends.video import load_video
        cap = load_video(path, use_cache=use_cache, progress=progress)
        source = "video"
    else:
        from roomscan.frontends.photos import run_photo_tier
        return run_photo_tier(path, out_dir, use_cache=use_cache, progress=progress, damage=damage, known=known,
                              timing=timing)
    timing["load"] = time.time() - t0
    gate_load(timing, "load", len(cap.frames), tier)
    return run_posed(cap, out_dir, source=source, drift=drift, damage=damage, use_cache=use_cache,
                     progress=progress, timing=timing, key=(str(path.resolve()), tier, stride), known=known)


def run_posed(cap: PosedCapture, out_dir: Path, source: str, drift: str, damage: bool,
              use_cache: bool, progress: bool, timing: dict, key: tuple, known=None) -> dict:
    from roomscan.export.build import build_output
    from roomscan.export.render import render_plan
    from roomscan.geometry.drift import correct_drift
    from roomscan.geometry.layout import extract_layout
    from roomscan.geometry.openings import detect_openings

    warnings: list[str] = list(cap.meta.pop("input_warnings", []))  # what the loader skipped or assumed
    t = time.time()
    drift_info = {"method": "none", "enabled": False}
    if DRIFT_MODES.get(drift):
        cap, drift_info = correct_drift(cap, use_cache=use_cache, key=key, progress=progress,
                                        **DRIFT_MODES[drift])
    timing["drift"] = time.time() - t
    from roomscan import stages as S
    if isinstance(timing, S.Stages):
        S.gate_drift(timing, drift_info)

    t = time.time()
    cloud = cached_fuse(cap, key + (drift,), use_cache=use_cache, progress=progress)
    timing["fuse"] = time.time() - t
    if isinstance(timing, S.Stages):
        S.gate_fuse(timing, len(cloud.points))

    t = time.time()
    from roomscan.geometry.metrics import crispness
    drift_info["wall_crispness"] = round(crispness(cloud), 3)
    layout = extract_layout(cloud)
    timing["layout"] = time.time() - t
    if not layout.rooms and cap.tier != "lidar":
        from roomscan.geometry.boxfit import box_layout
        bl = box_layout(cloud, noise=0.08)
        if bl is not None:
            layout = bl
            warnings.append("walls do not close into rooms: capture fitted as a single rectangular room")
    ks_plan = None
    if known is not None and not known.empty() and layout.rooms:
        if cap.tier == "lidar":  # never rescaled: print the differences as a self-check
            from roomscan.known_sizes import dims_of, lidar_check
            warnings += lidar_check(known, [dims_of(r) for r in layout.rooms])
        else:
            from roomscan.frontends.video import apply_known_sizes
            cap, cloud, layout, ks_plan = apply_known_sizes(cap, cloud, layout, known, warnings)
    if not layout.rooms:
        warnings.append("no closed room found")
    if isinstance(timing, S.Stages):
        S.gate_layout(timing, layout)

    t = time.time()
    mirrors: list = []
    openings = detect_openings(cap, layout, cloud=cloud if cap.tier == "lidar" else None, mirrors=mirrors)
    for mi in mirrors:
        warnings.append(f"{mi['wall_id']}: reflective surface {mi['width']} m wide treated as a mirror, "
                        f"not reported as an opening")
    timing["openings"] = time.time() - t
    if isinstance(timing, S.Stages):
        S.gate_openings(timing, openings)

    dmg, flags, scope = [], [], []
    if damage:
        t = time.time()
        from roomscan.damage.pipeline import assess_damage
        dmg, flags, scope, dw = assess_damage(cap, layout, openings, cloud, progress=progress)
        warnings += dw
        timing["damage"] = time.time() - t
        if isinstance(timing, S.Stages):
            S.gate_damage(timing, dmg)

    t = time.time()
    info = {"id": cap.name, "tier": cap.tier, "source": source, "n_frames_used": len(cap.frames),
            "meta": {k: v for k, v in cap.meta.items() if isinstance(v, (str, int, float, list, tuple))}}
    out = build_output(layout, openings, cap.tier, info, drift_info, dmg, flags, scope, warnings, timing)
    if ks_plan is not None:
        from roomscan.known_sizes import finish
        finish(out, ks_plan, layout.rooms, known, mode="global")
    timing["export"] = time.time() - t
    out.timing_s = {k: round(v, 2) for k, v in timing.items()}
    # UTF-8, not the Windows locale code page: names come from the user's folder and file names
    (out_dir / "result.json").write_text(out.model_dump_json(indent=2), encoding="utf-8")
    render_plan(out, out_dir / "plan.png")
    res = json.loads(out.model_dump_json())
    if isinstance(timing, S.Stages):
        S.gate_export(timing, res)
    return res
