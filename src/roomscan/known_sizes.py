"""Known sizes: a few tape measurements that fix the metric scale of the video and photo tiers.

The video and photo tiers take metric scale from a monocular depth model, which is wrong by a
factor that varies per image (0.4x - 3.4x seen) and per capture. No capture instruction fixes
that; one number per room measured with a tape does. The user writes them next to the capture:

    <photo folder>/measurements.yaml         (photo tier: keys are the room folder names)
    <video>.measurements.yaml, e.g. walk.mp4.measurements.yaml or walk.measurements.yaml
    <folder>/measurements.yaml               (any tier, when the capture is a folder)

    rooms:
      01_hall:    {length: 4.20, width: 3.10, height: 2.60}   # any subset, metres
      02_kitchen: {height: 2.60}
      any:        {height: 2.50}      # every room without its own entry
    scale_reference: {length: 1.00}   # optional free note, not used

length / width are the room's longer / shorter side (wall to wall), height floor to ceiling.
`rooms` may also be a list of {length, width, height} without names (video: rooms have no
names), applied to the fitted rooms by best match (aspect ratio, then size rank).

Use per tier:
  * photo: per room, scale = median of (given / fitted) over the given numbers; applied to that
    room before stitching. Rooms without a number get the median of the measured rooms, except
    rooms whose scale a printed A4 marker sets (roomscan.markers; priority: typed length or
    width > marker > the median of the measured rooms > depth model).
  * video: one global scale = median over the measured rooms' scales (the poses are one map).
  * LiDAR: never rescaled; the differences are printed as a self-check.
The measured quantity's interval shrinks to the tape's own uncertainty (TAPE_M); every other
quantity keeps its tier interval with the residual disagreement of the given numbers added as a
relative term. Numbers that disagree with each other by more than DISAGREE are reported.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

TAPE_M = 0.01  # 90 % half-width of a tape / laser reading (m)
Z90 = 1.6449
DISAGREE = 0.10  # given numbers implying scales more than 10 % apart: warn and widen
QTY = ("length", "width", "height")
LIMITS = (0.3, 50.0)  # plausible metres for a room side or ceiling
FILE = "measurements.yaml"


class MeasurementsError(ValueError):
    """measurements.yaml cannot be used; the message says what to fix."""


@dataclass
class Known:
    label: str  # room key in the file, "any", or "#3" for the third list entry
    length: float | None = None
    width: float | None = None
    height: float | None = None

    def given(self) -> dict[str, float]:
        return {q: getattr(self, q) for q in QTY if getattr(self, q) is not None}


@dataclass
class KnownSizes:
    named: dict[str, Known] = field(default_factory=dict)
    listed: list[Known] = field(default_factory=list)
    any: Known | None = None
    source: str = ""

    def empty(self) -> bool:
        return not self.named and not self.listed and self.any is None


@dataclass
class RoomDims:
    """What the fitted room says, in its current units."""
    name: str
    length: float  # longer side of the room's axis-aligned extent in the plan frame
    width: float
    height: float | None  # floor to ceiling plane; None when no ceiling plane was found
    rect: bool = True  # four walls: the sides ARE walls

    @property
    def area(self) -> float:
        return self.length * self.width


@dataclass
class Ratio:
    room: str
    label: str
    qty: str
    given: float
    fitted: float

    @property
    def r(self) -> float:
        return self.given / self.fitted


@dataclass
class ScalePlan:
    per_room: dict[str, float]  # room -> scale to apply (photo); empty for video / LiDAR
    scale: float  # global scale (video), or median of room scales (photo)
    ratios: list[Ratio]
    spread: float  # largest relative deviation of a ratio from its room's (photo) / global scale
    source: str
    warnings: list[str]
    assigned: dict[str, Known]  # room -> Known used for it

    def meta(self) -> dict:
        return {"known_size_scale": round(self.scale, 4),
                "known_size_source": self.source,
                "known_size_spread": round(self.spread, 4),
                "known_size_room_scales": [f"{k}: x{v:.3f}" for k, v in self.per_room.items()],
                "known_size_ratios": [f"{x.room} {x.qty} given {x.given:.3f} m / fitted {x.fitted:.3f} = {x.r:.3f}"
                                      for x in self.ratios]}


# ------------------------------------------------------------------ reading


def find_file(*paths: Path) -> Path | None:
    """measurements.yaml for a capture: in a capture folder, or beside a video / zip file."""
    for p in paths:
        if p is None:
            continue
        p = Path(p)
        cands = [p / FILE] if p.is_dir() else [p.with_name(p.name + "." + FILE), p.with_name(p.stem + "." + FILE)]
        if p.is_dir():
            sub = [d for d in p.iterdir() if d.is_dir() and not d.name.startswith(".")]
            if len(sub) == 1:  # "Extract all" makes x/x/...
                cands.append(sub[0] / FILE)
        for c in cands:
            if c.is_file():
                return c
    return None


def _number(v, where: str) -> float:
    if isinstance(v, str):
        v = v.strip().lower().removesuffix("m").strip().replace(",", ".")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise MeasurementsError(f"{where}: '{v}' is not a number of metres") from None
    if not LIMITS[0] <= x <= LIMITS[1]:
        raise MeasurementsError(f"{where}: {x} m is not a plausible room size (metres, {LIMITS[0]}-{LIMITS[1]}); "
                                f"centimetres or millimetres are not accepted")
    return x


def _known(label: str, d, where: str) -> Known:
    if not isinstance(d, dict):
        raise MeasurementsError(f"{where}: expected {{length: .., width: .., height: ..}}, got {d!r}")
    extra = set(map(str, d)) - set(QTY) - {"name"}
    if extra:
        raise MeasurementsError(f"{where}: unknown key(s) {', '.join(sorted(extra))}; use length, width, height")
    k = Known(label=str(d.get("name", label)), **{q: _number(d[q], f"{where}.{q}") for q in QTY if d.get(q) is not None})
    if k.length is not None and k.width is not None and k.width > k.length:
        k.length, k.width = k.width, k.length  # length is the longer side by definition
    if not k.given():
        raise MeasurementsError(f"{where}: no length, width or height given")
    return k


def parse(data, source: str = "") -> KnownSizes:
    if data is None:
        return KnownSizes(source=source)
    if not isinstance(data, dict):
        raise MeasurementsError(f"{source or FILE}: expected a 'rooms:' mapping at the top")
    rooms = data.get("rooms")
    ks = KnownSizes(source=source)
    if rooms is None:
        return ks
    if isinstance(rooms, list):
        for i, d in enumerate(rooms, 1):
            k = _known(f"#{i}", d, f"rooms[{i}]")
            if isinstance(d, dict) and d.get("name"):
                ks.named[str(d["name"])] = k
            else:
                ks.listed.append(k)
    elif isinstance(rooms, dict):
        for name, d in rooms.items():
            if str(name).lower() == "any":
                ks.any = _known("any", d, "rooms.any")
            else:
                ks.named[str(name)] = _known(str(name), d, f"rooms.{name}")
    else:
        raise MeasurementsError(f"{source or FILE}: 'rooms' must be a mapping of room names or a list")
    return ks


def load(path: Path) -> KnownSizes:
    import yaml

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as e:
        raise MeasurementsError(f"{Path(path).name} is not valid YAML: {e}") from e
    return parse(data, source=Path(path).name)


# ------------------------------------------------------------------ matching


def _norm(s: str) -> str:
    return re.sub(r"^[\d\s_.\-]+", "", s.casefold()).replace(" ", "_")


def dims_of(room) -> RoomDims:
    """Fitted room (geometry.layout.Room) -> its sides and ceiling height."""
    ext = room.polygon.max(0) - room.polygon.min(0)
    h = room.height if getattr(room, "ceiling_source", "") == "ceiling_plane" else None
    return RoomDims(room.id, float(ext.max()), float(ext.min()), None if h is None else float(h),
                    rect=len(room.polygon) == 4)


def assign(ks: KnownSizes, rooms: list[RoomDims], notes: list[str]) -> dict[str, Known]:
    """Room name -> the Known that describes it. Named entries match room names (exactly, by case,
    or ignoring a leading number); the rest are matched by aspect ratio and size rank."""
    out: dict[str, Known] = {}
    names = {r.name: r for r in rooms}
    pool: list[Known] = list(ks.listed)
    for key, k in ks.named.items():
        hit = key if key in names else next((n for n in names if n.casefold() == key.casefold()), None)
        if hit is None:
            hit = next((n for n in names if _norm(n) == _norm(key) and _norm(key)), None)
        if hit is not None and hit not in out:
            out[hit] = k
        else:
            pool.append(k)
    free = [r for r in rooms if r.name not in out]
    sized = [k for k in pool if k.length is not None or k.width is not None]
    heights = [k for k in pool if k.length is None and k.width is None]
    if sized and free:
        from scipy.optimize import linear_sum_assignment

        def size(k: Known) -> float:
            return (k.length or k.width) * (k.width or k.length)

        rk = {id(k): i for i, k in enumerate(sorted(sized, key=size, reverse=True))}
        rr = {r.name: i for i, r in enumerate(sorted(free, key=lambda r: r.area, reverse=True))}
        C = np.zeros((len(sized), len(free)))
        for i, k in enumerate(sized):
            for j, r in enumerate(free):
                c = 0.2 * abs(rk[id(k)] - rr[r.name]) / max(len(free), 1)
                if k.length is not None and k.width is not None:
                    c += abs(np.log(k.length / k.width) - np.log(r.length / max(r.width, 1e-6)))
                C[i, j] = c
        ii, jj = linear_sum_assignment(C)
        for i, j in zip(ii, jj):
            out[free[j].name] = sized[i]
            if not sized[i].label.startswith("#") and sized[i].label not in names:
                notes.append(f"known sizes: '{sized[i].label}' is not a room name here; matched to {free[j].name} "
                             f"by shape and size")
        for i in set(range(len(sized))) - set(ii):
            notes.append(f"known sizes: entry '{sized[i].label}' not used (more entries than rooms)")
    elif sized:
        notes.append("known sizes: no room to apply the given lengths to")
    # height-only entries without a room, and 'any': every room still without an entry
    broadcast = [k for k in heights] + ([ks.any] if ks.any is not None else [])
    for k in broadcast:
        for r in rooms:
            if r.name not in out:
                out[r.name] = k
    return out


def ratios_for(k: Known, r: RoomDims, notes: list[str]) -> list[Ratio]:
    out = []
    for q, g in k.given().items():
        if q == "height":
            if r.height is None:
                notes.append(f"{r.name}: ceiling height {g:.2f} m given, but no ceiling plane found: not used for scale")
                continue
            f = r.height
        else:
            f = getattr(r, q)
        if f > 0:
            out.append(Ratio(r.name, k.label, q, g, f))
    return out


def _spread(rs: np.ndarray, s: float) -> float:
    return float(np.max(np.abs(rs / s - 1.0))) if len(rs) else 0.0


def plan(ks: KnownSizes, rooms: list[RoomDims], mode: str, own_scale=frozenset()) -> ScalePlan | None:
    """mode 'room': one scale per room (photo); 'global': one scale for the capture (video, and the
    LiDAR self-check). None when nothing given applies.

    own_scale (room mode): rooms with a scale of their own (a printed marker). Without a typed
    length or width they are left out of per_room (not given the measured rooms' median)."""
    notes: list[str] = []
    assigned = assign(ks, rooms, notes)
    for name, k in assigned.items():  # numbers typed by a person: flag likely typos, still use them
        odd = [f"{q} {v:.2f} m" for q, v in (("length", k.length), ("width", k.width)) if v is not None and v > 12]
        if k.height is not None and not 2.0 <= k.height <= 4.5:
            odd.append(f"height {k.height:.2f} m")
        if odd:
            notes.append(f"{name}: {', '.join(odd)} is unusual for one room: please check the number "
                         f"(a typo, or centimetres?)")
    by_room = {r.name: r for r in rooms}
    ratios = [x for name, k in assigned.items() for x in ratios_for(k, by_room[name], notes)]
    if not ratios:
        if not ks.empty():
            notes.append("known sizes given but none could be compared with the fitted rooms; scale left as is")
            return ScalePlan({}, 1.0, [], 0.0, "depth model (known sizes not usable)", notes, assigned)
        return None
    room_scale: dict[str, float] = {}
    for name in dict.fromkeys(x.room for x in ratios):
        rs = np.array([x.r for x in ratios if x.room == name])
        if len(rs) > 1 and rs.max() / rs.min() - 1 > DISAGREE:
            notes.append(f"{name}: the given numbers disagree by {100 * (rs.max() / rs.min() - 1):.0f} % with this "
                         f"room's shape (ratios {', '.join(f'{v:.2f}' for v in rs)}): check them; intervals widened")
        sides = np.array([x.r for x in ratios if x.room == name and x.qty != "height"])
        if mode == "room" and not len(sides):
            # Held out on the sample flat, scaling photo rooms by the ceiling height alone made the
            # footprint worse (+109 % -> +177 %): fitted floor and height are not off by the same
            # factor. A height alone is compared in the output, never used for a photo room's scale.
            notes.append(f"{name}: only a ceiling height given: compared, not used for the scale (it does not "
                         f"fix the floor size on photos); give a length or width")
            continue
        room_scale[name] = float(np.median(sides if len(sides) else rs))
    if not room_scale:
        return ScalePlan({}, 1.0, ratios, 0.0, "depth model (known heights compared only)", notes, assigned)
    s_all = float(np.median(list(room_scale.values())))
    if mode == "room":
        only_h = [n for n in room_scale if all(x.qty == "height" for x in ratios if x.room == n)]
        if only_h:  # held-out on the sample flat: photo footprint +109 % -> +177 % with heights alone
            notes.append(f"known sizes: {', '.join(only_h)} scaled by the ceiling height only; on photos the "
                         f"height does not fix the floor size reliably: add a length (docs/capture_protocol.md)")
        spread =max(_spread(np.array([x.r for x in ratios if x.room == n]), s) for n, s in room_scale.items())
        per = {r.name: room_scale.get(r.name, s_all) for r in rooms
               if r.name in room_scale or r.name not in own_scale}
        unmeasured = [r.name for r in rooms if r.name not in room_scale and r.name not in own_scale]
        src = (f"known sizes from {ks.source or FILE}: {len(room_scale)} room(s) measured"
               + (f"; {len(unmeasured)} without a number scaled by their median x{s_all:.3f}" if unmeasured else ""))
        if unmeasured:
            notes.append(f"known sizes: no usable number for {', '.join(unmeasured)}; scaled by the median of the "
                         f"measured rooms (x{s_all:.3f}), their size is less certain")
        return ScalePlan(per, s_all, ratios, spread, src, notes, assigned)
    rs = np.array([x.r for x in ratios])
    s = float(np.median(list(room_scale.values())))
    spread = _spread(rs, s)
    if len(room_scale) > 1:
        v = np.array(list(room_scale.values()))
        if v.max() / v.min() - 1 > DISAGREE:
            notes.append(f"known sizes: rooms imply scales {v.min():.2f}-{v.max():.2f} (more than "
                         f"{100 * DISAGREE:.0f} % apart): one number or one room fit is off; the median is used and "
                         f"intervals widened")
    src = f"known sizes from {ks.source or FILE}: median over {len(room_scale)} room(s), {len(rs)} number(s)"
    return ScalePlan({}, s, ratios, spread, src, notes, assigned)


def lidar_check(ks: KnownSizes, rooms: list[RoomDims]) -> list[str]:
    """LiDAR is not rescaled: one line per given number with the difference, for the user to check."""
    p = plan(ks, rooms, "global")
    if p is None:
        return []
    out = [f"known sizes (LiDAR self-check, not applied): {x.room} {x.qty} given {x.given:.2f} m, scanned "
           f"{x.fitted:.2f} m ({100 * (x.fitted / x.given - 1):+.1f} %)" for x in p.ratios]
    return out + [w for w in p.warnings if "disagree" not in w]


def scale_room(room, s: float) -> None:
    """Multiply every length of a fitted room (geometry.layout.Room) by s, in place."""
    from dataclasses import replace

    room.polygon = room.polygon * s
    for w in room.walls:
        w.start, w.end = w.start * s, w.end * s
        w.sigma, w.spread = w.sigma * s, w.spread * s
    room.floor = replace(room.floor, value=room.floor.value * s, sigma=room.floor.sigma * s)
    if room.ceiling is not None:
        room.ceiling = replace(room.ceiling, value=room.ceiling.value * s, sigma=room.ceiling.sigma * s)


# ------------------------------------------------------------------ intervals


def _m(value: float, half: float, unit: str, note: str | None):
    from roomscan.schema import Measurement

    lo = max(value - half, 0.0)
    return Measurement(value=round(value, 4), ci90=(round(lo, 4), round(value + half, 4)),
                       sigma=round(half / Z90, 5), unit=unit, note=note)


def _widen(m, rel: float):
    """Add a relative term (1-sigma, fraction of the value) to an interval."""
    if m is None or m.value is None or m.sigma is None or rel <= 0:
        return m
    s = float(np.hypot(m.sigma, rel * abs(m.value)))
    lo = m.value - Z90 * s
    if m.ci90 is not None and m.ci90[0] >= 0:
        lo = max(lo, 0.0)
    return m.model_copy(update={"sigma": round(s, 5), "ci90": (round(lo, 4), round(m.value + Z90 * s, 4))})


def tighten(out, p: ScalePlan, rooms: list[RoomDims], rel_extra: float = 0.0) -> None:
    """Measured quantities -> the tape's interval; the rest widened by the given numbers' spread.

    `out` is the schema.Output built from the rescaled layout; `rooms` its fitted rooms (current
    units, after scaling) so a given length can be tied to the walls it describes."""
    rel = max(p.spread, rel_extra) / Z90 if p.spread > 0.02 or rel_extra > 0 else 0.0
    by = {r.name: r for r in rooms}
    for room in out.rooms:
        k = p.assigned.get(room.id)
        measured_walls: set[str] = set()
        if k is not None and k.height is not None:
            if room.ceiling_height.value is not None:
                # measured: keep the capture's own value and interval, so the comparison with the
                # user's number means something (overwriting it showed a 0 % difference)
                v = room.ceiling_height.value
                room.ceiling_height = room.ceiling_height.model_copy(
                    update={"note": f"measured {v:.2f} m; user gave {k.height:.2f} m ({100 * (v / k.height - 1):+.0f} %)"})
            else:  # not observed: the user's number is the only one, labelled as such
                room.ceiling_height = _m(k.height, TAPE_M, "m",
                                         "ceiling not observed in the capture: height given by the user (tape)")
                for w in room.walls:
                    w.height = room.ceiling_height
        r = by.get(room.id)
        if k is not None and r is not None and r.rect:
            for q in ("length", "width"):
                g = getattr(k, q)
                if g is None:
                    continue
                for w in room.walls:
                    v = w.length.value
                    if v and abs(v / g - 1) < 0.25 and abs(v - getattr(r, q)) < 0.02 + 0.01 * v:
                        lo, hi = min(v, g) - TAPE_M, max(v, g) + TAPE_M
                        w.length = _m(v, max(v - lo, hi - v), "m", f"room {q} given (tape)")
                        measured_walls.add(w.id)
        if rel > 0:
            for w in room.walls:
                if w.id not in measured_walls:
                    w.length = _widen(w.length, rel)
                w.area = _widen(w.area, rel * 2)
            room.floor_area = _widen(room.floor_area, rel * 2)
            room.perimeter = _widen(room.perimeter, rel)
            if not (k is not None and k.height is not None):
                room.ceiling_height = _widen(room.ceiling_height, rel)
            for s in room.surfaces:
                s.area = _widen(s.area, rel * 2)
    if rel > 0:
        out.property.footprint_area = _widen(out.property.footprint_area, rel * 2)
        out.property.bbox = _widen(out.property.bbox, rel)


def finish(out, p: ScalePlan, final_rooms: list, known: KnownSizes, mode: str) -> None:
    """After the rescaled capture went through layout and export: tie the given numbers to the
    final rooms (room ids can change when a video's layout is extracted again), measure what is
    left of the disagreement, tighten / widen the intervals and record the scale in capture.meta."""
    dims = [dims_of(r) for r in final_rooms]
    residual = plan(known, dims, mode) if dims else None
    if residual is not None and residual.ratios:
        p.assigned = residual.assigned
        if mode == "global":  # the layout was extracted again: what still disagrees is real
            p.spread = max(p.spread, _spread(np.array([x.r for x in residual.ratios]), 1.0))
    tighten(out, p, dims)
    out.capture.meta.update(p.meta())
    applied = ", ".join(f"{k} x{v:.3f}" for k, v in p.per_room.items()) if p.per_room else f"x{p.scale:.3f}"
    out.warnings.append(f"metric scale from known sizes ({p.source}): {applied} applied to the depth model's "
                        f"scale; given numbers agree within {100 * p.spread:.0f} %")
