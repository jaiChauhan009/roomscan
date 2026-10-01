import sys, numpy as np, cv2
from pathlib import Path
from roomscan.frontends.lidar_stray import load_stray
from roomscan.geometry.pointcloud import Cloud
from roomscan.geometry.layout import extract_layout
import roomscan.geometry.openings as O
cap = load_stray('../data/single_scan_with_ceiling', stride=5)
z = np.load('runs/dev_ceiling/cloud.npz'); cloud = Cloud(z['p'], z['n'], z['w'])
L = extract_layout(cloud)
room = [r for r in L.rooms if r.id == sys.argv[1]][0]
L.rooms = [room]
captured = []
orig = O._extract
def grab(a, i):
    captured.append(a); return orig(a, i)
O._extract = grab
ops = O.detect_openings(cap, L)
out = Path('runs/dev_ceiling/walls'); out.mkdir(exist_ok=True)
for a in captured:
    w = a.wall
    print(w.id, w.orient, 'len', round(w.length, 2), 'start', np.round(w.start, 2), 'inward', np.round(w.inward, 2), 'hit', int(a.hit.sum()), 'pass', int(a.pas.sum()))
    vis = np.concatenate([np.log1p(a.hit), np.log1p(a.pas)], 0)
    vis = (255 * vis / max(vis.max(), 1e-6)).astype(np.uint8)[::-1]
    cv2.imwrite(str(out / f'{w.id}.png'), cv2.resize(vis, (a.nu * 2, vis.shape[0] * 8), interpolation=cv2.INTER_NEAREST))
