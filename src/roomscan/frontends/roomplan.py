"""RoomPlan tier front end: the zip our own iOS app writes (Apple RoomPlan live capture).

Zip / folder layout::

    capture.json    rooms (walls, doors, windows, openings, floor, objects) and frame poses
    frames/*.jpg    colour keyframes (optional: without them there is no damage detection)
    room.usdz       RoomPlan's own 3D model (not read: capture.json carries what is used)

capture.json is a fixed contract with the app ("format": "roomscan.roomplan/1"). Coordinates
are ARKit world: metres, +Y up; the plan is (x, z) as given (no rotation), so a plan point
is world (x, z). Frame transforms are camera-to-world in ARKit's camera convention (x right,
y up, z backward), 16 floats column-major; intrinsics 9 floats column-major at the frame's
width x height.

What the engine does with it (no point cloud, no depth model):
  * each room's outline: the floor polygon when given; else the wall segments chained into
    a closed outline (endpoints within SNAP joined, ordered by connectivity); if they do not
    close, the convex hull of the wall endpoints (with a warning)
  * walls are RoomPlan's walls (length = segment length); ceiling height = median wall height
  * doors / windows / openings go on the wall named by wall_id, else on the nearest wall
  * merged = false: the rooms have independent frames; they are laid out side by side (no
    overlap) and adjacency between them is unknown (a warning says so). Several captures given
    together (one zip per room, see find_roomplan_roots) are kept in their world frame when
    every one says merged = true and no two rooms overlap; otherwise laid out side by side.
  * damage: depth is not recorded, so each frame's depth is rendered from the RoomPlan walls,
    floors and ceilings (ray cast from the frame's pose and intrinsics; doors and openings are
    holes). The existing detector (damage.detect) then puts CLIP tiles and region outlines on
    the RoomPlan surfaces exactly as it does with LiDAR depth. Furniture is not in the rendered
    depth, so a tile on a sofa in front of a wall is attributed to the wall behind it.

Accuracy: RoomPlan's own error (published / reported: ~1-3 cm on walls, ~1 % on room sides) is
the 'roomplan' prior in uncertainty/calibration.yaml, not yet calibrated against truth. Each
item's RoomPlan confidence sets its raw sigma (CONF_SIGMA): medium and low widen the interval.
"""
from __future__ import annotations

import json
import math
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import MultiPoint, Point, Polygon
from shapely.geometry.polygon import orient

from roomscan.capture import Frame, PosedCapture
from roomscan.geometry.layout import RES, Layout, PlanFrame, Room, Wall
from roomscan.geometry.openings import Opening
from roomscan.geometry.planes import Level
from roomscan.pipeline import InputError, listdir, visible

FORMAT = "roomscan.roomplan/1"
FORMAT_PREFIX = "roomscan.roomplan/"
SOURCE = "roomplan_app"
CONFIDENCES = ("high", "medium", "low")
# raw 1-sigma (m) of a wall plane / an opening edge by RoomPlan's confidence; the tier prior
# (calibration.yaml 'roomplan') is added on top. high: RoomPlan's typical 1-3 cm; medium and
# low are guesses (no truth yet), wide on purpose.
CONF_SIGMA = {"high": 0.01, "medium": 0.03, "low": 0.08}
FLOOR_SIGMA = 0.005  # RoomPlan's floor is ARKit's plane estimate
SNAP = 0.05  # m: wall endpoints this close are one corner
MIN_WALL = 0.05  # m: shorter wall segments are ignored
SIDE_GAP = 1.0  # m between rooms laid out side by side
DOOR_NEAR = 0.30  # m: a door this close to another room's outline connects the two rooms
OVERLAP_MAX = 0.5  # m2: rooms of separate captures overlapping more are not in one frame
DAMAGE_FRAMES_PER_ROOM = 40  # evenly spaced candidate frames per room for damage detection
DEPTH_W = 256  # rendered depth width (ARKit's own depth map is 256x192)


# ---------------------------------------------------------------- finding the capture

def read_format(d: Path) -> str | None:
    """The "format" of d/capture.json, or None when there is none / it is not JSON."""
    f = Path(d) / "capture.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return "unreadable"
    return str(data.get("format")) if isinstance(data, dict) and data.get("format") is not None else "unknown"


def is_roomplan_dir(d: Path) -> bool:
    """A folder with a capture.json: the parser then says what is wrong with it, if anything."""
    return read_format(d) is not None


def zip_capture_json(zpath: Path) -> dict | None:
    """capture.json of a zip (top level or one folder down) without extracting it; None when the
    zip holds none. A capture.json that is not JSON gives {"_error": message}."""
    try:
        with zipfile.ZipFile(zpath) as z:
            names = [n for n in z.namelist() if n.replace("\\", "/").rsplit("/", 1)[-1] == "capture.json"
                     and n.replace("\\", "/").count("/") <= 1 and "__MACOSX" not in n]
            if not names:
                return None
            raw = z.read(sorted(names, key=len)[0])
    except (OSError, zipfile.BadZipFile, NotImplementedError, RuntimeError, KeyError):
        return None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        return {"_error": f"capture.json is not valid JSON ({e})"}
    return data if isinstance(data, dict) else {"_error": "capture.json is not a JSON object"}


