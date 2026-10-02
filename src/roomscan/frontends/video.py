"""Video tier front end: handheld iPhone clip, no depth, no poses.

1. KLT optical-flow tracking at ~25 fps (corner tracks survive blank walls and blur far
   better than descriptor matching between sparse frames); keyframes on parallax
2. monocular metric depth per keyframe (Depth Anything V2 metric indoor)
3. incremental pose: PnP of each keyframe against 3D points of live tracks; the
   keyframe's depth map is scale-aligned to the map before it seeds new track points
4. metric scale = median over keyframes of the depth model's own scale, bias-corrected
5. gravity: plane direction along which the camera height stays constant
Output is a PosedCapture consumed by the same back end as LiDAR.

Intrinsics: an iPhone clip carries no calibration. We use, in order: a camera_matrix.csv
or intrinsics.json next to the video, an explicit hfov, or the iPhone main-camera default
(DEFAULT_HFOV_DEG). A focal-length error shows up as scale error and is covered by the
tier's calibrated interval.

Input: .mov / .mp4 / .m4v, H.264 or HEVC (8 or 10 bit), any resolution and frame rate,
variable frame rate included (frame times are read from the file, not derived from an
average rate). A clip filmed with the phone upright is stored landscape with a rotation
flag; the flag is applied so frames are upright, as the depth model expects.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from roomscan.capture import Frame, PosedCapture
from roomscan.pipeline import VIDEO_EXT, InputError, capture_videos, unwrap

DEFAULT_HFOV_DEG = 63.0  # iPhone 15 main camera, 1x, video mode (stabilisation crop)
WORK_W = 640
DEPTH_W = 256  # depth maps are reduced to LiDAR-like resolution for the shared back end
CACHE = Path(".cache")
MAX_KEYFRAMES = 320
# keyframes come ~4 per second (track continuity) and depth (~0.5 s per frame on a laptop CPU)
# dominates the tier once tracking is threaded. depth_keyframes can run it on a subset (about
# one per DEPTH_DT seconds plus wherever the map needs new points); off by default (0.0) until
# a subset is shown not to cost rooms on the benchmark clip (a first try, 0.5 s / 160 shared
# tracks, kept 470 of 494 keyframes and found 1 room instead of 2)
DEPTH_DT = 0.0
DEPTH_MIN_SHARED = 160
MIN_SECONDS = 3.0  # shorter clips cannot show a room
CODEC_HINT = ("the file is damaged or incomplete, or its codec is not supported (this build decodes H.264 "
              "and HEVC in .mov / .mp4). Copy the original clip again (not through a chat app)")


def quiet_ffmpeg() -> None:
    """Keep FFmpeg's own log lines ("moov atom not found", per-frame decode errors) off the
    console: our messages say what is wrong. OpenCV reads OPENCV_FFMPEG_LOGLEVEL when its
    FFmpeg plugin starts (first capture); on Windows the plugin reads msvcrt's copy of the
    environment, which os.environ does not update. A level the user set is kept."""
    if "OPENCV_FFMPEG_LOGLEVEL" in os.environ:
        return
    os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"  # AV_LOG_QUIET
    if os.name == "nt":
        try:
            import ctypes
            ctypes.cdll.msvcrt._putenv(b"OPENCV_FFMPEG_LOGLEVEL=-8")
        except (OSError, AttributeError):
            pass


def _capture(video: Path, auto_rotate: bool = True) -> cv2.VideoCapture:
    quiet_ffmpeg()
    cap = cv2.VideoCapture(str(video))  # FFmpeg backend: non-ASCII Windows paths work here
    # explicit, because OpenCV's default differs between versions (4.11: off)
    cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1 if auto_rotate else 0)
    return cap


def probe_video(video: Path, auto_rotate: bool = True) -> dict:
    """fps, frame count (container estimate, may be 0), upright frame size and the rotation
    flag of a clip; InputError when it cannot be opened or its first frame not decoded."""
    video = Path(video)
    if not video.is_file():
        raise InputError(f"{video} does not exist")
    if video.stat().st_size == 0:
        raise InputError(f"{video.name} is empty (0 bytes): copy the clip again")
    cap = _capture(video, auto_rotate)
    try:
        if not cap.isOpened():
            raise InputError(f"cannot open video {video.name}: {CODEC_HINT}")
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC)).to_bytes(4, "little").decode("latin1").strip("\x00 ")
        ok, img = cap.read()
        if not ok or img is None:
            raise InputError(f"cannot decode video {video.name} (codec '{fourcc or 'unknown'}'): {CODEC_HINT}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        return {"fps": float(fps) if np.isfinite(fps) and 1.0 <= fps <= 1000.0 else 30.0,
                "n_frames": max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)),
                "size": (int(img.shape[1]), int(img.shape[0])),
                "rotation": int(cap.get(cv2.CAP_PROP_ORIENTATION_META) or 0) if auto_rotate else 0,
                "codec": fourcc}
    finally:
        cap.release()


def find_video(path: Path) -> Path:
    """The clip to process: `path` itself, or the one walk-through clip in a folder (Live
    Photo companions of photos are not clips)."""
    path = Path(path)
    if path.is_file():
        return path
    if not path.is_dir():
        raise InputError(f"{path} does not exist")
    vids = capture_videos(unwrap(path))
    if not vids:
        raise InputError(f"no video ({', '.join(sorted(VIDEO_EXT))}) in {path}")
    if len(vids) > 1:
        names = ", ".join(p.name for p in vids[:4]) + (", ..." if len(vids) > 4 else "")
        raise InputError(f"{path} holds {len(vids)} videos ({names}): the video tier takes one walk-through "
                         f"clip, pass that file")
    return vids[0]


def _rotate_K(K: np.ndarray, rotation: int, W: int, H: int) -> np.ndarray:
    """Intrinsics of a stored W x H picture after OpenCV's auto-rotation for the clip's flag.

    OpenCV turns a clip flagged 90 clockwise ((u, v) -> (H - 1 - v, u)), 270 counter-
    clockwise and 180 upside down (checked in tests/test_inputs.py).
    """
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    if rotation == 90:
        return np.array([[fy, 0, H - 1 - cy], [0, fx, cx], [0, 0, 1.0]])
    if rotation == 270:
        return np.array([[fy, 0, cy], [0, fx, W - 1 - cx], [0, 0, 1.0]])
    if rotation == 180:
        return np.array([[fx, 0, W - 1 - cx], [0, fy, H - 1 - cy], [0, 0, 1.0]])
    return K


def _intrinsics(video: Path, w: int, h: int, hfov_deg: float | None, rotation: int = 0) -> tuple[np.ndarray, str]:
    """K for the upright w x h frames. A calibration file describes the stored picture, so
    for a rotated clip it is scaled to the stored size and then turned with the frames."""
    for cand in [video.with_name("camera_matrix.csv"), video.with_name("intrinsics.json")]:
        if cand.exists():
            if cand.suffix == ".csv":
                K = np.loadtxt(cand, delimiter=",")
                src_w = 2 * K[0, 2]
            else:
                j = json.loads(cand.read_text())
                K = np.array(j["K"], float)
                src_w = float(j.get("width", 2 * K[0, 2]))
            K = K.copy()
            W, H = (h, w) if rotation in (90, 270) else (w, h)  # stored picture at the processing size
            K[:2] *= W / src_w
            return _rotate_K(K, rotation, W, H), f"file:{cand.name}" + (f" (turned {rotation} deg)" if rotation else "")
    hf = np.deg2rad(hfov_deg or DEFAULT_HFOV_DEG)
    f = (max(w, h) / 2) / np.tan(hf / 2)  # hfov refers to the long image side
    return np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]]), f"hfov:{hfov_deg or DEFAULT_HFOV_DEG}"


def select_keyframes(cands: list[dict], min_common: int = 320, max_kf: int = MAX_KEYFRAMES) -> list[dict]:
    """Greedy thinning of keyframe candidates that keeps track continuity.

    From keyframe i jump to the farthest candidate still sharing >= min_common tracks,
    preferring the sharpest of the last few that qualify. Where nothing qualifies (a real
    tracking break) the next candidate is taken and a new segment starts there.
    """
    def run(mc):
        out = [0]
        i = 0
        while i < len(cands) - 1:
            ids_i = cands[i]["ids"]
            ok = []
            for j in range(i + 1, min(i + 40, len(cands))):
                c = np.intersect1d(ids_i, cands[j]["ids"], assume_unique=True).size
                if c >= mc:
                    ok.append(j)
                elif c < mc // 2:
                    break
            if ok:
                tail = ok[-3:]
                j = max(tail, key=lambda q: cands[q]["sharp"])
            else:
                j = i + 1
            out.append(j)
            i = j
        return out

    idx = run(min_common)
    if len(idx) > max_kf:
        idx = run(min_common // 2)
    sel = [cands[i] for i in idx]
    for k in sel:
        if "img" not in k:
            k["img"] = cv2.imdecode(k["jpg"], cv2.IMREAD_COLOR)[:, :, ::-1].copy()
    return sel


def track_video(video: Path, target_fps: float = 25.0, kf_parallax: float = 20.0,
                progress: bool = False) -> dict:
    """Pass 1: KLT tracks + keyframe candidates (frequent; thinned by select_keyframes)."""
    from tqdm import tqdm

    info = probe_video(video)
    cap = _capture(video)
    src_fps = info["fps"]
    n = info["n_frames"]  # estimate only: the loop below runs until the decoder stops
    step = max(1, int(round(src_fps / target_fps)))
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    lk = dict(winSize=(21, 21), maxLevel=3,
              criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    prev = None
    pts = np.zeros((0, 2), np.float32)
    ids = np.zeros(0, np.int64)
    # track ids (ascending: ids only grow) and positions at the last keyframe
    kf_ids = np.zeros(0, np.int64)
    kf_pts = np.zeros((0, 2), np.float32)
    next_id = 0
    kfs = []  # dicts: frame, t, img, ids, pts

    def add_keyframe(i, t_s, img, gray):
        nonlocal pts, ids, next_id, kf_ids, kf_pts
        mask = np.full(gray.shape, 255, np.uint8)
        for p in pts:
            cv2.circle(mask, (int(p[0]), int(p[1])), 8, 0, -1)
        want = 1200 - len(pts)
        if want > 50:
            new = cv2.goodFeaturesToTrack(gray, maxCorners=want, qualityLevel=0.004, minDistance=8,
                                          mask=mask, blockSize=5)
            if new is not None:
                new = new[:, 0, :]
                pts = np.concatenate([pts, new])
                ids = np.concatenate([ids, np.arange(next_id, next_id + len(new))])
                next_id += len(new)
        kf_ids, kf_pts = ids.copy(), pts.copy()
        sharp = float(cv2.Laplacian(gray, cv2.CV_32F).var())
        jpg = cv2.imencode(".jpg", img[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 92])[1]
        kfs.append({"frame": i, "t": t_s, "jpg": jpg, "ids": ids.copy(), "pts": pts.copy(),
                    "sharp": sharp})

    def decode(q: queue.Queue, stop: threading.Event):
        """Decoder thread: OpenCV releases the GIL while decoding, so the next frames are
        decoded and prepared while the main thread tracks this one."""
        i = -1
        t_last = -1.0
        try:
            # until the decoder stops: the container's frame count is an estimate (variable frame rate)
            while not stop.is_set() and cap.grab():
                i += 1
                # presentation time of this frame; i / fps is only right at a constant frame rate
                t_s = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                if not (np.isfinite(t_s) and t_s > t_last):
                    t_s = t_last + 1.0 / src_fps if t_last >= 0 else 0.0
                t_last = t_s
                if i % step:
                    continue
                ok, bgr = cap.retrieve()
                if not ok or bgr is None:
                    continue  # one undecodable frame: keep going
                h0, w0 = bgr.shape[:2]
                s = WORK_W / max(w0, h0)
                img = cv2.resize(bgr, (int(round(w0 * s)), int(round(h0 * s))), interpolation=cv2.INTER_AREA)
                gray = clahe.apply(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
                q.put((i, t_s, t_last, img, gray))
        finally:
            q.put((i, None, t_last, None, None))

    q: queue.Queue = queue.Queue(maxsize=8)
    stop = threading.Event()
    th = threading.Thread(target=decode, args=(q, stop), daemon=True)
    th.start()
    i = -1
    since_kf = 0
    t_last = -1.0
    done = 0
    bar = tqdm(total=n or None, desc="track", disable=not progress)
    try:
        while True:
            i, t_s, t_last, img, gray = q.get()
            bar.update(i + 1 - done)
            done = i + 1
            if img is None:
                break
            if prev is None:
                add_keyframe(i, t_s, img[:, :, ::-1].copy(), gray)
                prev = gray
                continue
            if len(pts):
                p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, gray, pts.reshape(-1, 1, 2), None, **lk)
                p0, st2, _ = cv2.calcOpticalFlowPyrLK(gray, prev, p1, None, **lk)
                fb = np.linalg.norm(p0[:, 0] - pts, axis=1)
                p1 = p1[:, 0]
                good = (st[:, 0] == 1) & (st2[:, 0] == 1) & (fb < 1.0) & (p1[:, 0] > 2) & (p1[:, 1] > 2) \
                    & (p1[:, 0] < gray.shape[1] - 2) & (p1[:, 1] < gray.shape[0] - 2)
                pts, ids = p1[good], ids[good]
            prev = gray
            since_kf += 1
            pos = np.minimum(np.searchsorted(kf_ids, ids), max(len(kf_ids) - 1, 0))
            sel = kf_ids[pos] == ids if len(kf_ids) else np.zeros(len(ids), bool)
            old = kf_pts[pos[sel]].reshape(-1, 2)
            n_kf = max(len(kfs[-1]["ids"]), 1)
            alive = len(old) / n_kf
            par = float(np.median(np.linalg.norm(pts[sel] - old, axis=1))) if len(old) else 1e9
            if par > kf_parallax or alive < 0.7 or since_kf > 2 * target_fps:
                add_keyframe(i, t_s, img[:, :, ::-1].copy(), gray)
                since_kf = 0
    finally:
        stop.set()
        while th.is_alive():  # unblock a decoder waiting on a full queue
            try:
                q.get(timeout=0.05)
            except queue.Empty:
                pass
        bar.close()
        cap.release()
    return {"kfs": kfs, "fps": src_fps, "n_frames": i + 1, "duration_s": max(t_last, 0.0),
            "rotation": info["rotation"], "codec": info["codec"]}


def depth_keyframes(kfs: list[dict], min_dt: float = DEPTH_DT, min_shared: int = DEPTH_MIN_SHARED) -> list[bool]:
    """Which keyframes get a depth map (the expensive step of the tier).

    A keyframe gets depth when it is the first or last, when min_dt seconds have passed
    since the last one with depth, or when the next keyframe would share fewer than
    min_shared tracks with the last one with depth (then it must seed new map points for
    PnP to continue). The others are posed by PnP only, which keeps the map's outlier
    pruning, and are left out of the output.
    """
    n = len(kfs)
    if min_dt <= 0:
        return [True] * n
    use = [False] * n
    if n == 0:
        return use
    use[0] = use[-1] = True
    last = 0
    for j in range(1, n - 1):
        nxt = np.intersect1d(kfs[last]["ids"], kfs[j + 1]["ids"], assume_unique=True).size
        if kfs[j]["t"] - kfs[last]["t"] >= min_dt or nxt < min_shared:
            use[j] = True
            last = j
    return use


def solve_poses(kfs: list[dict], depths: list[np.ndarray | None], K: np.ndarray) -> tuple[list[int], list[np.ndarray], list[float], dict]:
    """Pass 2: incremental PnP on track points. Returns kept keyframe indices, poses, scales.

    A keyframe whose depth is None is posed (its PnP still prunes outlier tracks from the
    map) but neither seeds map points nor is kept."""
    h, w = kfs[0]["img"].shape[:2]

    def depth_at(i, p):
        d = depths[i]
        u = np.clip((p[:, 0] * d.shape[1] / w).astype(int), 0, d.shape[1] - 1)
        v = np.clip((p[:, 1] * d.shape[0] / h).astype(int), 0, d.shape[0] - 1)
        return d[v, u]

    def lift(i, p, scale, T):
        z = depth_at(i, p) * scale
        P = np.stack([(p[:, 0] - K[0, 2]) * z / K[0, 0], (p[:, 1] - K[1, 2]) * z / K[1, 1], z], 1)
        return P @ T[:3, :3].T + T[:3, 3]

    segments = []  # each: {"kept": [], "poses": [], "scales": []}
    seg = None
    misses = 0
    world: dict[int, np.ndarray] = {}
    inl_counts = []
    for i, kf in enumerate(kfs):
        ids, p = kf["ids"], kf["pts"]
        has_d = depths[i] is not None
        known = np.array([int(t) in world for t in ids], bool)
        pose = None
        if seg is not None and known.sum() >= 12:
            obj = np.array([world[int(t)] for t in ids[known]], np.float64)
            ip = p[known].astype(np.float64)
            try:
                ok, rvec, tvec, inl = cv2.solvePnPRansac(obj, ip, K, None, iterationsCount=400,
                                                         reprojectionError=3.0, confidence=0.999,
                                                         flags=cv2.SOLVEPNP_SQPNP)
            except cv2.error:
                ok, inl = False, None
            if ok and inl is not None and len(inl) >= 12:
                inl = inl[:, 0]
                rvec, tvec = cv2.solvePnPRefineLM(obj[inl], ip[inl], K, None, rvec, tvec)
                R, _ = cv2.Rodrigues(rvec)
                T_cw = np.eye(4)
                T_cw[:3, :3], T_cw[:3, 3] = R, tvec[:, 0]
                Pc = obj[inl] @ R.T + tvec[:, 0]
                if has_d:
                    zp = depth_at(i, ip[inl].astype(np.float32))
                    gz = (Pc[:, 2] > 0.1) & (zp > 0.1)
                    if gz.sum() >= 8:
                        pose = (np.linalg.inv(T_cw), float(np.median(Pc[gz, 2] / zp[gz])))
                elif (Pc[:, 2] > 0.1).sum() >= 8:
                    pose = (np.linalg.inv(T_cw), None)
                if pose is not None:
                    inl_counts.append(len(inl))
                    # drop outlier tracks from the map
                    bad = np.setdiff1d(np.arange(len(obj)), inl)
                    for t in ids[known][bad]:
                        world.pop(int(t), None)
        if not has_d:
            continue  # posed only (or not at all): a keyframe with depth follows
        if pose is None:
            if seg is not None and known.sum() >= 12 and misses < 3:
                misses += 1  # bad keyframe (blur): skip it, live tracks still carry the map
                continue
            seg = {"kept": [], "poses": [], "scales": []}
            segments.append(seg)
            world = {}
            pose = (np.eye(4), 1.0)
        misses = 0
        T, s = pose
        seg["kept"].append(i)
        seg["poses"].append(T)
        seg["scales"].append(s)
        P = lift(i, p, s, T)
        zs = depth_at(i, p)
        for t, X, z in zip(ids, P, zs):
            if int(t) not in world and z > 0.1:
                world[int(t)] = X
    n_seg = len(segments)
    seg_sizes = sorted([len(sg["kept"]) for sg in segments], reverse=True)
    best, n_merged = merge_segments(segments, [k["img"] for k in kfs], depths, K,
                                    [k.get("sharp", 1.0) for k in kfs])
    order = np.argsort(best["kept"])
    stats = {"segments": n_seg, "segment_sizes": seg_sizes[:8], "segments_merged": n_merged,
             "keyframes": len(kfs),
             "tracked_keyframes": len(best["kept"]),
             "median_inliers": int(np.median(inl_counts)) if inl_counts else 0}
    return ([best["kept"][i] for i in order], [best["poses"][i] for i in order],
            [best["scales"][i] for i in order], stats)


class Matcher:
    """SIFT features + depth-assisted relative pose between two images (lazy, cached)."""

    def __init__(self, imgs: list[np.ndarray], depths: list[np.ndarray], K_of):
        self.imgs, self.depths, self.K_of = imgs, depths, K_of
        self.sift = cv2.SIFT_create(nfeatures=2500, contrastThreshold=0.01)
        self.clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        self.bf = cv2.BFMatcher(cv2.NORM_L2)
        self.feats: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def feat(self, i: int):
        if i not in self.feats:
            kp, des = self.sift.detectAndCompute(
                self.clahe.apply(cv2.cvtColor(self.imgs[i], cv2.COLOR_RGB2GRAY)), None)
            pts = np.array([k.pt for k in kp], np.float32).reshape(-1, 2)
            self.feats[i] = (pts, des if des is not None else np.zeros((0, 128), np.float32))
        return self.feats[i]

    def depth_at(self, i: int, p: np.ndarray) -> np.ndarray:
        d = self.depths[i]
        h, w = self.imgs[i].shape[:2]
        u = np.clip((p[:, 0] * d.shape[1] / w).astype(int), 0, d.shape[1] - 1)
        v = np.clip((p[:, 1] * d.shape[0] / h).astype(int), 0, d.shape[0] - 1)
        return d[v, u]

    def lift(self, i: int, p: np.ndarray) -> np.ndarray:
        """Keypoints of image i -> 3D in camera i (units of the predicted depth)."""
        K = self.K_of(i)
        z = self.depth_at(i, p)
        return np.stack([(p[:, 0] - K[0, 2]) * z / K[0, 0], (p[:, 1] - K[1, 2]) * z / K[1, 1], z], 1)

    def relative(self, a: int, b: int, min_inl: int = 30):
        """Pose of camera b in camera a's frame (a's depth units) + b's depth scale.

        Returns (T_ab, scale_b, n_inliers) or None. scale_b multiplies b's predicted depth
        to express it in a's depth units.
        """
        pa, da = self.feat(a)
        pb, db = self.feat(b)
        if len(pa) < 30 or len(pb) < 30:
            return None
        m = self.bf.knnMatch(db, da, k=2)
        good = [x for x, y in (q for q in m if len(q) == 2) if x.distance < 0.75 * y.distance]
        if len(good) < min_inl:
            return None
        qi = np.array([g.queryIdx for g in good])
        ti = np.array([g.trainIdx for g in good])
        obj = self.lift(a, pa[ti]).astype(np.float64)
        ip = pb[qi].astype(np.float64)
        ok = obj[:, 2] > 0.1
        obj, ip = obj[ok], ip[ok]
        if len(obj) < min_inl:
            return None
        try:
            okp, rvec, tvec, inl = cv2.solvePnPRansac(obj, ip, self.K_of(b), None, iterationsCount=600,
                                                      reprojectionError=3.0, confidence=0.999,
                                                      flags=cv2.SOLVEPNP_SQPNP)
        except cv2.error:
            return None
        if not okp or inl is None or len(inl) < min_inl:
            return None
        inl = inl[:, 0]
        rvec, tvec = cv2.solvePnPRefineLM(obj[inl], ip[inl], self.K_of(b), None, rvec, tvec)
        R, _ = cv2.Rodrigues(rvec)
        T_ba = np.eye(4)
        T_ba[:3, :3], T_ba[:3, 3] = R, tvec[:, 0]
        Pc = obj[inl] @ R.T + tvec[:, 0]
        zp = self.depth_at(b, ip[inl].astype(np.float32))
        gz = (Pc[:, 2] > 0.1) & (zp > 0.1)
        if gz.sum() < 10:
            return None
        return np.linalg.inv(T_ba), float(np.median(Pc[gz, 2] / zp[gz])), int(len(inl))


def merge_segments(segments: list[dict], imgs, depths, K, sharp: list[float] | None = None) -> tuple[dict, int]:
    """Attach broken tracking segments to the largest one by SIFT relocalisation."""
    segs = sorted([s for s in segments if len(s["kept"]) >= 3], key=lambda s: -len(s["kept"]))
    if not segs:
        return max(segments, key=lambda s: len(s["kept"])), 1
    base, pending = segs[0], segs[1:]
    if not pending:
        return base, 1
    sharp = sharp or [1.0] * len(imgs)
    mt = Matcher(imgs, depths, lambda i: K)
    merged = 1
    changed = True
    while changed and pending:
        changed = False
        # nearest in time first: the gap between two segments is usually a few blank frames
        pending.sort(key=lambda s: min(abs(s["kept"][0] - k) for k in base["kept"]))
        for sg in list(pending):
            bk = np.array(base["kept"])
            # the sharpest keyframes of the segment, spread over its length
            chunks = np.array_split(np.array(sg["kept"]), min(4, len(sg["kept"])))
            cand_b = [int(max(c, key=lambda q: sharp[q])) for c in chunks if len(c)]
            att = None
            for b in cand_b:
                near = bk[np.argsort(np.abs(bk - b))]
                spread = bk[np.linspace(0, len(bk) - 1, min(14, len(bk))).astype(int)]
                best = None
                for a_ in list(dict.fromkeys([int(x) for x in list(near[:4]) + list(spread)])):
                    r = mt.relative(a_, b, min_inl=25)
                    if r is not None and (best is None or r[2] > best[2][2]):
                        best = (a_, b, r)
                        if r[2] > 80:
                            break
                if best is not None:
                    att = best
                    break
            if att is None:
                continue
            a, b, (T_ab, s_b_in_a, _) = att
            ia, ib = base["kept"].index(a), sg["kept"].index(b)
            Ta, sa = base["poses"][ia], base["scales"][ia]
            Tb_seg, sb_seg = sg["poses"][ib], sg["scales"][ib]
            rel = T_ab.copy()
            rel[:3, 3] *= sa  # a's depth units -> base map units
            Tb = Ta @ rel
            sb = s_b_in_a * sa
            ratio = sb / sb_seg  # base map units per segment unit
            for k_, T, s_ in zip(sg["kept"], sg["poses"], sg["scales"]):
                r_ = np.linalg.inv(Tb_seg) @ T
                r_[:3, 3] *= ratio
                base["kept"].append(k_)
                base["poses"].append(Tb @ r_)
                base["scales"].append(s_ * ratio)
            pending.remove(sg)
            merged += 1
            changed = True
    return base, merged


def gravity_rotation(poses: list[np.ndarray], clouds_n: list[np.ndarray], clouds_p: list[np.ndarray],
                     image_down_hint: bool = True) -> tuple[np.ndarray, str]:
    """Rotation taking the map frame to a +Y-up world.

    Candidates for "down" are the mean camera +y, -y, +x, -x axes (phone held landscape or
    portrait). For each, the direction is refined with surface normals; the floor is the
    candidate with strong support below the cameras and near-constant camera height.
    """
    C = np.array([T[:3, 3] for T in poses])
    N = np.concatenate(clouds_n)
    P = np.concatenate(clouds_p)
    best = None
    for name, col, sign in [("+y", 1, 1), ("-y", 1, -1), ("+x", 0, 1), ("-x", 0, -1)]:
        down = sign * np.mean([T[:3, col] for T in poses], axis=0)
        if np.linalg.norm(down) < 0.5:
            continue  # this axis points all over the place: not gravity
        up = -down / np.linalg.norm(down)
        for _ in range(4):
            m = N @ up > np.cos(np.deg2rad(20))
            if m.sum() < 200:
                break
            up = N[m].mean(0)
            up /= np.linalg.norm(up)
        m = N @ up > np.cos(np.deg2rad(12))
        if m.sum() < 200:
            continue
        hgt = P[m] @ up
        floor = np.percentile(hgt, 20)
        cam_h = C @ up - floor
        support = float(m.mean())
        score = support / (0.05 + cam_h.std()) * (1.0 if 0.5 < np.median(cam_h) < 2.2 else 0.2)
        if name == "+y" and image_down_hint:
            score *= 1.5
        if best is None or score > best[0]:
            best = (score, up, name)
    if best is None:
        up, name = -np.mean([T[:3, 1] for T in poses], axis=0), "+y(fallback)"
        up /= np.linalg.norm(up)
    else:
        _, up, name = best
    y = np.array([0.0, 1.0, 0.0])
    v = np.cross(up, y)
    c = float(up @ y)
    if np.linalg.norm(v) < 1e-9:
        return (np.eye(3) if c > 0 else np.diag([1, -1, -1.0])), name
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c)), name


def build_posed_capture(name: str, tier: str, imgs, depths, K, ts, frame_ids, poses, scales,
                        metric: float, meta: dict, depth_sigma_rel: float) -> PosedCapture:
    """imgs/depths/ts/frame_ids/poses/scales are aligned lists over the kept frames."""
    from roomscan.geometry.pointcloud import backproject, image_normals

    h, w = imgs[0].shape[:2]
    small, Ks = [], []
    for d, s in zip(depths, scales):
        dh = int(round(DEPTH_W * d.shape[0] / d.shape[1]))
        ds = cv2.resize(d, (DEPTH_W, dh), interpolation=cv2.INTER_AREA) * s * metric
        Kd = K.copy()
        Kd[0] *= DEPTH_W / w
        Kd[1] *= dh / h
        small.append(ds.astype(np.float32))
        Ks.append(Kd)
    Tm = []
    for T in poses:
        T = T.copy()
        T[:3, 3] *= metric
        Tm.append(T)
    ns, ps = [], []
    for idx in range(0, len(small), max(1, len(small) // 60)):
        Pc = backproject(small[idx], Ks[idx])
        Nc = image_normals(Pc)
        ok = (small[idx] > 0.2) & (small[idx] < 6)
        sel = np.zeros_like(ok)
        sel[::3, ::3] = True
        ok &= sel
        ns.append(Nc[ok] @ Tm[idx][:3, :3].T)
        ps.append(Pc[ok] @ Tm[idx][:3, :3].T + Tm[idx][:3, 3])
    Rg, gname = gravity_rotation(Tm, ns, ps)
    G = np.eye(4)
    G[:3, :3] = Rg
    frames = []
    for i in range(len(small)):
        frames.append(Frame(index=int(frame_ids[i]), timestamp=float(ts[i]), T_wc=G @ Tm[i], K=Ks[i],
                            depth_fn=(lambda d=small[i]: d), rgb_fn=(lambda im=imgs[i]: im),
                            K_rgb=K.copy(), depth_sigma_rel=depth_sigma_rel))
    meta = dict(meta, gravity_axis=gname)
    return PosedCapture(tier=tier, name=name, frames=frames, meta=meta)


def cached_depths(imgs: list[np.ndarray], key: str, use_cache: bool, progress: bool,
                  want: list[bool] | None = None) -> list[np.ndarray | None]:
    """Depth maps of imgs (None where want is False), cached under key."""
    from roomscan.ml.depth import predict_depth

    want = [True] * len(imgs) if want is None else list(want)
    idx = [i for i, w_ in enumerate(want) if w_]
    cf = CACHE / f"depth_{key}.npz"
    out: list[np.ndarray | None] = [None] * len(imgs)
    if use_cache and cf.exists():
        z = np.load(cf)
        if sorted(z.files) == sorted(f"d{i}" for i in idx):
            for i in idx:
                out[i] = z[f"d{i}"].astype(np.float32)
            return out
    depths = predict_depth([imgs[i] for i in idx], progress=progress)
    if use_cache:
        CACHE.mkdir(exist_ok=True)
        np.savez_compressed(cf, **{f"d{i}": d.astype(np.float16) for i, d in zip(idx, depths)})
    for i, d in zip(idx, depths):
        out[i] = d.astype(np.float16).astype(np.float32)
    return out


def load_video(path: Path, use_cache: bool = True, progress: bool = True,
               hfov_deg: float | None = None) -> PosedCapture:
    from roomscan.ml.depth import DEPTH_SCALE_BIAS

    video = find_video(Path(path))
    prof = {}
    t0 = time.time()
    tr = track_video(video, progress=progress)
    prof["track"] = time.time() - t0
    if len(tr["kfs"]) < 2 or tr["duration_s"] < MIN_SECONDS:
        raise InputError(f"{video.name} is too short ({tr['n_frames']} frames, {tr['duration_s']:.1f} s): the video "
                         f"tier needs a walk through the rooms (docs/capture_protocol.md)")
    kfs = select_keyframes(tr["kfs"])
    want = depth_keyframes(kfs)
    imgs = [k["img"] for k in kfs]
    h, w = imgs[0].shape[:2]
    K, k_src = _intrinsics(video, w, h, hfov_deg, rotation=tr["rotation"])
    # rotated clips are keyed apart: depth cached before rotation was applied must not be reused
    rot = f"|rot{tr['rotation']}" if tr["rotation"] else ""
    sub = f"|dk{sum(want)}" if not all(want) else ""  # depth on every keyframe: the key as before
    key = hashlib.sha1(f"{video.resolve()}|{video.stat().st_size}|{len(imgs)}|{kfs[-1]['frame']}{rot}{sub}"
                       .encode()).hexdigest()[:16]
    t0 = time.time()
    depths = cached_depths(imgs, key, use_cache, progress, want)
    prof["depth"] = time.time() - t0
    t0 = time.time()
    kept, poses, scales, stats = solve_poses(kfs, depths, K)
    prof["pnp"] = time.time() - t0
    # map units -> metres: the model's own (bias-corrected) scale, median over keyframes
    metric = float(np.median(1.0 / np.array(scales))) / DEPTH_SCALE_BIAS
    meta = {"source": "video", "video": video.name, "intrinsics": k_src, "n_video_frames": tr["n_frames"],
            "codec": tr["codec"], "rotation_applied_deg": tr["rotation"], "duration_s": round(tr["duration_s"], 2),
            "n_keyframe_candidates": len(tr["kfs"]), "n_keyframes": len(kfs), "n_depth_keyframes": int(sum(want)),
            "n_tracked": len(kept), "vo_segments": stats["segments"],
            "load_profile_s": {k: round(v, 1) for k, v in prof.items()},
            "vo_segments_merged": stats["segments_merged"], "vo_median_inliers": stats["median_inliers"], "metric_scale": round(metric, 4),
            "depth_model": "Depth-Anything-V2-Metric-Indoor-Small"}
    return build_posed_capture(video.stem, "video", [imgs[i] for i in kept], [depths[i] for i in kept], K,
                               [kfs[i]["t"] for i in kept], [kfs[i]["frame"] for i in kept], poses, scales,
                               metric, meta, depth_sigma_rel=0.05)


def scale_capture(cap: PosedCapture, s: float) -> PosedCapture:
    """The same capture with every length multiplied by s (depth maps and camera positions)."""
    from dataclasses import replace

    frames = []
    for f in cap.frames:
        T = f.T_wc.copy()
        T[:3, 3] *= s
        frames.append(replace(f, T_wc=T, depth_fn=(lambda fn=f.depth_fn: fn() * np.float32(s))))
    return PosedCapture(tier=cap.tier, name=cap.name, frames=frames, meta=dict(cap.meta))


def apply_known_sizes(cap: PosedCapture, cloud, layout, known, warnings: list[str]):
    """Rescale a video capture so its rooms agree with the user's tape numbers (one global
    scale: the median over the measured rooms), then extract the layout again in metres.

    Returns (cap, cloud, layout, plan); plan is None when nothing given could be used."""
    from roomscan import known_sizes as KS
    from roomscan.geometry.boxfit import box_layout
    from roomscan.geometry.layout import extract_layout
    from roomscan.geometry.pointcloud import Cloud

    p = KS.plan(known, [KS.dims_of(r) for r in layout.rooms], "global")
    if p is None:
        return cap, cloud, layout, None
    warnings += p.warnings
    if not p.ratios:
        return cap, cloud, layout, None
    s = p.scale
    if abs(s - 1.0) > 1e-3:
        cap = scale_capture(cap, s)
        cloud = Cloud(cloud.points * np.float32(s), cloud.normals, cloud.weight)
        rescaled = extract_layout(cloud)
        if not rescaled.rooms:
            rescaled = box_layout(cloud, noise=0.08) or rescaled
        if rescaled.rooms:
            layout = rescaled
        else:  # keep the rooms found before, scaled
            warnings.append("known sizes: no room found again after rescaling; the earlier rooms are kept")
            for r in layout.rooms:
                KS.scale_room(r, s)
    cap.meta["metric_scale"] = round(float(cap.meta.get("metric_scale", 1.0)) * s, 4)
    cap.meta["metric_scale_source"] = "known sizes"
    return cap, cloud, layout, p
