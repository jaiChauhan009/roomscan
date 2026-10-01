"""Evaluate video-tier VO against ARKit poses of the same Stray capture."""
import sys, time
import numpy as np, cv2
from pathlib import Path
from roomscan.frontends.video import load_video
from roomscan.frontends.lidar_stray import load_stray

scan = Path(sys.argv[1])
hfov = float(sys.argv[2]) if len(sys.argv) > 2 else None
video = scan / "rgb.mp4"
t = time.time()
if hfov:  # simulate "no intrinsics" by copying video elsewhere is slow; temporarily hide csv
    import shutil
    tmp = Path(".cache/vid_noK"); tmp.mkdir(parents=True, exist_ok=True)
    if not (tmp / f"{scan.name}.mp4").exists():
        shutil.copy(video, tmp / f"{scan.name}.mp4")
    video = tmp / f"{scan.name}.mp4"
cap = load_video(video, hfov_deg=hfov)
print("video tier load", round(time.time() - t, 1), "s", cap.meta)
ref = load_stray(scan, stride=1)
fps = cv2.VideoCapture(str(scan / "rgb.mp4")).get(5)
A, B = [], []
for f in cap.frames:
    i = f.index
    if i < len(ref.frames):
        A.append(f.T_wc[:3, 3]); B.append(ref.frames[i].T_wc[:3, 3])
A, B = np.array(A), np.array(B)
# similarity alignment A -> B
ma, mb = A.mean(0), B.mean(0)
U, S, Vt = np.linalg.svd((B - mb).T @ (A - ma))
D = np.eye(3); D[2, 2] = np.sign(np.linalg.det(U @ Vt))
R = U @ D @ Vt
s = np.trace(np.diag(S) @ D) / ((A - ma) ** 2).sum()
err = np.linalg.norm((s * (A - ma) @ R.T + mb) - B, axis=1)
path = np.linalg.norm(np.diff(B, axis=0), axis=1).sum()
print(f"frames {len(A)}  path {path:.2f} m  scale(ref/video) {s:.3f}  ATE rmse {np.sqrt((err**2).mean())*100:.1f} cm  max {err.max()*100:.1f} cm")