def find_roomplan_roots(path: Path, max_depth: int = 2) -> list[Path]:
    """The capture folders (holding capture.json) at `path` or up to two levels below it, in
    natural order; zips of RoomPlan captures inside a folder are extracted (pipeline cache).
    [] when there is none."""
    from roomscan.pipeline import _unzip

    path = Path(path)
    if path.is_file():
        return []
    if is_roomplan_dir(path):
        return [path]
    found: list[Path] = []
    level = [path]
    for _ in range(max_depth):
        nxt = []
        for d in level:
            try:
                entries = listdir(d)
            except InputError:
                continue
            for e in entries:
                if e.is_dir():
                    (found if is_roomplan_dir(e) else nxt).append(e)
                elif e.suffix.lower() == ".zip" and zip_capture_json(e) is not None:
                    inner = _unzip(e)
                    found += [inner] if is_roomplan_dir(inner) else [q for q in listdir(inner)
                                                                    if q.is_dir() and is_roomplan_dir(q)]
        if found:
            break
        level = nxt
    return found


# ---------------------------------------------------------------- parsing

@dataclass
class RPWall:
    uid: str
    start: np.ndarray  # plan (x, z)
    end: np.ndarray
    height: float
    confidence: str

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.end - self.start))


@dataclass
class RPOpening:
    kind: str  # door | window | opening
    uid: str
    wall_uid: str | None
    center: np.ndarray  # world (x, y, z)
    width: float
    height: float
    confidence: str


@dataclass
class RPRoom:
    name: str
    index: int
    story: int
    walls: list[RPWall]
    openings: list[RPOpening]
    floor_poly: np.ndarray | None
    floor_y: float | None
    objects: list[dict]
    section_labels: list[str]
    capture: str  # name of the capture (folder) it came from


@dataclass
class RPFrame:
    file: Path
    room: int  # index into RoomPlanCapture.rooms
    t: float
    T_ar: np.ndarray  # 4x4 camera-to-world, ARKit camera axes
    K: np.ndarray  # 3x3 at width x height
    width: int
    height: int


@dataclass
class RoomPlanCapture:
    name: str
    rooms: list[RPRoom]
    frames: list[RPFrame]
    meta: dict
    warnings: list[str] = field(default_factory=list)
    side_by_side: bool = False


def _num(v, where: str, positive: bool = False, allow_zero: bool = True) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise InputError(f"capture.json: {where} must be a number (got {json.dumps(v)[:40]})")
    if positive and (v < 0 or (v == 0 and not allow_zero)):
        raise InputError(f"capture.json: {where} must be {'positive' if not allow_zero else '0 or more'} (got {v})")
    return float(v)


def _vec(v, n: int, where: str) -> np.ndarray:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise InputError(f"capture.json: {where} must be a list of {n} numbers (got {json.dumps(v)[:60]})")
    return np.array([_num(x, f"{where}[{i}]") for i, x in enumerate(v)], float)


def _list(d: dict, key: str, where: str, required: bool = False) -> list:
    v = d.get(key)
    if v is None:
        if required:
            raise InputError(f"capture.json: {where} has no '{key}' list")
        return []
    if not isinstance(v, list):
        raise InputError(f"capture.json: {where}.{key} must be a list")
    return v


def _conf(v, where: str, notes: list[str]) -> str:
    if v is None:
        return "medium"
    c = str(v).lower()
    if c not in CONFIDENCES:
        notes.append(f"{where}: unknown confidence '{v}', treated as low")
        return "low"
    return c


def _mat(v, n: int, where: str, last_row: tuple | None) -> np.ndarray:
    """n x n matrix from n*n column-major floats; a row-major list (recognised by last_row) is
    accepted too."""
    a = _vec(v, n * n, where)
    col = a.reshape(n, n).T
    if last_row is None:
        return col
    if np.allclose(col[-1], last_row, atol=1e-6):
        return col
    row = a.reshape(n, n)
    if np.allclose(row[-1], last_row, atol=1e-6):
        return row
    raise InputError(f"capture.json: {where} is not a valid {n}x{n} column-major matrix (last row "
                     f"{np.round(col[-1], 4).tolist()}, expected {list(last_row)})")


