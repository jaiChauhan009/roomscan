"""Assemble the output contract from layout, openings and damage results."""
from __future__ import annotations

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from roomscan import schema as S
from roomscan.geometry.layout import Layout
from roomscan.geometry.openings import Opening
from roomscan.uncertainty.intervals import measure

SHARED_WALL_GAP = 0.40  # rooms whose walls are closer than this share a wall

# Where a wall ends. A wall's length is fixed by the two walls that end it, and layout gives each
# wall the standard error of its plane fit: noise only (0.03 m when no plane was found). When an
# end wall has no plane evidence over part of its length it may be a furniture side or a raster
# edge rather than a wall: the outline then stops the wall at a notch and the real corner can be
# a metre or more further on (laser rooms: such walls 0.17-2.3 m short of the true wall). So a
# wall's length sigma combines its end walls' end_sigma = hypot(plane sigma, EVIDENCE_K * deficit),
# deficit = (FULL_COVERAGE - coverage) / FULL_COVERAGE clipped to [0, 1]: 0 for a wall with plane
# evidence along its whole length, 1 for a wall without a plane. Raw metres, before the tier's
# inflate and scale. FULL_COVERAGE: layout measures coverage between 10 cm end margins, so a wall
# of 2 m or more seen end to end reads 0.90 or more (the laser rooms' true walls: 0.92-0.97).
# EVIDENCE_K = 0.30: the smallest value at which, on the four laser-truth rooms, walls ending at
# a wall below full coverage have the same median |error| / sigma as walls between two fully
# covered walls (3.18 vs 3.29; 29.6 vs 3.29 without the term). Per tier: video and photo keep the
# plane sigmas their scales were fitted with.
# Floor area and perimeter keep the plane sigmas: a notch displaces the outline by the
# furniture's depth, not by the hidden stretch of wall (laser rooms: the two notched rooms' areas
# are off by -0.58 and +0.03 m2, inside their plane-sigma intervals; this term would have
# multiplied those sigmas by 5).
FULL_COVERAGE = 0.90
EVIDENCE_K = {"lidar": 0.30}


def evidence_deficit(w) -> float:
    """0 for a wall with plane evidence along its whole length, 1 for one without a plane."""
    return float(np.clip((FULL_COVERAGE - float(w.coverage)) / FULL_COVERAGE, 0.0, 1.0))


def end_sigma(w, tier: str) -> float:
    """1-sigma (raw, m) of where wall w ends the walls next to it: plane noise plus missing evidence."""
    return float(np.hypot(w.sigma, EVIDENCE_K.get(tier, 0.0) * evidence_deficit(w)))


def room_label(poly: Polygon, height: float | None, n_doors: int) -> str:
    minx, miny, maxx, maxy = poly.bounds
    w, h = sorted([maxx - minx, maxy - miny])
    if w < 1.6 and h / max(w, 1e-6) > 2.2:
        return "corridor"
    if poly.area < 4.0:
        return "small_room"  # bathroom / utility / storage sized
    return "room"


