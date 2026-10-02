"""Synthetic rooms with exactly known dimensions, as fused point clouds."""
from __future__ import annotations

import numpy as np

from roomscan.geometry.pointcloud import Cloud


def _plane(origin, e1, e2, n, l1, l2, step, holes=()):
    u, v = np.meshgrid(np.arange(step / 2, l1, step), np.arange(step / 2, l2, step))
    u, v = u.ravel(), v.ravel()
    keep = np.ones(len(u), bool)
    for (u0, u1, v0, v1) in holes:
        keep &= ~((u > u0) & (u < u1) & (v > v0) & (v < v1))
    p = np.asarray(origin, float) + u[keep, None] * np.asarray(e1, float) + v[keep, None] * np.asarray(e2, float)
    return p, np.tile(np.asarray(n, float), (len(p), 1))


def box_room(width=4.0, depth=3.0, height=2.7, origin=(0.0, 0.0), step=0.02, noise=0.003,
             door=None, floor_y=-1.4, seed=0) -> Cloud:
    """Axis-aligned room. Plan x in [ox, ox+width], z in [oz, oz+depth]; +Y up.

    door = (wall index 0..3, offset along wall, width, height) cuts an opening.
    Walls: 0 = z-min side, 1 = x-max side, 2 = z-max side, 3 = x-min side.
    """
    ox, oz = origin
    rng = np.random.default_rng(seed)
    parts = [
        _plane((ox, floor_y, oz), (1, 0, 0), (0, 0, 1), (0, 1, 0), width, depth, step),
        _plane((ox, floor_y + height, oz), (1, 0, 0), (0, 0, 1), (0, -1, 0), width, depth, step),
    ]
    walls = [((ox, floor_y, oz), (1, 0, 0), (0, 0, 1), width),
             ((ox + width, floor_y, oz), (0, 0, 1), (-1, 0, 0), depth),
             ((ox + width, floor_y, oz + depth), (-1, 0, 0), (0, 0, -1), width),
             ((ox, floor_y, oz + depth), (0, 0, -1), (1, 0, 0), depth)]
    for i, (o, e1, n, length) in enumerate(walls):
        holes = [(door[1], door[1] + door[2], 0.0, door[3])] if door and door[0] == i else []
        parts.append(_plane(o, e1, (0, 1, 0), n, length, height, step, holes))
    P = np.concatenate([p for p, _ in parts])
    N = np.concatenate([n for _, n in parts])
    P = P + N * rng.normal(0, noise, (len(P), 1))
    return Cloud(P.astype(np.float32), N.astype(np.float32), np.ones(len(P), np.float32))


def furnished_room(boxes, width=4.0, depth=3.0, height=2.7, floor_y=-1.4, seed=0) -> Cloud:
    """box_room with furniture against its walls: boxes = (x0, x1, z0, z1, height), each
    touching the z = 0 or the z = depth wall. The wall and floor a piece hides are removed;
    its front, sides and top are added."""
    room = box_room(width, depth, height, floor_y=floor_y, seed=seed)
    p = room.points
    keep = np.ones(len(p), bool)
    parts = []
    for x0, x1, z0, z1, h in boxes:
        inside = (p[:, 0] > x0 - 0.01) & (p[:, 0] < x1 + 0.01) & (p[:, 2] > z0 - 0.01) & (p[:, 2] < z1 + 0.01)
        keep &= ~(inside & (p[:, 1] < floor_y + h))
        front = (_plane((x0, floor_y, z1), (1, 0, 0), (0, 1, 0), (0, 0, 1), x1 - x0, h, 0.02) if z0 < 0.05 else
                 _plane((x0, floor_y, z0), (1, 0, 0), (0, 1, 0), (0, 0, -1), x1 - x0, h, 0.02))
        parts += [front, _plane((x0, floor_y, z0), (0, 0, 1), (0, 1, 0), (-1, 0, 0), z1 - z0, h, 0.02),
                  _plane((x1, floor_y, z0), (0, 0, 1), (0, 1, 0), (1, 0, 0), z1 - z0, h, 0.02),
                  _plane((x0, floor_y + h, z0), (1, 0, 0), (0, 0, 1), (0, 1, 0), x1 - x0, z1 - z0, 0.02)]
    P = np.concatenate([p[keep]] + [q for q, _ in parts])
    N = np.concatenate([room.normals[keep]] + [n for _, n in parts])
    return Cloud(P.astype(np.float32), N.astype(np.float32), np.ones(len(P), np.float32))


def merge(*clouds: Cloud) -> Cloud:
    return Cloud(np.concatenate([c.points for c in clouds]), np.concatenate([c.normals for c in clouds]),
                 np.concatenate([c.weight for c in clouds]))