def parse_capture(root: Path, notes: list[str]) -> tuple[dict, list[RPRoom], list[RPFrame]]:
    """Read and validate root/capture.json. InputError with a plain message when it cannot be used."""
    root = Path(root)
    f = root / "capture.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
    except OSError as e:
        raise InputError(f"cannot read {f}: {e.strerror or e}") from e
    except ValueError as e:
        raise InputError(f"{f.name} in {root.name} is not valid JSON ({e}): export the scan again") from e
    if not isinstance(data, dict):
        raise InputError(f"{f.name} in {root.name} is not a JSON object")
    fmt = data.get("format")
    if not isinstance(fmt, str) or not fmt.startswith(FORMAT_PREFIX):
        raise InputError(f"{root.name}/capture.json: format is {json.dumps(fmt)}, expected \"{FORMAT}\"")
    if fmt != FORMAT:
        raise InputError(f"{root.name}/capture.json: format {fmt} is newer than this engine reads ({FORMAT}): "
                         f"update roomscan")
    units = data.get("units", "m")
    if units != "m":
        raise InputError(f"capture.json: units must be \"m\" (got {json.dumps(units)})")
    cf = data.get("coordinate_frame", "arkit_world_y_up")
    if cf != "arkit_world_y_up":
        raise InputError(f"capture.json: coordinate_frame must be \"arkit_world_y_up\" (got {json.dumps(cf)})")
    merged = data.get("merged", True)
    if not isinstance(merged, bool):
        raise InputError(f"capture.json: merged must be true or false (got {json.dumps(merged)})")
    rooms_raw = _list(data, "rooms", "the capture", required=True)
    if not rooms_raw:
        raise InputError(f"{root.name}/capture.json has no rooms: scan at least one room")
    rooms: list[RPRoom] = []
    for i, r in enumerate(rooms_raw):
        where = f"rooms[{i}]"
        if not isinstance(r, dict):
            raise InputError(f"capture.json: {where} must be an object")
        name = str(r.get("name") or f"Room {i + 1}").strip() or f"Room {i + 1}"
        walls = []
        for k, w in enumerate(_list(r, "walls", where)):
            ww = f"{where}.walls[{k}]"
            if not isinstance(w, dict):
                raise InputError(f"capture.json: {ww} must be an object")
            walls.append(RPWall(uid=str(w.get("id") or f"{i}_{k}"), start=_vec(w.get("start"), 2, f"{ww}.start"),
                                end=_vec(w.get("end"), 2, f"{ww}.end"),
                                height=_num(w.get("height"), f"{ww}.height", positive=True, allow_zero=False),
                                confidence=_conf(w.get("confidence"), f"{name} wall {k + 1}", notes)))
            if w.get("thickness") not in (None, 0, 0.0):
                _num(w.get("thickness"), f"{ww}.thickness", positive=True)
        ops = []
        for key, kind in (("doors", "door"), ("windows", "window"), ("openings", "opening")):
            for k, o in enumerate(_list(r, key, where)):
                ow = f"{where}.{key}[{k}]"
                if not isinstance(o, dict):
                    raise InputError(f"capture.json: {ow} must be an object")
                ops.append(RPOpening(kind=kind, uid=str(o.get("id") or f"{key}{k}"),
                                     wall_uid=None if o.get("wall_id") in (None, "") else str(o.get("wall_id")),
                                     center=_vec(o.get("center"), 3, f"{ow}.center"),
                                     width=_num(o.get("width"), f"{ow}.width", positive=True, allow_zero=False),
                                     height=_num(o.get("height"), f"{ow}.height", positive=True, allow_zero=False),
                                     confidence=_conf(o.get("confidence"), f"{name} {kind} {k + 1}", notes)))
        floor_poly, floor_y = None, None
        fl = r.get("floor")
        if fl is not None:
            if not isinstance(fl, dict):
                raise InputError(f"capture.json: {where}.floor must be an object or null")
            pts = _list(fl, "polygon", f"{where}.floor")
            if pts:
                floor_poly = np.array([_vec(p, 2, f"{where}.floor.polygon[{k}]") for k, p in enumerate(pts)])
                if len(floor_poly) > 1 and np.allclose(floor_poly[0], floor_poly[-1]):
                    floor_poly = floor_poly[:-1]
                if len(floor_poly) < 3 or not Polygon(floor_poly).is_valid or Polygon(floor_poly).area < 0.25:
                    notes.append(f"{name}: floor polygon unusable (fewer than 3 points, self-crossing or "
                                 f"tiny): outline taken from the walls")
                    floor_poly = None
            if fl.get("y") is not None:
                floor_y = _num(fl.get("y"), f"{where}.floor.y")
        objects = []
        for k, o in enumerate(_list(r, "objects", where)):
            if not isinstance(o, dict):
                raise InputError(f"capture.json: {where}.objects[{k}] must be an object")
            obj = {"category": str(o.get("category") or "object")}
            for key, n in (("center", 3), ("dimensions", 3)):
                if o.get(key) is not None:
                    obj[key] = [round(float(x), 4) for x in _vec(o[key], n, f"{where}.objects[{k}].{key}")]
            if o.get("yaw") is not None:
                obj["yaw"] = round(_num(o["yaw"], f"{where}.objects[{k}].yaw"), 4)
            objects.append(obj)
        labels = [str(s) for s in _list(r, "section_labels", where) if str(s).strip()]
        rooms.append(RPRoom(name=name, index=int(r.get("index", i)) if isinstance(r.get("index", i), int) else i,
                            story=int(r.get("story") or 0) if isinstance(r.get("story") or 0, int) else 0,
                            walls=walls, openings=ops, floor_poly=floor_poly, floor_y=floor_y, objects=objects,
                            section_labels=labels, capture=root.name))
    by_index = {r.index: k for k, r in enumerate(rooms)}
    frames: list[RPFrame] = []
    missing = bad_size = 0
    for k, fr in enumerate(_list(data, "frames", "the capture")):
        fw = f"frames[{k}]"
        if not isinstance(fr, dict):
            raise InputError(f"capture.json: {fw} must be an object")
        name = fr.get("file")
        if not isinstance(name, str) or not name:
            raise InputError(f"capture.json: {fw}.file must be a file name")
        ri = fr.get("room_index", 0)
        if not isinstance(ri, int) or (ri not in by_index and not 0 <= ri < len(rooms)):
            raise InputError(f"capture.json: {fw}.room_index {json.dumps(ri)} names no room")
        T = _mat(fr.get("transform"), 4, f"{fw}.transform", (0, 0, 0, 1))
        K = _mat(fr.get("intrinsics"), 3, f"{fw}.intrinsics", (0, 0, 1))
        width = fr.get("width")
        height = fr.get("height")
        if not (isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
            raise InputError(f"capture.json: {fw}.width / height must be positive whole numbers")
        if K[0, 0] <= 0 or K[1, 1] <= 0:
            raise InputError(f"capture.json: {fw}.intrinsics has no positive focal length")
        p = root / Path(*Path(name.replace("\\", "/")).parts)
        if not p.is_file() or not visible(p):
            missing += 1
            continue
        try:
            from PIL import Image
            with Image.open(p) as im:
                size = im.size
        except Exception:  # noqa: BLE001 - unreadable image: counted with the missing ones
            missing += 1
            continue
        if size != (width, height):
            bad_size += 1
            continue
        frames.append(RPFrame(file=p, room=by_index.get(ri, ri), t=_num(fr.get("t", k), f"{fw}.t"), T_ar=T, K=K,
                              width=width, height=height))
    if missing:
        notes.append(f"{root.name}: {missing} frame(s) listed in capture.json missing or unreadable: skipped")
    if bad_size:
        notes.append(f"{root.name}: {bad_size} frame(s) whose image size differs from capture.json's width x "
                     f"height (rotated?): skipped, their intrinsics would not fit")
    dev = data.get("device") if isinstance(data.get("device"), dict) else {}
    meta = {"format": fmt, "app_version": str(data.get("app_version") or ""),
            "device": {"model": str(dev.get("model") or ""), "system": str(dev.get("system") or "")},
            "captured_at": str(data.get("captured_at") or ""), "merged": merged}
    for key in ("session_id",):  # optional, outside the contract: groups one-room zips of one session
        if data.get(key) is not None:
            meta[key] = str(data[key])
    return meta, rooms, frames


def load_capture(path: Path) -> RoomPlanCapture:
    """Every RoomPlan capture at `path` (one capture folder, or a folder of several: one zip per
    room) as one RoomPlanCapture in a single plan frame."""
    path = Path(path)
    roots = find_roomplan_roots(path)
    if not roots:
        raise InputError(f"{path} holds no RoomPlan capture (capture.json)")
    notes: list[str] = []
    rooms: list[RPRoom] = []
    frames: list[RPFrame] = []
    metas = []
    for root in roots:
        meta, rs, fs = parse_capture(root, notes)
        off = len(rooms)
        for f in fs:
            f.room += off
        rooms += rs
        frames += fs
        metas.append((meta, len(rs)))
    merged_all = all(m["merged"] for m, _ in metas)
    side = False
    if len(roots) == 1 and not merged_all and len(rooms) > 1:
        side = True
        notes.append("rooms were captured in separate coordinate frames (merged = false): laid out side by side; "
                      "adjacency between them is unknown")
    elif len(roots) > 1:
        sessions = {m.get("session_id") for m, _ in metas}
        if not merged_all or len(sessions) > 1:
            side = True
            notes.append(f"{len(roots)} RoomPlan captures not in one world frame (a capture with merged = false, "
                         f"or different sessions): rooms laid out side by side; adjacency between them is unknown")
        else:
            ov = _max_cross_overlap(rooms)
            if ov > OVERLAP_MAX:
                side = True
                notes.append(f"{len(roots)} RoomPlan captures say merged = true but their rooms overlap by "
                             f"{ov:.1f} m2, so they are not in one world frame: rooms laid out side by side; "
                             f"adjacency between them is unknown")
    if side:
        _side_by_side(rooms, frames)
    m0 = metas[0][0]
    meta = {"format": m0["format"], "app_version": m0["app_version"], "device": m0["device"],
            "captured_at": m0["captured_at"], "merged": merged_all and not side, "n_captures": len(roots),
            "frames": len(frames)}
    if len(roots) > 1:
        meta["captures"] = [{"name": r.name, "app_version": m["app_version"], "captured_at": m["captured_at"],
                             "merged": m["merged"], "rooms": n} for r, (m, n) in zip(roots, metas)]
    name = roots[0].name if len(roots) == 1 else (path.name or "roomplan")
    return RoomPlanCapture(name=name, rooms=rooms, frames=frames, meta=meta, warnings=notes, side_by_side=side)


def _room_points(r: RPRoom) -> np.ndarray:
    pts = [w.start for w in r.walls] + [w.end for w in r.walls]
    if r.floor_poly is not None:
        pts += list(r.floor_poly)
    return np.array(pts) if pts else np.zeros((0, 2))


def _rough_poly(r: RPRoom) -> Polygon | None:
    if r.floor_poly is not None:
        return Polygon(r.floor_poly)
    pts = _room_points(r)
    if len(pts) < 3:
        return None
    hull = MultiPoint([tuple(p) for p in pts]).convex_hull
    return hull if hull.geom_type == "Polygon" else None


def _max_cross_overlap(rooms: list[RPRoom]) -> float:
    polys = [(r.capture, _rough_poly(r)) for r in rooms]
    best = 0.0
    for i, (ca, a) in enumerate(polys):
        for cb, b in polys[i + 1:]:
            if a is not None and b is not None and ca != cb:
                best = max(best, float(a.intersection(b).area))
    return best


def _side_by_side(rooms: list[RPRoom], frames: list[RPFrame]) -> None:
    """Shift each room (and its frames) along x so the rooms sit in a row SIDE_GAP apart."""
    cursor = 0.0
    for k, r in enumerate(rooms):
        pts = _room_points(r)
        if not len(pts):
            continue
        lo, hi = pts.min(0), pts.max(0)
        d = np.array([cursor - lo[0], -lo[1]])
        for w in r.walls:
            w.start, w.end = w.start + d, w.end + d
        for o in r.openings:
            o.center = o.center + np.array([d[0], 0.0, d[1]])
        if r.floor_poly is not None:
            r.floor_poly = r.floor_poly + d
        for o in r.objects:
            if "center" in o:
                o["center"] = [round(o["center"][0] + d[0], 4), o["center"][1], round(o["center"][2] + d[1], 4)]
        for f in frames:
            if f.room == k:
                f.T_ar = f.T_ar.copy()
                f.T_ar[0, 3] += d[0]
                f.T_ar[2, 3] += d[1]
        cursor += hi[0] - lo[0] + SIDE_GAP


# ---------------------------------------------------------------- geometry

def chain_walls(walls: list[RPWall], snap: float = SNAP) -> tuple[list[tuple[int, bool]], np.ndarray] | None:
    """Walls -> one closed outline: [(wall index, reversed)] in order around it and the corner
    points (one per wall, at the start of each ordered wall). None when the walls do not form a
    single closed loop (an end without a partner within `snap`, a T junction, two loops)."""
    n = len(walls)
    if n < 3:
        return None
    ends = np.array([p for w in walls for p in (w.start, w.end)])  # endpoint 2i = start, 2i+1 = end
    parent = list(range(2 * n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a in range(2 * n):
        for b in range(a + 1, 2 * n):
            if np.linalg.norm(ends[a] - ends[b]) <= snap:
                parent[find(a)] = find(b)
    node = [find(a) for a in range(2 * n)]
    for i in range(n):
        if node[2 * i] == node[2 * i + 1]:
            return None  # a wall shorter than the snap distance
    deg: dict[int, list[int]] = {}
    for a in range(2 * n):
        deg.setdefault(node[a], []).append(a // 2)
    if any(len(v) != 2 for v in deg.values()):
        return None
    order: list[tuple[int, bool]] = [(0, False)]
    used = {0}
    cur = node[1]
    while True:
        nxt = [i for i in deg[cur] if i not in used]
        if not nxt:
            break
        i = nxt[0]
        rev = node[2 * i] != cur
        order.append((i, rev))
        used.add(i)
        cur = node[2 * i] if rev else node[2 * i + 1]
    if len(order) != n or cur != node[0]:
        return None
    corners = []
    for i, rev in order:
        a = node[2 * i + (1 if rev else 0)]
        corners.append(ends[[k for k in range(2 * n) if node[k] == a]].mean(0))
    return order, np.array(corners)


def _signed_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _orient_name(d: np.ndarray) -> str:
    if abs(d[1]) < 0.02:
        return "H"
    if abs(d[0]) < 0.02:
        return "V"
    return "D"


@dataclass
class Built:
    layout: Layout
    labels: dict[str, str]  # room id -> label (RoomPlan section label or room name)
    info: list[dict]  # per room: id, name, story, objects ...
    wall_uid: dict[str, str]  # RoomPlan wall uuid -> our wall id (per room: key room_id|uuid)
    warnings: list[str]


def build_layout(rp: RoomPlanCapture) -> Built:
    notes: list[str] = []
    rooms: list[Room] = []
    labels, info, uid = {}, [], {}
    for k, r in enumerate(rp.rooms):
        # the room's own name as its id ("kitchen", "hall_2" when repeated), like photo rooms keep
        # their folder name; "room_N" only when the name has no usable characters
        slug = re.sub(r"[^a-z0-9]+", "_", r.name.lower()).strip("_") or f"room_{len(rooms) + 1}"
        rid, n = slug, 2
        while any(x.id == rid for x in rooms):
            rid, n = f"{slug}_{n}", n + 1
        walls = [w for w in r.walls if w.length >= MIN_WALL]
        if len(walls) < len(r.walls):
            notes.append(f"{r.name}: {len(r.walls) - len(walls)} wall segment(s) shorter than {MIN_WALL * 100:.0f} cm "
                         f"ignored")
        if not walls:
            notes.append(f"{r.name}: RoomPlan found no walls: room left out (scan it again)")
            continue
        ch = chain_walls(walls)
        if r.floor_poly is not None:
            poly = r.floor_poly.copy()
        elif ch is not None:
            poly = ch[1]
        else:
            pts = np.array([p for w in walls for p in (w.start, w.end)])
            hull = MultiPoint([tuple(p) for p in pts]).convex_hull
            if hull.geom_type != "Polygon" or hull.area < 0.25:
                notes.append(f"{r.name}: walls enclose no area: room left out")
                continue
            poly = np.array(hull.exterior.coords)[:-1]
            notes.append(f"{r.name}: walls do not close into an outline (no floor polygon either): outline is the "
                         f"convex hull of the wall ends, so area and perimeter may be too large")
        poly = np.array(orient(Polygon(poly), 1.0).exterior.coords)[:-1]
        shape = Polygon(poly)
        # walls in order around the room, counter-clockwise like the outline
        if ch is not None:
            seq = [(walls[i], rev) for i, rev in ch[0]]
            if _signed_area(ch[1]) < 0:
                seq = [(w, not rev) for w, rev in reversed(seq)]
        else:
            seq = [(w, False) for w in walls]
        heights = [w.height for w in walls]
        floor_y = r.floor_y
        if floor_y is None:
            doors = [o.center[1] - o.height / 2 for o in r.openings if o.kind == "door"]
            if doors:
                floor_y = float(np.median(doors))
                notes.append(f"{r.name}: no floor height in capture.json: taken from the doors' bottoms")
            else:
                floor_y = 0.0
                notes.append(f"{r.name}: no floor height in capture.json and no doors: floor assumed at y = 0, "
                             f"window sill heights may be off")
        sig = [CONF_SIGMA[w.confidence] for w in walls]
        h = float(np.median(heights))
        h_sigma = float(np.hypot(np.median(sig), 1.4826 * np.median(np.abs(np.array(heights) - h))
                                 / max(np.sqrt(len(heights)), 1.0)))
        floor = Level(floor_y, FLOOR_SIGMA, 0)
        ceil = Level(floor_y + h, h_sigma, 0)
        centroid = np.array(shape.centroid.coords[0])
        ws = []
        for j, (w, rev) in enumerate(seq):
            s, e = (w.end, w.start) if rev else (w.start, w.end)
            d = (e - s) / w.length
            n = np.array([-d[1], d[0]])
            mid = (s + e) / 2
            if shape.contains(Point(*(mid + 0.1 * n))):
                inward = n
            elif shape.contains(Point(*(mid - 0.1 * n))):
                inward = -n
            else:
                inward = n if float(n @ (centroid - mid)) >= 0 else -n
            wid = f"{rid}_w{j + 1}"
            uid[f"{rid}|{w.uid}"] = wid
            ws.append(Wall(id=wid, orient=_orient_name(d), start=s.copy(), end=e.copy(), inward=inward,
                           sigma=CONF_SIGMA[w.confidence], support=0, coverage=1.0, spread=0.0))
        low = [w for w in walls if w.confidence != "high"]
        if low:
            notes.append(f"{r.name}: {len(low)} of {len(walls)} walls with {'/'.join(sorted({w.confidence for w in low}))}"
                         f" RoomPlan confidence: their intervals are wider")
        rooms.append(Room(id=rid, label_id=len(rooms) + 1, polygon=poly, walls=ws, floor=floor, ceiling=ceil,
                          ceiling_source="roomplan"))
        labels[rid] = (r.section_labels[0] if r.section_labels else r.name).strip().lower() or "room"
        info.append({"id": rid, "name": r.name, "story": r.story, "roomplan_index": r.index,
                     "section_labels": r.section_labels, "capture": r.capture,
                     "outline": "floor_polygon" if r.floor_poly is not None else
                     ("walls" if ch is not None else "convex_hull"),
                     "objects": r.objects, "_k": k, "_heights": [w.height for w, _ in seq]})
    if rooms:
        allp = np.concatenate([r.polygon for r in rooms])
        lo, hi = allp.min(0) - 0.5, allp.max(0) + 0.5
        shape = (int(np.ceil((hi[1] - lo[1]) / RES)) + 1, int(np.ceil((hi[0] - lo[0]) / RES)) + 1)
        fr = PlanFrame(yaw=0.0, a0=float(lo[0]), b0=float(lo[1]), res=RES, shape=shape)
        grid = np.zeros(shape, np.int32)
        for r in rooms:
            cells = np.round(np.stack([(r.polygon[:, 0] - fr.a0) / RES, (r.polygon[:, 1] - fr.b0) / RES], 1)
                             ).astype(np.int32)
            cv2.fillPoly(grid, [cells], int(r.label_id))
        floor = Level(float(np.median([r.floor.value for r in rooms])), FLOOR_SIGMA, 0)
    else:
        fr, grid, floor = PlanFrame(yaw=0.0, shape=(1, 1)), np.zeros((1, 1), np.int32), Level(0.0, FLOOR_SIGMA, 0)
    layout = Layout(frame=fr, floor=floor, ceiling=None, rooms=rooms, labels=grid, grids={})
    return Built(layout=layout, labels=labels, info=info, wall_uid=uid, warnings=notes)


def place_openings(rp: RoomPlanCapture, built: Built) -> tuple[list[Opening], list[str]]:
    """RoomPlan doors / windows / openings -> Openings on our walls: the wall named by wall_id,
    else the nearest wall of the room. Doors and open passages within DOOR_NEAR of another
    room's outline connect the two rooms (not when the rooms were laid out side by side)."""
    notes: list[str] = []
    out: list[Opening] = []
    rooms = built.layout.rooms
    polys = {r.id: Polygon(r.polygon) for r in rooms}
    for inf, room in zip(built.info, rooms):
        src = rp.rooms[inf["_k"]]
        far = 0
        for o in src.openings:
            p = o.center[[0, 2]]
            wid = built.wall_uid.get(f"{room.id}|{o.wall_uid}") if o.wall_uid else None
            if wid is not None:
                w = next(w for w in room.walls if w.id == wid)
            else:
                w = min(room.walls, key=lambda w: _seg_dist(p, w.start, w.end))
            if _seg_dist(p, w.start, w.end) > 0.5:
                far += 1
            d = (w.end - w.start) / w.length
            uc = float((p - w.start) @ d)
            u0, u1 = max(uc - o.width / 2, 0.0), min(uc + o.width / 2, w.length)
            if u1 - u0 < 0.05:
                notes.append(f"{src.name}: {o.kind} lies off the end of its wall: left out")
                continue
            bottom = max(float(o.center[1] - o.height / 2 - room.floor.value), 0.0)
            connects = None
            if o.kind != "window" and not rp.side_by_side:
                best = None
                for r2 in rooms:
                    if r2.id == room.id:
                        continue
                    dist = polys[r2.id].exterior.distance(Point(*p))
                    if dist <= DOOR_NEAR and (best is None or dist < best[0]):
                        best = (dist, r2.id)
                connects = best[1] if best else None
            out.append(Opening(id=f"{room.id}_{o.kind}_{sum(x.room_id == room.id for x in out) + 1}", kind=o.kind,
                               room_id=room.id, wall_id=w.id, u0=u0, u1=u1, bottom=bottom,
                               top=bottom + o.height, sigma_w=CONF_SIGMA[o.confidence], connects=connects,
                               support=0))
        if far:
            notes.append(f"{src.name}: {far} opening(s) more than 0.5 m from the wall they were put on")
    return out, notes


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    d = b - a
    L2 = float(d @ d)
    t = 0.0 if L2 == 0 else float(np.clip((p - a) @ d / L2, 0, 1))
    return float(np.linalg.norm(p - (a + t * d)))


# ---------------------------------------------------------------- frames and rendered depth

AR_TO_CV = np.diag([1.0, -1.0, -1.0, 1.0])  # ARKit camera (y up, z back) -> OpenCV (y down, z forward)


class PlaneScene:
    """The RoomPlan surfaces as a ray-casting scene: wall rectangles (floor to wall top, doors and
    openings cut out), floor and ceiling polygons."""

    def __init__(self, layout: Layout, openings: list[Opening], heights: dict[str, list[float]]):
        P0, P1, Y0, Y1, holes = [], [], [], [], []
        self.horiz: list[tuple[float, Polygon]] = []
        for r in layout.rooms:
            hs = heights.get(r.id) or []
            for j, w in enumerate(r.walls):
                P0.append(w.start)
                P1.append(w.end)
                Y0.append(r.floor.value)
                Y1.append(r.floor.value + (hs[j] if j < len(hs) else (r.height or 2.4)))
                holes.append([(o.u0, o.u1, r.floor.value + o.bottom, r.floor.value + o.top)
                              for o in openings if o.wall_id == w.id and o.kind != "window"])
            poly = Polygon(r.polygon)
            self.horiz.append((r.floor.value, poly))
            if r.height:
                self.horiz.append((r.floor.value + r.height, poly))
        self.P0, self.P1 = np.array(P0).reshape(-1, 2), np.array(P1).reshape(-1, 2)
        self.Y0, self.Y1, self.holes = np.array(Y0), np.array(Y1), holes

    def depth(self, T_wc: np.ndarray, K: np.ndarray, w: int, h: int) -> np.ndarray:
        """h x w depth (camera z, metres; 0 = no surface) seen by an OpenCV camera at T_wc."""
        import shapely

        u, v = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
        dc = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u)], -1).reshape(-1, 3)
        D = dc @ T_wc[:3, :3].T  # world direction per pixel, scaled so that t = camera z
        o = T_wc[:3, 3]
        best = np.full(len(D), np.inf)
        if len(self.P0):
            seg = self.P1 - self.P0
            L = np.linalg.norm(seg, axis=1)
            dd = seg / L[:, None]
            nrm = np.stack([-dd[:, 1], dd[:, 0]], 1)
            denom = D[:, [0, 2]] @ nrm.T  # (N, W)
            num = np.einsum("ij,ij->i", self.P0 - o[[0, 2]], nrm)  # (W,)
            with np.errstate(divide="ignore", invalid="ignore"):
                t = num[None, :] / denom
            t[~np.isfinite(t) | (t <= 0.05)] = np.inf
            for k in range(len(self.P0)):
                tk = t[:, k]
                m = np.isfinite(tk)
                if not m.any():
                    continue
                idx = np.flatnonzero(m)
                hit = o[None, [0, 2]] + tk[idx, None] * D[idx][:, [0, 2]]
                s = (hit - self.P0[k]) @ dd[k]
                y = o[1] + tk[idx] * D[idx, 1]
                ok = (s >= 0) & (s <= L[k]) & (y >= self.Y0[k]) & (y <= self.Y1[k])
                for u0, u1, b0, b1 in self.holes[k]:
                    ok &= ~((s > u0) & (s < u1) & (y > b0) & (y < b1))
                idx = idx[ok]
                best[idx] = np.minimum(best[idx], tk[idx])
        for y0, poly in self.horiz:
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (y0 - o[1]) / D[:, 1]
            m = np.isfinite(t) & (t > 0.05) & (t < best)
            idx = np.flatnonzero(m)
            if not len(idx):
                continue
            x = o[0] + t[idx] * D[idx, 0]
            z = o[2] + t[idx] * D[idx, 2]
            inside = shapely.contains_xy(poly, x, z)
            best[idx[inside]] = t[idx[inside]]
        best[~np.isfinite(best)] = 0.0
        return best.reshape(h, w).astype(np.float32)


def _read_rgb(p: Path, size: tuple[int, int]) -> np.ndarray | None:
    try:
        buf = np.fromfile(str(p), np.uint8)
    except OSError:
        return None
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR) if buf.size else None
    if img is None or (img.shape[1], img.shape[0]) != size:
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def posed_capture(rp: RoomPlanCapture, scene: PlaneScene) -> tuple[PosedCapture, dict[int, int]]:
    """Frames as a PosedCapture (OpenCV camera axes, depth rendered from the RoomPlan surfaces),
    and frame index -> room index (into rp.rooms)."""
    frames, room_of = [], {}
    for i, f in enumerate(rp.frames):
        T = f.T_ar @ AR_TO_CV
        dw = DEPTH_W
        dh = max(1, int(round(DEPTH_W * f.height / f.width)))
        Kd = f.K.copy()
        Kd[0] *= dw / f.width
        Kd[1] *= dh / f.height
        frames.append(Frame(index=i, timestamp=f.t, T_wc=T, K=Kd, K_rgb=f.K.copy(),
                            depth_fn=(lambda T=T, Kd=Kd, dw=dw, dh=dh: scene.depth(T, Kd, dw, dh)),
                            rgb_fn=(lambda p=f.file, s=(f.width, f.height): _read_rgb(p, s)),
                            depth_sigma_rel=0.0))
        room_of[i] = f.room
    return PosedCapture(tier="roomplan", name=rp.name, frames=frames, meta={}), room_of


