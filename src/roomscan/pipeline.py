"""End-to-end pipeline: capture folder -> JSON + rendered plan. One call per capture."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np

from roomscan.capture import PosedCapture
from roomscan.geometry.pointcloud import Cloud, fuse_capture

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic"}
CACHE_DIR = Path(".cache")


def detect_tier(path: Path) -> str:
    from roomscan.frontends.lidar_stray import find_stray_root

    if path.is_file() and path.suffix.lower() in VIDEO_EXT:
        return "video"
    if path.is_dir():
        if find_stray_root(path) is not None:
            return "lidar"
        files = [p for p in path.iterdir() if p.is_file()]
        if any(p.suffix.lower() in VIDEO_EXT for p in files) and not any(p.suffix.lower() in IMAGE_EXT for p in files):
            return "video"
        subdirs = [p for p in path.iterdir() if p.is_dir()]
        if any(p.suffix.lower() in IMAGE_EXT for p in files) or any(
                any(q.suffix.lower() in IMAGE_EXT for q in d.iterdir()) for d in subdirs):
            return "photo"
    raise ValueError(f"cannot tell which tier {path} is: expected a Stray Scanner folder, a video, "
                     f"or folders of photos")


def _cache_key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def cached_fuse(cap: PosedCapture, key_parts: tuple, voxel: float = 0.02, use_cache: bool = True,
                progress: bool = True) -> Cloud:
    key = _cache_key(*key_parts, voxel, len(cap.frames))
    f = CACHE_DIR / f"cloud_{key}.npz"
    if use_cache and f.exists():
        z = np.load(f)
        return Cloud(z["p"], z["n"], z["w"])
    cloud = fuse_capture(cap, voxel=voxel, progress=progress)
    if use_cache:
        CACHE_DIR.mkdir(exist_ok=True)
        np.savez_compressed(f, p=cloud.points, n=cloud.normals, w=cloud.weight)
    return cloud


def run(path: Path, out_dir: Path, tier: str = "auto", stride: int = 5, drift: bool = True,
        damage: bool = True, use_cache: bool = True, progress: bool = True) -> dict:
    path, out_dir = Path(path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tier = detect_tier(path) if tier == "auto" else tier
    timing: dict[str, float] = {}
    t0 = time.time()

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
        return run_photo_tier(path, out_dir, use_cache=use_cache, progress=progress, damage=damage)
    timing["load"] = time.time() - t0
    return run_posed(cap, out_dir, source=source, drift=drift, damage=damage, use_cache=use_cache,
                     progress=progress, timing=timing, key=(str(path.resolve()), tier, stride))


def run_posed(cap: PosedCapture, out_dir: Path, source: str, drift: bool, damage: bool,
              use_cache: bool, progress: bool, timing: dict, key: tuple) -> dict:
    from roomscan.export.build import build_output
    from roomscan.export.render import render_plan
    from roomscan.geometry.drift import correct_drift
    from roomscan.geometry.layout import extract_layout
    from roomscan.geometry.openings import detect_openings

    warnings: list[str] = []
    t = time.time()
    drift_info = {"method": "none", "enabled": False}
    if drift:
        cap, drift_info = correct_drift(cap, use_cache=use_cache, key=key, progress=progress)
    timing["drift"] = time.time() - t

    t = time.time()
    cloud = cached_fuse(cap, key + (drift, drift_info.get("method")), use_cache=use_cache, progress=progress)
    timing["fuse"] = time.time() - t

    t = time.time()
    layout = extract_layout(cloud)
    timing["layout"] = time.time() - t
    if not layout.rooms:
        warnings.append("no closed room found")

    t = time.time()
    openings = detect_openings(cap, layout)
    timing["openings"] = time.time() - t

    dmg, flags, scope = [], [], []
    if damage:
        t = time.time()
        from roomscan.damage.pipeline import assess_damage
        dmg, flags, scope, dw = assess_damage(cap, layout, openings, cloud, progress=progress)
        warnings += dw
        timing["damage"] = time.time() - t

    t = time.time()
    info = {"id": cap.name, "tier": cap.tier, "source": source, "n_frames_used": len(cap.frames),
            "meta": {k: v for k, v in cap.meta.items() if isinstance(v, (str, int, float, list, tuple))}}
    out = build_output(layout, openings, cap.tier, info, drift_info, dmg, flags, scope, warnings, timing)
    timing["export"] = time.time() - t
    out.timing_s = {k: round(v, 2) for k, v in timing.items()}
    (out_dir / "result.json").write_text(out.model_dump_json(indent=2))
    render_plan(out, out_dir / "plan.png")
    return json.loads(out.model_dump_json())
