"""Turn an ARKitScenes raw sequence into a Stray Scanner export that `roomscan run` reads unchanged.

usage: python scripts/arkitscenes_to_stray.py <raw_dir> <out_dir> [--no-check]

<raw_dir> holds lowres_wide.traj and lowres_depth/, confidence/, lowres_wide/, lowres_wide_intrinsics/
(the layout of scripts/fetch_arkitscenes.py and of Apple's download_data.py). <out_dir> gets what the
Stray loader (src/roomscan/frontends/lidar_stray.py) reads:
    depth/000000.png       256x192 uint16 mm   (the ARKitScenes PNG, byte for byte)
    confidence/000000.png  256x192 uint8 0..2  (byte for byte)
    rgb.mp4                the lowres_wide frames, one per odometry row, in order (256x192, mp4v)
    odometry.csv           timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy, distortion_center_x,
                           distortion_center_y: the header and number format of the sample exports
    camera_matrix.csv      3x3 intrinsics of the colour frames (median over frames)
    arkitscenes.json       source, licence, sky direction, the pose check below (the loader ignores it)
No imu.csv: ARKitScenes has none and the loader does not use it.

Frames: only those with a measured pose. lowres_wide.traj is 10 Hz while depth is 60 Hz; a frame is used
when its timestamp is within 5 ms of a pose (the official loader's rule). Interpolating the trajectory to
60 Hz was measured to cost 0.2-0.6 deg of rotation (median) at 5 Hz spacing, roughly a quarter of that at
10 Hz: centimetres on a wall 3 m away, an error no real Stray export has. The export therefore runs at
10 fps (a Stray export runs at ~46-60), so roomscan's default stride of 5 uses 2 frames per second.

Tracking jumps: where the camera moves faster than 2.5 m/s between two poses (ARKit re-localising; clean
captures peak at 0.6-1.4 m/s, one selected capture jumps 0.9 m in 0.2 s after 5.5 s of initialisation),
only the longest run of poses without a jump is kept; arkitscenes.json records what was dropped.

Poses: a traj line is t, angle-axis, translation of the WORLD-TO-CAMERA transform with OpenCV camera axes
(the official loader inverts it and back-projects depth with K^-1 [u v 1] d), in a world with +Z up
(rectify_im.py: the image is upright when world z in camera coordinates is (0, -1, 0)). Stray poses are
camera-to-world with OpenCV camera axes in a +Y-up world, so
    T_stray = W @ inverse([R(angle-axis) | t]),   W = rotation taking +Z to +Y (x -> x, y -> -z, z -> y).
The check (on by default) fuses every 5th frame's confident depth for this and for the alternative
readings (not inverted; ARKit/OpenGL camera axes; no Z-to-Y turn) and requires the chosen one to be the
sharpest: most points in one 2 cm floor slice, floor below the cameras, surfaces either level or vertical.
The conversion stops if another reading is sharper.

Sky direction: frames are written as stored, like Stray does for a phone held in any orientation; the
pose carries the device rotation. arkitscenes.json records the metadata sky direction and the one the
poses imply; they must agree.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

MATCH_S = 0.005
JUMP_MPS = 2.5  # faster camera motion between two 10 Hz poses is a tracking jump
W_ZUP_TO_YUP = np.array([[1.0, 0, 0], [0, 0, 1], [0, -1, 0]])
CV_TO_GL = np.diag([1.0, -1.0, -1.0])  # OpenCV camera axes <-> ARKit/OpenGL camera axes
HEADER = "timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy, distortion_center_x, distortion_center_y"
LICENSE = "ARKitScenes (Apple), non-commercial licence: https://github.com/apple/ARKitScenes/blob/main/LICENSE"


def say(*a):
    print(*a, flush=True)


def read_traj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """(timestamps, raw 4x4 transforms as stored: rotation from angle-axis, translation)."""
    rows = np.array([[float(x) for x in ln.split()] for ln in path.read_text().splitlines() if ln.strip()])
    if rows.ndim != 2 or rows.shape[1] != 7:
        raise SystemExit(f"{path}: expected 7 columns (t, angle-axis, translation)")
    E = np.tile(np.eye(4), (len(rows), 1, 1))
    E[:, :3, :3] = Rotation.from_rotvec(rows[:, 1:4]).as_matrix()
    E[:, :3, 3] = rows[:, 4:7]
    return rows[:, 0], E


def stray_pose(E: np.ndarray, invert: bool = True, z_up: bool = True, gl_camera: bool = False) -> np.ndarray:
    """Camera-to-world, OpenCV camera axes, +Y up, from a stored traj transform (default: the official
    reading; the flags give the alternatives the check compares against)."""
    T = np.linalg.inv(E) if invert else E.copy()
    if gl_camera:
        T[..., :3, :3] = T[..., :3, :3] @ CV_TO_GL
    if z_up:
        W = np.eye(4)
        W[:3, :3] = W_ZUP_TO_YUP
        T = W @ T
    return T


def consistent_run(t: np.ndarray, E: np.ndarray) -> tuple[slice, list[dict]]:
    """The longest run of poses without a tracking jump: the camera moving faster than JUMP_MPS between
    two poses is ARKit re-localising, not a hand (measured peaks on clean captures: 0.6-1.4 m/s)."""
    c = np.linalg.inv(E)[:, :3, 3]
    speed = np.linalg.norm(np.diff(c, axis=0), axis=1) / np.maximum(np.diff(t), 1e-3)
    brk = np.where(speed > JUMP_MPS)[0]
    starts = np.r_[0, brk + 1]
    ends = np.r_[brk, len(t) - 1]
    k = int(np.argmax(ends - starts))
    jumps = [{"t": round(float(t[i] - t[0]), 2), "speed_mps": round(float(speed[i]), 2)} for i in brk]
    return slice(int(starts[k]), int(ends[k]) + 1), jumps


def stem_time(stem: str) -> float:
    return float(stem.split("_", 1)[1])


def pair_frames(raw: Path, traj_t: np.ndarray) -> list[dict]:
    """One frame per pose that has depth, confidence and colour within MATCH_S, plus its intrinsics."""
    def stems(sub, ext):
        return {p.stem: p for p in (raw / sub).glob(f"*{ext}")}
    depth, conf, rgb = stems("lowres_depth", ".png"), stems("confidence", ".png"), stems("lowres_wide", ".png")
    kfiles = stems("lowres_wide_intrinsics", ".pincam")
    common = sorted(set(depth) & set(conf) & set(rgb), key=stem_time)
    if not common:
        raise SystemExit(f"{raw}: no frame has depth, confidence and colour")
    ct = np.array([stem_time(s) for s in common])
    kst = sorted(kfiles, key=stem_time)
    kt = np.array([stem_time(s) for s in kst])
    out, used = [], set()
    for i, t in enumerate(traj_t):
        j = int(np.clip(np.searchsorted(ct, t), 1, len(ct) - 1))
        j = j - 1 if abs(ct[j - 1] - t) <= abs(ct[j] - t) else j
        if abs(ct[j] - t) > MATCH_S or j in used:
            continue
        k = int(np.argmin(np.abs(kt - ct[j]))) if len(kt) else -1
        if k < 0 or abs(kt[k] - ct[j]) > 0.0015:
            continue
        used.add(j)
        s = common[j]
        w, h, fx, fy, cx, cy = np.loadtxt(kfiles[kst[k]])
        out.append({"stem": s, "pose": i, "t": float(t), "depth": depth[s], "conf": conf[s], "rgb": rgb[s],
                    "K": (float(fx), float(fy), float(cx), float(cy)), "size": (int(w), int(h))})
    return out


def read16(p: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_UNCHANGED)


def fuse(frames: list[dict], T: np.ndarray, every: int = 5, px: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Confident depth (conf 2, 0.2-5 m) of every `every`-th frame in the world of T; camera centres."""
    pts, cams = [], []
    for f in frames[::every]:
        d = read16(f["depth"]).astype(np.float32) / 1000.0
        c = read16(f["conf"])
        h, w = d.shape
        fx, fy, cx, cy = f["K"]
        sx, sy = w / f["size"][0], h / f["size"][1]
        v, u = np.mgrid[0:h:px, 0:w:px]
        z = d[v, u]
        m = (c[v, u] >= 2) & (z > 0.2) & (z < 5.0)
        X = np.stack([(u[m] - cx * sx) * z[m] / (fx * sx), (v[m] - cy * sy) * z[m] / (fy * sy), z[m]], 1)
        Tf = T[f["pose"]]
        pts.append(X @ Tf[:3, :3].T + Tf[:3, 3])
        cams.append(Tf[:3, 3])
    return np.concatenate(pts), np.array(cams)


