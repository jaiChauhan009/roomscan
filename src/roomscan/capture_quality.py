"""Capture-quality check: before the long run, tell the person holding the phone, in plain
words, whether to retake a capture and how.

Every check returns a `Finding` with a level:
    OK      nothing to do
    WARN    the run will go ahead, but the result may be worse; retake if it is easy
    RETAKE  the capture breaks the protocol badly enough that the result will be poor

Each tier has one entry point (`check_photos`, `check_video`, `check_lidar`, `check_roomplan`); all work on
downscaled images or a few dozen sampled frames, so a capture is checked in seconds.
The metric functions (`sharpness`, `overlap_inliers`, `look_up_fraction`, ...) are pure
and are what the unit tests exercise.

Thresholds were set on the real captures in ../data; the values that place each one are
noted beside it:
  photos  sample protocol photos (3-5 per room, 1440x1920) vs. our own photos taken while
          walking (9-55 per room) and the WhatsApp copies (960x1280, no EXIF);
  video   sample sweep (1920x1440) vs. our moto g45 walk-through and the WhatsApp copy
          (464x832);
  LiDAR   sample scan A (looks up at the ceiling) vs. scan B (never does) and our three
          iPhone scans.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

OK, WARN, RETAKE = "OK", "WARN", "RETAKE"
_RANK = {OK: 0, WARN: 1, RETAKE: 2}

# Every threshold below is followed by the values measured on ../data that place it.
# ---- photos
PHOTOS_MIN, PHOTOS_MAX = 2, 8           # what the protocol asks for; sample set: 3-5 per room
PHOTOS_RETAKE = 12                      # more = taken while walking; own photos_1: 9-55, WhatsApp set: 4-19
CHAT_LONG_SIDE = 1600                   # px; WhatsApp copies 1280, sample 1920, moto g45 originals 4080
WORK_SIDE = 640                         # long side the image checks run at
BLUR_DETAIL = 0.15                      # detail_ratio below = blurred; lowest of 179 real photos: 0.18
                                        # (sample 0.19-1.0); Gaussian blur sigma 1.5 gives < 0.1
OVERLAP_MIN = 8                         # RANSAC inliers; fewer = no common geometry with the neighbour
LOOKBACK_MIN = 15                       # ORB inliers for a look-back photo to count as found:
                                        # sample 6 rooms: 0, 996, 634, 1149, 1149, 7; WhatsApp set: 0, 14, 10, 138
LOOKBACK_FRAC = 0.5                     # WARN if fewer rooms than this have one (sample 4/6, WhatsApp 1/4)
# ---- video
VIDEO_MIN_HEIGHT = 720                  # short side below this: a chat-app copy (WhatsApp clip: 464)
VIDEO_SAMPLES = 40
MOTION_DT = 0.2                         # s between the two frames of a motion sample
PAN_WARN, PAN_RETAKE = 0.45, 0.75       # median image motion, image widths per second (phase
                                        # correlation): sample sweeps 0.26, own walk 0.88, WhatsApp 1.03
BLANK_STD = 12.0                        # grey-level std (0-255) of a near-blank frame; real frames >= 20
BLANK_FRAC_WARN, BLANK_FRAC_RETAKE = 0.15, 0.35
VBLUR_FRAC_WARN, VBLUR_FRAC_RETAKE = 0.25, 0.5  # fraction of frames below BLUR_DETAIL (real: 5th pct >= 0.2)
# ---- LiDAR
LOOK_UP_DEG = 20.0                      # camera axis this far above horizontal: with the ~30 deg half
                                        # field of view the top of the picture is 50 deg up, on the ceiling.
                                        # Scan A tops out at 33 deg (only 1 % of frames above 30), scan B at -8.
LOOK_UP_FRAC = 0.01                     # fraction of frames above LOOK_UP_DEG: scan A 16.6 %, lidar_1 3.2 %,
                                        # lidar_2 17.4 %; scan B 0 %, lidar_3 0 % (highest 12 deg)
JUMP_M = 0.30                           # pose step between consecutive frames; normal steps <= 0.085 m
LIDAR_MIN_S = 20.0
TURN_WARN_DEG_S = 100.0                 # 95th percentile of the turn rate: samples 75-79, own 112-142


@dataclass
class Finding:
    check: str
    level: str
    message: str            # what was measured
    advice: str = ""        # what to do; empty when OK

    def line(self) -> str:
        s = f"{self.level:<6} {self.check}: {self.message}"
        return s + (f" -> {self.advice}" if self.advice and self.level != OK else "")


def worst(findings: Iterable[Finding]) -> str:
    return max((f.level for f in findings), key=_RANK.__getitem__, default=OK)


def report_lines(findings: list[Finding]) -> list[str]:
    v = worst(findings)
    head = {OK: "capture quality: OK",
            WARN: "capture quality: usable, but see the warnings (retake if it is easy)",
            RETAKE: "capture quality: RETAKE advised (see below)"}[v]
    return [head] + ["  " + f.line() for f in findings]


# ---------------------------------------------------------------- image metrics

def to_gray_small(img: np.ndarray, side: int = WORK_SIDE) -> np.ndarray:
    import cv2
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    s = side / max(g.shape[:2])
    return cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else g


def load_gray(path: Path, side: int = WORK_SIDE) -> np.ndarray:
    """Greyscale, long side `side`; JPEGs are decoded at reduced scale (fast)."""
    from PIL import Image, ImageOps
    if path.suffix.lower() in (".heic", ".heif"):
        from roomscan.heif import register
        register()
    with warnings.catch_warnings(), Image.open(path) as im:
        warnings.simplefilter("ignore")
        im.draft("L", (side, side))
        im = ImageOps.exif_transpose(im.convert("L"))
        a = np.asarray(im)
    return to_gray_small(a, side)


def sharpness(gray: np.ndarray) -> float:
    """Variance of the Laplacian (higher = sharper); compare images of the same size."""
    import cv2
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def detail_ratio(gray: np.ndarray) -> float:
    """Fine-scale over coarse-scale Laplacian energy: about 0.2-0.8 for a sharp photo of
    any scene (bare wall or busy shelf), near 0.05 for a blurred one. Scene-independent,
    unlike the plain variance of the Laplacian."""
    import cv2
    half = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    return sharpness(gray) / max(sharpness(half), 1e-6)


def orb_features(gray: np.ndarray, n: int = 1000):
    import cv2
    orb = cv2.ORB_create(n)
    return orb.detectAndCompute(gray, None)


def overlap_inliers(fa, fb) -> int:
    """Feature matches between two images that agree on one two-view geometry (RANSAC
    fundamental matrix): a few means the views barely overlap."""
    import cv2
    (ka, da), (kb, db) = fa, fb
    if da is None or db is None or len(ka) < 8 or len(kb) < 8:
        return 0
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < 8:
        return 0
    pa = np.float32([ka[m.queryIdx].pt for m in good])
    pb = np.float32([kb[m.trainIdx].pt for m in good])
    _, mask = cv2.findFundamentalMat(pa, pb, cv2.FM_RANSAC, 2.0, 0.99)
    return int(mask.sum()) if mask is not None else 0


def image_motion(a: np.ndarray, b: np.ndarray) -> float:
    """Shift between two greyscale frames, in image widths (phase correlation: handles
    pans of up to half the picture, unlike small-window optical flow). Frames with nothing
    in common give a large random shift, which is what a too-fast move should give."""
    import cv2
    win = cv2.createHanningWindow(a.shape[::-1], cv2.CV_64F)
    (dx, dy), _ = cv2.phaseCorrelate(a.astype(np.float64), b.astype(np.float64), win)
    return float(np.hypot(dx, dy)) / a.shape[1]


def is_blank(gray: np.ndarray) -> bool:
    """A frame with almost nothing in it (bare wall too close, lens covered)."""
    return float(gray.std()) < BLANK_STD


# ---------------------------------------------------------------- photos

def _focal_and_size(p: Path) -> tuple[bool, int]:
    from PIL import Image
    if p.suffix.lower() in (".heic", ".heif"):
        from roomscan.heif import register
        register()
    with warnings.catch_warnings(), Image.open(p) as im:
        warnings.simplefilter("ignore")
        ex = dict(im.getexif().get_ifd(0x8769))
        return bool(ex.get(0xA405) or ex.get(0x920A)), max(im.size)


def check_photos(rooms: dict[str, list[Path]], name: Callable[[Path], str] = lambda p: p.name,
                 look_back: bool = True) -> list[Finding]:
    """`rooms`: room folder name -> photos in sweep order (walk order = dict order)."""
    F: list[Finding] = []
    names = list(rooms)
    # folders numbered
    if len(names) > 1:
        un = [r for r in names if not re.match(r"\d", r)]
        F.append(Finding("folders", WARN if un else OK,
                         f"{len(un)} of {len(names)} room folders not numbered ({', '.join(un[:4])})" if un
                         else f"{len(names)} room folders, numbered",
                         "name them in walk order: 01_hall, 02_kitchen, ... (the order is guessed otherwise)"))
    # count
    many = {r: len(f) for r, f in rooms.items() if len(f) > PHOTOS_RETAKE}
    some = {r: len(f) for r, f in rooms.items() if PHOTOS_MAX < len(f) <= PHOTOS_RETAKE or len(f) < PHOTOS_MIN}
    lvl = RETAKE if many else WARN if some else OK
    bad = {**many, **some}
    F.append(Finding("photo count", lvl,
                     ("; ".join(f"{r}: {n}" for r, n in bad.items()) + f" (asked: {PHOTOS_MIN}-{PHOTOS_MAX})") if bad
                     else "; ".join(f"{r}: {len(f)}" for r, f in rooms.items()),
                     "taken while walking: stand in the doorway, take 5-6 photos turning left to right, "
                     "then one looking back into the room you came from" if many else
                     f"take {PHOTOS_MIN}-{PHOTOS_MAX} photos per room from the doorway"))
    # originals vs chat-app copies
    nofocal, small, n = [], [], 0
    for r, files in rooms.items():
        for p in files:
            n += 1
            try:
                foc, side = _focal_and_size(p)
            except Exception:
                continue  # unreadable files are walkin's completeness check
            if not foc:
                nofocal.append(p)
            if side < CHAT_LONG_SIDE:
                small.append((p, side))
    if small:
        F.append(Finding("originals", RETAKE,
                         f"{len(small)} of {n} photos are only {small[0][1]} px on the long side"
                         + (f" and {len(nofocal)} carry no focal length" if nofocal else "")
                         + " (a chat-app copy)",
                         "send the original photos (Photos app > Share > Save to Files, or AirDrop), "
                         "not through WhatsApp or another chat app"))
    elif nofocal:
        F.append(Finding("originals", WARN, f"{len(nofocal)} of {n} photos carry no focal length "
                         f"(e.g. {name(nofocal[0])})",
                         "copy the originals, not edited or chat-app copies"))
    else:
        F.append(Finding("originals", OK, f"{n} photos, full resolution with EXIF focal length"))
    # blur and overlap, on 640-px greyscale
    feats: dict[Path, tuple] = {}
    blurred, weak, npairs = [], [], 0
    for r, files in rooms.items():
        grays = {}
        for p in files:
            try:
                grays[p] = load_gray(p)
            except Exception:
                continue
        blurred += [f"{r}/{name(p)}" for p, g in grays.items() if detail_ratio(g) < BLUR_DETAIL]
        for p, g in grays.items():
            feats[p] = orb_features(g)
        seq = [p for p in files if p in feats]
        for a, b in zip(seq, seq[1:]):
            npairs += 1
            k = overlap_inliers(feats[a], feats[b])
            if k < OVERLAP_MIN:
                weak.append((f"{r}/{name(a)} -> {name(b)}", k))
    F.append(Finding("sharpness", WARN if blurred else OK,
                     (f"{len(blurred)} blurred photo(s): " + ", ".join(blurred[:5])
                      + (", ..." if len(blurred) > 5 else "")) if blurred else "no blurred photos",
                     "retake those photos holding the phone still (lean on the door frame), with the lights on"))
    # Information only, never a warning: on bare indoor walls feature matching also misses
    # the overlap of protocol photos (sample set: 11 of 21 neighbour pairs below OVERLAP_MIN,
    # WhatsApp set 33 of 53, own walking set alike), so it cannot tell the user to retake.
    F.append(Finding("overlap", OK,
                     f"{npairs - len(weak)} of {npairs} neighbouring pairs matched (information only; "
                     f"each photo should share about a third of the previous one)"))
    # look-back: the last photo of room k shows a room walked through before
    if look_back and len(names) > 1:
        missing, n_rooms = [], 0
        for i, r in enumerate(names[1:], 1):
            last = rooms[r][-1] if rooms[r] else None
            if last not in feats:
                continue
            prev = [p for q in names[:i] for p in rooms[q] if p in feats]
            if len(prev) > 24:
                prev = [prev[j] for j in np.linspace(0, len(prev) - 1, 24).astype(int)]
            n_rooms += 1
            best = max((overlap_inliers(feats[last], feats[p]) for p in prev), default=0)
            if best < LOOKBACK_MIN:
                missing.append(r)
        found = n_rooms - len(missing)
        F.append(Finding("look-back photo", WARN if found < LOOKBACK_FRAC * n_rooms else OK,
                         f"found in {found} of {n_rooms} rooms after the first"
                         + (f" (none in {', '.join(missing[:4])}{', ...' if len(missing) > 4 else ''})" if missing else ""),
                         "without moving your feet, turn around and take one last photo of the room you came from"))
    return F


# ---------------------------------------------------------------- video

def video_samples(path: Path, n: int = VIDEO_SAMPLES, side: int = 320):
    """(info, samples) for `n` evenly spaced moments; a sample is (frame, frame MOTION_DT
    later, both `side` px; the first frame again at WORK_SIDE px)."""
    import cv2
    vc = cv2.VideoCapture(str(path))
    try:
        N, fps = int(vc.get(cv2.CAP_PROP_FRAME_COUNT) or 0), float(vc.get(cv2.CAP_PROP_FPS) or 0)
        w, h = int(vc.get(cv2.CAP_PROP_FRAME_WIDTH)), int(vc.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out = []
        gap = max(1, int(round(MOTION_DT * fps))) if fps > 0 else 6
        for f in np.linspace(0, max(N - gap - 2, 0), n).astype(int):
            vc.set(cv2.CAP_PROP_POS_FRAMES, int(f))
            ok1, a = vc.read()
            for _ in range(gap - 1):
                vc.grab()
            ok2, b = vc.read()
            if ok1 and ok2:
                out.append((to_gray_small(a, side), to_gray_small(b, side), to_gray_small(a, WORK_SIDE)))
    finally:
        vc.release()
    return {"frames": N, "fps": fps, "width": w, "height": h,
            "duration": N / fps if fps > 0 else 0.0, "gap": gap}, out


def video_metrics(info: dict, samples) -> dict:
    fps = info["fps"] or 30.0
    dt = info.get("gap", 1) / fps
    speed = np.array([image_motion(a, b) / dt for a, b, _ in samples])        # widths / s
    blank = np.array([is_blank(a) for a, _, _ in samples])
    sharp = np.array([detail_ratio(g) for _, _, g in samples])
    return {"speed": speed, "blank": blank, "sharp": sharp}


def check_video(path: Path) -> list[Finding]:
    info, samples = video_samples(path)
    return video_findings(info, video_metrics(info, samples))


def video_findings(info: dict, m: dict) -> list[Finding]:
    F: list[Finding] = []
    d = info["duration"]
    F.append(Finding("duration", WARN if d < 30 else OK, f"{d:.0f} s",
                     "about one minute per room: walk at half speed and cover every wall"))
    short = min(info["width"], info["height"])
    F.append(Finding("resolution", RETAKE if short < VIDEO_MIN_HEIGHT else OK,
                     f"{info['width']}x{info['height']}" + (" (a chat-app copy)" if short < VIDEO_MIN_HEIGHT else ""),
                     "send the original clip (Photos app > Share > Save to Files), not through WhatsApp "
                     "or another chat app"))
    sp = m["speed"]
    if len(sp):
        med = float(np.median(sp))
        lvl = RETAKE if med > PAN_RETAKE else WARN if med > PAN_WARN else OK
        F.append(Finding("motion", lvl, f"the picture moves {med:.2f} image widths per second (median; "
                         f"protocol sweeps about 0.3, at most {PAN_WARN})",
                         "walking or turning too fast: slow down to one step every two seconds, "
                         "step sideways while turning, never pan quickly"))
        sh = m["sharp"]
        bl = float(np.mean(sh < BLUR_DETAIL))
        lvl = RETAKE if bl > VBLUR_FRAC_RETAKE else WARN if bl > VBLUR_FRAC_WARN else OK
        F.append(Finding("sharpness", lvl, f"{100 * bl:.0f} % of sampled frames blurred "
                         f"(median detail ratio {np.median(sh):.2f})",
                         "motion blur: move more slowly and switch on all the lights"))
        bk = float(np.mean(m["blank"]))
        lvl = RETAKE if bk > BLANK_FRAC_RETAKE else WARN if bk > BLANK_FRAC_WARN else OK
        F.append(Finding("blank frames", lvl, f"{100 * bk:.0f} % of sampled frames nearly blank",
                         "facing a bare wall too closely: keep 1.5 m or more from walls and some floor in view"))
    else:
        F.append(Finding("frames", RETAKE, "no frame could be decoded",
                         "record with Settings > Camera > Formats > Most Compatible"))
    return F


# ---------------------------------------------------------------- LiDAR

def camera_pitch_deg(quats: np.ndarray) -> np.ndarray:
    """Elevation of the camera's viewing axis above horizontal, degrees, per pose.

    `quats`: (N, 4) qx, qy, qz, qw mapping OpenCV camera axes (z forward) into the ARKit
    world (+Y up), as Stray Scanner's odometry.csv stores them."""
    q = np.asarray(quats, float)
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    x, y, z, w = q.T
    fy = 2 * (y * z - x * w)            # world-Y component of R @ [0, 0, 1] (R[1, 2])
    return np.degrees(np.arcsin(np.clip(fy, -1, 1)))


