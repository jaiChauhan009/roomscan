"""Photo-tier test set cut from a Stray Scanner capture (development proxy).

No iPhone stills exist for the sample apartment, so stills are cut from the scan's RGB
video in the shape the photo protocol prescribes:
  * per room, 2-5 "sweep" frames taken near its entry doorway looking into the room,
    ordered left to right
  * for every room but the first, one "look-back" frame at the same doorway looking into
    the room it was entered from (saved last in the folder)
Depth and poses are thrown away; only JPEGs with an EXIF focal length are written.
truth.json records which LiDAR room each folder is (for evaluation only; the photo
pipeline never reads it). Video frames are softer and lower resolution than real iPhone
photos and were not shot from a fixed standpoint, so the set is a pessimistic stand-in.

    make_photo_set.py <scan> <out>             the benchmark set, from bench/photo_set_recipe.yaml
    make_photo_set.py <scan> <out> --select    a new set, chosen from the scan's current layout
    make_photo_set.py --check <set>            compare a set with the recipe, file by file

Default: rebuild the benchmark set (capture apt_photo_a) byte for byte. The recipe gives
per photo the video frame, rotation and focal length, the truth.json content, and the
SHA-256 of every file, which is checked after writing. Only the scan's rgb.mp4 is read, so
changes to the LiDAR pipeline cannot change the set.

--select: rooms are ordered by a breadth-first walk over the door graph from the
best-connected room; per room the sharpest frames near the doorway are kept. The choice
follows the LiDAR layout, so it changes whenever the layout code does: an earlier version
of this mode cut the benchmark set on 2026-10-01, and the same scan now gives other rooms
and frames.

The set is written to <out>.part (removed first if an interrupted run left it) and renamed
to <out> when complete; <out> must not exist or must be empty.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import piexif
import yaml
from PIL import Image

RECIPE = Path(__file__).resolve().parent.parent / "bench" / "photo_set_recipe.yaml"
QUALITY = 93
ROTATE_CW = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def focal_35mm(K: np.ndarray) -> int:
    """35 mm-equivalent focal length of an RGB camera matrix (43.267 mm: 35 mm film diagonal)."""
    return int(round(K[0, 0] / np.hypot(2 * K[0, 2], 2 * K[1, 2]) * 43.267))


def save(img: np.ndarray, f35: int, path: Path, quality: int = QUALITY) -> None:
    exif = piexif.dump({"Exif": {piexif.ExifIFD.FocalLengthIn35mmFilm: int(f35)}})
    Image.fromarray(img).save(path, quality=quality, exif=exif)


def write_truth(path: Path, truth: dict) -> None:
    """json.dumps(indent=2) with CRLF line ends: the recorded set was written by
    Path.write_text on Windows; fixed here so the bytes are the same on every OS."""
    path.write_bytes(json.dumps(truth, indent=2).replace("\n", "\r\n").encode())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- benchmark set from the recipe

def load_recipe(path: Path = RECIPE) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def find_video(scan: Path, recipe: dict) -> Path:
    """The recipe's video in `scan` (the scan folder or the export folder inside it), checked by SHA-256."""
    v = recipe["video"]
    rel = Path(v["path"])
    path = next((p for p in (scan / rel, scan / rel.name) if p.is_file()), None)
    if path is None:
        raise SystemExit(f"{scan}: no {rel.as_posix()} in it; the recipe is cut from the scan {recipe['scan']}")
    if path.stat().st_size != v["bytes"] or sha256(path) != v["sha256"]:
        raise SystemExit(f"{path} is not the video the recipe is cut from (SHA-256 differs): it needs the scan "
                         f"{recipe['scan']}; for other scans use --select")
    return path


def frames_at(video: Path, positions: set[int]):
    """Yield (position, RGB frame) for 0-based frame positions, reading the video from the start.

    Not a cv2 seek: on a variable-frame-rate video CAP_PROP_POS_FRAMES = N lands at
    N / average fps, not on the N-th frame."""
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)  # frames as stored, as the pipeline reads them
    try:
        for k in range(max(positions) + 1):
            if k in positions:
                ok, img = cap.read()
            else:
                ok, img = cap.grab(), None
            if not ok:
                raise SystemExit(f"{video}: cannot read frame {k} (the recipe needs frames up to {max(positions)})")
            if img is not None:
                yield k, cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    finally:
        cap.release()


def build_from_recipe(scan: Path, out: Path, recipe: dict) -> None:
    video = find_video(Path(scan), recipe)
    todo: dict[int, list[tuple[Path, dict]]] = {}  # video frame -> photos cut from it
    for folder, photos in recipe["photos"].items():
        (out / folder).mkdir(parents=True)
        for p in photos:
            todo.setdefault(p["video_frame"], []).append((out / folder / p["file"], p))
    print(f"cutting {sum(map(len, todo.values()))} photos from {video} (reads frames 0-{max(todo)})", flush=True)
    for k, img in frames_at(video, set(todo)):
        for dest, p in todo[k]:
            rot = ROTATE_CW[p["rotate_cw"]]
            save(img if rot is None else cv2.rotate(img, rot), p["focal_35mm"], dest, recipe["jpeg_quality"])
    write_truth(out / "truth.json", recipe["truth"])