def build_output(layout: Layout, openings: list[Opening], tier: str, capture_info: dict,
                 drift_info: dict, damage: list | None = None, flags: list | None = None,
                 scope: list | None = None, warnings: list[str] | None = None,
                 timing: dict | None = None, stitch_method: str = "single_capture") -> S.Output:
    warnings = list(warnings or [])
    origin = np.min(np.concatenate([r.polygon for r in layout.rooms]), axis=0) if layout.rooms else np.zeros(2)

    def P(p):
        q = np.asarray(p) - origin
        return (round(float(q[0]), 4), round(float(q[1]), 4))

    rooms_out: list[S.Room] = []
    ops_by_room: dict[str, list[Opening]] = {}
    for op in openings:
        ops_by_room.setdefault(op.room_id, []).append(op)

    for room in layout.rooms:
        poly = Polygon(room.polygon)
        h = room.height
        hs = None
        if room.ceiling is not None:
            hs = float(np.hypot(room.floor.sigma, room.ceiling.sigma))
        if room.ceiling_source == "ceiling_plane" and h is not None:
            ceiling = measure(h, hs, tier, "ceiling_height")
        else:
            ceiling = measure(None, 0, tier, "ceiling_height", lower_bound=h,
                              note="ceiling not captured; lower bound = top of observed walls")
            warnings.append(f"{room.id}: ceiling not observed, height reported as lower bound only")
        h_val = h if h is not None else 2.4
        walls_out, surfaces = [], []
        room_ops = ops_by_room.get(room.id, [])
        n = len(room.walls)
        ends = [end_sigma(w, tier) for w in room.walls]
        for i, w in enumerate(room.walls):
            ls = float(np.hypot(ends[i - 1], ends[(i + 1) % n]))  # the two walls that end it
            w_ops = [o for o in room_ops if o.wall_id == w.id]
            open_area = sum(o.width * o.height for o in w_ops)
            gross = w.length * h_val
            walls_out.append(S.Wall(
                id=w.id, start=P(w.start), end=P(w.end),
                length=measure(w.length, ls, tier, "wall_length"),
                height=ceiling,
                area=measure(max(gross - open_area, 0.0), ls * h_val + w.length * (hs or 0.05), tier, "area", "m2"),
                opening_ids=[o.id for o in w_ops],
                evidence_coverage=round(w.coverage, 3), plane_spread=round(w.spread, 4)))
            surfaces.append(S.Surface(id=f"{w.id}", type="wall", ref=w.id,
                                      area=walls_out[-1].area))
        per_sigma = float(np.sqrt(sum(w.sigma ** 2 for w in room.walls)))
        area_sigma = float(np.mean([w.sigma for w in room.walls]) * poly.length / 2) if room.walls else 0.05
        floor_area = measure(poly.area, area_sigma, tier, "area", "m2")
        surfaces += [S.Surface(id=f"{room.id}_floor", type="floor", ref=room.id, area=floor_area),
                     S.Surface(id=f"{room.id}_ceiling", type="ceiling", ref=room.id, area=floor_area)]
        ops_out = []
        for o in room_ops:
            ops_out.append(S.Opening(
                id=o.id, type=o.kind, wall_id=o.wall_id, room_id=o.room_id, connects_to=o.connects,
                width=measure(o.width, o.sigma_w, tier, "opening_width", note=o.note),
                height=measure(o.height, 0.03, tier, "opening_height"),
                sill_height=measure(o.bottom, 0.03, tier, "opening_height"),
                offset_along_wall=round(o.u0, 4)))
        n_doors = sum(1 for o in room_ops if o.kind == "door")
        rooms_out.append(S.Room(
            id=room.id, label=room_label(poly, h, n_doors), polygon=[P(p) for p in room.polygon],
            floor_area=floor_area, perimeter=measure(poly.length, per_sigma, tier, "wall_length"),
            ceiling_height=ceiling, ceiling_source=room.ceiling_source, walls=walls_out,
            openings=ops_out, surfaces=surfaces))

    adjacency = _adjacency(layout, openings)
    # union, not sum: where two room outlines overlap, the floor is there once
    fp = float(unary_union([Polygon(r.polygon) for r in layout.rooms]).area) if layout.rooms else 0.0
    fp_sigma = float(np.sqrt(sum((r.floor_area.sigma or 0) ** 2 for r in rooms_out)))
    if rooms_out:
        allp = np.concatenate([r.polygon for r in layout.rooms])
        diag = float(np.linalg.norm(allp.max(0) - allp.min(0)))
    else:
        diag = 0.0
    prop = S.Property(
        footprint_area=S.Measurement(value=round(fp, 4), ci90=(round(fp - 1.6449 * fp_sigma, 4), round(fp + 1.6449 * fp_sigma, 4)),
                                     sigma=round(fp_sigma, 5), unit="m2"),
        bbox=measure(diag, 0.02, tier, "wall_length"),
        room_ids=[r.id for r in rooms_out], adjacency=adjacency,
        stitch_method=stitch_method, drift_correction=drift_info)
    return S.Output(
        capture=S.CaptureInfo(**capture_info), property=prop, rooms=rooms_out,
        damage=damage or [], concealed_damage_flags=flags or [], scope=scope or [],
        warnings=warnings, timing_s={k: round(v, 2) for k, v in (timing or {}).items()})


def _adjacency(layout: Layout, openings: list[Opening]) -> list[S.Adjacency]:
    out: dict[tuple, S.Adjacency] = {}
    for o in openings:
        if o.connects and o.kind != "window":
            key = tuple(sorted([o.room_id, o.connects]))
            if key not in out:
                out[key] = S.Adjacency(rooms=key, via=o.id, kind="door" if o.kind == "door" else "opening")
    rooms = layout.rooms
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            key = (rooms[i].id, rooms[j].id)
            if key in out:
                continue
            if _shared_wall(rooms[i], rooms[j]) > 0.5:
                out[key] = S.Adjacency(rooms=key, via=None, kind="shared_wall")
    return list(out.values())


def _shared_wall(r1, r2) -> float:
    """Length over which a wall of r1 runs parallel and close to a wall of r2."""
    best = 0.0
    for w1 in r1.walls:
        l1 = LineString([w1.start, w1.end])
        for w2 in r2.walls:
            d1 = (w1.end - w1.start) / max(w1.length, 1e-9)
            d2 = (w2.end - w2.start) / max(w2.length, 1e-9)
            if abs(abs(float(d1 @ d2)) - 1) > 0.02:
                continue
            if l1.distance(LineString([w2.start, w2.end])) > SHARED_WALL_GAP:
                continue
            # overlap of projections onto w1's direction
            a = sorted([float(w1.start @ d1), float(w1.end @ d1)])
            b = sorted([float(w2.start @ d1), float(w2.end @ d1)])
            best = max(best, min(a[1], b[1]) - max(a[0], b[0]))
    return best