def look_up_fraction(quats: np.ndarray, deg: float = LOOK_UP_DEG) -> float:
    p = camera_pitch_deg(quats)
    return float(np.mean(p > deg)) if len(p) else 0.0


def pose_jumps(xyz: np.ndarray, thr: float = JUMP_M) -> int:
    st = np.linalg.norm(np.diff(np.asarray(xyz, float), axis=0), axis=1)
    return int(np.sum(st > thr))


def turn_rate_deg_s(t: np.ndarray, quats: np.ndarray) -> np.ndarray:
    """Rotation speed between consecutive poses, degrees per second."""
    q = np.asarray(quats, float)
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    dot = np.abs(np.sum(q[1:] * q[:-1], axis=1)).clip(0, 1)
    dt = np.diff(np.asarray(t, float))
    ang = np.degrees(2 * np.arccos(dot))
    return ang / np.where(dt > 1e-4, dt, np.inf)


def check_lidar(root: Path) -> list[Finding]:
    from roomscan.frontends.lidar_stray import read_odometry
    od = read_odometry(Path(root))
    od = od[np.isfinite(od[:, :9]).all(axis=1)]
    return lidar_findings(od[:, 0], od[:, 2:5], od[:, 5:9], (Path(root) / "rgb.mp4").is_file())


