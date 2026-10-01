import sys, time
from pathlib import Path
import cv2, numpy as np
sys.path.insert(0, "scripts")
from dev_synth_damage import paint
from roomscan.frontends.lidar_stray import load_stray
from roomscan.geometry.layout import extract_layout
from roomscan.pipeline import cached_fuse
from roomscan.damage.detect import SurfaceIndex, select_frames, classify_tiles, detect_frame
from roomscan.geometry.pointcloud import backproject

scan = sys.argv[1]
cap = load_stray(Path(scan), stride=5)
cloud = cached_fuse(cap, (str(Path(scan).resolve()), "lidar", 5, "off"), progress=False)
layout = extract_layout(cloud)
room, wall = max(((r, w) for r in layout.rooms for w in r.walls if w.coverage > 0.8), key=lambda rw: rw[1].length)
print("wall", wall.id, round(wall.length, 2), "room h", room.height)
cap2 = paint(cap, layout, wall, room, wall.length / 2, 1.3, 0.25)
sidx = SurfaceIndex(layout, 0.06)
fr = layout.frame
d_w = (wall.end - wall.start) / wall.length
centre_plan = wall.start + d_w * wall.length / 2
cw = fr.to_world(centre_plan[None])[0]
C3 = np.array([cw[0], room.floor.value + 1.3, cw[1]])
out = Path("runs/dev_damage"); out.mkdir(parents=True, exist_ok=True)
seen = 0
t = time.time()
for f in cap2.frames:
    pc = (C3 - f.T_wc[:3, 3]) @ f.T_wc[:3, :3]
    if pc[2] < 0.8 or pc[2] > 3.5:
        continue
    u, v = f.K_rgb[0, 0] * pc[0] / pc[2] + f.K_rgb[0, 2], f.K_rgb[1, 1] * pc[1] / pc[2] + f.K_rgb[1, 2]
    if not (350 < u < 1570 and 350 < v < 1090):
        continue
    img = f.rgb_fn()
    seen += 1
    if seen <= 2:
        cv2.imwrite(str(out / f"painted_{seen}.jpg"), cv2.resize(img, (960, 720))[:, :, ::-1])
    s = 0.5
    x, y = int(u * s), int(v * s)
    small = cv2.resize(img, (960, 720))
    tiles = []
    for size in (224, 384):
        x0, y0 = np.clip(x - size // 2, 0, 960 - size), np.clip(y - size // 2, 0, 720 - size)
        tiles.append(small[y0:y0 + size, x0:x0 + size])
    cls, p = classify_tiles(tiles)
    obs = detect_frame(f, sidx, 0.6)
    print("frame", f.index, "dist", round(pc[2], 2), "centred tiles:", list(zip(cls, np.round(p, 2))), "detections:", [(o.damage_class, o.surface_id, round(o.score, 2), round(o.area, 3)) for o in obs])
    if seen >= 8:
        break
print("frames seeing the stain:", seen, "time", round(time.time() - t, 1))