def sharpness(P: np.ndarray, cams: np.ndarray) -> dict:
    """Floor slice share along +Y, floor below the cameras, and how Manhattan the surfaces are."""
    import open3d as o3d
    y = P[:, 1]
    lo, hi = np.percentile(y, [0.5, 99.5])
    edges = np.arange(lo, hi + 0.005, 0.005)
    h, _ = np.histogram(y, edges)
    win = np.convolve(h, np.ones(4), mode="valid")  # 2 cm windows, 5 mm steps
    # the floor: the best 2 cm slice in the lower half of the height range
    lower = (edges[:-4] + 0.01) < 0.5 * (lo + hi)
    k = int(np.argmax(np.where(lower, win, -1)))
    floor_y = edges[k] + 0.01
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P))
    pc = pc.voxel_down_sample(0.02)
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.06, max_nn=30))
    ny = np.abs(np.asarray(pc.normals)[:, 1])
    near_floor = y[np.abs(y - floor_y) < 0.05] - floor_y
    spread = 1.4826 * np.median(np.abs(near_floor - np.median(near_floor))) if len(near_floor) else np.nan
    return {"floor_slice_share": round(float(win[k] / len(y)), 4),
            "floor_thickness_mm": round(float(spread) * 1000, 1),  # robust sigma of heights within 5 cm
            "best_slice_share_any_height": round(float(win.max() / len(y)), 4),
            "camera_height_above_floor_m": round(float(np.median(cams[:, 1] - floor_y)), 3),
            "points_below_floor_share": round(float(np.mean(y < floor_y - 0.05)), 4),
            "level_or_vertical_share": round(float(np.mean((ny > 0.995) | (ny < 0.105))), 4)}


