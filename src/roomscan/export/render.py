"""Render the stitched, dimensioned floor plan (PNG + SVG) from the output contract."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

from roomscan.schema import Output  # noqa: E402

FILL = {"corridor": "#f1ead8", "small_room": "#dcecf2", "room": "#eef2e6"}
DAMAGE_COLOR = {"water_stain": "#2b6cb0", "crack": "#c53030", "mold": "#2f855a",
                "peeling_paint": "#b7791f", "hole": "#6b46c1", "other": "#4a5568"}


def _fmt(m) -> str:
    if m.value is None:
        return "n/a" if m.lower_bound is None else f">{m.lower_bound:.2f}"
    half = (m.ci90[1] - m.ci90[0]) / 2 if m.ci90 else 0
    return f"{m.value:.2f}±{half * 100:.0f}cm" if m.unit == "m" else f"{m.value:.1f}m²"


def render_plan(out: Output, path_png: Path, title: str | None = None) -> None:
    fig, ax = plt.subplots(figsize=(11, 11), dpi=110)
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.axis("off")
    wall_of = {}
    for room in out.rooms:
        poly = np.array(room.polygon)
        ax.add_patch(MplPolygon(poly, closed=True, facecolor=FILL.get(room.label, "#eef2e6"),
                                edgecolor="none", zorder=1))
        for w in room.walls:
            wall_of[w.id] = w
            s, e = np.array(w.start), np.array(w.end)
            ax.plot([s[0], e[0]], [s[1], e[1]], color="#1a202c", lw=2.6, solid_capstyle="projecting", zorder=3)
            if w.length.value and w.length.value >= 0.6:
                mid = (s + e) / 2
                d = (e - s) / max(np.linalg.norm(e - s), 1e-9)
                inward = np.array([-d[1], d[0]])
                ang = np.degrees(np.arctan2(d[1], d[0]))
                if ang > 90 or ang < -90:
                    ang += 180
                p = mid + inward * 0.22
                ax.text(p[0], p[1], _fmt(w.length), fontsize=6.5, ha="center", va="center",
                        rotation=-ang, color="#2d3748", zorder=5)
        c = np.array(room.polygon).mean(0)
        txt = f"{room.id} ({room.label})\n{_fmt(room.floor_area)}  h {_fmt(room.ceiling_height)}"
        ax.text(c[0], c[1], txt, fontsize=7.5, ha="center", va="center", weight="bold", color="#1a202c", zorder=6)
        for o in room.openings:
            w = wall_of.get(o.wall_id)
            if w is None:
                continue
            s, e = np.array(w.start), np.array(w.end)
            d = (e - s) / max(np.linalg.norm(e - s), 1e-9)
            p0 = s + d * o.offset_along_wall
            p1 = p0 + d * (o.width.value or 0)
            col = "#38a169" if o.type == "door" else ("#3182ce" if o.type == "window" else "#d69e2e")
            ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color="white", lw=3.2, zorder=4)
            ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color=col, lw=1.6, zorder=4.5,
                    ls="-" if o.type != "window" else "--")
            if o.width.value and o.width.value > 0.45:
                m = (p0 + p1) / 2
                inward = np.array([-d[1], d[0]])
                q = m - inward * 0.18
                ax.text(q[0], q[1], f"{o.width.value * 100:.0f}", fontsize=5.5, color=col, ha="center",
                        va="center", zorder=6)
    for dmg in out.damage:
        ax.plot(dmg.center[0], dmg.center[1], marker="X", ms=9, color=DAMAGE_COLOR.get(dmg.damage_class, "k"), zorder=7)
        ax.text(dmg.center[0] + 0.1, dmg.center[1] - 0.1, dmg.damage_class, fontsize=6,
                color=DAMAGE_COLOR.get(dmg.damage_class, "k"), zorder=7)
    for a in out.property.adjacency:
        pass
    allp = np.concatenate([np.array(r.polygon) for r in out.rooms]) if out.rooms else np.zeros((1, 2))
    lo, hi = allp.min(0) - 0.6, allp.max(0) + 0.6
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(hi[1], lo[1])
    # scale bar
    ax.plot([lo[0] + 0.3, lo[0] + 1.3], [hi[1] - 0.25, hi[1] - 0.25], color="k", lw=2)
    ax.text(lo[0] + 0.8, hi[1] - 0.35, "1 m", ha="center", fontsize=7)
    fp = out.property.footprint_area
    head = title or f"{out.capture.id} · tier: {out.capture.tier}"
    ax.set_title(f"{head}\nfootprint {fp.value:.1f} m² (90% CI {fp.ci90[0]:.1f}–{fp.ci90[1]:.1f}) · "
                 f"{len(out.rooms)} rooms · dims: value ± 90% half-width", fontsize=9)
    fig.tight_layout()
    fig.savefig(path_png)
    fig.savefig(Path(path_png).with_suffix(".svg"))
    plt.close(fig)