def lidar_findings(t: np.ndarray, xyz: np.ndarray, quats: np.ndarray, has_rgb: bool = True) -> list[Finding]:
    F: list[Finding] = []
    d = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    F.append(Finding("duration", WARN if d < LIDAR_MIN_S else OK, f"{d:.0f} s",
                     "30-40 seconds per room: walk once around every room"))
    up = look_up_fraction(quats)
    p = camera_pitch_deg(quats)
    F.append(Finding("ceiling", OK if up >= LOOK_UP_FRAC else WARN if up > 0 else RETAKE,
                     f"{100 * up:.1f} % of frames look more than {LOOK_UP_DEG:.0f} deg up "
                     f"(highest {p.max() if len(p) else 0:.0f} deg)",
                     "tilt the phone up to the ceiling once in every room (no ceiling height otherwise)"))
    j = pose_jumps(xyz)
    F.append(Finding("tracking", WARN if j else OK,
                     f"{j} tracking jump(s) over {JUMP_M} m between frames" if j else "no tracking jumps",
                     "tracking was lost: move more slowly, keep 50 cm or more from walls, do not cover the sensor"))
    tr = turn_rate_deg_s(t, quats)
    if len(tr) > 10:
        fast = float(np.percentile(tr, 95))
        F.append(Finding("turn speed", WARN if fast > TURN_WARN_DEG_S else OK,
                         f"95th percentile {fast:.0f} deg/s",
                         "turning too fast: turn slowly, about a quarter turn in two seconds"))
    F.append(Finding("colour video", OK if has_rgb else WARN, "rgb.mp4 present" if has_rgb else "no rgb.mp4",
                     "copy the whole Stray Scanner folder (Compress it first): no damage detection without it"))
    return F