def check_poses(frames: list[dict], E: np.ndarray) -> dict:
    variants = {
        "inverse, opencv camera, z-up -> y-up (used)": dict(invert=True, z_up=True, gl_camera=False),
        "as stored (not inverted)": dict(invert=False, z_up=True, gl_camera=False),
        "inverse, arkit/opengl camera axes": dict(invert=True, z_up=True, gl_camera=True),
        "inverse, no z-up -> y-up turn": dict(invert=True, z_up=False, gl_camera=False),
    }
    res = {}
    for name, kw in variants.items():
        P, cams = fuse(frames, stray_pose(E, **kw))
        res[name] = sharpness(P, cams)
        say(f"  {name:45s} {res[name]}")
    used = res[next(iter(variants))]
    others = [r for n, r in res.items() if n != next(iter(variants))]
    ok = (all(used["floor_slice_share"] > r["floor_slice_share"] for r in others)
          and used["camera_height_above_floor_m"] > 0.5 and used["points_below_floor_share"] < 0.02
          and all(used["level_or_vertical_share"] > r["level_or_vertical_share"] for r in others))
    return {"variants": res, "used_is_sharpest": bool(ok)}


def sky_from_poses(T: np.ndarray) -> str:
    """Where the sky is in the stored image, from camera-to-world poses in a +Y-up world: world +Y in
    camera axes is the second row of R. Names as in ARKitScenes' rectify_im.py (Left: sky at the left)."""
    up = np.median(T[:, 1, :3], axis=0)
    x, y = up[0], up[1]
    if abs(y) >= abs(x):
        return "Up" if y < 0 else "Down"
    return "Left" if x < 0 else "Right"


def write_odometry(path: Path, frames: list[dict], T: np.ndarray) -> None:
    lines = [HEADER]
    for i, f in enumerate(frames):
        Tf = T[f["pose"]]
        qx, qy, qz, qw = Rotation.from_matrix(Tf[:3, :3]).as_quat()
        fx, fy, cx, cy = f["K"]
        x, y, z = Tf[:3, 3]
        lines.append(f"{f['t']:.9f}, {i:06d}, {x:.8g}, {y:.8g}, {z:.8g}, {qx:.8g}, {qy:.8g}, {qz:.8g}, {qw:.8g}, "
                     f"{fx:.8g}, {fy:.8g}, {cx:.8g}, {cy:.8g}, , ")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_video(path: Path, frames: list[dict], fps: float) -> int:
    first = cv2.imdecode(np.fromfile(str(frames[0]["rgb"]), np.uint8), cv2.IMREAD_COLOR)
    h, w = first.shape[:2]
    tmp = path.with_name("rgb_tmp.mp4")
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not vw.isOpened():
        raise SystemExit("OpenCV cannot write mp4v video here")
    for f in frames:
        img = cv2.imdecode(np.fromfile(str(f["rgb"]), np.uint8), cv2.IMREAD_COLOR)
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h))
        vw.write(img)
    vw.release()
    cap = cv2.VideoCapture(str(tmp))
    n = 0
    while cap.grab():
        n += 1
    cap.release()
    if n != len(frames):
        raise SystemExit(f"rgb.mp4 decodes to {n} frames, expected {len(frames)}")
    tmp.replace(path)
    return n


