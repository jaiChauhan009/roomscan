"""Render the stitched, dimensioned floor plan (PNG + SVG) from the output contract."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from shapely.ops import polylabel  # noqa: E402

from roomscan.schema import Output  # noqa: E402

FILL = {"corridor": "#f1ead8", "small_room": "#dcecf2", "room": "#eef2e6"}
NAME = {"corridor": "Corridor"}  # everything else is "Room"; the fill colour marks small rooms
OPENING = {"door": ("#38a169", "-"), "window": ("#3182ce", "--"), "opening": ("#d69e2e", "-")}
DAMAGE_COLOR = {"water_stain": "#2b6cb0", "crack": "#c53030", "mold": "#2f855a",
                "peeling_paint": "#b7791f", "hole": "#6b46c1", "other": "#4a5568"}
MIN_LABELLED_WALL = 0.6  # m; shorter segments keep their length in result.json only


def _fmt(m) -> str:
    if m.value is None:
        return "n/a" if m.lower_bound is None else f">{m.lower_bound:.2f}"
    if m.unit != "m":
        return f"{m.value:.1f}m²"
    if not m.ci90:
        return f"{m.value:.2f}"
    half = (m.ci90[1] - m.ci90[0]) / 2
    if half > 0.5 * m.value:  # e.g. a photo-tier wall: "±402cm" on 3.51 m reads as nonsense, a range does not
        return f"{m.value:.2f} ({m.ci90[0]:.2f}–{m.ci90[1]:.2f})"
    return f"{m.value:.2f}±{half * 100:.0f}cm"


def _room_name(room) -> str:
    num = room.id.split("_")[-1]
    return f"{NAME.get(room.label, 'Room')} {num}" if num.isdigit() else room.id


def _height(m) -> str:
    if m.value is not None:
        return f"h {m.value:.2f} m"
    return f"h > {m.lower_bound:.2f} m" if m.lower_bound is not None else "h n/a"


def _declutter(fig, labels: list[tuple[int, object]], pad: float = 2.0) -> None:
    """Remove labels that would overlap a label of higher priority (lower number first)."""
    renderer = fig.canvas.get_renderer()
    kept = []
    for _, t in sorted(labels, key=lambda x: x[0]):
        b = t.get_window_extent(renderer).expanded(1.0, 1.0)
        box = (b.x0 - pad, b.y0 - pad, b.x1 + pad, b.y1 + pad)
        if any(box[0] < k[2] and k[0] < box[2] and box[1] < k[3] and k[1] < box[3] for k in kept):
            t.remove()
        else:
            kept.append(box)


def render_plan(out: Output, path_png: Path, title: str | None = None) -> None:
    fig, ax = plt.subplots(figsize=(11, 11), dpi=110)
    ax.set_aspect("equal")
    ax.axis("off")
    allp = np.concatenate([np.array(r.polygon) for r in out.rooms]) if out.rooms else np.zeros((1, 2))
    lo, hi = allp.min(0) - 0.6, allp.max(0) + 0.6
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(hi[1], lo[1])  # y down, as the plan frame
    labels: list[tuple[int, object]] = []  # (priority, text): rooms first, then openings, then long walls
    wall_of = {w.id: w for r in out.rooms for w in r.walls}
    for room in out.rooms:
        poly = np.array(room.polygon)
        ax.add_patch(MplPolygon(poly, closed=True, facecolor=FILL.get(room.label, "#eef2e6"),
                                edgecolor="none", zorder=1))
        for w in room.walls:
            s, e = np.array(w.start), np.array(w.end)
            ax.plot([s[0], e[0]], [s[1], e[1]], color="#1a202c", lw=2.6, solid_capstyle="projecting", zorder=3)
            if w.length.value and w.length.value >= MIN_LABELLED_WALL:
                mid = (s + e) / 2
                d = (e - s) / max(np.linalg.norm(e - s), 1e-9)
                inward = np.array([-d[1], d[0]])
                ang = np.degrees(np.arctan2(d[1], d[0]))
                if ang > 90 or ang < -90:
                    ang += 180
                p = mid + inward * 0.22
                t = ax.text(p[0], p[1], _fmt(w.length), fontsize=6.5, ha="center", va="center",
                            rotation=-ang, color="#2d3748", zorder=5)
                labels.append((3 - min(w.length.value, 2.9) / 3, t))  # longer walls keep their label first
        shape = Polygon(poly)
        c = polylabel(shape, tolerance=0.02) if shape.is_valid else shape.centroid
        # a room narrower than ~1.6 m at its widest point gets a smaller label, so it stays inside
        narrow = shape.is_valid and shape.exterior.distance(c) < 0.8
        t = ax.text(c.x, c.y, f"{_room_name(room)}\n{_fmt(room.floor_area)} · {_height(room.ceiling_height)}",
                    fontsize=6.0 if narrow else 7.5, ha="center", va="center", weight="bold", color="#1a202c",
                    zorder=6, linespacing=1.3)
        labels.append((0, t))
        for o in room.openings:
            w = wall_of.get(o.wall_id)
            if w is None:
                continue
            s, e = np.array(w.start), np.array(w.end)
            d = (e - s) / max(np.linalg.norm(e - s), 1e-9)
            p0 = s + d * o.offset_along_wall
            p1 = p0 + d * (o.width.value or 0)
            col, ls = OPENING.get(o.type, OPENING["opening"])
            ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color="white", lw=3.2, zorder=4)
            ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color=col, lw=1.6, zorder=4.5, ls=ls)
            if o.width.value and o.width.value > 0.45:
                m = (p0 + p1) / 2
                q = m - np.array([-d[1], d[0]]) * 0.18
                t = ax.text(q[0], q[1], f"{o.width.value * 100:.0f}", fontsize=5.5, color=col, ha="center",
                            va="center", zorder=6)
                labels.append((1, t))
    for dmg in out.damage:
        col = DAMAGE_COLOR.get(dmg.damage_class, "k")
        ax.plot(dmg.center[0], dmg.center[1], marker="X", ms=9, color=col, zorder=7)
        t = ax.text(dmg.center[0] + 0.1, dmg.center[1] - 0.1, dmg.damage_class.replace("_", " "), fontsize=6,
                    color=col, zorder=7)
        labels.append((0.5, t))
    _declutter(fig, labels)
    # scale bar and legend
    ax.plot([lo[0] + 0.3, lo[0] + 1.3], [hi[1] - 0.25, hi[1] - 0.25], color="k", lw=2)
    ax.text(lo[0] + 0.8, hi[1] - 0.35, "1 m", ha="center", fontsize=7)
    ax.legend(handles=[Line2D([], [], color=c, ls=ls, lw=1.6, label=f"{k} (width cm)")
                       for k, (c, ls) in OPENING.items()],
              loc="lower right", fontsize=6.5, frameon=False)
    fp = out.property.footprint_area
    head = title or f"{out.capture.id} · tier: {out.capture.tier}"
    ax.set_title(f"{head}\nfootprint {fp.value:.1f} m² (90% CI {fp.ci90[0]:.1f}–{fp.ci90[1]:.1f}) · "
                 f"{len(out.rooms)} rooms · walls: length ± 90% half-width", fontsize=9)
    fig.tight_layout()
    fig.savefig(path_png)
    fig.savefig(Path(path_png).with_suffix(".svg"))
    plt.close(fig)
