"""Damage assessment: visible regions -> concealed-damage flags -> scope line items."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml
from shapely.geometry import LineString, Polygon

from roomscan import schema as S
from roomscan.capture import PosedCapture
from roomscan.damage.detect import Region, detect_damage, select_frames
from roomscan.export.build import SHARED_WALL_GAP, room_label
from roomscan.geometry.layout import Layout
from roomscan.geometry.openings import Opening
from roomscan.uncertainty.intervals import measure

HERE = Path(__file__).parent


def _room_labels(layout: Layout) -> dict[str, str]:
    return {r.id: room_label(Polygon(r.polygon), r.height, 0) for r in layout.rooms}


def _wall_backs_onto(layout: Layout, wall_id: str, labels: dict[str, str]) -> set[str]:
    """Labels of the rooms on the other side of a wall."""
    out = set()
    owner, wall = next((r, w) for r in layout.rooms for w in r.walls if w.id == wall_id)
    line = LineString([wall.start, wall.end])
    d1 = (wall.end - wall.start) / max(wall.length, 1e-9)
    for r in layout.rooms:
        if r.id == owner.id:
            continue
        for w in r.walls:
            d2 = (w.end - w.start) / max(w.length, 1e-9)
            if abs(abs(float(d1 @ d2)) - 1) > 0.02 or line.distance(LineString([w.start, w.end])) > SHARED_WALL_GAP:
                continue
            a = sorted([float(wall.start @ d1), float(wall.end @ d1)])
            b = sorted([float(w.start @ d1), float(w.end @ d1)])
            if min(a[1], b[1]) - max(a[0], b[0]) > 0.3:
                out.add(labels[r.id])
    return out


def _fires(when: dict, reg: Region, layout: Layout, openings: list[Opening], labels: dict[str, str]) -> bool:
    room = next(r for r in layout.rooms if r.id == reg.room_id)
    if "class" in when and reg.damage_class not in when["class"]:
        return False
    if "surface" in when and reg.surface_type != when["surface"]:
        return False
    if "min_area" in when and reg.area < when["min_area"]:
        return False
    if "min_extent" in when and max(reg.extent_u, reg.extent_v) < when["min_extent"]:
        return False
    if "bottom_below" in when and not (reg.surface_type == "wall" and reg.bottom <= when["bottom_below"]):
        return False
    if "top_within_ceiling" in when:
        if not (reg.surface_type == "wall" and room.height and room.height - reg.top <= when["top_within_ceiling"]):
            return False
    if "near_opening" in when:
        cond = when["near_opening"]
        near = False
        for o in openings:
            if o.wall_id != reg.surface_id or o.kind not in cond["types"]:
                continue
            gap_u = max(o.u0 - reg.u_range[1], reg.u_range[0] - o.u1, 0.0)
            gap_v = max(o.bottom - reg.top, reg.bottom - o.top, 0.0)
            if np.hypot(gap_u, gap_v) <= cond["within"]:
                near = True
        if not near:
            return False
    if "shares_wall_with" in when:
        if reg.surface_type != "wall" or when["shares_wall_with"] not in _wall_backs_onto(layout, reg.surface_id, labels):
            return False
    return True


def concealed_flags(regions: list[tuple[str, Region]], layout: Layout, openings: list[Opening]) -> list[S.ConcealedFlag]:
    rules = yaml.safe_load((HERE / "rules.yaml").read_text())["rules"]
    labels = _room_labels(layout)
    flags: dict[tuple, S.ConcealedFlag] = {}
    for did, reg in regions:
        for rule in rules:
            if not _fires(rule["when"], reg, layout, openings, labels):
                continue
            key = (rule["id"], reg.surface_id)
            if key in flags:
                flags[key].triggered_by.append(did)
            else:
                flags[key] = S.ConcealedFlag(id=f"flag_{len(flags) + 1}", surface_id=reg.surface_id,
                                             room_id=reg.room_id, rule_id=rule["id"], rule=rule["rule"],
                                             triggered_by=[did], risk=rule["risk"],
                                             recommendation=rule["recommendation"])
    return list(flags.values())


def _surface_area(layout: Layout, openings: list[Opening], surface_id: str, tier: str) -> S.Measurement:
    for r in layout.rooms:
        h = r.height or 2.4
        if surface_id in (f"{r.id}_floor", f"{r.id}_ceiling"):
            poly = Polygon(r.polygon)
            sig = float(np.mean([w.sigma for w in r.walls]) * poly.length / 2)
            return measure(poly.area, sig, tier, "area", "m2")
        for i, w in enumerate(r.walls):
            if w.id == surface_id:
                ls = float(np.hypot(r.walls[i - 1].sigma, r.walls[(i + 1) % len(r.walls)].sigma))
                open_area = sum(o.width * o.height for o in openings if o.wall_id == w.id)
                return measure(max(w.length * h - open_area, 0.0), ls * h + w.length * 0.02, tier, "area", "m2")
    return measure(None, 0, tier, "area", "m2")


def scope_items(regions: list[tuple[str, Region]], flags: list[S.ConcealedFlag], layout: Layout,
                openings: list[Opening], tier: str) -> list[S.ScopeItem]:
    table = yaml.safe_load((HERE / "scope.yaml").read_text())
    items: list[S.ScopeItem] = []
    seen = set()

    def add(surface_id, room_id, code, desc, qty, unit, source):
        items.append(S.ScopeItem(id=f"scope_{len(items) + 1}", surface_id=surface_id, room_id=room_id, code=code,
                                 description=desc, quantity=qty, unit=unit, source=source))

    for did, reg in regions:
        for it in table["damage"].get(reg.damage_class, {}).get(reg.surface_type, []):
            if it.get("once_per_surface"):
                if (it["code"], reg.surface_id) in seen:
                    continue
                seen.add((it["code"], reg.surface_id))
            q = it["quantity"]
            if q == "damage_area":
                v = max(reg.area * it.get("factor", 1.0), it.get("minimum", 0.0))
                qty = measure(v, reg.area_sigma * it.get("factor", 1.0), tier, "damage", "m2")
            elif q == "damage_length":
                v = max(max(reg.extent_u, reg.extent_v) * it.get("factor", 1.0), it.get("minimum", 0.0))
                qty = measure(v, reg.extent_sigma * it.get("factor", 1.0), tier, "damage", "m")
            elif q == "surface_area":
                qty = _surface_area(layout, openings, reg.surface_id, tier)
            else:
                qty = S.Measurement(value=1.0, ci90=(1.0, 1.0), sigma=0.0, unit="m", note="count")
            add(reg.surface_id, reg.room_id, it["code"], it["description"], qty, it["unit"], did)
    for fl in flags:
        for it in table["flags"].get(fl.risk, []):
            if (it["code"], fl.surface_id) in seen:
                continue
            seen.add((it["code"], fl.surface_id))
            add(fl.surface_id, fl.room_id, it["code"], it["description"],
                S.Measurement(value=1.0, ci90=(1.0, 1.0), sigma=0.0, unit="m", note="count"), "each", fl.id)
    return items


def assess_damage(cap: PosedCapture, layout: Layout, openings: list[Opening], cloud=None,
                  progress: bool = False, threshold: float = 0.6):
    warnings: list[str] = []
    if not layout.rooms:
        return [], [], [], warnings
    frames = select_frames(cap, 6)
    lum = [float(np.mean(f.rgb_fn())) for f in frames if f.rgb_fn() is not None]
    if lum and np.median(lum) < 45:
        warnings.append("low light (median brightness %.0f/255): damage detection is unreliable on this capture"
                        % np.median(lum))
    regions = detect_damage(cap, layout, threshold=threshold, progress=progress)
    named = [(f"dmg_{i + 1}", r) for i, r in enumerate(regions)]
    fr = layout.frame
    origin = np.min(np.concatenate([r.polygon for r in layout.rooms]), axis=0)
    dmg = []
    for did, r in named:
        c = r.center
        dmg.append(S.DamageRegion(
            id=did, surface_id=r.surface_id, room_id=r.room_id, damage_class=r.damage_class,
            score=round(r.score, 3), area=measure(r.area, r.area_sigma, cap.tier, "damage", "m2"),
            extent_u=measure(r.extent_u, r.extent_sigma, cap.tier, "damage"),
            extent_v=measure(r.extent_v, r.extent_sigma, cap.tier, "damage"),
            center=(round(float(c[0] - origin[0]), 3), round(float(c[1] - origin[1]), 3), round(float(c[2]), 3)),
            evidence_frames=[int(i) for i in r.frames]))
    flags = concealed_flags(named, layout, openings)
    scope = scope_items(named, flags, layout, openings, cap.tier)
    return dmg, flags, scope, warnings