def convert(raw: Path, out: Path, check: bool = True) -> dict:
    raw, out = Path(raw), Path(out)
    traj_t, E = read_traj(raw / "lowres_wide.traj")
    n_all = len(traj_t)
    run, jumps = consistent_run(traj_t, E)
    traj_t, E = traj_t[run], E[run]
    if jumps:
        say(f"tracking jumps at {jumps}: keeping poses {run.start}-{run.stop - 1} of {n_all}")
    frames = pair_frames(raw, traj_t)
    if len(frames) < 2:
        raise SystemExit(f"{raw}: only {len(frames)} frames have a pose")
    say(f"{raw}: {n_all} poses, {len(frames)} frames with depth, confidence, colour and a pose")
    meta = json.loads((raw / "meta.json").read_text()) if (raw / "meta.json").exists() else {}
    report = {"source": meta.get("source", str(raw)), "license": LICENSE, "video_id": meta.get("video_id"),
              "visit_id": meta.get("visit_id"), "n_poses": int(n_all), "tracking_jumps": jumps,
              "poses_kept": [run.start, run.stop - 1], "n_frames": len(frames),
              "fps": round(float((len(frames) - 1) / (frames[-1]["t"] - frames[0]["t"])), 2)}
    if check:
        say("pose check (fused depth of every 5th frame, confidence 2):")
        report["pose_check"] = check_poses(frames, E)
        if not report["pose_check"]["used_is_sharpest"]:
            raise SystemExit("pose check failed: another reading of the poses is sharper; nothing written")
    T = stray_pose(E)
    report["sky_direction_metadata"] = meta.get("sky_direction")
    report["sky_direction_from_poses"] = sky_from_poses(T[[f["pose"] for f in frames]])
    if report["sky_direction_metadata"] and report["sky_direction_metadata"] != report["sky_direction_from_poses"]:
        say(f"WARNING: metadata sky direction {report['sky_direction_metadata']} but the poses say "
            f"{report['sky_direction_from_poses']}")
    if out.exists():
        shutil.rmtree(out)
    (out / "depth").mkdir(parents=True)
    (out / "confidence").mkdir()
    for i, f in enumerate(frames):
        d = read16(f["depth"])
        if d is None or d.dtype != np.uint16 or d.ndim != 2:
            raise SystemExit(f"{f['depth']}: not a 16-bit depth image")
        shutil.copyfile(f["depth"], out / "depth" / f"{i:06d}.png")
        shutil.copyfile(f["conf"], out / "confidence" / f"{i:06d}.png")
    write_odometry(out / "odometry.csv", frames, T)
    K = np.median(np.array([f["K"] for f in frames]), axis=0)
    (out / "camera_matrix.csv").write_text(f"{K[0]}, 0.0, {K[2]}\n0.0, {K[1]}, {K[3]}\n0.0, 0.0, 1.0",
                                           encoding="utf-8", newline="\n")
    report["rgb_frames"] = write_video(out / "rgb.mp4", frames, report["fps"])
    (out / "arkitscenes.json").write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
    say(f"wrote {out} ({len(frames)} frames at {report['fps']} fps; sky {report['sky_direction_from_poses']})")
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("raw_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--no-check", action="store_true")
    a = ap.parse_args()
    if not (Path(a.raw_dir) / "lowres_wide.traj").exists():
        sys.exit(f"{a.raw_dir} has no lowres_wide.traj: fetch it with scripts/fetch_arkitscenes.py")
    convert(Path(a.raw_dir), Path(a.out_dir), check=not a.no_check)


if __name__ == "__main__":
    main()
