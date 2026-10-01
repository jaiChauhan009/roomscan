"""Dev: top-down and side views of a fused cloud for any tier (video file or Stray folder)."""
import sys
from pathlib import Path
import numpy as np, cv2
from roomscan.pipeline import detect_tier, cached_fuse
from roomscan.geometry.layout import extract_layout

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
tier = detect_tier(src)
if tier == "video":
    from roomscan.frontends.video import load_video
    cap = load_video(src, progress=False)
else:
    from roomscan.frontends.lidar_stray import load_stray
    cap = load_stray(src, stride=5)
cloud = cached_fuse(cap, (str(src.resolve()), tier, "view"), progress=False)
P = cloud.points
print(tier, "points", len(P), "y pct", np.round(np.percentile(P[:, 1], [1, 50, 99]), 2))
for name, (i, j) in {"top": (0, 2), "side": (0, 1)}.items():
    a, b = P[:, i], P[:, j]
    lo = np.percentile(np.c_[a, b], 1, axis=0) - 0.3; hi = np.percentile(np.c_[a, b], 99, axis=0) + 0.3
    res = 0.02
    g = np.zeros((int((hi[1] - lo[1]) / res) + 1, int((hi[0] - lo[0]) / res) + 1))
    m = (a > lo[0]) & (a < hi[0]) & (b > lo[1]) & (b < hi[1])
    np.add.at(g, (((b[m] - lo[1]) / res).astype(int), ((a[m] - lo[0]) / res).astype(int)), 1)
    g = np.clip(g / max(np.percentile(g[g > 0], 95), 1), 0, 1)
    img = (255 - 255 * g).astype(np.uint8)
    cv2.imwrite(str(out / f"{name}.png"), img if name == "top" else img[::-1])
try:
    L = extract_layout(cloud)
    print("floor", round(L.floor.value, 3), "rooms", len(L.rooms))
    g = L.grids
    img = np.full(L.frame.shape + (3,), 255, np.uint8)
    img[g["floor_obs"]] = (200, 220, 200); img[g["wall"]] = (0, 0, 0); img[g["lintel"]] = (0, 0, 255)
    cv2.imwrite(str(out / "grids.png"), img)
except Exception as e:
    print("layout failed:", e)
