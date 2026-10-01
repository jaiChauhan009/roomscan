import sys, numpy as np, cv2
from pathlib import Path
import roomscan.frontends.video as V
video = Path(sys.argv[1])
tr = V.track_video(video)
kfs = V.select_keyframes(tr["kfs"])
print("keyframes", len(kfs), "of", len(tr["kfs"]), "candidates,", tr["n_frames"], "frames")
imgs = [k["img"] for k in kfs]
h, w = imgs[0].shape[:2]
K, _ = V._intrinsics(video, w, h, None)
import hashlib
key = hashlib.sha1(f"{video.resolve()}|{video.stat().st_size}|{len(imgs)}|{kfs[-1]['frame']}".encode()).hexdigest()[:16]
depths = V.cached_depths(imgs, key, True, False)
# survival: how many tracks of kf i are still present in kf i+1
row = []
for i in range(1, len(kfs)):
    common = np.intersect1d(kfs[i - 1]["ids"], kfs[i]["ids"]).size
    sharp = cv2.Laplacian(cv2.cvtColor(imgs[i], cv2.COLOR_RGB2GRAY), cv2.CV_32F).var()
    row.append((i, kfs[i]["frame"], len(kfs[i]["ids"]), common, int(sharp)))
bad = [r for r in row if r[3] < 30]
print("kf pairs with <30 common tracks:", len(bad))
print(bad[:40])

kept, poses, scales, stats = V.solve_poses(kfs, depths, K)
print(stats)