def damage_frames(cap: PosedCapture, room_of: dict[int, int], layout: Layout,
                  per_room: int = DAMAGE_FRAMES_PER_ROOM) -> list[Frame]:
    """At most `per_room` evenly spaced frames per room, thinned further to those that add surface
    coverage (damage.detect.select_frames_coverage). Keeps damage detection to seconds."""
    from roomscan.damage.detect import select_frames_coverage

    by_room: dict[int, list[Frame]] = {}
    for f in cap.frames:
        by_room.setdefault(room_of[f.index], []).append(f)
    cand: list[Frame] = []
    for fs in by_room.values():
        fs = sorted(fs, key=lambda f: f.timestamp)
        if len(fs) > per_room:
            fs = [fs[int(i)] for i in np.linspace(0, len(fs) - 1, per_room).round().astype(int)]
        cand += fs
    if not cand:
        return []
    sub = PosedCapture(tier=cap.tier, name=cap.name, frames=cand, meta={})
    chosen = select_frames_coverage(sub, layout, max_frames=len(cand), images=False)
    return cand if chosen is None else chosen


# ---------------------------------------------------------------- the run

def run_roomplan(path: Path, out_dir: Path, damage: bool = True, known=None, timing=None,
                 progress: bool = False) -> dict:
    """The roomplan tier: load -> layout -> openings -> damage -> export (roomscan.stages)."""
    from roomscan import stages as S
    from roomscan.export.build import build_output
    from roomscan.export.render import render_plan

    timing = timing if timing is not None else {}
    staged = isinstance(timing, S.Stages)
    t = time.time()
    rp = load_capture(path)
    warnings = list(rp.warnings)
    timing["load"] = time.time() - t
    if staged:
        gate_load(timing, rp)

    t = time.time()
    built = build_layout(rp)
    layout = built.layout
    warnings += built.warnings
    if known is not None and not known.empty() and layout.rooms:
        from roomscan.known_sizes import dims_of, lidar_check
        warnings += lidar_check(known, [dims_of(r) for r in layout.rooms], label="RoomPlan")
    if not layout.rooms:
        warnings.append("no room with walls in the capture")
    timing["layout"] = time.time() - t
    if staged:
        S.gate_layout(timing, layout)

    t = time.time()
    openings, notes = place_openings(rp, built)
    warnings += notes
    timing["openings"] = time.time() - t
    if staged:
        S.gate_openings(timing, openings)

    heights = {inf["id"]: inf["_heights"] for inf in built.info}
    scene = PlaneScene(layout, openings, heights)
    cap, room_of = posed_capture(rp, scene)
    dmg, flags, scope = [], [], []
    if damage:
        t = time.time()
        if not cap.frames:
            warnings.append("no frames in the capture: no damage detection")
        from roomscan.damage.pipeline import assess_damage
        chosen = damage_frames(cap, room_of, layout) if layout.rooms else []
        dmg, flags, scope, dw = assess_damage(cap, layout, openings, None, progress=progress, frames=chosen)
        warnings += dw
        timing["damage"] = time.time() - t
        if staged:
            S.gate_damage(timing, dmg)

    t = time.time()
    meta = dict(rp.meta)
    meta["rooms"] = [{k: v for k, v in inf.items() if not k.startswith("_")} for inf in built.info]
    meta["frames_used_for_damage"] = len(chosen) if damage else 0
    info = {"id": rp.name, "tier": "roomplan", "source": SOURCE, "n_frames_used": len(cap.frames), "meta": meta}
    drift_info = {"method": "none", "enabled": False, "note": "ARKit world tracking (RoomPlan)"}
    stitch = "roomplan_side_by_side" if rp.side_by_side else "roomplan_world_frame"
    out = build_output(layout, openings, "roomplan", info, drift_info, dmg, flags, scope, warnings,
                       dict(timing), stitch_method=stitch, labels=built.labels)
    timing["export"] = time.time() - t
    out.timing_s = {k: round(v, 2) for k, v in timing.items()}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(out.model_dump_json(indent=2), encoding="utf-8")
    render_plan(out, out_dir / "plan.png")
    res = json.loads(out.model_dump_json())
    if staged:
        S.gate_export(timing, res)
    return res


def gate_load(st, rp: RoomPlanCapture) -> None:
    n_walls = sum(len(r.walls) for r in rp.rooms)
    if not n_walls:
        st.gate("load", "fail", f"{len(rp.rooms)} room(s) but no walls: RoomPlan found no structure; scan again")
    note = f"{len(rp.rooms)} room(s), {n_walls} walls, {len(rp.frames)} frames"
    st.gate("load", "warn" if not rp.frames else "ok",
            note + ("; no frames, so no damage detection" if not rp.frames else ""))