def check(set_dir: Path, recipe: dict) -> list[str]:
    """How a photo set folder differs from the recipe, one line per file (empty: identical)."""
    want = {f"{folder}/{p['file']}": p["sha256"] for folder, photos in recipe["photos"].items() for p in photos}
    want["truth.json"] = recipe["truth_sha256"]
    have = {p.relative_to(set_dir).as_posix(): p for p in Path(set_dir).rglob("*") if p.is_file()}
    return ([f"missing: {n}" for n in want if n not in have]
            + [f"differs: {n}" for n in want if n in have and sha256(have[n]) != want[n]]
            + [f"not in the recipe: {n}" for n in sorted(have) if n not in want])


# ---------------------------------------------------------------- new set from the current layout

def upright(img: np.ndarray, T_wc: np.ndarray) -> np.ndarray:
    """Rotate by k*90 deg so that world up is image up."""
    up_in_cam = T_wc[:3, :3].T @ np.array([0, 1.0, 0])  # world up in camera axes (x right, y down)
    ang = np.degrees(np.arctan2(up_in_cam[0], -up_in_cam[1]))  # 0 = already upright
    k = int(np.round(ang / 90)) % 4
    return {0: img, 1: cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE), 2: cv2.rotate(img, cv2.ROTATE_180),
            3: cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)}[k]


def heading(v: np.ndarray) -> float:
    return float(np.arctan2(v[0], -v[1]))  # plan (a, b): 0 = -b, clockwise positive