# ---------------------------------------------------------------- RoomPlan

def check_roomplan(path: Path) -> list[Finding]:
    """A RoomPlan capture (our iOS app's capture.json, one or several): RETAKE when capture.json
    cannot be used or a room has no walls; WARN on walls RoomPlan is not sure of (confidence
    low) and when there are no frames (no damage detection); OK otherwise. Reads capture.json
    and the frames' image headers only."""
    from roomscan.frontends.roomplan import find_roomplan_roots, parse_capture
    from roomscan.pipeline import InputError

    retake = "scan the room again with the app, slowly, pointing at every wall"
    try:
        roots = find_roomplan_roots(Path(path))
    except InputError as e:
        return [Finding("roomplan export", RETAKE, str(e), "export the scan again from the app")]
    if not roots:
        return [Finding("roomplan export", RETAKE, "no capture.json found", "upload the zip the app made, unchanged")]
    F: list[Finding] = []
    for root in roots:
        notes: list[str] = []
        try:
            meta, rooms, frames = parse_capture(root, notes)
        except InputError as e:
            F.append(Finding("roomplan export", RETAKE, str(e), "export the scan again from the app"))
            continue
        tag = f"{root.name}: " if len(roots) > 1 else ""
        F.append(Finding("roomplan export", OK, f"{tag}{len(rooms)} room(s) from app {meta['app_version'] or '?'} "
                         f"on {meta['device']['model'] or 'unknown device'}"))
        for r in rooms:
            if not r.walls:
                F.append(Finding("walls", RETAKE, f"{tag}{r.name}: RoomPlan found no walls", retake))
                continue
            unsure = [w for w in r.walls if w.confidence != "high"]
            low = sum(w.confidence == "low" for w in unsure)
            F.append(Finding("walls", WARN if low else OK,
                             f"{tag}{r.name}: {len(r.walls)} walls"
                             + (f", {len(unsure)} not high confidence ({low} low)" if unsure else ""),
                             "low-confidence walls get wide intervals: scan the room again, slowly, keeping each "
                             "wall in view for a moment"))
        F.append(Finding("frames", OK if frames else WARN, f"{tag}{len(frames)} frame(s)",
                         "no frames in the capture: no damage detection (turn frame capture on in the app)"))
        for n in notes:
            if "frame" in n:
                F.append(Finding("frames", WARN, n, "export the scan again from the app"))
    return F
