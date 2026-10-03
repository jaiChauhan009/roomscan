"""Synthetic RoomPlan captures (capture.json of our iOS app, format roomscan.roomplan/1).

Truth: a Kitchen 4.0 x 3.0 m (x 0..4, z 0..3) and a Hall 3.0 x 3.0 m (x 4..7) sharing the wall
x = 4, walls 2.6 m high, floor at y = 0. A door 0.9 x 2.1 m in the shared wall (listed by the
Kitchen, on its wall by wall_id), a window 1.2 x 1.0 m with a 1.0 m sill in the Kitchen's z = 0
wall, an opening 1.0 x 2.0 m in the Hall's far wall (no wall_id: goes to the nearest wall).

Variants: "merged" (one world frame, floor polygons), "no_floor" (no floor polygons, walls
shuffled, one reversed, corners jittered by up to 2 cm), "separate" (merged = false: the Hall
in its own frame, on top of the Kitchen), "single" (the Kitchen alone, one room per zip as the
app uploads it), "malformed".
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np

H = 2.6
KITCHEN = (0.0, 0.0, 4.0, 3.0)
HALL = (4.0, 0.0, 7.0, 3.0)
TRUTH = {"Kitchen": {"area": 12.0, "sides": (4.0, 3.0)}, "Hall": {"area": 9.0, "sides": (3.0, 3.0)}}
FRAME_W, FRAME_H = 64, 48
K = np.array([[20.0, 0, 32.0], [0, 20.0, 24.0], [0, 0, 1]])  # wide: floor, wall and ceiling in view


def rect_walls(prefix: str, x0, z0, x1, z1, conf: str = "high") -> list[dict]:
    c = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    return [{"id": f"{prefix}{i}", "start": list(c[i]), "end": list(c[(i + 1) % 4]), "height": H,
             "thickness": 0.0, "confidence": conf} for i in range(4)]


def look_minus_x(cam: tuple[float, float, float]) -> list[float]:
    """ARKit camera-to-world (column-major 16 floats) at `cam` looking along world -x."""
    T = np.eye(4)
    T[:3, 0] = (0, 0, -1)  # camera x (right)
    T[:3, 1] = (0, 1, 0)  # camera y (up)
    T[:3, 2] = (1, 0, 0)  # camera z (backward): looking along -x
    T[:3, 3] = cam
    return T.flatten(order="F").tolist()


def room(name: str, index: int, box, walls_prefix: str, floor: bool, labels: list[str]) -> dict:
    x0, z0, x1, z1 = box
    return {"name": name, "index": index, "story": 0, "walls": rect_walls(walls_prefix, *box),
            "doors": [], "windows": [], "openings": [],
            "floor": {"polygon": [[x0, z0], [x1, z0], [x1, z1], [x0, z1]], "y": 0.0} if floor else None,
            "objects": [{"category": "table", "center": [(x0 + x1) / 2, 0.4, (z0 + z1) / 2],
                         "dimensions": [1.2, 0.8, 0.8], "yaw": 0.0}],
            "section_labels": labels}


def capture(variant: str = "merged", frames_per_room: int = 0) -> dict:
    if variant == "malformed":
        return {"format": "roomscan.roomplan/1", "rooms": [{"name": "Bad", "walls": [{"start": [0, 0], "end": "x",
                                                                                      "height": 2.6}]}]}
    floor = variant in ("merged", "separate", "single")
    k = room("Kitchen", 0, KITCHEN, "K", floor, ["kitchen"])
    k["doors"].append({"id": "D1", "wall_id": "K1", "center": [4.0, 1.05, 1.5], "width": 0.9, "height": 2.1,
                       "confidence": "high", "is_open": None})
    k["windows"].append({"id": "W1", "wall_id": "K0", "center": [2.0, 1.5, 0.0], "width": 1.2, "height": 1.0,
                         "confidence": "medium", "is_open": None})
    rooms = [k]
    if variant != "single":
        hall = room("Hall", 1, HALL, "H", floor, [])
        hall["openings"].append({"id": "O1", "wall_id": None, "center": [7.0, 1.0, 1.5], "width": 1.0, "height": 2.0,
                                 "confidence": "high", "is_open": None})
        rooms.append(hall)
    if variant == "no_floor":
        rng = np.random.default_rng(3)
        for r in rooms:
            for w in r["walls"]:  # jitter shared corners consistently: same offset for the same corner
                for key in ("start", "end"):
                    p = w[key]
                    off = (np.sin(7 * p[0] + 3 * p[1]) * 0.015, np.cos(5 * p[0] - 2 * p[1]) * 0.015)
                    w[key] = [p[0] + off[0], p[1] + off[1]]
            r["walls"] = [r["walls"][i] for i in rng.permutation(4)]
            w = r["walls"][1]
            w["start"], w["end"] = w["end"], w["start"]
    if variant == "separate":  # the Hall in its own frame: shifted onto the Kitchen
        hall = rooms[1]
        for w in hall["walls"]:
            w["start"], w["end"] = [w["start"][0] - 4.0, w["start"][1]], [w["end"][0] - 4.0, w["end"][1]]
        hall["floor"]["polygon"] = [[x - 4.0, z] for x, z in hall["floor"]["polygon"]]
        for o in hall["openings"]:
            o["center"][0] -= 4.0
    frames = []
    for ri, r in enumerate(rooms):
        xs = [p for w in r["walls"] for p in (w["start"][0], w["end"][0])]
        cx = (min(xs) + max(xs)) / 2
        for j in range(frames_per_room):
            frames.append({"file": f"frames/{ri:02d}{j:04d}.jpg", "room_index": r["index"], "t": float(ri * 100 + j),
                           "transform": look_minus_x((cx + 0.1 * j, 1.4, 1.5)),
                           "intrinsics": K.flatten(order="F").tolist(), "width": FRAME_W, "height": FRAME_H})
    return {"format": "roomscan.roomplan/1", "app_version": "0.1.0",
            "device": {"model": "iPhone16,1", "system": "iOS 17.5"}, "captured_at": "2026-10-03T14:00:00Z",
            "units": "m", "coordinate_frame": "arkit_world_y_up", "merged": variant != "separate",
            "rooms": rooms, "frames": frames}


def _jpeg(seed: int) -> bytes:
    img = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    cv2.circle(img, (10 + seed % 40, 20), 5, (90, 80, 70), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


def write_folder(d: Path, variant: str = "merged", frames_per_room: int = 0, data: dict | None = None) -> Path:
    data = data if data is not None else capture(variant, frames_per_room)
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    (d / "capture.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    for i, f in enumerate(data.get("frames") or []):
        (d / f["file"]).parent.mkdir(parents=True, exist_ok=True)
        (d / f["file"]).write_bytes(_jpeg(i))
    (d / "room.usdz").write_bytes(b"PK usdz stand-in")
    return d


def zip_bytes(variant: str = "merged", frames_per_room: int = 0, data: dict | None = None) -> bytes:
    data = data if data is not None else capture(variant, frames_per_room)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("capture.json", json.dumps(data))
        for i, f in enumerate(data.get("frames") or []):
            z.writestr(f["file"], _jpeg(i))
        z.writestr("room.usdz", b"PK usdz stand-in")
    return buf.getvalue()


def single_room(name: str, box, index: int = 0, session: str | None = None, merged: bool = True,
                walls_prefix: str = "W", frames_per_room: int = 0) -> dict:
    """One room per capture, as the app uploads each room right after scanning it."""
    d = capture("single", frames_per_room)
    r = room(name, index, box, walls_prefix, True, [name.lower()])
    d["rooms"] = [r]
    d["merged"] = merged
    if session is not None:
        d["session_id"] = session
    return d