def select_frames(scan: Path, out: Path) -> None:
    from roomscan.frontends.lidar_stray import load_stray
    from roomscan.geometry.layout import extract_layout
    from roomscan.geometry.openings import detect_openings
    from roomscan.pipeline import cached_fuse

    cap = load_stray(Path(scan), stride=5)
    cloud = cached_fuse(cap, (str(Path(scan).resolve()), "lidar", "photoset"), progress=False)
    layout = extract_layout(cloud)
    ops = [o for o in detect_openings(cap, layout) if o.connects and o.kind != "window"]
    fr = layout.frame
    walls = {w.id: w for r in layout.rooms for w in r.walls}
    by_label = {r.label_id: r.id for r in layout.rooms}

    cam_ab = fr.to_plan(np.array([f.T_wc[:3, 3][[0, 2]] for f in cap.frames]))
    fwd3 = np.array([f.T_wc[:3, 2] for f in cap.frames])
    fwd_ab = fr.to_plan(fwd3[:, [0, 2]])
    fwd_ab /= np.maximum(np.linalg.norm(fwd_ab, axis=1, keepdims=True), 1e-6)
    pitch = fwd3[:, 1]
    r, c = fr.to_cell(cam_ab)
    okc = (r >= 0) & (r < fr.shape[0]) & (c >= 0) & (c < fr.shape[1])
    room_of = np.array([by_label.get(int(layout.labels[rr, cc]), None) if o else None
                        for rr, cc, o in zip(r, c, okc)], dtype=object)

    sharp_cache = {}

    def sharp(i):
        if i not in sharp_cache:
            img = cap.frames[i].rgb_fn()
            sharp_cache[i] = (0.0, None) if img is None else (
                float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY), cv2.CV_32F).var()), img)
        return sharp_cache[i]

    def pick(idx, n_try=10):
        best = None
        for i in idx[:: max(1, len(idx) // n_try)]:
            s, img = sharp(i)
            if img is not None and (best is None or s > best[0]):
                best = (s, i)
        return None if best is None else best[1]

    # door graph and walk order
    doors = {}
    for o in ops:
        w = walls[o.wall_id]
        d = (w.end - w.start) / w.length
        centre = w.start + d * (o.u0 + o.u1) / 2
        doors.setdefault(frozenset([o.room_id, o.connects]), (centre, o.room_id, w.inward.copy()))
    # start from the best-connected room (the hub); the scan's first room may have no linked door
    degree = {}
    for key in doors:
        for rid_ in key:
            degree[rid_] = degree.get(rid_, 0) + 1
    root = max(degree, key=degree.get) if degree else layout.rooms[0].id
    order, parent = [root], {root: None}
    queue = [root]
    while queue:
        cur = queue.pop(0)
        for key in doors:
            if cur in key:
                other = next(iter(key - {cur}))
                if other not in parent:
                    parent[other] = cur
                    order.append(other)
                    queue.append(other)

    truth = {}
    for n, rid in enumerate(order, start=1):
        if parent[rid] is None:
            first = int(np.argmax(room_of == rid))
            stand = cam_ab[first]
            centroid = next(r_.polygon.mean(0) for r_ in layout.rooms if r_.id == rid)
            v = centroid - stand
            n_in = np.array([np.sign(v[0]), 0.0]) if abs(v[0]) > abs(v[1]) else np.array([0.0, np.sign(v[1])])
            door = stand
        else:
            centre, wall_room, inward = doors[frozenset([rid, parent[rid]])]
            n_in = inward if wall_room == rid else -inward
            door = centre
            stand = centre + 0.15 * n_in
        ang = np.degrees(np.mod(np.array([heading(f) for f in fwd_ab]) - heading(n_in) + np.pi, 2 * np.pi) - np.pi)
        dist = np.linalg.norm(cam_ab - stand, axis=1)
        chosen = []
        for radius in (0.7, 1.1, 1.6):
            near = (dist < radius) & (np.abs(pitch) < 0.35) & (np.abs(ang) < 75)
            chosen = []
            for lo in (-75, -37.5, 0, 37.5):
                idx = np.where(near & (ang >= lo) & (ang < lo + 37.5))[0]
                if len(idx):
                    p = pick(list(idx))
                    if p is not None:
                        chosen.append(p)
            if len(chosen) >= 3:
                break
        if len(chosen) < 2:
            print(rid, "skipped: fewer than 2 usable frames near its doorway")
            continue
        chosen.sort(key=lambda i: ang[i])
        look = None
        if parent[rid] is not None:
            idx = np.where((np.linalg.norm(cam_ab - door, axis=1) < 1.0) & (np.abs(pitch) < 0.35)
                           & (np.abs(np.abs(ang) - 180) < 35))[0]
            look = pick(list(idx)) if len(idx) else None
            if look is None:
                print(rid, "skipped: no frame looking back through its entry door")
                continue
        d = out / f"{n:02d}_{rid}"
        d.mkdir(parents=True, exist_ok=True)
        files = chosen + ([look] if look is not None else [])
        for k, i in enumerate(files, start=1):
            f = cap.frames[i]
            tag = "lookback" if (look is not None and k == len(files)) else "sweep"
            save(upright(sharp(i)[1], f.T_wc), focal_35mm(f.K_rgb), d / f"{k:02d}_{tag}_frame{f.index:06d}.jpg")
        truth[d.name] = {"lidar_room": rid, "parent": parent[rid], "n_sweep": len(chosen),
                         "has_look_back": look is not None,
                         "sweep_angles_deg": [round(float(ang[i]), 1) for i in chosen]}
        print(d.name, "sweep", len(chosen), [round(float(ang[i])) for i in chosen], "look-back", look is not None,
              "parent", parent[rid])
    write_truth(out / "truth.json", truth)


# ---------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].splitlines()[0],
                                 epilog="See the top of this file for the two modes.")
    ap.add_argument("scan", nargs="?", type=Path, help="Stray Scanner export the stills are cut from")
    ap.add_argument("out", nargs="?", type=Path, help="folder for the set (must not exist or must be empty)")
    ap.add_argument("--select", action="store_true",
                    help="choose rooms and frames from the scan's current LiDAR layout (a new set; no recipe)")
    ap.add_argument("--recipe", type=Path, default=RECIPE, help="recipe to rebuild from or check against "
                                                                "(default: bench/photo_set_recipe.yaml)")
    ap.add_argument("--check", type=Path, metavar="SET",
                    help="compare an existing set with the recipe, file by file; exit status 1 if they differ")
    a = ap.parse_args(argv)
    if a.check:
        diffs = check(a.check, load_recipe(a.recipe))
        print("\n".join(diffs) if diffs else
              f"{a.check}: identical to the set in {a.recipe.name} (SHA-256 of every photo and truth.json)")
        return 1 if diffs else 0
    if a.scan is None or a.out is None:
        ap.error("scan and out are required (or --check SET)")
    out = a.out
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise SystemExit(f"{out} already exists: remove it or pass a new folder")
    recipe = None if a.select else load_recipe(a.recipe)
    part = out.with_name(out.name + ".part")
    shutil.rmtree(part, ignore_errors=True)  # left by an interrupted run
    part.mkdir(parents=True)
    try:
        if recipe is None:
            select_frames(a.scan, part)
        else:
            build_from_recipe(a.scan, part, recipe)
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise
    if out.exists():
        out.rmdir()
    part.rename(out)
    if recipe is None:
        print("wrote", out)
        return 0
    diffs = check(out, recipe)
    n = sum(map(len, recipe["photos"].values()))
    if diffs:
        print(f"WARNING: {len(diffs)} of {n + 1} files differ from the recorded set in {a.recipe.name} (another "
              f"OpenCV/FFmpeg or Pillow build?), so photo-tier results may differ from bench/reports:")
        print("\n".join("  " + d for d in diffs))
    else:
        print(f"wrote {out}: {len(recipe['photos'])} rooms, {n} photos and truth.json, identical to the recorded "
              f"set (SHA-256 of every file)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
