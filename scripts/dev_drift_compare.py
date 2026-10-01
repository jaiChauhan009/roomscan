"""Compare drift variants with a segmentation-independent crispness metric."""
import sys
import numpy as np
from roomscan.frontends.lidar_stray import load_stray
from roomscan.geometry.drift import correct_drift
from roomscan.geometry.pointcloud import fuse_capture
from roomscan.geometry.planes import manhattan_yaw


def crispness(cloud):
    """Inverse participation ratio of axis-aligned wall coordinates (5 mm bins), x1000.
    Blurred (drifted) walls spread over more bins -> lower value."""
    yaw = manhattan_yaw(cloud)
    c, s = np.cos(yaw), np.sin(yaw)
    p, n = cloud.points, cloud.normals
    a = c * p[:, 0] + s * p[:, 2]; b = -s * p[:, 0] + c * p[:, 2]
    na = c * n[:, 0] + s * n[:, 2]; nb = -s * n[:, 0] + c * n[:, 2]
    out = []
    for coord, nn in [(a, na), (b, nb)]:
        m = (np.abs(nn) > 0.95) & (np.abs(n[:, 1]) < 0.2)
        h, _ = np.histogram(coord[m], bins=np.arange(coord[m].min(), coord[m].max() + 0.005, 0.005),
                            weights=cloud.weight[m])
        out.append((h ** 2).sum() / h.sum() ** 2 * 1000)
    return np.mean(out)


cap = load_stray(sys.argv[1], stride=5)
for name, kw in [("off", None), ("loop", dict(loop_closure=True, heading=False)),
                 ("heading", dict(loop_closure=False, heading=True)), ("loop+heading", dict())]:
    c = cap if kw is None else correct_drift(cap, **kw)[0]
    cl = fuse_capture(c, progress=False)
    print(f"{name:13s} crispness {crispness(cl):.3f}")
