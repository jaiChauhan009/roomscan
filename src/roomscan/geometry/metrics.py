"""Map self-consistency metrics (no ground truth needed)."""
from __future__ import annotations

import numpy as np

from roomscan.geometry.planes import manhattan_yaw
from roomscan.geometry.pointcloud import Cloud


def crispness(cloud: Cloud) -> float:
    """Inverse participation ratio (x1000) of axis-aligned wall coordinates in 5 mm bins.

    A wall observed at different times lands in the same bins if poses are consistent;
    drift spreads it over more bins and lowers the value. Comparable between runs of the
    same capture only.
    """
    yaw = manhattan_yaw(cloud)
    c, s = np.cos(yaw), np.sin(yaw)
    p, n = cloud.points, cloud.normals
    a = c * p[:, 0] + s * p[:, 2]
    b = -s * p[:, 0] + c * p[:, 2]
    na = c * n[:, 0] + s * n[:, 2]
    nb = -s * n[:, 0] + c * n[:, 2]
    out = []
    for coord, nn in [(a, na), (b, nb)]:
        m = (np.abs(nn) > 0.95) & (np.abs(n[:, 1]) < 0.2)
        if m.sum() < 100:
            continue
        h, _ = np.histogram(coord[m], bins=np.arange(coord[m].min(), coord[m].max() + 0.005, 0.005),
                            weights=cloud.weight[m])
        out.append((h ** 2).sum() / h.sum() ** 2 * 1000)
    return float(np.mean(out)) if out else float("nan")
