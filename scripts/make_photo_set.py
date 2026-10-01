"""Build a photo-tier test set from a Stray Scanner capture (development proxy).

No iPhone stills exist for the sample apartment, so stills are cut from the scan's RGB
video in the shape the photo protocol prescribes:
  * per room, 2-5 "sweep" frames taken near its entry doorway looking into the room,
    ordered left to right
  * for every room but the first, one "look-back" frame at the same doorway looking into
    the room it was entered from (saved last in the folder)
Rooms are ordered by a breadth-first walk over the door graph from the room where the
scan starts. Depth and poses are thrown away; only JPEGs with an EXIF focal length are
written. truth.json records which LiDAR room each folder is (for evaluation only; the
photo pipeline never reads it).

Video frames are softer and lower resolution than real iPhone photos and were not shot
from a fixed standpoint, so this set is a pessimistic stand-in.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import piexif
from PIL import Image

from roomscan.frontends.lidar_stray import load_stray
from roomscan.geometry.layout import extract_layout
from roomscan.geometry.openings import detect_openings
from roomscan.pipeline import cached_fuse


def upright(img: np.ndarray, T_wc: np.ndarray) -> np.ndarray:
    """Rotate by k*90 deg so that world up is image up."""
    up_in_cam = T_wc[:3, :3].T @ np.array([0, 1.0, 0])  # world up in camera axes (x right, y down)
    ang = np.degrees(np.arctan2(up_in_cam[0], -up_in_cam[1]))  # 0 = already upright
    k = int(np.round(ang / 90)) % 4
    return {0: img, 1: cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE), 2: cv2.rotate(img, cv2.ROTATE_180),
            3: cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)}[k]


def save(img: np.ndarray, K: np.ndarray, path: Path) -> None:
    f35 = K[0, 0] / np.hypot(2 * K[0, 2], 2 * K[1, 2]) * 43.267
    exif = piexif.dump({"Exif": {piexif.ExifIFD.FocalLengthIn35mmFilm: int(round(f35))}})
    Image.fromarray(img).save(path, quality=93, exif=exif)


def heading(v: np.ndarray) -> float:
    return float(np.arctan2(v[0], -v[1]))  # plan (a, b): 0 = -b, clockwise positive


def main(scan: str, out: str):
    out = Path(out)
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
            save(upright(sharp(i)[1], f.T_wc), f.K_rgb, d / f"{k:02d}_{tag}_frame{f.index:06d}.jpg")
        truth[d.name] = {"lidar_room": rid, "parent": parent[rid], "n_sweep": len(chosen),
                         "has_look_back": look is not None,
                         "sweep_angles_deg": [round(float(ang[i]), 1) for i in chosen]}
        print(d.name, "sweep", len(chosen), [round(float(ang[i])) for i in chosen], "look-back", look is not None,
              "parent", parent[rid])
    (out / "truth.json").write_text(json.dumps(truth, indent=2))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
