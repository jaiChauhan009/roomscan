"""Dev helper: fuse a LiDAR capture, extract layout, dump diagnostic images."""
import sys, time
from pathlib import Path
import numpy as np, cv2
from roomscan.frontends.lidar_stray import load_stray
from roomscan.geometry.pointcloud import fuse_capture
from roomscan.geometry.layout import extract_layout

src, out = Path(sys.argv[1]), Path(sys.argv[2])
stride = int(sys.argv[3]) if len(sys.argv) > 3 else 5
out.mkdir(parents=True, exist_ok=True)
t = time.time()
cap = load_stray(src, stride=stride)
cache = out / "cloud.npz"
if cache.exists():
    from roomscan.geometry.pointcloud import Cloud
    z = np.load(cache); cloud = Cloud(z["p"], z["n"], z["w"])
else:
    cloud = fuse_capture(cap, progress=False)
    np.savez(cache, p=cloud.points, n=cloud.normals, w=cloud.weight)
print("fused", len(cloud), "pts in", round(time.time() - t, 1), "s")
t = time.time()
L = extract_layout(cloud)
print("layout in", round(time.time() - t, 1), "s; floor", round(L.floor.value, 3),
      "ceiling", None if L.ceiling is None else round(L.ceiling.value, 3))
g = L.grids
img = np.full(L.frame.shape + (3,), 255, np.uint8)
img[g["footprint"]] = (230, 230, 230)
img[g["floor_obs"]] = (200, 220, 200)
rng = np.random.default_rng(0)
for r in L.rooms:
    img[r.mask] = rng.integers(120, 230, 3)
img[g["wall"]] = (0, 0, 0)
img[g["lintel"]] = (0, 0, 255)
for r in L.rooms:
    pts = ((r.polygon - [L.frame.a0, L.frame.b0]) / L.frame.res).astype(np.int32)
    cv2.polylines(img, [pts], True, (255, 0, 0), 1)
    c = pts.mean(0).astype(int)
    hgt = r.height
    cv2.putText(img, f"{r.id} {r.area:.1f}m2 h={hgt if hgt is None else round(hgt,3)}", tuple(c - [60, 0]),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)
    print(r.id, "area", round(r.area, 2), "h", None if hgt is None else round(hgt, 3), r.ceiling_source,
          "walls", [(w.orient, round(w.length, 3), w.support, round(w.coverage, 2)) for w in r.walls])
cv2.imwrite(str(out / "layout_debug.png"), img[:, :, ::-1])
